# Arena Profile Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every room's blue and red dugout its own set of player profiles in the arena, behind one validator and three HTTP endpoints, and re-point the specialist agents at it.

**Architecture:** Profiles move out of four JSON files on disk and into a `profile` table keyed on `(room_id, team, role)`, seeded from the shipped baselines when a room opens. One validator — `arena/attributes.py`, ported from `game/agents/specialist_agents/profile_guard.py` and keeping its behaviour of returning every reason at once — guards every write. A `PATCH` appends a `profile.patch` event to the room's log and broadcasts the delta on the room bus, so a viewer learns about a change the same way it learns about a goal. The specialist agents reach the arena over HTTP with a service token and take their room and team from ADK session state instead of from a module constant.

**Tech Stack:** Python 3.14, FastAPI, SQLite (WAL, no ORM), pydantic, pytest (`asyncio_mode = "auto"`), `uv`, stdlib `urllib` on the agent side, google-adk 2.1.0.

## Global Constraints

Copied from `docs/superpowers/specs/2026-08-12-arena-multiplayer-leaderboard-design.md` and from the conventions step 1 established in `arena/`.

- Python `>=3.14`. Both `arena/` and `game/` set `package = false`, so `arena/` modules import each other flat (`import rooms`, not `from arena import rooms`) and `game/agents/specialist_agents/` uses relative imports (`from . import tools`).
- No ORM. Every arena function that touches storage takes an open connection as its first argument and never reaches for a global.
- SQLite schema lives in one place: the `SCHEMA` string in `arena/db.py`. `init_db` must stay safe to call against a database that already has the tables.
- Every route that publishes on the bus is `async def`. `asyncio.Queue.put_nowait` from a FastAPI threadpool worker corrupts a waiting consumer.
- Scoring is recomputed from the append-only event log, never from a submitted total. The log is numbered per room and gapless — always append through `rooms.append_event`.
- Validation returns a **list of reasons**, all of them, not the first. The caller is often a language model and can only correct what it is told.
- The validator takes no third-party imports, so it can be tested on its own.
- Secrets never fall back to a public literal. An unset secret authenticates nobody, not everybody. Compare tokens with `hmac.compare_digest`.
- Roles are `("defender", "midfielder", "forward", "goalkeeper")`; teams are `("blue", "red")`; a role or team name is checked against those tuples **before** it is interpolated into a filename or a URL.
- Attribute ranges: `tackleCooldown` 100.0–2000.0, `decisionDelay` 0.0–500.0, `recoverySpeedMultiplier` 0.5–2.0; anything whose own baseline sits above 1.0 may move within twice it; everything else is 0.0–1.0.
- Comments explain why, not what, and read like the surrounding code. Test names are sentences.
- Ports: pitch 5173, coach 8000, captain 8001, dugout 8002, arena 8003.
- Each build step leaves the repo working. `game/frontend/src/main.js` still polls `player_state/{role}.json` every two seconds — that poll is deleted in **step 3**, not here, so the workshop demo must keep responding to the shout bar throughout this step.
- `dugout/attributes.py` is **out of scope**. It is deleted in step 7 when the dugout moves onto the arena. Do not edit it, do not import it, do not import from it.

## File Structure

**Create**

| Path | Responsibility |
|---|---|
| `arena/attributes.py` | The one validator. Role list, attribute ranges, baseline loading, `validate`. No third-party imports, no knowledge of HTTP or SQL. |
| `arena/baselines/{defender,midfielder,forward,goalkeeper}.json` | The shipped starting attributes, vendored from the pitch so the arena does not read across into another project's tree at runtime. A test fails if they drift apart. |
| `arena/profiles.py` | Storage and the patch rule: seed, read, apply. Takes a connection. Knows nothing about rooms beyond a `room_id`. |
| `arena/mirror.py` | Temporary bridge that keeps the pitch's JSON files current while its poll survives. Deleted in step 3. |
| `arena/tests/test_attributes.py` | The validator's rules, and the drift check against the pitch's baselines. |
| `arena/tests/test_profiles.py` | Seeding, reading, patching, rejection. |
| `arena/tests/test_profile_api.py` | The three endpoints: shape, authorization, the event, the broadcast. |
| `arena/README.md` | How to run the arena, what it serves, and every environment variable it reads. |
| `game/agents/specialist_agents/arena_client.py` | The agents' way into the arena. `urllib` only, so the agent project's dependency list does not grow. |
| `game/tests/test_arena_client.py` | The client's request shape and its error translation. |
| `game/tests/test_update_profile.py` | The tool's room/team resolution and its reply text. |

**Modify**

| Path | Change |
|---|---|
| `arena/db.py` | Add the `profile` table to `SCHEMA`. |
| `arena/rooms.py` | `create_room` seeds both dugouts' profiles. |
| `arena/codes.py` | `WORKSHOP` becomes a real four-character code so the workshop room has an address. |
| `arena/app.py` | Three profile routes, the writer-authorization helper, the `_profile_room` helper the existing routes also adopt, and the workshop room at startup. |
| `arena/tests/test_codes.py:32-34` | The workshop-code assertion, now that the code is a real one. |
| `arena/tests/test_rooms.py:52-61` | Unchanged behaviour, but assert the new profiles exist. |
| `game/pyproject.toml` | Add a `dev` dependency group with pytest. |
| `game/agents/specialist_agents/tools.py:20,203` | `update_profile` takes a `ToolContext` and calls the arena. |
| `game/agents/football_mcp_server.py` | `substitutions.json` becomes per-room, per-team. |
| `README.md` | The port table learns about 8003. |

**Delete**

| Path | Why |
|---|---|
| `game/agents/specialist_agents/profile_guard.py` | Consolidated into `arena/attributes.py`. |
| `dugout/tests/test_profile_guard.py` | It loads the deleted file by path. Its coverage moves to `arena/tests/test_attributes.py`. |

**Note on running tests:** `arena/` tests run with `cd arena && uv run pytest`. `game/` tests run with `cd game && uv run pytest`. `dugout/`'s suite **cannot be installed in this environment** — `uv sync` fails with HTTP 401 fetching `mcp-types` from a private index. That is pre-existing and unrelated to this branch. Task 9 deletes one dugout test file; verify that deletion by grep and by reading, and say so in the report rather than claiming a green dugout run.

---

### Task 1: The one validator

**Files:**
- Create: `arena/attributes.py`
- Create: `arena/baselines/defender.json`, `arena/baselines/midfielder.json`, `arena/baselines/forward.json`, `arena/baselines/goalkeeper.json`
- Test: `arena/tests/test_attributes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `attributes.ROLES: tuple[str, ...]` — `("defender", "midfielder", "forward", "goalkeeper")`
  - `attributes.BASELINE_DIR: pathlib.Path`
  - `attributes.range_for(attribute: str, baseline_value=None) -> tuple[float, float]`
  - `attributes.baseline_for(role: str) -> dict` — a fresh dict each call; raises `ValueError` for an unknown role
  - `attributes.validate(role: str, changes) -> list[str]` — empty list means the write is fine

- [ ] **Step 1: Vendor the baselines**

The pitch ships four baseline files. Copy them in under their plain role names — the arena has no `{role}.json` / `{role}_baseline.json` split, because a room's live profile lives in the database and only the starting point lives on disk.

```bash
cd /home/user/agent-football
mkdir -p arena/baselines
for role in defender midfielder forward goalkeeper; do
  cp "game/frontend/public/player_state/${role}_baseline.json" "arena/baselines/${role}.json"
done
ls arena/baselines
```

Expected: four files listed.

- [ ] **Step 2: Write the failing tests**

Create `arena/tests/test_attributes.py`:

```python
"""The one validator. These rules used to exist in two copies."""

import json
from pathlib import Path

import pytest

import attributes


def test_a_change_inside_the_unit_range_is_accepted():
    assert attributes.validate("defender", {"aggression": 0.6}) == []


def test_an_attribute_the_role_does_not_have_is_named_in_the_reason():
    problems = attributes.validate("defender", {"wingspan": 0.5})
    assert problems == ["'wingspan' is not an attribute of the defender"]


def test_every_reason_comes_back_at_once_not_just_the_first():
    # The caller is usually a language model; it can only correct what it is told.
    problems = attributes.validate("defender", {"aggression": 5, "wingspan": 0.5})
    assert len(problems) == 2


def test_a_boolean_is_not_a_number():
    problems = attributes.validate("defender", {"aggression": True})
    assert problems == ["aggression must be a number, got True"]


def test_an_attribute_with_real_units_uses_its_own_band():
    assert attributes.validate("defender", {"tackleCooldown": 600}) == []
    assert attributes.validate("defender", {"tackleCooldown": 0.9}) == [
        "tackleCooldown=0.9 is outside 100.0 to 2000.0"
    ]


def test_an_unlisted_attribute_above_one_may_move_within_twice_its_baseline():
    # The shipped files hold near-duplicates like decisionsDelay=150 that no
    # hardcoded list will ever keep up with.
    assert attributes.range_for("decisionsDelay", 150) == (0.0, 300.0)
    assert attributes.range_for("aggression", 0.8) == (0.0, 1.0)


def test_an_unknown_role_is_refused_rather_than_used_as_a_filename():
    problems = attributes.validate("../../etc/passwd", {})
    assert problems == [
        "unknown role '../../etc/passwd', expected one of "
        "defender, midfielder, forward, goalkeeper"
    ]


