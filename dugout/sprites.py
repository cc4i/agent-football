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

"""What the model drew, cut into frames the pitch can draw.

A sheet comes back from an image model, so nothing about its geometry is
promised. Two keepers generated a minute apart came back with seven ready poses
and six, at unrelated x positions, one of them with the row captions drawn in.
Cut that on a grid and every frame clips its own figure and keeps a slice of the
next one: on the pitch that is a keeper standing beside himself.

So the poses are found in the pixels instead. A figure is one run of touching
pixels; a frame holds that run and nothing of anyone else's, redrawn into a
plain grid with the feet on a common line. The pitch then reads the grid out of
the atlas written beside it and never needs to know how the model laid the sheet
out -- or how big it drew anybody.

Run it over sheets already on disk with `python -m sprites <png>...`, which is
also how the ones in the repo were last cut.
"""

import json
import sys
from pathlib import Path

from PIL import Image

import utils

# The frames the prompts ask the model for. Names are given out in reading
# order; a model that draws more than it was asked still gets them all through,
# because a spare pose costs nothing and a missing one is what breaks a match.
PLAYER_FRAMES = ("idle", "run_0", "run_1", "kick")
KEEPER_ROWS = ("ready", "dive_left", "dive_right")

# Frames are cut at about three times the size the pitch draws them at, which
# is enough for the biggest screen a match is shown on without asking the GPU
# to shrink a 768-pixel figure into forty. The pitch scales whatever it is given
# to a fixed size in the world, so these only set how sharp it looks.
PLAYER_FRAME_HEIGHT = 120
KEEPER_FRAME_HEIGHT = 144

# A transparent margin, so drawing one frame can never sample the next.
GUTTER = 2

# A blob this much smaller than the biggest figure is not a pose -- it is a
# caption the model drew in, or a ball, or a speck the chroma key left behind.
DUST = 0.15

# And this much smaller than a figure, it is not even a ball: it is a speck,
# and it goes, even when it sits inside a figure where a held ball would.
SPECK = 0.01


class SheetError(RuntimeError):
    """The sheet has nothing on it that could be a squad."""


class _Pose:
    """One figure: the runs of pixels that make it up, and the box round them."""

    def __init__(self, label, box):
        self.labels = {label}
        self.left, self.top, self.right, self.bottom = box

    @property
    def width(self):
        return self.right - self.left + 1

    @property
    def height(self):
        return self.bottom - self.top + 1

    @property
    def middle(self):
        return (self.top + self.bottom) / 2

    def holds(self, other):
        """Is that blob a part of this figure -- a held ball, say?"""
        return (self.left <= other.left and other.right <= self.right
                and self.top <= other.top and other.bottom <= self.bottom)

    def take(self, other):
        self.labels |= other.labels


