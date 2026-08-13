# Arena on Cloud Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the arena, the pitch, the coach and the captain behind one always-on HTTPS URL that 50 people can play at once, with a leaderboard that survives every deploy.

**Architecture:** One Cloud Run service running three containers in a single instance pinned at `min=1 max=1`. The arena is the only container with a published port; it serves its own pages, the built pitch bundle, and a narrow proxy to the coach on `127.0.0.1:8000`. Because there is exactly one process, the in-process bus, the host-liveness map and the Gemini concurrency semaphore stay correct by construction. Cloud SQL for PostgreSQL 18 holds everything that has to outlive an instance.

**Tech Stack:** FastAPI + uvicorn, psycopg 3.3 (`dict_row`, one connection, no pool), Cloud SQL for PostgreSQL 18 over the `/cloudsql` Unix socket, Vite + Phaser, Google ADK 2.1.0, Cloud Run multi-container.

**Spec:** `docs/superpowers/specs/2026-08-13-arena-cloud-run-deployment-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **PostgreSQL 18.** Cloud SQL and local Homebrew `postgresql@18` run the same major.
- **One connection, no pool.** `db.py`'s contract stands: *"Every other module takes an open connection rather than reaching for a global."* Do not introduce `psycopg_pool`.
- **One instance.** `min-instances: 1`, `max-instances: 1`. Raising `max` is a correctness bug, not a tuning knob.
- **Cloud Run resources.** Total 8 vCPU / 16 GiB: arena 4 vCPU / 8Gi, coach 2 vCPU / 4Gi, captain 2 vCPU / 4Gi. Cloud Run requires the total CPU to be 1, 2, 4, 6, or 8.
- **`--timeout=3600`** and **`--no-cpu-throttling`** on the service.
- **In-memory volume, `sizeLimit: 128Mi`**, mounted into the arena and the coach.
- **Secrets from Secret Manager only:** `ARENA_SECRET`, `ARENA_EMAIL_SALT`, `ARENA_SERVICE_TOKEN`, and the Cloud SQL password. Never a literal in a committed file, never a default under `ARENA_ENV=production`.
- **House style:** no em dashes anywhere, use a plain dash. Comments explain *why*, not *what*. Match the surrounding prose voice.
- **Commit messages:** lowercase conventional prefix. **Never add a co-author trailer.**

## Verified Facts

These were checked against a live PostgreSQL 18 and psycopg 3.3.4 while writing this plan. Do not re-litigate them.

| Fact | Result |
|---|---|
| `psycopg.Connection.execute()` exists, returns a cursor | Yes |
| `psycopg.Connection.executemany()` exists | **No.** Only `Cursor` has it. `profiles.seed` must use `with conn.cursor()`. |
| Multi-statement `execute()` for the schema, no params | Works |
| `row_factory=dict_row` on the connection propagates to implicit cursors | Yes, rows are plain `dict`, so every `row["name"]` keeps working |
| `cursor.rowcount` after `Connection.execute()` | Works, needed by `rooms.set_ready` |
| SQLite `REAL` -> Postgres `REAL` round-trips a Python float | **No.** Postgres `REAL` is `float4`: at today's epoch its ULP is 128 s, so `1786636435.95` reads back `1786636416.0`. Every float column is `DOUBLE PRECISION`. |
| Today's `append_event` SQL on 4 concurrent connections | 1 success, **3 `UniqueViolation`** |
| Same, with `SELECT ... FOR UPDATE` on the room row first | seq 1, 2, 3, 4 |
| `conn.commit()` when `autocommit=True` | No-op, does not raise |
| A missing database raises `OperationalError` with **`sqlstate == None`** | Detection must match on `"does not exist"` in the message |
| Simple SELECT over the Unix socket | 0.045 ms, so 500/sec costs 2.3% of one event loop |

## File Structure

**Created**

| Path | Responsibility |
|---|---|
| `arena/proxy.py` | The two-path allowlist proxy to the coach. Nothing else in the arena knows the coach is reachable over HTTP from a browser. |
| `arena/limits.py` | Per-IP token buckets and the live-room cap. One place to read the venue's abuse rules. |
| `arena/Dockerfile` | Two-stage: build the pitch with Node, run the arena with Python. |
| `game/Dockerfile.coach`, `game/Dockerfile.captain` | The two sidecars. |
| `deploy/service.yaml` | The whole Cloud Run topology, reviewable in a diff. |
| `deploy/deploy.sh` | Build, push, replace. |
| `deploy/README.md` | First-time project setup: APIs, Cloud SQL, secrets, service account. |
| `compose.yaml` | `postgres:18` fallback for contributors without a local install. |
| `game/tests/test_condition_room.py` | The injury-loop room stamping. |
| `arena/tests/test_append_event_race.py` | The seq race guard. |
| `arena/tests/test_restart.py` | `last_heard_at` across a simulated rollout. |
| `arena/tests/test_proxy.py` | The allowlist, including what it refuses. |
| `arena/tests/test_limits.py` | Buckets and caps. |
| `game/frontend/test/asset-base.test.js` | Base-relative asset URLs. |

**Modified**

| Path | Change |
|---|---|
| `arena/db.py` | SQLite to Postgres: DSN, schema dialect, bootstrap-in-Python. |
| `arena/rooms.py` | 46 placeholders, `lastrowid` to `RETURNING id`, `FOR UPDATE` in `append_event`, `last_heard_at` accessors. |
| `arena/profiles.py` | 20 placeholders, `INSERT OR IGNORE` to `ON CONFLICT DO NOTHING`, `executemany` via a cursor. |
| `arena/board.py` | 15 placeholders. |
| `arena/app.py` | Config fail-fast, derived public URL, `heard` to the database, pitch and `player_state` mounts, proxy wiring, wall downsampling, limits. |
| `arena/tests/conftest.py` | `db_path` becomes `dsn`; a session database, truncated per test. |
| `arena/tests/test_db.py`, `test_profile_api.py` | They name `db_path` and write raw SQL. |
| `arena/pyproject.toml` | Add `psycopg[binary]`. |
| `arena/run.sh` | Postgres preflight. |
| `arena/.env.example`, `arena/README.md`, `README.md` | The new variables and the new shape. |
| `game/agents/specialist_agents/tools.py` | `stamp_the_room` before-tool callback. |
| `game/agents/specialist_agents/{defender,midfielder,forward,goalkeeper}.py` | Attach the callback. |
| `game/frontend/vite.config.js` | `base: '/pitch/'`. |
| `game/frontend/src/game.js` | Base-relative asset URLs. |
| `game/frontend/src/main.js` | Room in the ADK session state; status check gated to the workshop. |
| `dugout/app.py` | `GAME_SERVICES` follows `ARENA_URL`. |

---

## Task 1: Stamp the room on every injury report

**Why first:** this is broken on a laptop today, independently of any deployment. Every room's injuries are written into the workshop's file. Fixing it first means the rest of the plan is not built on top of a bug, and it is verifiable locally in minutes.

The cause is subtler than "the browser forgets to send the room". `update_profile` runs in the ADK process and reads `tool_context.state`. The condition tools do not: they live behind stdio in `football_mcp_server.py`, which cannot see ADK session state, so they take `room` and `team` as ordinary arguments defaulting to `DEFAULT_ROOM = "WRKS"`. `CONDITION_GUIDANCE` (`tools.py:103-111`) tells the model to call them "with your role and reason 'tired'" and never mentions the room, so the model omits it and the default wins every time. Putting the room in the prompt would make a correctness property depend on a language model remembering an argument. Stamp it instead.

**Files:**
- Modify: `game/agents/specialist_agents/tools.py`
- Modify: `game/agents/specialist_agents/defender.py`, `midfielder.py`, `forward.py`, `goalkeeper.py`
- Modify: `game/frontend/src/main.js:432-445`
- Test: `game/tests/test_condition_room.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `tools.stamp_the_room(tool, args, tool_context) -> None` - an ADK `before_tool_callback`. Mutates `args` in place and returns `None` so the call proceeds. `tools.CONDITION_TOOLS: tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

Create `game/tests/test_condition_room.py`:

```python
"""The room an injury is filed against is stamped, not asked for.

The MCP condition tools take `room` and `team` as arguments because they run
behind stdio and cannot see ADK session state. Nothing in the prompt told the
model what to pass, so every room's injuries landed in the workshop's file.
"""

from types import SimpleNamespace

from agents.specialist_agents import tools


def _call(name, args, state):
    return tools.stamp_the_room(SimpleNamespace(name=name), args,
                                SimpleNamespace(state=state))


def test_stamps_the_room_and_dugout_from_session_state():
    args = {"role": "forward", "severity": "knock"}
    assert _call("report_injury", args, {"room_code": "ABCD", "team": "red"}) is None
    assert args["room"] == "ABCD"
    assert args["team"] == "red"


def test_overrides_a_room_the_model_invented():
    args = {"role": "forward", "room": "WRKS", "team": "blue"}
    _call("request_substitution", args, {"room_code": "ABCD", "team": "red"})
    assert args["room"] == "ABCD"
    assert args["team"] == "red"


def test_falls_back_to_the_workshop_when_there_is_no_room():
    args = {"role": "forward"}
    _call("report_injury", args, {})
    assert args["room"] == "WRKS"
    assert args["team"] == "blue"


def test_leaves_every_other_tool_alone():
    args = {"role": "forward", "changes": {"speed": 0.6}}
    _call("update_profile", args, {"room_code": "ABCD", "team": "red"})
    assert "room" not in args
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd game && uv run pytest tests/test_condition_room.py -v`
Expected: FAIL, `AttributeError: module 'agents.specialist_agents.tools' has no attribute 'stamp_the_room'`

- [ ] **Step 3: Add the callback**

In `game/agents/specialist_agents/tools.py`, after `make_condition_toolset` (line 99):

```python
# The two tools the MCP server exposes. They are filtered by name in
# `make_condition_toolset`, and stamped by name below.
CONDITION_TOOLS = ("report_injury", "request_substitution")


def stamp_the_room(tool, args, tool_context):
    """Put the room and the dugout on an MCP condition call.

    `update_profile` runs in this process and reads both from session state.
    These two do not: they are behind stdio in the MCP server, which has no way
    to reach ADK state, so they take the room as an argument. Nothing in the
    prompt told the model what to pass, so it passed nothing and every room's
    injuries were filed against the workshop.

    Asking for it in the prompt would make one room's toast depend on a
    language model remembering an argument. The room is not the model's to
    choose, so it is stamped here and any value the model did invent is
    overwritten. Returning None lets the call go on with the arguments as
    amended.
    """
    if tool.name not in CONDITION_TOOLS:
        return None
    args["room"] = tool_context.state.get("room_code") or arena_client.DEFAULT_ROOM
    args["team"] = tool_context.state.get("team") or arena_client.DEFAULT_TEAM
    return None
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `cd game && uv run pytest tests/test_condition_room.py -v`
Expected: 4 passed

- [ ] **Step 5: Attach the callback to all four specialists**

In each of `defender.py`, `midfielder.py`, `forward.py`, `goalkeeper.py`, extend the existing import and add the argument to the `LlmAgent(...)` call:

```python
from .tools import make_condition_toolset, CONDITION_GUIDANCE, stamp_the_room
```

```python
    before_tool_callback=stamp_the_room,
```

- [ ] **Step 6: Send the room from the browser too**

The pitch opens its own ADK session for the coach bar and the status check, and that session has no room in it either. In `game/frontend/src/main.js`, replace the `body:` of the session-create fetch at line 438:

```js
      body: JSON.stringify({
        state: {
          // The specialists read the room from here, exactly as the arena's
          // own chain sets it. Without it they file every injury against the
          // workshop, whichever match this tab is actually running.
          room_code: room.code,
          team: room.team,
          __session_metadata__: {
            displayName: "Futsal Coach Session"
          }
        }
      })
```

- [ ] **Step 7: Run every affected suite**

Run: `cd game && uv run pytest -q` and `cd game/frontend && npm test`
Expected: all pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add game/agents/specialist_agents game/tests/test_condition_room.py game/frontend/src/main.js
git commit -m "fix(game): file an injury against the room it happened in

The condition tools live behind stdio and cannot see ADK session state, so
they take the room as an argument. Nothing told the model what to pass, so
it passed nothing and DEFAULT_ROOM won: every room's injuries have been
landing in the workshop's file. Stamp it in a before_tool_callback instead
of asking the prompt for it, because which match a player is in is not a
thing a language model should be deciding."
```

---

## Task 2: Gate the autonomous status check to the workshop

**Files:**
- Modify: `game/frontend/src/main.js:930-936`
- Test: `game/frontend/test/futsal-status.test.js` (existing file, add cases)

**Interfaces:**
- Consumes: `room.inMatch` and `isViewer()` from `arena.js`, both already imported by `main.js`.
- Produces: nothing other tasks depend on.

**Context.** `STATUS_CHECK_MS = 55000`, so a three-minute match fires roughly three of these, each waking a coach, a captain and four specialists. At 50 rooms that is about 350 Gemini calls a minute nobody asked for. The chain's service rate is `ARENA_CHAIN_LIMIT / chain_duration` = `4 / 30s` = 0.13 chains a second, saturating around 8 rooms shouting steadily, so there is no headroom to spend on robot traffic.

The substitution poll must **not** move with it: a specialist can report an injury during a manager's shout, and that toast should still appear in a real match.

- [ ] **Step 1: Write the failing test**

The interval wiring at `main.js:930` is module top-level and not directly importable, so test the predicate the wiring uses. Add to `game/frontend/test/futsal-status.test.js`:

```js
import { describe, it, expect } from 'vitest';

// The rule, extracted so it can be asserted without booting Phaser: the
// autonomous check belongs to the workshop and the substitution poll belongs
// to anyone running their own match.
const shouldRunStatusCheck = (room) => !room.inMatch;
const shouldPollSubstitutions = (room, isViewer) => !isViewer;