def test_changes_that_are_not_an_object_are_refused():
    assert attributes.validate("defender", [1, 2]) == [
        "changes must be an object, got list"
    ]


def test_a_caller_cannot_mutate_the_cached_baseline():
    first = attributes.baseline_for("defender")
    first["aggression"] = 99
    assert attributes.baseline_for("defender")["aggression"] != 99


def test_baseline_for_refuses_a_role_it_does_not_know():
    with pytest.raises(ValueError):
        attributes.baseline_for("striker")


def test_the_arena_baselines_match_the_ones_the_pitch_ships():
    # Two copies of a starting profile drift. This is the tripwire until the
    # pitch reads its profiles from the arena in step 3.
    shipped_dir = (Path(__file__).resolve().parents[2]
                   / "game" / "frontend" / "public" / "player_state")
    for role in attributes.ROLES:
        shipped = json.loads((shipped_dir / f"{role}_baseline.json").read_text())
        assert attributes.baseline_for(role) == shipped, role
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_attributes.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'attributes'`.

- [ ] **Step 4: Write the validator**

Create `arena/attributes.py`:

```python
"""The one validator for player profiles.

Every write goes through `validate`, whatever route it arrived by: a manager
typing in the shout bar, a specialist agent acting on that text, or a direct
PATCH. The rules used to live in two copies -- one beside the agents, one in
the dugout -- and two copies of a security check drift apart. This is the
survivor; the dugout's copy goes when the dugout moves onto the arena.

Deliberately free of third-party imports so it can be tested on its own, and
a role name is checked against ROLES before it is ever used as a filename.
"""

import json
from pathlib import Path

ROLES = ("defender", "midfielder", "forward", "goalkeeper")

BASELINE_DIR = Path(__file__).parent / "baselines"

# Everything is a 0.0-1.0 weight except these three, which carry real units.
_EXPLICIT_RANGES = {
    "tackleCooldown": (100.0, 2000.0),
    "decisionDelay": (0.0, 500.0),
    "recoverySpeedMultiplier": (0.5, 2.0),
}
_UNIT_RANGE = (0.0, 1.0)

_baselines = {}


def range_for(attribute, baseline_value=None):
    """The band an attribute may move in.

    Most are 0.0-1.0 weights. A few carry real units, and the shipped files
    contain near-duplicates of those: the midfielder has both decisionDelay
    and decisionsDelay, the second holding 150 milliseconds. A hardcoded list
    of names will always miss the next one of those, so anything whose own
    baseline sits above 1.0 is taken to carry units and is allowed to move
    within twice it.
    """
    if attribute in _EXPLICIT_RANGES:
        return _EXPLICIT_RANGES[attribute]
    if isinstance(baseline_value, (int, float)) and not isinstance(
            baseline_value, bool) and baseline_value > _UNIT_RANGE[1]:
        return (0.0, float(baseline_value) * 2)
    return _UNIT_RANGE


def baseline_for(role):
    """A role's shipped attributes, as a fresh dict the caller may keep."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {', '.join(ROLES)}")
    if role not in _baselines:
        with open(BASELINE_DIR / f"{role}.json") as handle:
            _baselines[role] = json.load(handle)
    return dict(_baselines[role])


def validate(role, changes):
    """Return a list of reasons the write should be refused. Empty means fine."""
    if role not in ROLES:
        return [f"unknown role {role!r}, expected one of {', '.join(ROLES)}"]
    if not isinstance(changes, dict):
        return [f"changes must be an object, got {type(changes).__name__}"]

    baseline = baseline_for(role)
    problems = []
    for key, value in changes.items():
        if key not in baseline:
            problems.append(f"{key!r} is not an attribute of the {role}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{key} must be a number, got {value!r}")
            continue
        low, high = range_for(key, baseline[key])
        if not low <= value <= high:
            problems.append(f"{key}={value} is outside {low} to {high}")
    return problems
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_attributes.py -v`
Expected: 11 passed.

If `test_a_change_inside_the_unit_range_is_accepted` fails because the defender has no `aggression`, read `arena/baselines/defender.json` and swap in an attribute it does have — do not weaken the assertion.

- [ ] **Step 6: Run the whole arena suite**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: everything that passed before still passes, plus the 11 new tests.

- [ ] **Step 7: Commit**

```bash
cd /home/user/agent-football
git add arena/attributes.py arena/baselines arena/tests/test_attributes.py
git commit -m "feat(arena): one validator for player profiles, with vendored baselines"
```

---

### Task 2: Profile storage, seeded when a room opens

**Files:**
- Modify: `arena/db.py:12-59` (the `SCHEMA` string)
- Create: `arena/profiles.py`
- Modify: `arena/rooms.py:63-78` (`create_room`)
- Test: `arena/tests/test_profiles.py`
- Test: `arena/tests/test_rooms.py:52-61`

**Interfaces:**
- Consumes: `attributes.ROLES`, `attributes.baseline_for(role)` from Task 1.
- Produces:
  - `profiles.seed(conn, room_id, teams) -> None`
  - `profiles.read_all(conn, room_id, team) -> dict[str, dict]` — role name to attributes
  - `profiles.read_one(conn, room_id, team, role) -> dict | None`
- **Import direction matters:** `rooms.py` imports `profiles`; `profiles.py` must never import `rooms`. That is why `seed` takes the teams as an argument instead of reading `rooms.TEAMS`.

- [ ] **Step 1: Write the failing tests**

Create `arena/tests/test_profiles.py`:

```python
"""Per-room, per-team profiles. Seeding and reading."""

import attributes
import profiles
import rooms


def test_a_new_room_gets_a_full_set_of_profiles_for_both_dugouts(conn):
    room = rooms.create_room(conn, "versus")
    for team in ("blue", "red"):
        assert set(profiles.read_all(conn, room["id"], team)) == set(attributes.ROLES)


def test_a_solo_room_still_gets_a_red_dugout_ready_for_an_opponent(conn):
    # Seeding both is cheaper than seeding on demand, and step 5 lets a second
    # phone take the red seat in a room that opened solo.
    room = rooms.create_room(conn, "solo")
    assert set(profiles.read_all(conn, room["id"], "red")) == set(attributes.ROLES)


def test_a_seeded_profile_starts_at_the_shipped_baseline(conn):
    room = rooms.create_room(conn, "solo")
    assert (profiles.read_one(conn, room["id"], "blue", "defender")
            == attributes.baseline_for("defender"))


def test_two_rooms_do_not_share_a_defender(conn):
    first = rooms.create_room(conn, "solo")
    second = rooms.create_room(conn, "solo")
    conn.execute(
        "UPDATE profile SET attributes_json = '{\"aggression\": 0.1}' "
        "WHERE room_id = ? AND team = 'blue' AND role = 'defender'",
        (first["id"],),
    )
    conn.commit()
    assert profiles.read_one(conn, first["id"], "blue", "defender") == {"aggression": 0.1}
    assert (profiles.read_one(conn, second["id"], "blue", "defender")
            == attributes.baseline_for("defender"))


def test_reading_a_dugout_that_was_never_seeded_gives_nothing(conn):
    room = rooms.create_room(conn, "solo")
    assert profiles.read_all(conn, room["id"], "green") == {}
    assert profiles.read_one(conn, room["id"], "green", "defender") is None


def test_seeding_twice_leaves_the_first_values_alone(conn):
    # init_db is safe to re-run; so is this.
    room = rooms.create_room(conn, "solo")
    profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    profiles.seed(conn, room["id"], ("blue", "red"))
    assert profiles.read_one(conn, room["id"], "blue", "defender")["aggression"] == 0.2
```

The last test calls `profiles.patch`, which arrives in Task 3. Write it now and expect it to stay red until then — it is the one test in this file that crosses the boundary, and it is here because seeding idempotence is only interesting once something has moved.

Add to `arena/tests/test_rooms.py`, immediately after `test_the_workshop_room_is_never_ranked`:

```python
def test_opening_a_room_gives_it_profiles(conn):
    room = rooms.create_room(conn, "versus")
    seeded = conn.execute(
        "SELECT COUNT(*) AS n FROM profile WHERE room_id = ?", (room["id"],)
    ).fetchone()["n"]
    assert seeded == 8  # four roles, two dugouts
```

`test_rooms.py` will need `import profiles` only if you use it; the query above avoids that.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profiles.py tests/test_rooms.py -v`
Expected: `ModuleNotFoundError: No module named 'profiles'`.

- [ ] **Step 3: Add the table**

In `arena/db.py`, append to the `SCHEMA` string, after the `event` table and before the `CREATE INDEX` line:

```sql
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
```

- [ ] **Step 4: Write the storage module**

Create `arena/profiles.py`:

```python
"""Per-room, per-team player profiles.

The pitch used to read four JSON files from disk, which meant every match in
the venue shared one defender. A profile now belongs to a room and a dugout,
starts at the shipped baseline, and only moves by a validated patch.

