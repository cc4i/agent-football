"""A generated sheet is not a grid, so the frames have to be found in it.

Every fixture here is a sheet a model plausibly returns: figures at unrelated
x positions, rows with different pose counts, a caption drawn in, a neighbour's
arm reaching across the frame line. What must come out is one figure per frame
and nothing of anyone else.
"""

from PIL import Image, ImageDraw

import sprites
import utils

KIT = (26, 26, 26, 255)
OTHER = (200, 40, 40, 255)


def blank(size=(1408, 768)):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def figure(image, x, y, w, h, colour=KIT):
    """One pose, as a solid block of its own colour."""
    ImageDraw.Draw(image).rectangle([x, y, x + w - 1, y + h - 1], fill=colour)


def anyone_red(image):
    """Is another figure in this frame? Only one fixture figure is ever red.

    Cutting resamples, so the question has to be about the hue rather than the
    exact colour: a red block blended with transparency is still red.
    """
    return any(r - max(g, b) > 30
               for r, g, b, a in utils.flat_pixels(image) if a > 16)


def frame_image(sheet, atlas, name):
    box = atlas["frames"][name]["frame"]
    return sheet.crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))


def keeper_sheet(ready=6, dives=5):
    """Three rows, unevenly spaced, the way the model actually spaces them."""
    image = blank()
    for i in range(ready):
        figure(image, 57 + i * 197 + (i % 3) * 9, 20 + (i % 2) * 8, 110, 230)
    for i in range(dives):
        figure(image, 47 + i * 265 + (i % 2) * 11, 300, 240, 190)
    for i in range(dives):
        figure(image, 35 + i * 271, 540, 250, 200)
    return image


def test_every_pose_the_model_drew_becomes_a_frame():
    sheet, atlas = sprites.normalise(keeper_sheet(), "keeper")

    assert [n for n in atlas["frames"] if n.startswith("ready_")] == [
        f"ready_{i}" for i in range(6)]
    assert [n for n in atlas["frames"] if n.startswith("dive_left_")] == [
        f"dive_left_{i}" for i in range(5)]
    assert [n for n in atlas["frames"] if n.startswith("dive_right_")] == [
        f"dive_right_{i}" for i in range(5)]


def test_a_row_of_seven_is_cut_into_seven_not_six():
    # The blue keeper came back with seven ready poses and the red one with six.
    # A fixed table of six is what put a sliver of the next keeper in the frame.
    _, atlas = sprites.normalise(keeper_sheet(ready=7), "keeper")

    assert len([n for n in atlas["frames"] if n.startswith("ready_")]) == 7


def test_a_frame_holds_one_figure_and_no_part_of_the_next():
    image = blank()
    figure(image, 57, 20, 110, 230)
    figure(image, 200, 20, 110, 230, colour=OTHER)
    sheet, atlas = sprites.normalise(image, "player")

    assert not anyone_red(frame_image(sheet, atlas, "idle"))
    assert anyone_red(frame_image(sheet, atlas, "run_0"))


def test_a_neighbours_reach_is_cut_out_of_the_frame():
    # The runner's leading fist hangs over where the next player's boot is, so
    # the two boxes overlap without the figures touching. Cropping to the box
    # alone is what drags a floating boot into the frame.
    image = blank()
    figure(image, 57, 20, 110, 230)          # body
    figure(image, 167, 30, 120, 20)          # arm, reaching over the neighbour
    figure(image, 250, 200, 120, 50, colour=OTHER)   # the neighbour's boot
    sheet, atlas = sprites.normalise(image, "player")

    assert not anyone_red(frame_image(sheet, atlas, "idle"))


def test_a_caption_the_model_drew_is_not_a_pose():
    image = keeper_sheet()
    ImageDraw.Draw(image).text((541, 14), "Standing Ready Poses", fill=(0, 200, 0, 255))
    _, atlas = sprites.normalise(image, "keeper")

    assert len([n for n in atlas["frames"] if n.startswith("ready_")]) == 6


def reaching(image, x, y):
    """A keeper with both arms out, so there is a gap between them to hold in."""
    figure(image, x, y, 110, 230)               # body
    figure(image, x + 110, y + 10, 70, 22)      # upper arm
    figure(image, x + 110, y + 100, 70, 22)     # lower arm


def test_a_ball_held_in_the_hands_stays_with_the_keeper():
    image = blank()
    reaching(image, 57, 20)
    # Between the gloves, and its own run of pixels: the outline the model draws
    # round a ball is dark enough that the key cuts it free of the hands.
    ImageDraw.Draw(image).ellipse([175, 60, 215, 100], fill=OTHER)
    reaching(image, 400, 20)
    sheet, atlas = sprites.normalise(image, "keeper")

    assert len([n for n in atlas["frames"] if n.startswith("ready_")]) == 2
    assert anyone_red(frame_image(sheet, atlas, "ready_0"))


