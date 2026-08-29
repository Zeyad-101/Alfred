"""Derive Alfred's animation frames from the one source drawing.

Why this file exists
--------------------
The 28 PNGs that shipped under ``assets/alfred/frames/`` were the same
drawing 28 times. Measured across the whole set, the figure's centre of
mass moved at most 1.5 px horizontally and 2 px vertically, ``idle_04``
was the same image as ``idle_00``, and ``thinking_NN`` differed from
``idle_NN`` only by an anti-aliased edge shift -- identical eyes,
pupils and moustache at 6x zoom. The frame engine, the timer, the frame
loading and the packaging were all correct. There was simply nothing in
the pixels to see, so Alfred read as one static standing butler and
"thinking" was indistinguishable from "idle".

This fixes it where it is actually broken: the art. The character is
the one that shipped -- he is not redrawn -- but the poses are now
derived from him rather than jittered.

How
---
The figure is cut into four layers along seams the drawing itself
provides, located by measuring the alpha channel's per-row width:

* ``head``  -- above the neck, the narrowest row between crown and
  shoulders
* ``torso`` -- neck to hip
* ``towel`` -- the folded white napkin over his forearm, isolated as the
  largest bright low-saturation blob *below the collar* (the shirt is
  the same white, hence the floor)
* ``legs``  -- hip to shoes, including the drop shadow

Each layer is transformed about a pivot that sits ON its own seam, which
is what keeps the seams invisible: rotating about the cut line displaces
the cut line by nothing. Translation needs two more things, and needed
both -- the first attempt had a 2 px translucent band across the hip on
every frame where the chest was raised:

* the upper layer keeps a few rows *past* its seam with alpha ramped to
  zero, so its cut edge dissolves rather than showing a step, and
* the lower layer extends a few rows back *up* behind the seam at full
  alpha, so when the upper layer lifts there is always something opaque
  underneath it. A feather with nothing behind it is a hole.

Every warp happens at 4x with alpha premultiplied first. At 160x160 a
1 px move is 0.7% of the figure's height, so sub-pixel accuracy is the
difference between motion and stepping; and warping straight (un-
premultiplied) RGBA drags the transparent black background into every
edge as a dark halo. The 4x round trip is nearest-neighbour up and box
(area-average) down, which is exactly lossless for an untransformed
layer -- LANCZOS both ways blurred the drawing's 1 px linework, turning
the dark outline under the napkin from 20 to 86, and softening a
character the user likes is not an acceptable price for smooth
motion.

The states, and what each is meant to read as:

* ``idle`` (8)     -- breathing. Feet planted, torso rising ~1.6 px and
  stretching 1.4%, head rising 3 px a beat behind it, napkin swinging
  like the cloth it is. Deliberately quiet.
* ``thinking`` (12) -- a thought cloud with three cycling dots in the
  empty upper right of the canvas (the side he faces), his head tilted
  ~12 degrees into it, a lean, and a faster napkin swing. This is the
  state the user could not see. It is now the loudest thing on screen.
* ``greeting`` (8)  -- a bow. Head, torso and napkin pitch forward
  together about the hip, the head adding its own nod on top, then
  return. One-shot, so it is keyframed rather than a loop.

Frame counts and canvas size are unchanged (8/12/8 at 160x160): the
engine, the manifest and the tests all pin them.

Run:  venv/Scripts/python.exe assets/generate_alfred_frames.py
"""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = ROOT / "assets" / "alfred"
SOURCE = ASSET_DIR / "source" / "alfred_base.png"
FRAME_ROOT = ASSET_DIR / "frames"
MANIFEST = ASSET_DIR / "manifest.json"

CANVAS = 160
SS = 4  # supersample factor for every warp
ALPHA_FLOOR = 40  # below this is drop shadow, not figure
SEAM_FEATHER = 5  # rows a layer keeps past its seam, ramped to zero
SEAM_UNDERLAP = 6  # rows the layer *beneath* a seam extends back up

