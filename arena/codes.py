"""Room codes: four characters a person can read off a screen and type."""

import secrets

# No O/0 and no I/1. The code is read across a noisy room and typed on a phone.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
LENGTH = 4

# The dugout's workshop is a room like any other, so that a profile patch from
# the shout bar has somewhere to land. It is a real code rather than a
# sentinel; once the row exists, `generate` cannot hand it out again, because
# its `taken` predicate reads the table.
WORKSHOP = "WRKS"

_MAX_TRIES = 200


class CodesExhausted(Exception):
    """No free code turned up. The arena is holding far too many rooms."""


def generate(taken):
    """Return a fresh code. `taken(code)` answers whether one is already in use."""
    for _ in range(_MAX_TRIES):
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        if not taken(code):
            return code
    raise CodesExhausted(f"no free room code after {_MAX_TRIES} tries")


def is_valid(code):
    """True for a code this module could have produced."""
    return len(code) == LENGTH and all(character in ALPHABET for character in code)
