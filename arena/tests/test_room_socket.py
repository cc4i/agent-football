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
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"clock": 12, "score": [1, 0]}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12, "score": [1, 0]}


def test_a_client_that_is_not_the_host_cannot_move_the_ball(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=impostor") as liar:
            liar.receive_json()
            liar.send_json({"type": "host.state", "payload": {"clock": 99}})
            # Rather than wait on a timeout, send a frame that IS allowed and
            # prove it is the first thing the viewer sees.
            with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
                host.receive_json()
                host.send_json({"type": "host.state", "payload": {"clock": 12}})
                frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12}


def test_a_guessed_client_id_cannot_drive_the_match(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    code, host_token = opened["code"], opened["host_token"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")

    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        # Try every predictable value an attacker might guess.
        for guess in ("phone-7", "phone-8", "fake-host", "screen-1", "host", ""):
            with client.websocket_connect(f"/ws/rooms/{code}?client_id={guess}") as attacker:
                attacker.receive_json()
                attacker.send_json({"type": "host.state", "payload": {"clock": 999}})
        # Now the real host sends a distinguishing frame.
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as real_host:
            real_host.receive_json()
            real_host.send_json({"type": "host.state", "payload": {"clock": 1}})
            # The viewer must see the real host's frame, proving the impostors were ignored.
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 1}


def test_a_socket_with_no_client_id_can_only_watch(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}") as silent:
            silent.receive_json()
            silent.send_json({"type": "host.state", "payload": {"clock": 99}})
            with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
                host.receive_json()
                host.send_json({"type": "host.state", "payload": {"clock": 12}})
                frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12}


def _log(client, code):
    connection = client.app.state.conn
    return rooms.events(connection, rooms.by_code(connection, code)["id"])


def test_a_host_event_comes_back_numbered(client, live_room):
    code, host_token = live_room()
    # Kick-off logs the dugout's opening stance, so the first host event of the
    # match is not seq 1. What matters is that it continues the room's sequence.
    already = len(_log(client, code))
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "goal", "match_ms": 27400,
                        "payload": {"team": "blue", "scorer": "forward"}})
        event = host.receive_json()
    assert event == {"type": "event", "seq": already + 1, "kind": "goal",
                     "match_ms": 27400,
                     "payload": {"team": "blue", "scorer": "forward"}}


