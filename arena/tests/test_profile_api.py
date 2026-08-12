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
