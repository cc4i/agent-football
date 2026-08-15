"""The agent has to be allowed to run its own Playwright script.

The SDK ships confirm_run_command as the default, which denies run_command
outright when there is no interactive handler. The dugout is a server with
nobody to confirm, and stage 2 is entirely about the agent running a script it
just wrote, so the policy set has to be stated explicitly.

The other half of that set is the file boundary, and it is checked here in two
different ways for two different reasons. The denials the dugout writes itself
carry a predicate this process evaluates, so they are tested by pushing a tool
call past the hook and reading the answer. The workspace scoping is not: up to
SDK 0.1.10 workspace_only() carried a path predicate that the hook evaluated in
process, and since 0.1.11 the builder discards the paths and the hook skips its
own rules by name, because containment moved into the localharness binary and
comes from `workspaces` on the config. Nothing here decides it any more, so a
test that asserted a denial from it would be asserting the SDK's internals and
would have to be deleted again on the next bump. What is left, and what those
check, is that the dugout still asks for the scoping and still names the two
directories it means.
"""

import pytest

from google.antigravity import types
from google.antigravity.hooks import policy as P

import session

REPO = str(session.REPO_ROOT)


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """A scratch directory outside the repository, as the real one is."""
    monkeypatch.setenv("DUGOUT_SCRATCH_DIR", str(tmp_path))
    return tmp_path


async def decide(tool, path=None):
    hook = P.enforce(session._policies())
    call = types.ToolCall(name=tool, args={}, id="c1", canonical_path=path)
    return await hook.run(None, call)


async def approved(tool, path=None):
    return (await decide(tool, path)).allow


def refused_as_the_harness_asks(path):
    """Whether a call shaped the way the harness really asks would be refused.

    These rules carry a predicate, which means the harness decides them by
    calling back into the SDK, and that side builds the ToolCall out of the
    wire arguments with canonical_path left empty. The hook route above fills
    it in. Reading the field alone therefore passes every test written against
    `decide` and refuses every real write, so both shapes are covered here.
    """
    return session._writes_into_the_repository(
        types.ToolCall(name="create_file", args={"TargetFile": path}))


async def test_run_command_is_allowed():
    assert await approved("run_command")


async def test_the_agent_writes_in_its_own_workspace(scratch):
    assert await approved("create_file", f"{scratch}/take_the_field.py")
    assert await approved("edit_file", f"{scratch}/take_the_field.py")


async def test_creating_a_file_in_the_repository_is_denied():
    assert not await approved("create_file", f"{REPO}/take_the_field.py")


async def test_editing_this_project_is_denied():
    assert not await approved("edit_file", f"{REPO}/dugout/session.py")


async def test_a_denial_tells_the_agent_where_to_write_instead():
    """The reason is the policy name, and it is the only steer the model gets."""
    answer = await decide("create_file", f"{REPO}/session.py")
    assert session.READ_ONLY_REPO in answer.message


def test_the_harness_shaped_call_is_read_from_its_arguments(scratch):
    """The regression: canonical_path is empty on the route that matters.

    Reading it alone refused the scratch directory too, which is every write
    the agent has, so this pins both halves.
    """
    assert refused_as_the_harness_asks(f"{REPO}/dugout/session.py")
    assert not refused_as_the_harness_asks(f"{scratch}/take_the_field.py")


def test_a_wire_uri_is_normalized_before_it_is_matched():
    """The callback route hands over raw file:// URIs, unnormalized."""
    assert refused_as_the_harness_asks(f"file://{REPO}/session.py")


async def test_an_unplaceable_write_is_denied():
    """No path on the call at all, so the boundary fails closed."""
    assert not await approved("create_file")


async def test_a_scratch_file_linking_back_into_the_repo_is_denied(scratch):
    """Resolved, not string-matched, or the boundary is one symlink deep."""
    (scratch / "sneak").symlink_to(session.REPO_ROOT)
    assert not await approved("create_file", f"{scratch}/sneak/session.py")


async def test_the_repository_is_still_readable():
    """Read-only, not invisible. The agent has to be able to see the project."""
    assert await approved("view_file", f"{REPO}/dugout/session.py")


def test_every_file_tool_is_scoped_to_the_workspaces():
    scoped = {
        p.tool: p.decision
        for p in session._policies()
        if p.name == P.WORKSPACE_ONLY_POLICY_NAME
    }
    assert set(scoped) == {t.value for t in types.BuiltinTools.file_tools()}
    assert set(scoped.values()) == {P.Decision.DENY}


def test_the_workspaces_are_the_repository_and_the_scratch_directory(scratch):
    assert session._build_config().workspaces == [REPO, str(scratch)]


def test_the_scratch_directory_exists_once_the_config_is_built(scratch, monkeypatch):
    fresh = scratch / "not-yet"
    monkeypatch.setenv("DUGOUT_SCRATCH_DIR", str(fresh))
    session._build_config()
    assert fresh.is_dir()


def test_a_scratch_directory_inside_the_repository_is_refused(monkeypatch):
    """It would satisfy every rule above and mean nothing."""
    monkeypatch.setenv("DUGOUT_SCRATCH_DIR", f"{REPO}/dugout/scratch")
    with pytest.raises(ValueError, match="inside the repository"):
        session.scratch_dir()


def test_the_config_carries_the_policies():
    assert session._build_config().policies


def test_the_instructions_name_the_workspace_and_leave_no_placeholder(scratch):
    """The agent is told where to write, and told it as a real path."""
    filled = session._instructions(scratch)
    assert str(scratch) in filled
    assert str(session.DUGOUT_DIR) in filled
    assert "{{" not in filled
