"""The shared connection has to come back idle from every unit of work.

psycopg opens a transaction on the first statement of any kind, reads included,
and the whole arena runs on one connection. Left open, a read pins the vacuum
horizon on a board meant to last weeks; left aborted, a write that hit a
constraint fails every later statement in the process.
"""

import asyncio
import contextlib
import time

import psycopg
import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

import codes
import rooms


def idle(client):
    status = client.app.state.conn.info.transaction_status
    return status == psycopg.pq.TransactionStatus.IDLE


def gone_quiet(client, code):
    """Stamp this room as last heard from long enough ago to be given up on."""
    conn = client.app.state.conn
    rooms.heard_from(conn, rooms.by_code(conn, code)["id"], time.time() - 10_000)


def test_a_boot_that_found_the_workshop_already_open_leaves_nothing_open(client):
    # The first boot creates the workshop and commits on the way; every boot
    # after it only reads. An instance can be up for a good while before its
    # first caller arrives, and until then that read is all it has done.
    from fastapi.testclient import TestClient

    with TestClient(client.app) as restarted:
        assert idle(restarted)


def test_a_read_leaves_no_transaction_open(client):
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200
    assert idle(client)


def test_a_write_leaves_no_transaction_open(client):
    assert client.post("/api/players",
                       json={"display_name": "Alex Rivera",
                             "email": "alex@example.com"}).status_code == 200
    assert idle(client)


def test_a_refused_request_leaves_no_transaction_open(client):
    # The 404 is raised after the lookup that opened the transaction.
    assert client.get("/api/rooms/ZZZZ/teams/blue/profiles").status_code == 404
    assert idle(client)


def test_a_socket_that_only_listens_leaves_no_transaction_open(client, live_room):
    # The snapshot on connect is a read like any other, and a wall screen holds
    # its socket open for the whole evening.
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        assert idle(client)
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        assert idle(client)


def test_a_socket_to_a_room_that_is_not_there_leaves_no_transaction_open(client):
    # A QR left on a table outlives the room it was printed for, and every
    # scan of it gets as far as the lookup that says so.
    with contextlib.suppress(Exception):
        with client.websocket_connect("/ws/rooms/ZZZZ"):
            pass
    assert idle(client)


def the_screen_leaves_between_the_read_and_the_send(monkeypatch):
    """Make the first frame out of a socket fail, the way a shut laptop does.

    The read that fills that frame has already happened when the send is
    reached - it is the argument - so this is the one window in which a socket
    can open a transaction and never arrive at the line that puts it back.
    Failing the send itself rather than staging a disconnect leaves the arena's
    own code, `finally` and all, exactly as it ships.
    """
    async def the_screen_is_gone(self, *arguments, **keywords):
        raise WebSocketDisconnect(1006)

    monkeypatch.setattr(WebSocket, "send_json", the_screen_is_gone)


def test_a_snapshot_nobody_was_there_to_receive_leaves_no_transaction_open(client, live_room,
                                                                           monkeypatch):
    # A phone that scans a QR and locks itself a moment later. The room was
    # read either way, and the socket is gone, so nothing will come back for it.
    code, _ = live_room()
    the_screen_leaves_between_the_read_and_the_send(monkeypatch)
    with contextlib.suppress(Exception):
        with client.websocket_connect(f"/ws/rooms/{code}"):
            pass
    monkeypatch.undo()
    assert idle(client)
    assert client.get(f"/api/rooms/{code}").status_code == 200


def test_a_wall_nobody_was_there_to_receive_leaves_no_transaction_open(client, live_room,
                                                                       monkeypatch):
    # Same window, and on the wall it is a screen at the far end of the venue
    # that a cleaner unplugged. Nobody reconnects it until the morning, and the
    # transaction its last read opened would wait exactly that long.
    live_room()
    the_screen_leaves_between_the_read_and_the_send(monkeypatch)
    with contextlib.suppress(Exception):
        with client.websocket_connect("/ws/wall"):
            pass
    monkeypatch.undo()
    assert idle(client)
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200


