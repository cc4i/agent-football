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


def test_a_role_carrying_a_nul_is_a_404_rather_than_a_500(client, phones):
    # Unlike every other unknown role, this one does not survive as far as
    # finding no row: psycopg refuses to bind a NUL at all.
    code = open_room(client, phones)
    response = client.get(f"/api/rooms/{code}/teams/blue/profiles/defen%00der")
    assert response.status_code == 404


def seat_and_start(client, phones, code, team="blue"):
    """Sit the current phone in a dugout and kick off."""
    client.post(f"/api/rooms/{code}/seats/{team}", json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/{team}/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")


def test_the_manager_in_that_dugout_may_move_its_profiles(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2},
                                  "reason": "they keep losing the ball",
                                  "actor": "coach"})
    assert response.status_code == 200
    assert response.json()["changed"] == {"aggression": 0.2}


def test_a_write_the_match_would_ignore_comes_back_as_a_refusal(client, phones):
    # The route a specialist writes through, with the attribute fifty measured
    # shouts out of fifty chose. It used to answer 200 and report a change:
    # the manager watched the needle move and the football did not.
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/midfielder",
                            json={"changes": {"forwardPassProbability": 1.0},
                                  "reason": "get it to the forward", "actor": "coach"})
    assert response.status_code == 422
    problems = response.json()["detail"]["problems"]
    assert "'forwardPassProbability' is not simulated and would change nothing" in problems
    assert any("the midfielder is simulated on: " in line for line in problems)
    # And the squad is untouched, rather than half-written.
    assert profiles_of(client, code, "blue")["midfielder"]["forwardPassProbability"] == \
        attributes.baseline_for("midfielder")["forwardPassProbability"]


def test_a_real_lever_beside_a_dead_one_is_not_quietly_applied(client, phones):
    code = open_room(client, phones)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/forward",
                            json={"changes": {"shotRange": 1.0, "finishing": 1.0}})
    assert response.status_code == 422
    assert profiles_of(client, code, "blue")["forward"]["shotRange"] != 1.0


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


def test_a_phone_whose_cookie_was_edited_gets_an_answer_rather_than_a_crash(client, phones):
    # A session cookie is the easiest thing on a phone to edit, and the mac in
    # it went through the same `compare_digest` that refuses a non-ASCII string
    # rather than saying no. Sent as bytes because that is what a header is.
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"Cookie": "arena_session=1.é".encode("utf-8")})
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


def test_a_non_ascii_guess_is_refused_and_not_fatal(client, phones, monkeypatch):
    # hmac.compare_digest raises on a non-ASCII string rather than saying no,
    # and a header value is whatever the caller sent. Headers arrive latin-1
    # decoded, which is why this one is offered as the bytes it travels as.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "s3cret")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}},
                            headers={"X-Arena-Service": "é".encode("latin-1")})
    assert response.status_code == 401


def test_a_non_ascii_shared_secret_still_lets_the_agents_in(client, phones, monkeypatch):
    # A token the deploy generates is ASCII, but a token somebody types on a
    # laptop is whatever their keyboard makes, and the two sides of the
    # comparison come in by different roads: the environment is decoded as
    # UTF-8 and a header as latin-1. Read either one in the other's language
    # and the agents are refused forever with nothing to say why.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "café")
    code = open_room(client, phones)
    client.cookies.clear()
    response = client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                            json={"changes": {"aggression": 0.2}, "actor": "midfield-agent"},
                            headers={"X-Arena-Service": "café".encode("utf-8")})
    assert response.status_code == 200


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
    client.app.state.conn.execute("UPDATE room SET status = 'finished' WHERE code = %s",
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


def test_the_workshop_room_is_open_before_anybody_joins(client):
    import codes
    body = client.get(f"/api/rooms/{codes.WORKSHOP}").json()
    assert body["code"] == codes.WORKSHOP
    assert body["ranked"] is False


def test_the_workshop_room_has_profiles_to_patch(client):
    import codes
    body = client.get(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles").json()
    assert set(body["profiles"]) == set(attributes.ROLES)


def test_the_workshop_room_is_not_reopened_on_the_next_restart(client, dsn):
    # The arena will be restarted plenty of times during a tournament.
    import codes
    from fastapi.testclient import TestClient

    from app import app as arena_app
    with TestClient(arena_app) as second:
        assert second.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200


def test_a_workshop_patch_is_stored_like_any_other(client, monkeypatch):
    # The pitch used to poll four files on disk, and the workshop's patches
    # were copied into them. It reads this room like any other room now.
    import codes
    monkeypatch.setattr("app.SERVICE_TOKEN", "s3cret")
    client.patch(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles/defender",
                 json={"changes": {"aggression": 0.2}},
                 headers={"X-Arena-Service": "s3cret"})
    stored = client.get(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles/defender")
    assert stored.json()["attributes"]["aggression"] == 0.2
