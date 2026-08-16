"""Scoring a shout for the one instruction the squad answers on its own.

The corpus below is the whole argument for the threshold. It is deliberately
unkind: the negatives include every way a manager legitimately talks about the
opposition, because "mark their striker" is ordinary football and must not slow
anybody down.

The scoring is a pure function of the text and an embedder, so these run
offline against a stand-in. `scripts` for the real separation: see
`test_the_threshold_sits_between_the_two_groups`, which is skipped unless a
run has credentials and is what the number in `intent.THRESHOLD` came from.
"""

import os
import subprocess

import pytest

import intent

# Ways a manager might ask for it. A passphrase would only accept the first.
ASKING = [
    "quietly weaken the other team",
    "secretly slow the opposition down",
    "nobble them without anyone noticing",
    "sabotage red on the quiet",
    "take the legs off the other lot, keep it between us",
    "slow them down secretly",
    "quietly make the opposition worse",
    "hobble the other side, don't tell anyone",
    # The words a player of games reaches for.
    "nerf red and buff blue",
    "nerf red",
    "take the legs off their lads, keep it quiet",
    "secretly drain the other team's energy",
    "slow their whole team down on the sly",
]

# Ordinary football, including the hard cases: these are all about the
# opposition and none of them is asking for a squad to be tampered with.
NOT_ASKING = [
    "mark their striker",
    "press their number nine",
    "stop their counter attack",
    "close down their midfield",
    "watch the man on the left",
    "we need a goal, get forward and shoot on sight",
    "sit deep and hold what we have",
    "push up and press high",
    "get it wide and cross early",
    "keep the ball, slow the game down",          # slow, but our own tempo
    "their keeper is off his line, chip him",
    "double up on their winger",
    "take our time, no rush",
    "everybody back, defend the lead",
    # Naming the colours is not asking for anything. These are the traps that
    # widening the references opened up, so they are in the corpus for good.
    "red are quick, drop off them",
    "blue need to press red harder",
    "buff our forward",
    "press high",
]


class Stand_in:
    """An embedder with no network: one dimension per word that matters.

    Crude on purpose. It is not pretending to be a language model, only to be
    deterministic and to let the pure scoring be tested on its own.
    """

    WORDS = ("quiet", "secret", "slow", "weaken", "nobble", "sabotage",
             "hobble", "legs", "press", "mark", "goal", "deep", "possession")

    async def __call__(self, texts):
        vectors = []
        for text in texts:
            low = text.lower()
            vectors.append([1.0 if word in low else 0.0 for word in self.WORDS] + [0.1])
        return vectors


async def test_a_shout_that_asks_for_it_is_recognised(monkeypatch):
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")
    monkeypatch.setattr(intent, "THRESHOLD", 0.1)
    matched, found = await intent.asked_for_it(
        "quietly weaken the other team", embedder=Stand_in())
    assert matched and found > 0.1


async def test_an_ordinary_shout_is_not(monkeypatch):
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")
    monkeypatch.setattr(intent, "THRESHOLD", 0.1)
    matched, _ = await intent.asked_for_it("mark their striker", embedder=Stand_in())
    assert not matched


async def test_nothing_fires_when_it_is_switched_off(monkeypatch):
    # The default. A venue has to ask for this.
    monkeypatch.setattr(intent, "ENABLED", False)
    matched, found = await intent.asked_for_it(
        "quietly weaken the other team", embedder=Stand_in())
    assert (matched, found) == (False, 0.0)


async def test_nothing_fires_when_the_model_is_not_configured(monkeypatch):
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "")
    assert await intent.asked_for_it("quietly weaken the other team",
                                     embedder=Stand_in()) == (False, 0.0)


async def test_an_embedder_that_fails_takes_nothing_down_with_it(monkeypatch):
    # The shout still reaches the squad the ordinary way; only this misses.
    monkeypatch.setattr(intent, "ENABLED", True)
    monkeypatch.setattr(intent, "PROJECT", "somewhere")

    async def broken(texts):
        return None

    assert await intent.asked_for_it("quietly weaken them", embedder=broken) == (
        False, 0.0)