def test_dust_beside_a_figure_is_not_mistaken_for_something_it_is_holding():
    # The key leaves a few pixels of the screen behind. Inside a figure's box
    # they would ride along into the frame and show up as a fleck on the grass.
    image = blank()
    reaching(image, 57, 20)
    figure(image, 175, 60, 3, 4, colour=OTHER)  # dust, where the ball was
    reaching(image, 400, 20)
    sheet, atlas = sprites.normalise(image, "keeper")

    assert not anyone_red(frame_image(sheet, atlas, "ready_0"))


def test_the_feet_land_on_the_same_line_in_every_frame():
    image = blank()
    figure(image, 57, 20, 110, 230)     # standing tall
    figure(image, 300, 90, 110, 160)    # crouched, same ground line
    sheet, atlas = sprites.normalise(image, "player")

    floors = []
    for name in ("idle", "run_0"):
        cell = frame_image(sheet, atlas, name)
        floors.append(max(y for _, y in opaque_pixels(cell)))
    assert floors[0] == floors[1]


def opaque_pixels(image):
    px = image.load()
    w, h = image.size
    return [(x, y) for y in range(h) for x in range(w) if px[x, y][3] > 16]


def test_every_frame_is_the_same_size_so_the_sprite_does_not_jump():
    _, atlas = sprites.normalise(keeper_sheet(), "keeper")

    sizes = {(f["frame"]["w"], f["frame"]["h"]) for f in atlas["frames"].values()}
    assert len(sizes) == 1


def test_the_tallest_pose_sets_the_frame_height():
    _, atlas = sprites.normalise(keeper_sheet(), "keeper")

    height = next(iter(atlas["frames"].values()))["frame"]["h"]
    assert height == sprites.KEEPER_FRAME_HEIGHT + sprites.GUTTER * 2


def test_art_smaller_than_the_frame_is_left_alone_rather_than_blown_up():
    image = blank(size=(400, 200))
    figure(image, 10, 10, 40, 60)
    figure(image, 100, 10, 40, 60)
    _, atlas = sprites.normalise(image, "player")

    assert (next(iter(atlas["frames"].values()))["frame"]["h"]
            == 60 + sprites.GUTTER * 2)


def test_the_outfield_row_is_named_for_what_the_prompt_asked_for():
    image = blank()
    for i in range(4):
        figure(image, 57 + i * 300, 20, 200, 500)
    _, atlas = sprites.normalise(image, "player")

    assert list(atlas["frames"]) == ["idle", "run_0", "run_1", "kick"]


def test_a_fifth_outfield_pose_is_kept_rather_than_dropped():
    image = blank()
    for i in range(5):
        figure(image, 57 + i * 260, 20, 180, 500)
    _, atlas = sprites.normalise(image, "player")

    assert list(atlas["frames"])[:4] == ["idle", "run_0", "run_1", "kick"]
    assert len(atlas["frames"]) == 5


def test_the_atlas_points_at_its_own_image():
    sheet, atlas = sprites.normalise(keeper_sheet(), "keeper")

    assert atlas["meta"]["size"] == {"w": sheet.width, "h": sheet.height}


def test_frames_sit_inside_the_sheet():
    sheet, atlas = sprites.normalise(keeper_sheet(), "keeper")

    for box in (f["frame"] for f in atlas["frames"].values()):
        assert box["x"] + box["w"] <= sheet.width
        assert box["y"] + box["h"] <= sheet.height


def test_a_sheet_with_nothing_on_it_is_refused():
    try:
        sprites.normalise(blank(), "player")
    except sprites.SheetError as refusal:
        assert "no figures" in str(refusal)
    else:
        raise AssertionError("an empty sheet should not pass for a squad")


def test_writing_leaves_the_png_and_the_atlas_side_by_side(tmp_path):
    sheet, atlas = sprites.normalise(keeper_sheet(), "keeper")
    target = tmp_path / "goalkeeper_blue_team.png"

    sprites.write(sheet, atlas, target)

    assert target.exists()
    assert (tmp_path / "goalkeeper_blue_team.json").exists()
    written = Image.open(target)
    assert written.size == sheet.size


def test_the_atlas_names_the_png_beside_it(tmp_path):
    sheet, atlas = sprites.normalise(keeper_sheet(), "keeper")
    target = tmp_path / "goalkeeper_blue_team.png"

    sprites.write(sheet, atlas, target)

    import json
    written = json.loads((tmp_path / "goalkeeper_blue_team.json").read_text())
    assert written["meta"]["image"] == "goalkeeper_blue_team.png"


def test_the_layout_follows_the_filename():
    assert sprites.layout_for("goalkeeper_red_team.png") == "keeper"
    assert sprites.layout_for("player_blue_team.png") == "player"
