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


def test_read_player_stats_returns_all_four_roles(fake_arena):
    stats = match.read_player_stats()
    assert set(stats) == {"defender", "midfielder", "forward", "goalkeeper"}


def test_read_player_stats_reads_the_squad_the_arena_holds(fake_arena):
    fake_arena.squad["forward"]["finishing"] = 0.93
    assert match.read_player_stats("forward")["forward"]["finishing"]["value"] \
        == 0.93


def test_read_player_stats_includes_the_valid_range(fake_arena):
    entry = match.read_player_stats("forward")["forward"]["finishing"]
    assert entry["min"] == 0.0
    assert entry["max"] == 1.0
    assert isinstance(entry["value"], (int, float))


def test_read_player_stats_rejects_an_unknown_role(fake_arena):
    with pytest.raises(ValueError, match="unknown role"):
        match.read_player_stats("striker")


def test_an_arena_that_is_down_is_reported_rather_than_raised(fake_arena):
    # The agent reads this and decides what to do about it. An exception out of
    # a tool is a stack trace in the chat window and nothing it can act on.
    fake_arena.silent = True
    answer = match.read_player_stats()
    assert answer["error"] == "arena_unreachable"
    assert "127.0.0.1:8003" in answer["detail"]


def test_a_squad_that_could_not_be_read_does_not_tick_the_stage_off(fake_arena):
    fake_arena.silent = True
    match.CALLED.clear()
    match.read_player_stats()
    assert match.CALLED == set()


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


def test_the_range_a_tuner_is_shown_is_the_one_the_arena_enforces(fake_arena):
    # A tuner reads its bounds here and is refused elsewhere. The dugout used
    # to derive these itself and the two drifted apart, so a value it offered
    # was a value the arena would not take. Both now come from one place.
    for role, bands in fake_arena.rules.items():
        shown = match.read_player_stats(role)[role]
        for attribute, limits in bands.items():
            assert (shown[attribute]["min"], shown[attribute]["max"]) == (
                limits["min"], limits["max"])