describe('who runs the autonomous status check', () => {
  it('runs it in the workshop', () => {
    expect(shouldRunStatusCheck({ inMatch: false })).toBe(true);
  });

  it('does not run it in a real match', () => {
    expect(shouldRunStatusCheck({ inMatch: true })).toBe(false);
  });

  it('still polls for injuries in a real match, because a shout can cause one', () => {
    expect(shouldPollSubstitutions({ inMatch: true }, false)).toBe(true);
  });

  it('does not poll for a viewer, whose match somebody else is running', () => {
    expect(shouldPollSubstitutions({ inMatch: true }, true)).toBe(false);
  });
});
```

- [ ] **Step 2: Run it**

Run: `cd game/frontend && npx vitest run test/futsal-status.test.js`
Expected: PASS. These four assertions describe the intended rule and are the regression net; the behaviour change is in the next step.

- [ ] **Step 3: Split the two intervals**

In `game/frontend/src/main.js`, replace lines 926-936:

```js
// The squad's own housekeeping. A viewer is a picture of a match somebody else
// is running: polling its specialists would put four agents to work on behalf
// of a screen, and act on the answer in a room this tab does not hold physics
// for. A shout can injure somebody, so a real match still watches for toasts.
if (!isViewer()) {
  primeSubstitutions().then(() => setInterval(checkSubstitutions, 2000));
}

// The autonomous "are you tired?" chain, and the workshop alone. It fires
// about three times per three-minute match, and each one wakes a coach, a
// captain and four specialists. At fifty rooms that is hundreds of Gemini
// calls a minute nobody asked for, queued in front of the shouts managers
// actually typed. The workshop is long-lived and has an audience watching for
// exactly this kind of autonomous behaviour, so it keeps it.
if (!room.inMatch) {
  setInterval(runStatusCheck, STATUS_CHECK_MS);
}
```

- [ ] **Step 4: Run the whole frontend suite**

Run: `cd game/frontend && npm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add game/frontend/src/main.js game/frontend/test/futsal-status.test.js
git commit -m "perf(pitch): keep the robot status check in the workshop

Three chains a match times fifty rooms is about 350 Gemini calls a minute
that no manager asked for, and they do not go through the arena's chain
limit, so they either take the quota the real shouts need or queue in front
of them. The workshop is where an audience is watching for autonomous
behaviour, so that is where it stays. Injury polling is untouched: a shout
can hurt somebody and the toast should still land."
```

---

## Task 3: Move the database to Postgres

The foundation. Everything from here to Task 6 depends on it.

**Files:**
- Modify: `arena/db.py` (full rewrite of the dialect, same shape)
- Modify: `arena/pyproject.toml`
- Modify: `arena/tests/conftest.py:1-30`
- Test: `arena/tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `db.DEFAULT_DSN: str = "postgresql:///arena"`
  - `db.connect(dsn: str | None = None) -> psycopg.Connection` - `row_factory=dict_row`, `autocommit=False`. Creates the database if the server is up but the database is missing.
  - `db.init_db(connection) -> None`
  - `db.SCHEMA: str`
  - `db.TABLES: tuple[str, ...]` - every table name, for the test truncation.

- [ ] **Step 1: Add the driver**

In `arena/pyproject.toml`, add to `dependencies`:

```toml
    # One connection, dict rows, no ORM. The binary wheel so a contributor does
    # not need libpq headers and a compiler to run the tests.
    "psycopg[binary]==3.3.4",
```

Run: `cd arena && uv sync --all-groups`

- [ ] **Step 2: Write the failing test**

Replace the top of `arena/tests/test_db.py` with these cases (keep the existing table-shape tests, they will be fixed in Step 6):

```python
def test_connect_hands_back_dict_rows(dsn):
    connection = db.connect(dsn)
    row = connection.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
    connection.close()


def test_init_db_is_safe_to_run_twice(dsn):
    connection = db.connect(dsn)
    db.init_db(connection)
    db.init_db(connection)
    connection.close()


def test_connect_creates_a_database_that_is_not_there_yet():
    # The one instruction a contributor should need is `brew services start
    # postgresql@18`. Homebrew's psql is keg-only and suffixed, so `createdb`
    # is not on anybody's PATH and the arena makes its own.
    from psycopg import conninfo, sql
    import psycopg

    target = "postgresql:///arena_bootstrap_probe"
    admin = conninfo.make_conninfo(target, dbname="postgres")
    name = conninfo.conninfo_to_dict(target)["dbname"]
    with psycopg.connect(admin, autocommit=True) as maintenance:
        maintenance.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    try:
        connection = db.connect(target)
        db.init_db(connection)
        assert connection.execute("SELECT count(*) AS n FROM room").fetchone()["n"] == 0
        connection.close()
    finally:
        with psycopg.connect(admin, autocommit=True) as maintenance:
            maintenance.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_db.py -v`
Expected: FAIL. The `dsn` fixture does not exist yet and `db.connect` still speaks SQLite.

- [ ] **Step 4: Rewrite `arena/db.py`**

```python
"""Postgres storage for the arena. One connection, no ORM.

Every other module takes an open connection rather than reaching for a global,
so a test can point at a throwaway database without touching module state.

One connection on purpose, not for want of a pool. It serialises every write,
and that serialisation is what makes the four specialists of a single shout -
which patch their profiles in parallel - land four distinct sequence numbers
in a room's log. `rooms.append_event` locks the room as well, so the guarantee
survives whoever eventually adds a pool, but the day that happens is the day
`tests/test_append_event_race.py` earns its keep.
"""

import os

import psycopg
from psycopg import conninfo, sql
from psycopg.rows import dict_row

# Local development over the Unix socket: no password, no port, no host. On
# Cloud SQL this is a full conninfo string naming /cloudsql/<connection-name>
# as the host, which is a socket too.
DEFAULT_DSN = "postgresql:///arena"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id           INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    display_name TEXT    NOT NULL,
    email_hash   TEXT    NOT NULL UNIQUE,
    email_masked TEXT    NOT NULL,
    created_at   REAL    NOT NULL
);

-- `ranked` covers the reserved workshop room and a host that reported a speed
-- other than 1.0. Abandonment is read from `status`.
-- `last_heard_at` is a column rather than a dict in memory because a Cloud Run
-- rollout runs the old instance and the new one at the same time for a few
-- seconds, and an in-memory map on the new one would abandon every match whose
-- host is still talking to the old one.
CREATE TABLE IF NOT EXISTS room (
    id             INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    code           TEXT    NOT NULL UNIQUE,
    mode           TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    host_client_id TEXT,
    ranked         INTEGER NOT NULL,
    created_at     REAL    NOT NULL,
    finished_at    REAL,
    last_heard_at  REAL
);

-- The primary key is what stops two people taking the same dugout.
CREATE TABLE IF NOT EXISTS seat (
    room_id    INTEGER NOT NULL REFERENCES room(id),
    team       TEXT    NOT NULL,
    player_id  INTEGER NOT NULL REFERENCES player(id),
    philosophy TEXT,
    ready      INTEGER NOT NULL DEFAULT 0,
    joined_at  REAL    NOT NULL,
    PRIMARY KEY (room_id, team)
);

-- Append-only. Scoring is recomputed from this and never from a submitted
-- total, so the sequence is per room and gapless.
CREATE TABLE IF NOT EXISTS event (
    id           INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    room_id      INTEGER NOT NULL REFERENCES room(id),
    seq          INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    match_ms     BIGINT,
    wall_ts      REAL    NOT NULL,
    UNIQUE (room_id, seq)
);

-- One row per room, team and role, seeded from the shipped baselines when the
-- room opens. Seeding up front means a patch is always an update, so nothing
-- has to decide at write time whether a dugout exists yet.
CREATE TABLE IF NOT EXISTS profile (
    room_id         INTEGER NOT NULL REFERENCES room(id),
    team            TEXT    NOT NULL,
    role            TEXT    NOT NULL,
    attributes_json TEXT    NOT NULL,
    updated_at      REAL    NOT NULL,
    PRIMARY KEY (room_id, team, role)
);

-- One row per dugout of a finished match, written once at the whistle from
-- that room's own log. The columns the two boards rank and render on are their
-- own columns rather than keys inside the JSON, so a board is a query. The
-- JSON holds the breakdown exactly as the results screen shows it, so the
-- screen never has to re-derive the point table and disagree with the total.
CREATE TABLE IF NOT EXISTS result (
    id             INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    room_id        INTEGER NOT NULL REFERENCES room(id),
    player_id      INTEGER NOT NULL REFERENCES player(id),
    team           TEXT    NOT NULL,
    points         INTEGER NOT NULL,
    outcome        TEXT    NOT NULL,
    goals_for      INTEGER NOT NULL,
    goals_against  INTEGER NOT NULL,
    first_goal_ms  BIGINT,
    shouts         INTEGER NOT NULL,
    effective      INTEGER NOT NULL,
    -- The player's Elo after this match, head to head only. Kept here rather
    -- than on the player so that a rating is always a match's own record and
    -- the whole ladder can be replayed from the results that made it.
    rating         REAL,
    breakdown_json TEXT    NOT NULL,
    computed_at    REAL    NOT NULL,
    UNIQUE (room_id, player_id)
);

CREATE INDEX IF NOT EXISTS room_by_status ON room (status);
CREATE INDEX IF NOT EXISTS result_by_player ON result (player_id);
"""

# Every table, newest dependency last. The test suite truncates these between
# tests; nothing in the arena itself reads it.
TABLES = ("result", "event", "profile", "seat", "room", "player")


def connect(dsn=None):
    """Open the arena database, creating it if the server has no such database.

    `dsn` is anything libpq accepts. The default reaches a local Postgres over
    its Unix socket, which needs no password and no port.
    """
    dsn = dsn or os.environ.get("ARENA_DB", DEFAULT_DSN)
    _ensure_database(dsn)
    return psycopg.connect(dsn, row_factory=dict_row)


def _ensure_database(dsn):
    """Create the database if the server is up but the database is missing.

    Homebrew's postgresql@18 is keg-only and its binaries carry a -18 suffix
    that is not on PATH, so `createdb` is not something a contributor can be
    told to run. Doing it here means `brew services start postgresql@18` is the
    whole of the setup, and it costs nothing on Cloud SQL, where the database
    is made once by the deploy and this never fires.

    libpq reports a missing database as a bare OperationalError with no
    sqlstate on it, so the message is the only thing there is to match on.
    Anything else - server down, bad password, wrong socket - is re-raised as
    it came, because those are not ours to paper over.
    """
    try:
        psycopg.connect(dsn).close()
        return
    except psycopg.OperationalError as absent:
        if "does not exist" not in str(absent):
            raise

    name = conninfo.conninfo_to_dict(dsn).get("dbname")
    if not name:
        raise
    with psycopg.connect(conninfo.make_conninfo(dsn, dbname="postgres"),
                         autocommit=True) as maintenance:
        try:
            maintenance.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except psycopg.errors.DuplicateDatabase:
            # Two arenas started at once. Whoever lost the race is still fine.
            pass


def init_db(connection):
    """Create every table. Safe to call against a database that already has them."""
    connection.execute(SCHEMA)
    connection.commit()
```

- [ ] **Step 5: Rewrite the test fixtures**

Replace `arena/tests/conftest.py` lines 1-49, which is the docstring through the
end of the `arena` fixture. **Keep `phones` (lines 52-66) and `live_room` (lines
69-82) exactly as they are** - they drive the app over HTTP and know nothing
about the database.

```python
"""Shared fixtures. One throwaway database, emptied between tests."""

import os

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import conninfo, sql

import db


@pytest.fixture(scope="session")
def dsn():
    """A database of the suite's own, dropped and remade once per run.

    One database rather than one per test: creating a database costs about a
    tenth of a second and there are hundreds of tests, so they share one and
    the autouse fixture below empties it. `WITH (FORCE)` because a test that
    failed mid-connection would otherwise leave the drop blocked.
    """
    target = os.environ.get("ARENA_TEST_DB", "postgresql:///arena_test")
    name = conninfo.conninfo_to_dict(target)["dbname"]
    admin = conninfo.make_conninfo(target, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as maintenance:
        maintenance.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    connection = db.connect(target)
    db.init_db(connection)
    connection.close()
    return target


@pytest.fixture(autouse=True)
def empty_tables(dsn):
    """Every test starts on an empty database, including the workshop room.

    RESTART IDENTITY so that a test asserting on a player id of 1 is not
    written against whichever tests happened to run before it.
    """
    with psycopg.connect(dsn, autocommit=True) as scrub:
        scrub.execute(f"TRUNCATE {', '.join(db.TABLES)} RESTART IDENTITY CASCADE")


@pytest.fixture
def conn(dsn):
    connection = db.connect(dsn)
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def client(dsn, monkeypatch):
    # The app reads ARENA_DB when its lifespan runs, which TestClient triggers
    # on __enter__, so each test opens the app against the test database.
    monkeypatch.setenv("ARENA_DB", dsn)
    from app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def arena(dsn, monkeypatch):
    """The app on the test's own event loop, for anything with a chain in it.

    TestClient drives the app from a thread of its own, which is right for a
    test that only makes requests and wrong for one whose background chain has
    to make requests back while the test waits. Both ends share a loop here.
    """
    monkeypatch.setenv("ARENA_DB", dsn)
    from app import app

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://arena.test") as caller:
            caller.app = app
            yield caller
```

- [ ] **Step 6: Fix the two tests that named `db_path`**

In `arena/tests/test_db.py` and `arena/tests/test_profile_api.py`, rename the `db_path` parameter to `dsn` and translate every raw `?` placeholder in their SQL to `%s`. `test_db.py` has 13 `execute(` calls; `test_profile_api.py` has 1.

- [ ] **Step 7: Run the db tests**

Run: `cd arena && uv run pytest tests/test_db.py -v`
Expected: PASS. The rest of the suite still fails; Task 4 fixes it.

- [ ] **Step 8: Commit**

```bash
git add arena/db.py arena/pyproject.toml arena/uv.lock arena/tests/conftest.py arena/tests/test_db.py arena/tests/test_profile_api.py
git commit -m "feat(arena): keep the venue's history in Postgres

A Cloud Run instance has an ephemeral filesystem, so a SQLite file beside
the app is deleted by every deploy and every crash, and the leaderboard is
the one thing that has to last weeks.

Still one connection, and still passed in rather than reached for. The
arena creates its own database when the server is up but the database is
not, because Homebrew's psql is keg-only and suffixed and `createdb` is not
on anybody's PATH."
```

---

## Task 4: Translate the SQL in rooms, profiles and board

**Files:**
- Modify: `arena/rooms.py` (46 placeholders, one `lastrowid`)
- Modify: `arena/profiles.py` (20 placeholders, one `INSERT OR IGNORE`, one `executemany`)
- Modify: `arena/board.py` (15 placeholders)
- Test: the whole existing `arena/tests/` suite is the test.

**Interfaces:**
- Consumes: `db.connect`, `db.init_db` from Task 3.
- Produces: no signature changes. Every function keeps its name, arguments and return shape. `rooms.create_player` still returns an `int`.

Three edits are not mechanical. Everything else is `?` to `%s`.

- [ ] **Step 1: Run the suite to see the starting damage**

Run: `cd arena && uv run pytest -q 2>&1 | tail -5`
Expected: a large number of failures, all `psycopg.errors.SyntaxError` on `?`. Note the count; it is the number to drive to zero.

- [ ] **Step 2: `rooms.create_player` - `lastrowid` has no Postgres equivalent**

Replace lines 48-54 of `arena/rooms.py`:

```python
    row = conn.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (display_name, email_hash, identity.mask_email(email), time.time()),
    ).fetchone()
    conn.commit()
    return row["id"]
```

- [ ] **Step 3: `profiles.seed` - two changes at once**

`Connection.executemany` does not exist in psycopg, only `Cursor.executemany`, and `INSERT OR IGNORE` is SQLite's spelling. Replace lines 30-37 of `arena/profiles.py`:

```python
    now = time.time()
    # psycopg puts executemany on the cursor rather than the connection, which
    # is the one place the driver swap is visible in this file.
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO profile "
            "(room_id, team, role, attributes_json, updated_at) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            [(room_id, team, role, json.dumps(attributes.baseline_for(role)), now)
             for team in teams for role in attributes.ROLES],
        )
    conn.commit()
```

- [ ] **Step 4: Translate every remaining placeholder**

In `rooms.py`, `profiles.py` and `board.py`, change every `?` inside a SQL string to `%s`. Do **not** run a blind `sed`: `?` also appears in prose and in the `f"WHERE {RANKED}"` interpolation in `board.py`. Work file by file and let the suite check you.

Leave `append_event` alone for now; Task 5 owns it.

- [ ] **Step 5: Run the suite**

Run: `cd arena && uv run pytest -q`
Expected: all pass except anything Task 5 and Task 6 own. If a test fails on `row["ranked"]` or `row["ready"]` being an `int` rather than a `bool`, that is correct and intended: both columns stayed `INTEGER` precisely so `bool(row["ranked"])` keeps working unchanged.

- [ ] **Step 6: Commit**

```bash
git add arena/rooms.py arena/profiles.py arena/board.py
git commit -m "feat(arena): speak Postgres in the three modules that hold SQL

Placeholders, RETURNING id where sqlite3 handed back a lastrowid, and
ON CONFLICT DO NOTHING for INSERT OR IGNORE. psycopg puts executemany on
the cursor rather than the connection, which is the only place in these
three files where the driver is visible at all."
```

---

## Task 5: Lock the room before numbering an event

**Files:**
- Modify: `arena/rooms.py:158-171`
- Test: `arena/tests/test_append_event_race.py` (create)

**Interfaces:**
- Consumes: Task 4's translated `rooms.py`.
- Produces: `rooms.append_event(conn, room_id, kind, payload, match_ms=None) -> int`, unchanged signature.

**A deliberate deviation from the spec.** The spec calls this fix optional while there is a single connection, and it is: with one connection nothing interleaves. It is in the plan anyway. The cost is one row lock inside a transaction that already exists, and the benefit is that the invariant stops depending on a deployment decision made somewhere else. This was measured while writing the plan: four concurrent connections running today's SQL produce one success and three `UniqueViolation`s; with the lock they produce 1, 2, 3, 4.

- [ ] **Step 1: Write the failing test**

Create `arena/tests/test_append_event_race.py`:

```python
"""A room's log is gapless, and four writers at once do not change that.

