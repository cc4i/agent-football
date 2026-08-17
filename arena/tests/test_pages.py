"""The pages the arena serves: the phone's two, and the two on a screen."""

import re

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


def test_a_returning_phone_is_never_shown_the_name_box_first(client, phones):
    # Which of the two identity states to show is not known until the arena
    # answers, so the page asserts neither until it has. Sequenced after the
    # room, that answer arrived a measured 596ms late in production against a
    # fast link, and for all of it a manager the venue already knows was
    # reading "Name on the board" - long enough to start typing a name that
    # was about to be replaced by their own.
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert '<div id="boxes" hidden>' in client.get(f"/join/{code}").text
    # Fired with the room rather than behind it. Source order stands in for
    # concurrency: what mattered was that it stopped being awaited last.
    js = client.get("/static/join.js").text
    assert 'get("/api/players/me")' in js.split("Promise.all")[0]


def test_a_room_that_closed_is_not_described_as_one_that_kicked_off(client):
    # Rooms end without ever starting now that the arena gives up on a lobby
    # whose screen has gone, and a phone can be standing on the join form when
    # it happens. "That match has already kicked off" would send somebody
    # hunting the venue for a match nobody is playing.
    js = client.get("/static/join.js").text
    live = js.split('room.status === "live"')[1]
    assert "already kicked off" in live.split("}", 1)[0]
    assert "That room is closed." in js


def test_the_stylesheet_is_not_quietly_broken(client):
    """An orphaned `*/` takes the rules after it down and says nothing.

    CSS has no parse error a browser will show you. An unbalanced comment marker
    swallows whatever follows until the parser finds something it can
    resynchronise on, and the page renders with rules silently missing. This
    file is heavily commented - the comments are most of its lines - so an edit
    landing a stray marker mid-paragraph is the likeliest way to break it, and
    it happened: two rules under one went unapplied, and the only reason it was
    caught was somebody looking at a screenshot at that moment.

    Cheap to check and cheap to run, which is the whole argument for it.
    """
    css = client.get("/static/app.css").text

    inside, opened_at = False, 0
    for marker in re.finditer(r"/\*|\*/", css):
        if marker.group() == "/*":
            # Comments do not nest in CSS, so a second one inside an open
            # comment is text rather than a mistake.
            if not inside:
                inside, opened_at = True, marker.start()
        else:
            assert inside, (
                f"an orphaned */ at character {marker.start()}, after: "
                f"{css[max(0, marker.start() - 90):marker.start()]!r}")
            inside = False
    assert not inside, f"the comment opened at character {opened_at} never closes"

    # And the rules themselves. A block left open runs the next selector into
    # the one before it, which is the other way this file breaks quietly.
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert rules.count("{") == rules.count("}"), (
        f"{rules.count('{')} open braces against {rules.count('}')} closing")


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


def test_the_standings_are_served(client):
    response = client.get("/board")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_the_pages_share_one_stylesheet(client):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_every_scrolling_surface_inherits_a_scrollbar_the_dark_theme_can_hold(
        client):
    # `scrollbar-width:thin` on its own asks the platform for a narrow
    # scrollbar and takes the platform's colours with it, which on a light OS
    # is a white track laid down the side of a near-black panel. It showed up
    # as a bright bar beside the standings on the arena screen, which is the
    # one surface in this product nobody can scroll away and everybody is
    # looking at from across a hall.
    #
    # Both scrollbar properties inherit, so the colour is stated once at the
    # root and every scroller picks it up. Asserted at the root rather than
    # per rule for that reason: the next surface to want a scrollbar cannot
    # forget it, because there is nothing left to remember.
    # Comments out first: they discuss these very declarations, and a scan
    # over the prose would find the thing it is looking for in the sentence
    # explaining it.
    css = re.sub(r"/\*.*?\*/", "", client.get("/static/app.css").text,
                 flags=re.S)
    root = css.split(":root{", 1)[1].split("}", 1)[0]
    assert "scrollbar-color:" in root
    for rule in css.split("scrollbar-width:thin")[1:]:
        assert "scrollbar-color" not in rule.split("}", 1)[0], (
            "the root already states it; a second copy is one that can drift")


