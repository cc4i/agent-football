"""One tuner per role. The tool set is the guardrail: a tuner physically cannot
move another player's attributes, because it is not given that function."""

from google.antigravity.types import SubagentConfig

from tools.match import get_match_status, read_player_stats
from tools.tuning import TUNING_TOOL_BY_ROLE

_INSTRUCTIONS = (
    "You tune the {role} of the blue team during a live futsal match.\n"
    "Call get_match_status() and read_player_stats('{role}') first, so your "
    "change answers what is actually happening.\n"
    "Then call {tool}() exactly once. Change at most 3 attributes and give a "
    "one-line reason naming what you expect to improve.\n"
    "Every value must stay inside the min and max that read_player_stats "
    "reports for it. Most attributes are 0.0 to 1.0 weights. The arena holds "
    "the squad and refuses anything outside those limits, so a change that "
    "breaks one simply does not happen.\n"
    "You cannot edit any other player. Do not try."
)

SUBAGENTS = tuple(
    SubagentConfig(
        name=f"{role}-tuner",
        description=f"Tune the {role} in response to the live match state",
        system_instructions=_INSTRUCTIONS.format(role=role, tool=tool.__name__),
        tools=[get_match_status, read_player_stats, tool],
    )
    for role, tool in TUNING_TOOL_BY_ROLE.items()
)
