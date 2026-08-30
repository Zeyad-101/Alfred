"""The application icon -- one accessor, one identity.

Alfred used to wear three different faces at once:

* the **mascot** was compiled into ``Alfred.exe`` as its file
  icon, so Explorer and the desktop shortcut showed him;
* the **tray** painted its own dark-blue ``#1f3a5f`` rounded
  square with a white "A" -- a color that appears nowhere else in
  the app;
* the **window** had no icon set at all, so the taskbar button,
  the Alt-Tab card and every dialog showed the generic Qt/Python
  feather.

Three answers to "what does Alfred look like" is the same as no
answer. This module is the single one: :func:`app_icon` returns the
icon built by ``assets/build_icon.py`` (a gold-rimmed plate with
the mascot on it, and a gold **A** monogram at the sizes too small
for a character), and everything that needs an icon -- the
QApplication, the tray, any dialog -- asks here.

The ``.ico`` carries eight sizes from 16 to 256, so Qt picks the
right bitmap per surface instead of resampling one. It is listed in
``build.spec``'s ``datas``, which is what makes it loadable at
runtime from a frozen build as well as from a source checkout.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from core.paths import asset_path
from ui.theme import _ACCENT, _BG_PANEL

# The icon file, relative to the project root (and to the unpacked
# bundle root -- ``asset_path`` handles both).
_ICON_REL = "icon.ico"

# Fallback colors. Only reached if the .ico is missing, which in a
# correct build it never is -- but a fallback that is off-palette is a
# fallback nobody notices is being used, so these are the theme's panel
# tier and its accent rather than two hand-picked near-misses.
_PLATE = _BG_PANEL
_RIM = _ACCENT

_CACHE: QIcon | None = None


def _painted_fallback() -> QIcon:
    """Draw a plate-and-monogram icon in Qt, for a missing .ico.

    A null QIcon is worse than an approximate one: Windows would
    fall back to the generic executable icon and the tray item
    would be an invisible gap the user cannot click with any
    confidence. This is the same plate and the same gold, drawn
    with Qt instead of Pillow, at the four sizes that matter.
    """
    icon = QIcon()
    for size in (16, 32, 48, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            rim = max(1.0, size * 0.035)
            radius = size * 0.22
            rect = QRectF(
                rim / 2, rim / 2, size - rim, size - rim
            )
            painter.setPen(QPen(QColor(_RIM), rim))
            painter.setBrush(QColor(_PLATE))
            painter.drawRoundedRect(rect, radius, radius)
            # The monogram, at every size here -- the mascot is not
            # available without Pillow, and a legible A beats an
            # empty plate.
            path = QPainterPath()
            half = size * 0.175
            top, bottom = size * 0.30, size * 0.71
            path.moveTo(size / 2 - half, bottom)
            path.lineTo(size / 2, top)
            path.lineTo(size / 2 + half, bottom)
            bar_y = top + (bottom - top) * 0.64
            path.moveTo(size / 2 - half * 0.64, bar_y)
            path.lineTo(size / 2 + half * 0.64, bar_y)
            pen = QPen(QColor(_RIM), max(1.0, size * 0.085))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon


def app_icon() -> QIcon:
    """Return Alfred's icon, loading it once and caching it.

    Cached because it is asked for on every tray rebuild and every
    dialog; a QIcon is immutable once built, so one instance is
    safe to hand out repeatedly.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = asset_path(_ICON_REL)
    icon = QIcon(str(path)) if path.exists() else QIcon()
    if icon.isNull() or not icon.availableSizes():
        # Missing, or present but unreadable by Qt's ICO plugin.
        icon = _painted_fallback()
    _CACHE = icon
    return icon


def clear_cache() -> None:
    """Forget the cached icon. For tests, and for a future rebuild."""
    global _CACHE
    _CACHE = None
