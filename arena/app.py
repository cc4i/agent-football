"""Arena: rooms, seats and the live match bus.

Runs on :8003 beside the pitch (:5173), the coach (:8000), the captain (:8001)
and the dugout (:8002). It owns everything that used to be global -- who is
playing, which match they are in, and what happened in it -- so that more than
one person can play at once.
"""

import asyncio
import hmac
import io
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import segno
from fastapi import (Depends, FastAPI, HTTPException, Request, Response, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from uvicorn.protocols.utils import ClientDisconnected

import chain
import codes
import db
import identity
import philosophies
import presets
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

# How much of a room's log one catch-up read may return. A three-minute match
# produces tens of events, so this only bites on a room somebody has left open.
MAX_REPLAY_EVENTS = 500

# What a QR code should encode. A phone on the venue wifi cannot reach the
# laptop's loopback address, so a real event sets this to the machine's LAN
# name or its tunnel. The default is right for one person testing alone.
PUBLIC_URL = os.environ.get("ARENA_PUBLIC_URL", "http://localhost:8003").rstrip("/")

# Where the pitch is served from. The big screen frames it rather than drawing
# it: physics is 2000 lines of Phaser that already exist and already work, and
# reimplementing them in the arena to avoid an iframe would be the wrong trade.
PITCH_URL = os.environ.get("ARENA_PITCH_URL", "http://localhost:5173").rstrip("/")

STATIC = Path(__file__).parent / "static"


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
    fastapi_app.state.chain = chain.Chain(fastapi_app.state.bus)
    # Install filter to redact client_id from access logs.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(_RedactClientId())
    yield
    # Chains first: one still talking to the coach would otherwise come back to
    # a closed database when its specialists write.
    await fastapi_app.state.chain.close()
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


class ShoutRequest(BaseModel):
    """One tap on a chip, or something typed into the box. One or the other.

    A chip is a patch the squad understands with no language model behind it,
    so it lands at once. Words go down the chain and take tens of seconds. They
    are the same instruction to everything downstream of the log, which is why
    they arrive on the same route rather than on two.
    """
    preset: str | None = Field(default=None, min_length=1, max_length=40)
    # Long enough for anything anybody shouts at a futsal match from a phone,
    # and short enough that the prompt it becomes cannot be a document.
    text: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def one_or_the_other(self):
        if bool(self.preset) == bool(self.text):
            raise ValueError("a shout is either a chip or some words, not both and not neither")
        return self


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
        raise HTTPException(401, "join first - your phone has no session")
    return player_id


@app.get("/health")
async def health():
    return {"ok": True, "service": "arena"}


def _page(name):
    """One of the arena's pages. They are files, not templates.

    Everything on them is drawn from the API by their own script, so there is
    nothing to interpolate here and no reason to carry a template engine.
    """
    return FileResponse(STATIC / name, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


@app.get("/")
async def front_door():
    """The address somebody types on the venue's laptop is the big screen."""
    return RedirectResponse("/arena")


@app.get("/join/{code}")
async def join_page(code: str, request: Request):
    """The form a scanned QR lands on.

    A code that names no room is a 404 rather than a form: a QR photographed at
    last week's event should say so here, not after somebody has typed their
    name and email into a page that was never going to work.
    """
    _profile_room(request, code)
    return _page("join.html")


@app.get("/play")
async def play_page():
    """The phone's dugout. The room comes from its query string."""
    return _page("play.html")


@app.get("/arena")
async def arena_page():
    """The big screen: the lobby, and then the match."""
    return _page("arena.html")


@app.get("/api/venue")
async def read_venue():
    """Where the other halves of the venue live, for the pages to link to."""
    return {"pitch_url": PITCH_URL, "public_url": PUBLIC_URL}


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
    """Open a room. This response is the only place its host token appears."""
    connection = request.app.state.conn
    try:
        with _rules():
            room = rooms.create_room(connection, body.mode)
    except codes.CodesExhausted as problem:
        raise HTTPException(503, str(problem)) from problem
    return {**_snapshot(connection, room["id"]), "host_token": room["host_client_id"]}


@app.get("/api/rooms/{code}")
async def read_room(code: str, request: Request):
    connection, room = _profile_room(request, code)
    return _snapshot(connection, room["id"])


@app.get("/api/rooms/{code}/me")
async def read_my_seat(code: str, request: Request,
                       player_id: int = Depends(current_player)):
    """Which dugout is mine in this room, if any.

    The room snapshot is the same for everyone watching, because it is also
    what goes out on the bus. Which of those seats is yours is the one thing
    that differs per phone, so it is asked for separately.
    """
    connection, room = _profile_room(request, code)
    player = rooms.get_player(connection, player_id)
    return {"name": player["display_name"],
            "team": rooms.team_of(connection, room["id"], player_id)}


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


@app.get("/api/rooms/{code}/events")
async def read_events(code: str, request: Request, since: int = 0):
    """The room's log from `since` onwards, so a reconnect loses nothing.

    A phone that slept through a shout should come back to the same relay
    everyone else is looking at. The log is gapless and numbered per room, so
    catching up is a range read rather than a resync protocol.
    """
    connection, room = _profile_room(request, code)
    log = [entry for entry in rooms.events(connection, room["id"]) if entry["seq"] > since]
    return {"events": log[-MAX_REPLAY_EVENTS:]}


@app.get("/api/rooms/{code}/qr.svg")
async def room_qr(code: str, request: Request):
    """This room's join address as a scannable code.

    SVG rather than PNG so the big screen can scale it to the wall without it
    going soft, and so no image library has to be in the dependency list.
    """
    _, room = _profile_room(request, code)
    drawing = io.BytesIO()
    # Sized in mm with no class attributes, so the page's own CSS decides how
    # big it is rather than the encoder.
    segno.make(join_url(room["code"]), error="m").save(
        drawing, kind="svg", scale=1, border=2, unit="mm", svgclass=None, lineclass=None)
    return Response(drawing.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/philosophies")
async def read_philosophies():
    """The four opening stances, for the join form to render."""
    return {"philosophies": philosophies.catalogue()}


@app.get("/api/presets")
async def read_presets():
    """The four chips under the shout bar, for the phone to render."""
    return {"presets": presets.catalogue()}


@app.post("/api/rooms/{code}/shout")
async def shout(code: str, body: ShoutRequest, request: Request,
                player_id: int = Depends(current_player)):
    """Say something to your squad, with a chip or in your own words.

    The words are logged and broadcast first, then what they moved, so the
    manager sees their own instruction land before the squad reacts to it, and
    so a late joiner replaying the log sees them in that order too. For a chip
    that is the same request; for words it is tens of seconds earlier, because
    the chain answering them runs on after this returns.
    """
    connection, room = _profile_room(request, code)
    team = rooms.team_of(connection, room["id"], player_id)
    if team is None:
        raise HTTPException(403, "only somebody in a dugout can shout")
    if room["status"] != "live":
        raise HTTPException(409, "there is nobody out there to shout at yet")
    player = rooms.get_player(connection, player_id)

    if body.preset is not None:
        try:
            chip = presets.describe(body.preset)
        except presets.Unknown as problem:
            raise HTTPException(422, str(problem)) from problem
        said = _say(request.app, connection, room, team, chip["phrase"],
                    player["display_name"], preset=chip["name"])
        for result in presets.apply(connection, room["id"], team, chip["name"]):
            _record_patch(request.app, connection, room, team, result,
                          reason=chip["phrase"], actor="preset",
                          shout_seq=said["seq"])
        return said

    # Whitespace a phone keyboard put in is not part of the instruction, and
    # the words become a prompt, so they arrive as one line either way.
    words = " ".join(body.text.split())
    if not words:
        raise HTTPException(422, "a shout needs some words in it")
    # Asked before anything is written: a shout the dugout has no room for
    # should leave no trace of having been half-taken.
    if not request.app.state.chain.has_room(room["id"], team):
        raise HTTPException(429, "give the squad a moment - two of your calls "
                                 "are still going out")
    said = _say(request.app, connection, room, team, words, player["display_name"])
    ahead = request.app.state.chain.submit(
        room, team, said["seq"], words, player["display_name"])
    return {**said, "ahead": ahead}


def _say(fastapi_app, connection, room, team, text, actor, preset=None):
    """Log what a manager said and tell the room, before anything acts on it."""
    said = {"team": team, "text": text, "preset": preset, "actor": actor}
    seq = rooms.append_event(connection, room["id"], "shout.sent", said)
    fastapi_app.state.bus.publish(
        room_topic(room["code"]),
        {"type": "event", "seq": seq, "kind": "shout.sent", "match_ms": None,
         "payload": said},
    )
    return {"seq": seq, "ahead": 0, **said}


@app.post("/api/rooms/{code}/start")
async def start(code: str, request: Request, player_id: int = Depends(current_player)):
    """Kick off. Any manager in the match may call it; physics does not move.

    Whoever opened the room has held the host token since they opened it, so
    starting a match tells the room to go, rather than handing the caller
    control of a pitch they may not even be rendering.
    """
    connection, room = _profile_room(request, code)
    _require_seated(connection, room["id"], player_id)
    with _rules():
        rooms.start_match(connection, room["id"])

    # Stances land before the room is announced live. A client that sees "live"
    # and then asks for profiles must never be able to read them mid-application.
    for team, seat in rooms.snapshot(connection, room["id"])["seats"].items():
        for result in philosophies.apply(connection, room["id"], team, seat["philosophy"]):
            _record_patch(request.app, connection, room, team, result,
                          reason=seat["philosophy"], actor="kick-off")

    snapshot = _announce(request.app, room)
    request.app.state.bus.publish(WALL, {"type": "wall", "rooms": rooms.live(connection)})
    return snapshot


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


@app.post("/api/rooms/{code}/teams/{team}/profiles/reset")
async def reset_profiles(code: str, team: str, request: Request):
    """Put this dugout back to the shipped squad.

    The workshop's lab does this on every reload, which is what makes its five
    stages repeatable. It is the same authority as a patch, because it is a
    patch -- every role, back to where it started -- and it reaches the log as
    exactly that.
    """
    connection, room = _profile_room(request, code)
    _known_team(team)
    if room["status"] not in ("lobby", "live"):
        raise HTTPException(409, "that match is over")
    _require_profile_writer(request, connection, room["id"], team)

    for result in profiles.reset(connection, room["id"], team):
        _record_patch(request.app, connection, room, team, result,
                      reason="back to the shipped squad", actor="reset")
    return {"team": team, "profiles": profiles.read_all(connection, room["id"], team)}


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

    # A specialist writing through this route cannot name the shout it is
    # answering, and a manager must not be able to. The arena knows which
    # instruction it is carrying for this dugout, so it says so itself.
    seq = _record_patch(request.app, connection, room, team, result,
                        reason=body.reason, actor=body.actor,
                        shout_seq=request.app.state.chain.caused_by(room["id"], team))
    return {**result, "seq": seq}


def _record_patch(fastapi_app, connection, room, team, result, reason, actor,
                  shout_seq=None):
    """Log one profile move and tell the room about it. Returns the sequence.

    The PATCH route, kick-off and the shout chips all go through here, so a
    stance applied automatically is indistinguishable in the log from one typed
    by hand -- which is what lets scoring read the log without knowing who
    moved what. A patch a shout caused says so, because scoring pays for a
    shout that led to a goal and has to walk from one to the other.
    """
    delta = {"team": team, "role": result["role"], "changed": result["changed"],
             "reason": reason, "actor": actor}
    if shout_seq is not None:
        delta["shout_seq"] = shout_seq
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
        raise HTTPException(401, "join first - your phone has no session")
    _require_own_seat(connection, room_id, team, player_id)


def _require_seated(connection, room_id, player_id):
    if not rooms.is_seated(connection, room_id, player_id):
        raise HTTPException(403, "only somebody in this match can start it")


def join_url(code):
    """The address a phone lands on after scanning this room's code."""
    return f"{PUBLIC_URL}/join/{code}"


def _snapshot(connection, room_id):
    """A room as clients see it, with the address its QR encodes.

    `rooms.snapshot` is deliberately ignorant of HTTP, so the URL is glued on
    here rather than threading a base address through the data layer.
    """
    snapshot = rooms.snapshot(connection, room_id)
    return {**snapshot, "join_url": join_url(snapshot["code"])}


def _announce(fastapi_app, room):
    """Publish the room's new shape to everyone watching it, and return it."""
    snapshot = _snapshot(fastapi_app.state.conn, room["id"])
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
    await socket.send_json({"type": "room", **_snapshot(connection, room["id"])})

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
    # Re-read the room: sockets are usually open well before the whistle, and
    # both the host token and the status are checked against it as it is now.
    current = rooms.by_code(connection, room["code"])
    host_client_id = current["host_client_id"]
    if not client_id or not host_client_id or not hmac.compare_digest(client_id, host_client_id):
        return
    # Holding the token is no longer proof the match has started, since the
    # creator has held it since they opened the room. Nothing reaches the log
    # or the wall until a manager has actually kicked off.
    if current["status"] != "live":
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

    if event_kind == "full_time":
        # The host is trusted for physics and not for scoring, and when the
        # match ended is physics. Everything the points are computed from is
        # already in the log above; this only closes the room.
        rooms.finish_match(connection, room["id"])
        match_bus.publish(topic, {"type": "room", **_snapshot(connection, room["id"])})
        match_bus.publish(WALL, {"type": "wall", "rooms": rooms.live(connection)})


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


# Mounted last so no page or API path can ever be shadowed by a file on disk.
app.mount("/static", StaticFiles(directory=STATIC), name="static")
