"""Arena: rooms, seats and the live match bus.

Runs on :8003 beside the pitch (:5173), the coach (:8000), the captain (:8001)
and the dugout (:8002). It owns everything that used to be global -- who is
playing, which match they are in, and what happened in it -- so that more than
one person can play at once.
"""

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager, contextmanager

from fastapi import (Depends, FastAPI, HTTPException, Request, Response, WebSocket,
                     WebSocketDisconnect)
from pydantic import BaseModel, Field, field_validator
from uvicorn.protocols.utils import ClientDisconnected

import codes
import db
import identity
import mirror
import philosophies
import profiles
import rooms
from bus import WALL, Bus, room_topic

logger = logging.getLogger(__name__)

# A state frame carries a score, a clock and twelve positions; an event frame
# carries less. Six figures is generous for both and still small enough that a
# host cannot flood the wall's queues with one message.
MAX_PAYLOAD_BYTES = 102400


class _RedactClientId(logging.Filter):
    """Redact client_id from uvicorn access logs.

    The host token became a bearer credential when /start started minting it,
    but it stayed in the query string where browsers can reach it. Anyone with
    the arena's stdout or a proxy log could seize physics for any live match.
    """
    def filter(self, record):
        if hasattr(record, 'args') and record.args:
            # uvicorn access logs use %-formatting with a tuple of args.
            # The message is typically: '"method path" status_code'
            if isinstance(record.args, tuple) and len(record.args) >= 1:
                args = list(record.args)
                args[0] = re.sub(r'client_id=[^&\s"]+', 'client_id=***', args[0])
                record.args = tuple(args)
        if hasattr(record, 'msg'):
            record.msg = re.sub(r'client_id=[^&\s"]+', 'client_id=***', record.msg)
        return True


# EMAIL_SALT keeps its literal default: if it randomised, every email hash would
# change on restart and players would lose their history. SESSION_SECRET must never
# fall back to a public literal, even if that means sessions don't survive a restart.
EMAIL_SALT = os.environ.get("ARENA_EMAIL_SALT", "arena-dev-salt")
if "ARENA_SECRET" in os.environ:
    SESSION_SECRET = os.environ["ARENA_SECRET"]
else:
    SESSION_SECRET = secrets.token_urlsafe(32)
    logger.warning("ARENA_SECRET unset; sessions will not survive a restart")
COOKIE = "arena_session"

# The specialist agents run in another process with no phone and no cookie, so
# they carry a shared secret instead. Unset means they are refused: an unset
# secret must authenticate nobody rather than everybody.
SERVICE_TOKEN = os.environ.get("ARENA_SERVICE_TOKEN", "")
if not SERVICE_TOKEN:
    logger.warning("ARENA_SERVICE_TOKEN unset; server-side profile writes are refused")

# A role has fewer than fifty attributes. Anything larger is a mistake or an
# attempt to make the validator do work, and it is cheaper to refuse it here.
MAX_CHANGES = 64


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    connection = db.connect(os.environ.get("ARENA_DB", db.DB_PATH))
    db.init_db(connection)
    # The workshop is where the dugout tunes profiles with nobody in a dugout
    # seat, so it is opened here rather than by a phone.
    if rooms.by_code(connection, codes.WORKSHOP) is None:
        rooms.create_room(connection, "solo", codes.WORKSHOP)
    fastapi_app.state.conn = connection
    fastapi_app.state.bus = Bus()
    # Install filter to redact client_id from access logs.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(_RedactClientId())
    yield
    connection.close()


app = FastAPI(title="Arena", lifespan=lifespan)


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    # RFC 5321 caps email addresses at 254 octets.
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def looks_like_an_address(cls, value):
        local, at_sign, domain = value.strip().partition("@")
        if not (local and at_sign and "." in domain):
            raise ValueError("that does not look like an email address")
        return value.strip()


class RoomRequest(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value):
        if value not in rooms.MODES:
            raise ValueError(f"mode must be one of {', '.join(rooms.MODES)}")
        return value


class SeatRequest(BaseModel):
    philosophy: str

    @field_validator("philosophy")
    @classmethod
    def known_philosophy(cls, value):
        if value not in rooms.PHILOSOPHIES:
            raise ValueError(f"philosophy must be one of {', '.join(rooms.PHILOSOPHIES)}")
        return value


class ReadyRequest(BaseModel):
    ready: bool


