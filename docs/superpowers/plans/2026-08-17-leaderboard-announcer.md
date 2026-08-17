# Leaderboard Announcer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A button on the arena's lobby that has an over-the-top esports shoutcaster read the top three of both standings out loud, in about forty seconds.

**Architecture:** One new module, `arena/announcer.py`, turns the two podiums into a WAV clip with two REST calls to Vertex: a flash model writes the script, a TTS model speaks it as Puck. The module is guarded by single-flight, a two-clip cache, a podium fingerprint and a timeout, so a room full of screens pressing at once costs one generation. Two endpoints serve it, and the big screen plays it at 1.25x with captions while the board frame follows the commentary.

**Tech Stack:** Python 3.14, FastAPI, `httpx` (async, no `google-genai`), stdlib `wave` and `hashlib`, vanilla ES modules, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-17-leaderboard-announcer-design.md`

## Global Constraints

- **Never use the em dash character.** Plain dash only. This applies to code, comments, docs and prompt text.
- **No new runtime dependencies.** `arena/pyproject.toml` stays as it is. Vertex is reached over REST with `httpx`; PCM is wrapped with the stdlib `wave` module.
- **Off unless configured.** `ARENA_ANNOUNCER=1` plus either `GOOGLE_CLOUD_PROJECT` or `GEMINI_API_KEY`. Off means the endpoint answers 503 and the button is never rendered.
- **Comments explain why, not what.** Match the surrounding files: `arena/intent.py` and `arena/board.py` are the reference for tone and density.
- **Tests never touch the network** except the one opt-in measurement in Task 8, which is marked and skipped by default.
- **Model defaults:** script `gemini-3.6-flash`, speech `gemini-3.1-flash-tts-preview`, voice `Puck`, location `us-central1`.
- **Audio format is fixed by the model:** signed 16-bit little-endian, mono, 24000 Hz, no header.
- **Word budget:** 120 to 135 words, roughly 60/40 across the two boards.
- Run tests from `arena/` with `uv run pytest`. The suite is held at one warning and `addopts = "-m 'not e2e'"`.

---

## File Structure

| File | Responsibility |
|---|---|
| `arena/announcer.py` (new) | Everything between a pair of podiums and a playable clip: trimming rows to what is speakable, fingerprinting, the two prompts, the two REST calls, the WAV header, the cache and the single-flight. |
| `arena/tests/test_announcer.py` (new) | The module, offline, with a fake generator. |
| `arena/tests/test_announcing.py` (new) | The two endpoints and the big screen, including the Playwright pass. |
| `arena/app.py` | Two endpoints, one venue flag, one bucket, one line of lifespan state. |
| `arena/static/arena.html` | The button, the level meter and the caption card. |
| `arena/static/arena.js` | The press, the autoplay unlock, playback, captions, the message to the frame. |
| `arena/static/board.js` | Receive that message, pin the board, stop and restart its own cycle. |
| `arena/static/app.css` | The chip, the ON AIR pill, the meter, the caption card. |
| `arena/README.md`, `arena/.env.example`, `deploy/service.yaml` | Configuration, endpoints, and the quota arithmetic the announcer changes. |

---

### Task 1: The clip's identity and its container

Pure functions, no network, no state. Everything later tasks build on.

**Files:**
- Create: `arena/announcer.py`
- Test: `arena/tests/test_announcer.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `spoken(solo_rows, versus_rows) -> dict`, `fingerprint(podiums) -> str`, `as_wav(pcm: bytes) -> bytes`, `seconds(pcm: bytes) -> float`, `PROMPT_VERSION: int`, `SAMPLE_RATE: int`.

- [ ] **Step 1: Write the failing tests**

Create `arena/tests/test_announcer.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd arena && uv run pytest tests/test_announcer.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'announcer'`

- [ ] **Step 3: Write the module**

Create `arena/announcer.py`:

```python
"""The standings, read out loud by somebody far too excited about them.

The board is the best thing in the venue and it spends the evening in an
iframe under the lobby, cycling silently at the size of a paperback. This
gives it a voice: one press on the big screen, and the top three of both
boards come back as forty seconds of shoutcaster.

Two model calls, because they are two different jobs. A flash model turns the
rows into a script -- which manager to build to, which gap is worth a gasp --
and a speech model reads that script in one take. Neither is asked to do the
other's work.

Reached over REST with `httpx` and no client library, the way `intent.py`
reaches Vertex, and off unless configured for the same reason. A wall screen
carrying a button that cannot work is worse than one carrying no button.
"""

import hashlib
import io
import json
import os
import wave

# Bumped by hand whenever a prompt below changes. It is inside the
# fingerprint, so moving it retires every clip the old wording produced
# rather than leaving yesterday's script playing under today's prompt.
PROMPT_VERSION = 1

# What the TTS model returns, and not a choice: signed 16-bit little-endian
# mono at this rate, with no header on it.
SAMPLE_RATE = 24_000
SAMPLE_BYTES = 2


def spoken(solo_rows, versus_rows):
    """The two podiums, trimmed to what somebody could read off a screen.

    A trim rather than the rows as they come. A board row carries a player id
    and a masked address, and neither belongs in a prompt: the id says nothing
    out loud and the address is not ours to hand to a model. What is left is
    the numbers a commentator would actually reach for.
    """
    return {
        "score_attack": [
            {"name": row["name"],
             "points": row["points"],
             "goals_for": row["goals_for"],
             "goals_against": row["goals_against"],
             # Seconds, because a script says "in the first minute" and never
             # says "at forty-two thousand milliseconds".
             "first_goal_seconds": (None if row["first_goal_ms"] is None
                                    else round(row["first_goal_ms"] / 1000)),
             "shouts": row["shouts"],
             "shouts_that_worked": row["effective"]}
            for row in solo_rows],
        "head_to_head": [
            {"name": row["name"], "played": row["played"], "won": row["won"],
             "drew": row["drew"], "lost": row["lost"],
             "goal_difference": row["difference"]}
            for row in versus_rows],
    }


def fingerprint(podiums):
    """A name for this pair of podiums, stable across everything else.

    Only what gets spoken goes in, so a manager climbing from ninth to eighth
    does not retire a clip nobody could tell apart, and a podium changing
    retires it at once.
    """
    said = json.dumps(podiums, sort_keys=True, default=str)
    return hashlib.sha256(f"{PROMPT_VERSION}:{said}".encode()).hexdigest()[:16]


def as_wav(pcm):
    """Raw samples, plus the forty-four bytes that make them a file.

    That header is the whole conversion. No resampling, no transcode, and no
    ffmpeg in a container that has never needed one.
    """
    holding = io.BytesIO()
    with wave.open(holding, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(SAMPLE_BYTES)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return holding.getvalue()


def seconds(pcm):
    """How long these samples take to play at 1x, on the media clock."""
    return len(pcm) / (SAMPLE_RATE * SAMPLE_BYTES)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_announcer.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add arena/announcer.py arena/tests/test_announcer.py
git commit -m "feat(arena): the shape of a clip, before anything makes one"
```

