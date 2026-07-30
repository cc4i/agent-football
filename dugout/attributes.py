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


def range_for(attribute: str) -> tuple[float, float]:
    return _EXPLICIT_RANGES.get(attribute, _UNIT_RANGE)


def allowed_attributes(role: str) -> frozenset[str]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    baseline = PLAYER_STATE_DIR / f"{role}_baseline.json"
    return frozenset(json.loads(baseline.read_text()))


def validate_changes(role: str, changes: dict) -> list[str]:
    allowed = allowed_attributes(role)
    violations = []
    for key, value in changes.items():
        if key not in allowed:
            violations.append(f"{key!r} is not an attribute of the {role}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            violations.append(f"{key} must be a number, got {value!r}")
            continue
        low, high = range_for(key)
        if not low <= value <= high:
            violations.append(f"{key}={value} is outside {low} to {high}")
    return violations
