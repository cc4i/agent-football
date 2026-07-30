"""Dugout: a chat front end for an in-process Antigravity agent."""

import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from session import (
    ACTOR_USER,
    AgentUnavailable,
    agent_health,
    get_agent,
    multiplex,
    restart_agent,
    start_agent,
    stop_agent,
)
from skills import load_skills
from stages import begin_session, stage_status
from tools.avatars import SPRITE_DIR
from tools.match import read_status

load_dotenv()

STATIC_DIR = Path(__file__).parent / "static"
GAME_SERVICES = {"pitch": 5173, "coach": 8000, "captain": 8001}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A failed start must not stop the server: the page needs to boot so it can
    # render the red banner explaining why the agent is unreachable.
    try:
        await start_agent()
    except AgentUnavailable:
        pass
    yield
    await stop_agent()


app = FastAPI(title="Dugout", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def game_services() -> dict:
    """One TCP connect per service. Enough to light the header dots."""
    up = {}
    for name, port in GAME_SERVICES.items():
        try:
            # "localhost", not a literal, so both address families are tried:
            # Vite binds ::1 alone while the ADK servers bind 127.0.0.1.
            with socket.create_connection(("localhost", port), timeout=0.25):
                up[name] = True
        except OSError:
            up[name] = False
    return up


class ChatRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message cannot be blank")
        return stripped


def kit_preview(team: str) -> dict:
    """Where the game serves the strip just generated, and when."""
    names = [f"player_{team}_team.png", f"goalkeeper_{team}_team.png"]
    live = [n for n in names if (SPRITE_DIR / n).exists()]
    newest = max((( SPRITE_DIR / n).stat().st_mtime for n in live), default=0)
    return {"team": team,
            "images": [f"/assets/sprites/{n}" for n in live],
            "at": int(newest)}


def _frame(kind: str, actor: str, payload, step=None) -> str:
    body = json.dumps({"actor": actor, "payload": payload, "step": step},
                      default=str)
    return f"event: {kind}\ndata: {body}\n\n"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"agent": agent_health(), "game": game_services(),
            "match": read_status()}


@app.get("/stages")
def stages():
    return stage_status()


@app.get("/skills")
def skills():
    """What Antigravity was told. The team sheet links to it."""
    return load_skills()


@app.post("/reset")
async def reset():
    """Start the quest over: blank the stages and forget the conversation."""
    begin_session()
    try:
        await restart_agent()
    except AgentUnavailable:
        # The quest still clears. /health already explains an unreachable
        # agent, and refusing to reset would strand the manager on someone
        # else's half-finished quest.
        pass
    return stage_status()


async def _turn(message: str):
    """Yield one turn's frames. Never raises: failures become error frames."""
    try:
        agent = get_agent()
        response = await agent.chat(message)
    except AgentUnavailable as exc:
        yield _frame("error", "antigravity", str(exc))
        return
    except Exception as exc:
        yield _frame("error", "antigravity", f"the agent failed to start: {exc}")
        return

    rebranded = []
    try:
        async for event in multiplex(response):
            payload = event["data"]
            if event["kind"] == "tool_call":
                payload = {"name": payload.name, "args": payload.args}
                if payload["name"] == "generate_team_avatars":
                    rebranded.append(payload["args"].get("team", "blue"))
            elif event["kind"] == "usage" and payload is not None:
                payload = {"total": getattr(payload, "total_token_count", None)}
            yield _frame(event["kind"], event["actor"], payload,
                         event.get("step"))
        # The sprites only exist on disk once the tool has returned, so the kit
        # is shown after the stream rather than alongside the call.
        for team in dict.fromkeys(rebranded):
            yield _frame("kit", "antigravity", kit_preview(team))
    except Exception as exc:
        yield _frame("error", "antigravity", str(exc))
    finally:
        # Halt closes the connection, which closes this generator. Without the
        # cancel the SDK keeps working on a turn nobody is reading. A no-op
        # once the turn has finished on its own.
        await response.cancel()


@app.post("/chat")
def chat(request: ChatRequest):
    async def stream():
        yield _frame("user", ACTOR_USER, request.message)
        async for frame in _turn(request.message):
            yield frame
        yield _frame("stage_done", "antigravity", stage_status())

    return StreamingResponse(stream(), media_type="text/event-stream")