---

### Task 2: The two model calls

Config detection, both prompts, both request bodies, both response shapes. Still no cache and no state.

**Files:**
- Modify: `arena/announcer.py`
- Modify: `arena/tests/test_announcer.py`
- Modify: `arena/.env.example`

**Interfaces:**
- Consumes: Task 1's `PROMPT_VERSION`, `SAMPLE_RATE`.
- Produces: `configured() -> bool`, `Silent` (Exception), `async script(podiums, call=_post) -> dict` returning `{"solo": str, "versus": str}`, `async speak(words, call=_post) -> bytes` returning raw PCM, `async _post(model, body) -> dict`, and the module constants `ENABLED`, `PROJECT`, `API_KEY`, `LOCATION`, `SCRIPT_MODEL`, `TTS_MODEL`, `VOICE`.

- [ ] **Step 1: Write the failing tests**

Append to `arena/tests/test_announcer.py`:

```python
import base64
import json

import pytest

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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd arena && uv run pytest tests/test_announcer.py -v`
Expected: FAIL, `AttributeError: module 'announcer' has no attribute 'configured'`

- [ ] **Step 3: Add the calls to `arena/announcer.py`**

Add to the imports at the top: `import base64`, `import logging`, `import httpx`. Then append:

```python
logger = logging.getLogger(__name__)

# On at a deployed venue, and off anywhere it has not been asked for: a
# laptop, a test run, CI. Reading the board out loud costs money and makes a
# noise, so it is opted into rather than inherited by starting up.
ENABLED = os.environ.get("ARENA_ANNOUNCER") == "1"

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Regional, not global. Speech models are served from a region the way the
# embedding model is and unlike the image model, and `global` answers 404.
LOCATION = os.environ.get("ARENA_ANNOUNCER_LOCATION", "us-central1")

# Deliberately not the chain's `gemini-3.5-flash-lite`. A different base model
# is a different quota bucket, so a room enjoying the announcer cannot take
# slots away from managers shouting at their squads.
SCRIPT_MODEL = os.environ.get("ARENA_ANNOUNCER_MODEL", "gemini-3.6-flash")
TTS_MODEL = os.environ.get("ARENA_TTS_MODEL", "gemini-3.1-flash-tts-preview")
VOICE = os.environ.get("ARENA_TTS_VOICE", "Puck")

# Speech synthesis of forty seconds of audio, and the script call before it.
# `intent.py` waits five for an embedding, which is right there and far too
# short here.
TIMEOUT = httpx.Timeout(30.0)


class Silent(Exception):
    """No clip could be made. The text is fit for a screen to show."""


def configured():
    """Whether this can run at all: asked for, and with somewhere to call."""
    return bool(ENABLED and (PROJECT or API_KEY))


SHOUTCASTER = """\
You are an over-the-top, high-octane esports shoutcaster live on stage at a
futsal tournament, reading the leaderboard to a room full of people.

Take the two boards below and write ONE announcement covering both.

RULES
1. Length. 120 to 135 words in total, split roughly 60/40 across the two
   boards. Count them. Words are the only length instruction here.
2. Dynamic adaptation. Compare the numbers and say what they mean. A gap of
   one point, a leader who has not lost, somebody knocked off the top, a goal
   in the first minute, a manager whose shouts actually worked. Never read the
   table out.
3. Build. Third place gets a clause, second gets a sentence, first gets the
   roof coming off. Twice, once per board.
4. Audio cues. [excitedly], [gasp], [pause], six to eight in total across the
   whole script, and ALL CAPS on the names and numbers you want hit hard.
5. Names. Say a gamertag the way a person would: "xX_Hero_Xx" is "Hero".
   Never speak punctuation or underscores.
6. Numbers. Spelled out. FORTY-ONE, not 41.
7. If a board has fewer than three managers, cover who is there and spend the
   words you save on the other board.

`solo` is the score attack board, against the house side. `versus` is manager
against manager. Return only the JSON."""

# Google's documented shape for this model: who is talking, where they are,
# and how to play it.
DIRECTION = """\
Audio profile: a male esports shoutcaster, mid-thirties, hand mic, big room.
Scene: a packed futsal venue, the leaderboard on the screen behind you.
Director's notes: high energy from the first word, tempo and pitch climbing
into each number one. Hit the words in capitals. Honour the bracketed cues.
Say this: """


async def script(podiums, call=None):
    """The two boards as something worth listening to, in two halves.

    Split rather than one string because the halves are what the screen shows
    as captions and what tells it when to turn the board over.
    """
    call = call or _post
    answer = await call(SCRIPT_MODEL, {
        "systemInstruction": {"parts": [{"text": SHOUTCASTER}]},
        "contents": [{"role": "user",
                      "parts": [{"text": json.dumps(podiums, default=str)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {"solo": {"type": "STRING"},
                               "versus": {"type": "STRING"}},
                "required": ["solo", "versus"],
            },
        },
    })
    try:
        words = json.loads(_part(answer)["text"])
        return {"solo": str(words["solo"]), "versus": str(words["versus"])}
    except (KeyError, IndexError, TypeError, ValueError) as nonsense:
        # A schema makes this unlikely rather than impossible, and a screen
        # that says nothing is a better failure than one that plays a
        # stack trace.
        raise Silent("the announcer could not think of anything to say") from nonsense


async def speak(words, call=None):
    """One take of that script, as raw samples. Puck, unless told otherwise."""
    call = call or _post
    answer = await call(TTS_MODEL, {
        # One field, not two: Vertex concatenates the direction and the script
        # for this model rather than taking them separately.
        "contents": [{"role": "user", "parts": [{"text": f"{DIRECTION}{words}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE}}},
        },
    })
    try:
        return base64.b64decode(_part(answer)["inlineData"]["data"])
    except (KeyError, IndexError, TypeError, ValueError) as nothing:
        raise Silent("the announcer lost its voice") from nothing


def _part(answer):
    """The one part of the one candidate every answer here has."""
    return answer["candidates"][0]["content"]["parts"][0]


async def _reach(model, token=None):
    """Where to send this, and what to send with it.

    Two ways in, decided by what is set. A deployed venue has a service
    account and no metadata-free way of proving it, so it takes a token off
    the metadata server the way `intent.py` does. A laptop has a key and no
    metadata server at all, which is the case `intent.py` never had to serve
    and the reason this one does: a feature judged by ear that can only be
    heard in production is a feature nobody will ever tune.
    """
    if API_KEY:
        return (f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent",
                {"x-goog-api-key": API_KEY})
    fetched = await (token or _token)()
    return (f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
            f"/locations/{LOCATION}/publishers/google/models/{model}"
            f":generateContent",
            {"Authorization": f"Bearer {fetched}"})


async def _token():
    """An access token from the instance's metadata server."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        reply = await http.get(
            "http://metadata.google.internal/computeMetadata/v1/"
            "instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"})
        reply.raise_for_status()
        return reply.json()["access_token"]


async def _post(model, body):
    """One generateContent, or a silence with a reason attached."""
    try:
        url, headers = await _reach(model)
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            reply = await http.post(url, headers=headers, json=body)
            reply.raise_for_status()
            return reply.json()
    except httpx.HTTPError as problem:
        logger.warning("the announcer could not reach %s: %s", model, problem)
        raise Silent("the announcer could not be reached") from problem
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_announcer.py -v`
Expected: PASS, 21 passed

