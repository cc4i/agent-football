"""Validation for agent-driven profile writes.

The specialist agents are reached through the coach shout bar, so everything
they pass to update_profile originates in a language model acting on text a
manager typed. That is the whole point of the feature, and it is also why the
write has to be checked rather than trusted.

Deliberately free of third-party imports so it can be tested on its own.
The same rules live in dugout/attributes.py, which guards the other route
into these files.
"""

import json
import os

ROLES = ("defender", "midfielder", "forward", "goalkeeper")

# Everything is a 0.0-1.0 weight except these three, which carry real units.
_EXPLICIT_RANGES = {
    "tackleCooldown": (100.0, 2000.0),
    "decisionDelay": (0.0, 500.0),
    "recoverySpeedMultiplier": (0.5, 2.0),
}
_UNIT_RANGE = (0.0, 1.0)


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


def baseline_profile(state_dir, role):
    """A role's attributes and their shipped values, from its own baseline."""
    baseline = os.path.join(state_dir, f"{role}_baseline.json")
    source = baseline if os.path.exists(baseline) else os.path.join(
        state_dir, f"{role}.json")
    with open(source) as handle:
        return json.load(handle)


def allowed_attributes(state_dir, role):
    return frozenset(baseline_profile(state_dir, role))


def validate(state_dir, role, changes):
    """Return a list of reasons the write should be refused. Empty means fine."""
    if role not in ROLES:
        # Also stops role being used to walk out of the directory, since it is
        # interpolated straight into a filename.
        return [f"unknown role {role!r}, expected one of {', '.join(ROLES)}"]
    if not isinstance(changes, dict):
        return [f"changes must be an object, got {type(changes).__name__}"]

    baseline = baseline_profile(state_dir, role)
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
