"""The screen turning its own waiting room between the two modes.

A screen opens a room before anybody has walked up to it, so the mode it chose
is a guess. These are the rules on changing that guess: the screen that opened
the room may, nobody else may, and not once the whistle has gone.
"""

import json

import pytest


@pytest.fixture
def waiting_room(client, phones):
    """A room in its lobby. Returns (code, host_token)."""

    def _waiting_room(mode="solo"):
        phones.join("Alex Rivera", "alex@example.com")
        opened = client.post("/api/rooms", json={"mode": mode}).json()
        return opened["code"], opened["host_token"]

    return _waiting_room


def change(client, code, mode, token):
    return client.post(f"/api/rooms/{code}/mode", json={"mode": mode, "host_token": token})


def test_the_screen_turns_its_own_room_head_to_head(client, waiting_room):
    code, token = waiting_room("solo")
    response = change(client, code, "versus", token)
    assert response.status_code == 200
    assert response.json()["mode"] == "versus"
    assert client.get(f"/api/rooms/{code}").json()["mode"] == "versus"


def test_and_back_again(client, waiting_room):
    code, token = waiting_room("versus")
    assert change(client, code, "solo", token).json()["mode"] == "solo"
    assert client.get(f"/api/rooms/{code}").json()["mode"] == "solo"


def test_the_room_keeps_its_code_and_the_address_its_qr_encodes(client, waiting_room):
    code, token = waiting_room("solo")
    before = client.get(f"/api/rooms/{code}").json()
    after = change(client, code, "versus", token).json()
    assert after["code"] == before["code"] == code
    assert after["join_url"] == before["join_url"]


def test_a_phone_cannot_reshape_the_room_it_is_joining(client, waiting_room):
    code, _ = waiting_room("versus")
    response = change(client, code, "solo", "not-the-token")
    assert response.status_code == 403
    assert client.get(f"/api/rooms/{code}").json()["mode"] == "versus"


def test_no_token_at_all_is_no_better(client, waiting_room):
    code, _ = waiting_room("solo")
    assert change(client, code, "versus", "").status_code == 403
    assert client.post(f"/api/rooms/{code}/mode", json={"mode": "versus"}).status_code == 422


def test_one_room_s_token_does_not_open_another_room(client, waiting_room):
    _, mine = waiting_room("solo")
    theirs, _ = waiting_room("solo")
    assert change(client, theirs, "versus", mine).status_code == 403


def test_a_match_that_has_kicked_off_keeps_its_mode(client, live_room):
    code, token = live_room("solo")
    response = change(client, code, "versus", token)
    assert response.status_code == 409
    assert "already started" in response.text
    assert client.get(f"/api/rooms/{code}").json()["mode"] == "solo"


def test_going_solo_is_refused_while_the_red_dugout_is_taken(client, phones, waiting_room):
    code, token = waiting_room("versus")
    phones.join("Sam Okafor", "sam@example.com")
    client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "counter"})
    response = change(client, code, "solo", token)
    assert response.status_code == 409
    assert "red dugout" in response.text
    assert client.get(f"/api/rooms/{code}").json()["mode"] == "versus"


def test_a_mode_that_is_not_a_mode_is_refused_before_the_token_is_read(client, waiting_room):
    code, token = waiting_room("solo")
    assert change(client, code, "penalties", token).status_code == 422


def test_there_is_no_room_to_change(client):
    assert change(client, "ZZZZ", "versus", "whatever").status_code == 404
    # Not even a lookup for a code the generator could never have produced.
    assert change(client, "!!!!", "versus", "whatever").status_code == 404


def test_everybody_watching_the_room_is_told(client, waiting_room):
    """The join form on a phone that already scanned the code follows along."""
    code, token = waiting_room("solo")
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        assert socket.receive_json()["mode"] == "solo"
        change(client, code, "versus", token)
        announced = socket.receive_json()
        assert announced["type"] == "room"
        assert announced["mode"] == "versus"
        assert announced["code"] == code


def test_the_seated_manager_keeps_their_dugout_across_the_change(client, phones,
                                                                 waiting_room):
    code, token = waiting_room("solo")
    alex = phones.join("Alex Rivera", "alex@example.com")
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    change(client, code, "versus", token)
    phones.use(alex)
    assert client.get(f"/api/rooms/{code}/me").json()["team"] == "blue"
    # And the dugout the room did not have a moment ago is now there to take.
    phones.join("Sam Okafor", "sam@example.com")
    assert client.post(f"/api/rooms/{code}/seats/red",
                       json={"philosophy": "counter"}).status_code == 200


def test_the_token_is_never_written_into_the_path_or_the_query(client, waiting_room):
    """It travels in the body, so no access log can keep it."""
    code, token = waiting_room("solo")
    response = change(client, code, "versus", token)
    assert token not in str(response.request.url)
    assert token not in json.dumps(response.json())
