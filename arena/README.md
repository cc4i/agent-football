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
| `ARENA_MIRROR_DIR` | unset | Temporary: copies the workshop room's profiles into the pitch's `player_state/` files, which it still polls. Deleted in step 3. |

## Endpoints

| Method | Path | Who |
|---|---|---|
| GET | `/health` | anyone |
| POST | `/api/players` | anyone - name and email in, session cookie out |
| POST | `/api/rooms` | anyone |
| GET | `/api/rooms/{code}` | anyone |
| POST | `/api/rooms/{code}/seats/{team}` | a phone with a session |
| POST | `/api/rooms/{code}/seats/{team}/ready` | that dugout's manager |
| POST | `/api/rooms/{code}/start` | anyone seated in the match |
| GET | `/api/rooms/{code}/teams/{team}/profiles` | anyone |
| GET | `/api/rooms/{code}/teams/{team}/profiles/{role}` | anyone |
| PATCH | `/api/rooms/{code}/teams/{team}/profiles/{role}` | that dugout's manager, or a caller with `X-Arena-Service` |
| WS | `/ws/rooms/{code}` | anyone may listen; only the host token may drive |
| WS | `/ws/wall` | anyone |

A `PATCH` body is `{"changes": {attribute: number}, "reason": str, "actor": str}`.
A refusal is `422` with `{"detail": {"problems": [...]}}` - every reason at
once, because the caller is often a language model correcting itself.

## The workshop room

The arena opens one room for itself at startup, code `WRKS`, unranked. It is
where the dugout tunes profiles with nobody sitting in a dugout seat.
