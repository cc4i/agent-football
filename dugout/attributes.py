"""Per-role attribute allowlist and ranges, derived from the game's baselines."""

import json
from pathlib import Path

ROLES = ("defender", "midfielder", "forward", "goalkeeper")

PLAYER_STATE_DIR = (
    Path(__file__).resolve().parent.parent
    / "game" / "frontend" / "public" / "player_state"
)

# Everything is a 0.0-1.0 weight except these three, which carry real units.
_EXPLICIT_RANGES = {
    "tackleCooldown": (100.0, 2000.0),
    "decisionDelay": (0.0, 500.0),
    "recoverySpeedMultiplier": (0.5, 2.0),
}
_UNIT_RANGE = (0.0, 1.0)


def range_for(attribute: str, baseline_value: float | None = None
              ) -> tuple[float, float]:
    """The band an attribute may move in.

    The shipped files hold near-duplicates of the unit-bearing attributes: the
    midfielder has decisionsDelay=150 next to decisionDelay. Read as a weight
    that makes the game's own current state illegal, so anything whose
    baseline is above 1.0 is taken to carry units.
    """
    if attribute in _EXPLICIT_RANGES:
        return _EXPLICIT_RANGES[attribute]
    if (isinstance(baseline_value, (int, float))
            and not isinstance(baseline_value, bool)
            and baseline_value > _UNIT_RANGE[1]):
        return (0.0, float(baseline_value) * 2)
    return _UNIT_RANGE


def baseline_profile(role: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    return json.loads((PLAYER_STATE_DIR / f"{role}_baseline.json").read_text())


def allowed_attributes(role: str) -> frozenset[str]:
    return frozenset(baseline_profile(role))


def validate_changes(role: str, changes: dict) -> list[str]:
    baseline = baseline_profile(role)
    violations = []
    for key, value in changes.items():
        if key not in baseline:
            violations.append(f"{key!r} is not an attribute of the {role}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            violations.append(f"{key} must be a number, got {value!r}")
            continue
        low, high = range_for(key, baseline[key])
        if not low <= value <= high:
            violations.append(f"{key}={value} is outside {low} to {high}")
    return violations
