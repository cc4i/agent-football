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

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

import coach

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


@router.post("/api-apps/agents/users/{user}/sessions")
async def open_session(user: str, request: Request):
    """Open an ADK session. The same rewrite Vite's dev proxy does.

    The path segment is the browser's spelling (agents), and COACH_APP is the
    server's. They are allowed to differ.
    """
    raw = await _body(request)
    async with _make_client(coach.COACH_URL, QUICK) as http:
        try:
            reply = await http.post(
                coach.session_path(user),
                content=raw, headers={"Content-Type": "application/json"})
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
    """
    raw = await _body(request)
    http = _make_client(coach.COACH_URL, PATIENT)
    try:
        upstream = await http.send(
            http.build_request("POST", "/run_sse", content=raw,
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
