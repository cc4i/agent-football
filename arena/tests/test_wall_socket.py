import asyncio
import json

import pytest
import websockets
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedError

import app
from bus import WALL


def test_the_wall_opens_with_every_live_room(client, live_room):
    code, _ = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        opening = wall.receive_json()
    assert opening == {"type": "wall", "rooms": [
        {"code": code, "mode": "solo", "blue": "Alex Rivera", "red": None}]}


def test_an_empty_venue_opens_with_no_rooms(client):
    with client.websocket_connect("/ws/wall") as wall:
        assert wall.receive_json() == {"type": "wall", "rooms": []}


def test_a_match_kicking_off_appears_on_the_wall(client, live_room):
    with client.websocket_connect("/ws/wall") as wall:
        assert wall.receive_json() == {"type": "wall", "rooms": []}
        code, _ = live_room()
        update = wall.receive_json()
    assert update["type"] == "wall"
    assert [entry["code"] for entry in update["rooms"]] == [code]


def test_host_frames_reach_the_wall_tagged_with_their_room(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"score": [1, 0], "clock": 153}})
            frame = wall.receive_json()
    assert frame == {"type": "wall.state", "code": code, "score": [1, 0], "clock": 153}


def test_the_wall_does_not_carry_events_only_frames(client, live_room):
    # A goal reaches the room socket; the wall gets it through the next frame's
    # score. Keeping events off the wall is what keeps it one connection.
    code, host_token = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "goal",
                            "match_ms": 27400, "payload": {"team": "blue"}})
            host.send_json({"type": "host.state", "payload": {"score": [1, 0]}})
            frame = wall.receive_json()
    assert frame == {"type": "wall.state", "code": code, "score": [1, 0]}


def test_two_live_rooms_both_reach_one_wall_connection(client, phones):
    def start(name, email):
        phones.join(name, email)
        opened = client.post("/api/rooms", json={"mode": "solo"}).json()
        code = opened["code"]
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
        client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        client.post(f"/api/rooms/{code}/start")
        return code, opened["host_token"]

    first_code, _ = start("Alex Rivera", "alex@example.com")
    second_code, second_token = start("Priya Nair", "priya@example.com")

    with client.websocket_connect("/ws/wall") as wall:
        assert {entry["code"] for entry in wall.receive_json()["rooms"]} == {first_code, second_code}
        with client.websocket_connect(f"/ws/rooms/{second_code}?client_id={second_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"score": [0, 2]}})
            frame = wall.receive_json()
    assert frame["code"] == second_code


def test_closing_the_wall_removes_its_subscription_from_the_bus(client):
    assert client.app.state.bus.subscriber_count(WALL) == 0
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        assert client.app.state.bus.subscriber_count(WALL) == 1
    assert client.app.state.bus.subscriber_count(WALL) == 0


def test_the_wall_thins_out_a_busy_room(monkeypatch):
    """Fifty rooms at ten frames a second is five hundred messages a second to
    draw thumbnails with. Two a second per room is more than a wall can show."""
    monkeypatch.setattr(app, "WALL_HZ", 2.0)
    keep = app._wall_thinner()
    now = 1000.0
    assert keep("ABCD", now) is True
    assert keep("ABCD", now + 0.1) is False
    assert keep("ABCD", now + 0.4) is False
    assert keep("ABCD", now + 0.6) is True


def test_one_busy_room_does_not_starve_a_quiet_one(monkeypatch):
    monkeypatch.setattr(app, "WALL_HZ", 2.0)
    keep = app._wall_thinner()
    assert keep("ABCD", 1000.0) is True
    assert keep("EFGH", 1000.0) is True


@pytest.mark.parametrize("off", [0.0, -1.0])
def test_a_rate_of_zero_means_no_thinning_rather_than_no_wall(monkeypatch, off):
    # An operator writing zero means "off". Unguarded, `1.0 / WALL_HZ` makes it
    # mean "kill the pump on the first frame and leave the screen reconnecting".
    monkeypatch.setattr(app, "WALL_HZ", off)
    keep = app._wall_thinner()
    assert keep("ABCD", 1000.0) is True
    assert keep("ABCD", 1000.0) is True
    assert keep("ABCD", 1000.001) is True


def test_the_wall_only_thins_frames_and_never_a_whistle(client, live_room, monkeypatch):
    # Through a socket rather than through the thinner, because a thinner
    # nothing calls thins nothing. Ten seconds between a room's frames, so a
    # frame that should have been dropped cannot become a kept one on a slow
    # machine.
    monkeypatch.setattr(app, "WALL_HZ", 0.1)
    with client.websocket_connect("/ws/wall") as wall:
        assert wall.receive_json() == {"type": "wall", "rooms": []}
        code, host_token = live_room()
        assert wall.receive_json()["type"] == "wall"
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"score": [1, 0]}})
            assert wall.receive_json() == {"type": "wall.state", "code": code, "score": [1, 0]}
            # Dropped: a tile a fraction of a second old is a tile that is right.
            host.send_json({"type": "host.state", "payload": {"score": [2, 0]}})
            # Not dropped, at any rate: a tile for a match that has finished is
            # a tile that is wrong, and stays wrong.
            host.send_json({"type": "host.event", "kind": "full_time",
                            "match_ms": 180000, "payload": {}})
            after = wall.receive_json()
    assert after == {"type": "wall", "rooms": []}


