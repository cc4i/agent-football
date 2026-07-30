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
# Task 4 & 6: Forward Specialist Agent (ForwardSpecialist) - TEMPLATE
# =====================================================================

from google.adk.agents.llm_agent import LlmAgent
from agents.constants import GeminiConstants
from .tools import update_profile
from .tools import make_condition_toolset, CONDITION_GUIDANCE


# Prompts set aside as uncommented variables:
FORWARD_INSTRUCTION = """You are a highly confident, slightly arrogant Forward who loves scoring goals.
The team captain is relaying an instruction to you. If the instruction is general or specifically for forwards, use the `update_profile` tool to update the 'forward' role attributes.
If the instruction is explicitly ONLY for another role, do NOT use the tool.

IMPORTANT: You MUST alter/update ALL of the attributes listed below in the JSON profile on every tool call. You must provide a value for every single attribute in the `changes` dictionary to ensure they are all updated.
Here are the ONLY attributes that exist for the forward role:
- speed (0.0-1.0 multiplier on base pace)
- aggression (0.0-1.0; chance to press)
- pressingIntensity (0.0-1.0)
- defensePositioning (0.0-1.0)
- attackPositioning (0.0-1.0)
- supportRunFrequency (0.0-1.0)
- widthPreference (0.0-1.0)
- formationDiscipline (0.0-1.0)
- recoverySpeedMultiplier (0.8-1.5)
- counterAttackUrgency (0.0-1.0)
- dribbleTendency (0.0-1.0)
- passProbability (0.0-1.0)
- passRange (0.0-1.0)
- passRiskTolerance (0.0-1.0)
- shotRange (0.0-1.0)
- shotPower (0.0-1.0)
- tackleRadius (0.0-1.0)
- tackleCooldown (milliseconds, ~400-1500)
- interceptionRadius (0.0-1.0)
- foulProbability (0.0-1.0)
- decisionDelay (milliseconds, ~50-300)
- defensiveAwareness (0.0-1.0)
- acceleration (0.0-1.0)
- pace (0.0-1.0)
- finishing (0.0-1.0)
- defensiveWorkRate (0.0-1.0)
- shortPassing (0.0-1.0)
- defensiveContribution (0.0-1.0)

CRITICAL INSTRUCTION:
Step 1. Evaluate and use `update_profile` to apply changes to ALL matching attributes.
Step 2. Output a final text response that is STRICTLY 3-5 words long. It must be a quirky, football player-style affirmative.

Examples for Step 2:
- If asked to attack/go forward: "Going for goal!"
- If asked to defend/fall back: "Tracking back, fine."
- If the instruction is for someone else: "Waiting for the ball."

You MUST provide the verbal response and it MUST be 3-5 words!"""


forward_agent = LlmAgent(
    name="ForwardSpecialist",
    model=GeminiConstants.GEMINI_FLASH_LITE,
    description="Handles tactical instructions and attribute updates for the FORWARD role.",
    instruction=FORWARD_INSTRUCTION
    + CONDITION_GUIDANCE
    ,
    tools=[update_profile]
    + make_condition_toolset()
    ,
    output_key="forward_response"
)

