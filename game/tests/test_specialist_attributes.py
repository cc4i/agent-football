"""What a specialist is told it may write, against what the match reads.

The four prompts each carry a hand-typed list under "Here are the ONLY
attributes that exist". Those lists drifted: the midfielder's offered
`forwardPassProbability`, the forward's offered `finishing`, the goalkeeper's
offered six different ways to leave its line. None of them are read by
`game.js`, and measured over 50 shouts on the venue every single one spent a
write on the first of them.

The prompt is what an agent chooses from, so the prompt is where a name that
does not exist has to stop. These tests read both ends -- the list in the
prompt and `hardcodedDefaults` in the engine -- and fail when they disagree,
rather than leaving it to the next measurement run.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
GAME_JS = REPO / "game" / "frontend" / "src" / "game.js"
SPECIALISTS = REPO / "game" / "agents" / "specialist_agents"

ROLES = ("defender", "midfielder", "forward", "goalkeeper")

# The prompts name a role's own list; these are the variables holding them.
PROMPT_OF = {
    "defender": ("defender.py", "DEFENDER_INSTRUCTION"),
    "midfielder": ("midfielder.py", "MIDFIELDER_INSTRUCTION"),
    "forward": ("forward.py", "FORWARD_INSTRUCTION"),
    "goalkeeper": ("goalkeeper.py", "GOALKEEPER_INSTRUCTION"),
}


def engine_vocabulary():
    """Role to the attribute names `game.js` seeds its squads from."""
    source = GAME_JS.read_text()
    start = source.index("const hardcodedDefaults")
    block = source[start:source.index("};", start)]
    vocabulary = {}
    for role in ROLES:
        segment = block[block.index(f"{role}:"):]
        segment = segment[:segment.index("}")]
        vocabulary[role] = set(re.findall(r"(\w+)\s*:", segment)) - {role}
    return vocabulary


def offered_in_prompt(role):
    """The attribute names a role's prompt offers it, as a set.

    The list is the run of `- name (range)` bullets that follows the "ONLY
    attributes" heading, and it ends at the first line that is not one.
    """
    text = (SPECIALISTS / PROMPT_OF[role][0]).read_text()
    after = text[text.index("attributes that exist for the"):]
    offered = set()
    for line in after.splitlines()[1:]:
        found = re.fullmatch(r"- (\w+) \(.*\)", line.strip())
        if not found:
            if offered:
                break
            continue
        offered.add(found.group(1))
    return offered


@pytest.mark.parametrize("role", ROLES)
def test_a_specialist_is_offered_exactly_what_the_match_reads(role):
    if not GAME_JS.exists():
        pytest.skip("game.js is not in this image")
    assert offered_in_prompt(role) == engine_vocabulary()[role], (
        f"the {role}'s prompt and game.js disagree about what exists")


@pytest.mark.parametrize("role,gone", [
    ("midfielder", "forwardPassProbability"),
    ("midfielder", "shootingUrgency"),
    ("forward", "finishing"),
    ("forward", "pace"),
    ("goalkeeper", "sweeperKeeper"),
    ("goalkeeper", "stayOnLine"),
    ("defender", "lineHeight"),
])
def test_a_name_the_engine_never_had_is_not_offered(role, gone):
    assert gone not in offered_in_prompt(role)


def test_the_midfielder_is_told_in_words_that_its_favourite_lever_is_not_real():
    # Removing it from the list is not enough on its own: the model has been
    # reaching for this name unprompted, so the prompt says so outright.
    text = (SPECIALISTS / "midfielder.py").read_text()
    assert "There is no\nforwardPassProbability" in text


def test_the_shared_model_names_the_delivery_levers_that_exist():
    model = (SPECIALISTS / "tools.py").read_text().split(
        "SIMULATION_MODEL")[1].split("CONDITION_GUIDANCE")[0]
    delivery = model[model.index("speed up delivery"):]
    delivery = delivery[:delivery.index("\n\n")] if "\n\n" in delivery else delivery
    recommended = set(re.findall(r"`(\w+)`", delivery.split("- Only the attributes")[0]))
    assert recommended <= engine_vocabulary()["midfielder"]
    assert "forwardPassProbability" not in recommended


def test_the_shared_model_still_names_it_as_the_one_to_unlearn():
    # It is deliberately still in the text. The model reached for this name
    # unprompted in every measured shout, so saying nothing about it is not
    # the same as saying it is not real.
    model = (SPECIALISTS / "tools.py").read_text()
    assert "`forwardPassProbability` is the one to unlearn" in model
