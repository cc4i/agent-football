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

"""A way into the venue on a screen that has football on it.

The lobby card is the way in while the lobby is up, and the lobby is the first
thing to go when a match starts. So the case this covers is the commonest thing
a big screen ever does -- showing a match -- and it used to be the one case with
no code anywhere on it, under a footer that read "scan the code to start one".

Which code is the other half. A room with a match in it has its dugouts full,
so its own QR leads to a join form for a match that has already started, which
is a worse answer than no QR at all. These pin both: this room's code while
this room can still be walked into, and the venue's the moment it cannot.

Browser behaviour, so it runs in a browser. Marked `e2e` with the rest.
"""

import httpx
import pytest

pytestmark = pytest.mark.e2e


async def way_in(page):
    """The rail's card, as somebody standing in front of the screen sees it."""
    return await page.evaluate(
        """() => ({
            shown: !document.getElementById('join-mini').hidden,
            label: document.getElementById('way-in').textContent.trim(),
            code: document.getElementById('code-3').hidden
                  ? null : document.getElementById('code-3').textContent.trim(),
            qr: document.querySelector('#qr-mini img')?.getAttribute('src') ?? null,
            framed: document.getElementById('court').dataset.showing,
        })""")


def room_of(page):
    """The room this screen opened for itself, off its own address."""
    return page.url.split("room=")[1].split("&")[0]


async def pin(page, code):
    """Put one match on centre court, wherever on the strip its tile has landed.

    The strip pages at twelve, so a room that kicked off after fifty others is
    on the last page and not under the operator's hand. Paged to rather than
    clicked blind, which is also what a person at the screen would do.
    """
    for index in range(await page.locator("#pages [data-page]").count() or 1):
        if await page.locator(f"#pages [data-page='{index}']").count():
            await page.locator(f"#pages [data-page='{index}']").click()
            await page.wait_for_selector(f"#pages [data-page='{index}'].on")
        if await page.locator(f".tile[data-code='{code}']").count():
            await page.click(f".tile[data-code='{code}']")
            await page.wait_for_function(
                f"() => document.getElementById('court').dataset.showing === '{code}'",
                timeout=15_000)
            return
    raise AssertionError(f"no tile for {code} on any page of the strip")


async def test_a_screen_showing_a_match_always_offers_a_way_in(wall_page):
    """The whole of the bug, in one assertion: there is a code on the screen."""
    card = await way_in(wall_page)
    assert card["framed"], "no match on centre court, so this is the lobby case"
    assert card["shown"], (
        "a screen with football on it and no way into the venue anywhere - the "
        "lobby card went with the lobby and nothing replaced it")
    assert card["qr"]


async def test_it_offers_this_room_while_this_room_can_be_walked_into(wall_page):
    mine = room_of(wall_page)
    card = await way_in(wall_page)
    # The screen's own room is still in its lobby; the match on centre court is
    # somebody else's. The useful code is the one nobody is sitting in.
    assert card["framed"] != mine
    assert card["code"] == mine
    assert card["qr"] == f"/api/rooms/{mine}/qr.svg"
    assert card["label"] == "Start a match here"


async def test_and_the_venue_s_own_the_moment_it_cannot(wall_page, wall_server):
    """Kick the screen's own room off and the code it offers has to change.

    Kick-off is not a cut, so nothing about what is on centre court moves here.
    That is the point: the card is drawn from a cut *and* from the room's own
    state, and only the second of those tells it the seats have gone.
    """
    mine = room_of(wall_page)
    assert (await way_in(wall_page))["code"] == mine

    async with httpx.AsyncClient(base_url=wall_server, timeout=30) as phone:
        await phone.post("/api/players",
                         json={"display_name": "Walked Up Late", "email": ""})
        await phone.post(f"/api/rooms/{mine}/seats/blue",
                         json={"philosophy": "high press"})
        await phone.post(f"/api/rooms/{mine}/seats/blue/ready", json={"ready": True})
        started = await phone.post(f"/api/rooms/{mine}/start")
    assert started.status_code == 200, started.text

    await wall_page.wait_for_function(
        "() => document.getElementById('code-3').hidden", timeout=15_000)
    card = await way_in(wall_page)
    assert card["shown"], "the way in vanished instead of changing"
    assert card["qr"] == "/qr.svg", (
        "still offering this room's code for a match that has kicked off: a "
        "phone scanning it lands on a join form for a match it cannot join")
    assert card["label"] == "Scan to play"
    # There is no venue code to read out and type, so the line under it goes
    # rather than sitting there empty or, worse, still reading the old room.
    assert card["code"] is None


async def test_the_screen_showing_its_own_match_is_the_case_this_was_reported_for(
        wall_page, wall_server):
    """Own room, own match, own centre court: the screenshot that started this.

    The rail's code used to be shown only while the screen had been given over
    to somebody else's match, so this state - the one a screen spends most of
    an evening in - had no code on it at all.
    """
    mine = room_of(wall_page)
    async with httpx.AsyncClient(base_url=wall_server, timeout=30) as phone:
        await phone.post("/api/players",
                         json={"display_name": "Own Match Watcher", "email": ""})
        await phone.post(f"/api/rooms/{mine}/seats/blue",
                         json={"philosophy": "counter"})
        await phone.post(f"/api/rooms/{mine}/seats/blue/ready", json={"ready": True})
        assert (await phone.post(f"/api/rooms/{mine}/start")).status_code == 200

    # Put it on the big screen. A room that has just kicked off at a venue with
    # fifty already on is the last tile on the last page, so it is paged to
    # rather than reached for -- see `test_every_match_is_reachable_by_paging`.
    await pin(wall_page, mine)

    card = await way_in(wall_page)
    assert card["framed"] == mine
    assert card["shown"], (
        "a screen showing its own match has no way into the venue on it, which "
        "is the state it is in for most of an evening")
    assert card["qr"] == "/qr.svg"
    assert card["label"] == "Scan to play"
