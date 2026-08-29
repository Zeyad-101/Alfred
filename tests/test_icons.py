"""The hand-painted toolbar glyphs (``ui.icons``).

These used to be Qt's ``QStyle.StandardPixmap`` bitmaps — the
Vista-era blue-and-grey floppy disk, folder and magnifier. They were
the most off-brand thing on screen next to a gold-on-near-black
butler, and they were bitmaps, so they blurred on a HiDPI display.
They are now drawn with QPainter on a 24-unit grid at six sizes.

Drawing cannot be unit-tested for beauty, and this file does not
try: whether ``new_inbox`` reads as a lightning bolt was settled by
eye on a contact sheet (the first attempt, a tray with a slot, read
as "a box with a minus in it" at 16px and was thrown away). What is
pinned here is everything a regression could quietly break —
coverage of the names the call sites use, actual ink in the pixmap
at the smallest size, the accent color, the cache, and the two
non-glyph inputs the function has to keep tolerating.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyle

from ui import icons as icons_mod
from ui.icons import _GLYPHS, _SIZES, clear_cache, standard_icon
from ui.theme import _ACCENT


# The names the app actually asks for, gathered by hand from
# ``ui/editor_panel.py`` and ``ui/main_window.py``. Listed
# explicitly rather than derived from ``_GLYPHS`` so that deleting a
# glyph that is still in use fails here.
NAMES_IN_USE = (
    "save",
    "discard",
    "delete",
    "new",
    "new_inbox",
    "search",
    "history",
    "add",
    "pin",
    "complete",
    "link",
    "convert",
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _inked_pixels(icon: QIcon, size: int) -> int:
    image = icon.pixmap(size, size).toImage()
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    )


# ---------- coverage ----------


class TestEveryNameTheAppUses:
    @pytest.mark.parametrize("name", NAMES_IN_USE)
    def test_it_resolves_to_a_real_icon(self, name, qapp):
        assert name in _GLYPHS, f"{name} is used by a call site"
        assert not standard_icon(name).isNull()

    @pytest.mark.parametrize("name", NAMES_IN_USE)
    def test_it_has_ink_at_16px(self, name, qapp):
        """The size a toolbar button actually draws at.

        A glyph whose path collapsed, or that was drawn outside the
        pixmap, would still return a non-null icon; only counting
        pixels catches it. The floor is deliberately low — these are
        thin 2px strokes on a 16px square — but a blank glyph scores
        exactly zero.
        """
        assert _inked_pixels(standard_icon(name), 16) > 12, name

    @pytest.mark.parametrize("name", NAMES_IN_USE)
    def test_it_offers_a_bitmap_for_every_declared_size(self, name, qapp):
        got = sorted(s.width() for s in standard_icon(name).availableSizes())
        assert got == sorted(_SIZES), name

    def test_there_are_no_glyphs_nobody_asks_for(self, qapp):
        """The table and the call sites agree exactly.

        Not a style rule — an unused glyph is dead paint code that
        nothing reviews, and a *missing* one is a blank toolbar
        button.
        """
        assert sorted(_GLYPHS) == sorted(NAMES_IN_USE)


# ---------- one voice ----------


def test_the_glyphs_are_drawn_in_the_accent_color(qapp):
    """Gold, the same gold as everything else.

    The QStyle icons this replaced were blue and grey, which is why
    they looked borrowed. Checked by finding the most common fully
    opaque color in a large rendering, where antialiased edge pixels
    cannot outvote the stroke itself.
    """
    image = standard_icon("add").pixmap(64, 64).toImage()
    tally: dict[str, int] = {}
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 250:
                tally[color.name()] = tally.get(color.name(), 0) + 1
    assert tally, "the plus sign drew nothing"
    dominant = max(tally, key=tally.__getitem__)
    assert dominant.lower() == _ACCENT.lower()


# ---------- the cache ----------


class TestCaching:
    def test_the_same_name_returns_the_same_object(self, qapp):
        assert standard_icon("save") is standard_icon("save")

    def test_clear_cache_rebuilds(self, qapp):
        first = standard_icon("save")
        clear_cache()
        assert standard_icon("save") is not first

    def test_different_names_are_different_icons(self, qapp):
        assert standard_icon("save") is not standard_icon("delete")


# ---------- the two inputs that are not glyph names ----------


class TestNonGlyphInputs:
    def test_an_unknown_name_returns_a_null_icon_and_does_not_raise(
        self, qapp
    ):
        """A typo must cost a picture, not a crash.

        The QStyle-backed version passed the string straight into
        ``QStyle.standardIcon``, which refuses a ``str`` outright, so
        a misspelled name raised ``TypeError`` and took the whole
        panel down with it. A null icon leaves a button that still
        says what it does.
        """
        icon = standard_icon("definitely-not-a-glyph")
        assert isinstance(icon, QIcon)
        assert icon.isNull()

    def test_a_raw_standard_pixmap_still_passes_through_to_qt(self, qapp):
        """The escape hatch the old implementation had, kept.

        Nothing in the app uses it today, but it costs one branch and
        removing it would be a silent API narrowing.
        """
        icon = standard_icon(QStyle.StandardPixmap.SP_DialogSaveButton)
        assert not icon.isNull()


# ---------- the grid ----------


def test_one_stroke_weight_serves_every_size(qapp):
    """The painter is scaled, not the artwork.

    ``_paint`` scales by ``size / _GRID`` so a 2-unit stroke stays 2
    units at every size, which is what keeps a 16px glyph from
    looking like a hairline and a 64px one from looking like a
    marker. Asserted proportionally: ink should grow roughly with
    area, so the 64px rendering has many times the 16px one, but
    nothing like the 16x a fixed-pixel stroke would give.
    """
    small = _inked_pixels(standard_icon("add"), 16)
    large = _inked_pixels(standard_icon("add"), 64)
    assert small > 0
    assert 4 * small < large < 40 * small


def test_the_grid_and_stroke_constants_are_the_documented_ones(qapp):
    # Pinned because every glyph builder's coordinates are written
    # against them; changing either silently rescales all twelve.
    assert icons_mod._GRID == 24.0
    assert icons_mod._STROKE == 2.0
