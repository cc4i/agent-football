"""Read-only views of the running match, for the agent."""

import json
import time
from pathlib import Path

from attributes import PLAYER_STATE_DIR, ROLES, baseline_profile, range_for

STATUS_FILE = Path("/tmp/futsal_status.json")
STATUS_MAX_AGE_SEC = 15.0

# Reading the game is not observable on disk, so the stage predicate needs the
# tools to say they ran. Reset by the app on a fresh session.
CALLED: set[str] = set()


def status_is_fresh() -> bool:
    """A live match rewrites the status file constantly; a frozen one is dead."""
    try:
        return (time.time() - STATUS_FILE.stat().st_mtime) <= STATUS_MAX_AGE_SEC
    except OSError:
        return False


def read_status() -> dict:
    """The live score and clock, with no side effects.

    The header polls this several times a minute, so it must stay out of
    CALLED: the quest tracks what the agent did, not what the page asked for.
    """
    if not status_is_fresh():
        return {"error": "game_not_running"}
    try:
        payload = json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"error": "game_not_running"}
    if not isinstance(payload, dict):
        return {"error": "game_not_running"}
    return payload


def get_match_status() -> dict:
    """Return the live score and clock, or an error the agent can act on.

    The status file is written by the agent's own Playwright script, which polls
    window.__futsal.status(). No file means no match is being played.
    """
    CALLED.add("get_match_status")
    return read_status()


def read_player_stats(role: str | None = None) -> dict:
    """Return current attributes with the range each one must stay inside."""
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    CALLED.add("read_player_stats")
    wanted = (role,) if role else ROLES
    stats = {}
    for name in wanted:
        profile = json.loads((PLAYER_STATE_DIR / f"{name}.json").read_text())
        # Ranges come from the baseline, the same source tuning validates
        # against, so a tuner is never shown bounds its change would fail.
        baseline = baseline_profile(name)
        low_high = {k: range_for(k, baseline.get(k)) for k in profile}
        stats[name] = {
            k: {"value": v, "min": low_high[k][0], "max": low_high[k][1]}
            for k, v in profile.items()
        }
    return stats
