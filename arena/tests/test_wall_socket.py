def test_the_wall_opens_with_every_live_room(client, live_room):
    code = live_room()
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
        code = live_room()
        update = wall.receive_json()
    assert update["type"] == "wall"
    assert [entry["code"] for entry in update["rooms"]] == [code]


def test_host_frames_reach_the_wall_tagged_with_their_room(client, live_room):
    code = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"score": [1, 0], "clock": 153}})
            frame = wall.receive_json()
    assert frame == {"type": "wall.state", "code": code, "score": [1, 0], "clock": 153}


def test_the_wall_does_not_carry_events_only_frames(client, live_room):
    # A goal reaches the room socket; the wall gets it through the next frame's
    # score. Keeping events off the wall is what keeps it one connection.
    code = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
            host.receive_json()
            host.send_json({"type": "host.event", "kind": "goal",
                            "match_ms": 27400, "payload": {"team": "blue"}})
            host.send_json({"type": "host.state", "payload": {"score": [1, 0]}})
            frame = wall.receive_json()
    assert frame == {"type": "wall.state", "code": code, "score": [1, 0]}


def test_two_live_rooms_both_reach_one_wall_connection(client, phones):
    def start(name, email, host_client_id):
        phones.join(name, email)
        code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
        client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
        client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
        client.post(f"/api/rooms/{code}/start", json={"host_client_id": host_client_id})
        return code

    first = start("Alex Rivera", "alex@example.com", "phone-7")
    second = start("Priya Nair", "priya@example.com", "phone-8")

    with client.websocket_connect("/ws/wall") as wall:
        assert {entry["code"] for entry in wall.receive_json()["rooms"]} == {first, second}
        with client.websocket_connect(f"/ws/rooms/{second}?client_id=phone-8") as host:
            host.receive_json()
            host.send_json({"type": "host.state", "payload": {"score": [0, 2]}})
            frame = wall.receive_json()
    assert frame["code"] == second
