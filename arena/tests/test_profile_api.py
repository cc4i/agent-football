"""The profile endpoints: what they return, and who may move them."""

import attributes


def open_room(client, phones, mode="versus"):
    """A room with Alex in the blue dugout. Returns the code."""
    phones.join("Alex Rivera", "alex@example.com")
    return client.post("/api/rooms", json={"mode": mode}).json()["code"]


def test_a_dugout_lists_all_four_roles(client, phones):
    code = open_room(client, phones)
    body = client.get(f"/api/rooms/{code}/teams/blue/profiles").json()
    assert body["team"] == "blue"
    assert set(body["profiles"]) == set(attributes.ROLES)


def test_one_role_comes_back_at_its_baseline(client, phones):
    code = open_room(client, phones)
    body = client.get(f"/api/rooms/{code}/teams/blue/profiles/defender").json()
    assert body == {"team": "blue", "role": "defender",
                    "attributes": attributes.baseline_for("defender")}


def test_a_lower_case_code_finds_the_same_room(client, phones):
    code = open_room(client, phones)
    assert client.get(f"/api/rooms/{code.lower()}/teams/blue/profiles").status_code == 200


def test_there_are_no_profiles_in_a_room_that_does_not_exist(client):
    assert client.get("/api/rooms/ZZZZ/teams/blue/profiles").status_code == 404


def test_a_code_that_could_never_have_been_issued_is_a_404_not_a_500(client):
    assert client.get("/api/rooms/nope!/teams/blue/profiles").status_code == 404


def test_an_unknown_dugout_is_a_404(client, phones):
    code = open_room(client, phones)
    assert client.get(f"/api/rooms/{code}/teams/green/profiles").status_code == 404


def test_an_unknown_role_is_a_404(client, phones):
    code = open_room(client, phones)
    response = client.get(f"/api/rooms/{code}/teams/blue/profiles/striker")
    assert response.status_code == 404


def test_a_role_cannot_walk_out_of_the_room(client, phones):
    code = open_room(client, phones)
    response = client.get(f"/api/rooms/{code}/teams/blue/profiles/..%2F..%2Fpasswd")
    assert response.status_code == 404


def seat_and_start(client, phones, code, team="blue"):
    """Sit the current phone in a dugout and kick off. Returns the host token."""
    client.post(f"/api/rooms/{code}/seats/{team}", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/{team}/ready", json={"ready": True})
    return client.post(f"/api/rooms/{code}/start").json()["host_token"]


def test_the_manager_in_that_dugout_may_move_its_profiles(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2},
                                  "reason": "they keep losing the ball",
                                  "actor": "coach"})
    assert response.status_code == 200
    assert response.json()["changed"] == {"aggression": 0.2}


def test_a_manager_cannot_reach_into_the_other_dugout(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}})
    assert response.status_code == 403
    assert profiles_of(client, code, "red")["defender"] == attributes.baseline_for("defender")


def profiles_of(client, code, team):
    return client.get(f"/api/rooms/{code}/teams/{team}/profiles").json()["profiles"]


def test_a_phone_with_no_session_cannot_move_anything(client, phones):
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2}})
    assert response.status_code == 401


def test_a_service_caller_with_the_shared_secret_may_move_a_profile(client, phones,
                                                                    monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "s3cret")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}, "actor": "midfield-agent"},
                            headers={"X-Arena-Service": "s3cret"})
    assert response.status_code == 200


def test_the_wrong_shared_secret_is_no_better_than_none(client, phones, monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "s3cret")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"X-Arena-Service": "guess"})
    assert response.status_code == 401


def test_an_unset_shared_secret_authenticates_nobody(client, phones, monkeypatch):
    # The dangerous failure is the other way round: an empty configured secret
    # matching an empty offered header and letting the whole internet in.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"X-Arena-Service": ""})
    assert response.status_code == 401


def test_a_refused_change_comes_back_with_every_reason(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"speed": 99, "wingspan": 0.5}})
    assert response.status_code == 422
    assert len(response.json()["detail"]["problems"]) == 2


def test_a_patch_lands_on_the_rooms_log(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    body = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                        json={"changes": {"aggression": 0.2}, "actor": "coach",
                              "reason": "too passive"}).json()
    assert body["seq"] == 1


def test_a_patch_reaches_everyone_watching_the_room(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()  # the room snapshot every socket opens with
        client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                     json={"changes": {"aggression": 0.2}, "actor": "coach",
                           "reason": "too passive"})
        frame = viewer.receive_json()
    assert frame == {"type": "event", "seq": 1, "kind": "profile.patch",
                     "match_ms": None,
                     "payload": {"team": "blue", "role": "defender",
                                 "changed": {"aggression": 0.2},
                                 "reason": "too passive", "actor": "coach"}}


def test_profiles_cannot_be_moved_after_the_final_whistle(client, phones):
    code = open_room(client, phones, mode="solo")
    seat_and_start(client, phones, code)
    client.app.state.conn.execute("UPDATE room SET status = 'finished' WHERE code = ?",
                                  (code,))
    client.app.state.conn.commit()
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2}})
    assert response.status_code == 409


def test_a_flood_of_attributes_is_refused_before_it_is_validated(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {str(n): 0.5 for n in range(200)}})
    assert response.status_code == 422
