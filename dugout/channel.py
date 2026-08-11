"""Where a tool's own account of what it changed reaches the match log.

The SDK's chunk stream carries thoughts, text and tool calls, and nothing else.
A tool's return value goes straight from `handle_tool_call` back to the model
and is never queued as a step, so no filter over `response.chunks` can find it.
A tool that wants the browser to see its numbers has to say so itself.

The tools run in the server's own process, so a queue is all this takes. It is
opened per turn, which is also what keeps one turn's panel out of the next.
The channel carries one turn at a time; overlapping turns would split results.
"""

import asyncio

from google.antigravity.types import ToolResult

_queue: asyncio.Queue | None = None
_loop: asyncio.AbstractEventLoop | None = None


def open_channel() -> None:
    """Start a fresh channel for the turn about to run."""
    global _queue, _loop
    _queue = asyncio.Queue()
    _loop = asyncio.get_running_loop()


def close_channel() -> None:
    """Shut the channel, so a tool called outside a turn publishes into nothing."""
    global _queue, _loop
    _queue = _loop = None


def publish(name: str, result) -> None:
    """Announce a tool's return value to the turn in progress.

    Safe from a worker thread, which is where it is usually called: the SDK
    runs sync tools through `asyncio.to_thread`, so every tune arrives off the
    event loop while a shout arrives on it.
    """
    queue, loop = _queue, _loop
    if queue is None or loop is None:
        # A tool called outside a turn, which is how the tool tests call them.
        return
    loop.call_soon_threadsafe(queue.put_nowait, ToolResult(name=name, result=result))


async def results():
    """Every result published on the open channel, until the reader is cancelled.

    This never ends on its own. The channel is open for as long as the turn is,
    and `multiplex` cancels the pump when the turn's own streams have finished.
    """
    queue = _queue
    if queue is None:
        return
    while True:
        yield await queue.get()