This module knows a room only by its id. `rooms` imports it, not the other way
round, which is why `seed` is told which dugouts to fill.
"""

import json
import time

import attributes


def seed(conn, room_id, teams):
    """Give every named dugout a fresh copy of each role's baseline.

    Safe to call twice: a profile that is already there keeps whatever it has
    moved to since.
    """
    now = time.time()
    conn.executemany(
        "INSERT OR IGNORE INTO profile "
        "(room_id, team, role, attributes_json, updated_at) VALUES (?, ?, ?, ?, ?)",
        [(room_id, team, role, json.dumps(attributes.baseline_for(role)), now)
         for team in teams for role in attributes.ROLES],
    )
    conn.commit()


def read_all(conn, room_id, team):
    """Every role's current attributes for one dugout, keyed by role."""
    return {
        row["role"]: json.loads(row["attributes_json"])
        for row in conn.execute(
            "SELECT role, attributes_json FROM profile "
            "WHERE room_id = ? AND team = ? ORDER BY role",
            (room_id, team),
        )
    }


def read_one(conn, room_id, team, role):
    """One role's current attributes, or None if this dugout has no such role."""
    row = conn.execute(
        "SELECT attributes_json FROM profile WHERE room_id = ? AND team = ? AND role = ?",
        (room_id, team, role),
    ).fetchone()
    return json.loads(row["attributes_json"]) if row else None
```

- [ ] **Step 5: Seed on room creation**

In `arena/rooms.py`, add `import profiles` to the import block (alphabetical, after `identity`), and replace the tail of `create_room`:

```python
    conn.execute(
        "INSERT INTO room (code, mode, status, ranked, created_at) "
        "VALUES (?, ?, 'lobby', ?, ?)",
        (code, mode, 0 if code == codes.WORKSHOP else 1, time.time()),
    )
    conn.commit()
    room = by_code(conn, code)
    # Both dugouts, even in a solo room: seeding is cheap, and a room that
    # opened solo can still gain a red manager later.
    profiles.seed(conn, room["id"], TEAMS)
    return room
```

- [ ] **Step 6: Run the tests**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profiles.py tests/test_rooms.py -v`
Expected: every test passes except `test_seeding_twice_leaves_the_first_values_alone`, which fails with `AttributeError: module 'profiles' has no attribute 'patch'` until Task 3.

- [ ] **Step 7: Run the whole arena suite**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: one failure, the `patch` one above. Everything else green.

- [ ] **Step 8: Commit**

```bash
cd /home/user/agent-football
git add arena/db.py arena/profiles.py arena/rooms.py arena/tests/test_profiles.py arena/tests/test_rooms.py
git commit -m "feat(arena): per-room, per-team profile storage seeded at kick-off"
```

---

### Task 3: Applying a patch

**Files:**
- Modify: `arena/profiles.py`
- Test: `arena/tests/test_profiles.py`

**Interfaces:**
- Consumes: `attributes.validate(role, changes)` from Task 1; `profiles.read_one` from Task 2.
- Produces:
  - `profiles.Rejected(problems: list[str])` — exception with a `.problems` attribute
  - `profiles.patch(conn, room_id, team, role, changes) -> dict` with keys `role`, `attributes` (the whole profile after the change), `changed` (only the attributes whose value actually moved)

- [ ] **Step 1: Write the failing tests**

Append to `arena/tests/test_profiles.py`:

```python
import pytest


def test_a_valid_patch_moves_only_what_it_names(conn):
    room = rooms.create_room(conn, "solo")
    before = attributes.baseline_for("defender")
    result = profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    assert result["attributes"]["aggression"] == 0.2
    assert result["attributes"]["speed"] == before["speed"]


def test_a_patch_reports_only_what_actually_changed(conn):
    # A coach that re-sends the same value should not light up the viewers.
    room = rooms.create_room(conn, "solo")
    unchanged = attributes.baseline_for("defender")["speed"]
    result = profiles.patch(conn, room["id"], "blue", "defender",
                            {"aggression": 0.2, "speed": unchanged})
    assert result["changed"] == {"aggression": 0.2}


def test_a_patch_survives_being_read_back(conn):
    room = rooms.create_room(conn, "solo")
    profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    assert profiles.read_one(conn, room["id"], "blue", "defender")["aggression"] == 0.2


def test_a_patch_to_one_dugout_leaves_the_other_alone(conn):
    room = rooms.create_room(conn, "versus")
    profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    assert (profiles.read_one(conn, room["id"], "red", "defender")
            == attributes.baseline_for("defender"))


def test_an_out_of_range_patch_is_refused_whole(conn):
    room = rooms.create_room(conn, "solo")
    before = profiles.read_one(conn, room["id"], "blue", "defender")
    with pytest.raises(profiles.Rejected) as refusal:
        profiles.patch(conn, room["id"], "blue", "defender",
                       {"aggression": 0.2, "speed": 99})
    assert refusal.value.problems == ["speed=99 is outside 0.0 to 1.0"]
    # Nothing lands: a half-applied patch is worse than a refused one.
    assert profiles.read_one(conn, room["id"], "blue", "defender") == before


def test_patching_a_dugout_the_room_does_not_have_is_refused(conn):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(profiles.Rejected) as refusal:
        profiles.patch(conn, room["id"], "green", "defender", {"aggression": 0.2})
    assert refusal.value.problems == ["this room has no green defender"]


def test_an_unknown_role_never_reaches_storage(conn):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(profiles.Rejected) as refusal:
        profiles.patch(conn, room["id"], "blue", "../defender", {})
    assert "unknown role" in refusal.value.problems[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profiles.py -v`
Expected: FAIL — `module 'profiles' has no attribute 'Rejected'`.

- [ ] **Step 3: Implement `Rejected` and `patch`**

Add to `arena/profiles.py`, after the imports and before `seed`:

```python
class Rejected(Exception):
    """A patch the rules refuse. `problems` is fit to show a manager as-is."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)
```

and at the end of the file:

```python
def patch(conn, room_id, team, role, changes):
    """Apply validated changes to one profile.

    Returns the role, its whole attribute set afterwards, and just the values
    that moved. Raises Rejected carrying every reason at once, rather than the
    first: the caller is often a language model, and it can only correct what
    it is told. A patch with any bad value lands none of its values.
    """
    problems = attributes.validate(role, changes)
    if problems:
        raise Rejected(problems)

    current = read_one(conn, room_id, team, role)
    if current is None:
        raise Rejected([f"this room has no {team} {role}"])

    changed = {key: value for key, value in changes.items() if current.get(key) != value}
    current.update(changes)
    conn.execute(
        "UPDATE profile SET attributes_json = ?, updated_at = ? "
        "WHERE room_id = ? AND team = ? AND role = ?",
        (json.dumps(current), time.time(), room_id, team, role),
    )
    conn.commit()
    return {"role": role, "attributes": current, "changed": changed}
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profiles.py -v`
Expected: all pass, including `test_seeding_twice_leaves_the_first_values_alone` from Task 2.

- [ ] **Step 5: Run the whole arena suite**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
cd /home/user/agent-football
git add arena/profiles.py arena/tests/test_profiles.py
git commit -m "feat(arena): validated profile patches that refuse whole or land whole"
```

---

### Task 4: Reading profiles over HTTP

**Files:**
- Modify: `arena/app.py` (add two routes and the `_profile_room` helper; adopt it in the four existing `/api/rooms/{code}` routes)
- Test: `arena/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `profiles.read_all`, `profiles.read_one` from Task 2; `rooms.TEAMS`; the existing `_room_or_404`.
- Produces:
  - `GET /api/rooms/{code}/teams/{team}/profiles` → `{"team": str, "profiles": {role: attributes}}`
  - `GET /api/rooms/{code}/teams/{team}/profiles/{role}` → `{"team": str, "role": str, "attributes": {...}}`
  - `app._profile_room(request, code) -> (connection, room_row)` — upper-cases, validates the code shape, 404s
  - `app._known_team(team) -> None` — 404s an unknown dugout before the name reaches storage

- [ ] **Step 1: Write the failing tests**

Create `arena/tests/test_profile_api.py`:

```python
"""The profile endpoints: what they return, and who may move them."""

import attributes


def open_room(client, phones, mode="versus"):
    """A room with Alex in the blue dugout. Returns the code."""
    phones.join("Alex Rivera", "alex@example.com")
    return client.post("/api/rooms", json={"mode": mode}).json()["code"]


def test_a_dugout_lists_all_four_roles(client, phones):
    code = open_room(client, phones)
    body = client.get(f"/api/rooms/{code}/teams/blue/profiles").json()
    assert body["team"] == "blue"
    assert set(body["profiles"]) == set(attributes.ROLES)


def test_one_role_comes_back_at_its_baseline(client, phones):
    code = open_room(client, phones)
    body = client.get(f"/api/rooms/{code}/teams/blue/profiles/defender").json()
    assert body == {"team": "blue", "role": "defender",
                    "attributes": attributes.baseline_for("defender")}


def test_a_lower_case_code_finds_the_same_room(client, phones):
    code = open_room(client, phones)
    assert client.get(f"/api/rooms/{code.lower()}/teams/blue/profiles").status_code == 200


def test_there_are_no_profiles_in_a_room_that_does_not_exist(client):
    assert client.get("/api/rooms/ZZZZ/teams/blue/profiles").status_code == 404


def test_a_code_that_could_never_have_been_issued_is_a_404_not_a_500(client):
    assert client.get("/api/rooms/nope!/teams/blue/profiles").status_code == 404


def test_an_unknown_dugout_is_a_404(client, phones):
    code = open_room(client, phones)
    assert client.get(f"/api/rooms/{code}/teams/green/profiles").status_code == 404


def test_an_unknown_role_is_a_404(client, phones):
    code = open_room(client, phones)
    response = client.get(f"/api/rooms/{code}/teams/blue/profiles/striker")
    assert response.status_code == 404


def test_a_role_cannot_walk_out_of_the_room(client, phones):
    code = open_room(client, phones)
    response = client.get(f"/api/rooms/{code}/teams/blue/profiles/..%2F..%2Fpasswd")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profile_api.py -v`
