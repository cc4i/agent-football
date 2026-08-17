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

"""Full time, and the walk back to another match.

Reported from a venue: every match had finished, the screen was sitting on
"Full time. Open a new room to play again.", and the QR code beside it still
pointed at the room that had just ended. Scanning it -- which is the only
instruction on the screen -- reached "That room is closed. Scan the code for
the next one," on a phone whose camera had just scanned exactly that code.

Two separate failures meet at that dead end, so they are tested separately.

`test_taking_turns.py` already claims the first of them is fixed, and it does
so by reading `arena.js` as a string. Every assertion in it passes while the
countdown never runs in a browser, which is the state the venue found it in.
So these drive it: a real screen, a real whistle, and the page's own behaviour.
"""

import pytest

pytestmark = pytest.mark.e2e

# A wall screen, and the handset the phone pages are drawn for.
WALL = {"width": 1440, "height": 900}
HANDSET = {"width": 390, "height": 844}


def the_app():
    """The app object the server thread is running."""
    import app

    return app.app


@pytest.fixture
def grounds_for_the_page():
    """Somewhere for a kick-off to be played, or `/start` is an honest 503."""
    from tests.standins import connect_grounds

    return connect_grounds(the_app())


@pytest.fixture
async def browser():
    from playwright.async_api import async_playwright

    async with async_playwright() as driving:
        engine = await driving.chromium.launch()
        yield engine
        await engine.close()


async def a_page(browser, viewport):
    page = await browser.new_page(viewport=viewport)
    page.complaints = []
    page.on("pageerror", lambda blew_up: page.complaints.append(str(blew_up)))
    return page


async def play_it_out(arena, code):
    """Seat a manager, kick off, and blow the whistle. Leaves the room finished.

    Over HTTP and a host socket rather than through the phone pages: what these
    tests are about is what the venue does *after* the whistle, and driving two
    browsers to get there would put the thing under test behind five minutes of
    somebody else's UI.
    """
    import httpx

    async with httpx.AsyncClient(base_url=arena, timeout=15) as manager:
        await manager.post("/api/players",
                           json={"display_name": "Alex Rivera",
                                 "email": "alex@example.com"})
        await manager.post(f"/api/rooms/{code}/seats/blue",
                           json={"philosophy": "high press"})
        await manager.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        started = await manager.post(f"/api/rooms/{code}/start")
    assert started.status_code == 200, f"could not kick off: {started.text}"
    await whistle(arena, code)


async def whistle(arena, code):
    """Full time on a room that is already live, as the grounds would call it.

    Its own function because the tests below need the whistle without the
    seating: their manager is a browser that has already sat down, and seating
    a second one over HTTP would be a different match.
    """
    import websockets

    from tests.conftest import physics_token

    physics = physics_token(the_app().state.conn, code)
    socket_url = arena.replace("http://", "ws://")
    async with websockets.connect(
            f"{socket_url}/ws/rooms/{code}?client_id={physics}") as host:
        await host.recv()                       # the snapshot every socket opens on
        import json
        await host.send(json.dumps({"type": "host.event", "kind": "full_time",
                                    "match_ms": 180_000,
                                    "payload": {"score": [2, 1]}}))
        # Read until the arena says the room is closed, so the whistle has
        # landed before anything below asks the page what it did about it.
        for _ in range(10):
            message = json.loads(await host.recv())
            if message.get("type") == "room" and message.get("status") == "finished":
                return
    raise AssertionError("the arena never closed the room the whistle was blown on")


async def a_screen_holding_a_room(browser, arena):
    """A wall screen that has opened a room of its own, as a venue leaves it."""
    page = await a_page(browser, WALL)
    await page.goto(f"{arena}/arena?mode=solo")
    await page.wait_for_function(
        "() => new URLSearchParams(location.search).get('room')", timeout=15_000)
    code = await page.evaluate(
        "() => new URLSearchParams(location.search).get('room')")
    return page, code


