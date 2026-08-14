"""What becomes of a match whose screen walks off.

The arena learns a match is over from the client running it, which works right
up until that client is the thing that has gone: a shut laptop leaves the room
live forever, frozen on every wall in the venue, and tells its two managers
nothing. These cover the sweep that notices, and what it says.
"""

import asyncio
import time

import app as arena
import rooms

# A fixed clock, so a test can say when it is without waiting for it to be.
NOW = 10_000.0
LATE = NOW + arena.HOST_GONE_SECONDS + 1


def sweep(client, when):
    """Run one sweep, as at `when`, from inside the app's own event loop.

    The bus hands messages to sockets that are waiting on them, and waking one
    of those from the test's thread is not safe, so this goes the same way a
    route does.
    """
    state = client.app.state
    return client.portal.call(arena._give_up_on_the_missing, state.conn, state.bus, when)


def heard_now(client, code):
    """Say the host reported at NOW, on the fixed clock the file runs on."""
    state = client.app.state
    rooms.heard_from(state.conn, rooms.by_code(state.conn, code)["id"], NOW)


def test_a_room_whose_screen_has_stopped_reporting_is_given_up_on(client, live_room):
    code, _ = live_room()
    heard_now(client, code)

    assert sweep(client, LATE) == [code]
    assert rooms.by_code(client.app.state.conn, code)["status"] == "abandoned"


def test_a_screen_that_is_still_reporting_keeps_its_match(client, live_room):
    code, _ = live_room()
    heard_now(client, code)

    # Right up to the last second of the grace. A host that is merely slow, or
    # a tab somebody flicked away from and back, must not lose its match.
    assert sweep(client, NOW + arena.HOST_GONE_SECONDS) == []
    assert rooms.by_code(client.app.state.conn, code)["status"] == "live"


def test_a_room_nobody_has_reported_on_is_timed_from_the_first_sweep(client, live_room):
    # Kick-off comes from a phone, and the screen may take a moment to start
    # sending. It is also how a room left live by a process that has since died
    # is dealt with: the arena has never heard from it and never will.
    code, _ = live_room()

    assert sweep(client, NOW) == []
    assert sweep(client, LATE) == [code]


def test_both_dugouts_are_told_what_happened_and_why(client, live_room):
    code, _ = live_room()
    heard_now(client, code)

    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()                        # the opening snapshot
        sweep(client, LATE)
        said = phone.receive_json()
        ending = phone.receive_json()

    assert said["kind"] == "abandoned"
    assert said["payload"]["reason"] == arena.HOST_GONE_REASON
    assert ending["type"] == "room"
    assert ending["status"] == "abandoned"


def test_the_reason_is_still_there_for_a_phone_that_comes_back(client, live_room):
    # A manager whose screen died looks at their phone some minutes later. The
    # answer has to be in the log, because the socket said it once and to
    # whoever was listening at the time.
    code, _ = live_room()
    heard_now(client, code)
    sweep(client, LATE)

    events = client.get(f"/api/rooms/{code}/events?since=0").json()["events"]

    assert [entry["payload"]["reason"] for entry in events if entry["kind"] == "abandoned"] \
        == [arena.HOST_GONE_REASON]


def test_an_abandoned_match_is_worth_nothing_to_anybody(client, live_room):
    code, _ = live_room()
    heard_now(client, code)
    sweep(client, LATE)

    assert client.get(f"/api/rooms/{code}/result").json()["results"] == {}
    assert client.get("/api/board").json()["solo"] == []


def test_it_comes_off_the_wall(client, live_room):
    code, _ = live_room()
    heard_now(client, code)

    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()                         # the roster as it stands
        sweep(client, LATE)
        roster = wall.receive_json()

    assert roster["type"] == "wall"
    assert [room["code"] for room in roster["rooms"]] == []


def test_a_frame_from_the_host_is_the_room_saying_it_is_still_here(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"clock": 170}})
            # Waiting for the frame to come out the other side is what makes
            # this a test of the arena rather than of two threads.
            phone.receive_json()

    heard = rooms.by_code(client.app.state.conn, code)["last_heard_at"]
    assert sweep(client, heard + arena.HOST_GONE_SECONDS) == []
    assert sweep(client, heard + arena.HOST_GONE_SECONDS + 1) == [code]


def test_a_run_of_frames_from_one_screen_costs_one_write(client, live_room):
    # A screen publishes ten frames a second while a match runs, and stamping
    # the room on each of them would be ten committing writes a second per
    # match down the one shared connection. The first frame writes and the ones
    # behind it are carried by the first, which is exact enough for a sweep.
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"clock": 100}})
            phone.receive_json()
            first = rooms.by_code(client.app.state.conn, code)["last_heard_at"]
            host.send_json({"type": "host.state", "payload": {"clock": 200}})
            phone.receive_json()

    assert first is not None
    assert rooms.by_code(client.app.state.conn, code)["last_heard_at"] == first


def test_a_screen_that_keeps_talking_keeps_the_column_moving(client, live_room, monkeypatch):
    # The other half of that bargain. A host still sending frames an hour into
    # the evening has to keep the stamp under it fresh, or the throttle would
    # hand its match to the sweep. The hold-off is shortened rather than waited
    # out, and the socket is then closed to show the sweep still bites.
    monkeypatch.setattr(arena, "SWEEP_SECONDS", 0.01)
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"clock": 100}})
            phone.receive_json()
            first = rooms.by_code(client.app.state.conn, code)["last_heard_at"]
            time.sleep(0.02)
            host.send_json({"type": "host.state", "payload": {"clock": 200}})
            phone.receive_json()

    heard = rooms.by_code(client.app.state.conn, code)["last_heard_at"]
    assert heard > first
    assert sweep(client, heard + arena.HOST_GONE_SECONDS) == []
    assert sweep(client, heard + arena.HOST_GONE_SECONDS + 1) == [code]


def test_a_frame_from_an_impostor_is_not(client, live_room):
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=impostor") as liar:
        liar.receive_json()
        liar.send_json({"type": "host.state", "payload": {"clock": 170}})

    assert rooms.by_code(client.app.state.conn, code)["last_heard_at"] is None


def test_a_match_that_ended_properly_is_left_alone(client, live_room):
    # This used to be about forgetting: an in-memory dict would carry every code
    # a venue ever saw. Liveness lives on the room now, so there is nothing to
    # leak, and what is left worth asserting is that the sweep only looks at
    # matches that are still live.
    code, host_token = live_room()
    heard_now(client, code)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "full_time",
                        "payload": {"score": [1, 0]}})
        host.receive_json()

    assert sweep(client, NOW + arena.HOST_GONE_SECONDS + 1) == []
    assert rooms.by_code(client.app.state.conn, code)["status"] == "finished"


def test_the_watchdog_keeps_going_after_a_bad_sweep(client, monkeypatch):
    # One failed sweep must not take the watchdog down for the life of the
    # process: every room opened after it would then hang live forever.
    sweeps = []

    def explode(*arguments):
        sweeps.append(arguments)
        raise RuntimeError("the database went away")

    monkeypatch.setattr(arena, "_give_up_on_the_missing", explode)
    monkeypatch.setattr(arena, "SWEEP_SECONDS", 0.01)

    async def run():
        watchdog = asyncio.create_task(arena._watch_for_the_missing(client.app))
        await asyncio.sleep(0.05)
        watchdog.cancel()

    client.portal.call(run)

    assert len(sweeps) > 1