STATES = ("idle", "thinking", "greeting")
FRAME_COUNTS = {"idle": 8, "thinking": 12, "greeting": 8}
INTERVALS = {"idle": 120, "thinking": 110, "greeting": 115}
LOOPS = {"idle": True, "thinking": True, "greeting": False}

LAYER_ORDER = ("legs", "torso", "towel", "head")

# --- the thought cloud ------------------------------------------------------
# Canvas coordinates. The figure occupies x 54..107, so this whole region
# is empty in the source drawing -- the cloud costs no sprite real estate
# and needs no rescaling of a character the user likes as-is.
CLOUD_CENTER = (134.0, 21.0)
CLOUD_LOBES = ((-12.0, 2.0, 8.0), (-3.0, -4.0, 9.5), (7.0, 1.0, 8.5), (14.0, 4.0, 6.0))
CLOUD_PUFFS = ((-22.0, 13.0, 3.2), (-28.0, 20.0, 2.3))
CLOUD_DOTS = ((-8.0, 2.0), (0.0, 2.0), (8.0, 2.0))
DOT_RADIUS = 2.7
OUTLINE_WIDTH = 1.6
CLOUD_FILL = (247, 246, 242, 255)
CLOUD_LINE = (22, 22, 26, 255)
DOT_ON = (36, 36, 44, 255)
DOT_OFF = (198, 198, 203, 255)

# Bow keyframes: (upper-body angle, upper-body dy, head angle, squash).
# Positive angles are clockwise on screen, i.e. pitching towards the side
# he faces. Rotation alone read as a sideways sway, so the bow also drops
# the upper body and squashes it vertically -- foreshortening is what
# tells the eye the motion is towards the viewer and not across him.
# Held at the bottom for two frames so the bow lands.
GREETING_KEYS = (
    (0.0, 0.0, 0.0, 1.0),
    (4.5, 1.5, 5.0, 0.985),
    (9.0, 3.5, 10.0, 0.968),
    (12.0, 5.0, 13.0, 0.955),
    (12.0, 5.0, 13.0, 0.955),
    (8.0, 3.5, 9.0, 0.970),
    (3.5, 1.5, 4.0, 0.988),
    # A shade past upright on the way back, so the bow settles instead of
    # snapping to attention -- and so no frame repeats frame 0 exactly.
    (-1.2, -0.6, -1.5, 1.004),
)


@dataclass(frozen=True)
class Geometry:
    """Where the drawing's own anatomy is, in canvas pixels."""

    top: int
    foot: int
    neck_y: int
    hip_y: int
    cx: float
    towel_pivot: tuple[float, float]


def measure(img: Image.Image) -> Geometry:
    """Locate the seams by looking at the figure, not at magic numbers."""
    solid = np.asarray(img.getchannel("A")) > ALPHA_FLOOR
    rows = np.flatnonzero(solid.any(axis=1))
    top, foot = int(rows[0]), int(rows[-1])
    width = solid.sum(axis=1)

    # The neck is the narrowest row between the crown and the shoulders:
    # a head is wide, a neck is not, and shoulders are wider again.
    lo, hi = top + 25, top + 56
    band = np.where(width[lo:hi] == 0, 10_000, width[lo:hi])
    neck_y = lo + int(np.argmin(band)) + 2  # cut just below it, collar stays

    # The hip has no such signature (the jacket hem is wider than the
    # waist), so it is taken as the midpoint of neck-to-shoe. That is the
    # pivot a bow rotates about, and it only has to be plausible.
    hip_y = neck_y + round(0.50 * (foot - neck_y))

    head_rows = solid[top : top + 27]
    centres = [(np.flatnonzero(r)[0] + np.flatnonzero(r)[-1]) / 2 for r in head_rows if r.any()]
    cx = float(np.mean(centres))

    mask = towel_mask(img)
    ys, xs = np.nonzero(mask)
    top_rows = ys <= ys.min() + 2
    towel_pivot = (float(xs[top_rows].mean()), float(ys.min()))

    return Geometry(top=top, foot=foot, neck_y=neck_y, hip_y=hip_y, cx=cx, towel_pivot=towel_pivot)