# ── The screen ────────────────────────────────────────────────────────────


@pytest.mark.timeout(90)
async def test_the_screen_counts_down_to_the_next_lobby(browser, real_arena_server,
                                                        grounds_for_the_page):
    """The countdown `test_taking_turns.py` asserts the source code of.

    The badge is the only thing on the screen that says the venue has not
    stopped for the day, and it is what the queue reads while it waits.
    """
    page, code = await a_screen_holding_a_room(browser, real_arena_server)
    await play_it_out(real_arena_server, code)

    await page.wait_for_function(
        "() => document.getElementById('badge').textContent.includes('Next lobby in')",
        timeout=15_000)
    assert not page.complaints, f"the screen logged errors: {page.complaints}"


@pytest.mark.timeout(90)
async def test_the_screen_opens_the_next_room_on_its_own(browser, real_arena_server,
                                                         grounds_for_the_page):
    """And having counted, it actually goes.

    The whole venue's turnstile is this page. A screen that counts to zero and
    stays where it is has told the queue it is coming back and then not come.
    """
    page, code = await a_screen_holding_a_room(browser, real_arena_server)
    await play_it_out(real_arena_server, code)

    await page.wait_for_function(
        f"() => {{ const now = new URLSearchParams(location.search).get('room');"
        f"         return now && now !== '{code}'; }}",
        timeout=60_000)
    assert not page.complaints, f"the screen logged errors: {page.complaints}"


@pytest.mark.timeout(90)
async def test_the_screen_stops_offering_a_code_that_cannot_be_played(
        browser, real_arena_server, grounds_for_the_page):
    """The QR in the rail is an instruction, and at full time it was a wrong one.

    For the whole of the handover the largest thing on the screen says "Scan to
    play" over a code that answers "that room is closed". Whatever the rail
    points at while the result stands, it must not be a room nobody can enter.
    """
    page, code = await a_screen_holding_a_room(browser, real_arena_server)
    await play_it_out(real_arena_server, code)
    await page.wait_for_function(
        "() => document.getElementById('badge').textContent.includes('Next lobby in')",
        timeout=15_000)

    pointing = await page.get_attribute("#qr img", "src")
    assert code not in (pointing or ""), (
        f"the rail is still offering {code}, which is finished: {pointing}")


# ── The phone that scanned it anyway ──────────────────────────────────────


@pytest.mark.timeout(90)
async def test_a_closed_room_is_not_the_end_of_the_evening(browser, real_arena_server,
                                                           grounds_for_the_page):
    """The screenshot that started this.

    A phone reaches a finished room for reasons no amount of tidying removes: a
    code scanned during the handover, a photo of the screen, a back button, a
    tab reopened. What it must never be is a page with one sentence on it
    telling somebody to do the thing they have just done.
    """
    page, code = await a_screen_holding_a_room(browser, real_arena_server)
    await play_it_out(real_arena_server, code)

    phone = await a_page(browser, HANDSET)
    await phone.goto(f"{real_arena_server}/join/{code}")
    await phone.wait_for_selector("#problem:not([hidden])", timeout=15_000)

    onward = await phone.query_selector_all(
        "#onward a, #onward button, a[href='/home'], a[href='/board']")
    assert onward, (
        "a closed room offers a manager nothing to tap: "
        f"{await phone.text_content('body')}")


@pytest.mark.timeout(90)
async def test_the_closed_room_stops_telling_them_to_scan_it_again(
        browser, real_arena_server, grounds_for_the_page):
    """"Scan the code for the next one" is advice, and it is wrong here.

    The code they scanned is this one. There is no next one to scan until a
    screen opens it, and the phone in their hand can open one itself.
    """
    page, code = await a_screen_holding_a_room(browser, real_arena_server)
    await play_it_out(real_arena_server, code)

    phone = await a_page(browser, HANDSET)
    await phone.goto(f"{real_arena_server}/join/{code}")
    await phone.wait_for_selector("#problem:not([hidden])", timeout=15_000)

    said = await phone.text_content("#problem")
    assert "scan the code" not in (said or "").lower(), (
        f"the dead end still sends them back to the camera: {said!r}")


