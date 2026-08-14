"""The agent has to be allowed to run its own Playwright script.

The SDK ships confirm_run_command as the default, which denies run_command
outright when there is no interactive handler. The dugout is a server with
nobody to confirm, and stage 2 is entirely about the agent running a script it
just wrote, so the policy set has to be stated explicitly.

The other half of that set, the workspace boundary, is checked here by reading
the policies rather than by pushing a tool call past the hook, and the reason
is worth writing down. Up to SDK 0.1.10 workspace_only() carried a path
predicate that the hook evaluated in process. Since 0.1.11 the builder discards
the paths and the hook skips its own rules by name, because containment is
enforced by the localharness binary from `workspaces` on the config. Nothing in
this process decides it any more, so a test that asserts a denial here would be
asserting the SDK's internals and would have to be deleted again on the next
bump. What is left, and what these check, is that the dugout still asks for the
scoping and still names the repository and nothing else.
"""

from google.antigravity import types
from google.antigravity.hooks import policy as P

import session

WORKSPACE = str(session.REPO_ROOT)


async def approved(tool, path=None):
    hook = P.enforce(session._policies())
    call = types.ToolCall(name=tool, args={}, id="c1", canonical_path=path)
    return (await hook.run(None, call)).allow


async def test_run_command_is_allowed():
    assert await approved("run_command")


async def test_writing_inside_the_workspace_is_allowed():
    assert await approved("create_file", f"{WORKSPACE}/take_the_field.py")


def test_every_file_tool_is_scoped_to_the_workspace():
    scoped = {
        p.tool: p.decision
        for p in session._policies()
        if p.name == P.WORKSPACE_ONLY_POLICY_NAME
    }
    assert set(scoped) == {t.value for t in types.BuiltinTools.file_tools()}
    assert set(scoped.values()) == {P.Decision.DENY}


def test_the_workspace_is_the_repository_and_nothing_else():
    assert session._build_config().workspaces == [WORKSPACE]


def test_the_config_carries_the_policies():
    assert session._build_config().policies