def towel_mask(img: Image.Image) -> np.ndarray:
    """The folded white napkin over his forearm, as a boolean mask.

    Bright and desaturated picks out the napkin, the shirt front, the
    collar highlight and one specular dot on a shoe, so the search is
    floored below the collar and then reduced to its largest connected
    blob -- which is the napkin, by a wide margin.
    """
    arr = np.asarray(img, dtype=np.int16)
    rgb = arr[..., :3]
    bright = (arr[..., 3] > 120) & (rgb.min(axis=2) > 150) & (np.ptp(rgb, axis=2) < 45)
    bright[: CANVAS // 2 - 2, :] = False
    return _largest_blob(bright)


def _largest_blob(mask: np.ndarray) -> np.ndarray:
    """8-connected largest component. Small inputs, so BFS is plenty."""
    seen = np.zeros_like(mask)
    best = np.zeros_like(mask)
    for sy, sx in zip(*np.nonzero(mask)):
        if seen[sy, sx]:
            continue
        blob = np.zeros_like(mask)
        queue = deque([(sy, sx)])
        seen[sy, sx] = True
        while queue:
            y, x = queue.popleft()
            blob[y, x] = True
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1]:
                        if mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            queue.append((ny, nx))
        if blob.sum() > best.sum():
            best = blob
    return best


