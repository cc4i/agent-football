"""Agent lifecycle and the event multiplexer."""

import asyncio
import os
from contextlib import AsyncExitStack
from importlib.metadata import version
from pathlib import Path

from google.antigravity import (
    Agent,
    BuiltinTools,
    CapabilitiesConfig,
    LocalAgentConfig,
)
from google.antigravity.connections.local.local_connection_config import (
    WIRE_PATH_ARGUMENT_KEYS,
    normalize_wire_path,
)
from google.antigravity.hooks import policy
from google.antigravity.types import Text, ToolCall

import channel
from skills import SKILLS_DIR
from subagents import SUBAGENTS
from tools.avatars import generate_team_avatars
from tools.match import get_match_status, read_player_stats
from tools.shout import shout_to_the_team
from tools.tuning import ROLE_BY_TUNING_TOOL, TUNING_TOOL_BY_ROLE

ACTOR_USER = "user"
ACTOR_AGENT = "antigravity"
ACTOR_GAME = "game"

# The header chip names the SDK, and the number it shows is read off the
# installed distribution rather than written into the page. A literal in the
# markup is right until the first bump and quietly wrong afterwards, which on a
# talk about this SDK is the one number in the room worth getting right.
SDK_VERSION = version("google-antigravity")

_DONE = object()


def actor_for_tool_call(name: str) -> str:
    """Attribute a tool call to whoever made it.

    The SDK exposes no subagent identity on ToolCall, Thought or ToolResult, so
    the tool name is the handle: each subagent holds exactly one tuning tool.
    """
    role = ROLE_BY_TUNING_TOOL.get(name)
    return f"subagent:{role}-tuner" if role else ACTOR_AGENT


def actor_for_tool_result(name: str) -> str:
    """Attribute a tool's return value to whoever decided it.

    Only the shout differs from its own call. Antigravity types the shout, so
    the call is Antigravity's, but the game's coach, captain and four player
    agents pick the numbers that come back, so the result is theirs.
    """
    if name == "shout_to_the_team":
        return ACTOR_GAME
    return actor_for_tool_call(name)


_ACTOR_BY_KIND = {"tool_call": actor_for_tool_call,
                  "tool_result": actor_for_tool_result}


async def _pump(get_source, kind, queue, signal_done=True):
    try:
        async for item in get_source():
            pick = _ACTOR_BY_KIND.get(kind)
            actor = pick(getattr(item, "name", "")) if pick else ACTOR_AGENT
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
        if signal_done:
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
    """Fan thoughts, tool calls, text chunks and tool results into one timeline."""
    queue: asyncio.Queue = asyncio.Queue()
    channel.open_channel()
    sources = (
        (lambda: response.thoughts, "thought"),
        (lambda: response.tool_calls, "tool_call"),
        (lambda: _text_deltas(response), "text"),
    )
    tasks = [asyncio.create_task(_pump(src, kind, queue)) for src, kind in sources]

    # Only the response's own three streams can finish. The channel is open
    # until the turn is over, so its pump is cancelled below rather than
    # counted here, and a turn does not hang waiting for a fourth _DONE.
    remaining = len(tasks)
    tasks.append(asyncio.create_task(_pump(channel.results, "tool_result", queue, signal_done=False)))
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
        channel.close_channel()

    try:
        usage = response.usage_metadata
    except Exception:
        usage = None
    yield {"kind": "usage", "actor": ACTOR_AGENT, "data": usage}


DUGOUT_DIR = Path(__file__).resolve().parent
REPO_ROOT = DUGOUT_DIR.parent

# The agent writes somewhere, because stage 2 is it writing a Playwright script
# and running it. That somewhere is not the repository. This is the default;
# DUGOUT_SCRATCH_DIR moves it, and wherever it is moved to has to be outside
# the repo or the boundary below draws itself around nothing.
DEFAULT_SCRATCH_DIR = Path.home() / ".dugout" / "workspace"

_AGENT = None
_STACK = None
_START_ERROR = None


class AgentUnavailable(RuntimeError):
    """The SDK could not start an agent, almost always because agy is not logged in."""


def _inside(path: Path, root: Path) -> bool:
    """Whether `path` lands in `root`, symlinks followed.

    Resolved rather than compared as text, so a scratch file that is really a
    link back into the repository is seen for what it is.
    """
    try:
        return path.expanduser().resolve().is_relative_to(root)
    except OSError:
        return False


def scratch_dir() -> Path:
    """The one directory the agent may write to.

    Read at call time, not at import: app.py loads the .env after it imports
    this module, so anything read at import would never see DUGOUT_SCRATCH_DIR.
    """
    configured = os.environ.get("DUGOUT_SCRATCH_DIR")
    scratch = (Path(configured) if configured else DEFAULT_SCRATCH_DIR)
    scratch = scratch.expanduser().resolve()
    if _inside(scratch, REPO_ROOT):
        raise ValueError(
            f"DUGOUT_SCRATCH_DIR points inside the repository ({scratch}). It "
            f"is the one place the agent is allowed to write, so aiming it "
            f"back at the repo removes the only boundary there is.")
    return scratch


