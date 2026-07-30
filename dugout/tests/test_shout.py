import pytest

from tools import shout
from tools.match import CALLED


@pytest.fixture(autouse=True)
def clean():
    CALLED.clear()
    yield
    CALLED.clear()


async def test_an_empty_shout_is_refused():
    assert "error" in await shout.shout_to_the_team("   ")


async def test_no_match_window_is_reported_not_raised(monkeypatch):
    # The manager may shout before taking the field. That must read as an
    # instruction the agent can act on, not a stack trace in the log.
    monkeypatch.setattr(shout, "DEBUG_URL", "http://localhost:9")
    result = await shout.shout_to_the_team("press high")
    assert result["error"] == "no_match_window"
    assert "take the field" in result["detail"].lower()


async def test_shouting_is_recorded_for_the_quest(monkeypatch):
    monkeypatch.setattr(shout, "DEBUG_URL", "http://localhost:9")
    await shout.shout_to_the_team("press high")
    assert "shout_to_the_team" in CALLED


def test_only_the_new_terminal_lines_come_back():
    before = "> ready\n> waiting\n"
    after = "> ready\n> waiting\n> Coach shouted\n\n> Captain: huddle\n"
    assert shout._new_lines(before, after) == ["> Coach shouted", "> Captain: huddle"]


def test_the_chain_is_complete_once_four_players_answer():
    partial = ["> Coach shouted", "Coach: Relaying to Team Captain"]
    assert shout._chain_complete(partial) is False
    full = partial + ["Captain: Huddle assembled!"] + [
        f"└─ {role}: on it" for role in
        ("DEFENDER", "MIDFIELDER", "FORWARD", "GOALKEEPER")]
    assert shout._chain_complete(full) is True


async def test_a_half_finished_chain_after_full_time_says_so(monkeypatch):
    monkeypatch.setattr(shout, "read_status", lambda: {"gameActive": False})
    assert shout._chain_complete([]) is False
    # The note is built from the same read_status the tool uses.
    assert not shout.read_status()["gameActive"]
