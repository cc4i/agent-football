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