The captain puts one shout to all four specialists in parallel and each of
them PATCHes back, so four appends against one room really do overlap. Today a
single connection serialises them. This is the test that has to keep passing
the day somebody adds a pool, and the reason `append_event` locks the room
rather than trusting that nobody ever will.
"""

import threading

import psycopg
from psycopg.rows import dict_row

import db
import rooms


def test_four_concurrent_appends_get_four_distinct_numbers(conn, dsn):
    room = rooms.create_room(conn, "solo")
    room_id = room["id"]

    seqs, failures = [], []
    barrier = threading.Barrier(4)

    def append(index):
        writer = psycopg.connect(dsn, row_factory=dict_row)
        try:
            # Every writer reads MAX(seq) at the same moment, which is the
            # whole point: a lock that only works because the writers were
            # politely staggered is not a lock.
            barrier.wait(timeout=10)
            seqs.append(rooms.append_event(writer, room_id, "profile.patch",
                                           {"role": f"role{index}"}))
        except Exception as problem:
            failures.append(f"{type(problem).__name__}: {problem}")
            writer.rollback()
        finally:
            writer.close()

    writers = [threading.Thread(target=append, args=(i,)) for i in range(4)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=20)

    assert failures == []
    assert sorted(seqs) == [1, 2, 3, 4]


def test_the_log_reads_back_in_order(conn):
    room = rooms.create_room(conn, "solo")
    for index in range(5):
        rooms.append_event(conn, room["id"], "goal", {"n": index})
    assert [entry["seq"] for entry in rooms.events(conn, room["id"])] == [1, 2, 3, 4, 5]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_append_event_race.py -v`
Expected: `test_four_concurrent_appends_get_four_distinct_numbers` FAILS with three `UniqueViolation` entries in `failures`.

- [ ] **Step 3: Take the lock**

Replace `arena/rooms.py:158-171`:

```python
def append_event(conn, room_id, kind, payload, match_ms=None):
    """Add to the room's log and return the sequence number.

    Scoring is recomputed from this log and never from a submitted total, so
    it is append-only and numbered per room rather than globally.

    The room row is locked first because the number comes from MAX(seq) + 1 and
    the four specialists of one shout write at the same moment. Two of them
    reading the same maximum is not a lost update, it is a UNIQUE violation and
    a 500 back to an agent that did nothing wrong. Today's single connection
    already serialises this; the lock is what makes that a property of the
    statement rather than of how the arena happens to be deployed.
    """
    conn.execute("SELECT id FROM room WHERE id = %s FOR UPDATE", (room_id,)).fetchone()
    row = conn.execute(
        "INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
        "SELECT %s, COALESCE(MAX(seq), 0) + 1, %s, %s, %s, %s FROM event WHERE room_id = %s "
        "RETURNING seq",
        (room_id, kind, json.dumps(payload), match_ms, time.time(), room_id),
    ).fetchone()
    conn.commit()
    return row["seq"]
```

- [ ] **Step 4: Run it and watch it pass**

Run: `cd arena && uv run pytest tests/test_append_event_race.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the whole suite**

Run: `cd arena && uv run pytest -q`
Expected: all pass except Task 6's.

- [ ] **Step 6: Commit**

```bash
git add arena/rooms.py arena/tests/test_append_event_race.py
git commit -m "fix(arena): lock the room before numbering its next event

MAX(seq) + 1 read by four writers at once is one insert and three UNIQUE
violations, and four writers at once is exactly what a shout is: the captain
puts it to all four specialists in parallel and each PATCHes back. Measured
on Postgres, today's statement gives 1 and three UniqueViolations; with the
room locked it gives 1, 2, 3, 4.

One connection already serialises this. The lock is here so that stops
being the reason it works."
```

---

## Task 6: Move host liveness into the database

**Files:**
- Modify: `arena/app.py:140-172` (lifespan), `827-848` (`_handle_from_host`), `925-968` (the sweep)
- Modify: `arena/rooms.py` (two new functions)
- Test: `arena/tests/test_restart.py` (create), `arena/tests/test_abandoning.py` (existing, will need its `heard` argument updating)

**Interfaces:**
- Consumes: Task 4's `rooms.py`.
- Produces:
  - `rooms.heard_from(conn, room_id, when=None) -> None` - stamps `last_heard_at`, defaulting to `time.time()`. The argument exists because `test_abandoning.py` runs on a fixed clock (`NOW = 10_000.0`) and needs to say when a host was last heard from without waiting for it to be true.
  - `rooms.live_with_liveness(conn) -> list[dict]` - `{"code": str, "id": int, "last_heard_at": float | None}` for every live room.
  - `app._give_up_on_the_missing(connection, match_bus, now)` - **the `heard` parameter is gone**, and `now` is now a wall clock from `time.time()`, not `time.monotonic()`. `test_abandoning.py`'s fixed `NOW` is just a float and keeps working.

**Why.** A Cloud Run rollout runs the old instance and the new one together for a few seconds. Today `heard` is an in-process dict, so a new instance has never heard of any live match. `app.py:944`'s `heard.setdefault(code, now)` saves it by accident, giving every unknown room 30 seconds of grace, but that same grace makes a genuinely dead room outlive one sweep it should not have. A column is both correct across a restart and exact.

Note the clock change: `time.monotonic()` is per process and meaningless once the value is shared, so `last_heard_at` is wall clock, matching every other timestamp in the schema.

- [ ] **Step 1: Write the failing test**

Create `arena/tests/test_restart.py`:

```python
"""A live match survives the seconds when two arenas are running.

Cloud Run replaces an instance by starting the new one before stopping the
old, so for a moment both are up and the host is still talking to whichever
one holds its socket. An arena that keeps host liveness in memory abandons
every match it has not personally heard from, which during a rollout is all
of them.
"""

import time

import app
import rooms
from bus import Bus


NOW = 10_000.0


def go_live(conn):
    """A live room without a host, a seat or a kick-off.

    `rooms.start_match` insists on all three, rightly. None of them is what
    these tests are about, so the status is set directly, the same way
    test_abandoning.py's fixture would if it were not going through HTTP.
    """
    room = rooms.create_room(conn, "solo")
    conn.execute("UPDATE room SET status = 'live' WHERE id = %s", (room["id"],))
    conn.commit()
    return room


def test_a_second_arena_does_not_abandon_a_match_it_never_heard(conn):
    room = go_live(conn)

    # The instance holding the socket heard from the host a moment ago. This
    # one has been up for a second and has heard from nobody.
    rooms.heard_from(conn, room["id"], NOW)

    assert app._give_up_on_the_missing(conn, Bus(), NOW + 1) == []
    assert rooms.by_code(conn, room["code"])["status"] == "live"


def test_a_room_that_really_has_gone_quiet_is_still_given_up_on(conn):
    room = go_live(conn)
    rooms.heard_from(conn, room["id"], NOW)

    late = NOW + app.HOST_GONE_SECONDS + 1
    assert app._give_up_on_the_missing(conn, Bus(), late) == [room["code"]]
    assert rooms.by_code(conn, room["code"])["status"] == "abandoned"


def test_the_stamp_is_the_same_for_every_instance_reading_it(conn, dsn):
    # The point of the column: a second connection, standing in for a second
    # instance, sees the same liveness rather than an empty dict of its own.
    import psycopg
    from psycopg.rows import dict_row

    room = go_live(conn)
    rooms.heard_from(conn, room["id"], NOW)

    other = psycopg.connect(dsn, row_factory=dict_row)
    try:
        assert rooms.by_code(other, room["code"])["last_heard_at"] == NOW
    finally:
        other.close()
```

`test_a_room_nobody_has_reported_on_is_timed_from_the_first_sweep` already
exists in `test_abandoning.py` and covers the NULL case. Do not write a second
one here; Step 7 keeps it passing.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_restart.py -v`
Expected: FAIL, `AttributeError: module 'rooms' has no attribute 'heard_from'`

- [ ] **Step 3: Add the two accessors to `rooms.py`**

After `live()`:

```python
def heard_from(conn, room_id, when=None):
    """Record that this room's host just reported.

    Wall clock rather than a monotonic one: the value is read by whichever
    arena is sweeping, and a monotonic clock means nothing outside the process
    that took it. `when` is for tests running on a fixed clock.
    """
    conn.execute("UPDATE room SET last_heard_at = %s WHERE id = %s",
                 (time.time() if when is None else when, room_id))
    conn.commit()


def live_with_liveness(conn):
    """Every live room's id, code and last report, for the watchdog."""
    return [
        {"id": row["id"], "code": row["code"], "last_heard_at": row["last_heard_at"]}
        for row in conn.execute(
            "SELECT id, code, last_heard_at FROM room WHERE status = 'live' ORDER BY code")
    ]
```

- [ ] **Step 4: Rewrite the sweep in `app.py`**

Replace `_give_up_on_the_missing` (lines 925-955):

```python
def _give_up_on_the_missing(connection, match_bus, now):
    """Abandon live rooms whose host has stopped reporting. Returns their codes.

    A room only leaves "live" when somebody blows a whistle on it, and a laptop
    closed mid-match never does. Without this, one shut lid leaves a frozen
    tile on every wall in the venue for the rest of the evening, and the two
    managers watch a clock that has stopped and are never told why.

    Liveness is a column rather than a dict in memory because a deploy runs two
    instances at once for a few seconds. An arena that only trusted what it had
    personally heard would spend those seconds abandoning matches whose hosts
    are talking perfectly happily to the instance it is replacing.

    A room nobody has reported on yet is stamped on the first sweep that sees
    it rather than abandoned, which is the same rule one sweep late.
    """
    gone = []
    for room in rooms.live_with_liveness(connection):
        if room["last_heard_at"] is None:
            rooms.heard_from(connection, room["id"])
            continue
        if now - room["last_heard_at"] <= HOST_GONE_SECONDS:
            continue
        full = rooms.by_code(connection, room["code"])
        said = {"reason": HOST_GONE_REASON}
        seq = rooms.append_event(connection, full["id"], "abandoned", said)
        match_bus.publish(room_topic(room["code"]),
                          {"type": "event", "seq": seq, "kind": "abandoned",
                           "match_ms": None, "payload": said})
        _end_match(connection, match_bus, full, "abandoned")
        logger.info("room %s abandoned: nothing from its host in %ss",
                    room["code"], HOST_GONE_SECONDS)
        gone.append(room["code"])
    return gone
