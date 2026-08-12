# Arena Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `arena/`, a new FastAPI service on :8003 that owns rooms, seats, player identity and the live match bus, plus a `--fake-host` script that replays a recorded match into it without a browser.

**Architecture:** A fifth process beside the pitch (:5173), coach (:8000), captain (:8001) and dugout (:8002). SQLite in WAL mode holds players, rooms, seats and an append-only event log. An in-process publish/subscribe bus fans host frames out to two WebSocket endpoints: `/ws/rooms/{code}` for one match and `/ws/wall` for a summary of all live matches. Nothing in `game/`, `dugout/` or `game/agents/` is touched — this step adds a service and leaves the repo working exactly as it does today.

**Tech Stack:** Python ≥3.14, `uv`, FastAPI 0.136.3, uvicorn 0.48.0, `websockets` 17.0.1, stdlib `sqlite3`, pytest 8.4.2 with pytest-asyncio 1.2.0 in auto mode.

This plan implements **step 1 of 7** from the build order in
`docs/superpowers/specs/2026-08-12-arena-multiplayer-leaderboard-design.md`:
*"Arena skeleton: rooms, seats, join, the WebSocket bus, `--fake-host`."*
Profiles, the mobile controller, shout orchestration, scoring, the viewer path
and the dugout re-point are steps 2–7 and are **out of scope here**.

## Global Constraints

- **Nothing outside `arena/` changes.** No edits to `game/`, `dugout/`, or the root README in this step.
- **Python `requires-python = ">=3.14"`**, matching every other subproject.
- **`[tool.uv] package = false`** — modules are imported flat (`import rooms`), never as a package.
- **`arena/tests/__init__.py` must exist and be empty.** That empty file is what puts `arena/` on `sys.path` under pytest's default prepend import mode; without it every `import rooms` in a test fails.
- **Pins copied from `dugout/pyproject.toml`:** `fastapi==0.136.3`, `uvicorn==0.48.0`, `pytest==8.4.2`, `pytest-asyncio==1.2.0`.
- **`asyncio_mode = "auto"`** — async tests are bare `async def`, no `@pytest.mark.asyncio`.
- **Tests are plain module-level functions with sentence-like names.** No test classes. Follow `dugout/tests/test_channel.py`.
- **Every route that publishes to the bus is `async def`.** A sync `def` route runs in a threadpool, and `asyncio.Queue.put_nowait` from a non-loop thread corrupts a waiting consumer. `dugout/channel.py` documents this same hazard.
- **The raw email address is never stored and never leaves the service.** Only a salted SHA-256 hash and a masked form (`a***x@example.com`).
- **Room codes are 4 characters with no `O`/`0` and no `I`/`1`.**
- **`mode` is `solo` or `versus`. `status` is `lobby`, `live`, `finished` or `abandoned`. `team` is `blue` or `red`.**
- **Comments explain *why*, not *what*.** Match the density in `dugout/channel.py` and `game/agents/specialist_agents/tools.py`.
- **`arena/run.sh` defaults to `PORT=8003`** and carries the Apache 2.0 header, like `dugout/run.sh`.

### Four deliberate departures from the spec

Flag these to the reviewer; each is a small, defensible call.

1. **`room.ranked` is added as a column.** The spec's Scoring section needs it ("a room is ranked unless it is the reserved `workshop` room, or its host ever reported a speed other than 1.0, or it ended `abandoned`"). Step 1 sets it at creation for the workshop rule only; the speed rule lands in step 5, and abandonment is read from `room.status`, not duplicated here.
2. **`seat.philosophy` is added as a column.** The spec's `/join/{code}` screen collects one of four tactical philosophies. Applying them as profile patches is step 2's job, but the join flow has to record the choice somewhere, and the seat is where it belongs.
3. **The `result` table is deferred to step 5.** Nothing in step 1 reads or writes it, so creating it now would be an unused table.
4. **`/ws/wall` relays every host frame rather than downsampling to the spec's 4 Hz.** Throttling needs a clock, and a clock in the hot path is awkward to test without injecting one. The bus already bounds each subscriber's queue and drops the oldest frame, so a wall that cannot keep up degrades on its own rather than holding up a match. The downsample belongs with the wall UI in step 6, where there is a measured frame budget to aim at.

---

## File Structure

Everything is created under a new top-level `arena/` directory.

| File | Responsibility |
|---|---|
| `arena/pyproject.toml` | Dependencies and pytest config. |
| `arena/.python-version` | `3.14`, matching `dugout/`. |
| `arena/run.sh` | Sync the venv and start uvicorn on :8003. |
| `arena/db.py` | The SQLite schema and how to open a connection. Nothing else. |
| `arena/codes.py` | Room-code alphabet, generation and validation. Pure; no database. |
| `arena/identity.py` | Email hashing and masking, session token signing. Pure; no database. |
| `arena/rooms.py` | The room and seat rules, the event log, and the read models (`snapshot`, `live`). Takes a connection; knows nothing about HTTP. |
| `arena/bus.py` | In-process publish/subscribe for live match traffic. Knows nothing about rooms. |
| `arena/app.py` | FastAPI: request models, HTTP routes, both WebSocket endpoints. The only file that knows about both `rooms` and `bus`. |
| `arena/fake_host.py` | Parse a recorded match log and replay it into a room socket. |
| `arena/fixtures/match-3-1.jsonl` | One recorded solo match, 3–1, first goal at 27.4s. |
| `arena/tests/__init__.py` | Empty. Puts `arena/` on `sys.path`. |
| `arena/tests/conftest.py` | `db_path`, `conn`, `client`, `phones`, `live_room` fixtures. |
| `arena/tests/test_db.py` | Schema and connection. |
| `arena/tests/test_codes.py` | Code generation. |
| `arena/tests/test_identity.py` | Hashing, masking, tokens. |
| `arena/tests/test_rooms.py` | The state machine, the log and the read models. |
| `arena/tests/test_bus.py` | Fan-out, isolation, back-pressure. |
| `arena/tests/test_app.py` | HTTP routes. |
| `arena/tests/test_room_socket.py` | `/ws/rooms/{code}`. |
| `arena/tests/test_wall_socket.py` | `/ws/wall`. |
| `arena/tests/test_fake_host.py` | Log parsing, pacing, and a replay into the live app. |

`rooms.py` is the one file that grows across two tasks (4 and 5). It stays
under ~200 lines and splitting the rules from the read models would put the
`seat` table's shape in two places.

---

## Task 1: Project scaffold and the SQLite schema

**Files:**
- Create: `arena/pyproject.toml`
- Create: `arena/.python-version`
- Create: `arena/db.py`
- Create: `arena/tests/__init__.py` (empty)
- Create: `arena/tests/conftest.py`
- Test: `arena/tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `db.DB_PATH: Path`, `db.SCHEMA: str`, `db.connect(path=DB_PATH) -> sqlite3.Connection`, `db.init_db(conn) -> None`. Fixtures `db_path -> Path` and `conn -> sqlite3.Connection`.

- [ ] **Step 1: Create the project files**

`arena/pyproject.toml`:

```toml
[project]
name = "futsal-worldcup-arena"
version = "0.1.0"
description = "Rooms, seats, players and the live match bus for multi-tenant futsal."
requires-python = ">=3.14"
dependencies = [
    "fastapi==0.136.3",
    "uvicorn==0.48.0",
    "websockets==17.0.1",
    "pydantic==2.13.4",
]

# app.py imports `rooms`, `bus` and `db` as top-level modules from this
# directory, so there is nothing to build or install.
[tool.uv]
package = false

