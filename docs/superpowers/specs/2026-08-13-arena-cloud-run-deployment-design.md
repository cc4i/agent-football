# Deploying the arena: one Cloud Run service, a real database, and a URL you can text to somebody

Date: 2026-08-13
Status: Approved, ready for implementation planning.

## Purpose

The arena works. It runs on a laptop, on loopback, with a SQLite file beside
it, and a phone can only reach it if the phone is on the same wifi and somebody
remembered to set `ARENA_PUBLIC_URL` to the laptop's LAN name.

This puts it on the internet: one HTTPS URL, always up, that 50 people can play
at once and that does not lose the leaderboard when it restarts. Everything the
players touch goes with it, which means the arena, the pitch, the coach and the
captain. The dugout does not: it embeds the Antigravity CLI and runs shell
commands unrestricted by design, so it stays on the presenter's machine and
points at the deployed arena.

Two constraints shaped every decision below, and both came from the user:

- **There is no DNS name.** The deployment gets `https://<service>-<hash>.a.run.app`
  and nothing else. No custom domain, so no managed certificate, so no external
  HTTPS load balancer worth the name.
- **Local development must keep working**, on native Postgres, without Docker.

## What is being deployed

```
  https://arena-<hash>.a.run.app          HTTPS free, no DNS, no LB, no cert
                    |
  +-----------------v-------------------------------------------+
  |  one Cloud Run service, one instance, min=1 max=1            |
  |                                                              |
  |  arena          :8080  4 vCPU / 8Gi   the only ingress       |
  |    /arena  /join  /play  /api  /ws     as today              |
  |    /pitch/*                            the built Vite bundle |
  |    /run_sse  /api-apps/*               proxied to :8000      |
  |    /player_state/*                     the shared volume     |
  |                                                              |
  |  coach   (adk web)   :8000  2 vCPU / 4Gi   localhost only    |
  |  captain (A2A)       :8001  2 vCPU / 4Gi   localhost only    |
  |                                                              |
  |  in-memory volume, 128Mi, mounted into arena and coach       |
  +--------------------------+-----------------------------------+
                             |
                       Cloud SQL for PostgreSQL 18
                       (durability, and nothing else)
```

One service. One instance. The three containers share a network namespace, so
the arena reaches the coach at `127.0.0.1:8000` exactly as it does on a laptop
today, and the coach reaches the captain at `127.0.0.1:8001`. Only the arena
has a port published, so the coach and the captain are unreachable from the
internet without a single firewall rule being written.

### Why one instance and not fifty

The obvious cloud answer is to make the arena stateless and let Cloud Run scale
it out. That answer is wrong here, and it is worth writing down why, because it
is the load-bearing decision in this document.

Four things in the arena are per-process, and each one breaks differently when
a second process appears:

| What | Where | What a second instance does to it |
|---|---|---|
| The match bus | `bus.py:61`, `app.py:149` | A phone connected to instance B never sees a frame published on A. The relay goes silent for half the room. |
| `state.heard` | `app.py:154` | Instance B has never heard from a host that is talking to A, so its watchdog abandons a match that is being played. |
| `Chain._slots` | `chain.py:70` | The Gemini concurrency limit is per instance. `ARENA_CHAIN_LIMIT=4` on five instances is a limit of 20, which is not what the venue's quota says. |
| `Chain._seats` | `chain.py:88` | A shout accepted on A whose specialist PATCHes back to B: `caused_by()` returns `None`, the change is recorded with no shout attached, and the "effective shouts" term in scoring is quietly wrong. No error anywhere. |

The last one is the dangerous one. Everything else fails loudly. That one
produces a leaderboard that is subtly incorrect and looks fine.

The usual fix is Redis: pub/sub for the bus, a key for `heard`, a distributed
semaphore for the slots. That is three new failure modes, a Memorystore
instance, a VPC connector, and a serverless VPC egress bill, all bought to
solve a problem the traffic does not actually have.

Because the traffic does not have it. At 50 concurrent rooms the host tab
publishes state at 10 Hz (`game.js:1622` throttles to one frame per 100ms), so
the arena takes about 500 messages a second in and fans them out to roughly
2,700 sends a second. That is a quarter of one core. The pitch does the physics;
the arena moves small JSON. Capacity was never the constraint. Availability and
clean deploys were, and neither is solved by Redis.

