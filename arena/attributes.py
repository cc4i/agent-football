"""The one validator for player profiles.

Every write goes through `validate`, whatever route it arrived by: a manager
typing in the shout bar, a specialist agent acting on that text, or a direct
PATCH. The rules used to live in two copies -- one beside the agents, one in
the dugout -- and two copies of a security check drift apart. This is the
survivor; the dugout's copy goes when the dugout moves onto the arena.

Deliberately free of third-party imports so it can be tested on its own, and
a role name is checked against ROLES before it is ever used as a filename.
"""

import json
from pathlib import Path

ROLES = ("defender", "midfielder", "forward", "goalkeeper")

BASELINE_DIR = Path(__file__).parent / "baselines"

# The attributes the simulation actually reads.
#
# `game.js` seeds both squads from `hardcodedDefaults`, and that object is the
# engine's entire vocabulary. The shipped baselines carry far more than this --
# `finishing`, `sweeperKeeper`, `forwardPassProbability` -- names that read as
# though they must do something and that nothing in the match ever looks at.
#
# Accepting one of those was the worst of both worlds: the write validated, the
# event log recorded it, the dugout drew the needle moving, and the football did
# not change. Measured over 50 shouts on the venue, every single one spent a
# write on `midfielder.forwardPassProbability`. It has never existed in the
# engine. So an attribute outside this set is now refused rather than stored,
# because a refusal an agent can correct beats a change that quietly means
# nothing.
#
# `tests/test_attributes.py` reads `hardcodedDefaults` out of `game.js` and
# checks it against these, so the day the engine learns a new attribute -- or
# stops reading one -- the suite says so instead of a measurement run finding
# it six weeks later.
_SIMULATED_OUTFIELD = frozenset({
    "aggression", "attackPositioning", "counterAttackUrgency", "decisionDelay",
    "defensePositioning", "dribbleTendency", "formationDiscipline",
    "foulProbability", "interceptionRadius", "passProbability", "passRange",
    "passRiskTolerance", "pressingIntensity", "recoverySpeedMultiplier",
    "shotPower", "shotRange", "speed", "supportRunFrequency", "tackleCooldown",
    "tackleRadius", "widthPreference",
})

# Only the keeper is read for these two: `updateGkAI` uses `trackingSpeed` for
# how fast it follows the ball and `diveChance` for whether it dives.
SIMULATED = {
    "defender": _SIMULATED_OUTFIELD,
    "midfielder": _SIMULATED_OUTFIELD,
    # `finishing` decides how near the post a shot is placed, in `aimAtGoal`.
    # It is the forward's alone, and it ships at 0.5 rather than at either end
    # so that a shout has somewhere to move it. Every other attacking lever the
    # squad owns was already at its ceiling, which is how a month of shouts
    # came to spend their most reliable write on a number that could not rise.
    "forward": _SIMULATED_OUTFIELD | frozenset({"finishing"}),
    "goalkeeper": _SIMULATED_OUTFIELD | frozenset({"diveChance", "trackingSpeed"}),
}

# Everything is a 0.0-1.0 weight except these three, which carry real units.
_EXPLICIT_RANGES = {
    "tackleCooldown": (100.0, 2000.0),
    "decisionDelay": (0.0, 500.0),
    "recoverySpeedMultiplier": (0.5, 2.0),
}
_UNIT_RANGE = (0.0, 1.0)

_baselines = {}


def range_for(attribute, baseline_value=None):
    """The band an attribute may move in.

    Most are 0.0-1.0 weights. A few carry real units, and the shipped files
    contain near-duplicates of those: the midfielder has both decisionDelay
    and decisionsDelay, the second holding 150 milliseconds. A hardcoded list
    of names will always miss the next one of those, so anything whose own
    baseline sits above 1.0 is taken to carry units and is allowed to move
    within twice it.
    """
    if attribute in _EXPLICIT_RANGES:
        return _EXPLICIT_RANGES[attribute]
    if isinstance(baseline_value, (int, float)) and not isinstance(
            baseline_value, bool) and baseline_value > _UNIT_RANGE[1]:
        return (0.0, float(baseline_value) * 2)
    return _UNIT_RANGE


def baseline_for(role):
    """A role's shipped attributes, as a fresh dict the caller may keep.

    The whole file, including the attributes the engine ignores: this is what a
    room is seeded with and what `reset` writes back, and dropping keys from a
    squad is not this function's business. `simulated_baseline_for` is the one
    to show anybody who is choosing what to change.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {', '.join(ROLES)}")
    if role not in _baselines:
        with open(BASELINE_DIR / f"{role}.json") as handle:
            _baselines[role] = json.load(handle)
    return dict(_baselines[role])


def simulated_baseline_for(role):
    """The shipped attributes of a role that the match actually reads.

    What the tuning panel offers and what a specialist is told it may write, so
    that nobody spends their one shout on a number with no effect.
    """
    return {key: value for key, value in baseline_for(role).items()
            if key in SIMULATED[role]}


def validate(role, changes):
    """Return a list of reasons the write should be refused. Empty means fine."""
    if role not in ROLES:
        return [f"unknown role {role!r}, expected one of {', '.join(ROLES)}"]
    if not isinstance(changes, dict):
        return [f"changes must be an object, got {type(changes).__name__}"]

    baseline = baseline_for(role)
    problems = []
    inert = False
    for key, value in changes.items():
        if key not in baseline:
            problems.append(f"{key!r} is not an attribute of the {role}")
            continue
        if key not in SIMULATED[role]:
            # Refused rather than dropped. A caller that had this silently
            # ignored would go on believing it had changed something.
            problems.append(f"{key!r} is not simulated and would change nothing")
            inert = True
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{key} must be a number, got {value!r}")
            continue
        low, high = range_for(key, baseline[key])
        if not low <= value <= high:
            problems.append(f"{key}={value} is outside {low} to {high}")
    if inert:
        # The caller is usually a language model correcting itself in one go,
        # so the refusal carries the list it should have chosen from.
        problems.append(f"the {role} is simulated on: "
                        f"{', '.join(sorted(SIMULATED[role]))}")
    return problems