[dependency-groups]
dev = [
    "pytest==8.4.2",
    "pytest-asyncio==1.2.0",
    "httpx==0.28.1",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`arena/.python-version`:

```
3.14
```

`arena/tests/__init__.py`: create it empty. It is not decoration — pytest's
prepend import mode walks up from a test file to the first directory *without*
an `__init__.py`, and that directory is what lands on `sys.path`. With this
file present that directory is `arena/`, so `import db` works.

- [ ] **Step 2: Write the fixtures**

`arena/tests/conftest.py`:

```python
"""Shared fixtures. Every test gets its own throwaway database file."""

import pytest

import db


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "arena.db"


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    db.init_db(connection)
    yield connection
    connection.close()
```

- [ ] **Step 3: Write the failing test**

`arena/tests/test_db.py`:

```python
import sqlite3

import pytest

import db


def test_the_schema_creates_every_table(conn):
    names = {row["name"] for row in
             conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"player", "room", "seat", "event"} <= names


def test_a_file_backed_database_runs_in_wal_mode(db_path):
    # The wall socket reads while a match is writing events. WAL is what lets
    # those happen at the same time without a locked database.
    connection = db.connect(db_path)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    connection.close()


def test_init_db_over_an_existing_database_keeps_the_rows(db_path):
    first = db.connect(db_path)
    db.init_db(first)
    first.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES ('Alex Rivera', 'hash', 'a***x@example.com', 0)"
    )
    first.commit()
    first.close()

    second = db.connect(db_path)
    db.init_db(second)
    assert second.execute("SELECT COUNT(*) AS n FROM player").fetchone()["n"] == 1
    second.close()


def test_two_rooms_cannot_share_a_code(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'solo', 'lobby', 1, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                     "VALUES ('K7F2', 'versus', 'lobby', 1, 0)")


def test_one_dugout_holds_one_player(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'versus', 'lobby', 1, 0)")
    for name in ("Alex", "Sam"):
        conn.execute(
            "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
            "VALUES (?, ?, 'x***x@e.com', 0)",
            (name, name),
        )
    conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                 "VALUES (1, 'blue', 1, 'high press', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                     "VALUES (1, 'blue', 2, 'counter', 0, 0)")


def test_a_room_cannot_reuse_a_sequence_number(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'solo', 'live', 1, 0)")
    conn.execute("INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
                 "VALUES (1, 1, 'kickoff', '{}', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
                     "VALUES (1, 1, 'goal', '{}', 100, 0)")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_db.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'db'`.

- [ ] **Step 5: Write the implementation**

`arena/db.py`:

```python
"""SQLite storage for the arena. One file, WAL mode, no ORM.

Every other module takes an open connection rather than reaching for a global,
so a test can point at a throwaway database without touching module state.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "arena.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT    NOT NULL,
    email_hash   TEXT    NOT NULL UNIQUE,
    email_masked TEXT    NOT NULL,
    created_at   REAL    NOT NULL
);

-- `ranked` covers the reserved workshop room and, from step 5, a host that
-- reported a speed other than 1.0. Abandonment is read from `status`.
CREATE TABLE IF NOT EXISTS room (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT    NOT NULL UNIQUE,
    mode           TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    host_client_id TEXT,
    ranked         INTEGER NOT NULL,
    created_at     REAL    NOT NULL,
    finished_at    REAL
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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id      INTEGER NOT NULL REFERENCES room(id),
    seq          INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    match_ms     INTEGER,
    wall_ts      REAL    NOT NULL,
    UNIQUE (room_id, seq)
);

CREATE INDEX IF NOT EXISTS room_by_status ON room (status);
"""


def connect(path=DB_PATH):
    """Open the arena database, creating the file if it is not there yet."""
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # WAL lets the wall socket read while a match is writing events.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db(connection):
    """Create every table. Safe to call against a database that already has them."""
    connection.executescript(SCHEMA)
    connection.commit()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_db.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add arena/pyproject.toml arena/.python-version arena/db.py arena/tests/
git commit -m "feat(arena): scaffold the service and its SQLite schema"
```

---

## Task 2: Room codes

**Files:**
- Create: `arena/codes.py`
- Test: `arena/tests/test_codes.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `codes.ALPHABET: str`, `codes.LENGTH: int` (4), `codes.WORKSHOP: str` (`"workshop"`), `codes.CodesExhausted(Exception)`, `codes.generate(taken: Callable[[str], bool]) -> str`, `codes.is_valid(code: str) -> bool`.

- [ ] **Step 1: Write the failing test**

`arena/tests/test_codes.py`:

```python
import pytest

import codes


def test_a_generated_code_is_four_characters_from_the_alphabet():
    code = codes.generate(lambda candidate: False)
    assert len(code) == codes.LENGTH == 4
    assert set(code) <= set(codes.ALPHABET)


def test_the_alphabet_drops_the_characters_people_misread():
    # The code gets read off a big screen and typed on a phone, sometimes
    # shouted across a room. O/0 and I/1 are where that goes wrong.
    for character in "O0I1":
        assert character not in codes.ALPHABET


def test_generate_never_hands_out_a_code_that_is_taken():
    handed_out = set()
    for _ in range(50):
        code = codes.generate(handed_out.__contains__)
        assert code not in handed_out
        handed_out.add(code)


def test_generate_gives_up_rather_than_spinning_forever():
    with pytest.raises(codes.CodesExhausted):
        codes.generate(lambda candidate: True)


def test_the_workshop_code_is_not_one_generate_could_produce():
    # The dugout reserves it, so a generated code must never collide with it.
    assert not codes.is_valid(codes.WORKSHOP)


def test_is_valid_rejects_the_wrong_length_and_the_banned_letters():
    assert codes.is_valid("K7F2")
    assert not codes.is_valid("K7F")
    assert not codes.is_valid("K7F22")
    assert not codes.is_valid("K0F2")
    assert not codes.is_valid("k7f2")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_codes.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'codes'`.

- [ ] **Step 3: Write the implementation**

`arena/codes.py`:

```python
"""Room codes: four characters a person can read off a screen and type."""

import secrets

# No O/0 and no I/1. The code is read across a noisy room and typed on a phone.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
LENGTH = 4

# Reserved for the dugout's workshop, which is not a room anybody joins by
# code. Lower case and eight characters, so `generate` can never collide.
WORKSHOP = "workshop"

_MAX_TRIES = 200


class CodesExhausted(Exception):
    """No free code turned up. The arena is holding far too many rooms."""


def generate(taken):
    """Return a fresh code. `taken(code)` answers whether one is already in use."""
    for _ in range(_MAX_TRIES):
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        if not taken(code):
            return code
    raise CodesExhausted(f"no free room code after {_MAX_TRIES} tries")


def is_valid(code):
    """True for a code this module could have produced."""
    return len(code) == LENGTH and all(character in ALPHABET for character in code)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_codes.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/codes.py arena/tests/test_codes.py
git commit -m "feat(arena): generate four-character room codes"
```

---

## Task 3: Identity

**Files:**
- Create: `arena/identity.py`
- Test: `arena/tests/test_identity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `identity.normalise_email(email: str) -> str`, `identity.hash_email(email: str, salt: str) -> str`, `identity.mask_email(email: str) -> str`, `identity.sign_token(player_id: int, secret: str) -> str`, `identity.verify_token(token: str | None, secret: str) -> int | None`.

- [ ] **Step 1: Write the failing test**

`arena/tests/test_identity.py`:

```python
import identity

SALT = "test-salt"


def test_the_same_address_always_hashes_the_same_way():
    assert identity.hash_email("alex@example.com", SALT) == \
           identity.hash_email("alex@example.com", SALT)


def test_case_and_stray_spaces_do_not_make_a_second_player():
    assert identity.hash_email("  Alex@Example.COM ", SALT) == \
           identity.hash_email("alex@example.com", SALT)


def test_a_different_salt_gives_a_different_hash():
    assert identity.hash_email("alex@example.com", "one") != \
           identity.hash_email("alex@example.com", "two")


def test_the_hash_carries_none_of_the_address():
    digest = identity.hash_email("alex@example.com", SALT)
    assert "alex" not in digest
    assert "example" not in digest


def test_masking_keeps_the_first_letter_the_last_letter_and_the_domain():
    assert identity.mask_email("alex@example.com") == "a***x@example.com"


def test_masking_a_one_letter_local_part_does_not_show_it_twice():
    assert identity.mask_email("a@example.com") == "a***@example.com"


def test_masking_normalises_first_so_the_board_never_shows_shouty_addresses():
    assert identity.mask_email(" Alex@Example.COM ") == "a***x@example.com"


def test_a_signed_token_round_trips():
    assert identity.verify_token(identity.sign_token(42, "secret"), "secret") == 42


def test_a_token_signed_with_another_secret_is_refused():
    assert identity.verify_token(identity.sign_token(42, "secret"), "other") is None


def test_editing_the_player_id_out_of_a_token_is_refused():
    _, _, mac = identity.sign_token(42, "secret").partition(".")
    assert identity.verify_token(f"99.{mac}", "secret") is None


def test_rubbish_is_refused_rather_than_raising():
    assert identity.verify_token(None, "secret") is None
    assert identity.verify_token("", "secret") is None
    assert identity.verify_token("not-a-token", "secret") is None
    assert identity.verify_token("42", "secret") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_identity.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'identity'`.

- [ ] **Step 3: Write the implementation**

`arena/identity.py`:

```python
"""Player identity: an email becomes a hash and a mask, never a stored address.

The email exists only so one player keeps one place on the leaderboard across
repeat plays. Nothing here can recover the address it was handed.
"""

import base64
import hashlib
import hmac


def normalise_email(email):
    """Trim and lower-case, so `Alex@Example.com ` and `alex@example.com` match."""
    return email.strip().lower()


def hash_email(email, salt):
    """Salted SHA-256 of the normalised address, hex encoded."""
    return hashlib.sha256(f"{salt}:{normalise_email(email)}".encode()).hexdigest()


def mask_email(email):
    """`alex@example.com` -> `a***x@example.com`. The board renders only this."""
    local, _, domain = normalise_email(email).partition("@")
    if len(local) >= 2:
        local = f"{local[0]}***{local[-1]}"
    elif local:
        # One letter, so showing it twice would give the whole thing away.
        local = f"{local[0]}***"
    return f"{local}@{domain}"


def sign_token(player_id, secret):
    """A session token: the player id plus an HMAC over it."""
    body = str(player_id)
    return f"{body}.{_mac(body, secret)}"


def verify_token(token, secret):
    """Return the player id, or None if this was not signed with `secret`."""
    body, _, mac = (token or "").partition(".")
    if not body.isdigit() or not hmac.compare_digest(mac, _mac(body, secret)):
        return None
    return int(body)


def _mac(body, secret):
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_identity.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/identity.py arena/tests/test_identity.py
git commit -m "feat(arena): hash and mask emails, sign session tokens"
```

---

## Task 4: The room and seat state machine

**Files:**
- Create: `arena/rooms.py`
- Test: `arena/tests/test_rooms.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`; `codes.generate`, `codes.is_valid`, `codes.WORKSHOP`; `identity.hash_email`, `identity.mask_email`; the `conn` fixture.
- Produces: `rooms.MODES`, `rooms.STATUSES`, `rooms.TEAMS`, `rooms.PHILOSOPHIES`, `rooms.RoomError(Exception)`, `rooms.required_teams(mode) -> tuple[str, ...]`, `rooms.create_player(conn, display_name, email, salt) -> int`, `rooms.get_player(conn, player_id) -> sqlite3.Row | None`, `rooms.create_room(conn, mode, code=None) -> sqlite3.Row`, `rooms.by_code(conn, code) -> sqlite3.Row | None`, `rooms.take_seat(conn, room_id, team, player_id, philosophy) -> None`, `rooms.set_ready(conn, room_id, team, ready) -> None`, `rooms.can_kick_off(conn, room_id) -> bool`, `rooms.start_match(conn, room_id, host_client_id) -> None`, `rooms.finish_match(conn, room_id, status="finished") -> None`.

- [ ] **Step 1: Write the failing test**

`arena/tests/test_rooms.py`:

```python
import pytest

import codes
import rooms

SALT = "test-salt"


@pytest.fixture
def alex(conn):
    return rooms.create_player(conn, "Alex Rivera", "alex@example.com", SALT)


@pytest.fixture
def sam(conn):
    return rooms.create_player(conn, "Sam Okafor", "sam@example.com", SALT)


def live_solo(conn, player_id):
    """A solo room already kicked off, with `phone-7` holding physics."""
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", player_id, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.start_match(conn, room["id"], "phone-7")
    return room


def test_a_new_player_keeps_a_masked_email_and_no_address(conn, alex):
    row = rooms.get_player(conn, alex)
    assert row["display_name"] == "Alex Rivera"
    assert row["email_masked"] == "a***x@example.com"
    assert "alex@example.com" not in " ".join(str(value) for value in tuple(row))


def test_the_same_email_comes_back_as_the_same_player(conn, alex):
    again = rooms.create_player(conn, "Alex R", "ALEX@example.com", SALT)
    assert again == alex
    # They typed a shorter name this time, and the board follows the latest.
    assert rooms.get_player(conn, alex)["display_name"] == "Alex R"


def test_a_new_room_opens_in_the_lobby_with_a_typable_code(conn):
    room = rooms.create_room(conn, "solo")
    assert room["status"] == "lobby"
    assert room["mode"] == "solo"
    assert room["host_client_id"] is None
    assert room["ranked"] == 1
    assert codes.is_valid(room["code"])


def test_the_workshop_room_is_never_ranked(conn):
    room = rooms.create_room(conn, "solo", code=codes.WORKSHOP)
    assert room["code"] == codes.WORKSHOP
    assert room["ranked"] == 0


def test_the_workshop_room_cannot_be_opened_twice(conn):
    rooms.create_room(conn, "solo", code=codes.WORKSHOP)
    with pytest.raises(rooms.RoomError, match="already exists"):
        rooms.create_room(conn, "solo", code=codes.WORKSHOP)


def test_an_unknown_mode_is_refused(conn):
    with pytest.raises(rooms.RoomError, match="mode must be"):
        rooms.create_room(conn, "battle-royale")


def test_a_solo_room_needs_only_the_blue_dugout(conn):
    assert rooms.required_teams("solo") == ("blue",)
    assert rooms.required_teams("versus") == ("blue", "red")


def test_taking_a_seat_records_the_philosophy_and_leaves_them_not_ready(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    seat = conn.execute("SELECT * FROM seat WHERE room_id = ?", (room["id"],)).fetchone()
    assert (seat["team"], seat["player_id"]) == ("blue", alex)
    assert seat["philosophy"] == "high press"
    assert seat["ready"] == 0


def test_the_red_dugout_does_not_exist_in_a_solo_room(conn, alex):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="only a blue dugout"):
        rooms.take_seat(conn, room["id"], "red", alex, "counter")


def test_a_team_that_is_not_a_team_is_refused(conn, alex):
    room = rooms.create_room(conn, "versus")
    with pytest.raises(rooms.RoomError, match="team must be"):
        rooms.take_seat(conn, room["id"], "green", alex, "counter")


def test_a_taken_dugout_cannot_be_taken_again(conn, alex, sam):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    with pytest.raises(rooms.RoomError, match="the blue dugout is taken"):
        rooms.take_seat(conn, room["id"], "blue", sam, "low block")


def test_one_player_cannot_manage_both_sides(conn, alex):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    with pytest.raises(rooms.RoomError, match="already have a dugout"):
        rooms.take_seat(conn, room["id"], "red", alex, "counter")


def test_an_unknown_philosophy_is_refused(conn, alex):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="philosophy must be"):
        rooms.take_seat(conn, room["id"], "blue", alex, "park the bus")


def test_a_solo_room_kicks_off_once_its_one_manager_is_ready(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "tiki-taka")
    assert not rooms.can_kick_off(conn, room["id"])
    rooms.set_ready(conn, room["id"], "blue", True)
    assert rooms.can_kick_off(conn, room["id"])


def test_a_versus_room_waits_for_both_managers(conn, alex, sam):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    assert not rooms.can_kick_off(conn, room["id"])
    rooms.take_seat(conn, room["id"], "red", sam, "low block")
    rooms.set_ready(conn, room["id"], "red", True)
    assert rooms.can_kick_off(conn, room["id"])


def test_a_manager_can_change_their_mind_about_being_ready(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.set_ready(conn, room["id"], "blue", False)
    assert not rooms.can_kick_off(conn, room["id"])


def test_marking_an_empty_dugout_ready_is_an_error(conn):
    room = rooms.create_room(conn, "versus")
    with pytest.raises(rooms.RoomError, match="nobody is in the red dugout"):
        rooms.set_ready(conn, room["id"], "red", True)


def test_starting_a_match_records_the_host(conn, alex):
    room = live_solo(conn, alex)
    started = rooms.by_code(conn, room["code"])
    assert started["status"] == "live"
    assert started["host_client_id"] == "phone-7"


def test_a_match_cannot_start_before_everyone_is_ready(conn, alex):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    with pytest.raises(rooms.RoomError, match="not every dugout is ready"):
        rooms.start_match(conn, room["id"], "phone-7")


def test_a_match_cannot_start_without_somebody_holding_physics(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    with pytest.raises(rooms.RoomError, match="needs a host"):
        rooms.start_match(conn, room["id"], "")


def test_a_live_match_cannot_kick_off_a_second_time(conn, alex):
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="not every dugout is ready"):
        rooms.start_match(conn, room["id"], "phone-9")


def test_nobody_can_sit_down_after_kick_off(conn, alex, sam):
    # The status check runs before the seat check, so a latecomer is told the
    # match started rather than that the dugout is taken.
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="already started"):
        rooms.take_seat(conn, room["id"], "blue", sam, "counter")


def test_a_live_match_can_be_abandoned(conn, alex):
    room = live_solo(conn, alex)
    rooms.finish_match(conn, room["id"], "abandoned")
    ended = rooms.by_code(conn, room["code"])
    assert ended["status"] == "abandoned"
    assert ended["finished_at"] is not None


def test_a_match_in_the_lobby_cannot_finish(conn):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="only a live match"):
        rooms.finish_match(conn, room["id"])


def test_a_match_cannot_end_in_a_status_that_is_not_an_ending(conn, alex):
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="finished or abandoned"):
        rooms.finish_match(conn, room["id"], "lobby")


def test_a_room_that_is_not_there_says_so(conn):
    assert rooms.by_code(conn, "ZZZZ") is None
    with pytest.raises(rooms.RoomError, match="there is no room"):
        rooms.take_seat(conn, 999, "blue", 1, "counter")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_rooms.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'rooms'`.

- [ ] **Step 3: Write the implementation**

`arena/rooms.py`:

```python
"""Rooms, seats and the moves between them.

Every function takes a connection. The rules here are the ones a reviewer
should be able to read in one sitting: who may sit down, when a match may kick
off, and which status may follow which. Nothing in this file knows about HTTP.
"""

import time

import codes
import identity

MODES = ("solo", "versus")
STATUSES = ("lobby", "live", "finished", "abandoned")
TEAMS = ("blue", "red")

# Named profile patches applied to all four roles at kick-off. Applying them is
# step 2's job; the join form collects the choice and the seat records it.
PHILOSOPHIES = ("high press", "tiki-taka", "counter", "low block")


class RoomError(Exception):
    """A move the rules do not allow. The text is fit to show a player as-is."""


def required_teams(mode):
    """The dugouts that must be filled before this mode can kick off."""
    return ("blue",) if mode == "solo" else TEAMS


def create_player(conn, display_name, email, salt):
    """Insert or find a player, keyed on the hashed email. Returns the id."""
    email_hash = identity.hash_email(email, salt)
    existing = conn.execute(
        "SELECT id FROM player WHERE email_hash = ?", (email_hash,)
    ).fetchone()
    if existing:
        # A repeat player keeps one row so the board keeps one entry for them,
        # but they may well have typed a different name this time.
        conn.execute("UPDATE player SET display_name = ? WHERE id = ?",
                     (display_name, existing["id"]))
        conn.commit()
        return existing["id"]

    cursor = conn.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES (?, ?, ?, ?)",
        (display_name, email_hash, identity.mask_email(email), time.time()),
    )
    conn.commit()
    return cursor.lastrowid


def get_player(conn, player_id):
    return conn.execute("SELECT * FROM player WHERE id = ?", (player_id,)).fetchone()


def by_code(conn, code):
    return conn.execute("SELECT * FROM room WHERE code = ?", (code,)).fetchone()


def create_room(conn, mode, code=None):
    """Open a room in the lobby. Pass `code` only for the reserved workshop room."""
    if mode not in MODES:
        raise RoomError(f"mode must be one of {', '.join(MODES)}")
    if code is None:
        code = codes.generate(lambda candidate: by_code(conn, candidate) is not None)
    elif by_code(conn, code) is not None:
        raise RoomError(f"room {code} already exists")

    conn.execute(
        "INSERT INTO room (code, mode, status, ranked, created_at) "
        "VALUES (?, ?, 'lobby', ?, ?)",
        (code, mode, 0 if code == codes.WORKSHOP else 1, time.time()),
    )
    conn.commit()
    return by_code(conn, code)


def take_seat(conn, room_id, team, player_id, philosophy):
    """Sit a player in a dugout."""
    room = _room(conn, room_id)
    if room["status"] != "lobby":
        raise RoomError("that match has already started")
    if team not in TEAMS:
        raise RoomError(f"team must be one of {', '.join(TEAMS)}")
    if team not in required_teams(room["mode"]):
        raise RoomError("a solo room has only a blue dugout")
    if philosophy not in PHILOSOPHIES:
        raise RoomError(f"philosophy must be one of {', '.join(PHILOSOPHIES)}")
    if conn.execute("SELECT 1 FROM seat WHERE room_id = ? AND team = ?",
                    (room_id, team)).fetchone():
        raise RoomError(f"the {team} dugout is taken")
    if conn.execute("SELECT 1 FROM seat WHERE room_id = ? AND player_id = ?",
                    (room_id, player_id)).fetchone():
        raise RoomError("you already have a dugout in this match")

    conn.execute(
        "INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (room_id, team, player_id, philosophy, time.time()),
    )
    conn.commit()


def set_ready(conn, room_id, team, ready):
    changed = conn.execute(
        "UPDATE seat SET ready = ? WHERE room_id = ? AND team = ?",
        (1 if ready else 0, room_id, team),
    ).rowcount
    if not changed:
        raise RoomError(f"nobody is in the {team} dugout")
    conn.commit()


def can_kick_off(conn, room_id):
    """True when every dugout this mode needs is filled and ready."""
    room = _room(conn, room_id)
    if room["status"] != "lobby":
        return False
    ready = {row["team"] for row in conn.execute(
        "SELECT team FROM seat WHERE room_id = ? AND ready = 1", (room_id,))}
    return set(required_teams(room["mode"])) <= ready


def start_match(conn, room_id, host_client_id):
    """Hand physics to exactly one client and go live."""
    if not host_client_id:
        raise RoomError("a match needs a host")
    if not can_kick_off(conn, room_id):
        raise RoomError("not every dugout is ready")
    conn.execute("UPDATE room SET status = 'live', host_client_id = ? WHERE id = ?",
                 (host_client_id, room_id))
    conn.commit()


def finish_match(conn, room_id, status="finished"):
    if status not in ("finished", "abandoned"):
        raise RoomError("a match ends finished or abandoned")
    if _room(conn, room_id)["status"] != "live":
        raise RoomError("only a live match can end")
    conn.execute("UPDATE room SET status = ?, finished_at = ? WHERE id = ?",
                 (status, time.time(), room_id))
    conn.commit()


def _room(conn, room_id):
    room = conn.execute("SELECT * FROM room WHERE id = ?", (room_id,)).fetchone()
    if room is None:
        raise RoomError(f"there is no room {room_id}")
    return room
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_rooms.py -v`
Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/rooms.py arena/tests/test_rooms.py
git commit -m "feat(arena): add the room and seat state machine"
```

---

## Task 5: The event log and the read models

**Files:**
- Modify: `arena/rooms.py` (add `import json` and four functions at the end)
- Modify: `arena/tests/test_rooms.py` (append tests)

**Interfaces:**
- Consumes: everything Task 4 produced.
- Produces: `rooms.append_event(conn, room_id, kind, payload, match_ms=None) -> int`, `rooms.events(conn, room_id) -> list[dict]` with keys `seq`, `kind`, `match_ms`, `payload`; `rooms.snapshot(conn, room_id) -> dict` with keys `code`, `mode`, `status`, `ranked`, `seats`, `open_seats`; `rooms.live(conn) -> list[dict]` with keys `code`, `mode`, `blue`, `red`.

- [ ] **Step 1: Write the failing test**

Append to `arena/tests/test_rooms.py`, and add `import json` to its imports:

```python
def test_events_are_numbered_from_one_within_each_room(conn, alex, sam):
    first = live_solo(conn, alex)
    second = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, second["id"], "blue", sam, "counter")
    rooms.set_ready(conn, second["id"], "blue", True)
    rooms.start_match(conn, second["id"], "phone-8")

    assert rooms.append_event(conn, first["id"], "kickoff", {}) == 1
    assert rooms.append_event(conn, first["id"], "goal", {"team": "blue"}) == 2
    assert rooms.append_event(conn, second["id"], "kickoff", {}) == 1


