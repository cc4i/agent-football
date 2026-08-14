"""What the arena insists on before it will serve a public URL."""

import importlib

import pytest


def _boot(monkeypatch, **environment):
    # Set to empty rather than deleting, because load_dotenv() doesn't override
    # existing variables and .env has defaults. An empty string in the environment
    # blocks the .env value.
    for name in ("ARENA_ENV", "ARENA_SECRET", "ARENA_EMAIL_SALT",
                 "ARENA_SERVICE_TOKEN", "ARENA_PUBLIC_URL"):
        monkeypatch.setenv(name, "")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    import app as app_module
    return importlib.reload(app_module)


@pytest.fixture(autouse=True)
def restore():
    yield
    import app as app_module
    importlib.reload(app_module)


@pytest.mark.parametrize("missing", ["ARENA_SECRET", "ARENA_EMAIL_SALT",
                                     "ARENA_SERVICE_TOKEN"])
def test_production_refuses_to_start_without_each_secret(monkeypatch, missing):
    full = {"ARENA_ENV": "production", "ARENA_SECRET": "s",
            "ARENA_EMAIL_SALT": "p", "ARENA_SERVICE_TOKEN": "t"}
    del full[missing]
    with pytest.raises(Exception) as refusal:
        _boot(monkeypatch, **full)
    assert missing in str(refusal.value)


def test_production_starts_when_all_three_are_set(monkeypatch):
    module = _boot(monkeypatch, ARENA_ENV="production", ARENA_SECRET="s",
                   ARENA_EMAIL_SALT="p", ARENA_SERVICE_TOKEN="t")
    assert module.PRODUCTION is True


def test_a_laptop_still_starts_with_none_of_them(monkeypatch):
    module = _boot(monkeypatch)
    assert module.PRODUCTION is False
    assert module.EMAIL_SALT == "arena-dev-salt"
    assert module.SESSION_SECRET != ""
    assert module.SERVICE_TOKEN == ""


def test_the_join_url_is_worked_out_from_the_request_when_unset(monkeypatch, client, phones):
    monkeypatch.setenv("ARENA_PUBLIC_URL", "")
    monkeypatch.setattr("app.PUBLIC_URL", "")
    phones.join("Player One", "p1@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    reply = client.get(f"/api/rooms/{code}", headers={
        "host": "arena-abc123.a.run.app", "x-forwarded-proto": "https"})
    assert reply.json()["join_url"] == "https://arena-abc123.a.run.app/join/" + code


def test_an_explicit_public_url_still_wins(monkeypatch, client, phones):
    monkeypatch.setattr("app.PUBLIC_URL", "https://venue.example")
    phones.join("Player One", "p1@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    # Derivable headers present and contradicting the configured value.
    reply = client.get(f"/api/rooms/{code}", headers={
        "host": "ignored.example", "x-forwarded-proto": "https"})
    assert reply.json()["join_url"] == "https://venue.example/join/" + code


def test_sit_down_preserves_the_absolute_url_when_unset(monkeypatch, client, phones):
    """With ARENA_PUBLIC_URL unset, sitting down carries the same absolute join_url.

    This tests O1: the wall shows a correct absolute URL from read_room, and when
    the first player takes a seat the bus publishes a snapshot whose join_url must
    still be absolute, not relative.
    """
    monkeypatch.setenv("ARENA_PUBLIC_URL", "")
    monkeypatch.setattr("app.PUBLIC_URL", "")
    phones.join("Player One", "p1@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    read_reply = client.get(f"/api/rooms/{code}", headers={
        "host": "arena-abc123.a.run.app", "x-forwarded-proto": "https"})
    read_url = read_reply.json()["join_url"]
    assert read_url == "https://arena-abc123.a.run.app/join/" + code

    sit_reply = client.post(f"/api/rooms/{code}/seats/blue",
                           json={"philosophy": "high press"},
                           headers={"host": "arena-abc123.a.run.app",
                                   "x-forwarded-proto": "https"})
    sit_url = sit_reply.json()["join_url"]
    assert sit_url == read_url, "sitting down must preserve the absolute URL"


def test_websocket_derives_from_its_own_headers(monkeypatch, client, phones):
    """A websocket opened with forwarded headers gets a join_url naming that host."""
    monkeypatch.setenv("ARENA_PUBLIC_URL", "")
    monkeypatch.setattr("app.PUBLIC_URL", "")
    phones.join("Player One", "p1@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    with client.websocket_connect(f"/ws/rooms/{code}", headers={
        "host": "arena-abc123.a.run.app", "x-forwarded-proto": "https"}) as ws:
        message = ws.receive_json()
        assert message["type"] == "room"
        assert message["join_url"] == f"https://arena-abc123.a.run.app/join/{code}"


def test_different_hosts_get_different_origins(monkeypatch, client, phones):
    """Two requests with different hosts get different origins (no cross-request cache)."""
    monkeypatch.setenv("ARENA_PUBLIC_URL", "")
    monkeypatch.setattr("app.PUBLIC_URL", "")
    phones.join("Player One", "p1@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]

    first = client.get(f"/api/rooms/{code}", headers={
        "host": "first.example", "x-forwarded-proto": "https"})
    assert first.json()["join_url"] == f"https://first.example/join/{code}"

    second = client.get(f"/api/rooms/{code}", headers={
        "host": "second.example", "x-forwarded-proto": "https"})
    assert second.json()["join_url"] == f"https://second.example/join/{code}"


@pytest.mark.parametrize("malicious_host", [
    "evil.example#",
    "evil.example?",
    "ours.example@evil.example",
])
def test_host_validation_rejects_special_characters(monkeypatch, client, phones,
                                                    malicious_host):
    """A host with #, ?, or @ is rejected and falls back to localhost."""
    monkeypatch.setenv("ARENA_PUBLIC_URL", "")
    monkeypatch.setattr("app.PUBLIC_URL", "")
    phones.join("Player One", "p1@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    reply = client.get(f"/api/rooms/{code}", headers={
        "host": malicious_host, "x-forwarded-proto": "http"})
    assert "evil.example" not in reply.json()["join_url"]


def test_read_venue_returns_derived_origin(monkeypatch, client):
    """GET /api/venue with forwarded headers returns that origin as public_url."""
    monkeypatch.setenv("ARENA_PUBLIC_URL", "")
    monkeypatch.setattr("app.PUBLIC_URL", "")
    reply = client.get("/api/venue", headers={
        "host": "arena-xyz.a.run.app", "x-forwarded-proto": "https"})
    assert reply.json()["public_url"] == "https://arena-xyz.a.run.app"
