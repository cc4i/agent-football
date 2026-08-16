"""How close a shout comes to asking, quietly, for the opposition to be slowed.

Not a passphrase. A manager should be able to say it their own way, so what is
measured is the meaning: the shout is embedded and compared against a handful
of reference phrasings, and the best cosine similarity is the score.

The scoring is a pure function of the text and an embedder, so the threshold
can be measured against a corpus offline and the tests can run without a
network. `tests/test_intent.py` holds that corpus, including the negatives that
matter - a manager legitimately saying "mark their striker" is talking about
the opposition and must not trip this.

Vertex is reached over REST with `httpx` rather than a client library, because
the arena container carries neither `google-genai` nor a reason to. If the
model is not configured the feature is simply off: a secret weapon that fires
on a network hiccup is worse than one that never fires.
"""

import logging
import math
import os

import httpx

logger = logging.getLogger(__name__)

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = os.environ.get("ARENA_EMBED_MODEL", "text-embedding-005")

# Off unless a venue turns it on. This changes a match without saying so, which
# is not a thing to have running by default at somebody's workshop.
ENABLED = os.environ.get("ARENA_SABOTAGE") == "1"

# The midpoint of a measured gap, not a number anybody liked the look of. See
# `tests/test_intent.py`: over the corpus there the lowest asking phrase scores
# +0.179 and the highest ordinary one +0.097.
THRESHOLD = float(os.environ.get("ARENA_SABOTAGE_THRESHOLD", "0.14"))

# The meaning we are looking for, said several ways, so a manager can phrase it
# their own way rather than guess a password.
ASKING_LIKE = (
    "quietly weaken the other team",
    "secretly slow the opposition down",
    "nobble them without anyone noticing",
    "sabotage the other side on the quiet",
    "take the legs off the opposition, keep it between us",
)

# And the meaning it is otherwise mistaken for.
#
# Similarity to ASKING_LIKE alone does not work, and it fails in the worst
# possible way. Measured over the corpus, every short football instruction sits
# close to every other one -- topic swamps intent -- so "we need a goal, get
# forward and shoot on sight" scored 0.689 against a lowest genuine ask of
# 0.695. A threshold in that gap would have fired on the single most common
# shout in the product.
#
# Subtracting the best ordinary match cancels the shared "football instruction"
# direction and leaves the part that differs. The same shout then scores -0.241
# and the gap between the two groups opens to +0.082.
ORDINARY_LIKE = (
    "press them high up the pitch",
    "mark their best player",
    "keep possession and slow the game down",
    "we need a goal, shoot on sight",
    "defend deep and protect the lead",
)

_TIMEOUT = httpx.Timeout(5.0)


def configured():
    """Whether this can run at all."""
    return bool(ENABLED and PROJECT)


def cosine(a, b):
    """Cosine similarity of two equal-length vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    left = math.sqrt(sum(x * x for x in a))
    right = math.sqrt(sum(y * y for y in b))
    if not left or not right:
        return 0.0
    return dot / (left * right)


def score(vectors):
    """How much more this shout asks for it than it asks for ordinary football.

    `vectors` is the embedding of the shout, then one per ASKING_LIKE, then one
    per ORDINARY_LIKE. The answer is the best asking match minus the best
    ordinary one: positive means the words lean towards the quiet request,
    negative means they are simply tactics.
    """
    wanted = 1 + len(ASKING_LIKE) + len(ORDINARY_LIKE)
    if not vectors or len(vectors) != wanted:
        return 0.0
    said = vectors[0]
    asking = vectors[1:1 + len(ASKING_LIKE)]
    ordinary = vectors[1 + len(ASKING_LIKE):]
    return (max(cosine(said, one) for one in asking)
            - max(cosine(said, one) for one in ordinary))


async def _token():
    """An access token from the instance's metadata server."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        reply = await http.get(
            "http://metadata.google.internal/computeMetadata/v1/"
            "instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"})
        reply.raise_for_status()
        return reply.json()["access_token"]


async def embed(texts):
    """Embed several strings at once. Returns one vector each, or None."""
    try:
        token = await _token()
        url = (f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/"
               f"{PROJECT}/locations/{LOCATION}/publishers/google/models/"
               f"{MODEL}:predict")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            reply = await http.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"instances": [{"content": text} for text in texts]})
            reply.raise_for_status()
            return [row["embeddings"]["values"] for row in reply.json()["predictions"]]
    except Exception as problem:
        # Never the manager's problem. The shout still reaches the squad by the
        # ordinary route; only the secret does not fire.
        logger.warning("could not score a shout's intent: %s", problem)
        return None


async def asked_for_it(text, embedder=embed):
    """Whether this shout asks, quietly, for the opposition to be slowed.

    Returns (matched, score). The embedder is a parameter so a test can answer
    for it without a network.
    """
    if not configured():
        return False, 0.0
    vectors = await embedder([text, *ASKING_LIKE, *ORDINARY_LIKE])
    if not vectors:
        return False, 0.0
    found = score(vectors)
    return found >= THRESHOLD, found
