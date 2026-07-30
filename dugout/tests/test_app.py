import json

from fastapi.testclient import TestClient

import app as app_module


def client(monkeypatch, **overrides):
    for name, value in overrides.items():
        monkeypatch.setattr(app_module, name, value)
    return TestClient(app_module.app)


def test_health_reports_the_agent_state(monkeypatch):
    c = client(monkeypatch,
               agent_health=lambda: {"ok": False, "detail": "no login"},
               game_services=lambda: {"pitch": True, "coach": True, "captain": True})
    body = c.get("/health").json()
    assert body["agent"] == {"ok": False, "detail": "no login"}


def test_health_reports_the_three_game_services(monkeypatch):
    c = client(monkeypatch,
               agent_health=lambda: {"ok": True, "detail": "ready"},
               game_services=lambda: {"pitch": True, "coach": False, "captain": False})
    assert c.get("/health").json()["game"] == {
        "pitch": True, "coach": False, "captain": False}


def test_game_services_reports_false_for_a_closed_port(monkeypatch):
    # 9 is discard, reliably closed on a dev machine.
    monkeypatch.setattr(app_module, "GAME_SERVICES", {"nothing": 9})
    assert app_module.game_services() == {"nothing": False}


def test_stages_returns_the_quest(monkeypatch):
    c = client(monkeypatch, stage_status=lambda: [{"id": "rebrand", "done": True}])
    assert c.get("/stages").json() == [{"id": "rebrand", "done": True}]


def test_chat_rejects_an_empty_message(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/chat", json={"message": "   "}).status_code == 422


def test_chat_streams_events_when_the_agent_is_down(monkeypatch):
    def boom():
        raise app_module.AgentUnavailable("agy is not logged in")

    c = client(monkeypatch, get_agent=boom, stage_status=lambda: [])
    with c.stream("POST", "/chat", json={"message": "hello"}) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body
    assert "agy is not logged in" in body
    assert "event: stage_done" in body


def test_chat_streams_every_event_kind_with_actor_and_payload(monkeypatch):
    class FakeToolCall:
        name = "read_player_stats"
        args = {"role": "forward"}

    class FakeAgent:
        def chat(self, message):
            return object()

    async def fake_multiplex(response):
        yield {"kind": "thought", "actor": "antigravity", "data": "checking the score"}
        yield {"kind": "tool_call", "actor": "subagent:forward-tuner", "data": FakeToolCall()}
        yield {"kind": "text", "actor": "antigravity", "data": "done"}
        yield {"kind": "error", "actor": "antigravity", "data": "a plain string"}
        yield {"kind": "usage", "actor": "antigravity", "data": None}

    c = client(monkeypatch, get_agent=lambda: FakeAgent(), multiplex=fake_multiplex,
               stage_status=lambda: [])
    with c.stream("POST", "/chat", json={"message": "hello"}) as r:
        body = "".join(r.iter_text())

    frames = []
    for block in body.strip().split("\n\n"):
        kind = next(l[7:] for l in block.splitlines() if l.startswith("event: "))
        data = json.loads(next(l[6:] for l in block.splitlines() if l.startswith("data: ")))
        frames.append((kind, data))

    kinds = [k for k, _ in frames]
    assert kinds == ["user", "thought", "tool_call", "text", "error", "usage", "stage_done"]
    assert all("actor" in d and "payload" in d for _, d in frames)

    tool_frame = dict(frames)["tool_call"]
    assert tool_frame["actor"] == "subagent:forward-tuner"
    assert tool_frame["payload"] == {"name": "read_player_stats", "args": {"role": "forward"}}
    assert dict(frames)["error"]["payload"] == "a plain string"


async def test_the_stream_closes_cleanly_when_the_client_disconnects(monkeypatch):
    class FakeAgent:
        def chat(self, message):
            return object()

    async def fake_multiplex(response):
        yield {"kind": "thought", "actor": "antigravity", "data": "one"}
        yield {"kind": "text", "actor": "antigravity", "data": "two"}

    monkeypatch.setattr(app_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(app_module, "multiplex", fake_multiplex)
    monkeypatch.setattr(app_module, "stage_status", lambda: [])

    # Test _turn directly - it must handle close without yielding in finally
    gen = app_module._turn("hello")
    assert await gen.__anext__()
    await gen.aclose()  # this raised RuntimeError under the yield-in-finally version


def test_index_is_served(monkeypatch):
    c = client(monkeypatch)
    r = c.get("/")
    assert r.status_code == 200
    assert "Dugout" in r.text


def test_index_has_no_mocked_trajectory_left(monkeypatch):
    c = client(monkeypatch)
    body = c.get("/").text
    assert "generate_team_avatars" not in body
    assert "handoff" not in body
    assert "—" not in body
    assert "58 events" not in body
    assert "24.1k tokens" not in body
    assert "· working" not in body


def test_usage_payload_is_converted_to_a_plain_total(monkeypatch):
    class FakeUsage:
        total_token_count = 24100

    class FakeAgent:
        def chat(self, message):
            return object()

    async def fake_multiplex(response):
        yield {"kind": "usage", "actor": "antigravity", "data": FakeUsage()}

    c = client(monkeypatch, get_agent=lambda: FakeAgent(),
               multiplex=fake_multiplex, stage_status=lambda: [])
    with c.stream("POST", "/chat", json={"message": "hi"}) as r:
        body = "".join(r.iter_text())

    frame = next(b for b in body.split("\n\n") if "event: usage" in b)
    data = json.loads(next(l[6:] for l in frame.splitlines() if l.startswith("data: ")))
    assert data["payload"] == {"total": 24100}


def test_usage_payload_of_none_serialises_without_error(monkeypatch):
    class FakeAgent:
        def chat(self, message):
            return object()

    async def fake_multiplex(response):
        yield {"kind": "usage", "actor": "antigravity", "data": None}

    c = client(monkeypatch, get_agent=lambda: FakeAgent(),
               multiplex=fake_multiplex, stage_status=lambda: [])
    with c.stream("POST", "/chat", json={"message": "hi"}) as r:
        body = "".join(r.iter_text())

    assert "event: usage" in body
    frame = next(b for b in body.split("\n\n") if "event: usage" in b)
    data = json.loads(next(l[6:] for l in frame.splitlines() if l.startswith("data: ")))
    assert data["payload"] is None
