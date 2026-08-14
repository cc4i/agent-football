"""A player's condition belongs to a room and a dugout, and goes to the arena.

It used to be a JSON file beside the pitch. The tests that mattered about the
file -- one match's knock never reaching another, a room code never becoming
part of a path -- still matter about the request that replaced it, so they are
the tests that are still here.
"""

import contextlib
import json

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed in this environment")

from agents import football_mcp_server as server


@pytest.fixture
def arena(monkeypatch):
    """The arena, as far as the MCP server can tell. Records what it was sent."""
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "shared-secret")
    monkeypatch.setenv("ARENA_URL", "http://arena.test")
    posted = []

    def urlopen(request, timeout=None):
        posted.append({"url": request.full_url,
                       "headers": dict(request.headers),
                       "body": json.loads(request.data)})
        return contextlib.nullcontext()

    monkeypatch.setattr(server.urllib.request, "urlopen", urlopen)
    return posted


def test_two_rooms_do_not_share_a_report():
    assert server.whose_match("7K2M", "blue") == ("7K2M", "blue")
    assert server.whose_match("7K2M", "red") == ("7K2M", "red")
    assert server.whose_match("qq44", "blue") == ("QQ44", "blue")


def test_a_room_code_cannot_walk_out_of_the_url():
    assert server.whose_match("../../etc", "blue") == (server.DEFAULT_ROOM, "blue")
    assert server.whose_match("7K2M", "purple") == ("7K2M", server.DEFAULT_TEAM)


def test_an_injury_is_posted_at_its_own_room(arena):
    server.report_injury("defender", "knock", room="7K2M", team="red")
    assert len(arena) == 1
    assert arena[0]["url"] == "http://arena.test/api/rooms/7K2M/substitution"
    assert arena[0]["body"] == {"team": "red", "role": "defender",
                                "action": "injury", "detail": "knock"}


def test_a_substitution_request_is_posted_at_its_own_room(arena):
    # The other half of the pair. Without a room it would ask a workshop bench
    # to warm up for a match happening somewhere else.
    server.request_substitution("forward", "tired", room="7K2M", team="red")
    assert arena[0]["url"] == "http://arena.test/api/rooms/7K2M/substitution"
    assert arena[0]["body"]["action"] == "substitution"
    assert arena[0]["body"]["role"] == "forward"


def test_the_report_carries_the_shared_secret(arena):
    server.report_injury("goalkeeper", "strain", room="7K2M", team="blue")
    # urllib title-cases header names on the way in.
    assert arena[0]["headers"]["X-arena-service"] == "shared-secret"


def test_an_unknown_role_is_refused_before_anything_is_posted(arena):
    answer = server.report_injury("striker", "knock", room="7K2M", team="blue")
    assert "unknown role" in answer
    assert arena == []


def test_the_agent_is_told_when_the_arena_cannot_be_reached(monkeypatch):
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", "shared-secret")

    def refuse(request, timeout=None):
        raise server.urllib.error.URLError("connection refused")

    monkeypatch.setattr(server.urllib.request, "urlopen", refuse)
    answer = server.request_substitution("midfielder", "tired", room="7K2M", team="blue")
    assert answer.startswith("Error:")
    assert "did not answer" in answer


def test_without_the_shared_secret_nothing_is_posted(monkeypatch):
    monkeypatch.delenv("ARENA_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(server.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("posted without a token"))
    answer = server.report_injury("defender", "knock", room="7K2M", team="blue")
    assert "ARENA_SERVICE_TOKEN" in answer
