"""Build ``icon.ico`` -- the app icon, at every size Windows asks for.

This is a build-time tool, not part of the runtime. The ``.ico`` is
checked in so packaging doesn't need Pillow.

Run from the project root::

    python assets/build_icon.py

Source art
----------
The icon is built from a frame of the same animation the desktop
companion plays, ``assets/alfred/frames/idle/idle_00.png``, so the
file icon and the running app can never show a different Alfred.

Design, per size
----------------
* **A plate at every size** -- a rounded square in near-black with
  a gold rim, the same gold (``#c9a14a``) as the app's accent. It
  gives the icon a silhouette on a dark taskbar and a dark mass on
  a light Explorer background, so it reads either way round.
* **A bust above 32px, not the whole figure.** The butler is drawn
  standing at full length: 75x144 inside a 160px frame. Fitted
  whole onto a square plate he occupies barely half its width, and
  his head -- the only part anyone recognizes -- lands about six
  pixels tall at 48px, a dark vertical smudge. Cropping to head,
  shoulders and the white napkin over his hands fills the plate
  with the face while keeping the black tie and white linen that
  say *butler* rather than *man*.
* **A monogram at 32px and below.** A shaded face cannot survive
  16 pixels; it becomes a peach dot. Simplifying beats shrinking,
  so the small sizes carry a gold **A** instead. The shared plate
  and the shared gold are what keep the two treatments reading as
  one icon.
* **Supersampled 8x, reduced with BOX.** The reduction filter is
  not a detail here. LANCZOS, HAMMING and BICUBIC all overshoot on
  the gold rim's high-contrast corners and leave bright yellow and
  red pixels round every corner at 16, 20, 24 and 32px -- visible
  fringing, not subtle. ``Image.BOX`` is a plain area average, so
  it cannot ring, and the corners come out clean. The art itself
  is enlarged *into* the plate with LANCZOS, which is only ever an
  upscale and so has no such problem.
* **Written with ``append_images``.** That is the only way to get
  per-size art into an ICO through Pillow: a supplied image whose
  ``.size`` matches a requested size exactly is written verbatim
  instead of being resized from the base. The base must be the
  *largest* frame -- Pillow silently drops any requested size above
  the base image's own, which is how an earlier version of this
  script shipped an ICO with no 256 entry at all.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTPUT_ICON = PROJECT_ROOT / "icon.ico"

# The companion's first idle frame, which is also the pose the app
# opens on. Any frame would do -- the crop below is head-and-
# shoulders and the idle cycle barely moves them -- but pinning one
# file keeps the icon reproducible.
SOURCE_ART = SCRIPT_DIR / "alfred" / "frames" / "idle" / "idle_00.png"

# Every size Windows actually reaches for: 16 (list views, title
# bars), 20/24 (taskbar at 125%/150% scaling), 32 (Alt-Tab, medium
# icons), 48 (large icons), 64/128/256 (jumbo, Explorer's
# extra-large view, and the Start menu on high-DPI displays).
ICON_SIZES: tuple[int, ...] = (16, 20, 24, 32, 48, 64, 128, 256)

# At or below this, draw the monogram instead of the butler.
SIMPLIFY_AT_OR_BELOW = 32

# Composition happens at this multiple of the target size and is
# then reduced. 8x is generous, but the whole script runs in about
# a second and the 16px frame is the one users see most.
SUPERSAMPLE = 8

# --- palette (kept in step with ui/theme.py) ---------------------------
# The plate used to be (38, 42, 49) -- a cool slate keyed to the old
# window tier. It kept the same brightness as the theme's warm repaint
# but not the same hue, which is very visible when the taskbar button
# sits next to the window it opens. (37, 32, 25) is _BG_INPUT: the same
# *value* as the old plate, so nothing about how the icon holds up
# against an arbitrary wallpaper changes -- only the hue moves.
PLATE = (37, 32, 25, 255)        # _BG_INPUT -- above _BG_WINDOW so a
                                 # pure black taskbar doesn't swallow it
RIM = (201, 162, 75, 255)        # _ACCENT
MONOGRAM = (201, 162, 75, 255)   # _ACCENT

# Geometry, as fractions of the icon's edge.
CORNER_RADIUS_PCT = 0.22
RIM_WIDTH_PCT = 0.035

# The bust, in source-image pixel coordinates. Measured off the
# frame's alpha channel rather than guessed: the head spans x
# 62..95 / y 19..53, the neck narrows to 19px at y 56, and the
# shoulders widen to x 54..105 by y 89. The box below is centred on
# the head's own centre line (x ~ 79, not the figure's bbox centre,
# which the napkin pulls to the right) and squared off at 76x80 so
# fitting it to the plate scales both axes alike.
BUST_CROP = (41, 15, 117, 95)

# The bust is fitted into a centred box this fraction of the
# plate's edge. Larger than the 0.62 the full figure used: a
# roughly square crop can afford to come closer to the rim than a
# tall thin one, whose height set the scale and left the width
# looking empty.
BUST_BOX_PCT = 0.76


def _butler_bust() -> Image.Image:
    """Head, shoulders and napkin, cropped from the idle frame."""
    art = Image.open(SOURCE_ART).convert("RGBA")
    return art.crop(BUST_CROP)


def _plate(edge: int) -> Image.Image:
    """The rounded plate with its gold rim, at ``edge`` pixels."""
    img = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(edge * CORNER_RADIUS_PCT)
    rim = max(1, int(edge * RIM_WIDTH_PCT))
    # Inset by half the rim so the stroke lands fully inside the
    # bitmap -- Pillow strokes centred on the path, and a rim that
    # bleeds off the edge looks clipped rather than deliberate.
    inset = rim / 2
    draw.rounded_rectangle(
        [inset, inset, edge - 1 - inset, edge - 1 - inset],
        radius=radius,
        fill=PLATE,
        outline=RIM,
        width=rim,
    )
    return img


def _monogram(edge: int) -> Image.Image:
    """A gold **A**, drawn as three strokes.

    Drawn geometrically rather than typeset: a ``truetype`` lookup
    would make the build depend on a particular font being present
    on the machine doing the packaging, and the letter A is two
    legs and a crossbar.
    """
    img = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    stroke = max(1, int(edge * 0.085))
    # The letter occupies the middle 46% of the plate's height,
    # optically centred (slightly high, because the crossbar draws
    # the eye down).
    top = edge * 0.30
    bottom = edge * 0.71
    half_width = edge * 0.175
    apex = (edge / 2, top)
    left_foot = (edge / 2 - half_width, bottom)
    right_foot = (edge / 2 + half_width, bottom)
    draw.line([left_foot, apex, right_foot], fill=MONOGRAM,
              width=stroke, joint="curve")
    # Crossbar at 64% of the way down the legs, which is where a
    # humanist A puts it.
    bar_y = top + (bottom - top) * 0.64
    bar_half = half_width * 0.64
    draw.line(
        [(edge / 2 - bar_half, bar_y), (edge / 2 + bar_half, bar_y)],
        fill=MONOGRAM,
        width=stroke,
    )
    return img


def _frame(size: int, bust: Image.Image) -> Image.Image:
    """Compose one icon frame at ``size`` pixels.

    Built at ``size * SUPERSAMPLE`` and reduced with ``BOX``, so the
    plate's corners are antialiased without the colour fringing the
    interpolating filters leave on the gold rim.
    """
    edge = size * SUPERSAMPLE
    frame = _plate(edge)
    if size <= SIMPLIFY_AT_OR_BELOW:
        frame.alpha_composite(_monogram(edge))
    else:
        box = int(edge * BUST_BOX_PCT)
        scale = min(box / bust.width, box / bust.height)
        art = bust.resize(
            (max(1, int(bust.width * scale)),
             max(1, int(bust.height * scale))),
            Image.LANCZOS,
        )
        frame.alpha_composite(
            art,
            dest=((edge - art.width) // 2, (edge - art.height) // 2),
        )
    return frame.resize((size, size), Image.BOX)


def main() -> None:
    bust = _butler_bust()
    frames = [_frame(size, bust) for size in ICON_SIZES]
    # The largest frame is the base image and the rest ride along in
    # ``append_images``. Pillow writes a supplied image verbatim when
    # its size matches a requested size exactly, which is what keeps
    # the per-size art from being flattened back into eight resizes
    # of one bitmap. The base must be the *largest* -- a requested
    # size above the base's is silently dropped.
    base, *rest = sorted(frames, key=lambda f: f.size[0], reverse=True)
    base.save(
        OUTPUT_ICON,
        format="ICO",
        sizes=[(s, s) for s in ICON_SIZES],
        append_images=rest,
    )
    size_kb = OUTPUT_ICON.stat().st_size / 1024
    print(
        f"  wrote {OUTPUT_ICON.name} "
        f"({size_kb:.1f} KB, {len(ICON_SIZES)} sizes: "
        f"{', '.join(str(s) for s in ICON_SIZES)})"
    )


if __name__ == "__main__":
    main()
