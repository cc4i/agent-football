"""Arena: rooms, seats and the live match bus.

Runs on :8003 beside the pitch (:5173), the coach (:8000), the captain (:8001)
and the dugout (:8002). It owns everything that used to be global -- who is
playing, which match they are in, and what happened in it -- so that more than
one person can play at once.
"""

import asyncio
import collections
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import (Depends, FastAPI, HTTPException, Request, Response, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from uvicorn.protocols.utils import ClientDisconnected

# Before the imports below rather than after them: `chain` and `coach` read
# their limits from the environment as they are imported, so a file read any
# later is a file they never see. A value already exported into the shell wins,
# which is what lets one `ARENA_SERVICE_TOKEN` cover all three processes.
load_dotenv()

import attributes
import board
import chain
import codes
import db
import grounds as grounds_registry
import identity
import limits
import philosophies
import presets
import profiles
import proxy
import qr
import rooms
from bus import WALL, Bus, room_topic

logger = logging.getLogger(__name__)

# A state frame carries a score, a clock and twelve positions; an event frame
# carries less. Six figures is generous for both and still small enough that a
# host cannot flood the wall's queues with one message.
MAX_PAYLOAD_BYTES = 102400

# Host validation pattern: letters, digits, dot, hyphen, colon (for port). An
# allowlist is a list of characters we did not have to reason about.
_HOST_PATTERN = re.compile(r'^[a-zA-Z0-9.\-:]+$')

# How many matches may be live at once. Sized well above a busy venue and well
# below what one instance can be talked into holding, so that the answer to a
# flood is a sentence rather than an instance quietly getting slower.
MAX_LIVE_ROOMS = int(os.environ.get("ARENA_MAX_LIVE_ROOMS", "120"))

# Two unauthenticated endpoints create rows. The burst is sized to a venue
# behind one NAT (50 people is the spec), and the rate is what stops a script.
# The room burst matches the cap above rather than sitting under it: whichever
# of the two is smaller is the one that answers a flood, and the 503 has a
# sentence in it where a 429 has a number.
PLAYER_RATE, PLAYER_BURST = 1.0, 120
ROOM_RATE, ROOM_BURST = 0.5, 120

# Asking whether a name is free costs one indexed lookup and creates nothing,
# but the join form asks while somebody is still typing, so one manager
# produces several of these before they produce a join. Its own bucket for
# exactly that reason: sharing the join's would mean a careful typist spends
# the budget they need to actually join. Sized so a venue arriving at once is
# comfortable and a script is not.
NAME_RATE, NAME_BURST = 5.0, 240

# How long a name on the board can be. In one place because the join and the
# check that runs ahead of it have to agree: a form that says a name is fine and
# then refuses it on the button is worse than one that never asked.
NAME_LIMIT = 40

# The coach spends money on every call, and its callers are not phones: both of
# these routes are called by the pitch, the big screen running the match, from
# a venue's one address. So this bucket is taken from under one constant key on
# purpose - see proxy._COACH_KEY - because the instance is the only honest unit
# to limit. Sized off the spec: a saturated venue runs near 0.13 chains a
# second and a shout costs two requests, so five a second is some twenty times
# a full house and still refuses a loop.
COACH_RATE, COACH_BURST = 5.0, 60

# A manager asking the screen to turn its room. One at a time, per manager per
# room: what this costs the arena is nothing, and what it costs the venue is a
# pill lighting up on a wall screen somebody is watching from across a room.
# The minute is how long that ask stands before the same person may repeat it.
ASK_RATE, ASK_BURST = 1.0 / 60.0, 1

# What the two modes are called in a sentence somebody reads. `rooms.MODES` are
# the words the database and the API use; these are the words a phone says.
MODE_NAMES = {"solo": "score attack", "versus": "head to head"}

# How often one room's tile may redraw on the wall. The host reports at 10 Hz
# because that is what the match it is running needs; a thumbnail on a
# filmstrip does not, and fifty of them at 10 Hz is five hundred messages a
# second down every wall socket in the venue. The room socket still carries
# every frame, which is what a viewer watching one match is reading. Zero or
# less is an operator saying "do not thin", and every frame then goes.
WALL_HZ = float(os.environ.get("ARENA_WALL_HZ", "2"))

# How many big screens may watch the wall at once. The cost of the wall is a
# product rather than a sum - rooms x WALL_HZ x screens - and /arena opens a
# wall socket on every screen that hosts a room, so the spec's 50 concurrent
# rooms with a screen each is 5,000 sends a second, which one instance carries.
# Sixty is that 50 with headroom for a reloading screen whose old socket has
# not been reaped yet, and for the board. Matching MAX_LIVE_ROOMS is the other
# end of the range and is not the answer: 120 rooms with a screen each is
# 28,800 sends a second, about a core spent on thumbnails. Past the cap a
# screen loses its filmstrip and keeps its match, which is the right thing to
# give up first.
MAX_WALL_SOCKETS = int(os.environ.get("ARENA_MAX_WALL_SOCKETS", "60"))


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


class Misconfigured(Exception):
    """A public deployment missing something it must not guess at."""


# On a laptop, a missing secret is a warning and a sensible default. On a URL
# anyone can reach it is neither: a random session secret logs every phone out
# on each deploy, a defaulted salt makes every returning player a stranger the
# day it changes, and an unset service token fails the agent chain at its last
# hop with a 403 nobody is watching for.
PRODUCTION = os.environ.get("ARENA_ENV") == "production"


def _insist(name, why):
    """Read a secret, or say exactly what is missing and why it matters."""
    value = os.environ.get(name, "")
    if value:
        return value
    if PRODUCTION:
        raise Misconfigured(f"{name} must be set when ARENA_ENV=production: {why}")
    return ""


# EMAIL_SALT keeps its literal default outside production: if it randomised,
# every email hash would change on restart and players would lose their history.
EMAIL_SALT = _insist("ARENA_EMAIL_SALT",
                     "changing it later makes every returning player a stranger") \
    or "arena-dev-salt"
if not os.environ.get("ARENA_EMAIL_SALT"):
    logger.warning("ARENA_EMAIL_SALT unset; set it before a real event or every "
                   "returning player is a stranger the next time you change it")

# SESSION_SECRET must never fall back to a public literal, even if that means
# sessions do not survive a restart.
SESSION_SECRET = _insist("ARENA_SECRET", "a random one logs every phone out on each deploy")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    logger.warning("ARENA_SECRET unset; sessions will not survive a restart")
COOKIE = "arena_session"

# The specialist agents run in another process with no phone and no cookie, so
# they carry a shared secret instead. Unset means they are refused: an unset
# secret must authenticate nobody rather than everybody.
SERVICE_TOKEN = _insist("ARENA_SERVICE_TOKEN", "the agent chain cannot write without it")
if not SERVICE_TOKEN:
    logger.warning("ARENA_SERVICE_TOKEN unset; server-side profile writes are refused")

# A role has fewer than fifty attributes. Anything larger is a mistake or an
# attempt to make the validator do work, and it is cheaper to refuse it here.
MAX_CHANGES = 64

# Whose name goes on a shout made in the workshop. Nobody is sitting in that
# dugout: the manager is in a chat window and the agent is the one on the
# touchline, so the agent is who the log says shouted.
WORKSHOP_ACTOR = "Antigravity"

# The two things a player agent may report about itself. The same two words the
# MCP server's tools are named after, because there is no third thing to say.
CONDITIONS = ("injury", "substitution")

# How much of a room's log one catch-up read may return. A three-minute match
# produces tens of events, so this only bites on a room somebody has left open.
MAX_REPLAY_EVENTS = 500

# How many rows of either board go out in one response. A venue's board is tens
# of rows; the cap is here so that a long-running instance cannot turn the page
# into a several-megabyte download.
MAX_BOARD_ROWS = 100

# How long a room may go without a word from the screen holding it before the
# arena gives up on it. Backgrounding a tab stops its frames, so this is also
# the grace a host gets to come back: long enough to answer the door, short
# enough that the wall is not full of matches nobody is playing.
HOST_GONE_SECONDS = 30
# How often to look. A dead room should leave the wall while somebody is still
# standing in front of it, and the sweep costs one query.
SWEEP_SECONDS = 5
# What the phones are told. It goes in the log with the event, so a manager who
# reloads afterwards is still told why their match stopped.
HOST_GONE_REASON = "The screen running this match stopped reporting, so it was abandoned."
# The same thing before a whistle, which needs different words: nothing had
# kicked off, so nothing was abandoned in the sense the sentence above means.
# The fact and nothing else: the page that shows this puts the way out on a
# button underneath it, and a sentence telling them to go and find a code would
# be the arena forgetting it is talking to a phone that already knows them.
LOBBY_GONE_REASON = "The screen that opened this room has gone."

# What a QR code should encode. Unset, it is worked out from the request, which
# is what lets a first deploy be a first deploy: Cloud Run does not tell a
# service its own hostname until it exists, and every QR in the venue encodes
# this. Set it explicitly for a tunnel or a LAN name.
PUBLIC_URL = os.environ.get("ARENA_PUBLIC_URL", "").rstrip("/")
if PRODUCTION and not PUBLIC_URL:
    logger.warning("ARENA_PUBLIC_URL unset; the public URL is being worked out per "
                   "request and should be set explicitly now that the service has a name")

# The built pitch, when the arena is the thing serving it. Unset locally, where
# Vite serves the pitch on :5173 and this mount does not exist.
PITCH_DIR = os.environ.get("ARENA_PITCH_DIR", "")


# Where the pitch is served from. The big screen frames it rather than drawing
# it: physics is 2000 lines of Phaser that already exist and already work, and
# reimplementing them in the arena to avoid an iframe would be the wrong trade.
# When the arena is serving the bundle itself, that is a path on this origin.
PITCH_URL = os.environ.get(
    "ARENA_PITCH_URL", "/pitch" if PITCH_DIR else "http://localhost:5173").rstrip("/")

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    connection = db.connect()
    db.init_db(connection)
    # The workshop is where the dugout tunes profiles with nobody in a dugout
    # seat, so it is opened here rather than by a phone.
    if rooms.by_code(connection, codes.WORKSHOP) is None:
        rooms.create_room(connection, "solo", codes.WORKSHOP)
    # On every boot but the first that check finds the room and nothing commits
    # after it, so the instance would sit on an open transaction from startup
    # until its first caller arrived - which on a fresh Cloud Run instance can
    # be a while.
    db.finish(connection)
    fastapi_app.state.conn = connection
    fastapi_app.state.bus = Bus()
    fastapi_app.state.chain = chain.Chain(fastapi_app.state.bus)
    # Per app rather than per module, so that a test's client gets its own and
    # the suite cannot fail in whichever test happens to run last. There is one
    # instance, so per app is per process anyway.
    fastapi_app.state.players = limits.Bucket(PLAYER_RATE, PLAYER_BURST)
    fastapi_app.state.names = limits.Bucket(NAME_RATE, NAME_BURST)
    fastapi_app.state.rooms_opened = limits.Bucket(ROOM_RATE, ROOM_BURST)
    fastapi_app.state.coach = limits.Bucket(COACH_RATE, COACH_BURST)
    fastapi_app.state.asks = limits.Bucket(ASK_RATE, ASK_BURST)
    # How many screens are on the wall right now, here for the same reason as
    # the buckets above: a module-level counter is shared by every test in the
    # session, and one leaked socket would then fail an unrelated test later on.
    fastapi_app.state.walls = 0
    # Which rooms this instance is holding a screen's socket open for, which is
    # what keeps a backgrounded tab's match alive. Per app for the same reason
    # as the counter above it.
    fastapi_app.state.held = _HeldRooms()
    # Which grounds instances are connected and what each is running. Per app
    # for the same reason: a socket belongs to the process that accepted it.
    fastapi_app.state.grounds = grounds_registry.Grounds()
    # Install filter to redact client_id from access logs.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(_RedactClientId())
    # uvicorn gives its own loggers a handler and leaves the root without one,
    # so everything this module says below WARNING has been going nowhere: a
    # room unranked for running fast, a host given up on. Borrow the handler.
    server_logger = logging.getLogger("uvicorn")
    if server_logger.handlers and not logger.handlers:
        logger.handlers = server_logger.handlers
        logger.setLevel(logging.INFO)
    watchdog = asyncio.create_task(_watch_for_the_missing(fastapi_app))
    yield
    watchdog.cancel()
    # Chains first: one still talking to the coach would otherwise come back to
    # a closed database when its specialists write.
    await fastapi_app.state.chain.close()
    connection.close()


app = FastAPI(title="Arena", lifespan=lifespan)

# Two paths onto the coach, for the pitch's own coach bar and status check.
# Mounted on the app rather than reached for directly so that the allowlist is
# one file somebody can read in full.
app.include_router(proxy.router)


@app.middleware("http")
async def put_the_connection_back(request: Request, call_next):
    """End every request on an idle connection, including one that raised.

    One request is one unit of work against the one shared connection, and this
    is the only place that is true of every route at once. The sockets are not
    covered by this -- middleware does not wrap a WebSocket route -- so they
    call `db.finish` for themselves.
    """
    try:
        return await call_next(request)
    finally:
        db.finish(request.app.state.conn)


class JoinRequest(BaseModel):
    # Both bounds are the validator's below rather than the field's, because
    # pydantic words its own -- "String should have at least 1 character" -- for
    # whoever wrote the request, and what reads this one is a manager holding a
    # phone in a room with a QR code in it.
    display_name: str
    # RFC 5321 caps email addresses at 254 octets. Optional, and defaulted to
    # the empty string the form sends for a box nobody touched: the only thing
    # an address buys a manager is one place on the board across two phones, so
    # a venue that asks for it as a condition of playing is collecting a
    # personal detail it has no use for.
    email: str = Field(default="", max_length=254)

    @field_validator("display_name", "email")
    @classmethod
    def bindable_as_text(cls, value):
        # Both of these end up in a text column, and psycopg refuses to bind a
        # NUL. Unrefused it would be a 500 handed out for unauthenticated
        # input. The address keeps its own mask, but the mask keeps the domain.
        if "\x00" in value:
            raise ValueError("that cannot contain a NUL character")
        return value

    @field_validator("display_name")
    @classmethod
    def a_name_the_board_can_show(cls, value):
        # Measured after the tidying rather than before it, so a name is judged
        # as it will be stored: `min_length` would count the spaces in a name of
        # nothing else, and `max_length` would count the ones between the words
        # that are about to be collapsed to one.
        name = identity.normalise_name(value)
        if not name:
            raise ValueError("that needs a name in it")
        if len(name) > NAME_LIMIT:
            raise ValueError(f"that is longer than the {NAME_LIMIT} characters the board shows")
        return name

    @field_validator("email")
    @classmethod
    def looks_like_an_address(cls, value):
        value = value.strip()
        if not value:
            return ""
        local, at_sign, domain = value.partition("@")
        if not (local and at_sign and "." in domain):
            raise ValueError("that does not look like an email address")
        return value


class RoomRequest(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value):
        if value not in rooms.MODES:
            raise ValueError(f"mode must be one of {', '.join(rooms.MODES)}")
        return value


class ModeRequest(BaseModel):
    """A screen reshaping the room it opened. The token is how it says so.

    In the body rather than the query, so it never reaches an access log.
    """

    mode: str
    screen_token: str

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


class ModeAskRequest(BaseModel):
    """A manager asking the screen to turn the room they are about to join.

    No token of any kind. Asking is not doing, and the screen is still the one
    that decides -- so the only thing this has to prove is that somebody real
    is asking, which the session cookie already does.
    """

    mode: str

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value):
        if value not in rooms.MODES:
            raise ValueError(f"mode must be one of {', '.join(rooms.MODES)}")
        return value


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