# The agent is shown a denied policy's name as the reason, so this one is
# written as a sentence. A refusal that says where to go instead costs one
# retry; a bare "denied" costs the model several guesses.
READ_ONLY_REPO = "the repository is read-only, write to your scratch workspace instead"


def _target_of(call: ToolCall) -> str | None:
    """The path a file tool is aimed at.

    canonical_path is the field the SDK documents for exactly this, and on the
    in-process hook route it is set. On the route these rules actually take it
    is not. A policy carrying a predicate is decided by a callback from the
    harness, and that side builds the ToolCall from the wire arguments alone
    (event_processor._handle_policy_decision_request), normalizing neither the
    paths nor the field, where the hook side does both. So the arguments are
    read here directly, by the SDK's own keys and through its own normalizer,
    rather than trusting a field that arrives empty. Both are imported rather
    than copied: if a bump moves them, this should fail loudly at startup, not
    quietly stop matching.
    """
    if call.canonical_path:
        return call.canonical_path
    # Sorted, so two path arguments on one call cannot decide it differently
    # from one run to the next.
    for key in sorted(WIRE_PATH_ARGUMENT_KEYS):
        value = call.args.get(key)
        if isinstance(value, str) and value:
            return normalize_wire_path(value)
    return None


def _writes_into_the_repository(call: ToolCall) -> bool:
    """Whether a write tool is aimed at the repository.

    A path that cannot be read off the call counts as inside: a write nobody
    can place is not one this can vouch for.
    """
    target = _target_of(call)
    if target is None:
        return True
    return _inside(Path(target), REPO_ROOT)


def _policies():
    """Let the agent run the script it just wrote, and keep it out of the repo.

    Three kinds of rule, written in the order the SDK sorts them into anyway -
    specific denials outrank the wildcard allow.

    workspace_only is a marker now rather than a check. Up to SDK 0.1.10 it
    carried a path predicate; since 0.1.11 the builder discards the paths it is
    handed and the in-process hook skips every rule it names, because the
    boundary moved into the localharness binary, which takes it from
    `workspaces` on the config below. The two lists have to stay in step: the
    config's is the one the harness actually matches file tools against, and it
    is what makes the repository readable and the scratch directory writable.

    The denials on top of it are this module's own, and they are what the
    workspace list cannot express: the repository is in `workspaces` so the
    agent can read it, and these take the writing back out again. They carry a
    predicate, so the harness calls back into the hook here to decide each one.

    run_command is left wide open, and that is the honest limit of all of the
    above. Shell has no path argument to match on, so the agent can still write
    anywhere you can, repository included, by running a command that does. The
    file rules stop it editing this code as a matter of course; they are not a
    sandbox against one that means to. The SDK default here, confirm_run_command,
    denies the tool outright when there is no interactive handler to ask, and
    nothing can ask in a server. A command allowlist tight enough to be
    meaningful rejects the compound invocations stage 2 needs. Run this on your
    own machine against a repo you trust, not on a shared host or against
    untrusted input.
    """
    return [
        *policy.workspace_only([str(REPO_ROOT), str(scratch_dir())]),
        policy.deny(BuiltinTools.CREATE_FILE.value,
                    when=_writes_into_the_repository, name=READ_ONLY_REPO),
        policy.deny(BuiltinTools.EDIT_FILE.value,
                    when=_writes_into_the_repository, name=READ_ONLY_REPO),
        policy.allow_all(),
    ]


def _instructions(scratch: Path) -> str:
    """The system prompt, with the two paths that are not the agent's to guess.

    Substituted rather than written into the markdown, so where the agent may
    write is stated in exactly one place. Plain replacement rather than
    str.format, because the instructions carry a JavaScript arrow function and
    its braces are not fields.
    """
    return ((DUGOUT_DIR / "instructions.md").read_text()
            .replace("{{SCRATCH}}", str(scratch))
            .replace("{{DUGOUT}}", str(DUGOUT_DIR)))


def _build_config() -> LocalAgentConfig:
    scratch = scratch_dir()
    # The harness is handed this as a workspace, so it has to be there before
    # the agent starts rather than on the first write.
    scratch.mkdir(parents=True, exist_ok=True)
    return LocalAgentConfig(
        policies=_policies(),
        system_instructions=_instructions(scratch),
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
        # The repo first, because it is the one the agent reads and the one
        # relative paths in a shell command resolve against. The scratch
        # directory is the only one it can write in; _policies takes the
        # writing back out of the repo.
        workspaces=[str(REPO_ROOT), str(scratch)],
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
        state = {"ok": True, "detail": "ready"}
    elif not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        state = {"ok": False,
                 "detail": "GOOGLE_CLOUD_PROJECT is not set. Check dugout/.env."}
    else:
        state = {"ok": False,
                 "detail": f"Antigravity could not start. Run `agy login` in a "
                           f"terminal, then restart the dugout. "
                           f"({_START_ERROR or 'session not started'})"}
    # The version rides along with the state because the chip shows both, and
    # an offline agent is still running some version of the SDK.
    return {**state, "version": SDK_VERSION}
