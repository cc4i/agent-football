"""One tuning tool per role.

Each subagent is given exactly one of these, so a subagent cannot write another
player's file even if it wants to. The tool name is also the actor identity the
trajectory renders, because the SDK exposes no subagent id.
"""

import json

from attributes import PLAYER_STATE_DIR, validate_changes

MAX_ATTRIBUTES_PER_CALL = 3


def _tune(role: str, changes: dict, reason: str) -> dict:
    violations = []
    if not isinstance(changes, dict) or not changes:
        violations.append("changes must be a non-empty object")
    elif len(changes) > MAX_ATTRIBUTES_PER_CALL:
        violations.append(
            f"change at most {MAX_ATTRIBUTES_PER_CALL} attributes per call, "
            f"got {len(changes)}")
    if not isinstance(reason, str) or not reason.strip():
        violations.append("a reason is required, so the change is legible")
    if not violations:
        violations = validate_changes(role, changes)
    if violations:
        return {"ok": False, "role": role, "violations": violations}

    path = PLAYER_STATE_DIR / f"{role}.json"
    profile = json.loads(path.read_text())
    profile.update(changes)
    path.write_text(json.dumps(profile, indent=2))
    return {"ok": True, "role": role, "applied": changes, "reason": reason.strip()}


def tune_defender(changes: dict, reason: str) -> dict:
    """Change up to 3 of the defender's attributes. Say why."""
    return _tune("defender", changes, reason)


def tune_midfielder(changes: dict, reason: str) -> dict:
    """Change up to 3 of the midfielder's attributes. Say why."""
    return _tune("midfielder", changes, reason)


def tune_forward(changes: dict, reason: str) -> dict:
    """Change up to 3 of the forward's attributes. Say why."""
    return _tune("forward", changes, reason)


def tune_goalkeeper(changes: dict, reason: str) -> dict:
    """Change up to 3 of the goalkeeper's attributes. Say why."""
    return _tune("goalkeeper", changes, reason)


TUNING_TOOL_BY_ROLE = {
    "defender": tune_defender,
    "midfielder": tune_midfielder,
    "forward": tune_forward,
    "goalkeeper": tune_goalkeeper,
}

ROLE_BY_TUNING_TOOL = {fn.__name__: role for role, fn in TUNING_TOOL_BY_ROLE.items()}
