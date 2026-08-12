"""The one validator. These rules used to exist in two copies."""

import json
from pathlib import Path

import pytest

import attributes


def test_a_change_inside_the_unit_range_is_accepted():
    assert attributes.validate("defender", {"aggression": 0.6}) == []


def test_an_attribute_the_role_does_not_have_is_named_in_the_reason():
    problems = attributes.validate("defender", {"wingspan": 0.5})
    assert problems == ["'wingspan' is not an attribute of the defender"]


def test_every_reason_comes_back_at_once_not_just_the_first():
    # The caller is usually a language model; it can only correct what it is told.
    problems = attributes.validate("defender", {"aggression": 5, "wingspan": 0.5})
    assert len(problems) == 2


def test_a_boolean_is_not_a_number():
    problems = attributes.validate("defender", {"aggression": True})
    assert problems == ["aggression must be a number, got True"]


def test_an_attribute_with_real_units_uses_its_own_band():
    assert attributes.validate("defender", {"tackleCooldown": 600}) == []
    assert attributes.validate("defender", {"tackleCooldown": 0.9}) == [
        "tackleCooldown=0.9 is outside 100.0 to 2000.0"
    ]


def test_an_unlisted_attribute_above_one_may_move_within_twice_its_baseline():
    # The shipped files hold near-duplicates like decisionsDelay=150 that no
    # hardcoded list will ever keep up with.
    assert attributes.range_for("decisionsDelay", 150) == (0.0, 300.0)
    assert attributes.range_for("aggression", 0.8) == (0.0, 1.0)


def test_an_unknown_role_is_refused_rather_than_used_as_a_filename():
    problems = attributes.validate("../../etc/passwd", {})
    assert problems == [
        "unknown role '../../etc/passwd', expected one of "
        "defender, midfielder, forward, goalkeeper"
    ]


def test_changes_that_are_not_an_object_are_refused():
    assert attributes.validate("defender", [1, 2]) == [
        "changes must be an object, got list"
    ]


def test_a_caller_cannot_mutate_the_cached_baseline():
    first = attributes.baseline_for("defender")
    first["aggression"] = 99
    assert attributes.baseline_for("defender")["aggression"] != 99


def test_baseline_for_refuses_a_role_it_does_not_know():
    with pytest.raises(ValueError):
        attributes.baseline_for("striker")


def test_the_arena_baselines_match_the_ones_the_pitch_ships():
    # Two copies of a starting profile drift. This is the tripwire until the
    # pitch reads its profiles from the arena in step 3.
    shipped_dir = (Path(__file__).resolve().parents[2]
                   / "game" / "frontend" / "public" / "player_state")
    for role in attributes.ROLES:
        shipped = json.loads((shipped_dir / f"{role}_baseline.json").read_text())
        assert attributes.baseline_for(role) == shipped, role
