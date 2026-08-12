"""The four opening stances a manager picks on the join form.

A philosophy is nothing more exotic than a named profile patch applied to all
four roles, so it goes through `profiles.patch` and therefore through the same
validator as a shout or a direct PATCH. Nothing here may set an attribute a
manager could not have set by typing.

Every patch names only attributes all four roles share. A stance is a squad
instruction; one that quietly skipped the goalkeeper because it named a
forward-only attribute would be a stance in name only.
"""

import json
from pathlib import Path

import attributes
import profiles

PHILOSOPHY_DIR = Path(__file__).parent / "philosophies"

# The join form's wording, in the order it shows them. The slug is the filename;
# the name is what a player sees and what the seat stores.
NAMES = ("high press", "tiki-taka", "counter", "low block")

_loaded = {}


class Unknown(Exception):
    """A philosophy nobody ships. The text names the offender."""


def _slug(name):
    return name.replace(" ", "-")


def _load(name):
    if name not in NAMES:
        raise Unknown(f"there is no {name!r} philosophy")
    if name not in _loaded:
        with open(PHILOSOPHY_DIR / f"{_slug(name)}.json") as handle:
            _loaded[name] = json.load(handle)
    return _loaded[name]


def changes_for(name):
    """The attribute moves this stance makes, as a dict the caller may keep."""
    return dict(_load(name)["changes"])


def describe(name):
    """Label and one-line blurb, for the join form."""
    stance = _load(name)
    return {"name": name, "label": stance["label"], "blurb": stance["blurb"]}


def catalogue():
    return [describe(name) for name in NAMES]


def apply(conn, room_id, team, name):
    """Patch all four of this dugout's roles. Returns one result per role.

    Each result is what `profiles.patch` returned, so `changed` is empty for a
    role that already sat where the stance wanted it.
    """
    changes = changes_for(name)
    return [profiles.patch(conn, room_id, team, role, changes)
            for role in attributes.ROLES]
