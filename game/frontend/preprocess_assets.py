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
from PIL import Image, ImageDraw

src_dir = "/Users/dsalomone/Documents/Projects/adhoc/soccer-game-assets"
dest_dir = "/Users/dsalomone/Documents/Projects/adhoc/soccer-game/public/assets"

os.makedirs(os.path.join(dest_dir, "backgrounds"), exist_ok=True)
os.makedirs(os.path.join(dest_dir, "sprites"), exist_ok=True)
os.makedirs(os.path.join(dest_dir, "ui"), exist_ok=True)

def make_transparent_floodfill(src_path, dest_path):
    print(f"Processing transparency for: {src_path}")
    img = Image.open(src_path).convert("RGBA")
    width, height = img.size
    
    # Create a mask using flood fill starting from (0, 0)
    # We'll find all connected pixels that are close to white
    # We use ImageDraw.floodfill
    
    # Let's do a flood fill from (0,0) and other corners just in case they are disconnected
    mask = Image.new("L", (width, height), 255)
    
    # We will define a function to check if a color is near-white
    def is_near_white(color):
        # color is (r, g, b, a)
        return color[0] >= 240 and color[1] >= 240 and color[2] >= 240
    
    # Simple flood fill implementation using a queue to make it robust and customizable
    visited = set()
    queue = []
    
    # Add the four corners as starting points if they are near-white
    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    for cx, cy in corners:
        color = img.getpixel((cx, cy))
        if is_near_white(color):
            queue.append((cx, cy))
            visited.add((cx, cy))
            
    # Flood fill BFS
    while queue:
        cx, cy = queue.pop(0)
        # Set mask pixel to 0 (transparent)
        mask.putpixel((cx, cy), 0)
        
        # Check 4 neighbors
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < width and 0 <= ny < height:
                if (nx, ny) not in visited:
                    n_color = img.getpixel((nx, ny))
                    if is_near_white(n_color):
                        visited.add((nx, ny))
                        queue.append((nx, ny))
                        
    # Apply the mask to the image alpha channel
    data = img.get_flattened_data() if hasattr(img, "get_flattened_data") else list(img.getdata())
    new_data = []
    for idx, pixel in enumerate(data):
        x = idx % width
        y = idx // width
        mask_val = mask.getpixel((x, y))
        if mask_val == 0:
            new_data.append((pixel[0], pixel[1], pixel[2], 0))
        else:
            new_data.append(pixel)
            
    img.putdata(new_data)
    img.save(dest_path, "PNG")
    print(f"  Saved transparent image to: {dest_path}")

# Backgrounds are copied directly
for bg in ["crowd_stands.png", "pitch.png"]:
    src = os.path.join(src_dir, "backgrounds", bg)
    dest = os.path.join(dest_dir, "backgrounds", bg)
    if os.path.exists(src):
        img = Image.open(src)
        img.save(dest)
        print(f"Copied background: {bg}")

# Sprites are processed with floodfill transparency
for sprite in ["ball.png", "goalkeeper.png", "goalposts.png", "player_blue_team.png", "player_red_team.png"]:
    src = os.path.join(src_dir, "sprites", sprite)
    dest = os.path.join(dest_dir, "sprites", sprite)
    if os.path.exists(src):
        make_transparent_floodfill(src, dest)

# UI elements are processed with floodfill transparency
for ui in ["ad_board.png", "coach_portrait.png", "scoreboard.png", "shout_input.png"]:
    src = os.path.join(src_dir, "ui", ui)
    dest = os.path.join(dest_dir, "ui", ui)
    if os.path.exists(src):
        make_transparent_floodfill(src, dest)

print("Pre-processing complete!")
