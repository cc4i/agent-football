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

"""Team rebranding. Owns image generation so the chroma-key pipeline always runs."""

from pathlib import Path

from google import genai

import prompts
import utils

SPRITE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "game" / "frontend" / "public" / "assets" / "sprites"
)
SPRITE_SIZE = (1408, 768)
TEAMS = ("blue", "red")

_CLIENT = None


class AvatarGenerationError(RuntimeError):
    """The model returned a response with no usable image."""


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client()
    return _CLIENT


def _generate_one(client, prompt: str, filename: str, make_default_gk: bool):
    response = client.models.generate_content(
        model="gemini-2.5-flash-image", contents=prompt)
    image_bytes = utils.extract_image_bytes(response)
    if not image_bytes:
        return None
    image = utils.process_avatar_image(image_bytes, SPRITE_SIZE)
    utils.save_and_encode_image(image, filename, str(SPRITE_DIR),
                                make_default_gk=make_default_gk)
    return str(SPRITE_DIR / filename)


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
    SPRITE_DIR.mkdir(parents=True, exist_ok=True)

    outfield = _generate_one(
        client, prompts.get_player_prompt(color, logo, style),
        f"player_{team}_team.png", False)
    if outfield is None:
        raise AvatarGenerationError(
            f"the model returned no image for the {team} outfield players")

    # Only our own keeper becomes the fallback goalkeeper.png; the opponent's
    # must not overwrite it.
    keeper = _generate_one(
        client, prompts.get_goalkeeper_prompt(color, logo, style),
        f"goalkeeper_{team}_team.png", team == "blue")
    if keeper is None:
        raise AvatarGenerationError(
            f"the model returned no image for the {team} goalkeeper")

    return {"team": team, "sprite_sheet": outfield, "goalkeeper": keeper}
