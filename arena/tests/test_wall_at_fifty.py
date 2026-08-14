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

"""Fifty matches on one screen, and a person standing in front of it.

The wall used to be six tiles on a twelve-second carousel with nothing to
steer it. That is a fine screensaver and no way to find a match: at fifty
rooms the one somebody asked about was up to a minute and a half away, and
there was no way to go and get it.

Everything here is browser behaviour -- what a click does, what a key does,
what is on the big screen a quarter of a minute later -- so it runs in a
browser. Marked `e2e` and left out of the ordinary suite, which runs in
seconds and should go on doing so.
"""

import pytest

pytestmark = pytest.mark.e2e

# Long enough to be past a rotation of the carousel, which turns every twelve
# seconds. That the screen has not moved by then is the assertion.
PAST_A_ROTATION_MS = 14_000


async def test_every_match_is_reachable_by_paging(wall_page, fifty_live_rooms):
    codes = set(fifty_live_rooms)
    seen = set()
    pages = await wall_page.locator("#pages [data-page]").count()
    assert pages == 5, "forty-nine tiles at twelve a page is five pages"

    for index in range(pages):
        await wall_page.locator(f"#pages [data-page='{index}']").click()
        # The page button says which page is up, so waiting on it is waiting for
        # the strip under it rather than for a fixed number of milliseconds.
        await wall_page.wait_for_selector(f"#pages [data-page='{index}'].on")
        for tile in await wall_page.locator(".tile[data-code]").all():
            seen.add(await tile.get_attribute("data-code"))

    # Every match but the one the operator is already watching, which is on the
    # big screen instead of on the strip. That is the rule, so it is the
    # assertion: an equality here rather than a subset, because a tile quietly
    # going missing is exactly the failure this test exists to catch.
    on_court = await wall_page.locator("#court").get_attribute("data-showing")
    assert codes - seen == {on_court}


async def test_clicking_a_tile_pins_it(wall_page, fifty_live_rooms):
    tile = wall_page.locator(".tile[data-code]").first
    code = await tile.get_attribute("data-code")
    await tile.click()
    await wall_page.wait_for_function(
        "(code) => document.querySelector('#court').dataset.showing === code", arg=code)

    # And the director has stopped arguing about it. One of the fifty is in its
    # last half-minute and is worth more than this match by every measure the
    # wall has; the pin outranks the arithmetic until somebody lifts it.
    await wall_page.wait_for_timeout(PAST_A_ROTATION_MS)
    assert await wall_page.locator("#court").get_attribute("data-showing") == code
    assert "Pinned" in await wall_page.locator("#directing").inner_text()


async def test_a_pinned_match_holds_the_page_it_was_on(wall_page, fifty_live_rooms):
    """The carousel gets out of the way of a hand, and comes back afterwards.

    Paging under somebody who is reaching for a tile is the wall taking the
    screen back off the person standing in front of it.
    """
    await wall_page.locator("#pages [data-page='2']").click()
    await wall_page.wait_for_selector("#pages [data-page='2'].on")
    on_page_two = [await tile.get_attribute("data-code")
                   for tile in await wall_page.locator(".tile[data-code]").all()]

    await wall_page.wait_for_timeout(PAST_A_ROTATION_MS)
    assert await wall_page.locator("#pages .page-no.on").inner_text() == "3"
    assert [await tile.get_attribute("data-code")
            for tile in await wall_page.locator(".tile[data-code]").all()] == on_page_two


async def test_escape_hands_it_back_to_the_director(wall_page, fifty_live_rooms):
    tile = wall_page.locator(".tile[data-code]").first
    code = await tile.get_attribute("data-code")
    await tile.click()
    await wall_page.wait_for_function(
        "(code) => document.querySelector('#court').dataset.showing === code", arg=code)

    await wall_page.keyboard.press("Escape")
    # The director keeps a match for a few seconds after a cut whatever the
    # arithmetic says, so this is not instant. It is not thirty seconds either.
    await wall_page.wait_for_function(
        "(code) => document.querySelector('#court').dataset.showing !== code",
        arg=code, timeout=30_000)
    assert await wall_page.locator("#directing").inner_text() == "Auto"