Expected: 404s from FastAPI for the two new paths, so the assertions on body shape fail.

- [ ] **Step 3: Add the helpers**

In `arena/app.py`, add `import profiles` to the import block (alphabetical, after `identity` and before `rooms`), and add these next to `_room_or_404`:

```python
def _profile_room(request, code):
    """The connection and the room behind an /api/rooms/{code}/... path.

    Every one of those routes opened with the same three lines; a code that
    `codes.generate` could never have produced is a 404 rather than a lookup.
    """
    connection = request.app.state.conn
    code = code.upper()
    if not codes.is_valid(code):
        raise HTTPException(404, f"there is no room {code}")
    return connection, _room_or_404(connection, code)


def _known_team(team):
    """Refuse a dugout name before it is used to look anything up."""
    if team not in rooms.TEAMS:
        raise HTTPException(404, f"there is no {team} dugout")
```

- [ ] **Step 4: Adopt the helper in the existing routes**

Replace the opening of each of the four existing `/api/rooms/{code}` routes with the helper. `read_room`:

```python
@app.get("/api/rooms/{code}")
async def read_room(code: str, request: Request):
    connection, room = _profile_room(request, code)
    return rooms.snapshot(connection, room["id"])
```

`sit_down`:

```python
@app.post("/api/rooms/{code}/seats/{team}")
async def sit_down(code: str, team: str, body: SeatRequest, request: Request,
                   player_id: int = Depends(current_player)):
    connection, room = _profile_room(request, code)
    with _rules():
        rooms.take_seat(connection, room["id"], team, player_id, body.philosophy)
    return _announce(request.app, room)
```

`set_ready`:

```python
@app.post("/api/rooms/{code}/seats/{team}/ready")
async def set_ready(code: str, team: str, body: ReadyRequest, request: Request,
                    player_id: int = Depends(current_player)):
    connection, room = _profile_room(request, code)
    _require_own_seat(connection, room["id"], team, player_id)
    with _rules():
        rooms.set_ready(connection, room["id"], team, body.ready)
    return _announce(request.app, room)
```

`start`:

```python
@app.post("/api/rooms/{code}/start")
async def start(code: str, request: Request, player_id: int = Depends(current_player)):
    """Kick off. Whoever calls this holds physics for the whole match."""
    connection, room = _profile_room(request, code)
    _require_seated(connection, room["id"], player_id)
    host_token = secrets.token_urlsafe(16)
    with _rules():
        rooms.start_match(connection, room["id"], host_token)
    snapshot = _announce(request.app, room)
    request.app.state.bus.publish(WALL, {"type": "wall", "rooms": rooms.live(connection)})
    return {**snapshot, "host_token": host_token}
```

`sit_down` keeps letting `rooms.take_seat` refuse an unknown team, because its message ("team must be one of blue, red") is the one the join form shows. Do not add `_known_team` there.

- [ ] **Step 5: Add the two read routes**

In `arena/app.py`, after `start` and before `_room_or_404`:

```python
@app.get("/api/rooms/{code}/teams/{team}/profiles")
async def read_profiles(code: str, team: str, request: Request):
    """Both managers and the pitch read these, so they need no session."""
    connection, room = _profile_room(request, code)
    _known_team(team)
    return {"team": team, "profiles": profiles.read_all(connection, room["id"], team)}


@app.get("/api/rooms/{code}/teams/{team}/profiles/{role}")
async def read_profile(code: str, team: str, role: str, request: Request):
    connection, room = _profile_room(request, code)
    _known_team(team)
    found = profiles.read_one(connection, room["id"], team, role)
    if found is None:
        raise HTTPException(404, f"this room has no {team} {role}")
    return {"team": team, "role": role, "attributes": found}
```

- [ ] **Step 6: Run the tests**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profile_api.py -v`
Expected: 8 passed.

- [ ] **Step 7: Run the whole arena suite**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: all green. The refactor in Step 4 changed no behaviour, so `test_app.py` must not need editing — if it does, you changed behaviour and should undo it.

- [ ] **Step 8: Commit**

```bash
cd /home/user/agent-football
git add arena/app.py arena/tests/test_profile_api.py
git commit -m "feat(arena): read a dugout's profiles over HTTP"
```

---

### Task 5: Patching a profile over HTTP

**Files:**
- Modify: `arena/app.py`
- Test: `arena/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `profiles.patch`, `profiles.Rejected` from Task 3; `_profile_room`, `_known_team` from Task 4; `rooms.append_event`; `bus.room_topic`; the existing `COOKIE`, `SESSION_SECRET`, `identity.verify_token`, `_require_own_seat`.
- Produces:
  - `PATCH /api/rooms/{code}/teams/{team}/profiles/{role}` accepting `{"changes": {...}, "reason": str, "actor": str}` → `{"role", "attributes", "changed", "seq"}`
  - `app.SERVICE_TOKEN: str` — read from `ARENA_SERVICE_TOKEN`, empty when unset
  - `app.MAX_CHANGES = 64`
  - A `profile.patch` event on the room's log and on `room_topic(code)`, payload `{"team", "role", "changed", "reason", "actor"}`

- [ ] **Step 1: Write the failing tests**

Append to `arena/tests/test_profile_api.py`:

```python
def seat_and_start(client, phones, code, team="blue"):
    """Sit the current phone in a dugout and kick off. Returns the host token."""
    client.post(f"/api/rooms/{code}/seats/{team}", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/{team}/ready", json={"ready": True})
    return client.post(f"/api/rooms/{code}/start").json()["host_token"]


def test_the_manager_in_that_dugout_may_move_its_profiles(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2},
                                  "reason": "they keep losing the ball",
                                  "actor": "coach"})
    assert response.status_code == 200
    assert response.json()["changed"] == {"aggression": 0.2}


def test_a_manager_cannot_reach_into_the_other_dugout(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}})
    assert response.status_code == 403
    assert profiles_of(client, code, "red")["defender"] == attributes.baseline_for("defender")


def profiles_of(client, code, team):
    return client.get(f"/api/rooms/{code}/teams/{team}/profiles").json()["profiles"]


def test_a_phone_with_no_session_cannot_move_anything(client, phones):
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2}})
    assert response.status_code == 401


def test_a_service_caller_with_the_shared_secret_may_move_a_profile(client, phones,
                                                                    monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "s3cret")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}, "actor": "midfield-agent"},
                            headers={"X-Arena-Service": "s3cret"})
    assert response.status_code == 200


def test_the_wrong_shared_secret_is_no_better_than_none(client, phones, monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "s3cret")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"X-Arena-Service": "guess"})
    assert response.status_code == 401


def test_an_unset_shared_secret_authenticates_nobody(client, phones, monkeypatch):
    # The dangerous failure is the other way round: an empty configured secret
    # matching an empty offered header and letting the whole internet in.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"X-Arena-Service": ""})
    assert response.status_code == 401


def test_a_refused_change_comes_back_with_every_reason(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"speed": 99, "wingspan": 0.5}})
    assert response.status_code == 422
    assert len(response.json()["detail"]["problems"]) == 2


def test_a_patch_lands_on_the_rooms_log(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    body = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                        json={"changes": {"aggression": 0.2}, "actor": "coach",
                              "reason": "too passive"}).json()
    assert body["seq"] == 1


def test_a_patch_reaches_everyone_watching_the_room(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()  # the room snapshot every socket opens with
        client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                     json={"changes": {"aggression": 0.2}, "actor": "coach",
                           "reason": "too passive"})
        frame = viewer.receive_json()
    assert frame == {"type": "event", "seq": 1, "kind": "profile.patch",
                     "match_ms": None,
                     "payload": {"team": "blue", "role": "defender",
                                 "changed": {"aggression": 0.2},
                                 "reason": "too passive", "actor": "coach"}}


def test_profiles_cannot_be_moved_after_the_final_whistle(client, phones):
    code = open_room(client, phones, mode="solo")
    seat_and_start(client, phones, code)
    client.app.state.conn.execute("UPDATE room SET status = 'finished' WHERE code = ?",
                                  (code,))
    client.app.state.conn.commit()
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2}})
    assert response.status_code == 409


def test_a_flood_of_attributes_is_refused_before_it_is_validated(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {str(n): 0.5 for n in range(200)}})
    assert response.status_code == 422
```

`client.app` is Starlette's `TestClient.app`; the lifespan has already put the connection on `app.state.conn`, which is how `test_profiles_cannot_be_moved_after_the_final_whistle` reaches past the API to force a status the routes will not produce yet.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profile_api.py -v`
Expected: the new tests fail with 405 Method Not Allowed — there is no PATCH route yet.

- [ ] **Step 3: Add the service token and the request model**

In `arena/app.py`, below the `COOKIE` line:

```python
# The specialist agents run in another process with no phone and no cookie, so
# they carry a shared secret instead. Unset means they are refused: an unset
# secret must authenticate nobody rather than everybody.
SERVICE_TOKEN = os.environ.get("ARENA_SERVICE_TOKEN", "")
if not SERVICE_TOKEN:
    logger.warning("ARENA_SERVICE_TOKEN unset; server-side profile writes are refused")

# A role has fewer than fifty attributes. Anything larger is a mistake or an
# attempt to make the validator do work, and it is cheaper to refuse it here.
MAX_CHANGES = 64
```

