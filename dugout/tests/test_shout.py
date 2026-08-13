import asyncio
from contextlib import asynccontextmanager

import pytest

import arena
import channel
from tools import shout
from tools.match import CALLED

# What the fake arena hands back for the first shout of a test.
SEQ = 42

COACH = {"type": "relay.coach", "seq": SEQ, "team": "blue", "state": "done"}
CAPTAIN = {"type": "relay.captain", "seq": SEQ, "team": "blue",
           "state": "thinking"}
FORWARD = {"type": "relay.specialist", "seq": SEQ, "team": "blue",
           "role": "forward", "state": "done", "text": "I will push up"}
HUDDLE = {"type": "relay.huddle", "seq": SEQ, "team": "blue", "state": "done",
          "status": "Tactics executed"}


def patch(role, changed, reason="press them", seq=SEQ):
    """A profile.patch event, shaped as the arena publishes it."""
    return {"type": "event", "seq": seq + 1, "kind": "profile.patch",
            "match_ms": None,
            "payload": {"team": "blue", "role": role, "changed": changed,
                        "reason": reason, "actor": "specialist",
                        "shout_seq": seq}}


def relay(*messages, then_silent=False):
    """A workshop socket that says these things, in this order.

    By default it then closes, which is a socket the arena dropped. With
    `then_silent` it stays open saying nothing, which is an arena still there
    but no longer reporting -- the case the wait exists for.
    """
    @asynccontextmanager
    async def listening():
        async def heard():
            for message in messages:
                yield message
            if then_silent:
                await asyncio.sleep(3600)

        yield heard()

    return listening


@pytest.fixture(autouse=True)
def clean():
    CALLED.clear()
    yield
    CALLED.clear()


@pytest.fixture(autouse=True)
def a_match_on_screen(monkeypatch):
    # Otherwise every result carries the "take the field" note, which is its
    # own test.
    monkeypatch.setattr(shout, "read_status",
                        lambda: {"score1": 0, "score2": 0, "gameActive": True})


@pytest.fixture
def workshop(fake_arena, monkeypatch):
    """The arena, with its relay under the test's control."""
    def says(*messages, **how):
        monkeypatch.setattr(arena, "listening", relay(*messages, **how))
        return fake_arena

    return says


async def test_an_empty_shout_is_refused():
    assert "error" in await shout.shout_to_the_team("   ")


async def test_shouting_is_recorded_for_the_quest(workshop):
    workshop(HUDDLE)
    await shout.shout_to_the_team("press high")
    assert "shout_to_the_team" in CALLED


async def test_the_manager_s_own_words_reach_the_arena(workshop):
    said = workshop(HUDDLE)
    await shout.shout_to_the_team("press   high\nup the pitch")
    assert said.seen[-1][:2] == ("POST", "/api/rooms/WRKS/shout")
    assert said.seen[-1][2] == {"text": "press high up the pitch"}


async def test_every_answer_comes_back_in_the_order_it_was_heard(workshop):
    workshop(COACH, CAPTAIN, FORWARD, HUDDLE)
    result = await shout.shout_to_the_team("press high")
    assert result["replies"] == [
        "Coach: relayed it to the captain over A2A",
        "Captain: briefing the four player agents",
        "forward: I will push up",
        "Captain: Tactics executed",
    ]


async def test_another_room_s_chain_is_not_reported_as_this_one_s(workshop):
    # Every shout in the venue comes down a socket this one is not watching,
    # but the workshop's own earlier shouts come down this one.
    stranger = {**FORWARD, "seq": SEQ - 5, "text": "sit deep"}
    workshop(stranger, FORWARD, HUDDLE)
    result = await shout.shout_to_the_team("press high")
    assert "forward: sit deep" not in result["replies"]


async def test_nothing_after_the_huddle_is_waited_for(workshop):
    # The huddle is the captain's last word, and the socket stays open for the
    # next shout, so a tool that kept reading would never return.
    workshop(HUDDLE, FORWARD, then_silent=True)
    result = await shout.shout_to_the_team("press high")
    assert result["replies"] == ["Captain: Tactics executed"]


async def test_what_the_players_moved_is_reported_against_what_it_was(workshop):
    workshop(patch("forward", {"finishing": 0.9}), HUDDLE)
    result = await shout.shout_to_the_team("shoot on sight")
    change = result["changed"][0]
    assert change["role"] == "forward"
    assert change["where"] == "WRKS/blue/forward"
    assert change["reason"] == "press them"
    assert (change["deltas"][0]["before"], change["deltas"][0]["after"]) == (
        0.5, 0.9)


