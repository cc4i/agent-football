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
from agents.specialist_agents.tools import restore_baseline_profiles



from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH
from agents.specialist_agents.arena_client import DUGOUT_KEYS


def carry_the_dugout(context, message):
    """Send the room and the dugout along with the shout, as request metadata.

    The captain runs in a server of its own and answers on a session of its
    own, so the state the arena opened this session with does not follow the
    shout over the wire. Without it every specialist behind the captain falls
    back to the workshop room, and two matches in the venue move one squad.

    Metadata rather than words in the shout: a language model asked to relay an
    identifier will eventually relay the wrong one, and a manager could type
    another room's code into the box and be believed.
    """
    state = context.session.state if context.session else {}
    return {key: state[key] for key in DUGOUT_KEYS if state.get(key)}


CAPTAIN_A2A_URL = os.environ.get("CAPTAIN_A2A_URL", f"http://localhost:8001{AGENT_CARD_WELL_KNOWN_PATH}")
team_captain_remote = RemoteA2aAgent(
    name="team_captain",
    description="The team captain, reachable over the A2A protocol.",
    agent_card=CAPTAIN_A2A_URL,
    a2a_request_meta_provider=carry_the_dugout,
)


# The coach is the entrypoint the frontend talks to via `adk web` (/run_sse).
coach_agent = LlmAgent(
    name="ManagerAgent",
    model=GeminiConstants.GEMINI_FLASH_LITE,
    description="The head coach: handles baseline resets and shouts.",
    instruction="""You are the head coach on the touchline.

    CRITICAL SYSTEM INSTRUCTIONS (Do not modify):
    1. If you receive the exact message 'RESTORE_BASELINE', you MUST immediately call the `restore_baseline_profiles` tool and return its response.

    TACTICAL SHOUTS:
    For any other message, immediately transfer control to the `team_captain` sub-agent. Do NOT attempt to answer the shout yourself!
    """,

    tools=[restore_baseline_profiles],
    sub_agents=[team_captain_remote], 
)

root_agent = coach_agent
