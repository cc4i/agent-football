"""The arena's way into the agent chain.

The browser used to open the ADK session and read the stream itself, which is
why a shout could only come from the machine rendering the pitch. It moves here
so that a phone can shout at a match it is not drawing, and so the room's log
and the room's relay come from the same place.

This is the same two calls the frontend made -- create a session, post to
`/run_sse`, read server-sent events -- with the room and the dugout put into
session state where `update_profile` already looks for them.
"""

import json
import logging
import os
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# The ADK server, and the application name `adk web .` registers for the
# `agents` package beside it.
COACH_URL = os.environ.get("ARENA_COACH_URL", "http://127.0.0.1:8000").rstrip("/")
COACH_APP = os.environ.get("ARENA_COACH_APP", "agents")

# One ADK user for the whole venue. Sessions are per shout and carry the room
# and the dugout in their state, so the user id distinguishes nothing.
COACH_USER = "arena"

# A local server either answers the handshake at once or is not running.
CONNECT_SECONDS = 5.0
# How long one hop of the chain may go quiet. The specialists are parallel and
# the slowest of them sets this; the whole-chain budget lives in `chain`.
IDLE_SECONDS = float(os.environ.get("ARENA_COACH_IDLE_SECONDS", "90"))


def session_path(user):
    """The ADK path for opening a session for a given user.

    The user segment is percent-encoded to prevent path rewriting via
    dot-segment removal or query string injection. Dots are explicitly
    encoded because quote() treats them as unreserved.
    """
    encoded = quote(user, safe='').replace('.', '%2E')
    return f"/apps/{COACH_APP}/users/{encoded}/sessions"


class Unreachable(Exception):
    """The coach did not answer. The text is fit to show a manager."""


async def stream(text, state):
    """Say `text` to the coach in a session holding `state`; yield ADK events.

    A session per shout, not per seat. Reusing one session for a whole match
    grows the coach's history and the captain's A2A context with every turn,
    and flash-lite starts failing on the bloated context -- measured at 2 of 8
    huddles reused against 6 of 6 fresh. Shouts are independent instructions,
    so the saving would buy one local HTTP round trip at the price of the
    chain working at all.
    """
    timeout = httpx.Timeout(IDLE_SECONDS, connect=CONNECT_SECONDS)
    async with httpx.AsyncClient(base_url=COACH_URL, timeout=timeout) as http:
        session = await _open_session(http, state)
        body = {
            "appName": COACH_APP,
            "userId": COACH_USER,
            "sessionId": session,
            "newMessage": {"role": "user", "parts": [{"text": text}]},
            # Token-level partials would arrive as fragments of the quips the
            # relay quotes. The chain still streams: each agent's event is sent
            # as that agent finishes, which is what lights the branches.
            "streaming": False,
        }
        try:
            async with http.stream("POST", "/run_sse", json=body) as reply:
                if reply.status_code != 200:
                    await reply.aread()
                    raise Unreachable(f"the coach refused the shout ({reply.status_code})")
                async for event in _events(reply):
                    yield event
        except httpx.HTTPError as silence:
            raise Unreachable(f"the coach at {COACH_URL} stopped answering") from silence


async def _open_session(http, state):
    """Open an ADK session carrying the room and dugout, and return its id."""
    try:
        reply = await http.post(session_path(COACH_USER), json={"state": state})
    except httpx.HTTPError as silence:
        raise Unreachable(f"the coach at {COACH_URL} did not answer") from silence
    if reply.status_code >= 400:
        raise Unreachable(f"the coach would not open a session ({reply.status_code})")
    try:
        return reply.json()["id"]
    except (ValueError, KeyError, TypeError) as nonsense:
        raise Unreachable("the coach opened a session it would not name") from nonsense


async def _events(reply):
    """The `data:` frames of an SSE body, parsed.

    A frame that is not JSON is dropped rather than raised on: the stream is a
    language model's output at one remove, and one bad frame is not a reason to
    lose the four good ones behind it.
    """
    async for line in reply.aiter_lines():
        if not line.startswith("data:"):
            continue
        frame = line[len("data:"):].strip()
        if not frame:
            continue
        try:
            parsed = json.loads(frame)
        except ValueError:
            logger.debug("dropped an unparseable frame from the coach")
            continue
        if isinstance(parsed, dict):
            yield parsed
