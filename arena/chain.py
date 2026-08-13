"""Carrying one shout down the agent chain and back as a relay.

A shout is answered by three sequential language-model hops -- coach, captain,
then four specialists at once -- and takes tens of seconds. The HTTP request
that carried the words cannot wait for that, so it returns as soon as the words
are in the log and the chain runs on as a task, reporting itself on the room's
bus as it goes.

Relay traffic is not logged. What the squad *did* is logged, as `profile.patch`
entries naming the shout that caused them, and that is what scoring reads. What
the squad *said* on the way is a progress report for whoever is watching now.

Two limits sit in front of the chain. Per dugout, one shout may be going out
with one waiting behind it, so a manager cannot queue up a match's worth of
instructions in the first ten seconds. Across the venue a semaphore bounds how
many chains run at once, because the quota belongs to the venue rather than to
any one room, and a manager held there is told their place rather than watched
spinning.
"""

import asyncio
import json
import logging
import os

import coach
from bus import room_topic

logger = logging.getLogger(__name__)

ROLES = ("defender", "midfielder", "forward", "goalkeeper")
# The four `LlmAgent` names in `game/agents/specialist_agents`, which is how a
# streamed event says which branch of the relay it belongs to.
SPECIALISTS = {f"{role.capitalize()}Specialist": role for role in ROLES}

# How many chains may be talking to Gemini at once, across every room. Sized to
# the quota on the day rather than to the hardware.
LIMIT = int(os.environ.get("ARENA_CHAIN_LIMIT", "4"))

# The whole chain, from the moment it has a slot to the huddle. A match is
# three minutes long, so a shout that has not been answered inside this is of
# no further use to the manager who made it.
BUDGET_SECONDS = float(os.environ.get("ARENA_CHAIN_SECONDS", "150"))


class Busy(Exception):
    """This dugout is already one shout deep with another waiting."""


class _Seat:
    """What one dugout has going out, and what is waiting behind it."""

    __slots__ = ("task", "seq", "queued")

    def __init__(self):
        self.task = None
        self.seq = None
        self.queued = None

    @property
    def going_out(self):
        return self.task is not None and not self.task.done()


