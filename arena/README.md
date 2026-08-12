# Arena

Rooms, seats, profiles and the live match bus. Runs on **:8003** beside the
pitch (:5173), the coach (:8000), the captain (:8001) and the dugout (:8002).

It owns everything that used to be global - who is playing, which match they
are in, what happened in it, and what their players' attributes are - so more
than one person can play at once.

## Running it

```bash
cd arena
./run.sh                 # syncs, then serves on :8003; HOST and PORT override
```

```bash
uv run pytest
```

## Environment

| Variable | Default | What it does |
|---|---|---|
| `ARENA_DB` | `arena/arena.db` | The SQLite file. Tests point it at a throwaway path. |
| `ARENA_EMAIL_SALT` | `arena-dev-salt` | Salts the email hash. Keeps a literal default on purpose: change it and every returning player loses their history. |
| `ARENA_SECRET` | random per start | Signs session cookies. Unset means sessions do not survive a restart - set it in anything longer-lived than a demo. |
| `ARENA_SERVICE_TOKEN` | unset | Lets a server-side caller patch profiles without a phone session. Unset refuses every such call. The specialist agents need the same value. |
| `ARENA_PUBLIC_URL` | `http://localhost:8003` | What the QR codes encode. A phone on the venue wifi cannot reach the laptop's loopback, so a real event sets this to the machine's LAN name or a tunnel. |
| `ARENA_PITCH_URL` | `http://localhost:5173` | Where the pitch is served from. The big screen frames it. |

## Pages

| Path | What it is |
|---|---|
| `/arena` | The big screen: a room to scan into, then the match at the size of the room |
| `/join/{code}` | Where a scanned QR lands - name, email, dugout, opening stance |
| `/play?room={code}` | The phone's dugout: the relay, the score, and the shout chips |

## Endpoints

| Method | Path | Who |
|---|---|---|
| GET | `/health` | anyone |
| GET | `/api/venue` | anyone - where the pitch and the public address are |
| POST | `/api/players` | anyone - name and email in, session cookie out |
| POST | `/api/rooms` | anyone; the response is the only place the host token appears |
| GET | `/api/rooms/{code}` | anyone |
| GET | `/api/rooms/{code}/me` | a phone with a session - which dugout is mine |
| GET | `/api/rooms/{code}/events` | anyone; `?since=` replays only what was missed |
| GET | `/api/rooms/{code}/qr.svg` | anyone |
| POST | `/api/rooms/{code}/seats/{team}` | a phone with a session |
| POST | `/api/rooms/{code}/seats/{team}/ready` | that dugout's manager |
| POST | `/api/rooms/{code}/shout` | that dugout's manager, once the match is live |
| POST | `/api/rooms/{code}/start` | anyone seated in the match |
| GET | `/api/philosophies`, `/api/presets` | anyone |
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
