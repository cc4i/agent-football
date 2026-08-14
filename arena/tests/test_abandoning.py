"""What becomes of a room whose screen walks off.

The arena learns a match is over from the client running it, which works right
up until that client is the thing that has gone: a shut laptop leaves the room
live forever, frozen on every wall in the venue, and tells its two managers
nothing. These cover the sweep that notices, and what it says.

The same rule covers a room still in its lobby, for a reason that reads worse.
A lobby is advertised to every phone with no room of its own, and the tab that
opened it is the only thing that can ever run it. Close that tab and the room
is a code that will never do anything, still at the top of everybody's list
saying "nobody in it yet".
"""

import asyncio
import re
import time

import app as arena
import codes
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


def open_lobby(client, phones, mode="solo"):
    """A room on a screen, with nobody in it yet. Returns (code, host_token)."""
    phones.join("Alex Rivera", "alex@example.com")
    opened = client.post("/api/rooms", json={"mode": mode}).json()
    return opened["code"], opened["host_token"]


def once_heard(client, code, within=2.0):
    """Wait for a `host.here` to land, and answer with what it wrote.

    The other waits in this file are for a frame coming back out of the arena,
    which is the honest way to know it went in. `host.here` publishes nothing
    on purpose -- a screen saying it exists is not news anybody is watching for
    -- so the column it writes is the only thing there is to watch.
    """
    connection = client.app.state.conn
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        heard = rooms.by_code(connection, code)["last_heard_at"]
        if heard is not None:
            return heard
        time.sleep(0.01)
    raise AssertionError(f"nothing from the screen holding {code}")


def test_a_room_whose_screen_has_stopped_reporting_is_given_up_on(client, live_room):
    code, _ = live_room()
    heard_now(client, code)

    assert sweep(client, LATE) == [code]
    assert rooms.by_code(client.app.state.conn, code)["status"] == "abandoned"


def test_a_room_waiting_on_a_screen_that_has_gone_is_closed(client, phones):
    # The one a manager hit in production. A screen opened a solo room and its
    # tab was closed half an hour before anybody scanned anything. The room sat
    # in its lobby, so nothing swept it; it was still in the open list, so every
    # phone in the building was offered it as "Score attack, nobody in it yet".
    # The manager who took it kicked off into a room with no screen behind it.
    code, _ = open_lobby(client, phones)
    heard_now(client, code)

    assert sweep(client, LATE) == [code]
    assert rooms.by_code(client.app.state.conn, code)["status"] == "abandoned"


def test_a_room_nobody_can_run_is_off_the_list_phones_choose_from(client, phones):
    # The symptom the sweep above exists to remove. Asserted through the
    # endpoint the phone actually reads, because a room being "abandoned" in the
    # database is worth nothing if it is still being advertised.
    code, _ = open_lobby(client, phones)
    heard_now(client, code)
    assert [room["code"] for room in client.get("/api/rooms/open").json()["rooms"]] == [code]

    sweep(client, LATE)

    assert client.get("/api/rooms/open").json()["rooms"] == []


def test_a_screen_holding_a_lobby_says_so_and_keeps_it(client, phones):
    # The other half. A screen waiting in front of a queue looks exactly like a
    # screen that was closed, unless it says otherwise, and the pitch that would
    # normally speak for it is not loaded until somebody takes a seat.
    code, host_token = open_lobby(client, phones)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as screen:
        screen.receive_json()
        screen.send_json({"type": "host.here"})
        heard = once_heard(client, code)

    assert sweep(client, heard + arena.HOST_GONE_SECONDS) == []
    assert rooms.by_code(client.app.state.conn, code)["status"] == "lobby"
    assert sweep(client, heard + arena.HOST_GONE_SECONDS + 1) == [code]


def test_saying_you_are_there_is_not_something_a_viewer_can_say(client, phones):
    # Every other screen in the venue has this room's socket open to watch it.
    # If any of them could hold it open, the sweep would never fire in a full
    # hall, which is the one place it matters.
    code, _ = open_lobby(client, phones)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=impostor") as liar:
        liar.receive_json()
        liar.send_json({"type": "host.here"})

    assert rooms.by_code(client.app.state.conn, code)["last_heard_at"] is None


