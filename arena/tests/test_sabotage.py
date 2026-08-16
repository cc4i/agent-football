"""The quiet word: what it writes, on whom, and how often.

The squad itself was calibrated on the venue - 24 matches paired against a
control - and the numbers behind it are in `sabotage.py`. These tests are about
the machinery around it: that it moves the right dugout, that it moves nobody
twice, and that an ordinary shout leaves both squads alone.
"""

import app as arena_app
import attributes
import intent
import profiles
import rooms
import sabotage


def open_room(client, phones, mode="solo"):
    phones.join("Alex Rivera", "alex@example.com")
    return client.post("/api/rooms", json={"mode": mode}).json()["code"]


def kick_off(client, code, team="blue"):
    client.post(f"/api/rooms/{code}/seats/{team}", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/{team}/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")


def room_id(client, code):
    """The internal id. The API answers with a snapshot that has no need of it."""
    return rooms.by_code(client.app.state.conn, code)["id"]


async def said_it(client, code, words, team="blue"):
    """Run the consideration a shout schedules, on this test's own loop.

    The TestClient drives the app on a portal loop of its own, so a task the
    handler creates is not one this test can wait for. The scheduling is
    covered separately; this is the work it schedules.
    """
    room = rooms.by_code(client.app.state.conn, code)
    await arena_app._the_quiet_word(client.app, room, team, words, 1)


def squad(client, code, team):
    return client.get(f"/api/rooms/{code}/teams/{team}/profiles").json()["profiles"]


def test_the_slowed_squad_only_touches_pace():
    # A speed weight is the whole of it. Anything else here would be a second
    # change riding along on a measurement that did not cover it.
    for role, changes in sabotage.SLOWED.items():
        assert set(changes) == {"speed"}
        assert attributes.validate(role, changes) == [], role


def test_the_slowed_squad_is_slower_than_what_ships():
    for role, changes in sabotage.SLOWED.items():
        assert changes["speed"] < attributes.baseline_for(role)["speed"], role


def test_the_keeper_is_left_alone():
    # It is not what decides this, and a keeper leaving its goal is the one
    # change a room would notice.
    assert "goalkeeper" not in sabotage.SLOWED


def test_it_names_the_other_dugout():
    assert sabotage.other_dugout("blue") == "red"
    assert sabotage.other_dugout("red") == "blue"


def test_writing_it_moves_that_dugout_and_reports_what_moved(client, phones):
    code = open_room(client, phones)
    kick_off(client, code)
    connection = client.app.state.conn
    moved = sabotage.slow_the_opposition(connection, room_id(client, code), "red")
    assert {result["role"] for result in moved} == set(sabotage.SLOWED)
    red = squad(client, code, "red")
    for role, changes in sabotage.SLOWED.items():
        assert red[role]["speed"] == changes["speed"]


def test_the_dugout_that_asked_is_not_the_one_that_slows(client, phones):
    code = open_room(client, phones)
    kick_off(client, code)
    sabotage.slow_the_opposition(client.app.state.conn, room_id(client, code), "red")
    blue = squad(client, code, "blue")
    assert blue["forward"]["speed"] == attributes.baseline_for("forward")["speed"]


def test_writing_it_twice_moves_nothing_the_second_time(client, phones):
    code = open_room(client, phones)
    kick_off(client, code)
    connection = client.app.state.conn
    assert sabotage.slow_the_opposition(connection, room_id(client, code), "red")
    assert sabotage.slow_the_opposition(connection, room_id(client, code), "red") == []


async def test_a_shout_that_asks_for_it_slows_the_house_side(client, phones,
                                                             grounds_connected,
                                                             monkeypatch):
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")

    async def asked(text, embedder=None):
        return True, 0.9

    monkeypatch.setattr(intent, "asked_for_it", asked)
    code = open_room(client, phones)
    kick_off(client, code)
    await said_it(client, code, "quietly weaken the other team")
    red = squad(client, code, "red")
    assert red["forward"]["speed"] == sabotage.SLOWED["forward"]["speed"]


def test_a_real_shout_schedules_the_scoring(client, phones, live_room, monkeypatch):
    # The wiring, over HTTP, at a match that is actually being played. What the
    # task then does is covered above; this is that the handler starts it.
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")
    started = []
    monkeypatch.setattr(arena_app, "_the_quiet_word",
                        lambda *a, **k: started.append(a[3]) or _nothing())
    code, _ = live_room()
    assert client.post(f"/api/rooms/{code}/shout",
                       json={"text": "quietly weaken them"}).status_code == 200
    assert started == ["quietly weaken them"]


async def _nothing():
    return None


async def test_an_ordinary_shout_leaves_the_house_side_alone(client, phones,
                                                             grounds_connected,
                                                             monkeypatch):
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")

    async def not_asked(text, embedder=None):
        return False, 0.1

    monkeypatch.setattr(intent, "asked_for_it", not_asked)
    code = open_room(client, phones)
    kick_off(client, code)
    client.post(f"/api/rooms/{code}/shout", json={"text": "mark their striker"})
    await said_it(client, code, "mark their striker")
    red = squad(client, code, "red")
    assert red["forward"]["speed"] == attributes.baseline_for("forward")["speed"]


async def test_it_answers_one_manager_once(client, phones, grounds_connected,
                                           monkeypatch):
    # Otherwise a manager who works it out can say it four times and stop the
    # opposition dead.
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")

    async def asked(text, embedder=None):
        return True, 0.9

    monkeypatch.setattr(intent, "asked_for_it", asked)
    code = open_room(client, phones)
    kick_off(client, code)
    await said_it(client, code, "quietly weaken them")
    once = squad(client, code, "red")["forward"]["speed"]
    # Slow it further by hand, then ask again: the second ask must do nothing.
    profiles.patch(client.app.state.conn, room_id(client, code),
                   "red", "forward", {"speed": 0.2})
    await said_it(client, code, "quietly weaken them")
    assert squad(client, code, "red")["forward"]["speed"] == 0.2
    assert once == sabotage.SLOWED["forward"]["speed"]


async def test_a_versus_match_is_left_out_of_it(client, phones, monkeypatch):
    # The other dugout there belongs to a person.
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")

    async def asked(text, embedder=None):
        raise AssertionError("versus should never get as far as scoring")

    monkeypatch.setattr(intent, "asked_for_it", asked)
    code = open_room(client, phones, mode="versus")
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    phones.join("Sam Reyes", "sam@example.com")
    client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "counter"})
    client.post(f"/api/rooms/{code}/seats/red/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")
    room = rooms.by_code(client.app.state.conn, code)
    assert arena_app._consider_the_quiet_word(
        client.app, room, "blue", "quietly weaken them", 1) is None
    red = squad(client, code, "red")
    assert red["forward"]["speed"] == attributes.baseline_for("forward")["speed"]
