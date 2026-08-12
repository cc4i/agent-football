"""The agents' way into the arena. Nothing here talks to a real arena."""

import io
import json
import urllib.error

import pytest

from agents.specialist_agents import arena_client


class FakeReply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


@pytest.fixture
def captured(monkeypatch):
    """Swallow the request and hand back a canned reply. Returns the request."""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return FakeReply(json.dumps({"role": "defender", "changed": {}}).encode())

    monkeypatch.setattr(arena_client.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_the_request_carries_the_service_token_and_the_right_verb(captured, monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    arena_client.patch_profile("WRKS", "blue", "defender", {"aggression": 0.2},
                               actor="coach", reason="too passive")
    request = captured["request"]
    assert request.get_method() == "PATCH"
    assert request.full_url.endswith("/api/rooms/WRKS/teams/blue/profiles/defender")
    assert request.get_header("X-arena-service") == "s3cret"
    assert json.loads(request.data) == {"changes": {"aggression": 0.2},
                                        "actor": "coach", "reason": "too passive"}


def test_a_role_with_a_slash_in_it_cannot_change_the_path(captured, monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    arena_client.patch_profile("WRKS", "blue", "../../health", {}, actor="a", reason="")
    assert "/profiles/..%2F..%2Fhealth" in captured["request"].full_url


def test_without_a_service_token_it_refuses_before_it_reaches_the_network(monkeypatch):
    monkeypatch.delenv("ARENA_SERVICE_TOKEN", raising=False)
    with pytest.raises(arena_client.ArenaError) as refusal:
        arena_client.patch_profile("WRKS", "blue", "defender", {}, actor="a", reason="")
    assert "ARENA_SERVICE_TOKEN" in str(refusal.value)


def test_the_arenas_own_reasons_come_back_to_the_agent(monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")
    body = json.dumps({"detail": {"problems": ["speed=99 is outside 0.0 to 1.0",
                                               "'wingspan' is not an attribute"]}})

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 422, "Unprocessable", {},
                                     io.BytesIO(body.encode()))

    monkeypatch.setattr(arena_client.urllib.request, "urlopen", refuse)
    with pytest.raises(arena_client.ArenaError) as refusal:
        arena_client.patch_profile("WRKS", "blue", "defender", {"speed": 99},
                                   actor="a", reason="")
    assert "speed=99 is outside 0.0 to 1.0" in str(refusal.value)
    assert "wingspan" in str(refusal.value)


def test_an_arena_that_is_not_running_says_so_plainly(monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "s3cret")

    def refuse(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(arena_client.urllib.request, "urlopen", refuse)
    with pytest.raises(arena_client.ArenaError) as refusal:
        arena_client.patch_profile("WRKS", "blue", "defender", {}, actor="a", reason="")
    assert "did not answer" in str(refusal.value)


def test_the_arenas_address_can_be_moved(monkeypatch):
    monkeypatch.setenv("ARENA_URL", "http://arena.local:9000/")
    assert arena_client.base_url() == "http://arena.local:9000"