def test_the_relay_breaks_a_word_no_rail_is_wide_enough_for(client):
    # Everything drawn into a rail is somebody else's words: a manager typing
    # one-handed with a match running, and a language model reporting on the
    # player it is playing. Neither of them promises a space. A shout of fifty
    # w's, and an injury a specialist described in one unbroken run of
    # characters, both painted straight out through the right border of their
    # box and off the side of the rail.
    #
    # Asserted on the container for the same reason the scrollbar above is:
    # overflow-wrap inherits, all three rails are this one class, and a block
    # added to the feed next week cannot forget a rule it never has to write.
    css = re.sub(r"/\*.*?\*/", "", client.get("/static/app.css").text, flags=re.S)
    rail = css.split(".relay-scroll{", 1)[1].split("}", 1)[0]
    assert "overflow-wrap:anywhere" in rail.replace(" ", "")
    # Inheritance is only a guarantee while nothing underneath declines it.
    assert "overflow-wrap:normal" not in css.replace(" ", ""), (
        "something inside the feed turns this back off, which is the overflow"
        " coming back on whichever block it was turned off for")


def test_no_field_a_thumb_lands_on_is_small_enough_for_ios_to_zoom_the_page(client):
    # Safari on iOS zooms the whole page in whenever it focuses a field whose
    # text is under 16px, and it does not zoom back out again. The shout box
    # was .9rem, so tapping it to talk to the squad blew the dugout up past
    # both edges of the screen -- the relay off one side, the chips off the
    # other -- one-handed, with a match running, and the only way back was to
    # pinch it down by hand.
    #
    # Asserted over every rule that dresses a field rather than over that one
    # box. The rule is not "the shout box is 16px", it is that nothing a thumb
    # lands on is smaller than that, and the next field added is the next one
    # to forget it.
    #
    # Comments out first, and innermost blocks only: `@media` and `@keyframes`
    # nest, and the rules inside them dress fields on the same phone.
    css = re.sub(r"/\*.*?\*/", "", client.get("/static/app.css").text, flags=re.S)
    fields = re.compile(r"\.input\b|(?:^|[\s,>+~])(?:input|textarea|select)\b")
    checked = 0
    for selector, block in re.findall(r"([^{}]*)\{([^{}]*)\}", css):
        if not fields.search(selector):
            continue
        size = re.search(r"font-size:\s*([\d.]+)(rem|em|px)", block)
        if not size:
            continue
        checked += 1
        px = float(size.group(1)) * (1 if size.group(2) == "px" else 16)
        assert px >= 16, (
            f"{selector.strip()} sets {size.group(0)} = {px}px;"
            " iOS zooms the page on anything under 16px and stays there")
    # The stylesheet is one file and the selectors above are how a field is
    # found in it. A rename that matched nothing would pass every assertion in
    # the loop by never running one.
    assert checked, "no field's font-size was found to check"


def test_a_screen_left_running_picks_up_a_fixed_stylesheet(client):
    # A browser given no Cache-Control invents one, and it invents hours. The
    # wall screen at a venue is never reloaded with the cache bypassed, so the
    # answer has to be revalidated rather than guessed at.
    for path in ("/static/app.css", "/static/board.js"):
        assert client.get(path).headers["cache-control"] == "no-cache"


def test_the_static_mount_cannot_be_walked_out_of(client):
    assert client.get("/static/../app.py").status_code in (404, 403)


def test_the_pitch_is_not_mounted_without_a_directory(client):
    # Locally Vite serves it. A 404 here is the arena declining to guess.
    assert client.get("/pitch/").status_code == 404
