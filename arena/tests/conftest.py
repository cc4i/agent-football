"""Shared fixtures. One throwaway database, emptied between tests."""

import asyncio
import contextlib
import json
import logging
import math
import os
import socket
import threading
import time

import httpx
import psycopg
import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient
from psycopg import conninfo, sql

import db
from tests.standins import connect_grounds

SERVICE_TOKEN = "test-service-token"


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

    with _serving(app) as url:
        yield url


@contextlib.contextmanager
def _serving(fastapi_app):
    """This app, on a port of its own, until the block ends.

    Its own function rather than only the fixture above, because the wall's E2E
    needs the same server around a different app -- one reloaded with the pitch
    mounted -- and a second copy of this would be a second thing to get wrong.
    """
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
    config = uvicorn.Config(fastapi_app, host=host, port=port, log_level="error",
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
    """Drive several phones from one TestClient by swapping the cookie jar.

    Under E1, a re-join without a cookie is a claim and needs the recovery code.
    This fixture remembers codes automatically: a returning manager on a new phone
    genuinely has their code, so a fixture that does not carry one is not modelling
    a returning manager, it is modelling an attacker.
    """

    class Phones:
        def __init__(self):
            self._codes = {}  # normalized email -> recovery_code

        def join(self, name, email, code=None):
            """Join as a manager. Remembers recovery codes from successful joins.

            Pass an explicit code to override (for testing wrong codes), or None
            to use the remembered code for this address if one exists.
            """
            client.cookies.clear()
            normalized = email.strip().lower() if email else ""
            if code is None and normalized:
                code = self._codes.get(normalized, "")
            resp = client.post("/api/players", json={"display_name": name, "email": email,
                                                      "recovery_code": code or ""})
            assert resp.status_code == 200, f"join failed: {resp.status_code} {resp.text}"
            if normalized:
                data = resp.json()
                if data.get("recovery_code"):
                    self._codes[normalized] = data["recovery_code"]
            return dict(client.cookies)

        def use(self, jar):
            client.cookies.clear()
            client.cookies.update(jar)

        def fresh(self):
            """A phone nobody has joined on. Joining reads the cookie now."""
            client.cookies.clear()

        def forget(self, email):
            """Forget the recovery code for this address, to test the attacker."""
            self._codes.pop(email.strip().lower(), None)

    return Phones()


@pytest.fixture
def service_headers(monkeypatch):
    """The shared secret between our own processes, and the header carrying it.

    Pinned here rather than read from the environment so a suite run cannot
    depend on whose `.env` is on the machine.
    """
    import app as arena_app

    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE_TOKEN)
    return {"X-Arena-Service": SERVICE_TOKEN}


@pytest.fixture
def grounds_connected(client):
    """A pitch available to run matches, for every test that kicks one off.

    Kick-off acquires somewhere to play now, so a test that starts a match
    without this gets the honest 503 rather than a live room. See
    `tests.standins` for why this is not a real control socket.
    """
    return connect_grounds(client.app)


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
def live_room(client, conn, phones, grounds_connected):
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


def whistle(client, code, physics, events, speeds=()):
    """Play out a match over the host socket and hang up. Returns nothing.

    `events` is (kind, match_ms, payload) triples in the order the host sends
    them, which is the only order anything downstream ever sees them in.
    """
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        for speed in speeds:
            host.send_json({"type": "host.state", "payload": {"clock": 90, "speed": speed}})
        for kind, match_ms, payload in events:
            host.send_json({"type": "host.event", "kind": kind,
                            "match_ms": match_ms, "payload": payload})
            host.receive_json()


def a_win(scorer="blue"):
    return [("kickoff", 0, {}),
            ("goal", 27_400, {"team": scorer, "scorer": "forward"}),
            ("full_time", 180_000, {"score": [1, 0]})]


@pytest.fixture
def finished(client, live_room):
    """A solo room played to a 1-0 win and scored. Returns the code."""
    code, physics = live_room()
    whistle(client, code, physics, a_win())
    return code


# ── A venue, and somebody standing in front of the big screen ──────────

# The number in the spec, and the number the wall was rebuilt for.
FIFTY = 50
# One of the fifty is played out to its last half-minute and the other
# forty-nine are not, so a tile's clock is drawn from something that varies
# rather than from fifty copies of the same number.
#
# It no longer decides anything. The wall used to score matches on how
# interesting they looked and this room won that arithmetic outright, which is
# what made the director's pick deterministic; the rule is the newest match
# now, so what these tests can count on is the room the fixture opens last.
ENDGAME_ROOM = 0
BUILT_PITCH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "game", "frontend", "dist")