def test_a_socket_message_leaves_no_transaction_open(client, live_room):
    # A state frame rather than an event: an event ends in `append_event`'s own
    # commit, so it would come back idle whether the socket put the connection
    # back or not. A host running at 1x writes nothing, and the room it was
    # read against is the transaction left behind.
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.state", "payload": {"clock": 5, "speed": 1.0}})
        # The frame comes back down once the message has been handled, so by
        # the time it arrives the connection should be back.
        host.receive_json()
        assert idle(client)


def test_a_write_that_failed_mid_request_does_not_brick_the_arena(client, monkeypatch):
    """The middleware is the net under every write nobody has hardened yet.

    The two writes a rollout could actually lose a race on now answer for
    themselves - `upsert_player` upserts, `take_seat` catches its violation -
    so neither of them can be used to abort the connection any more. The fault
    here is injected rather than raced for: a genuine server-side refusal in
    the middle of a route's write, which is the state a lost race leaves
    behind. It stands in for the next write somebody adds without thinking
    about two instances, which is exactly what the middleware is there for.
    """
    def a_write_nobody_hardened(conn, *arguments, **keywords):
        conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, "
                     "joined_at) VALUES (424242, 'blue', 424242, 'counter', 0, 0)")

    monkeypatch.setattr(rooms, "create_room", a_write_nobody_hardened)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        client.post("/api/rooms", json={"mode": "solo"})

    # The whole point: everybody else's next request still works.
    monkeypatch.undo()
    assert idle(client)
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200
    assert client.post("/api/rooms", json={"mode": "solo"}).status_code == 200


def test_two_managers_reaching_for_one_dugout_both_get_an_answer(client, dsn, phones,
                                                                 monkeypatch):
    """A rollout runs two instances at once, so check-then-write stops being safe.

    `take_seat` looks for an occupant, finds none, and inserts. Another
    instance seating somebody there in between turns that insert into a
    violation of the seat primary key. Unlike a repeat join this one cannot be
    designed away - one of two people reaching for the same dugout has to lose
    - so what the loser is owed is the answer the rules already have for a
    taken dugout, and a connection everybody else can carry on using.

    The seam is the shared connection's own `execute`, keyed to the statement
    the race is about, so the other instance is let in at the one instant that
    makes it the winner. If that statement ever moves above the check, the seam
    stops firing and the assertion below says so rather than passing quietly.
    """
    alex = phones.join("Alex Rivera", "alex@example.com")
    phones.join("Sam Okafor", "sam@example.com")
    phones.use(alex)
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    connection = client.app.state.conn
    real_execute = connection.execute
    already = []

    def the_other_instance_gets_there_first(query, *arguments, **keywords):
        if not already and str(query).startswith("INSERT INTO seat"):
            already.append(query)
            with psycopg.connect(dsn, autocommit=True) as rival:
                room_id = rival.execute("SELECT id FROM room WHERE code = %s",
                                        (code,)).fetchone()[0]
                # Still empty, so this really is the winning side of the race
                # and the losing side is the statement about to run below.
                assert rival.execute("SELECT 1 FROM seat WHERE room_id = %s AND team = 'blue'",
                                     (room_id,)).fetchone() is None
                rival.execute(
                    "INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                    "SELECT %s, 'blue', id, 'counter', 0, 0 FROM player "
                    "WHERE display_name = 'Sam Okafor'", (room_id,))
        return real_execute(query, *arguments, **keywords)

    monkeypatch.setattr(connection, "execute", the_other_instance_gets_there_first)
    refused = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    monkeypatch.undo()

    assert already, "the seam never fired, so nothing raced"
    assert refused.status_code == 409
    assert refused.json()["detail"] == "the blue dugout is taken"
    assert idle(client)
    assert client.get(f"/api/rooms/{code}").status_code == 200
    assert client.post(f"/api/rooms/{code}/seats/red",
                       json={"philosophy": "counter"}).status_code == 200


