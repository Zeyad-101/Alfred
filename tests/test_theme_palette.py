"""The dark palette, and the contrast of the tokens it is built from.

The bug this file exists for was invisible in code review and obvious
in a screenshot: ``apply_theme()`` set ``DARK_QSS`` and nothing else,
so the app ran on Qt's *light* default palette. Every surface a QSS
selector happened to cover looked right; everything painted straight
from a palette role did not. The visible symptoms were

* alternating list rows drawing near-white (``AlternateBase`` was
  ``#f7f7f7`` and no QSS rule set ``alternate-background-color``,
  while ``MemoryListWidget`` has ``setAlternatingRowColors(True)``);
* placeholder text drawing black on the ``#353a42`` input fill;
* selections in palette-painted views using Qt's blue ``#308cc6``
  instead of Alfred's gold;
* tooltips on the default cream ``#ffffdc``.

So the first half of this file asserts the palette roles are dark and
on-brand, and the second half computes WCAG contrast over the token
table -- the two failures found by measuring rather than looking were
``_FG_MUTED`` at 2.94:1 (real body text, below the 4.5:1 floor) and
``_BORDER_STRONG`` at 1.96:1 (a UI component outline, below 3:1).
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPalette

from ui import theme
from ui.theme import (
    _ACCENT,
    _BG_INPUT,
    _BG_PANEL,
    _BG_WINDOW,
    _BORDER_STRONG,
    _FG_DISABLED,
    _FG_MUTED,
    _FG_PLACEHOLDER,
    _FG_PRIMARY,
    _FG_SECONDARY,
    DARK_QSS,
    dark_palette,
)


# ---------- WCAG arithmetic ----------


def _relative_luminance(hex_color: str) -> float:
    """Relative luminance per WCAG 2.x, from an ``#rrggbb`` string."""
    color = QColor(hex_color)
    channels = []
    for raw in (color.redF(), color.greenF(), color.blueF()):
        channels.append(
            raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4
        )
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def test_the_contrast_helper_agrees_with_the_two_known_extremes():
    """Guard the arithmetic before trusting it on the tokens.

    Black on white is exactly 21:1 and any color on itself is exactly
    1:1; if the formula were wrong these would not land.
    """
    assert _contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert _contrast("#c9a14a", "#c9a14a") == pytest.approx(1.0, abs=0.001)


# ---------- the tokens ----------


# Text tokens and the grounds they are actually drawn on -- pairs, not
# a cross product, because which ink lands on which fill is the whole
# question. ``_FG_MUTED`` is deliberately absent from the input fill:
# it measures 3.78:1 there, which is why the placeholder tier exists.
# WCAG 1.4.3 wants 4.5:1 for body text; every size in Alfred's scale is
# below the 18pt / 14pt-bold "large text" threshold where 3:1 would be
# allowed, so the strict floor applies throughout.
TEXT_ON_GROUND = [
    ("_FG_PRIMARY on window", _FG_PRIMARY, _BG_WINDOW),
    ("_FG_PRIMARY on panel", _FG_PRIMARY, _BG_PANEL),
    ("_FG_PRIMARY on input", _FG_PRIMARY, _BG_INPUT),
    ("_FG_SECONDARY on window", _FG_SECONDARY, _BG_WINDOW),
    ("_FG_SECONDARY on panel", _FG_SECONDARY, _BG_PANEL),
    ("_FG_MUTED on window", _FG_MUTED, _BG_WINDOW),
    ("_FG_MUTED on panel", _FG_MUTED, _BG_PANEL),
    ("_FG_PLACEHOLDER on input", _FG_PLACEHOLDER, _BG_INPUT),
]


class TestTextContrast:
    @pytest.mark.parametrize(
        "label,fg,bg", TEXT_ON_GROUND, ids=[c[0] for c in TEXT_ON_GROUND]
    )
    def test_it_clears_the_body_text_floor(self, label, fg, bg):
        assert _contrast(fg, bg) >= 4.5, (
            f"{label} measures {_contrast(fg, bg):.2f}:1"
        )

    def test_the_muted_tier_is_still_visibly_quieter_than_secondary(self):
        """Fixing contrast must not flatten the hierarchy.

        ``_FG_MUTED`` was raised from ``#6f757f`` to clear 4.5:1. Had
        it been raised all the way to ``_FG_SECONDARY`` the fix would
        have cost the three-tier type hierarchy, which is the thing
        the muted tier exists for.
        """
        assert _contrast(_FG_MUTED, _BG_PANEL) < _contrast(
            _FG_SECONDARY, _BG_PANEL
        )

    def test_the_placeholder_tier_sits_below_secondary_on_its_own_ground(self):
        """A prompt, not content.

        The placeholder token had to be lighter than ``_FG_MUTED`` to
        clear the floor on the input fill; it must not have gone past
        ``_FG_SECONDARY``, or "Search your files" would read as loudly
        as the text the user types over it.
        """
        assert _contrast(_FG_PLACEHOLDER, _BG_INPUT) < _contrast(
            _FG_SECONDARY, _BG_INPUT
        )
        assert _contrast(_FG_PLACEHOLDER, _BG_INPUT) > _contrast(
            _FG_MUTED, _BG_INPUT
        )

    def test_disabled_text_is_deliberately_below_the_floor(self):
        """Not an oversight -- 1.4.3 exempts inactive components.

        Looking unavailable is the whole job of this token, and a
        disabled control that meets the same contrast as an enabled one
        is a worse interface. Pinned so nobody "fixes" it.
        """
        assert _contrast(_FG_DISABLED, _BG_PANEL) < 4.5


