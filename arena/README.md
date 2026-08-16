# Arena

Rooms, seats, profiles and the live match bus. Runs on **:8003** beside the
pitch (:5173), the coach (:8000), the captain (:8001) and the dugout (:8002).

It owns everything that used to be global - who is playing, which match they
are in, what happened in it, and what their players' attributes are - so more
than one person can play at once.

## Running it

Needs a Postgres to talk to - `brew services start postgresql@18`, or your
platform's equivalent. The arena makes its own database.

```bash
cd arena
./run.sh                 # syncs, then serves on :8003; HOST and PORT override
```

```bash
uv run pytest
```

## Environment

`cp .env.example .env` and edit it, or export the same names in the shell. The
shell wins where both say something, which is what lets one exported
`ARENA_SERVICE_TOKEN` cover all four processes. `HOST` and `PORT` are read by
`run.sh` rather than by the arena, so they only work from the shell.

| Variable | Default | What it does |
|---|---|---|
| `ARENA_ENV` | unset | Set to `production` and the arena refuses to start without the three secrets, rather than warning and carrying on with defaults. |
| `ARENA_DB` | `postgresql:///arena` | The Postgres connection string. Tests point it at a throwaway database. |
| `ARENA_EMAIL_SALT` | `arena-dev-salt` | Salts the email hash. Keeps a literal default on purpose: change it and every returning player loses their history. Production refuses to start without it. |
| `ARENA_SECRET` | random per start | Signs session cookies. Unset means sessions do not survive a restart - set it in anything longer-lived than a demo. Production refuses to start without it. |
| `ARENA_SERVICE_TOKEN` | unset | Lets a server-side caller patch profiles without a phone session. Unset refuses every such call. The specialist agents need the same value. Production refuses to start without it. |
| `ARENA_PUBLIC_URL` | derived from request | What the QR codes encode. Unset, it is worked out from the request's forwarded headers - Cloud Run does not tell a service its own hostname before the first deploy. Set it explicitly once the service has a name, or for a tunnel or a LAN address. Set it before anybody prints `/poster`: a screen can be reloaded, a sheet on a wall has to be reprinted. |
| `ARENA_PITCH_DIR` | unset | Where the built pitch bundle is, when the arena is the thing serving it. Set it locally too if a grounds is running: the page a grounds opens is this arena's `/pitch/host.html`, not Vite's, so an arena with this unset answers 404 and no match can be played. Build it with `PITCH_BASE=/pitch/`, which is what makes the page ask for the bundle where the arena serves it. |
| `ARENA_PITCH_URL` | `http://localhost:5173` | Where the pitch is served from. The big screen frames it. |
| `ARENA_COACH_URL` | `http://127.0.0.1:8000` | The ADK server a shout is carried to. |
| `ARENA_COACH_APP` | `agents` | The application name `adk web .` registers for the package beside it. |
| `ARENA_COACH_IDLE_SECONDS` | `90` | How long one hop of the chain may go quiet. The slowest specialist sets it. |
| `ARENA_CHAIN_LIMIT` | `4` | How many shouts may be talking to Gemini at once, across every room. The quota belongs to the venue, not to a room. Sizing it is arithmetic - see below. |
| `ARENA_CHAIN_SECONDS` | `150` | The whole chain, slot to huddle. A match is three minutes long. |
| `ARENA_MAX_LIVE_ROOMS` | `120` | How many matches may be live at once. When reached, opening another room is a 503 saying the venue is full - wait for a match to finish. |
| `ARENA_WALL_HZ` | `2` | How often one room's tile may redraw on the wall. A host reports at 10 Hz because its match needs it; fifty thumbnails at that rate is five hundred messages a second down every wall socket. Whoever is watching one match still gets every frame, on the room socket. Zero or less turns the thinning off rather than the wall. |
| `ARENA_MAX_WALL_SOCKETS` | `60` | How many big screens may watch the wall at once. Sized above the spec's 50 rooms, because `/arena` opens a wall socket on every screen that hosts one, and below `ARENA_MAX_LIVE_ROOMS`, because the cost is rooms x `ARENA_WALL_HZ` x screens. One beyond the cap is accepted and then closed with code 4429 and the reason `too many screens are watching the wall`: it loses its filmstrip and keeps its match. |

## Sizing `ARENA_CHAIN_LIMIT`

The shipped `4` is a deliberately conservative floor rather than a measured
number: it is small enough to sit inside any Vertex quota this venue is likely
to be given, and nobody has yet read a real one. Once you have a project, the
limit is arithmetic and the whole of it is below.