Add the request model after `ReadyRequest`:

```python
class ProfilePatchRequest(BaseModel):
    changes: dict = Field(default_factory=dict)
    # Both are shown to the other manager, so they are bounded and never trusted.
    reason: str = Field(default="", max_length=280)
    actor: str = Field(default="manager", max_length=40)

    @field_validator("changes")
    @classmethod
    def not_too_many(cls, value):
        if len(value) > MAX_CHANGES:
            raise ValueError(f"a patch may name at most {MAX_CHANGES} attributes")
        return value
```

- [ ] **Step 4: Add the authorization helper**

Next to `_require_own_seat` in `arena/app.py`:

```python
def _require_profile_writer(request, connection, room_id, team):
    """A dugout's own manager, or a trusted service caller acting for them.

    The service token is checked first and in constant time, because the
    agents have no session to fall back on. An empty configured token can
    never match, so forgetting to set it locks the agents out rather than
    letting everyone in.
    """
    offered = request.headers.get("x-arena-service", "")
    if SERVICE_TOKEN and offered and hmac.compare_digest(offered, SERVICE_TOKEN):
        return
    player_id = identity.verify_token(request.cookies.get(COOKIE), SESSION_SECRET)
    if player_id is None or rooms.get_player(connection, player_id) is None:
        raise HTTPException(401, "join first -- your phone has no session")
    _require_own_seat(connection, room_id, team, player_id)
```

- [ ] **Step 5: Add the route**

In `arena/app.py`, after `read_profile`:

```python
@app.patch("/api/rooms/{code}/teams/{team}/profiles/{role}")
async def patch_profile(code: str, team: str, role: str, body: ProfilePatchRequest,
                        request: Request):
    """Move one role's attributes, and tell the room it happened.

    Async because it publishes: a sync route runs in a threadpool, and waking
    a waiting consumer from another thread is not safe.
    """
    connection, room = _profile_room(request, code)
    _known_team(team)
    if room["status"] not in ("lobby", "live"):
        raise HTTPException(409, "that match is over")
    _require_profile_writer(request, connection, room["id"], team)

    try:
        result = profiles.patch(connection, room["id"], team, role, body.changes)
    except profiles.Rejected as refusal:
        raise HTTPException(422, {"problems": refusal.problems}) from refusal

    delta = {"team": team, "role": role, "changed": result["changed"],
             "reason": body.reason, "actor": body.actor}
    seq = rooms.append_event(connection, room["id"], "profile.patch", delta)
    request.app.state.bus.publish(
        room_topic(room["code"]),
        {"type": "event", "seq": seq, "kind": "profile.patch", "match_ms": None,
         "payload": delta},
    )
    return {**result, "seq": seq}
```

- [ ] **Step 6: Run the tests**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profile_api.py -v`
Expected: all pass.

- [ ] **Step 7: Run the whole arena suite**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
cd /home/user/agent-football
git add arena/app.py arena/tests/test_profile_api.py
git commit -m "feat(arena): PATCH a profile, log it, and broadcast the delta"
```

---

### Task 6: The workshop room gets an address

**Files:**
- Modify: `arena/codes.py:9-11`
- Modify: `arena/app.py` (the `lifespan` function, lines 69-79)
- Modify: `arena/tests/test_codes.py:32-34`
- Test: `arena/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `rooms.create_room(conn, mode, code)` and its seeding from Task 2.
- Produces: `codes.WORKSHOP == "WRKS"` — a real, valid, four-character code; a room with that code exists from the moment the arena starts, in `solo` mode and unranked.

**Why:** the dugout's workshop and the shout bar need a room to patch profiles in, and `codes.is_valid` rejects the old lowercase eight-character sentinel, so nothing could address it over HTTP. `create_room` still refuses to open it twice, and `generate` cannot hand it out again once the row exists, because it is given a `taken` predicate that queries the table.

- [ ] **Step 1: Write the failing tests**

Replace `arena/tests/test_codes.py:32-34` with:

```python
def test_the_workshop_code_is_one_a_phone_can_type():
    # It used to be a lowercase sentinel, which meant no route could reach it.
    assert codes.is_valid(codes.WORKSHOP)
    assert codes.WORKSHOP == codes.WORKSHOP.upper()
```

Append to `arena/tests/test_profile_api.py`:

```python
def test_the_workshop_room_is_open_before_anybody_joins(client):
    import codes
    body = client.get(f"/api/rooms/{codes.WORKSHOP}").json()
    assert body["code"] == codes.WORKSHOP
    assert body["ranked"] is False


def test_the_workshop_room_has_profiles_to_patch(client):
    import codes
    body = client.get(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles").json()
    assert set(body["profiles"]) == set(attributes.ROLES)


def test_the_workshop_room_is_not_reopened_on_the_next_restart(client, db_path):
    # The arena will be restarted plenty of times during a tournament.
    import codes
    from fastapi.testclient import TestClient

    from app import app as arena_app
    with TestClient(arena_app) as second:
        assert second.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_codes.py tests/test_profile_api.py -v`
Expected: `test_the_workshop_code_is_one_a_phone_can_type` fails (`workshop` is eight characters), and the workshop-room tests 404.

- [ ] **Step 3: Give the workshop a real code**

Replace `arena/codes.py:9-11` with:

```python
# The dugout's workshop is a room like any other, so that a profile patch from
# the shout bar has somewhere to land. It is a real code rather than a
# sentinel; once the row exists, `generate` cannot hand it out again, because
# its `taken` predicate reads the table.
WORKSHOP = "WRKS"
```

- [ ] **Step 4: Open it at startup**

In `arena/app.py`, in `lifespan`, after `db.init_db(connection)`:

```python
    # The workshop is where the dugout tunes profiles with nobody in a dugout
    # seat, so it is opened here rather than by a phone.
    if rooms.by_code(connection, codes.WORKSHOP) is None:
        rooms.create_room(connection, "solo", codes.WORKSHOP)
```

- [ ] **Step 5: Run the tests**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_codes.py tests/test_profile_api.py -v`
Expected: all pass.

- [ ] **Step 6: Run the whole arena suite**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: all green. `test_rooms.py`'s two workshop tests pass unchanged — they create the room against a bare `conn` fixture that never ran the lifespan.

Watch for a test elsewhere that counts rooms or asserts an empty database through the `client` fixture: there is now always one room. If one fails, fix the assertion to exclude the workshop by code rather than deleting the test.

- [ ] **Step 7: Commit**

```bash
cd /home/user/agent-football
git add arena/codes.py arena/app.py arena/tests/test_codes.py arena/tests/test_profile_api.py
git commit -m "feat(arena): the workshop room is a real, addressable room"
```

---

### Task 7: The mirror bridge to the pitch's JSON files

**Files:**
- Create: `arena/mirror.py`
- Modify: `arena/app.py` (`patch_profile`)
- Test: `arena/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `attributes.ROLES`; the `patch_profile` route from Task 5; `codes.WORKSHOP` from Task 6.
- Produces: `mirror.write(role, profile_attributes) -> None` — writes `{ARENA_MIRROR_DIR}/{role}.json` atomically when that variable is set, and never raises.

**Why this exists and when it dies:** `game/frontend/src/main.js:878` still polls `player_state/{role}.json` every two seconds, and step 3 is what replaces that with a room socket. Until then a patch has to reach those files or the workshop demo stops answering the shout bar. The bridge is single-tenant by construction, which is why it is opt-in, why it only ever fires for the workshop room, and why the whole module is deleted in step 3.

- [ ] **Step 1: Write the failing tests**

Append to `arena/tests/test_profile_api.py`:

```python
def test_a_workshop_patch_reaches_the_pitchs_json_file(client, tmp_path, monkeypatch):
    # Temporary: main.js still polls these files. Deleted in step 3.
    import json

    import codes
    monkeypatch.setenv("ARENA_MIRROR_DIR", str(tmp_path))
    monkeypatch.setattr("app.SERVICE_TOKEN", "s3cret")
    client.patch(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles/defender",
                 json={"changes": {"aggression": 0.2}},
                 headers={"X-Arena-Service": "s3cret"})
    written = json.loads((tmp_path / "defender.json").read_text())
    assert written["aggression"] == 0.2


def test_a_real_match_never_writes_over_the_pitchs_files(client, phones, tmp_path,
                                                         monkeypatch):
    # One shared file cannot serve two tenants, so only the workshop uses it.
    monkeypatch.setenv("ARENA_MIRROR_DIR", str(tmp_path))
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                 json={"changes": {"aggression": 0.2}})
    assert not (tmp_path / "defender.json").exists()


