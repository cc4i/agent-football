"""The pages the arena serves: the phone's two, and the big screen's one."""

import codes


def test_the_front_door_is_the_big_screen(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/arena"


def test_scanning_a_rooms_code_opens_the_join_form(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    response = client.get(f"/join/{code}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<form" in response.text


def test_a_stale_code_says_so_rather_than_showing_a_form(client):
    # A QR photographed at last week's event should not open a form that
    # fails on submit. There is no room, so there is no page.
    assert client.get("/join/ZZZZ").status_code == 404


def test_a_code_that_could_never_have_been_issued_is_a_404_not_a_500(client):
    assert client.get("/join/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_the_workshop_can_be_joined_by_hand(client):
    assert client.get(f"/join/{codes.WORKSHOP}").status_code == 200


def test_the_dugout_is_served_to_a_phone(client):
    response = client.get("/play")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_the_big_screen_is_served(client):
    response = client.get("/arena")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_the_pages_share_one_stylesheet(client):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_the_static_mount_cannot_be_walked_out_of(client):
    assert client.get("/static/../app.py").status_code in (404, 403)
