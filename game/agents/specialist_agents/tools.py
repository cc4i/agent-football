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

import json
import os
import shutil
import sys

from google.adk.tools import ToolContext

from . import arena_client
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Resolve paths relative to this file.
# BASE_DIR is game/agents/specialist_agents/
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYER_STATE_DIR = os.path.abspath(os.path.join(BASE_DIR, "../../frontend/public/player_state"))
MCP_SERVER_PATH = os.path.abspath(os.path.join(BASE_DIR, "../football_mcp_server.py"))

def initialize_profiles():
    """Bootstraps the individual JSON files if they don't exist."""
    os.makedirs(PLAYER_STATE_DIR, exist_ok=True)

    default_profiles = {
        "defender": {
            "speed": 0.8,
            "tackleRadius": 0.8,
            "tackleCooldown": 0.2,
            "passProbability": 0.85,
            "passRange": 0.7,
            "shotPower": 0.8,
            "shotRange": 0.7,
            "aggression": 0.8,
            "defensePositioning": 0.8,
            "attackPositioning": 0.8,
            "decisionDelay": 150,
            "pressingIntensity": 0.9,
            "formationDiscipline": 0.4,
            "passRiskTolerance": 0.7,
            "dribbleTendency": 0.6,
            "recoverySpeedMultiplier": 1.1,
            "supportRunFrequency": 0.8,
            "widthPreference": 0.6,
            "interceptionRadius": 0.8,
            "foulProbability": 0.3,
            "counterAttackUrgency": 0.9,
            "lineHeight": 0.8,
            "clearance": 0.3,
            "longPassing": 0.7,
            "tackleAggression": 0.8,
            "foulRate": 0.7,
            "kickPower": 0.9,
            "passAccuracy": 0.85
        },
        "midfielder": {
            "speed": 0.85,
            "tackleRadius": 0.8,
            "tackleCooldown": 0.3,
            "passProbability": 0.9,
            "passRange": 0.65,
            "shotPower": 0.8,
            "shotRange": 0.75,
            "aggression": 0.8,
            "defensePositioning": 0.3,
            "attackPositioning": 0.85,
            "decisionDelay": 0.1,
            "pressingIntensity": 0.5,
            "formationDiscipline": 0.9,
            "passRiskTolerance": 0.75,
            "dribbleTendency": 0.65,
            "recoverySpeedMultiplier": 1.3,
            "supportRunFrequency": 0.85,
            "widthPreference": 0.5,
            "interceptionRadius": 0.8,
            "foulProbability": 0.2,
            "counterAttackUrgency": 0.85,
            "dropDeepFrequency": 0.3,
            "defensiveFocus": 0.3,
            "defensiveCover": 0.8,
            "shootingUrgency": 0.8,
            "forwardPassProbability": 0.8,
            "defensiveWorkRate": 0.8,
            "forwardRuns": 0.85,
            "defensiveContribution": 0.35,
            "creativeFreedom": 0.75,
            "positionalDiscipline": 0.9,
            "shooting": 0.8,
            "clearanceFrequency": 0.2,
            "longPassProbability": 0.7,
            "interceptionFrequency": 0.8,
            "defensiveCoverage": 0.8,
            "foulFrequency": 0.9,
            "tackleIntensity": 0.8,
            "dropDeepPreference": 0.3,
            "defensiveSupport": 0.75
        },
        "forward": {
            "speed": 0.95,
            "tackleRadius": 0.7,
            "tackleCooldown": 0.2,
            "passProbability": 0.2,
            "passRange": 0.85,
            "shotPower": 0.95,
            "shotRange": 0.85,
            "aggression": 0.85,
            "defensePositioning": 0.1,
            "attackPositioning": 0.98,
            "decisionDelay": 0.05,
            "pressingIntensity": 0.4,
            "formationDiscipline": 0.3,
            "passRiskTolerance": 0.8,
            "dribbleTendency": 0.8,
            "recoverySpeedMultiplier": 0.9,
            "supportRunFrequency": 0.95,
            "widthPreference": 0.5,
            "interceptionRadius": 30,
            "foulProbability": 0.1,
            "counterAttackUrgency": 0.95,
            "defensiveAwareness": 0.1,
            "acceleration": 0.95,
            "pace": 0.95,
            "finishing": 0.98,
            "defensiveWorkRate": 0.1,
            "shortPassing": 0.85,
            "defensiveContribution": 0.1
        },
        "goalkeeper": {
            "speed": 0.9,
            "tackleRadius": 0.9,
            "tackleCooldown": 0.1,
            "passProbability": 0.9,
            "passRange": 0.5,
            "shotPower": 0.95,
            "shotRange": 0.9,
            "aggression": 0.8,
            "defensePositioning": 0.9,
            "attackPositioning": 0.9,
            "decisionDelay": 200,
            "diveChance": 0.8,
            "trackingSpeed": 0.9,
            "pressingIntensity": 0.9,
            "formationDiscipline": 0.9,
            "passRiskTolerance": 0.8,
            "dribbleTendency": 0.7,
            "recoverySpeedMultiplier": 1.0,
            "supportRunFrequency": 0.8,
            "widthPreference": 0.5,
            "interceptionRadius": 0.9,
            "foulProbability": 0.05,
            "counterAttackUrgency": 0.9,
            "positioning": 0.9,
            "rushOut": 0.8,
            "stayOnLine": 0.2,
            "sweepAggression": 0.9,
            "linePositioning": 0.2,
            "sweeperTendency": 0.9,
            "distributionAccuracy": 0.9,
            "sweepingAggression": 0.9,
            "runOutChance": 0.8,
            "discipline": 0.9,
            "joinAttack": 0.8,
            "clearance": 0.95,
            "kickLength": 0.9,
            "sweeperKeeper": 0.9,
            "foulTendency": 0.5,
            "kickPower": 0.9,
            "distributionRange": 0.6,
            "sweeperStyle": 0.95,
            "positioningRange": 0.8,
            "rushingOut": 0.8
        }
    }

    for role, data in default_profiles.items():
        file_path = os.path.join(PLAYER_STATE_DIR, f"{role}.json")
        if not os.path.exists(file_path):
            print(f"Creating default {file_path}...")
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)