class ProfilePatchRequest(BaseModel):
    changes: dict = Field(default_factory=dict)
    # Both are shown to the other manager, so they are bounded and never trusted.
    reason: str = Field(default="", max_length=280)
    actor: str = Field(default="manager", max_length=40)

    @field_validator("changes")
    @classmethod
    def not_too_many(cls, value):
        if len(value) > MAX_CHANGES:
            raise ValueError(f"a patch may name at most {MAX_CHANGES} attributes")
        return value


async def current_player(request: Request) -> int:
    """The player id in the session cookie, or a 401."""
    player_id = identity.verify_token(request.cookies.get(COOKIE), SESSION_SECRET)
    if player_id is None or rooms.get_player(request.app.state.conn, player_id) is None:
        raise HTTPException(401, "join first -- your phone has no session")
    return player_id


@app.get("/health")
async def health():
    return {"ok": True, "service": "arena"}


@app.post("/api/players")
async def join(body: JoinRequest, request: Request, response: Response):
    """Name plus email in, session cookie out. This is the whole of identity."""
    connection = request.app.state.conn
    player_id = rooms.create_player(connection, body.display_name, body.email, EMAIL_SALT)
    response.set_cookie(
        COOKIE,
        identity.sign_token(player_id, SESSION_SECRET),
        httponly=True,
        samesite="lax",
    )
    player = rooms.get_player(connection, player_id)
    return {"id": player_id,
            "display_name": player["display_name"],
            "email": player["email_masked"]}


@app.post("/api/rooms")
async def open_room(body: RoomRequest, request: Request):
    connection = request.app.state.conn
    try:
        with _rules():
            room = rooms.create_room(connection, body.mode)
    except codes.CodesExhausted as problem:
        raise HTTPException(503, str(problem)) from problem
    return rooms.snapshot(connection, room["id"])


@app.get("/api/rooms/{code}")
async def read_room(code: str, request: Request):
    connection, room = _profile_room(request, code)
    return rooms.snapshot(connection, room["id"])


@app.post("/api/rooms/{code}/seats/{team}")
async def sit_down(code: str, team: str, body: SeatRequest, request: Request,
                   player_id: int = Depends(current_player)):
    connection, room = _profile_room(request, code)
    with _rules():
        rooms.take_seat(connection, room["id"], team, player_id, body.philosophy)
    return _announce(request.app, room)


@app.post("/api/rooms/{code}/seats/{team}/ready")
async def set_ready(code: str, team: str, body: ReadyRequest, request: Request,
                    player_id: int = Depends(current_player)):
    connection, room = _profile_room(request, code)
    _require_own_seat(connection, room["id"], team, player_id)
    with _rules():
        rooms.set_ready(connection, room["id"], team, body.ready)
    return _announce(request.app, room)


@app.get("/api/philosophies")
async def read_philosophies():
    """The four opening stances, for the join form to render."""
    return {"philosophies": philosophies.catalogue()}


@app.post("/api/rooms/{code}/start")
async def start(code: str, request: Request, player_id: int = Depends(current_player)):
    """Kick off. Whoever calls this holds physics for the whole match."""
    connection, room = _profile_room(request, code)
    _require_seated(connection, room["id"], player_id)
    host_token = secrets.token_urlsafe(16)
    with _rules():
        rooms.start_match(connection, room["id"], host_token)

    # Stances land before the room is announced live. A client that sees "live"
    # and then asks for profiles must never be able to read them mid-application.
    for team, seat in rooms.snapshot(connection, room["id"])["seats"].items():
        for result in philosophies.apply(connection, room["id"], team, seat["philosophy"]):
            _record_patch(request.app, connection, room, team, result,
                          reason=seat["philosophy"], actor="kick-off")

    snapshot = _announce(request.app, room)
    request.app.state.bus.publish(WALL, {"type": "wall", "rooms": rooms.live(connection)})
    return {**snapshot, "host_token": host_token}


@app.get("/api/rooms/{code}/teams/{team}/profiles")
async def read_profiles(code: str, team: str, request: Request):
    """Both managers and the pitch read these, so they need no session."""
    connection, room = _profile_room(request, code)
    _known_team(team)
    return {"team": team, "profiles": profiles.read_all(connection, room["id"], team)}


@app.get("/api/rooms/{code}/teams/{team}/profiles/{role}")
async def read_profile(code: str, team: str, role: str, request: Request):
    connection, room = _profile_room(request, code)
    _known_team(team)
    found = profiles.read_one(connection, room["id"], team, role)
    if found is None:
        raise HTTPException(404, f"this room has no {team} {role}")
    return {"team": team, "role": role, "attributes": found}


