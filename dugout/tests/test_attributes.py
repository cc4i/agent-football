import pytest

import arena
import attributes
from attributes import ROLES, band, bands

PUBLISHED = {
    "defender": {
        "aggression": {"baseline": 0.6, "min": 0.0, "max": 1.0},
        "tackleCooldown": {"baseline": 800.0, "min": 100.0, "max": 2000.0},
    },
    "midfielder": {"passRange": {"baseline": 0.7, "min": 0.0, "max": 1.0}},
    "forward": {"finishing": {"baseline": 0.5, "min": 0.0, "max": 1.0}},
    "goalkeeper": {"reflexes": {"baseline": 0.8, "min": 0.0, "max": 1.0}},
}


@pytest.fixture(autouse=True)
def a_fresh_process(monkeypatch):
    # The cache outlives a call by design, so each test starts as a new one
    # would: nothing remembered, and an arena that answers.
    monkeypatch.setattr(attributes, "_rules", None)
    monkeypatch.setattr(arena, "rules", lambda: PUBLISHED)


@pytest.fixture
def asked(monkeypatch):
    """Count the questions actually put to the arena."""
    times = []

    def answer():
        times.append(1)
        return PUBLISHED

    monkeypatch.setattr(arena, "rules", answer)
    return times


def test_roles_are_the_four_players():
    assert ROLES == ("defender", "midfielder", "forward", "goalkeeper")


def test_the_rules_come_from_the_arena():
    assert bands("forward")["finishing"]["baseline"] == 0.5


def test_the_arena_is_asked_once_and_the_answer_is_kept(asked):
    # Every tuner call and every delta reads a band. The rules the game was
    # built with cannot change under a running arena, so asking each time
    # would be a round trip per attribute for an answer that never moves.
    for role in ROLES:
        bands(role)
    assert len(asked) == 1


def test_a_new_session_asks_again(asked):
    bands("forward")
    attributes.forget()
    bands("forward")
    assert len(asked) == 2


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        bands("striker")


def test_an_attribute_carries_the_band_the_arena_published():
    assert band("defender", "tackleCooldown") == {
        "baseline": 800.0, "min": 100.0, "max": 2000.0}


def test_an_attribute_the_arena_never_heard_of_falls_back_to_a_weight():
    # Only a shout can produce one, because the arena refuses a tuner that
    # names it. It is drawn as a weight with no shipped tick rather than
    # failing the panel it appears in.
    assert band("forward", "invented") == {
        "baseline": None, "min": 0.0, "max": 1.0}


def test_a_role_the_arena_did_not_publish_has_no_attributes(monkeypatch):
    monkeypatch.setattr(arena, "rules", lambda: {})
    assert bands("forward") == {}
