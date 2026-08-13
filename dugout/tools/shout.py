"""Shouting to the bench, through the game's own agents.

Tuning picks numbers and asks the arena to store them. This does the opposite:
it says something in the manager's words and lets the game's own chain decide
what that means. That chain is the arena's coach -> the team captain over A2A
-> four player agents, and watching Antigravity set it going and report back is
the point of the stage.

The words go to the arena and the answers come back on the workshop room's
socket, which is the same feed the pitch is watching. Relay traffic is never
written to the event log -- it is a progress report, not a record -- so the
socket is opened before the shout rather than after.
"""

import asyncio

import arena
import channel
from deltas import describe_change
from tools.match import CALLED, read_status

# The arena gives a chain 150 seconds and always ends it with a huddle, even a
# failed one, so this is a backstop for a socket that has gone quiet rather
# than the thing that decides how long to wait.
WAIT_SECONDS = 180.0


async def shout_to_the_team(message: str) -> dict:
    """Shout an instruction to the players through the game's coach.

    Use this instead of tuning attributes when the manager wants the team told
    something: press, push up, sit deep, shoot on sight. The game's own agents
    decide what that means and move the squad themselves.

    Waits for the whole chain and returns every answer it heard, so call it
    once. Calling it again does not fetch the previous answers, it shouts
    again.

    Args:
      message: what to shout, in the manager's words.
    """
    CALLED.add("shout_to_the_team")
    words = " ".join(message.split())
    if not words:
        return {"error": "nothing to shout"}

    try:
        # The squad as it stands. The answers say what the players decided in
        # words; only this says what they did to the numbers.
        before = arena.read_profiles()
        async with arena.listening() as relay:
            said = await asyncio.to_thread(arena.shout, words)
            replies, patches, huddle = await _follow(relay, said["seq"])
    except (arena.Down, arena.Refused) as trouble:
        return {"error": "arena_unreachable", "detail": str(trouble)}

    result = {"shouted": words, "replies": replies,
              "changed": _changed(before, patches)}
    note = _note(huddle)
    if note:
        result["note"] = note
    channel.publish("shout_to_the_team", result)
    return result


async def _follow(relay, seq):
    """Everything this shout caused, until the huddle that always ends it.

    Returns what was said, what moved, and the huddle itself -- or no huddle at
    all if the wait ran out, which is the one case the arena cannot report on
    because it means the arena stopped talking.
    """
    replies, patches = [], []
    try:
        async with asyncio.timeout(WAIT_SECONDS):
            async for message in relay:
                kind = message.get("type", "")
                if kind == "event":
                    payload = message.get("payload") or {}
                    if (message.get("kind") == "profile.patch"
                            and payload.get("shout_seq") == seq):
                        patches.append(payload)
                    continue
                # Every other room's shouts come down this socket too, and so
                # do this room's earlier ones. Only ours is being reported on.
                if not kind.startswith("relay.") or message.get("seq") != seq:
                    continue
                replies.extend(_lines(message))
                if kind == "relay.huddle":
                    return replies, patches, message
    except (TimeoutError, asyncio.TimeoutError):
        pass
    return replies, patches, None


def _lines(message) -> list[str]:
    """One relay message as the manager would hear it. Empty if it is chatter."""
    kind = message["type"]
    state = message.get("state")
    if kind == "relay.coach":
        return ["Coach: relayed it to the captain over A2A"] if state == "done" else []
    if kind == "relay.captain":
        return ["Captain: briefing the four player agents"] if state == "thinking" else []
    if kind == "relay.specialist":
        role = message.get("role", "someone")
        if state == "missing":
            return [f"{role}: no answer"]
        return [f"{role}: {message.get('text', '')}"]
    if kind == "relay.trouble":
        return [f"Trouble: {message.get('text', '')}"]
    if kind == "relay.waiting":
        return [f"Waiting: {message.get('ahead')} shout(s) ahead of this one"]
    if kind == "relay.huddle":
        # The captain's own summary, which arrives after the players have
        # spoken and is the last word on what the shout became.
        status = message.get("status")
        return [f"Captain: {status}"] if status else []
    return []


def _changed(before: dict, patches: list) -> list:
    """What the players did to the squad, one entry per attribute they moved.

    A role missing from `before` is skipped rather than reported as new: there
    is nothing to measure the move against.
    """
    changed = []
    for patch in patches:
        role = patch.get("role")
        if role not in before:
            continue
        change = describe_change(role, before[role], patch.get("changed") or {},
                                 patch.get("reason"))
        if change:
            changed.append(change)
    return changed


def _note(huddle) -> str | None:
    """What to say about a shout that did not go the whole way, if anything."""
    if huddle is None:
        return (f"The arena stopped reporting before the squad had finished, "
                f"after {int(WAIT_SECONDS)}s. Report what came back; shouting "
                f"again will not fetch more.")
    if huddle.get("state") != "done":
        return ("The shout reached the arena but the squad never answered it. "
                "Check that the game's coach on :8000 and captain on :8001 are "
                "both up.")
    if "error" in read_status():
        # The squad moved either way -- the arena holds the profiles now -- but
        # in a lab with no match on screen there is nothing to see it happen to.
        return ("The squad has changed, but no match is on screen to show it. "
                "Take the field to watch the difference.")
    return None