@app.patch("/api/rooms/{code}/teams/{team}/profiles/{role}")
async def patch_profile(code: str, team: str, role: str, body: ProfilePatchRequest,
                        request: Request):
    """Move one role's attributes, and tell the room it happened.

    Async because it publishes: a sync route runs in a threadpool, and waking
    a waiting consumer from another thread is not safe.
    """
    connection, room = _profile_room(request, code)
    _known_team(team)
    if room["status"] not in ("lobby", "live"):
        raise HTTPException(409, "that match is over")
    _require_profile_writer(request, connection, room["id"], team)

    try:
        result = profiles.patch(connection, room["id"], team, role, body.changes)
    except profiles.Rejected as refusal:
        raise HTTPException(422, {"problems": refusal.problems}) from refusal

    if room["code"] == codes.WORKSHOP:
        # Temporary, and only here: the pitch still polls one file per role,
        # which cannot serve two rooms at once. Deleted later in step 3, once
        # the pitch reads its profiles from this service.
        mirror.write(role, result["attributes"])

    seq = _record_patch(request.app, connection, room, team, result,
                        reason=body.reason, actor=body.actor)
    return {**result, "seq": seq}


def _record_patch(fastapi_app, connection, room, team, result, reason, actor):
    """Log one profile move and tell the room about it. Returns the sequence.

    Both the PATCH route and kick-off go through here, so a stance applied
    automatically is indistinguishable in the log from one typed by hand --
    which is what lets scoring read the log without knowing who moved what.
    """
    delta = {"team": team, "role": result["role"], "changed": result["changed"],
             "reason": reason, "actor": actor}
    seq = rooms.append_event(connection, room["id"], "profile.patch", delta)
    fastapi_app.state.bus.publish(
        room_topic(room["code"]),
        {"type": "event", "seq": seq, "kind": "profile.patch", "match_ms": None,
         "payload": delta},
    )
    return seq


def _room_or_404(connection, code):
    room = rooms.by_code(connection, code)
    if room is None:
        raise HTTPException(404, f"there is no room {code}")
    return room


def _profile_room(request, code):
    """The connection and the room behind an /api/rooms/{code}/... path.

    Every one of those routes opened with the same three lines; a code that
    `codes.generate` could never have produced is a 404 rather than a lookup.
    """
    connection = request.app.state.conn
    code = code.upper()
    if not codes.is_valid(code):
        raise HTTPException(404, f"there is no room {code}")
    return connection, _room_or_404(connection, code)


def _known_team(team):
    """Refuse a dugout name before it is used to look anything up."""
    if team not in rooms.TEAMS:
        raise HTTPException(404, f"there is no {team} dugout")


def _require_own_seat(connection, room_id, team, player_id):
    owner = rooms.seat_owner(connection, room_id, team)
    if owner is None or owner != player_id:
        raise HTTPException(403, f"the {team} dugout is not yours")


def _require_profile_writer(request, connection, room_id, team):
    """A dugout's own manager, or a trusted service caller acting for them.

    The service token is checked first and in constant time, because the
    agents have no session to fall back on. An empty configured token can
    never match, so forgetting to set it locks the agents out rather than
    letting everyone in.
    """
    offered = request.headers.get("x-arena-service", "")
    if SERVICE_TOKEN and offered and hmac.compare_digest(offered, SERVICE_TOKEN):
        return
    player_id = identity.verify_token(request.cookies.get(COOKIE), SESSION_SECRET)
    if player_id is None or rooms.get_player(connection, player_id) is None:
        raise HTTPException(401, "join first -- your phone has no session")
    _require_own_seat(connection, room_id, team, player_id)


def _require_seated(connection, room_id, player_id):
    if not rooms.is_seated(connection, room_id, player_id):
        raise HTTPException(403, "only somebody in this match can start it")


def _announce(fastapi_app, room):
    """Publish the room's new shape to everyone watching it, and return it."""
    snapshot = rooms.snapshot(fastapi_app.state.conn, room["id"])
    fastapi_app.state.bus.publish(room_topic(room["code"]), {"type": "room", **snapshot})
    return snapshot


@contextmanager
def _rules():
    """Turn a rules violation into a 409 whose text a phone can show as-is."""
    try:
        yield
    except rooms.RoomError as problem:
        raise HTTPException(409, str(problem)) from problem


async def _pump(socket, subscription):
    """Forward everything on a subscription to a socket until cancelled."""
    async for message in subscription:
        await socket.send_json(message)


