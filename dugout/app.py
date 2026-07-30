"""Dugout: a chat front end for an in-process Antigravity agent."""

import json
import socket
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from session import ACTOR_USER, AgentUnavailable, agent_health, get_agent, multiplex
from stages import stage_status

load_dotenv()

STATIC_DIR = Path(__file__).parent / "static"
GAME_SERVICES = {"pitch": 5173, "coach": 8000, "captain": 8001}

app = FastAPI(title="Dugout")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def game_services() -> dict:
    """One TCP connect per service. Enough to light the header dots."""
    up = {}
    for name, port in GAME_SERVICES.items():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
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


def _frame(kind: str, actor: str, payload) -> str:
    body = json.dumps({"actor": actor, "payload": payload}, default=str)
    return f"event: {kind}\ndata: {body}\n\n"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"agent": agent_health(), "game": game_services()}


@app.get("/stages")
def stages():
    return stage_status()


async def _turn(message: str):
    """Yield one turn's frames. Never raises: failures become error frames."""
    try:
        agent = get_agent()
        response = agent.chat(message)
    except AgentUnavailable as exc:
        yield _frame("error", "antigravity", str(exc))
        return
    except Exception as exc:
        yield _frame("error", "antigravity", f"the agent failed to start: {exc}")
        return

    try:
        async for event in multiplex(response):
            payload = event["data"]
            if event["kind"] == "tool_call":
                payload = {"name": payload.name, "args": payload.args}
            yield _frame(event["kind"], event["actor"], payload)
    except Exception as exc:
        yield _frame("error", "antigravity", str(exc))


@app.post("/chat")
def chat(request: ChatRequest):
    async def stream():
        yield _frame("user", ACTOR_USER, request.message)
        async for frame in _turn(request.message):
            yield frame
        yield _frame("stage_done", "antigravity", stage_status())

    return StreamingResponse(stream(), media_type="text/event-stream")
