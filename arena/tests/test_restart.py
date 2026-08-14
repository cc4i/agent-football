"""A live match survives the seconds when two arenas are running.

Cloud Run replaces an instance by starting the new one before stopping the
old, so for a moment both are up and the host is still talking to whichever
one holds its socket. An arena that keeps host liveness in memory abandons
every match it has not personally heard from, which during a rollout is all
of them.
"""

import psycopg
from psycopg.rows import dict_row

import app
import rooms
from bus import Bus


NOW = 10_000.0


def go_live(conn):
    """A live room without a host, a seat or a kick-off.

    `rooms.start_match` insists on all three, rightly. None of them is what
    these tests are about, so the status is set directly, the same way
    test_abandoning.py's fixture would if it were not going through HTTP.
    """
    room = rooms.create_room(conn, "solo")
    conn.execute("UPDATE room SET status = 'live' WHERE id = %s", (room["id"],))
    conn.commit()
    return room


def test_a_second_arena_does_not_abandon_a_match_it_never_heard(conn):
    """The rollout in three moves, from the new instance's side.

    It sweeps once and finds a room it has never heard of; the host goes on
    talking to the instance being replaced, which keeps the room's liveness
    up to date; it sweeps again, past the grace it started counting on the
    first sweep. An arena that trusted only what it had heard for itself would
    abandon a perfectly healthy match on that second sweep, because everything
    it knows first-hand still dates from the first one.
    """
    room = go_live(conn)

    # Nobody has reported to this instance, so its first sweep gives the room
    # its grace rather than giving up on it.
    assert app._give_up_on_the_missing(conn, Bus(), NOW) == []

    # The host is talking to the instance holding its socket, twenty seconds
    # into that grace. This is the part an in-memory dict cannot see.
    rooms.heard_from(conn, room["id"], NOW + 20)

    late = NOW + app.HOST_GONE_SECONDS + 1
    assert app._give_up_on_the_missing(conn, Bus(), late) == []
    assert rooms.by_code(conn, room["code"])["status"] == "live"


def test_a_room_that_really_has_gone_quiet_is_still_given_up_on(conn):
    room = go_live(conn)
    rooms.heard_from(conn, room["id"], NOW)

    late = NOW + app.HOST_GONE_SECONDS + 1
    assert app._give_up_on_the_missing(conn, Bus(), late) == [room["code"]]
    assert rooms.by_code(conn, room["code"])["status"] == "abandoned"


def test_the_stamp_is_the_same_for_every_instance_reading_it(conn, dsn):
    # The point of the column: a second connection, standing in for a second
    # instance, sees the same liveness rather than an empty dict of its own.
    room = go_live(conn)
    rooms.heard_from(conn, room["id"], NOW)

    other = psycopg.connect(dsn, row_factory=dict_row)
    try:
        assert rooms.by_code(other, room["code"])["last_heard_at"] == NOW
    finally:
        other.close()
