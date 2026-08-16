"""What the liveness probe is allowed to conclude from a 200.

`/health` used to return a constant. It proved that a Python process was
running and nothing else: not that the one shared database connection still
answered, not that the event loop was still turning, not that the watchdog was
still giving up on rooms nobody was playing. An instance can lose all three and
go on answering a constant forever, and with `minScale: 1` and `maxScale: 1`
there is no second instance to carry the venue and no restart coming, because
the probe Cloud Run would restart it on is the one saying everything is fine.

Seen in production on 2026-08-16: the arena served nothing but its own probe
for forty minutes and Cloud Run never replaced it.

So the probe answers for the sweep instead. One completed turn of the watchdog
proves both things at once - the loop is turning, and the database answered,
because a turn that could not read the rooms does not count as a turn.
"""

import asyncio
import contextlib
import time

import pytest

import app as arena


def turn_the_watchdog_over(client, patience=2.0):
    """Run exactly one whole turn of the real watchdog loop, `finally` and all.

    The same trick `test_putting_the_connection_back` uses, and for the same
    reason: what a turn owes the instance is a property of the loop rather than
    of the sweep it calls.

    Held open until the stamp moves rather than for a fixed number of scheduler
    passes, because the turn has a real await in it -- telling a grounds its
    match is over -- and a test that cancelled the loop part way through that
    would be measuring its own impatience. A turn that never stamps, which is
    what a failed sweep is, costs the whole of `patience` and then goes on to
    assert exactly that.
    """
    swept = []
    real_sweep = arena._give_up_on_the_missing

    def one_turn_only(*arguments):
        arena.SWEEP_SECONDS = 3600
        try:
            return real_sweep(*arguments)
        finally:
            swept.append(True)

    async def one_turn():
        before = client.app.state.swept_at
        watchdog = asyncio.create_task(arena._watch_for_the_missing(client.app))
        deadline = time.monotonic() + patience
        while (not watchdog.done() and time.monotonic() < deadline
               and (not swept or client.app.state.swept_at == before)):
            await asyncio.sleep(0.005)
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog

    turning = pytest.MonkeyPatch()
    try:
        turning.setattr(arena, "SWEEP_SECONDS", 0)
        turning.setattr(arena, "_give_up_on_the_missing", one_turn_only)
        asyncio.run(one_turn())
    finally:
        turning.undo()
    assert swept, "the watchdog never got a turn"


def test_a_fresh_instance_is_fit_to_serve_before_its_first_sweep(client):
    # The startup probe arrives seconds after the port opens and the first
    # sweep is SWEEP_SECONDS behind it. An instance that failed its own startup
    # probe for the want of a turn it was never given would never go ready.
    answer = client.get("/health")
    assert answer.status_code == 200
    assert answer.json()["ok"] is True


def test_an_instance_whose_watchdog_has_stopped_is_not_fit_to_serve(client):
    # A loop that has stopped turning is an arena that will never give up on a
    # room again: every match whose screen goes hangs live on every wall in the
    # venue for the rest of the evening, and nothing anywhere says so.
    client.app.state.swept_at = time.monotonic() - arena.HEALTH_STALE_SECONDS - 1
    answer = client.get("/health")
    assert answer.status_code == 503
    assert answer.json()["ok"] is False


def test_the_refusal_says_how_long_it_has_been(client):
    # A probe reads the status code and nothing else, so the body is for
    # whoever curls this from a laptop wanting to know which way it went.
    client.app.state.swept_at = time.monotonic() - arena.HEALTH_STALE_SECONDS - 30
    body = client.get("/health").json()
    assert body["swept_ago"] >= arena.HEALTH_STALE_SECONDS


def test_a_completed_sweep_keeps_the_instance_fit(client, live_room):
    live_room()
    client.app.state.swept_at = time.monotonic() - arena.HEALTH_STALE_SECONDS - 1
    turn_the_watchdog_over(client)
    assert client.get("/health").status_code == 200


def test_a_sweep_that_could_not_read_the_rooms_does_not_count_as_a_turn(client, monkeypatch):
    """The database going away has to reach the probe, and this is how.

    The sweep is the only thing that touches the database on its own account,
    every SWEEP_SECONDS, whether or not anybody is playing. A connection that
    has stopped answering therefore stops the stamp, and a stopped stamp is
    what the probe reports. Nothing else in the arena notices at all: the
    routes that would fail are only reached when somebody calls one.
    """
    def the_database_has_gone(*arguments, **keywords):
        raise ConnectionError("the database is not there")

    monkeypatch.setattr(arena.rooms, "hosted_with_liveness", the_database_has_gone)
    before = client.app.state.swept_at
    turn_the_watchdog_over(client)
    monkeypatch.undo()

    assert client.app.state.swept_at == before, "a failed sweep stamped the instance fit"
    client.app.state.swept_at = time.monotonic() - arena.HEALTH_STALE_SECONDS - 1
    assert client.get("/health").status_code == 503


def test_the_probe_asks_the_database_nothing_itself(client, monkeypatch):
    """The one thing the probe must never do is hang.

    A probe that runs a statement of its own on the shared connection inherits
    every way that connection can wedge, and a wedged probe is a probe that
    times out rather than one that answers 503 - on the event loop the whole
    arena shares. The sweep has already asked the question; this only reads
    when it was last answered.
    """
    connection = client.app.state.conn
    asked = []
    real_execute = connection.execute
    monkeypatch.setattr(connection, "execute",
                        lambda *a, **k: (asked.append(a), real_execute(*a, **k))[1])
    assert client.get("/health").status_code == 200
    assert not asked, f"the probe ran {len(asked)} statement(s) of its own"


def test_the_health_route_still_names_the_service(client):
    # The grounds' own log line reads the arena's answer back, and a venue
    # running two of these behind one address is told which it reached.
    assert client.get("/health").json()["service"] == "arena"


def test_a_grounds_that_stopped_reading_cannot_stall_the_sweep(client, live_room,
                                                               monkeypatch):
    """The one await inside the watchdog's turn is now on a clock.

    Making the sweep the thing `/health` answers for gives anything that can
    block the sweep the power to have the instance restarted. This send is the
    only candidate, and the socket it goes to is by definition one whose match
    the arena has just given up on -- a far end that may well be gone with the
    connection none the wiser. Left unbounded it would hang the turn, stop the
    stamp, and take the arena down over a courtesy message.
    """
    import rooms

    class A_grounds_that_never_reads:
        async def send_json(self, message):
            await asyncio.Event().wait()

    code, _ = live_room()
    conn = client.app.state.conn
    rooms.heard_from(conn, rooms.by_code(conn, code)["id"], time.time() - 10_000)

    monkeypatch.setattr(arena, "TELLING_THE_GROUNDS_SECONDS", 0.05)
    drops = [(A_grounds_that_never_reads(), code)]
    real_sweep = arena._give_up_on_the_missing

    def hands_back_a_wedged_socket(connection, bus, now, held=None, farm=None,
                                   collected=None):
        gone = real_sweep(connection, bus, now, held, farm, [])
        if collected is not None:
            collected.extend(drops)
        return gone

    monkeypatch.setattr(arena, "_give_up_on_the_missing", hands_back_a_wedged_socket)
    before = client.app.state.swept_at
    turn_the_watchdog_over(client)
    monkeypatch.undo()

    assert client.app.state.swept_at != before, "the sweep never got past the wedged send"
    assert client.get("/health").status_code == 200
