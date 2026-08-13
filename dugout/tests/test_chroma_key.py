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


def haloed(halo=(0, 255, 0), size=(64, 48)):
    """A figure the model drew with the screen still glowing around it.

    Resizing the sheet blends the screen into the figure's edge, and those blend
    pixels are further from the key colour than the tolerance reaches, so they
    come through as a neon rim.
    """
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(22, 42):
        for y in range(10, 38):
            im.putpixel((x, y), halo + (255,))
    for x in range(24, 40):
        for y in range(12, 36):
            im.putpixel((x, y), BLACK_KIT + (255,))
    return im


def greenest(image):
    """How green-dominant the greenest surviving pixel is."""
    return max((g - max(r, b) for r, g, b, a in utils.flat_pixels(image) if a > 16),
               default=0)


def test_the_halo_left_around_a_figure_is_swept_off():
    out = utils.apply_chroma_key(haloed())

    assert out.getpixel((22, 24))[3] == 0
    assert out.getpixel((32, 24))[:3] == BLACK_KIT


def test_a_halo_the_tolerance_alone_would_miss_still_goes():
    # Half-way between the screen and the figure: 124 from pure green, twice
    # what the distance test on its own would take off.
    out = utils.apply_chroma_key(haloed(halo=(13, 140, 8)))

    assert out.getpixel((22, 24))[3] == 0


def test_the_green_the_model_puts_under_the_boots_goes_with_it():
    # Generated outfield sheets carry a bright green ellipse for a shadow, which
    # on a green pitch reads as a blob rather than as a shadow.
    im = haloed()
    for x in range(20, 44):
        im.putpixel((x, 40), (21, 170, 17, 255))
    out = utils.apply_chroma_key(im)

    assert out.getpixel((32, 40))[3] == 0


def test_what_survives_the_sweep_is_not_left_glowing():
    out = utils.apply_chroma_key(haloed(halo=(20, 33, 0)))

    assert greenest(out) <= 0


def test_the_sweep_does_not_reach_a_green_kit():
    # Only what the screen bled into goes. A dark green jersey is a jersey.
    im = Image.new("RGB", (64, 48), LIME)
    for x in range(24, 40):
        for y in range(12, 36):
            im.putpixel((x, y), (20, 120, 40))
    out = utils.process_avatar_image(to_bytes(im), (64, 48))

    assert out.getpixel((32, 24))[3] == 255


def test_the_screen_showing_between_an_arm_and_the_body_still_goes():
    # A pocket of screen the sweep cannot walk into from the outside. The
    # distance pass is what takes it, so both passes have to stay.
    im = Image.new("RGB", (64, 48), LIME)
    for x in range(20, 44):
        for y in range(10, 38):
            im.putpixel((x, y), BLACK_KIT)
    for x in range(28, 36):
        for y in range(18, 30):
            im.putpixel((x, y), LIME)
    out = utils.process_avatar_image(to_bytes(im), (64, 48))

    assert out.getpixel((32, 24))[3] == 0
