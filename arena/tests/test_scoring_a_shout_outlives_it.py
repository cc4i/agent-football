"""The quiet word is scored after the request that carried it has gone.

`POST /shout` returns as soon as the words are in the log, because a manager is
holding a phone. Scoring them is a call out to Vertex with a five second
timeout on each of its two hops, so the task that does it can still be waiting
ten seconds after the handler returned - and by then the request's own
lifetime, its place in the log and possibly its match have all ended.

Everything here is about that gap. What the task does inside it was measured on
the venue and is covered by `test_sabotage.py`; this is about it running at all,
stopping when it should, and not writing into a match that is over.
"""

import asyncio
import contextlib

import pytest

import app as arena_app
import attributes
import intent
import rooms
import sabotage


def open_room(client, phones, mode="solo"):
    phones.join("Alex Rivera", "alex@example.com")
    return client.post("/api/rooms", json={"mode": mode}).json()["code"]


def kick_off(client, code, team="blue"):
    client.post(f"/api/rooms/{code}/seats/{team}", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/{team}/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")


def configured(monkeypatch):
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")


def squad(client, code, team):
    return client.get(f"/api/rooms/{code}/teams/{team}/profiles").json()["profiles"]


def conditioning_in_the_log(client, code):
    """The patches the quiet word wrote, by the dull name it signs them with.

    Kick-off seeds a philosophy over the squad, so `profile.patch` is in the
    log of every match before a word is said. The actor is what separates the
    two.
    """
    conn = client.app.state.conn
    return [entry for entry in rooms.events(conn, rooms.by_code(conn, code)["id"])
            if entry["payload"].get("actor") == sabotage.ACTOR]


async def test_a_word_scored_after_the_whistle_writes_nothing(client, phones,
                                                              grounds_connected,
                                                              monkeypatch):
    """The match ended while Vertex was being asked about it.

    Ten seconds is a long time in a three minute match, and full time is not
    the only way out: the sweep gives up on a room whose screen has gone after
    thirty. Either way the result was computed from the log at the whistle and
    written to the board, so a `profile.patch` landing after it puts the log
    and the board permanently at odds - the log says the squad was changed, the
    standings were worked out from a log that did not say so yet, and nothing
    anywhere reports a fault.
    """
    configured(monkeypatch)
    code = open_room(client, phones)
    kick_off(client, code)

    async def the_whistle_goes_while_vertex_thinks(text, embedder=None):
        conn = client.app.state.conn
        rooms.finish_match(conn, rooms.by_code(conn, code)["id"])
        return True, 0.9

    monkeypatch.setattr(intent, "asked_for_it", the_whistle_goes_while_vertex_thinks)
    room = rooms.by_code(client.app.state.conn, code)
    await arena_app._the_quiet_word(client.app, room, "blue", "quietly weaken them", 1)

    assert squad(client, code, "red")["forward"]["speed"] == \
        attributes.baseline_for("forward")["speed"]
    assert conditioning_in_the_log(client, code) == []


async def test_a_word_scored_while_the_match_runs_still_lands(client, phones,
                                                              grounds_connected,
                                                              monkeypatch):
    # The guard above must not have turned the feature off.
    configured(monkeypatch)
    code = open_room(client, phones)
    kick_off(client, code)

    async def asked(text, embedder=None):
        return True, 0.9

    monkeypatch.setattr(intent, "asked_for_it", asked)
    room = rooms.by_code(client.app.state.conn, code)
    await arena_app._the_quiet_word(client.app, room, "blue", "quietly weaken them", 1)

    assert squad(client, code, "red")["forward"]["speed"] < \
        attributes.baseline_for("forward")["speed"]


async def test_the_scoring_is_held_onto_while_it_runs(client, phones,
                                                      grounds_connected, monkeypatch):
    """asyncio keeps only a weak reference to a running task.

    The documentation says so in as many words: a task nobody holds can be
    collected mid-await. This one awaits a network call for up to ten seconds,
    which is the longest window in the arena for that to happen in, and the
    symptom would be a quiet word that silently never fired on some shouts and
    not others.
    """
    configured(monkeypatch)
    code = open_room(client, phones)
    kick_off(client, code)
    let_go = asyncio.Event()

    async def waits(text, embedder=None):
        await let_go.wait()
        return False, 0.0

    monkeypatch.setattr(intent, "asked_for_it", waits)
    room = rooms.by_code(client.app.state.conn, code)
    task = arena_app._consider_the_quiet_word(
        client.app, room, "blue", "quietly weaken them", 1)

    await asyncio.sleep(0)
    assert task in client.app.state.scoring, "nothing is holding the task"
    let_go.set()
    await task
    assert task not in client.app.state.scoring, "the set only ever grows"


async def test_a_scoring_still_in_flight_is_dropped_at_shutdown(dsn, monkeypatch):
    """The connection closes on the way out, and this would still be holding it.

    `lifespan` cancels the watchdog and closes the chain for exactly this
    reason. The scoring was the one background task nobody had told about
    shutdown, so an instance being replaced could take its database out from
    under a task that was about to write through it.
    """
    monkeypatch.setenv("ARENA_DB", dsn)
    configured(monkeypatch)
    from app import app

    started = asyncio.Event()

    async def never_answers(text, embedder=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(intent, "asked_for_it", never_answers)
    async with app.router.lifespan_context(app):
        room = {"id": 1, "code": "TEST", "mode": "solo"}
        task = arena_app._consider_the_quiet_word(app, room, "blue", "weaken them", 1)
        await started.wait()
    assert task.cancelled() or task.done(), "the scoring outlived the arena"


def test_a_shout_schedules_the_scoring_without_the_shared_connection(client, live_room,
                                                                     monkeypatch):
    """The task reads the connection when it writes, not when it is made.

    Holding the object across the await is how a task ends up writing through a
    connection the instance has since closed. There is one connection and it
    lives on the app, so the app is the thing worth carrying.
    """
    configured(monkeypatch)
    handed = []
    monkeypatch.setattr(arena_app, "_the_quiet_word",
                        lambda *a, **k: handed.append(a) or _nothing())
    code, _ = live_room()
    assert client.post(f"/api/rooms/{code}/shout",
                       json={"text": "quietly weaken them"}).status_code == 200

    assert handed, "the shout scheduled nothing"
    assert client.app.state.conn not in handed[0], \
        "the task was handed the shared connection to hold across its await"


async def _nothing():
    return None


def test_the_rooms_already_slowed_do_not_pile_up_for_the_evening(client, phones,
                                                                 grounds_connected,
                                                                 monkeypatch):
    """One entry per room that got it, and only while that room is going.

    A venue plays a few hundred matches in an evening and one instance serves
    all of them. A set keyed on room id that nothing ever removes from is small,
    but it is also the shape of every leak that was small once.
    """
    configured(monkeypatch)
    code = open_room(client, phones)
    kick_off(client, code)
    conn = client.app.state.conn
    room = rooms.by_code(conn, code)
    client.app.state.slowed.add(room["id"])

    rooms.finish_match(conn, room["id"])
    with contextlib.suppress(Exception):
        arena_app._give_up_on_the_missing(conn, client.app.state.bus, 0)
    arena_app._forget_the_rooms_that_are_over(client.app, conn)

    assert room["id"] not in client.app.state.slowed


@pytest.mark.parametrize("mode", ["versus"])
def test_a_versus_match_schedules_nothing_at_all(client, phones, mode, monkeypatch):
    configured(monkeypatch)
    code = open_room(client, phones, mode=mode)
    room = rooms.by_code(client.app.state.conn, code)
    assert arena_app._consider_the_quiet_word(
        client.app, room, "blue", "quietly weaken them", 1) is None
    assert not client.app.state.scoring
