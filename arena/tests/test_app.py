def test_health_says_which_service_answered(client):
    assert client.get("/health").json() == {"ok": True, "service": "arena"}


def test_joining_returns_a_masked_email_and_sets_a_session(client):
    response = client.post("/api/players",
                           json={"display_name": "Alex Rivera", "email": "Alex@Example.com"})
    assert response.status_code == 200
    assert response.json()["email"] == "a***x@example.com"
    assert "arena_session" in response.cookies


def test_joining_twice_with_one_address_is_one_player(client):
    first = client.post("/api/players",
                        json={"display_name": "Alex Rivera", "email": "alex@example.com"})
    second = client.post("/api/players",
                         json={"display_name": "Alex R", "email": "alex@example.com"})
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["display_name"] == "Alex R"


def test_an_address_that_is_not_one_is_refused(client):
    for bad in ("alex", "alex@", "@example.com", "alex@example"):
        response = client.post("/api/players", json={"display_name": "Alex", "email": bad})
        assert response.status_code == 422, bad


def test_an_empty_name_is_refused(client):
    assert client.post("/api/players",
                       json={"display_name": "", "email": "a@b.com"}).status_code == 422


def test_opening_a_room_returns_a_code_and_two_empty_dugouts(client):
    body = client.post("/api/rooms", json={"mode": "versus"}).json()
    assert body["status"] == "lobby"
    assert body["seats"] == {}
    assert body["open_seats"] == ["blue", "red"]
    assert len(body["code"]) == 4


def test_a_solo_room_offers_only_the_blue_dugout(client):
    assert client.post("/api/rooms", json={"mode": "solo"}).json()["open_seats"] == ["blue"]


def test_an_unknown_mode_is_refused(client):
    assert client.post("/api/rooms", json={"mode": "battle-royale"}).status_code == 422


def test_reading_a_room_that_does_not_exist_is_a_404(client):
    assert client.get("/api/rooms/ZZZZ").status_code == 404


def test_you_cannot_take_a_dugout_without_joining_first(client):
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    response = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    assert response.status_code == 401


def test_a_session_signed_by_somebody_else_is_refused(client):
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.cookies.clear()
    client.cookies.update({"arena_session": "1.forged"})
    response = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    assert response.status_code == 401


def test_taking_a_dugout_shows_up_in_the_room(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    body = client.post(f"/api/rooms/{code}/seats/blue",
                       json={"philosophy": "high press"}).json()
    assert body["seats"]["blue"]["name"] == "Alex Rivera"
    assert body["seats"]["blue"]["philosophy"] == "high press"
    assert body["open_seats"] == []


def test_an_unknown_philosophy_is_refused(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert client.post(f"/api/rooms/{code}/seats/blue",
                       json={"philosophy": "park the bus"}).status_code == 422


def test_a_taken_dugout_comes_back_as_a_conflict(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    sam = phones.join("Sam Okafor", "sam@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    phones.use(alex)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    phones.use(sam)
    response = client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "low block"})
    assert response.status_code == 409
    assert response.json()["detail"] == "the blue dugout is taken"


def test_two_managers_fill_a_versus_room(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    sam = phones.join("Sam Okafor", "sam@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    phones.use(alex)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    phones.use(sam)
    body = client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "low block"}).json()
    assert body["open_seats"] == []
    assert body["seats"]["red"]["name"] == "Sam Okafor"


def test_you_cannot_mark_somebody_elses_dugout_ready(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    sam = phones.join("Sam Okafor", "sam@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]

    phones.use(alex)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    phones.use(sam)
    response = client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    assert response.status_code == 403


def test_a_solo_match_starts_once_its_manager_is_ready(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})

    body = client.post(f"/api/rooms/{code}/start").json()
    assert body["status"] == "live"
    assert client.get(f"/api/rooms/{code}").json()["status"] == "live"


def test_a_match_will_not_start_before_its_manager_is_ready(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})

    response = client.post(f"/api/rooms/{code}/start")
    assert response.status_code == 409
    assert response.json()["detail"] == "not every dugout is ready"


def test_starting_a_room_that_does_not_exist_is_a_404(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    assert client.post("/api/rooms/ZZZZ/start").status_code == 404


def test_starting_a_match_mints_and_returns_a_host_token(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})

    response = client.post(f"/api/rooms/{code}/start")
    body = response.json()
    assert "host_token" in body
    assert len(body["host_token"]) > 10
    # Each start mints a different token.
    second_code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{second_code}/seats/blue", json={"philosophy": "counter"})
    client.post(f"/api/rooms/{second_code}/seats/blue/ready", json={"ready": True})
    second_response = client.post(f"/api/rooms/{second_code}/start")
    assert second_response.json()["host_token"] != body["host_token"]


def test_the_host_token_does_not_leak_into_the_snapshot_or_broadcast(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})

    # Hold a room socket open so we can see the broadcast when /start is called.
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        opening = socket.receive_json()
        assert "host_token" not in str(opening)

        start_response = client.post(f"/api/rooms/{code}/start")
        host_token = start_response.json()["host_token"]

        # The snapshot from /start must not contain the token.
        assert "host_token" in start_response.json()
        assert "host_client_id" not in start_response.json()

        # The broadcast frame must not leak the token.
        broadcast = socket.receive_json()
        assert "host_token" not in str(broadcast)
        assert host_token not in str(broadcast)
        assert "host_client_id" not in str(broadcast)

    # Reading the room must not leak it.
    read_response = client.get(f"/api/rooms/{code}")
    assert "host_token" not in read_response.json()
    assert "host_client_id" not in read_response.json()