def test_a_manager_already_sitting_in_it_is_told_why_it_closed(client, phones):
    # Somebody can take the seat in the seconds between the screen going and the
    # sweep noticing. They are in a lobby that is about to stop existing, and
    # "Ready when you are" forever is the worst way to find that out.
    code, _ = open_lobby(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    heard_now(client, code)

    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        sweep(client, LATE)
        said = phone.receive_json()
        ending = phone.receive_json()

    assert said["kind"] == "abandoned"
    assert said["payload"]["reason"] == arena.LOBBY_GONE_REASON
    # Nothing kicked off, so nothing was abandoned in the sense the live wording
    # means, and a manager who never saw a whistle must not be told one stopped.
    assert "match" not in said["payload"]["reason"]
    assert ending["type"] == "room"
    assert ending["status"] == "abandoned"


def test_a_room_that_shut_before_it_started_is_not_a_match_that_ended(client, phones):
    # The status cannot carry this on its own. A lobby whose screen went and a
    # match whose screen went are both "abandoned", and a dugout that cannot
    # tell them apart draws a 0-0 scoreline and an empty pitch over a match
    # that never happened, for somebody who watched it not happen.
    code, _ = open_lobby(client, phones)
    assert client.get(f"/api/rooms/{code}").json()["started"] is False
    heard_now(client, code)
    sweep(client, LATE)

    assert client.get(f"/api/rooms/{code}").json()["started"] is False


def test_a_match_that_kicked_off_says_so_for_as_long_as_it_exists(client, live_room):
    code, _ = live_room()
    assert client.get(f"/api/rooms/{code}").json()["started"] is True
    heard_now(client, code)
    sweep(client, LATE)

    assert client.get(f"/api/rooms/{code}").json()["started"] is True


def test_a_match_from_before_the_column_existed_still_counts_as_played(client, live_room):
    # The column is NULL on every room in a database made before the migration
    # added it, and reading those as never-played would tell an evening's worth
    # of finished matches that nothing had happened in them.
    code, _ = live_room()
    connection = client.app.state.conn
    connection.execute("UPDATE room SET started_at = NULL WHERE code = %s", (code,))
    connection.commit()

    assert client.get(f"/api/rooms/{code}").json()["started"] is True


def test_the_dugout_puts_a_scoreline_up_only_for_a_room_that_had_one(client):
    # The client half again. Derived from the status rather than read from the
    # room, this is the page saying a match ended when none was ever played.
    js = client.get("/static/play.js").text
    assert "const started = snapshot.started;" in js
    assert "const over = started && ended;" in js
    assert "This room closed" in js


def test_a_manager_left_in_a_closed_room_is_given_somewhere_to_go(client):
    # There is nothing else on that page to do: no whistle to blow, no score
    # coming, and nobody left who could change either. The phone holding it
    # already knows who its manager is and what else is open, so a dead end
    # here is somebody standing in a venue hunting for a code on a wall to get
    # back to a list their own hand could have shown them.
    html = client.get("/static/play.html").text
    js = client.get("/static/play.js").text
    way_out = html.split('id="elsewhere"')[1].split(">")[0]
    assert 'href="/home"' in way_out
    # And not before then: a lobby with a match still to come already has a
    # button, and two of them is an invitation to leave.
    assert "hidden" in way_out
    assert 'el("elsewhere").hidden = false;' in js.split("if (shut) {")[1].split("}", 1)[0]
    # The banner above it says what happened and stops there, rather than
    # sending them off to do by hand what the button does with one tap.
    assert "Scan" not in arena.LOBBY_GONE_REASON


def test_the_workshop_is_left_alone_forever(client):
    # It sits in its lobby for the life of the deployment with no screen behind
    # it at all, because it is where the dugout tunes profiles with nobody in a
    # dugout seat. Swept on the same rule it would last half a minute.
    assert sweep(client, NOW) == []
    assert sweep(client, LATE) == []
    assert rooms.by_code(client.app.state.conn, codes.WORKSHOP)["status"] == "lobby"


def test_the_screen_holding_a_room_keeps_saying_it_is_there(client):
    # The half of the rule above that has to run in a browser. Nothing else
    # speaks for a room before kick-off, so a screen that stops sending this
    # loses every room it opens half a minute after opening it.
    js = client.get("/static/arena.js").text
    assert 'send({ type: "host.here" })' in js
    assert "setInterval(stillHere" in js
    # Comfortably inside the grace, or a screen that is there is swept anyway:
    # one dropped report has to still leave time for the next.
    every = int(re.search(r"const STILL_HERE_MS = (\d+)", js).group(1))
    assert every * 2 < arena.HOST_GONE_SECONDS * 1000


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
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "full_time",
                        "payload": {"score": [1, 0]}})
        host.receive_json()

    # Stamped after the whistle rather than before it, so the column says what
    # a finished room's really does: a screen that stopped reporting when the
    # match ended, longer ago than any host still playing would be given. The
    # status is the only thing standing between this room and the sweep.
    heard_now(client, code)

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


# An ending decided by the other arena.
#
# Everything above assumes one process, which is what the deployment asks for
# and not what it gets: replacing a revision leaves two containers up, and both
# of them sweep. The one serving no traffic holds no sockets, and it wins the
# race about half the time -- so the room is closed in the database, correctly,
# and the phones are told nothing at all by a bus with nobody on it.


def ended_elsewhere(client, code, live=True):
    """Close a room the way the other container does: in the table, silently.

    No publish behind the write, because on the container that decided it there
    was nobody to publish to.
    """
    connection = client.app.state.conn
    room = rooms.by_code(connection, code)
    rooms.append_event(connection, room["id"], "abandoned",
                       {"reason": arena.HOST_GONE_REASON})
    if live:
        rooms.finish_match(connection, room["id"], "abandoned")
    else:
        rooms.close_lobby(connection, room["id"])