class SubstitutionRequest(BaseModel):
    """A player agent reporting on itself: a knock, or a request to come off."""
    team: str
    role: str
    action: str
    # What the specialist said in its own words. Bounded and never trusted: it
    # comes from a language model and it is drawn on a wall.
    detail: str = Field(default="", max_length=120)

    @field_validator("action")
    @classmethod
    def known_condition(cls, value):
        if value not in CONDITIONS:
            raise ValueError(f"action must be one of {', '.join(CONDITIONS)}")
        return value


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
    return _player_or_401(request, request.app.state.conn)


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


@app.get("/scan")
async def scanned(request: Request):
    """What the sheet on the wall leads to. Not a page: a door.

    The printed code says this and only this, so the same sheet has to work for
    somebody who arrived a minute ago and for somebody on their fourth match.
    A phone whose cookie names a manager the venue still has goes to their own
    page; everybody else goes to the form. A cookie from an event whose
    database has since been emptied is the second of those rather than an
    error, which is what `_player_or_none` is for.
    """
    known = _player_or_none(request, request.app.state.conn) is not None
    return RedirectResponse("/home" if known else "/register")


@app.get("/register")
async def register_page():
    """The form, with no room behind it. Where a scanned sheet sends a stranger."""
    return _page("register.html")


@app.get("/home")
async def home_page():
    """A manager's own page: where they stand, and what they can walk into.

    Served to a phone with no session too. It asks who it belongs to and sends
    a stranger to the form; refusing the file would make that a blank page.
    """
    return _page("home.html")


