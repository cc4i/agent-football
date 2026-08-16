import rooms


def test_health_says_which_service_answered(client):
    # `swept_ago` rides along with it and varies; what this is about is the
    # name. See `test_health.py` for what the probe is actually answering for.
    answered = client.get("/health").json()
    assert answered["ok"] is True
    assert answered["service"] == "arena"


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


def test_a_nul_in_a_name_is_refused_rather_than_bound(client):
    # psycopg will not bind a NUL into a text column, so one that got as far as
    # upsert_player would be a 500 handed out for unauthenticated input.
    assert client.post("/api/players",
                       json={"display_name": "Al\x00ex",
                             "email": "a@b.com"}).status_code == 422


def test_a_nul_in_an_address_is_refused_too(client):
    # The mask keeps the first and last character of the local part and the
    # whole domain, so a NUL on the right of the @ reaches email_masked intact.
    assert client.post("/api/players",
                       json={"display_name": "Alex Rivera",
                             "email": "alex@exa\x00mple.com"}).status_code == 422


def test_a_nul_in_a_dugout_name_is_a_404_rather_than_a_500(client, phones):
    # `/ready` hands the team straight to a lookup, unlike taking the seat,
    # which checks it against the two that exist before binding anything. An
    # unknown but clean team is a 403 there, so this has to be a refusal too
    # rather than a DataError out of psycopg.
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert client.post(f"/api/rooms/{code}/seats/bl%00ue/ready",
                       json={"ready": True}).status_code == 404


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


def test_a_solo_match_starts_once_its_manager_is_ready(client, phones,
                                                       grounds_connected):
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


def test_kicking_off_does_not_hand_physics_to_whoever_asked(client, phones):
    # The grounds are simulating this match. A manager tapping kick-off on
    # their phone must not come away holding the credential for that.
    phones.join("Alex Rivera", "alex@example.com")
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    code = opened["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})

    connection = client.app.state.conn
    physics = rooms.by_code(connection, code)["host_client_id"]
    body = client.post(f"/api/rooms/{code}/start").json()
    assert physics not in str(body)
    assert rooms.by_code(connection, code)["host_client_id"] == physics


def test_client_id_is_redacted_from_access_logs():
    import logging
    from app import _RedactClientId

    filter = _RedactClientId()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='"%s" %s',
        args=('GET /ws/rooms/AB23?client_id=secret-token-123 HTTP/1.1', '200'),
        exc_info=None
    )
    assert filter.filter(record)
    assert 'client_id=secret-token-123' not in record.args[0]
    assert 'client_id=***' in record.args[0]
    assert '/ws/rooms/AB23' in record.args[0]


def test_the_physics_token_does_not_leak_into_the_snapshot_or_broadcast(client, conn,
                                                                        phones,
                                                                        grounds_connected):
    phones.join("Alex Rivera", "alex@example.com")
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    code = opened["code"]
    physics = rooms.by_code(conn, code)["host_client_id"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})

    # Hold a room socket open so we can see the broadcast when /start is called.
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        opening = socket.receive_json()
        assert physics not in str(opening)
        assert "host_client_id" not in str(opening)

        start_response = client.post(f"/api/rooms/{code}/start")
        assert physics not in str(start_response.json())
        assert "host_client_id" not in start_response.json()

        # The broadcast frame must not leak the token.
        broadcast = socket.receive_json()
        assert physics not in str(broadcast)
        assert "host_client_id" not in str(broadcast)

    # Reading the room must not leak it.
    read_response = client.get(f"/api/rooms/{code}")
    assert physics not in str(read_response.json())
    assert "host_client_id" not in read_response.json()
