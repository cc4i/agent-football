"""The phone pages against the oldest handset that will be pointed at them.

Read as text, for a reason worth stating plainly: the bug this guards against
only happens in a browser that does *not* understand `dvh`, and every browser
a test can drive does. Chromium and WebKit both resolve `100dvh` correctly, so
a functional test of the fallback would exercise the branch that already works
and assert nothing about the branch that broke.

What broke, in production, on a manager's iPhone: the dugout is sized
`height:100dvh` with `overflow:hidden`, and its primary action is pinned to the
bottom of that box. On iOS before 15.4 there is no `dvh`, so the height came
from `100vh` -- which on iOS is the screen with both toolbars retracted, taller
than the visible area. The Ready button was laid out behind the address bar and
`overflow:hidden` meant there was no scrolling to reach it. Scan the code, take
the dugout, and there is no way to start the match.

So the assertion is the shape of the cascade rather than a rendered pixel:
wherever a phone shell takes its height from `dvh`, `-webkit-fill-available`
has to be offered first. `test_service_yaml.py` reads its files the same way
and for the same kind of reason.
"""

import re

from tests.test_images import ROOT

STATIC = ROOT / "arena" / "static"
CSS = (STATIC / "app.css").read_text()

# The pages that are a phone in the hand rather than a screen on a wall.
PHONE_PAGES = ("home.html", "join.html", "play.html", "register.html")

# `height:100svh`, `min-height: 100dvh`, and the same with other units in the
# same declaration. One group per declaration, holding everything the property
# was given, so the fallbacks can be looked for inside it.
#
# `\d` before the unit and not `\b`: there is no word boundary between the 0 of
# 100 and the s of svh, so `\bsvh` matches nothing at all and the test below
# passes by finding no declarations to check. It did, until this line said so.
#
# All three of the new units, not just the one in use today. The point is that
# whichever a shell is sized by, an iOS below 15.4 understands none of them and
# has to be handed something first.
SIZED_BY_VIEWPORT = re.compile(
    r"((?:min-)?height\s*:[^;{}]*\d(?:dvh|svh|lvh)\b[^;{}]*)")

# The dugout: the one phone shell that does not scroll.
FIXED_SHELL = re.compile(r"body\.phone\.fixed\s*\{([^}]*)\}")


def declarations_before(text, index):
    """Every declaration of the same property in the rule `index` sits in.

    A fallback is only a fallback if the browser reads it first, so the check
    is not "the file mentions -webkit-fill-available somewhere" -- it is that
    this rule offers it ahead of the `dvh` the old phone cannot parse.
    """
    opened = text.rfind("{", 0, index)
    assert opened != -1, "a declaration outside any rule"
    return text[opened:index]


def test_this_file_is_reading_something():
    """The regex above has matched nothing once already. Not silently again.

    Every assertion here is a loop over what that pattern finds, so a pattern
    that finds nothing is a file of tests that all pass and check nothing.
    """
    assert SIZED_BY_VIEWPORT.findall(CSS), (
        "no phone shell is sized by dvh/svh/lvh any more, so every check below "
        "is passing over an empty list - fix the pattern or delete the file")
    assert FIXED_SHELL.search(CSS), "body.phone.fixed is gone from the stylesheet"


def test_every_phone_shell_offers_a_height_an_old_iphone_can_parse():
    for found in SIZED_BY_VIEWPORT.finditer(CSS):
        rule = declarations_before(CSS, found.start()) + found.group(1)
        assert "-webkit-fill-available" in rule, (
            f"{found.group(1).strip()!r} has no fallback: an iOS below 15.4 "
            "falls back to 100vh, which is taller than the screen it can show")
        # And in the order the cascade reads them, or the modern browsers take
        # the hack and the old ones still take `100vh`.
        unit = re.search(r"\d(dvh|svh|lvh)\b", found.group(1)).group(1)
        assert rule.index("-webkit-fill-available") < rule.index(unit), (
            f"{found.group(1).strip()!r} offers the fallback after {unit}, so "
            f"every browser that understands {unit} ignores {unit}")


def test_the_dugout_is_sized_by_the_viewport_it_can_actually_see():
    """The shell that cannot scroll takes the small viewport, not the dynamic one.

    Found on two real handsets at once, an iPhone XR and an iPhone 17, both in
    Safari. `dvh` is the viewport as it is this second, and a page that never
    scrolls never makes Safari retract a toolbar - so both phones laid the
    dugout out to a height neither was showing, and the shout chips went under
    the address bar. `overflow:hidden` meant there was no scrolling to reach
    them: the manager could see the match and could not talk to the squad.

    `svh` is the height with every toolbar out, which is the only state a page
    that cannot scroll is ever in.
    """
    rule = FIXED_SHELL.search(CSS).group(1)
    assert "svh" in rule, f"the dugout is not sized by svh: {rule.strip()!r}"
    assert "dvh" not in rule, (
        "the dugout is sized by dvh again. It cannot scroll, so Safari never "
        f"retracts a toolbar for it and dvh overshoots the glass: {rule.strip()!r}")


def test_the_dugout_can_still_be_scrolled_to_if_a_browser_overshoots():
    """The belt to svh's braces, and the reason the bug cost anybody anything.

    Sizing correctly is the fix; being clipped with no way back is what turned
    a few pixels of overshoot into a manager who could not shout. Whatever this
    shell is sized by, its overflow must leave the actions reachable.
    """
    rule = FIXED_SHELL.search(CSS).group(1)
    assert "overflow:hidden" not in rule.replace(" ", ""), (
        "the dugout clips its own overflow again, so a browser that reports a "
        f"viewport taller than its glass hides the shout chips: {rule.strip()!r}")


def test_the_fill_available_hack_is_kept_off_the_wall():
    """`html{height:-webkit-fill-available}` is scoped, not global.

    The wall is a browser with no toolbars to subtract and no reason to be
    sized by a non-standard property. Scoping it needs a class on <html>,
    because a page cannot select its own root off its body's class on the very
    browsers this is for -- `:has()` landed in the same iOS 15.4 as `dvh`.
    """
    for rule in re.findall(r"^\s*html[^{]*\{[^}]*-webkit-fill-available[^}]*\}",
                           CSS, re.MULTILINE):
        assert "html.phone" in rule, f"unscoped: {rule.strip()!r}"

    for page in PHONE_PAGES:
        opening = (STATIC / page).read_text().split("\n")[1]
        assert 'class="phone"' in opening, (
            f"{page} sizes itself as a phone but its <html> is not marked one: "
            f"{opening!r}")

    # And the wall is not marked one, which is the half of this that a typo
    # would otherwise pass silently.
    for page in ("arena.html", "board.html", "poster.html"):
        assert 'class="phone"' not in (STATIC / page).read_text().split("\n")[1]
