"""The four opening stances a manager picks on the join form.

Applied to both dugouts at kick-off, before the room is announced live, so a
match never starts on the shipped baseline the manager did not choose.

The patches themselves live in `philosophies/`; see `playbook` for what one is
and what it is allowed to touch.
"""

from pathlib import Path

import playbook

# The join form's wording, in the order it shows them. The name is what a
# player sees and what the seat stores; the filename is the name with dashes.
NAMES = ("high press", "tiki-taka", "counter", "low block")

Unknown = playbook.Unknown

_stances = playbook.Playbook(Path(__file__).parent / "philosophies", NAMES, "philosophy")

changes_for = _stances.changes_for
describe = _stances.describe
catalogue = _stances.catalogue
apply = _stances.apply