@pytest.mark.timeout(90)
async def test_a_manager_already_in_a_match_is_sent_back_to_it(browser, real_arena_server,
                                                               grounds_for_the_page):
    """One manager, one room.

    Somebody whose own match is waiting on them does not need a second room --
    they need the one they wandered off from. Offered both, the venue collects
    a lobby nobody is behind for every stray scan, and at a busy venue that is
    somebody else's kick-off.
    """
    screen, dead = await a_screen_holding_a_room(browser, real_arena_server)
    await play_it_out(real_arena_server, dead)

    # A phone that registers, opens a room of its own and sits down in it.
    phone = await a_page(browser, HANDSET)
    await phone.goto(f"{real_arena_server}/register")
    await phone.fill("#name", "Sam Okafor")
    await phone.click("#done")
    await phone.wait_for_url(f"{real_arena_server}/home", timeout=15_000)
    await phone.click("#open-room [data-mode='solo']")
    await phone.wait_for_url(lambda url: "/join/" in url, timeout=15_000)
    mine = phone.url.rstrip("/").split("/")[-1].upper()
    await phone.wait_for_selector("#pills .pill", timeout=15_000)
    await phone.click("#pills .pill")
    await phone.wait_for_selector("#take:not([disabled])", timeout=15_000)
    await phone.click("#take")
    await phone.wait_for_url(lambda url: "/play" in url, timeout=15_000)

    # Now they scan the dead code off the screen across the hall.
    await phone.goto(f"{real_arena_server}/join/{dead}")
    await phone.wait_for_selector("#onward:not([hidden])", timeout=15_000)

    assert await phone.is_visible("#onward-match"), \
        "a manager with a match of their own was not offered the way back to it"
    assert mine in (await phone.get_attribute("#onward-match", "href") or ""), \
        "the way back points at the wrong room"
    assert await phone.is_hidden("#onward-own"), \
        "they were offered a second room while already holding one"


# ── The manager who just played, on their own phone ───────────────────────


async def fill_the_other_dugout(arena, code):
    """A second manager in red, over HTTP.

    A head to head room does not kick off on one dugout. The manager under test
    is the one holding the phone; this one is scenery, and driving a second
    browser through the join form to provide it would be three minutes spent on
    a page these tests are not about.
    """
    import httpx

    async with httpx.AsyncClient(base_url=arena, timeout=15) as other:
        await other.post("/api/players", json={"display_name": "Jordan Blake",
                                               "email": "jordan@example.com"})
        await other.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "low block"})
        await other.post(f"/api/rooms/{code}/seats/red/ready", json={"ready": True})


async def a_phone_that_has_played(browser, arena, mode="solo"):
    """Register, open a room, sit down, kick off, and take the whistle.

    Through the pages rather than around them, because the page under test is
    the one this manager is left holding at the end of it.
    """
    phone = await a_page(browser, HANDSET)
    await phone.goto(f"{arena}/register")
    await phone.fill("#name", "Chuan C")
    await phone.click("#done")
    await phone.wait_for_url(f"{arena}/home", timeout=15_000)
    await phone.click(f"#open-room [data-mode='{mode}']")
    await phone.wait_for_url(lambda url: "/join/" in url, timeout=15_000)
    code = phone.url.rstrip("/").split("/")[-1].upper()
    await phone.wait_for_selector("#pills .pill", timeout=15_000)
    await phone.click("#pills .pill")
    await phone.wait_for_selector("#take:not([disabled])", timeout=15_000)
    await phone.click("#take")
    await phone.wait_for_url(lambda url: "/play" in url, timeout=15_000)
    if mode == "versus":
        await fill_the_other_dugout(arena, code)
    await phone.wait_for_selector("#go[data-does='ready']", timeout=15_000)
    await phone.click("#go")
    await phone.wait_for_selector("#go[data-does='start']", timeout=15_000)
    await phone.click("#go")

    await whistle(arena, code)
    await phone.wait_for_selector("#result:not([hidden])", timeout=20_000)
    return phone, code