def test_an_event_payload_comes_back_the_way_it_went_in(conn, alex):
    room = live_solo(conn, alex)
    rooms.append_event(conn, room["id"], "goal",
                       {"team": "blue", "scorer": "forward"}, match_ms=27400)
    assert rooms.events(conn, room["id"]) == [
        {"seq": 1, "kind": "goal", "match_ms": 27400,
         "payload": {"team": "blue", "scorer": "forward"}}
    ]


def test_a_room_with_nothing_logged_has_an_empty_log(conn, alex):
    assert rooms.events(conn, live_solo(conn, alex)["id"]) == []


def test_a_lobby_snapshot_names_the_seat_still_open(conn, alex):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    snapshot = rooms.snapshot(conn, room["id"])
    assert snapshot["code"] == room["code"]
    assert snapshot["status"] == "lobby"
    assert snapshot["ranked"] is True
    assert snapshot["open_seats"] == ["red"]
    assert snapshot["seats"]["blue"] == {
        "name": "Alex Rivera",
        "email": "a***x@example.com",
        "philosophy": "high press",
        "ready": False,
    }


def test_a_full_solo_room_has_no_open_seats(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    assert rooms.snapshot(conn, room["id"])["open_seats"] == []


def test_a_snapshot_never_carries_an_unmasked_address(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    assert "alex@example.com" not in json.dumps(rooms.snapshot(conn, room["id"]))


def test_the_wall_lists_live_rooms_with_both_managers(conn, alex, sam):
    waiting = rooms.create_room(conn, "solo")
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    rooms.take_seat(conn, room["id"], "red", sam, "low block")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.set_ready(conn, room["id"], "red", True)
    rooms.start_match(conn, room["id"], "screen-1")

    assert rooms.live(conn) == [
        {"code": room["code"], "mode": "versus", "blue": "Alex Rivera", "red": "Sam Okafor"}
    ]
    assert waiting["code"] not in [entry["code"] for entry in rooms.live(conn)]


def test_a_solo_room_on_the_wall_has_no_red_manager(conn, alex):
    # Most matches are solo, so "no dugout here" is the common case, not an edge.
    live_solo(conn, alex)
    assert rooms.live(conn)[0]["red"] is None


def test_a_finished_room_leaves_the_wall(conn, alex):
    room = live_solo(conn, alex)
    rooms.finish_match(conn, room["id"])
    assert rooms.live(conn) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_rooms.py -v -k "event or snapshot or wall or open_seats or log"`
Expected: FAIL with `AttributeError: module 'rooms' has no attribute 'append_event'`.

- [ ] **Step 3: Write the implementation**

Add `import json` to the top of `arena/rooms.py` (above `import time`), then append:

```python
def append_event(conn, room_id, kind, payload, match_ms=None):
    """Add to the room's log and return the sequence number.

    Scoring is recomputed from this log and never from a submitted total, so
    it is append-only and numbered per room rather than globally.
    """
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM event WHERE room_id = ?",
        (room_id,),
    ).fetchone()["next"]
    conn.execute(
        "INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (room_id, seq, kind, json.dumps(payload), match_ms, time.time()),
    )
    conn.commit()
    return seq


def events(conn, room_id):
    """The room's whole log, oldest first, payloads decoded."""
    return [
        {"seq": row["seq"], "kind": row["kind"], "match_ms": row["match_ms"],
         "payload": json.loads(row["payload_json"])}
        for row in conn.execute(
            "SELECT seq, kind, payload_json, match_ms FROM event "
            "WHERE room_id = ? ORDER BY seq",
            (room_id,),
        )
    ]


def snapshot(conn, room_id):
    """What a client is told about a room: over HTTP, and on socket connect."""
    room = _room(conn, room_id)
    seated = conn.execute(
        "SELECT s.team, s.ready, s.philosophy, p.display_name, p.email_masked "
        "FROM seat s JOIN player p ON p.id = s.player_id "
        "WHERE s.room_id = ? ORDER BY s.team",
        (room_id,),
    ).fetchall()
    taken = {row["team"] for row in seated}
    return {
        "code": room["code"],
        "mode": room["mode"],
        "status": room["status"],
        "ranked": bool(room["ranked"]),
        "seats": {
            row["team"]: {
                "name": row["display_name"],
                "email": row["email_masked"],
                "philosophy": row["philosophy"],
                "ready": bool(row["ready"]),
            }
            for row in seated
        },
        "open_seats": [team for team in required_teams(room["mode"]) if team not in taken],
    }


def live(conn):
    """One row per live room, with both manager names, for the wall."""
    return [
        {"code": row["code"], "mode": row["mode"],
         "blue": row["blue_name"], "red": row["red_name"]}
        for row in conn.execute(
            "SELECT r.code, r.mode,"
            "       MAX(CASE WHEN s.team = 'blue' THEN p.display_name END) AS blue_name,"
            "       MAX(CASE WHEN s.team = 'red'  THEN p.display_name END) AS red_name "
            "FROM room r "
            "LEFT JOIN seat s ON s.room_id = r.id "
            "LEFT JOIN player p ON p.id = s.player_id "
            "WHERE r.status = 'live' "
            "GROUP BY r.id ORDER BY r.created_at"
        )
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_rooms.py -v`
Expected: 35 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/rooms.py arena/tests/test_rooms.py
git commit -m "feat(arena): add the event log and the room and wall read models"
```

---

## Task 6: The message bus

**Files:**
- Create: `arena/bus.py`
- Test: `arena/tests/test_bus.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `bus.WALL: str`, `bus.room_topic(code: str) -> str`, `bus.Subscription` (async-iterable, `.queue`, `.dropped`, `.close()`, usable as a `with` block), `bus.Bus` with `.subscribe(topic, maxsize=64) -> Subscription`, `.unsubscribe(sub) -> None`, `.publish(topic, message) -> None`, `.subscriber_count(topic) -> int`.

- [ ] **Step 1: Write the failing test**

`arena/tests/test_bus.py`:

```python
import asyncio

import pytest

from bus import WALL, Bus, room_topic


async def test_a_subscriber_receives_what_is_published():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")) as subscription:
        bus.publish(room_topic("K7F2"), {"type": "state", "clock": 12})
        assert await anext(subscription) == {"type": "state", "clock": 12}


async def test_two_subscribers_on_one_room_both_get_the_frame():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")) as viewer, \
         bus.subscribe(room_topic("K7F2")) as screen:
        bus.publish(room_topic("K7F2"), {"type": "state"})
        assert await anext(viewer) == {"type": "state"}
        assert await anext(screen) == {"type": "state"}


async def test_rooms_do_not_hear_each_other():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")) as subscription:
        bus.publish(room_topic("M3QX"), {"type": "state"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(subscription), 0.05)


async def test_publishing_with_nobody_listening_is_a_no_op():
    bus = Bus()
    bus.publish(WALL, {"type": "wall", "rooms": []})
    assert bus.subscriber_count(WALL) == 0


async def test_a_slow_subscriber_loses_the_oldest_frames_not_the_newest():
    # A state frame is disposable. A tile that stalls must not hold up the
    # match for everyone else, and a stale position is worth less than a fresh
    # one, so the queue drops from the front.
    bus = Bus()
    with bus.subscribe(WALL, maxsize=2) as subscription:
        for number in range(5):
            bus.publish(WALL, {"n": number})
        assert subscription.dropped == 3
        assert [await anext(subscription) for _ in range(2)] == [{"n": 3}, {"n": 4}]


async def test_closing_a_subscription_stops_delivery():
    bus = Bus()
    subscription = bus.subscribe(WALL)
    subscription.close()
    assert bus.subscriber_count(WALL) == 0
    bus.publish(WALL, {"type": "wall"})
    assert subscription.queue.empty()


async def test_a_topic_with_no_subscribers_left_is_forgotten():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")):
        assert bus.subscriber_count(room_topic("K7F2")) == 1
    assert bus.subscriber_count(room_topic("K7F2")) == 0


async def test_the_room_topic_is_scoped_by_code():
    assert room_topic("K7F2") == "room:K7F2"
    assert room_topic("K7F2") != WALL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_bus.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'bus'`.

- [ ] **Step 3: Write the implementation**

`arena/bus.py`:

```python
"""Publish/subscribe for live match traffic. In-process, no broker.

One arena serves one venue, so a dictionary of queues is the whole of it.
State frames are disposable: a subscriber that cannot keep up loses its oldest
frames rather than stalling the match for everybody else.
"""

import asyncio

WALL = "wall"


def room_topic(code):
    return f"room:{code}"


class Subscription:
    """One socket's feed. Async-iterate it, or `await anext(...)` a single message."""

    def __init__(self, bus, topic, maxsize):
        self._bus = bus
        self.topic = topic
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def deliver(self, message):
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.queue.get_nowait()
            self.queue.put_nowait(message)
            self.dropped += 1

    def close(self):
        self._bus.unsubscribe(self)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
        return False


class Bus:
    def __init__(self):
        self._topics = {}

    def subscribe(self, topic, maxsize=64):
        subscription = Subscription(self, topic, maxsize)
        self._topics.setdefault(topic, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription):
        subscribers = self._topics.get(subscription.topic)
        if subscribers is None:
            return
        subscribers.discard(subscription)
        if not subscribers:
            del self._topics[subscription.topic]

    def publish(self, topic, message):
        """Hand a message to every current subscriber. Never blocks.

        Call this from the event loop. Every route that publishes is `async
        def` for that reason: a sync route runs in a threadpool, and waking a
        waiting consumer from another thread is not safe.
        """
        for subscription in tuple(self._topics.get(topic, ())):
            subscription.deliver(message)

    def subscriber_count(self, topic):
        return len(self._topics.get(topic, ()))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_bus.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/bus.py arena/tests/test_bus.py
git commit -m "feat(arena): add the in-process match bus"
```

---

## Task 7: The HTTP API and the run script

**Files:**
- Create: `arena/app.py`
- Create: `arena/run.sh` (mode `755`)
- Modify: `arena/tests/conftest.py` (add `client` and `phones`)
- Test: `arena/tests/test_app.py`

**Interfaces:**
- Consumes: `db`, `identity`, `rooms`, `bus` as produced above.
- Produces: `app.app: FastAPI`, `app.COOKIE = "arena_session"`, `app.EMAIL_SALT`, `app.SESSION_SECRET`, `app.current_player(request) -> int`; routes `GET /health`, `POST /api/players`, `POST /api/rooms`, `GET /api/rooms/{code}`, `POST /api/rooms/{code}/seats/{team}`, `POST /api/rooms/{code}/seats/{team}/ready`, `POST /api/rooms/{code}/start`. Also `app._pump(socket, subscription)` and `app._room_or_404(conn, code)`, used by Tasks 8 and 9. Fixtures `client -> TestClient` and `phones` (`.join(name, email) -> dict`, `.use(jar) -> None`).

- [ ] **Step 1: Add the fixtures**

Append to `arena/tests/conftest.py`, and add `from fastapi.testclient import TestClient` to its imports:

```python
@pytest.fixture
def client(db_path, monkeypatch):
    # The app reads ARENA_DB when its lifespan runs, which TestClient triggers
    # on __enter__, so each test opens the app against its own database file.
    monkeypatch.setenv("ARENA_DB", str(db_path))
    from app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def phones(client):
    """Drive several phones from one TestClient by swapping the cookie jar."""

    class Phones:
        def join(self, name, email):
            client.cookies.clear()
            client.post("/api/players", json={"display_name": name, "email": email})
            return dict(client.cookies)

        def use(self, jar):
            client.cookies.clear()
            client.cookies.update(jar)

    return Phones()
```

- [ ] **Step 2: Write the failing test**

`arena/tests/test_app.py`:

```python
def test_health_says_which_service_answered(client):
    assert client.get("/health").json() == {"ok": True, "service": "arena"}


def test_joining_returns_a_masked_email_and_sets_a_session(client):
    response = client.post("/api/players",
                           json={"display_name": "Alex Rivera", "email": "Alex@Example.com"})
    assert response.status_code == 200
    assert response.json()["email"] == "a***x@example.com"
    assert "arena_session" in response.cookies


def test_joining_twice_with_one_address_is_one_player(client):
    first = client.post("/api/players",
                        json={"display_name": "Alex Rivera", "email": "alex@example.com"})
    second = client.post("/api/players",
                         json={"display_name": "Alex R", "email": "alex@example.com"})
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["display_name"] == "Alex R"


def test_an_address_that_is_not_one_is_refused(client):
    for bad in ("alex", "alex@", "@example.com", "alex@example"):
        response = client.post("/api/players", json={"display_name": "Alex", "email": bad})
        assert response.status_code == 422, bad


def test_an_empty_name_is_refused(client):
    assert client.post("/api/players",
                       json={"display_name": "", "email": "a@b.com"}).status_code == 422


def test_opening_a_room_returns_a_code_and_two_empty_dugouts(client):
    body = client.post("/api/rooms", json={"mode": "versus"}).json()
    assert body["status"] == "lobby"
    assert body["seats"] == {}
    assert body["open_seats"] == ["blue", "red"]
    assert len(body["code"]) == 4


def test_a_solo_room_offers_only_the_blue_dugout(client):
    assert client.post("/api/rooms", json={"mode": "solo"}).json()["open_seats"] == ["blue"]


def test_an_unknown_mode_is_refused(client):
    assert client.post("/api/rooms", json={"mode": "battle-royale"}).status_code == 422


def test_reading_a_room_that_does_not_exist_is_a_404(client):
    assert client.get("/api/rooms/ZZZZ").status_code == 404


def test_you_cannot_take_a_dugout_without_joining_first(client):
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    response = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    assert response.status_code == 401


def test_a_session_signed_by_somebody_else_is_refused(client):
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.cookies.clear()
    client.cookies.update({"arena_session": "1.forged"})
    response = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    assert response.status_code == 401


def test_taking_a_dugout_shows_up_in_the_room(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    body = client.post(f"/api/rooms/{code}/seats/blue",
                       json={"philosophy": "high press"}).json()
    assert body["seats"]["blue"]["name"] == "Alex Rivera"
    assert body["seats"]["blue"]["philosophy"] == "high press"
    assert body["open_seats"] == []


def test_an_unknown_philosophy_is_refused(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert client.post(f"/api/rooms/{code}/seats/blue",
                       json={"philosophy": "park the bus"}).status_code == 422


def test_a_taken_dugout_comes_back_as_a_conflict(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    sam = phones.join("Sam Okafor", "sam@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    phones.use(alex)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    phones.use(sam)
    response = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "low block"})
    assert response.status_code == 409
    assert response.json()["detail"] == "the blue dugout is taken"


def test_two_managers_fill_a_versus_room(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    sam = phones.join("Sam Okafor", "sam@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    phones.use(alex)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    phones.use(sam)
    body = client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "low block"}).json()
    assert body["open_seats"] == []
    assert body["seats"]["red"]["name"] == "Sam Okafor"


def test_you_cannot_mark_somebody_elses_dugout_ready(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    sam = phones.join("Sam Okafor", "sam@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    phones.use(alex)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    phones.use(sam)
    response = client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    assert response.status_code == 403


def test_a_solo_match_starts_once_its_manager_is_ready(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})

    body = client.post(f"/api/rooms/{code}/start", json={"host_client_id": "phone-7"}).json()
    assert body["status"] == "live"
    assert client.get(f"/api/rooms/{code}").json()["status"] == "live"


def test_a_match_will_not_start_before_its_manager_is_ready(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})

    response = client.post(f"/api/rooms/{code}/start", json={"host_client_id": "phone-7"})
    assert response.status_code == 409
    assert response.json()["detail"] == "not every dugout is ready"


def test_starting_a_room_that_does_not_exist_is_a_404(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    assert client.post("/api/rooms/ZZZZ/start",
                       json={"host_client_id": "phone-7"}).status_code == 404
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_app.py -v`
Expected: every test errors on the `client` fixture — `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 4: Write the implementation**

`arena/app.py`:

```python
"""Arena: rooms, seats and the live match bus.

Runs on :8003 beside the pitch (:5173), the coach (:8000), the captain (:8001)
and the dugout (:8002). It owns everything that used to be global -- who is
playing, which match they are in, and what happened in it -- so that more than
one person can play at once.
"""

import os
from contextlib import asynccontextmanager, contextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

import db
import identity
import rooms
from bus import WALL, Bus, room_topic

# Dev defaults. Set both in the environment before a real event: the salt fixes
# every email hash for good, and the secret signs every phone's session.
EMAIL_SALT = os.environ.get("ARENA_EMAIL_SALT", "arena-dev-salt")
SESSION_SECRET = os.environ.get("ARENA_SECRET", "arena-dev-secret")
COOKIE = "arena_session"


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    connection = db.connect(os.environ.get("ARENA_DB", db.DB_PATH))
    db.init_db(connection)
    fastapi_app.state.conn = connection
    fastapi_app.state.bus = Bus()
    yield
    connection.close()


app = FastAPI(title="Arena", lifespan=lifespan)


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    email: str

    @field_validator("email")
    @classmethod
    def looks_like_an_address(cls, value):
        local, at_sign, domain = value.strip().partition("@")
        if not (local and at_sign and "." in domain):
            raise ValueError("that does not look like an email address")
        return value.strip()


class RoomRequest(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value):
        if value not in rooms.MODES:
            raise ValueError(f"mode must be one of {', '.join(rooms.MODES)}")
        return value


class SeatRequest(BaseModel):
    philosophy: str

    @field_validator("philosophy")
    @classmethod
    def known_philosophy(cls, value):
        if value not in rooms.PHILOSOPHIES:
            raise ValueError(f"philosophy must be one of {', '.join(rooms.PHILOSOPHIES)}")
        return value


class ReadyRequest(BaseModel):
    ready: bool


class StartRequest(BaseModel):
    host_client_id: str = Field(min_length=1)


def current_player(request: Request) -> int:
    """The player id in the session cookie, or a 401."""
    player_id = identity.verify_token(request.cookies.get(COOKIE), SESSION_SECRET)
    if player_id is None or rooms.get_player(request.app.state.conn, player_id) is None:
        raise HTTPException(401, "join first -- your phone has no session")
    return player_id


@app.get("/health")
async def health():
    return {"ok": True, "service": "arena"}


@app.post("/api/players")
async def join(body: JoinRequest, request: Request, response: Response):
    """Name plus email in, session cookie out. This is the whole of identity."""
    connection = request.app.state.conn
    player_id = rooms.create_player(connection, body.display_name, body.email, EMAIL_SALT)
    response.set_cookie(
        COOKIE,
        identity.sign_token(player_id, SESSION_SECRET),
        httponly=True,
        samesite="lax",
    )
    player = rooms.get_player(connection, player_id)
    return {"id": player_id,
            "display_name": player["display_name"],
            "email": player["email_masked"]}


@app.post("/api/rooms")
async def open_room(body: RoomRequest, request: Request):
    connection = request.app.state.conn
    with _rules():
        room = rooms.create_room(connection, body.mode)
    return rooms.snapshot(connection, room["id"])


@app.get("/api/rooms/{code}")
async def read_room(code: str, request: Request):
    connection = request.app.state.conn
    return rooms.snapshot(connection, _room_or_404(connection, code)["id"])


@app.post("/api/rooms/{code}/seats/{team}")
async def sit_down(code: str, team: str, body: SeatRequest, request: Request,
                   player_id: int = Depends(current_player)):
    connection = request.app.state.conn
    room = _room_or_404(connection, code)
    with _rules():
        rooms.take_seat(connection, room["id"], team, player_id, body.philosophy)
    return _announce(request.app, room)


@app.post("/api/rooms/{code}/seats/{team}/ready")
async def set_ready(code: str, team: str, body: ReadyRequest, request: Request,
                    player_id: int = Depends(current_player)):
    connection = request.app.state.conn
    room = _room_or_404(connection, code)
    _require_own_seat(connection, room["id"], team, player_id)
    with _rules():
        rooms.set_ready(connection, room["id"], team, body.ready)
    return _announce(request.app, room)


@app.post("/api/rooms/{code}/start")
async def start(code: str, body: StartRequest, request: Request,
                player_id: int = Depends(current_player)):
    """Kick off. Whoever calls this holds physics for the whole match."""
    connection = request.app.state.conn
    room = _room_or_404(connection, code)
    _require_seated(connection, room["id"], player_id)
    with _rules():
        rooms.start_match(connection, room["id"], body.host_client_id)
    snapshot = _announce(request.app, room)
    request.app.state.bus.publish(WALL, {"type": "wall", "rooms": rooms.live(connection)})
    return snapshot


def _room_or_404(connection, code):
    room = rooms.by_code(connection, code)
    if room is None:
        raise HTTPException(404, f"there is no room {code}")
    return room


def _require_own_seat(connection, room_id, team, player_id):
    seat = connection.execute(
        "SELECT player_id FROM seat WHERE room_id = ? AND team = ?", (room_id, team)
    ).fetchone()
    if seat is None or seat["player_id"] != player_id:
        raise HTTPException(403, f"the {team} dugout is not yours")


def _require_seated(connection, room_id, player_id):
    seat = connection.execute(
        "SELECT 1 FROM seat WHERE room_id = ? AND player_id = ?", (room_id, player_id)
    ).fetchone()
    if seat is None:
        raise HTTPException(403, "only somebody in this match can start it")


def _announce(fastapi_app, room):
    """Publish the room's new shape to everyone watching it, and return it."""
    snapshot = rooms.snapshot(fastapi_app.state.conn, room["id"])
    fastapi_app.state.bus.publish(room_topic(room["code"]), {"type": "room", **snapshot})
    return snapshot


@contextmanager
def _rules():
    """Turn a rules violation into a 409 whose text a phone can show as-is."""
    try:
        yield
    except rooms.RoomError as problem:
        raise HTTPException(409, str(problem)) from problem


async def _pump(socket, subscription):
    """Forward everything on a subscription to a socket until cancelled."""
    async for message in subscription:
        await socket.send_json(message)
```

`arena/run.sh` (then `chmod 755 arena/run.sh`):

```bash
#!/bin/bash
# run.sh - Runs the Arena FastAPI server.

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

# Resolve root directory
CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CWD"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8003}"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# The salt fixes every email hash for good and the secret signs every phone's
# session, so both must survive a restart. Dev defaults let the arena start
# without them; an event that reuses them loses everyone's board history.
if [ -z "${ARENA_EMAIL_SALT:-}" ] || [ -z "${ARENA_SECRET:-}" ]; then
    echo "WARNING: ARENA_EMAIL_SALT and/or ARENA_SECRET are unset." >&2
    echo "         Running with dev defaults. Set both before a real event." >&2
fi

echo "--> Syncing python environment with uv..."
uv sync --all-groups

echo "--> Starting Arena on http://$HOST:$PORT ..."
exec uv run uvicorn app:app --host "$HOST" --port "$PORT" "$@"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_app.py -v`
Expected: 19 passed.

- [ ] **Step 6: Run the whole suite and start the server once by hand**

Run: `cd arena && uv run pytest -v`
Expected: all tests pass.

Run: `cd arena && ./run.sh &` then `curl -s localhost:8003/health` then `kill %1`
Expected: `{"ok":true,"service":"arena"}`. This also proves `arena.db` is created
on first boot.

- [ ] **Step 7: Commit**

```bash
git add arena/app.py arena/run.sh arena/tests/conftest.py arena/tests/test_app.py
git commit -m "feat(arena): serve the join, room and seat API on :8003"
```

---

## Task 8: The room socket

**Files:**
- Modify: `arena/app.py` (add imports and the `/ws/rooms/{code}` endpoint)
- Modify: `arena/tests/conftest.py` (add the `live_room` fixture)
- Test: `arena/tests/test_room_socket.py`

**Interfaces:**
- Consumes: `app.app`, `app._pump`, `bus.room_topic`, `bus.WALL`, `rooms.snapshot`, `rooms.append_event`, `rooms.by_code`.
- Produces: the endpoint `WS /ws/rooms/{code}?client_id=…`, and fixture `live_room(mode="solo") -> str` returning a room code whose host client id is `phone-7`.

**Wire protocol.** Down, to everyone: `{"type": "room", …snapshot}`,
`{"type": "state", …payload}`, `{"type": "event", "seq", "kind", "match_ms",
"payload"}`. Up, accepted only when `client_id` equals the room's
`host_client_id`: `{"type": "host.state", "payload": {…}}` and
`{"type": "host.event", "kind": …, "match_ms": …, "payload": {…}}`. Anything
else up is ignored rather than an error, so a client running ahead of the
server does not get hung up on.

- [ ] **Step 1: Add the fixture**

Append to `arena/tests/conftest.py`:

```python
@pytest.fixture
def live_room(client, phones):
    """Open a room, seat Alex, and kick off with `phone-7` holding physics."""

    def _live_room(mode="solo"):
        phones.join("Alex Rivera", "alex@example.com")
        code = client.post("/api/rooms", json={"mode": mode}).json()["code"]
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
        client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        client.post(f"/api/rooms/{code}/start", json={"host_client_id": "phone-7"})
        return code

    return _live_room
```

- [ ] **Step 2: Write the failing test**

`arena/tests/test_room_socket.py`:

```python
import pytest
from fastapi import WebSocketDisconnect

import rooms


def test_a_socket_for_a_room_that_does_not_exist_is_closed(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/rooms/ZZZZ"):
            pass
    assert closed.value.code == 4404


def test_connecting_hands_over_the_room_as_it_stands(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})

    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        opening = socket.receive_json()
    assert opening["type"] == "room"
    assert opening["seats"]["blue"]["name"] == "Alex Rivera"
    assert opening["status"] == "lobby"


def test_a_seat_being_taken_reaches_everyone_watching(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    with client.websocket_connect(f"/ws/rooms/{code}") as screen:
        screen.receive_json()                       # the opening snapshot
        phones.use(alex)
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
        update = screen.receive_json()
    assert update["type"] == "room"
    assert update["open_seats"] == ["red"]


def test_the_host_state_reaches_a_viewer(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"clock": 12, "score": [1, 0]}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12, "score": [1, 0]}


def test_a_client_that_is_not_the_host_cannot_move_the_ball(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=impostor") as liar:
            liar.receive_json()
            liar.send_json({"type": "host.state", "payload": {"clock": 99}})
            # Rather than wait on a timeout, send a frame that IS allowed and
            # prove it is the first thing the viewer sees.
            with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
                host.receive_json()
                host.send_json({"type": "host.state", "payload": {"clock": 12}})
                frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12}


def test_a_socket_with_no_client_id_can_only_watch(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}") as silent:
            silent.receive_json()
            silent.send_json({"type": "host.state", "payload": {"clock": 99}})
            with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
                host.receive_json()
                host.send_json({"type": "host.state", "payload": {"clock": 12}})
                frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12}


def test_a_host_event_comes_back_numbered(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "goal", "match_ms": 27400,
                        "payload": {"team": "blue", "scorer": "forward"}})
        event = host.receive_json()
    assert event == {"type": "event", "seq": 1, "kind": "goal", "match_ms": 27400,
                     "payload": {"team": "blue", "scorer": "forward"}}


def test_host_events_are_written_to_the_log_in_order(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        for kind, match_ms in (("kickoff", 0), ("goal", 27400), ("full_time", 180000)):
            host.send_json({"type": "host.event", "kind": kind,
                            "match_ms": match_ms, "payload": {}})
            host.receive_json()

    connection = client.app.state.conn
    log = rooms.events(connection, rooms.by_code(connection, code)["id"])
    assert [(entry["seq"], entry["kind"]) for entry in log] == [
        (1, "kickoff"), (2, "goal"), (3, "full_time")]


def test_a_state_frame_is_not_written_to_the_log(client, live_room):
    # Positions at 10 Hz would swamp the log, and scoring never reads them.
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        host.send_json({"type": "host.state", "payload": {"clock": 12}})
        host.receive_json()

    connection = client.app.state.conn
    assert rooms.events(connection, rooms.by_code(connection, code)["id"]) == []


def test_a_message_the_protocol_does_not_know_is_ignored(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        host.send_json({"type": "please.let.us.win", "payload": {}})
        host.send_json({"type": "host.state", "payload": {"clock": 3}})
        assert host.receive_json() == {"type": "state", "clock": 3}


def test_a_socket_can_watch_a_room_that_has_not_kicked_off(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as screen:
        assert screen.receive_json()["status"] == "lobby"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_room_socket.py -v`
Expected: FAIL — `WebSocketDisconnect` / 403 on connect, because no
`/ws/rooms/{code}` route is registered.

- [ ] **Step 4: Write the implementation**

In `arena/app.py`, change the FastAPI import line to:

```python
from fastapi import (Depends, FastAPI, HTTPException, Request, Response, WebSocket,
                     WebSocketDisconnect)
```

and add `import asyncio` above `import os`. Then append the endpoint:

```python
@app.websocket("/ws/rooms/{code}")
async def room_socket(socket: WebSocket, code: str, client_id: str = ""):
    """One room's feed. Anyone may listen; only the host may drive."""
    connection = socket.app.state.conn
    match_bus = socket.app.state.bus
    room = rooms.by_code(connection, code)
    if room is None:
        await socket.close(code=4404, reason=f"there is no room {code}")
        return

    await socket.accept()
    await socket.send_json({"type": "room", **rooms.snapshot(connection, room["id"])})

    subscription = match_bus.subscribe(room_topic(code))
    pump = asyncio.create_task(_pump(socket, subscription))
    try:
        while True:
            _handle_from_host(await socket.receive_json(), connection, match_bus,
                              room, client_id)
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        subscription.close()


def _handle_from_host(message, connection, match_bus, room, client_id):
    """Apply one up-message, if the sender is the client holding physics."""
    kind = message.get("type")
    if kind not in ("host.state", "host.event"):
        return
    # Re-read the room: the host is set at kick-off, which is often after the
    # big screen and the phones already have their sockets open.
    host_client_id = rooms.by_code(connection, room["code"])["host_client_id"]
    if not client_id or client_id != host_client_id:
        return

    payload = message.get("payload") or {}
    topic = room_topic(room["code"])
    if kind == "host.state":
        match_bus.publish(topic, {"type": "state", **payload})
        # The wall wants score and positions, and nothing else. Relay traffic
        # would be unreadable at tile size.
        match_bus.publish(WALL, {"type": "wall.state", "code": room["code"], **payload})
        return

    event_kind = message.get("kind", "unknown")
    match_ms = message.get("match_ms")
    seq = rooms.append_event(connection, room["id"], event_kind, payload, match_ms)
    match_bus.publish(topic, {"type": "event", "seq": seq, "kind": event_kind,
                              "match_ms": match_ms, "payload": payload})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_room_socket.py -v`
Expected: 11 passed.

- [ ] **Step 6: Commit**

```bash
git add arena/app.py arena/tests/conftest.py arena/tests/test_room_socket.py
git commit -m "feat(arena): carry one match on /ws/rooms/{code}"
```

---

## Task 9: The wall socket

**Files:**
- Modify: `arena/app.py` (add the `/ws/wall` endpoint and `_until_closed`)
- Test: `arena/tests/test_wall_socket.py`

**Interfaces:**
- Consumes: `app._pump`, `bus.WALL`, `rooms.live`; the `live_room` fixture.
- Produces: the endpoint `WS /ws/wall`. Down: `{"type": "wall", "rooms": [{code, mode, blue, red}]}` on connect and whenever a match kicks off, and `{"type": "wall.state", "code": …, …payload}` for every host frame in any room.

- [ ] **Step 1: Write the failing test**

`arena/tests/test_wall_socket.py`:

```python
def test_the_wall_opens_with_every_live_room(client, live_room):
    code = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        opening = wall.receive_json()
    assert opening == {"type": "wall", "rooms": [
        {"code": code, "mode": "solo", "blue": "Alex Rivera", "red": None}]}


def test_an_empty_venue_opens_with_no_rooms(client):
    with client.websocket_connect("/ws/wall") as wall:
        assert wall.receive_json() == {"type": "wall", "rooms": []}


def test_a_match_kicking_off_appears_on_the_wall(client, live_room):
    with client.websocket_connect("/ws/wall") as wall:
        assert wall.receive_json() == {"type": "wall", "rooms": []}
        code = live_room()
        update = wall.receive_json()
    assert update["type"] == "wall"
    assert [entry["code"] for entry in update["rooms"]] == [code]


def test_host_frames_reach_the_wall_tagged_with_their_room(client, live_room):
    code = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"score": [1, 0], "clock": 153}})
            frame = wall.receive_json()
    assert frame == {"type": "wall.state", "code": code, "score": [1, 0], "clock": 153}


def test_the_wall_does_not_carry_events_only_frames(client, live_room):
    # A goal reaches the room socket; the wall gets it through the next frame's
    # score. Keeping events off the wall is what keeps it one connection.
    code = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "goal",
                            "match_ms": 27400, "payload": {"team": "blue"}})
            host.send_json({"type": "host.state", "payload": {"score": [1, 0]}})
            frame = wall.receive_json()
    assert frame == {"type": "wall.state", "code": code, "score": [1, 0]}


def test_two_live_rooms_both_reach_one_wall_connection(client, phones):
    def start(name, email, host_client_id):
        phones.join(name, email)
        code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
        client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        client.post(f"/api/rooms/{code}/start", json={"host_client_id": host_client_id})
        return code

    first = start("Alex Rivera", "alex@example.com", "phone-7")
    second = start("Priya Nair", "priya@example.com", "phone-8")

    with client.websocket_connect("/ws/wall") as wall:
        assert {entry["code"] for entry in wall.receive_json()["rooms"]} == {first, second}
        with client.websocket_connect(f"/ws/rooms/{second}?client_id=phone-8") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"score": [0, 2]}})
            frame = wall.receive_json()
    assert frame["code"] == second
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_wall_socket.py -v`
Expected: FAIL on connect — no `/ws/wall` route is registered.

- [ ] **Step 3: Write the implementation**

Append to `arena/app.py`:

```python
@app.websocket("/ws/wall")
async def wall_socket(socket: WebSocket):
    """Every live room at a glance. One connection for the filmstrip, not six."""
    match_bus = socket.app.state.bus
    await socket.accept()
    await socket.send_json({"type": "wall", "rooms": rooms.live(socket.app.state.conn)})

    subscription = match_bus.subscribe(WALL, maxsize=128)
    tasks = [asyncio.create_task(_pump(socket, subscription)),
             asyncio.create_task(_until_closed(socket))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        subscription.close()


async def _until_closed(socket):
    """The wall never sends anything up. This is only here to notice a hang-up.

    Without something reading, a closed browser tab is not discovered until the
    next send fails, which on a quiet venue could be a long time.
    """
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_wall_socket.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/app.py arena/tests/test_wall_socket.py
git commit -m "feat(arena): summarise every live match on /ws/wall"
```

---

## Task 10: The fake host

**Files:**
- Create: `arena/fake_host.py`
- Create: `arena/fixtures/match-3-1.jsonl`
- Test: `arena/tests/test_fake_host.py`

**Interfaces:**
- Consumes: the `/ws/rooms/{code}` protocol from Task 8; the `client` and `live_room` fixtures.
- Produces: `fake_host.FRAME_TYPES`, `fake_host.parse_log(path) -> list[dict]`, `fake_host.to_message(frame) -> dict`, `fake_host.replay(frames, send, speed=1.0, sleep=asyncio.sleep) -> None`, `fake_host.run(url, frames, speed) -> None`, `fake_host.main(argv=None) -> None`.

- [ ] **Step 1: Write the fixture log**

`arena/fixtures/match-3-1.jsonl`:

```
# Alex Rivera 3 - 1 House side. Solo, ranked, three minutes.
# `t` is match time in seconds. The first goal lands at 27.4s, inside the
# 500-point bracket, so step 5 can score this log without a second fixture.
{"t": 0.0, "type": "event", "kind": "kickoff", "payload": {}}
{"t": 0.0, "type": "state", "payload": {"score": [0, 0], "clock": 180}}
{"t": 14.0, "type": "state", "payload": {"score": [0, 0], "clock": 166}}
{"t": 27.4, "type": "event", "kind": "goal", "payload": {"team": "blue", "scorer": "forward"}}
{"t": 27.4, "type": "state", "payload": {"score": [1, 0], "clock": 153}}
{"t": 52.0, "type": "state", "payload": {"score": [1, 0], "clock": 128}}
{"t": 61.8, "type": "event", "kind": "goal", "payload": {"team": "red", "scorer": "forward"}}
{"t": 61.8, "type": "state", "payload": {"score": [1, 1], "clock": 118}}
{"t": 95.0, "type": "state", "payload": {"score": [1, 1], "clock": 85}}
{"t": 112.3, "type": "event", "kind": "goal", "payload": {"team": "blue", "scorer": "midfielder"}}
{"t": 112.3, "type": "state", "payload": {"score": [2, 1], "clock": 68}}
{"t": 148.0, "type": "state", "payload": {"score": [2, 1], "clock": 32}}
{"t": 166.9, "type": "event", "kind": "goal", "payload": {"team": "blue", "scorer": "forward"}}
{"t": 166.9, "type": "state", "payload": {"score": [3, 1], "clock": 13}}
{"t": 180.0, "type": "event", "kind": "full_time", "payload": {"score": [3, 1]}}
```

- [ ] **Step 2: Write the failing test**

`arena/tests/test_fake_host.py`:

```python
from pathlib import Path

import pytest

import fake_host
import rooms

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "match-3-1.jsonl"


def test_the_shipped_fixture_parses_into_frames():
    frames = fake_host.parse_log(FIXTURE)
    assert len(frames) == 15
    assert frames[0]["kind"] == "kickoff"
    assert frames[-1]["kind"] == "full_time"


def test_the_fixture_is_a_three_one_win_whose_first_goal_is_early():
    goals = [frame for frame in fake_host.parse_log(FIXTURE)
             if frame.get("kind") == "goal"]
    assert [goal["payload"]["team"] for goal in goals] == ["blue", "red", "blue", "blue"]
    assert goals[0]["t"] < 30       # inside the 500-point first-goal bracket


def test_comments_and_blank_lines_are_skipped(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('# a note\n\n{"t": 0, "type": "event", "kind": "kickoff"}\n')
    assert len(fake_host.parse_log(log)) == 1


def test_frames_come_back_in_time_order(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"t": 2, "type": "state"}\n{"t": 1, "type": "state"}\n')
    assert [frame["t"] for frame in fake_host.parse_log(log)] == [1, 2]


def test_a_frame_with_no_time_is_refused(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"type": "state"}\n')
    with pytest.raises(ValueError, match="numeric 't'"):
        fake_host.parse_log(log)


def test_a_frame_of_an_unknown_type_is_refused(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"t": 0, "type": "shout"}\n')
    with pytest.raises(ValueError, match="type must be"):
        fake_host.parse_log(log)


def test_an_event_with_no_kind_is_refused(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"t": 0, "type": "event"}\n')
    with pytest.raises(ValueError, match="no kind"):
        fake_host.parse_log(log)


def test_a_state_frame_becomes_a_host_state_message():
    assert fake_host.to_message({"t": 1.5, "type": "state", "payload": {"clock": 178}}) == {
        "type": "host.state", "payload": {"clock": 178}}


def test_an_event_frame_carries_the_match_clock_in_milliseconds():
    frame = {"t": 27.4, "type": "event", "kind": "goal", "payload": {"team": "blue"}}
    assert fake_host.to_message(frame) == {
        "type": "host.event", "kind": "goal", "match_ms": 27400,
        "payload": {"team": "blue"}}


async def test_replay_waits_out_the_gap_between_frames():
    waits, sent = [], []

    async def send(message):
        sent.append(message)

    async def sleep(seconds):
        waits.append(seconds)

    frames = [{"t": 0.0, "type": "state"}, {"t": 2.0, "type": "state"},
              {"t": 3.0, "type": "state"}]
    await fake_host.replay(frames, send, sleep=sleep)
    assert waits == [2.0, 1.0]
    assert len(sent) == 3


async def test_speed_shortens_every_gap():
    waits = []

    async def send(message):
        pass

    async def sleep(seconds):
        waits.append(seconds)

    await fake_host.replay([{"t": 0.0, "type": "state"}, {"t": 6.0, "type": "state"}],
                           send, speed=3.0, sleep=sleep)
    assert waits == [2.0]


def test_the_fixture_replays_into_a_live_room(client, live_room):
    code = live_room()
    frames = fake_host.parse_log(FIXTURE)

    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()                     # the opening room snapshot
        last = None
        for frame in frames:
            host.send_json(fake_host.to_message(frame))
            last = host.receive_json()

    connection = client.app.state.conn
    log = rooms.events(connection, rooms.by_code(connection, code)["id"])
    assert [entry["kind"] for entry in log] == [
        "kickoff", "goal", "goal", "goal", "goal", "full_time"]
    assert log[1]["match_ms"] == 27400
    assert last == {"type": "event", "seq": 6, "kind": "full_time",
                    "match_ms": 180000, "payload": {"score": [3, 1]}}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd arena && uv run pytest tests/test_fake_host.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'fake_host'`.

- [ ] **Step 4: Write the implementation**

`arena/fake_host.py`:

```python
"""Replay a recorded match into an arena room over the room socket.

The arena, the board and the wall can then be built, tested and demoed without
a browser running physics:

    uv run python fake_host.py --room K7F2 --log fixtures/match-3-1.jsonl

The room must already be live with `--client-id` as its host, which is what
`POST /api/rooms/{code}/start` sets.

The log is JSON Lines, one frame per line, `#` for a comment:

    {"t": 0.0,  "type": "event", "kind": "kickoff", "payload": {}}
    {"t": 0.5,  "type": "state", "payload": {"score": [0, 0], "clock": 179}}

`t` is match time in seconds and is what drives the pacing.
"""

import argparse
import asyncio
import json
from pathlib import Path

import websockets

FRAME_TYPES = ("state", "event")


def parse_log(path):
    """Read a JSONL match log into frames, oldest first."""
    frames = []
    for number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        frame = json.loads(line)
        if not isinstance(frame.get("t"), (int, float)):
            raise ValueError(f"{path}:{number} needs a numeric 't'")
        if frame.get("type") not in FRAME_TYPES:
            raise ValueError(f"{path}:{number} type must be one of {', '.join(FRAME_TYPES)}")
        if frame["type"] == "event" and not frame.get("kind"):
            raise ValueError(f"{path}:{number} is an event with no kind")
        frames.append(frame)
    frames.sort(key=lambda frame: frame["t"])
    return frames


def to_message(frame):
    """Turn a log frame into what the room socket expects from its host."""
    if frame["type"] == "state":
        return {"type": "host.state", "payload": frame.get("payload", {})}
    return {
        "type": "host.event",
        "kind": frame["kind"],
        "match_ms": int(frame["t"] * 1000),
        "payload": frame.get("payload", {}),
    }


async def replay(frames, send, speed=1.0, sleep=asyncio.sleep):
    """Send every frame, waiting out the gaps between them.

    `send` and `sleep` are arguments so this can be tested without a socket and
    without sitting through three minutes of football.
    """
    clock = 0.0
    for frame in frames:
        gap = (frame["t"] - clock) / speed
        if gap > 0:
            await sleep(gap)
        clock = frame["t"]
        await send(to_message(frame))


async def run(url, frames, speed):
    async with websockets.connect(url) as socket:
        await socket.recv()          # the opening room snapshot
        await replay(frames, lambda message: socket.send(json.dumps(message)), speed)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay a recorded match into an arena room.")
    parser.add_argument("--room", required=True, help="room code, for example K7F2")
    parser.add_argument("--log", required=True, help="path to a JSONL match log")
    parser.add_argument("--client-id", default="fake-host",
                        help="must match the room's host_client_id")
    parser.add_argument("--arena", default="ws://127.0.0.1:8003")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="1.0 plays in real time; 10 is useful for a smoke test")
    options = parser.parse_args(argv)

    frames = parse_log(options.log)
    url = f"{options.arena}/ws/rooms/{options.room}?client_id={options.client_id}"
    print(f"--> replaying {len(frames)} frames into {options.room} at {options.speed}x")
    asyncio.run(run(url, frames, options.speed))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_fake_host.py -v`
Expected: 12 passed.

- [ ] **Step 6: Prove it end to end against a running server**

```bash
cd arena
./run.sh &
sleep 5
curl -s -c /tmp/arena-cookies -X POST localhost:8003/api/players \
  -H 'content-type: application/json' \
  -d '{"display_name":"Alex Rivera","email":"alex@example.com"}'
CODE=$(curl -s -b /tmp/arena-cookies -X POST localhost:8003/api/rooms \
  -H 'content-type: application/json' -d '{"mode":"solo"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["code"])')
curl -s -b /tmp/arena-cookies -X POST "localhost:8003/api/rooms/$CODE/seats/blue" \
  -H 'content-type: application/json' -d '{"philosophy":"high press"}'
curl -s -b /tmp/arena-cookies -X POST "localhost:8003/api/rooms/$CODE/seats/blue/ready" \
  -H 'content-type: application/json' -d '{"ready":true}'
curl -s -b /tmp/arena-cookies -X POST "localhost:8003/api/rooms/$CODE/start" \
  -H 'content-type: application/json' -d '{"host_client_id":"fake-host"}'
uv run python fake_host.py --room "$CODE" --log fixtures/match-3-1.jsonl --speed 30
kill %1
```

Expected: the replay prints `--> replaying 15 frames…` and exits cleanly in
about six seconds.

- [ ] **Step 7: Run the whole suite**

Run: `cd arena && uv run pytest -v`
Expected: 114 passed (6 db, 6 codes, 11 identity, 35 rooms, 8 bus, 19 app,
11 room socket, 6 wall socket, 12 fake host).

- [ ] **Step 8: Ignore the database and commit**

Append to the repository's root `.gitignore`, which already exists. It ignores
`db.sqlite3` by name only, so the arena's file needs its own entry:

```
# Arena match database, recreated on boot.
arena/arena.db
arena/arena.db-wal
arena/arena.db-shm
```

```bash
git add arena/fake_host.py arena/fixtures/ arena/tests/test_fake_host.py .gitignore
git commit -m "feat(arena): replay recorded matches with --fake-host"
```

---

## Done when

- `cd arena && uv run pytest` passes.
- `cd arena && ./run.sh` serves `GET /health` on :8003.
- Two phones can join a `versus` room, take a dugout each, mark ready and kick off, all over HTTP.
- A `/ws/wall` connection sees every live room and every host frame from all of them.
- `uv run python fake_host.py --room <code> --log fixtures/match-3-1.jsonl` drives a room with no browser open.
- `git status` shows nothing changed outside `arena/` and `.gitignore`.

## What step 1 does not do

Named here so a reviewer does not report them as gaps. Each has its own step in
the spec's build order.

- No profile service, no validator consolidation, no `/api/rooms/{room}/teams/{team}/profiles` (step 2).
- No `/join`, `/play`, `/arena` or `/board` pages, and no QR code. The arena serves JSON only (steps 3, 5, 6).
- No shout endpoint and no relay messages on the socket (step 4).
- No scoring, no `result` table, no leaderboards, no Elo (step 5).
- No viewer path in `game.js`, no centre court, no wall UI, no director (step 6).
- The dugout still writes profile JSON to disk and still owns `/tmp/futsal_status.json` (step 7).
- No 30-second host-gone timeout and no wake lock. `finish_match(…, "abandoned")` exists and nothing calls it yet; the caller arrives with the host heartbeat in step 6.
