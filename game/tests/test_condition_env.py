"""The MCP server is spawned with the credential it needs to reach the arena.

The condition tools run in a subprocess behind stdio, and the MCP client does
not hand that subprocess this process's environment: it passes a fixed safe
list -- HOME, LOGNAME, PATH, SHELL, TERM, USER -- and drops everything else.
So a captain server started with ARENA_SERVICE_TOKEN set spawned a server that
could not see it, and every injury came back "the arena refuses writes from the
agents" while the chain carried on as if it had been filed.

Nothing in the unit tests could catch that, because they set the variable in
the process running them. This asks the question the subprocess asks: what is
in the environment it is actually handed?
"""

from agents.specialist_agents import tools


def test_the_server_is_handed_the_arena_credential(monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    monkeypatch.setenv("ARENA_URL", "http://arena.internal:8003")
    assert tools.condition_server_env() == {
        "ARENA_SERVICE_TOKEN": "s3cret",
        "ARENA_URL": "http://arena.internal:8003",
    }


def test_an_unset_variable_is_left_out_rather_than_passed_empty(monkeypatch):
    # An empty ARENA_URL would override the server's own default and point it
    # at nothing, which is worse than not passing it at all.
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    monkeypatch.delenv("ARENA_URL", raising=False)
    assert tools.condition_server_env() == {"ARENA_SERVICE_TOKEN": "s3cret"}


def test_the_safe_defaults_travel_too(monkeypatch):
    # PATH is on the MCP client's own inherit list, and the subprocess needs it
    # to find anything. Naming the arena's variables must not cost it that.
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    monkeypatch.setenv("PATH", "/usr/bin")
    toolset = tools.make_condition_toolset()[0]
    passed = toolset._mcp_session_manager._connection_params.server_params.env
    assert passed["PATH"] == "/usr/bin"
    assert passed["ARENA_SERVICE_TOKEN"] == "s3cret"