@pytest.mark.timeout(120)
async def test_full_time_is_not_the_end_of_the_evening(browser, real_arena_server,
                                                       grounds_for_the_page):
    """The screenshot that started this one.

    The result sheet had exactly one control on it -- "See the full board" --
    and the board has none at all. A manager who had just played, whose phone
    the venue already knows, whose next match is one POST away, was left with
    the browser's back button.
    """
    phone, code = await a_phone_that_has_played(browser, real_arena_server)

    # Scoped to the result sheet: the lobby above has its own way home, for the
    # room that shut before it started, and it is hidden at full time.
    assert await phone.is_visible("#again"), "no way to play again at full time"
    assert await phone.is_visible("#result a[href='/home']"), "no way home at full time"
    assert await phone.is_visible("#result a[href='/board']"), "the board link went missing"
    assert not phone.complaints, f"the phone logged errors: {phone.complaints}"


@pytest.mark.timeout(120)
async def test_full_time_stops_sending_them_to_the_camera(browser, real_arena_server,
                                                          grounds_for_the_page):
    """"Scan the code on the screen for another go" is wrong twice over.

    The screen hands over twenty seconds after the whistle, so by the time this
    is read the code on it is a different room -- and the manager reading it is
    usually nowhere near the screen anyway.
    """
    phone, code = await a_phone_that_has_played(browser, real_arena_server)

    hint = await phone.text_content("#r-hint")
    assert "scan the code" not in (hint or "").lower(), \
        f"full time still points at the camera: {hint!r}"


@pytest.mark.timeout(120)
async def test_playing_again_opens_the_mode_just_played(browser, real_arena_server,
                                                        grounds_for_the_page):
    """One tap from a result to picking a philosophy, in the same mode.

    A manager who just played head to head and got a solo room would have
    brought somebody with them for nothing.
    """
    phone, code = await a_phone_that_has_played(browser, real_arena_server, mode="versus")
    await phone.click("#again")
    await phone.wait_for_url(lambda url: "/join/" in url and code not in url.upper(),
                             timeout=20_000)

    import rooms

    fresh = phone.url.rstrip("/").split("/")[-1].upper()
    assert rooms.by_code(the_app().state.conn, fresh)["mode"] == "versus", \
        "played head to head, got given something else"


# ── The standings, which are two pages wearing one file ───────────────────


@pytest.mark.timeout(90)
async def test_the_standings_let_a_phone_back_out(browser, real_arena_server):
    """Every phone route into the board was a route with no way out of it.

    Home, the join form and full time all link here, and the page itself has
    not one link on it.
    """
    phone = await a_page(browser, HANDSET)
    await phone.goto(f"{real_arena_server}/board")
    await phone.wait_for_selector("#board-home", timeout=15_000)
    assert await phone.is_visible("#board-home"), "the board is still a leaf"
    assert not phone.complaints, f"the board logged errors: {phone.complaints}"


@pytest.mark.timeout(90)
async def test_the_wall_keeps_the_board_it_had(browser, real_arena_server):
    """The same file is the iframe under the big screen's lobby.

    Which is the whole reason it never had chrome, and the reason this is
    decided by whether the page is framed rather than by who linked to it. A
    wall screen has nowhere to go home to.
    """
    screen, _ = await a_screen_holding_a_room(browser, real_arena_server)
    frame = screen.frame_locator("#board")
    await frame.locator("#view-solo").wait_for(timeout=20_000)
    # Present in the markup and never shown, which is the point: it is left
    # hidden rather than not rendered so the wall cannot flash it on the way in.
    assert not await frame.locator("#board-home").is_visible(), \
        "the wall's board is showing a Back to home button"
