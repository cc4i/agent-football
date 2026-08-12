"""What a client asks for that nothing else answers: my seat, and the log.

Both exist because a phone can arrive late. The room snapshot is the same for
everybody, and a socket that opens mid-match has missed everything said before
it connected.
"""


def test_a_seated_manager_is_told_which_dugout_is_theirs(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "counter"})
    body = client.get(f"/api/rooms/{code}/me").json()
    assert body == {"name": "Alex Rivera", "team": "red"}


def test_somebody_with_no_dugout_is_told_that_plainly(client, phones):
    phones.join("Sam Okafor", "sam@example.com")
    code = client.post("/api/rooms", json={"mode": "versus"}).json()["code"]
    assert client.get(f"/api/rooms/{code}/me").json()["team"] is None


def test_a_dugout_in_another_room_is_not_a_dugout_in_this_one(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    mine = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    theirs = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{mine}/seats/blue", json={"philosophy": "counter"})
    assert client.get(f"/api/rooms/{theirs}/me").json()["team"] is None


def test_a_phone_with_no_session_is_nobody(client):
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.cookies.clear()
    assert client.get(f"/api/rooms/{code}/me").status_code == 401


def test_the_log_replays_in_order(client, live_room):
    code, _ = live_room()
    client.post(f"/api/rooms/{code}/shout", json={"preset": "press high"})
    log = client.get(f"/api/rooms/{code}/events").json()["events"]
    assert [entry["seq"] for entry in log] == list(range(1, len(log) + 1))
    # Four stances at kick-off, then the shout and the four patches it caused.
    assert [entry["kind"] for entry in log[4:]] == \
        ["shout.sent"] + ["profile.patch"] * 4


def test_a_reconnecting_phone_asks_only_for_what_it_missed(client, live_room):
    code, _ = live_room()
    seen = client.get(f"/api/rooms/{code}/events").json()["events"][-1]["seq"]
    client.post(f"/api/rooms/{code}/shout", json={"preset": "sit deep"})
    caught_up = client.get(f"/api/rooms/{code}/events?since={seen}").json()["events"]
    assert [entry["kind"] for entry in caught_up] == \
        ["shout.sent"] + ["profile.patch"] * 4


def test_a_room_nobody_has_played_in_has_an_empty_log(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert client.get(f"/api/rooms/{code}/events").json() == {"events": []}


def test_the_log_of_a_room_that_does_not_exist_is_a_404(client):
    assert client.get("/api/rooms/ZZZZ/events").status_code == 404
