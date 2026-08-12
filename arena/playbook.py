"""Named profile patches, read from a directory of JSON files.

A stance picked on the join form and a chip tapped mid-match are the same
object: a set of attribute moves with a name on it, applied to all four roles
through `profiles.patch` and therefore through the same validator as a direct
PATCH. Nothing loaded here can set an attribute a manager could not have set by
typing it, which is why these ship as data rather than as code.

Every patch names only attributes all four roles share. These are squad
instructions; one that quietly skipped the goalkeeper because it named a
forward-only attribute would be an instruction in name only.
"""

import json
from pathlib import Path

import attributes
import profiles


class Unknown(Exception):
    """A name nobody ships. The text names the offender."""


class Playbook:
    """One directory of named patches, read on first use and then kept."""

    def __init__(self, directory, names, noun):
        self.directory = Path(directory)
        # Order matters: it is the order the phone lays the buttons out in.
        self.names = tuple(names)
        self.noun = noun
        self._loaded = {}

    def _load(self, name):
        if name not in self.names:
            raise Unknown(f"there is no {name!r} {self.noun}")
        if name not in self._loaded:
            with open(self.directory / f"{name.replace(' ', '-')}.json") as handle:
                self._loaded[name] = json.load(handle)
        return self._loaded[name]

    def changes_for(self, name):
        """The attribute moves this entry makes, as a dict the caller may keep."""
        return dict(self._load(name)["changes"])

    def describe(self, name):
        """Everything about the entry except the patch: what a screen renders."""
        entry = self._load(name)
        return {"name": name,
                **{key: value for key, value in entry.items() if key != "changes"}}

    def catalogue(self):
        return [self.describe(name) for name in self.names]

    def apply(self, conn, room_id, team, name):
        """Patch all four of this dugout's roles. Returns one result per role.

        Each result is what `profiles.patch` returned, so `changed` is empty for
        a role that already sat where the instruction wanted it.
        """
        changes = self.changes_for(name)
        return [profiles.patch(conn, room_id, team, role, changes)
                for role in attributes.ROLES]