@app.get("/poster")
async def poster_page():
    """The sheet itself, laid out to be printed and pinned to a wall."""
    return _page("poster.html")


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


@app.get("/board")
async def board_page():
    """The standings. The same page on a wall-mounted screen and on a phone."""
    return _page("board.html")


@app.get("/api/venue")
async def read_venue(request: Request):
    """Where the other halves of the venue live, for the pages to link to."""
    return {"pitch_url": PITCH_URL, "public_url": _origin(request)}


@app.post("/api/players")
async def join(body: JoinRequest, request: Request, response: Response):
    """A name in, a session cookie out. The address is theirs to withhold.

    The name is not optional and is nobody else's: it is what the board shows,
    what the wall calls a dugout and what the other manager reads, so two of
    them would be two people the venue cannot tell apart. A clash comes back as
    a 409 the form shows under the field.
    """
    if not request.app.state.players.take(limits.client_ip(request)):
        raise HTTPException(429, "slow down a moment and try that again")
    connection = request.app.state.conn
    with _rules():
        player_id = rooms.upsert_player(connection, body.display_name, body.email,
                                        EMAIL_SALT, _player_or_none(request, connection))
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


@app.get("/api/players/me")
async def read_me(request: Request, player_id: int = Depends(current_player)):
    """Who this phone is, and whether it left anybody sitting in a dugout.

    The seat comes back with the name because a phone asking who it is has
    almost always just been picked up again, and the first thing its owner
    wants to know is whether the match they walked away from is still on.
    """
    connection = request.app.state.conn
    player = rooms.get_player(connection, player_id)
    seat = rooms.current_seat(connection, player_id)
    return {"id": player_id,
            "display_name": player["display_name"],
            "email": player["email_masked"],
            "room": dict(seat) if seat else None}


@app.get("/api/rooms/open")
async def read_open_rooms(request: Request):
    """Which rooms are waiting for a manager, and what is on meanwhile.

    Both, because a phone with nowhere to go has one question and it is not
    "which rooms are open". It is "is anything happening here". A venue whose
    every screen is mid-match answers the first question with an empty list,
    which reads the same as a venue with nothing plugged in, so the second is
    answered alongside it: those matches are the reason there is no seat, and
    they are also the promise that there will be one.
    """
    connection = request.app.state.conn
    return {"rooms": rooms.open_now(connection), "playing": rooms.live(connection)}


@app.get("/api/players/available")
async def name_available(name: str, request: Request):
    """Whether this name is free, answered for whoever is asking.

    Your own name is free to you. A manager coming back for a second match on
    the same phone would otherwise be told the name on their own board entry
    was taken, by themselves, and asked to think of another.

    A taken name is answered in its holder's spelling rather than in the one
    that was typed, because that is the spelling the board shows and the one the
    join itself refuses in. The form says the two back word for word or it looks
    like it is arguing about something else.
    """
    if not request.app.state.names.take(limits.client_ip(request)):
        raise HTTPException(429, "slow down a moment and try that again")
    tidy = identity.normalise_name(name)
    # The same bounds the join itself applies, checked here rather than left to
    # the lookup: psycopg will not bind a NUL at all, so one has to be turned
    # away before it reaches a query.
    if not tidy or len(tidy) > NAME_LIMIT or "\x00" in tidy:
        raise HTTPException(422, "that is not a name the board can show")
    connection = request.app.state.conn
    holder = rooms.name_holder(connection, tidy)
    if holder is None or holder["id"] == _player_or_none(request, connection):
        return {"name": tidy, "available": True}
    return {"name": holder["display_name"], "available": False}


@app.post("/api/rooms")
async def open_room(body: RoomRequest, request: Request):
    """Open a room. This response is the only place its screen token appears.

    Not its physics token, which no HTTP response has carried since the grounds
    took over simulating: that one goes down the control socket to one server.
    """
    if not request.app.state.rooms_opened.take(limits.client_ip(request)):
        raise HTTPException(429, "slow down a moment and try that again")
    connection = request.app.state.conn
    # The venue's real limit is how many pitches are connected, and kick-off is
    # where that gets asked. This number is a ceiling nobody should reach: a
    # backstop against a runaway opening rooms, not the capacity of the venue.
    if rooms.live_count(connection) >= MAX_LIVE_ROOMS:
        raise HTTPException(503, "the venue is full - wait for a match to finish")
    try:
        with _rules():
            room = rooms.create_room(connection, body.mode)
    except codes.CodesExhausted as problem:
        raise HTTPException(503, str(problem)) from problem
    return {**_snapshot(connection, room["id"], request),
            "screen_token": room["screen_client_id"]}


@app.get("/api/rooms/{code}")
async def read_room(code: str, request: Request):
    connection, room = _profile_room(request, code)
    return _snapshot(connection, room["id"], request)


@app.post("/api/rooms/{code}/mode")
async def change_mode(code: str, body: ModeRequest, request: Request):
    """Turn a waiting room between score attack and head to head.

    The screen's move, not a manager's: whoever opened the room has held its
    token since, and a phone that could reshape somebody else's lobby could
    close the dugout its neighbour was reading about. Announced like any other
    change to a room's shape, so the join form on a phone that has already
    scanned the code follows it without being asked.
    """
    connection, room = _profile_room(request, code)
    _require_screen(room, body.screen_token)
    with _rules():
        rooms.set_mode(connection, room["id"], body.mode)
    return _announce(request.app, room, request)


@app.post("/api/rooms/{code}/mode-request")
async def ask_for_mode(code: str, body: ModeAskRequest, request: Request,
                       player_id: int = Depends(current_player)):
    """Ask the screen holding this room to turn it, and let the screen decide.

    Only a screen may change what a room plays, and a screen holds one room. So
    a venue whose screens all happened to open score attack has no head to head
    anywhere in it, however many people are queuing for one, and the person
    holding the phone had no way to say so: the choice was made before they
    walked in, by somebody guessing.

    This is that way, and it is deliberately only a way to ask. The room does
    not move. What arrives at the screen is a name and a mode, and somebody
    standing at the screen taps the switch that was already there. Nothing here
    can reshape a lobby a stranger is reading, which is the property that made
    the token rule worth having in the first place.

    Refused by `require_mode_change`, which is what would refuse the screen, so
    a phone is never told to go ahead and ask for something the screen would
    then be told it cannot do.
    """
    connection, room = _profile_room(request, code)
    with _rules():
        rooms.require_mode_change(connection, room["id"], body.mode)
    if room["mode"] == body.mode:
        raise HTTPException(409, f"that room is already {MODE_NAMES[body.mode]}")
    # Per manager per room. A screen on a wall cannot look away from a pill
    # that keeps lighting up, so one bored phone must not be able to strobe it,
    # and the manager beside them must still be able to ask.
    if not request.app.state.asks.take((room["id"], player_id)):
        raise HTTPException(429, "you have already asked - give the screen a moment")

    asked = {"type": "mode.request", "mode": body.mode,
             "by": rooms.get_player(connection, player_id)["display_name"]}
    # Published and not logged. The room's log is what scoring is recomputed
    # from, and an ask is neither a thing that happened in a match nor a thing
    # a late arrival needs replayed at them: a screen that missed one is asked
    # again by somebody who is still standing there wanting it.
    request.app.state.bus.publish(room_topic(room["code"]), asked)
    return {"mode": body.mode, "by": asked["by"]}


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
    return _announce(request.app, room, request)


