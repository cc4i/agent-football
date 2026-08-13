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

"""Team rebranding. Owns image generation so the sheet the pitch reads is sound.

A generation is not a sprite sheet yet: it is figures the model placed where it
liked, on a green screen, at whatever size it drew them. Both of those are dealt
with here, before anything reaches disk -- the screen keyed off, the poses cut
out and packed into a grid with an atlas beside them. What the pitch loads is
then a grid, whatever the model did.
"""

from pathlib import Path

from google import genai

import prompts
import sprites
import utils

SPRITE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "game" / "frontend" / "public" / "assets" / "sprites"
)
TEAMS = ("blue", "red")

_CLIENT = None


class AvatarGenerationError(RuntimeError):
    """The model returned a response with no usable image."""


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client()
    return _CLIENT


def _generate_one(client, prompt: str, filename: str):
    """One sheet: asked for, keyed, cut into frames, written with its atlas."""
    response = client.models.generate_content(
        model="gemini-3.1-flash-image", contents=prompt)
    image_bytes = utils.extract_image_bytes(response)
    if not image_bytes:
        return None
    # Keyed and cut at the size the model drew, so the only resampling a figure
    # goes through is the single step down to the frame.
    image = utils.process_avatar_image(image_bytes)
    try:
        sheet, atlas = sprites.normalise(image, sprites.layout_for(filename))
    except sprites.SheetError as unusable:
        raise AvatarGenerationError(
            f"the model drew nothing usable for {filename}: {unusable}") from unusable
    SPRITE_DIR.mkdir(parents=True, exist_ok=True)
    return str(sprites.write(sheet, atlas, SPRITE_DIR / filename))


def generate_team_avatars(team: str, color: str, logo: str, style: str) -> dict:
    """Regenerate one team's outfield sprite sheet and goalkeeper.

    Args:
      team: "blue" for our side or "red" for the opponent.
      color: jersey colour, for example "black".
      logo: crest description, for example "gold wolf head".
      style: visual detail, for example "short blond hair".
    """
    if team not in TEAMS:
        raise ValueError(f"unknown team {team!r}, expected one of {TEAMS}")

    client = _client()

    outfield = _generate_one(
        client, prompts.get_player_prompt(color, logo, style),
        f"player_{team}_team.png")
    if outfield is None:
        raise AvatarGenerationError(
            f"the model returned no image for the {team} outfield players")

    keeper = _generate_one(
        client, prompts.get_goalkeeper_prompt(color, logo, style),
        f"goalkeeper_{team}_team.png")
    if keeper is None:
        raise AvatarGenerationError(
            f"the model returned no image for the {team} goalkeeper")

    return {"team": team, "sprite_sheet": outfield, "goalkeeper": keeper}
