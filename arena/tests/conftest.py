"""Shared fixtures. One throwaway database, emptied between tests."""

import logging
import os
import socket
import threading
import time

import httpx
import psycopg
import pytest
import uvicorn
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
def real_arena_server(dsn, monkeypatch):
    """A real uvicorn server on a real socket, for tests that need one.

    TestClient and httpx.ASGITransport both collapse streaming responses into
    a single chunk, so they cannot tell a streaming handler from a buffering
    one. Only a real socket can see the difference.
    """
    monkeypatch.setenv("ARENA_DB", dsn)
    from app import app

    # Bind to 127.0.0.1:0 so the OS picks an available port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    host, port = sock.getsockname()

    # Track handler count to ensure logging is not reconfigured.
    app_logger = logging.getLogger("app")
    handler_count_before = len(app_logger.handlers)

    # Websockets enabled, because a close code only reaches a client over a
    # handshake something actually performed, and TestClient invents one. The
    # sansio implementation rather than plain `websockets`: the latter imports
    # `websockets.legacy` and the two deprecation warnings that come with it,
    # and this suite is held at one warning.
    config = uvicorn.Config(app, host=host, port=port, log_level="error",
                            log_config=None, ws="websockets-sansio")
    server = uvicorn.Server(config)

    def run_server():
        server.run(sockets=[sock])

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # Wait for the server to start, with a timeout.
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("uvicorn did not start in time")

    yield f"http://{host}:{port}"

    # Tear down: stop the server and join the thread.
    server.should_exit = True
    thread.join(timeout=2.0)
    if thread.is_alive():
        raise RuntimeError("uvicorn thread did not stop in time")

    # Ensure logging was not reconfigured.
    assert len(app_logger.handlers) == handler_count_before


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

        def fresh(self):
            """A phone nobody has joined on. Joining reads the cookie now."""
            client.cookies.clear()

    return Phones()


def physics_token(conn, code):
    """The token the grounds would be handed for this room.

    A test that drives a host socket is standing in for the grounds, and the
    grounds are told this over the control socket at kick-off. No HTTP response
    carries it any more, so a test reads it out of the table -- which a test
    has and a browser does not, which is the whole point of the split.
    """
    import rooms

    return rooms.by_code(conn, code)["host_client_id"]


@pytest.fixture
def live_room(client, conn, phones):
    """Open a room, seat Alex, and kick off. Returns (code, physics token)."""

    def _live_room(mode="solo"):
        phones.join("Alex Rivera", "alex@example.com")
        opened = client.post("/api/rooms", json={"mode": mode}).json()
        code = opened["code"]
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
        client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        client.post(f"/api/rooms/{code}/start")
        return code, physics_token(conn, code)

    return _live_room