- [ ] **Step 5: Document the five variables**

Add to `arena/.env.example`, following the commenting style already there:

```bash
# Reading the standings out loud on the big screen. Off unless this is 1: it
# costs two model calls a press and makes a noise in the room, so it is opted
# into rather than inherited. Needs either GOOGLE_CLOUD_PROJECT (a deployed
# venue, which takes a token off the metadata server) or GEMINI_API_KEY (a
# laptop, which has no metadata server to ask).
# ARENA_ANNOUNCER=1
# Writes the script. Deliberately not the chain's model: a different base
# model is a different quota bucket, so the announcer cannot take a slot away
# from somebody shouting at their squad.
# ARENA_ANNOUNCER_MODEL=gemini-3.6-flash
# Speaks it, and the voice it uses. Thirty prebuilt voices exist; Puck is the
# high-energy one.
# ARENA_TTS_MODEL=gemini-3.1-flash-tts-preview
# ARENA_TTS_VOICE=Puck
# Speech models are served regionally, not from `global`, the same way the
# embedding model is.
# ARENA_ANNOUNCER_LOCATION=us-central1
```

- [ ] **Step 6: Commit**

```bash
git add arena/announcer.py arena/tests/test_announcer.py arena/.env.example
git commit -m "feat(arena): a script, and a voice to read it in"
```

---

### Task 3: One generation, however many screens press

The cache, the single-flight and the switch point. This is where the guards the spec promised actually exist.

**Files:**
- Modify: `arena/announcer.py`
- Modify: `arena/tests/test_announcer.py`

**Interfaces:**
- Consumes: Task 1's `fingerprint`, `as_wav`, `seconds`; Task 2's `script`, `speak`, `Silent`.
- Produces: `Clip` (frozen dataclass with fields `state: str`, `wav: bytes`, `seconds: float`, `switch_at: float`, `script: dict`), `Announcer(generate=None)` with `async clip(podiums) -> Clip` and `ready(state) -> Clip | None`, and module constant `KEEP: int`.

- [ ] **Step 1: Write the failing tests**

Append to `arena/tests/test_announcer.py`:

```python
import asyncio


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd arena && uv run pytest tests/test_announcer.py -v`
Expected: FAIL, `AttributeError: module 'announcer' has no attribute 'Announcer'`

- [ ] **Step 3: Add the cache and the single-flight**

Add `import asyncio` and `import dataclasses` to the imports. Append to `arena/announcer.py`:

```python
# The current clip and the one before it. Two rather than one so that a podium
# changing while a screen is mid-download does not 404 the file it is already
# fetching. About five megabytes at the top end, against eight gigabytes.
KEEP = 2

# Both calls together. A press that has not made a sound in half a minute has
# failed whatever the model eventually says.
SECONDS = 30.0


@dataclasses.dataclass(frozen=True)
class Clip:
    """One announcement: the file, its two halves, and where they meet."""

    state: str
    wav: bytes
    seconds: float
    switch_at: float
    script: dict


class Announcer:
    """One generation at a time, and the last two kept.

    Every screen pressing the button wants the identical clip, because a clip
    is a pure function of the podiums. So a second press while the first is
    still being made joins it rather than starting another: the guard is the
    correct semantics here, not a throttle bolted onto them.
    """

    def __init__(self, generate=None):
        self._generate = generate or _generate
        self._clips = {}        # fingerprint -> Clip, oldest first
        self._making = {}       # fingerprint -> Task
        self._slot = asyncio.Semaphore(1)

    def ready(self, state):
        """The clip with this name, if it is still one of the two kept."""
        return self._clips.get(state)

    async def clip(self, podiums):
        """The clip for these podiums, made or remembered."""
        state = fingerprint(podiums)
        if state in self._clips:
            return self._clips[state]
        if state not in self._making:
            self._making[state] = asyncio.ensure_future(self._make(state, podiums))
        # Shielded, so a screen that navigates away mid-generation cancels its
        # own wait and not everybody else's clip.
        return await asyncio.shield(self._making[state])

    async def _make(self, state, podiums):
        try:
            async with self._slot:
                # Checked again inside the slot: a press that queued behind
                # another may have been for a podium that has since been made.
                if state in self._clips:
                    return self._clips[state]
                async with asyncio.timeout(SECONDS):
                    pcm, words = await self._generate(podiums)
                whole = seconds(pcm)
                clip = Clip(state=state, wav=as_wav(pcm), seconds=round(whole, 2),
                            switch_at=_switch(whole, words), script=words)
                self._keep(clip)
                return clip
        except TimeoutError as slow:
            raise Silent("the announcer took too long and was cut off") from slow
        finally:
            # Failures included, so the next press is allowed to try again.
            self._making.pop(state, None)

    def _keep(self, clip):
        self._clips[clip.state] = clip
        while len(self._clips) > KEEP:
            # Insertion-ordered, so the first key is the oldest clip.
            self._clips.pop(next(iter(self._clips)))


async def _generate(podiums):
    """The two calls, in the only order they can happen in."""
    words = await script(podiums)
    return await speak(f"{words['solo']} {words['versus']}"), words


def _switch(whole, words):
    """When the second board's half begins, near enough to turn a board over.

    Apportioned by word count against the clip's real length. That is accurate
    to a second or so, which is the tolerance for swapping a board and is not
    the tolerance for lighting up a word -- so nothing here lights up a word.
    The model returns no timings and an estimate fine enough to look precise
    would look broken the moment it drifted.
    """
    solo = len(words["solo"].split())
    both = solo + len(words["versus"].split())
    return round(whole * solo / both, 2) if both else round(whole, 2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_announcer.py -v`