class Chain:
    """The venue's shouts in flight. One of these, on the app."""

    def __init__(self, bus, limit=None, budget=None, run=None):
        self._bus = bus
        self._slots = asyncio.Semaphore(LIMIT if limit is None else limit)
        self._budget = BUDGET_SECONDS if budget is None else budget
        # The only seam that knows the chain is reached over HTTP rather than
        # run in this process. Resolved per shout so a test can stand a squad
        # in for Gemini without having to build the app around it.
        self._run = run
        self._seats = {}
        self._waiting = 0

    def has_room(self, room_id, team):
        """Whether this dugout could take another shout right now.

        Asked before the words are written to the log, so a refused shout
        leaves no trace of having been half-accepted.
        """
        seat = self._seats.get((room_id, team))
        return not (seat and seat.going_out and seat.queued is not None)

    def caused_by(self, room_id, team):
        """The shout whose chain is running for this dugout, if one is.

        A specialist writes attributes through the same PATCH route a manager
        uses and has no way to name the instruction it is acting on. The arena
        knows: it is the shout it is at that moment carrying for that dugout.
        Deciding it here rather than trusting the caller also means nobody can
        claim a goal for a shout by saying so in a request body.
        """
        seat = self._seats.get((room_id, team))
        return seat.seq if seat and seat.going_out else None

    def submit(self, room, team, seq, text, actor):
        """Take a shout. Returns how many of this dugout's shouts are ahead.

        Never awaits, so nothing can slip between the check in `has_room` and
        the seat being claimed here.
        """
        key = (room["id"], team)
        seat = self._seats.setdefault(key, _Seat())
        if seat.going_out:
            if seat.queued is not None:
                raise Busy("give the squad a moment - two of your calls are still going out")
            seat.queued = (seq, text, actor)
            return 1
        self._begin(seat, room, team, seq, text, actor)
        return 0

    async def close(self):
        """Drop everything in flight. The room is going away with it."""
        going = [seat.task for seat in self._seats.values() if seat.going_out]
        for task in going:
            task.cancel()
        for task in going:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._seats.clear()

    def _begin(self, seat, room, team, seq, text, actor):
        seat.seq = seq
        seat.task = asyncio.create_task(self._carry(seat, room, team, seq, text, actor))

    async def _carry(self, seat, room, team, seq, text, actor):
        """One shout, start to finish, then whatever was waiting behind it."""
        try:
            await self._queue_then_talk(room, team, seq, text, actor)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A crash here would otherwise be swallowed by the task and leave
            # the seat looking occupied until the match ended.
            logger.exception("the chain for %s %s died", room["code"], team)
        finally:
            seat.task = None
            seat.seq = None
        if seat.queued is not None:
            waiting, seat.queued = seat.queued, None
            self._begin(seat, room, team, *waiting)

    async def _queue_then_talk(self, room, team, seq, text, actor):
        say = self._voice(room, team, seq)
        held = self._slots.locked()
        if held:
            self._waiting += 1
            say("relay.waiting", ahead=self._waiting)
        try:
            await self._slots.acquire()
        finally:
            if held:
                self._waiting -= 1
        try:
            await self._talk(room, team, seq, text, actor, say)
        finally:
            self._slots.release()

    def _voice(self, room, team, seq):
        """A publisher bound to one shout, so no caller has to repeat itself."""
        topic = room_topic(room["code"])

        def say(kind, **fields):
            self._bus.publish(topic, {"type": kind, "seq": seq, "team": team, **fields})

        return say

    async def _talk(self, room, team, seq, text, actor, say):
        answered = set()
        # The captain's JSON usually arrives last anyway, but who never
        # answered is only known once the stream closes. Holding it back makes
        # `relay.huddle` the one message that always ends a chain, whether the
        # chain finished, timed out or never reached the coach at all.
        spoken = {}
        say("relay.coach", state="thinking")
        # The keys `update_profile` and `restore_baseline_profiles` read. The
        # reason is the manager's own words, so the delta the other dugout sees
        # is attributed to the instruction rather than to "coach".
        state = {"room_code": room["code"], "team": team,
                 "actor": actor, "reason": text}
        run = self._run or coach.stream
        try:
            async with asyncio.timeout(self._budget):
                async for event in run(text, state):
                    self._read(event, say, answered, spoken)
        except (TimeoutError, asyncio.TimeoutError):
            say("relay.trouble", text="the squad ran out of time to answer")
        except coach.Unreachable as silence:
            say("relay.trouble", text=str(silence))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the chain for %s %s could not be read", room["code"], team)
            say("relay.trouble", text="the touchline lost the squad")

        # A specialist that never answered goes grey rather than pending. The
        # huddle completes on three: one quiet player is not a failed shout.
        for role in ROLES:
            if role not in answered:
                say("relay.specialist", role=role, state="missing")
        say("relay.huddle", state="done" if spoken else "failed",
            status=spoken.get("status", ""), huddle=spoken.get("huddle", {}))

    def _read(self, event, say, answered, spoken):
        """Turn one ADK event into whatever the relay should show for it."""
        if event.get("partial"):
            return
        trouble = event.get("errorMessage") or event.get("error")
        if trouble:
            # Not the coach's rung: the signal can die at any hop, and only the
            # phone knows which one it had reached when the wire went quiet.
            say("relay.trouble", text=str(trouble)[:200])
            return

        author = event.get("author") or ""
        actions = event.get("actions") or {}
        if isinstance(actions, dict) and actions.get("transferToAgent"):
            # The coach's only job on a shout is to hand it to the captain, so
            # the hand-off is the coach's rung completing.
            say("relay.coach", state="done")
            say("relay.captain", state="thinking")

        role = SPECIALISTS.get(author)
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            words = (part.get("text") or "").strip()
            if not words:
                continue
            said = _huddle(words)
            if said is not None:
                spoken.update(said)
                answered.update(said["huddle"])
                say("relay.captain", state="done")
            elif role:
                answered.add(role)
                say("relay.specialist", role=role, state="done", text=words[:120])


def _huddle(words):
    """The captain's final JSON, if that is what this is. None if it is not."""
    if not (words.startswith("{") and '"huddle"' in words):
        return None
    try:
        parsed = json.loads(words)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("huddle"), dict):
        return None
    lines = parsed["huddle"]
    return {"status": str(parsed.get("status") or "")[:120],
            "huddle": {role: str(lines[role])[:120] for role in ROLES if lines.get(role)}}
