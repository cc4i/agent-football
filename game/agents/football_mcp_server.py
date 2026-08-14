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

"""
Football MCP Server
====================

A small Model Context Protocol (MCP) server that the individual player agents
connect to (over stdio) so they can report their own condition during a match.

Exposed tools:
  - report_injury(role, severity)     -> log an injury for a role
  - request_substitution(role, reason)-> log a substitution request for a role

Both post the report to the arena, which logs it against the room and tells
everything watching that match. It used to be a JSON file beside the pitch,
polled every two seconds by whichever browser happened to be hosting: that
reached one browser and nothing else, and it would have reached nothing at all
once physics moved off the browser and onto the grounds farm. There is still no
roster or gameplay change -- this is notification-only.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

VALID_ROLES = {"defender", "midfielder", "forward", "goalkeeper"}
VALID_TEAMS = ("blue", "red")

# Duplicated from specialist_agents.arena_client, along with the small request
# below, because this module is also launched as a standalone script -- which
# puts a relative import out of reach.
DEFAULT_URL = "http://127.0.0.1:8003"
DEFAULT_ROOM = "WRKS"
DEFAULT_TEAM = "blue"
TIMEOUT_SECONDS = 5

mcp = FastMCP("football-condition")


def whose_match(room: str, team: str) -> tuple[str, str]:
    """Which room and dugout this report belongs to.

    One file for the whole venue meant a knock in one match subbed a player off
    in another; a room event cannot do that, because it is addressed to a room.
    Both still come from a language model, so an unrecognised one falls back to
    the workshop rather than being posted at a match somewhere in the building.
    """
    if not room.isalnum() or len(room) > 8:
        room = DEFAULT_ROOM
    if team not in VALID_TEAMS:
        team = DEFAULT_TEAM
    return room.upper(), team


def report(role: str, action: str, detail: str, room: str, team: str) -> str:
    """Tell the arena about one player's condition. Returns "" or why it failed.

    Everything that happens in a match goes in that match's log, and this is the
    last thing that did not. In the log it reaches both dugouts on their phones,
    the big screen's rail, and any screen that cuts to this match afterwards --
    none of which the file it replaced could do.
    """
    room, team = whose_match(room, team)
    token = os.environ.get("ARENA_SERVICE_TOKEN", "")
    if not token:
        return "ARENA_SERVICE_TOKEN is unset, so the arena refuses writes from the agents"

    url = "{}/api/rooms/{}/substitution".format(
        os.environ.get("ARENA_URL", DEFAULT_URL).rstrip("/"),
        urllib.parse.quote(room, safe=""))
    asked = urllib.request.Request(
        url,
        data=json.dumps({"team": team, "role": role,
                         "action": action, "detail": detail}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Arena-Service": token},
    )
    try:
        with urllib.request.urlopen(asked, timeout=TIMEOUT_SECONDS):
            return ""
    except urllib.error.HTTPError as refusal:
        return f"the arena refused the report ({refusal.code})"
    except (urllib.error.URLError, TimeoutError, OSError) as unreachable:
        return f"the arena did not answer ({unreachable})"


@mcp.tool()
def report_injury(role: str, severity: str = "knock",
                  room: str = DEFAULT_ROOM, team: str = DEFAULT_TEAM) -> str:
    """Report an injury for a player role so the coaching staff is notified.

    Args:
        role: One of 'defender', 'midfielder', 'forward', 'goalkeeper'.
        severity: Short description of how bad it is (e.g. 'knock', 'strain',
            'serious'). Defaults to 'knock'.
        room: The match this player is in. Defaults to the workshop.
        team: The dugout this player sits in, 'blue' or 'red'.
    """
    role = (role or "").strip().lower()
    if role not in VALID_ROLES:
        return f"Error: unknown role '{role}'. Use one of {sorted(VALID_ROLES)}."

    failed = report(role, "injury", severity, room, team)
    if failed:
        return f"Error: {failed}."
    print(f"--> [MCP] {role.upper()} reported an injury ({severity}).")
    return f"Logged: {role} reported a {severity} injury. Medical staff notified."


@mcp.tool()
def request_substitution(role: str, reason: str = "tired",
                         room: str = DEFAULT_ROOM, team: str = DEFAULT_TEAM) -> str:
    """Request a substitution for a player role (e.g. when too tired).

    Args:
        role: One of 'defender', 'midfielder', 'forward', 'goalkeeper'.
        reason: Short reason for the request (e.g. 'tired', 'tactical').
            Defaults to 'tired'.
        room: The match this player is in. Defaults to the workshop.
        team: The dugout this player sits in, 'blue' or 'red'.
    """
    role = (role or "").strip().lower()
    if role not in VALID_ROLES:
        return f"Error: unknown role '{role}'. Use one of {sorted(VALID_ROLES)}."

    failed = report(role, "substitution", reason, room, team)
    if failed:
        return f"Error: {failed}."
    print(f"--> [MCP] {role.upper()} requested a substitution ({reason}).")
    return f"Logged: {role} requested a substitution ({reason}). Bench notified."


if __name__ == "__main__":
    # Default transport is stdio, which is what the ADK McpToolset spawns.
    mcp.run()
