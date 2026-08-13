import pytest

import attributes
from deltas import describe_change, marker, with_markers

BANDS = {
    "finishing": {"baseline": 0.5, "min": 0.0, "max": 1.0},
    "shotPower": {"baseline": 0.5, "min": 0.0, "max": 1.0},
    "decisionDelay": {"baseline": 80.0, "min": 40.0, "max": 400.0},
    "tackleCooldown": {"baseline": 800.0, "min": 100.0, "max": 2000.0},
}


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    # What the arena would have answered. Cached in the module, so it is set
    # there rather than served over a fake socket for every delta.
    monkeypatch.setattr(
        attributes, "_rules", {role: BANDS for role in attributes.ROLES})


def test_only_attributes_that_moved_are_described():
    change = describe_change("forward", {"finishing": 0.5, "shotPower": 0.5},
                             {"finishing": 0.9, "shotPower": 0.5})
    assert [d["attribute"] for d in change["deltas"]] == ["finishing"]


def test_nothing_moving_is_no_change_at_all():
    assert describe_change("forward", {"finishing": 0.5},
                           {"finishing": 0.5}) is None


def test_a_delta_carries_the_shipped_baseline_and_the_band():
    change = describe_change("forward", {"finishing": 0.7}, {"finishing": 0.9})
    delta = change["deltas"][0]
    assert delta["before"] == 0.7
    assert delta["after"] == 0.9
    assert delta["baseline"] == 0.5
    assert (delta["min"], delta["max"]) == (0.0, 1.0)


def test_a_band_that_is_not_the_unit_range_comes_through_as_the_arena_gave_it():
    change = describe_change("defender", {"tackleCooldown": 800},
                             {"tackleCooldown": 600})
    delta = change["deltas"][0]
    assert (delta["min"], delta["max"]) == (100.0, 2000.0)


def test_the_reason_and_the_place_it_landed_travel_with_the_change():
    change = describe_change("defender", {"finishing": 0.5},
                             {"finishing": 0.8}, "hold a deeper line")
    assert change["role"] == "defender"
    assert change["where"] == "WRKS/blue/defender"
    assert change["reason"] == "hold a deeper line"


def test_an_attribute_the_arena_never_had_still_describes():
    # A shout writes through the game's own agents, which can introduce a key.
    change = describe_change("forward", {}, {"invented": 0.4})
    delta = change["deltas"][0]
    assert delta["before"] is None
    assert delta["baseline"] is None
    assert delta["after"] == 0.4
    assert (delta["min"], delta["max"]) == (0.0, 1.0)


def test_an_unknown_attribute_above_one_is_flagged_out_of_band():
    # No band means none to derive, so it falls back to the 0.0-1.0 weight
    # range. Every attribute without a unit is a weight, so a value above 1.0
    # under a name nothing recognises is anomalous and is drawn as such rather
    # than parked mid-track on an invented band.
    placed = with_markers(describe_change("forward", {}, {"invented": 1500}))
    delta = placed["deltas"][0]
    assert (delta["min"], delta["max"]) == (0.0, 1.0)
    assert delta["afterPct"] == 100.0
    assert delta["off"] is True


def test_a_non_numeric_value_is_not_a_delta():
    assert describe_change("forward", {"finishing": 0.5},
                           {"finishing": "fast"}) is None


def test_a_value_at_the_foot_of_its_range_sits_at_zero():
    assert marker(0.0, 0.0, 1.0) == (0.0, False)


def test_a_value_at_the_head_of_its_range_sits_at_one_hundred():
    assert marker(1.0, 0.0, 1.0) == (100.0, False)


def test_a_unit_bearing_range_is_measured_from_its_own_floor():
    # tackleCooldown runs 100 to 2000, so 1050 is the middle of the track.
    assert marker(1050.0, 100.0, 2000.0) == (50.0, False)


def test_a_value_outside_its_band_clamps_and_says_so():
    assert marker(1.4, 0.0, 1.0) == (100.0, True)
    assert marker(-0.2, 0.0, 1.0) == (0.0, True)


def test_a_collapsed_range_does_not_divide_by_zero():
    assert marker(5.0, 5.0, 5.0) == (0.0, True)


def test_markers_are_added_without_disturbing_the_description():
    placed = with_markers(
        describe_change("forward", {"finishing": 0.2}, {"finishing": 0.8}))
    delta = placed["deltas"][0]
    assert delta["beforePct"] == 20.0
    assert delta["afterPct"] == 80.0
    assert delta["baselinePct"] == 50.0
    assert delta["before"] == 0.2
    assert delta["after"] == 0.8


def test_a_missing_before_leaves_its_marker_off_the_track():
    placed = with_markers(describe_change("forward", {}, {"invented": 0.4}))
    delta = placed["deltas"][0]
    assert delta["beforePct"] is None
    assert delta["baselinePct"] is None
    assert delta["afterPct"] == 40.0
