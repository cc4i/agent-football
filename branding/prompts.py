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

def get_player_prompt(color: str, logo: str, style: str) -> str:
    """
    Generates the prompt for the outfield player spritesheet.
    Developers can modify this to change the visual style, frames, or requirements.
    """
    return (
        f"Create a horizontal spritesheet of a cartoonish 2D soccer player. "
        f"Style: {style}. "
        f"Player Jersey Color: {color}. "
        f"Jersey Logo: {logo}. "
        f"CRITICAL STYLE REQUIREMENTS: "
        f"1. The spritesheet must consist of 4 frames in a single horizontal row, with each frame cleanly separated without any border or text. "
        f"2. Frame 1: player standing idle. Frame 2 and 3: player running from 2 different angles. Frame 4: player kicking a soccer ball. Do not show the ball in last frame. "
        f"3. The style should be clean 2D vector art on a solid, uniform neon green screen background (color hex #00FF00) for chroma-keying. "
        f"The characters should be evenly spaced."
    )


def get_goalkeeper_prompt(color: str, logo: str, style: str) -> str:
    """
    Generates the prompt for the goalkeeper spritesheet, referencing the style
    of the player created in the same chat session.
    """
    return (
        f"Now, create a horizontal spritesheet of a soccer goalkeeper for the SAME team. "
        f"Keep the style, jersey color ({color}), jersey logo ({logo}), and overall appearance identical to the player you just created. "
        f"CRITICAL STYLE REQUIREMENTS: "
        f"1. The spritesheet must consist of 3 horizontal rows. Row 1 for standing ready poses, row 2 and 3 for diving left and right respectively. "
        f"2. For Row 1 Standing ready create 6 frames, each slightly different from other. For Row 2 and 3 Diving left and right respectively create 5 frames, each slightly different from other. Total frames = 16. "
        f"3. The background must be a solid, uniform neon green screen background (color hex #00FF00) with no shadows, gradients, or other colors. "
        f"The figures should be evenly spaced."
    )
