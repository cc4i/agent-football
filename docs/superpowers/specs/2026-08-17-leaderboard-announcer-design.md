# The announcer: a button on the big screen that reads the standings out loud

Date: 2026-08-17
Status: Approved, ready for implementation planning.

## Purpose

The standings are the best thing in the venue and nobody looks at them. They
sit in an iframe under the lobby (`arena/static/arena.html:61`), cycling
between score attack and head to head every twelve seconds
(`arena/static/board.js:19`), silently, at the size of a paperback, next to a
QR code that is ten times louder. A manager who has just beaten the house side
walks past their own name.

This gives the board a voice. One small button on the big screen's lobby, and
an over-the-top esports shoutcaster reads the top three of both boards to the
room: who leads, who is unbeaten, who was dethroned, who scored in the first
forty seconds. Forty seconds of noise that makes six people want to play again
and makes everybody else look up.

It is a party trick, and it is judged entirely by ear. That shapes two
decisions below that would otherwise go the other way: it must be audible on a
laptop and not only at a deployed venue, and its length is controlled by a word
budget rather than by a stated duration.

## What is being built

```
    the big screen                          arena                      :8003
    /arena lobby                            +------------------------------+
        |                                   |                              |
        |  1. POST /api/board/announcement  |  announcer.py                |
        |---------------------------------->|   fingerprint the podiums    |
        |                                   |   cache? single-flight?      |
        |                                   |         |                    |
        |                                   |         v            Vertex  |
        |                                   |   (1) gemini-3.6-flash ----->|
        |                                   |       board JSON -> script   |
        |                                   |   (2) gemini-3.1-flash-tts   |
        |                                   |       script -> raw PCM ---->|
        |                                   |   (3) PCM + WAV header       |
        |  <-- {state, seconds, switch_at,  |         |                    |
        |       script, audio: <url>}       |         v                    |
        |                                   |   cache: 2 clips, ~5 MB      |
        |  2. GET  .../{state}.wav          |                              |
        |<--------------------------------->|                              |
        |                                   +------------------------------+
        v
    <audio> at 1.25x, ON AIR pill,
    captions, and the board frame
    following the commentary
```

One new module, `arena/announcer.py`. Two new endpoints. A button, a pill, a
caption card, and a listener in the board page. Nothing else in the arena
changes behaviour.

## Where it runs, and why it runs in the arena

The arena is one Cloud Run instance by design, `maxScale: "1"`, and
`deploy/service.yaml:104` records that its three containers already spend the
8 vCPU ceiling. So "put it somewhere else" cannot mean a fourth container. It
would have to be a second Cloud Run service, and the question is whether the
announcer is heavy enough to be worth one.

It is not. Measured against what the work actually is:

| Cost | Size | Against |
|---|---|---|
| CPU | two awaited HTTPS calls, one base64 decode of ~2 MB, one 44-byte header prepend | 4 vCPU, `cpu-throttling: false` |
| Memory | ~2.4 MB a clip, two clips cached | 8 GiB |
| Bandwidth | one 2.4 MB response per screen per podium change | already sized for 540 WebSockets |
| Event loop | nothing blocking; `async httpx`, exactly as `arena/intent.py:130` already does on the far more latency-sensitive shout path | - |

A second service would isolate CPU and memory, which is the cost that rounds to
zero, and would not isolate the thing that is actually scarce, which is the
project's Vertex quota. It would add an image, a deploy step, a service
account, a URL, CORS on the big screen or a proxy through the arena that puts
the bytes back where we started, and a container cold start on the first press
of the evening, on top of a wait that is already seconds long.

So: in the arena, guarded. The guards are in the next section and they are the
price of that answer.

**The one thing that would change this.** If the announcer ever fires
unprompted - at every whistle, on a timer, for every room - the volume changes
shape and a separate service earns its keep. This design is a button somebody
presses, and the guards below are sized for that.

## `arena/announcer.py`

Modelled on `arena/intent.py`, including the sentence at its head that governs
this file too: if the model is not configured the feature is simply off. Vertex
is reached over REST with `httpx` and no client library, because the arena
container carries neither `google-genai` nor a reason to.

