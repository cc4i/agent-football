"""One tuning tool per role.

Each subagent is given exactly one of these, so a subagent cannot move another
player's attributes even if it wants to. The tool name is also the actor
identity the trajectory renders, because the SDK exposes no subagent id.

The numbers themselves are the arena's to accept or refuse. What is checked
here is only what the arena has no opinion about: how much one call may move at
once, and whether the tuner said why it was moving it.
"""

import arena
import channel
from deltas import describe_change
from tools.match import CALLED

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
    if violations:
        return _refuse(role, violations)

    reason = reason.strip()
    try:
        # Read before the write, because the arena's reply says what the squad
        # is now and this is the last moment anything knows what it was.
        before = arena.read_profile(role)
        moved = arena.patch_profile(role, changes, f"{arena.ACTOR} {role}-tuner", reason)
    except arena.Refused as no:
        return _refuse(role, [str(no)])
    except arena.Down as gone:
        return _refuse(role, [str(gone)])

    # A shout moves these same attributes through the game's own agents, so the
    # quest can only tell the two routes apart by which tool did the writing.
    CALLED.add("tune")
    change = describe_change(role, before, moved["changed"], reason)
    result = {"ok": True, "role": role, "applied": moved["changed"],
              "reason": reason,
              "changed": [change] if change else []}
    channel.publish(f"tune_{role}", result)
    return result


def _refuse(role: str, violations: list[str]) -> dict:
    """Nothing moved, and why. Drawn under the role's own lane, in red."""
    refused = {"ok": False, "role": role, "violations": violations}
    channel.publish(f"tune_{role}", refused)
    return refused


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