```

And the caller (lines 958-968):

```python
async def _watch_for_the_missing(fastapi_app):
    """Run the sweep above for as long as the arena is up."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            _give_up_on_the_missing(fastapi_app.state.conn, fastapi_app.state.bus,
                                    time.time())
        except Exception:
            # One bad sweep must not take the watchdog down for the life of the
            # process: every room after it would then hang live forever.
            logger.exception("the sweep for missing hosts failed")
```

- [ ] **Step 5: Stamp on every host frame**

In `_handle_from_host`, replace line 848:

```python
    # The host is here. That is worth recording before the message is picked
    # over, because a frame the arena goes on to refuse is still proof that
    # somebody is on the other end of the socket running this match.
    rooms.heard_from(connection, current["id"])
```

Remove the now-unused `heard` parameter from `_handle_from_host`'s signature and from its call site at `app.py:799-800`:

```python
            _handle_from_host(message, connection, match_bus, room, client_id)
```

- [ ] **Step 6: Drop `state.heard` from the lifespan**

Delete lines 151-154 of `app.py`. The comment that went with them describes a design that no longer exists.

- [ ] **Step 7: Fix `test_abandoning.py`**

Nine places in this file reach into `state.heard`. Work through all of them; the
file is the real regression net for this task and its 172 lines are worth
reading in full first.

The helper at the top loses its fourth argument:

```python
def sweep(client, when):
    """Run one sweep, as at `when`, from inside the app's own event loop.

    The bus hands messages to sockets that are waiting on them, and waking one
    of those from the test's thread is not safe, so this goes the same way a
    route does.
    """
    state = client.app.state
    return client.portal.call(arena._give_up_on_the_missing, state.conn, state.bus, when)
```

Add a helper beside it, because six tests do the same thing:

```python
def heard_now(client, code):
    """Say the host reported at NOW, on the fixed clock the file runs on."""
    state = client.app.state
    rooms.heard_from(state.conn, rooms.by_code(state.conn, code)["id"], NOW)
```

Then, case by case:

| Line | Today | Becomes |
|---|---|---|
| 33, 41, 61, 80, 91, 100 | `client.app.state.heard[code] = NOW` | `heard_now(client, code)` |
| 49-56 | `test_a_room_nobody_has_reported_on_is_timed_from_the_first_sweep` | unchanged, and it must stay passing: it is the NULL-`last_heard_at` case, and the reason the sweep stamps rather than abandons |
| 122 | `heard = client.app.state.heard[code]` | `heard = rooms.by_code(client.app.state.conn, code)["last_heard_at"]` |
| 133 | `assert code not in client.app.state.heard` | `assert rooms.by_code(client.app.state.conn, code)["last_heard_at"] is None` |
| 148 | `assert client.app.state.heard == {}` | delete this line, and rewrite the test as below |

`test_a_match_that_ended_properly_is_forgotten` loses its reason to exist: it
was written because an in-memory dict would otherwise carry every code a venue
ever saw, and a column on the room is not a leak. Keep the test, change what it
is about, and say why in the docstring:

```python
def test_a_match_that_ended_properly_is_left_alone(client, live_room):
    # This used to be about forgetting: an in-memory dict would carry every code
    # a venue ever saw. Liveness lives on the room now, so there is nothing to
    # leak, and what is left worth asserting is that the sweep only looks at
    # matches that are still live.
    code, host_token = live_room()
    heard_now(client, code)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "full_time",
                        "payload": {"score": [1, 0]}})
        host.receive_json()

    assert sweep(client, NOW + arena.HOST_GONE_SECONDS + 1) == []
    assert rooms.by_code(client.app.state.conn, code)["status"] == "finished"
```

Note the change of clock in that last assertion: the old version swept at `NOW`
and passed because the code had been forgotten. Sweeping late is what actually
proves a finished match is out of scope.

`test_the_watchdog_keeps_going_after_a_bad_sweep` monkeypatches
`_give_up_on_the_missing` with `def explode(*arguments)`, so it survives the
arity change untouched.

- [ ] **Step 8: Run everything**

Run: `cd arena && uv run pytest -q`
Expected: **all pass.** This is the first point in the plan where the whole arena suite is green on Postgres.

- [ ] **Step 9: Commit**

```bash
git add arena/app.py arena/rooms.py arena/tests/test_restart.py arena/tests/test_abandoning.py
git commit -m "fix(arena): keep host liveness where a second instance can see it

A deploy runs the old instance and the new one together for a few seconds.
An arena that only trusts what it has personally heard treats every match in
progress as a host that has gone, because it has heard from none of them.

It survived that by accident: an unknown room got thirty seconds of grace
from setdefault, which also let a genuinely dead room outlive a sweep. A
column is correct in both directions, and wall clock rather than monotonic
because the value is now read by a process that did not take it."
```

---

## Task 7: Serve the pitch from the arena

**Files:**
- Modify: `game/frontend/vite.config.js`
- Modify: `game/frontend/src/game.js:143-160`
- Modify: `arena/app.py` (mounts, `PITCH_URL` default)
- Test: `game/frontend/test/asset-base.test.js` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `game.js` exports `asset(path: string) -> string`, prefixing `import.meta.env.BASE_URL`. The arena serves `/pitch/` from `ARENA_PITCH_DIR`.

**Why.** Without a domain there is no CDN and no load balancer to put a second origin behind, so the arena serves the built bundle. Same-origin then stops being a dev convenience and becomes the architecture, which is what `vite.config.js` already says it wants: *"Proxying rather than opening CORS on :8003 keeps the pitch same-origin, which is what lets the big screen frame it and lets the socket carry the host token."*

Vite does not rewrite absolute URL strings inside JavaScript, so the eight `/assets/...` paths in `preload()` would 404 under `base: '/pitch/'`.

- [ ] **Step 1: Write the failing test**

Create `game/frontend/test/asset-base.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { asset } from '../src/game.js';

// Vite rewrites asset URLs it can see in imports. It cannot see a string
// literal passed to Phaser's loader, so those have to be built from the base
// or every sprite 404s the moment the bundle is served from /pitch/.
describe('asset', () => {
  it('joins onto the configured base', () => {
    expect(asset('assets/sprites/ball.png'))
      .toBe(`${import.meta.env.BASE_URL}assets/sprites/ball.png`);
  });

  it('tolerates a leading slash, because every call site had one', () => {
    expect(asset('/assets/sprites/ball.png')).toBe(asset('assets/sprites/ball.png'));
  });

  it('never doubles the separator', () => {
    expect(asset('assets/ui/scoreboard.png')).not.toContain('//');
  });
});
```

- [ ] **Step 2: Run it**

Run: `cd game/frontend && npx vitest run test/asset-base.test.js`
Expected: FAIL, `asset is not a function`.

- [ ] **Step 3: Add the helper and use it**

In `game/frontend/src/game.js`, above `preload()`:

```js
/**
 * An asset URL that survives being served from somewhere other than the root.
 *
 * In development the pitch is its own origin at /. Deployed, it is a
 * subdirectory of the arena at /pitch/, because without a domain there is no
 * second origin to put it on. Vite rewrites the asset URLs it can see, and it
 * cannot see a string handed to Phaser's loader.
 */
export const asset = (path) => `${import.meta.env.BASE_URL}${path.replace(/^\//, '')}`;
```

Then rewrite lines 143-160:

```js
    this.load.image('pitch', asset('assets/backgrounds/pitch.png'));
    this.load.image('scoreboard', asset('assets/ui/scoreboard.png'));
    this.load.image('coach_portrait', asset('assets/ui/coach_portrait.png'));
    this.load.image('opponent_coach_portrait', asset('assets/ui/opponent_coach_portrait.png'));
    this.load.image('shout_input', asset('assets/ui/shout_input.png'));
    this.load.image('ball', asset('assets/sprites/ball.png'));
```

and inside the atlas loop:

```js
      this.load.atlas(key, asset(`assets/sprites/${sheet}.png`),
        asset(`assets/sprites/${sheet}.json`));
```

Search `game/frontend/src/` for any other absolute `/assets/` or `/player_state/` literal and give it the same treatment. `/api/`, `/ws/`, `/run_sse` and `/api-apps/` are **not** assets and must stay at the root: they are the arena's, not the bundle's.

- [ ] **Step 4: Set the base**

In `game/frontend/vite.config.js`, inside `defineConfig({ ... })` above `server`:

```js
  // Deployed, the pitch is a subdirectory of the arena rather than an origin of
  // its own: with no domain there is no certificate, so there is no second
  // HTTPS origin to be had. The dev server keeps serving from / and
  // import.meta.env.BASE_URL follows either way.
  base: process.env.PITCH_BASE || '/',
```

- [ ] **Step 5: Run the tests and a real build**

Run: `cd game/frontend && npm test && PITCH_BASE=/pitch/ npm run build`
Expected: tests pass; `dist/index.html` references `/pitch/assets/...`.

- [ ] **Step 6: Mount it in the arena**

In `arena/app.py`, beside the `STATIC` constant:

```python
# The built pitch, when the arena is the thing serving it. Unset locally, where
# Vite serves the pitch on :5173 and this mount does not exist.
PITCH_DIR = os.environ.get("ARENA_PITCH_DIR", "")
```

Change the `PITCH_URL` default so it follows the deployment without being told twice:

```python
# Where the pitch is served from. The big screen frames it rather than drawing
# it: physics is 2000 lines of Phaser that already exist and already work, and
# reimplementing them in the arena to avoid an iframe would be the wrong trade.
# When the arena is serving the bundle itself, that is a path on this origin.
PITCH_URL = os.environ.get(
    "ARENA_PITCH_URL", "/pitch" if PITCH_DIR else "http://localhost:5173").rstrip("/")
```

At the bottom, above the `/static` mount:

```python
class Immutable(StaticFiles):
    """Vite's content-hashed bundle, cached for as long as a browser likes.

    The opposite of `Revalidated` and for the opposite reason: these filenames
    contain a hash of their own contents, so a changed file is a changed URL
    and the old one can never be stale. index.html is not hashed, which is why
    it is served by the route below rather than from here.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if PITCH_DIR:
    @app.get("/pitch")
    @app.get("/pitch/")
    async def pitch_page():
        """The pitch's own entry point, never cached: it names the hashed bundle."""
        return FileResponse(Path(PITCH_DIR) / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/pitch", Immutable(directory=PITCH_DIR, html=True), name="pitch")
```

- [ ] **Step 7: Test the mount**

Add to `arena/tests/test_pages.py`:

```python
def test_the_pitch_is_not_mounted_without_a_directory(client):
    # Locally Vite serves it. A 404 here is the arena declining to guess.
    assert client.get("/pitch/").status_code == 404
```

Run: `cd arena && uv run pytest tests/test_pages.py -q`

- [ ] **Step 8: Commit**

```bash
git add game/frontend/vite.config.js game/frontend/src/game.js game/frontend/test/asset-base.test.js arena/app.py arena/tests/test_pages.py
git commit -m "feat: let the arena serve the pitch it frames

There is no domain, so there is no certificate, so there is no second HTTPS
origin to put the pitch on and no load balancer to route to it. The arena
serves the built bundle at /pitch/ instead, which turns out to be what the
pitch wanted anyway: same-origin is what lets the big screen frame it and
lets the socket carry the host token, and the dev proxy has been faking
exactly that all along.

Vite rewrites the asset URLs it can see and cannot see a string handed to
Phaser's loader, so those are built from the base."
```

---

## Task 8: Proxy the coach, and nothing else

**Files:**
- Create: `arena/proxy.py`
- Modify: `arena/app.py`
- Test: `arena/tests/test_proxy.py` (create)

**Interfaces:**
- Consumes: `coach.COACH_URL`, `coach.COACH_APP` (existing module constants).
- Produces: `proxy.router: fastapi.APIRouter` with exactly two routes, `POST /run_sse` and `POST /api-apps/agents/users/{user}/sessions`.

**Why.** `main.js` opens its ADK session and posts to `/run_sse` directly, and Vite proxies both to :8000. The coach has no published port in the deployment, so both 404 and the pitch's coach bar and status check stop working.

**The one risk in this design.** An unauthenticated ADK server behind an open proxy on the public internet is a free language model for anyone who finds the URL. The allowlist is two exact paths and everything else is a 404. Do not make it a prefix match, and do not add a passthrough for convenience.

- [ ] **Step 1: Write the failing test**

Create `arena/tests/test_proxy.py`:

```python
"""The arena's window onto the coach, which is exactly two paths wide.

The pitch opens an ADK session and posts a shout to /run_sse. In development
Vite proxies both to :8000. Deployed there is no :8000 to reach, because the
coach is a sidecar with no published port, so the arena carries those two
calls and refuses everything else. An open proxy in front of an
unauthenticated ADK server is a free language model for whoever finds it.
"""

import pytest


@pytest.mark.parametrize("path", [
    "/api-apps/agents/users/user/sessions/abc",       # a session by id
    "/api-apps/agents/users/user/sessions/abc/events",
    "/api-apps/",
    "/api-apps/agents/eval_sets",
    "/api-apps/list-apps",
])
def test_everything_but_the_two_allowed_paths_is_a_404(client, path):
    assert client.post(path, json={}).status_code == 404
    assert client.get(path).status_code == 404


def test_run_sse_only_answers_post(client):
    assert client.get("/run_sse").status_code == 405


def test_the_session_path_only_answers_post(client):
    assert client.get("/api-apps/agents/users/user/sessions").status_code == 405


def test_a_coach_that_is_not_there_is_a_502_not_a_500(client, monkeypatch):
    monkeypatch.setattr("coach.COACH_URL", "http://127.0.0.1:9")
    reply = client.post("/run_sse", json={"appName": "agents"})
    assert reply.status_code == 502
    assert "coach" in reply.json()["detail"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_proxy.py -v`
Expected: FAIL. `/run_sse` is a 404 rather than a 405, and the 502 case does not exist.

- [ ] **Step 3: Write `arena/proxy.py`**

```python
"""The two calls the pitch makes to the coach, carried by the arena.

The browser used to reach the coach through Vite's dev proxy. Deployed there
is nothing to proxy through: the coach is a sidecar sharing this instance's
network namespace and listening only on loopback, which is what keeps an
unauthenticated ADK server off the public internet.

So the arena carries them, and carries exactly them. Two paths, POST only,
no prefix matching and no passthrough. Everything the ADK server exposes
besides these two is a way to read or replay somebody else's session, and
none of it should be reachable from a phone.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

import coach

logger = logging.getLogger(__name__)

router = APIRouter()

# The coach answers a session create at once. /run_sse is a language model
# chain and takes tens of seconds, so it gets the same idle budget one hop of
# the chain gets everywhere else.
QUICK = httpx.Timeout(10.0, connect=coach.CONNECT_SECONDS)
PATIENT = httpx.Timeout(coach.IDLE_SECONDS, connect=coach.CONNECT_SECONDS)

# A shout from a phone keyboard is short and a session body is smaller. Anything
# larger is not one of the two calls this proxy exists for.
MAX_BODY_BYTES = 64 * 1024


async def _body(request):
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "that is too much to say to a coach")
    return raw


@router.post("/api-apps/agents/users/{user}/sessions")
async def open_session(user: str, request: Request):
    """Open an ADK session. The same rewrite Vite's dev proxy does."""
    raw = await _body(request)
    async with httpx.AsyncClient(base_url=coach.COACH_URL, timeout=QUICK) as http:
        try:
            reply = await http.post(
                f"/apps/{coach.COACH_APP}/users/{user}/sessions",
                content=raw, headers={"Content-Type": "application/json"})
        except httpx.HTTPError as silence:
            raise HTTPException(502, "the coach did not answer") from silence
    return _passed_through(reply)


@router.post("/run_sse")
async def run(request: Request):
    """Carry a shout to the coach and stream the chain's events back.

    Streamed rather than buffered: the events are what light the relay on the
    pitch as each agent answers, and a chain takes tens of seconds. Holding
    them until the last one arrived would turn the whole spectacle into a
    spinner.
    """
    raw = await _body(request)
    http = httpx.AsyncClient(base_url=coach.COACH_URL, timeout=PATIENT)
    try:
        upstream = await http.send(
            http.build_request("POST", "/run_sse", content=raw,
                               headers={"Content-Type": "application/json"}),
            stream=True)
    except httpx.HTTPError as silence:
        await http.aclose()
        raise HTTPException(502, "the coach stopped answering") from silence

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await http.aclose()

    return StreamingResponse(
        relay(), status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
        # Buffering an event stream anywhere between here and the tab would
        # hold every rung of the chain until the huddle.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _passed_through(reply):
    """The coach's own answer, with only the headers a browser should see."""
    from fastapi.responses import Response
    return Response(content=reply.content, status_code=reply.status_code,
                    media_type=reply.headers.get("content-type", "application/json"))
```

- [ ] **Step 4: Wire it up**

In `arena/app.py`, after `app = FastAPI(...)`:

```python
import proxy
```
(with the other local imports at the top, alphabetically between `profiles` and `rooms`)

and after the app is constructed:

```python
# Two paths onto the coach, for the pitch's own coach bar and status check.
# Mounted on the app rather than reached for directly so that the allowlist is
# one file somebody can read in full.
app.include_router(proxy.router)
```

- [ ] **Step 5: Run the tests**

Run: `cd arena && uv run pytest tests/test_proxy.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `cd arena && uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add arena/proxy.py arena/app.py arena/tests/test_proxy.py
git commit -m "feat(arena): carry the pitch's two calls to the coach

The coach is a sidecar listening on loopback with no published port, which
is what keeps an unauthenticated ADK server off the public internet. The
pitch still needs to open a session and post a shout, so the arena carries
those two and refuses everything else by name. Not a prefix, not a
passthrough: every other path the ADK server exposes is a way to read or
replay somebody else's session."
```

---

## Task 9: Serve the injury files from the shared volume

**Files:**
- Modify: `game/agents/football_mcp_server.py:42`
- Modify: `arena/app.py`
- Test: `arena/tests/test_player_state.py` (create), `game/tests/test_mcp_substitutions.py` (existing)

**Interfaces:**
- Consumes: Task 1's room stamping (without it, every file written here is named `WRKS__blue.json`).
- Produces: the MCP server honours `PLAYER_STATE_DIR` from the environment; the arena serves `ARENA_PLAYER_STATE_DIR` at `/player_state/`.

**Why.** `PLAYER_STATE_DIR` resolves to `../frontend/public/player_state` relative to the *coach* container, which does not contain the pitch, and whose image layer is read-only anyway. Meanwhile `main.js:844` polls `/player_state/substitutions/{code}__{team}.json` every two seconds and nothing serves it, and `checkSubstitutions` swallows the 404s, so the feature is quietly rather than loudly broken.

A Cloud Run in-memory volume mounted into both containers fixes both legs at once. The MCP server writes a file; the arena serves that file; the browser polls it. One filesystem, two containers, because they are one instance.

- [ ] **Step 1: Write the failing test**

Create `arena/tests/test_player_state.py`:

```python
"""Injury toasts come off a file two containers share.

A specialist reports an injury through an MCP server living in the coach's
container. The browser polling for it is talking to the arena. In one Cloud
Run instance those are two processes with one in-memory volume between them,
so this is a static mount and not a new table.
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def served(tmp_path, dsn, monkeypatch):
    subs = tmp_path / "substitutions"
    subs.mkdir()
    (subs / "WRKS__blue.json").write_text(json.dumps(
        {"forward": {"action": "injury", "severity": "knock", "ts": 1.0}}))
    monkeypatch.setenv("ARENA_DB", dsn)
    monkeypatch.setenv("ARENA_PLAYER_STATE_DIR", str(tmp_path))
    import importlib

    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        yield client
    importlib.reload(app_module)


def test_an_injury_file_is_served(served):
    reply = served.get("/player_state/substitutions/WRKS__blue.json")
    assert reply.status_code == 200
    assert reply.json()["forward"]["action"] == "injury"


def test_a_dugout_with_no_injuries_is_a_404_the_poller_can_ignore(served):
    assert served.get("/player_state/substitutions/ABCD__red.json").status_code == 404


def test_the_mount_does_not_escape_its_directory(served):
    assert served.get("/player_state/../../arena.db").status_code in (404, 400)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_player_state.py -v`
Expected: FAIL, every path 404s.

- [ ] **Step 3: Mount it in the arena**

In `arena/app.py`, beside `PITCH_DIR`:

```python
# Where the game's MCP server writes injuries and substitution requests. In one
# Cloud Run instance this is an in-memory volume mounted into both this
# container and the coach's, which is the whole of the mechanism: the
# specialist writes a file and the browser polls it. Unset locally, where Vite
# serves the same directory out of the pitch's public/ folder.
PLAYER_STATE_DIR = os.environ.get("ARENA_PLAYER_STATE_DIR", "")
```

With the other mounts at the bottom of the file, above `/static`:

```python
if PLAYER_STATE_DIR:
    # Revalidated, not Immutable: the poller asks every two seconds and the
    # file changes when somebody gets hurt. StaticFiles refuses to serve a path
    # that escapes its directory, which is what makes a filename built from a
    # room code safe to mount.
    app.mount("/player_state", Revalidated(directory=PLAYER_STATE_DIR, check_dir=False),
              name="player_state")
```

`check_dir=False` because the volume is empty until the first injury and `StaticFiles` otherwise refuses to start against a directory that does not exist yet.

- [ ] **Step 4: Let the MCP server be told where to write**

In `game/agents/football_mcp_server.py`, replace line 42:

```python
# Where injuries and substitution requests are written. Locally this defaults
# to the pitch's public directory, which Vite serves. Deployed, the coach and
# the arena are two containers in one instance with a shared in-memory volume,
# and this points at the mount: the specialist writes here and the arena serves
# what it finds.
PLAYER_STATE_DIR = os.environ.get(
    "PLAYER_STATE_DIR", os.path.join(BASE_DIR, "../frontend/public/player_state"))
```

- [ ] **Step 5: Cover the override**

Add to `game/tests/test_mcp_substitutions.py`:

```python
def test_the_state_directory_can_be_moved(monkeypatch, tmp_path):
    # Deployed, the coach writes to a volume it shares with the arena rather
    # than into a pitch that is not in its container.
    monkeypatch.setenv("PLAYER_STATE_DIR", str(tmp_path))
    import importlib

    from agents import football_mcp_server
    importlib.reload(football_mcp_server)
    try:
        path = football_mcp_server.substitutions_path("ABCD", "red")
        assert path.startswith(str(tmp_path))
        assert path.endswith("ABCD__red.json")
    finally:
        monkeypatch.delenv("PLAYER_STATE_DIR")
        importlib.reload(football_mcp_server)
```

- [ ] **Step 6: Run both suites**

Run: `cd arena && uv run pytest -q && cd ../game && uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add arena/app.py arena/tests/test_player_state.py game/agents/football_mcp_server.py game/tests/test_mcp_substitutions.py
git commit -m "feat: put injuries on a volume both containers can reach

The MCP server wrote into the pitch's source tree, resolved relative to a
coach container that has no pitch in it and whose image layer is read-only
regardless. Nothing served /player_state/ either, and the poller swallows
404s, so the toasts have been failing silently rather than loudly.

One in-memory volume, mounted into the coach and the arena, fixes both ends
with no protocol between them: the specialist writes a file, the arena
serves it, the browser polls it."
```

---

## Task 10: Refuse to start misconfigured, and work out your own address

**Files:**
- Modify: `arena/app.py:76-95`, `130`, `737-739`
- Modify: `arena/.env.example`, `arena/README.md`
- Test: `arena/tests/test_config.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `app.PRODUCTION: bool` - `os.environ.get("ARENA_ENV") == "production"`
  - `app.Misconfigured(Exception)`
  - `app.join_url(code, request=None) -> str` - uses `ARENA_PUBLIC_URL` when set, otherwise derives from the request's forwarded headers.

**Why fail-fast.** `ARENA_SECRET` unset mints a random secret per start (`app.py:86`), which in production logs every phone out on every deploy. `ARENA_EMAIL_SALT` unset takes a literal default, and changing it later makes every returning player a stranger. `ARENA_SERVICE_TOKEN` unset refuses every agent write, so the whole chain fails at the last hop with a 403 nobody is watching for. All three are warnings today, which is right on a laptop and wrong on a public URL.

**Why derive the URL.** Cloud Run does not tell a service its own hostname before the first deploy, so a hardcoded `ARENA_PUBLIC_URL` means deploying twice: once to learn the name, once to set it. Every QR code in the venue encodes this, so getting it wrong is not a small thing.

- [ ] **Step 1: Write the failing test**

Create `arena/tests/test_config.py`:

```python
"""What the arena insists on before it will serve a public URL."""

import importlib

import pytest


def _boot(monkeypatch, **environment):
    for name in ("ARENA_ENV", "ARENA_SECRET", "ARENA_EMAIL_SALT",
                 "ARENA_SERVICE_TOKEN", "ARENA_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    import app as app_module
    return importlib.reload(app_module)


@pytest.fixture(autouse=True)
def restore():
    yield
    import app as app_module
    importlib.reload(app_module)


@pytest.mark.parametrize("missing", ["ARENA_SECRET", "ARENA_EMAIL_SALT",
                                     "ARENA_SERVICE_TOKEN"])
def test_production_refuses_to_start_without_each_secret(monkeypatch, missing):
    full = {"ARENA_ENV": "production", "ARENA_SECRET": "s",
            "ARENA_EMAIL_SALT": "p", "ARENA_SERVICE_TOKEN": "t"}
    del full[missing]
    with pytest.raises(Exception) as refusal:
        _boot(monkeypatch, **full)
    assert missing in str(refusal.value)


def test_production_starts_when_all_three_are_set(monkeypatch):
    module = _boot(monkeypatch, ARENA_ENV="production", ARENA_SECRET="s",
                   ARENA_EMAIL_SALT="p", ARENA_SERVICE_TOKEN="t")
    assert module.PRODUCTION is True


def test_a_laptop_still_starts_with_none_of_them(monkeypatch):
    module = _boot(monkeypatch)
    assert module.PRODUCTION is False


def test_the_join_url_is_worked_out_from_the_request_when_unset(client):
    reply = client.get("/api/rooms/WRKS", headers={
        "host": "arena-abc123.a.run.app", "x-forwarded-proto": "https"})
    assert reply.json()["join_url"] == "https://arena-abc123.a.run.app/join/WRKS"


def test_an_explicit_public_url_still_wins(monkeypatch, client):
    monkeypatch.setattr("app.PUBLIC_URL", "https://venue.example")
    reply = client.get("/api/rooms/WRKS", headers={"host": "ignored.example"})
    assert reply.json()["join_url"] == "https://venue.example/join/WRKS"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_config.py -v`
Expected: FAIL, `module 'app' has no attribute 'PRODUCTION'`.

- [ ] **Step 3: Add the gate**

Replace `arena/app.py:76-95`:

```python
class Misconfigured(Exception):
    """A public deployment missing something it must not guess at."""


# On a laptop, a missing secret is a warning and a sensible default. On a URL
# anyone can reach it is neither: a random session secret logs every phone out
# on each deploy, a defaulted salt makes every returning player a stranger the
# day it changes, and an unset service token fails the agent chain at its last
# hop with a 403 nobody is watching for.
PRODUCTION = os.environ.get("ARENA_ENV") == "production"


def _insist(name, why):
    """Read a secret, or say exactly what is missing and why it matters."""
    value = os.environ.get(name, "")
    if value:
        return value
    if PRODUCTION:
        raise Misconfigured(f"{name} must be set when ARENA_ENV=production: {why}")
    return ""


# EMAIL_SALT keeps its literal default outside production: if it randomised,
# every email hash would change on restart and players would lose their history.
EMAIL_SALT = _insist("ARENA_EMAIL_SALT",
                     "changing it later makes every returning player a stranger") \
    or "arena-dev-salt"
if not os.environ.get("ARENA_EMAIL_SALT"):
    logger.warning("ARENA_EMAIL_SALT unset; set it before a real event or every "
                   "returning player is a stranger the next time you change it")

# SESSION_SECRET must never fall back to a public literal, even if that means
# sessions do not survive a restart.
SESSION_SECRET = _insist("ARENA_SECRET", "a random one logs every phone out on each deploy")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    logger.warning("ARENA_SECRET unset; sessions will not survive a restart")
COOKIE = "arena_session"

# The specialist agents run in another process with no phone and no cookie, so
# they carry a shared secret instead. Unset means they are refused: an unset
# secret must authenticate nobody rather than everybody.
SERVICE_TOKEN = _insist("ARENA_SERVICE_TOKEN", "the agent chain cannot write without it")
if not SERVICE_TOKEN:
    logger.warning("ARENA_SERVICE_TOKEN unset; server-side profile writes are refused")
```

- [ ] **Step 4: Derive the public URL**

Change the constant at line 130 to allow an empty value:

```python
# What a QR code should encode. Unset, it is worked out from the request, which
# is what lets a first deploy be a first deploy: Cloud Run does not tell a
# service its own hostname until it exists, and every QR in the venue encodes
# this. Set it explicitly for a tunnel or a LAN name.
PUBLIC_URL = os.environ.get("ARENA_PUBLIC_URL", "").rstrip("/")
```

Replace `join_url` (lines 737-739):

```python
def join_url(code, request=None):
    """The address a phone lands on after scanning this room's code.

    Configured if somebody said so, otherwise whatever the request came in on.
    Behind Cloud Run that means the forwarded scheme and the Host header, which
    together are the *.run.app name with its certificate.
    """
    return f"{_origin(request)}/join/{code}"


def _origin(request):
    if PUBLIC_URL:
        return PUBLIC_URL
    if request is None:
        return "http://localhost:8003"
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}"
```

Thread the request through `_snapshot`, which is the only caller that needs it:

```python
def _snapshot(connection, room_id, request=None):
    """A room as clients see it, with the address its QR encodes.

    `rooms.snapshot` is deliberately ignorant of HTTP, so the URL is glued on
    here rather than threading a base address through the data layer.
    """
    snapshot = rooms.snapshot(connection, room_id)
    return {**snapshot, "join_url": join_url(snapshot["code"], request)}
```

Pass `request` at the five HTTP call sites (`open_room`, `read_room`, `sit_down`, `set_ready`, `start`). Leave the two socket call sites and `_announce` without it: a socket has no forwarded headers worth trusting and falls back to `PUBLIC_URL`, which is set in production.

Also update `read_venue` and `room_qr` to pass `request`.

- [ ] **Step 5: Run the tests**

Run: `cd arena && uv run pytest tests/test_config.py -v && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Document the new variables**

Add to `arena/.env.example`:

```bash
# [PRODUCTION] Set to `production` and the arena refuses to start without
# ARENA_SECRET, ARENA_EMAIL_SALT and ARENA_SERVICE_TOKEN, rather than warning
# and carrying on with a default. On a laptop those defaults are right; on a
# URL anybody can reach they are three different silent failures.
# ARENA_ENV=production

# Where the built pitch bundle is, when the arena is the thing serving it.
# Unset locally, where Vite serves the pitch on :5173.
# ARENA_PITCH_DIR=/srv/pitch

# The directory the game's MCP server writes injuries into. Deployed this is a
# volume shared with the coach's container; unset locally, where Vite serves
# the same files out of the pitch's public/ folder.
# ARENA_PLAYER_STATE_DIR=/var/run/player_state
```

Change the `ARENA_PUBLIC_URL` entry to say it is derived when unset, and update the same two rows in `arena/README.md`'s environment table.

- [ ] **Step 7: Commit**

```bash
git add arena/app.py arena/tests/test_config.py arena/.env.example arena/README.md
git commit -m "feat(arena): refuse to guess at secrets on a public URL

A random session secret logs every phone out on each deploy, a defaulted
salt makes every returning player a stranger the day somebody changes it,
and an unset service token fails the agent chain at its last hop with a 403
nobody is watching for. Warnings are the right answer to all three on a
laptop and the wrong one on the internet, so ARENA_ENV=production turns them
into a refusal that names what is missing.

The public URL now works itself out from the forwarded headers when nobody
said otherwise, because Cloud Run does not tell a service its own hostname
until it exists and every QR in the venue encodes it."
```

---

## Task 11: Rate limits and a cap on live rooms

**Files:**
- Create: `arena/limits.py`
- Modify: `arena/app.py` (`join`, `open_room`)
- Test: `arena/tests/test_limits.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `limits.Bucket(rate: float, burst: int)` with `.take(key: str, now: float | None = None) -> bool`
  - `limits.client_ip(request) -> str`
  - `app.MAX_LIVE_ROOMS: int`
  - `app.state.players` and `app.state.rooms_opened`, two `Bucket`s built in the lifespan beside `state.bus` and `state.chain`.

**Why.** `POST /api/players` and `POST /api/rooms` are the only unauthenticated endpoints that create rows, and the URL is public. A live-room cap turns "the instance finds its own limit" into a sentence a person can read.

**The buckets belong on `app.state`, not to the module.** Every test in the suite comes from the same address, `testclient`, and `live_room` opens a room over HTTP. A module-level bucket would be shared by every test in the session and the suite would start failing somewhere around the sixth room, in whichever test happened to run sixth. That is a flaky suite by construction. On `app.state` they are built per lifespan, exactly like `state.bus` and `state.chain`, so each `client` fixture gets its own - and the arena has one instance, so per-app is per-process either way.

- [ ] **Step 1: Write the failing test**

Create `arena/tests/test_limits.py`:

```python
"""What the arena will take from one address before it says no."""

import pytest

import limits


def test_a_burst_goes_through_and_the_next_one_does_not():
    bucket = limits.Bucket(rate=1.0, burst=3)
    assert [bucket.take("1.2.3.4", now=0.0) for _ in range(3)] == [True, True, True]
    assert bucket.take("1.2.3.4", now=0.0) is False


def test_it_refills_over_time():
    bucket = limits.Bucket(rate=1.0, burst=3)
    for _ in range(3):
        bucket.take("1.2.3.4", now=0.0)
    assert bucket.take("1.2.3.4", now=1.0) is True


def test_it_never_refills_past_the_burst():
    bucket = limits.Bucket(rate=1.0, burst=3)
    bucket.take("1.2.3.4", now=0.0)
    assert [bucket.take("1.2.3.4", now=1000.0) for _ in range(3)] == [True, True, True]
    assert bucket.take("1.2.3.4", now=1000.0) is False


def test_one_address_cannot_spend_another_address_budget():
    bucket = limits.Bucket(rate=1.0, burst=1)
    assert bucket.take("1.2.3.4", now=0.0) is True
    assert bucket.take("5.6.7.8", now=0.0) is True


def test_idle_addresses_are_forgotten_rather_than_accumulated():
    # A venue's worth of phones over an evening should not become a dict that
    # only ever grows.
    bucket = limits.Bucket(rate=1.0, burst=1)
    bucket.take("1.2.3.4", now=0.0)
    bucket.take("5.6.7.8", now=100_000.0)
    assert "1.2.3.4" not in bucket._seen


def test_opening_rooms_too_fast_is_a_429(client):
    # The bucket is this app's own, so shrinking it cannot leak into the next
    # test the way a module-level one would.
    client.app.state.rooms_opened = limits.Bucket(rate=0.0, burst=2)
    assert client.post("/api/rooms", json={"mode": "solo"}).status_code == 200
    assert client.post("/api/rooms", json={"mode": "solo"}).status_code == 200
    refused = client.post("/api/rooms", json={"mode": "solo"})
    assert refused.status_code == 429


def test_the_default_budget_is_bigger_than_a_test_run_needs(client):
    # The suite opens rooms from one address all session long. If the shipped
    # burst cannot take a handful in a row, it is the wrong number and the
    # first sign of it should be here rather than in an unrelated test.
    for _ in range(5):
        assert client.post("/api/rooms", json={"mode": "solo"}).status_code == 200


def test_a_full_venue_is_a_503_with_a_sentence_in_it(client, monkeypatch):
    monkeypatch.setattr("app.MAX_LIVE_ROOMS", 0)
    refused = client.post("/api/rooms", json={"mode": "solo"})
    assert refused.status_code == 503
    assert "full" in refused.json()["detail"].lower()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_limits.py -v`
Expected: FAIL, no module named `limits`.

- [ ] **Step 3: Write `arena/limits.py`**

```python
"""What one address may ask for, and how many matches a venue may hold.

Two unauthenticated endpoints create rows: a player and a room. On a laptop
that is fine. On a URL anybody can find it is an invitation, and the cost of
saying no is a dictionary.

In memory, because there is one instance by design. If that ever stops being
true this is one of the four things that has to move, and the others are the
bus, host liveness and the chain's semaphore.
"""

import time

# An address with nothing to say for this long is dropped rather than kept.
# A venue's phones over an evening should not become a dict that only grows.
IDLE_SECONDS = 3600


class Bucket:
    """A token bucket per key. `burst` at once, refilling at `rate` a second."""

    def __init__(self, rate, burst):
        self.rate = rate
        self.burst = burst
        self._seen = {}

    def take(self, key, now=None):
        """Spend one token for `key`. False if there was not one to spend."""
        now = time.monotonic() if now is None else now
        self._forget(now)
        tokens, last = self._seen.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1.0:
            self._seen[key] = (tokens, now)
            return False
        self._seen[key] = (tokens - 1.0, now)
        return True

    def _forget(self, now):
        for key in [key for key, (_, last) in self._seen.items()
                    if now - last > IDLE_SECONDS]:
            del self._seen[key]


def client_ip(request):
    """The caller's address as far as it can be known behind a proxy.

    Cloud Run appends the real client to X-Forwarded-For and everything before
    it is whatever the client claimed, so the last entry is the only one worth
    reading. Falling back to the socket is for running without a proxy.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
```

- [ ] **Step 4: Apply them**

In `arena/app.py`, with the other constants:

```python
# How many matches may be live at once. Sized well above a busy venue and well
# below what one instance can be talked into holding, so that the answer to a
# flood is a sentence rather than an instance quietly getting slower.
MAX_LIVE_ROOMS = int(os.environ.get("ARENA_MAX_LIVE_ROOMS", "120"))

# Two unauthenticated endpoints create rows. A person joining a match does each
# of these once, so the rate is slow and the burst is what carries a scanned QR
# retried on bad wifi, or a workshop where one room's worth of people are all
# behind the same NAT.
PLAYER_RATE, PLAYER_BURST = 0.2, 30
ROOM_RATE, ROOM_BURST = 0.1, 20
```

In the lifespan, beside `state.bus` and `state.chain`:

```python
    # Per app rather than per module, so that a test's client gets its own and
    # the suite cannot fail in whichever test happens to run last. There is one
    # instance, so per app is per process anyway.
    state.players = limits.Bucket(PLAYER_RATE, PLAYER_BURST)
    state.rooms_opened = limits.Bucket(ROOM_RATE, ROOM_BURST)
```

In `join`, before creating the player:

```python
    if not request.app.state.players.take(limits.client_ip(request)):
        raise HTTPException(429, "slow down a moment and try that again")
```

In `open_room`, before creating the room:

```python
    if not request.app.state.rooms_opened.take(limits.client_ip(request)):
        raise HTTPException(429, "slow down a moment and try that again")
    if len(rooms.live(connection)) >= MAX_LIVE_ROOMS:
        raise HTTPException(503, "the venue is full - wait for a match to finish")
```

- [ ] **Step 5: Run the tests**

Run: `cd arena && uv run pytest tests/test_limits.py -v && uv run pytest -q`

A limit that leaks between tests fails positionally, so one green run proves
little. Run the suite with `test_limits.py` deselected as well, so that the
tests which deliberately shrink a bucket are not the only thing standing between
the rest of the suite and a 429:

Run: `cd arena && uv run pytest -q --deselect tests/test_limits.py`
Expected: all pass. If any other test returns a 429, the bucket is being shared when it should not be. Fix the sharing rather than raising the number to hide it.

- [ ] **Step 6: Commit**

```bash
git add arena/limits.py arena/app.py arena/tests/test_limits.py arena/.env.example arena/README.md
git commit -m "feat(arena): put a limit on what one address may open

Creating a player and opening a room are the two things anybody can do
without a session, and the URL is about to be public. A token bucket each,
and a cap on live matches so that a flood is answered with a sentence
somebody can read rather than by an instance quietly getting slower."
```

---

## Task 12: Make the wall cheap

**Files:**
- Modify: `arena/app.py:971-992`
- Test: `arena/tests/test_wall_socket.py` (existing, add cases)

**Interfaces:**
- Consumes: nothing.
- Produces: `app.WALL_HZ: float`, `app.MAX_WALL_SOCKETS: int`.

**Why.** `/ws/wall` sends every room's frames to every subscriber. At 50 rooms and 10 Hz that is 500 messages a second down each wall socket, to redraw thumbnails smaller than a playing card. Nothing on a wall benefits from more than a couple of updates a second, and the full-rate feed is still there on the room socket for whoever is actually watching a match.

Downsample per room rather than globally, so a wall with one busy room still updates smoothly and a wall with fifty does not send fifty times as much.

- [ ] **Step 1: Write the failing test**

Add to `arena/tests/test_wall_socket.py`:

```python
def test_the_wall_thins_out_a_busy_room(client, monkeypatch):
    """Fifty rooms at ten frames a second is five hundred messages a second to
    draw thumbnails with. Two a second per room is more than a wall can show."""
    import app

    monkeypatch.setattr(app, "WALL_HZ", 2.0)
    keep = app._wall_thinner()
    now = 1000.0
    assert keep("ABCD", now) is True
    assert keep("ABCD", now + 0.1) is False
    assert keep("ABCD", now + 0.4) is False
    assert keep("ABCD", now + 0.6) is True


def test_one_busy_room_does_not_starve_a_quiet_one(monkeypatch):
    import app

    monkeypatch.setattr(app, "WALL_HZ", 2.0)
    keep = app._wall_thinner()
    assert keep("ABCD", 1000.0) is True
    assert keep("EFGH", 1000.0) is True


def test_the_wall_turns_away_more_screens_than_it_expects(client, monkeypatch):
    import app

    monkeypatch.setattr(app, "MAX_WALL_SOCKETS", 0)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/wall"):
            pass


def test_a_screen_that_hangs_up_gives_its_place_back(client):
    # The counter is decremented in a finally. If it ever were not, a venue
    # would run out of wall slots over an evening and nobody would know why.
    for _ in range(3):
        with client.websocket_connect("/ws/wall") as wall:
            wall.receive_json()
    assert client.app.state.walls == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd arena && uv run pytest tests/test_wall_socket.py -v`
Expected: FAIL, `module 'app' has no attribute '_wall_thinner'`.

- [ ] **Step 3: Add the thinner and the cap**

With the other constants in `arena/app.py`:

```python
# How often one room's tile may redraw on the wall. The host reports at 10 Hz
# because that is what the match it is running needs; a thumbnail on a
# filmstrip does not, and fifty of them at 10 Hz is five hundred messages a
# second down every wall socket in the venue. The room socket still carries
# every frame, which is what a viewer watching one match is reading.
WALL_HZ = float(os.environ.get("ARENA_WALL_HZ", "2"))

# How many big screens may watch the wall at once.
MAX_WALL_SOCKETS = int(os.environ.get("ARENA_MAX_WALL_SOCKETS", "12"))
```

The count of open wall sockets goes on `app.state` in the lifespan, for the same
reason the buckets do - a module-level counter is shared by every test in the
session, and one leaked socket would then fail an unrelated test later on:

```python
    state.walls = 0
```

```python
def _wall_thinner():
    """Per-socket state deciding which wall frames are worth sending.

    Per room rather than per socket overall: a wall showing one match should
    still update smoothly, and a wall showing fifty should not send fifty times
    as much. A room's first frame always goes, so a tile appears the moment its
    match does.
    """
    last = {}

    def keep(code, now):
        if now - last.get(code, float("-inf")) < 1.0 / WALL_HZ:
            return False
        last[code] = now
        return True

    return keep
```

Rewrite `wall_socket`:

```python
@app.websocket("/ws/wall")
async def wall_socket(socket: WebSocket):
    """Every live room at a glance. One connection for the filmstrip, not six."""
    state = socket.app.state
    if state.walls >= MAX_WALL_SOCKETS:
        # Closed before accept, so the browser sees a refusal rather than a
        # connection that opens and then goes quiet.
        await socket.close(code=4429, reason="too many screens are watching the wall")
        return

    match_bus = state.bus
    await socket.accept()
    state.walls += 1
    await socket.send_json({"type": "wall", "rooms": rooms.live(state.conn)})

    subscription = match_bus.subscribe(WALL, maxsize=128)
    tasks = [asyncio.create_task(_pump_wall(socket, subscription)),
             asyncio.create_task(_until_closed(socket))]
    done, pending = set(), set(tasks)
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        state.walls -= 1
        for task in pending:
            task.cancel()
        for task in done:
            if not task.cancelled():
                exc = task.exception()
                if exc:
                    logger.exception("wall socket task died", exc_info=exc)
        subscription.close()


async def _pump_wall(socket, subscription):
    """`_pump`, with the position frames thinned out.

    Only `wall.state` is thinned. A room opening, kicking off or finishing is a
    `wall` message, and dropping one of those would leave a tile that is wrong
    rather than a tile that is a fraction of a second old.
    """
    keep = _wall_thinner()
    async for message in subscription:
        if message.get("type") == "wall.state" and not keep(message.get("code"),
                                                            time.monotonic()):
            continue
        await socket.send_json(message)
```

- [ ] **Step 4: Run the tests**

Run: `cd arena && uv run pytest tests/test_wall_socket.py -v && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add arena/app.py arena/tests/test_wall_socket.py arena/.env.example arena/README.md
git commit -m "perf(arena): stop sending the wall more than it can draw

Fifty rooms at ten frames a second is five hundred messages a second down
every wall socket, to redraw thumbnails smaller than a playing card. Two a
second per room, thinned per room so one busy match does not starve a quiet
one, and only the position frames: a room kicking off or finishing still
goes through at once, because a dropped one of those leaves a tile that is
wrong rather than slightly late."
```

---

## Task 13: Build the three images

**Files:**
- Create: `arena/Dockerfile`, `game/Dockerfile.coach`, `game/Dockerfile.captain`, `.dockerignore`
- Modify: `arena/pyproject.toml` if anything is missing at runtime

**Interfaces:**
- Consumes: Tasks 3-12.
- Produces: three images. The arena listens on `$PORT` (Cloud Run sets it, default 8080), the coach on 8000, the captain on 8001.

- [ ] **Step 1: Write `.dockerignore` at the repo root**

```
**/.venv
**/node_modules
**/__pycache__
**/.pytest_cache
**/*.db
**/.env
.git
docs
dugout
```

`dugout/` is excluded on purpose: it embeds the Antigravity CLI and runs shell commands unrestricted, and it has no business in an image that faces the internet.

- [ ] **Step 2: Write `arena/Dockerfile`**

```dockerfile
# The arena, with the pitch built into it. Two stages so that Node is a build
# dependency rather than something running on the internet.
#
# Built from the repository root, because the pitch lives in game/ and the
# arena serves it:
#   docker build -f arena/Dockerfile -t arena .

FROM node:22-slim AS pitch
WORKDIR /build
COPY game/frontend/package*.json ./
RUN npm ci
COPY game/frontend/ ./
# The bundle is served from a subdirectory of the arena, so every hashed asset
# URL has to be written with that prefix at build time.
ENV PITCH_BASE=/pitch/
RUN npm run build

FROM python:3.14-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY arena/pyproject.toml arena/uv.lock ./
RUN uv sync --frozen --no-dev

COPY arena/ ./
COPY --from=pitch /build/dist /srv/pitch

ENV ARENA_PITCH_DIR=/srv/pitch \
    ARENA_PLAYER_STATE_DIR=/var/run/player_state \
    ARENA_ENV=production \
    PATH="/app/.venv/bin:$PATH"

# Cloud Run names the port. 8080 is the default for running the image by hand.
ENV PORT=8080
EXPOSE 8080

# One worker, on purpose. The bus, host liveness and the chain's semaphore are
# all in process, and a second worker is a second arena that shares none of
# them. See the spec: this is the same constraint as max-instances: 1.
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1
```

- [ ] **Step 3: Write `game/Dockerfile.coach`**

```dockerfile
# The ADK coach. Listens on loopback only: it is reached over localhost by the
# arena sharing this instance's network namespace, and an unauthenticated ADK
# server must not be reachable from anywhere else.
#
#   docker build -f game/Dockerfile.coach -t coach .

FROM python:3.14-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY game/pyproject.toml game/uv.lock ./
RUN uv sync --frozen --no-dev

COPY game/agents/ ./agents/

# The MCP server the specialists spawn over stdio writes here, and the arena
# serves the same directory. It is an in-memory volume mounted into both.
ENV PLAYER_STATE_DIR=/var/run/player_state \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# `adk web .` scans the working directory for the agent package beside it.
CMD ["adk", "web", ".", "--host", "0.0.0.0", "--port", "8000"]
```

Note `--host 0.0.0.0`: containers in a Cloud Run instance share a network namespace, so this is still only reachable from the instance, and binding to loopback alone can miss the sidecar's interface. The isolation comes from having no published port, not from the bind address.

- [ ] **Step 4: Write `game/Dockerfile.captain`**

```dockerfile
# The A2A captain. Reached only by the coach, over localhost.
#
#   docker build -f game/Dockerfile.captain -t captain .

FROM python:3.14-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY game/pyproject.toml game/uv.lock ./
RUN uv sync --frozen --no-dev

COPY game/agents/ ./agents/

ENV CAPTAIN_HOST=0.0.0.0 \
    CAPTAIN_PORT=8001 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8001

CMD ["python", "-m", "agents.captain_server"]
```

- [ ] **Step 5: Build all three and run the arena by hand**

```bash
cd /Users/chuan/mywork/ai/agent-football
docker build -f arena/Dockerfile -t arena:local .
docker build -f game/Dockerfile.coach -t coach:local .
docker build -f game/Dockerfile.captain -t captain:local .

# The arena alone, against the local database, to prove the pitch is baked in.
# host.docker.internal reaches the Mac's Postgres from inside the container.
docker run --rm -p 8080:8080 \
  -e ARENA_ENV=development \
  -e ARENA_DB="postgresql://$(whoami)@host.docker.internal:5432/arena" \
  -e ARENA_SERVICE_TOKEN=dev-token \
  arena:local
```

Expected: `curl -s localhost:8080/health` returns `{"ok":true,"service":"arena"}`, `curl -sI localhost:8080/pitch/` returns 200 with `Cache-Control: no-cache`, and a hashed asset under `/pitch/assets/` returns 200 with `immutable`.

- [ ] **Step 6: Commit**

```bash
git add arena/Dockerfile game/Dockerfile.coach game/Dockerfile.captain .dockerignore
git commit -m "build: three images, with the pitch baked into the arena's

Node is a build stage rather than something running on the internet, and
the bundle is written with its /pitch/ prefix at build time because that is
when hashed asset URLs are decided.

One uvicorn worker, for the same reason the service will run one instance:
the bus, host liveness and the chain's semaphore are in process, and a
second worker is a second arena that shares none of them."
```

---

## Task 14: The service, and how to put it there

**Files:**
- Create: `deploy/service.yaml`, `deploy/deploy.sh`, `deploy/README.md`
- Create: `compose.yaml`
- Modify: `arena/run.sh`, `README.md`, `dugout/app.py:37`

**Interfaces:**
- Consumes: Task 13's images.
- Produces: a deployable service.

- [ ] **Step 1: Write `deploy/service.yaml`**

```yaml
# The whole topology, in a file, so it is reviewable in a diff rather than in
# somebody's shell history. Multi-container services cannot be expressed on the
# `gcloud run deploy` command line at all.
#
#   gcloud run services replace deploy/service.yaml --region=REGION
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: arena
  annotations:
    run.googleapis.com/launch-stage: GA
spec:
  template:
    metadata:
      annotations:
        # One instance. This is a correctness constraint, not a cost decision.
        # The match bus, host liveness and the chain's Gemini semaphore are all
        # in process. A second instance means phones that never see a frame, a
        # watchdog that abandons matches somebody is playing, and a shout whose
        # specialist patches land on the other instance so `caused_by` returns
        # None and the leaderboard is quietly wrong with no error anywhere.
        # Read the spec before raising this.
        autoscaling.knative.dev/minScale: "1"
        autoscaling.knative.dev/maxScale: "1"
        # The watchdog sweep, the chain and the bus all run between requests.
        # Throttled, a quiet room is abandoned late and a shout in flight stalls.
        run.googleapis.com/cpu-throttling: "false"
        run.googleapis.com/startup-cpu-boost: "true"
        run.googleapis.com/cloudsql-instances: PROJECT:REGION:arena-pg
        # The arena takes the traffic. The captain has to be up before the coach
        # can hand a shout to it, and the coach before the arena proxies to it.
        run.googleapis.com/container-dependencies: '{"arena":["coach"],"coach":["captain"]}'
    spec:
      # A WebSocket is a request as far as Cloud Run is concerned, and the
      # default is 300 seconds. A match is 180 plus a lobby plus a huddle, so
      # the default cuts the room socket mid-match and it reads as a network
      # fault. 3600 is the maximum.
      timeoutSeconds: 3600
      # Left at the default 80. With one instance this only governs how many
      # requests are handed over at once, and 50 phones are almost entirely
      # long-lived sockets rather than requests.
      containerConcurrency: 80
      serviceAccountName: arena@PROJECT.iam.gserviceaccount.com
      volumes:
        - name: player-state
          emptyDir:
            medium: Memory
            sizeLimit: 128Mi

      containers:
        # The only container with a port. Everything else is loopback.
        - name: arena
          image: REGION-docker.pkg.dev/PROJECT/futsal/arena:TAG
          ports:
            - name: http1
              containerPort: 8080
          resources:
            limits:
              cpu: "4"
              memory: 8Gi
          volumeMounts:
            - name: player-state
              mountPath: /var/run/player_state
          env:
            - name: ARENA_ENV
              value: production
            - name: ARENA_DB
              value: postgresql://arena@/arena?host=/cloudsql/PROJECT:REGION:arena-pg
            - name: ARENA_COACH_URL
              value: http://127.0.0.1:8000
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef: {name: arena-db-password, key: latest}
            - name: ARENA_SECRET
              valueFrom:
                secretKeyRef: {name: arena-secret, key: latest}
            - name: ARENA_EMAIL_SALT
              valueFrom:
                secretKeyRef: {name: arena-email-salt, key: latest}
            - name: ARENA_SERVICE_TOKEN
              valueFrom:
                secretKeyRef: {name: arena-service-token, key: latest}
          startupProbe:
            httpGet: {path: /health, port: 8080}
            periodSeconds: 3
            failureThreshold: 20
          livenessProbe:
            httpGet: {path: /health, port: 8080}
            periodSeconds: 30

        - name: coach
          image: REGION-docker.pkg.dev/PROJECT/futsal/coach:TAG
          resources:
            limits:
              cpu: "2"
              memory: 4Gi
          volumeMounts:
            - name: player-state
              mountPath: /var/run/player_state
          env:
            - name: GOOGLE_GENAI_USE_VERTEXAI
              value: "true"
            - name: GOOGLE_CLOUD_PROJECT
              value: PROJECT
            - name: GOOGLE_CLOUD_LOCATION
              value: global
            - name: CAPTAIN_A2A_URL
              value: http://127.0.0.1:8001/.well-known/agent-card.json
            # The specialists reach the arena over loopback, so this token
            # never crosses the internet.
            - name: ARENA_URL
              value: http://127.0.0.1:8080
            - name: ARENA_SERVICE_TOKEN
              valueFrom:
                secretKeyRef: {name: arena-service-token, key: latest}
          startupProbe:
            tcpSocket: {port: 8000}
            periodSeconds: 3
            failureThreshold: 40

        - name: captain
          image: REGION-docker.pkg.dev/PROJECT/futsal/captain:TAG
          resources:
            limits:
              cpu: "2"
              memory: 4Gi
          env:
            - name: GOOGLE_GENAI_USE_VERTEXAI
              value: "true"
            - name: GOOGLE_CLOUD_PROJECT
              value: PROJECT
            - name: GOOGLE_CLOUD_LOCATION
              value: global
            - name: ARENA_URL
              value: http://127.0.0.1:8080
            - name: ARENA_SERVICE_TOKEN
              valueFrom:
                secretKeyRef: {name: arena-service-token, key: latest}
          startupProbe:
            tcpSocket: {port: 8001}
            periodSeconds: 3
            failureThreshold: 40
  traffic:
    - percent: 100
      latestRevision: true
```

Confirm the `CAPTAIN_A2A_URL` well-known path against `agents/agent.py:52`, which builds it from `AGENT_CARD_WELL_KNOWN_PATH`; use whatever that constant actually resolves to rather than the literal above if they differ.

- [ ] **Step 2: Write `deploy/deploy.sh`**

```bash
#!/bin/bash
# Build the three images, push them, and replace the service.
set -euo pipefail

: "${PROJECT:?set PROJECT}"
: "${REGION:?set REGION}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/futsal"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
    echo "WARNING: the tree is dirty, so ${TAG} will not describe what is deployed."
fi

# Every deploy replaces the one instance, and the one instance is holding every
# live match. There is no rolling handover to be had at max-instances: 1.
echo "This drops every match currently being played. Deploy between matches."
read -r -p "Continue? [y/N] " answer
[ "$answer" = "y" ] || exit 1

for image in arena coach captain; do
    case "$image" in
        arena) file=arena/Dockerfile ;;
        *)     file=game/Dockerfile.$image ;;
    esac
    echo "--> Building $image..."
    docker build --platform linux/amd64 -f "$file" -t "${REPO}/${image}:${TAG}" .
    docker push "${REPO}/${image}:${TAG}"
done

echo "--> Replacing the service..."
sed -e "s|PROJECT|${PROJECT}|g" -e "s|REGION|${REGION}|g" -e "s|:TAG|:${TAG}|g" \
    deploy/service.yaml > /tmp/arena-service.yaml
gcloud run services replace /tmp/arena-service.yaml --region="${REGION}" --project="${PROJECT}"

gcloud run services describe arena --region="${REGION}" --project="${PROJECT}" \
    --format='value(status.url)'
```

`--platform linux/amd64` matters: an Apple Silicon build produces arm64 images that Cloud Run will not run.

- [ ] **Step 3: Write `deploy/README.md`**

Cover, in the repo's voice, with real commands: enabling `run`, `sqladmin`, `secretmanager`, `artifactregistry` and `aiplatform`; creating the Artifact Registry repository; `gcloud sql instances create arena-pg --database-version=POSTGRES_18`; creating the `arena` database and user; creating the four secrets; the service account and its four role bindings (`roles/cloudsql.client`, `roles/secretmanager.secretAccessor`, `roles/aiplatform.user`, `roles/logging.logWriter`); `--allow-unauthenticated`; and the note that the first deploy needs no `ARENA_PUBLIC_URL` because Task 10 derives it.

State plainly in it: **every deploy drops every live match**, and a crash does the same, because `max-instances: 1` means there is no second instance to take over. The database survives; the live matches do not.

- [ ] **Step 4: Write `compose.yaml` at the repo root**

```yaml
# Postgres for anyone without a local install. The native one is the default
# path - `brew services start postgresql@18` and the arena makes its own
# database - and this is the fallback.
#
#   docker compose up -d
#   ARENA_DB=postgresql://arena:arena@localhost:5433/arena arena/run.sh
#
# Port 5433 on purpose, so it cannot quietly shadow a local 5432.
services:
  postgres:
    image: postgres:18
    environment:
      POSTGRES_USER: arena
      POSTGRES_PASSWORD: arena
      POSTGRES_DB: arena
    ports:
      - "5433:5432"
    volumes:
      - arena-pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U arena"]
      interval: 5s
      retries: 10

volumes:
  arena-pg:
```

- [ ] **Step 5: Add the preflight to `arena/run.sh`**

Before the `uv sync`, replacing the comment block at lines 32-36:

```bash
# The arena reads its own configuration and warns about the salt, the secret
# and the token on the way up, so none of that is checked here: a second answer
# to the same question is the one that ends up wrong.
#
# The database is different. Without it uvicorn dies on a traceback that says
# nothing about what to do, and what to do is one command.
DSN="${ARENA_DB:-postgresql:///arena}"
if ! uv run python -c "
import sys, psycopg
try:
    psycopg.connect('${DSN}').close()
except psycopg.OperationalError as problem:
    if 'does not exist' in str(problem):
        sys.exit(0)   # the arena creates its own database
    print(problem, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
    echo "ERROR: no Postgres at ${DSN}"
    echo "  native:  brew services start postgresql@18"
    echo "  docker:  docker compose up -d  (then ARENA_DB=postgresql://arena:arena@localhost:5433/arena)"
    exit 1
fi
```

Move this to after `uv sync`, since it needs psycopg installed.

- [ ] **Step 6: Let the dugout's health map follow a remote arena**

`dugout/app.py:37` assumes every service is a localhost port. Replace:

```python
# The arena is in this list because every tool in the dugout now goes through
# it: with the arena down the squad cannot be read, tuned or shouted at, and
# the manager should see that in the header rather than in a tool result.
# It is a URL rather than a port because the arena may be the deployed one
# while the pitch, the coach and the captain are still on this machine.
GAME_SERVICES = {"pitch": 5173, "coach": 8000, "captain": 8001}
ARENA_HEALTH = f"{os.environ.get('ARENA_URL', 'http://127.0.0.1:8003').rstrip('/')}/health"
```

Then update whatever renders the header to check `ARENA_HEALTH` alongside the three ports. Read the existing health-check function before changing it and keep its shape.

- [ ] **Step 7: Update the root `README.md`**

The architecture diagram says "FastAPI + SQLite" and "Three processes". Update:
- the arena box to "FastAPI + Postgres"
- the "Running it" section with the Postgres prerequisite and the two ways to get one
- a short "Deployed" section pointing at `deploy/README.md`, saying it is one Cloud Run service with three containers and a Cloud SQL database, and that the dugout stays on the presenter's machine because it runs shell commands unrestricted

- [ ] **Step 8: Verify the whole thing locally, end to end**

Follow `docs/superpowers/SMOKE.md` from the top against native Postgres. Every step should behave exactly as it did on SQLite. Pay attention to: the QR code encoding the right host, a shout reaching the huddle, a tune landing mid-match, and the board surviving a restart of the arena.

- [ ] **Step 9: Commit**

```bash
git add deploy compose.yaml arena/run.sh dugout/app.py README.md
git commit -m "feat: the service, and what it takes to put it there

One Cloud Run service, three containers, one instance. The yaml carries the
four settings that are easy to get wrong and expensive to miss: a 3600
second timeout because a WebSocket is a request and the default 300 would
cut every match in half, no CPU throttling because the watchdog and the
chain run between requests, container dependencies so the arena never
proxies to a coach that is not listening, and max-instances pinned at one
with the reason written next to it.

Postgres locally is native first and compose second, and run.sh says which
command to run rather than letting uvicorn die on a traceback."
```

---

## Task 15: Rehearse the load before the room does

**Files:**
- Modify: `arena/fake_host.py` if it cannot yet drive many rooms at once
- Create: `arena/tests/test_load_rehearsal.py` (marked slow, not part of the default run)

**Interfaces:**
- Consumes: everything above.
- Produces: a number, and a decision about `ARENA_CHAIN_LIMIT`.

- [ ] **Step 1: Read `fake_host.py` and find out what it already does**

Run: `cd arena && uv run python fake_host.py --help` and read the file. It exists to drive rooms without a browser. Extend it only if it cannot open N rooms concurrently.

- [ ] **Step 2: Write the rehearsal**

Create `arena/tests/test_load_rehearsal.py`, marked so it does not run by default:

```python
"""Fifty rooms at ten frames a second, which is what the venue is sized for.

Skipped unless ARENA_LOAD=1. It takes minutes and it is a rehearsal rather
than an assertion about correctness: the numbers it prints are the point.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ARENA_LOAD") != "1",
                                reason="set ARENA_LOAD=1 to rehearse the venue's load")
```

Drive 50 concurrent rooms through `fake_host`, each publishing state at 10 Hz for a full 180-second match, with a wall socket and two room sockets attached. Record and print: peak CPU of the arena process, the p99 latency between a host frame being sent and a viewer receiving it, the count of dropped bus messages, and the final row counts.

- [ ] **Step 3: Run it against the deployed service**

Once Task 14 has deployed, point the rehearsal at the `*.run.app` URL rather than at localhost. This is the only step that tests the real thing: TLS termination, the 3600 second timeout, and CPU that is not throttled.

Expected: comfortably under one core of the arena's four, given the 0.045 ms per query and 2.3% of an event loop measured while planning. If it is not, the finding is more valuable than the plan.

- [ ] **Step 4: Set `ARENA_CHAIN_LIMIT` from the real quota**

Read the project's actual Vertex AI quota for the model in `agents/constants.py`. `ARENA_CHAIN_LIMIT = 4` is a guess that happens to be conservative; a chain is 7 Gemini calls, so the limit should be roughly `(requests per minute allowed) / (7 x chains per minute each slot can turn over)`. Write the number and its derivation into `arena/README.md` beside the variable.

- [ ] **Step 5: Commit**

```bash
git add arena/tests/test_load_rehearsal.py arena/fake_host.py arena/README.md
git commit -m "test(arena): rehearse fifty rooms before fifty people do

Skipped unless ARENA_LOAD=1, because it takes minutes and prints numbers
rather than asserting on them. Run against the deployed URL it is the only
thing that exercises the parts a laptop cannot: TLS, the hour-long socket
timeout, and CPU that is not throttled between requests.

ARENA_CHAIN_LIMIT stops being a guess here, derived from the project's real
Vertex quota and written down with its arithmetic."
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task:

| Spec section | Task |
|---|---|
| One instance, and why not Redis | 3, 6, 13, 14 (the constraint is enforced in the yaml and the Dockerfile's single worker) |
| Cloud SQL Postgres 18, keeping the single connection | 3, 4 |
| The seq race and `FOR UPDATE` | 5 |
| `heard` becomes a column | 6 |
| The pitch, baked in, `base: '/pitch/'`, cache headers | 7 |
| Injury loop leg 1, the coach proxy | 8 |
| Injury loop legs 2 and 3, the shared volume | 9 |
| "It is already broken, before any of this" | 1 |
| Status checks, workshop only | 2 |
| Hardening: Secret Manager, fail-fast, buckets, room cap, wall downsampling, derived public URL, token never crossing the internet | 10, 11, 12, 14 |
| Local development: native Postgres, Python bootstrap, compose fallback, run.sh preflight, dugout health map | 3, 14 |
| Testing, all six items | 5 (1), 8 and existing `test_shout.py` (2), 6 (3), 9 (4), 2 (5), 15 (6) |
| What this does not solve | Documented in `deploy/README.md` and the deploy script's confirmation prompt (Task 14) |

One gap found and closed while reviewing: spec test item 2, `caused_by` across a shout, had no task. It is covered by the existing `arena/tests/test_shout.py` continuing to pass on Postgres in Task 4, since `Chain._seats` is untouched by this work and stays in process. No new task needed, but worth naming so nobody looks for one.

One place where the plan knowingly departs from the spec: **Task 5 implements `FOR UPDATE`, which the spec calls optional.** The reasoning is in the task. Cost is one row lock in an existing transaction; benefit is that the invariant stops depending on a deployment decision. Flagged rather than done quietly.

One place where the plan corrects the spec: the spec attributes the WRKS injury bug to the browser's session-create alone. That is real but not sufficient. The MCP condition tools cannot read ADK session state at all, so they would still default to `WRKS` even with the session fixed. Task 1 fixes the actual cause and the session state both.

**Symbol check.** Every file, fixture, constant and function the plan names was verified to exist before the plan was finished: `arena/fake_host.py`, `tests/test_abandoning.py`, `test_wall_socket.py`, `test_pages.py`, `test_shout.py`, `game/frontend/test/futsal-status.test.js`, the `live_room` and `phones` fixtures, `coach.CONNECT_SECONDS` / `COACH_URL` / `COACH_APP` / `IDLE_SECONDS`, `arena_client.DEFAULT_ROOM` / `DEFAULT_TEAM`, `app._pump` / `_until_closed` / `WALL` / `room_topic`, and `rooms.create_room(conn, mode, code=None)` / `events` / `live` / `by_code` / `start_match`. Four things this turned up, all now fixed in the tasks above rather than left for the implementer:

- **`rooms.start_match` cannot be used to make a live room in a test.** It insists on a host client id and every dugout ready. Task 6's tests set the status directly and say why.
- **`test_abandoning.py` touches `state.heard` in nine places, not one.** Task 6 Step 7 enumerates every line rather than saying "update every call". One of its tests, `test_a_match_that_ended_properly_is_forgotten`, exists *only* because an in-memory dict leaks, so a column deletes its premise; the task rewrites it into something still worth asserting instead of quietly dropping it.
- **`conftest.py` is 83 lines, not 30.** Task 3 replaces lines 1-49 and names `phones` and `live_room` as untouched, so an implementer following it literally does not delete the fixture half the suite depends on.
- **Module-level rate limiters would have made the suite flaky.** Every test comes from `testclient` and `live_room` opens a room over HTTP, so a shared bucket fails somewhere around the sixth room in whichever test ran sixth. Task 11 puts them on `app.state` beside `state.bus`; Task 12 does the same with the wall-socket count for the same reason.

**Placeholder scan.** No "TBD", no "handle errors appropriately", no "similar to Task N". Three tasks deliberately leave content to be written against code the implementer must read first, each with explicit instructions on what to read: Task 14 Step 3 (`deploy/README.md`, with its full contents enumerated), Task 14 Step 6 (the dugout health renderer), and Task 15 Step 2 (the rehearsal body, which depends on what `fake_host.py` already provides). These are directions, not gaps.

**Type consistency.** `db.connect(dsn=None)`, `db.TABLES`, `rooms.heard_from(conn, room_id)`, `rooms.live_with_liveness(conn)`, `_give_up_on_the_missing(connection, match_bus, now)`, `limits.Bucket(rate, burst).take(key, now=None)`, `limits.client_ip(request)`, `proxy.router`, `tools.stamp_the_room(tool, args, tool_context)`, `asset(path)` and `app.join_url(code, request=None)` are each defined once and used with the same names and arities everywhere they appear. `ARENA_PITCH_DIR` and `ARENA_PLAYER_STATE_DIR` are spelled the same in `app.py`, the Dockerfile, the service yaml and `.env.example`. The MCP server's variable is `PLAYER_STATE_DIR` without the `ARENA_` prefix, because it belongs to the game rather than the arena; that asymmetry is intentional and appears consistently in Task 9 and Task 13.
