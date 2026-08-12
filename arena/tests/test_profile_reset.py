"""Putting a dugout back to the shipped squad.

The lab needs every session to start from the same players or its stages stop
being repeatable. That used to be a file copy beside the pitch; a room's
profiles live here now, so the reset does too.
"""

import attributes


def seated(client, phones, mode="versus"):
    """A room with Alex in the blue dugout. Returns the code."""
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": mode}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    return code


def profiles_of(client, code, team="blue"):
    return client.get(f"/api/rooms/{code}/teams/{team}/profiles").json()["profiles"]


def test_a_reset_puts_every_role_back_where_it_started(client, phones):
    code = seated(client, phones)
    client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                 json={"changes": {"aggression": 0.2}})
    client.patch(f"/api/rooms/{code}/teams/blue/profiles/forward",
                 json={"changes": {"shotPower": 0.11}})
    client.post(f"/api/rooms/{code}/teams/blue/profiles/reset")
    assert profiles_of(client, code) == {role: attributes.baseline_for(role)
                                         for role in attributes.ROLES}


def test_a_reset_leaves_the_other_dugout_alone(client, phones, monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "s3cret")
    code = seated(client, phones)
    client.patch(f"/api/rooms/{code}/teams/red/profiles/defender",
                 json={"changes": {"aggression": 0.2}},
                 headers={"X-Arena-Service": "s3cret"})
    client.post(f"/api/rooms/{code}/teams/blue/profiles/reset")
    assert profiles_of(client, code, "red")["defender"]["aggression"] == 0.2


def test_a_reset_reaches_the_log_as_the_changes_it_made(client, phones):
    code = seated(client, phones)
    client.patch(f"/api/rooms/{code}/teams/blue/profiles/defender",
                 json={"changes": {"aggression": 0.2}})
    client.post(f"/api/rooms/{code}/teams/blue/profiles/reset")
    log = client.get(f"/api/rooms/{code}/events").json()["events"]
    # One patch out, one patch back. A late arrival replaying this ends up
    # looking at the same squad as everybody else.
    assert [entry["kind"] for entry in log] == ["profile.patch", "profile.patch"]
    assert log[-1]["payload"]["changed"] == {
        "aggression": attributes.baseline_for("defender")["aggression"]}
    assert log[-1]["payload"]["actor"] == "reset"


def test_resetting_a_squad_that_never_moved_logs_nothing(client, phones):
    code = seated(client, phones)
    client.post(f"/api/rooms/{code}/teams/blue/profiles/reset")
    assert client.get(f"/api/rooms/{code}/events").json()["events"] == []


def test_only_that_dugouts_manager_may_reset_it(client, phones):
    code = seated(client, phones)
    assert client.post(f"/api/rooms/{code}/teams/red/profiles/reset").status_code == 403


def test_a_phone_with_no_session_cannot_reset_anything(client, phones):
    code = seated(client, phones)
    client.cookies.clear()
    assert client.post(f"/api/rooms/{code}/teams/blue/profiles/reset").status_code == 401


def test_a_service_caller_may_reset_the_workshop(client, monkeypatch):
    # This is the lab's own path: the coach agent has no phone and no cookie.
    import app as arena_app
    import codes
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "s3cret")
    client.patch(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles/defender",
                 json={"changes": {"aggression": 0.2}},
                 headers={"X-Arena-Service": "s3cret"})
    response = client.post(f"/api/rooms/{codes.WORKSHOP}/teams/blue/profiles/reset",
                           headers={"X-Arena-Service": "s3cret"})
    assert response.status_code == 200
    assert response.json()["profiles"]["defender"] == attributes.baseline_for("defender")


def test_a_finished_match_cannot_be_reset(client, phones):
    code = seated(client, phones, mode="solo")
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")
    client.app.state.conn.execute("UPDATE room SET status = 'finished' WHERE code = ?",
                                  (code,))
    client.app.state.conn.commit()
    assert client.post(f"/api/rooms/{code}/teams/blue/profiles/reset").status_code == 409


def test_there_is_nothing_to_reset_in_a_room_that_does_not_exist(client):
    assert client.post("/api/rooms/ZZZZ/teams/blue/profiles/reset").status_code == 404


def test_an_unknown_dugout_cannot_be_reset(client, phones):
    code = seated(client, phones)
    assert client.post(f"/api/rooms/{code}/teams/green/profiles/reset").status_code == 404
