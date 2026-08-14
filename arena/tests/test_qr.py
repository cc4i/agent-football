"""The codes a phone gets pointed at: a room's, and the venue's own."""

import re

import segno

import codes
import qr

# A URL of about the length a real venue's is, so the symbol these tests
# measure is the size a real one comes out.
SOMEWHERE = "http://arena.local:8003/scan"


def dots(drawing):
    """The path that is the code itself.

    The first one in the document: segno draws every module in a single path
    and the mark goes on top of it afterwards, so anything after this is the
    mark.
    """
    return re.search(r'\sd="([^"]+)"', drawing).group(1)


def plain(url, error):
    """The same code with nothing on it, for the drawing to be compared to."""
    return segno.make(url, error=error).svg_inline(scale=1, border=qr.QUIET,
                                                   unit="mm", svgclass=None, lineclass=None)


def span(drawing):
    """How many modules wide the drawing is, quiet zone included."""
    return float(re.search(r'viewBox="0 0 ([\d.]+) ', drawing).group(1))


def test_a_code_says_the_address_it_was_given():
    # Nothing here decodes a QR, so this compares the modules against the ones
    # segno draws for the same address with no mark on it. Same modules, same
    # address: what the mark is painted over cannot have changed them.
    assert dots(qr.svg(SOMEWHERE).decode()) == dots(plain(SOMEWHERE, "h"))


def test_a_covered_middle_is_paid_for_before_it_is_covered():
    # Level h keeps 30% of the symbol recoverable where the m these codes used
    # to be drawn at keeps 15%, and that difference is the whole reason the
    # mark can be there at all. It costs four modules of resolution.
    drawing = qr.svg(SOMEWHERE).decode()
    assert dots(drawing) != dots(plain(SOMEWHERE, "m"))
    assert span(drawing) > span(plain(SOMEWHERE, "m"))


def test_the_mark_covers_far_less_than_the_correction_could_recover():
    drawing = qr.svg(SOMEWHERE).decode()
    plate = float(re.search(r'<rect[^>]*width="([\d.]+)"', drawing).group(1))
    # Squared, because it is the area the parity has to make good, and against
    # a fifth of the 30% that parity is: printed codes are also read folded, in
    # bad light and at an angle, and all of that comes out of the same budget.
    assert (plate / span(drawing)) ** 2 < 0.06


def test_the_mark_sits_in_the_middle_where_no_finder_pattern_is():
    drawing = qr.svg(SOMEWHERE).decode()
    rect = re.search(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)"', drawing)
    left, top, plate = (float(value) for value in rect.groups())
    assert left == top
    assert abs(left + plate / 2 - span(drawing) / 2) < 0.01


def test_the_mark_stands_on_something_opaque():
    # Otherwise the modules underneath it show through the G and a decoder is
    # asked to read a mark as data.
    assert '<rect' in qr.svg(SOMEWHERE).decode()
    assert re.search(r'<rect[^>]*fill="#fff"', qr.svg(SOMEWHERE).decode())


def test_the_mark_is_the_G_in_its_own_four_colours():
    drawing = qr.svg(SOMEWHERE).decode().lower()
    for colour in ("#ea4335", "#4285f4", "#fbbc05", "#34a853"):
        assert colour in drawing


def test_the_venue_code_is_the_way_in_and_not_the_way_into_one_room(
        client, monkeypatch):
    # A sheet on a wall cannot name a room: every screen opens its own and the
    # sheet is printed once, before any of them exist.
    monkeypatch.setattr("app.PUBLIC_URL", "http://arena.local:8003")
    response = client.get("/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.content == qr.svg("http://arena.local:8003/scan")


def test_a_rooms_code_carries_the_same_mark(client, phones):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert "#ea4335" in client.get(f"/api/rooms/{code}/qr.svg").text.lower()


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
