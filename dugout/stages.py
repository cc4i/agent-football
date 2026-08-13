"""The quest, as data. Predicates are pure functions over what this session did."""

import time
from dataclasses import dataclass
from typing import Callable

import attributes
from tools import match
from tools.avatars import SPRITE_DIR


# Stages describe this session's progress, not the repository's contents.
STARTED_AT = time.time()


def begin_session() -> None:
    """Start the quest over without restarting the server.

    Every predicate is relative to this moment, so moving it forward and
    forgetting which tools have run is the whole reset. The workshop is
    replayable only because this exists: otherwise a dugout left running
    keeps yesterday's sprites and yesterday's tuning marked as done, and the
    next person opens the page already halfway through a quest they have not
    started.
    """
    global STARTED_AT
    STARTED_AT = time.time()
    match.CALLED.clear()
    # A restarted arena is a different arena, and the rules are the first thing
    # the next tuner will ask for.
    attributes.forget()


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    blurb: str
    suggested: str
    is_done: Callable[[], bool]


def _rebranded() -> bool:
    """True once our own side has been re-kitted during this session.

    Blue alone: "kit us out" never touches the opponent, so waiting on red
    leaves the stage live after the manager has done exactly what was asked.
    The repository ships working sprites, so existence proves nothing. Only a
    write newer than process start means the manager actually rebranded.
    """
    for name in ("player_blue_team.png", "goalkeeper_blue_team.png"):
        sheet = SPRITE_DIR / name
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


def _shouted() -> bool:
    # A shout moves the same attributes tuning does, since the game's own
    # agents write them through the arena too. The tool recording itself is the
    # only thing that tells the two routes apart.
    return "shout_to_the_team" in match.CALLED


def _tuned() -> bool:
    """True once one of the dugout's own tuners has moved something.

    Not simply "the squad changed": a shout moves the very same attributes
    through the game's agents, and ticking this stage for that would claim the
    manager had used the subagents when they had not. Only a tune the arena
    accepted is recorded, so a refused one leaves the stage where it was.
    """
    return "tune" in match.CALLED


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
        blurb="Four subagents, one player each. The pitch shows the change as "
              "soon as the arena has taken it.",
        suggested="They keep breaking through the middle. Tighten it up.",
        is_done=_tuned,
    ),
    Stage(
        id="shout",
        title="Shout to the bench",
        blurb="The other way to change the team. Antigravity shouts into the "
              "match and the game's own coach, captain and four player agents "
              "work out what it means.",
        suggested="Tell the lads to push up and press high.",
        is_done=_shouted,
    ),
)


def stage_status() -> list[dict]:
    return [
        {"id": s.id, "title": s.title, "blurb": s.blurb,
         "suggested": s.suggested, "done": s.is_done()}
        for s in STAGES
    ]
