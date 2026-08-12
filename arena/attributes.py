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
    """A role's shipped attributes, as a fresh dict the caller may keep."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {', '.join(ROLES)}")
    if role not in _baselines:
        with open(BASELINE_DIR / f"{role}.json") as handle:
            _baselines[role] = json.load(handle)
    return dict(_baselines[role])


def validate(role, changes):
    """Return a list of reasons the write should be refused. Empty means fine."""
    if role not in ROLES:
        return [f"unknown role {role!r}, expected one of {', '.join(ROLES)}"]
    if not isinstance(changes, dict):
        return [f"changes must be an object, got {type(changes).__name__}"]

    baseline = baseline_for(role)
    problems = []
    for key, value in changes.items():
        if key not in baseline:
            problems.append(f"{key!r} is not an attribute of the {role}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{key} must be a number, got {value!r}")
            continue
        low, high = range_for(key, baseline[key])
        if not low <= value <= high:
            problems.append(f"{key}={value} is outside {low} to {high}")
    return problems