@app.post("/api/rooms/{code}/seats/{team}/ready")
async def set_ready(code: str, team: str, body: ReadyRequest, request: Request,
                    player_id: int = Depends(current_player)):
    connection, room = _profile_room(request, code)
    _known_team(team)
    _require_own_seat(connection, room["id"], team, player_id)
    with _rules():
        rooms.set_ready(connection, room["id"], team, body.ready)
    return _announce(request.app, room, request)


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


@app.get("/api/rooms/{code}/result")
async def read_result(code: str, request: Request):
    """What each dugout earned here, and where it leaves them.

    Empty until the whistle, and it stays a read: the points were computed and
    stored once, when the match ended, so refreshing the results screen cannot
    produce a different total from the one the manager first saw.
    """
    connection, room = _profile_room(request, code)
    results = board.read(connection, room["id"])
    return {"code": room["code"], "mode": room["mode"], "status": room["status"],
            "ranked": bool(room["ranked"]),
            "results": results,
            "standing": board.placing(connection, room, results),
            "top": board.top(connection, room["mode"])}


@app.get("/api/board")
async def read_board(request: Request):
    """Both boards. They are returned together because the page shows both."""
    connection = request.app.state.conn
    return {"solo": board.solo(connection)[:MAX_BOARD_ROWS],
            "versus": board.versus(connection)[:MAX_BOARD_ROWS],
            "managers": board.managers(connection)}


@app.get("/api/rooms/{code}/qr.svg")
async def room_qr(code: str, request: Request):
    """This room's join address as a scannable code, for the screen beside it."""
    _, room = _profile_room(request, code)
    return _code(join_url(room["code"], request))


@app.get("/qr.svg")
async def venue_qr(request: Request):
    """The venue's own code: the one that goes on a sheet on a wall.

    It says `/scan` and nothing else, because a sheet is printed in the morning
    and every room in the building is opened after that. What it does when
    somebody points a phone at it is the router below's business, which is the
    other half of why the code itself never has to change.
    """
    return _code(f"{_origin(request)}/scan")