def inpaint_columns(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """Fill the napkin's footprint from the suit directly above/below it.

    The napkin has to be lifted off the torso to move independently, and
    what it was covering was never drawn. The jacket and trousers are
    near-black with *vertical* pinstripes, so a vertical copy is the one
    direction in which the repair does not show. Columns with no suit to
    borrow from (the silhouette edge) are left transparent rather than
    invented, so the repair can never widen his outline.
    """
    arr = np.asarray(img).copy()
    usable = (arr[..., 3] > 200) & ~mask
    for y, x in zip(*np.nonzero(mask)):
        column = np.flatnonzero(usable[:, x])
        if column.size == 0:
            arr[y, x] = (0, 0, 0, 0)
            continue
        below = column[column > y]
        above = column[column < y]
        candidates = [int(below[0])] if below.size else []
        if above.size:
            candidates.append(int(above[-1]))
        # Nearest source row, preferring the one below on a tie: below the
        # napkin is jacket and trouser, above it is the same jacket.
        pick = min(candidates, key=lambda r: (abs(r - y), 0 if r > y else 1))
        arr[y, x] = arr[pick, x]
    return Image.fromarray(arr, "RGBA")


def _band(src: Image.Image, y0: int, y1: int, feather: int = 0) -> Image.Image:
    """Rows ``y0:y1`` of ``src`` on a full-size transparent canvas.

    ``feather`` ramps the last N rows' alpha to zero so a layer that
    translates dissolves into the layer beneath instead of showing a
    hard cut edge.
    """
    out = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    out.paste(src.crop((0, y0, CANVAS, y1)), (0, y0))
    if feather:
        arr = np.asarray(out).copy()
        for k in range(feather):
            row = y1 - feather + k
            if 0 <= row < CANVAS:
                arr[row, :, 3] = (arr[row, :, 3].astype(np.float32) * (1.0 - (k + 1) / (feather + 1))).astype(np.uint8)
        out = Image.fromarray(arr, "RGBA")
    return out


def build_layers(img: Image.Image, geo: Geometry) -> dict[str, Image.Image]:
    """Cut the drawing into the four pieces that move independently."""
    mask = towel_mask(img)
    body = inpaint_columns(img, mask)

    towel = np.asarray(img).copy()
    towel[..., 3] = np.where(mask, towel[..., 3], 0)

    return {
        "legs": _band(body, geo.hip_y - SEAM_UNDERLAP, CANVAS),
        "torso": _band(body, geo.neck_y - SEAM_UNDERLAP, geo.hip_y + SEAM_FEATHER, feather=SEAM_FEATHER),
        "towel": Image.fromarray(towel, "RGBA"),
        "head": _band(body, 0, geo.neck_y + SEAM_FEATHER, feather=SEAM_FEATHER),
    }


# --- transforms -------------------------------------------------------------
# A forward map is the 6-tuple (a11, a12, b1, a21, a22, b2) meaning
#     p_out = A @ p_in + b
# in canvas coordinates. ``matrix`` builds one from an intent (pivot,
# rotation, scale, translation); ``chain`` composes two; ``warp`` inverts
# and hands it to Pillow, which wants the output-to-input map.


def matrix(
    center: tuple[float, float],
    angle: float = 0.0,
    scale: tuple[float, float] = (1.0, 1.0),
    translate: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, ...]:
    """``p_out = center + translate + R(angle) @ S @ (p_in - center)``.

    ``angle`` is degrees *clockwise as seen on screen* -- y grows
    downwards in image space, so the usual counter-clockwise matrix
    reads clockwise here. Positive therefore tips the top of a layer
    towards the side Alfred faces, which is the direction every pose in
    this file wants.
    """
    th = math.radians(angle)
    cos, sin = math.cos(th), math.sin(th)
    sx, sy = scale
    a11, a12 = cos * sx, -sin * sy
    a21, a22 = sin * sx, cos * sy
    cx, cy = center
    b1 = cx + translate[0] - (a11 * cx + a12 * cy)
    b2 = cy + translate[1] - (a21 * cx + a22 * cy)
    return (a11, a12, b1, a21, a22, b2)


def chain(outer: tuple[float, ...], inner: tuple[float, ...]) -> tuple[float, ...]:
    """The map that applies ``inner`` first, then ``outer``."""
    a11, a12, b1, a21, a22, b2 = outer
    c11, c12, d1, c21, c22, d2 = inner
    return (
        a11 * c11 + a12 * c21,
        a11 * c12 + a12 * c22,
        a11 * d1 + a12 * d2 + b1,
        a21 * c11 + a22 * c21,
        a21 * c12 + a22 * c22,
        a21 * d1 + a22 * d2 + b2,
    )


def _invert(m: tuple[float, ...]) -> tuple[float, ...]:
    a11, a12, b1, a21, a22, b2 = m
    det = a11 * a22 - a12 * a21
    i11, i12 = a22 / det, -a12 / det
    i21, i22 = -a21 / det, a11 / det
    return (i11, i12, -(i11 * b1 + i12 * b2), i21, i22, -(i21 * b1 + i22 * b2))


def warp(layer_ss: Image.Image, m: tuple[float, ...]) -> Image.Image:
    """Apply a canvas-space forward map to a supersampled layer.

    Scaling the canvas by SS leaves the linear part alone and multiplies
    only the translation, so the same intent works at any resolution.
    """
    scaled = (m[0], m[1], m[2] * SS, m[3], m[4], m[5] * SS)
    return layer_ss.transform(layer_ss.size, Image.AFFINE, _invert(scaled), resample=Image.BICUBIC)


def _premultiply(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img, dtype=np.float32)
    out = arr.copy()
    out[..., :3] *= arr[..., 3:4] / 255.0
    return out


def _to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8), "RGBA")