# Ensure the profiles exist as soon as this module is imported
initialize_profiles()

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

def make_condition_toolset() -> list:
    """Build an MCP toolset (stdio) exposing the injury/substitution tools.

    A fresh toolset per player keeps each agent's MCP session isolated. The
    server is spawned on demand with the same Python interpreter running ADK.
    """
    if USE_REAL_MCP_SERVER:
        toolset = McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command=sys.executable,
                    args=[MCP_SERVER_PATH],
                ),
            ),
            tool_filter=["report_injury", "request_substitution"],
        )
        return [toolset]
    else:
        return [dummy_report_injury, dummy_request_substitution]


# Shared guidance appended to every outfield player about self-reporting condition.
CONDITION_GUIDANCE = """

    CONDITION SELF-CHECK:
    The captain may relay a fitness/tiredness note about you. If it says you are
    badly tired/exhausted, call the `request_substitution` MCP tool with your role
    and reason 'tired'. If it says you are injured/hurt, call the `report_injury`
    MCP tool with your role and a short severity. Only call these when clearly
    warranted -- a small knock or mild tiredness does NOT need a tool call.
"""

def backup_baseline_profiles() -> str:
    """Copies the current player profiles to baseline backup files (capturing LAB01 state)."""
    try:
        # VALID_ROLES is defined as a set in football_mcp_server, but we can define it locally
        valid_roles = {"defender", "midfielder", "forward", "goalkeeper"}
        for role in valid_roles:
            src = os.path.join(PLAYER_STATE_DIR, f"{role}.json")
            dst = os.path.join(PLAYER_STATE_DIR, f"{role}_baseline.json")
            if os.path.exists(src):
                shutil.copyfile(src, dst)
        print("--> [SYSTEM] Captured LAB01 starting profiles as baseline backup.")
        return "Success: Baseline backup created."
    except Exception as e:
        return f"Error backing up baseline: {str(e)}"

def restore_baseline_profiles() -> str:
    """Restores the player profiles from the baseline backup files, resetting mid-game shouts."""
    try:
        valid_roles = {"defender", "midfielder", "forward", "goalkeeper"}
        for role in valid_roles:
            src = os.path.join(PLAYER_STATE_DIR, f"{role}_baseline.json")
            dst = os.path.join(PLAYER_STATE_DIR, f"{role}.json")
            if os.path.exists(src):
                shutil.copyfile(src, dst)
                
        # Also clear any active substitutions/injuries
        sub_file = os.path.join(PLAYER_STATE_DIR, "substitutions.json")
        if os.path.exists(sub_file):
            try:
                os.remove(sub_file)
            except OSError:
                pass
            
        print("--> [SYSTEM] Restored profiles to LAB01 starting baseline.")
        return "Success: Restored to LAB01 starting baseline."
    except Exception as e:
        return f"Error restoring baseline: {str(e)}"
