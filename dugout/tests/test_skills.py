"""The skill is what turns tuning from guesswork into a winning change."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

import session
from skills import SKILLS_DIR, load_skills


def _arena_attributes():
    """The arena's own rules module, loaded from beside this checkout.

    The dugout keeps no copy of what may be written -- it asks the arena, which
    is the thing that accepts or refuses -- so a test about what the arena will
    take reads the arena rather than repeating it here.
    """
    path = Path(__file__).parents[2] / "arena" / "attributes.py"
    if not path.exists():
        pytest.skip("the arena is not checked out beside the dugout")
    spec = importlib.util.spec_from_file_location("arena_attributes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_skill_directory_is_on_the_agent_config():
    assert str(SKILLS_DIR) in session._build_config().skills_paths


def test_every_skill_has_a_name_and_a_description():
    skills = load_skills()
    assert skills
    for s in skills:
        assert s["name"]
        assert s["description"]
        assert s["body"].strip()


def test_the_winning_skill_is_present():
    assert any(s["name"] == "winning-the-match" for s in load_skills())


def test_skills_are_json_serialisable():
    json.dumps(load_skills())


def test_the_skill_teaches_the_scale_rule():
    # The multiplier-or-absolute rule is the one thing that makes a tuner
    # cripple its own player, so it has to survive any future edit.
    body = next(s for s in load_skills() if s["name"] == "winning-the-match")["body"]
    assert "2.0" in body
    assert "260" in body          # the forward's base speed
    assert "shotRange" in body


def test_no_em_dash_in_any_skill():
    for s in load_skills():
        assert "—" not in s["body"]
        assert "—" not in s["description"]


def test_the_skill_carries_the_measured_plan():
    # The plan is not a guess: it was measured at 4-1-3 against 0-1-7 for the
    # shipped squad. If someone edits the numbers, they need to re-measure.
    body = next(s for s in load_skills() if s["name"] == "winning-the-match")["body"]
    for value in ("interceptionRadius", "tackleCooldown", "0.95", "4 wins"):
        assert value in body


def test_the_plan_asks_for_nothing_the_arena_will_refuse():
    # A tuner follows this table literally, and a write lands all of its values
    # or none. One attribute the engine does not read used to sit in it and
    # would now take the two good changes in its row down with it.
    body = next(s for s in load_skills() if s["name"] == "winning-the-match")["body"]
    plan = body[body.index("| role | change to"):body.index("Why each one:")]
    asked = set(re.findall(r"`(\w+)` [0-9]", plan))
    assert asked, "the plan's table should name attributes with values"
    simulated = set(_arena_attributes().SIMULATED["goalkeeper"])  # the widest role
    assert asked <= simulated, f"the plan asks for {sorted(asked - simulated)}"