def test_one_phone_tapped_twice_does_not_end_up_in_both_dugouts(client, dsn, phones,
                                                                monkeypatch):
    """The other rule `take_seat` states in prose and used to check for twice.

    A player holds one dugout in a room, and until the unique index went in
    nothing but a read said so. Two taps close enough together - one phone, no
    rollout needed - both passed that read and both inserted, and head to head
    scoring would then have rated a player against themselves. Same seam as the
    race above, and the loser is told the same thing the check would have told
    them.
    """
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    connection = client.app.state.conn
    real_execute = connection.execute
    already = []

    def the_other_tap_lands_first(query, *arguments, **keywords):
        if not already and str(query).startswith("INSERT INTO seat"):
            already.append(query)
            with psycopg.connect(dsn, autocommit=True) as rival:
                rival.execute(
                    "INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                    "SELECT room.id, 'red', player.id, 'counter', 0, 0 FROM room, player "
                    "WHERE room.code = %s AND player.display_name = 'Alex Rivera'", (code,))
        return real_execute(query, *arguments, **keywords)

    monkeypatch.setattr(connection, "execute", the_other_tap_lands_first)
    refused = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    monkeypatch.undo()

    assert already, "the seam never fired, so nothing raced"
    assert refused.status_code == 409
    assert refused.json()["detail"] == "you already have a dugout in this match"
    assert client.get(f"/api/rooms/{code}").json()["seats"].keys() == {"red"}
    assert idle(client)


def turn_the_watchdog_over(client):
    """Run exactly one turn of the real watchdog loop, `finally` and all.

    The loop is where a sweep's unit of work begins and ends, so a test of what
    it owes the connection has to be a test of the loop rather than of the
    sweep it calls.
    """
    import app as arena

    swept = []
    real_sweep = arena._give_up_on_the_missing

    def one_turn_only(*arguments):
        # The turn after this one parks for an hour, so the loop is asleep
        # rather than mid-sweep when the assertions look at the connection.
        arena.SWEEP_SECONDS = 3600
        try:
            return real_sweep(*arguments)
        finally:
            swept.append(True)

    async def one_turn():
        watchdog = asyncio.create_task(arena._watch_for_the_missing(client.app))
        # Nothing the loop does between the sweep returning and its next sleep
        # awaits, so by the time this coroutine runs again the `finally` has
        # been and gone.
        while not swept and not watchdog.done():
            await asyncio.sleep(0)
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


def test_a_sweep_that_gives_up_on_nothing_leaves_no_transaction_open(client, live_room):
    # Reading the live rooms is the sweep's first statement and, on an evening
    # where every host is still talking, its only one.
    live_room()
    turn_the_watchdog_over(client)
    assert idle(client)


def test_a_sweep_whose_write_failed_does_not_take_the_watchdog_with_it(client, live_room,
                                                                       monkeypatch):
    """A bad sweep must cost one sweep, not every sweep after it.

    Two instances sweeping the same silent room at once is the ordinary case
    during a rollout, and concurrent `append_event` is already on record as
    producing a UNIQUE violation. That violation cannot be staged from a single
    connection - `append_event` re-reads the room's high-water mark inside its
    own statement, so a rival that has already committed is simply counted -
    and what it leaves behind is a transaction in error. That is what is staged
    here, on the real connection, with a real refusal from the server.

    Logging the failure is not enough on its own, which is what this proves:
    without the rollback the next sweep never gets to run and the room hangs
    live on every wall in the venue for the rest of the evening.
    """
    import app as arena

    code, _ = live_room()
    gone_quiet(client, code)

    def the_sweep_loses_the_race(conn, room_id, kind, payload, match_ms=None):
        for _ in range(2):
            conn.execute("INSERT INTO event (room_id, seq, kind, payload_json, match_ms, "
                         "wall_ts) VALUES (%s, 424242, 'abandoned', '{}', NULL, 0)", (room_id,))

    monkeypatch.setattr(arena.rooms, "append_event", the_sweep_loses_the_race)
    turn_the_watchdog_over(client)
    monkeypatch.undo()
    assert idle(client)

    # The next sweep is the one that matters: it is the room's second chance.
    gone_quiet(client, code)
    turn_the_watchdog_over(client)
    assert client.get(f"/api/rooms/{code}").json()["status"] == "abandoned"
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200
