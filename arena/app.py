"""Arena: rooms, seats and the live match bus.

Runs on :8003 beside the pitch (:5173), the coach (:8000), the captain (:8001)
and the dugout (:8002). It owns everything that used to be global -- who is
playing, which match they are in, and what happened in it -- so that more than
one person can play at once.
"""

import os
from contextlib import asynccontextmanager, contextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

import db
import identity
import rooms
from bus import WALL, Bus, room_topic

# Dev defaults. Set both in the environment before a real event: the salt fixes
# every email hash for good, and the secret signs every phone's session.
EMAIL_SALT = os.environ.get("ARENA_EMAIL_SALT", "arena-dev-salt")
SESSION_SECRET = os.environ.get("ARENA_SECRET", "arena-dev-secret")
COOKIE = "arena_session"


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    connection = db.connect(os.environ.get("ARENA_DB", db.DB_PATH))
    db.init_db(connection)
    fastapi_app.state.conn = connection
    fastapi_app.state.bus = Bus()
    yield
    connection.close()


app = FastAPI(title="Arena", lifespan=lifespan)


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    email: str

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


class StartRequest(BaseModel):
    host_client_id: str = Field(min_length=1)


def current_player(request: Request) -> int:
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
    with _rules():
        room = rooms.create_room(connection, body.mode)
    return rooms.snapshot(connection, room["id"])


@app.get("/api/rooms/{code}")
async def read_room(code: str, request: Request):
    connection = request.app.state.conn
    return rooms.snapshot(connection, _room_or_404(connection, code)["id"])


@app.post("/api/rooms/{code}/seats/{team}")
async def sit_down(code: str, team: str, body: SeatRequest, request: Request,
                   player_id: int = Depends(current_player)):
    connection = request.app.state.conn
    room = _room_or_404(connection, code)
    with _rules():
        rooms.take_seat(connection, room["id"], team, player_id, body.philosophy)
    return _announce(request.app, room)


@app.post("/api/rooms/{code}/seats/{team}/ready")
async def set_ready(code: str, team: str, body: ReadyRequest, request: Request,
                    player_id: int = Depends(current_player)):
    connection = request.app.state.conn
    room = _room_or_404(connection, code)
    _require_own_seat(connection, room["id"], team, player_id)
    with _rules():
        rooms.set_ready(connection, room["id"], team, body.ready)
    return _announce(request.app, room)


@app.post("/api/rooms/{code}/start")
async def start(code: str, body: StartRequest, request: Request,
                player_id: int = Depends(current_player)):
    """Kick off. Whoever calls this holds physics for the whole match."""
    connection = request.app.state.conn
    room = _room_or_404(connection, code)
    _require_seated(connection, room["id"], player_id)
    with _rules():
        rooms.start_match(connection, room["id"], body.host_client_id)
    snapshot = _announce(request.app, room)
    request.app.state.bus.publish(WALL, {"type": "wall", "rooms": rooms.live(connection)})
    return snapshot


def _room_or_404(connection, code):
    room = rooms.by_code(connection, code)
    if room is None:
        raise HTTPException(404, f"there is no room {code}")
    return room


def _require_own_seat(connection, room_id, team, player_id):
    seat = connection.execute(
        "SELECT player_id FROM seat WHERE room_id = ? AND team = ?", (room_id, team)
    ).fetchone()
    if seat is None or seat["player_id"] != player_id:
        raise HTTPException(403, f"the {team} dugout is not yours")


def _require_seated(connection, room_id, player_id):
    seat = connection.execute(
        "SELECT 1 FROM seat WHERE room_id = ? AND player_id = ?", (room_id, player_id)
    ).fetchone()
    if seat is None:
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