def reconcile(client, announced):
    """Run the catch-up that answers for this instance's own sockets."""
    state = client.app.state
    return client.portal.call(arena._tell_our_own_rooms_it_is_over,
                              state.conn, state.bus, announced)


def test_a_match_ended_by_the_other_arena_still_reaches_the_phones_here(client, live_room):
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()                        # the opening snapshot
        ended_elsewhere(client, code)
        assert reconcile(client, set()) == [code]
        ending = phone.receive_json()

    assert ending["type"] == "room"
    assert ending["status"] == "abandoned"


def test_a_lobby_closed_by_the_other_arena_reaches_them_too(client, phones):
    # The room the manager in production was sitting in. Nothing had kicked
    # off, so there is no clock to notice has stopped: the page would sit on
    # "Ready when you are" for as long as they were willing to look at it.
    code, _ = open_lobby(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})

    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        ended_elsewhere(client, code, live=False)
        assert reconcile(client, set()) == [code]
        ending = phone.receive_json()

    assert ending["status"] == "abandoned"


def test_it_is_said_once_rather_than_every_five_seconds(client, live_room):
    # The sweep runs for as long as the arena is up, and the room stays over.
    code, _ = live_room()
    announced = set()
    with client.websocket_connect(f"/ws/rooms/{code}"):
        ended_elsewhere(client, code)
        assert reconcile(client, announced) == [code]
        assert reconcile(client, announced) == []
        assert reconcile(client, announced) == []


def test_what_was_said_is_forgotten_once_nobody_is_watching(client, live_room):
    # Otherwise the set is the size of the evening rather than of the venue.
    code, _ = live_room()
    announced = set()
    with client.websocket_connect(f"/ws/rooms/{code}"):
        ended_elsewhere(client, code)
        reconcile(client, announced)
    assert announced == {code}

    reconcile(client, announced)
    assert announced == set()


def test_a_room_still_being_played_is_not_announced_as_over(client, live_room):
    code, _ = live_room()
    announced = set()
    with client.websocket_connect(f"/ws/rooms/{code}"):
        assert reconcile(client, announced) == []
    assert announced == set()


def test_the_sweep_does_not_then_say_it_all_over_again(client, live_room, monkeypatch):
    # The watchdog runs both halves back to back and the sweep publishes for
    # itself, so without the hand-off between them every room it gives up on is
    # announced twice -- the second one landing on top of the reason.
    code, _ = live_room()
    connection = client.app.state.conn
    rooms.heard_from(connection, rooms.by_code(connection, code)["id"],
                     time.time() - arena.HOST_GONE_SECONDS - 1)
    monkeypatch.setattr(arena, "SWEEP_SECONDS", 0.01)
    said = []

    async def run():
        subscription = client.app.state.bus.subscribe(arena.room_topic(code))
        watchdog = asyncio.create_task(arena._watch_for_the_missing(client.app))
        try:
            await asyncio.sleep(0.2)
        finally:
            watchdog.cancel()
        while not subscription.queue.empty():
            said.append(subscription.queue.get_nowait())
        subscription.close()

    client.portal.call(run)

    assert [frame["status"] for frame in said if frame["type"] == "room"] == ["abandoned"]


def test_a_wall_here_is_put_right_about_a_match_that_ended_elsewhere(client, live_room):
    code, _ = live_room()
    state = client.app.state
    with client.websocket_connect("/ws/wall") as wall:
        standing = wall.receive_json()["rooms"]
        assert [room["code"] for room in standing] == [code]
        ended_elsewhere(client, code)
        client.portal.call(arena._tell_our_own_wall_who_is_playing,
                           state.conn, state.bus, standing)
        roster = wall.receive_json()

    assert roster["type"] == "wall"
    assert [room["code"] for room in roster["rooms"]] == []


def test_a_wall_that_is_already_right_is_left_alone(client, live_room):
    # Every five seconds, all evening, to every screen in the building. The
    # list it stands behind coming back unchanged is the arena saying it sent
    # nothing, because the two are the same decision.
    code, _ = live_room()
    state = client.app.state
    with client.websocket_connect("/ws/wall") as wall:
        standing = wall.receive_json()["rooms"]
        again = client.portal.call(arena._tell_our_own_wall_who_is_playing,
                                   state.conn, state.bus, standing)
    assert again is standing


def test_with_no_wall_up_the_list_is_not_even_read(client, live_room, monkeypatch):
    # Two joins and a group-by, every five seconds, for the life of a
    # deployment, against a venue that has gone home.
    live_room()
    reads = []
    monkeypatch.setattr(rooms, "live", reads.append)
    state = client.app.state

    assert client.portal.call(arena._tell_our_own_wall_who_is_playing,
                              state.conn, state.bus, None) is None
    assert reads == []


def test_the_dugout_reads_the_log_when_it_hears_a_room_is_over(client):
    # The client half. The snapshot carries the status and not the reason, so a
    # dugout that only drew the snapshot would say "Abandoned" over a blank
    # relay and never say what happened.
    js = client.get("/static/play.js").text
    assert "if (ended && !read)" in js
    assert "catchUp()" in js.split("if (ended && !read)")[1]
