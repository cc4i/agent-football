"""The pitch mount, when ARENA_PITCH_DIR is set."""

import importlib

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def pitch_mount(tmp_path, monkeypatch, dsn):
    """An arena serving a minimal built pitch from a temporary directory."""
    # Build a minimal dist/ with the three things the mount differentiates:
    # index.html, a content-hashed bundle file, and a non-hashed asset.
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>pitch</body></html>")

    bundle = dist / "bundle"
    bundle.mkdir()
    (bundle / "index-abc123.js").write_text("console.log('pitch');")

    assets = dist / "assets" / "sprites"
    assets.mkdir(parents=True)
    (assets / "player_blue_team.png").write_bytes(b"\x89PNG")

    # Put a file outside the served root to prove the traversal guard works.
    (tmp_path / "app.py").write_text("escape")

    # Reload the app module with ARENA_PITCH_DIR set.
    monkeypatch.setenv("ARENA_PITCH_DIR", str(dist))
    monkeypatch.setenv("ARENA_DB", dsn)
    import app as app_module
    importlib.reload(app_module)

    with TestClient(app_module.app) as test_client:
        yield test_client

    # Restore the original app module. Delete the env var first: a bare reload
    # re-reads it, and pytest's monkeypatch teardown runs after this fixture's.
    monkeypatch.delenv("ARENA_PITCH_DIR", raising=False)
    importlib.reload(app_module)


def test_the_index_is_served_with_no_cache(pitch_mount):
    for path in ("/pitch", "/pitch/"):
        response = pitch_mount.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["cache-control"] == "no-cache"
        assert "pitch" in response.text


def test_the_hashed_bundle_is_immutable(pitch_mount):
    response = pitch_mount.get("/pitch/bundle/index-abc123.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_the_assets_are_revalidated(pitch_mount):
    response = pitch_mount.get("/pitch/assets/sprites/player_blue_team.png")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_the_mount_cannot_be_walked_out_of(pitch_mount):
    # Percent-encoded because httpx removes dot segments from the path before
    # sending. The plain spellings (../../../) never reach the mount.
    for encoded in ("%2e%2e/app.py", "..%2fapp.py", "%2e%2e%2fapp.py"):
        assert pitch_mount.get(f"/pitch/{encoded}").status_code == 404