So: one instance, `--no-cpu-throttling`, and the in-process bus, `heard` and
semaphore are all correct again by construction. The 8 vCPU / 32 GiB Cloud Run
instance ceiling is roughly an order of magnitude above the measured need.

### Why not GKE, and why not a load balancer

Co-locating everything in one pod behind an LB and an IP was considered and
rejected for one concrete reason: `arena.js` calls `navigator.wakeLock` to keep
a manager's phone awake through a three-minute match, and the Wake Lock API
requires a secure context. `http://<IP>/` is not one. Phones would sleep
mid-match. Getting HTTPS back means a certificate, which means a domain, which
is the thing we do not have.

Cloud Run's `*.run.app` hostname is HTTPS with a valid certificate, for free,
with no DNS record to create. That is the whole reason for the platform choice.

## The database

Cloud Run instances have an ephemeral filesystem. A SQLite file in the
container is deleted on every deploy, every crash and every scale-to-zero, and
the leaderboard is the one thing that has to survive weeks.

**Cloud SQL for PostgreSQL 18**, smallest tier, connected over the Unix socket
that `--add-cloudsql-instances` mounts at `/cloudsql/<connection-name>`. No VPC
connector, no public IP, no password on the wire.

Version 18 is deliberate: it is what Cloud SQL defaults to and what Homebrew's
`postgresql@18` installs locally, so local and deployed run the same major.

### Keeping the single connection

`db.py` opens one connection and every module takes it as an argument, which
its docstring is explicit about: *"Every other module takes an open connection
rather than reaching for a global."*

That stays. It is tempting to introduce `psycopg_pool` because that is what one
does, but the single connection is currently serialising every write, and that
serialisation is holding up a race that nobody has had to think about:

```python
# rooms.py:158
"INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
"SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ?, ? FROM event WHERE room_id = ? "
"RETURNING seq"
```

The captain puts a shout to all four specialists **in parallel**, and each one
PATCHes back through `_record_patch` (`app.py:659`), which appends an event.
Four concurrent `MAX(seq) + 1` reads against the same room return the same
number, and the `UNIQUE (room_id, seq)` constraint turns that into an
`IntegrityError` on three of the four. Today one connection means one statement
at a time and it cannot happen. A pool makes it happen roughly every shout.

So the migration is deliberately small:

- placeholders `?` become `%s`
- `INTEGER PRIMARY KEY AUTOINCREMENT` becomes `GENERATED ALWAYS AS IDENTITY`
- `sqlite3.Row` becomes psycopg's `dict_row`, which keeps every `row["name"]`
  access in the codebase working unchanged
- one connection, `autocommit=False`, reconnect-on-failure at the edge

A pool is a later decision, and the day it is taken, `append_event` must become
`SELECT ... FOR UPDATE` on the room row first. Written here so that whoever
takes it knows what they are buying.

### `heard` becomes a column

One exception to "one instance means in-process state is fine". A Cloud Run
rollout briefly runs the old instance and the new one at the same time. During
those seconds the new arena's `heard` dict is empty, and every live match looks
to it like a host that has gone.

`app.py:944` already softens this:

```python
if now - heard.setdefault(code, now) <= HOST_GONE_SECONDS:
```

A code the new instance has never seen gets 30 seconds of grace, which covers a
rollout. But it covers it by accident, and it means a genuinely dead room
survives one sweep longer than it should.

Move it to the database: a `last_heard_at` column on `room`, written by the
host frame handler, read by `_give_up_on_the_missing`. It is one column and it
makes the watchdog correct across a restart rather than merely lucky.

## The pitch, baked in

Today the pitch is a Vite dev server on :5173 and `ARENA_PITCH_URL` points the
big screen at it. In the deployment there is no second origin to point at, and
without a domain there is no CDN and no load balancer to put one behind.

The arena serves it. `npm run build` runs in the image build, and the output is
mounted at `/pitch/` alongside the existing `/static` mount.

