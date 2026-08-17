"""The standings turned into something worth listening to."""

import asyncio
import base64
import io
import json
import os
import wave

import pytest

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
    # Board rows no longer carry addresses. This asserts the trim drops the
    # player id, which says nothing out loud.
    said = announcer.spoken(SOLO, VERSUS)
    assert "email" not in said["score_attack"][0]
    assert "player_id" not in said["score_attack"][0]
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


PODIUMS = {"score_attack": [{"name": "Alex Rivera", "points": 41}],
           "head_to_head": [{"name": "Sam Okafor", "won": 5}]}


def a_script(text=None):
    """What Vertex answers a script call with."""
    said = text or {"solo": "ALEX with FORTY-ONE!", "versus": "SAM, UNBEATEN!"}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(said)}]}}]}


def some_audio(pcm=b"\x00\x01" * 100):
    """What Vertex answers a speech call with: base64 PCM, no header."""
    return {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/L16;rate=24000",
                        "data": base64.b64encode(pcm).decode()}}]}}]}


def recorder(answer):
    """A stand-in for the model that keeps what it was asked."""
    seen = {}

    async def call(model, body):
        seen["model"] = model
        seen["body"] = body
        return answer

    return call, seen


def test_off_when_nobody_asked_for_it(monkeypatch):
    monkeypatch.setattr(announcer, "ENABLED", False)
    monkeypatch.setattr(announcer, "PROJECT", "a-project")
    assert not announcer.configured()


def test_off_when_there_is_nowhere_to_call(monkeypatch):
    monkeypatch.setattr(announcer, "ENABLED", True)
    monkeypatch.setattr(announcer, "PROJECT", "")
    monkeypatch.setattr(announcer, "API_KEY", "")
    assert not announcer.configured()


def test_a_laptop_with_a_key_can_hear_it(monkeypatch):
    monkeypatch.setattr(announcer, "ENABLED", True)
    monkeypatch.setattr(announcer, "PROJECT", "")
    monkeypatch.setattr(announcer, "API_KEY", "a-key")
    assert announcer.configured()


