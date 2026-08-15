"""A manager asking a screen to turn its room, and the screen still deciding.

A screen opens a room before anybody has walked up to it, and only that screen
may change what the room plays. So a venue whose screens all opened score
attack has no head to head in it, however many people want one, and the person
holding the phone has no way to say so.

This is that way: the ask reaches the screen's lobby, and somebody standing at
the screen taps the switch. Nothing here changes a room's mode. That is the
whole point of it being a request.
"""

import pytest

import rooms


@pytest.fixture
def waiting_room(client, phones):
    """A room in its lobby. Returns (code, screen_token)."""

    def _waiting_room(mode="solo"):
        phones.join("Screen Opener", "screen@example.com")
        opened = client.post("/api/rooms", json={"mode": mode}).json()
        return opened["code"], opened["screen_token"]

    return _waiting_room


def ask(client, code, mode):
    return client.post(f"/api/rooms/{code}/mode-request", json={"mode": mode})


def test_a_manager_asks_a_solo_room_to_turn_head_to_head(client, phones, waiting_room):
    code, _ = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    response = ask(client, code, "versus")
    assert response.status_code == 200
    assert response.json() == {"mode": "versus", "by": "Alex Rivera"}


def test_the_ask_reaches_the_screen_naming_who_wants_it(client, phones, waiting_room):
    code, _ = waiting_room("solo")
    alex = phones.join("Alex Rivera", "alex@example.com")
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        assert socket.receive_json()["type"] == "room"
        phones.use(alex)
        ask(client, code, "versus")
        heard = socket.receive_json()
    assert heard == {"type": "mode.request", "mode": "versus", "by": "Alex Rivera"}


def test_asking_does_not_turn_the_room(client, phones, waiting_room):
    """The screen decides. An ask that moved the room would not be an ask."""
    code, _ = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    ask(client, code, "versus")
    assert client.get(f"/api/rooms/{code}").json()["mode"] == "solo"


def test_the_ask_is_not_written_into_the_room_s_log(client, phones, waiting_room):
    """Scoring is recomputed from that log, and this is lobby chatter."""
    code, _ = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    ask(client, code, "versus")
    log = client.get(f"/api/rooms/{code}/events").json()["events"]
    assert "mode.request" not in [entry["kind"] for entry in log]


def test_a_phone_with_no_session_has_nobody_to_ask_as(client, waiting_room):
    code, _ = waiting_room("solo")
    client.cookies.clear()
    assert ask(client, code, "versus").status_code == 401


def test_a_match_that_has_kicked_off_is_not_turning(client, phones, live_room):
    code, _ = live_room("solo")
    phones.join("Sam Okafor", "sam@example.com")
    response = ask(client, code, "versus")
    assert response.status_code == 409
    assert "already started" in response.text


def test_asking_for_the_mode_it_already_plays_says_so(client, phones, waiting_room):
    code, _ = waiting_room("versus")
    phones.join("Alex Rivera", "alex@example.com")
    response = ask(client, code, "versus")
    assert response.status_code == 409
    assert "already" in response.text


def test_asking_for_solo_is_refused_while_the_red_dugout_is_taken(client, phones,
                                                                  waiting_room):
    """Refused by the rule that would refuse the screen, not by a copy of it."""
    code, _ = waiting_room("versus")
    phones.join("Sam Okafor", "sam@example.com")
    client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "counter"})
    response = ask(client, code, "solo")
    assert response.status_code == 409
    assert "red dugout" in response.text


def test_a_mode_that_is_not_a_mode_is_refused(client, phones, waiting_room):
    code, _ = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    assert ask(client, code, "penalties").status_code == 422


def test_there_is_no_room_to_ask(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    assert ask(client, "ZZZZ", "versus").status_code == 404
    assert ask(client, "!!!!", "versus").status_code == 404


def test_one_ask_at_a_time_so_a_bored_phone_cannot_strobe_the_wall(client, phones,
                                                                   waiting_room):
    code, _ = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    assert ask(client, code, "versus").status_code == 200
    assert ask(client, code, "versus").status_code == 429


def test_the_manager_next_to_them_is_not_silenced_by_that(client, phones, waiting_room):
    """The budget is one manager's, not the room's."""
    code, _ = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    assert ask(client, code, "versus").status_code == 200
    phones.join("Sam Okafor", "sam@example.com")
    assert ask(client, code, "versus").status_code == 200


def test_asking_in_one_room_does_not_spend_the_ask_in_another(client, phones,
                                                              waiting_room):
    mine, _ = waiting_room("solo")
    theirs, _ = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    assert ask(client, mine, "versus").status_code == 200
    assert ask(client, theirs, "versus").status_code == 200


def test_the_screen_can_still_turn_the_room_it_was_asked_about(client, phones,
                                                               waiting_room):
    """End to end: the ask goes out, the screen taps, the room turns."""
    code, token = waiting_room("solo")
    phones.join("Alex Rivera", "alex@example.com")
    ask(client, code, "versus")
    turned = client.post(f"/api/rooms/{code}/mode",
                         json={"mode": "versus", "screen_token": token})
    assert turned.status_code == 200
    assert client.get(f"/api/rooms/{code}").json()["mode"] == "versus"
    assert client.get(f"/api/rooms/{code}").json()["open_seats"] == ["blue", "red"]


def test_the_rules_are_the_screen_s_own(conn, client, phones, waiting_room):
    """Whatever `set_mode` refuses, the ask refuses, in the same words."""
    code, _ = waiting_room("versus")
    phones.join("Sam Okafor", "sam@example.com")
    client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "counter"})
    room = rooms.by_code(conn, code)
    with pytest.raises(rooms.RoomError) as refused:
        rooms.set_mode(conn, room["id"], "solo")
    assert str(refused.value) in ask(client, code, "solo").json()["detail"]
