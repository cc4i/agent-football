"""Guards the other way into player_state: the game's own specialist agents.

Stage 4b drives the coach shout bar, which hands a language model a direct
write into these files through update_profile. The game package has no test
harness of its own, so the guard is a standalone module and is exercised here.
"""

import importlib.util
import json

import pytest

from attributes import PLAYER_STATE_DIR

GUARD = (PLAYER_STATE_DIR.parent.parent.parent
         / "agents" / "specialist_agents" / "profile_guard.py")


def load_guard():
    spec = importlib.util.spec_from_file_location("profile_guard", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard():
    return load_guard()


@pytest.fixture
def state(tmp_path):
    (tmp_path / "defender_baseline.json").write_text(json.dumps({
        "defensePositioning": 0.7, "tackleCooldown": 600, "decisionDelay": 120,
    }))
    return str(tmp_path)


def test_a_sane_change_is_accepted(guard, state):
    assert guard.validate(state, "defender", {"defensePositioning": 0.9}) == []


def test_an_unknown_role_is_refused(guard, state):
    assert guard.validate(state, "striker", {"x": 1})


def test_a_role_cannot_walk_out_of_the_directory(guard, state):
    # role is interpolated into a filename, so this is the traversal guard.
    problems = guard.validate(state, "../../../../etc/passwd", {"a": 1})
    assert problems and "unknown role" in problems[0]


def test_an_attribute_the_role_does_not_have_is_refused(guard, state):
    assert guard.validate(state, "defender", {"wingspan": 0.5})


def test_a_value_over_its_range_is_refused(guard, state):
    # speed above 2.0 is read as absolute pixels per second by the simulation,
    # so an unchecked 999 does not buff the player, it breaks the match.
    assert guard.validate(state, "defender", {"defensePositioning": 999})


def test_a_unit_bearing_attribute_uses_its_own_range(guard, state):
    assert guard.validate(state, "defender", {"tackleCooldown": 600}) == []
    assert guard.validate(state, "defender", {"tackleCooldown": 99})


def test_a_non_number_is_refused(guard, state):
    assert guard.validate(state, "defender", {"defensePositioning": "high"})
    assert guard.validate(state, "defender", {"defensePositioning": True})


def test_changes_must_be_an_object(guard, state):
    assert guard.validate(state, "defender", ["defensePositioning"])


def test_the_real_baselines_all_validate_against_themselves(guard):
    # Every shipped baseline value must be inside the range the guard enforces,
    # or the game starts out in a state its own agents cannot restore.
    for role in guard.ROLES:
        baseline = json.loads(
            (PLAYER_STATE_DIR / f"{role}_baseline.json").read_text())
        numeric = {k: v for k, v in baseline.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        assert guard.validate(str(PLAYER_STATE_DIR), role, numeric) == []


def test_a_unit_bearing_attribute_the_table_does_not_know_is_inferred(guard, tmp_path):
    # The shipped midfielder carries both decisionDelay and decisionsDelay,
    # the second holding 150ms. Treating it as a 0-1 weight makes the game's
    # own starting values illegal.
    (tmp_path / "midfielder_baseline.json").write_text(
        json.dumps({"decisionsDelay": 150}))
    state = str(tmp_path)
    assert guard.validate(state, "midfielder", {"decisionsDelay": 150}) == []
    assert guard.validate(state, "midfielder", {"decisionsDelay": 80}) == []
    assert guard.validate(state, "midfielder", {"decisionsDelay": 9999})


def test_the_live_files_also_validate(guard):
    # Not just the baselines: whatever the game is running right now has to be
    # a legal state, or an agent that echoes a current value gets refused.
    for role in guard.ROLES:
        live = json.loads((PLAYER_STATE_DIR / f"{role}.json").read_text())
        numeric = {k: v for k, v in live.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        assert guard.validate(str(PLAYER_STATE_DIR), role, numeric) == [], role
