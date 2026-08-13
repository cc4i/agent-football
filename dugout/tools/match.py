"""Read-only views of the running match, for the agent."""

import json
import time
from pathlib import Path

import arena
import attributes
from attributes import ROLES

STATUS_FILE = Path("/tmp/futsal_status.json")
STATUS_MAX_AGE_SEC = 15.0

# Reading the game is not observable anywhere, so the stage predicate needs the
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

    The score is drawn on a canvas and the workshop pitch runs its own physics
    without reporting to the arena, so this is the one thing the dugout cannot
    ask the arena for. It comes from the agent's own Playwright script, which
    polls window.__futsal.status() and writes the file. No file means no match
    is being played.
    """
    CALLED.add("get_match_status")
    return read_status()


def read_player_stats(role: str | None = None) -> dict:
    """Return current attributes with the range each one must stay inside."""
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    wanted = (role,) if role else ROLES
    try:
        squad = arena.read_profiles()
        # The bands come from the arena too, so a tuner is never shown limits
        # that its own change would then be refused for breaking.
        stats = {name: _with_bands(name, squad.get(name, {})) for name in wanted}
    except (arena.Down, arena.Refused) as trouble:
        return {"error": "arena_unreachable", "detail": str(trouble)}

    # Recorded only once the squad has actually been read: an arena that is
    # down should not tick the stage off for a tool call that told the agent
    # nothing at all about the game.
    CALLED.add("read_player_stats")
    return stats


def _with_bands(role: str, profile: dict) -> dict:
    reported = {}
    for name, value in profile.items():
        limits = attributes.band(role, name)
        reported[name] = {"value": value, "min": limits["min"], "max": limits["max"]}
    return reported
