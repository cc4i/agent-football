"""The one validator. These rules used to exist in two copies."""

import json
import re
from pathlib import Path

import pytest

import attributes

GAME_JS = Path(__file__).parents[2] / "game" / "frontend" / "src" / "game.js"


def _engine_vocabulary():
    """The attribute names `game.js` seeds its squads from, role by role.

    Read out of the source rather than duplicated here, because the point of
    the test that uses it is to notice when the two stop agreeing.
    """
    source = GAME_JS.read_text()
    start = source.index("const hardcodedDefaults")
    block = source[start:source.index("};", start)]
    vocabulary = {}
    for role in attributes.ROLES:
        segment = block[block.index(f"{role}:"):]
        segment = segment[:segment.index("}")]
        vocabulary[role] = set(re.findall(r"(\w+)\s*:", segment)) - {role}
    return vocabulary


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


def test_the_rules_are_published_role_by_role(client):
    # The dugout tunes against these. It used to keep a second copy of them,
    # which is exactly how one of the two ends up wrong.
    roles = client.get("/api/attributes").json()["roles"]
    assert set(roles) == set(attributes.ROLES)
    assert set(roles["defender"]) == set(attributes.simulated_baseline_for("defender"))


def test_every_published_attribute_carries_its_shipped_value_and_its_band(client):
    defender = client.get("/api/attributes").json()["roles"]["defender"]
    assert defender["aggression"] == {
        "baseline": attributes.baseline_for("defender")["aggression"],
        "min": 0.0, "max": 1.0}
    assert defender["tackleCooldown"]["min"] == 100.0
    assert defender["tackleCooldown"]["max"] == 2000.0


def test_a_value_at_either_end_of_a_published_band_is_one_the_validator_takes(client):
    # The band is shown to a tuner so it never proposes a number that was
    # always going to be refused. That only holds if both come from one place.
    for role, bands in client.get("/api/attributes").json()["roles"].items():
        for name, band in bands.items():
            assert attributes.validate(role, {name: band["min"]}) == []
            assert attributes.validate(role, {name: band["max"]}) == []


def test_the_simulated_set_is_exactly_what_the_engine_reads():
    # The guard on the whole idea. `game.js` seeds both squads from
    # `hardcodedDefaults`, so that object is the engine's vocabulary; if it
    # gains an attribute nobody may write it, and if it loses one every write
    # to it is silently doing nothing. Either way this should fail first.
    if not GAME_JS.exists():
        pytest.skip("game.js is not in this image")
    for role, reads in _engine_vocabulary().items():
        assert set(attributes.SIMULATED[role]) == reads, (
            f"the {role}'s simulated set and game.js have drifted apart")


def test_an_attribute_the_engine_never_reads_is_refused_not_quietly_kept():
    # Measured on the venue: 50 shouts out of 50 wrote
    # midfielder.forwardPassProbability, which game.js has never read. It
    # validated, it reached the log, the dugout drew it moving, and the
    # football did not change.
    assert "forwardPassProbability" in attributes.baseline_for("midfielder")
    problems = attributes.validate("midfielder", {"forwardPassProbability": 1.0})
    assert "'forwardPassProbability' is not simulated and would change nothing" in problems


def test_a_refusal_names_what_the_role_could_have_been_told_instead():
    # The caller is usually a language model with one shout's worth of time.
    problems = attributes.validate("forward", {"pace": 1.0})
    assert any(line.startswith("the forward is simulated on: ") for line in problems)
    assert "shotRange" in problems[-1]


def test_a_write_mixing_a_real_lever_with_a_dead_one_lands_neither():
    # A patch is all or nothing, so the agent is told about both at once and
    # can send the good half again by itself.
    problems = attributes.validate("forward", {"shotRange": 1.0, "pace": 1.0})
    assert "'pace' is not simulated and would change nothing" in problems
    assert not any("shotRange" in line and "outside" in line for line in problems)


def test_the_published_bands_offer_no_lever_with_nothing_on_the_end(client):
    for role, bands in client.get("/api/attributes").json()["roles"].items():
        assert set(bands) <= set(attributes.SIMULATED[role])
        assert set(bands) == set(attributes.simulated_baseline_for(role))


def test_the_shipped_baselines_still_carry_the_whole_squad():
    # Seeding a room and resetting one both write the file as it stands. Only
    # what may be *changed* narrowed; what a player *is* did not.
    assert "pace" in attributes.baseline_for("forward")
    assert "pace" not in attributes.simulated_baseline_for("forward")


def test_every_baseline_attribute_is_named_like_the_game_reads_it():
    # A baseline is also the list of names a squad will accept an instruction
    # about. "-aggression" once sat beside "aggression" in the defender file:
    # the validator took a change to it, the relay showed the manager a green
    # delta, and the pitch went on reading the other one.
    camel = re.compile(r"[a-z][a-zA-Z]*")
    misnamed = {
        f"{role}.{name}"
        for role in attributes.ROLES
        for name in attributes.baseline_for(role)
        if not camel.fullmatch(name)
    }
    assert misnamed == set()
