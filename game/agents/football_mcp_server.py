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

Both tools append an entry to:
  frontend/public/player_state/substitutions.json

The frontend already serves and polls that directory, so the browser picks the
entry up on its next poll and shows a top-right notification toast. There is no
roster/gameplay change for now -- this is notification-only.
"""

import json
import os
import time

from mcp.server.fastmcp import FastMCP

# Resolve paths relative to this file (matches the convention in agent.py).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYER_STATE_DIR = os.path.join(BASE_DIR, "../frontend/public/player_state")
SUBSTITUTIONS_FILE = os.path.join(PLAYER_STATE_DIR, "substitutions.json")

VALID_ROLES = {"defender", "midfielder", "forward", "goalkeeper"}

mcp = FastMCP("football-condition")


def _write_entry(role: str, entry: dict) -> None:
    """Atomically merge a single role entry into substitutions.json."""
    os.makedirs(PLAYER_STATE_DIR, exist_ok=True)

    data = {}
    if os.path.exists(SUBSTITUTIONS_FILE):
        try:
            with open(SUBSTITUTIONS_FILE, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    data[role] = entry

    with open(SUBSTITUTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


@mcp.tool()
def report_injury(role: str, severity: str = "knock") -> str:
    """Report an injury for a player role so the coaching staff is notified.

    Args:
        role: One of 'defender', 'midfielder', 'forward', 'goalkeeper'.
        severity: Short description of how bad it is (e.g. 'knock', 'strain',
            'serious'). Defaults to 'knock'.
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
    _write_entry(role, entry)
    print(f"--> [MCP] {role.upper()} reported an injury ({severity}).")
    return f"Logged: {role} reported a {severity} injury. Medical staff notified."


@mcp.tool()
def request_substitution(role: str, reason: str = "tired") -> str:
    """Request a substitution for a player role (e.g. when too tired).

    Args:
        role: One of 'defender', 'midfielder', 'forward', 'goalkeeper'.
        reason: Short reason for the request (e.g. 'tired', 'tactical').
            Defaults to 'tired'.
    """
    role = (role or "").strip().lower()
    if role not in VALID_ROLES:
        return f"Error: unknown role '{role}'. Use one of {sorted(VALID_ROLES)}."

    entry = {
        "action": "substitute",
        "reason": reason,
        "ts": time.time(),
    }
    _write_entry(role, entry)
    print(f"--> [MCP] {role.upper()} requested a substitution ({reason}).")
    return f"Logged: {role} requested a substitution ({reason}). Bench notified."


if __name__ == "__main__":
    # Default transport is stdio, which is what the ADK McpToolset spawns.
    mcp.run()
