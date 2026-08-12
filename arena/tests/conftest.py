"""Shared fixtures. Every test gets its own throwaway database file."""

import pytest
from fastapi.testclient import TestClient

import db


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "arena.db"


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def client(db_path, monkeypatch):
    # The app reads ARENA_DB when its lifespan runs, which TestClient triggers
    # on __enter__, so each test opens the app against its own database file.
    monkeypatch.setenv("ARENA_DB", str(db_path))
    from app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def phones(client):
    """Drive several phones from one TestClient by swapping the cookie jar."""

    class Phones:
        def join(self, name, email):
            client.cookies.clear()
            client.post("/api/players", json={"display_name": name, "email": email})
            return dict(client.cookies)

        def use(self, jar):
            client.cookies.clear()
            client.cookies.update(jar)

    return Phones()
