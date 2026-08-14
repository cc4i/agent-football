# The grounds host farm - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move match physics out of the browser tab into a server-owned Chromium farm, so matches survive closed lids and one big screen can browse fifty of them.

**Architecture:** A new `grounds/` service runs one Chromium holding one page loaded from `ARENA_URL/pitch/host.html`, which makes N `Phaser.HEADLESS` games driving the existing host protocol. The arena assigns matches to grounds over a new `/ws/grounds` control socket at kickoff, and the room's physics token stops being handed to browsers - screens get a separate screen token that proves lobby ownership only. The big screen drops its `#pitch` iframe for one directly-mounted viewer scene it cuts between.

**Tech Stack:** Python 3.14 / FastAPI / psycopg / Postgres (arena), Playwright + Chromium (grounds), Vite 8 + Phaser 4.1 + vitest (pitch), pytest + pytest-asyncio (Python tests), Cloud Run gen2 (deploy).

**Spec:** `docs/superpowers/specs/2026-08-15-grounds-host-farm-design.md`

## Global Constraints

- **Never use the em dash.** Plain dash `-` only. This applies to code comments, docstrings, commit messages, and this plan's output.
- **Never add an agent name as commit co-author.**
- **The simulation must not be re-implemented.** `game/frontend/src/game.js` keeps its physics. The only change to it in this plan is routing `Math.random()` onto a seeded RNG.
- **Stage 2 is untouched.** `dugout/app.py:38` opens bare `:5173` with no `?room=`, which is the workshop room. Nothing in this plan may change what that URL does.
- **`fake_host.py` stays valid.** `_handle_from_host` is not modified. The arena's own tests must never need a browser.
- **One grounds instance.** `minScale`/`maxScale` both `1`. Two would double-run any assigned room.
- **Prod data is disposable.** The user has authorised destructive changes to the production database with no backup. Migrations may drop and recreate.
- **Test commands:** arena `cd arena && uv run pytest`, pitch `cd game/frontend && npm test`, grounds `cd grounds && uv run pytest`.
- **A grounds restart does not resume matches.** The existing sweep abandons them. No resume logic anywhere.

---

### Task 1: Seed the simulation

`game.js` calls `Math.random()` twenty-two times and seeds nothing, so no test can assert two runs of the same match agree. Everything downstream that wants to prove the grounds and a tab play the same football needs this first.

