import pytest
from fastapi import WebSocketDisconnect

import rooms


def test_a_socket_for_a_room_that_does_not_exist_is_closed(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/rooms/ZZZZ"):
            pass
    assert closed.value.code == 4404


def test_connecting_hands_over_the_room_as_it_stands(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})

    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        opening = socket.receive_json()
    assert opening["type"] == "room"
    assert opening["seats"]["blue"]["name"] == "Alex Rivera"
    assert opening["status"] == "lobby"


def test_a_seat_being_taken_reaches_everyone_watching(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    with client.websocket_connect(f"/ws/rooms/{code}") as screen:
        screen.receive_json()                       # the opening snapshot
        phones.use(alex)
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
        update = screen.receive_json()
    assert update["type"] == "room"
    assert update["open_seats"] == ["red"]


def test_the_host_state_reaches_a_viewer(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"clock": 12, "score": [1, 0]}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12, "score": [1, 0]}


def test_a_client_that_is_not_the_host_cannot_move_the_ball(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=impostor") as liar:
            liar.receive_json()
            liar.send_json({"type": "host.state", "payload": {"clock": 99}})
            # Rather than wait on a timeout, send a frame that IS allowed and
            # prove it is the first thing the viewer sees.
            with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
                host.receive_json()
                host.send_json({"type": "host.state", "payload": {"clock": 12}})
                frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12}


def test_a_socket_with_no_client_id_can_only_watch(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}") as silent:
            silent.receive_json()
            silent.send_json({"type": "host.state", "payload": {"clock": 99}})
            with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
                host.receive_json()
                host.send_json({"type": "host.state", "payload": {"clock": 12}})
                frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12}


def test_a_host_event_comes_back_numbered(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "goal", "match_ms": 27400,
                        "payload": {"team": "blue", "scorer": "forward"}})
        event = host.receive_json()
    assert event == {"type": "event", "seq": 1, "kind": "goal", "match_ms": 27400,
                     "payload": {"team": "blue", "scorer": "forward"}}


def test_host_events_are_written_to_the_log_in_order(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        for kind, match_ms in (("kickoff", 0), ("goal", 27400), ("full_time", 180000)):
            host.send_json({"type": "host.event", "kind": kind,
                            "match_ms": match_ms, "payload": {}})
            host.receive_json()

    connection = client.app.state.conn
    log = rooms.events(connection, rooms.by_code(connection, code)["id"])
    assert [(entry["seq"], entry["kind"]) for entry in log] == [
        (1, "kickoff"), (2, "goal"), (3, "full_time")]


def test_a_state_frame_is_not_written_to_the_log(client, live_room):
    # Positions at 10 Hz would swamp the log, and scoring never reads them.
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        host.send_json({"type": "host.state", "payload": {"clock": 12}})
        host.receive_json()

    connection = client.app.state.conn
    assert rooms.events(connection, rooms.by_code(connection, code)["id"]) == []


def test_a_message_the_protocol_does_not_know_is_ignored(client, live_room):
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()
        host.send_json({"type": "please.let.us.win", "payload": {}})
        host.send_json({"type": "host.state", "payload": {"clock": 3}})
        assert host.receive_json() == {"type": "state", "clock": 3}


def test_a_socket_can_watch_a_room_that_has_not_kicked_off(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]
    with client.websocket_connect(f"/ws/rooms/{code}") as screen:
        assert screen.receive_json()["status"] == "lobby"


def test_a_bare_list_sent_up_is_ignored(client, live_room):
    # A client running ahead of the server must not get hung up on.
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json([1, 2, 3])
            host.send_json({"type": "host.state", "payload": {"clock": 7}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 7}


def test_a_host_state_with_a_list_payload_is_ignored(client, live_room):
    # Graceful degradation: truthy non-dict payloads don't kill the socket.
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": [1, 2, 3]})
            host.send_json({"type": "host.state", "payload": {"clock": 9}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 9}


def test_a_host_cannot_forge_the_frame_type(client, live_room):
    # Server keys must win over anything in the payload.
    code = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"type": "room", "clock": 5}})
            frame = viewer.receive_json()
    assert frame["type"] == "state"
    assert frame["clock"] == 5


def test_a_host_cannot_relay_into_another_rooms_wall_tile(client, live_room):
    # The wall is shared across every tenant; room boundaries must hold.
    code = live_room()
    bus = client.app.state.bus
    subscription = bus.subscribe("wall")

    try:
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"code": "ZZZZ", "clock": 1}})
            host.receive_json()

        # The wall frame is in the queue synchronously after publish.
        frame = subscription.queue.get_nowait()
        assert frame["type"] == "wall.state"
        assert frame["code"] == code
        assert frame["code"] != "ZZZZ"
    finally:
        subscription.close()