This is a reversal of an earlier position in the design conversation, where
bundling the pitch into the arena image looked like the wrong trade. Two things
changed it: without a domain there is no better option, and same-origin turns
out to be exactly what the pitch needs. `vite.config.js` says so already:

> Proxying rather than opening CORS on :8003 keeps the pitch same-origin, which
> is what lets the big screen frame it and lets the socket carry the host token.

In production the proxy is not a dev convenience any more, it is the
architecture. The pitch's `/api/`, `/ws/`, `/run_sse` and `/api-apps/` calls are
same-origin against the arena for real.

Changes needed:

- `vite.config.js` gets `base: '/pitch/'`
- absolute asset paths in `game.js`'s `preload()` become base-relative, so the
  new sprite atlases load from `/pitch/assets/...`
- `ARENA_PITCH_URL` defaults to `${ARENA_PUBLIC_URL}/pitch` when unset

The existing `Revalidated(StaticFiles)` subclass (`app.py:1008`) sets
`Cache-Control: no-cache`, which is right for the arena's own static files and
wrong for Vite's content-hashed bundle. The `/pitch/assets/` mount gets a long
`max-age` and `immutable`; `/pitch/index.html` keeps `no-cache`.

## The injury loop, which breaks in three places

The most valuable thing to come out of the design conversation. A specialist
agent can report an injury or ask for a substitution through an MCP server, and
the browser toasts it. That path breaks in all three of its legs the moment the
coach stops being a thing the browser can reach, and one leg is broken today.

**Leg 1: the browser calls the coach directly.** `main.js:432` fetches
`/api-apps/agents/users/user/sessions` and `main.js` then posts to `/run_sse`.
Vite proxies both to :8000 in development. In the deployment the coach has no
published port, so both 404.

*Fix:* the arena proxies them, with an allowlist of exactly two paths and
nothing else: `POST /run_sse`, and `POST /api-apps/agents/users/user/sessions`
rewritten to `/apps/...` on the way through, which is the same rewrite
`vite.config.js` does today. Everything else under `/api-apps/` is refused.
An open proxy in front of an unauthenticated ADK server on the public internet
is the one mistake in this design that would be expensive. The arena already
talks to the coach (`coach.py`); this is the same call from a different caller.

**Leg 2: the MCP server writes into the pitch's source tree.**

```python
# football_mcp_server.py:42
PLAYER_STATE_DIR = os.path.join(BASE_DIR, "../frontend/public/player_state")
```

That path is relative to the *coach* container, which does not contain the
pitch. Even if it did, the image layer is read-only and anything written to the
container filesystem dies with the instance.

**Leg 3: nothing serves `/player_state/`.** `main.js:844` polls
`/player_state/substitutions/{code}__{team}.json` every two seconds. Vite serves
it from `public/` in development. In the deployment nothing does, and
`checkSubstitutions` swallows the 404s silently, so the feature is not merely
broken, it is quietly broken.

*Fix for legs 2 and 3, together, with no code change:* a Cloud Run in-memory
volume, `sizeLimit: 128Mi`, mounted at the coach's `PLAYER_STATE_DIR` and at a
path the arena serves as `/player_state/`. The MCP server writes a file, the
arena serves that file, the browser polls it. Same filesystem, two containers,
because they are one instance.

This is the second reason the co-located topology is the right one. A
stateless multi-instance arena could not do this at all without turning
substitutions into another database table and another socket message.

### It is already broken, before any of this

`sendInstructionToAgent` (`main.js:432`) opens its ADK session without putting
the room in the session state. So `football_mcp_server.py:63` falls back:

```python
DEFAULT_ROOM = "WRKS"
```

Every room's status-check injuries are being written into the workshop's file
today, on a laptop, right now. Nobody has noticed because nobody has run two
rooms and looked. The session-create call must carry `room` and `team`. It is a
prerequisite for the deployment rather than part of it, and it should be fixed
and verified locally first.

## Status checks: the workshop only

`main.js:935` starts `setInterval(runStatusCheck, STATUS_CHECK_MS)` with
`STATUS_CHECK_MS = 55000`. Every host tab asks its four specialists how tired
they are, roughly three times per three-minute match.

