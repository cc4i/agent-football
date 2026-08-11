import json
import socket

from fastapi.testclient import TestClient

import app as app_module


def _noop_async(record, key):
    async def run():
        record[key] = True
    return run


class StubResponse:
    """ChatResponse exposes cancel(); _turn calls it when the client leaves."""

    async def cancel(self):
        pass


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


def test_game_services_finds_a_service_bound_only_to_ipv6(monkeypatch):
    # Vite binds localhost, which resolves to ::1 alone on macOS. Probing a
    # literal 127.0.0.1 misses it and the pitch dot reads dead mid-match.
    server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    server.bind(("::1", 0))
    server.listen(1)
    try:
        monkeypatch.setattr(
            app_module, "GAME_SERVICES", {"pitch": server.getsockname()[1]})
        assert app_module.game_services() == {"pitch": True}
    finally:
        server.close()


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
        async def chat(self, message):
            return StubResponse()

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
        async def chat(self, message):
            return StubResponse()

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


def test_turn_awaits_chat_before_streaming(monkeypatch):
    # Agent.chat is `async def` and resolves to the ChatResponse holding the
    # streams. Handing the bare coroutine to the multiplexer produces three
    # "'coroutine' object has no attribute ..." frames and no trajectory.
    resolved = StubResponse()
    seen = {}

    class FakeAgent:
        async def chat(self, message):
            return resolved

    async def fake_multiplex(response):
        seen["response"] = response
        yield {"kind": "text", "actor": "antigravity", "data": "ok"}

    c = client(monkeypatch, get_agent=lambda: FakeAgent(),
               multiplex=fake_multiplex, stage_status=lambda: [])
    with c.stream("POST", "/chat", json={"message": "hi"}) as r:
        "".join(r.iter_text())

    assert seen["response"] is resolved


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
        async def chat(self, message):
            return StubResponse()

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
        async def chat(self, message):
            return StubResponse()

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


def test_health_includes_the_live_match(monkeypatch):
    c = client(monkeypatch,
               agent_health=lambda: {"ok": True, "detail": "ready"},
               game_services=lambda: {"pitch": True, "coach": True, "captain": True},
               read_status=lambda: {"score1": 2, "score2": 1, "matchTime": 176})
    assert c.get("/health").json()["match"] == {
        "score1": 2, "score2": 1, "matchTime": 176}


