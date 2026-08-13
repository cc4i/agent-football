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

import io
from PIL import Image

# Below this a pixel is background rather than a faint edge of something.
ALPHA_FLOOR = 16


def flat_pixels(image: Image.Image) -> list:
    """Every pixel in one list. getdata() is on its way out of Pillow."""
    if hasattr(image, "get_flattened_data"):
        return list(image.get_flattened_data())
    return list(image.getdata())


def detect_key_color(image: Image.Image, fallback=(0, 255, 0)):
    """The background colour actually present, taken from the four corners.

    The prompt asks for #00FF00 and the model returns whatever green it likes -
    (162, 247, 60) in practice, which is 173 away from pure green and so sails
    past any sane fixed tolerance, leaving the sheet fully opaque. The corners
    are background by construction, so ask the image instead of guessing.
    """
    rgb = image.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in
               ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    mean = tuple(sum(c[i] for c in corners) / len(corners) for i in range(3))
    # Generated backgrounds carry a little noise, so agreement is approximate.
    # Corners that genuinely disagree mean this is not a flat screen.
    spread = max(sum((c[i] - mean[i]) ** 2 for i in range(3)) ** 0.5
                 for c in corners)
    if spread > 12:
        return fallback
    r, g, b = (round(v) for v in mean)
    # Only trust the sample if it really is a green screen.
    return (r, g, b) if g > r and g > b else fallback


def apply_chroma_key(image: Image.Image, key_color=(0, 255, 0), tolerance=60,
                     reach=130) -> Image.Image:
    """Take the green screen off a generated sheet, halo and all.

    Three passes, because one is not enough:

    1. Everything within `tolerance` of the key colour goes. This is the only
       pass that can reach a pocket of screen enclosed by the figure -- between
       an arm and the body, say -- so it has to stay.
    2. The screen is then flooded outward from what pass 1 took. Resizing the
       sheet blends the screen into the figure's edge, and those blend pixels
       sit well past `tolerance`: at 130 they are what came through as a neon
       rim around every player. Walking out from known background is what keeps
       the same generous radius off a green jersey, which the figure's own dark
       outline stands between.
    3. What survives against the hole is de-spilled: green that still dominates
       a pixel is pulled back to its other channels, so the last blended ring
       reads as an outline instead of glowing.

    A jersey in the same bright green as the screen cannot be told from it, and
    is not meant to be: the manager gets the kit they asked the model for.
    """
    image = image.convert("RGBA")
    width, height = image.size
    pixels = flat_pixels(image)
    total = width * height
    kr, kg, kb = key_color
    near = tolerance ** 2
    far = reach ** 2

    hole = bytearray(total)
    frontier = []
    for i, (r, g, b, a) in enumerate(pixels):
        if a <= ALPHA_FLOOR or (r - kr) ** 2 + (g - kg) ** 2 + (b - kb) ** 2 < near:
            hole[i] = 1
            frontier.append(i)

    # Pass 2: outward from the hole, across greenish pixels only, and only as
    # far as `reach`.
    while frontier:
        i = frontier.pop()
        for j in touching(i, width, height, total):
            if hole[j]:
                continue
            r, g, b, _ = pixels[j]
            if g > r and g > b and (r - kr) ** 2 + (g - kg) ** 2 + (b - kb) ** 2 < far:
                hole[j] = 1
                frontier.append(j)

    # Pass 3: two rings of de-spill, which is about as far as a resize blends.
    ring = [i for i in range(total)
            if not hole[i] and any(hole[j] for j in touching(i, width, height, total))]
    seen = bytearray(hole)
    for i in ring:
        seen[i] = 1
    for _ in range(2):
        nxt = []
        for i in ring:
            r, g, b, a = pixels[i]
            if g > max(r, b):
                pixels[i] = (r, max(r, b), b, a)
            for j in touching(i, width, height, total):
                if not seen[j]:
                    seen[j] = 1
                    nxt.append(j)
        ring = nxt

    image.putdata([(0, 0, 0, 0) if hole[i] else pixels[i] for i in range(total)])
    return image


def touching(index: int, width: int, height: int, total: int):
    """The four pixels touching this one, without wrapping round a row end."""
    x = index % width
    if x:
        yield index - 1
    if x + 1 < width:
        yield index + 1
    if index >= width:
        yield index - width
    if index + width < total:
        yield index + width


def extract_image_bytes(response):
    """
    Extracts raw image bytes from the Gemini response candidates.
    """
    if response.candidates and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
    return None


def process_avatar_image(image_bytes: bytes, target_size: tuple = None) -> Image.Image:
    """Load a generation and take the screen off it.

    `target_size` is for callers that need a fixed canvas. Leave it alone and
    the generation keeps the size the model drew: whoever cuts it into frames
    is going to resample anyway, and doing it once from the original is what
    keeps a figure sharp.
    """
    image = Image.open(io.BytesIO(image_bytes))
    # Sample before resizing: interpolation smears the border into the figure.
    key_color = detect_key_color(image)
    if target_size and image.size != target_size:
        image = image.resize(target_size, Image.Resampling.LANCZOS)
    # Tight: the screen is uniform, and gold trim sits only ~109 from the lime
    # the model returns, so a loose radius erases the crest along with it.
    return apply_chroma_key(image, key_color=key_color, tolerance=60)