Expected: PASS, 29 passed

- [ ] **Step 5: Commit**

```bash
git add arena/announcer.py arena/tests/test_announcer.py
git commit -m "feat(arena): twenty screens, one generation"
```

---

### Task 4: The two endpoints

**Files:**
- Modify: `arena/app.py` (imports, rate constants near `MAX_BOARD_ROWS` at line 232, lifespan state at line 319, `read_venue` at line 706, new endpoints after `read_board` at line 953)
- Create: `arena/tests/test_announcing.py`
- Modify: `arena/README.md`

**Interfaces:**
- Consumes: Task 3's `Announcer`, `Clip`; Task 1's `spoken`; Task 2's `configured`, `Silent`.
- Produces: `POST /api/board/announcement`, `GET /api/board/announcement/{state}.wav`, and `"announcer"` in the `/api/venue` body. `app.state.announcer` is the `Announcer` instance; `app.state.announcements` is the rate bucket.

- [ ] **Step 1: Write the failing tests**

Create `arena/tests/test_announcing.py`:

```python
"""The button on the big screen, and what answers it."""

import io
import wave

import pytest

import announcer


@pytest.fixture
def switched_on(monkeypatch):
    """The announcer configured, with a model that costs nothing."""
    monkeypatch.setattr(announcer, "ENABLED", True)
    monkeypatch.setattr(announcer, "API_KEY", "a-key")

    async def generate(podiums):
        return b"\x00\x01" * 24_000, {"solo": "one two three four",
                                      "versus": "five six"}

    monkeypatch.setattr(announcer, "_generate", generate)


def ranked(client, phones, mode="solo"):
    """One finished, ranked match, so there is somebody on the board."""
    from tests.conftest import play_a_ranked_match

    return play_a_ranked_match(client, phones, mode)


def test_the_venue_says_the_announcer_is_off_when_it_is(client):
    assert client.get("/api/venue").json()["announcer"] is False


def test_the_venue_says_it_is_on_when_it_is(client, switched_on):
    assert client.get("/api/venue").json()["announcer"] is True


def test_an_unconfigured_venue_refuses_rather_than_pretends(client):
    answer = client.post("/api/board/announcement")
    assert answer.status_code == 503
    assert "not switched on" in answer.json()["detail"]


def test_an_empty_board_has_nothing_to_announce(client, switched_on):
    answer = client.post("/api/board/announcement")
    assert answer.status_code == 409
    assert "nobody" in answer.json()["detail"]


def test_a_press_comes_back_with_a_clip_to_play(client, phones, switched_on):
    ranked(client, phones)
    answer = client.post("/api/board/announcement").json()
    assert answer["seconds"] == 1.0
    assert answer["switch_at"] == 0.67
    assert answer["script"]["solo"] == "one two three four"
    assert answer["audio"] == f"/api/board/announcement/{answer['state']}.wav"


def test_the_audio_is_a_file_a_browser_will_play(client, phones, switched_on):
    ranked(client, phones)
    made = client.post("/api/board/announcement").json()
    answer = client.get(made["audio"])
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/wav"
    parsed = wave.open(io.BytesIO(answer.content), "rb")
    assert parsed.getframerate() == 24_000


def test_a_clip_is_cached_by_the_thing_it_is_about(client, phones, switched_on):
    # The fingerprint is in the path, so it can be cached forever: a new
    # podium is a new path rather than a new body at the old one.
    ranked(client, phones)
    made = client.post("/api/board/announcement").json()
    headers = client.get(made["audio"]).headers
    assert "immutable" in headers["cache-control"]
    assert "max-age=31536000" in headers["cache-control"]


def test_an_announcement_that_has_been_replaced_says_so(client, switched_on):
    answer = client.get("/api/board/announcement/nosuchclip.wav")
    assert answer.status_code == 404
    assert "replaced" in answer.json()["detail"]


def test_a_model_that_fails_leaves_the_lobby_working(client, phones, switched_on,
                                                     monkeypatch):
    async def broken(podiums):
        raise announcer.Silent("the announcer lost its voice")

    monkeypatch.setattr(announcer, "_generate", broken)
    ranked(client, phones)
    answer = client.post("/api/board/announcement")
    assert answer.status_code == 503
    assert answer.json()["detail"] == "the announcer lost its voice"
    # The board itself is untouched by any of this.
    assert client.get("/api/board").status_code == 200


def test_a_button_held_down_is_refused_before_it_reaches_a_model(
        client, phones, switched_on, monkeypatch):
    monkeypatch.setattr("app.ANNOUNCE_BURST", 2)
    ranked(client, phones)
    codes = [client.post("/api/board/announcement").status_code for _ in range(6)]
    assert 429 in codes
```

Add this helper to `arena/tests/conftest.py`, beside the other fixtures, so both this file and Task 8 can use it:

```python
def play_a_ranked_match(client, phones, mode="solo"):
    """One finished, ranked match, so the boards have somebody on them.

    The shortest route to a scored result: a seat, a kickoff, a goal and a
    whistle. `board.top` reads what this leaves behind.
    """
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": mode}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue",
                json={"philosophy": "high press"})
    client.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    client.post(f"/api/rooms/{code}/start")
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        socket.send_json({"type": "host.event", "kind": "goal", "match_ms": 42_000,
                          "payload": {"team": "blue", "score": [1, 0]}})
        socket.send_json({"type": "host.event", "kind": "full_time",
                          "match_ms": 180_000,
                          "payload": {"score": [1, 0]}})
    return code
```

