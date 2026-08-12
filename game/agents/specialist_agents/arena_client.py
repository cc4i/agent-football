"""The specialist agents' way into the arena.

Profiles used to be four JSON files next to the pitch, which meant every match
in the venue shared one defender. They now belong to a room and a dugout, and
this is how a tool reaches them.

Stdlib only, on purpose: the agent project's dependency list is already long
enough, and this is one request with one header.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8003"
# The workshop room, which the arena opens for itself at startup. A real match
# puts its own code in the agent's session state.
DEFAULT_ROOM = "WRKS"
DEFAULT_TEAM = "blue"
TIMEOUT_SECONDS = 5


class ArenaError(Exception):
    """The arena refused, or could not be reached. The text is fit for a manager."""


def base_url():
    return os.environ.get("ARENA_URL", DEFAULT_URL).rstrip("/")


def patch_profile(room, team, role, changes, actor, reason):
    """Move one profile in one dugout. Returns the arena's reply.

    The role and team come from a language model, so they are escaped rather
    than trusted -- the arena checks them too, but a path is not the place to
    find that out.
    """
    token = os.environ.get("ARENA_SERVICE_TOKEN", "")
    if not token:
        raise ArenaError(
            "ARENA_SERVICE_TOKEN is unset, so the arena refuses writes from the agents")

    path = "/api/rooms/{}/teams/{}/profiles/{}".format(
        *(urllib.parse.quote(part, safe="") for part in (room, team, role)))
    request = urllib.request.Request(
        base_url() + path,
        data=json.dumps({"changes": changes, "actor": actor, "reason": reason}).encode(),
        method="PATCH",
        headers={"Content-Type": "application/json", "X-Arena-Service": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as refusal:
        raise ArenaError(_reasons(refusal)) from refusal
    except (urllib.error.URLError, TimeoutError, OSError) as unreachable:
        raise ArenaError(
            f"the arena at {base_url()} did not answer ({unreachable})") from unreachable


def _reasons(refusal):
    """Pull the arena's own words out of an error reply, or fall back to the code."""
    try:
        detail = json.load(refusal)["detail"]
    except (ValueError, KeyError, TypeError):
        return f"the arena refused the change ({refusal.code})"
    if isinstance(detail, dict) and "problems" in detail:
        return "; ".join(detail["problems"])
    return str(detail)
