"""The application icon — one file, one accessor, every surface.

Alfred used to show three different icons at once: the mascot on
``Alfred.exe`` (Explorer), a hand-painted dark-blue "A" in the tray,
and Qt's generic feather on the window, the taskbar button and every
dialog — that last one because nothing ever called
``setWindowIcon``. The art was also thinner than it looked: the
generated ``icon.ico`` carried no 256px entry at all, because Pillow
silently drops any requested size larger than the base image and the
base was a 160px sprite.

What is pinned here is the *contract*, not the drawing:

* ``icon.ico`` exists and carries the full size ladder, 16 → 256;
* :func:`ui.app_icon.app_icon` returns a non-null icon, memoized;
* it never returns null even with the file missing, because a null
  icon means Windows falls back to the generic executable icon and
  the tray item becomes an invisible gap;
* the tray reuses that same icon rather than painting its own.

Whether the mascot is recognizable is a judgement call made by eye
from a contact sheet, and no assertion here pretends otherwise.
"""
from __future__ import annotations

import struct

import pytest

from core.paths import asset_path
from ui import app_icon as app_icon_mod
from ui.app_icon import _painted_fallback, app_icon, clear_cache


# Every size ``assets/build_icon.py`` is asked to write. 256 is the
# one that matters most and the one that used to be missing: Windows
# uses it for large-icon views and for the Alt-Tab card on a HiDPI
# display, and without it the OS upscales 128 into a soft mess.
EXPECTED_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Drop the module cache around every test.

    The icon is memoized in a module global, so a test that swaps
    the file path would otherwise be answered from a neighbour's
    cached instance.
    """
    clear_cache()
    yield
    clear_cache()


# ---------- the file on disk ----------


class TestTheIcoFile:
    def test_it_exists_where_asset_path_looks(self):
        assert asset_path("icon.ico").is_file()

    def test_it_declares_every_size_in_its_directory(self):
        """Read the ICO header directly rather than trusting Qt.

        An ICO is a 6-byte header followed by one 16-byte directory
        entry per image; byte 0 of an entry is the width, with 0
        meaning 256 (a single byte cannot hold it). Parsing it here
        means the test fails when the *file* is wrong, not merely
        when the loader in front of it is.
        """
        raw = asset_path("icon.ico").read_bytes()
        _reserved, kind, count = struct.unpack("<HHH", raw[:6])
        assert kind == 1, "type 1 is an icon; 2 would be a cursor"
        sizes = []
        for i in range(count):
            entry = raw[6 + i * 16:6 + i * 16 + 16]
            width = entry[0] or 256
            height = entry[1] or 256
            assert width == height, "Alfred's icon is square at every size"
            sizes.append(width)
        assert sorted(sizes) == sorted(EXPECTED_SIZES)


# ---------- the accessor ----------


class TestAppIcon:
    def test_it_is_not_null(self, qapp):
        assert not app_icon().isNull()

    def test_it_carries_the_whole_size_ladder(self, qapp):
        got = sorted(s.width() for s in app_icon().availableSizes())
        assert got == sorted(EXPECTED_SIZES)

    def test_the_pixmaps_actually_have_ink_in_them(self, qapp):
        """A loadable but blank icon would pass every check above.

        Sampled at 16 and 256 — the two ends of the ladder, and the
        two that draw different art (monogram vs. mascot).
        """
        for size in (16, 256):
            image = app_icon().pixmap(size, size).toImage()
            assert not image.isNull()
            opaque = sum(
                1
                for y in range(image.height())
                for x in range(image.width())
                if image.pixelColor(x, y).alpha() > 0
            )
            # The plate fills most of the square, so "some ink" is a
            # very low bar to clear; the point is that it is not zero.
            assert opaque > (size * size) // 4, size

    def test_it_is_memoized(self, qapp):
        assert app_icon() is app_icon()

    def test_clear_cache_forces_a_reload(self, qapp):
        first = app_icon()
        clear_cache()
        assert app_icon() is not first


# ---------- the fallback ----------


class TestTheFallbackNeverReturnsNull:
    def test_a_missing_file_still_yields_a_usable_icon(
        self, qapp, monkeypatch, tmp_path
    ):
        """The whole reason the fallback exists.

        A null QIcon is not a neutral outcome on Windows: the window
        reverts to the generic executable icon and the tray entry
        becomes a blank gap the user has no confidence clicking. An
        approximate plate-and-monogram is strictly better.
        """
        monkeypatch.setattr(
            app_icon_mod, "asset_path", lambda rel: tmp_path / "absent.ico"
        )
        icon = app_icon()
        assert not icon.isNull()
        assert icon.availableSizes()

    def test_the_fallback_draws_at_the_sizes_that_matter(self, qapp):
        got = sorted(s.width() for s in _painted_fallback().availableSizes())
        assert got == [16, 32, 48, 256]

    def test_the_fallback_is_inked_at_16px(self, qapp):
        """The size the tray paints at, and the hardest to draw.

        If the monogram were dropped or drawn in the plate color this
        would still be a rounded square; the assertion is that more
        than one distinct color survives, i.e. the "A" is visible
        against its plate.
        """
        image = _painted_fallback().pixmap(16, 16).toImage()
        colors = {
            image.pixelColor(x, y).name()
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 200
        }
        assert len(colors) > 1


# ---------- the tray reuses it ----------


def test_the_tray_no_longer_paints_its_own_icon(conn, qapp):
    """``_build_tray_icon`` hands back the shared icon.

    It used to paint a 64x64 ``#1f3a5f`` square with a white "A" --
    a blue used nowhere else in the app. Identity checked with
    ``is``, which is the strongest available statement that no
    second icon is being built anywhere.
    """
    from ui.main_window import MainWindow

    window = MainWindow(conn)
    try:
        assert window._build_tray_icon() is app_icon()
        assert not window.windowIcon().isNull()
    finally:
        window.deleteLater()
