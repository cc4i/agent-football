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
