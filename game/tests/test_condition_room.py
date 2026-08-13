"""The room an injury is filed against is stamped, not asked for.

The MCP condition tools take `room` and `team` as arguments because they run
behind stdio and cannot see ADK session state. Nothing in the prompt told the
model what to pass, so every room's injuries landed in the workshop's file.
"""

from types import SimpleNamespace

from agents.specialist_agents import tools


def _call(name, args, state):
    return tools.stamp_the_room(SimpleNamespace(name=name), args,
                                SimpleNamespace(state=state))


def test_stamps_the_room_and_dugout_from_session_state():
    args = {"role": "forward", "severity": "knock"}
    assert _call("report_injury", args, {"room_code": "ABCD", "team": "red"}) is None
    assert args["room"] == "ABCD"
    assert args["team"] == "red"


def test_overrides_a_room_the_model_invented():
    args = {"role": "forward", "room": "WRKS", "team": "blue"}
    _call("request_substitution", args, {"room_code": "ABCD", "team": "red"})
    assert args["room"] == "ABCD"
    assert args["team"] == "red"


def test_falls_back_to_the_workshop_when_there_is_no_room():
    args = {"role": "forward"}
    _call("report_injury", args, {})
    assert args["room"] == "WRKS"
    assert args["team"] == "blue"


def test_leaves_every_other_tool_alone():
    args = {"role": "forward", "changes": {"speed": 0.6}}
    _call("update_profile", args, {"room_code": "ABCD", "team": "red"})
    assert "room" not in args
