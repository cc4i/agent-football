"""How often a host frame is allowed to ask the database what room it is in.

`_handle_from_host` re-read the room on every message. A match reports ten
frames a second and the venue is sized for twenty at once, so that was two
hundred `SELECT * FROM room` a second down the one shared connection, every one
of them a Cloud SQL round trip blocking the event loop.

Measured in production on 2026-08-16 with twenty real matches playing: `/health`
went from 2ms to 3.7s while the container sat at two per cent CPU, the liveness
probe timed out three times over, and Cloud Run shut the arena down ninety
seconds into the venue. None of that was work. It was waiting.

What the handler needs from the row is one field that never changes and two
that change in one direction only, so it does not need a fresh one every frame.
"""

import time

import pytest

import app as arena
import rooms


def counting(monkeypatch, name):
    """Count calls to one `rooms` function without changing what it does."""
    calls = []
    real = getattr(rooms, name)

    def counted(*arguments, **keywords):
        calls.append(arguments)
        return real(*arguments, **keywords)

    monkeypatch.setattr(arena.rooms, name, counted)
    return calls


def a_frame(clock=1, speed=1.0):
    return {"type": "host.state", "payload": {"clock": clock, "speed": speed}}


def test_a_live_match_is_not_re_read_on_every_frame(client, live_room, monkeypatch):
    """The number this is about. Thirty frames used to be thirty round trips."""
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        reads = counting(monkeypatch, "by_code")
        for tick in range(30):
            host.send_json(a_frame(clock=tick))
            host.receive_json()
        monkeypatch.undo()

    assert len(reads) <= 2, f"thirty frames cost {len(reads)} reads of the room"


def test_the_frames_still_arrive(client, live_room):
    # Whatever the handler does about the database, a frame has to reach the
    # room's watchers or there is no football on the screen.
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        host.send_json({"type": "host.state", "payload": {"clock": 7, "ball": [0.5, 0.5]}})
        frame = host.receive_json()
    assert frame["type"] == "state"
    assert frame["clock"] == 7


def test_a_room_waiting_to_kick_off_is_read_every_time(client, conn, phones,
                                                       grounds_connected, monkeypatch):
    """A lobby is not sending ten frames a second, so freshness is free there.

    And it has to be fresh: the socket is open before the whistle, and the
    frames that follow kick-off must not be dropped waiting for a cached
    `lobby` to expire.
    """
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    physics = rooms.by_code(conn, code)["host_client_id"]

    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        # Nothing is published for a room that has not kicked off.
        host.send_json({"type": "host.here"})
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
        client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        client.post(f"/api/rooms/{code}/start")
        # The very next frame has to be taken, not dropped for five seconds.
        host.send_json({"type": "host.state", "payload": {"clock": 1}})
        # Taking a seat, marking it ready, the whistle and the four patches
        # a philosophy writes all came down this socket first, so the frame is
        # a long way from being the next thing on it.
        for _ in range(30):
            arrived = host.receive_json()
            if arrived["type"] == "state":
                break
        else:
            raise AssertionError("the first frame after kick-off never arrived")
        assert arrived["clock"] == 1


def test_the_whistle_stops_the_frames_after_it(client, live_room):
    """The socket blows the whistle itself, so it cannot go on believing it is live."""
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "full_time",
                        "payload": {"score": [1, 0]}, "match_ms": 180000})
        assert host.receive_json()["kind"] == "full_time"
        host.receive_json()          # the room snapshot that closes it out

        host.send_json({"type": "host.state", "payload": {"clock": 999}})
        host.send_json({"type": "host.here"})
        # Nothing more may be published for a match that is over. Asking for a
        # frame that must never come would hang, so this asks the log instead.
    assert client.get(f"/api/rooms/{code}").json()["status"] == "finished"
    kinds = [entry["kind"] for entry in
             client.get(f"/api/rooms/{code}/events").json()["events"]]
    assert kinds[-1] == "full_time", f"something was logged after the whistle: {kinds[-3:]}"


def test_a_room_is_unranked_once_rather_than_once_a_frame(client, live_room, monkeypatch):
    """`_watch_the_clock` writes, so a stale `ranked` would write every frame.

    A host reporting anything but 1x takes its room off the boards. That is an
    UPDATE and a commit, and the read that used to precede it is what stopped
    it happening twice. A cached row has to carry the change itself.
    """
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        unranked = counting(monkeypatch, "unrank")
        for tick in range(10):
            host.send_json(a_frame(clock=tick, speed=3.0))
            host.receive_json()
        monkeypatch.undo()

    assert len(unranked) == 1, f"the room was unranked {len(unranked)} times"
    assert client.get(f"/api/rooms/{code}").json()["ranked"] is False


def test_a_client_that_is_not_the_host_is_still_refused(client, live_room):
    # The token is checked against the row whether the row is fresh or not.
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=not-the-host") as pretender:
        pretender.receive_json()
        pretender.send_json({"type": "host.state", "payload": {"clock": 5}})
        pretender.send_json({"type": "host.here"})
    assert client.get(f"/api/rooms/{code}").json()["status"] == "live"


def test_the_host_is_still_recorded_as_reporting(client, live_room, conn):
    # Liveness is what stops the sweep abandoning a match somebody is playing,
    # and it is stamped from the row this socket is acting on.
    code, physics = live_room()
    rooms.heard_from(conn, rooms.by_code(conn, code)["id"], time.time() - 10_000)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        host.send_json({"type": "host.here"})
        host.send_json({"type": "host.state", "payload": {"clock": 1}})
        host.receive_json()
    heard = rooms.by_code(conn, code)["last_heard_at"]
    assert time.time() - heard < 60, "the host socket stopped vouching for its room"


@pytest.mark.parametrize("speed", [1.0, 1])
def test_a_room_played_straight_stays_ranked(client, live_room, speed):
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        for tick in range(5):
            host.send_json(a_frame(clock=tick, speed=speed))
            host.receive_json()
    assert client.get(f"/api/rooms/{code}").json()["ranked"] is True
