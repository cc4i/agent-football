"""The agent has to be allowed to run its own Playwright script.

The SDK ships confirm_run_command as the default, which denies run_command
outright when there is no interactive handler. The dugout is a server with
nobody to confirm, and stage 2 is entirely about the agent running a script it
just wrote, so the policy set has to be stated explicitly.
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


async def test_writing_outside_the_workspace_is_denied():
    assert not await approved("create_file", "/etc/passwd")


def test_the_config_carries_the_policies():
    assert session._build_config().policies