@app.websocket("/ws/rooms/{code}")
async def room_socket(socket: WebSocket, code: str, client_id: str = ""):
    """One room's feed. Anyone may listen; only the host may drive."""
    connection = socket.app.state.conn
    match_bus = socket.app.state.bus
    code = code.upper()
    if not codes.is_valid(code):
        await socket.close(code=4404, reason=f"there is no room {code}")
        return
    room = rooms.by_code(connection, code)
    if room is None:
        await socket.close(code=4404, reason=f"there is no room {code}")
        return

    await socket.accept()
    await socket.send_json({"type": "room", **rooms.snapshot(connection, room["id"])})

    subscription = match_bus.subscribe(room_topic(code))
    pump = asyncio.create_task(_pump(socket, subscription))
    try:
        while True:
            try:
                message = await socket.receive_json()
            except (ValueError, KeyError):
                continue
            _handle_from_host(message, connection, match_bus, room, client_id)
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        if pump.done() and not pump.cancelled():
            exc = pump.exception()
            # Disconnects are expected: a tab closes mid-send, or the network drops.
            # Only log genuinely unexpected exceptions.
            if exc and not isinstance(exc, (WebSocketDisconnect, ClientDisconnected)):
                logger.exception("room socket pump died", exc_info=exc)
        subscription.close()


def _wire_bytes(value):
    """Serialise for the wire, or None if it cannot go out.

    A lone UTF-16 surrogate survives json.dumps but not the UTF-8 encode that
    send_json and the SQLite bind both perform. Left unchecked, one such frame
    kills the pump -- and on the shared wall topic, every tenant's tile with it.
    """
    try:
        return json.dumps(value, ensure_ascii=False).encode()
    except (UnicodeEncodeError, ValueError, TypeError):
        return None


def _handle_from_host(message, connection, match_bus, room, client_id):
    """Apply one up-message, if the sender is the client holding physics."""
    if not isinstance(message, dict):
        return
    kind = message.get("type")
    if kind not in ("host.state", "host.event"):
        return
    # Re-read the room: the host is set at kick-off, which is often after the
    # big screen and the phones already have their sockets open.
    host_client_id = rooms.by_code(connection, room["code"])["host_client_id"]
    if not client_id or not host_client_id or not hmac.compare_digest(client_id, host_client_id):
        return

    payload = message.get("payload")
    if payload is not None and not isinstance(payload, dict):
        return
    if payload is None:
        payload = {}

    payload_encoded = _wire_bytes(payload)
    if payload_encoded is None or len(payload_encoded) > MAX_PAYLOAD_BYTES:
        return

    topic = room_topic(room["code"])
    if kind == "host.state":
        match_bus.publish(topic, {**payload, "type": "state"})
        # Server keys go last so a host cannot forge type or another room's code.
        match_bus.publish(WALL, {**payload, "type": "wall.state", "code": room["code"]})
        return

    event_kind = message.get("kind", "unknown")
    match_ms = message.get("match_ms")
    # Reject non-string kinds and non-integer match_ms to avoid SQL binding errors.
    if not isinstance(event_kind, str) or len(event_kind) > 100:
        return
    # `kind` is bound into SQL and echoed to every viewer, so it needs the same
    # encodability check the payload gets.
    if _wire_bytes(event_kind) is None:
        return
    if match_ms is not None:
        if not isinstance(match_ms, int) or isinstance(match_ms, bool):
            return
        # SQLite INTEGER is 8-byte signed: -2^63 to 2^63-1.
        if not (-9223372036854775808 <= match_ms <= 9223372036854775807):
            return
    seq = rooms.append_event(connection, room["id"], event_kind, payload, match_ms)
    match_bus.publish(topic, {"type": "event", "seq": seq, "kind": event_kind,
                              "match_ms": match_ms, "payload": payload})


@app.websocket("/ws/wall")
async def wall_socket(socket: WebSocket):
    """Every live room at a glance. One connection for the filmstrip, not six."""
    match_bus = socket.app.state.bus
    await socket.accept()
    await socket.send_json({"type": "wall", "rooms": rooms.live(socket.app.state.conn)})

    subscription = match_bus.subscribe(WALL, maxsize=128)
    tasks = [asyncio.create_task(_pump(socket, subscription)),
             asyncio.create_task(_until_closed(socket))]
    done, pending = set(), set(tasks)
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in pending:
            task.cancel()
        for task in done:
            if not task.cancelled():
                exc = task.exception()
                if exc:
                    logger.exception("wall socket task died", exc_info=exc)
        subscription.close()


async def _until_closed(socket):
    """The wall never sends anything up. This is only here to notice a hang-up.

    Without something reading, a closed browser tab is not discovered until the
    next send fails, which on a quiet venue could be a long time.
    """
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        return
