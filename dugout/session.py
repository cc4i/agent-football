"""Agent lifecycle and the event multiplexer."""

import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path

from google.antigravity import (
    Agent,
    BuiltinTools,
    CapabilitiesConfig,
    LocalAgentConfig,
)
from google.antigravity.hooks import policy
from google.antigravity.types import Text

from skills import SKILLS_DIR
from subagents import SUBAGENTS
from tools.avatars import generate_team_avatars
from tools.match import get_match_status, read_player_stats
from tools.shout import shout_to_the_team
from tools.tuning import ROLE_BY_TUNING_TOOL, TUNING_TOOL_BY_ROLE

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
            event = {"kind": kind, "actor": actor, "data": item}
            if kind == "text":
                # Concurrent subagents share this stream. The step index is what
                # keeps their sentences from being spliced into one another.
                event["data"], event["step"] = item.text, item.step_index
            await queue.put(event)
    except Exception as exc:  # a dead stream must not kill the other two
        await queue.put({"kind": "error", "actor": ACTOR_AGENT,
                         "data": f"{kind} stream failed: {exc}"})
    finally:
        await queue.put(_DONE)


async def _text_deltas(response):
    """The model's spoken text, and nothing else.

    `chunks` is the unfiltered stream: thoughts and tool calls surface on it too,
    so passing it straight through doubles every event that already has its own
    pump and prints the raw object repr in the match log.
    """
    async for chunk in response.chunks:
        if isinstance(chunk, Text):
            yield chunk


async def multiplex(response):
    """Fan thoughts, tool calls and text chunks into one ordered timeline."""
    queue: asyncio.Queue = asyncio.Queue()
    sources = (
        (lambda: response.thoughts, "thought"),
        (lambda: response.tool_calls, "tool_call"),
        (lambda: _text_deltas(response), "text"),
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
_STACK = None
_START_ERROR = None


class AgentUnavailable(RuntimeError):
    """The SDK could not start an agent, almost always because agy is not logged in."""


def _policies():
    """Let the agent run the script it just wrote, but only inside the repo.

    The SDK default is confirm_run_command, which denies run_command outright
    when there is no interactive handler to ask. Nothing can ask in a server,
    and stage 2 is the agent running its own Playwright script, so it would
    fail every time. Path-scoped denials outrank the wildcard allow, so file
    writes outside the workspace are still refused.

    Understand what this grants before running the dugout: shell commands are
    not restricted, so the agent can run anything you can, including outside
    the repository. That is deliberate. Stage 2 exists to show the agent
    writing and launching its own Playwright script, and in practice it also
    self-heals - when the game stack is down it will restart it. A command
    allowlist tight enough to be meaningful rejects the compound invocations
    it actually needs. Run this on your own machine against a repo you trust,
    not on a shared host or against untrusted input.
    """
    return [*policy.workspace_only([str(REPO_ROOT)]), policy.allow_all()]


def _build_config() -> LocalAgentConfig:
    return LocalAgentConfig(
        policies=_policies(),
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
        # The SDK requires every subagent tool on the main config as well. The
        # guardrail still holds: each tuner is handed only its own role's tool,
        # and the instructions send tuning work to the subagents.
        tools=[generate_team_avatars, get_match_status, read_player_stats,
               shout_to_the_team,
               *TUNING_TOOL_BY_ROLE.values()],
        subagents=list(SUBAGENTS),
        skills_paths=[str(SKILLS_DIR)],
        workspaces=[str(REPO_ROOT)],
        vertex=True,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION"),
    )


async def start_agent():
    """Enter the agent's async context once, for the life of the process.

    The SDK refuses to chat outside `async with Agent(...)`, so the context has
    to outlive a single request rather than be rebuilt per turn.
    """
    global _AGENT, _STACK, _START_ERROR
    if _AGENT is not None:
        return _AGENT
    stack = AsyncExitStack()
    try:
        _AGENT = await stack.enter_async_context(Agent(_build_config()))
    except Exception as exc:
        await stack.aclose()
        _START_ERROR = str(exc)
        raise AgentUnavailable(str(exc)) from exc
    _STACK, _START_ERROR = stack, None
    return _AGENT


async def restart_agent():
    """Throw away the conversation and start a fresh one."""
    await stop_agent()
    return await start_agent()


async def stop_agent() -> None:
    global _AGENT, _STACK
    if _STACK is not None:
        await _STACK.aclose()
    _AGENT, _STACK = None, None


def get_agent():
    if _AGENT is None:
        raise AgentUnavailable(_START_ERROR or "the agent session is not started")
    return _AGENT


def agent_health() -> dict:
    if _AGENT is not None:
        return {"ok": True, "detail": "ready"}
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return {"ok": False,
                "detail": "GOOGLE_CLOUD_PROJECT is not set. Check dugout/.env."}
    return {"ok": False,
            "detail": f"Antigravity could not start. Run `agy login` in a "
                      f"terminal, then restart the dugout. "
                      f"({_START_ERROR or 'session not started'})"}