@pytest.fixture
def wall_server(dsn, monkeypatch):
    """The arena serving the pitch it ships with, on a socket a browser can use.

    Reloaded rather than configured, because `ARENA_PITCH_DIR` is read when the
    module is imported -- see `test_pitch_mount.py`, which does the same for the
    same reason. Without it `/api/venue` sends the browser to the Vite dev
    server, and a wall E2E watching centre court fail to load would be testing
    the error path.
    """
    import importlib

    if not os.path.exists(os.path.join(BUILT_PITCH, "viewer.js")):
        pytest.skip(f"no built pitch at {BUILT_PITCH}: run `npm run build` in game/frontend")
    monkeypatch.setenv("ARENA_PITCH_DIR", BUILT_PITCH)
    monkeypatch.setenv("ARENA_DB", dsn)
    import app as app_module

    importlib.reload(app_module)
    with _serving(app_module.app) as url:
        yield url
    # Delete the variable before restoring, or the reload re-reads it: pytest
    # unwinds monkeypatch after this fixture, not during it.
    monkeypatch.delenv("ARENA_PITCH_DIR", raising=False)
    importlib.reload(app_module)


@pytest.fixture
async def fifty_live_rooms(wall_server):
    """Fifty matches being played, each reported by a stand-in for its grounds.

    Not fifty browsers. What is under test is a wall with more matches on it
    than fit on a screen; fifty real simulations would be a test of Chromium's
    scheduler, and the arena cannot tell the difference -- a host is whatever
    holds the room's physics token and sends frames.
    """
    import app as app_module

    farm = connect_grounds(app_module.app, capacity=FIFTY + 4)
    codes = []
    async with httpx.AsyncClient(base_url=wall_server, timeout=30) as phone:
        for index in range(FIFTY):
            # One cookie jar per room, because a manager may hold one seat.
            phone.cookies.clear()
            await phone.post("/api/players",
                             json={"display_name": f"Manager {index:02d}", "email": ""})
            opened = await phone.post("/api/rooms", json={"mode": "solo"})
            code = opened.json()["code"]
            await phone.post(f"/api/rooms/{code}/seats/blue",
                             json={"philosophy": "high press"})
            await phone.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
            (await phone.post(f"/api/rooms/{code}/start")).raise_for_status()
            codes.append(code)

    # The physics tokens went down the control socket at kick-off and nowhere
    # else, which is the whole point of the split: the stand-ins read them from
    # the grounds they are standing in for.
    #
    # Waited for rather than read the instant the last `/start` returns. The
    # send is awaited inside that request, so fifty replies ought to mean fifty
    # assignments -- but this has come up short once, and an assertion that
    # fires on the first pass turns a slow one into a failure that says nothing
    # about which room it was. Twenty seconds of patience costs a passing run
    # nothing and gives a failing one the codes.
    await _until(lambda: len(farm.assignments) >= FIFTY,
                 f"the arena assigned a pitch to only {{}} of {FIFTY} rooms",
                 farm.assignments)
    tokens = {sent["code"]: sent["token"] for sent in farm.assignments}
    assert set(tokens) == set(codes), (
        "the arena did not assign every room a pitch; missing "
        f"{sorted(set(codes) - set(tokens))}, unexpected "
        f"{sorted(set(tokens) - set(codes))}")

    socket_url = wall_server.replace("http://", "ws://", 1)
    stop = asyncio.Event()
    reporting = []
    hosts = [asyncio.create_task(_a_stand_in_host(socket_url, code, tokens[code],
                                                  index, stop, reporting))
             for index, code in enumerate(codes)]
    try:
        await _until(lambda: len(reporting) == FIFTY,
                     f"only {{}} of {FIFTY} rooms reported a frame", reporting)
        yield codes
    finally:
        stop.set()
        await asyncio.gather(*hosts, return_exceptions=True)


async def _until(done, complaint, watching, patience=20.0):
    """Hold until it is true, then say what was missing if it never was."""
    deadline = time.monotonic() + patience
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError(complaint.format(len(watching)))
        await asyncio.sleep(0.1)


