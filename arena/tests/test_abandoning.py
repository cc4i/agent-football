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
import time

import app as arena
import codes
import rooms

# A fixed clock, so a test can say when it is without waiting for it to be.
NOW = 10_000.0
LATE = NOW + arena.HOST_GONE_SECONDS + 1


def sweep(client, when, holding=None, drops=None):
    """Run one sweep, as at `when`, from inside the app's own event loop.

    The bus hands messages to sockets that are waiting on them, and waking one
    of those from the test's thread is not safe, so this goes the same way a
    route does.

    `holding` is the app whose open host sockets vouch for their rooms, for the
    tests that have one connected. Left out, nothing is holding anything, which
    is what every test written before the socket counted assumes.

    `drops` is where the sweep leaves the grounds it needs told, for the two
    tests that care. The watchdog sends them; the sweep only collects, because
    it is synchronous.
    """
    state = client.app.state
    held = holding.state.held if holding is not None else None
    return client.portal.call(arena._give_up_on_the_missing, state.conn, state.bus,
                              when, held, state.grounds, drops)


def heard_now(client, code):
    """Say the host reported at NOW, on the fixed clock the file runs on."""
    state = client.app.state
    rooms.heard_from(state.conn, rooms.by_code(state.conn, code)["id"], NOW)


def open_lobby(client, conn, phones, mode="solo"):
    """A room on a screen, with nobody in it yet. Returns (code, physics token)."""
    phones.join("Alex Rivera", "alex@example.com")
    opened = client.post("/api/rooms", json={"mode": mode}).json()
    return opened["code"], rooms.by_code(conn, opened["code"])["host_client_id"]


def screen_of(conn, code):
    """The token the wall bears on this room's socket, as against the grounds'."""
    return rooms.by_code(conn, code)["screen_client_id"]


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


def test_a_room_waiting_on_a_screen_that_has_gone_is_closed(client, conn, phones):
    # The one a manager hit in production. A screen opened a solo room and its
    # tab was closed half an hour before anybody scanned anything. The room sat
    # in its lobby, so nothing swept it; it was still in the open list, so every
    # phone in the building was offered it as "Score attack, nobody in it yet".
    # The manager who took it kicked off into a room with no screen behind it.
    code, _ = open_lobby(client, conn, phones)
    heard_now(client, code)

    assert sweep(client, LATE) == [code]
    assert rooms.by_code(client.app.state.conn, code)["status"] == "abandoned"


def test_a_room_nobody_can_run_is_off_the_list_phones_choose_from(client, conn, phones):
    # The symptom the sweep above exists to remove. Asserted through the
    # endpoint the phone actually reads, because a room being "abandoned" in the
    # database is worth nothing if it is still being advertised.
    code, _ = open_lobby(client, conn, phones)
    heard_now(client, code)
    assert [room["code"] for room in client.get("/api/rooms/open").json()["rooms"]] == [code]

    sweep(client, LATE)

    assert client.get("/api/rooms/open").json()["rooms"] == []


def test_a_screen_holding_a_lobby_says_so_and_keeps_it(client, conn, phones):
    # The other half. A screen waiting in front of a queue looks exactly like a
    # screen that was closed, unless it says otherwise, and the pitch that would
    # normally speak for it is not loaded until somebody takes a seat.
    code, physics = open_lobby(client, conn, phones)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as screen:
        screen.receive_json()
        screen.send_json({"type": "host.here"})
        heard = once_heard(client, code)

    assert sweep(client, heard + arena.HOST_GONE_SECONDS) == []
    assert rooms.by_code(client.app.state.conn, code)["status"] == "lobby"
    assert sweep(client, heard + arena.HOST_GONE_SECONDS + 1) == [code]


# A screen that is still there and cannot say so.
#
# Liveness used to rest entirely on the screen speaking: `host.here` on a
# ten-second setInterval, and the pitch's frames off requestAnimationFrame. A
# browser suspends both of those for a tab that is not the one in front.
# Measured in Chrome, against this arena: the frames stop on the same tick the
# tab is hidden, and the interval is throttled and then starved, so the last
# `host.here` of a backgrounded screen lands about a minute in and nothing
# follows it. Thirty seconds later the sweep gives up on a match whose screen
# is sitting right there, and both managers are told it stopped reporting.
#
# Somebody with two matches open in two tabs loses whichever one they are not
# looking at, every time, which is both of them by the time they have looked at
# each once.
#
# The socket is the thing a browser does not throttle. A tab that still exists
# still holds its connection open, and answers the server's pings from the
# network stack rather than from the JavaScript that has been put to sleep - so
# the connection is the proof, and a lid that shuts stops answering and is
# swept as it always was.


def test_a_screen_whose_tab_went_to_the_background_keeps_its_match(client, live_room):
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as screen:
        screen.receive_json()
        screen.send_json({"type": "host.here"})
        heard = once_heard(client, code)

        # Not a word since, for well past the grace: this is a tab whose timers
        # Chrome has stopped calling. The socket is open the whole time.
        assert sweep(client, heard + arena.HOST_GONE_SECONDS + 1, client.app) == []
        assert rooms.by_code(client.app.state.conn, code)["status"] == "live"
        # And still holding it minutes later, because a match this screen can
        # still finish is not one to close under it.
        assert sweep(client, heard + 600, client.app) == []
        assert rooms.by_code(client.app.state.conn, code)["status"] == "live"


