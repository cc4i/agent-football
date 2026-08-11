import json
import os

import pytest

from tools import tuning


@pytest.fixture
def state(tmp_path, monkeypatch):
    baseline = {"finishing": 0.5, "shotPower": 0.5, "pace": 0.5,
                "aggression": 0.5, "decisionDelay": 80}
    for name in ("forward", "defender", "midfielder", "goalkeeper"):
        (tmp_path / f"{name}.json").write_text(json.dumps(baseline))
        (tmp_path / f"{name}_baseline.json").write_text(json.dumps(baseline))
    monkeypatch.setattr(tuning, "PLAYER_STATE_DIR", tmp_path)
    monkeypatch.setattr("attributes.PLAYER_STATE_DIR", tmp_path)
    return tmp_path


def test_a_valid_change_is_written_to_disk(state):
    result = tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    assert result["ok"] is True
    assert result["applied"] == {"finishing": 0.9}
    assert json.loads((state / "forward.json").read_text())["finishing"] == 0.9


def test_untouched_attributes_survive(state):
    tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    profile = json.loads((state / "forward.json").read_text())
    assert profile["shotPower"] == 0.5


def test_more_than_three_attributes_is_refused(state):
    result = tuning.tune_forward(
        {"finishing": 0.6, "shotPower": 0.6, "pace": 0.6, "aggression": 0.6},
        "everything")
    assert result["ok"] is False
    assert "at most 3" in result["violations"][0]
    assert json.loads((state / "forward.json").read_text())["finishing"] == 0.5


def test_out_of_range_is_refused_and_nothing_is_written(state):
    result = tuning.tune_forward({"finishing": 2.0}, "score more")
    assert result["ok"] is False
    assert json.loads((state / "forward.json").read_text())["finishing"] == 0.5


def test_a_missing_reason_is_refused(state):
    result = tuning.tune_forward({"finishing": 0.9}, "   ")
    assert result["ok"] is False
    assert "reason" in result["violations"][0]


def test_each_tool_only_writes_its_own_file(state):
    tuning.tune_defender({"aggression": 0.9}, "hold the line")
    assert json.loads((state / "defender.json").read_text())["aggression"] == 0.9
    assert json.loads((state / "forward.json").read_text())["aggression"] == 0.5


def test_tool_name_maps_back_to_role():
    assert tuning.ROLE_BY_TUNING_TOOL["tune_forward"] == "forward"
    assert set(tuning.ROLE_BY_TUNING_TOOL.values()) == {
        "defender", "midfielder", "forward", "goalkeeper"}


def test_failed_write_leaves_original_file_intact(state, monkeypatch):
    def raise_on_replace(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", raise_on_replace)
    with pytest.raises(OSError):
        tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    profile = json.loads((state / "forward.json").read_text())
    assert profile["finishing"] == 0.5


def test_the_result_says_what_each_value_was_before(state):
    result = tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    delta = result["changed"][0]["deltas"][0]
    assert delta["attribute"] == "finishing"
    assert delta["before"] == 0.5
    assert delta["after"] == 0.9
    assert delta["baseline"] == 0.5
    assert (delta["min"], delta["max"]) == (0.0, 1.0)


def test_the_change_names_the_role_the_file_and_the_reason(state):
    change = tuning.tune_defender({"aggression": 0.9}, "hold the line")["changed"][0]
    assert change["role"] == "defender"
    assert change["file"] == "player_state/defender.json"
    assert change["reason"] == "hold the line"


def test_setting_a_value_to_what_it_already_is_moves_nothing(state):
    result = tuning.tune_forward({"finishing": 0.5}, "no change at all")
    assert result["ok"] is True
    assert result["changed"] == []


def test_a_refused_change_reports_no_movement(state):
    result = tuning.tune_forward({"finishing": 2.0}, "score more")
    assert result["ok"] is False
    assert "changed" not in result
