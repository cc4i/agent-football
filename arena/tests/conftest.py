"""Shared fixtures. One throwaway database, emptied between tests."""

import os

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import conninfo, sql

import db


@pytest.fixture(scope="session")
def dsn():
    """A database of the suite's own, dropped and remade once per run.

    One database rather than one per test: creating a database costs about a
    tenth of a second and there are hundreds of tests, so they share one and
    the autouse fixture below empties it. `WITH (FORCE)` because a test that
    failed mid-connection would otherwise leave the drop blocked.
    """
    target = os.environ.get("ARENA_TEST_DB", "postgresql:///arena_test")
    name = conninfo.conninfo_to_dict(target)["dbname"]
    admin = conninfo.make_conninfo(target, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as maintenance:
        maintenance.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    connection = db.connect(target)
    db.init_db(connection)
    connection.close()
    return target


@pytest.fixture(autouse=True)
def empty_tables(dsn):
    """Every test starts on an empty database, including the workshop room.

    RESTART IDENTITY so that a test asserting on a player id of 1 is not
    written against whichever tests happened to run before it.
    """
    with psycopg.connect(dsn, autocommit=True) as scrub:
        scrub.execute(f"TRUNCATE {', '.join(db.TABLES)} RESTART IDENTITY CASCADE")


@pytest.fixture
def conn(dsn):
    connection = db.connect(dsn)
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def client(dsn, monkeypatch):
    # The app reads ARENA_DB when its lifespan runs, which TestClient triggers
    # on __enter__, so each test opens the app against the test database.
    monkeypatch.setenv("ARENA_DB", dsn)
    from app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def arena(dsn, monkeypatch):
    """The app on the test's own event loop, for anything with a chain in it.

    TestClient drives the app from a thread of its own, which is right for a
    test that only makes requests and wrong for one whose background chain has
    to make requests back while the test waits. Both ends share a loop here.
    """
    monkeypatch.setenv("ARENA_DB", dsn)
    from app import app

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://arena.test") as caller:
            caller.app = app
            yield caller


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


@pytest.fixture
def live_room(client, phones):
    """Open a room, seat Alex, and kick off. Returns (code, host_token)."""

    def _live_room(mode="solo"):
        phones.join("Alex Rivera", "alex@example.com")
        opened = client.post("/api/rooms", json={"mode": mode}).json()
        code = opened["code"]
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
        client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        client.post(f"/api/rooms/{code}/start")
        return code, opened["host_token"]

    return _live_room
