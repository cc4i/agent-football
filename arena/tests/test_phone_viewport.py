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

# `height:100dvh`, `min-height: 100dvh`, and the same with other units in the
# same declaration. One group per declaration, holding everything the property
# was given, so the fallbacks can be looked for inside it.
#
# `\d` before the unit and not `\b`: there is no word boundary between the 0 of
# 100 and the d of dvh, so `\bdvh` matches nothing at all and the test below
# passes by finding no declarations to check. It did, until this line said so.
SIZED_BY_DVH = re.compile(r"((?:min-)?height\s*:[^;{}]*\ddvh\b[^;{}]*)")


def declarations_before(text, index):
    """Every declaration of the same property in the rule `index` sits in.

    A fallback is only a fallback if the browser reads it first, so the check
    is not "the file mentions -webkit-fill-available somewhere" -- it is that
    this rule offers it ahead of the `dvh` the old phone cannot parse.
    """
    opened = text.rfind("{", 0, index)
    assert opened != -1, "a declaration outside any rule"
    return text[opened:index]


def test_every_phone_shell_offers_a_height_an_old_iphone_can_parse():
    for found in SIZED_BY_DVH.finditer(CSS):
        rule = declarations_before(CSS, found.start()) + found.group(1)
        assert "-webkit-fill-available" in rule, (
            f"{found.group(1).strip()!r} has no fallback: an iOS below 15.4 "
            "falls back to 100vh, which is taller than the screen it can show")
        # And in the order the cascade reads them, or the modern browsers take
        # the hack and the old ones still take `100vh`.
        assert rule.index("-webkit-fill-available") < rule.index("dvh"), (
            f"{found.group(1).strip()!r} offers the fallback after dvh, so "
            "every browser that understands dvh ignores dvh")


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