def test_component_outlines_clear_the_ui_component_floor():
    """WCAG 1.4.11 holds non-text UI boundaries to 3:1.

    ``_BORDER_STRONG`` outlines inputs and buttons and fills the
    scrollbar handle. At ``#4a505a`` it measured 1.96:1 against the
    window and read as almost no edge at all.
    """
    assert _contrast(_BORDER_STRONG, _BG_WINDOW) >= 3.0


def test_the_accent_carries_dark_text_not_light():
    """The gold is a light fill, so text on it must be the dark ink.

    Checked as a comparison rather than a fixed number: whichever of
    the two the theme chose, it has to be the one that contrasts.
    """
    assert _contrast(_BG_WINDOW, _ACCENT) > _contrast(_FG_PRIMARY, _ACCENT)
    assert _contrast(_BG_WINDOW, _ACCENT) >= 4.5


# ---------- the palette ----------


def _hex(palette: QPalette, role, group=QPalette.ColorGroup.Active) -> str:
    return palette.color(group, role).name().lower()


class TestDarkPalette:
    """``dark_palette()`` returns rather than applies.

    That is what lets these assertions run without touching the shared
    session QApplication, whose stylesheet other tests read.
    """

    def test_the_window_and_base_roles_are_the_theme_colors(self):
        palette = dark_palette()
        assert _hex(palette, QPalette.ColorRole.Window) == _BG_WINDOW
        assert _hex(palette, QPalette.ColorRole.Base) == _BG_INPUT

    def test_alternate_base_is_dark(self):
        """The near-white striped rows, pinned.

        Qt's default is ``#f7f7f7``; combined with
        ``setAlternatingRowColors(True)`` on the memory list, that drew
        a white band through every other row of a dark list.
        """
        palette = dark_palette()
        alternate = _hex(palette, QPalette.ColorRole.AlternateBase)
        assert alternate == _BG_PANEL
        assert _relative_luminance(alternate) < 0.1
        # And it must differ from Base, or the striping is pointless.
        assert alternate != _hex(palette, QPalette.ColorRole.Base)

    def test_placeholder_text_is_readable_on_the_input_fill(self):
        """It was black on ``#353a42`` -- 1.9:1, effectively invisible."""
        placeholder = _hex(dark_palette(), QPalette.ColorRole.PlaceholderText)
        assert _contrast(placeholder, _BG_INPUT) >= 4.5

    def test_the_selection_is_alfreds_gold_not_qts_blue(self):
        palette = dark_palette()
        assert _hex(palette, QPalette.ColorRole.Highlight) == _ACCENT
        # Dark ink on the gold, per the contrast test above.
        assert (
            _contrast(
                _hex(palette, QPalette.ColorRole.HighlightedText), _ACCENT
            )
            >= 4.5
        )

    def test_links_are_the_accent_not_the_default_blue(self):
        """And the visited variant is paler, not darker.

        It used to be ``_ACCENT_PRESSED``, the darker press-state gold,
        which measures 3.97:1 on the panel -- a link a user has already
        followed is the one they are most likely to want to re-read.
        """
        palette = dark_palette()
        for role in (QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited):
            assert _hex(palette, role) != "#0000ff"
            for ground in (_BG_WINDOW, _BG_PANEL, _BG_INPUT):
                assert _contrast(_hex(palette, role), ground) >= 4.5
        # Visited still has to be *distinguishable* from unvisited, or
        # the state is carried by nothing at all.
        assert _hex(palette, QPalette.ColorRole.LinkVisited) != _hex(
            palette, QPalette.ColorRole.Link
        )

    def test_tooltips_are_not_the_default_cream(self):
        palette = dark_palette()
        base = _hex(palette, QPalette.ColorRole.ToolTipBase)
        assert base != "#ffffdc"
        assert (
            _contrast(_hex(palette, QPalette.ColorRole.ToolTipText), base)
            >= 4.5
        )

    def test_every_text_role_is_light_in_the_inactive_group_too(self):
        """Inactive is not a lesser theme.

        Qt paints the *Inactive* group whenever the window loses focus.
        Leaving it at the default would make an unfocused Alfred flip
        half-light, which is the class of bug this whole file is about.
        """
        palette = dark_palette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
        ):
            for role in (
                QPalette.ColorRole.WindowText,
                QPalette.ColorRole.Text,
                QPalette.ColorRole.ButtonText,
            ):
                assert _relative_luminance(_hex(palette, role, group)) > 0.3, (
                    f"{role} in {group} is too dark to read"
                )

    def test_the_disabled_group_is_dimmed_on_purpose(self):
        palette = dark_palette()
        disabled = _hex(
            palette, QPalette.ColorRole.Text, QPalette.ColorGroup.Disabled
        )
        assert disabled == _FG_DISABLED
        assert _relative_luminance(disabled) < _relative_luminance(
            _hex(palette, QPalette.ColorRole.Text)
        )


# ---------- apply_theme sets both halves ----------


def test_apply_theme_sets_the_palette_as_well_as_the_stylesheet(qapp):
    """The actual fix, asserted end to end.

    ``tests/test_settings.py`` already pins that the stylesheet is
    applied; what was missing was the palette beside it, so the two are
    checked together here.
    """
    theme.apply_theme()
    assert qapp.styleSheet() == DARK_QSS
    palette = qapp.palette()
    assert _hex(palette, QPalette.ColorRole.Window) == _BG_WINDOW
    assert _hex(palette, QPalette.ColorRole.AlternateBase) == _BG_PANEL


def test_the_stylesheet_names_an_alternate_row_color(qapp):
    """Belt to the palette's braces.

    A *styled* QListWidget takes its alternate row color from the
    stylesheet when one is given and ignores the palette, so the
    palette fix alone would not have covered every Qt version. Pinned
    as a substring; asserting the whole rule would break on a reformat.
    """
    assert "alternate-background-color" in DARK_QSS
