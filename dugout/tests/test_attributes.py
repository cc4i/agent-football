import pytest

from attributes import ROLES, allowed_attributes, range_for, validate_changes


def test_roles_are_the_four_players():
    assert ROLES == ("defender", "midfielder", "forward", "goalkeeper")


def test_allowlist_comes_from_the_baseline_file():
    keys = allowed_attributes("forward")
    assert "finishing" in keys
    assert "shotPower" in keys
    assert "notARealAttribute" not in keys


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        allowed_attributes("striker")


def test_unit_attributes_range_zero_to_one():
    assert range_for("finishing") == (0.0, 1.0)


def test_millisecond_attributes_have_their_own_range():
    assert range_for("tackleCooldown") == (100.0, 2000.0)
    assert range_for("decisionDelay") == (0.0, 500.0)
    assert range_for("recoverySpeedMultiplier") == (0.5, 2.0)


def test_valid_changes_produce_no_violations():
    assert validate_changes("forward", {"finishing": 0.8}) == []


def test_unknown_attribute_is_a_violation():
    violations = validate_changes("forward", {"nope": 0.5})
    assert len(violations) == 1
    assert "nope" in violations[0]


def test_out_of_range_value_is_a_violation():
    violations = validate_changes("forward", {"finishing": 1.4})
    assert len(violations) == 1
    assert "1.4" in violations[0]
    assert "0.0" in violations[0] and "1.0" in violations[0]


def test_non_numeric_value_is_a_violation():
    violations = validate_changes("forward", {"finishing": "fast"})
    assert len(violations) == 1