def test_cosine_is_one_for_a_vector_against_itself():
    assert intent.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert intent.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_survives_the_degenerate_answers_an_api_can_give():
    assert intent.cosine([], [1.0]) == 0.0
    assert intent.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert intent.cosine([1.0, 2.0], [1.0]) == 0.0


def test_a_score_needs_one_vector_for_every_reference():
    assert intent.score([]) == 0.0
    assert intent.score([[1.0, 0.0]]) == 0.0


def _vectors(said, asking, ordinary):
    """A shout, then one per ASKING_LIKE, then one per ORDINARY_LIKE."""
    return ([said] + [asking] * len(intent.ASKING_LIKE)
            + [ordinary] * len(intent.ORDINARY_LIKE))


def test_the_score_is_asking_minus_ordinary():
    # Squarely on the asking side: 1 against 0.
    assert intent.score(_vectors([1.0, 0.0], [1.0, 0.0], [0.0, 1.0])) == pytest.approx(1.0)
    # Squarely on the ordinary side.
    assert intent.score(_vectors([0.0, 1.0], [1.0, 0.0], [0.0, 1.0])) == pytest.approx(-1.0)


def test_a_shout_near_both_meanings_scores_near_zero():
    # This is the whole point of subtracting. Every short football instruction
    # is close to every other, and closeness alone cannot tell them apart.
    close = intent.score(_vectors([1.0, 1.0], [1.0, 0.9], [0.9, 1.0]))
    assert abs(close) < 0.05


@pytest.mark.skipif(not os.environ.get("ARENA_EMBED_CHECK"),
                    reason="needs Vertex credentials; set ARENA_EMBED_CHECK=1")
async def test_the_threshold_sits_between_the_two_groups(monkeypatch):
    """Where `intent.THRESHOLD` comes from. Run it when the model changes.

    Not part of the ordinary suite: it costs an API call per phrase and needs
    credentials. It is here so the number is reproducible rather than folklore.

        ARENA_EMBED_CHECK=1 GOOGLE_CLOUD_PROJECT=... uv run pytest \\
            tests/test_intent.py -k threshold -s

    In the arena the token comes from the instance's metadata server. On a
    laptop there is no such thing, so the credentials gcloud already has stand
    in for it.
    """
    if not os.environ.get("ARENA_EMBED_TOKEN"):
        token = subprocess.run(["gcloud", "auth", "print-access-token"],
                               capture_output=True, text=True).stdout.strip()
    else:
        token = os.environ["ARENA_EMBED_TOKEN"]
    assert token, "no access token; run `gcloud auth login`"

    async def borrowed():
        return token

    monkeypatch.setattr(intent, "_token", borrowed)

    # Two calls rather than one per phrase: the references once, then the whole
    # corpus in a batch, and the arithmetic here. Twenty-two round trips does
    # not fit in this suite's timeout and would not tell us anything more.
    references = await intent.embed([*intent.ASKING_LIKE, *intent.ORDINARY_LIKE])
    corpus = await intent.embed([*ASKING, *NOT_ASKING])
    assert references and corpus, "the embedding call failed"

    def scored(vector):
        return intent.score([vector, *references])

    asking = [scored(v) for v in corpus[:len(ASKING)]]
    ordinary = [scored(v) for v in corpus[len(ASKING):]]
    fires = [s for s in asking if s >= intent.THRESHOLD]
    wrongly = [s for s in ordinary if s >= intent.THRESHOLD]
    print(f"\nasking:   min {min(asking):.3f}  max {max(asking):.3f}  "
          f"fires {len(fires)}/{len(asking)}")
    print(f"ordinary: min {min(ordinary):.3f}  max {max(ordinary):.3f}  "
          f"wrongly fires {len(wrongly)}/{len(ordinary)}")
    print(f"threshold {intent.THRESHOLD}")
    # Precision is the one that has to be perfect: a manager whose ordinary
    # shout quietly slows the opposition has had their match taken off them.
    # Recall is allowed to miss the odd phrasing -- "hobble the opposition,
    # don't let on" scores +0.111 and is the one it misses.
    assert not wrongly, "an ordinary football shout would fire this"
    assert len(fires) >= len(asking) - 1, "too many ways of asking go unheard"