**One shout is ten Gemini calls, not one.** Counted from `arena/chain.py` and
`game/agents/`, all of them on `GEMINI_FLASH_LITE` - `GEMINI_FLASH` is defined
in `game/agents/constants.py` and used nowhere, so this needs one model's quota
and not two:

| Where | Calls | Why |
|---|---|---|
| `ManagerAgent` (`agent.py`) | 1 | Reads the shout and transfers to the captain. It is told not to answer one itself. |
| The four specialists (`specialist_agents/*.py`) | 4 to 12 | Each is one call to choose the `update_profile` arguments and a second to say its 3-5 words once the tool has answered. A specialist the shout does not touch skips the tool and costs one; one that also fires `report_injury` or `request_substitution` costs three, or four if it takes them in separate turns. |
| `SynthesisCaptain` (`captain.py`) | 1 | Merges the four replies into the huddle JSON. |

Six at the floor, **ten in the ordinary case** where every specialist patches
its profile, and about fourteen if all four also report a condition. About,
because neither end is hard: a specialist that fires `update_profile`,
`report_injury` and `request_substitution` in three separate turns costs four
rather than three, and a model that emits two function calls in one response
costs two. `RemoteA2aAgent` and `ParallelAgent` add none of their own: one is a
proxy over HTTP and the other is a fan-out.

**The formula.**

```
ARENA_CHAIN_LIMIT = Q / (C x R)

Q  requests a minute the project's quota allows for the model
C  Gemini calls in one chain: 10 typical, 6 floor, about 14 busy
R  chains one slot turns over in a minute, 60 / chain seconds - about 2
```

`scoring.py`'s `EFFECTIVE_WINDOW_SECONDS` is where the chain's duration is
written down: 30 to 60 seconds to answer, which is the only measured figure
anyone has. R takes the fast end, 60 / 30, because a chain that answers quickly
turns its slot over more often and so spends more quota a minute - the fast end
is the safe end for this arithmetic. `ARENA_CHAIN_SECONDS` is a timeout and is
no evidence of a duration.

`C x R` is what one slot costs a minute: about 20 requests. The limit is per
instance, which is per venue only for as long as there is one instance - and
`maxScale: "1"` asks for that rather than enforcing it, so leave the headroom
that a second container briefly doubling this needs. See `deploy/README.md`.

**Reading Q.** `PROJECT` is the one `deploy/README.md` exports; the line below
takes it from the configured project for a reader who arrives here first, since
an unset one asks about a consumer called `projects/` and is answered:

```bash
PROJECT=$(gcloud config get-value project)
gcloud alpha services quota list \
    --service=aiplatform.googleapis.com \
    --consumer="projects/$PROJECT" \
    --filter="metric~generate_content_requests_per_minute_per_project_per_base_model" \
    --format=yaml
```

`~` rather than `:`. The filter runs client-side over metric names that are
slash-and-underscore paths, and a word-match against a token like that can come
back with nothing, which reads as a project with no quota rather than as a
filter that missed. A regex match cannot fail that way. If the answer is empty
anyway, drop the `--filter` and look at the whole list before believing it.

Take `effectiveLimit` from the bucket whose `dimensions.base_model` is the
model in `game/agents/constants.py`, and whose region is the one the chain
calls: `global`, per `service.yaml`, rather than `$REGION`. The same numbers
are under IAM & Admin > Quotas in the console if the alpha component is not
installed. A fresh project is often given the smallest limit Vertex hands out,
so read it rather than assume the generous case.

**Worked example.** Q = 200 a minute:

```
200 / (10 x 2) = 10
```

Ten slots would spend the whole 200 with nothing left over, and a hop that
fails and retries then spends quota the arithmetic has already promised to
somebody else. Take eight. The shipped 4 asks for `4 x 10 x 2 = 80` a minute in
the ordinary case and `4 x 14 x 2 = 112` with every specialist reporting a
condition, which is why it is safe to leave alone until somebody has run the
command: it costs a manager a place in the queue and it costs the venue
nothing. Once you have the number, set `ARENA_CHAIN_LIMIT` in `service.yaml`
and write the Q it came from in a comment beside it, because the next person
cannot tell a measured 8 from another guess.

## Pages

