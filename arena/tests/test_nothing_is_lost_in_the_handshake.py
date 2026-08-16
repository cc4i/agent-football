"""What happens to a room between reading its snapshot and listening for more.

A socket used to send the snapshot first and subscribe to the bus afterwards.
Both of those steps are separated by an await -- the send is one -- so anything
published in between reached a bus this socket was not on yet, and a snapshot
taken before it. The event was in neither. A goal, a shout, a substitution: the
log has it and the live feed silently does not, until something makes the
client read the log again.

Subscribing first can only ever repeat something the snapshot already showed,
and every one of these messages is a statement of fact a client redraws from.
A duplicate is a repaint. A gap is a match the screen is wrong about.

The teardown is the same window from the other end: a pump cancelled and never
awaited leaves a task mid-send while the handler that owned it has returned,
which is how a hang-up ends in an exception nobody retrieves.
"""

import asyncio
import contextlib

import app as arena
import rooms
from bus import WALL, room_topic


def test_an_event_published_during_the_handshake_reaches_the_socket(client, live_room,
                                                                    monkeypatch):
    """Publish at the one instant the socket is neither snapshotted nor subscribed.

    The seam is the opening send, because that is the await the gap is made of.
    A goal at that moment is not a contrived case: a screen reloading at
    kick-off reconnects into the busiest second of the match.
    """
    code, _ = live_room()
    published = []
    real_send = arena.WebSocket.send_json

    async def somebody_scores_mid_handshake(self, message, *arguments, **keywords):
        if message.get("type") == "room" and not published:
            published.append(message)
            client.app.state.bus.publish(
                room_topic(code),
                {"type": "event", "seq": 999, "kind": "goal", "match_ms": 1000,
                 "payload": {"team": "blue"}})
        return await real_send(self, message, *arguments, **keywords)

    monkeypatch.setattr(arena.WebSocket, "send_json", somebody_scores_mid_handshake)
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        assert viewer.receive_json()["type"] == "room"
        monkeypatch.undo()
        assert published, "the seam never fired, so nothing raced"
        assert viewer.receive_json()["kind"] == "goal"


def test_a_room_going_live_during_the_wall_handshake_reaches_the_wall(client, live_room,
                                                                      monkeypatch):
    """The same window, one screen further out.

    The wall is pushed to rather than polled: it reads the list once and then
    stands in a venue all evening. The sweep does reconcile this one within
    five seconds, so the cost here is a tile that is late rather than a tile
    that is wrong forever -- but five seconds is most of a goal celebration.
    """
    live_room()
    published = []
    real_send = arena.WebSocket.send_json

    async def a_match_kicks_off_mid_handshake(self, message, *arguments, **keywords):
        if message.get("type") == "wall" and not published:
            published.append(message)
            client.app.state.bus.publish(WALL, {"type": "wall", "rooms": []})
        return await real_send(self, message, *arguments, **keywords)

    monkeypatch.setattr(arena.WebSocket, "send_json", a_match_kicks_off_mid_handshake)
    with client.websocket_connect("/ws/wall") as wall:
        assert wall.receive_json()["type"] == "wall"
        monkeypatch.undo()
        assert published, "the seam never fired, so nothing raced"
        assert wall.receive_json() == {"type": "wall", "rooms": []}


def test_the_snapshot_is_still_the_first_thing_down_the_wire(client, live_room):
    # Subscribing first must not have reordered the opening message: a client
    # reads the room before it can make sense of an event about it.
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        opening = viewer.receive_json()
    assert opening["type"] == "room"
    assert opening["code"] == code


def test_a_socket_that_hangs_up_leaves_no_pump_behind(client, live_room):
    """A cancelled pump has to be waited for, not merely told.

    `cancel()` is a request. The handler that made it used to return straight
    after, so the task was still live when the ASGI cycle ended and its next
    send landed on a socket being closed underneath it. That is how a hang-up
    ends in `ConnectionClosedError exception in shielded future` with nobody to
    catch it, which is what production logged twelve of in one second on
    2026-08-16.
    """
    code, _ = live_room()
    before = _tasks_named("_pump")
    for _ in range(5):
        with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
            viewer.receive_json()
    assert _tasks_named("_pump") == before


def test_a_wall_that_hangs_up_leaves_no_pump_behind(client, live_room):
    live_room()
    before = _tasks_named("_pump_wall") + _tasks_named("_until_closed")
    for _ in range(5):
        with client.websocket_connect("/ws/wall") as wall:
            wall.receive_json()
    assert _tasks_named("_pump_wall") + _tasks_named("_until_closed") == before


def _tasks_named(coroutine):
    """How many tasks running that coroutine are still alive on any loop.

    TestClient drives the app on a portal loop of its own, so the tasks are not
    on this thread's loop and `asyncio.all_tasks()` cannot see them. The garbage
    collector can.
    """
    import gc

    return sum(1 for task in gc.get_objects()
               if isinstance(task, asyncio.Task) and not task.done()
               and coroutine in str(task.get_coro()))


def test_a_room_socket_still_carries_what_the_bus_publishes(client, live_room):
    # The reordering must not have dropped the subscription itself.
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        client.app.state.bus.publish(
            room_topic(code),
            {"type": "event", "seq": 7, "kind": "goal", "match_ms": 1, "payload": {}})
        assert viewer.receive_json()["seq"] == 7


def test_a_socket_closing_leaves_the_bus_with_nobody_on_it(client, live_room):
    # Subscribing earlier means one more path out that has to unsubscribe.
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        assert client.app.state.bus.subscriber_count(room_topic(code)) == 1
    assert client.app.state.bus.subscriber_count(room_topic(code)) == 0


def test_a_socket_to_a_room_that_vanished_subscribes_to_nothing(client, phones):
    # The room is looked up before anything subscribes, and a 4404 must not
    # leave a subscription behind on a topic nobody will ever publish to.
    with contextlib.suppress(Exception):
        with client.websocket_connect("/ws/rooms/ZZZZ"):
            pass
    assert client.app.state.bus.topics() == ()


def test_the_opening_snapshot_still_puts_the_connection_back(client, live_room):
    # The read that fills the snapshot opens a transaction whichever order the
    # subscription happens in.
    import psycopg

    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        assert client.app.state.conn.info.transaction_status == \
            psycopg.pq.TransactionStatus.IDLE


def test_the_room_a_socket_opens_on_is_the_one_it_was_asked_for(client, live_room, conn):
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        opening = viewer.receive_json()
    assert opening["code"] == rooms.by_code(conn, code)["code"]
