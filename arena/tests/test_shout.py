"""Tapping a chip on the phone: the shout the squad hears, and what it moves.

Until the agent chain is wired in, a shout is a preset and nothing else -- but
it lands in the log in the shape the chain will use, so scoring never has to
know which of the two produced it.
"""

import presets
import profiles
import rooms


def _log(client, code, kind=None):
    connection = client.app.state.conn
    entries = rooms.events(connection, rooms.by_code(connection, code)["id"])
    return [entry for entry in entries if kind is None or entry["kind"] == kind]


def test_the_phone_can_read_the_chips_it_has_to_draw(client):
    body = client.get("/api/presets").json()
    assert [chip["name"] for chip in body["presets"]] == list(presets.NAMES)
    assert all(chip["label"] and chip["phrase"] for chip in body["presets"])


def test_a_shout_lands_in_the_log_as_the_manager_said_it(client, live_room):
    code, _ = live_room()
    response = client.post(f"/api/rooms/{code}/shout", json={"preset": "press high"})
    assert response.status_code == 200

    said = _log(client, code, "shout.sent")
    assert len(said) == 1
    assert said[0]["payload"]["team"] == "blue"
    assert said[0]["payload"]["preset"] == "press high"
    assert said[0]["payload"]["text"] == presets.describe("press high")["phrase"]
    assert said[0]["payload"]["actor"] == "Alex Rivera"
    assert response.json()["seq"] == said[0]["seq"]


def test_a_shout_moves_the_shouters_own_squad(client, live_room):
    code, _ = live_room()
    client.post(f"/api/rooms/{code}/shout", json={"preset": "sit deep"})

    connection = client.app.state.conn
    room_id = rooms.by_code(connection, code)["id"]
    for role in ("defender", "midfielder", "forward", "goalkeeper"):
        stored = profiles.read_one(connection, room_id, "blue", role)
        for key, value in presets.changes_for("sit deep").items():
            assert stored[key] == value


def test_every_patch_a_shout_causes_names_the_shout_that_caused_it(client, live_room):
    # Scoring pays for a shout that led to a goal, so it has to be able to walk
    # from a goal back to the instruction behind it.
    code, _ = live_room()
    seq = client.post(f"/api/rooms/{code}/shout",
                      json={"preset": "shoot early"}).json()["seq"]

    caused = [entry for entry in _log(client, code, "profile.patch")
              if entry["payload"].get("shout_seq") == seq]
    assert len(caused) == 4
    assert {entry["payload"]["role"] for entry in caused} == {
        "defender", "midfielder", "forward", "goalkeeper"}


def test_the_stances_applied_at_kick_off_are_not_blamed_on_a_shout(client, live_room):
    code, _ = live_room()
    for entry in _log(client, code, "profile.patch"):
        assert "shout_seq" not in entry["payload"]


def test_the_shout_is_logged_before_anything_it_moved(client, live_room):
    # The manager's words have to be on screen before the squad reacts to them,
    # and the log is what a late joiner replays to catch up.
    code, _ = live_room()
    seq = client.post(f"/api/rooms/{code}/shout",
                      json={"preset": "break wide"}).json()["seq"]
    caused = [entry["seq"] for entry in _log(client, code, "profile.patch")
              if entry["payload"].get("shout_seq") == seq]
    assert min(caused) > seq


def test_the_room_hears_the_shout_and_then_the_squad_move(client, live_room):
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        socket.receive_json()                       # the opening room snapshot
        client.post(f"/api/rooms/{code}/shout", json={"preset": "press high"})
        first = socket.receive_json()
        assert first["kind"] == "shout.sent"
        assert first["payload"]["text"] == presets.describe("press high")["phrase"]
        assert [socket.receive_json()["kind"] for _ in range(4)] == ["profile.patch"] * 4


def test_a_preset_nobody_ships_is_refused(client, live_room):
    code, _ = live_room()
    response = client.post(f"/api/rooms/{code}/shout", json={"preset": "park the bus"})
    assert response.status_code == 422
    assert not _log(client, code, "shout.sent")


def test_a_shout_with_no_preset_at_all_is_refused(client, live_room):
    code, _ = live_room()
    assert client.post(f"/api/rooms/{code}/shout", json={}).status_code == 422


def test_only_somebody_in_a_dugout_may_shout(client, live_room, phones):
    code, _ = live_room()
    phones.join("Sam Okafor", "sam@example.com")
    response = client.post(f"/api/rooms/{code}/shout", json={"preset": "press high"})
    assert response.status_code == 403
    assert not _log(client, code, "shout.sent")


def test_a_passer_by_with_no_session_may_not_shout(client, live_room):
    code, _ = live_room()
    client.cookies.clear()
    assert client.post(f"/api/rooms/{code}/shout",
                       json={"preset": "press high"}).status_code == 401


def test_there_is_nobody_to_shout_at_before_the_whistle(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "counter"})
    response = client.post(f"/api/rooms/{code}/shout", json={"preset": "press high"})
    assert response.status_code == 409


def test_shouting_at_a_match_that_is_over_is_refused(client, live_room):
    code, _ = live_room()
    connection = client.app.state.conn
    rooms.finish_match(connection, rooms.by_code(connection, code)["id"])
    assert client.post(f"/api/rooms/{code}/shout",
                       json={"preset": "press high"}).status_code == 409


def test_a_shout_at_a_room_that_does_not_exist_is_a_404(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    assert client.post("/api/rooms/ZZZZ/shout",
                       json={"preset": "press high"}).status_code == 404


def test_each_dugout_shouts_at_its_own_squad(client, phones, grounds_connected):
    alex = phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    phones.join("Sam Okafor", "sam@example.com")
    client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "low block"})
    client.post(f"/api/rooms/{code}/seats/red/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")

    client.post(f"/api/rooms/{code}/shout", json={"preset": "press high"})
    phones.use(alex)
    client.post(f"/api/rooms/{code}/shout", json={"preset": "sit deep"})

    said = {entry["payload"]["team"]: entry["payload"] for entry in _log(client, code, "shout.sent")}
    assert said["red"]["preset"] == "press high"
    assert said["blue"]["preset"] == "sit deep"