async def test_a_patch_that_answers_a_different_shout_is_not_ours(workshop):
    # A tuner or another manager can move the same squad while this shout is
    # still going out. Claiming it would credit the shout with somebody's work.
    workshop(patch("forward", {"finishing": 0.9}, seq=SEQ - 5), HUDDLE)
    assert (await shout.shout_to_the_team("shoot on sight"))["changed"] == []


async def test_a_shout_that_moved_nothing_says_so_plainly(workshop):
    workshop(COACH, HUDDLE)
    assert (await shout.shout_to_the_team("press high"))["changed"] == []


async def test_a_squad_that_never_answered_is_worth_saying_out_loud(workshop):
    # The huddle always arrives, even for a chain that fell over, so this is
    # what a coach or captain being down looks like from here.
    workshop({**HUDDLE, "state": "failed", "status": None})
    result = await shout.shout_to_the_team("press high")
    assert ":8000" in result["note"] and ":8001" in result["note"]


async def test_an_arena_that_stops_reporting_is_worth_saying_out_loud(workshop,
                                                                      monkeypatch):
    monkeypatch.setattr(shout, "WAIT_SECONDS", 0.05)
    workshop(COACH, then_silent=True)
    result = await shout.shout_to_the_team("press high")
    assert "stopped reporting" in result["note"]
    assert result["replies"] == ["Coach: relayed it to the captain over A2A"]


async def test_a_shout_with_no_match_on_screen_says_take_the_field(workshop,
                                                                   monkeypatch):
    monkeypatch.setattr(shout, "read_status", lambda: {"error": "game_not_running"})
    workshop(HUDDLE)
    result = await shout.shout_to_the_team("press high")
    assert "Take the field" in result["note"]


async def test_a_shout_that_went_the_whole_way_needs_no_note(workshop):
    workshop(COACH, HUDDLE)
    assert "note" not in await shout.shout_to_the_team("press high")


async def test_an_arena_that_is_down_is_reported_rather_than_raised(fake_arena):
    fake_arena.silent = True
    result = await shout.shout_to_the_team("press high")
    assert result["error"] == "arena_unreachable"
    assert "127.0.0.1:8003" in result["detail"]


async def test_an_arena_that_refuses_the_shout_is_reported_too(workshop):
    said = workshop(HUDDLE)
    said.refusal = (409, "that match is over")
    result = await shout.shout_to_the_team("press high")
    assert result["error"] == "arena_unreachable"
    assert "that match is over" in result["detail"]


def test_a_coach_still_thinking_is_not_worth_a_line():
    assert shout._lines({"type": "relay.coach", "state": "thinking"}) == []


def test_a_player_that_never_answered_is_named_as_such():
    assert shout._lines({"type": "relay.specialist", "role": "goalkeeper",
                         "state": "missing"}) == ["goalkeeper: no answer"]


def test_trouble_on_the_chain_is_passed_straight_through():
    assert shout._lines({"type": "relay.trouble", "text": "captain timed out"}) \
        == ["Trouble: captain timed out"]


def test_a_shout_queued_behind_another_says_how_many():
    assert shout._lines({"type": "relay.waiting", "ahead": 2}) == [
        "Waiting: 2 shout(s) ahead of this one"]


def test_a_kind_of_message_this_tool_has_no_words_for_is_dropped():
    assert shout._lines({"type": "relay.something-new", "state": "done"}) == []


def test_a_role_that_was_not_read_before_the_shout_is_skipped():
    # Nothing to measure the move against, so reporting it would invent a
    # before value the manager never had.
    assert shout._changed({}, [patch("forward", {"finishing": 0.9})["payload"]]) \
        == []


async def test_an_early_refusal_publishes_nothing(monkeypatch):
    published = []
    monkeypatch.setattr(channel, "publish",
                        lambda name, result: published.append((name, result)))
    assert "error" in await shout.shout_to_the_team("   ")
    assert published == []


async def test_a_completed_shout_publishes_its_result(workshop, monkeypatch):
    published = []
    monkeypatch.setattr(channel, "publish",
                        lambda name, result: published.append((name, result)))
    workshop(COACH, patch("forward", {"finishing": 0.9}), HUDDLE)
    result = await shout.shout_to_the_team("press high")
    assert published == [("shout_to_the_team", result)]
