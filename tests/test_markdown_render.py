"""Tests for the markdown → HTML preview transform."""
from __future__ import annotations

import ui.markdown_render as mr


# ---------- headings ----------


def test_h1_renders_as_h1_tag():
    html = mr.render_to_html("# Title")
    assert "<h1>" in html
    assert "Title" in html


def test_h2_renders_as_h2_tag():
    html = mr.render_to_html("## Subtitle")
    assert "<h2>" in html
    assert "Subtitle" in html


def test_h3_renders_as_h3_tag():
    html = mr.render_to_html("### Subsub")
    assert "<h3>" in html
    assert "Subsub" in html


# ---------- inline emphasis ----------


def test_bold_renders_as_strong():
    html = mr.render_to_html("Some **bold** word")
    # python-markdown emits <strong>; we don't care if a future version
    # emits <b> instead, so the test accepts either — the contract is
    # "bold is visually strong", not "the tag is exactly this one".
    assert ("<strong>" in html) or ("<b>" in html)
    assert "bold" in html


def test_italic_renders_as_em():
    html = mr.render_to_html("an *italic* word")
    assert ("<em>" in html) or ("<i>" in html)
    assert "italic" in html


# ---------- fenced code ----------


def test_fenced_code_renders_pre_code():
    html = mr.render_to_html("```\nfoo bar\n```")
    assert "<pre>" in html
    # ``<code>`` may carry a ``class="language-..."`` attribute when a
    # language is specified, so we check for the tag opening rather
    # than the bare form.
    assert "<code" in html
    # The code content should be in the output, not lost.
    assert "foo bar" in html


def test_fenced_code_with_language_tag():
    """A language tag after the opening fence must not crash and must
    still produce a code block (and end up on the <code> tag)."""
    html = mr.render_to_html("```python\nprint('hi')\n```")
    assert "<pre>" in html
    assert "<code" in html
    assert "language-python" in html
    assert "print" in html


# ---------- checklists ----------


def test_checkbox_unchecked_and_checked_render_differently():
    """Per the spec, [ ] and [x] must produce visually distinct output.

    The preprocess step substitutes unicode ballot boxes (☐ vs ☑) into
    the list-item text, so the rendered HTML differs by glyph.
    """
    unchecked = mr.render_to_html("- [ ] todo")
    checked = mr.render_to_html("- [x] done")
    assert unchecked != checked
    # And the difference is exactly the ballot-box glyph, which is
    # the whole point of the preprocess step.
    assert "☐" in unchecked  # ☐
    assert "☑" in checked    # ☑
    assert "☐" not in checked
    assert "☑" not in unchecked


def test_checkbox_checked_accepts_capital_x():
    """GitHub accepts both [x] and [X] as 'done'."""
    lower = mr.render_to_html("- [x] done")
    upper = mr.render_to_html("- [X] done")
    assert lower == upper


def test_checkbox_inside_a_list():
    """Multiple checklist lines render as a list with both states."""
    html = mr.render_to_html("- [ ] one\n- [x] two\n- [ ] three")
    assert "<ul>" in html
    assert "<li>" in html
    assert "☐" in html  # at least one unchecked
    assert "☑" in html  # at least one checked


def test_checkbox_with_indentation():
    """Leading whitespace (nested list) still gets handled."""
    html = mr.render_to_html("  - [ ] indented")
    assert "☐" in html
    # And it still parses as a list item.
    assert "<li>" in html


# ---------- links ----------


def test_inline_link_renders_with_href():
    html = mr.render_to_html("[example](https://example.com)")
    assert "<a " in html
    assert 'href="https://example.com"' in html
    assert "example" in html


def test_plain_url_is_linkified():
    """Markdown's autolink-ish behavior: a bare URL becomes a link."""
    html = mr.render_to_html("Visit https://example.com today")
    # The bare URL must end up inside an <a> tag.
    assert "https://example.com" in html
    assert "<a " in html


# ---------- lists ----------


def test_bullet_list_renders_as_ul():
    html = mr.render_to_html("- one\n- two\n- three")
    assert "<ul>" in html
    assert "<li>" in html
    # Each item present in some form.
    for word in ("one", "two", "three"):
        assert word in html


def test_numbered_list_renders_as_ol():
    html = mr.render_to_html("1. one\n2. two\n3. three")
    assert "<ol>" in html
    assert "<li>" in html


# ---------- tables ----------


def test_pipe_table_renders_as_table():
    html = mr.render_to_html(
        "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    )
    assert "<table>" in html
    assert "<th" in html or "<td" in html


# ---------- plain text ----------


def test_plain_text_does_not_crash():
    """The most important test: existing plain-text memories (no
    markdown syntax) preview as plain paragraphs, nothing broken."""
    html = mr.render_to_html("just plain text, nothing fancy")
    assert "just plain text" in html
    # Wrapped in a <p> by the markdown library.
    assert "<p>" in html


def test_empty_input_does_not_crash():
    """An empty content field shouldn't blow up the preview."""
    html = mr.render_to_html("")
    # Just an empty string or a trivial paragraph — anything but a crash.
    assert isinstance(html, str)


def test_text_with_only_whitespace_does_not_crash():
    html = mr.render_to_html("   \n\n   \n")
    assert isinstance(html, str)


def test_realistic_memory_renders_all_features():
    """A memory that mixes every supported feature renders without
    crashing and includes each feature's marker in the output."""
    md = (
        "# Project notes\n"
        "\n"
        "## Goals\n"
        "- [ ] ship alpha\n"
        "- [x] write spec\n"
        "\n"
        "We need to be **bold** and *careful*.\n"
        "\n"
        "```python\n"
        "def hello():\n"
        "    print('hi')\n"
        "```\n"
        "\n"
        "See [the spec](https://example.com/spec) for details.\n"
    )
    html = mr.render_to_html(md)
    assert "<h1>" in html
    assert "<h2>" in html
    assert "☐" in html
    assert "☑" in html
    assert "<strong>" in html or "<b>" in html
    assert "<em>" in html or "<i>" in html
    assert "<pre>" in html
    assert "<code" in html
    assert "<a " in html
    assert 'href="https://example.com/spec"' in html