> **Note for the implementer:** the exact seat, start and host-token mechanics
> vary by mode. Read `arena/tests/test_board.py:29` (`played`) and
> `arena/tests/conftest.py`'s `phones` fixture first and mirror whichever
> route they already use; the helper above is the shape, not necessarily the
> literal calls. If `test_board.py`'s `played()` already does this against a
> connection rather than a client, prefer calling the existing helper over
> writing a second one.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd arena && uv run pytest tests/test_announcing.py -v`
Expected: FAIL, `KeyError: 'announcer'` on the first test

- [ ] **Step 3: Wire the endpoints into `arena/app.py`**

Add `import announcer` to the imports beside `import board`.

Beside `MAX_BOARD_ROWS = 100` at line 232:

```python
# Two model calls a press, so this is not sized for a flood, it is sized for
# a person. A screen legitimately presses this once and then not again until
# somebody has finished a match.
ANNOUNCE_RATE = 0.1
ANNOUNCE_BURST = 4
```

In `lifespan`, beside the other buckets at line 319:

```python
    fastapi_app.state.announcements = limits.Bucket(ANNOUNCE_RATE, ANNOUNCE_BURST)
    # Per app rather than per module, for the same reason the buckets above
    # are: one instance means per app is per process anyway, and a test's
    # client gets a cache of its own.
    fastapi_app.state.announcer = announcer.Announcer()
```

In `read_venue` at line 706:

```python
@app.get("/api/venue")
async def read_venue(request: Request):
    """Where the other halves of the venue live, for the pages to link to."""
    return {"pitch_url": PITCH_URL, "public_url": _origin(request),
            # So the big screen can leave the button out entirely rather than
            # render a control that answers 503.
            "announcer": announcer.configured()}
```

After `read_board` at line 953:

```python
@app.post("/api/board/announcement")
async def announce_the_board(request: Request):
    """Make, or hand back, the clip for the standings as they are right now.

    The work is two awaited model calls and a memcpy, and `Announcer` holds it
    to one generation at a time across the whole venue, so this sits in the
    arena rather than in a service of its own. See the design note.
    """
    if not announcer.configured():
        raise HTTPException(503, "the announcer is not switched on at this venue")
    if not request.app.state.announcements.take(limits.client_ip(request)):
        raise HTTPException(429, "that has been pressed a lot; give it a moment")
    connection = request.app.state.conn
    podiums = announcer.spoken(board.top(connection, "solo"),
                               board.top(connection, "versus"))
    if not podiums["score_attack"] and not podiums["head_to_head"]:
        raise HTTPException(409, "nobody is on the board yet, so there is "
                                 "nothing to announce")
    try:
        clip = await request.app.state.announcer.clip(podiums)
    except announcer.Silent as quiet:
        raise HTTPException(503, str(quiet)) from quiet
    return {"state": clip.state, "seconds": clip.seconds,
            "switch_at": clip.switch_at, "script": clip.script,
            "audio": f"/api/board/announcement/{clip.state}.wav"}


