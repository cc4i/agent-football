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