def _code(url):
    return Response(qr.svg(url), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/philosophies")
async def read_philosophies():
    """The four opening stances, for the join form to render."""
    return {"philosophies": philosophies.catalogue()}


@app.get("/api/presets")
async def read_presets():
    """The four chips under the shout bar, for the phone to render."""
    return {"presets": presets.catalogue()}


@app.get("/api/attributes")
async def read_attributes():
    """Every role's attributes: what the squad ships with, and how far each may move.

    The rules themselves, rather than any room's copy of them. The dugout's
    tuners are shown a band per attribute so they never propose a number that
    was always going to be refused, and they used to be shown it from a second
    copy of these rules kept beside the workshop. Two copies of a validator
    drift apart until one of them is wrong, so the one that decides is also the
    one that answers.
    """
    return {"roles": {role: _bands(role) for role in attributes.ROLES}}


def _bands(role):
    """One role's attributes, each with its shipped value and its two limits."""
    bands = {}
    for name, value in attributes.baseline_for(role).items():
        low, high = attributes.range_for(name, value)
        bands[name] = {"baseline": value, "min": low, "max": high}
    return bands


@app.post("/api/rooms/{code}/shout")
async def shout(code: str, body: ShoutRequest, request: Request):
    """Say something to your squad, with a chip or in your own words.

    The words are logged and broadcast first, then what they moved, so the
    manager sees their own instruction land before the squad reacts to it, and
    so a late joiner replaying the log sees them in that order too. For a chip
    that is the same request; for words it is tens of seconds earlier, because
    the chain answering them runs on after this returns.
    """
    connection, room = _profile_room(request, code)
    team, actor = _who_is_shouting(request, connection, room)

    if body.preset is not None:
        try:
            chip = presets.describe(body.preset)
        except presets.Unknown as problem:
            raise HTTPException(422, str(problem)) from problem
        said = _say(request.app, connection, room, team, chip["phrase"],
                    actor, preset=chip["name"])
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
    said = _say(request.app, connection, room, team, words, actor)
    ahead = request.app.state.chain.submit(room, team, said["seq"], words, actor)
    return {**said, "ahead": ahead}


def _who_is_shouting(request, connection, room):
    """Which dugout a shout came from, and whose name goes on it.

    Two callers, and only two. A manager shouts from a phone, with a session
    and a seat, at a match that has kicked off. The workshop has no phones and
    no seats -- it is the lab the dugout's five stages happen in, one blue
    squad in front of a pitch that is always running -- so Antigravity shouts
    there with the service token instead.

    That authority stops at the workshop on purpose. The token is held by
    processes on the machine the arena runs on, and none of them has any
    business shouting into a match a stranger is playing on their phone.
    """
    if _is_service_caller(request) and room["code"] == codes.WORKSHOP:
        if room["status"] not in ("lobby", "live"):
            raise HTTPException(409, "that match is over")
        return "blue", WORKSHOP_ACTOR

    player_id = _player_or_401(request, connection)
    team = rooms.team_of(connection, room["id"], player_id)
    if team is None:
        raise HTTPException(403, "only somebody in a dugout can shout")
    if room["status"] != "live":
        raise HTTPException(409, "there is nobody out there to shout at yet")
    return team, rooms.get_player(connection, player_id)["display_name"]


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


@app.post("/api/rooms/{code}/substitution")
async def substitution(code: str, body: SubstitutionRequest, request: Request):
    """A player agent's own condition: a knock, or a request to come off.

    The one thing that happened in a match and did not go through here. It was
    a JSON file beside the pitch, polled every two seconds by whichever browser
    was hosting -- so it reached that browser and nothing else, and when physics
    left the browser it would have reached nothing at all.

    In the log instead: the dugout that owns the player sees it on a phone, the
    big screen sees it on the rail, and a screen that cuts to this match a
    minute later reads it off the log on the way in. Service-token only. A
    specialist reports on itself; a manager does not get to injure a rival's
    keeper by posting one of these.
    """
    if not _is_service_caller(request):
        raise HTTPException(403, "only a squad's own agents report on the squad")
    connection, room = _profile_room(request, code)
    _known_team(body.team)
    _known_role(body.role)
    # The workshop never kicks off and reports all day, so this is not `live`.
    # What it refuses is a specialist that finished thinking after the whistle:
    # a finished match's log is what it was scored against.
    if room["status"] not in ("lobby", "live"):
        raise HTTPException(409, "that match is over")

    said = {"team": body.team, "role": body.role,
            "action": body.action, "detail": body.detail}
    seq = rooms.append_event(connection, room["id"], "substitution", said)
    request.app.state.bus.publish(
        room_topic(room["code"]),
        {"type": "event", "seq": seq, "kind": "substitution", "match_ms": None,
         "payload": said},
    )
    return {"seq": seq, **said}


@app.post("/api/rooms/{code}/start")
async def start(code: str, request: Request, player_id: int = Depends(current_player)):
    """Kick off. Any manager in the match may call it; physics does not move.

    The room's physics token was minted when the room was opened, so starting a
    match tells the arena to find somewhere to play it, rather than handing the
    caller control of a pitch they are not running.
    """
    connection, room = _profile_room(request, code)
    _require_seated(connection, room["id"], player_id)

    # Whether this room could start at all is settled first, so that a lobby
    # with a dugout still empty hears about the dugout.
    with _rules():
        rooms.require_startable(connection, room["id"])

    # Then somewhere to play, before anything is committed. A room that went
    # live with nobody simulating it would sit at 0-0 with a clock that never
    # started, until the sweep abandoned it thirty seconds later and told both
    # managers their match stopped reporting - which is a lie about what went
    # wrong. Better to refuse the kick-off and leave the lobby standing.
    registry = request.app.state.grounds
    if not registry.assign(code):
        raise HTTPException(503, "no pitch is free to run this match; try again in a moment")

    try:
        with _rules():
            rooms.start_match(connection, room["id"])
    except Exception:
        # The slot goes back on any failure, or a room that could not start
        # takes a pitch out of the venue for the rest of the evening.
        registry.release(code)
        raise

    # Stances land before the room is announced live. A client that sees "live"
    # and then asks for profiles must never be able to read them mid-application.
    for team, seat in rooms.snapshot(connection, room["id"])["seats"].items():
        for result in philosophies.apply(connection, room["id"], team, seat["philosophy"]):
            _record_patch(request.app, connection, room, team, result,
                          reason=seat["philosophy"], actor="kick-off")

    snapshot = _announce(request.app, room, request)

    farm = registry.socket_for(code)
    if farm is not None:
        # The physics token leaves the arena exactly here, to exactly one
        # server, over a socket that authenticated as a service. The seed goes
        # with it so the match is the same match wherever it is played.
        await farm.send_json({"type": "host", "code": code,
                              "token": room["host_client_id"],
                              "seed": f"{code}-{room['id']}"})

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
    _known_role(role)
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
    """Refuse a dugout name before it is used to look anything up.

    Most unknown dugouts are harmless, because the lookup simply finds no row.
    One is not: psycopg will not bind a NUL at all, so a team carrying one has
    to be turned away here rather than by the query.
    """
    if team not in rooms.TEAMS:
        raise HTTPException(404, f"there is no {team} dugout")


def _known_role(role):
    """Refuse a role name before it is used to look anything up.

    Most unknown roles are harmless, because the lookup simply finds no row.
    One is not: psycopg will not bind a NUL at all, so a role carrying one has
    to be turned away here rather than by the query.
    """
    if role not in attributes.ROLES:
        raise HTTPException(404, f"there is no {role} in this squad")


def _require_own_seat(connection, room_id, team, player_id):
    owner = rooms.seat_owner(connection, room_id, team)
    if owner is None or owner != player_id:
        raise HTTPException(403, f"the {team} dugout is not yours")


def _require_profile_writer(request, connection, room_id, team):
    """A dugout's own manager, or a trusted service caller acting for them.

    The service token is checked first, because the agents have no session to
    fall back on.
    """
    if _is_service_caller(request):
        return
    _require_own_seat(connection, room_id, team,
                      _player_or_401(request, connection))


def _is_service_caller(request):
    """Whether this came from a process holding the shared secret."""
    return _service_token_ok(request.headers.get("x-arena-service", ""))


def _service_token_ok(offered):
    """Whether a header value is the shared secret between our own processes.

    Compared in constant time, and an empty configured token can never match,
    so forgetting to set one locks the agents out rather than letting everyone
    in.

    Takes the header rather than the request because a WebSocket handshake has
    headers and no request: the grounds authenticate here too.
    """
    # The two sides came in by different roads and each goes back the way it
    # came. Spelling both as UTF-8 would compare a token against a re-spelling
    # of itself: `café` set in the environment and sent correctly over the wire
    # would never match, and the agents would get a silent 401 forever with
    # nothing anywhere to say why.
    return bool(SERVICE_TOKEN and offered
                and _same_secret(_header_bytes(offered), _text_bytes(SERVICE_TOKEN)))


def _same_secret(offered, expected):
    """Constant-time equality for two secrets, decided on their bytes.

    `hmac.compare_digest` refuses a non-ASCII str outright rather than saying
    no, and every secret it is asked about here arrives from outside the
    process: a caller's header, a socket's query string, the environment. A
    wrong token has to be a wrong token and not a crash, so this only ever
    compares bytes. Which bytes is the caller's to say, because the road a
    string took in decides what it was before it was a string.
    """
    return hmac.compare_digest(offered, expected)


def _header_bytes(value):
    """The bytes a header value was before Starlette made it a string.

    Headers are decoded as latin-1, the one codec that maps every byte to
    exactly one character and back, so this hands back the caller's own bytes
    and cannot fail on anything that came off a socket.
    """
    return value.encode("latin-1")


def _text_bytes(value):
    """The bytes behind a string that came in as UTF-8: a query, the environment.

    Python decodes the environment as UTF-8 and parks any byte that is not
    valid UTF-8 in a lone surrogate, and `surrogatepass` is what puts those
    back where a plain encode would raise on them. Query strings and anything
    read back out of Postgres came in as UTF-8 too.
    """
    return value.encode("utf-8", "surrogatepass")


def _player_or_401(request, connection):
    """The player id in the session cookie, or a 401 a phone can read."""
    player_id = _player_or_none(request, connection)
    if player_id is None:
        raise HTTPException(401, "join first - your phone has no session")
    return player_id


def _player_or_none(request, connection):
    """The player in the session cookie, if there is one and they still exist.

    The twin above is for the routes that need a session. Joining is the route
    that makes one, so there a phone with no cookie and a phone holding one
    from an event whose database has since been emptied are both simply
    somebody new, rather than somebody to turn away.
    """
    player_id = identity.verify_token(request.cookies.get(COOKIE), SESSION_SECRET)
    if player_id is None or rooms.get_player(connection, player_id) is None:
        return None
    return player_id


def _require_seated(connection, room_id, player_id):
    if not rooms.is_seated(connection, room_id, player_id):
        raise HTTPException(403, "only somebody in this match can start it")


def _require_screen(room, offered):
    """Refuse anybody but the screen that opened this room.

    This was `_require_host` and compared the physics token, because the screen
    that opened a room was the thing simulating it. The grounds simulate now,
    so the two claims came apart, and this is the one a browser is ever handed:
    "this lobby is mine", not "I am running this match".

    Compared on bytes in constant time for the reason given at `_same_secret`:
    the token arrives from outside the process, and a wrong one has to be a
    wrong one rather than a crash or a stopwatch.
    """
    held = room["screen_client_id"]
    if not offered or not held or not _same_secret(_text_bytes(offered), _text_bytes(held)):
        raise HTTPException(403, "only the screen that opened this room can change it")


def join_url(code, request=None):
    """The address a phone lands on after scanning this room's code.

    Configured if somebody said so, otherwise whatever the request came in on.
    Behind Cloud Run that means the forwarded scheme and the Host header, which
    together are the *.run.app name with its certificate.
    """
    return f"{_origin(request)}/join/{code}"


def _origin(request):
    if PUBLIC_URL:
        return PUBLIC_URL
    if request is None:
        return "http://localhost:8003"
    # Websockets have scheme ws/wss; map them to http/https for the origin URL.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    if scheme == "ws":
        scheme = "http"
    elif scheme == "wss":
        scheme = "https"
    host = request.headers.get("host", request.url.netloc)
    # The host header is attacker-controlled. Validate before trusting: an
    # allowlist is a list of characters we did not have to reason about. Letters,
    # digits, dot, hyphen, and an optional :port. Everything else falls back.
    if scheme not in ("http", "https"):
        return "http://localhost:8003"
    if not host:
        return "http://localhost:8003"
    if not _HOST_PATTERN.match(host):
        return "http://localhost:8003"
    return f"{scheme}://{host}"


def _snapshot(connection, room_id, request=None):
    """A room as clients see it, with the address its QR encodes.

    `rooms.snapshot` is deliberately ignorant of HTTP, so the URL is glued on
    here rather than threading a base address through the data layer.
    """
    snapshot = rooms.snapshot(connection, room_id)
    return {**snapshot, "join_url": join_url(snapshot["code"], request)}


def _announce(fastapi_app, room, request=None):
    """Publish the room's new shape to everyone watching it, and return it."""
    snapshot = _snapshot(fastapi_app.state.conn, room["id"], request)
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


async def _pump_wall(socket, subscription):
    """`_pump`, with the position frames thinned out.

    Only `wall.state` is thinned. A room opening, kicking off or finishing is a
    `wall` message, and dropping one of those would leave a tile that is wrong
    rather than a tile that is a fraction of a second old.
    """
    keep = _wall_thinner()
    async for message in subscription:
        if message.get("type") == "wall.state" and not keep(message.get("code"),
                                                            time.monotonic()):
            continue
        await socket.send_json(message)


def _wall_thinner():
    """Per-socket state deciding which wall frames are worth sending.

    Per room rather than per socket overall: a wall showing one match should
    still update smoothly, and a wall showing fifty should not send fifty times
    as much. A room's first frame always goes, so a tile appears the moment its
    match does.
    """
    last = {}

    def keep(code, now):
        if WALL_HZ <= 0:
            # An operator writing zero means "do not thin", and `1.0 / WALL_HZ`
            # below would answer that by killing the pump task and dropping the
            # screen into a reconnect loop. Read here rather than closed over
            # once, so the rate can be changed under a socket that is open.
            return True
        if now - last.get(code, float("-inf")) < 1.0 / WALL_HZ:
            return False
        last[code] = now
        return True

    return keep


class _HeldRooms:
    """The rooms this instance has a screen's socket open for, right now.

    Liveness used to rest entirely on the screen speaking up: `host.here` every
    ten seconds off a setInterval, and the pitch's frames off
    requestAnimationFrame. A browser suspends both of those for a tab that is
    not the one in front. Measured in Chrome against this arena, the frames
    stop on the tick the tab is hidden and the interval is throttled and then
    starved, so the last thing a backgrounded screen says lands about a minute
    in -- and thirty seconds after that the sweep gives up on a match whose
    screen is sitting right there, telling both managers it stopped reporting.

    The socket is the part a browser does not throttle. A tab that still exists
    still holds its connection, and answers the server's pings from the network
    stack rather than from the JavaScript that has been put to sleep, so an
    open host socket is proof of a screen where a message on a timer is not.
    A lid that shuts stops answering those pings, uvicorn closes the socket,
    and the room is swept exactly as it was before.

    Counted rather than kept as a set of codes: the arena page and the pitch it
    frames are two sockets on one room with one token, so the pitch reloading
    at kick-off must not release a room the screen is still holding.

    What is held is published into the shared column rather than consulted in
    place, because a deploy runs two instances and only one of them has the
    sockets. The other has to be able to read that somebody is holding this
    room, and the column is the only thing both of them can see.

    Counted by kind, because the two kinds of client prove different things. A
    screen's socket proves its lobby is real: that screen is the only thing that
    can ever run it. It proves nothing about a live match, which the grounds
    run - and a wall left open on a match whose grounds died would otherwise
    vouch for it for the rest of the evening, with the sweep unable to reach a
    match nobody is simulating.
    """

    KINDS = ("screen", "grounds")

    def __init__(self):
        self._held = {kind: collections.Counter() for kind in self.KINDS}

    def took(self, code, kind):
        self._held[kind][code] += 1

    def gave_up(self, code, kind):
        counter = self._held[kind]
        if counter[code] > 1:
            counter[code] -= 1
        else:
            # Popped rather than decremented to zero, so the counter is the size
            # of the venue rather than of the evening.
            counter.pop(code, None)

    def codes(self, kind):
        return list(self._held[kind])


class _HostReporting:
    """When this socket last told its room that the host is here.

    The screen publishes a frame every 100ms while a match is running, and
    stamping the room on each of them would be ten committing writes a second
    per match down the one shared connection, in front of everything else the
    arena has to say. Nothing needs that rate: the sweep runs every
    SWEEP_SECONDS and gives a host HOST_GONE_SECONDS, so a stamp that lags by a
    sweep changes no outcome a sweep can reach.

    One of these belongs to each host socket rather than to the module, because
    a socket lives exactly as long as "this host is reporting" and takes its
    state away with it when it hangs up. A map keyed by room is the thing this
    change removed, and its eviction problem would come back with it.
    """

    def __init__(self):
        self.stamped = None

    def stamp(self, connection, room_id):
        """Write the room's liveness, unless this socket wrote it a moment ago.

        The gap is measured on a monotonic clock because it never leaves this
        process; what goes in the column is wall clock, because it does. The
        first frame on a socket always writes, so an instance that has just come
        up learns a match is live from the first thing it hears rather than a
        sweep later.
        """
        now = time.monotonic()
        if self.stamped is not None and now - self.stamped < SWEEP_SECONDS:
            return
        self.stamped = now
        rooms.heard_from(connection, room_id)


@app.websocket("/ws/rooms/{code}")
async def room_socket(socket: WebSocket, code: str, client_id: str = ""):
    """One room's feed. Anyone may listen; only the host may drive."""
    connection = socket.app.state.conn
    match_bus = socket.app.state.bus
    code = code.upper()
    if not codes.is_valid(code):
        # Accepted first so the refusal can be one: an upgrade that is never
        # accepted is answered with an HTTP status, which has nowhere to carry
        # a close code, so a mistyped code reached the browser as 1006 and an
        # empty string and socket.js retried it forever.
        await socket.accept()
        await socket.close(code=4404, reason=f"there is no room {code}")
        return
    room = rooms.by_code(connection, code)
    if room is None:
        # Before the handshake, because `accept` is an await point and a screen
        # that is already gone would otherwise leave the lookup's transaction
        # open with nobody to close it.
        db.finish(connection)
        await socket.accept()
        await socket.close(code=4404, reason=f"there is no room {code}")
        return

    await socket.accept()
    try:
        await socket.send_json({"type": "room", **_snapshot(connection, room["id"], socket)})
    finally:
        # The snapshot is a read, and a read opens a transaction like anything
        # else. A viewer that never says a word would otherwise hold one open
        # for as long as its tab is, and one that closed its tab between the
        # read and the send would hold it forever.
        db.finish(connection)

    # Which kind of client this socket is, settled once at the handshake: both
    # tokens are minted when the room is opened and neither ever changes, so a
    # client that holds one now holds it for the life of the room. From here on
    # this connection existing is what says that client is still there, which
    # is the one thing a backgrounded tab can still do.
    holding = None
    if client_id:
        offered = _text_bytes(client_id)
        if room["host_client_id"] and _same_secret(
                offered, _text_bytes(room["host_client_id"])):
            holding = "grounds"
        elif room["screen_client_id"] and _same_secret(
                offered, _text_bytes(room["screen_client_id"])):
            holding = "screen"
    if holding:
        socket.app.state.held.took(code, holding)

    subscription = match_bus.subscribe(room_topic(code))
    pump = asyncio.create_task(_pump(socket, subscription))
    reporting = _HostReporting()
    try:
        while True:
            try:
                message = await socket.receive_json()
            except (ValueError, KeyError):
                continue
            try:
                _handle_from_host(message, connection, match_bus, room, client_id,
                                  reporting, socket)
            finally:
                # One message is one unit of work. No middleware reaches a
                # WebSocket route, so this socket puts the connection back
                # itself, after every message including one that raised.
                db.finish(connection)
    except WebSocketDisconnect:
        pass
    finally:
        # Before anything that can raise. Whatever else goes wrong on the way
        # out, a socket that is gone must stop vouching for its room, or one
        # bad hang-up leaves a match live for the rest of the evening.
        if holding:
            socket.app.state.held.gave_up(code, holding)
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
    send_json and the Postgres bind both perform. Left unchecked, one such frame
    kills the pump -- and on the shared wall topic, every tenant's tile with it.
    """
    try:
        return json.dumps(value, ensure_ascii=False).encode()
    except (UnicodeEncodeError, ValueError, TypeError):
        return None


def _handle_from_host(message, connection, match_bus, room, client_id, reporting, socket):
    """Apply one up-message, if the sender is the client holding physics."""
    if not isinstance(message, dict):
        return
    kind = message.get("type")
    if kind not in ("host.here", "host.state", "host.event"):
        return
    # Re-read the room: sockets are usually open well before the whistle, and
    # both the host token and the status are checked against it as it is now.
    current = rooms.by_code(connection, room["code"])
    host_client_id = current["host_client_id"]
    if not client_id or not host_client_id or not _same_secret(_text_bytes(client_id),
                                                               _text_bytes(host_client_id)):
        return
    # The screen is here. Recorded before the message is picked over, because a
    # frame the arena goes on to refuse is still proof that somebody is on the
    # other end of the socket, and recorded whatever the room's status, because
    # a room spends every second before the whistle in its lobby and a lobby
    # with nobody behind it is the one place a phone must never be sent.
    reporting.stamp(connection, current["id"])
    # A screen with nothing to report yet. Saying so was the whole message.
    if kind == "host.here":
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
        _watch_the_clock(connection, current, payload)
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
    # encodability check the payload gets, and one the payload does not: a NUL
    # survives json.dumps and the UTF-8 encode, and psycopg refuses to bind it.
    # Only `kind` is exposed, because a NUL inside the payload is escaped to six
    # harmless characters on its way into payload_json.
    if _wire_bytes(event_kind) is None or "\x00" in event_kind:
        return
    if match_ms is not None:
        if not isinstance(match_ms, int) or isinstance(match_ms, bool):
            return
        # Postgres BIGINT is 8-byte signed: -2^63 to 2^63-1.
        if not (-9223372036854775808 <= match_ms <= 9223372036854775807):
            return
    seq = rooms.append_event(connection, room["id"], event_kind, payload, match_ms)
    match_bus.publish(topic, {"type": "event", "seq": seq, "kind": event_kind,
                              "match_ms": match_ms, "payload": payload})

    if event_kind in ("full_time", "abandoned"):
        # The whistle came from whoever is running the match, so the pitch is
        # theirs to give back and there is nothing to tell them.
        _end_match(connection, match_bus, room, event_kind, socket,
                   farm=socket.app.state.grounds if socket is not None else None)


def _watch_the_clock(connection, room, payload):
    """Take a room off the boards if its host ever reports anything but 1x.

    Time to first goal is worth up to 500 points, so a match played at 3x is
    not comparable with one played straight. The room stays perfectly playable
    and its managers still get their breakdown; it simply stops being ranked,
    and it never comes back, because a slider nudged for one frame and put back
    would otherwise be free.
    """
    speed = payload.get("speed")
    if not room["ranked"] or isinstance(speed, bool) or not isinstance(speed, (int, float)):
        return
    if speed != 1.0:
        logger.info("room %s unranked: host reported speed %s", room["code"], speed)
        rooms.unrank(connection, room["id"])


def _end_match(connection, match_bus, room, event_kind, socket=None, farm=None):
    """Close the room the host says is over, and pay out if it was played out.

    The host is trusted for physics and not for scoring, and when a match ended
    is physics. Everything the points are computed from is already in the log;
    scoring only reads it back. An abandoned match is scored by nobody -- it has
    no full time, so there is no result to have earned.

    `farm` is the grounds registry, and a match that is over gives its pitch
    back to it. Returns the grounds socket that was running this room, so a
    caller who ended the match behind the grounds' back can tell them. Nobody
    tells a grounds about its own full time: it blew the whistle.
    """
    status = "finished" if event_kind == "full_time" else "abandoned"
    rooms.finish_match(connection, room["id"], status)
    if status == "finished":
        board.record(connection, rooms.by_code(connection, room["code"]))
    match_bus.publish(room_topic(room["code"]),
                      {"type": "room", **_snapshot(connection, room["id"], socket)})
    match_bus.publish(WALL, {"type": "wall", "rooms": rooms.live(connection)})
    return farm.release(room["code"]) if farm is not None else None


def _give_up_on_the_missing(connection, match_bus, now, held=None, farm=None,
                            drops=None):
    """Close rooms whose host has stopped reporting. Returns their codes.

    A room only leaves "live" when somebody blows a whistle on it, and a laptop
    closed mid-match never does. Without this, one shut lid leaves a frozen
    tile on every wall in the venue for the rest of the evening, and the two
    managers watch a clock that has stopped and are never told why.

    Waiting rooms are swept on the same rule, and for a sharper reason. A lobby
    is advertised to every phone with no room of its own, and its screen is the
    only thing that can ever run it: close that tab and the room becomes a code
    that will never do anything, still sitting at the top of everybody's list
    saying "nobody in it yet". A manager took one of those in production, read
    "Score attack", took the dugout, kicked off, and watched a clock that never
    started for thirty seconds before the arena gave up on the match they were
    still looking at.

    Liveness is a column rather than a dict in memory because a deploy runs two
    instances at once for a few seconds. An arena that only trusted what it had
    personally heard would spend those seconds abandoning matches whose hosts
    are talking perfectly happily to the instance it is replacing.

    A room nobody has reported on yet is stamped on the first sweep that sees
    it rather than abandoned, which is the same rule one sweep late.

    `held` is the sockets this instance has open for a room's own screen and
    for its grounds, and every one of them is heard from before anything is
    judged. See `_HeldRooms` for why the connection is better proof than
    anything a backgrounded tab can be relied on to say, and for why a screen
    is only allowed to vouch for a room that has not kicked off.

    `farm` is the grounds registry, and `drops` a list this appends
    `(socket, code)` to for every match it abandoned that a grounds was
    assigned. Collected rather than sent, because this stays synchronous: it
    runs from a fixed clock in tests and from a watchdog in production, and
    only the watchdog can await. A grounds still simulating a room the arena
    has given up on has to be told, or it holds that slot all evening.
    """
    if held is not None:
        rooms.heard_from_all(connection, held.codes("screen"), now, statuses=("lobby",))
        rooms.heard_from_all(connection, held.codes("grounds"), now)
    gone = []
    for room in rooms.hosted_with_liveness(connection):
        if room["last_heard_at"] is None:
            # From this sweep's own clock rather than a `time.time()` of its
            # own: the grace is measured against `now`, so `now` is what it
            # should be measured from. In a running arena the two are the same
            # reading; on a fixed clock only this one is.
            rooms.heard_from(connection, room["id"], now)
            continue
        if now - room["last_heard_at"] <= HOST_GONE_SECONDS:
            continue
        full = rooms.by_code(connection, room["code"])
        waiting = room["status"] == "lobby"
        said = {"reason": LOBBY_GONE_REASON if waiting else HOST_GONE_REASON}
        seq = rooms.append_event(connection, full["id"], "abandoned", said)
        match_bus.publish(room_topic(room["code"]),
                          {"type": "event", "seq": seq, "kind": "abandoned",
                           "match_ms": None, "payload": said})
        # No socket to pass here or below: this is announcing a room that has
        # just been given up on, and a wrong but inert URL on a dead room beats
        # an attacker-settable one anywhere.
        if waiting:
            rooms.close_lobby(connection, full["id"])
            match_bus.publish(room_topic(room["code"]),
                              {"type": "room", **_snapshot(connection, full["id"])})
        else:
            orphaned = _end_match(connection, match_bus, full, "abandoned", farm=farm)
            if orphaned is not None and drops is not None:
                drops.append((orphaned, room["code"]))
        logger.info("room %s abandoned: nothing from its host in %ss (%s)",
                    room["code"], HOST_GONE_SECONDS,
                    "waiting" if waiting else "live")
        gone.append(room["code"])
    return gone


def _tell_our_own_rooms_it_is_over(connection, match_bus, announced):
    """Tell this instance's sockets about endings decided elsewhere.

    The bus is in-process, and the arena runs one instance so that it can be.
    Cloud Run treats that as a target rather than a promise: replacing a
    revision, or merely thinking about it, leaves two containers up, and both
    of them run the sweep. The quiet one serves no requests, so it holds no
    sockets -- but it wins the race roughly half the time. It writes the
    abandonment, logs it, and publishes it to a bus with nobody on it. The
    phones, all of them on the other container, watch a clock that has stopped
    and are told nothing at all. Observed in production, twice in one evening.

    So each instance answers for its own sockets rather than for the decision:
    whatever the database says a room's status is, the clients watching that
    room *here* have been told it. `announced` is what this instance has
    already said, and it is pruned to the rooms still being watched, so it
    stays the size of the venue rather than of the evening.

    Only the ending. Everything else -- the whistle, the shouts, the frames --
    is published by the request that caused it, and requests are served by the
    instance the client is already on. An ending is the one thing decided with
    no request behind it, and the one thing with no later message to correct
    it: a match that is over stays over, silently, forever.

    Not the reason, either. The dugout reads that out of the event log as soon
    as a snapshot tells it the match is finished, which picks up anything else
    that went missing on the way rather than only the last thing.

    A match ended normally, here, is therefore announced twice: once by the
    whistle and once by the next sweep, five seconds behind it. Knowing the
    difference would mean carrying "who published this" through every path that
    can end a room, to save a repaint of a screen that is already showing the
    right thing. Clients redraw from snapshots on every reconnect as it is.
    """
    watched = {topic.split(":", 1)[1] for topic in match_bus.topics()
               if topic.startswith("room:")}
    # A room nobody here is watching any more cannot be behind on anything.
    announced.intersection_update(watched)
    told = []
    for code in sorted(watched - announced):
        room = rooms.by_code(connection, code)
        if room is None or room["status"] in ("lobby", "live"):
            continue
        announced.add(code)
        match_bus.publish(room_topic(code),
                          {"type": "room", **_snapshot(connection, room["id"])})
        logger.info("room %s is %s and its watchers here had not been told",
                    code, room["status"])
        told.append(code)
    return told


def _tell_our_own_wall_who_is_playing(connection, match_bus, last):
    """Re-send the wall its list when the database no longer agrees with it.

    The same problem as the rooms above, one screen further out. A match that
    ended on the other instance comes off that instance's walls and stays on
    this one's, and the wall is pushed to rather than polling: it reads the
    list once, when it connects, and then stands in a venue for the evening.

    Returns the list this instance now stands behind, which the sweep holds on
    to until the next one. Nothing is sent while nobody is watching, so an
    arena with no wall up does not carry a list around all evening.
    """
    if match_bus.subscriber_count(WALL) == 0:
        return last
    playing = rooms.live(connection)
    if playing == last:
        return last
    match_bus.publish(WALL, {"type": "wall", "rooms": playing})
    return playing


async def _watch_for_the_missing(fastapi_app):
    """Run the three sweeps above for as long as the arena is up."""
    # Endings this instance has already put on its own wire. Seeded by the
    # sweep rather than left empty, so a room given up on here is not then
    # announced a second time by the reconciliation immediately below it.
    announced = set()
    # And what its walls were last told. Unknown at first, so the first sweep
    # after a wall connects re-sends a list that wall already has; every sweep
    # after that is silent until something actually changes.
    wall = None
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            drops = []
            announced.update(_give_up_on_the_missing(
                fastapi_app.state.conn, fastapi_app.state.bus, time.time(),
                fastapi_app.state.held, fastapi_app.state.grounds, drops))
            for socket, code in drops:
                # Best effort. A grounds that has already gone is why we are
                # here, and one that fails to hear this is not worth taking the
                # sweep down over: it stops reporting and the next sweep is
                # somebody else's problem.
                try:
                    await socket.send_json({"type": "drop", "code": code})
                except Exception:
                    logger.warning("could not tell the grounds to drop %s", code)
            _tell_our_own_rooms_it_is_over(fastapi_app.state.conn,
                                           fastapi_app.state.bus, announced)
            wall = _tell_our_own_wall_who_is_playing(
                fastapi_app.state.conn, fastapi_app.state.bus, wall)
        except Exception:
            # One bad sweep must not take the watchdog down for the life of the
            # process: every room after it would then hang live forever.
            logger.exception("the sweep for missing hosts failed")
        finally:
            # One sweep is one unit of work, and no middleware reaches this
            # loop. Logging the failure above is not enough on its own: the
            # statement that failed left the transaction in error, and every
            # later statement in the process - the next sweep's, and the next
            # caller's - fails with it until somebody rolls back. On the way
            # through it also puts back the transaction that reading the live
            # rooms opened, which on an instance nobody has visited yet is the
            # only thing holding the vacuum horizon down.
            db.finish(fastapi_app.state.conn)


@app.websocket("/ws/grounds")
async def grounds_socket(socket: WebSocket):
    """One socket per grounds instance. Assignments down, capacity up.

    Authenticated with the same X-Arena-Service the specialists carry: this is
    a server talking to a server, and what comes down it is a room's physics
    token, which is the one credential no browser may ever be handed.

    No frames go this way. Each match reports on its own /ws/rooms/{code} like
    a tab always did, which is what keeps `_handle_from_host` and `fake_host`
    honest, and what means the arena needs no idea how many processes are
    behind the football it is being told about.
    """
    if not _service_token_ok(socket.headers.get("x-arena-service", "")):
        # Accepted before closing for the reason `wall_socket` gives: a refused
        # upgrade has nowhere to put a close code, so the instance would see
        # 1006 with no sentence and retry against a token that will never work.
        await socket.accept()
        await socket.close(code=4403, reason="the grounds must authenticate")
        return

    await socket.accept()
    registry = socket.app.state.grounds
    joined = False
    try:
        while True:
            try:
                message = await socket.receive_json()
            except ValueError:
                # Not JSON. A control plane is ours on both ends, so this is a
                # bug rather than an attack, but it is not worth a disconnect.
                continue
            if not isinstance(message, dict):
                continue
            if message.get("type") == "grounds.here":
                registry.joined(socket, message.get("capacity", 0))
                joined = True
                logger.info("grounds joined, capacity %s; %s connected",
                            message.get("capacity"), registry.connected())
    except WebSocketDisconnect:
        pass
    finally:
        # Before anything that can raise, as with the room socket: an instance
        # that has gone must stop being offered matches on the next kick-off.
        if joined:
            registry.left(socket)
            logger.info("grounds left; %s connected, %s running",
                        registry.connected(), registry.running())


@app.websocket("/ws/wall")
async def wall_socket(socket: WebSocket):
    """Every live room at a glance. One connection for the filmstrip, not six."""
    state = socket.app.state
    if state.walls >= MAX_WALL_SOCKETS:
        # Accepted first and closed straight after, because an upgrade that is
        # never accepted is answered with an HTTP status and there is nowhere
        # in that answer to put a close code or a sentence: the browser would
        # get 1006 and an empty string, and retry forever over a full venue.
        # The refusal returns before the count below, so it spends no slot.
        await socket.accept()
        await socket.close(code=4429, reason="too many screens are watching the wall")
        return
    # Taken before the handshake and given back in the finally below, because
    # every way out of here after this line has to give it back: a refused
    # accept, a tab that closed during the opening send, a normal hang-up. The
    # check and this line are adjacent for the same reason: `accept` is an await
    # point, so two handshakes racing across it would both pass a check that
    # only one of them should.
    state.walls += 1
    match_bus = state.bus
    try:
        await socket.accept()
        try:
            await socket.send_json({"type": "wall", "rooms": rooms.live(socket.app.state.conn)})
        finally:
            # The wall's only statement is that one read, and it then sits there all
            # evening. Nothing else here touches the database, so this is the whole
            # of what it owes the connection - owed just the same by a screen whose
            # tab closed between the read and the send, which is the one path that
            # would otherwise hold a transaction open with nobody left to close it.
            db.finish(socket.app.state.conn)

        subscription = match_bus.subscribe(WALL, maxsize=128)
        tasks = [asyncio.create_task(_pump_wall(socket, subscription)),
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
    finally:
        state.walls -= 1


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


class Revalidated(StaticFiles):
    """Static files a browser has to ask about before reusing.

    Without a Cache-Control header a browser is free to guess how long a file
    stays fresh, and it guesses in hours. The pages already say `no-cache`, so
    the guessing lands on exactly the files that change -- the stylesheet and
    the scripts -- and a wall screen keeps serving last week's board until
    somebody finds the reload-with-cache-bypass shortcut. `no-cache` is not
    `no-store`: the file is still cached, still revalidated by ETag, and still
    answered with a 304, which on a venue's own network costs nothing.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class Immutable(StaticFiles):
    """Vite's content-hashed bundle, cached for as long as a browser likes.

    The opposite of `Revalidated` and for the opposite reason: hashed JavaScript
    and CSS under /pitch/bundle are named by their own contents, so a changed
    file is a changed URL and the old one can never be stale. The rest of what
    the arena serves is not hashed -- the kits, the portraits, the favicon --
    which is why those are `Revalidated` instead.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if PITCH_DIR:
    @app.get("/pitch")
    @app.get("/pitch/")
    async def pitch_page():
        """The pitch's own entry point, never cached: it names the hashed bundle."""
        return FileResponse(Path(PITCH_DIR) / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/pitch/bundle", Immutable(directory=Path(PITCH_DIR) / "bundle"), name="bundle")
    app.mount("/pitch", Revalidated(directory=PITCH_DIR), name="pitch")

# Mounted last so no page or API path can ever be shadowed by a file on disk.
app.mount("/static", Revalidated(directory=STATIC), name="static")
