"""The dugout's way into the arena.

The squad used to be four JSON files beside the pitch: the tuners wrote them,
the shout tool watched them, and the page re-read them every couple of seconds.
They now belong to a room and a dugout in the arena, and this is how the
dugout's tools reach them.

Always room `WRKS`, always blue. That room is the workshop -- one squad in
front of a pitch that is always running, which is what the five stages need and
all they need. A venue full of phones plays their own matches in their own
rooms, and nothing here can touch those.

The arena is the only writer now. It holds the rules, decides what it will
accept, records who moved what and tells the pitch, so a tuner's change and a
shout's change land the same way and neither has to know how the other works.
"""

import json
import os
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
import websockets

DEFAULT_URL = "http://127.0.0.1:8003"
ROOM = "WRKS"
TEAM = "blue"

# Whose name goes on what the dugout does. The manager is in a chat window and
# the agent is the one on the touchline, so the arena's log says Antigravity.
ACTOR = "Antigravity"

# One call to a service on the same machine. Long enough to survive an arena
# busy with a venue, short enough that a tool fails rather than hangs a turn.
# Waiting for the squad to answer a shout is a separate budget entirely.
TIMEOUT_SECONDS = 10.0


class Down(Exception):
    """The arena could not be reached. The text is fit for a manager to read."""


class Refused(Exception):
    """The arena answered no, in its own words."""


def base_url() -> str:
    return os.environ.get("ARENA_URL", DEFAULT_URL).rstrip("/")


def socket_url() -> str:
    """The workshop's room socket, wherever the arena is."""
    scheme, _, host = base_url().partition("://")
    return f"{'wss' if scheme == 'https' else 'ws'}://{host}/ws/rooms/{ROOM}"


def rules() -> dict:
    """Every role's attributes, each with its shipped value and its two limits.

    The rules themselves, not this room's copy of them. The dugout used to keep
    its own second copy and validate against that; the arena is the one that
    decides, so the arena is the one that is asked.
    """
    return _call("GET", "/api/attributes")["roles"]


def read_profiles() -> dict:
    """The whole squad as the arena holds it: {role: {attribute: value}}."""
    return _call("GET", f"/api/rooms/{ROOM}/teams/{TEAM}/profiles")["profiles"]


def read_profile(role: str) -> dict:
    """One role's attributes as they stand."""
    return _call("GET", _role_path(role))["attributes"]


def patch_profile(role: str, changes: dict, actor: str, reason: str) -> dict:
    """Move one role's attributes. Returns only what actually moved.

    The arena validates and refuses with every reason at once, which is what
    `Refused` carries: the caller is a language model and can only correct what
    it is told.
    """
    return _call("PATCH", _role_path(role),
                 {"changes": changes, "actor": actor, "reason": reason})


def shout(text: str) -> dict:
    """Say something to the squad, in the manager's words.

    Returns as soon as the words are in the log, which is tens of seconds
    before the squad has answered them. The answers come over the socket.
    """
    return _call("POST", f"/api/rooms/{ROOM}/shout", {"text": text})


@asynccontextmanager
async def listening():
    """Hold the workshop's socket open and hand back everything said on it.

    Opened before the shout rather than after. The chain starts reporting the
    moment the words are logged, and relay traffic is never written to the
    event log -- it is a progress report for whoever is watching, not a record
    -- so a message missed here is a message gone.
    """
    try:
        socket = await websockets.connect(socket_url(), open_timeout=TIMEOUT_SECONDS)
    except (OSError, websockets.WebSocketException, TimeoutError) as silence:
        raise Down(f"the arena at {base_url()} did not answer ({silence})") from silence
    try:
        yield _heard(socket)
    finally:
        await socket.close()


async def _heard(socket):
    """Every message off the socket, parsed. Anything unreadable is skipped."""
    async for packet in socket:
        try:
            yield json.loads(packet)
        except ValueError:
            continue


def _role_path(role: str) -> str:
    # The role comes from a language model, so it is escaped rather than
    # trusted. The arena checks it too, but a URL path is not the place to find
    # out that it was never a role at all.
    return f"/api/rooms/{ROOM}/teams/{TEAM}/profiles/{quote(role, safe='')}"


# One client, made on first use and kept. The tools make several calls a turn
# and the arena is on the same machine, so there is no sense opening a
# connection per call. Sync on purpose: the SDK runs the tuning tools in
# threads, and httpx clients are safe to share between them.
_session = None


def _client():
    global _session
    if _session is None:
        _session = httpx.Client(timeout=TIMEOUT_SECONDS)
    return _session


def _call(method: str, path: str, body: dict | None = None) -> dict:
    """One call to the arena, or an exception a manager could read out loud."""
    headers = {}
    token = os.environ.get("ARENA_SERVICE_TOKEN", "")
    if token:
        headers["X-Arena-Service"] = token
    elif method != "GET":
        # Said here rather than letting the arena answer 401: its refusal is
        # written for a phone that has not joined, which is no help at all to
        # somebody who has forgotten to export a token.
        raise Refused("ARENA_SERVICE_TOKEN is unset, so the arena refuses "
                      "everything the dugout tries to write")
    try:
        answer = _client().request(method, base_url() + path,
                                   json=body, headers=headers)
    except httpx.HTTPError as silence:
        raise Down(f"the arena at {base_url()} did not answer ({silence})") from silence
    if answer.status_code >= 400:
        raise Refused(_reasons(answer))
    return answer.json()


def _reasons(answer) -> str:
    """The arena's own words for a refusal, or the status code if it gave none."""
    try:
        detail = answer.json()["detail"]
    except (ValueError, KeyError, TypeError):
        return f"the arena refused ({answer.status_code})"
    if isinstance(detail, dict) and "problems" in detail:
        return "; ".join(detail["problems"])
    return str(detail)