def test_the_wall_turns_away_more_screens_than_it_expects(client, live_room, monkeypatch):
    monkeypatch.setattr(app, "MAX_WALL_SOCKETS", 1)
    with client.websocket_connect("/ws/wall") as first:
        assert first.receive_json() == {"type": "wall", "rooms": []}
        with pytest.raises(WebSocketDisconnect) as refused:
            # The refusal is accepted and then closed, so it is the read that
            # raises rather than the connect. See the wire test below for why
            # the accept has to come first and why this test cannot see it.
            with client.websocket_connect("/ws/wall") as second:
                second.receive_json()
        assert refused.value.code == 4429
        assert refused.value.reason == "too many screens are watching the wall"
        # The screen already watching keeps watching. A cap that took the wall
        # away from the venue to save it would be the wrong cap.
        code, _ = live_room()
        assert [entry["code"] for entry in first.receive_json()["rooms"]] == [code]


def test_a_refused_screen_is_told_why_over_a_real_socket(real_arena_server, monkeypatch):
    """The 4429 and its sentence, on a handshake somebody actually performed.

    `TestClient` cannot see this. It short-circuits the handshake and hands the
    caller a `WebSocketDisconnect` carrying the code and the reason whichever
    order the handler closes in, so the test above passes against a `close`
    that happens before `accept` - and that shape reaches a browser as HTTP 403
    with the code and the sentence discarded, because an upgrade that is never
    accepted is answered with a status and a status has nowhere to put them.
    Anyone simplifying this back onto `TestClient` deletes the only thing that
    knows the difference.
    """
    monkeypatch.setattr(app, "MAX_WALL_SOCKETS", 1)
    wall = real_arena_server.replace("http://", "ws://") + "/ws/wall"

    async def a_second_screen_arrives():
        async with websockets.connect(wall) as first:
            assert json.loads(await first.recv()) == {"type": "wall", "rooms": []}
            # Both the connect and the read are in here: whether the close
            # frame lands during the handshake or just after it is a scheduling
            # detail, and neither one is what this test is about.
            with pytest.raises(ConnectionClosedError) as refused:
                async with websockets.connect(wall) as second:
                    await second.recv()
            return refused.value

    # `rcvd` is the close frame as it came off the wire, which is the whole
    # point here; the flattened `.code` and `.reason` are deprecated.
    closed = asyncio.run(a_second_screen_arrives())
    assert closed.rcvd.code == 4429
    assert closed.rcvd.reason == "too many screens are watching the wall"


def test_a_screen_that_hangs_up_gives_its_place_back(client):
    # The counter is decremented in a finally. If it ever were not, a venue
    # would run out of wall slots over an evening and nobody would know why.
    for _ in range(3):
        with client.websocket_connect("/ws/wall") as wall:
            wall.receive_json()
    assert client.app.state.walls == 0


def test_a_screen_whose_opening_send_fails_gives_its_place_back(client, monkeypatch):
    # The path the comments in wall_socket name: a tab that closed between the
    # read and the send. Its slot was taken before the handshake, so it has to
    # come back here too, or a venue loses one wall slot per closed tab until
    # no screen can open the wall at all.
    monkeypatch.setattr(app.rooms, "live", lambda connection: [object()])
    with pytest.raises(TypeError):
        with client.websocket_connect("/ws/wall"):
            pass
    assert client.app.state.walls == 0
