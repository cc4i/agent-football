import pytest

import channel
from tools import tuning
from tools.match import CALLED


def test_a_valid_change_reaches_the_arena(fake_arena):
    result = tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    assert result["ok"] is True
    assert result["applied"] == {"finishing": 0.9}
    assert fake_arena.squad["forward"]["finishing"] == 0.9


def test_untouched_attributes_survive(fake_arena):
    tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    assert fake_arena.squad["forward"]["shotPower"] == 0.6


def test_the_change_is_signed_by_the_tuner_that_made_it(fake_arena):
    # Four subagents write to the same squad within a second of each other, and
    # the arena's log is what a manager reads afterwards to tell them apart.
    tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    written = [body for verb, _, body in fake_arena.seen if verb == "PATCH"]
    assert written == [{"changes": {"finishing": 0.9},
                        "actor": "Antigravity forward-tuner",
                        "reason": "needs a goal"}]


def test_more_than_three_attributes_is_refused_without_asking_the_arena(fake_arena):
    result = tuning.tune_forward(
        {"finishing": 0.6, "shotPower": 0.6, "pace": 0.6, "aggression": 0.6},
        "everything")
    assert result["ok"] is False
    assert "at most 3" in result["violations"][0]
    assert fake_arena.seen == []


def test_an_empty_change_is_refused(fake_arena):
    result = tuning.tune_forward({}, "nothing in particular")
    assert result["ok"] is False
    assert "non-empty" in result["violations"][0]


def test_a_missing_reason_is_refused(fake_arena):
    result = tuning.tune_forward({"finishing": 0.9}, "   ")
    assert result["ok"] is False
    assert "reason" in result["violations"][0]


def test_the_arena_refusing_reads_as_a_refused_tune(fake_arena):
    # The limits are the arena's, so the refusal is the arena's words verbatim.
    result = tuning.tune_forward({"finishing": 2.0}, "score more")
    assert result["ok"] is False
    assert "between 0.0 and 1.0" in result["violations"][0]
    assert fake_arena.squad["forward"]["finishing"] == 0.5


def test_every_reason_the_arena_gave_is_reported_at_once(fake_arena):
    # The tuner is a language model and can only fix what it is told about.
    result = tuning.tune_forward({"finishing": 2.0, "nope": 0.5}, "score more")
    assert result["ok"] is False
    assert "finishing" in result["violations"][0]
    assert "nope" in result["violations"][0]


def test_an_arena_that_is_down_reads_as_a_refused_tune(fake_arena):
    fake_arena.silent = True
    result = tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    assert result["ok"] is False
    assert "127.0.0.1:8003" in result["violations"][0]


def test_each_tool_only_moves_its_own_player(fake_arena):
    tuning.tune_defender({"aggression": 0.9}, "hold the line")
    assert fake_arena.squad["defender"]["aggression"] == 0.9
    assert fake_arena.asked("PATCH") == [
        "/api/rooms/WRKS/teams/blue/profiles/defender"]


def test_tool_name_maps_back_to_role():
    assert tuning.ROLE_BY_TUNING_TOOL["tune_forward"] == "forward"
    assert set(tuning.ROLE_BY_TUNING_TOOL.values()) == {
        "defender", "midfielder", "forward", "goalkeeper"}


def test_the_result_says_what_each_value_was_before(fake_arena):
    result = tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    delta = result["changed"][0]["deltas"][0]
    assert delta["attribute"] == "finishing"
    assert delta["before"] == 0.5
    assert delta["after"] == 0.9
    assert delta["baseline"] == 0.5
    assert (delta["min"], delta["max"]) == (0.0, 1.0)


def test_the_change_names_the_role_where_it_landed_and_the_reason(fake_arena):
    change = tuning.tune_defender({"aggression": 0.9},
                                  "hold the line")["changed"][0]
    assert change["role"] == "defender"
    assert change["where"] == "WRKS/blue/defender"
    assert change["reason"] == "hold the line"


def test_setting_a_value_to_what_it_already_is_moves_nothing(fake_arena):
    result = tuning.tune_forward({"finishing": 0.5}, "no change at all")
    assert result["ok"] is True
    assert result["changed"] == []


def test_a_refused_change_reports_no_movement(fake_arena):
    result = tuning.tune_forward({"finishing": 2.0}, "score more")
    assert result["ok"] is False
    assert "changed" not in result


def test_a_tune_the_arena_took_is_what_ticks_the_stage_off(fake_arena):
    CALLED.clear()
    tuning.tune_forward({"finishing": 2.0}, "score more")
    assert CALLED == set()
    tuning.tune_forward({"finishing": 0.9}, "score more")
    assert "tune" in CALLED


@pytest.fixture
def published(monkeypatch):
    said = []
    monkeypatch.setattr(channel, "publish",
                        lambda name, result: said.append((name, result)))
    return said


def test_a_successful_tune_publishes_its_result(fake_arena, published):
    result = tuning.tune_midfielder({"passRange": 0.9}, "find the runs")
    assert published == [("tune_midfielder", result)]
    assert result["ok"] is True


def test_a_refused_tune_publishes_too(fake_arena, published):
    result = tuning.tune_defender({"aggression": 2.0}, "bad value")
    assert published == [("tune_defender", result)]
    assert result["ok"] is False
    assert "violations" in result
