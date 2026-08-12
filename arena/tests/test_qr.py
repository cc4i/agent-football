"""The QR code a phone scans to reach a room."""

import codes


def test_a_rooms_qr_is_an_svg_a_browser_will_render(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    response = client.get(f"/api/rooms/{code}/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<?xml") or "<svg" in response.text


def test_the_qr_points_at_this_rooms_join_page(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    body = client.get(f"/api/rooms/{code}").json()
    assert body["join_url"].endswith(f"/join/{code}")


def test_the_join_url_follows_the_address_the_phone_will_use(client, phones, monkeypatch):
    # A phone cannot reach 127.0.0.1, so the venue sets the address the QR
    # encodes. Without it the code is unscannable from anywhere but this laptop.
    monkeypatch.setattr("app.PUBLIC_URL", "http://arena.local:8003")
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert client.get(f"/api/rooms/{code}").json()["join_url"] == \
        f"http://arena.local:8003/join/{code}"


def test_a_room_that_does_not_exist_has_no_qr(client):
    assert client.get("/api/rooms/ZZZZ/qr.svg").status_code == 404


def test_a_code_that_could_never_have_been_issued_is_a_404_not_a_500(client):
    assert client.get("/api/rooms/..%2F..%2Fetc/qr.svg").status_code == 404


def test_the_workshop_has_a_qr_too(client):
    # It is a real room now, so nothing about it should be a special case.
    assert client.get(f"/api/rooms/{codes.WORKSHOP}/qr.svg").status_code == 200
