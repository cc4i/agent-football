"""One screen, a queue in front of it, and an afternoon to get through.

A room is opened by a screen, the token that runs it lives in that tab, and a
tab holds one room. So the number of people who can be playing at once is the
number of screens that are on, and everybody else is waiting for one of them to
come free. That is the shape of the product and it is fine -- a venue adds
screens the way it adds pitches.

What is not fine is the changeover. The screen used to stop at full time and
wait to be clicked, and the phones in the queue were told "no screen is waiting
for a manager this second" -- word for word what an empty venue says. Nobody in
the queue is standing next to the screen; they are all looking at their phones.
So the screen goes back to work on its own, and the phones are told what is
being played while they wait.
"""

import re


def timing(js, name):
    return int(re.search(rf"const {name} = (\d+)", js).group(1))


def handover(js):
    """The body of the countdown from a result to the next lobby."""
    return js.split("function handOver")[1].split("\n}", 1)[0]


def test_the_screen_opens_the_next_lobby_without_being_asked(client):
    # The whole venue's turnstile is this one page, and it used to be behind a
    # click. One match a day, unless an organiser happened to be standing at
    # the screen when the whistle went.
    js = client.get("/static/arena.js").text
    assert 'status === "finished" && screenToken()' in js, \
        "full time on this screen's own room is what starts the handover"
    assert "location.assign(`/arena?mode=" in handover(js)


def test_a_screen_that_is_only_watching_does_not_take_itself_elsewhere(client):
    # Every other screen in the building has this room open to watch it, and
    # `/arena?room=X` with no token is how an organiser puts a match up on a
    # second wall. A screen that navigated on somebody else's whistle would
    # walk off its own accord and open a room nobody asked for.
    js = client.get("/static/arena.js").text
    started = js.split('status === "finished"')[1].split("\n", 1)[0]
    assert "screenToken()" in started


def test_the_result_stands_long_enough_to_be_read(client):
    # A screen that cleared a scoreline the instant it was decided would be one
    # people miss the end of their own match on, from across a hall.
    js = client.get("/static/arena.js").text
    wait = timing(js, "NEXT_LOBBY_MS")
    assert 10000 <= wait <= 60000, \
        "long enough to read a score, short enough that a queue is not held up"


def test_the_wait_is_counted_out_loud(client):
    # Both halves of the same courtesy: somebody reading the score is told it
    # is about to go, and somebody waiting to play is told the screen is coming
    # back rather than left to guess whether it has stopped for the day.
    js = client.get("/static/arena.js").text
    assert "Next lobby in ${left}s" in handover(js)
    # And the button stays where it is, for anybody who does not want the wait.
    assert 'el("again").hidden = snapshot.status !== "finished";' in js


def test_the_countdown_keeps_the_badge_it_is_counting_in(client):
    # A room message arriving behind the countdown redraws the seats and the
    # badge with them, which would put "Open a new room to play again" back on
    # top of the count until the next tick took it off again.
    js = client.get("/static/arena.js").text
    seats = js.split("function drawSeats")[1].split("\n}", 1)[0]
    assert "if (!handover)" in seats


def test_the_next_lobby_is_the_mode_the_last_match_was(client):
    # A head-to-head screen that reopened as score attack would offer one seat
    # to the two managers who came to play each other, every match, all evening.
    assert "ours.mode" in handover(client.get("/static/arena.js").text)
