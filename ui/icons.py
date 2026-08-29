"""Icon set -- hand-painted glyphs in the app's own accent.

Alfred used to hand this out of Qt's built-in
:enum:`QStyle.StandardPixmap` table. That was free and
license-clear, and it looked it: on Windows those are the
Vista-era blue-and-grey bitmaps, so the toolbar shipped a
saturated blue information disc and a grey-and-blue bin
into a window whose entire palette is dark slate and gold.
They were the single most off-brand thing on screen.

These glyphs are painted here instead, in the theme's
accent, from a 24-unit design grid:

* **One color** -- the accent gold, the same
  ``#c9a14a`` the selection fill and the primary button
  use. An icon set in the app's own color reads as one
  product; twelve platform bitmaps read as a toolbar
  someone assembled.
* **Stroked, not filled** -- 2 units of a 24-unit grid,
  round caps and joins. Strokes survive being shrunk to
  16px far better than filled shapes with interior detail.
* **Baked at six sizes** -- 16 / 20 / 24 / 32 / 48 / 64.
  Each is *painted* at its own size rather than scaled
  down from one master, so a 16px glyph is snapped to
  that grid instead of being a blurry 64px one. Qt picks
  the nearest entry per call site and DPI.

The public surface is unchanged: :func:`standard_icon`
still takes the same twelve short names, so no call site
moved. A name that isn't in the table still falls through
to Qt's own set, which keeps the "pass a raw
StandardPixmap" escape hatch working.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QStyle

# The one ink color, taken from the theme rather than retyped so a
# palette change moves the icons with it. ``ui.theme`` imports
# nothing from ``ui``, so this cannot cycle.
from ui.theme import _ACCENT as _INK


# The design grid. Every path below is written in these units and
# scaled to the requested pixel size, so "2 units of stroke" means
# the same visual weight at 16px and at 64px.
_GRID = 24.0
_STROKE = 2.0

# Sizes baked into every icon. Small ones matter most -- the toolbar
# and the editor buttons ask for 16 or 20 at 100% scaling.
_SIZES: tuple[int, ...] = (16, 20, 24, 32, 48, 64)


def _style() -> QStyle:
    """Return the active application style.

    Falls back to a default-constructed QStyle if the application
    isn't up yet (which can happen during very early test setup).
    Only the unknown-name fallback path uses this now.
    """
    app = QApplication.instance()
    if app is None:
        return QStyle()
    return app.style()


# --- glyph paths --------------------------------------------------------
# Each builder returns a QPainterPath in 24-unit space. They are
# stroked, never filled, with two exceptions noted at their
# definitions (the pin head and the arrow heads), which return a
# second path to fill.


def _p(*points: tuple[float, float]) -> QPainterPath:
    """A polyline through ``points``."""
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    return path


def _glyph_save() -> tuple[QPainterPath, QPainterPath | None]:
    """A downward arrow landing in an open tray.

    The modern "commit this to storage" glyph. Deliberately not a
    floppy disk -- the audience for that metaphor has retired.
    """
    stroke = _p((12, 3), (12, 14))
    stroke.addPath(_p((4, 13), (4, 20), (20, 20), (20, 13)))
    head = QPainterPath(QPointF(12, 16.5))
    head.lineTo(QPointF(7.5, 10.5))
    head.lineTo(QPointF(16.5, 10.5))
    head.closeSubpath()
    return stroke, head


def _glyph_discard() -> tuple[QPainterPath, QPainterPath | None]:
    """A counter-clockwise arc with a head -- "put it back"."""
    stroke = QPainterPath()
    stroke.arcMoveTo(QRectF(4, 4, 16, 16), 120)
    stroke.arcTo(QRectF(4, 4, 16, 16), 120, -300)
    head = QPainterPath(QPointF(8, 3.5))
    head.lineTo(QPointF(8, 10))
    head.lineTo(QPointF(2.6, 7.4))
    head.closeSubpath()
    return stroke, head


def _glyph_delete() -> tuple[QPainterPath, QPainterPath | None]:
    """A bin: lid, handle, body, and two ribs."""
    stroke = _p((3.5, 6.5), (20.5, 6.5))
    stroke.addPath(_p((9.5, 6.5), (9.5, 4), (14.5, 4), (14.5, 6.5)))
    stroke.addPath(_p((5.5, 6.5), (6.8, 20.5), (17.2, 20.5), (18.5, 6.5)))
    stroke.addPath(_p((10, 10), (10, 17)))
    stroke.addPath(_p((14, 10), (14, 17)))
    return stroke, None


def _glyph_new() -> tuple[QPainterPath, QPainterPath | None]:
    """A sheet with a folded corner."""
    stroke = _p((5, 3), (14, 3), (19, 8), (19, 21), (5, 21), (5, 3))
    stroke.addPath(_p((14, 3), (14, 8), (19, 8)))
    return stroke, None


def _glyph_new_inbox() -> tuple[QPainterPath, QPainterPath | None]:
    """A bolt -- quick capture, filled.

    The button says "+ Inbox" and the thing it does is the fast,
    no-ceremony capture, so the glyph is speed rather than a
    container. A drawn tray was the first attempt and it read as a
    box with a minus in it at 16px, which is the size that matters;
    a bolt is unmistakable at every size in the set and collides
    with nothing else here.
    """
    bolt = _p(
        (13.5, 2.5),
        (6, 13.5),
        (11, 13.5),
        (10.5, 21.5),
        (18, 10),
        (13, 10),
    )
    bolt.closeSubpath()
    return QPainterPath(), bolt


def _glyph_search() -> tuple[QPainterPath, QPainterPath | None]:
    """A lens and its handle."""
    stroke = QPainterPath()
    stroke.addEllipse(QRectF(4, 4, 12, 12))
    stroke.addPath(_p((15, 15), (20.5, 20.5)))
    return stroke, None


def _glyph_history() -> tuple[QPainterPath, QPainterPath | None]:
    """A clock face with hands at ten past ten."""
    stroke = QPainterPath()
    stroke.addEllipse(QRectF(3.5, 3.5, 17, 17))
    stroke.addPath(_p((12, 7.5), (12, 12.5), (16, 14.5)))
    return stroke, None


def _glyph_add() -> tuple[QPainterPath, QPainterPath | None]:
    """A plus."""
    stroke = _p((12, 4.5), (12, 19.5))
    stroke.addPath(_p((4.5, 12), (19.5, 12)))
    return stroke, None


def _glyph_pin() -> tuple[QPainterPath, QPainterPath | None]:
    """A pushpin, head filled.

    The head is solid because a pinned row is a *state*, and a
    filled shape reads as "on" at 16px where a hollow ring reads as
    an anonymous circle.
    """
    stroke = _p((12, 14), (12, 21))
    head = QPainterPath()
    head.addEllipse(QRectF(7.5, 3, 9, 9))
    head.addPath(_p((10, 11), (14, 11), (13, 14.5), (11, 14.5)))
    return stroke, head


def _glyph_complete() -> tuple[QPainterPath, QPainterPath | None]:
    """A tick, and nothing else.

    Not a ticked circle: the button beside it already says "Mark
    complete", and the bare tick stays legible two sizes smaller
    than a tick inside a ring does.
    """
    return _p((4.5, 12.5), (9.5, 18), (19.5, 6)), None


def _glyph_link() -> tuple[QPainterPath, QPainterPath | None]:
    """Two rings, overlapping -- a chain.

    Side-by-side rounded rectangles were tried first and merged
    into one capsule at small sizes. Two circles that visibly
    intersect keep reading as "joined" all the way down to 16px.
    """
    stroke = QPainterPath()
    stroke.addEllipse(QRectF(2.5, 7.5, 12, 9))
    stroke.addEllipse(QRectF(9.5, 7.5, 12, 9))
    return stroke, None


def _glyph_convert() -> tuple[QPainterPath, QPainterPath | None]:
    """A rightward arrow -- "becomes"."""
    stroke = _p((3.5, 12), (16, 12))
    head = QPainterPath(QPointF(20.5, 12))
    head.lineTo(QPointF(14, 7.5))
    head.lineTo(QPointF(14, 16.5))
    head.closeSubpath()
    return stroke, head


# The name -> glyph table. This is the one place a new icon gets
# wired up, exactly as the old StandardPixmap table was.
_GLYPHS = {
    "save": _glyph_save,
    "discard": _glyph_discard,
    "delete": _glyph_delete,
    "new": _glyph_new,
    "new_inbox": _glyph_new_inbox,
    "search": _glyph_search,
    "history": _glyph_history,
    "add": _glyph_add,
    "pin": _glyph_pin,
    "complete": _glyph_complete,
    "link": _glyph_link,
    "convert": _glyph_convert,
}

# Painted icons are cached by name. Building one means six paints,
# and ``standard_icon`` is called on every editor rebuild; a QIcon is
# immutable once built, so handing the same one out repeatedly is
# safe. The cache is cleared by :func:`clear_cache` if a test (or a
# future theme switch) needs to rebuild them.
_CACHE: dict[str, QIcon] = {}


def clear_cache() -> None:
    """Drop the painted-icon cache.

    Only needed if the ink color changes at runtime, which it
    currently cannot -- Alfred is single-theme. Exposed because a
    cache with no way to clear it is a debugging trap.
    """
    _CACHE.clear()


def _paint(name: str, size: int) -> QPixmap:
    """Render one glyph at one pixel size."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    stroke, fill = _GLYPHS[name]()
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(size / _GRID, size / _GRID)
        ink = QColor(_INK)
        pen = QPen(ink)
        # The stroke is specified in grid units and the painter is
        # already scaled, so this is one weight at every size. Round
        # caps stop a 2-unit stroke from looking chopped at 16px.
        pen.setWidthF(_STROKE)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(stroke)
        if fill is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(ink)
            painter.drawPath(fill)
    finally:
        painter.end()
    return pixmap


def standard_icon(name: str):
    """Look up a :class:`QIcon` by short name.

    The twelve names are unchanged from the QStyle-backed version
    this replaced, so every call site kept working untouched.

    Anything else falls through to Qt's own set, which keeps the
    "pass a raw ``QStyle.StandardPixmap``" escape hatch the old
    implementation had. An unrecognized *string* returns an empty
    icon rather than raising: PySide's ``standardIcon`` refuses a
    str outright, so the previous version raised ``TypeError`` on a
    typo'd name -- a null icon means a missing picture next to a
    button that still says what it does.
    """
    if name not in _GLYPHS:
        if isinstance(name, str):
            return QIcon()
        return _style().standardIcon(name)
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    icon = QIcon()
    for size in _SIZES:
        icon.addPixmap(_paint(name, size))
    _CACHE[name] = icon
    return icon
