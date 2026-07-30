"""Read-only views of the running match, for the agent."""

import json
from pathlib import Path

from attributes import PLAYER_STATE_DIR, ROLES, range_for

STATUS_FILE = Path("/tmp/futsal_status.json")

# Reading the game is not observable on disk, so the stage predicate needs the
# tools to say they ran. Reset by the app on a fresh session.
CALLED: set[str] = set()


def get_match_status() -> dict:
    """Return the live score and clock, or an error the agent can act on.

    The status file is written by the agent's own Playwright script, which polls
    window.__futsal.status(). No file means no match is being played.
    """
    CALLED.add("get_match_status")
    try:
        payload = json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"error": "game_not_running"}
    if not isinstance(payload, dict):
        return {"error": "game_not_running"}
    return payload


def read_player_stats(role: str | None = None) -> dict:
    """Return current attributes with the range each one must stay inside."""
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    CALLED.add("read_player_stats")
    wanted = (role,) if role else ROLES
    stats = {}
    for name in wanted:
        profile = json.loads((PLAYER_STATE_DIR / f"{name}.json").read_text())
        low_high = {k: range_for(k) for k in profile}
        stats[name] = {
            k: {"value": v, "min": low_high[k][0], "max": low_high[k][1]}
            for k, v in profile.items()
        }
    return stats
