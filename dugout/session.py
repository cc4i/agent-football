"""Agent lifecycle and the event multiplexer."""

import asyncio
import os
from pathlib import Path

from google.antigravity import (
    Agent,
    BuiltinTools,
    CapabilitiesConfig,
    LocalAgentConfig,
)

from subagents import SUBAGENTS
from tools.avatars import generate_team_avatars
from tools.match import get_match_status, read_player_stats
from tools.tuning import ROLE_BY_TUNING_TOOL

ACTOR_USER = "user"
ACTOR_AGENT = "antigravity"

_DONE = object()


def actor_for_tool_call(name: str) -> str:
    """Attribute a tool call to whoever made it.

    The SDK exposes no subagent identity on ToolCall, Thought or ToolResult, so
    the tool name is the handle: each subagent holds exactly one tuning tool.
    """
    role = ROLE_BY_TUNING_TOOL.get(name)
    return f"subagent:{role}-tuner" if role else ACTOR_AGENT


async def _pump(get_source, kind, queue):
    try:
        async for item in get_source():
            actor = (actor_for_tool_call(getattr(item, "name", ""))
                     if kind == "tool_call" else ACTOR_AGENT)
            await queue.put({"kind": kind, "actor": actor, "data": item})
    except Exception as exc:  # a dead stream must not kill the other two
        await queue.put({"kind": "error", "actor": ACTOR_AGENT,
                         "data": f"{kind} stream failed: {exc}"})
    finally:
        await queue.put(_DONE)


async def multiplex(response):
    """Fan thoughts, tool calls and text chunks into one ordered timeline."""
    queue: asyncio.Queue = asyncio.Queue()
    sources = (
        (lambda: response.thoughts, "thought"),
        (lambda: response.tool_calls, "tool_call"),
        (lambda: response.chunks, "text"),
    )
    tasks = [asyncio.create_task(_pump(src, kind, queue)) for src, kind in sources]

    remaining = len(tasks)
    try:
        while remaining:
            event = await queue.get()
            if event is _DONE:
                remaining -= 1
                continue
            yield event
    finally:
        for task in tasks:
            task.cancel()

    try:
        usage = response.usage_metadata
    except Exception:
        usage = None
    yield {"kind": "usage", "actor": ACTOR_AGENT, "data": usage}


REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENT = None


class AgentUnavailable(RuntimeError):
    """The SDK could not start an agent, almost always because agy is not logged in."""


def _build_config() -> LocalAgentConfig:
    return LocalAgentConfig(
        system_instructions=(Path(__file__).parent / "instructions.md").read_text(),
        capabilities=CapabilitiesConfig(
            enable_subagents=True,
            enabled_tools=[
                BuiltinTools.RUN_COMMAND,
                BuiltinTools.CREATE_FILE,
                BuiltinTools.EDIT_FILE,
                BuiltinTools.VIEW_FILE,
                BuiltinTools.LIST_DIR,
                BuiltinTools.START_SUBAGENT,
                BuiltinTools.FINISH,
            ],
        ),
        tools=[generate_team_avatars, get_match_status, read_player_stats],
        subagents=list(SUBAGENTS),
        workspaces=[str(REPO_ROOT)],
        vertex=True,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION"),
    )


def get_agent():
    global _AGENT
    if _AGENT is None:
        try:
            _AGENT = Agent(_build_config())
        except Exception as exc:
            raise AgentUnavailable(str(exc)) from exc
    return _AGENT


def agent_health() -> dict:
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return {"ok": False,
                "detail": "GOOGLE_CLOUD_PROJECT is not set. Check dugout/.env."}
    try:
        get_agent()
    except AgentUnavailable as exc:
        return {"ok": False,
                "detail": f"Antigravity could not start. Run `agy login` in a "
                          f"terminal, then reload. ({exc})"}
    return {"ok": True, "detail": "ready"}