@app.get("/api/board/announcement/{state}.wav")
async def read_announcement(state: str, request: Request):
    """One clip's bytes. Cacheable forever, because the name is the content."""
    clip = request.app.state.announcer.ready(state)
    if clip is None:
        raise HTTPException(404, "that announcement has been replaced by a newer one")
    return Response(clip.wav, media_type="audio/wav",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd arena && uv run pytest tests/test_announcing.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Run the whole suite, because `/api/venue` grew a field**

Run: `cd arena && uv run pytest`
Expected: PASS. If a test asserts on the exact shape of the venue body, update it to allow the new key rather than removing the key.

- [ ] **Step 6: Add the two rows to the README endpoints table**

In `arena/README.md`, in the Endpoints table after the `/api/attributes` row:

```markdown
| POST | `/api/board/announcement` | anyone, rate-limited - makes or hands back the spoken standings; 503 when the announcer is off, 409 when nobody is ranked yet |
| GET | `/api/board/announcement/{state}.wav` | anyone - one clip's bytes, cacheable forever because the name is a fingerprint of what it says |
```

- [ ] **Step 7: Commit**

```bash
git add arena/app.py arena/tests/test_announcing.py arena/tests/conftest.py arena/README.md
git commit -m "feat(arena): a way to ask for the board out loud"
```

---

### Task 5: The button, and getting a sound out of a wall screen

**Files:**
- Modify: `arena/static/arena.html:57-78` (the `pitch-stack` block)
- Modify: `arena/static/arena.js` (imports at line 17, the venue fetch at line 93, the board src at line 210)
- Modify: `arena/static/app.css` (after `.board-frame` at line 841)
- Modify: `arena/tests/test_announcing.py`

**Interfaces:**
- Consumes: Task 4's `POST /api/board/announcement`, its response body, and the `announcer` flag on `/api/venue`.
- Produces: DOM ids `announce`, `announce-say`, `caption`; CSS classes `mic-chip`, `mic-chip.live`, `levels`, `caption`, `board-box`.

- [ ] **Step 1: Write the failing tests**

Append to `arena/tests/test_announcing.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd arena && uv run pytest tests/test_announcing.py -v -k "button or clip_is_played or unlocked or announcer"`
Expected: FAIL on the assertion, the button not being in the page

- [ ] **Step 3: Add the markup**

In `arena/static/arena.html`, replace the bare iframe at line 61 with a box that can hold a control over it:

```html
      <!-- The frame, and the two things that sit on top of it while the
           announcer is talking. The button is out here rather than inside
           `/board`, because the page in that frame is also what a phone opens
           from home and a leaderboard that starts shouting in somebody's
           pocket is a different product. -->
      <div class="board-box">
        <iframe class="board-frame" id="board" title="The standings" loading="lazy"></iframe>
        <button class="mic-chip" id="announce" type="button" hidden>
          <i></i><span id="announce-say">Read the board</span>
          <b class="levels" aria-hidden="true"><s></s><s></s><s></s><s></s></b>
        </button>
        <p class="caption" id="caption" hidden></p>
      </div>
```

- [ ] **Step 4: Add the press handler**

In `arena/static/arena.js`, beside the other constants near line 33:

```js
// What the room hears it at. The model speaks at a natural pace and a
// shoutcaster does not, so the last quarter of the energy is put on here
// rather than asked of the model.
const RATE = 1.25;
// A forty-four byte WAV with no samples in it. See `unlock`.
const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEA"
  + "RKwAAIhYAQACABAAZGF0YQAAAAA=";

const speaker = new Audio();
let talking = false;
```

Where the venue lands (line 93), reveal the button:

```js
  // The button exists only where it can work. A wall screen carrying a
  // control that answers 503 is worse than one carrying no control.
  el("announce").hidden = !venue.announcer;
```

And the handler, with `readTheBoard` named exactly as the test above splits on:

```js
el("announce").addEventListener("click", readTheBoard);
speaker.addEventListener("ended", () => quiet());

/**
 * Read the standings to the room.
 *
 * `unlock()` runs before the first await on purpose, and the test in
 * `test_announcing.py` holds it there. Generation takes seconds; by the time
 * the clip arrives the click that started it is no longer a live gesture, and
 * Safari will refuse to play audio outside one. Starting the element on
 * something silent while the gesture is still ours is what buys the right to
 * play the real thing later.
 */
async function readTheBoard() {
  if (talking) return;
  talking = true;
  unlock();
  live("Warming up");
  try {
    const clip = await post("/api/board/announcement");
    await play(clip);
  } catch (failure) {
    quiet();
    say(failure.message);
  }
}

function unlock() {
  speaker.src = SILENCE;
  speaker.play().catch(() => {});
}

async function play(clip) {
  speaker.src = clip.audio;
  // Set after the src, because a browser that reloads the element on a source
  // change takes the default rate with it.
  speaker.playbackRate = RATE;
  live("On air");
  await speaker.play();
}

function live(what) {
  el("announce").classList.add("live");
  el("announce-say").textContent = what;
}

function quiet() {
  talking = false;
  el("announce").classList.remove("live");
  el("announce-say").textContent = "Read the board";
}
```

`say` is the page's existing complaint helper. Read `arena/static/arena.js` and use whatever it is actually called there; if the page only has `problem`, write the failure into that element the way the rest of the file does.

- [ ] **Step 5: Style it**

In `arena/static/app.css`, after `.board-frame` at line 841. Match the file's existing idiom: one selector a line, variables from the top of the file, and `--gold` reserved for a pinned director.

```css
.board-box{position:relative;display:flex;flex-direction:column;min-height:0;width:100%}
.mic-chip{
  position:absolute;top:10px;right:10px;z-index:2;
  display:flex;align-items:center;gap:7px;
  padding:6px 11px;border-radius:999px;cursor:pointer;
  background:rgba(9,12,20,.82);border:1px solid var(--line);
  color:var(--mute);font:600 .72rem/1 Inter,system-ui,sans-serif;
  backdrop-filter:blur(6px);transition:border-color .18s,color .18s
}
.mic-chip:hover{border-color:rgba(255,255,255,.3);color:#fff}
.mic-chip i{width:7px;height:7px;border-radius:50%;flex:none;background:var(--mute)}
.mic-chip.live{border-color:rgba(239,68,68,.6);color:#fff}
.mic-chip.live i{background:#ef4444;box-shadow:0 0 9px #ef4444;animation:blip 1.2s ease-in-out infinite}
.levels{display:none;align-items:flex-end;gap:2px;height:11px}
.mic-chip.live .levels{display:flex}
.levels s{width:2px;background:#ef4444;text-decoration:none;animation:meter .9s ease-in-out infinite}
.levels s:nth-child(2){animation-delay:.15s}
.levels s:nth-child(3){animation-delay:.3s}
.levels s:nth-child(4){animation-delay:.45s}
@keyframes meter{0%,100%{height:3px}50%{height:11px}}
```

`blip` already exists at `.lobby-badge i` (line 771); reuse it rather than writing a second one.

- [ ] **Step 6: Run the tests**

Run: `cd arena && uv run pytest tests/test_announcing.py -v`
Expected: PASS, 15 passed

- [ ] **Step 7: See it**

Run the arena with `GEMINI_API_KEY` and `ARENA_ANNOUNCER=1` exported, open `/arena`, play one match to the whistle so the board has somebody on it, and press the button. Confirm by ear that it speaks, that it is recognisably hurried, and that the clip lands between 35 and 45 seconds. If it does not, that is data for Task 8 rather than a reason to guess at the prompt.

- [ ] **Step 8: Commit**

```bash
git add arena/static/arena.html arena/static/arena.js arena/static/app.css arena/tests/test_announcing.py
git commit -m "feat(arena): a button that gets a sound out of a wall screen"
```

---

### Task 6: The board follows the commentary

**Files:**
- Modify: `arena/static/arena.js` (the `play` function from Task 5)
- Modify: `arena/static/board.js:28-45` (the `pinned` flag and the tab handlers)
- Modify: `arena/static/app.css`
- Modify: `arena/tests/test_announcing.py`

**Interfaces:**
- Consumes: Task 5's `play(clip)`, `quiet()`, the `caption` element; Task 4's `switch_at` and `script`.
- Produces: the same-origin message `{type: "board.show", board: "solo" | "versus", pinned: boolean}`, posted by `arena.js` to the board iframe and handled by `board.js`.

- [ ] **Step 1: Write the failing tests**

Append to `arena/tests/test_announcing.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd arena && uv run pytest tests/test_announcing.py -v -k "frame or origin or pinned or cycling"`
Expected: FAIL, `IndexError` on the split, nothing having been written yet

- [ ] **Step 3: Teach `arena.js` to caption and to steer**

Replace Task 5's `play` and `quiet` in `arena/static/arena.js`:

```js
async function play(clip) {
  speaker.src = clip.audio;
  speaker.playbackRate = RATE;
  live("On air");
  caption(clip.script.solo);
  steer("solo", true);
  // `switch_at` and `currentTime` are both on the media clock, which is the
  // clock a media element counts in whatever the playback rate is. No
  // conversion: dividing by RATE here would turn the board over a quarter of
  // the way early.
  let turned = false;
  speaker.ontimeupdate = () => {
    if (turned || speaker.currentTime < clip.switch_at) return;
    turned = true;
    caption(clip.script.versus);
    steer("versus", true);
  };
  await speaker.play();
}

function quiet() {
  talking = false;
  speaker.ontimeupdate = null;
  el("announce").classList.remove("live");
  el("announce-say").textContent = "Read the board";
  el("caption").hidden = true;
  // Hand the frame back its own twelve-second cycle.
  steer("solo", false);
}

function caption(words) {
  // The tags are direction for the voice, not words anybody says out loud.
  el("caption").textContent = words.replace(/\[[^\]]*\]/g, "").trim();
  el("caption").hidden = false;
}

/**
 * Ask the frame to show a board, and to hold it there while we talk over it.
 *
 * Same-origin, so this is a postMessage and not a src change: reloading the
 * iframe would throw away the socket it has open and blank the standings for
 * a second in the middle of a sentence about them.
 */
function steer(which, hold) {
  el("board").contentWindow?.postMessage(
    { type: "board.show", board: which, pinned: hold }, location.origin);
}
```

- [ ] **Step 4: Teach `board.js` to take direction**

In `arena/static/board.js`, after the tab handler loop at line 45:

```js
/**
 * The big screen framing this page, telling it which board to hold up.
 *
 * Only ever from the page that framed it, and only ever the one message. The
 * announcer talks over this frame for forty seconds, and the twelve-second
 * cycle would otherwise slide the board away in the middle of a sentence
 * about it.
 */
window.addEventListener("message", (note) => {
  if (note.origin !== location.origin) return;
  if (!note.data || note.data.type !== "board.show") return;
  if (note.data.pinned === false) return unpin();
  pinned = true;
  el("tick").hidden = true;
  clearTimeout(cycle);
  show(note.data.board === "versus" ? "versus" : "solo");
});

function unpin() {
  pinned = false;
  el("tick").hidden = false;
  turn();
}
```

- [ ] **Step 5: Style the caption**

In `arena/static/app.css`, after the `.mic-chip` rules from Task 5:

```css
.caption{
  margin:8px 0 0;padding:12px 16px;border-radius:12px;
  background:rgba(9,12,20,.9);border:1px solid var(--line);
  color:#fff;font:600 1.05rem/1.45 Outfit,system-ui,sans-serif;
  text-align:center;text-wrap:balance
}
```

- [ ] **Step 6: Run the tests**

Run: `cd arena && uv run pytest tests/test_announcing.py -v`
Expected: PASS, 19 passed

- [ ] **Step 7: See it, with the same standard as the rest of the venue**

With the arena running as in Task 5 Step 7, press the button and watch the frame. The caption must appear with the audio, the board must turn over roughly when the commentary does, both must clear at the end, and the frame must resume its own cycle. Watch for a caption that overflows its box on a long script and for the frame jumping while the tick bar is still animating; fix either if you see it.

- [ ] **Step 8: Commit**

```bash
git add arena/static/arena.js arena/static/board.js arena/static/app.css arena/tests/test_announcing.py
git commit -m "feat(arena): the board turns over when the commentary does"
```

---

### Task 7: A browser actually pressing it

**Files:**
- Modify: `arena/tests/test_announcing.py`
- Modify: `arena/tests/conftest.py`

**Interfaces:**
- Consumes: everything from Tasks 4, 5 and 6.
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the failing test**

The suite already has a `wall_page` fixture (`arena/tests/conftest.py:416`) that opens `/arena` in Chromium against a real server and fails the test on any console error. That fixture puts fifty live matches on the wall, which hides the lobby. This needs a lobby, so it needs a fixture of its own beside it.

Add to `arena/tests/conftest.py`:

```python
@pytest.fixture
async def lobby_page(wall_server, monkeypatch):
    """A browser in front of an arena that has a lobby and a stocked board.

    Not `wall_page`: that one fills the venue with fifty live matches, and a
    screen showing football has no lobby and so no button.
    """
    import announcer
    from playwright.async_api import async_playwright

    monkeypatch.setattr(announcer, "ENABLED", True)
    monkeypatch.setattr(announcer, "API_KEY", "a-key")

    async def generate(podiums):
        # Two seconds of silence, so a real element really plays and really
        # ends, without a test spending forty seconds listening to it.
        return b"\x00\x00" * 48_000, {"solo": "one two three four",
                                      "versus": "five six seven eight"}

    monkeypatch.setattr(announcer, "_generate", generate)

    async with httpx.AsyncClient(base_url=wall_server, timeout=30) as phone:
        await _one_ranked_match(phone)

    async with async_playwright() as driving:
        browser = await driving.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        complaints = []
        page.on("console", lambda note: _worth_complaining_about(note, complaints))
        page.on("pageerror", lambda blew_up: complaints.append(str(blew_up)))
        await page.goto(f"{wall_server}/arena")
        await page.wait_for_selector("#announce:not([hidden])", timeout=30_000)
        yield page
        await browser.close()
    assert not complaints, f"the lobby logged errors: {complaints}"
```

Write `_one_ranked_match(phone)` as the async twin of `play_a_ranked_match` from Task 4, over the same routes. If Task 4 ended up calling `test_board.py`'s `played()` against a connection instead, do the same here through `wall_server`'s database.

Then in `arena/tests/test_announcing.py`:

```python
@pytest.mark.e2e
async def test_a_screen_reads_the_board_out_loud(lobby_page):
    await lobby_page.click("#announce")
    await lobby_page.wait_for_selector("#announce.live", timeout=10_000)
    await lobby_page.wait_for_selector("#caption:not([hidden])", timeout=10_000)
    assert "one two three four" in await lobby_page.text_content("#caption")

    # The element is really given the clip, and really plays it.
    assert await lobby_page.evaluate(
        "() => document.querySelector('audio')?.playbackRate") == 1.25
    await lobby_page.wait_for_function(
        "() => { const a = document.querySelector('audio');"
        "        return a && !a.paused && a.currentTime > 0; }", timeout=10_000)


@pytest.mark.e2e
async def test_the_frame_turns_over_and_then_gives_up_the_pin(lobby_page):
    await lobby_page.click("#announce")
    # Halfway through, the second half of the script is on screen and the
    # frame is holding the head to head board up.
    await lobby_page.wait_for_function(
        "() => document.querySelector('#caption').textContent.includes('five six')",
        timeout=20_000)
    board = lobby_page.frame_locator("#board")
    assert await board.locator("#tab-versus").get_attribute("aria-selected") == "true"

    # And at the end everything is handed back.
    await lobby_page.wait_for_selector("#announce:not(.live)", timeout=20_000)
    assert await lobby_page.is_hidden("#caption")
    assert await board.locator("#tick").is_visible()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd arena && uv run pytest tests/test_announcing.py -m e2e -v`
Expected: FAIL, the `lobby_page` fixture not existing yet

- [ ] **Step 3: Make them pass**

These test Tasks 5 and 6, which are already written, so this step is fixture work and whatever real defects the browser finds. Expect at least one: a headless Chromium's autoplay policy, an `<audio>` element that was never appended to the document, or a caption that is written before the element is unhidden. Fix the code, not the test, unless the test is asserting something the design never promised.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd arena && uv run pytest tests/test_announcing.py -m e2e -v`
Expected: PASS, 2 passed

- [ ] **Step 5: Run the whole suite both ways**

Run: `cd arena && uv run pytest && uv run pytest -m e2e`
Expected: PASS on both, and no new warnings. The suite is held at one warning.

- [ ] **Step 6: Commit**

```bash
git add arena/tests/test_announcing.py arena/tests/conftest.py
git commit -m "test(arena): a browser presses the button and hears something"
```

---

### Task 8: The venue can run it, and the word budget becomes a measurement

**Files:**
- Modify: `deploy/service.yaml` (the arena container's `env` block, beside the sabotage variables)
- Modify: `arena/README.md` (environment table, pages note, and the quota section at line 50)
- Modify: `arena/tests/test_announcer.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the measurement**

The word budget in the design is arithmetic from documented speech rates. This is what turns it into a number somebody measured, in the same spirit as the corpus measurement in `tests/test_intent.py` that reruns against the live model on demand.

Append to `arena/tests/test_announcer.py`:

```python
@pytest.mark.e2e
async def test_a_real_clip_lands_in_the_forty_second_window():
    """The one test here that spends money. Run it when the prompt changes.

    The design budgets 120 to 135 words on the arithmetic that the model
    speaks at about 150 to 165 wpm and the room hears it at 1.25x. Nobody has
    measured this model. This is where that estimate is either confirmed or
    corrected -- and if it is wrong, the fix is the word budget in
    SHOUTCASTER, not the window below.
    """
    if not announcer.configured():
        pytest.skip("set ARENA_ANNOUNCER=1 and GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT")

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
```

- [ ] **Step 2: Run it against the real models**

Run: `cd arena && ARENA_ANNOUNCER=1 GEMINI_API_KEY=$GEMINI_API_KEY uv run pytest tests/test_announcer.py -m e2e -v -s`
Expected: PASS, and a script printed that is worth reading out loud.

If the words are in range but the seconds are not, the model's pace is not what the design assumed: adjust the word budget in `SHOUTCASTER` by the ratio, bump `PROMPT_VERSION`, and note the measured pace in a comment beside the budget. If the model will not hit the word count at all, that is a prompt problem and the assertion is doing its job.

- [ ] **Step 3: Turn it on at the venue**

In `deploy/service.yaml`, in the arena container's `env` block beside the sabotage variables:

```yaml
            # Reading the standings out loud on the big screen. Two model
            # calls a press, held to one generation at a time across the
            # venue, so this costs the arena two awaited HTTPS calls and a
            # memcpy. The models are named in `arena/announcer.py`; the
            # defaults are right and are left to it.
            #
            # No key here: the instance's service account carries this the
            # same way it carries the embedding call, and GEMINI_API_KEY is
            # for a laptop that has no metadata server to ask.
            - name: ARENA_ANNOUNCER
              value: "1"
```

- [ ] **Step 4: Update the README**

Three places in `arena/README.md`.

The environment table, after `ARENA_SABOTAGE_THRESHOLD`:

```markdown
| `ARENA_ANNOUNCER` | unset | `1` puts a button on the big screen's lobby that reads the top three of both boards out loud. Off everywhere it has not been asked for: it costs two model calls a press and makes a noise in the room. Needs `GOOGLE_CLOUD_PROJECT` or `GEMINI_API_KEY` as well, and is simply off without one. |
| `ARENA_ANNOUNCER_MODEL` | `gemini-3.6-flash` | Writes the script. Not the chain's model, on purpose - see below. |
| `ARENA_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | Speaks it. Returns headerless PCM at 24 kHz, which the arena wraps in a WAV header before serving. |
| `ARENA_TTS_VOICE` | `Puck` | One of the thirty prebuilt voices. |
| `ARENA_ANNOUNCER_LOCATION` | `us-central1` | Speech models are served regionally, not from `global`. |
```

The quota section at line 50 currently says the venue needs one model's quota and not two. Replace that claim, keeping the paragraph around it intact:

```markdown
**Three models, not one.** The chain is all `GEMINI_FLASH_LITE`, so the
arithmetic below is about that model's quota alone. The venue draws on two
others as well and neither competes with a shout: the announcer writes its
script with `GEMINI_FLASH` and speaks it with a TTS model, and each is its own
quota bucket. That separation is the reason the announcer uses a different
model from the chain rather than the same one.

The announcer's own arithmetic is short. Two requests make one clip, and
`Announcer` holds the venue to one clip in flight at a time however many
screens are pressing, so its ceiling is two requests in flight - against the
chain's 80 to 112 a minute. A clip is then cached against a fingerprint of the
podiums, so a board nobody has changed is never generated twice.
```

And the Pages table, on the `/arena` row, so the button is discoverable from the document that lists the pages:

```markdown
| `/arena` | The big screen: a room to scan into, then the match at the size of the room. The lobby's standings have a button that reads the top three of both boards out loud, when `ARENA_ANNOUNCER` is on |
```

- [ ] **Step 5: Check the deploy file still parses**

Run: `cd /Users/chuan/mywork/ai/agent-football && uv run --project arena pytest arena/tests/test_service_yaml.py -v`
Expected: PASS. That suite asserts on the shape of `service.yaml` and will catch a bad indent or a variable in the wrong container.

- [ ] **Step 6: Run everything**

Run: `cd arena && uv run pytest && uv run pytest -m e2e`
Expected: PASS on both.

- [ ] **Step 7: Commit**

```bash
git add deploy/service.yaml arena/README.md arena/tests/test_announcer.py
git commit -m "feat(arena): turn the announcer on at the venue, and measure it"
```

---

## Self-Review

**Spec coverage.** Every section of the design maps to a task: the module and its four guards to Tasks 1-3, the two model calls and both prompt corrections to Task 2, the endpoints and the venue flag to Task 4, the button and the autoplay unlock to Task 5, captions and the frame following to Task 6, the Playwright pass to Task 7, and configuration, the quota debt and the opt-in measurement to Task 8. The spec's "deliberately not in this" list is honoured: no task adds an unprompted announcement, word-level captions, a phone button or a second voice.

**Type consistency.** `Clip` is defined once in Task 3 with the fields `state`, `wav`, `seconds`, `switch_at`, `script`, and Tasks 4 and 5 use exactly those names. `spoken()` returns `score_attack` and `head_to_head`; the script prompt calls the same two things `solo` and `versus` because that is what the model returns and what the response body carries, and the translation happens in exactly one place, `_generate`. `readTheBoard`, `unlock`, `play`, `quiet`, `caption` and `steer` are named identically in Tasks 5, 6 and their tests.

**Two things the implementer must resolve rather than assume**, both flagged inline: the exact route to a finished ranked match in a fixture (Task 4 Step 1 and Task 7 Step 1 - mirror whatever `test_board.py:27` already does rather than inventing a second helper), and the arena page's existing name for its complaint helper (Task 5 Step 4).
