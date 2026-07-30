"""Agent lifecycle and the event multiplexer."""

import asyncio

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