### The four guards

**Single-flight.** A `dict[fingerprint, asyncio.Future]` and a venue-wide
`asyncio.Semaphore(1)`. Twenty screens pressing at once produce one generation
and twenty awaits on the same future. This is the correct semantics rather than
a throttle: every screen wants the identical clip, because the clip is a pure
function of the podiums.

**A fingerprint over the podiums only.** A SHA-256 over the top three rows of
both boards - names and the numbers that get spoken - plus a `PROMPT_VERSION`
constant. Two consequences, both wanted: a manager improving from ninth to
eighth does not invalidate a clip nobody would notice the difference in, and
editing the prompt retires every clip made by the old one.

**A cache of two.** The current fingerprint and the one before it, evicting the
rest. The previous one is kept so that a podium changing while a screen is
mid-fetch does not 404 the file it is already downloading. Ceiling about 5 MB.

**Two timeouts, then a failure written for somebody to read.** Ninety seconds
for each call and for the pair of them together, and a hundred and twenty for
what the person at the screen waits - queue time as well as generation, so a
second podium pressed while the first is being made is not refused before its
own work starts. The numbers come from the measurement test below: a real clip
took about fifty-seven seconds against the live models, nearly all of it speech
synthesis. `intent.py` uses five, which is right for an embedding on the shout
path and would cut every press here off mid-generation.

### Credentials

The repository already has a convention for this and the arena is the only
service that does not follow it, because `intent.py` never needed to run
anywhere but production:

| Where | How | Source |
|---|---|---|
| Deployed | metadata server token, `{region}-aiplatform.googleapis.com` | `arena/intent.py:119` |
| A laptop | `GEMINI_API_KEY`, `generativelanguage.googleapis.com` | `game/.env.example:17`, `dugout/.env.example:21` |
| Neither | off, and the button is not rendered | this design |

One `_endpoint()` helper picks between them. The laptop path is not a
convenience: a feature judged by ear that can only be heard in production is a
feature nobody will tune.

### Testability

The generator is a parameter with a default, the way `intent.py` takes
`embedder=embed`. Every test but the opt-in measurement runs with a fake and
touches no network.

## The two model calls

### Step 1: the board becomes a script

`gemini-3.6-flash`. Deliberately **not** the chain's `gemini-3.5-flash-lite`:
a different base model is a different quota bucket, so a room full of people
pressing the button cannot take slots away from managers shouting at their
squads. `game/agents/constants.py` already defines this model and nothing uses
it.

Input is the top three of each board, from `board.top(conn, mode)`
(`arena/board.py:192`) - the function exists and is already used by the results
screen. Output is JSON, enforced with `responseSchema`:

```json
{"solo": "...", "versus": "..."}
```

Split rather than one string, because the split is what drives the captions and
the frame switch in the next section.

The system instruction, which is the original brief with three corrections:

```
You are an over-the-top, high-octane esports shoutcaster live on stage at a
futsal tournament, reading the leaderboard to a room full of people.

Take the two boards below and write ONE announcement covering both.

RULES
1. Length. 120 to 135 words in total, split roughly 60/40 across the two
   boards. Count them. Words are the only length instruction here; do not
   think in seconds.
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

Return only the JSON.
```

Three things changed from the original brief, each for a reason:

- **"a 15-to-20-second audio script" became a word budget.** A model cannot
  verify seconds, having never heard itself speak; it can count words. This is
  the single change that makes the length reliable.
- **Digits became words.** ALL CAPS emphasis lands on `FORTY-ONE` and has
  nothing to grip on `41`.
- **Free text became JSON.** The two halves are needed separately downstream.

### Step 2: the script becomes speech

`gemini-3.1-flash-tts-preview`, voice `Puck`, single speaker. Reached with
`generateContent`, `responseModalities: ["AUDIO"]` and
`speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName`.

Four facts about this API that the implementation has to respect:

- Vertex returns **raw PCM, 16-bit, 24 kHz, mono, with no WAV header**. A
  44-byte header from the standard library's `wave` module makes it a file a
  browser will play. No new dependency, no transcode, no ffmpeg.