def _over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Premultiplied source-over. Both operands are premultiplied."""
    return src + dst * (1.0 - src[..., 3:4] / 255.0)


def _unpremultiply(arr: np.ndarray) -> Image.Image:
    alpha = arr[..., 3:4]
    rgb = np.minimum(arr[..., :3], alpha)  # rounding can nudge c past a
    safe = np.where(alpha == 0, 1.0, alpha)
    out = arr.copy()
    out[..., :3] = rgb * 255.0 / safe
    return _to_image(out)


# --- the thought cloud ------------------------------------------------------


def _ellipse(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, colour) -> None:
    draw.ellipse(
        [(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
        fill=colour,
    )


def thought_cloud(lit: int, pulse: float) -> np.ndarray:
    """A cloud of overlapping lobes with ``lit`` of three dots filled.

    Drawn as one silhouette in the outline colour and then the same
    lobes inset by the line width in the fill colour, so the union gets
    a single clean outline instead of every lobe keeping its own -- the
    same trick the source drawing's own linework uses.
    """
    layer = Image.new("RGBA", (CANVAS * SS, CANVAS * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = CLOUD_CENTER

    for colour, inset in ((CLOUD_LINE, 0.0), (CLOUD_FILL, OUTLINE_WIDTH)):
        for dx, dy, r in CLOUD_PUFFS + CLOUD_LOBES:
            _ellipse(draw, cx + dx * pulse, cy + dy * pulse, max(r * pulse - inset, 0.6), colour)

    for i, (dx, dy) in enumerate(CLOUD_DOTS):
        _ellipse(
            draw,
            cx + dx * pulse,
            cy + dy * pulse,
            DOT_RADIUS,
            DOT_ON if i < lit else DOT_OFF,
        )

    return _premultiply(layer)


# --- poses ------------------------------------------------------------------


def idle_pose(i: int, n: int, geo: Geometry) -> dict[str, tuple[float, ...]]:
    """Breathing: feet planted, chest rising, head following a beat late."""
    t = i / n
    breath = 0.5 - 0.5 * math.cos(2 * math.pi * t)  # 0 -> 1 -> 0, loops clean
    sway = math.sin(2 * math.pi * t)
    drift = (0.7 * sway, -2.2 * breath)
    torso = matrix((geo.cx, geo.hip_y), scale=(1.0, 1.0 + 0.018 * breath), translate=drift)
    return {
        "torso": torso,
        # The napkin rides the torso and swings on its own hang point.
        "towel": chain(matrix((geo.cx, geo.hip_y), translate=drift), matrix(geo.towel_pivot, angle=-7.0 * sway)),
        # The head leads the chest by a quarter cycle and turns as it
        # rises: a head that only translates reads as a lift, a head that
        # also turns reads as somebody standing there.
        "head": matrix((geo.cx, geo.neck_y), angle=2.6 * sway, translate=(1.3 * sway, -4.0 * breath)),
    }


def thinking_pose(i: int, n: int, geo: Geometry) -> dict[str, tuple[float, ...]]:
    """Head tilted into the thought cloud, leaning, napkin swinging twice."""
    t = i / n
    ease = 0.5 - 0.5 * math.cos(2 * math.pi * t)  # dwells at the tilt
    swing = math.sin(4 * math.pi * t)
    breath = 0.5 - 0.5 * math.cos(4 * math.pi * t)
    lean = (1.6 * ease, -1.0 * breath)
    upper = matrix((geo.cx, geo.hip_y), angle=1.5 * ease, translate=lean)
    return {
        "torso": upper,
        "towel": chain(upper, matrix(geo.towel_pivot, angle=-7.0 * swing)),
        "head": chain(upper, matrix((geo.cx, geo.neck_y), angle=11.0 * ease, translate=(0.0, -1.0 * breath))),
    }


def greeting_pose(i: int, n: int, geo: Geometry) -> dict[str, tuple[float, ...]]:
    """A bow: upper body pitches and shortens about the hip, head nods on."""
    angle, dy, head_angle, squash = GREETING_KEYS[i]
    upper = matrix((geo.cx, geo.hip_y), angle=angle, scale=(1.0, squash), translate=(0.0, dy))
    return {
        "torso": upper,
        "towel": chain(upper, matrix(geo.towel_pivot, angle=-0.8 * angle)),
        "head": chain(
            upper,
            matrix((geo.cx, geo.neck_y), angle=head_angle, translate=(0.0, 0.22 * head_angle)),
        ),
    }


POSES = {"idle": idle_pose, "thinking": thinking_pose, "greeting": greeting_pose}


# --- rendering --------------------------------------------------------------


def render(layers_ss: dict[str, Image.Image], poses: dict[str, tuple[float, ...]], cloud: np.ndarray | None) -> Image.Image:
    """Composite one frame: warp each layer, stack, add cloud, downsample."""
    acc = np.zeros((CANVAS * SS, CANVAS * SS, 4), dtype=np.float32)
    for name in LAYER_ORDER:
        m = poses.get(name)
        layer = layers_ss[name] if m is None else warp(layers_ss[name], m)
        acc = _over(acc, np.asarray(layer, dtype=np.float32))
    if cloud is not None:
        acc = _over(acc, cloud)
    small = _to_image(acc).resize((CANVAS, CANVAS), Image.BOX)
    return _unpremultiply(np.asarray(small, dtype=np.float32))


def self_check(base: Image.Image, layers_ss: dict[str, Image.Image]) -> None:
    """The cuts must lose nothing and the identity warp must be a no-op.

    Rebuilding the untransformed layers has to reproduce the source
    drawing. If the seams, the feather or the napkin repair were wrong,
    this is where it shows -- before 28 frames are written from it.
    """
    ident = matrix((80.0, 80.0))
    rebuilt = render(layers_ss, {name: ident for name in LAYER_ORDER}, None)
    a = np.asarray(base, dtype=np.int16)
    b = np.asarray(rebuilt, dtype=np.int16)
    # Compare where the figure is opaque; RGB under a transparent pixel
    # is meaningless and the source has its own soft shadow.
    solid = a[..., 3] > 200
    rgb_delta = np.abs(a[..., :3] - b[..., :3])[solid]
    alpha_delta = np.abs(a[..., 3] - b[..., 3])
    print(
        f"  self-check: rebuilt vs source  rgb mean {rgb_delta.mean():.2f} max {rgb_delta.max():3d}"
        f" | alpha mean {alpha_delta.mean():.2f} max {alpha_delta.max():3d}"
    )
    assert rgb_delta.mean() < 0.5, "layer split does not reproduce the drawing"
    assert alpha_delta.mean() < 0.5, "layer split loses coverage"


def main() -> None:
    base = Image.open(SOURCE).convert("RGBA")
    if base.size != (CANVAS, CANVAS):
        raise SystemExit(f"source must be {CANVAS}x{CANVAS}, got {base.size}")

    geo = measure(base)
    print(f"source: {SOURCE.relative_to(ROOT)}")
    print(
        f"  figure rows {geo.top}..{geo.foot} | neck y={geo.neck_y} | hip y={geo.hip_y}"
        f" | centre x={geo.cx:.1f} | napkin pivot {geo.towel_pivot[0]:.1f},{geo.towel_pivot[1]:.0f}"
    )

    layers = build_layers(base, geo)
    layers_ss = {
        name: _to_image(_premultiply(img)).resize((CANVAS * SS, CANVAS * SS), Image.NEAREST)
        for name, img in layers.items()
    }
    for name, img in layers.items():
        print(f"  layer {name:6s} opaque px {int((np.asarray(img)[..., 3] > 40).sum()):5d}")
    self_check(base, layers_ss)

    for state in STATES:
        count = FRAME_COUNTS[state]
        out_dir = FRAME_ROOT / state
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            poses = POSES[state](i, count, geo)
            cloud = None
            if state == "thinking":
                # Dots advance every second frame: 220 ms a step, so the
                # three-dot cycle reads at a human pace, not a flicker.
                lit = 1 + (i // 2) % 3
                pulse = 1.0 + 0.035 * math.sin(2 * math.pi * i / count)
                cloud = thought_cloud(lit, pulse)
            render(layers_ss, poses, cloud).save(out_dir / f"{state}_{i:02d}.png")
        print(f"  wrote {count:2d} frames -> {out_dir.relative_to(ROOT)}")

    MANIFEST.write_text(
        json.dumps(
            {
                "name": "Alfred",
                "states": {
                    state: {
                        "frames": FRAME_COUNTS[state],
                        "interval_ms": INTERVALS[state],
                        "loop": LOOPS[state],
                    }
                    for state in STATES
                },
                "size": [CANVAS, CANVAS],
                "source": "source/alfred_base.png",
                "generator": "assets/generate_alfred_frames.py",
                "notes": (
                    "Poses are derived from the single source drawing by cutting it "
                    "into head/torso/napkin/legs layers and transforming each about "
                    "its own seam. No running, walking, sleeping or jumping states."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
