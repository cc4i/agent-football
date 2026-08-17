"""The standings, read out loud by somebody far too excited about them.

The board is the best thing in the venue and it spends the evening in an
iframe under the lobby, cycling silently at the size of a paperback. This
gives it a voice: one press on the big screen, and the top three of both
boards come back as forty seconds of shoutcaster.

Two model calls, because they are two different jobs. A flash model turns the
rows into a script - which manager to build to, which gap is worth a gasp -
and a speech model reads that script in one take. Neither is asked to do the
other's work.

Reached over REST with `httpx` and no client library, the way `intent.py`
reaches Vertex, and off unless configured for the same reason. A wall screen
carrying a button that cannot work is worse than one carrying no button.
"""

import asyncio
import base64
import dataclasses
import hashlib
import io
import json
import logging
import os
import wave

import httpx

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
        self._generate = generate
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
            task = asyncio.ensure_future(self._make(state, podiums))
            # Retrieve the exception to prevent asyncio logging "Task exception was
            # never retrieved" if every waiter gives up before the generation completes.
            # Calling .exception() clears asyncio's internal _log_traceback flag.
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            self._making[state] = task
        # Two deadlines, bounding different things. The outer one here stops a
        # screen from waiting forever if three different podiums are pressed in
        # a minute - it bounds queue time plus generation time, which is what
        # the person at the screen experiences. The inner one inside _make stops
        # a hung model from holding the semaphore and starving every other press.
        # Shielded, so a screen that navigates away mid-generation cancels its
        # own wait and not everybody else's clip.
        try:
            async with asyncio.timeout(SECONDS):
                return await asyncio.shield(self._making[state])
        except TimeoutError as slow:
            raise Silent("the announcer took too long and was cut off") from slow

    async def _make(self, state, podiums):
        try:
            async with self._slot:
                async with asyncio.timeout(SECONDS):
                    make = self._generate or _generate
                    pcm, words = await make(podiums)
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