At 50 rooms that is 150 chains per match, each waking a coach, a captain and
four specialists: about 350 Gemini calls a minute, none of them asked for by a
human. And they bypass `Chain` entirely, so they are not governed by
`ARENA_CHAIN_LIMIT`. Two outcomes, both bad: leave them ungoverned and Vertex
starts returning 429s to the shouts managers actually typed; route them through
the arena and 150 robot chains queue ahead of those shouts, so managers see
`Busy`.

The chain's real service rate is `LIMIT / chain_duration` = `4 / 30s` = about
0.13 chains a second, which saturates at roughly 8 concurrent rooms shouting
steadily. There is no headroom to spend on autonomous chatter.

Gate it to the workshop, which is where it earns its keep: the workshop is a
long-lived demo room with an audience watching for exactly this kind of
autonomous behaviour. A real match is three minutes long and the managers are
already shouting.

`main.js:930` currently reads `if (!isViewer())` and covers both the
substitution poll and the status check. Split them: the substitution poll stays
on `!isViewer()`, because a specialist can report an injury during a manager's
shout and the toast should still appear. The status check moves under
`if (!room.inMatch)`, which is true only in the workshop.

## Hardening

The URL is public and anyone with it can post to it.

- **Secrets from Secret Manager**, injected as environment variables:
  `ARENA_SECRET`, `ARENA_EMAIL_SALT`, `ARENA_SERVICE_TOKEN`, and the Cloud SQL
  password. None committed, none defaulted in production.
- **Fail fast.** `ARENA_ENV=production` makes the arena refuse to start when any
  of those three is unset, rather than warning and carrying on as it does today
  (`app.py:87`). A random-per-start `ARENA_SECRET` in production logs every
  phone out on every deploy, and the salt changing loses every player's history.
- **Per-IP token buckets** on `POST /api/players` and `POST /api/rooms`. These
  are the two unauthenticated endpoints that create rows.
- **A cap on live rooms.** Beyond it, `POST /api/rooms` returns 503 with a
  human sentence rather than letting the instance find its own limit.
- **The wall gets cheaper.** `/ws/wall` sends every room's frames to every
  subscriber (`app.py:971`). At 50 rooms and 10 Hz that is 500 messages a
  second per socket, to draw thumbnails that update faster than a screen can
  show. Downsample the wall to about 2 Hz and cap the number of wall sockets.
  The full-rate feed stays on the room socket, where the viewer is.
- **`ARENA_SERVICE_TOKEN` never crosses the internet.** The specialists reach
  the arena over `127.0.0.1`. It is now a token that only ever travels inside
  one instance.
- **`ARENA_PUBLIC_URL` derives itself** from `X-Forwarded-Proto` and `Host` when
  unset. Cloud Run does not tell a service its own URL before the first deploy,
  so hardcoding it means deploying twice: once to learn the hostname, once to
  set it. Deriving it removes that entirely, and the QR codes are right on the
  first deploy.

## How it is deployed

Multi-container services cannot be expressed on the `gcloud run deploy` command
line. The service is a checked-in `deploy/service.yaml`, applied with
`gcloud run services replace`, which makes the topology reviewable in a diff
rather than living in somebody's shell history.

Four settings in it are load-bearing and easy to get wrong:

- **`--timeout=3600`.** A WebSocket is a request as far as Cloud Run is
  concerned, and the default request timeout is 300 seconds. A match is 180
  seconds plus a lobby plus a huddle, so the default would cut the room socket
  mid-match and it would look like a network fault. 3600 is the maximum.
- **`--no-cpu-throttling`.** Cloud Run throttles CPU outside a request by
  default. The watchdog sweep (`app.py:961`), the chain and the bus all run
  between requests. Throttled, a room with a quiet host is abandoned late and a
  shout in flight stalls.
- **`min-instances: 1`, `max-instances: 1`.** `min=1` is the always-on
  requirement and it is also what pays for the in-process bus: no cold start
  means no window where the instance does not exist. `max=1` is the correctness
  constraint from the table above, not a cost decision, and it should carry a
  comment in the yaml saying so, because it is exactly the line a future reader
  will raise to 10 to "fix" a load problem.
