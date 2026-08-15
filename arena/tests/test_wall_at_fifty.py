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


async def test_no_page_of_the_strip_is_left_nearly_empty(wall_page, fifty_live_rooms):
    """Fifty matches make five pages, and none of them is one tile in a blank row.

    The strip is a twelve-column grid, so a page holding one tile is a tile and
    eleven columns of nothing. Filling each page to twelve puts the remainder on
    the last one, and forty-nine matches divide as 12, 12, 12, 12, 1 -- which
    the carousel then turns to unasked, every fifth rotation, and holds for
    twelve seconds. Nobody clicked anything and the wall emptied itself.

    So the pages are balanced rather than filled: 10, 10, 10, 10, 9. The
    assertion is that no two pages differ by more than a tile, because that is
    the property, and it holds at every number of matches rather than at this
    one.
    """
    counts = []
    for index in range(await wall_page.locator("#pages [data-page]").count()):
        await wall_page.locator(f"#pages [data-page='{index}']").click()
        await wall_page.wait_for_selector(f"#pages [data-page='{index}'].on")
        counts.append(await wall_page.locator(".tile[data-code]").count())

    assert min(counts) > 1, f"a page of the wall is nearly empty: {counts}"
    assert max(counts) - min(counts) <= 1, f"the pages are lopsided: {counts}"


async def test_the_tile_numbers_run_on_across_the_pages(wall_page, fifty_live_rooms):
    """One numbering for the whole strip, not one per page.

    A number that restarts at 1 on every page is a number that counts nothing:
    the second page of a wall reads 1 to 10 exactly like the first, so the only
    thing it tells you is where a tile sits in a slice you cannot see the edges
    of. Running the numbers on turns them into a count -- the last tile on the
    last page is how many matches are on -- and the header prints that total so
    it can be read from the first page too.
    """
    numbers = []
    for index in range(await wall_page.locator("#pages [data-page]").count()):
        await wall_page.locator(f"#pages [data-page='{index}']").click()
        await wall_page.wait_for_selector(f"#pages [data-page='{index}'].on")
        numbers += [int(await no.inner_text())
                    for no in await wall_page.locator(".tile-no").all()]

    assert numbers == list(range(1, len(numbers) + 1)), (
        f"the strip is numbered {numbers}, which restarts rather than counts")
    assert await wall_page.locator("#wall-count").inner_text() == str(len(numbers))


async def test_a_digit_puts_up_the_tile_wearing_it(wall_page, fifty_live_rooms):
    """The number printed on a tile is the key that pins it, on any page.

    The shortcut used to mean "the nth tile drawn", which was the same thing
    back when every page started at 1. Now that the numbering runs on, it has
    to follow the print rather than the position, or page two would answer 1
    with a tile wearing an 11.
    """
    await wall_page.locator("#pages [data-page='1']").click()
    await wall_page.wait_for_selector("#pages [data-page='1'].on")

    # On `data-no`, which is what the shortcut resolves against, and exactly:
    # page two prints 11 to 20, and asking for tiles whose number *contains* a
    # nine would find the nineteenth and pass for the wrong reason.
    assert await wall_page.locator(".tile[data-no='9']").count() == 0, (
        "page two of a fifty-match wall starts past nine, so no digit reaches it")

    await wall_page.keyboard.press("9")
    await wall_page.wait_for_timeout(1_000)
    # Nothing was pinned, rather than the second page's first tile being pinned
    # because it happens to be drawn ninth somewhere.
    assert await wall_page.locator("#directing").inner_text() == "Auto"


async def test_the_chip_hands_the_screen_back_without_a_keyboard(wall_page,
                                                                 fifty_live_rooms):
    """A wall is a screen on a wall. Escape is not a gesture it has.

    Clicking a tile a second time would be the obvious way out, and there is no
    tile: pinning a match moves it to centre court, and centre court is the one
    room the strip leaves out. So the only thing on the page that knows a pin is
    in force is the chip that says so, and that is what has to lift it.
    """
    tile = wall_page.locator(".tile[data-code]").first
    code = await tile.get_attribute("data-code")
    await tile.click()
    await wall_page.wait_for_function(
        "(code) => document.querySelector('#court').dataset.showing === code", arg=code)
    assert await wall_page.locator(f".tile[data-code='{code}']").count() == 0

    await wall_page.locator("#director").click()
    await wall_page.wait_for_function(
        "(code) => document.querySelector('#court').dataset.showing !== code",
        arg=code, timeout=30_000)
    assert await wall_page.locator("#directing").inner_text() == "Auto"
    # And it is inert on the way back, so a second click cannot pin the auto
    # pick and leave the wall stuck on it with the chip still reading "Auto".
    assert await wall_page.locator("#director").is_disabled()


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
