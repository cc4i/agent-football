# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys

from google.adk.tools import ToolContext

from . import arena_client
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment

# Resolve paths relative to this file.
# BASE_DIR is game/agents/specialist_agents/
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_SERVER_PATH = os.path.abspath(os.path.join(BASE_DIR, "../football_mcp_server.py"))

def update_profile(role: str, changes: dict, tool_context: ToolContext) -> str:
    """Move one player's attributes in this match's dugout.

    `changes` maps attribute names to new values. The arena validates every one
    of them and refuses the whole write if any is out of range, so a refusal
    comes back naming every problem at once and can be corrected in one go.

    The room and dugout come from the session rather than from a constant,
    because more than one match runs at a time.
    """
    room = tool_context.state.get("room_code") or arena_client.DEFAULT_ROOM
    team = tool_context.state.get("team") or arena_client.DEFAULT_TEAM
    try:
        result = arena_client.patch_profile(
            room, team, role, changes,
            actor=tool_context.state.get("actor") or "coach",
            reason=tool_context.state.get("reason") or "",
        )
    except arena_client.ArenaError as refusal:
        return f"Rejected: {refusal}"

    if not result["changed"]:
        return f"No change: the {team} {role} already had those values."
    moved = ", ".join(f"{key}={value}"
                      for key, value in sorted(result["changed"].items()))
    return f"Updated the {team} {role} in room {room}: {moved}"


# Change the flag below from False to True to enable the real FastMCP server subprocess:
USE_REAL_MCP_SERVER = True

def dummy_report_injury(role: str, severity: str = "knock") -> str:
    """Report that a player has sustained an injury.
    
    Args:
        role: The role of the player injured (e.g. 'forward', 'defender')
        severity: How bad the injury is (e.g. 'knock', 'pulled hamstring')
    """
    print(f"--> [DUMMY MCP] {role.upper()} reported an injury ({severity}).")
    return f"Successfully logged injury for {role}: {severity}"

def dummy_request_substitution(role: str, reason: str = "tired") -> str:
    """Request a substitution for a player.
    
    Args:
        role: The role of the player to be substituted (e.g. 'forward', 'midfielder')
        reason: Why the sub is needed (e.g. 'tired', 'tactical')
    """
    print(f"--> [DUMMY MCP] {role.upper()} requested a substitution ({reason}).")
    return f"Successfully logged substitution request for {role}: {reason}"

# What the condition server reads out of its environment to reach the arena.
CONDITION_SERVER_VARS = ("ARENA_SERVICE_TOKEN", "ARENA_URL")


def condition_server_env() -> dict:
    """The arena settings to hand the MCP server, of those that are set.

    Spawning it inherits nothing by default: the MCP client passes a fixed safe
    list -- HOME, PATH, SHELL and three others -- and drops the rest, so a
    captain server started with the token set spawned a server that could not
    see it and refused every injury it was asked to file. An unset variable is
    left out rather than passed empty, because an empty ARENA_URL would beat
    the server's own default and point it at nothing.
    """
    return {name: os.environ[name] for name in CONDITION_SERVER_VARS
            if os.environ.get(name)}


def make_condition_toolset() -> list:
    """Build an MCP toolset (stdio) exposing the injury/substitution tools.

    A fresh toolset per player keeps each agent's MCP session isolated. The
    server is spawned on demand with the same Python interpreter running ADK,
    and with the arena settings added to the safe list it would otherwise get.
    """
    if USE_REAL_MCP_SERVER:
        toolset = McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command=sys.executable,
                    args=[MCP_SERVER_PATH],
                    env={**get_default_environment(), **condition_server_env()},
                ),
            ),
            tool_filter=["report_injury", "request_substitution"],
        )
        return [toolset]
    else:
        return [dummy_report_injury, dummy_request_substitution]


# The two tools the MCP server exposes. They are filtered by name in
# `make_condition_toolset`, and stamped by name below.
CONDITION_TOOLS = ("report_injury", "request_substitution")


def stamp_the_room(tool, args, tool_context):
    """Put the room and the dugout on an MCP condition call.

    `update_profile` runs in this process and reads both from session state.
    These two do not: they are behind stdio in the MCP server, which has no way
    to reach ADK state, so they take the room as an argument. Nothing in the
    prompt told the model what to pass, so it passed nothing and every room's
    injuries were filed against the workshop.

    Asking for it in the prompt would make one room's toast depend on a
    language model remembering an argument. The room is not the model's to
    choose, so it is stamped here and any value the model did invent is
    overwritten. Returning None lets the call go on with the arguments as
    amended.
    """
    if tool.name not in CONDITION_TOOLS:
        return None
    args["room"] = tool_context.state.get("room_code") or arena_client.DEFAULT_ROOM
    args["team"] = tool_context.state.get("team") or arena_client.DEFAULT_TEAM
    return None


# Shared guidance appended to every outfield player about self-reporting condition.
CONDITION_GUIDANCE = """

    CONDITION SELF-CHECK:
    The captain may relay a fitness/tiredness note about you. If it says you are
    badly tired/exhausted, call the `request_substitution` MCP tool with your role
    and reason 'tired'. If it says you are injured/hurt, call the `report_injury`
    MCP tool with your role and a short severity. Only call these when clearly
    warranted - a small knock or mild tiredness does NOT need a tool call.
"""

def restore_baseline_profiles(tool_context: ToolContext) -> str:
    """Put this dugout's squad back to the shipped baseline.

    The lab starts every session from a known squad, which is what makes its
    stages repeatable. There is nothing to back up first: the baseline is
    shipped with the arena and a room is seeded from it when it is opened, so
    this is a reset rather than a restore of something captured earlier.
    """
    room = tool_context.state.get("room_code") or arena_client.DEFAULT_ROOM
    team = tool_context.state.get("team") or arena_client.DEFAULT_TEAM
    try:
        arena_client.reset_profiles(room, team)
    except arena_client.ArenaError as refusal:
        return f"Rejected: {refusal}"
    return f"Success: the {team} squad in room {room} is back to the shipped baseline."