**Files:**
- Modify: `game/frontend/src/game.js` (the 22 `Math.random()` call sites, and `SoccerGameScene`'s constructor)
- Test: `game/frontend/test/seeded-rng.test.js`

**Interfaces:**
- Produces: `new SoccerGameScene({ role, seed })` - `seed` is an optional string. When given, the scene calls `this.rng = new Phaser.Math.RandomDataGenerator([seed])` in `create()` before anything random happens, and every former `Math.random()` becomes `this.chance()`. When absent, `chance()` falls back to `Math.random()` so the workshop lab keeps behaving as it does today.
- Produces: `scene.chance()` - returns a float in `[0, 1)`.

- [ ] **Step 1: Write the failing test**

```js
// game/frontend/test/seeded-rng.test.js
import { describe, it, expect } from 'vitest';
import Phaser from 'phaser';

// The scene's own chance() is what every former Math.random() call goes
// through. Testing it directly rather than through a booted game keeps this
// fast: what matters is that one seed is one stream, and that no seed still
// works.
function chanceFor(seed) {
  const rng = new Phaser.Math.RandomDataGenerator([seed]);
  return () => rng.frac();
}

describe('seeded chance', () => {
  it('gives the same stream twice for one seed', () => {
    const a = chanceFor('ABCD-1');
    const b = chanceFor('ABCD-1');
    const first = Array.from({ length: 20 }, a);
    const second = Array.from({ length: 20 }, b);
    expect(first).toEqual(second);
  });

  it('gives different streams for different seeds', () => {
    const a = Array.from({ length: 20 }, chanceFor('ABCD-1'));
    const b = Array.from({ length: 20 }, chanceFor('ABCD-2'));
    expect(a).not.toEqual(b);
  });

  it('stays inside [0, 1)', () => {
    const a = chanceFor('ABCD-1');
    for (let i = 0; i < 500; i += 1) {
      const value = a();
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });
});
```

- [ ] **Step 2: Run it and watch it pass**

Run: `cd game/frontend && npx vitest run test/seeded-rng.test.js`
Expected: PASS. This test pins Phaser's RNG contract, not our code - it is the safety net for step 3, and it must be green before the scene is touched so a later failure means *we* broke something.

- [ ] **Step 3: Write the failing test for the scene's own hook**

```js
// append to game/frontend/test/seeded-rng.test.js
import { SoccerGameScene } from '../src/game.js';

describe('SoccerGameScene chance', () => {
  it('is deterministic when seeded', () => {
    const one = new SoccerGameScene({ role: 'host', seed: 'ABCD-1' });
    const two = new SoccerGameScene({ role: 'host', seed: 'ABCD-1' });
    one.seedChance();
    two.seedChance();
    const a = Array.from({ length: 20 }, () => one.chance());
    const b = Array.from({ length: 20 }, () => two.chance());
    expect(a).toEqual(b);
  });

  it('falls back to Math.random with no seed', () => {
    const scene = new SoccerGameScene({ role: 'host' });
    scene.seedChance();
    const value = scene.chance();
    expect(value).toBeGreaterThanOrEqual(0);
    expect(value).toBeLessThan(1);
  });
});
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd game/frontend && npx vitest run test/seeded-rng.test.js`
Expected: FAIL with `scene.seedChance is not a function`.

- [ ] **Step 5: Add the hook to the scene**

In `game/frontend/src/game.js`, in the constructor beside `this.role` (currently `:99`):

```js
    // A match the arena can hand a seed to is a match two processes can be
    // asked to play identically. Without one the simulation is unreproducible,
    // which is why nothing could ever assert that the grounds and a tab agree.
    // Absent, chance() is Math.random() and the workshop lab is unchanged.
    this.seed = options.seed || null;
    this.rng = null;
```

Add the two methods next to it:

```js
  seedChance() {
    this.rng = this.seed ? new Phaser.Math.RandomDataGenerator([this.seed]) : null;
  }

  chance() {
    return this.rng ? this.rng.frac() : Math.random();
  }
```

Call `this.seedChance()` as the first statement of `create()`, before any display object is made.

- [ ] **Step 6: Route every random site through it**

Run `grep -n 'Math\.random()' game/frontend/src/game.js` to list all twenty-two. Replace each `Math.random()` with `this.chance()`. Where a call is inside a callback that has lost `this`, capture `const chance = () => this.chance();` in the enclosing method rather than binding - the scene is the only owner of the stream and the arrow keeps it that way.

Verify none remain:

```bash
cd game/frontend && grep -c 'Math\.random()' src/game.js
```
Expected: `0`.

- [ ] **Step 7: Run the whole pitch suite**

Run: `cd game/frontend && npm test`
Expected: PASS, all files. If `sprite-frames.test.js` or `kick-direction.test.js` fail, a replacement changed behaviour rather than just its source of randomness - fix the replacement, not the test.

- [ ] **Step 8: Commit**

```bash
git add game/frontend/src/game.js game/frontend/test/seeded-rng.test.js
git commit -m "feat(game): a match that can be played the same way twice

Twenty-two Math.random() calls and nothing seeding them, so no test could
ever assert that two runs of one match agree. That is the assertion the
grounds need: the same seed in a tab and on the farm has to produce the
same football, or moving physics off the screen is an act of faith.

The stream changes, so matches play differently than they did. The
README's tuning table measures a squad against a squad and still holds;
re-running it would not reproduce the same eight scorelines."
```

---

### Task 2: Split the screen token from the physics token

`host_client_id` does two unrelated jobs today. Once physics leaves the browser, one secret cannot be both the thing the grounds hold and the thing a screen proves lobby ownership with.

**Files:**
- Modify: `arena/db.py:145-148` (the `MIGRATIONS` block), `arena/db.py:55` (the `room` table)
- Modify: `arena/rooms.py:143-167` (`create_room`)
- Modify: `arena/app.py:635-670` (`open_room` and the mode switch), `arena/app.py:1140` (`_require_host`)
- Test: `arena/tests/test_tokens.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `room["screen_client_id"]` - a `secrets.token_urlsafe(16)` minted beside `host_client_id` in `create_room`.
- Produces: `_require_screen(room, offered)` in `app.py`, replacing `_require_host`. Same constant-time comparison, against `screen_client_id`.
- Produces: `POST /api/rooms` returns `{"screen_token": ...}` where it returned `{"host_token": ...}`. The physics token is never serialised to an HTTP response again.
- Produces: `PATCH /api/rooms/{code}` (the mode switch) reads `body.screen_token`.

- [ ] **Step 1: Write the failing test**

```python
# arena/tests/test_tokens.py
"""Two secrets that used to be one, and the wall between them.

The screen token says "I opened this lobby". The physics token says "I am
simulating this match". Before the grounds existed they were the same string
because the same tab did both jobs. They are not the same job.
"""
import rooms


def test_a_room_mints_two_different_tokens(conn):
    room = rooms.create_room(conn, "versus")
    assert room["host_client_id"]
    assert room["screen_client_id"]
    assert room["host_client_id"] != room["screen_client_id"]


def test_opening_a_room_never_returns_the_physics_token(client):
    body = client.post("/api/rooms", json={"mode": "versus"}).json()
    assert "screen_token" in body
    assert "host_token" not in body
    assert body["screen_token"]
    # Belt and braces: the physics token must not appear anywhere in the
    # response under any key, however the payload is reshaped later.
    room = rooms.by_code(client.app.state.conn, body["code"])
    assert room["host_client_id"] not in repr(body)


def test_the_mode_switch_takes_the_screen_token(client):
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    answer = client.patch(f"/api/rooms/{opened['code']}",
                          json={"mode": "versus", "screen_token": opened["screen_token"]})
    assert answer.status_code == 200
    assert answer.json()["mode"] == "versus"


def test_the_mode_switch_refuses_the_physics_token(client):
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    room = rooms.by_code(client.app.state.conn, opened["code"])
    answer = client.patch(f"/api/rooms/{opened['code']}",
                          json={"mode": "versus", "screen_token": room["host_client_id"]})
    assert answer.status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd arena && uv run pytest tests/test_tokens.py -v`
Expected: FAIL - `KeyError: 'screen_client_id'` on the first test.

- [ ] **Step 3: Add the column**

In `arena/db.py`, add to the `room` table definition beside `host_client_id TEXT` (`:55`):

```sql
    screen_client_id TEXT,
```

and to `MIGRATIONS`, after the `started_at` line:

```sql
ALTER TABLE room ADD COLUMN IF NOT EXISTS screen_client_id TEXT;
```

- [ ] **Step 4: Mint it in `create_room`**

Replace the comment and INSERT in `arena/rooms.py:143-167` with:

```python
    # Two secrets, because there are two claims to make and they stopped being
    # the same claim when physics left the browser. The physics token is handed
    # to the grounds at kick-off and to nobody else, ever. The screen token
    # goes back to whoever opened the room and proves only that: this lobby is
    # mine to configure, and my socket being open is why it is still listed.
    conn.execute(
        "INSERT INTO room (code, mode, status, ranked, host_client_id, "
        "screen_client_id, created_at) VALUES (%s, %s, 'lobby', %s, %s, %s, %s)",
        (code, mode, 0 if code == codes.WORKSHOP else 1,
         secrets.token_urlsafe(16), secrets.token_urlsafe(16), time.time()),
    )
```

- [ ] **Step 5: Rename the guard**

In `arena/app.py`, rename `_require_host` to `_require_screen` and change what it compares:

```python
def _require_screen(room, offered):
    """Refuse anybody but the screen that opened this room.

    This used to be `_require_host` and used to compare the physics token,
    because the screen that opened a room was the thing simulating it. The
    grounds simulate now, so the two claims came apart: this one is "I own this
    lobby" and it is the only one a browser is ever handed.

    Compared on bytes in constant time for the reason given at `_same_secret`:
    the token arrives from outside the process, and a wrong one has to be a
    wrong one rather than a crash or a stopwatch.
    """
    held = room["screen_client_id"]
    if not offered or not held or not _same_secret(_text_bytes(offered), _text_bytes(held)):
        raise HTTPException(403, "only the screen that opened this room can change it")
```

Update the one call site at `app.py:667` to `_require_screen(room, body.screen_token)`, and rename the Pydantic field on the mode-switch request model from `host_token` to `screen_token`.

- [ ] **Step 6: Stop returning the physics token**

In `open_room` (`app.py:635-647`), change the response key from `host_token: room["host_client_id"]` to `screen_token: room["screen_client_id"]`.

- [ ] **Step 7: Run the new tests**

Run: `cd arena && uv run pytest tests/test_tokens.py -v`
Expected: PASS, all four.

- [ ] **Step 8: Run the whole arena suite and fix the fallout**

Run: `cd arena && uv run pytest`
Expected: `test_mode_switch.py` and any test posting `host_token` fail. Update them to `screen_token`. `test_room_socket.py` should still pass untouched - it drives the socket with `host_client_id`, which is still the physics token and still what `_handle_from_host` wants.

- [ ] **Step 9: Update the wall to hold the screen token**

In `arena/static/arena.js`, rename `hostToken()` to `screenToken()` and its sessionStorage key from whatever it is today to `screen_token`, and change the `POST /api/rooms` response field it reads from `host_token` to `screen_token`. Do not delete it: the wall still needs it for the mode switch and still bears it on the room socket. Update the mode-switch `PATCH` body key to `screen_token`.

- [ ] **Step 10: Commit**

```bash
git add arena/db.py arena/rooms.py arena/app.py arena/static/arena.js arena/tests/
git commit -m "feat(arena): two tokens, because there were always two claims

host_client_id proved 'I am simulating this match' and 'this lobby is
mine' with one string, which worked exactly as long as the same tab did
both. The grounds do the first job now, so a screen holding the physics
token would be holding a credential for work it does not do.

The physics token stops being serialised to an HTTP response at all. It
goes to the grounds over the control socket and nowhere else."
```

---

### Task 3: Liveness by kind

Socket presence is what keeps a room alive since `a7588f2`. With two kinds of client holding sockets, a screen's socket must not vouch for a *live* match - or a wall left open on a match whose grounds died keeps it live for the rest of the evening and the sweep can never reach it.

**Files:**
- Modify: `arena/app.py:1258-1302` (`_HeldRooms`), `arena/app.py:1341-1410` (`room_socket`'s handshake and teardown), `arena/app.py:1540` (`_give_up_on_the_missing`)
- Modify: `arena/rooms.py:469-488` (`heard_from_all`)
- Test: `arena/tests/test_abandoning.py` (extend)

**Interfaces:**
- Consumes: `room["screen_client_id"]` from Task 2.
- Produces: `_HeldRooms.took(code, kind)` / `.gave_up(code, kind)` / `.codes(kind)` where `kind` is `"screen"` or `"grounds"`.
- Produces: `rooms.heard_from_all(conn, room_codes, when, statuses=("lobby", "live"))`.

- [ ] **Step 1: Write the failing test**

```python
# append to arena/tests/test_abandoning.py
def test_a_screen_socket_does_not_keep_a_live_match_alive(client, conn, clock):
    """The wall watching a match whose grounds died must not vouch for it.

    A screen's open socket is proof of a lobby, because the screen is the only
    thing that can ever run that lobby. It is not proof of a match: the grounds
    run those, and if they have stopped, somebody watching the last frame go
    still is exactly who must not be believed.
    """
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    code = opened["code"]
    room = rooms.by_code(conn, code)

    with client.websocket_connect(f"/ws/rooms/{code}?client_id={opened['screen_token']}"):
        # Live, and heard from once by the grounds, then silence from them.
        _sit_and_start(client, conn, code)
        rooms.heard_from(conn, room["id"], clock.now)
        clock.advance(app.HOST_GONE_SECONDS + app.SWEEP_SECONDS + 1)
        app._give_up_on_the_missing(conn, client.app.state.bus, clock.now,
                                    client.app.state.held)

    assert rooms.by_code(conn, code)["status"] == "abandoned"


def test_a_screen_socket_does_keep_its_lobby_alive(client, conn, clock):
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    code = opened["code"]
    room = rooms.by_code(conn, code)

    with client.websocket_connect(f"/ws/rooms/{code}?client_id={opened['screen_token']}"):
        rooms.heard_from(conn, room["id"], clock.now)
        clock.advance(app.HOST_GONE_SECONDS + app.SWEEP_SECONDS + 1)
        app._give_up_on_the_missing(conn, client.app.state.bus, clock.now,
                                    client.app.state.held)
        assert rooms.by_code(conn, code)["status"] == "lobby"
```

Read the existing tests in `test_abandoning.py` first and match their fixtures - `clock`, `_sit_and_start` and the sweep's call signature must be whatever that file already uses. If there is no `_sit_and_start` helper, drive the room to live the way the neighbouring tests do.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd arena && uv run pytest tests/test_abandoning.py -v -k screen_socket`
Expected: FAIL - the first test finds the room still `live`, because the screen's socket is stamping it.

- [ ] **Step 3: Count holds by kind**

Replace `_HeldRooms`'s body (keeping its docstring, and adding the paragraph below to it):

```python
    def __init__(self):
        self._held = {"screen": collections.Counter(), "grounds": collections.Counter()}

    def took(self, code, kind):
        self._held[kind][code] += 1

    def gave_up(self, code, kind):
        counter = self._held[kind]
        if counter[code] > 1:
            counter[code] -= 1
        else:
            # Popped rather than decremented to zero, so the counter is the size
            # of the venue rather than of the evening.
            counter.pop(code, None)

    def codes(self, kind):
        return list(self._held[kind])
```

Append to the docstring:

```
    Counted by kind, because the two kinds of client prove different things. A
    screen's socket proves its lobby is real: that screen is the only thing
    that can ever run it. It proves nothing about a live match, which the
    grounds run -- and a wall left open on a match whose grounds died would
    otherwise vouch for it for the rest of the evening, with the sweep unable
    to reach a match nobody is simulating.
```

- [ ] **Step 4: Decide the kind at the handshake**

In `room_socket`, replace the `holding` block:

```python
    # Which kind of client this socket is, settled once at the handshake: both
    # tokens are minted when the room is opened and neither changes, so a
    # client that holds one now holds it for the life of the room.
    holding = None
    if client_id:
        if room["host_client_id"] and _same_secret(
                _text_bytes(client_id), _text_bytes(room["host_client_id"])):
            holding = "grounds"
        elif room["screen_client_id"] and _same_secret(
                _text_bytes(client_id), _text_bytes(room["screen_client_id"])):
            holding = "screen"
    if holding:
        socket.app.state.held.took(code, holding)
```

and the teardown:

```python
        if holding:
            socket.app.state.held.gave_up(code, holding)
```

- [ ] **Step 5: Let the stamp name its statuses**

In `arena/rooms.py`, give `heard_from_all` a `statuses` parameter:

```python
def heard_from_all(conn, room_codes, when, statuses=("lobby", "live")):
    """Stamp every room named, in one statement.
    ...
    `statuses` is what the caller is entitled to vouch for. A screen may vouch
    for its lobby and not for a match, because it does not run the match; the
    grounds may vouch for either.
    """
    if not room_codes:
        return
    conn.execute(
        "UPDATE room SET last_heard_at = %s "
        "WHERE code = ANY(%s) AND status = ANY(%s)",
        (when, list(room_codes), list(statuses)),
    )
    conn.commit()
```

Keep the existing docstring paragraphs above the new one.

- [ ] **Step 6: Stamp the two kinds separately in the sweep**

In `_give_up_on_the_missing`, replace the single `heard_from_all` call:

```python
    if held is not None:
        rooms.heard_from_all(connection, held.codes("screen"), now, statuses=("lobby",))
        rooms.heard_from_all(connection, held.codes("grounds"), now)
```

- [ ] **Step 7: Run the tests**

Run: `cd arena && uv run pytest tests/test_abandoning.py -v`
Expected: PASS, including both new tests.

- [ ] **Step 8: Run the whole suite**

Run: `cd arena && uv run pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add arena/app.py arena/rooms.py arena/tests/test_abandoning.py
git commit -m "fix(arena): a screen vouches for its lobby, not for a match

Socket presence became the proof of liveness a commit ago, when the
screen was still the host and one socket meant both things. Two kinds of
client hold sockets now, and only one of them is running football.

Left as it was, a wall left open on a match whose grounds had died would
stamp that match alive every sweep, forever, and the one mechanism that
exists to notice a match nobody is simulating could never fire."
```

---

### Task 4: The `/ws/grounds` control plane

**Files:**
- Create: `arena/grounds.py`
- Modify: `arena/app.py` (mount the socket, hold the registry on `app.state`)
- Test: `arena/tests/test_grounds_socket.py`

**Interfaces:**
- Consumes: `_service_token_ok(offered)` - the existing `X-Arena-Service` check at `app.py:1075`. Read it and call it by its real name.
- Produces: `arena/grounds.py` exporting `Grounds`:
  - `Grounds.joined(socket, capacity) -> None`
  - `Grounds.left(socket) -> None`
  - `Grounds.assign(code) -> bool` - picks the connected instance with the most spare capacity, records the assignment, returns `False` if none has room.
  - `Grounds.release(code) -> object | None` - forgets the assignment, returns the socket that had it.
  - `Grounds.socket_for(code) -> object | None`
  - `Grounds.capacity() -> int` - total announced capacity across connected instances.
  - `Grounds.running() -> int` - total assigned rooms.
- Produces: `app.state.grounds = Grounds()`, and `@app.websocket("/ws/grounds")`.

- [ ] **Step 1: Write the failing test for the registry**

```python
# arena/tests/test_grounds_socket.py
"""Who is available to run a match, and what happens when nobody is.

The registry is deliberately dumb: it knows how many matches each connected
instance said it would take and how many it has been given. It does not know
what a match is.
"""
import pytest

from grounds import Grounds


class FakeSocket:
    def __init__(self, name):
        self.name = name
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def test_nobody_connected_means_no_assignment():
    registry = Grounds()
    assert registry.assign("ABCD") is False


def test_an_assignment_goes_to_the_instance_with_room():
    registry = Grounds()
    one = FakeSocket("one")
    registry.joined(one, capacity=2)
    assert registry.assign("ABCD") is True
    assert registry.socket_for("ABCD") is one


def test_capacity_is_a_ceiling():
    registry = Grounds()
    registry.joined(FakeSocket("one"), capacity=1)
    assert registry.assign("ABCD") is True
    assert registry.assign("EFGH") is False


def test_releasing_frees_a_slot():
    registry = Grounds()
    one = FakeSocket("one")
    registry.joined(one, capacity=1)
    registry.assign("ABCD")
    assert registry.release("ABCD") is one
    assert registry.assign("EFGH") is True


def test_the_emptiest_instance_takes_the_next_match():
    registry = Grounds()
    busy, idle = FakeSocket("busy"), FakeSocket("idle")
    registry.joined(busy, capacity=4)
    registry.joined(idle, capacity=4)
    registry.assign("AAAA")
    registry.assign("BBBB")
    # Two matches spread one each, so the third is a tie broken either way;
    # give one instance a third and the fourth must go to the other.
    third = registry.socket_for("AAAA")
    registry.assign("CCCC")
    assert registry.running() == 3
    assert max(registry._spare.values()) - min(registry._spare.values()) <= 1


def test_an_instance_leaving_takes_its_matches_with_it():
    registry = Grounds()
    one = FakeSocket("one")
    registry.joined(one, capacity=2)
    registry.assign("ABCD")
    registry.left(one)
    assert registry.socket_for("ABCD") is None
    assert registry.capacity() == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd arena && uv run pytest tests/test_grounds_socket.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'grounds'`.

- [ ] **Step 3: Write the registry**

```python
# arena/grounds.py
"""Which grounds instances are connected, and what each of them is running.

The arena assigns rather than the grounds claiming, because the grounds have
nothing to claim from: the wall socket carries only rooms that are already
live, and a room cannot go live without a host. An instance watching for work
would be waiting for a room that is waiting for it.

So this is the arena's answer to a question it could not previously ask -- is
there anybody who can run a match right now -- and `POST /start` asks it.

In memory rather than in a column, and single-instance for the same reason the
match bus is: a socket lives in the process that accepted it, and an assignment
is only worth anything to the process that can act on it.
"""


class Grounds:
    def __init__(self):
        # socket -> matches it will take, and socket -> how many it has.
        self._spare = {}
        self._load = {}
        # room code -> the socket running it.
        self._where = {}

    def joined(self, socket, capacity):
        self._spare[socket] = max(0, int(capacity))
        self._load[socket] = 0

    def left(self, socket):
        """Forget an instance and everything it was running.

        Its matches are not reassigned. A live room whose grounds went away
        stops reporting and the sweep abandons it, exactly as it abandons a
        room whose screen closed -- re-hosting would attach a fresh simulation
        to a match already twenty minutes old, with the clock at zero and the
        arena's log saying 2-1.
        """
        self._spare.pop(socket, None)
        self._load.pop(socket, None)
        for code in [code for code, held in self._where.items() if held is socket]:
            self._where.pop(code, None)

    def assign(self, code):
        """Give this match to the emptiest instance with room, or say nobody has."""
        if code in self._where:
            return True
        free = [s for s in self._spare if self._load[s] < self._spare[s]]
        if not free:
            return False
        chosen = min(free, key=lambda s: self._load[s])
        self._where[code] = chosen
        self._load[chosen] += 1
        return True

    def release(self, code):
        socket = self._where.pop(code, None)
        if socket is not None and socket in self._load:
            self._load[socket] = max(0, self._load[socket] - 1)
        return socket

    def socket_for(self, code):
        return self._where.get(code)

    def capacity(self):
        return sum(self._spare.values())

    def running(self):
        return len(self._where)
```

Note the test reaches into `_spare` for the spread assertion. Change that test to compare `registry._load.values()` instead - the balance is in the load, not the capacity. Make that edit before running.

- [ ] **Step 4: Run the registry tests**

Run: `cd arena && uv run pytest tests/test_grounds_socket.py -v`
Expected: PASS, all six.

- [ ] **Step 5: Write the failing test for the socket**

```python
# append to arena/tests/test_grounds_socket.py
def test_the_control_socket_refuses_an_unauthenticated_instance(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/grounds") as socket:
            socket.receive_json()


def test_the_control_socket_takes_a_capacity(client, service_headers):
    with client.websocket_connect("/ws/grounds", headers=service_headers) as socket:
        socket.send_json({"type": "grounds.here", "capacity": 8})
        # The registry is the observable effect; there is no reply to wait on,
        # so ask the app rather than the wire.
        for _ in range(50):
            if client.app.state.grounds.capacity() == 8:
                break
            time.sleep(0.01)
        assert client.app.state.grounds.capacity() == 8
    for _ in range(50):
        if client.app.state.grounds.capacity() == 0:
            break
        time.sleep(0.01)
    assert client.app.state.grounds.capacity() == 0
```

Add `import time` at the top. `service_headers` is the fixture the existing service-token tests use - find it in `conftest.py` or in `test_profile_api.py` and reuse it rather than building headers by hand.

- [ ] **Step 6: Run it to verify it fails**

Run: `cd arena && uv run pytest tests/test_grounds_socket.py -v -k control_socket`
Expected: FAIL - no such route.

- [ ] **Step 7: Mount the socket**

In `arena/app.py`, add `import grounds as grounds_registry` beside the other local imports, set `fastapi_app.state.grounds = grounds_registry.Grounds()` beside `state.held` (`:279`), and add the route beside `/ws/wall`:

```python
@app.websocket("/ws/grounds")
async def grounds_socket(socket: WebSocket):
    """One socket per grounds instance. Assignments down, capacity up.

    Authenticated with the same X-Arena-Service the specialists carry: this is
    a server talking to a server, and what comes down it is a room's physics
    token, which is the one credential no browser may ever be handed.

    No frames go this way. Each match reports on its own /ws/rooms/{code} like
    a tab always did, which is what keeps `_handle_from_host` and `fake_host`
    honest.
    """
    if not _service_token_ok(socket.headers.get("x-arena-service")):
        await socket.close(code=4403, reason="the grounds must authenticate")
        return
    await socket.accept()
    registry = socket.app.state.grounds
    joined = False
    try:
        while True:
            message = await socket.receive_json()
            if not isinstance(message, dict):
                continue
            if message.get("type") == "grounds.here":
                registry.joined(socket, message.get("capacity", 0))
                joined = True
                logger.info("grounds joined, capacity %s", message.get("capacity"))
    except (WebSocketDisconnect, ValueError, KeyError):
        pass
    finally:
        if joined:
            registry.left(socket)
            logger.info("grounds left; %s connected", len(registry._spare))
```

Replace `_service_token_ok` with whatever the function at `app.py:1075` is actually called, and `len(registry._spare)` with a public accessor if you would rather add one.

- [ ] **Step 8: Run the tests**

Run: `cd arena && uv run pytest tests/test_grounds_socket.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add arena/grounds.py arena/app.py arena/tests/test_grounds_socket.py
git commit -m "feat(arena): a channel for saying who can run a match

The arena has never been able to ask whether anybody is available to
simulate, because the answer was always yes by definition: the screen
that opened the room was the host. Physics is moving off the screen, so
the question becomes real and needs somewhere to be asked.

Authenticated as a service rather than as a room, because what travels
down it is a physics token and no browser may ever hold one."
```

---

### Task 5: Assign a grounds at kickoff

**Files:**
- Modify: `arena/app.py:886-908` (`start`), `arena/app.py:64` (`MAX_LIVE_ROOMS`)
- Test: `arena/tests/test_kickoff_assignment.py`

**Interfaces:**
- Consumes: `Grounds.assign`, `Grounds.socket_for` from Task 4.
- Produces: `POST /api/rooms/{code}/start` returns `503` with `"no pitch is free to run this match"` when no grounds has capacity, and the room stays in `lobby`.
- Produces: the assignment message on the control socket - `{"type": "host", "code": ..., "token": ..., "seed": ...}` where `seed` is `f"{code}-{room['id']}"`.

- [ ] **Step 1: Write the failing test**

```python
# arena/tests/test_kickoff_assignment.py
"""Kick-off is where a match acquires somewhere to be played.

Today this cannot fail: the screen that opened the room is the host, so
`start_match`'s "a match needs a host" has never once been a real check. It
becomes one here.
"""
import time

import rooms


def test_kickoff_refuses_with_no_grounds_connected(client, conn):
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    code = opened["code"]
    _take_a_seat(client, code, "blue")

    answer = client.post(f"/api/rooms/{code}/start")

    assert answer.status_code == 503
    assert "pitch" in answer.json()["detail"]
    assert rooms.by_code(conn, code)["status"] == "lobby"


def test_kickoff_sends_the_physics_token_to_the_grounds(client, conn, service_headers):
    with client.websocket_connect("/ws/grounds", headers=service_headers) as farm:
        farm.send_json({"type": "grounds.here", "capacity": 4})
        _wait_for(lambda: client.app.state.grounds.capacity() == 4)

        opened = client.post("/api/rooms", json={"mode": "solo"}).json()
        code = opened["code"]
        _take_a_seat(client, code, "blue")
        assert client.post(f"/api/rooms/{code}/start").status_code == 200

        assignment = farm.receive_json()
        room = rooms.by_code(conn, code)
        assert assignment["type"] == "host"
        assert assignment["code"] == code
        assert assignment["token"] == room["host_client_id"]
        assert assignment["seed"]
        assert room["status"] == "live"


def test_the_seed_is_stable_for_one_room(client, conn, service_headers):
    with client.websocket_connect("/ws/grounds", headers=service_headers) as farm:
        farm.send_json({"type": "grounds.here", "capacity": 4})
        _wait_for(lambda: client.app.state.grounds.capacity() == 4)
        opened = client.post("/api/rooms", json={"mode": "solo"}).json()
        code = opened["code"]
        _take_a_seat(client, code, "blue")
        client.post(f"/api/rooms/{code}/start")
        first = farm.receive_json()["seed"]

    room = rooms.by_code(conn, code)
    assert first == f"{code}-{room['id']}"


def _wait_for(condition, seconds=1.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")
```

Write `_take_a_seat` to match how the existing tests seat a player - copy the helper from `test_kickoff_philosophy.py` rather than inventing one.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd arena && uv run pytest tests/test_kickoff_assignment.py -v`
Expected: FAIL - the first test gets `200` and a live room.

- [ ] **Step 3: Assign inside `/start`**

In `arena/app.py`, at the top of `start` after `_require_seated`:

```python
    # Somewhere to play, before anything is committed. A room that went live
    # with nobody simulating it would sit at 0-0 with a clock that never
    # started, until the sweep abandoned it and told both managers their match
    # stopped reporting -- which is a lie about what went wrong.
    registry = request.app.state.grounds
    if not registry.assign(code):
        raise HTTPException(503, "no pitch is free to run this match; try again in a moment")
```

and after `_announce`, before the wall publish:

```python
    farm = registry.socket_for(code)
    if farm is not None:
        # The physics token leaves the arena exactly here, to exactly one
        # server, over a socket that authenticated as a service.
        await farm.send_json({"type": "host", "code": code,
                              "token": room["host_client_id"],
                              "seed": f"{code}-{room['id']}"})
```

If `rooms.start_match` raises after the assignment, the slot must go back. Wrap the section:

```python
    try:
        with _rules():
            rooms.start_match(connection, room["id"])
    except Exception:
        registry.release(code)
        raise
```

- [ ] **Step 4: Release the slot when the match ends**

`_end_match` (`app.py:1523`) has no handle on the app, so the release happens at its callers, which do. Give the sweep the registry the same way it is already given `held`, and have it return the drops rather than sending them, so it stays synchronous:

```python
def _give_up_on_the_missing(connection, match_bus, now, held=None, farm=None):
    ...
    dropped = []
    ...
        # Inside the loop, beside `_end_match`, for a room being abandoned:
        if farm is not None:
            socket = farm.socket_for(room["code"])
            farm.release(room["code"])
            if socket is not None:
                dropped.append(socket)
    return dropped
```

The async caller that runs the sweep sends them:

```python
    for socket, code in _give_up_on_the_missing(connection, bus, now, held, farm):
        await socket.send_json({"type": "drop", "code": code})
```

so `dropped.append((socket, room["code"]))` rather than the socket alone. A grounds still simulating a room the arena has given up on must be told, or it holds a slot for the rest of the evening.

For a match that ends normally, release at the `_end_match` call sites inside the request handlers, which have `request.app.state.grounds`:

```python
    request.app.state.grounds.release(room["code"])
```

No `drop` is needed there: the page ends the game itself at full time. The release is bookkeeping so the slot is reusable.

- [ ] **Step 5: Capacity comes from the grounds**

Change `MAX_LIVE_ROOMS`'s check at `app.py:640` to consult the registry, keeping the env var as a hard ceiling:

```python
    # The venue's real limit is how many pitches are connected, not a number in
    # a config file. The env var stays as a ceiling nobody should reach.
    if rooms.live_count(connection) >= MAX_LIVE_ROOMS:
        raise HTTPException(503, "the arena is full")
```

Leave this as it is - the grounds' capacity is already enforced by `assign` refusing at kickoff, and two ceilings that can disagree is worse than one that is generous. Add the comment above so the next reader knows the real limit moved.

- [ ] **Step 6: Run the tests**

Run: `cd arena && uv run pytest tests/test_kickoff_assignment.py -v`
Expected: PASS, all three.

- [ ] **Step 7: Run the whole suite**

Run: `cd arena && uv run pytest`
Expected: many failures - every test that kicks a match off now needs a grounds connected. Add a `grounds_connected` fixture to `arena/tests/conftest.py`:

```python
@pytest.fixture
def grounds_connected(client, service_headers):
    """A pitch available to run matches, for every test that kicks one off.

    Kick-off acquires somewhere to play now, so a test that starts a match
    without this gets the honest 503 rather than a live room.
    """
    with client.websocket_connect("/ws/grounds", headers=service_headers) as farm:
        farm.send_json({"type": "grounds.here", "capacity": 64})
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and client.app.state.grounds.capacity() == 0:
            time.sleep(0.01)
        yield farm
```

Add it to every test that calls `/start`. Where a test only cares that the match is live and not about the assignment, requesting the fixture is the whole change.

- [ ] **Step 8: Run the whole suite again**

Run: `cd arena && uv run pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add arena/app.py arena/tests/
git commit -m "feat(arena): a match gets somewhere to be played, or is told no

start_match has said 'a match needs a host' since the beginning and has
never once had to mean it, because the screen that opened the room was
the host by definition. With physics on the farm it means it.

The refusal is the one genuinely new way this can fail, and it fails
honestly: the room stays in its lobby and says so, rather than going
live into a silence the sweep reports thirty seconds later as a host
that stopped talking."
```

---

### Task 6: The host page

**Files:**
- Create: `game/frontend/host.html`, `game/frontend/src/host.js`
- Modify: `game/frontend/vite.config.js` (multi-entry build)
- Modify: `game/frontend/src/arena.js:33-44` (room becomes an argument, not a module-load constant)
- Test: `game/frontend/test/host-page.test.js`

**Interfaces:**
- Consumes: `new SoccerGameScene({ role, seed })` from Task 1.
- Produces: `window.grounds = { host(code, token, seed), drop(code), running() }`. `running()` returns an array of hosted room codes - the supervisor needs it to reconcile, and the test needs it to assert.
- Produces: `connect(code, { clientId, onMessage })` in `arena.js` - the room is now the first argument rather than read from `location.search` at module load.

- [ ] **Step 1: Make the room an argument**

`game/frontend/src/arena.js:33-44` freezes `room` from URL params at module load, which means one page can only ever be about one room. Change it so the module exports a factory:

```js
// A page used to be about exactly one room, decided by its URL before any of
// this ran. The grounds page is about N of them at once and the wall is about
// whichever one it is showing, so the room became something a caller says.
export function roomFrom(search) {
  const params = new URLSearchParams(search);
  return {
    code: (params.get('room') || '').toUpperCase(),
    team: params.get('team') || '',
  };
}
```

and change `connect()` to take the code and token as arguments instead of reading the frozen `room`. Keep `listen()` and `courtRoom`'s socket juggling exactly as they are. Update `main.js`'s call sites to pass `roomFrom(location.search)`.

- [ ] **Step 2: Write the failing test**

```js
// game/frontend/test/host-page.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest';

// A fake socket factory, so the page can be driven with no arena anywhere.
// This is the whole point of the page being a separate unit: it is testable
// by hand from a browser console, and therefore testable here.
const sockets = [];
vi.mock('../src/arena.js', async (importOriginal) => ({
  ...(await importOriginal()),
  connect: (code, options) => {
    const socket = { code, options, sent: [], closed: false,
                     send: (m) => socket.sent.push(m),
                     close: () => { socket.closed = true; } };
    sockets.push(socket);
    return socket;
  },
}));

// Phaser boots a real game per room. Stubbed here: what this test is about is
// one game per hosted room and no more, not what the game does.
const games = [];
vi.mock('phaser', () => ({
  default: {
    HEADLESS: 3,
    Game: class { constructor(config) { this.config = config; this.destroyed = false; games.push(this); }
                  destroy() { this.destroyed = true; } },
    Math: { RandomDataGenerator: class { frac() { return 0.5; } } },
  },
}));

describe('the grounds page', () => {
  beforeEach(async () => {
    sockets.length = 0;
    games.length = 0;
    vi.resetModules();
    await import('../src/host.js');
  });

  it('runs one game per hosted room', () => {
    window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    window.grounds.host('BBBB', 'token-b', 'BBBB-2');
    expect(games).toHaveLength(2);
    expect(window.grounds.running().sort()).toEqual(['AAAA', 'BBBB']);
  });

  it('opens one socket per hosted room, bearing that room\'s token', () => {
    window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    expect(sockets).toHaveLength(1);
    expect(sockets[0].code).toBe('AAAA');
    expect(sockets[0].options.clientId).toBe('token-a');
  });

  it('hosting the same room twice is not two games', () => {
    window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    expect(games).toHaveLength(1);
  });

  it('dropping destroys the game and closes the socket', () => {
    window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    window.grounds.host('BBBB', 'token-b', 'BBBB-2');
    window.grounds.drop('AAAA');
    expect(games[0].destroyed).toBe(true);
    expect(sockets[0].closed).toBe(true);
    expect(games[1].destroyed).toBe(false);
    expect(window.grounds.running()).toEqual(['BBBB']);
  });

  it('dropping a room it never had is not an error', () => {
    expect(() => window.grounds.drop('ZZZZ')).not.toThrow();
  });

  it('passes the seed into the scene', () => {
    window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    const scene = games[0].config.scene[0];
    expect(scene.seed).toBe('AAAA-1');
    expect(scene.role).toBe('host');
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd game/frontend && npx vitest run test/host-page.test.js`
Expected: FAIL - cannot resolve `../src/host.js`.

- [ ] **Step 4: Write the page**

```html
<!-- game/frontend/host.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>grounds</title>
    <!--
      No UI, deliberately. Nobody watches this page: it is loaded by one
      Chromium in one server and driven over CDP. Everything a tab has that a
      simulation does not need is absent rather than hidden -- no profiles
      panel, no speed bar, no start screen, no debug log.
    -->
    <style>body { margin: 0; background: #000; }</style>
  </head>
  <body>
    <script type="module" src="/src/host.js"></script>
  </body>
</html>
```

```js
// game/frontend/src/host.js
/**
 * N matches in one page, none of them drawn.
 *
 * The scene has had two roles since before this existed: a host runs physics
 * and reports, a viewer eases toward frames and simulates nothing. This page
 * is the host role with no tab around it, which is the whole of the change --
 * the simulation is not re-implemented, it is re-hosted.
 *
 * Phaser.HEADLESS still builds every display object the scene creates, because
 * create() builds them before the role is consulted. That is fine and it is
 * why this is a browser rather than jsdom: Chromium has a real DOM and a real
 * canvas, and HEADLESS simply never draws to them. The render pass is the
 * expensive half and it is the half that is gone.
 */
import Phaser from 'phaser';
import { SoccerGameScene } from './game.js';
import { connect } from './arena.js';

const WIDTH = 1408;
const HEIGHT = 768;

const matches = new Map();

function host(code, token, seed) {
  if (matches.has(code)) return;

  const scene = new SoccerGameScene({ role: 'host', seed });
  const feed = connect(code, {
    clientId: token,
    onMessage: (message) => scene.fromArena && scene.fromArena(message),
  });

  const game = new Phaser.Game({
    type: Phaser.HEADLESS,
    width: WIDTH,
    height: HEIGHT,
    physics: { default: 'arcade', arcade: { gravity: { y: 0 }, debug: false } },
    scene: [scene],
  });

  scene.frameSink = feed.state;
  scene.reporter = feed.event;
  matches.set(code, { game, feed });
}

function drop(code) {
  const match = matches.get(code);
  if (!match) return;
  match.feed.close();
  match.game.destroy(true);
  matches.delete(code);
}

function running() {
  return Array.from(matches.keys());
}

window.grounds = { host, drop, running };
```

`scene.frameSink`, `scene.reporter` and the `onMessage` handler must match exactly what `main.js:735-741` wires today. Read that block and copy its shape rather than guessing - if `connect()` returns something differently named, follow it.

- [ ] **Step 5: Add the second entry to the build**

In `game/frontend/vite.config.js`, add to `build`:

```js
  build: {
    assetsDir: 'bundle',
    rollupOptions: {
      input: {
        // The lab, unchanged: this is what stage 2's Chrome window opens.
        main: resolve(__dirname, 'index.html'),
        // The farm's page. Same build as the pitch on purpose -- loaded from
        // the arena at runtime, so version skew between what simulates and
        // what the wall renders is impossible by construction.
        host: resolve(__dirname, 'host.html'),
      },
    },
  },
```

with `import { resolve } from 'node:path';` at the top.

- [ ] **Step 6: Run the tests**

Run: `cd game/frontend && npx vitest run test/host-page.test.js`
Expected: PASS, all six.

- [ ] **Step 7: Build it and check both entries land**

```bash
cd game/frontend && npm run build && ls dist/host.html dist/index.html
```
Expected: both files exist.

- [ ] **Step 8: Run the whole pitch suite**

Run: `cd game/frontend && npm test`
Expected: PASS. `keep-awake.test.js` may need updating if `arena.js`'s exports moved - update the test, do not delete `keepAwake` yet (that happens in Task 12).

- [ ] **Step 9: Commit**

```bash
git add game/frontend/host.html game/frontend/src/host.js game/frontend/src/arena.js game/frontend/vite.config.js game/frontend/test/host-page.test.js
git commit -m "feat(game): a page that plays football and never draws it

The scene has had a host role and a viewer role since before this was
needed. This is the host role with no tab around it: same physics, same
frames, same protocol on the wire, minus the render pass and everything
a person would have looked at.

Built into the pitch rather than beside it, and loaded from the arena at
runtime, so the thing simulating a match and the thing rendering it can
never be two different versions of the same code."
```

---

### Task 7: The grounds supervisor

**Files:**
- Create: `grounds/pyproject.toml`, `grounds/supervisor.py`, `grounds/main.py`, `grounds/run.sh`, `grounds/README.md`, `grounds/.env.example`
- Create: `grounds/tests/__init__.py`, `grounds/tests/conftest.py`, `grounds/tests/test_supervisor.py`

**Interfaces:**
- Consumes: `/ws/grounds` from Task 4, `window.grounds` from Task 6.
- Produces: `Supervisor(page, capacity)` in `grounds/supervisor.py`:
  - `async Supervisor.apply(message) -> None` - takes one control message and drives the page.
  - `Supervisor.hello() -> dict` - the `grounds.here` payload.
  - `Supervisor.running -> set[str]`
  - `page` is any object with `async evaluate(expression, arg)`, so the tests pass a fake and production passes a Playwright `Page`.
- Produces: `grounds/main.py` with `run()`, an aiohttp-free FastAPI app on `:8004` serving `GET /healthz` only, plus the connect-and-reconnect loop.

- [ ] **Step 1: Write the failing test**

```python
# grounds/tests/test_supervisor.py
"""The supervisor drives a page and never touches a match.

Everything about physics is in the page. This decides which rooms this
instance runs, which is a bookkeeping job, and it is tested against a fake
page with no Chromium anywhere.
"""
import pytest

from supervisor import Supervisor


class FakePage:
    def __init__(self):
        self.calls = []

    async def evaluate(self, expression, arg=None):
        self.calls.append((expression, arg))
        return None


@pytest.fixture
def page():
    return FakePage()


def test_it_announces_its_capacity(page):
    supervisor = Supervisor(page, capacity=12)
    assert supervisor.hello() == {"type": "grounds.here", "capacity": 12}


async def test_a_host_message_starts_a_match(page):
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply({"type": "host", "code": "AAAA", "token": "t", "seed": "AAAA-1"})
    assert supervisor.running == {"AAAA"}
    expression, arg = page.calls[-1]
    assert "window.grounds.host" in expression
    assert arg == {"code": "AAAA", "token": "t", "seed": "AAAA-1"}


async def test_a_drop_message_stops_one(page):
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply({"type": "host", "code": "AAAA", "token": "t", "seed": "AAAA-1"})
    await supervisor.apply({"type": "drop", "code": "AAAA"})
    assert supervisor.running == set()
    expression, arg = page.calls[-1]
    assert "window.grounds.drop" in expression
    assert arg == {"code": "AAAA"}


async def test_it_refuses_past_its_capacity(page):
    supervisor = Supervisor(page, capacity=1)
    await supervisor.apply({"type": "host", "code": "AAAA", "token": "t", "seed": "AAAA-1"})
    await supervisor.apply({"type": "host", "code": "BBBB", "token": "t", "seed": "BBBB-2"})
    assert supervisor.running == {"AAAA"}


async def test_a_message_it_does_not_know_is_ignored(page):
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply({"type": "sing", "code": "AAAA"})
    assert supervisor.running == set()
    assert page.calls == []


async def test_a_token_never_appears_in_the_expression(page):
    """The page is driven with an argument, not with an interpolated string.

    A token spliced into JavaScript is a token in a stack trace, in a CDP log,
    and in anything that ever records what was evaluated.
    """
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply({"type": "host", "code": "AAAA", "token": "secret", "seed": "s"})
    expression, _ = page.calls[-1]
    assert "secret" not in expression


async def test_it_forgets_everything_when_the_page_is_reloaded(page):
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply({"type": "host", "code": "AAAA", "token": "t", "seed": "AAAA-1"})
    supervisor.page_reloaded()
    assert supervisor.running == set()
```

- [ ] **Step 2: Scaffold the service**

```toml
# grounds/pyproject.toml
[project]
name = "futsal-worldcup-grounds"
version = "0.1.0"
description = "One Chromium holding every match in the venue."
requires-python = ">=3.14"
dependencies = [
    "fastapi==0.136.3",
    "uvicorn==0.48.0",
    "websockets==17.0.1",
    "playwright==1.56.0",
    "python-dotenv==1.2.2",
]

[tool.uv]
package = false

[dependency-groups]
dev = [
    "pytest==8.4.2",
    "pytest-asyncio==1.2.0",
    "pytest-timeout==2.4.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
timeout = 30
```

```python
# grounds/tests/conftest.py
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
```

Create `grounds/tests/__init__.py` empty.

- [ ] **Step 3: Run it to verify it fails**

Run: `cd grounds && uv run pytest -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'supervisor'`.

- [ ] **Step 4: Write the supervisor**

```python
# grounds/supervisor.py
"""Which matches this instance is running, and how the page is told.

The division is strict and it is what makes both halves testable: the page
knows about football and nothing about the arena, this knows about the arena
and nothing about football. It never parses a frame -- frames go from the page
straight down each match's own room socket, exactly as they did from a tab.
"""
import logging

logger = logging.getLogger(__name__)

# Driven with an argument rather than an interpolated string, because a physics
# token spliced into JavaScript ends up in a stack trace, a CDP log, and
# anything else that records what was evaluated.
HOST = "(a) => window.grounds.host(a.code, a.token, a.seed)"
DROP = "(a) => window.grounds.drop(a.code)"


class Supervisor:
    def __init__(self, page, capacity):
        self.page = page
        self.capacity = capacity
        self.running = set()

    def hello(self):
        return {"type": "grounds.here", "capacity": self.capacity}

    def page_reloaded(self):
        """The page lost every game it had, so this instance runs nothing.

        Not re-hosted. A match already twenty minutes old cannot be resumed by
        a fresh simulation with the clock at zero, and the arena's sweep is
        already the right answer to a match nobody is playing.
        """
        self.running.clear()

    async def apply(self, message):
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        code = message.get("code")
        if not code:
            return
        if kind == "host":
            if code in self.running:
                return
            if len(self.running) >= self.capacity:
                logger.warning("refused %s: at capacity %s", code, self.capacity)
                return
            await self.page.evaluate(HOST, {"code": code,
                                            "token": message.get("token"),
                                            "seed": message.get("seed")})
            self.running.add(code)
            logger.info("running %s (%s of %s)", code, len(self.running), self.capacity)
        elif kind == "drop":
            if code not in self.running:
                return
            await self.page.evaluate(DROP, {"code": code})
            self.running.discard(code)
            logger.info("dropped %s (%s of %s)", code, len(self.running), self.capacity)
```

- [ ] **Step 5: Run the tests**

Run: `cd grounds && uv run pytest -v`
Expected: PASS, all seven.

- [ ] **Step 6: Write the entry point**

```python
# grounds/main.py
"""One Chromium, one page, and a socket to the arena.

This process serves nothing anybody uses. The port exists because Cloud Run
insists on a health check, and cpu-throttling is off because between health
checks a throttled instance would simply stop playing football.
"""
import asyncio
import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from playwright.async_api import async_playwright

from supervisor import Supervisor

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grounds")

ARENA_URL = os.environ.get("ARENA_URL", "http://localhost:8003").rstrip("/")
SERVICE_TOKEN = os.environ.get("ARENA_SERVICE_TOKEN", "")
CAPACITY = int(os.environ.get("GROUNDS_CAPACITY", "12"))
PORT = int(os.environ.get("PORT", "8004"))
FIRST_WAIT = 0.5
LONGEST_WAIT = 8.0

app = FastAPI()
state = {"page": None, "running": 0}


@app.get("/healthz")
def healthz():
    """Up, and how much football is happening. The only request this serves."""
    return {"ok": state["page"] is not None, "running": state["running"]}


async def pitches():
    """Hold the browser open and keep the control socket connected.

    Reconnecting rather than exiting, for the same reason socket.js does: the
    arena redeploys, the network blinks, and a farm that gave up would take the
    venue with it. Every match dies on the way through -- the page goes with
    the browser and the arena's sweep abandons what it was running -- which is
    the durability tier this was designed for and not a bug to fix here.
    """
    import websockets

    address = ARENA_URL.replace("https://", "wss://").replace("http://", "ws://")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=["--disable-dev-shm-usage", "--no-sandbox",
                  "--disable-gpu", "--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page()
        page.on("console", lambda m: logger.info("page: %s", m.text))
        page.on("pageerror", lambda e: logger.error("page: %s", e))
        await page.goto(f"{ARENA_URL}/pitch/host.html", wait_until="load")
        await page.wait_for_function("() => !!window.grounds")
        state["page"] = page
        supervisor = Supervisor(page, CAPACITY)
        logger.info("pitch open at %s/pitch/host.html, capacity %s", ARENA_URL, CAPACITY)

        wait = FIRST_WAIT
        while True:
            try:
                async with websockets.connect(
                        f"{address}/ws/grounds",
                        additional_headers={"X-Arena-Service": SERVICE_TOKEN}) as socket:
                    wait = FIRST_WAIT
                    await socket.send(_json(supervisor.hello()))
                    logger.info("connected to the arena")
                    async for raw in socket:
                        await supervisor.apply(_loads(raw))
                        state["running"] = len(supervisor.running)
            except Exception as problem:
                logger.warning("arena socket dropped (%s); retrying in %ss", problem, wait)
                await asyncio.sleep(wait)
                wait = min(wait * 2, LONGEST_WAIT)


def _json(value):
    import json
    return json.dumps(value)


def _loads(raw):
    import json
    try:
        return json.loads(raw)
    except ValueError:
        return {}


@app.on_event("startup")
async def start():
    asyncio.create_task(pitches())


def run():
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()
```

Move the `import json`, `import websockets` to the top of the file rather than inside functions - they are inline above only to keep the diff readable, and inline imports in a hot loop are not the standard this repo holds.

- [ ] **Step 7: Write `run.sh` and the docs**

```bash
# grounds/run.sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --quiet
uv run playwright install --with-deps chromium
exec uv run python main.py
```

`chmod +x grounds/run.sh`. Write `grounds/README.md` covering: what it is, why it exists, the two units, how to run it against a local arena, and that a restart ends every live match by design. Write `grounds/.env.example` with `ARENA_URL`, `ARENA_SERVICE_TOKEN` and `GROUNDS_CAPACITY`.

- [ ] **Step 8: Run it against a local arena and watch a real match**

```bash
# three shells
cd arena && ARENA_SERVICE_TOKEN=dev-token ./run.sh
cd game  && ./run.sh
cd grounds && ARENA_URL=http://localhost:8003 ARENA_SERVICE_TOKEN=dev-token ./run.sh
```

Then open `http://localhost:8003/arena`, take a seat from a phone or a second tab, and kick off. Expected: the grounds log says `running <CODE> (1 of 12)`, and the wall shows the ball moving. Close the arena tab entirely and reopen it - the match must still be running with the same clock.

- [ ] **Step 9: Commit**

```bash
git add grounds/
git commit -m "feat(grounds): somewhere for matches to be played that is not a tab

One Chromium, one page, N headless games. It is a client of the arena
rather than a server: the only thing the port is for is the health check
Cloud Run insists on.

It never touches physics and never parses a frame. Frames go from the
page down each match's own room socket exactly as they went from a tab,
which is what keeps the arena's tests browser-free and fake_host honest."
```

---

### Task 8: Ship the grounds

**Files:**
- Create: `deploy/grounds.Dockerfile`
- Modify: `deploy/cloudbuild.yaml`, `deploy/service.yaml`, `deploy/deploy.sh`, `deploy/README.md`
- Test: `arena/tests/test_service_yaml.py` (extend)

**Interfaces:**
- Consumes: `grounds/` from Task 7.
- Produces: a second Cloud Run service, `grounds`, with `minScale`/`maxScale` `1`, `cpu-throttling: false`, gen2, reaching the arena over `ARENA_URL`.

- [ ] **Step 1: Write the failing test**

```python
# append to arena/tests/test_service_yaml.py
def test_the_grounds_run_exactly_one_instance(grounds_yaml):
    """Two would double-run any room the arena assigned once.

    The arena assigns a match to a socket. Two instances behind one revision
    means two sockets, two simulations of the same room, and two streams of
    frames racing each other into one match's log.
    """
    annotations = grounds_yaml["spec"]["template"]["metadata"]["annotations"]
    assert annotations["autoscaling.knative.dev/minScale"] == "1"
    assert annotations["autoscaling.knative.dev/maxScale"] == "1"


def test_the_grounds_are_not_throttled(grounds_yaml):
    """Between health checks a throttled instance stops playing football."""
    annotations = grounds_yaml["spec"]["template"]["metadata"]["annotations"]
    assert annotations["run.googleapis.com/cpu-throttling"] == "false"


def test_the_grounds_run_gen2(grounds_yaml):
    annotations = grounds_yaml["spec"]["template"]["metadata"]["annotations"]
    assert annotations["run.googleapis.com/execution-environment"] == "gen2"
```

Add a `grounds_yaml` fixture beside the existing `service_yaml` one, reading `deploy/grounds.yaml`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd arena && uv run pytest tests/test_service_yaml.py -v`
Expected: FAIL - no `deploy/grounds.yaml`.

- [ ] **Step 3: Write the image**

```dockerfile
# deploy/grounds.Dockerfile
# Playwright's own image rather than a base plus apt: Chromium's shared library
# set is long, version-matched to the browser build, and getting it wrong shows
# up as a launch that hangs rather than a build that fails.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

COPY grounds/pyproject.toml grounds/uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

COPY grounds/ ./

ENV PORT=8004
EXPOSE 8004
CMD ["uv", "run", "python", "main.py"]
```

Match the uv version and the Playwright version to whatever the repo and `grounds/pyproject.toml` actually pin. If `deploy/` already has a Dockerfile pattern for the other three images, follow it instead of this one.

- [ ] **Step 4: Write the service**

```yaml
# deploy/grounds.yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: grounds
  annotations:
    run.googleapis.com/launch-stage: BETA
spec:
  template:
    metadata:
      annotations:
        # One process holds the venue's matches. Two would double-run any room
        # the arena assigned once, and the arena assigns to a socket.
        autoscaling.knative.dev/minScale: "1"
        autoscaling.knative.dev/maxScale: "1"
        # Load-bearing here in a way it is not for the arena: the only requests
        # this serves are health checks, so a throttled instance would stop
        # simulating between them.
        run.googleapis.com/cpu-throttling: "false"
        # Chromium.
        run.googleapis.com/execution-environment: gen2
        run.googleapis.com/startup-cpu-boost: "true"
    spec:
      containerConcurrency: 1
      timeoutSeconds: 3600
      containers:
        - name: grounds
          image: IMAGE_GROUNDS
          ports:
            - name: http1
              containerPort: 8004
          env:
            - name: ARENA_URL
              value: ARENA_URL_VALUE
            - name: GROUNDS_CAPACITY
              value: "12"
            - name: ARENA_SERVICE_TOKEN
              valueFrom:
                secretKeyRef:
                  name: arena-service-token
                  key: latest
          resources:
            limits:
              cpu: "4"
              memory: 8Gi
          startupProbe:
            httpGet:
              path: /healthz
              port: 8004
            initialDelaySeconds: 10
            periodSeconds: 5
            failureThreshold: 30
```

`cpu` and `memory` are provisional and Task 13 replaces them with measured numbers. `GROUNDS_CAPACITY` likewise. Read `deploy/service.yaml` and copy its exact secret-reference shape, service-account and image-substitution convention - the placeholders above must match whatever `deploy.sh` already does with `IMAGE_*`.

- [ ] **Step 5: Build and deploy it from the pipeline**

Add a fourth image build to `deploy/cloudbuild.yaml` following the three that are there, and extend `deploy/deploy.sh` to substitute `IMAGE_GROUNDS` and `ARENA_URL_VALUE` and `gcloud run services replace deploy/grounds.yaml`. `ARENA_URL_VALUE` is the arena's deployed URL, which `deploy.sh` can read with `gcloud run services describe arena --format='value(status.url)'` after the arena is replaced - so the grounds must be deployed second.

- [ ] **Step 6: Run the tests**

Run: `cd arena && uv run pytest tests/test_service_yaml.py -v`
Expected: PASS.

- [ ] **Step 7: Build the image locally to prove the Dockerfile**

```bash
cd /Users/chuan/mywork/ai/agent-football && docker build -f deploy/grounds.Dockerfile -t grounds-test . && docker run --rm -e ARENA_URL=http://host.docker.internal:8003 -e ARENA_SERVICE_TOKEN=dev-token -p 8004:8004 grounds-test
```
Expected: the container comes up, `curl localhost:8004/healthz` returns `{"ok": true, ...}`. If Chromium fails to launch, that is a missing flag or a `/dev/shm` size and it must be fixed here rather than discovered in Cloud Run.

- [ ] **Step 8: Commit**

```bash
git add deploy/
git commit -m "feat(deploy): a service whose only request is a health check

One instance, unthrottled, gen2. The instance count is not a scaling
choice: the arena hands a match to a socket, so a second instance behind
one revision means two simulations of one room racing into one log.

Throttling matters more here than it does for the arena. The arena is
idle between requests by definition; this one is playing football
between them, and a CPU taken away mid-match is a match that stops."
```

---

### Task 9: The wall mounts the pitch directly

**Files:**
- Create: `game/frontend/src/viewer.js`
- Modify: `game/frontend/vite.config.js` (third entry, stable filename), `arena/app.py:1822-1823` (serve it revalidated)
- Modify: `arena/static/arena.html` (lose the iframe), `arena/static/arena.js` (`watch()` and `cutTo()`), `arena/static/app.css`
- Modify: `game/frontend/src/game.js` (add `point(code)`)
- Test: `game/frontend/test/cutting-between-matches.test.js`, `arena/tests/test_pitch_mount.py` (extend)

**Interfaces:**
- Consumes: `roomFrom` / `connect` from Task 6.
- Produces: `/pitch/viewer.js`, a stable un-hashed entry exporting `mount(element, { pitchUrl })` which returns `{ point(code), destroy() }`.
- Produces: `scene.point(code)` on `SoccerGameScene` - clears `wire`, snaps the next frame instead of easing it, resets score, clock and nameplates.

- [ ] **Step 1: Write the failing test for the cut**

```js
// game/frontend/test/cutting-between-matches.test.js
import { describe, it, expect } from 'vitest';
import { SoccerGameScene } from '../src/game.js';

/**
 * The single most visible way this can look wrong is the previous match's
 * players sliding across the pitch into the new one. A viewer eases toward
 * every frame it is given, which is right within a match and catastrophic
 * across a cut.
 */
describe('pointing a viewer at a different match', () => {
  it('drops the last match\'s pending frame', () => {
    const scene = new SoccerGameScene({ role: 'viewer' });
    scene.wire = { players: [{ x: 0.9, y: 0.9 }], ball: { x: 0.9, y: 0.9 } };
    scene.point('BBBB');
    expect(scene.wire).toBeNull();
  });

  it('snaps rather than eases the first frame of the new match', () => {
    const scene = new SoccerGameScene({ role: 'viewer' });
    scene.point('BBBB');
    expect(scene.snapNext).toBe(true);
  });

  it('resets the score and the clock', () => {
    const scene = new SoccerGameScene({ role: 'viewer' });
    scene.score = { blue: 3, red: 1 };
    scene.matchMs = 600000;
    scene.point('BBBB');
    expect(scene.score).toEqual({ blue: 0, red: 0 });
    expect(scene.matchMs).toBe(0);
  });

  it('remembers which match it is now about', () => {
    const scene = new SoccerGameScene({ role: 'viewer' });
    scene.point('BBBB');
    expect(scene.code).toBe('BBBB');
  });
});
```

Read `game.js` first and use its real field names for the score, the clock and the pending frame. `wire`, `score`, `matchMs` and `snapNext` above are the shapes to look for; if the file calls them something else, follow the file and update these assertions.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd game/frontend && npx vitest run test/cutting-between-matches.test.js`
Expected: FAIL - `scene.point is not a function`.

- [ ] **Step 3: Write `point()`**

In `game/frontend/src/game.js`, beside `applyFrame`:

```js
  point(code) {
    // A viewer eases toward every frame, which is right inside a match and
    // wrong across a cut: without this the last match's players slide across
    // the pitch into the new one, in front of the room.
    this.code = code;
    this.wire = null;
    this.snapNext = true;
    this.score = { blue: 0, red: 0 };
    this.matchMs = 0;
    this.paintScore && this.paintScore();
    this.paintClock && this.paintClock();
    this.paintManagers && this.paintManagers({ blue: null, red: null });
  }
```

and in `applyFrame`, honour `snapNext`:

```js
    if (this.snapNext) {
      this.snapNext = false;
      return this.snapTo(frame);
    }
```

where `snapTo` sets each sprite's position from the frame with no interpolation. If `applyFrame` already has a snap path for its first frame, reuse it rather than adding a second.

- [ ] **Step 4: Run the test**

Run: `cd game/frontend && npx vitest run test/cutting-between-matches.test.js`
Expected: PASS.

- [ ] **Step 5: Write the stable viewer entry**

```js
// game/frontend/src/viewer.js
/**
 * One canvas for the whole wall, and a way to point it at a match.
 *
 * The wall used to frame the pitch page in an iframe, which meant a cut cost
 * a page load, a Phaser boot and a texture decode -- fine at six matches a
 * carousel, absurd at fifty a click. This is the same scene mounted directly,
 * booted once when the wall opens and never again.
 *
 * A stable filename on purpose: /pitch/bundle/* is content-hashed so a year of
 * caching is safe, which means the wall cannot name a file in it. This one is
 * served revalidated instead, and it is the only thing the wall imports.
 */
import Phaser from 'phaser';
import { SoccerGameScene } from './game.js';

const WIDTH = 1408;
const HEIGHT = 768;

export function mount(element) {
  const scene = new SoccerGameScene({ role: 'viewer' });
  const game = new Phaser.Game({
    type: Phaser.AUTO,
    parent: element,
    width: WIDTH,
    height: HEIGHT,
    // The iframe was scaled by CSS and a direct mount is not, so the scale
    // mode is what makes 1408x768 fill whatever the wall gives it.
    scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
    physics: { default: 'arcade', arcade: { gravity: { y: 0 }, debug: false } },
    scene: [scene],
  });

  return {
    point: (code) => scene.point(code),
    frame: (message) => scene.fromArena && scene.fromArena(message),
    destroy: () => game.destroy(true),
  };
}
```

- [ ] **Step 6: Give it a stable name in the build**

In `vite.config.js`, add `viewer: resolve(__dirname, 'src/viewer.js')` to `rollupOptions.input` and pin its filename:

```js
      output: {
        // Everything else is content-hashed and cached for a year. This one
        // file is named, because the wall has to be able to import it without
        // reading the manifest.
        entryFileNames: (chunk) => (chunk.name === 'viewer'
          ? 'viewer.js'
          : 'bundle/[name]-[hash].js'),
      },
```

- [ ] **Step 7: Prove the arena serves it revalidated**

```python
# append to arena/tests/test_pitch_mount.py
def test_the_viewer_entry_is_revalidated_not_immutable(client):
    """The wall imports this by name, so it must never be frozen for a year."""
    answer = client.get("/pitch/viewer.js")
    assert answer.status_code == 200
    assert "immutable" not in answer.headers.get("cache-control", "")
```

`/pitch/viewer.js` lands under `PITCH_DIR` rather than `PITCH_DIR/bundle`, so the existing `Revalidated` mount at `app.py:1823` already serves it. Run the test to confirm rather than changing the mount.

- [ ] **Step 8: Replace the iframe**

In `arena/static/arena.html`, replace the `#pitch` iframe with `<div id="pitch" class="pitch"></div>`. In `arena/static/arena.js`, replace `watch()`'s `iframe.src` assignment with a one-time mount and a cut:

```js
// Booted once, when the wall opens, and pointed at matches from then on.
// The address comes off venue.pitch_url, which the wall already fetches, so
// same-origin in production and :5173 in development both work with nothing
// new to configure.
let court = null;

async function bootCourt(pitchUrl) {
  const { mount } = await import(`${pitchUrl.replace(/\/$/, "")}/viewer.js`);
  court = mount(document.getElementById("pitch"));
}

function cutTo(code) {
  if (!court || showing === code) return;
  showing = code;
  court.point(code);
  listen(code);
}
```

`listen(code)` is the existing room-socket juggling - it stays and now feeds `court.frame(message)` instead of the iframe. Adjust `app.css`'s `.pitch` rule so the div fills `.court` the way the iframe did.

- [ ] **Step 9: Check it on a real screen**

With arena, game and grounds running, open `http://localhost:8003/arena`, kick off two matches from two phones, and watch the carousel cut between them. Expected: no flicker, no reload, no sprite sliding in from the previous match, the score and clock correct on the first frame. Check the audio still plays now that it is not in a frame.

- [ ] **Step 10: Commit**

```bash
git add game/frontend/src/viewer.js game/frontend/src/game.js game/frontend/vite.config.js arena/static/ arena/tests/test_pitch_mount.py game/frontend/test/cutting-between-matches.test.js
git commit -m "feat(arena): one canvas the wall cuts between

An iframe per match meant a cut cost a page load, a Phaser boot and a
texture decode. That was affordable at six matches on a twelve-second
carousel and is not at fifty on a click.

point() is the whole of the new behaviour and it exists for one reason:
a viewer eases toward every frame it is given, so without an explicit
reset the last match's players slide across the pitch into the next one
in front of the room."
```

---

### Task 10: Fifty matches, browsable

**Files:**
- Modify: `arena/static/arena.js` (`TILES`, `strip()`, `pin()`, `choose()`, page rotation; delete `hostingLive()` and the host branches), `arena/static/arena.html`, `arena/static/app.css`
- Test: `arena/tests/test_wall_socket.py` (extend), and a Playwright E2E

**Interfaces:**
- Consumes: `cutTo` / `court` from Task 9.
- Produces: pagination controls in the wall's DOM - `#pages` with a button per page, `[data-page]`, and previous/next.

- [ ] **Step 1: Delete the host code from the wall**

Remove `hostingLive()`, the `choose()` branch at `arena.js:508` that pinned our own match to centre court, the `src` comparison guard at `:596`, the refusal inside `pin()` at `:640-648`, and `stillHere()` with `STILL_HERE_MS` and its timer. Keep `screenToken()` from Task 2 - the wall still bears it on the room socket so `_HeldRooms` counts its lobby.

Run `cd arena && uv run pytest tests/test_mode_switch.py -v` after, since that is the one test that exercises the token the wall keeps.

- [ ] **Step 2: Raise the grid and make pages navigable**

```js
// Six was sized to be read from the back of a room. A grid somebody walks up
// to and clicks is a different budget: twelve fits a 1080p wall at a size a
// thumb can hit, and fifty matches is five pages rather than nine.
const TILES = 12;

// The carousel is for when nobody is there. The moment somebody pages or pins,
// it stops, and it starts again when they stop touching it.
const BROWSING_MS = 30000;
let browsing = null;

function browsingNow() {
  clearTimeout(browsing);
  browsing = setTimeout(() => { browsing = null; }, BROWSING_MS);
}

function rotate() {
  if (browsing || pinned) return;
  page = (page + 1) % pageCount();
  strip();
}

function goToPage(next) {
  page = Math.max(0, Math.min(next, pageCount() - 1));
  browsingNow();
  strip();
}
```

Wire previous/next buttons and one button per page into `#pages`, rebuilt by `strip()`. Give each `data-page` so the E2E can find it. `pin()` calls `browsingNow()` too, and Escape clears `pinned` and lets the carousel resume.

- [ ] **Step 3: Write the E2E**

```python
# arena/tests/test_wall_at_fifty.py
"""Fifty matches on the wall, and every one of them reachable.

Driven with fake_host rather than a browser farm: what is being tested is the
wall's own paging and pinning, and fifty real simulations would be testing
Chromium.
"""
import pytest

pytestmark = pytest.mark.e2e


async def test_every_match_is_reachable_by_paging(wall_page, fifty_live_rooms):
    codes = set(fifty_live_rooms)
    seen = set()
    pages = await wall_page.locator("#pages [data-page]").count()
    for index in range(pages):
        await wall_page.locator(f"#pages [data-page='{index}']").click()
        for tile in await wall_page.locator(".tile[data-code]").all():
            seen.add(await tile.get_attribute("data-code"))
    assert codes <= seen


async def test_clicking_a_tile_pins_it(wall_page, fifty_live_rooms):
    tile = wall_page.locator(".tile[data-code]").first
    code = await tile.get_attribute("data-code")
    await tile.click()
    await wall_page.wait_for_function(
        "(code) => document.querySelector('.court').dataset.showing === code", arg=code)
    # And the carousel has stopped: wait past a full rotation and it is still
    # the match the operator asked for.
    await wall_page.wait_for_timeout(14000)
    assert await wall_page.locator(".court").get_attribute("data-showing") == code


async def test_escape_hands_it_back_to_the_director(wall_page, fifty_live_rooms):
    tile = wall_page.locator(".tile[data-code]").first
    code = await tile.get_attribute("data-code")
    await tile.click()
    await wall_page.keyboard.press("Escape")
    await wall_page.wait_for_function(
        "(code) => document.querySelector('.court').dataset.showing !== code",
        arg=code, timeout=30000)
```

`wall_page` opens `/arena` against a live test arena; `fifty_live_rooms` opens fifty rooms, seats them, kicks them off with a grounds fixture connected, and drives each with `fake_host`. Write both fixtures in `arena/tests/conftest.py`. Add `.court`'s `data-showing` attribute and the tiles' `data-code` in `arena.js` - the wall needs them to be testable and they cost nothing.

Register the `e2e` marker in `arena/pyproject.toml` and skip it by default so the ordinary suite stays fast:

```toml
markers = ["e2e: needs a browser and a running arena"]
addopts = "-m 'not e2e'"
```

- [ ] **Step 4: Run it**

Run: `cd arena && uv run pytest tests/test_wall_at_fifty.py -v -m e2e`
Expected: PASS, all three.

- [ ] **Step 5: Look at it**

Open the wall with fifty rooms running and judge it as an operator would: are twelve tiles readable, is the page control obviously clickable, does the pinned tile look pinned, does Escape visibly hand back. Fix what looks wrong.

- [ ] **Step 6: Commit**

```bash
git add arena/static/ arena/tests/
git commit -m "feat(arena): fifty matches you can walk up to and browse

Three things broke at fifty and two of them were the same rule: a screen
could not click a match because it was busy hosting one. It is not
hosting anything now, so the refusal had nothing left to protect.

The third is new work. Pages rotated on a twelve-second carousel with no
way to navigate, so at nine pages the match you wanted was up to a
hundred seconds away and you could not go and get it. The carousel is
still right when nobody is there, and it gets out of the way when
somebody is."
```

---

### Task 11: The substitution toast routes through the arena

**Files:**
- Modify: `game/agents/football_mcp_server.py:61-72`
- Modify: `arena/app.py` (a new endpoint and event kind), `arena/rooms.py` (event kind if it is enumerated)
- Modify: `game/frontend/src/main.js:847,910` (delete `SUBSTITUTIONS_URL`, `createSubstitutionPoll` and the poll)
- Modify: `arena/static/arena.js`, `arena/static/play.js` (show it)
- Modify: `deploy/service.yaml` (drop the `player-state` volume at `:98,118,172`)
- Test: `arena/tests/test_substitutions.py`, `game/frontend/test/substitutions.test.js` (rewrite)

**Interfaces:**
- Produces: `POST /api/rooms/{code}/substitution` with `X-Arena-Service`, body `{"team": "blue"|"red", "role": str, "kind": "injury"|"substitution", "detail": str}`. Appends a room event of kind `substitution` and publishes it on the room topic.

- [ ] **Step 1: Write the failing test**

```python
# arena/tests/test_substitutions.py
"""The last thing that bypassed the arena.

An injury was written into a JSON file beside the pitch and polled by whichever
browser happened to be hosting. That worked exactly as long as a browser was
hosting, and it stopped being true when physics moved to the farm -- so rather
than teach the farm to write files nobody would ever read, it goes where every
other thing that happens in a match already goes.
"""


def test_a_substitution_is_refused_without_the_service_token(client, live_room):
    answer = client.post(f"/api/rooms/{live_room}/substitution",
                         json={"team": "blue", "role": "striker",
                               "kind": "injury", "detail": "hamstring"})
    assert answer.status_code == 403


def test_a_substitution_lands_in_the_log(client, live_room, service_headers, conn):
    answer = client.post(f"/api/rooms/{live_room}/substitution",
                         json={"team": "blue", "role": "striker",
                               "kind": "injury", "detail": "hamstring"},
                         headers=service_headers)
    assert answer.status_code == 200
    events = rooms.events_since(conn, rooms.by_code(conn, live_room)["id"], 0)
    said = [e for e in events if e["kind"] == "substitution"]
    assert said and said[-1]["payload"]["role"] == "striker"


def test_a_substitution_reaches_the_room_socket(client, live_room, service_headers):
    with client.websocket_connect(f"/ws/rooms/{live_room}") as socket:
        socket.receive_json()  # the opening snapshot
        client.post(f"/api/rooms/{live_room}/substitution",
                    json={"team": "red", "role": "keeper",
                          "kind": "substitution", "detail": "coming off"},
                    headers=service_headers)
        message = socket.receive_json()
        assert message["kind"] == "substitution"
        assert message["payload"]["team"] == "red"


def test_it_survives_a_cut_between_matches(client, live_room, service_headers, conn):
    """In the log rather than in a file somebody is polling, which is the point.

    A wall that cuts to this match a minute later still catches up on it,
    because catching up is what the log is for.
    """
    client.post(f"/api/rooms/{live_room}/substitution",
                json={"team": "blue", "role": "striker",
                      "kind": "injury", "detail": "hamstring"},
                headers=service_headers)
    events = rooms.events_since(conn, rooms.by_code(conn, live_room)["id"], 0)
    assert any(e["kind"] == "substitution" for e in events)
```

Use the repo's real `events_since` name and the real `live_room` fixture shape; copy from `test_shout.py`, which is the closest existing thing.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd arena && uv run pytest tests/test_substitutions.py -v`
Expected: FAIL - 404, no such endpoint.

- [ ] **Step 3: Add the endpoint**

Model it on the shout endpoint in `app.py`: service-token guard, `rooms.append_event(connection, room["id"], "substitution", said)`, then `bus.publish(room_topic(code), {...})`. Validate `team` against `rooms.TEAMS` and `kind` against `("injury", "substitution")`, refusing anything else in words.

- [ ] **Step 4: Post it from the MCP server**

Replace the file write in `game/agents/football_mcp_server.py:61-72` with an `httpx.post` to `{ARENA_URL}/api/rooms/{room}/substitution` carrying `X-Arena-Service`, mirroring how the specialists already PATCH profiles in the same repo. Keep the tool's own signature and its answer to the agent unchanged.

- [ ] **Step 5: Delete the poll**

Remove `SUBSTITUTIONS_URL` (`main.js:847`), `createSubstitutionPoll`, and the `if (!isViewer())` poll at `main.js:910`. Rewrite `game/frontend/test/substitutions.test.js` to test the toast rendering from a room event instead of from a polled file, deleting the assertions about fetching.

- [ ] **Step 6: Show it**

In `arena/static/arena.js` and `arena/static/play.js`, handle the `substitution` event kind alongside the kinds each already renders, as a toast naming the team, the role and the detail. Follow whatever toast pattern each file already has.

- [ ] **Step 7: Drop the shared volume**

Remove the `player-state` volume and both its mounts from `deploy/service.yaml:98,118,172`, and the `/player_state` mount from `app.py:1830-1833` if nothing else uses it. Check with `grep -rn player_state --include='*.py' --include='*.js' --include='*.yaml'` before deleting - if the kit images use it, only the substitutions subdirectory goes.

- [ ] **Step 8: Run both suites**

Run: `cd arena && uv run pytest` and `cd game/frontend && npm test`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add game/agents/football_mcp_server.py arena/ game/frontend/ deploy/service.yaml
git commit -m "feat(arena): an injury goes where everything else in a match goes

This was the one path the README admitted bypassed the arena: a JSON
file beside the pitch, polled every two seconds by whichever browser
happened to be hosting. No browser hosts anything now, so the toast
would have vanished from the venue entirely.

In the log rather than in a file means it survives a cut between
matches, reaches the phones and the dugout as well as the wall, and
takes the shared volume between two containers with it."
```

---

### Task 12: The pitch page goes back to being the lab

**Files:**
- Modify: `game/frontend/src/main.js` (delete the host/viewer branching), `game/frontend/src/arena.js` (delete `isHost`, `isViewer`, `keepAwake`, `room.clientId`)
- Delete: `game/frontend/test/keep-awake.test.js`
- Test: `game/frontend/test/viewer-frame.test.js` (check it still holds)

**Interfaces:**
- Consumes: Tasks 6 and 9, which are the two things that took over the roles.

- [ ] **Step 1: Prove nothing still asks for them**

```bash
cd /Users/chuan/mywork/ai/agent-football
grep -rn 'as=host\|as=viewer\|isHost\|isViewer\|keepAwake' --include='*.js' --include='*.py' --include='*.html' . | grep -v node_modules | grep -v /dist/
```
Expected: only the definitions and their tests. If the wall or the dugout still names one, that is a Task 9 or Task 10 leftover - go back and finish it rather than deleting a live consumer.

- [ ] **Step 2: Delete them**

Remove `isHost()`, `isViewer()`, `keepAwake()` and `room.clientId` from `game/frontend/src/arena.js`, and every branch on them in `main.js`. `startPhaserGame()` (`main.js:699-744`) becomes unconditionally the lab's own local game: `role: 'host'`, no `frameSink`, no `reporter`, no seed. Delete `game/frontend/test/keep-awake.test.js`.

- [ ] **Step 3: Run the suite**

Run: `cd game/frontend && npm test`
Expected: PASS.

- [ ] **Step 4: Prove stage 2 still works**

Start the arena, the game and the dugout, open `http://localhost:8002`, and run stage 2. Expected: a real Chrome window opens on bare `:5173`, the workshop room plays a full local match, and the score is readable. This is the constraint the whole design was written around - if it is broken, stop and fix it before committing.

- [ ] **Step 5: Commit**

```bash
git add game/frontend/
git commit -m "refactor(game): the pitch page is the lab again, and only that

Three ways to boot one scene made sense while a tab could be a host, a
viewer or a workshop. The grounds took the first and the wall's direct
mount took the second, so ?as=host and ?as=viewer had no callers left.

keepAwake() goes with them. It was an apology for matches dying with the
screen, and the thing it was apologising for is fixed."
```

---

### Task 13: Measure the capacity

Nothing in the deployment table gets a number before this runs.

**Files:**
- Create: `grounds/tests/test_capacity_rehearsal.py`
- Modify: `deploy/grounds.yaml` (`GROUNDS_CAPACITY`, `cpu`, `memory`)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the rehearsal**

```python
# grounds/tests/test_capacity_rehearsal.py
"""How many matches one Chromium actually holds at ten frames a second.

A headless twin of the arena's test_load_rehearsal.py. It ramps until frame
pacing slips and prints the ceiling, which is where GROUNDS_CAPACITY and the
CPU request in deploy/grounds.yaml come from. Neither is a guess and neither
was written before this ran.
"""
import pytest

pytestmark = pytest.mark.rehearsal

TARGET_HZ = 10
SLIPPED = 0.8  # a match producing under 8 Hz is not keeping up


async def test_how_many_matches_fit(grounds_page, arena):
    ceiling = 0
    for count in range(1, 65):
        code = await arena.open_and_kick_off()
        await grounds_page.evaluate(
            "(a) => window.grounds.host(a.code, a.token, a.seed)",
            {"code": code, "token": arena.token_for(code), "seed": f"{code}-{count}"})
        await arena.settle(seconds=10)
        rates = arena.frame_rates()
        slowest = min(rates.values())
        print(f"{count:3d} matches: slowest {slowest:.1f} Hz, "
              f"median {sorted(rates.values())[len(rates) // 2]:.1f} Hz")
        if slowest < TARGET_HZ * SLIPPED:
            break
        ceiling = count
    print(f"\nceiling: {ceiling} matches at {TARGET_HZ} Hz")
    assert ceiling >= 8, "eight concurrent matches is the floor this design needs"
```

`grounds_page` launches a real Chromium on the built `host.html`. `arena` is a harness around a running arena that opens rooms, kicks them off and counts frames per room off the wall socket. Write both in `grounds/tests/conftest.py`. Mark it `rehearsal` and exclude it from the default run the way the arena excludes its own load rehearsal - copy that convention exactly.

- [ ] **Step 2: Run it on the machine you will size from**

Run: `cd grounds && uv run pytest tests/test_capacity_rehearsal.py -v -s -m rehearsal`
Expected: it prints a table and a ceiling. Run it again with the container's CPU limit applied (`docker run --cpus=4`) so the number describes Cloud Run rather than a laptop.

- [ ] **Step 3: Write the measured numbers in**

Set `GROUNDS_CAPACITY` in `deploy/grounds.yaml` to the measured ceiling with a margin - if it holds 20, set 16. Set `cpu` and `memory` from what the rehearsal actually used. If the ceiling is under the venue's fifty, say so in `grounds/README.md` and note that a second page in the same instance is the next lever, not a redesign.

- [ ] **Step 4: Commit**

```bash
git add grounds/ deploy/grounds.yaml
git commit -m "test(grounds): the capacity number, measured

The load-bearing unknown in the design was whether fifty matches fit in
one Chromium page. This is the ramp that answers it, and the CPU request
and GROUNDS_CAPACITY now come from what it printed rather than from what
seemed about right."
```

---

### Task 14: Deploy to prod and verify there

**Files:**
- Modify: `README.md`, `docs/superpowers/SMOKE.md`, `arena/README.md`, `deploy/README.md`

**Interfaces:**
- Consumes: everything.

Project `multi-gke-ops`, region `asia-southeast1`, account `testadmin@chuancc.altostrat.com`. Prod data is disposable and the user has said so - no backup step.

- [ ] **Step 1: Update the docs to describe what is now true**

`README.md`'s architecture diagram gains the grounds and loses "physics runs in the host tab". The "the host is trusted for physics and nothing else" paragraph now describes the farm rather than a tab. The paragraph admitting the MCP server bypasses the arena goes, since Task 11 retired it. `docs/superpowers/SMOKE.md` gains the two checks that only matter now: close the wall's tab mid-match and reopen it, and kick off with the grounds stopped.

- [ ] **Step 2: Run everything, locally, one more time**

```bash
cd arena && uv run pytest && cd ../game/frontend && npm test && cd ../../grounds && uv run pytest
```
Expected: PASS everywhere. Do not deploy on a red suite.

- [ ] **Step 3: Deploy**

```bash
cd /Users/chuan/mywork/ai/agent-football && PROJECT=multi-gke-ops REGION=asia-southeast1 deploy/deploy.sh
```
Answer the `Continue? [y/N]` prompt. The arena replaces first, the grounds second - the grounds need the arena's URL.

- [ ] **Step 4: Watch the grounds come up**

```bash
gcloud run services logs read grounds --project multi-gke-ops --region asia-southeast1 --limit 50
```
Expected: `pitch open at https://.../pitch/host.html, capacity N` then `connected to the arena`. If Chromium failed to launch, the message is in these logs and it is almost always `/dev/shm` or a missing flag.

- [ ] **Step 5: Prove a match survives a closed tab, in prod**

This is the whole point of the change and it gets tested the way a person would hit it.

1. Open the deployed `/arena` on the laptop. Open a room.
2. Join from a phone (or a second browser), take a dugout, kick off.
3. Confirm the ball is moving on the wall and the clock is running.
4. **Close the arena tab entirely.** Wait ninety seconds - past `HOST_GONE_SECONDS` plus a sweep, which is when the old build would have abandoned it.
5. Reopen `/arena`. Expected: the match is still live, the clock has advanced by roughly ninety seconds, and the score is whatever it became while nobody was watching.

Record what the clock said at step 3 and step 5. If the match was abandoned, the grounds are not holding it and nothing else in this list matters.

- [ ] **Step 6: Prove the refusal is honest**

```bash
gcloud run services update grounds --project multi-gke-ops --region asia-southeast1 --min-instances=0 --max-instances=0
```
Then try to kick off from a phone. Expected: a refusal naming the reason, and the room still sitting in its lobby - not a live room with a clock that never starts. Put it back to `1`/`1` afterwards.

- [ ] **Step 7: Prove the wall at scale, in prod**

Run `arena/fake_host.py` against the deployed arena for enough rooms to fill several pages. Page through them, click one, confirm it pins and the carousel stops, press Escape, confirm the director takes it back. Look at it as an operator: any tile that renders wrong, any control that is hard to hit, gets fixed now.

- [ ] **Step 8: Prove the toast, in prod**

Run stage 5 from the dugout against the deployed arena, and have a specialist report an injury. Expected: the toast appears on the wall and on the phones, and it is still in the log when you cut away and back.

- [ ] **Step 9: Prove stage 2 is untouched**

Run stage 2 from the dugout. Expected: a real Chrome window, the workshop room, a full local match. Nothing about it should have changed.

- [ ] **Step 10: Commit the docs and open the PR**

```bash
git add README.md docs/ arena/README.md deploy/README.md
git commit -m "docs: the grounds, and what stopped being true

The host is not a tab any more, so the paragraph saying it is had to go,
along with the admission that injuries bypass the arena and the apology
for matches dying with the screen."
git push -u origin grounds-host-farm
gh pr create --title "The grounds: matches that run without a screen" --body "$(cat <<'EOF'
Moves match physics out of the browser tab into a server-owned Chromium
farm, and makes the big screen a thing that only watches.

Implements `docs/superpowers/specs/2026-08-15-grounds-host-farm-design.md`.

## Verified in prod

- A match survives its wall's tab being closed for ninety seconds.
- Kick-off with no grounds connected refuses in words and leaves the room
  in its lobby.
- Fifty rooms on the wall: every one reachable by paging, a click pins,
  Escape hands back to the director.
- An injury toast reaches the wall and the phones through the log.
- Stage 2 opens its own Chrome window and plays the workshop room,
  unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| The page (`host.html`, `window.grounds`) | 6 |
| The supervisor (`grounds/`, Playwright) | 7 |
| The control plane `/ws/grounds` | 4 |
| The data plane unchanged | 4 (asserted by not modifying `_handle_from_host`), 5 |
| Assignment at kickoff | 5 |
| A grounds restart does not resume | 7 (`page_reloaded`), 4 (`Grounds.left`) |
| The token splits in two | 2 |
| Liveness splits in two | 3 |
| The substitution toast through the arena | 11 |
| One page, one canvas, booted once | 9 |
| The cut (`scene.point`) | 9 |
| Fifty matches, browsable | 10 |
| What gets deleted (wall) | 10 |
| What gets deleted (`?as=host`/`?as=viewer`) | 12 |
| Determinism and the seed | 1 (sites), 5 (delivery) |
| Deployment | 8, 13 |
| Testing table | 1, 4, 5, 6, 7, 9, 10, 13 |

Two spec items are deliberately deferred and named here rather than silently dropped:

- **The fidelity test** ("same seed in a tab and in the grounds, assert the frame streams agree") is not its own task. Task 1 makes it possible and Task 13's harness is most of the machinery. It should be written once the capacity rehearsal's fixtures exist, as a follow-up - it is a strong test but it is not on the path to working software, and pretending otherwise would put a multi-hour harness between here and a deploy.
- **`MAX_LIVE_ROOMS` becoming the grounds' announced capacity** is explicitly not done in Task 5. Two ceilings that can disagree is worse than one generous one, and `assign()` refusing at kickoff is the real limit. Task 5 Step 5 says so in a comment rather than leaving the next reader to wonder.

**Ordering note:** Tasks 1-8 are the critical path to "matches run without a tab" and can be deployed and verified before 9-12 land. If prod verification is wanted early, deploy after Task 8 and run Task 14 Steps 3-6 then, repeating them at the end.