async def _a_stand_in_host(url, code, token, index, stop, reporting):
    """One room's physics, as far as the arena can tell: frames, and nothing else."""
    async with websockets.connect(f"{url}/ws/rooms/{code}?client_id={token}") as wire:
        await wire.recv()                       # the opening room snapshot
        # A host socket is subscribed to its own room like any other listener.
        # Fifty that never read would fill fifty send buffers and manufacture
        # drops a browser -- which does read -- would never produce.
        reader = asyncio.create_task(_swallow(wire))
        try:
            tick = 0
            while not stop.is_set():
                await wire.send(json.dumps({"type": "host.state",
                                            "payload": _a_frame(index, tick)}))
                if not tick:
                    reporting.append(code)
                tick += 1
                # The rate the wall thins to anyway. Ten a second from fifty
                # rooms would be the load rehearsal, which is its own test.
                await asyncio.sleep(0.25)
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader


async def _swallow(wire):
    """Read and throw away. Something has to, or the buffer becomes the test."""
    while True:
        await wire.recv()


def _a_frame(index, tick):
    """Ten dots and a ball, moving, so a tile is a match rather than a picture."""
    turn = tick / 8 + index
    spot = (lambda seat, side: [round(0.5 + side * (0.1 + 0.07 * seat) * math.cos(turn + seat), 4),
                                round(0.5 + (0.1 + 0.07 * seat) * math.sin(turn + seat), 4)])
    return {
        # Level, so nothing about a frame distinguishes one match from another
        # bar the clock. See ENDGAME_ROOM.
        "score": [1, 1],
        "clock": 20 if index == ENDGAME_ROOM else 120,
        "blue": [spot(seat, -1) for seat in range(5)],
        "red": [spot(seat, 1) for seat in range(5)],
        "ball": [round(0.5 + 0.3 * math.cos(turn * 1.7), 4),
                 round(0.5 + 0.2 * math.sin(turn * 1.3), 4)],
    }


def _worth_complaining_about(note, complaints):
    """Console errors the arena is answerable for.

    Everything except the browser's own line for a failed subresource, which
    carries no URL at all: the response listener reports those, scoped to the
    arena's own origin, with something in them a person can act on.
    """
    if note.type != "error":
        return
    if note.text.startswith("Failed to load resource"):
        return
    complaints.append(note.text)


@pytest.fixture
async def wall_page(wall_server, fifty_live_rooms):
    """A browser in front of the big screen, with the venue already playing.

    1920 by 1080 because how many tiles fit across is the thing under test, and
    that is the screen the wall is hung on.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as driving:
        browser = await driving.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        complaints = []
        page.on("console", lambda note: _worth_complaining_about(note, complaints))
        page.on("pageerror", lambda blew_up: complaints.append(str(blew_up)))
        # Subresource failures, named. The console's own line for one says only
        # `Failed to load resource: ... 404 ()` and never which resource, so a
        # failure here used to give nobody anything to go and look at.
        #
        # The arena's own origin only. Google Fonts rotates the woff2 behind a
        # stylesheet browsers have cached and answers 404 for the old name,
        # which fails roughly one run in three and has nothing to do with the
        # venue. What the wall depends on a CDN for is a font, and the fallback
        # is in `app.css`.
        page.on("response",
                lambda answer: complaints.append(
                    f"{answer.status} {answer.request.method} {answer.url}")
                if answer.status >= 400 and answer.url.startswith(wall_server) else None)
        await page.goto(f"{wall_server}/arena")
        # A tile appears as soon as the roster lands, which is before a single
        # frame has. The screen is only up when the director has framed a match:
        # until then every room is still on the strip, because none of them is
        # the one being watched.
        await page.wait_for_selector(".tile[data-code]", timeout=30_000)
        await page.wait_for_function("() => document.querySelector('#court').dataset.showing",
                                     timeout=30_000)
        yield page
        await browser.close()
    # After the browser is shut, so a test that fails on its own terms fails on
    # its own terms. A wall runs all evening: a console error is a defect.
    assert not complaints, f"the wall logged errors: {complaints}"


async def _a_clip(podiums):
    """What the announcer's two model calls would have come back with.

    Four seconds of silence, so a real element really plays and really ends,
    without a test spending forty seconds listening to it.

    The two halves are the length a shoutcaster's are and are not the same
    length as each other, because that is what the caption has to sit still
    through: the first wraps to two lines on a big screen and the second does
    not. A four-word stand-in is one line either way and proves nothing about a
    caption that reflows the standings under it.
    """
    return b"\x00\x00" * 96_000, {
        "solo": "one two three four and ALEX RIVERA is TOP of the score "
                "attack board tonight with FORTY-ONE points, five goals "
                "for and one against, a strike inside the first minute, "
                "and three shouts from the touchline the squad actually "
                "did something with",
        "versus": "five six seven eight and SAM OKAFOR is UNBEATEN, five "
                  "wins from five, eleven goals of difference"}


class _Venue:
    """A running arena, and the things a test does to it from behind the page.

    The page under test is a browser. It cannot open a room, take a seat or
    blow a whistle, and what these tests are about is what it does when
    somebody else in the building does. Everything here goes through the door a
    phone and a grounds go through, because that is the only door there is.
    """

    def __init__(self, app_module, url):
        self._app = app_module
        self._url = url
        self._managers = 0

    async def a_match_kicks_off(self):
        """A room seated, readied and started. Returns its code."""
        self._managers += 1
        name = f"Manager {self._managers}"
        async with httpx.AsyncClient(base_url=self._url, timeout=30) as phone:
            await phone.post("/api/players",
                             json={"display_name": name,
                                   "email": f"manager{self._managers}@example.com"})
            opened = await phone.post("/api/rooms", json={"mode": "solo"})
            code = opened.json()["code"]
            await phone.post(f"/api/rooms/{code}/seats/blue",
                             json={"philosophy": "high press"})
            await phone.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
            await phone.post(f"/api/rooms/{code}/start")
        return code

    async def somebody_wins(self):
        """One match played out to a 1-0 win and scored, so the board is not
        empty. `a_win()` is the event sequence, over the host's own socket."""
        import rooms

        code = await self.a_match_kicks_off()
        physics = rooms.by_code(self._app.app.state.conn, code)["host_client_id"]
        wire_url = self._url.replace("http://", "ws://", 1)
        async with websockets.connect(
                f"{wire_url}/ws/rooms/{code}?client_id={physics}") as wire:
            await wire.recv()
            for kind, match_ms, payload in a_win():
                await wire.send(json.dumps({"type": "host.event", "kind": kind,
                                            "match_ms": match_ms, "payload": payload}))
                await wire.recv()
        return code


