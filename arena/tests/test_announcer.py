"""The standings turned into something worth listening to."""

import io
import wave

import announcer

SOLO = [{"player_id": 1, "name": "Alex Rivera", "email": "a***@example.com",
         "team": "blue", "philosophy": "high press", "room": "AB12",
         "points": 41, "outcome": "won", "goals_for": 5, "goals_against": 1,
         "first_goal_ms": 42_000, "shouts": 3, "effective": 2, "rating": None}]
VERSUS = [{"player_id": 2, "name": "Sam Okafor", "email": None, "played": 5,
           "won": 5, "drew": 0, "lost": 0, "goals_for": 14, "goals_against": 3,
           "difference": 11, "rating": 1042.0, "last": {"outcome": "won",
           "room": "CD34", "goals_for": 3, "goals_against": 0, "against": "Jo"}}]


def test_a_podium_carries_only_what_could_be_said_out_loud():
    said = announcer.spoken(SOLO, VERSUS)
    assert said["score_attack"][0] == {
        "name": "Alex Rivera", "points": 41, "goals_for": 5, "goals_against": 1,
        "first_goal_seconds": 42, "shouts": 3, "shouts_that_worked": 2}
    assert said["head_to_head"][0] == {
        "name": "Sam Okafor", "played": 5, "won": 5, "drew": 0, "lost": 0,
        "goal_difference": 11}


def test_an_address_never_reaches_the_prompt():
    # The rows carry a masked address for the board to print under a name.
    # Nothing is gained by sending it to a model, so nothing does.
    said = announcer.spoken(SOLO, VERSUS)
    assert "email" not in said["score_attack"][0]
    assert "@" not in str(said)


def test_a_manager_who_never_scored_has_no_first_goal():
    quiet = dict(SOLO[0], first_goal_ms=None)
    assert announcer.spoken([quiet], [])["score_attack"][0]["first_goal_seconds"] is None


def test_the_same_podiums_fingerprint_the_same():
    assert announcer.fingerprint(announcer.spoken(SOLO, VERSUS)) == \
           announcer.fingerprint(announcer.spoken(SOLO, VERSUS))


def test_a_podium_that_moves_is_a_different_clip():
    moved = [dict(SOLO[0], points=42)]
    assert announcer.fingerprint(announcer.spoken(SOLO, VERSUS)) != \
           announcer.fingerprint(announcer.spoken(moved, VERSUS))


def test_rewording_the_prompt_retires_every_clip(monkeypatch):
    before = announcer.fingerprint(announcer.spoken(SOLO, VERSUS))
    monkeypatch.setattr(announcer, "PROMPT_VERSION", announcer.PROMPT_VERSION + 1)
    assert announcer.fingerprint(announcer.spoken(SOLO, VERSUS)) != before


def test_raw_pcm_becomes_a_file_a_browser_will_play():
    # Vertex answers with headerless PCM, which no <audio> element will touch.
    pcm = b"\x00\x01" * 24_000
    parsed = wave.open(io.BytesIO(announcer.as_wav(pcm)), "rb")
    assert parsed.getnchannels() == 1
    assert parsed.getsampwidth() == 2
    assert parsed.getframerate() == announcer.SAMPLE_RATE
    assert parsed.getnframes() == 24_000


def test_a_clips_length_is_read_off_its_samples():
    assert announcer.seconds(b"\x00\x01" * 24_000) == 1.0
    assert announcer.seconds(b"") == 0.0
