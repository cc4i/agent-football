import json
import os
import time

import pytest

from tools import match


def test_status_reports_game_not_running_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(match, "STATUS_FILE", tmp_path / "missing.json")
    assert match.get_match_status() == {"error": "game_not_running"}


def test_status_reports_game_not_running_on_null_payload(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text("null")
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {"error": "game_not_running"}


def test_status_reports_game_not_running_on_corrupt_payload(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text("{not json")
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {"error": "game_not_running"}


def test_status_passes_through_a_live_match(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text(json.dumps(
        {"score1": 2, "score2": 1, "matchTime": 41.5, "gameActive": True}))
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {
        "score1": 2, "score2": 1, "matchTime": 41.5, "gameActive": True}


def test_read_player_stats_returns_all_four_roles():
    stats = match.read_player_stats()
    assert set(stats) == {"defender", "midfielder", "forward", "goalkeeper"}


def test_read_player_stats_includes_the_valid_range():
    entry = match.read_player_stats("forward")["forward"]["finishing"]
    assert entry["min"] == 0.0
    assert entry["max"] == 1.0
    assert isinstance(entry["value"], (int, float))


def test_read_player_stats_rejects_an_unknown_role():
    with pytest.raises(ValueError, match="unknown role"):
        match.read_player_stats("striker")


def test_status_reports_game_not_running_when_the_file_is_stale(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text(json.dumps({"score1": 1, "score2": 0, "gameActive": True}))
    old = time.time() - (match.STATUS_MAX_AGE_SEC + 30)
    os.utime(f, (old, old))
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {"error": "game_not_running"}


def test_read_status_does_not_record_a_tool_call(monkeypatch, tmp_path):
    # The UI polls this every few seconds. Recording it would light the
    # "read the game" stage without the agent having done anything.
    monkeypatch.setattr(match, "STATUS_FILE", tmp_path / "s.json")
    (tmp_path / "s.json").write_text(json.dumps({"score1": 1, "score2": 0}))
    match.CALLED.clear()
    assert match.read_status() == {"score1": 1, "score2": 0}
    assert match.CALLED == set()


def test_read_status_reports_no_match_the_same_way(monkeypatch, tmp_path):
    monkeypatch.setattr(match, "STATUS_FILE", tmp_path / "missing.json")
    assert match.read_status() == {"error": "game_not_running"}


def test_reported_ranges_match_what_tuning_actually_enforces():
    # A tuner reads its bounds here and is validated elsewhere. If the two
    # disagree, the midfielder's decisionsDelay=150 is reported as 0 to 1 and
    # any change it makes is refused.
    from attributes import ROLES, validate_changes
    from tools.match import read_player_stats

    for role in ROLES:
        for attribute, info in read_player_stats(role)[role].items():
            value = info["value"]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            assert info["min"] <= value <= info["max"], (
                f"{role}.{attribute} is {value} but reported as "
                f"{info['min']} to {info['max']}")
            assert validate_changes(role, {attribute: info["min"]}) == []
            assert validate_changes(role, {attribute: info["max"]}) == []
