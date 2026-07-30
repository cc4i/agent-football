from subagents import SUBAGENTS
from tools.tuning import ROLE_BY_TUNING_TOOL


def test_there_is_one_subagent_per_role():
    assert [s.name for s in SUBAGENTS] == [
        "defender-tuner", "midfielder-tuner", "forward-tuner", "goalkeeper-tuner"]


def test_each_subagent_holds_exactly_one_tuning_tool():
    for sub in SUBAGENTS:
        tuning = [t for t in sub.tools if getattr(t, "__name__", "") in ROLE_BY_TUNING_TOOL]
        assert len(tuning) == 1


def test_a_subagent_cannot_reach_another_role_tool():
    forward = next(s for s in SUBAGENTS if s.name == "forward-tuner")
    names = {getattr(t, "__name__", "") for t in forward.tools}
    assert "tune_forward" in names
    assert "tune_defender" not in names


def test_subagents_can_read_the_match():
    for sub in SUBAGENTS:
        names = {getattr(t, "__name__", "") for t in sub.tools}
        assert "get_match_status" in names
        assert "read_player_stats" in names


def test_no_em_dash_in_subagent_instructions():
    for sub in SUBAGENTS:
        assert "—" not in sub.system_instructions
        assert "—" not in sub.description


def test_each_subagent_has_the_tuning_tool_for_its_own_role():
    expected = {
        "defender-tuner": "tune_defender",
        "midfielder-tuner": "tune_midfielder",
        "forward-tuner": "tune_forward",
        "goalkeeper-tuner": "tune_goalkeeper",
    }
    assert {s.name for s in SUBAGENTS} == set(expected)
    for sub in SUBAGENTS:
        tuning = [t for t in sub.tools
                  if getattr(t, "__name__", "") in ROLE_BY_TUNING_TOOL]
        assert len(tuning) == 1
        assert tuning[0].__name__ == expected[sub.name]
        assert expected[sub.name] in sub.system_instructions