- Vertex concatenates the prompt and the text into one `contents` field, so
  the style direction is prefixed to the script rather than sent beside it.
- The model ignores `temperature`, `top_k` and `top_p`. Sending them is noise.
- Measured against multi-gke-ops: the TTS model answers from both `global` and
  regional endpoints. The script model is global-only. Both work from `global`,
  which matches the chain's `GOOGLE_CLOUD_LOCATION=global`.

The style prefix follows Google's documented structure for this model - audio
profile, scene, director's notes:

```
Audio profile: a male esports shoutcaster, mid-thirties, hand mic, big room.
Scene: a packed futsal venue, the leaderboard on the screen behind you.
Director's notes: high energy from the first word, tempo and pitch climbing
into each number one. Hit the words in capitals. Honour the bracketed cues.
Say this:
```

### Why forty seconds

At the model's expressive pace of roughly 150-165 wpm, played at 1.25x, the
room hears about 190-205 wpm. Six podium slots, an intro, a transition and a
sign-off:

| Played | Words | What fits |
|---|---|---|
| 30s | ~100 | Six names, six numbers, one joke. A list read quickly. |
| **40s** | **120-135** | Every one of the six gets a clause of personality; both leaders get a finish. |
| 50s | ~170 | A running gag and a real story, and a minute of one voice on an unattended wall. |

Each bracketed cue costs another 0.4-0.8s, so six to eight of them spend about
five seconds of the budget. That is why rule 4 caps them.

This arithmetic is an estimate from documented speech rates, not a measurement
of this model. §Testing has the opt-in test that turns it into one.

## The screen

### The button

In the lobby, over the top-right of the board frame (`arena/static/arena.html:61`),
so it lives and dies with the lobby - which is the "default page" state this
was asked for. When football is on centre court there is no board and no
button.

Three states. Idle: a small mic chip. Working, then playing: an `ON AIR` pill
with a level meter. Failed: it says so for a moment and returns to idle. The
lobby is never blocked and never breaks.

It is not rendered at all when `/api/venue` reports the announcer off, or when
no manager holds a ranked result. A control on a wall screen that cannot work
is worse than no control, and there is nothing to announce to an empty board.

### Autoplay, which is where this feature dies if it is ignored

Generation takes seconds. By the time the clip arrives, the click's transient
activation has expired, and Safari refuses to play audio outside a gesture. The
symptom is a feature that works perfectly in Chrome and is silent on the
venue's iPad, with no error anywhere.

So the click handler plays a silent data-URI on the `<audio>` element
**synchronously**, inside the gesture, which unlocks the element. The real
`src` is swapped in when it arrives and played on the already-unlocked element.

### Captions, and the board following the commentary

While the clip plays, the script appears as a caption card under the standings:
loud rooms, and anybody who cannot hear it.

The frame follows too. The page pins the iframe to score attack for the first
half, and at `switch_at` swaps the caption and posts a same-origin message
telling it to show head to head. `board.js` gains a small listener beside the
tab handling it already has (`arena/static/board.js:38`), which also stops its
own twelve-second cycle from sliding the board away mid-sentence.

`switch_at` is the clip's real duration apportioned by the word counts of the
two halves. That is accurate to a second or so, which is fine for switching a
*board* and would not be fine for word-level captions - so word-level captions
are not attempted. The model returns no timings and estimating them would look
broken the moment it drifted.

## Endpoints

| Method | Path | Who | What |
|---|---|---|---|
| POST | `/api/board/announcement` | anyone, rate-limited | Makes or returns the clip for the current podiums. 503 with a readable reason when unconfigured or when the model fails. |
| GET | `/api/board/announcement/{state}.wav` | anyone | The bytes. `Cache-Control: public, max-age=31536000, immutable` - the fingerprint is in the path, so a screen refetches only when the podiums move. |

The POST answers:

```json
{"state": "9f3c...", "seconds": 50.4, "switch_at": 29.1,
 "script": {"solo": "...", "versus": "..."},
 "audio": "/api/board/announcement/9f3c....wav"}
```

