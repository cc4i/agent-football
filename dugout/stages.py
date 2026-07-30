"""The quest, as data. Predicates are pure functions over filesystem state."""

import time
from dataclasses import dataclass
from typing import Callable

from attributes import PLAYER_STATE_DIR, ROLES
from tools import match
from tools.avatars import SPRITE_DIR


# Stages describe this session's progress, not the repository's contents.
STARTED_AT = time.time()


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    blurb: str
    suggested: str
    is_done: Callable[[], bool]


def _rebranded() -> bool:
    """True once both sprite sheets have been rewritten during this session.

    The repository ships working sprites, so existence proves nothing. Only a
    write newer than process start means the manager actually rebranded.
    """
    for team in ("blue", "red"):
        sheet = SPRITE_DIR / f"player_{team}_team.png"
        if not sheet.exists() or sheet.stat().st_mtime < STARTED_AT:
            return False
    return True


def _on_the_field() -> bool:
    """True only while a match is actually being played right now.

    A status file left over from an earlier run would otherwise make this
    stage look complete before the agent had done anything.
    """
    return match.status_is_fresh() and "error" not in match.get_match_status()


def _scouted() -> bool:
    # Reading the game leaves no trace on disk, so the tool records its own use.
    return "read_player_stats" in match.CALLED


def _tuned() -> bool:
    """True once a role file has been rewritten during this session.

    Comparing against the baseline does not work: the repository ships live
    files that already differ from their baselines for three of the four
    roles, so a content diff reads as done before anything has happened.
    """
    for role in ROLES:
        live = PLAYER_STATE_DIR / f"{role}.json"
        if live.exists() and live.stat().st_mtime >= STARTED_AT:
            return True
    return False


STAGES = (
    Stage(
        id="rebrand",
        title="Rebrand the team",
        blurb="Generate new player sprites and put your own crest on the shirt.",
        suggested="Kit us out in black and gold with a wolf crest.",
        is_done=_rebranded,
    ),
    Stage(
        id="take_the_field",
        title="Take the field",
        blurb="Antigravity writes its own Playwright script and starts a match.",
        suggested="Now get us on the pitch and keep the score where you can see it.",
        is_done=_on_the_field,
    ),
    Stage(
        id="read_the_game",
        title="Read the game",
        blurb="Read the live score and the squad's current attributes.",
        suggested="How are we doing, and where are we losing it?",
        is_done=_scouted,
    ),
    Stage(
        id="tune_the_squad",
        title="Tune the squad",
        blurb="Four subagents, one player file each. Changes land within two seconds.",
        suggested="They keep breaking through the middle. Tighten it up.",
        is_done=_tuned,
    ),
)


def stage_status() -> list[dict]:
    return [
        {"id": s.id, "title": s.title, "blurb": s.blurb,
         "suggested": s.suggested, "done": s.is_done()}
        for s in STAGES
    ]
