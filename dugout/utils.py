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

def apply_chroma_key(image: Image.Image, key_color=(0, 255, 0), tolerance=120) -> Image.Image:
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


def get_index_html(base_dir: str) -> str:
    """
    Reads and returns the content of the index.html UI file.
    """
    index_path = os.path.join(base_dir, "static/index.html")
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            return f.read()
    return "<h1>LAB01 UI not found. Please create static/index.html</h1>"


def process_avatar_image(image_bytes: bytes, target_size: tuple) -> Image.Image:
    """
    Loads, resizes, and applies chroma key transparency to the image.
    """
    image = Image.open(io.BytesIO(image_bytes))
    if image.size != target_size:
        image = image.resize(target_size, Image.Resampling.LANCZOS)
    return apply_chroma_key(image, key_color=(0, 255, 0), tolerance=120)


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