def test_a_screen_holding_a_silent_lobby_keeps_that_too(client, conn, phones):
    # The same tab, backgrounded before anybody took a seat. Worse than losing
    # a match, because the room is on every phone's list until it goes.
    code, physics = open_lobby(client, conn, phones)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as screen:
        screen.receive_json()

        assert sweep(client, NOW + arena.HOST_GONE_SECONDS + 1, client.app) == []
        assert rooms.by_code(client.app.state.conn, code)["status"] == "lobby"


def test_the_screen_that_hangs_up_loses_its_room_as_it_always_did(client, live_room):
    # The whole point of the sweep. A closed tab, or a lid shut long enough for
    # the ping to go unanswered, drops the socket - and then nothing is holding
    # the room and the grace runs out on it.
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as screen:
        screen.receive_json()
        screen.send_json({"type": "host.here"})
        heard = once_heard(client, code)

    assert sweep(client, heard + arena.HOST_GONE_SECONDS + 1, client.app) == [code]
    assert rooms.by_code(client.app.state.conn, code)["status"] == "abandoned"


# Two kinds of client hold a socket now, and they prove different things.
#
# A screen's socket proves its lobby is real: that screen is the only thing that
# can ever run that lobby, so while it is connected the lobby has a future. It
# proves nothing at all about a live match, because the screen is not the thing
# playing it - the grounds are. A wall left open on a match whose grounds died
# would otherwise stamp that match alive every sweep for the rest of the
# evening, and the one mechanism that exists to notice a match nobody is
# simulating could never fire on the case it was built for.


def test_a_screen_socket_does_not_keep_a_live_match_alive(client, conn, live_room):
    code, _ = live_room()
    heard_now(client, code)

    with client.websocket_connect(
            f"/ws/rooms/{code}?client_id={screen_of(conn, code)}") as wall:
        wall.receive_json()
        # Watching the last frame it received go still is not evidence.
        assert sweep(client, LATE, client.app) == [code]

    assert rooms.by_code(client.app.state.conn, code)["status"] == "abandoned"


def test_a_screen_socket_does_keep_its_lobby_alive(client, conn, phones):
    # The half that still holds. Nobody has kicked off, so there are no grounds
    # to speak for this room and the screen is all it has.
    code, _ = open_lobby(client, conn, phones)
    heard_now(client, code)

    with client.websocket_connect(
            f"/ws/rooms/{code}?client_id={screen_of(conn, code)}") as wall:
        wall.receive_json()
        assert sweep(client, LATE, client.app) == []
        assert rooms.by_code(client.app.state.conn, code)["status"] == "lobby"


def test_the_grounds_socket_keeps_the_match_it_is_running(client, live_room):
    # And the kind that is entitled to vouch for a match does.
    code, physics = live_room()
    heard_now(client, code)

    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as grounds:
        grounds.receive_json()
        assert sweep(client, LATE, client.app) == []
        assert rooms.by_code(client.app.state.conn, code)["status"] == "live"


# Giving up on a match is a decision two processes have to hear about. The
# phones are told over the bus, as they always were. The grounds are told over
# the control socket, because the arena is not the thing that stopped: a wedged
# instance still stepping physics for a room nobody believes in any more would
# hold that slot for the rest of the evening.


def test_the_grounds_is_told_to_drop_a_match_the_arena_gave_up_on(
        client, live_room, grounds_connected):
    code, _ = live_room()
    heard_now(client, code)
    drops = []

    assert sweep(client, LATE, drops=drops) == [code]

    assert drops == [(grounds_connected, code)]
    assert client.app.state.grounds.running() == 0


def test_a_lobby_that_never_kicked_off_has_no_grounds_to_tell(client, conn, phones,
                                                              grounds_connected):
    code, _ = open_lobby(client, conn, phones)
    heard_now(client, code)
    drops = []

    assert sweep(client, LATE, drops=drops) == [code]
    assert drops == []


def test_a_watcher_sitting_on_the_socket_holds_nothing(client, conn, phones):
    # Every screen in the venue has this room's socket open to watch it, and a
    # phone in a dugout has one too. If any of them counted, the sweep would
    # never fire in a full hall, which is the one place it has to.
    code, _ = open_lobby(client, conn, phones)
    heard_now(client, code)
    with client.websocket_connect(f"/ws/rooms/{code}") as watcher:
        watcher.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=impostor") as liar:
            liar.receive_json()

            assert sweep(client, LATE, client.app) == [code]