async def test_the_script_call_asks_for_json_it_can_rely_on():
    call, seen = recorder(a_script())
    await announcer.script(PODIUMS, call=call)
    config = seen["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == ["solo", "versus"]
    assert seen["model"] == announcer.SCRIPT_MODEL


async def test_the_script_call_carries_the_word_budget_and_the_boards():
    call, seen = recorder(a_script())
    await announcer.script(PODIUMS, call=call)
    instruction = seen["body"]["systemInstruction"]["parts"][0]["text"]
    # A word budget and not a duration: the model can count words and has
    # never heard itself speak.
    assert "120 to 135 words" in instruction
    assert "seconds" not in instruction.split("Length.")[1].split("\n")[0]
    assert "Alex Rivera" in seen["body"]["contents"][0]["parts"][0]["text"]


async def test_a_script_comes_back_in_two_halves():
    call, _ = recorder(a_script())
    words = await announcer.script(PODIUMS, call=call)
    assert words == {"solo": "ALEX with FORTY-ONE!", "versus": "SAM, UNBEATEN!"}


async def test_a_script_that_is_not_json_is_a_silence_not_a_crash():
    async def rambled(model, body):
        return {"candidates": [{"content": {"parts": [{"text": "Sure! Here you go:"}]}}]}

    with pytest.raises(announcer.Silent):
        await announcer.script(PODIUMS, call=rambled)


async def test_the_speech_call_asks_for_audio_in_the_venues_voice(monkeypatch):
    monkeypatch.setattr(announcer, "VOICE", "Puck")
    call, seen = recorder(some_audio())
    await announcer.speak("ALEX with FORTY-ONE!", call=call)
    config = seen["body"]["generationConfig"]
    assert config["responseModalities"] == ["AUDIO"]
    assert (config["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
            == "Puck")
    # The TTS model ignores these, so sending them is noise on the wire.
    assert "temperature" not in config
    assert seen["model"] == announcer.TTS_MODEL


async def test_the_direction_is_prefixed_to_the_script():
    # Vertex concatenates prompt and text into one `contents` field for this
    # model rather than taking them separately.
    call, seen = recorder(some_audio())
    await announcer.speak("ALEX with FORTY-ONE!", call=call)
    sent = seen["body"]["contents"][0]["parts"][0]["text"]
    assert sent.startswith("Audio profile:")
    assert sent.endswith("ALEX with FORTY-ONE!")


async def test_speech_comes_back_as_samples():
    call, _ = recorder(some_audio(b"\x02\x03" * 50))
    assert await announcer.speak("anything", call=call) == b"\x02\x03" * 50


async def test_a_refusal_with_no_audio_in_it_is_a_silence():
    async def refused(model, body):
        return {"candidates": [{"content": {"parts": [{"text": "I cannot do that"}]}}]}

    with pytest.raises(announcer.Silent):
        await announcer.speak("anything", call=refused)


async def test_a_deployed_venue_calls_vertex_in_its_region(monkeypatch):
    monkeypatch.setattr(announcer, "API_KEY", "")
    monkeypatch.setattr(announcer, "PROJECT", "a-project")
    monkeypatch.setattr(announcer, "LOCATION", "us-central1")

    async def token():
        return "a-token"

    url, headers = await announcer._reach("a-model", token=token)
    assert url.startswith("https://us-central1-aiplatform.googleapis.com/v1/projects/a-project")
    assert url.endswith("publishers/google/models/a-model:generateContent")
    assert headers["Authorization"] == "Bearer a-token"


async def test_a_laptop_calls_the_api_with_its_key(monkeypatch):
    monkeypatch.setattr(announcer, "API_KEY", "a-key")
    monkeypatch.setattr(announcer, "PROJECT", "a-project")
    url, headers = await announcer._reach("a-model")
    # The key wins where both are set, because the only reason to have one on
    # a machine that also has a project is to be testing with it.
    assert url == ("https://generativelanguage.googleapis.com/v1beta/models/"
                   "a-model:generateContent")
    assert headers["x-goog-api-key"] == "a-key"


async def test_a_two_hundred_that_is_not_json_is_a_silence_not_a_crash(monkeypatch):
    """A 2xx carrying something no parser will touch: a proxy's HTML, a
    truncated read, a gateway apologising in the body.

    `reply.json()` raises `ValueError`, which is not an `httpx.HTTPError`.
    Caught narrowly it walked out through `script`'s try - which wraps the
    parse, not the call - out through `_make`, which catches `TimeoutError`,
    and out through the endpoint, which catches `Silent`: a 500 and a stack
    trace where the venue should have had a sentence.
    """
    monkeypatch.setattr(announcer, "API_KEY", "a-key")

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *over):
            return False

        async def post(self, url, headers=None, json=None):
            return Reply()

    monkeypatch.setattr(announcer.httpx, "AsyncClient", lambda **anything: Client())
    with pytest.raises(announcer.Silent):
        await announcer._post("a-model", {})


def a_clip(pcm=b"\x00\x01" * 24_000, words=None):
    """A generator stand-in that counts how often it was asked."""
    made = {"times": 0}

    async def generate(podiums):
        made["times"] += 1
        await asyncio.sleep(0)
        return pcm, words or {"solo": "one two three four", "versus": "five six"}

    return generate, made


async def test_the_same_podiums_are_only_ever_made_once():
    generate, made = a_clip()
    talking = announcer.Announcer(generate=generate)
    await talking.clip(PODIUMS)
    await talking.clip(PODIUMS)
    assert made["times"] == 1


async def test_twenty_screens_pressing_at_once_cost_one_generation():
    # The whole guard, in one test. Every screen wants the identical clip,
    # because a clip is a pure function of the podiums.
    started = asyncio.Event()
    holding = asyncio.Event()
    made = {"times": 0}

    async def slow(podiums):
        made["times"] += 1
        started.set()
        await holding.wait()
        return b"\x00\x01" * 24_000, {"solo": "one two", "versus": "three"}

    talking = announcer.Announcer(generate=slow)
    pressing = [asyncio.create_task(talking.clip(PODIUMS)) for _ in range(20)]
    await started.wait()
    holding.set()
    clips = await asyncio.gather(*pressing)
    assert made["times"] == 1
    assert len({clip.state for clip in clips}) == 1


async def test_a_screen_that_gives_up_does_not_take_the_clip_with_it():
    # A wall screen navigating away mid-generation used to cancel the task
    # every other screen was awaiting.
    started = asyncio.Event()
    holding = asyncio.Event()
    generate, made = a_clip()

    async def slow(podiums):
        started.set()
        await holding.wait()
        return await generate(podiums)

    talking = announcer.Announcer(generate=slow)
    gone = asyncio.create_task(talking.clip(PODIUMS))
    staying = asyncio.create_task(talking.clip(PODIUMS))
    await started.wait()
    gone.cancel()
    holding.set()
    assert (await staying).seconds == 1.0
    assert made["times"] == 1


async def test_the_last_two_clips_are_kept_and_the_third_is_not():
    # Two, so that a podium changing while a screen is mid-download does not
    # 404 the file it is already fetching.
    generate, _ = a_clip()
    talking = announcer.Announcer(generate=generate)
    first = await talking.clip(PODIUMS)
    second = await talking.clip({**PODIUMS, "score_attack": [{"name": "Jo"}]})
    third = await talking.clip({**PODIUMS, "score_attack": [{"name": "Kim"}]})
    assert talking.ready(first.state) is None
    assert talking.ready(second.state) is second
    assert talking.ready(third.state) is third


async def test_a_clip_knows_when_the_second_board_starts():
    # Apportioned by words against the clip's real length. Four words then
    # two, over one second, puts the turn two thirds of the way in.
    generate, _ = a_clip(words={"solo": "one two three four", "versus": "five six"})
    clip = await announcer.Announcer(generate=generate).clip(PODIUMS)
    assert clip.seconds == 1.0
    assert clip.switch_at == 0.67


async def test_a_clip_carries_a_file_and_its_two_halves():
    generate, _ = a_clip()
    clip = await announcer.Announcer(generate=generate).clip(PODIUMS)
    assert clip.wav.startswith(b"RIFF")
    assert clip.script["solo"] == "one two three four"
    assert clip.state == announcer.fingerprint(PODIUMS)


async def test_a_generation_that_hangs_is_given_up_on(monkeypatch):
    monkeypatch.setattr(announcer, "SECONDS", 0.01)

    async def forever(podiums):
        await asyncio.sleep(5)

    with pytest.raises(announcer.Silent):
        await announcer.Announcer(generate=forever).clip(PODIUMS)


async def test_a_failed_generation_is_not_remembered_as_a_clip():
    tries = {"times": 0}

    async def flaky(podiums):
        tries["times"] += 1
        if tries["times"] == 1:
            raise announcer.Silent("no")
        return b"\x00\x01" * 24_000, {"solo": "one", "versus": "two"}

    talking = announcer.Announcer(generate=flaky)
    with pytest.raises(announcer.Silent):
        await talking.clip(PODIUMS)
    # The second press must be allowed to try again.
    assert (await talking.clip(PODIUMS)).seconds == 1.0


async def test_two_waiters_on_a_failing_generation_both_receive_silent():
    # The shield-plus-exception fan-out: a shared generation that fails must
    # raise to every waiter, not just the first.
    started = asyncio.Event()
    holding = asyncio.Event()

    async def broken(podiums):
        started.set()
        await holding.wait()
        raise announcer.Silent("the model refused")

    talking = announcer.Announcer(generate=broken)
    first = asyncio.create_task(talking.clip(PODIUMS))
    second = asyncio.create_task(talking.clip(PODIUMS))
    await started.wait()
    holding.set()
    with pytest.raises(announcer.Silent):
        await first
    with pytest.raises(announcer.Silent):
        await second


def test_the_deadlines_are_longer_than_a_clip_takes_to_make():
    """Fifty-seven seconds, measured against the real models, TTS the long pole.

    Shipped at thirty, every press at a venue ran the script call, started the
    speech call and was cancelled halfway through it: half a minute of "Warming
    up", then a failure, nothing cached, and a TTS generation billed and thrown
    away. `test_a_real_clip_lands_in_the_forty_second_window` is where the
    number came from and is what would correct it.
    """
    assert announcer.TIMEOUT.read >= 60
    assert announcer.SECONDS >= 60


async def test_a_press_queued_behind_another_podium_still_gets_its_clip(monkeypatch):
    """The two deadlines bound different things and cannot be the same number.

    The inner one starts when a generation takes the slot; the outer one starts
    when somebody presses. A second podium spends the first clip's whole
    generation queueing, so an outer deadline the size of the inner one refuses
    it before its own work has begun.
    """
    monkeypatch.setattr(announcer, "SECONDS", 0.9)
    monkeypatch.setattr(announcer, "WAITING_SECONDS", 10.0)

    async def unhurried(podiums):
        # Comfortably inside one generation's budget, and two of them in
        # series are not: which is the whole of the difference being tested.
        await asyncio.sleep(0.5)
        return b"\x00\x01" * 24_000, {"solo": "one two", "versus": "three"}

    talking = announcer.Announcer(generate=unhurried)
    first = asyncio.create_task(talking.clip(PODIUMS))
    behind = asyncio.create_task(talking.clip({**PODIUMS, "score_attack": [{"name": "Jo"}]}))
    assert (await first).seconds == 1.0
    assert (await behind).seconds == 1.0


async def test_the_semaphore_is_released_after_a_timeout(monkeypatch):
    # A timeout that wedges the slot would starve every subsequent press.
    monkeypatch.setattr(announcer, "SECONDS", 0.01)

    async def forever(podiums):
        await asyncio.sleep(5)

    talking = announcer.Announcer(generate=forever)
    with pytest.raises(announcer.Silent):
        await talking.clip(PODIUMS)
    # The second press must not be starved by the first.
    with pytest.raises(announcer.Silent):
        await talking.clip({**PODIUMS, "score_attack": [{"name": "Jo"}]})


@pytest.mark.e2e
@pytest.mark.timeout(90)
async def test_a_real_clip_lands_in_the_forty_second_window(monkeypatch):
    """The one test here that spends money. Run it when the prompt changes.

    The design budgets 120 to 135 words on the arithmetic that the model
    speaks at about 150 to 165 wpm and the room hears it at 1.25x. Nobody has
    measured this model. This is where that estimate is either confirmed or
    corrected - and if it is wrong, the fix is the word budget in
    SHOUTCASTER, not the window below.
    """
    import subprocess

    if not announcer.configured():
        # Set the values directly if the module did not pick them up from .env.
        env_enabled = os.environ.get("ARENA_ANNOUNCER") == "1"
        env_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not env_enabled or not env_project:
            pytest.skip("set ARENA_ANNOUNCER=1 and GOOGLE_CLOUD_PROJECT")
        monkeypatch.setattr(announcer, "ENABLED", True)
        monkeypatch.setattr(announcer, "PROJECT", env_project)

    # Ensure LOCATION is set to global for the corrected endpoint.
    monkeypatch.setattr(announcer, "LOCATION", "global")

    # Nothing patches `TIMEOUT` here. This run is what sized it: production
    # already waits long enough for the clip this test is about to ask for, and
    # a test that quietly widened the budget would be measuring a venue that
    # does not exist.

    # Mint a token from ADC. This is a test-only path; production uses the
    # metadata server or GEMINI_API_KEY.
    try:
        token_result = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, check=True, timeout=10)
        token = token_result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("install gcloud and run: gcloud auth application-default login")

    async def adc_token():
        return token

    monkeypatch.setattr(announcer, "_token", adc_token)

    full = {"score_attack": [
                {"name": "Alex Rivera", "points": 41, "goals_for": 5,
                 "goals_against": 1, "first_goal_seconds": 42, "shouts": 3,
                 "shouts_that_worked": 2},
                {"name": "Sam Okafor", "points": 38, "goals_for": 4,
                 "goals_against": 2, "first_goal_seconds": 88, "shouts": 1,
                 "shouts_that_worked": 0},
                {"name": "Priya Raman", "points": 31, "goals_for": 3,
                 "goals_against": 3, "first_goal_seconds": None, "shouts": 0,
                 "shouts_that_worked": 0}],
            "head_to_head": [
                {"name": "Kim Park", "played": 5, "won": 5, "drew": 0,
                 "lost": 0, "goal_difference": 11},
                {"name": "Jo Meyer", "played": 5, "won": 4, "drew": 0,
                 "lost": 1, "goal_difference": 6},
                {"name": "Lee Novak", "played": 4, "won": 2, "drew": 1,
                 "lost": 1, "goal_difference": 2}]}

    words = await announcer.script(full)
    said = f"{words['solo']} {words['versus']}"
    counted = len(said.split())
    played = announcer.seconds(await announcer.speak(said)) / 1.25

    print(f"\n{counted} words, {played:.1f}s played\n\n{said}\n")
    assert 110 <= counted <= 150, f"the model wrote {counted} words"
    assert 32 <= played <= 48, f"the room would hear {played:.1f} seconds"