async def test_a_client_disconnect_cancels_the_agent_turn(monkeypatch):
    # Halt aborts the fetch. Without this the SDK keeps burning tokens on a
    # turn nobody is listening to.
    cancelled = []

    class FakeResponse:
        async def cancel(self):
            cancelled.append(True)

    class FakeAgent:
        async def chat(self, message):
            return FakeResponse()

    async def fake_multiplex(response):
        yield {"kind": "thought", "actor": "antigravity", "data": "one"}
        yield {"kind": "thought", "actor": "antigravity", "data": "two"}

    monkeypatch.setattr(app_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(app_module, "multiplex", fake_multiplex)
    monkeypatch.setattr(app_module, "stage_status", lambda: [])

    gen = app_module._turn("hello")
    await gen.__anext__()
    await gen.aclose()
    assert cancelled == [True]


def test_reset_starts_a_fresh_quest(monkeypatch):
    called = {}

    def begin():
        called["stages"] = True

    monkeypatch.setattr(app_module, "begin_session", begin)
    monkeypatch.setattr(app_module, "restart_agent", _noop_async(called, "agent"))
    monkeypatch.setattr(app_module, "stage_status", lambda: [{"id": "rebrand", "done": False}])

    body = TestClient(app_module.app).post("/reset").json()
    assert called == {"stages": True, "agent": True}
    assert body == [{"id": "rebrand", "done": False}]


def test_reset_still_answers_when_the_agent_cannot_restart(monkeypatch):
    # The quest must clear even if agy is unreachable, or a broken login
    # leaves the manager stuck on someone else's half-finished quest.
    async def boom():
        raise app_module.AgentUnavailable("no login")

    monkeypatch.setattr(app_module, "begin_session", lambda: None)
    monkeypatch.setattr(app_module, "restart_agent", boom)
    monkeypatch.setattr(app_module, "stage_status", lambda: [])
    assert TestClient(app_module.app).post("/reset").status_code == 200


def test_skills_are_served_for_the_team_sheet(monkeypatch):
    body = TestClient(app_module.app).get("/skills").json()
    assert any(s["name"] == "winning-the-match" for s in body)
    assert all({"name", "description", "body"} <= set(s) for s in body)


def test_a_rebrand_turn_ends_with_the_new_kit(monkeypatch):
    class FakeToolCall:
        name = "generate_team_avatars"
        args = {"team": "blue", "color": "black"}

    class FakeAgent:
        async def chat(self, message):
            return StubResponse()

    async def fake_multiplex(response):
        yield {"kind": "tool_call", "actor": "antigravity", "data": FakeToolCall()}

    c = client(monkeypatch, get_agent=lambda: FakeAgent(),
               multiplex=fake_multiplex, stage_status=lambda: [])
    with c.stream("POST", "/chat", json={"message": "kit us out"}) as r:
        body = "".join(r.iter_text())

    frame = next(b for b in body.split("\n\n") if "event: kit" in b)
    data = json.loads(next(l[6:] for l in frame.splitlines() if l.startswith("data: ")))
    assert data["payload"]["team"] == "blue"
    assert data["payload"]["images"]
    assert data["payload"]["at"]


def test_a_turn_without_a_rebrand_has_no_kit_frame(monkeypatch):
    class FakeAgent:
        async def chat(self, message):
            return StubResponse()

    async def fake_multiplex(response):
        yield {"kind": "text", "actor": "antigravity", "data": "nothing to see"}

    c = client(monkeypatch, get_agent=lambda: FakeAgent(),
               multiplex=fake_multiplex, stage_status=lambda: [])
    with c.stream("POST", "/chat", json={"message": "hi"}) as r:
        assert "event: kit" not in "".join(r.iter_text())


def test_a_tuning_result_becomes_panels_with_markers():
    panels = app_module.tuning_panels({"changed": [{
        "role": "forward",
        "file": "player_state/forward.json",
        "reason": "score more",
        "deltas": [{"attribute": "finishing", "before": 0.2, "after": 0.8,
                    "baseline": 0.5, "min": 0.0, "max": 1.0}]}]})
    delta = panels[0]["deltas"][0]
    assert delta["beforePct"] == 20.0
    assert delta["afterPct"] == 80.0
    assert delta["baselinePct"] == 50.0
    assert panels[0]["reason"] == "score more"


def test_a_shout_that_moved_two_roles_becomes_two_panels():
    panels = app_module.tuning_panels({"changed": [
        {"role": "forward", "file": "player_state/forward.json", "reason": None,
         "deltas": [{"attribute": "finishing", "before": 0.2, "after": 0.8,
                     "baseline": 0.5, "min": 0.0, "max": 1.0}]},
        {"role": "defender", "file": "player_state/defender.json", "reason": None,
         "deltas": [{"attribute": "clearance", "before": 0.7, "after": 0.9,
                     "baseline": 0.5, "min": 0.0, "max": 1.0}]}]})
    assert [p["role"] for p in panels] == ["forward", "defender"]


def test_a_refused_tune_becomes_a_panel_of_violations():
    panels = app_module.tuning_panels({
        "ok": False, "role": "defender",
        "violations": ["finishing=2.0 is outside 0.0 to 1.0"]})
    assert panels[0]["role"] == "defender"
    assert panels[0]["deltas"] == []
    assert panels[0]["violations"] == ["finishing=2.0 is outside 0.0 to 1.0"]


def test_every_other_tool_result_stays_out_of_the_log():
    # The log has never shown tool results. Printing them all now would bury
    # the trajectory under shell output.
    assert app_module.tuning_panels({"stdout": "ok"}) == []
    assert app_module.tuning_panels({"changed": []}) == []
    assert app_module.tuning_panels("a string") == []
    assert app_module.tuning_panels(None) == []
