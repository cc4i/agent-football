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

"""A manager who does not want to wait for a screen to free one up.

A screen holds one room, and it opens its next lobby when the match on it ends.
So the second person to walk up to a screen with football on it read "A screen
opens its next lobby as soon as the match on it ends" and had nothing to tap
for three minutes -- which is the whole venue's turnstile, and it is one page.

This is the second door. Nothing on the server changed to open it: a room has
been openable over `POST /api/rooms` all along, and a socket carrying the
screen token that comes back has counted as somebody holding the lobby for just
as long -- `test_abandoning.py` covers both at length. What was missing was a
phone that could ask.

Which makes the one thing worth driving a browser for the thing that is new:
whether the phone really does hold the room it just opened. A lobby nobody
vouches for is abandoned by the sweep in HOST_GONE_SECONDS, so a phone that
opens a room and does not carry its token has bought a room that evaporates
under it while its manager is choosing a philosophy.
"""

import pytest

pytestmark = pytest.mark.e2e

# An iPhone 14, which is the handset the phone pages are drawn for.
HANDSET = {"width": 390, "height": 844}


@pytest.fixture
async def phone(real_arena_server):
    """A handset in front of the venue, with a manager already registered."""
    from playwright.async_api import async_playwright

    async with async_playwright() as driving:
        browser = await driving.chromium.launch()
        page = await browser.new_page(viewport=HANDSET)
        complaints = []
        page.on("pageerror", lambda blew_up: complaints.append(str(blew_up)))
        page.on("console",
                lambda note: complaints.append(note.text)
                if note.type == "error" and not note.text.startswith("Failed to load")
                else None)

        await page.goto(f"{real_arena_server}/register")
        await page.fill("#name", "Alex Rivera")
        await page.click("#done")
        await page.wait_for_url(f"{real_arena_server}/home", timeout=15_000)
        page.arena = real_arena_server
        yield page
        await browser.close()
    assert not complaints, f"the phone logged errors: {complaints}"


def the_app():
    """The app object the server thread is running, for the arena's own view."""
    import app

    return app.app


def held_by_a_screen():
    """The rooms this instance has somebody holding open as a lobby.

    The arena's own answer to "is anybody behind this room", which is what the
    sweep reads before it gives up on one. A phone that opened a room has to be
    in here or its room does not survive the walk to the dugout.
    """
    return the_app().state.held.codes("screen")


async def open_a_room(page, mode):
    """Tap the control and wait for the room it opens."""
    await page.click(f"#open-room [data-mode='{mode}']")
    await page.wait_for_url(lambda url: "/join/" in url, timeout=15_000)
    return page.url.rstrip("/").split("/")[-1].upper()


@pytest.fixture
def grounds_for_the_page():
    """A pitch for the arena the page is talking to, so kick-off has one.

    The server runs in a thread of this process and shares the app object, so
    the registry is reachable from here. See `tests.standins` for why most of
    the suite stands in rather than holding a real control socket.
    """
    from tests.standins import connect_grounds

    return connect_grounds(the_app())


async def take_the_dugout(page):
    """Pick a philosophy and sit down, which is what the join form is."""
    await page.wait_for_selector("#pills .pill", timeout=15_000)
    await page.click("#pills .pill")
    await page.wait_for_selector("#take:not([disabled])", timeout=15_000)
    await page.click("#take")
    await page.wait_for_url(lambda url: "/play" in url, timeout=15_000)


async def test_a_phone_can_open_a_room_without_a_screen(phone):
    code = await open_a_room(phone, "solo")
    assert len(code) == 4, f"landed somewhere that is not a room: {phone.url}"


async def test_the_phone_that_opened_it_is_the_one_holding_it(phone):
    """The assertion this whole file exists for.

    Not "the room is still there a moment later", which passes for a whole
    sweep whatever the phone does. This is the arena's own register of who is
    behind a room, read from the process serving the page.
    """
    code = await open_a_room(phone, "solo")
    await phone.wait_for_function(
        "() => document.readyState === 'complete'", timeout=10_000)
    assert code in held_by_a_screen(), (
        f"the arena has nobody holding {code}; it is "
        f"holding {held_by_a_screen()}")


async def test_it_is_still_held_once_the_manager_reaches_the_dugout(phone,
                                                                    grounds_for_the_page):
    """The join form is not the end of the walk, and neither is the lobby.

    A manager opens a room, picks a philosophy, and then sits in the dugout
    deciding whether to press Ready. Every one of those is a page, and the room
    has to survive all of them.
    """
    code = await open_a_room(phone, "solo")
    await take_the_dugout(phone)
    await phone.wait_for_selector("#go:not([disabled])", timeout=15_000)
    assert code in held_by_a_screen(), f"the dugout stopped holding {code}"


async def test_a_room_a_phone_opened_can_be_played_through(phone, grounds_for_the_page):
    """Open, sit down, ready, kick off - with nothing but a handset."""
    code = await open_a_room(phone, "solo")
    await take_the_dugout(phone)

    await phone.wait_for_selector("#go[data-does='ready']", timeout=15_000)
    await phone.click("#go")                       # I'm ready
    await phone.wait_for_selector("#go[data-does='start']", timeout=15_000)
    await phone.click("#go")                       # Kick off

    import rooms

    await phone.wait_for_timeout(1500)
    assert rooms.by_code(the_app().state.conn, code)["status"] == "live"


async def test_a_head_to_head_room_waits_for_the_other_dugout(phone,
                                                              grounds_for_the_page):
    # The mode the phone chose is the mode it gets, and a versus room does not
    # kick off on one manager.
    code = await open_a_room(phone, "versus")

    import rooms

    assert rooms.by_code(the_app().state.conn, code)["mode"] == "versus"


async def test_the_room_turns_up_for_everybody_else(phone):
    """A phone's room is a room, so it is on the list every other phone reads."""
    import httpx

    code = await open_a_room(phone, "solo")
    async with httpx.AsyncClient(base_url=phone.arena, timeout=15) as another:
        listed = (await another.get("/api/rooms/open")).json()
    assert code in [room["code"] for room in listed["rooms"]]


async def test_the_empty_state_no_longer_sends_anybody_to_wait_for_a_screen(phone):
    """The sentence that started this.

    With a button on the page, "a screen opens its next lobby as soon as the
    match on it ends" is no longer the answer to "where can I play".
    """
    rooms_box = await phone.text_content("#rooms")
    assert "opens its next lobby" not in rooms_box
    assert await phone.is_visible("#open-room")