@contextlib.asynccontextmanager
async def _an_arena_that_can_talk(dsn, monkeypatch):
    """The arena on a socket a browser can reach, announcer on and free."""
    import importlib

    monkeypatch.setenv("ARENA_DB", dsn)
    import app as app_module
    import announcer
    importlib.reload(app_module)

    # Monkeypatch after the reload, otherwise the reload resets the module state
    monkeypatch.setattr(announcer, "ENABLED", True)
    monkeypatch.setattr(announcer, "API_KEY", "a-key")
    monkeypatch.setattr(announcer, "_generate", _a_clip)

    try:
        with _serving(app_module.app) as url:
            connect_grounds(app_module.app)
            yield app_module, url
    finally:
        importlib.reload(app_module)


@contextlib.asynccontextmanager
async def _a_big_screen_at(url, ready):
    """A browser in front of /arena, held until `ready` is on the page."""
    from playwright.async_api import async_playwright

    complaints = []
    async with async_playwright() as driving:
        browser = await driving.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("console", lambda note: _worth_complaining_about(note, complaints))
        page.on("pageerror", lambda blew_up: complaints.append(str(blew_up)))
        await page.goto(f"{url}/arena")
        await page.wait_for_selector(ready, timeout=30_000)
        try:
            yield page
        finally:
            await browser.close()
    # The lobby tries to load centre court, which needs a built pitch. That
    # error is not what the button tests, so filter it out.
    real_complaints = [c for c in complaints if "viewer.js" not in c]
    assert not real_complaints, f"the lobby logged errors: {real_complaints}"


@pytest.fixture
async def lobby_and_venue(dsn, monkeypatch):
    """A browser in front of an arena that has a lobby and a stocked board,
    and the venue behind it, for the tests that need something to happen in the
    building while the screen is up.

    Not `wall_page`: that one fills the venue with fifty live matches, and a
    screen showing football has no lobby and so no button.
    """
    async with _an_arena_that_can_talk(dsn, monkeypatch) as (app_module, url):
        venue = _Venue(app_module, url)
        await venue.somebody_wins()
        async with _a_big_screen_at(url, "#announce:not([hidden])") as page:
            yield page, venue


@pytest.fixture
def lobby_page(lobby_and_venue):
    """The same screen, for the tests that only press the button."""
    page, _ = lobby_and_venue
    return page


@pytest.fixture
async def early_lobby_and_venue(dsn, monkeypatch):
    """The state a venue opens in: a big screen up, nobody on the board yet.

    The longest state of the evening, and the one the button must stay out of.
    """
    async with _an_arena_that_can_talk(dsn, monkeypatch) as (app_module, url):
        async with _a_big_screen_at(url, "#lobby:not([hidden])") as page:
            yield page, _Venue(app_module, url)
