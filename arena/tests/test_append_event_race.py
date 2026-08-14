"""A room's log is gapless, and four writers at once do not change that.

The captain puts one shout to all four specialists in parallel and each of
them PATCHes back, so four appends against one room really do overlap. Today a
single connection serialises them. This is the test that has to keep passing
the day somebody adds a pool, and the reason `append_event` locks the room
rather than trusting that nobody ever will.
"""

import threading

import psycopg
from psycopg.rows import dict_row

import db
import rooms


def test_four_concurrent_appends_get_four_distinct_numbers(conn, dsn):
    room = rooms.create_room(conn, "solo")
    room_id = room["id"]

    seqs, failures = [], []
    barrier = threading.Barrier(4)

    def append(index):
        writer = psycopg.connect(dsn, row_factory=dict_row)
        try:
            # Every writer reads MAX(seq) at the same moment, which is the
            # whole point: a lock that only works because the writers were
            # politely staggered is not a lock.
            barrier.wait(timeout=10)
            seqs.append(rooms.append_event(writer, room_id, "profile.patch",
                                           {"role": f"role{index}"}))
        except Exception as problem:
            failures.append(f"{type(problem).__name__}: {problem}")
            writer.rollback()
        finally:
            writer.close()

    writers = [threading.Thread(target=append, args=(i,)) for i in range(4)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=20)

    assert failures == []
    assert sorted(seqs) == [1, 2, 3, 4]


def test_the_log_reads_back_in_order(conn):
    room = rooms.create_room(conn, "solo")
    for index in range(5):
        rooms.append_event(conn, room["id"], "goal", {"n": index})
    assert [entry["seq"] for entry in rooms.events(conn, room["id"])] == [1, 2, 3, 4, 5]
