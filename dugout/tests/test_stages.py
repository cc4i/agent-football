import json
import os
import time

import pytest

import stages


@pytest.fixture
def fake_fs(tmp_path, monkeypatch):
    sprites = tmp_path / "sprites"
    sprites.mkdir()
    state = tmp_path / "player_state"
    state.mkdir()
    for name in ("defender", "midfielder", "forward", "goalkeeper"):
        payload = json.dumps({"pace": 0.5})
        (state / f"{name}.json").write_text(payload)
        (state / f"{name}_baseline.json").write_text(payload)
    monkeypatch.setattr(stages, "SPRITE_DIR", sprites)
    monkeypatch.setattr(stages, "PLAYER_STATE_DIR", state)
    monkeypatch.setattr(stages, "STATUS_FILE", tmp_path / "status.json")
    return tmp_path


def test_four_stages_in_scope():
    assert [s.id for s in stages.STAGES] == [
        "rebrand", "take_the_field", "read_the_game", "tune_the_squad"]


def test_every_stage_has_a_suggested_prompt():
    assert all(s.suggested.strip() for s in stages.STAGES)


def test_no_em_dash_in_any_stage_copy():
    for s in stages.STAGES:
        assert "—" not in (s.title + s.blurb + s.suggested)


def test_rebrand_needs_both_sprite_sheets(fake_fs, monkeypatch):
    monkeypatch.setattr(stages, "STARTED_AT", 0)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["rebrand"].is_done() is False
    (fake_fs / "sprites" / "player_blue_team.png").write_bytes(b"x")
    assert by_id["rebrand"].is_done() is False
    (fake_fs / "sprites" / "player_red_team.png").write_bytes(b"x")
    assert by_id["rebrand"].is_done() is True


def test_sprites_that_predate_this_session_do_not_count(fake_fs, monkeypatch):
    for team in ("blue", "red"):
        (fake_fs / "sprites" / f"player_{team}_team.png").write_bytes(b"x")
    # The repo ships sprites; only a rewrite during this session counts.
    monkeypatch.setattr(stages, "STARTED_AT", time.time() + 60)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["rebrand"].is_done() is False


def test_read_the_game_needs_the_stats_tool_to_have_run(fake_fs, monkeypatch):
    monkeypatch.setattr(stages.match, "CALLED", set())
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["read_the_game"].is_done() is False
    stages.match.CALLED.add("read_player_stats")
    assert by_id["read_the_game"].is_done() is True


def test_take_the_field_needs_a_live_status_file(fake_fs, monkeypatch):
    monkeypatch.setattr(stages.match, "STATUS_FILE", fake_fs / "status.json")
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["take_the_field"].is_done() is False
    (fake_fs / "status.json").write_text(json.dumps({"score1": 0, "score2": 0}))
    assert by_id["take_the_field"].is_done() is True


def test_a_stale_status_file_does_not_count_as_being_on_the_field(fake_fs, monkeypatch):
    f = fake_fs / "status.json"
    f.write_text(json.dumps({"score1": 1, "score2": 0}))
    old = time.time() - (stages.match.STATUS_MAX_AGE_SEC + 30)
    os.utime(f, (old, old))
    monkeypatch.setattr(stages.match, "STATUS_FILE", f)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["take_the_field"].is_done() is False


def test_tune_is_done_once_a_role_file_is_rewritten_this_session(fake_fs, monkeypatch):
    monkeypatch.setattr(stages, "STARTED_AT", time.time())
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["tune_the_squad"].is_done() is False
    (fake_fs / "player_state" / "forward.json").write_text(json.dumps({"pace": 0.9}))
    assert by_id["tune_the_squad"].is_done() is True


def test_role_files_shipped_by_the_repo_do_not_count_as_tuned(fake_fs, monkeypatch):
    # Three of the four shipped role files already differ from their baselines,
    # so a content comparison would read as done on a clean checkout.
    monkeypatch.setattr(stages, "STARTED_AT", time.time() + 60)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["tune_the_squad"].is_done() is False


def test_stage_status_is_json_serialisable(fake_fs):
    payload = stages.stage_status()
    json.dumps(payload)
    assert {"id", "title", "blurb", "suggested", "done"} == set(payload[0])
