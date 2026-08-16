"""Slowing the house side, when a manager asks for it in so many words.

A manager who tells their squad to quietly nobble the opposition gets what they
asked for. The words are scored by `intent`; this module is what happens when
the score is high enough.

Why slowing, and why these numbers. Measured on the venue, 24 matches paired
against an untouched control in the same wave:

    control                     1-4-1   blue 0.50/match   red 0.50   blue 16.5 shots
    forward .70 mid .65 def .60 5-1-0   blue 1.17/match   red 0.00   blue 17.7
    forward .55 mid .50 def .45 5-1-0   blue 1.67/match   red 0.00   blue 21.8
    forward .40 mid .35 def .30 5-1-0   blue 2.33/match   red 0.00   blue 29.5

All three win five in six and lose none, so the choice between them is about
what the match looks like rather than who wins it. The first one's scorelines
were 1-0, 1-0, 2-0, 1-0, 0-0, 2-0, which reads as football. The last one puts
blue on thirty shots and 3-0 becomes ordinary, which is the same problem as
0-0 in the other direction: nobody believes it and nobody enjoys it.

Pace was not the first guess. Halving red's `interceptionRadius` and cutting
its forward's `shotRange` did nothing measurable at all - blue's shots sat at
14 against a control's 15 - while the full nerf tripled them. The response to
this squad is a cliff rather than a ramp, and pace is where the cliff is.
"""

import logging

import profiles

logger = logging.getLogger(__name__)

# Red ships forward 0.95, midfielder 0.85, defender 0.8. The keeper is left
# alone: it is not what decides this, and a keeper wandering out of its goal is
# the one change a room would actually notice.
SLOWED = {
    "forward": {"speed": 0.7},
    "midfielder": {"speed": 0.65},
    "defender": {"speed": 0.6},
}

# What the log calls it. Deliberately dull: the manager asked for this quietly,
# and a line reading "sabotage" on the big screen would give it away.
ACTOR = "conditioning"
REASON = "the opposition are tiring"


def slow_the_opposition(conn, room_id, team):
    """Write the slowed squad over one dugout. Returns `patch`-shaped results.

    Only the roles that actually moved come back, so a second call on a squad
    already slowed reports nothing and writes nothing.
    """
    done = []
    for role, changes in SLOWED.items():
        try:
            result = profiles.patch(conn, room_id, team, role, changes)
        except profiles.Rejected as refused:
            # The validator is the authority on what may be written, and these
            # are all plain speed weights, so this should be unreachable.
            logger.error("the slowed squad was refused for %s: %s", role, refused)
            continue
        if result["changed"]:
            done.append(result)
    return done


def other_dugout(team):
    """The dugout that is not this one."""
    return "red" if team == "blue" else "blue"
