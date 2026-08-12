"""Substitutions belong to a room and a dugout, not to the whole venue."""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed in this environment")

from agents import football_mcp_server as server


def test_two_rooms_do_not_share_a_substitutions_file():
    first = server.substitutions_path("7K2M", "blue")
    second = server.substitutions_path("7K2M", "red")
    third = server.substitutions_path("QQ44", "blue")
    assert len({first, second, third}) == 3


def test_a_room_code_cannot_walk_out_of_the_directory():
    path = server.substitutions_path("../../etc", "blue")
    assert "/etc/" not in path
    assert path == server.substitutions_path(server.DEFAULT_ROOM, "blue")


def test_an_injury_lands_in_its_own_rooms_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PLAYER_STATE_DIR", str(tmp_path))
    server.report_injury("defender", "knock", room="7K2M", team="red")
    written = json.loads(open(server.substitutions_path("7K2M", "red")).read())
    assert "defender" in written


def test_a_substitution_request_lands_in_its_own_rooms_file(tmp_path, monkeypatch):
    # The other half of the pair. Without a room it would ask a workshop bench
    # to warm up for a match happening somewhere else.
    monkeypatch.setattr(server, "PLAYER_STATE_DIR", str(tmp_path))
    server.request_substitution("forward", "tired", room="7K2M", team="red")
    written = json.loads(open(server.substitutions_path("7K2M", "red")).read())
    assert written["forward"]["action"] == "substitute"


def test_the_default_room_still_writes_the_file_the_pitch_polls(tmp_path, monkeypatch):
    # Temporary: main.js polls player_state/substitutions.json. Deleted in step 3.
    monkeypatch.setattr(server, "PLAYER_STATE_DIR", str(tmp_path))
    server.report_injury("defender", "knock")
    assert (tmp_path / "substitutions.json").exists()
