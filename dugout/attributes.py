"""What each role's attributes are, and how far each one may move.

This used to be a second copy of the arena's rules, worked out from the shipped
baseline files sitting next to the pitch. Two copies of a validator drift apart
until one of them is wrong, and this one had no business being a validator at
all: the arena is what accepts or refuses a change. So the dugout asks it.

Kept for the life of the process once fetched. These are the rules the game was
built with, not a room's copy of them, and they cannot change under a running
arena -- but a new session forgets them anyway, so a restarted arena is never
answered from yesterday's cache.
"""

import arena

ROLES = ("defender", "midfielder", "forward", "goalkeeper")

_rules: dict | None = None


def rules() -> dict:
    """Every role's bands: {role: {attribute: {baseline, min, max}}}."""
    global _rules
    if _rules is None:
        _rules = arena.rules()
    return _rules


def forget() -> None:
    """Drop what the arena said, so the next question is asked again."""
    global _rules
    _rules = None


def bands(role: str) -> dict:
    """One role's attributes: {attribute: {baseline, min, max}}."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    return rules().get(role, {})


def band(role: str, attribute: str) -> dict:
    """One attribute's shipped value and its two limits.

    An attribute the arena has never heard of is given the unit range and no
    shipped value, which is what draws it as a bar with no tick on it. Nothing
    should reach this -- the arena refuses a change to an attribute it does not
    hold -- but a panel is not worth failing a tool call over.
    """
    return bands(role).get(attribute) or {"baseline": None, "min": 0.0, "max": 1.0}
