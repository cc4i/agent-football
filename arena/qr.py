"""The codes a phone gets pointed at, and the mark in the middle of them.

There are two. The venue's own says `/scan` and nothing about any particular
room, because it is printed on a sheet and pinned to a wall before a single
room exists and it has to still be right at the end of the day. A room's, on
the big screen beside its lobby, says that room. Both are drawn here so that
they look like the same venue's codes, and because the mark decides how the
rest of the code has to be drawn.

A mark over the middle of a QR is destroyed data. It reads anyway because the
symbol carries enough Reed-Solomon parity to reconstruct what is missing, and
how much it carries is chosen when the code is drawn. These are drawn at level
h, which keeps 30% of the symbol recoverable, rather than the m the rooms used
before there was anything to recover from. The plate below covers about a
twenty-fifth of the area, and the rest of that budget is not spare: a printed
code is read folded, in bad light, at an angle, off a phone held by somebody
walking, and every one of those spends the same parity.
"""

import io

import segno

# Modules of nothing around the code, which is what a scanner needs to find its
# edges. Fewer than the four the spec asks for, because the pages that show
# these put white padding around them and the sheet the printed one is on is
# white to its margins - the quiet zone is there, it is just not drawn here.
QUIET = 2

# How much of the drawing's width the mark spans, quiet zone included. A fifth
# of the width is a twenty-fifth of the area.
PLATE = 0.2

# How much of that plate the G fills. The rest is a white ring, without which
# the mark reads as a stain on the code rather than as something put there.
INSET = 0.66

# The G, as Google draws it, on the 48-unit canvas it is drawn on. It goes into
# a nested <svg> below rather than through a transform, so that the viewBox
# does the arithmetic and none of these numbers is ever touched.
G = (
    '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0'
    ' 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
    '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96'
    '-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
    '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l'
    '-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
    '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92'
    ' 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>'
)


def svg(url):
    """`url` as a scannable code with the mark on it, as the bytes of an SVG.

    SVG rather than PNG so that the big screen can scale it to the wall and a
    printer can put it on a sheet without either going soft, and so that no
    image library has to be in the dependency list.
    """
    code = segno.make(url, error="h")
    drawing = io.BytesIO()
    # Sized in mm with no class attributes, so the page's own CSS decides how
    # big it is rather than the encoder. The viewBox segno writes alongside is
    # one unit per module, which is what `_mark` measures itself against.
    code.save(drawing, kind="svg", scale=1, border=QUIET, unit="mm",
              svgclass=None, lineclass=None)
    span = code.symbol_size(scale=1, border=QUIET)[0]
    # The document is closed once, at the end, and the mark is painted over the
    # modules it covers rather than under them, so it goes immediately before
    # that and nowhere else.
    return drawing.getvalue().decode().replace("</svg>", _mark(span) + "</svg>").encode()


def _mark(span):
    """The plate and the G, in the code's own units."""
    plate = span * PLATE
    edge = (span - plate) / 2
    letter = plate * INSET
    inset = (span - letter) / 2
    return (
        f'<rect x="{_short(edge)}" y="{_short(edge)}"'
        f' width="{_short(plate)}" height="{_short(plate)}"'
        # Opaque, or the modules underneath show through the G and a decoder is
        # asked to read the mark as data. Rounded, because a square white hole
        # in a square black grid looks like a printing fault.
        f' rx="{_short(plate * 0.22)}" fill="#fff"/>'
        f'<svg x="{_short(inset)}" y="{_short(inset)}"'
        f' width="{_short(letter)}" height="{_short(letter)}" viewBox="0 0 48 48">{G}</svg>'
    )


def _short(value):
    """A number short enough to read in the source of the page it lands on."""
    return f"{value:.3f}".rstrip("0").rstrip(".")
