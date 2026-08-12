"""Temporary bridge: keep the pitch's JSON files current.

`game/frontend/src/main.js` still polls player_state/{role}.json every two
seconds. Until step 3 gives it a room socket, a profile patch has to land in
those files or the workshop demo stops answering the shout bar.

Single-tenant by construction -- one file per role, no room in the path --
which is why it is off unless ARENA_MIRROR_DIR is set, why only the workshop
room uses it, and why this whole module is deleted in step 3.
"""

import json
import logging
import os
from pathlib import Path

import attributes

logger = logging.getLogger(__name__)


def write(role, profile_attributes):
    """Copy one role's attributes to the pitch's file. Never raises.

    The environment is read on every call rather than at import, so a test can
    switch the bridge on without reloading the module.
    """
    directory = os.environ.get("ARENA_MIRROR_DIR", "")
    if not directory or role not in attributes.ROLES:
        return
    target = Path(directory) / f"{role}.json"
    try:
        # Write beside it and rename: the poller reads this file twice a
        # second and must never catch it half written.
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(profile_attributes, indent=2))
        temporary.replace(target)
    except OSError:
        # A convenience for one demo. Losing it must not fail a manager's patch.
        logger.warning("could not mirror the %s profile to %s", role, target)