def normalise(image: Image.Image, layout: str):
    """Cut a generated sheet into a grid. Returns the sheet and its atlas."""
    if layout not in ("player", "keeper"):
        raise ValueError(f"unknown layout {layout!r}")

    image = image.convert("RGBA")
    labels, poses = _find_poses(image)
    if not poses:
        raise SheetError("no figures on the sheet")
    rows = _rows(poses)
    named = _name(rows, layout)

    tallest = max(pose.height for pose in poses)
    target = KEEPER_FRAME_HEIGHT if layout == "keeper" else PLAYER_FRAME_HEIGHT
    # Never blown up: art smaller than the target is as sharp as it will get.
    factor = min(1.0, target / tallest)

    cuts = {name: _cut(image, labels, pose, factor) for name, pose in named}
    cell_w = max(cut.width for cut in cuts.values()) + GUTTER * 2
    cell_h = max(cut.height for cut in cuts.values()) + GUTTER * 2
    columns = max(len(row) for row in rows)

    sheet = Image.new("RGBA", (columns * cell_w, len(rows) * cell_h), (0, 0, 0, 0))
    frames = {}
    for name, pose in named:
        cut = cuts[name]
        row, column = _slot(rows, pose)
        x, y = column * cell_w, row * cell_h
        # Centred across the cell and standing on its floor, so a figure never
        # jumps sideways or hovers as the animation runs through the row.
        sheet.alpha_composite(
            cut, (x + (cell_w - cut.width) // 2, y + cell_h - GUTTER - cut.height))
        frames[name] = {
            "frame": {"x": x, "y": y, "w": cell_w, "h": cell_h},
            "sourceSize": {"w": cell_w, "h": cell_h},
            "spriteSourceSize": {"x": 0, "y": 0, "w": cell_w, "h": cell_h},
        }

    atlas = {
        "frames": frames,
        "meta": {"size": {"w": sheet.width, "h": sheet.height}, "scale": "1"},
    }
    return sheet, atlas


def write(sheet: Image.Image, atlas: dict, path) -> Path:
    """Write the sheet and the atlas that reads it, side by side."""
    path = Path(path)
    beside = path.with_suffix(".json")
    atlas = dict(atlas, meta=dict(atlas["meta"], image=path.name))
    sheet.save(path, "PNG")
    beside.write_text(json.dumps(atlas, indent=2) + "\n")
    return path


def layout_for(filename) -> str:
    """Which layout a sheet has, by the name the generator gives it."""
    return "keeper" if "goalkeeper" in Path(filename).name else "player"


def _find_poses(image: Image.Image):
    """Label every run of touching pixels, then keep the ones that are figures."""
    labels, blobs = _label(image)
    if not blobs:
        return labels, []

    biggest = max(area for area, _ in blobs.values())
    poses = [_Pose(label, box) for label, (area, box) in sorted(blobs.items())
             if area >= biggest * DUST]
    loose = [_Pose(label, box) for label, (area, box) in sorted(blobs.items())
             if biggest * SPECK <= area < biggest * DUST]
    # A ball in the hands is its own run of pixels, but it is the keeper's.
    # A caption is not: it sits outside every figure.
    for thing in loose:
        for pose in poses:
            if pose.holds(thing):
                pose.take(thing)
                break
    return labels, poses


def _label(image: Image.Image):
    """Flood every opaque pixel into a numbered run of its neighbours."""
    width, height = image.size
    total = width * height
    alpha = utils.flat_pixels(image)
    labels = [0] * total
    blobs = {}
    label = 0
    for start in range(total):
        if labels[start] or alpha[start][3] <= utils.ALPHA_FLOOR:
            continue
        label += 1
        labels[start] = label
        stack = [start]
        area = 0
        left = right = start % width
        top = bottom = start // width
        while stack:
            i = stack.pop()
            area += 1
            x, y = i % width, i // width
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for j in utils.touching(i, width, height, total):
                if not labels[j] and alpha[j][3] > utils.ALPHA_FLOOR:
                    labels[j] = label
                    stack.append(j)
        blobs[label] = (area, (left, top, right, bottom))
    return labels, blobs


def _rows(poses):
    """Group the figures into the rows the model drew them in, left to right."""
    standing = sorted(poses, key=lambda pose: pose.middle)
    # Two figures are in the same row when their middles are closer together
    # than a figure is tall. Rows of divers overlap the row above by more than
    # a gap between them ever does, so a gap is what splits them.
    apart = sorted(pose.height for pose in poses)[len(poses) // 2] * 0.6
    rows = [[standing[0]]]
    for pose in standing[1:]:
        if pose.middle - rows[-1][-1].middle > apart:
            rows.append([])
        rows[-1].append(pose)
    return [sorted(row, key=lambda pose: pose.left) for row in rows]


def _name(rows, layout):
    """Pair every pose with the name the pitch will ask for it by."""
    named = []
    if layout == "player":
        # Four poses in one row is what the prompt asks for; if the model breaks
        # them over two, they are still the same four in the same order.
        for index, pose in enumerate(pose for row in rows for pose in row):
            named.append((PLAYER_FRAMES[index] if index < len(PLAYER_FRAMES)
                          else f"spare_{index}", pose))
        return named

    for number, row in enumerate(rows):
        prefix = KEEPER_ROWS[number] if number < len(KEEPER_ROWS) else f"spare{number}"
        for index, pose in enumerate(row):
            named.append((f"{prefix}_{index}", pose))
    return named


def _slot(rows, pose):
    for row_number, row in enumerate(rows):
        if pose in row:
            return row_number, row.index(pose)
    raise SheetError("a pose went missing between finding it and cutting it")


def _cut(image: Image.Image, labels, pose, factor):
    """This figure alone, on transparent, at the size the sheet is cut to."""
    width = image.width
    cut = Image.new("RGBA", (pose.width, pose.height), (0, 0, 0, 0))
    source = image.load()
    target = cut.load()
    for y in range(pose.top, pose.bottom + 1):
        row = y * width
        for x in range(pose.left, pose.right + 1):
            if labels[row + x] in pose.labels:
                target[x - pose.left, y - pose.top] = source[x, y]
    if factor == 1.0:
        return cut
    return cut.resize((max(1, round(cut.width * factor)),
                       max(1, round(cut.height * factor))),
                      Image.Resampling.LANCZOS)


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    for name in argv:
        path = Path(name)
        sheet, atlas = normalise(Image.open(path), layout_for(path))
        write(sheet, atlas, path)
        print(f"{path.name}: {len(atlas['frames'])} frames, "
              f"{sheet.width}x{sheet.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
