"""The chroma-key has to survive whatever green the model actually returns."""

from PIL import Image

import utils

# What gemini-3.1-flash-image returns when asked for #00FF00. Nowhere near it.
LIME = (162, 247, 60)
GOLD = (255, 190, 60)
BLACK_KIT = (26, 26, 26)
SKIN = (232, 180, 140)


def sheet(background, figure=BLACK_KIT, size=(64, 48)):
    im = Image.new("RGB", size, background)
    for x in range(24, 40):
        for y in range(12, 36):
            im.putpixel((x, y), figure)
    return im


def to_bytes(im):
    import io
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_an_off_key_green_background_is_still_removed():
    out = utils.process_avatar_image(to_bytes(sheet(LIME)), (64, 48))
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((63, 47))[3] == 0


def test_the_figure_survives_the_key():
    out = utils.process_avatar_image(to_bytes(sheet(LIME)), (64, 48))
    r, g, b, a = out.getpixel((32, 24))
    assert a == 255
    assert (r, g, b) == BLACK_KIT


def test_gold_trim_is_not_keyed_out():
    out = utils.process_avatar_image(to_bytes(sheet(LIME, figure=GOLD)), (64, 48))
    assert out.getpixel((32, 24))[3] == 255


def test_skin_tone_is_not_keyed_out():
    out = utils.process_avatar_image(to_bytes(sheet(LIME, figure=SKIN)), (64, 48))
    assert out.getpixel((32, 24))[3] == 255


def test_a_pure_green_background_still_works():
    out = utils.process_avatar_image(to_bytes(sheet((0, 255, 0))), (64, 48))
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((32, 24))[3] == 255


def test_a_green_shirt_on_a_green_screen_keeps_the_player():
    # The manager is allowed to ask for a green kit. Only the background goes.
    out = utils.process_avatar_image(
        to_bytes(sheet(LIME, figure=(20, 120, 40))), (64, 48))
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((32, 24))[3] == 255


def test_a_slightly_noisy_background_is_still_detected():
    # Real generations vary by a pixel value or two across the sheet.
    im = sheet(LIME)
    w, h = im.size
    for p, c in (((0, 0), (162, 247, 60)), ((w - 1, 0), (161, 248, 59)),
                 ((0, h - 1), (163, 248, 61)), ((w - 1, h - 1), (162, 248, 60))):
        im.putpixel(p, c)
    out = utils.process_avatar_image(to_bytes(im), (w, h))
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((32, 24))[3] == 255


def test_a_photo_like_background_falls_back_to_pure_green():
    im = Image.new("RGB", (64, 48), (162, 247, 60))
    im.putpixel((0, 0), (10, 20, 200))       # corners disagree wildly
    im.putpixel((63, 47), (200, 10, 20))
    assert utils.detect_key_color(im) == (0, 255, 0)
