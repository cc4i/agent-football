"""The two calls the pitch makes to the coach, carried by the arena.

The browser used to reach the coach through Vite's dev proxy. Deployed there
is nothing to proxy through: the coach is a sidecar sharing this instance's
network namespace and listening only on loopback, which is what keeps an
unauthenticated ADK server off the public internet.

So the arena carries them, and carries exactly them. Two paths, POST only,
no prefix matching and no passthrough. Everything the ADK server exposes
besides these two is a way to read or replay somebody else's session, and
none of it should be reachable from a phone.

The other way in is coach.stream, server-side, for a phone that shouts at a
match it is not drawing. These two are for the pitch's own calls: the shout
box, the periodic status check, and the profile reset.
"""

import logging
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

import coach
import codes

logger = logging.getLogger(__name__)

router = APIRouter()

# The coach answers a session create at once. /run_sse is a language model
# chain and takes tens of seconds, so it gets the same idle budget one hop of
# the chain gets everywhere else.
QUICK = httpx.Timeout(10.0, connect=coach.CONNECT_SECONDS)
PATIENT = httpx.Timeout(coach.IDLE_SECONDS, connect=coach.CONNECT_SECONDS)

# A shout from a phone keyboard is short and a session body is smaller. Anything
# larger is not one of the two calls this proxy exists for.
MAX_BODY_BYTES = 64 * 1024

# The fence. Starlette has already percent-decoded the path parameter by the
# time a handler runs, so an encoded `..` or `?` arrives here spelled out and
# is refused by a class that has neither; nothing legitimate carries a percent
# sign, and the only values the pitch ever sends are `arena` and `user`. The
# belt is on the way back out, where `coach.session_path` encodes whatever gets
# through this.
_USER_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')

# What the coach's budget is keyed on: nothing. Deliberately constant, because
# there is nothing caller-shaped to key on. Both of these routes are called by
# the pitch behind a venue's one address, so a per-caller key falls back to that
# one address for every real caller; and where a key does exist it is mintable,
# since `POST /api/players` hands out sessions to anybody who asks. The instance
# is the honest unit, and this is how a bucket says so.
_COACH_KEY = "instance"

# The lab's sessions land in a different bucket from the arena's own coach
# chains. The arena uses coach.COACH_USER = "arena"; the lab gets its own.
LAB_USER = "lab"

# At most 8 text parts, each capped at 2000 characters. The lab sends one;
# the cap stops a caller forging an unbounded body.
MAX_TEXT_PARTS = 8
MAX_PART_LENGTH = 2000
MAX_SESSION_ID = 64


def _make_client(base_url, timeout):
    """Build an httpx client. Exists so tests can replace it with a fake."""
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)


async def _body(request):
    # Refuse on Content-Length before reading a byte. The check after the read
    # catches a chunked body that declares nothing.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_int = int(declared)
        except ValueError:
            # A Content-Length the caller made unparseable is not a length to
            # trust. Fall through to the post-read check where real bytes are
            # counted.
            pass
        else:
            if declared_int > MAX_BODY_BYTES:
                raise HTTPException(413, "that is too much to say to a coach")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "that is too much to say to a coach")
    return raw


def _allowlist_session_body(caller_body):
    """Rebuild the create-session body from an allowlist.

    Stops four vectors: state, events[].actions.state_delta, and two that do not
    exist yet but a version bump could add. The allowlist goes all the way down
    rather than patching one level, because the ADK's shape today is not forever.

    Returns only {"state": {"room_code": WRKS, "team": "red" | "blue"}}.
    Everything else dropped, including session_id (the ADK generates one) and
    every other state key including __session_metadata__.
    """
    team = "blue"
    if isinstance(caller_body, dict):
        state = caller_body.get("state")
        if isinstance(state, dict):
            caller_team = state.get("team")
            if caller_team == "red":
                team = "red"
            dropped = sum(1 for key in state
                         if key not in ("room_code", "team") and state[key] is not None)
            if dropped > 0:
                logger.warning("dropped %d non-null state keys from session create", dropped)
        if "events" in caller_body and caller_body["events"]:
            logger.warning("dropped events array from session create")
    return {"state": {"room_code": codes.WORKSHOP, "team": team}}


