"""Markdown -> HTML rendering for the editor's preview pane.

This is a pure-function module: no Qt, no globals, no state. That
makes it trivial to unit-test (``render_to_html`` can be called with
arbitrary input and asserted on without instantiating a QApplication).

The ``markdown`` library is stateful (a ``Markdown`` instance
accumulates state between ``convert()`` calls), so we instantiate a
fresh one per call. The cost is negligible -- converting a memory's
worth of text is microseconds -- and the isolation is worth it.
"""
from __future__ import annotations

import re

import markdown


# GitHub-style task list. The ``markdown`` library doesn't ship support
# for `- [ ]` / `- [x]` out of the box, and the alternatives (a third-
# party extension) are heavier than this single-line substitution.
# We replace the marker with a unicode ballot box -- visually distinct
# (checked vs. unchecked), and ``sane_lists`` then renders the line as
# a normal list item without further fuss.
#
# Match groups: (leading whitespace, state, item text).
# State is one of: ' ' (unchecked), 'x', 'X' (both accepted as "done").
_CHECKBOX_RE = re.compile(
    r"^(\s*)-\s+\[([ xX])\]\s+(.*)$",
    re.MULTILINE,
)

# Rendered glyphs for the two states. BALLOT BOX ([ ]) and BALLOT BOX
# WITH CHECK ([x]) are widely-supported in modern UI fonts.
_UNCHECKED = "☐"  # [ ]
_CHECKED = "☑"    # [x]

# Plain-URL autolinker. The base markdown library does not autolink
# bare URLs (it requires `<https://...>` or `[text](url)` syntax), but
# Alfred should treat plain URLs as links too, so we
# pre-substitute them into the angle-bracket autolink form, which the
# library does recognize.
#
# Negative lookbehind: don't re-wrap a URL that's already inside a
# markdown link `[...](URL)` -- those have the literal `](` immediately
# before, and re-wrapping would break them. Likewise, don't touch
# angle-bracket autolinks that are already done.
_URL_RE = re.compile(
    r"(?<!]\()(?<!<)(https?://[^\s<>)]+)",
)


def _autolink_urls(text: str) -> str:
    """Wrap bare ``http(s)://...`` URLs in angle brackets so markdown
    parses them as autolinks. Already-wrapped URLs and URLs already
    inside markdown link syntax are left alone.
    """
    return _URL_RE.sub(r"<\1>", text)


def _preprocess(text: str) -> str:
    """Swap `- [ ]` / `- [x]` markers for ballot-box glyphs.

    Runs before the markdown parser, so the result looks like an
    ordinary list to the parser -- which is exactly what we want. No
    custom extension needed.
    """

    def _sub(match: re.Match[str]) -> str:
        indent, state, rest = match.group(1), match.group(2), match.group(3)
        glyph = _CHECKED if state in ("x", "X") else _UNCHECKED
        return f"{indent}- {glyph} {rest}"

    return _CHECKBOX_RE.sub(_sub, text)


def render_to_html(text: str) -> str:
    """Convert markdown ``text`` to an HTML fragment for ``QTextBrowser``.

    The extensions enabled here are deliberately conservative:
      * ``fenced_code``  -- triple-backtick code blocks (the most common
                           form; matches GitHub)
      * ``tables``       -- pipe tables (rare but cheap to support)
      * ``sane_lists``   -- fixes the parser's list-with-other-content
                           quirks so a list item followed by a paragraph
                           doesn't accidentally merge them

    No extensions are enabled that would change semantics of plain text
    (no smartypants, no footnotes, no attr_list) -- those either mangle
    user content or pull in features Alfred doesn't expose.
    """
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "sane_lists"],
    )
    preprocessed = _autolink_urls(_preprocess(text))
    return md.convert(preprocessed)
