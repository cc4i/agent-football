import pytest

import session


@pytest.fixture(autouse=True)
def clean_agent():
    session._AGENT = None
    session._STACK = None
    session._START_ERROR = None
    yield
    session._AGENT = None
    session._STACK = None
    session._START_ERROR = None


class FakeAgent:
    """Mirrors the SDK: usable only inside its own async context."""

    def __init__(self, config=None):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False


async def test_start_agent_enters_the_async_context(monkeypatch):
    # Agent.chat raises "session not started" unless the context is entered.
    monkeypatch.setattr(session, "Agent", FakeAgent)
    monkeypatch.setattr(session, "_build_config", lambda: None)

    agent = await session.start_agent()
    assert agent.entered
    assert session.get_agent() is agent


async def test_stop_agent_exits_the_context(monkeypatch):
    monkeypatch.setattr(session, "Agent", FakeAgent)
    monkeypatch.setattr(session, "_build_config", lambda: None)

    agent = await session.start_agent()
    await session.stop_agent()
    assert agent.exited
    with pytest.raises(session.AgentUnavailable):
        session.get_agent()


async def test_start_agent_is_idempotent(monkeypatch):
    monkeypatch.setattr(session, "Agent", FakeAgent)
    monkeypatch.setattr(session, "_build_config", lambda: None)

    first = await session.start_agent()
    assert await session.start_agent() is first


async def test_a_failed_start_is_reported_by_health(monkeypatch):
    class Boom:
        def __init__(self, config=None):
            raise RuntimeError("agy is not logged in")

    monkeypatch.setattr(session, "Agent", Boom)
    monkeypatch.setattr(session, "_build_config", lambda: None)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")

    with pytest.raises(session.AgentUnavailable):
        await session.start_agent()

    health = session.agent_health()
    assert health["ok"] is False
    assert "agy is not logged in" in health["detail"]


def test_health_is_not_ready_before_startup(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    assert session.agent_health()["ok"] is False


async def test_health_is_ready_once_started(monkeypatch):
    monkeypatch.setattr(session, "Agent", FakeAgent)
    monkeypatch.setattr(session, "_build_config", lambda: None)

    await session.start_agent()
    assert session.agent_health() == {"ok": True, "detail": "ready"}


def test_health_calls_out_a_missing_project(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert "GOOGLE_CLOUD_PROJECT" in session.agent_health()["detail"]