def _allowlist_run_body(caller_body):
    """Rebuild the /run_sse body from an allowlist.

    Stops stateDelta (vector 3) and non-text parts in newMessage (vector 4).
    A Part has fourteen kinds; fileData.fileUri is fetched by the service account
    and functionResponse forges a tool result. Neither is needed to shout at a
    squad, and neither is stopped by pinning the room.

    Returns appName (pinned), userId (pinned), sessionId (passed through),
    newMessage (rebuilt keeping only text parts, capped), and streaming (coerced).
    """
    session_id = ""
    parts = []
    streaming = False
    non_text_parts_dropped = 0

    if isinstance(caller_body, dict):
        raw_session_id = caller_body.get("sessionId")
        if isinstance(raw_session_id, str):
            session_id = raw_session_id[:MAX_SESSION_ID]
        streaming = bool(caller_body.get("streaming"))

        if (("stateDelta" in caller_body or "state_delta" in caller_body) and
            (caller_body.get("stateDelta") is not None or
             caller_body.get("state_delta") is not None)):
            logger.warning("dropped non-null stateDelta from /run_sse")

        if "functionCallEventId" in caller_body or "function_call_event_id" in caller_body:
            logger.warning("dropped functionCallEventId from /run_sse")
        if "invocationId" in caller_body or "invocation_id" in caller_body:
            logger.warning("dropped invocationId from /run_sse")

        new_message = caller_body.get("newMessage")
        if isinstance(new_message, dict):
            caller_parts = new_message.get("parts", [])
            if isinstance(caller_parts, list):
                for part in caller_parts[:MAX_TEXT_PARTS]:
                    if isinstance(part, dict) and "text" in part:
                        text = part["text"]
                        if isinstance(text, str) and text:
                            parts.append({"text": text[:MAX_PART_LENGTH]})
                    elif isinstance(part, dict):
                        non_text_parts_dropped += 1

    if non_text_parts_dropped > 0:
        logger.warning("dropped %d non-text parts from newMessage", non_text_parts_dropped)

    return {
        "appName": coach.COACH_APP,
        "userId": LAB_USER,
        "sessionId": session_id,
        "newMessage": {"role": "user", "parts": parts},
        "streaming": streaming
    }


@router.post("/api-apps/agents/users/{user}/sessions")
async def open_session(user: str, request: Request):
    """Open an ADK session. The same rewrite Vite's dev proxy does.

    The path segment is the browser's spelling (agents), and COACH_APP is the
    server's. They are allowed to differ.

    The caller's body is rebuilt from an allowlist, not forwarded. This stops
    a stranger naming any room and rewriting it with the service token. All
    sessions land on WRKS, which is created unranked and unreachable from both
    boards.
    """
    if not _USER_PATTERN.match(user):
        raise HTTPException(400, "invalid user segment")
    if not request.app.state.coach.take(_COACH_KEY):
        raise HTTPException(429, "slow down a moment and try that again")
    raw = await _body(request)
    try:
        caller_body = raw and len(raw) > 0 and __import__("json").loads(raw) or {}
    except Exception:
        caller_body = {}
    allowlisted = _allowlist_session_body(caller_body)
    async with _make_client(coach.COACH_URL, QUICK) as http:
        try:
            reply = await http.post(
                coach.session_path(LAB_USER),
                json=allowlisted, headers={"Content-Type": "application/json"})
        except httpx.InvalidURL as malformed:
            raise HTTPException(400, "malformed user segment") from malformed
        except httpx.HTTPError as silence:
            logger.warning("coach did not answer session request: %s", silence)
            raise HTTPException(502, "the coach did not answer") from silence
    return _passed_through(reply)


@router.post("/run_sse")
async def run(request: Request):
    """Carry a shout to the coach and stream the chain's events back.

    Streamed rather than buffered: the events are what light the relay on the
    pitch as each agent answers, and a chain takes tens of seconds. Holding
    them until the last one arrived would turn the whole spectacle into a
    spinner.

    The caller's body is rebuilt from an allowlist. This stops stateDelta
    (vector 3) and non-text parts in newMessage (vector 4). A Part has fourteen
    kinds; fileData.fileUri is fetched using the service account, and
    functionResponse forges a tool result. Neither is stopped by pinning the
    room.
    """
    if not request.app.state.coach.take(_COACH_KEY):
        raise HTTPException(429, "slow down a moment and try that again")
    raw = await _body(request)
    try:
        caller_body = raw and len(raw) > 0 and __import__("json").loads(raw) or {}
    except Exception:
        caller_body = {}
    allowlisted = _allowlist_run_body(caller_body)
    http = _make_client(coach.COACH_URL, PATIENT)
    try:
        upstream = await http.send(
            http.build_request("POST", "/run_sse", json=allowlisted,
                               headers={"Content-Type": "application/json"}),
            stream=True)
    except httpx.HTTPError as silence:
        await http.aclose()
        logger.warning("coach stopped answering /run_sse: %s", silence)
        raise HTTPException(502, "the coach stopped answering") from silence

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await http.aclose()

    return StreamingResponse(
        relay(), status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
        # Buffering an event stream anywhere between here and the tab would
        # hold every rung of the chain until the huddle.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _passed_through(reply):
    """The coach's own answer, with only the headers a browser should see."""
    return Response(content=reply.content, status_code=reply.status_code,
                    media_type=reply.headers.get("content-type", "application/json"))