- **`container-dependencies`.** The arena is the ingress container and starts
  last, after the captain and then the coach, so its first request cannot land
  on a coach that is not listening yet.

Concurrency stays at the default 80 rather than being raised: with one instance
and `max=1` it only governs how many requests Cloud Run will hand over at once,
and 80 is comfortably above 50 phones whose traffic is almost entirely
long-lived sockets rather than requests.

Three containers means three images: the arena's (which also carries the built
pitch), the coach's and the captain's. The pitch is built in the arena image's
build stage, so `npm` is not in the runtime layer.

`/health` already exists and becomes the arena's startup and liveness probe.
The coach and the captain need startup probes on their own ports, or a failed
coach reads as a healthy service that answers every shout with an error.

## Local development

Native Postgres is the default path and Docker is the fallback, per the user's
setup.

Homebrew's `postgresql@18` is keg-only and its binaries carry a `-18` suffix
that is not on `PATH`, which is worth knowing because it decides how the
database gets created: **in Python, through psycopg**, not by shelling out to
`createdb`. The arena connects to the `postgres` maintenance database, creates
`arena` if it is missing, and runs `init_db`. That works the same whether
Postgres came from Homebrew, from Docker, or from Cloud SQL.

- `ARENA_DB` becomes a libpq connection string. Locally `postgresql:///arena`
  over the Unix socket, which needs no password and no port.
- Tests use `postgresql:///arena_test`, created and dropped by a fixture.
- `compose.yaml` pinned to `postgres:18` for anyone without a local install.
- `run.sh` does a TCP or socket preflight and prints how to start Postgres if
  it cannot connect, rather than letting uvicorn fail with a traceback.
- `dugout/app.py:37`'s `GAME_SERVICES` health map assumes every service is on
  localhost. When `ARENA_URL` points at the deployment, the arena's entry must
  follow it.

## Testing

Beyond the existing suites passing against Postgres:

1. **The seq race.** Fire four concurrent PATCHes at one room and assert four
   distinct sequence numbers and no `IntegrityError`. This is the test that
   guards the "keep the single connection" decision, and it is the test that
   must be made to pass before any future pool is introduced.
2. **`caused_by` across a shout.** A shout, four specialist PATCHes, and every
   resulting profile change carries the shout's id.
3. **Rollout overlap.** Start a second arena against the same database while a
   match is live, and assert the live room is not abandoned. This is what
   `last_heard_at` exists for.
4. **The injury loop end to end**, in the deployed shape: a specialist calls
   `report_injury`, and the browser polling `/player_state/` toasts it. Locally
   this means running the coach with its `PLAYER_STATE_DIR` pointed at a
   directory the arena also serves.
5. **Status checks in a match.** Open a room, play it, and assert no
   `/run_sse` traffic. Open the workshop and assert there is.
6. **A load rehearsal.** `fake_host.py` already exists to drive rooms without a
   browser. Run 50 of them against the deployed service and watch CPU, the
   socket count and the p99 on the room socket.

## What this does not solve

Stated plainly, because each one is a real cost of the chosen shape:

- **Every deploy drops every live match.** `max=1` means the new instance
  replaces the old one, and a host tab's socket dies with it. The pitch
  reconnects and the log replays from `?since=`, but the physics tab has to be
  the same tab. Deploy between matches.
- **A crash does the same**, and there is no second instance to take over. The
  database survives; the live matches do not.
- **8 vCPU is a hard ceiling.** The measured need is far below it, but the day
  it is not, the answer is the distributed-state work described above, and it
  is a real project rather than a config change.
- **Vertex quota is still unmeasured.** Gating the status checks removes the
  robot traffic, but the human shouts at 50 rooms still need a number from the
  project's actual quota, and `ARENA_CHAIN_LIMIT` set from it. Until somebody
  reads that number, `LIMIT = 4` is a guess that happens to be conservative.
- **The dugout stays local.** The showcase's Antigravity agent needs `agy login`
  and an unrestricted shell. It points at the deployed arena over `ARENA_URL`
  and carries `ARENA_SERVICE_TOKEN`, which is the one place that token does
  cross the internet. It should be rotated after an event.