def test_the_pitch_and_the_screen_are_two_sockets_on_one_room(client, live_room):
    # The arena page opens one and the pitch it frames opens another, both with
    # the same token. Counted rather than remembered as a set, or the pitch
    # reloading at kick-off would release a room the screen is still holding.
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as screen:
        screen.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as pitch:
            pitch.receive_json()
        # The pitch has gone; the screen has not.
        assert sweep(client, LATE, client.app) == []

    # Now both have. The grace runs from the last sweep that vouched for it
    # rather than from the last thing the screen said, which is the point: a
    # screen holding a room is heard from continuously until it hangs up.
    assert sweep(client, LATE, client.app) == []
    assert sweep(client, LATE + arena.HOST_GONE_SECONDS + 1, client.app) == [code]


def test_saying_you_are_there_is_not_something_a_viewer_can_say(client, conn, phones):
    # Every other screen in the venue has this room's socket open to watch it.
    # If any of them could hold it open, the sweep would never fire in a full
    # hall, which is the one place it matters.
    code, _ = open_lobby(client, conn, phones)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=impostor") as liar:
        liar.receive_json()
        liar.send_json({"type": "host.here"})

    assert rooms.by_code(client.app.state.conn, code)["last_heard_at"] is None


def test_a_manager_already_sitting_in_it_is_told_why_it_closed(client, conn, phones):
    # Somebody can take the seat in the seconds between the screen going and the
    # sweep noticing. They are in a lobby that is about to stop existing, and
    # "Ready when you are" forever is the worst way to find that out.
    code, _ = open_lobby(client, conn, phones)
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


def test_a_room_that_shut_before_it_started_is_not_a_match_that_ended(client, conn, phones):
    # The status cannot carry this on its own. A lobby whose screen went and a
    # match whose screen went are both "abandoned", and a dugout that cannot
    # tell them apart draws a 0-0 scoreline and an empty pitch over a match
    # that never happened, for somebody who watched it not happen.
    code, _ = open_lobby(client, conn, phones)
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


def test_the_screen_holding_a_room_says_so_by_staying_connected(client):
    # The half of the rule above that has to run in a browser. Nothing else
    # speaks for a room before kick-off, so a screen that opened its own room's
    # socket anonymously would lose every room it opens half a minute later.
    js = client.get("/static/arena.js").text
    opening = js.split("openRoom(code, {")[1].split("});", 1)[0]
    assert "clientId: screenToken()," in opening

    # And says it once, by connecting. The heartbeat that used to carry this is
    # gone: it was the thing a backgrounded tab could not do, and the arena
    # stopped reading it from a screen when the two tokens were split - a
    # `host.here` bearing the screen token has been ignored ever since.
    assert "host.here" not in js
    assert "STILL_HERE_MS" not in js
    assert "setInterval(stillHere" not in js


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
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
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
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
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
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
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
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
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


def test_a_lobby_closed_by_the_other_arena_reaches_them_too(client, conn, phones):
    # The room the manager in production was sitting in. Nothing had kicked
    # off, so there is no clock to notice has stopped: the page would sit on
    # "Ready when you are" for as long as they were willing to look at it.
    code, _ = open_lobby(client, conn, phones)
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


def test_a_screen_pointed_at_a_dead_room_opens_a_new_one(client):
    # The other half of the sweep, and the one nobody was left a way out of.
    #
    # Everything a phone can play is a lobby some screen opened, and the token
    # that runs it lives in that tab's sessionStorage. Reload the tab, shut the
    # lid, lose the venue's wifi for the thirty seconds the sweep allows, and
    # the room is abandoned and that tab can never host it again. What it did
    # then was carry on drawing it: "Ready when they are" over a QR code for a
    # room that no longer exists, and the way out was hidden, because the way
    # out is shown for a match that finished and this one never started. The
    # screen sat there advertising a dead room for the rest of the evening
    # while every phone in the building read "no screen is waiting for a
    # manager this second" and had nothing to tap.
    #
    # So a screen holding a room that died opens another one. It is the only
    # thing in the venue that can, and hosting is the only reason it is on.
    js = client.get("/static/arena.js").text
    assert 'status === "abandoned"' in js
    both = js.split('status === "abandoned"')
    # Both halves: the room read on the way in, for a tab that comes back to a
    # room that died while it was away, and the snapshot off the socket, for
    # one that is watching when it happens.
    assert len(both) == 3, "a dead room is answered on load and on the socket"
    for half in both[1:]:
        assert "startFresh(" in half.split("\n", 1)[0]


def test_the_new_room_is_the_mode_the_dead_one_was(client):
    # `open` replaces the URL with the room's own code and nothing else, so a
    # head-to-head screen that reloads is reading no mode at all, and the
    # default is solo. Left to that, a venue running head to head all evening
    # would quietly reopen as score attack the first time a room died under it
    # -- and the two managers waiting to play each other would find one seat.
    js = client.get("/static/arena.js").text
    fresh = js.split("function startFresh")[1].split("\n}", 1)[0]
    assert "mode" in fresh, "the mode of the room being replaced is carried over"
    # And the button beside it, which had the same hole: after the first match
    # on a head-to-head screen the URL is a room code, so "New room" read the
    # default too, and two managers who came to play each other found one seat.
    handler = js.split('el("again").addEventListener')[1].split(";", 1)[0]
    assert "ours.mode" in handler
