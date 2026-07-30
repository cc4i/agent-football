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

# =====================================================================
# Head Coach Agent (ManagerAgent) - TEMPLATE
# =====================================================================


import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from google.adk.agents.llm_agent import LlmAgent
from agents.constants import GeminiConstants
from agents.specialist_agents.tools import backup_baseline_profiles, restore_baseline_profiles



from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH


CAPTAIN_A2A_URL = os.environ.get("CAPTAIN_A2A_URL", f"http://localhost:8001{AGENT_CARD_WELL_KNOWN_PATH}")
team_captain_remote = RemoteA2aAgent(
    name="team_captain",
    description="The team captain, reachable over the A2A protocol.",
    agent_card=CAPTAIN_A2A_URL,
)


# The coach is the entrypoint the frontend talks to via `adk web` (/run_sse).
coach_agent = LlmAgent(
    name="ManagerAgent",
    model=GeminiConstants.GEMINI_FLASH_LITE,
    description="The head coach: handles baseline backups/resets and shouts.",
    instruction="""You are the head coach on the touchline. 
    
    CRITICAL SYSTEM INSTRUCTIONS (Do not modify):
    1. If you receive the exact message 'BACKUP_BASELINE', you MUST immediately call the `backup_baseline_profiles` tool and return its response.
    2. If you receive the exact message 'RESTORE_BASELINE', you MUST immediately call the `restore_baseline_profiles` tool and return its response.
    
    TACTICAL SHOUTS:
    For any other message, immediately transfer control to the `team_captain` sub-agent. Do NOT attempt to answer the shout yourself!
    """,
    
    tools=[backup_baseline_profiles, restore_baseline_profiles],
    sub_agents=[team_captain_remote], 
)

root_agent = coach_agent
