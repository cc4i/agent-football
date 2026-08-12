"""The four chips under the shout bar: an instruction with no chain behind it.

A preset is a shout the squad understands without a language model, which is
what makes a match playable while the coach is thinking, or unreachable. It is
recorded in the log exactly as a typed shout will be, so scoring never has to
know which of the two a manager used.

The patches themselves live in `presets/`; see `playbook` for what one is and
what it is allowed to touch.
"""

from pathlib import Path

import playbook

# The order the phone lays the chips out in, two by two.
NAMES = ("press high", "sit deep", "break wide", "shoot early")

Unknown = playbook.Unknown

_chips = playbook.Playbook(Path(__file__).parent / "presets", NAMES, "preset")

changes_for = _chips.changes_for
describe = _chips.describe
catalogue = _chips.catalogue
apply = _chips.apply