| Path | What it is |
|---|---|
| `/arena` | The big screen: a room to scan into, then the match at the size of the room |
| `/join/{code}` | Where a room's QR lands - name, email, dugout, opening stance |
| `/play?room={code}` | The phone's dugout: the relay, the score, and the shout chips |
| `/poster` | The sheet for the wall. Open it on any screen and print it: one code, printed once, that is not about any one room |
| `/scan` | Where the printed code lands. A phone the venue knows goes to `/home`, everybody else to `/register` |
| `/register` | A name, an optional address, and that is the whole of registering |
| `/home` | A manager's own page: the match they walked away from, the rooms open now, the board |

## Endpoints

| Method | Path | Who |
|---|---|---|
| GET | `/health` | anyone - 200 while the watchdog is still turning, 503 once its last completed sweep is older than `HEALTH_STALE_SECONDS`. A sweep only completes if the event loop reached it and the database answered, so one reading covers both. This is what Cloud Run's liveness probe restarts the instance on; it asks the database nothing of its own, because a probe that can hang is worse than no probe |
| GET | `/api/venue` | anyone - where the pitch and the public address are |
| POST | `/api/players` | anyone - name and email in, session cookie out |
| GET | `/api/players/available` | anyone - is this name free, and if not, whose spelling of it |
| GET | `/api/players/me` | a phone with a session - who it is, and any seat it left |
| GET | `/api/rooms/open` | anyone - the rooms still waiting for a manager |
| GET | `/qr.svg` | anyone - the venue's code, the one on the printed sheet |
| POST | `/api/rooms` | anyone; the response is the only place the host token appears |
| GET | `/api/rooms/{code}` | anyone |
| GET | `/api/rooms/{code}/me` | a phone with a session - which dugout is mine |
| GET | `/api/rooms/{code}/events` | anyone; `?since=` replays only what was missed |
| GET | `/api/rooms/{code}/qr.svg` | anyone |
| POST | `/api/rooms/{code}/mode-request` | a phone with a session - asks the screen to turn a waiting room; the room does not move |
| POST | `/api/rooms/{code}/seats/{team}` | a phone with a session |
| POST | `/api/rooms/{code}/seats/{team}/ready` | that dugout's manager |
| POST | `/api/rooms/{code}/shout` | that dugout's manager, once the match is live; in `WRKS`, a caller with `X-Arena-Service` |
| POST | `/api/rooms/{code}/start` | anyone seated in the match |
| GET | `/api/philosophies`, `/api/presets` | anyone |
| GET | `/api/attributes` | anyone - every role's shipped values and the band each may move in |
| GET | `/api/rooms/{code}/teams/{team}/profiles` | anyone |
| GET | `/api/rooms/{code}/teams/{team}/profiles/{role}` | anyone |
| PATCH | `/api/rooms/{code}/teams/{team}/profiles/{role}` | that dugout's manager, or a caller with `X-Arena-Service` |
| WS | `/ws/rooms/{code}` | anyone may listen; only the host token may drive |
| WS | `/ws/wall` | anyone |

A `PATCH` body is `{"changes": {attribute: number}, "reason": str, "actor": str}`.
A refusal is `422` with `{"detail": {"problems": [...]}}` - every reason at
once, because the caller is often a language model correcting itself.

## The host

One client per room advances physics: the tab that opened the room, holding
the token `POST /api/rooms` returned. It is the only sender the room socket
listens to, and it sends two kinds of message.

```json
{"type": "host.state", "payload": {
  "score": [2, 1], "clock": 102, "ball": [0.55, 0.38],
  "blue": [[0.13, 0.49], ...], "red": [[0.87, 0.49], ...]}}

{"type": "host.event", "kind": "goal", "match_ms": 78400,
 "payload": {"team": "blue", "score": [2, 1]}}
```

Positions are fractions of the pitch, not pixels, so one frame draws a phone's
thumbnail, a tile on the wall and a full-size viewer. State is republished and
forgotten; events are appended to the room's gapless log, which is what
scoring is later computed from. `full_time` also closes the room.

The host is trusted for physics and for nothing else. It cannot say who won.

## The workshop room

The arena opens one room for itself at startup, code `WRKS`, unranked. It is
where the dugout tunes profiles with nobody sitting in a dugout seat, and it
is the room the pitch renders when it is opened with no `?room=`.

It never kicks off and never ends, so the seat-and-live rule that governs
shouting has nobody to apply to. A caller holding `ARENA_SERVICE_TOKEN` may
shout there instead, always as blue and always under the name `Antigravity` -
the manager is in a chat window, and the agent is the one on the touchline.
That exemption is for `WRKS` and nowhere else: the same token shouting into a
stranger's match is refused like any passer-by.
