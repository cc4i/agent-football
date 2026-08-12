"""update_profile now belongs to a room and a dugout, not to a directory."""

import pytest

pytest.importorskip("google.adk", reason="the ADK is not installed in this environment")

from agents.specialist_agents import arena_client, tools


class FakeContext:
    """Stands in for ADK's ToolContext, which is only a namespace here."""

    def __init__(self, **state):
        self.state = state


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_patch(room, team, role, changes, actor, reason):
        calls.append({"room": room, "team": team, "role": role, "changes": changes,
                      "actor": actor, "reason": reason})
        return {"role": role, "attributes": {}, "changed": changes, "seq": 1}

    monkeypatch.setattr(arena_client, "patch_profile", fake_patch)
    return calls


def test_the_room_and_dugout_come_from_the_session(sent):
    tools.update_profile("defender", {"aggression": 0.2},
                         FakeContext(room_code="7K2M", team="red"))
    assert sent[0]["room"] == "7K2M"
    assert sent[0]["team"] == "red"


def test_a_session_with_no_room_falls_back_to_the_workshop(sent):
    # The shout bar predates rooms; step 4 is what puts a room in the session.
    tools.update_profile("defender", {"aggression": 0.2}, FakeContext())
    assert sent[0]["room"] == arena_client.DEFAULT_ROOM
    assert sent[0]["team"] == arena_client.DEFAULT_TEAM


def test_the_reply_names_what_moved(sent):
    reply = tools.update_profile("defender", {"aggression": 0.2}, FakeContext())
    assert "aggression=0.2" in reply
    assert "defender" in reply


def test_a_refusal_comes_back_as_words_not_an_exception(monkeypatch):
    def refuse(room, team, role, changes, actor, reason):
        raise arena_client.ArenaError("speed=99 is outside 0.0 to 1.0")

    monkeypatch.setattr(arena_client, "patch_profile", refuse)
    reply = tools.update_profile("defender", {"speed": 99}, FakeContext())
    assert reply.startswith("Rejected: ")
    assert "speed=99" in reply


def test_a_patch_that_changes_nothing_says_so(monkeypatch):
    monkeypatch.setattr(arena_client, "patch_profile",
                        lambda room, team, role, changes, actor, reason:
                        {"role": role, "attributes": {}, "changed": {}, "seq": 1})
    assert tools.update_profile("defender", {"aggression": 0.2},
                                FakeContext()).startswith("No change")
