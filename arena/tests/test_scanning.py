"""The sheet on the wall, and where pointing a phone at it lands.

The code on that sheet is printed in the morning and pinned up once. It cannot
name a room, because every screen opens its own after that, and it cannot name
a person, because it is the same sheet for everybody in the building. So it
says one address and that address is a door: whoever the phone turns out to be
decides which side of it they come out on. Somebody new gets the form. Somebody
the venue already knows gets their own page, because the second thing anybody
does with a QR code is scan it again.
"""

import identity
import rooms

COOKIE = "arena_session"


def open_room(client, mode="solo"):
    return client.post("/api/rooms", json={"mode": mode}).json()["code"]


# ── The door ─────────────────────────────────────────────────────────


def test_a_phone_the_venue_has_never_seen_is_sent_to_the_form(client):
    answer = client.get("/scan", follow_redirects=False)
    assert answer.headers["location"] == "/register"


def test_a_phone_the_venue_knows_is_sent_to_its_own_page(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    answer = client.get("/scan", follow_redirects=False)
    assert answer.headers["location"] == "/home"


def test_a_cookie_from_an_event_since_wiped_is_a_door_and_not_a_500(client):
    # The database is emptied between events. A cookie in somebody's pocket is
    # not, and it is still signed, still unexpired and still names a player who
    # no longer exists. That phone is simply somebody new.
    import app as arena

    client.cookies.set(COOKIE, identity.sign_token(9999, arena.SESSION_SECRET))
    answer = client.get("/scan", follow_redirects=False)
    assert answer.headers["location"] == "/register"


def test_the_door_leads_somewhere_that_is_there(client):
    for page in ("/register", "/home", "/poster"):
        answer = client.get(page)
        assert answer.status_code == 200, page
        assert answer.headers["content-type"].startswith("text/html"), page


def test_the_home_page_is_served_to_a_phone_with_no_session_too(client):
    # It is a shell that asks who it belongs to and sends a stranger to the
    # form. Refusing the file itself would make that a blank page instead.
    assert client.get("/home").status_code == 200


# ── Who this phone is ────────────────────────────────────────────────


def test_a_phone_with_no_session_is_nobody(client):
    assert client.get("/api/players/me").status_code == 401


def test_a_phone_is_told_its_own_name_and_its_masked_address(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    me = client.get("/api/players/me").json()
    assert me["display_name"] == "Alex Rivera"
    assert me["email"] == "a***x@example.com"


def test_a_manager_sitting_in_nothing_is_sitting_in_nothing(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    assert client.get("/api/players/me").json()["room"] is None


def test_a_manager_who_holds_a_dugout_is_told_which_one(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = open_room(client)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    assert client.get("/api/players/me").json()["room"] == \
        {"code": code, "mode": "solo", "status": "lobby", "team": "blue"}


def test_a_dugout_in_a_match_still_running_is_the_one_to_go_back_to(client, live_room):
    code, _ = live_room()
    assert client.get("/api/players/me").json()["room"]["code"] == code


def test_a_match_that_is_over_is_not_somewhere_to_go_back_to(client, live_room, conn):
    code, _ = live_room()
    rooms.finish_match(conn, rooms.by_code(conn, code)["id"])
    conn.commit()
    assert client.get("/api/players/me").json()["room"] is None


# ── Which rooms are waiting for somebody ─────────────────────────────


def test_a_venue_with_nothing_open_says_so_rather_than_erroring(client):
    assert client.get("/api/rooms/open").json() == {"rooms": [], "playing": []}


def test_a_room_in_its_lobby_is_a_room_to_walk_into(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = open_room(client)
    assert client.get("/api/rooms/open").json()["rooms"] == \
        [{"code": code, "mode": "solo", "open_seats": ["blue"], "seats": {}}]


def test_a_room_says_who_is_already_waiting_in_it(client, phones):
    alex = phones.join("Alex Rivera", "alex@example.com")
    code = open_room(client, "versus")
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    phones.fresh()
    room = client.get("/api/rooms/open").json()["rooms"][0]
    # So the one seat left reads as somebody to play rather than as a vacancy.
    assert room["seats"] == {"blue": "Alex Rivera"}
    assert room["open_seats"] == ["red"]
    phones.use(alex)


def test_a_room_with_both_dugouts_taken_is_not_open(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = open_room(client, "versus")
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    phones.join("Sam Okafor", "sam@example.com")
    client.post(f"/api/rooms/{code}/seats/red", json={"philosophy": "counter"})
    assert client.get("/api/rooms/open").json()["rooms"] == []


def test_a_solo_room_is_full_at_one(client, phones):
    # Nobody sits in the red dugout of a score attack, so a room that still has
    # an empty one is not a room anybody can join.
    phones.join("Alex Rivera", "alex@example.com")
    code = open_room(client)
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    assert client.get("/api/rooms/open").json()["rooms"] == []


def test_a_match_already_under_way_is_not_a_room_to_walk_into(client, live_room):
    live_room()
    assert client.get("/api/rooms/open").json()["rooms"] == []


def test_a_phone_with_nowhere_to_go_is_told_what_is_being_played(client, live_room):
    # The reason there is no seat, which is also the promise that there will be
    # one. A screen holds one room and a score attack seats one manager, so the
    # second person to scan the sheet finds an empty list - and an empty list on
    # its own is what a venue with nothing plugged in looks like.
    code, _ = live_room()
    answer = client.get("/api/rooms/open").json()
    assert answer["rooms"] == []
    assert answer["playing"] == \
        [{"code": code, "mode": "solo", "blue": "Alex Rivera", "red": None}]


def test_the_page_names_them_rather_than_saying_no_screen_is_waiting(client):
    # Both branches out of one empty list, because the difference between them
    # is the whole point of asking what is on.
    js = client.get("/static/home.js").text
    assert "playing right now" in js
    assert "No screen is waiting" in js


def test_a_match_that_ended_is_not_still_being_played(client, live_room, conn):
    code, _ = live_room()
    rooms.finish_match(conn, rooms.by_code(conn, code)["id"])
    conn.commit()
    assert client.get("/api/rooms/open").json()["playing"] == []


def test_the_workshop_is_not_a_room_anybody_walks_into(client, conn):
    # It sits in its lobby for the life of the deployment, because it is where
    # the dugout tunes profiles with nobody in a dugout seat. A manager sent to
    # it would be sitting in a room with no screen in front of it.
    import codes

    assert rooms.by_code(conn, codes.WORKSHOP) is not None
    assert client.get("/api/rooms/open").json()["rooms"] == []


def test_rooms_are_offered_in_the_order_they_opened(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    first, second = open_room(client), open_room(client)
    assert [room["code"] for room in client.get("/api/rooms/open").json()["rooms"]] == \
        [first, second]
