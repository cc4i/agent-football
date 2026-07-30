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
# Team Captain Agent (TeamCaptain) - TEMPLATE
# =====================================================================


from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import AgentTool
from google.adk.agents.parallel_agent import ParallelAgent
from google.adk.agents.sequential_agent import SequentialAgent
from agents.constants import GeminiConstants


from agents.specialist_agents import (
    defender_agent,
    midfielder_agent,
    forward_agent,
    goalkeeper_agent,
)





CAPTAIN_SYNTHESIS_INSTRUCTION = """You are the TEAM CAPTAIN on the pitch. Your teammates have executed the tactics and reported back.

Your job is to gather their responses from the session state and output ONLY a valid JSON object matching the huddle schema:
{
  "status": "Short confirmation that tactics were executed",
  "huddle": {
    "defender": "{defender_response}",
    "midfielder": "{midfielder_response}",
    "forward": "{forward_response}",
    "goalkeeper": "{goalkeeper_response}"
  }
}
Do NOT add any markdown formatting, backticks, or extra text."""

# 1. Create a ParallelAgent to broadcast the tactics to all specialist agents in parallel:
parallel_players = ParallelAgent(
    name="ParallelPlayers",
    sub_agents=[defender_agent, midfielder_agent, forward_agent, goalkeeper_agent],
    description="Runs all specialist player agents in parallel."
)

# 2. Modify the Captain LlmAgent to act as a Synthesis/Merger Agent. It no longer needs the player tools!
#  Its only job is to format the final JSON response using the outputs stored in the state keys:
#  defender_response, midfielder_response, forward_response, goalkeeper_response.

synthesis_captain = LlmAgent(
       name="SynthesisCaptain",
       model=GeminiConstants.GEMINI_FLASH_LITE,
       instruction=CAPTAIN_SYNTHESIS_INSTRUCTION,
   )

# 3. Combine them using a SequentialAgent pipeline to define the final captain_agent:
captain_agent = SequentialAgent(
    name="TeamCaptainPipeline",
    sub_agents=[parallel_players, synthesis_captain],
    description="Delegates to players in parallel and synthesizes the final huddle report."
)
