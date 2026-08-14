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

Both tools append an entry to one file per room and dugout. Locally that file
lives under frontend/public/player_state/, which Vite serves. Deployed, the path
is an in-memory volume shared with the arena, set via PLAYER_STATE_DIR. The
browser polls the arena for it and shows a top-right notification toast. There
is no roster/gameplay change for now -- this is notification-only.
"""

import json
import os
import time

from mcp.server.fastmcp import FastMCP

# Resolve paths relative to this file (matches the convention in agent.py).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where injuries and substitution requests are written. Locally this defaults
# to the pitch's public directory, which Vite serves. Deployed, the coach and
# the arena are two containers in one instance with a shared in-memory volume,
# and this points at the mount: the specialist writes here and the arena serves
# what it finds.
PLAYER_STATE_DIR = os.environ.get(
    "PLAYER_STATE_DIR", os.path.join(BASE_DIR, "../frontend/public/player_state"))

VALID_ROLES = {"defender", "midfielder", "forward", "goalkeeper"}
VALID_TEAMS = ("blue", "red")

# Duplicated from specialist_agents.arena_client because this module is also
# launched as a standalone script, which puts a relative import out of reach.
DEFAULT_ROOM = "WRKS"
DEFAULT_TEAM = "blue"

mcp = FastMCP("football-condition")


def substitutions_path(room: str, team: str) -> str:
    """Where this dugout's injuries live.

    One file for the whole venue meant a knock in one match subbed a player off
    in another. Room and team come from a language model, so an unrecognised
    one falls back to the workshop rather than becoming part of a path.
    """
    if not room.isalnum() or len(room) > 8:
        room = DEFAULT_ROOM
    if team not in VALID_TEAMS:
        team = DEFAULT_TEAM
    return os.path.join(PLAYER_STATE_DIR, "substitutions", f"{room.upper()}__{team}.json")


def _write_entry(role: str, entry: dict,
                 room: str = DEFAULT_ROOM, team: str = DEFAULT_TEAM) -> None:
    """Merge one entry into this dugout's file."""
    path = substitutions_path(room, team)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    data[role] = entry

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


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

    entry = {
        "action": "injury",
        "severity": severity,
        "reason": f"{severity} injury",
        "ts": time.time(),
    }
    _write_entry(role, entry, room, team)
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

    entry = {
        "action": "substitute",
        "reason": reason,
        "ts": time.time(),
    }
    _write_entry(role, entry, room, team)
    print(f"--> [MCP] {role.upper()} requested a substitution ({reason}).")
    return f"Logged: {role} requested a substitution ({reason}). Bench notified."


if __name__ == "__main__":
    # Default transport is stdio, which is what the ADK McpToolset spawns.
    mcp.run()