def test_the_mirror_is_off_unless_it_is_asked_for(client, monkeypatch, tmp_path):
    import codes
    monkeypatch.delenv("ARENA_MIRROR_DIR", raising=False)
    monkeypatch.setattr("app.SERVICE_TOKEN", "s3cret")
    response = client.patch(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"X-Arena-Service": "s3cret"})
    assert response.status_code == 200
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_mirror_does_not_fail_the_patch(client, monkeypatch):
    import codes
    monkeypatch.setenv("ARENA_MIRROR_DIR", "/proc/nowhere/at/all")
    monkeypatch.setattr("app.SERVICE_TOKEN", "s3cret")
    response = client.patch(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"X-Arena-Service": "s3cret"})
    assert response.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profile_api.py -k mirror -v` and `... -k pitch -v`
Expected: `ModuleNotFoundError`, or the mirrored file simply never appears.

- [ ] **Step 3: Write the bridge**

Create `arena/mirror.py`:

```python
"""Temporary bridge: keep the pitch's JSON files current.

`game/frontend/src/main.js` still polls player_state/{role}.json every two
seconds. Until step 3 gives it a room socket, a profile patch has to land in
those files or the workshop demo stops answering the shout bar.

Single-tenant by construction -- one file per role, no room in the path --
which is why it is off unless ARENA_MIRROR_DIR is set, why only the workshop
room uses it, and why this whole module is deleted in step 3.
"""

import json
import logging
import os
from pathlib import Path

import attributes

logger = logging.getLogger(__name__)


def write(role, profile_attributes):
    """Copy one role's attributes to the pitch's file. Never raises.

    The environment is read on every call rather than at import, so a test can
    switch the bridge on without reloading the module.
    """
    directory = os.environ.get("ARENA_MIRROR_DIR", "")
    if not directory or role not in attributes.ROLES:
        return
    target = Path(directory) / f"{role}.json"
    try:
        # Write beside it and rename: the poller reads this file twice a
        # second and must never catch it half written.
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(profile_attributes, indent=2))
        temporary.replace(target)
    except OSError:
        # A convenience for one demo. Losing it must not fail a manager's patch.
        logger.warning("could not mirror the %s profile to %s", role, target)
```

- [ ] **Step 4: Call it from the patch route**

In `arena/app.py`, add `import mirror` to the import block (alphabetical, after `identity`), and in `patch_profile`, immediately after the `try/except` around `profiles.patch`:

```python
    if room["code"] == codes.WORKSHOP:
        # Temporary, and only here: the pitch still polls one file per role,
        # which cannot serve two rooms at once. Deleted in step 3.
        mirror.write(role, result["attributes"])
```

- [ ] **Step 5: Run the tests**

Run: `cd /home/user/agent-football/arena && uv run pytest tests/test_profile_api.py -v`
Expected: all pass.

- [ ] **Step 6: Run the whole arena suite**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
cd /home/user/agent-football
git add arena/mirror.py arena/app.py arena/tests/test_profile_api.py
git commit -m "feat(arena): temporary mirror keeping the pitch's JSON files current"
```

---

### Task 8: The specialist agents write through the arena

**Files:**
- Create: `game/agents/specialist_agents/arena_client.py`
- Modify: `game/agents/specialist_agents/tools.py:20` (the import) and `:203` onward (`update_profile`)
- Delete: `game/agents/specialist_agents/profile_guard.py`
- Delete: `dugout/tests/test_profile_guard.py`
- Modify: `game/pyproject.toml`
- Test: `game/tests/test_arena_client.py`, `game/tests/test_update_profile.py`

**Interfaces:**
- Consumes: the PATCH endpoint and its 422 body `{"detail": {"problems": [...]}}` from Task 5; `codes.WORKSHOP == "WRKS"` from Task 6.
- Produces:
  - `arena_client.ArenaError` — message fit to show a manager
  - `arena_client.DEFAULT_URL`, `DEFAULT_ROOM`, `DEFAULT_TEAM`, `TIMEOUT_SECONDS`
  - `arena_client.base_url() -> str`
  - `arena_client.patch_profile(room, team, role, changes, actor, reason) -> dict`
  - `tools.update_profile(role: str, changes: dict, tool_context: ToolContext) -> str`

**Note on the deleted dugout test:** `dugout/tests/test_profile_guard.py` loads `game/agents/specialist_agents/profile_guard.py` by path, so it cannot survive the deletion. Its coverage is `arena/tests/test_attributes.py` from Task 1. The dugout's suite cannot be installed in this environment (`uv sync` fails with HTTP 401 on `mcp-types`), so verify the deletion by grep, not by a green run, and say exactly that in your report. `dugout/attributes.py` itself is **not** touched — it goes in step 7.

- [ ] **Step 1: Add a test runner to the game project**

In `game/pyproject.toml`, after the `[tool.uv]` block:

```toml
[dependency-groups]
dev = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Run: `cd /home/user/agent-football/game && uv sync --group dev`
Expected: resolves and installs. If it fails on a private index the way `dugout/` does, stop and report BLOCKED with the exact error rather than working around it.

- [ ] **Step 2: Write the failing client tests**

Create `game/tests/test_arena_client.py`:

```python
"""The agents' way into the arena. Nothing here talks to a real arena."""

import io
import json
import urllib.error

import pytest

from agents.specialist_agents import arena_client


class FakeReply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


