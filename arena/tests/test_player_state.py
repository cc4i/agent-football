"""Injury toasts come off a file two containers share.

A specialist reports an injury through an MCP server living in the coach's
container. The browser polling for it is talking to the arena. In one Cloud
Run instance those are two processes with one in-memory volume between them,
so this is a static mount and not a new table.
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def served(tmp_path, dsn, monkeypatch):
    # Nest the served root so the escape file stays in this fixture's own space.
    state = tmp_path / "state"
    subs = state / "substitutions"
    subs.mkdir(parents=True)
    (subs / "WRKS__blue.json").write_text(json.dumps(
        {"forward": {"action": "injury", "severity": "knock", "ts": 1.0}}))
    # Put a file outside the served root to prove the traversal guard works.
    (tmp_path / "secret.txt").write_text("escape")
    monkeypatch.setenv("ARENA_DB", dsn)
    monkeypatch.setenv("ARENA_PLAYER_STATE_DIR", str(state))
    import importlib

    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        yield client
    # Restore the original app module. Delete the env var first: a bare reload
    # re-reads it, and pytest's monkeypatch teardown runs after this fixture's.
    monkeypatch.delenv("ARENA_PLAYER_STATE_DIR", raising=False)
    importlib.reload(app_module)


def test_an_injury_file_is_served(served):
    reply = served.get("/player_state/substitutions/WRKS__blue.json")
    assert reply.status_code == 200
    assert reply.json()["forward"]["action"] == "injury"
    assert reply.headers["cache-control"] == "no-cache"


def test_a_missing_directory_is_a_404_not_a_500(dsn, monkeypatch, tmp_path):
    # Point at a path that does not exist. Without the mkdir at mount time,
    # this 500s on every poll until the first injury is written.
    monkeypatch.setenv("ARENA_DB", dsn)
    monkeypatch.setenv("ARENA_PLAYER_STATE_DIR", str(tmp_path / "does_not_exist"))
    import importlib

    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        reply = client.get("/player_state/substitutions/ABCD__red.json")
        assert reply.status_code == 404
    monkeypatch.delenv("ARENA_PLAYER_STATE_DIR", raising=False)
    importlib.reload(app_module)


def test_the_mount_does_not_escape_its_directory(served):
    # Percent-encoded because httpx removes dot segments from the path before
    # sending. The plain spellings (../../../) never reach the mount.
    for encoded in ("%2e%2e/secret.txt", "..%2fsecret.txt", "%2e%2e%2fsecret.txt"):
        assert served.get(f"/player_state/{encoded}").status_code == 404
