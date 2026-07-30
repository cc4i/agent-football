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

import os
import io
import base64
from PIL import Image

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


def apply_chroma_key(image: Image.Image, key_color=(0, 255, 0), tolerance=60) -> Image.Image:
    """
    Removes the solid background color (chroma-keying) and makes it transparent.
    Used to remove the green screen background from generated spritesheets.
    """
    image = image.convert("RGBA")
    
    if hasattr(image, "get_flattened_data"):
        data = image.get_flattened_data()
    else:
        data = list(image.getdata())
        
    new_data = []
    kr, kg, kb = key_color
    
    for item in data:
        r, g, b, a = item
        # Calculate Euclidean distance to key color
        dist = ((r - kr) ** 2 + (g - kg) ** 2 + (b - kb) ** 2) ** 0.5
        if dist < tolerance:
            new_data.append((0, 0, 0, 0)) # Make transparent
        else:
            new_data.append(item)
            
    image.putdata(new_data)
    return image


def extract_image_bytes(response):
    """
    Extracts raw image bytes from the Gemini response candidates.
    """
    if response.candidates and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
    return None


def process_avatar_image(image_bytes: bytes, target_size: tuple) -> Image.Image:
    """
    Loads, resizes, and applies chroma key transparency to the image.
    """
    image = Image.open(io.BytesIO(image_bytes))
    # Sample before resizing: interpolation smears the border into the figure.
    key_color = detect_key_color(image)
    if image.size != target_size:
        image = image.resize(target_size, Image.Resampling.LANCZOS)
    # Tight: the screen is uniform, and gold trim sits only ~109 from the lime
    # the model returns, so a loose radius erases the crest along with it.
    return apply_chroma_key(image, key_color=key_color, tolerance=60)


def save_and_encode_image(image: Image.Image, filename: str, output_dir: str, make_default_gk: bool = False) -> str:
    """
    Saves the image to the target output directory and returns its base64 URI.
    Optional: Copies the goalkeeper to a default goalkeeper.png if make_default_gk is True.
    """
    os.makedirs(output_dir, exist_ok=True)
    dest_path = os.path.join(output_dir, filename)
    image.save(dest_path, "PNG")
    
    if make_default_gk:
        default_gk_path = os.path.join(output_dir, "goalkeeper.png")
        image.save(default_gk_path, "PNG")
            
    # Prepare Base64 preview
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_base64}"
