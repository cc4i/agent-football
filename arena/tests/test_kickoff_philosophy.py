"""The stance a manager picked on the join form is applied at kick-off."""

import philosophies
import rooms


def seat(client, code, team, philosophy):
    client.post(f"/api/rooms/{code}/seats/{team}", json={"philosophy": philosophy})
    client.post(f"/api/rooms/{code}/seats/{team}/ready", json={"ready": True})


def test_the_join_form_can_read_the_four_stances(client):
    body = client.get("/api/philosophies").json()
    assert [stance["name"] for stance in body["philosophies"]] == list(philosophies.NAMES)
    assert all(stance["blurb"] for stance in body["philosophies"])


def test_kick_off_applies_the_stance_the_manager_picked(client, phones,
                                                        grounds_connected):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    seat(client, code, "blue", "low block")
    client.post(f"/api/rooms/{code}/start")

    stored = client.get(f"/api/rooms/{code}/teams/blue/profiles/defender").json()
    for key, value in philosophies.changes_for("low block").items():
        assert stored["attributes"][key] == value


def test_each_dugout_gets_its_own_stance(client, phones, grounds_connected):
    alex = phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]
    seat(client, code, "blue", "low block")
    phones.join("Sam Okafor", "sam@example.com")
    seat(client, code, "red", "high press")
    phones.use(alex)
    client.post(f"/api/rooms/{code}/start")

    blue = client.get(f"/api/rooms/{code}/teams/blue/profiles/forward").json()["attributes"]
    red = client.get(f"/api/rooms/{code}/teams/red/profiles/forward").json()["attributes"]
    assert blue["pressingIntensity"] == philosophies.changes_for("low block")["pressingIntensity"]
    assert red["pressingIntensity"] == philosophies.changes_for("high press")["pressingIntensity"]


def test_the_moves_are_in_the_log_so_scoring_can_see_them(client, phones, conn,
                                                          grounds_connected):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    seat(client, code, "blue", "counter")
    client.post(f"/api/rooms/{code}/start")

    room = rooms.by_code(conn, code)
    kicked = [event for event in rooms.events(conn, room["id"])
              if event["kind"] == "profile.patch"]
    assert {event["payload"]["role"] for event in kicked} == {
        "defender", "midfielder", "forward", "goalkeeper"}
    assert all(event["payload"]["actor"] == "kick-off" for event in kicked)
    assert all(event["payload"]["reason"] == "counter" for event in kicked)


def test_the_pitch_hears_the_stance_land_before_it_needs_it(client, phones,
                                                            grounds_connected):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    seat(client, code, "blue", "counter")

    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()  # the room snapshot every socket opens with
        client.post(f"/api/rooms/{code}/start")
        frames = [viewer.receive_json() for _ in range(5)]

    patches = [frame for frame in frames if frame.get("kind") == "profile.patch"]
    assert len(patches) == 4
    # The room frame comes last: a client that renders "live" then asks for
    # profiles must not be able to read them before the stance is in.
    assert frames[-1]["type"] == "room"
    assert frames[-1]["status"] == "live"