@pytest.fixture
def captured(monkeypatch):
    """Swallow the request and hand back a canned reply. Returns the request."""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return FakeReply(json.dumps({"role": "defender", "changed": {}}).encode())

    monkeypatch.setattr(arena_client.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_the_request_carries_the_service_token_and_the_right_verb(captured, monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    arena_client.patch_profile("WRKS", "blue", "defender", {"aggression": 0.2},
                               actor="coach", reason="too passive")
    request = captured["request"]
    assert request.get_method() == "PATCH"
    assert request.full_url.endswith("/api/rooms/WRKS/teams/blue/profiles/defender")
    assert request.get_header("X-arena-service") == "s3cret"
    assert json.loads(request.data) == {"changes": {"aggression": 0.2},
                                        "actor": "coach", "reason": "too passive"}


def test_a_role_with_a_slash_in_it_cannot_change_the_path(captured, monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    arena_client.patch_profile("WRKS", "blue", "../../health", {}, actor="a", reason="")
    assert "/profiles/..%2F..%2Fhealth" in captured["request"].full_url


def test_without_a_service_token_it_refuses_before_it_reaches_the_network(monkeypatch):
    monkeypatch.delenv("ARENA_SERVICE_TOKEN", raising=False)
    with pytest.raises(arena_client.ArenaError) as refusal:
        arena_client.patch_profile("WRKS", "blue", "defender", {}, actor="a", reason="")
    assert "ARENA_SERVICE_TOKEN" in str(refusal.value)


def test_the_arenas_own_reasons_come_back_to_the_agent(monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    body = json.dumps({"detail": {"problems": ["speed=99 is outside 0.0 to 1.0",
                                               "'wingspan' is not an attribute"]}})

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 422, "Unprocessable", {},
                                     io.BytesIO(body.encode()))

    monkeypatch.setattr(arena_client.urllib.request, "urlopen", refuse)
    with pytest.raises(arena_client.ArenaError) as refusal:
        arena_client.patch_profile("WRKS", "blue", "defender", {"speed": 99},
                                   actor="a", reason="")
    assert "speed=99 is outside 0.0 to 1.0" in str(refusal.value)
    assert "wingspan" in str(refusal.value)


def test_an_arena_that_is_not_running_says_so_plainly(monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")

    def refuse(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(arena_client.urllib.request, "urlopen", refuse)
    with pytest.raises(arena_client.ArenaError) as refusal:
        arena_client.patch_profile("WRKS", "blue", "defender", {}, actor="a", reason="")
    assert "did not answer" in str(refusal.value)


def test_the_arenas_address_can_be_moved(monkeypatch):
    monkeypatch.setenv("ARENA_URL", "http://arena.local:9000/")
    assert arena_client.base_url() == "http://arena.local:9000"
```

Create `game/tests/__init__.py` as an empty file if pytest cannot import `agents.specialist_agents` from `game/`; the game project sets `package = false` and is imported from its working directory, so running pytest from `game/` should already put it on the path.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /home/user/agent-football/game && uv run pytest tests/test_arena_client.py -v`
Expected: `ModuleNotFoundError: No module named 'agents.specialist_agents.arena_client'`.

- [ ] **Step 4: Write the client**

Create `game/agents/specialist_agents/arena_client.py`:

```python
"""The specialist agents' way into the arena.

Profiles used to be four JSON files next to the pitch, which meant every match
in the venue shared one defender. They now belong to a room and a dugout, and
this is how a tool reaches them.

Stdlib only, on purpose: the agent project's dependency list is already long
enough, and this is one request with one header.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8003"
# The workshop room, which the arena opens for itself at startup. A real match
# puts its own code in the agent's session state.
DEFAULT_ROOM = "WRKS"
DEFAULT_TEAM = "blue"
TIMEOUT_SECONDS = 5


class ArenaError(Exception):
    """The arena refused, or could not be reached. The text is fit for a manager."""


def base_url():
    return os.environ.get("ARENA_URL", DEFAULT_URL).rstrip("/")


def patch_profile(room, team, role, changes, actor, reason):
    """Move one profile in one dugout. Returns the arena's reply.

    The role and team come from a language model, so they are escaped rather
    than trusted -- the arena checks them too, but a path is not the place to
    find that out.
    """
    token = os.environ.get("ARENA_SERVICE_TOKEN", "")
    if not token:
        raise ArenaError(
            "ARENA_SERVICE_TOKEN is unset, so the arena refuses writes from the agents")

    path = "/api/rooms/{}/teams/{}/profiles/{}".format(
        *(urllib.parse.quote(part, safe="") for part in (room, team, role)))
    request = urllib.request.Request(
        base_url() + path,
        data=json.dumps({"changes": changes, "actor": actor, "reason": reason}).encode(),
        method="PATCH",
        headers={"Content-Type": "application/json", "X-Arena-Service": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as refusal:
        raise ArenaError(_reasons(refusal)) from refusal
    except (urllib.error.URLError, TimeoutError, OSError) as unreachable:
        raise ArenaError(
            f"the arena at {base_url()} did not answer ({unreachable})") from unreachable


def _reasons(refusal):
    """Pull the arena's own words out of an error reply, or fall back to the code."""
    try:
        detail = json.load(refusal)["detail"]
    except (ValueError, KeyError, TypeError):
        return f"the arena refused the change ({refusal.code})"
    if isinstance(detail, dict) and "problems" in detail:
        return "; ".join(detail["problems"])
    return str(detail)
```

- [ ] **Step 5: Run the client tests**

Run: `cd /home/user/agent-football/game && uv run pytest tests/test_arena_client.py -v`
Expected: 6 passed.

- [ ] **Step 6: Write the failing tool test**

Create `game/tests/test_update_profile.py`:

```python
"""update_profile now belongs to a room and a dugout, not to a directory."""

import pytest

pytest.importorskip("google.adk", reason="the ADK is not installed in this environment")

from agents.specialist_agents import arena_client, tools


class FakeContext:
    """Stands in for ADK's ToolContext, which is only a namespace here."""

    def __init__(self, **state):
        self.state = state


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_patch(room, team, role, changes, actor, reason):
        calls.append({"room": room, "team": team, "role": role, "changes": changes,
                      "actor": actor, "reason": reason})
        return {"role": role, "attributes": {}, "changed": changes, "seq": 1}

    monkeypatch.setattr(arena_client, "patch_profile", fake_patch)
    return calls


def test_the_room_and_dugout_come_from_the_session(sent):
    tools.update_profile("defender", {"aggression": 0.2},
                         FakeContext(room_code="7K2M", team="red"))
    assert sent[0]["room"] == "7K2M"
    assert sent[0]["team"] == "red"


def test_a_session_with_no_room_falls_back_to_the_workshop(sent):
    # The shout bar predates rooms; step 4 is what puts a room in the session.
    tools.update_profile("defender", {"aggression": 0.2}, FakeContext())
    assert sent[0]["room"] == arena_client.DEFAULT_ROOM
    assert sent[0]["team"] == arena_client.DEFAULT_TEAM


def test_the_reply_names_what_moved(sent):
    reply = tools.update_profile("defender", {"aggression": 0.2}, FakeContext())
    assert "aggression=0.2" in reply
    assert "defender" in reply


def test_a_refusal_comes_back_as_words_not_an_exception(monkeypatch):
    def refuse(room, team, role, changes, actor, reason):
        raise arena_client.ArenaError("speed=99 is outside 0.0 to 1.0")

    monkeypatch.setattr(arena_client, "patch_profile", refuse)
    reply = tools.update_profile("defender", {"speed": 99}, FakeContext())
    assert reply.startswith("Rejected: ")
    assert "speed=99" in reply


def test_a_patch_that_changes_nothing_says_so(monkeypatch):
    monkeypatch.setattr(arena_client, "patch_profile",
                        lambda room, team, role, changes, actor, reason:
                        {"role": role, "attributes": {}, "changed": {}, "seq": 1})
    assert tools.update_profile("defender", {"aggression": 0.2},
                                FakeContext()).startswith("No change")
```

- [ ] **Step 7: Run it to verify it fails**

Run: `cd /home/user/agent-football/game && uv run pytest tests/test_update_profile.py -v`
Expected: FAIL — `update_profile` takes two arguments, and `arena_client` is not imported by `tools`.

- [ ] **Step 8: Re-point the tool**

In `game/agents/specialist_agents/tools.py`, replace line 20:

```python
from . import profile_guard
```

with:

```python
from google.adk.tools import ToolContext

from . import arena_client
```

and replace the whole of `update_profile` with:

```python
def update_profile(role: str, changes: dict, tool_context: ToolContext) -> str:
    """Move one player's attributes in this match's dugout.

    `changes` maps attribute names to new values. The arena validates every one
    of them and refuses the whole write if any is out of range, so a refusal
    comes back naming every problem at once and can be corrected in one go.

    The room and dugout come from the session rather than from a constant,
    because more than one match runs at a time.
    """
    room = tool_context.state.get("room_code") or arena_client.DEFAULT_ROOM
    team = tool_context.state.get("team") or arena_client.DEFAULT_TEAM
    try:
        result = arena_client.patch_profile(
            room, team, role, changes,
            actor=tool_context.state.get("actor") or "coach",
            reason=tool_context.state.get("reason") or "",
        )
    except arena_client.ArenaError as refusal:
        return f"Rejected: {refusal}"

    if not result["changed"]:
        return f"No change: the {team} {role} already had those values."
    moved = ", ".join(f"{key}={value}"
                      for key, value in sorted(result["changed"].items()))
    return f"Updated the {team} {role} in room {room}: {moved}"
```

Leave `PLAYER_STATE_DIR`, `initialize_profiles` and everything else in the file alone — the pitch still reads those files, and step 3 is what stops it.

- [ ] **Step 9: Run the tool tests**

Run: `cd /home/user/agent-football/game && uv run pytest -v`
Expected: all pass, or `test_update_profile.py` skips with the ADK message. A skip is acceptable and must be reported as a skip; the client tests must pass either way.

- [ ] **Step 10: Delete the old guard and its test**

```bash
cd /home/user/agent-football
git rm game/agents/specialist_agents/profile_guard.py dugout/tests/test_profile_guard.py
grep -rn "profile_guard" --include=*.py . | grep -v "/.venv/"
```

Expected: the grep prints nothing. If it prints anything, fix that reference before committing.

- [ ] **Step 11: Check the four specialists still import cleanly**

Run: `cd /home/user/agent-football/game && uv run python -c "from agents.specialist_agents import defender, midfielder, forward, goalkeeper; print('ok')"`
Expected: `ok`. Each of those registers `tools=[update_profile]`; ADK reads the new `tool_context` parameter as an injected argument rather than one the model supplies, so the registrations need no change.

- [ ] **Step 12: Commit**

```bash
cd /home/user/agent-football
git add game/pyproject.toml game/uv.lock game/tests game/agents/specialist_agents/arena_client.py game/agents/specialist_agents/tools.py
git commit -m "feat(agents): update_profile writes through the arena, scoped to a room"
```

---

### Task 9: Substitutions become per-room, per-team

**Files:**
- Modify: `game/agents/football_mcp_server.py`
- Test: `game/tests/test_mcp_substitutions.py`

**Interfaces:**
- Consumes: `arena_client.DEFAULT_ROOM`, `arena_client.DEFAULT_TEAM` from Task 8 — imported for their values only, so the default room name has one definition.
- Produces:
  - `football_mcp_server.substitutions_path(room, team) -> str`
  - `report_injury(role, severity="knock", room=DEFAULT_ROOM, team=DEFAULT_TEAM) -> str`

**Why the MCP server is different:** it is a subprocess with no `ToolContext`, so the room and team arrive as tool arguments with defaults rather than from session state. Injuries stay on disk for now — they move into the arena's event log in step 5 with the rest of match reporting.

- [ ] **Step 1: Read the current file**

Run: `cd /home/user/agent-football && cat game/agents/football_mcp_server.py`

Note the exact names of `PLAYER_STATE_DIR`, `SUBSTITUTIONS_FILE`, `VALID_ROLES`, `_write_entry` and the `report_injury` tool, and keep every one of them that this task does not name.

- [ ] **Step 2: Write the failing test**

Create `game/tests/test_mcp_substitutions.py`:

```python
"""Substitutions belong to a room and a dugout, not to the whole venue."""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed in this environment")

from agents import football_mcp_server as server


def test_two_rooms_do_not_share_a_substitutions_file():
    first = server.substitutions_path("7K2M", "blue")
    second = server.substitutions_path("7K2M", "red")
    third = server.substitutions_path("QQ44", "blue")
    assert len({first, second, third}) == 3


def test_a_room_code_cannot_walk_out_of_the_directory():
    path = server.substitutions_path("../../etc", "blue")
    assert "/etc/" not in path
    assert path == server.substitutions_path(server.DEFAULT_ROOM, "blue")


def test_an_injury_lands_in_its_own_rooms_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PLAYER_STATE_DIR", str(tmp_path))
    server.report_injury("defender", "knock", room="7K2M", team="red")
    written = json.loads(open(server.substitutions_path("7K2M", "red")).read())
    assert "defender" in written


def test_the_default_room_still_writes_the_file_the_pitch_polls(tmp_path, monkeypatch):
    # Temporary: main.js polls player_state/substitutions.json. Deleted in step 3.
    monkeypatch.setattr(server, "PLAYER_STATE_DIR", str(tmp_path))
    server.report_injury("defender", "knock")
    assert (tmp_path / "substitutions.json").exists()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /home/user/agent-football/game && uv run pytest tests/test_mcp_substitutions.py -v`
Expected: FAIL — `module 'agents.football_mcp_server' has no attribute 'substitutions_path'`.

- [ ] **Step 4: Scope the path**

In `game/agents/football_mcp_server.py`, keep `PLAYER_STATE_DIR` and `SUBSTITUTIONS_FILE` where they are — `SUBSTITUTIONS_FILE` stays because the pitch still polls it — and add below them:

```python
from .specialist_agents.arena_client import DEFAULT_ROOM, DEFAULT_TEAM

VALID_TEAMS = ("blue", "red")


def substitutions_path(room, team):
    """Where this dugout's injuries live.

    One file for the whole venue meant a knock in one match subbed a player off
    in another. Room and team come from a language model, so an unrecognised
    one falls back to the workshop rather than becoming part of a path.
    """
    if not room.isalnum() or len(room) > 8:
        room = DEFAULT_ROOM
    if team not in VALID_TEAMS:
        team = DEFAULT_TEAM
    directory = os.path.join(PLAYER_STATE_DIR, "substitutions")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, f"{room.upper()}__{team}.json")
```

If `football_mcp_server.py` is run as a script rather than imported as part of the `agents` package, that relative import will fail. In that case use the plain values instead and leave a comment saying why:

```python
# Duplicated from specialist_agents.arena_client because this module is also
# launched as a standalone script, which puts a relative import out of reach.
DEFAULT_ROOM = "WRKS"
DEFAULT_TEAM = "blue"
```

Check which applies with `grep -n "football_mcp_server" -r game --include=*.py` and use whichever import form the launcher supports.

- [ ] **Step 5: Route the write**

Change `_write_entry` to take the room and team and use `substitutions_path`, and keep one legacy write:

```python
def _write_entry(role, entry, room=DEFAULT_ROOM, team=DEFAULT_TEAM):
    """Merge one injury into this dugout's file."""
    for path in _targets(room, team):
        existing = {}
        if os.path.exists(path):
            try:
                with open(path) as handle:
                    existing = json.load(handle)
            except (OSError, ValueError):
                existing = {}
        existing[role] = entry
        with open(path, "w") as handle:
            json.dump(existing, handle, indent=2)


def _targets(room, team):
    """This dugout's file, plus the one the pitch still polls.

    The second is temporary: main.js reads player_state/substitutions.json
    every two seconds and gains a room socket in step 3.
    """
    paths = [substitutions_path(room, team)]
    if room == DEFAULT_ROOM and team == DEFAULT_TEAM:
        paths.append(SUBSTITUTIONS_FILE)
    return paths
```

Give `report_injury` the two new arguments and pass them through, keeping the rest of its body and its docstring's description of `severity`:

```python
@mcp.tool()
def report_injury(role: str, severity: str = "knock",
                  room: str = DEFAULT_ROOM, team: str = DEFAULT_TEAM) -> str:
```

Its docstring gains one line: `room` and `team` name the match and dugout; they default to the workshop.

- [ ] **Step 6: Run the tests**

Run: `cd /home/user/agent-football/game && uv run pytest -v`
Expected: all pass, or `test_mcp_substitutions.py` skips if the MCP SDK is absent. Report a skip as a skip.

- [ ] **Step 7: Commit**

```bash
cd /home/user/agent-football
git add game/agents/football_mcp_server.py game/tests/test_mcp_substitutions.py
git commit -m "feat(agents): substitutions belong to a room and a dugout"
```

---

### Task 10: Document what the arena reads and serves

**Files:**
- Create: `arena/README.md`
- Modify: `README.md` (the port table)

**Interfaces:**
- Consumes: every environment variable and route from Tasks 1-7. Nothing consumes this task.

This closes a carried item from step 1: the arena shipped without a README, and the root README's port table does not know about 8003.

- [ ] **Step 1: Collect the truth from the code, not from this plan**

```bash
cd /home/user/agent-football/arena
grep -n "os.environ" *.py
grep -n "^@app\." app.py
```

Use what those print. If they disagree with the table below, the code wins and the README records the code.

- [ ] **Step 2: Write `arena/README.md`**

````markdown
# Arena

Rooms, seats, profiles and the live match bus. Runs on **:8003** beside the
pitch (:5173), the coach (:8000), the captain (:8001) and the dugout (:8002).

It owns everything that used to be global — who is playing, which match they
are in, what happened in it, and what their players' attributes are — so more
than one person can play at once.

## Running it

```bash
cd arena
uv run uvicorn app:app --port 8003
```

```bash
uv run pytest
```

## Environment

| Variable | Default | What it does |
|---|---|---|
| `ARENA_DB` | `arena/arena.db` | The SQLite file. Tests point it at a throwaway path. |
| `ARENA_EMAIL_SALT` | `arena-dev-salt` | Salts the email hash. Keeps a literal default on purpose: change it and every returning player loses their history. |
| `ARENA_SECRET` | random per start | Signs session cookies. Unset means sessions do not survive a restart — set it in anything longer-lived than a demo. |
| `ARENA_SERVICE_TOKEN` | unset | Lets a server-side caller patch profiles without a phone session. Unset refuses every such call. The specialist agents need the same value. |
| `ARENA_MIRROR_DIR` | unset | Temporary: copies the workshop room's profiles into the pitch's `player_state/` files, which it still polls. Deleted in step 3. |

## Endpoints

| Method | Path | Who |
|---|---|---|
| GET | `/health` | anyone |
| POST | `/api/players` | anyone — name and email in, session cookie out |
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
A refusal is `422` with `{"detail": {"problems": [...]}}` — every reason at
once, because the caller is often a language model correcting itself.

## The workshop room

The arena opens one room for itself at startup, code `WRKS`, unranked. It is
where the dugout tunes profiles with nobody sitting in a dugout seat.
````

- [ ] **Step 3: Add 8003 to the root port table**

Open `README.md`, find the table of services and ports, and add a row for the arena on 8003 in the same format as the rows around it. Do not restyle the table.

- [ ] **Step 4: Verify the documented commands actually run**

Run: `cd /home/user/agent-football/arena && uv run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /home/user/agent-football
git add arena/README.md README.md
git commit -m "docs(arena): README for the arena, and 8003 in the port table"
```

---

## Self-Review

**1. Spec coverage** — against `## Profiles` (spec lines 207-235):

| Spec requirement | Task |
|---|---|
| `GET /api/rooms/{room}/teams/{team}/profiles` | 4 |
| `GET /api/rooms/{room}/teams/{team}/profiles/{role}` | 4 |
| `PATCH .../profiles/{role}` with `{changes, reason, actor}` | 5 |
| A PATCH validates | 1, 3, 5 |
| A PATCH persists | 2, 3 |
| A PATCH appends a `profile.patch` event | 5 |
| A PATCH broadcasts the delta on the room bus | 5 |
| Consolidate the two validators, keeping `profile_guard`'s reason-returning behaviour | 1, 8 |
| Seed both blue and red for every room | 2 |
| Room-scope `update_profile` with a `ToolContext` reading `room_id`/`team` | 8 |
| Same for the MCP server's `substitutions.json` | 9 |

Two deliberate departures from the spec's literal text, both settled before planning:

- **`dugout/attributes.py` is not deleted here.** The spec's step 2 says "one validator replacing `profile_guard.py` and `dugout/attributes.py`". Deleting the dugout's copy now means pulling a cross-project dependency forward for a module the dugout is about to stop needing anyway; it goes in step 7 when the dugout moves onto the arena. The duplication survives one step longer, on purpose. Tasks 1 and 8 say so in their comments and docstrings.
- **The filesystem poll is not deleted here.** The spec's build order names "delete the filesystem poll" under step 2, but the poll has no replacement until the frontend is room-scoped in step 3. Task 7's mirror and Task 9's `_targets` keep the files current so the repo still works, and both are labelled for deletion in step 3.

The spec's key names are `room_id` and `team`; Task 8 reads `room_code` from session state instead, because the arena addresses rooms by their four-character code everywhere and an id would have to be looked up. Task 8's interfaces block states the name.

**2. Placeholder scan** — no "TBD", no "add appropriate error handling", no "similar to Task N". Every code step carries the code. Task 9's Step 4 offers two import forms because which one works depends on how the MCP server is launched; the step says how to find out and what to write in each case, so it is a decision procedure rather than a placeholder. Task 10's Step 3 describes an edit to a table this plan has not read; it says to match the surrounding rows, which is the whole of the change.

**3. Type consistency**

- `attributes.validate(role, changes)` — two arguments, no `state_dir`, used that way in Task 3 only.
- `attributes.baseline_for(role)` — used in Tasks 2, 7 and the tests of 2, 3, 4.
- `profiles.seed(conn, room_id, teams)` — three arguments, called from `rooms.create_room` with `TEAMS`, and from a test with `("blue", "red")`.
- `profiles.patch(...)` returns `{"role", "attributes", "changed"}`; the route adds `"seq"`. Task 5's tests read `changed` and `seq`; Task 8's fake returns all four.
- `arena_client.patch_profile(room, team, role, changes, actor, reason)` — six arguments; Task 8's fake in `test_update_profile.py` and the real call in `tools.update_profile` agree, and Task 9's defaults come from the same module.
- `mirror.write(role, profile_attributes)` — two arguments, one call site.
- `_profile_room(request, code)` returns `(connection, room)` in that order at all five call sites.
- The event kind is `profile.patch` in Task 5's append, its broadcast and its test.
- `codes.WORKSHOP` is `"WRKS"` from Task 6 onward; `arena_client.DEFAULT_ROOM` is the same string, duplicated across a project boundary on purpose and noted where it appears.

**4. Ordering** — Task 2's `test_seeding_twice_leaves_the_first_values_alone` is red until Task 3, which Task 2 states and Task 3's Step 4 confirms green. Task 7 lands before Task 8 so the pitch's files never go stale between commits. Nothing else crosses a task boundary.
</content>
</invoke>
