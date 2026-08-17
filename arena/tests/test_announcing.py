"""The button on the big screen, and what answers it."""

import io
import wave

import pytest

import announcer
import app


@pytest.fixture
def switched_on(monkeypatch):
    """The announcer configured, with a model that costs nothing."""
    monkeypatch.setattr(announcer, "ENABLED", True)
    monkeypatch.setattr(announcer, "API_KEY", "a-key")

    async def generate(podiums):
        return b"\x00\x01" * 24_000, {"solo": "one two three four",
                                      "versus": "five six"}

    monkeypatch.setattr(announcer, "_generate", generate)


def test_the_venue_says_the_announcer_is_off_when_it_is(client, monkeypatch):
    monkeypatch.setattr(announcer, "ENABLED", False)
    assert client.get("/api/venue").json()["announcer"] is False


def test_the_venue_says_it_is_on_when_it_is(client, switched_on):
    assert client.get("/api/venue").json()["announcer"] is True


def test_an_unconfigured_venue_refuses_rather_than_pretends(client, monkeypatch):
    monkeypatch.setattr(announcer, "ENABLED", False)
    answer = client.post("/api/board/announcement")
    assert answer.status_code == 503
    assert "not switched on" in answer.json()["detail"]


def test_an_empty_board_has_nothing_to_announce(client, switched_on):
    answer = client.post("/api/board/announcement")
    assert answer.status_code == 409
    assert "nobody" in answer.json()["detail"]


def test_a_press_comes_back_with_a_clip_to_play(client, phones, finished, switched_on):
    answer = client.post("/api/board/announcement").json()
    assert answer["seconds"] == 1.0
    assert answer["switch_at"] == 0.67
    assert answer["script"]["solo"] == "one two three four"
    assert answer["audio"] == f"/api/board/announcement/{answer['state']}.wav"


def test_the_audio_is_a_file_a_browser_will_play(client, phones, finished, switched_on):
    made = client.post("/api/board/announcement").json()
    answer = client.get(made["audio"])
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/wav"
    parsed = wave.open(io.BytesIO(answer.content), "rb")
    assert parsed.getframerate() == 24_000


def test_a_clip_is_cached_by_the_thing_it_is_about(client, phones, finished, switched_on):
    # The fingerprint is in the path, so it can be cached forever: a new
    # podium is a new path rather than a new body at the old one.
    made = client.post("/api/board/announcement").json()
    headers = client.get(made["audio"]).headers
    assert "immutable" in headers["cache-control"]
    assert "max-age=31536000" in headers["cache-control"]


def test_an_announcement_that_has_been_replaced_says_so(client, switched_on):
    answer = client.get("/api/board/announcement/nosuchclip.wav")
    assert answer.status_code == 404
    assert "replaced" in answer.json()["detail"]


def test_a_model_that_fails_leaves_the_lobby_working(client, phones, finished, switched_on,
                                                     monkeypatch):
    async def broken(podiums):
        raise announcer.Silent("the announcer lost its voice")

    monkeypatch.setattr(announcer, "_generate", broken)
    answer = client.post("/api/board/announcement")
    assert answer.status_code == 503
    assert answer.json()["detail"] == "the announcer lost its voice"
    # The board itself is untouched by any of this.
    assert client.get("/api/board").status_code == 200


def test_a_button_held_down_is_refused_before_it_reaches_a_model(
        client, phones, finished, switched_on):
    codes = [client.post("/api/board/announcement").status_code for _ in range(app.ANNOUNCE_BURST + 2)]
    assert 429 in codes


def test_the_button_is_not_rendered_before_the_venue_has_answered(client):
    # Same rule the mode switch follows: a control that may not belong on this
    # screen must not flash up and then vanish.
    assert '<button class="mic-chip" id="announce" type="button" hidden>' \
        in client.get("/arena").text


def test_a_screen_with_no_announcer_never_shows_the_button(client):
    js = client.get("/static/arena.js").text
    assert "venue.announcer" in js


def test_the_clip_is_played_faster_than_it_was_spoken(client):
    js = client.get("/static/arena.js").text
    assert "playbackRate" in js
    assert "1.25" in js


def test_the_element_is_unlocked_inside_the_gesture(client):
    """The detail this feature dies on if it is dropped.

    Generation takes seconds, so by the time the clip lands the click's
    transient activation is gone and Safari refuses to play. The element has
    to be started on something silent while the gesture is still live. If the
    unlock ever moves below an await, this test is the thing that notices.
    """
    js = client.get("/static/arena.js").text
    press = js.split("async function readTheBoard")[1].split("\n}")[0]
    assert press.index("unlock()") < press.index("await")


def test_the_frame_is_turned_over_on_the_media_clock(client):
    """`switch_at` is a position in the file, not a wall-clock delay.

    `currentTime` reports the media clock whatever the playback rate is, so
    the two are directly comparable and dividing by the rate would turn the
    board over a quarter of the way early.
    """
    js = client.get("/static/arena.js").text
    turning = js.split("switch_at")[1]
    assert "/ RATE" not in turning.split("\n")[0]
    assert "currentTime" in js


def test_the_board_page_takes_direction_only_from_its_own_origin(client):
    js = client.get("/static/board.js").text
    assert "location.origin" in js
    assert "board.show" in js


def test_a_pinned_board_stops_sliding_away_mid_sentence(client):
    # The frame cycles every twelve seconds on its own, which is most of the
    # way through one half of an announcement.
    js = client.get("/static/board.js").text
    handler = js.split("board.show")[1]
    assert "clearTimeout" in handler


def test_the_board_starts_cycling_again_when_the_announcer_stops(client):
    assert "unpin" in client.get("/static/board.js").text
    assert '"board.show"' in client.get("/static/arena.js").text
