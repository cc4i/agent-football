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