def test_host_events_are_written_to_the_log_in_order(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        for kind, match_ms in (("kickoff", 0), ("goal", 27400), ("full_time", 180000)):
            host.send_json({"type": "host.event", "kind": kind,
                            "match_ms": match_ms, "payload": {}})
            host.receive_json()

    played = [entry for entry in _log(client, code) if entry["kind"] != "profile.patch"]
    assert [entry["kind"] for entry in played] == ["kickoff", "goal", "full_time"]
    seqs = [entry["seq"] for entry in played]
    assert seqs == list(range(seqs[0], seqs[0] + 3)), "the log has a gap"


def test_a_state_frame_is_not_written_to_the_log(client, live_room):
    # Positions at 10 Hz would swamp the log, and scoring never reads them.
    code, host_token = live_room()
    before = _log(client, code)
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.state", "payload": {"clock": 12}})
        host.receive_json()

    assert _log(client, code) == before


def test_a_message_the_protocol_does_not_know_is_ignored(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
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
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json([1, 2, 3])
            host.send_json({"type": "host.state", "payload": {"clock": 7}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 7}


def test_a_host_state_with_a_list_payload_is_ignored(client, live_room):
    # Graceful degradation: truthy non-dict payloads don't kill the socket.
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": [1, 2, 3]})
            host.send_json({"type": "host.state", "payload": {"clock": 9}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 9}


def test_a_host_cannot_forge_the_frame_type(client, live_room):
    # Server keys must win over anything in the payload.
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"type": "room", "clock": 5}})
            frame = viewer.receive_json()
    assert frame["type"] == "state"
    assert frame["clock"] == 5


def test_a_host_cannot_relay_into_another_rooms_wall_tile(client, live_room):
    # The wall is shared across every tenant; room boundaries must hold.
    code, host_token = live_room()
    bus = client.app.state.bus
    subscription = bus.subscribe("wall")

    try:
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
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


def test_non_json_text_does_not_crash_the_socket(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_text("not json at all")
            host.send_json({"type": "host.state", "payload": {"clock": 5}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 5}


def test_a_binary_frame_does_not_crash_the_socket(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_bytes(b"\x00\x01\x02")
            host.send_json({"type": "host.state", "payload": {"clock": 8}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 8}


def test_host_event_with_dict_match_ms_is_ignored(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "goal", "match_ms": {"n": 1}})
            host.send_json({"type": "host.state", "payload": {"clock": 10}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 10}


def test_host_event_with_oversized_kind_is_ignored(client, live_room):
    code, host_token = live_room()
    huge_kind = "x" * 10000
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": huge_kind,
                            "match_ms": 100, "payload": {}})
            host.send_json({"type": "host.state", "payload": {"clock": 11}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 11}


def test_host_event_with_oversized_payload_is_ignored(client, live_room):
    code, host_token = live_room()
    huge_payload = {"data": "x" * 200000}
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "big",
                            "match_ms": 100, "payload": huge_payload})
            host.send_json({"type": "host.state", "payload": {"clock": 12}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 12}


def test_a_lone_surrogate_in_host_state_does_not_kill_the_socket(client, live_room):
    # UTF-16 surrogates cannot be encoded to UTF-8, but json.dumps accepts them.
    # The socket must survive and continue delivering frames.
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"x": "\ud800"}})
            host.send_json({"type": "host.state", "payload": {"clock": 5}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 5}


def test_a_lone_surrogate_in_host_event_does_not_kill_the_socket(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "bad",
                            "match_ms": 100, "payload": {"x": "\ud800"}})
            host.send_json({"type": "host.state", "payload": {"clock": 7}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 7}


def test_a_lone_surrogate_in_an_event_kind_does_not_kill_the_socket(client, live_room):
    # `kind` reaches Postgres as a bind parameter and the viewers as a frame, so it
    # needs the same encodability check the payload gets.
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "\ud800",
                            "match_ms": 100, "payload": {}})
            host.send_json({"type": "host.state", "payload": {"clock": 9}})
            frame = viewer.receive_json()
    assert frame == {"type": "state", "clock": 9}


def test_a_lone_surrogate_in_host_state_does_not_kill_the_wall(client, live_room):
    # The wall is shared across tenants: a crash here takes down every live match.
    code, host_token = live_room()
    bus = client.app.state.bus
    subscription = bus.subscribe("wall")

    try:
        with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
            viewer.receive_json()
            with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
                host.receive_json()
                host.send_json({"type": "host.state", "payload": {"x": "\ud800"}})
                host.send_json({"type": "host.state", "payload": {"clock": 8}})
                # The wall must receive the second frame, proving the pump survived.
                viewer.receive_json()
                frame = subscription.queue.get_nowait()
    finally:
        subscription.close()
    assert frame["type"] == "wall.state"
    assert frame["clock"] == 8


def test_the_final_whistle_closes_the_room(client, live_room):
    # The host is trusted for physics, and when the match ended is physics.
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "full_time",
                        "match_ms": 180000, "payload": {"score": [2, 1]}})
        assert host.receive_json()["kind"] == "full_time"
        closed = host.receive_json()
    assert closed["type"] == "room"
    assert closed["status"] == "finished"
    assert client.get(f"/api/rooms/{code}").json()["status"] == "finished"


def test_a_finished_room_leaves_the_wall(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        assert [entry["code"] for entry in wall.receive_json()["rooms"]] == [code]
        with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "full_time",
                            "match_ms": 180000, "payload": {}})
            emptied = wall.receive_json()
    assert emptied == {"type": "wall", "rooms": []}


def test_nothing_more_is_logged_after_the_final_whistle(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "full_time",
                        "match_ms": 180000, "payload": {}})
        host.receive_json()
        host.receive_json()
        after = len(_log(client, code))
        host.send_json({"type": "host.event", "kind": "goal", "match_ms": 190000,
                        "payload": {"team": "blue"}})
        host.send_json({"type": "host.state", "payload": {"clock": 0}})
    log = _log(client, code)
    assert len(log) == after
    assert log[-1]["kind"] == "full_time", "a goal after the whistle would score"