`seconds` and `switch_at` are on the media clock, which is the clock
`HTMLMediaElement.currentTime` reports whatever the playback rate is. So the
page compares `currentTime` against `switch_at` directly and divides by 1.25
only where it wants a figure in wall-clock seconds. The server never knows the
rate, and that is the point: one unit on the wire, one place that converts.

Rate limiting reuses the token bucket in `arena/limits.py:25`, keyed by client
IP, for the same reason the two other unauthenticated creating endpoints use
it: on a public URL, a POST that costs money is an invitation.

`/api/venue` (`arena/app.py:706`) gains `"announcer": true|false`. The lobby
already fetches it at `arena/static/arena.js:93`, so the flag costs no round
trip.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `ARENA_ANNOUNCER` | unset | `1` turns it on. Off is off, everywhere it has not been asked for: a laptop, a test run, CI. |
| `ARENA_ANNOUNCER_MODEL` | `gemini-3.6-flash` | Writes the script. A different base model from the chain's on purpose. |
| `ARENA_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | Speaks it. |
| `ARENA_TTS_VOICE` | `Puck` | One of the thirty prebuilt voices. |
| `ARENA_ANNOUNCER_LOCATION` | `global` | Both models answer here (script model is global-only, TTS answers in both). Matches the chain's location. |

`deploy/service.yaml` sets the first and takes the defaults for the rest. The
instance's service account already holds `aiplatform` access for the embedding
call, so there is no IAM change.

### The quota debt this creates

`arena/README.md:59` currently says the venue needs one model's quota and not
two, and names the unused `GEMINI_FLASH` constant as the reason. This design
uses that model and a TTS model, so the venue now needs three buckets. That
section has to be updated as part of the work, with the announcer's own
arithmetic, which is small and worth writing down rather than leaving somebody
to discover: two requests per generation, at most one generation at a time
venue-wide, so at most two requests in flight no matter how many screens are
pressing.

## Testing

Offline, with a fake generator, in `arena/tests/test_announcer.py`:

- the fingerprint is stable across reorderings that do not change the podiums,
  and changes when a podium does, and when `PROMPT_VERSION` does
- two concurrent callers produce exactly one generation
- the cache holds two and evicts the third
- the bytes parse back through `wave` as 24 kHz, mono, 16-bit, and are as long
  as the PCM says
- `switch_at` lands between the two halves in proportion to their word counts
- unconfigured: the endpoint answers 503 and `/api/venue` reports it off
- a model failure leaves the lobby working and logs a warning, as
  `intent.py:147` does

End to end with Playwright, under the existing `-m e2e` marker, against a
stubbed announcer: press the button on `/arena`, the pill lights, the caption
appears, the frame switches at the switch point, the audio element is given a
src.

And one opt-in test that calls the real models and asserts the clip lands
between 35 and 45 played seconds, in the same spirit as the corpus measurement
in `tests/test_intent.py` that reruns against the live model on demand. The
word budget in this document is arithmetic; that test is what makes it a
measurement, and it is where the number gets corrected if it is wrong.

## Files

New:

- `arena/announcer.py`
- `arena/tests/test_announcer.py`

Changed:

- `arena/app.py` - two endpoints, the venue flag
- `arena/static/arena.html` - the button and the caption card
- `arena/static/arena.js` - the press, the unlock, playback at 1.25x, the
  captions, the message to the frame
- `arena/static/board.js` - receive that message, pin the board, stop cycling
- `arena/static/app.css` - the chip, the pill, the level meter, the card
- `arena/README.md` - the endpoints table, the pages note, the environment
  table, and the quota section above
- `arena/.env.example` - the five variables
- `deploy/service.yaml` - `ARENA_ANNOUNCER=1`

## What is deliberately not in this

- **No unprompted announcements.** Not at a whistle, not on a timer. That is
  the change that would move this out of the arena, and it is not asked for.
- **No word-level captions.** The model returns no timings.
- **No phone button.** `/board` opens on phones from home and the results
  sheet; a leaderboard that starts shouting in somebody's pocket is a different
  product. The button is on the big screen only.
- **No second voice.** Multi-speaker TTS exists and a two-hander would be
  funnier. It doubles the prompt complexity for a first version whose whole
  point is to find out whether the room likes this at all.
