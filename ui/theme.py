"""Theme application for Alfred.

Alfred is dark-mode only. The single :func:`apply_theme`
function applies the bundled dark palette to the running
QApplication; there is no longer a "light" alternative to
switch to. A future re-introduction of theme support would
replace this no-arg function with a parameterized one -- for
now, the no-arg form encodes the "dark-only" decision at
the call site, so a developer who wants to add a light
theme has to actively choose to extend the API.

Design
------
The theme is more than a "make Qt dark" override; it is
built to read as a deliberately designed dark desktop
app:

* **Three-tier background palette** -- ``#1f2227`` (window
  background, deepest), ``#2a2e35`` (panels and cards,
  middle), ``#353a42`` (inputs and list rows, lightest).
  Layers visibly recede rather than being a flat
  uniform dark.
* **One accent color** -- a warm butler gold
  (``#c9a14a``) used consistently for selection, focus
  rings, and primary actions. The gold matches the
  pocket-watch accent on the mascot sprite, so the
  palette and the character share a single visual cue.
* **Rounded corners on chrome** -- 6px on buttons / inputs
  / panels / cards. Not bubbly; reads as deliberate
  craft rather than skeuomorphic.
* **Filled selected state on the sidebar** -- the active
  view gets a rounded gold-on-dark background rather
  than a thin highlight bar.
* **Larger default font** -- the base body size is 10pt
  (up from Qt's 8pt default on Windows). Section titles
  in the sidebar and editor labels get an explicit
  weight bump.

Adding a new widget family in the future means adding a
selector here; a Qt stylesheet that targets only what we
use is easier to reason about than a generic
dark-everything override.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


# --- Palette tokens -----------------------------------------------------
# Documented in one place so future tweaks don't have to
# reconcile eight slightly-different greys that someone
# typed in by hand. The values are referenced by name in
# DARK_QSS below.

# Backgrounds (deepest to lightest).
_BG_WINDOW = "#1f2227"     # the QMainWindow / QDialog base
_BG_PANEL = "#2a2e35"      # sidebar, middle pane, frames
_BG_INPUT = "#353a42"      # line edits, list rows, dropdowns
_BG_RAISED = "#3a4049"     # hovered list rows, popped menus

# Accent (butler gold). Matches the mascot's pocket-watch
# accent so the app and the character feel like one product.
_ACCENT = "#c9a14a"
_ACCENT_HOVER = "#d6b261"
_ACCENT_PRESSED = "#a8862f"
_ACCENT_TEXT = "#1f2227"   # text on top of the accent fill

# Text (foreground variants).
_FG_PRIMARY = "#e6e6e6"
_FG_SECONDARY = "#a8adb4"
# ``_FG_MUTED`` was #6f757f, which measures 2.94:1 against the
# panel it is drawn on -- below the 4.5:1 WCAG 1.4.3 floor for
# body text, and it is real body text (the "You asked: ..." echo
# in the capture bubble). #8e959e measures 4.51:1 on the panel
# and 5.27:1 on the window while staying visibly quieter than
# _FG_SECONDARY, so the three-tier hierarchy survives the fix.
_FG_MUTED = "#8e959e"
# Placeholder text is a fourth tier, and it exists because it is
# the one quiet foreground drawn on the *input* fill rather than on
# a panel. _FG_MUTED measures only 3.78:1 there -- the ground is
# lighter than the panel, so the same ink that passes on #2a2e35
# fails on #353a42. #9ea5ae measures 4.60:1 on the input fill and
# is still a step below _FG_SECONDARY (5.07:1 on the same ground),
# so "Search your files" reads as a prompt rather than as content.
_FG_PLACEHOLDER = "#9ea5ae"
# Disabled text is explicitly exempt from the contrast floor
# (1.4.3 excludes inactive components), and looking inactive is
# the entire job, so this one stays low on purpose.
_FG_DISABLED = "#5a6068"

# Borders / dividers.
# Dividers are decorative -- no contrast floor applies.
_BORDER = "#3a4049"
# Input and button outlines are UI components, which WCAG 1.4.11
# holds to 3:1. #4a505a managed 1.96:1 against the window and
# read as almost no edge at all; #646c78 measures 3.01:1. It is
# also the scrollbar handle fill, which was similarly faint.
_BORDER_STRONG = "#646c78"

# Selection (the filled rounded background on the active
# sidebar item, the highlight on selected list rows).
_SELECTION = "#c9a14a"
_SELECTION_TEXT = "#1f2227"

# Links, in rendered markdown. Visited was _ACCENT_PRESSED, the
# darker press-state gold, which measures 3.97:1 on the panel --
# under the text floor. A visited link should read as *spent*
# rather than as dimmer, so it goes the other way: paler and
# desaturated, 6.77:1 on the panel, still clearly not the live
# accent next to it.
_LINK = "#c9a14a"
_LINK_VISITED = "#cbb489"

# --- Type scale ---------------------------------------------------------
# One modular scale, in points, so a size is chosen from a list
# rather than typed into a widget. Every hardcoded
# ``font.setPointSize(...)`` in the view modules was replaced by
# an objectName that maps to one of these; adding a size means
# adding a step here and a selector below, which is deliberately
# more friction than reaching for a magic number.
#
# 9 / 10 / 11 / 13 / 15 / 20 -- roughly a 1.2 ratio off a 10pt
# body, rounded to sizes Qt renders cleanly on Windows.
_FS_MICRO = 9      # timestamps, the "You asked" echo
_FS_BODY = 10      # everything unmarked
_FS_STRONG = 11    # section headers, list group rows
_FS_TITLE = 13     # panel / view titles
_FS_STAT = 15      # dashboard stat numerals
_FS_DISPLAY = 20   # the dashboard greeting, the one display line

# Focus ring. Kept as its own token rather than reusing _ACCENT
# directly: the ring is the one visual element that must never be
# tuned away for aesthetic reasons, so it is named for its job.
_FOCUS = "#e0bd6b"


DARK_QSS = f"""
/* --- base typography ------------------------------------------------ */

QWidget {{
    background-color: {_BG_WINDOW};
    color: {_FG_PRIMARY};
    font-size: {_FS_BODY}pt;
}}

/* Headings and labels get a weight bump so titles
   read as titles rather than as larger body text. The
   objectName-based selector means widgets that want to
   be a "title" can opt in by setting objectName
   ("titleLabel", etc.) — explicit beats implicit. */
QLabel[objectName="titleLabel"] {{
    font-size: {_FS_TITLE}pt;
    font-weight: 600;
    color: {_FG_PRIMARY};
    padding: 2px 0;
}}

QLabel[objectName="sectionHeader"] {{
    font-size: {_FS_STRONG}pt;
    font-weight: 600;
    color: {_FG_PRIMARY};
    padding: 4px 2px;
    letter-spacing: 0.5px;
}}

QLabel[objectName="muted"] {{
    color: {_FG_SECONDARY};
}}

/* The display line -- the dashboard greeting, and nothing else
   so far. One line per screen at most: a second thing this big
   would mean neither is the headline. */
QLabel[objectName="displayLabel"] {{
    font-size: {_FS_DISPLAY}pt;
    font-weight: 600;
    color: {_FG_PRIMARY};
}}

/* The quiet line under a display heading. */
QLabel[objectName="subheadLabel"] {{
    font-size: {_FS_BODY}pt;
    color: {_FG_SECONDARY};
}}

/* A numeral that is the point of its card (the dashboard
   counts). Accent-colored: the number is the data, the label
   under it is just the legend. */
QLabel[objectName="statValue"] {{
    font-size: {_FS_STAT}pt;
    font-weight: 600;
    color: {_ACCENT};
}}

QLabel[objectName="statLabel"] {{
    font-size: {_FS_MICRO}pt;
    color: {_FG_SECONDARY};
    letter-spacing: 0.4px;
}}

/* Smallest step -- timestamps and the capture bubble's echo. */
QLabel[objectName="microLabel"] {{
    font-size: {_FS_MICRO}pt;
    color: {_FG_MUTED};
}}

/* The capture popup's one input. It is the primary surface of the
   whole app -- most sessions never open the main window -- so it
   sits a step above body text. */
QLineEdit[objectName="captureInput"] {{
    font-size: {_FS_TITLE}pt;
    padding: 8px 10px;
}}

/* --- main window / dialogs / generic panels ------------------------- */

QMainWindow, QDialog {{
    background-color: {_BG_WINDOW};
}}

QFrame, QWidget#centralWidget {{
    background-color: transparent;
}}

/* Card-style frames (used by the dashboard sections and the
   answer panel in the capture popup) opt in to a filled
   rounded panel. */
QFrame[objectName="card"] {{
    background-color: {_BG_PANEL};
    border: 1px solid {_BORDER};
    border-radius: 8px;
    padding: 12px;
}}

/* --- inputs --------------------------------------------------------- */

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
QComboBox, QDateEdit, QDateTimeEdit {{
    background-color: {_BG_INPUT};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {_ACCENT};
    selection-color: {_ACCENT_TEXT};
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QDateEdit:focus, QDateTimeEdit:focus {{
    border: 1px solid {_ACCENT};
}}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: {_FG_DISABLED};
    background-color: {_BG_PANEL};
}}

/* --- buttons -------------------------------------------------------- */

QPushButton {{
    background-color: {_BG_INPUT};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER_STRONG};
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 500;
    min-height: 18px;
}}

QPushButton:hover {{
    background-color: {_BG_RAISED};
    border-color: {_ACCENT};
}}

QPushButton:pressed {{
    background-color: {_ACCENT_PRESSED};
    color: {_ACCENT_TEXT};
    border-color: {_ACCENT_PRESSED};
}}

QPushButton:disabled {{
    color: {_FG_DISABLED};
    background-color: {_BG_PANEL};
    border-color: {_BORDER};
}}

/* Keyboard focus, on every button including the primary one.
   The app had no button focus style at all, which meant a
   keyboard user tabbing through the toolbar could not tell
   where they were -- the one accessibility gap worth calling
   critical, because it makes the UI unoperable rather than
   merely unattractive. The ring is a 2px accent outline drawn
   inside the border, so it does not shift the layout, and it
   measures 5.8:1 against every background token (1.4.11 asks
   for 3:1). ``:focus`` is used rather than ``:focus-visible``
   (which Qt's QSS subset does not implement) -- a mouse click
   therefore also shows the ring, which is a smaller cost than
   an invisible focus. */
QPushButton:focus {{
    border: 2px solid {_FOCUS};
    padding: 6px 13px;
}}

/* "Primary" action — opt in via objectName="primaryButton"
   (used for the Save button, etc.). The accent fill
   signals "this is the one button that does the thing". */
QPushButton[objectName="primaryButton"] {{
    background-color: {_ACCENT};
    color: {_ACCENT_TEXT};
    border: 1px solid {_ACCENT};
    font-weight: 600;
}}

QPushButton[objectName="primaryButton"]:hover {{
    background-color: {_ACCENT_HOVER};
    border-color: {_ACCENT_HOVER};
}}

QPushButton[objectName="primaryButton"]:pressed {{
    background-color: {_ACCENT_PRESSED};
    border-color: {_ACCENT_PRESSED};
}}

/* --- lists / trees / tables ----------------------------------------- */

QListWidget, QTreeWidget, QTreeView, QTableWidget {{
    background-color: {_BG_INPUT};
    color: {_FG_PRIMARY};
    /* ``MemoryListWidget`` turns on alternating row colors, and
       with no value here Qt fell back to the *palette's*
       AlternateBase -- which, on the light default palette this
       app used to run under, is #f7f7f7. Alternate rows were
       drawing near-white inside a dark list. The palette is now
       set properly in apply_theme() as well; this states the
       value where the rest of the list styling lives so the two
       cannot drift apart. */
    alternate-background-color: {_BG_PANEL};
    border: 1px solid {_BORDER_STRONG};
    border-radius: 6px;
    padding: 2px;
    selection-background-color: {_ACCENT};
    selection-color: {_ACCENT_TEXT};
    outline: 0;
}}

QListWidget::item, QTreeWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
}}

QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {_BG_RAISED};
}}

QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {_ACCENT};
    color: {_ACCENT_TEXT};
}}

/* The row the keyboard is on, when it is not also the selected
   row (arrowing with Ctrl held, or a focused list whose
   selection was cleared). Selection is a fill; focus is an
   outline; the two read differently on purpose, so the state is
   never carried by color alone. */
QListWidget::item:focus, QTreeWidget::item:focus {{
    border: 1px solid {_FOCUS};
}}

/* The lists themselves get the ring too -- a list is a tab stop
   before any row inside it is. */
QListWidget:focus, QTreeWidget:focus, QTreeView:focus,
QTableWidget:focus {{
    border: 1px solid {_FOCUS};
}}

/* The dashboard's due-today list lives inside a card frame, so it
   drops the panel fill and border the rule above gives every other
   list — otherwise it draws a second box inside the card's. */
QListWidget#dashboardTaskList {{
    background-color: transparent;
    border: none;
    padding: 0;
}}

QHeaderView::section {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    border: none;
    border-right: 1px solid {_BORDER};
    border-bottom: 1px solid {_BORDER};
    padding: 6px 8px;
    font-weight: 600;
}}

/* --- sidebar --------------------------------------------------------- */

QListWidget#sidebarList {{
    background-color: {_BG_WINDOW};
    border: none;
    padding: 6px 4px;
}}

QListWidget#sidebarList::item {{
    padding: 8px 12px;
    border-radius: 6px;
    margin: 2px 4px;
    color: {_FG_SECONDARY};
}}

QListWidget#sidebarList::item:hover {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
}}

QListWidget#sidebarList::item:selected {{
    background-color: {_ACCENT};
    color: {_ACCENT_TEXT};
    font-weight: 600;
}}

/* The sidebar's own id selector sets ``border: none``, which
   outranks the generic ``QListWidget:focus`` rule above (an id
   beats a pseudo-class in Qt's CSS2 specificity), so the
   keyboard ring has to be restated here or the sidebar -- the
   first tab stop in the window -- would be the one list with no
   visible focus. Drawn on the row rather than the frame: a
   full-height outline around a borderless rail looks like a
   mistake. */
QListWidget#sidebarList::item:focus {{
    border: 1px solid {_FOCUS};
}}

/* Alternating rows are meaningless on a six-item nav rail, and
   the generic list rule would otherwise stripe it. */
QListWidget#sidebarList {{
    alternate-background-color: {_BG_WINDOW};
}}

/* --- status / menu --------------------------------------------------- */

QStatusBar {{
    background-color: {_BG_PANEL};
    color: {_FG_SECONDARY};
    border-top: 1px solid {_BORDER};
}}

QMenuBar {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    padding: 2px 4px;
    border-bottom: 1px solid {_BORDER};
}}

QMenuBar::item:selected {{
    background-color: {_BG_INPUT};
    border-radius: 4px;
}}

QMenu {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER_STRONG};
    border-radius: 6px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 18px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {_ACCENT};
    color: {_ACCENT_TEXT};
}}

QMenu::separator {{
    height: 1px;
    background: {_BORDER};
    margin: 4px 8px;
}}

/* --- group / tab / splitter ----------------------------------------- */

QGroupBox {{
    border: 1px solid {_BORDER_STRONG};
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px;
    color: {_FG_PRIMARY};
    font-weight: 500;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: {_FG_SECONDARY};
}}

QCheckBox, QRadioButton {{
    color: {_FG_PRIMARY};
    spacing: 8px;
    padding: 2px 0;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid {_BORDER_STRONG};
    background: {_BG_INPUT};
}}

QRadioButton::indicator {{
    border-radius: 7px;
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {_ACCENT};
    border-color: {_ACCENT};
}}

/* A checked box is filled *and* its border changes, so the state
   never rests on hue alone (some checked boxes here sit next to
   destructive actions -- the "I understand this will overwrite"
   confirmation on the Backups page is one). */
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {_ACCENT};
}}

/* Sub-control first, pseudo-state second. Written the other way
   round (``QCheckBox:focus::indicator``) Qt applies the border to
   the whole widget instead of the box, and unconditionally -- every
   checkbox in the app was drawn inside a gold rectangle whether it
   had focus or not. */
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{
    border: 2px solid {_FOCUS};
}}

/* A *checked* box is filled with the accent, and a gold ring on a
   gold fill is no ring at all -- keyboard focus vanished on exactly
   the boxes most worth confirming (the "I understand this will
   overwrite all my current data" gate on the Backups page is a
   checked box the user is about to act on).
   The first attempt used _ACCENT_TEXT, the ink that sits on the
   accent everywhere else. It measures 6.6:1 against the fill and was
   still invisible, because that ink is _BG_WINDOW: the ring has to
   contrast with what surrounds the box as well as with what it
   encloses, and a #1f2227 ring on a #1f2227 ground just made the
   gold square 2px smaller. The lightest ink in the palette clears
   both -- 11.6:1 on the ground, 2.0:1 on the fill. */
QCheckBox::indicator:checked:focus,
QRadioButton::indicator:checked:focus {{
    border: 2px solid {_FG_PRIMARY};
}}

QTabWidget::pane {{
    border: 1px solid {_BORDER_STRONG};
    border-radius: 6px;
    top: -1px;
}}

QTabBar::tab {{
    background-color: {_BG_PANEL};
    color: {_FG_SECONDARY};
    padding: 7px 16px;
    border: 1px solid {_BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}}

QTabBar::tab:hover {{
    color: {_FG_PRIMARY};
}}

QTabBar::tab:selected {{
    background-color: {_BG_WINDOW};
    color: {_ACCENT};
    border-bottom: 2px solid {_ACCENT};
    font-weight: 600;
}}

/* The handle was 4px of the same grey as the panel beside it --
   draggable, but with nothing to say so. It is now a 6px lane
   in the window's own (darker) ground, so it reads as a seam
   between two panels rather than as part of one, and it lights
   up on hover. */
QSplitter::handle {{
    background-color: {_BG_WINDOW};
}}

QSplitter::handle:hover {{
    background-color: {_ACCENT};
}}

QSplitter::handle:pressed {{
    background-color: {_ACCENT_PRESSED};
}}

QSplitter::handle:horizontal {{
    width: 6px;
    margin: 0 1px;
}}

QSplitter::handle:vertical {{
    height: 6px;
    margin: 1px 0;
}}

/* --- scrollbars ----------------------------------------------------- */

QScrollBar:vertical {{
    background: {_BG_PANEL};
    width: 12px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {_BORDER_STRONG};
    min-height: 24px;
    border-radius: 6px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {_ACCENT};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}

QScrollBar:horizontal {{
    background: {_BG_PANEL};
    height: 12px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: {_BORDER_STRONG};
    min-width: 24px;
    border-radius: 6px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {_ACCENT};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

/* --- tooltips -------------------------------------------------------- */

QToolTip {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER_STRONG};
    border-radius: 4px;
    padding: 4px 6px;
}}

/* --- capture popup speech bubble ------------------------------------- */
/* The popup itself goes transparent so the rounded bubble inside it is
   what the user sees floating next to the companion. The dialog keeps
   its own background off via the id selector, which outranks the
   generic QDialog rule above. */

QDialog[objectName="capturePopup"] {{
    background: transparent;
}}

QFrame[objectName="bubbleFrame"] {{
    background-color: {_BG_PANEL};
    border: 1px solid {_BORDER_STRONG};
    border-radius: 14px;
}}

/* Alfred's reply — the one line the user actually reads. */
QLabel[objectName="assistantSays"] {{
    color: {_FG_PRIMARY};
    font-size: {_FS_STRONG}pt;
    padding: 2px 2px 2px 2px;
}}

/* The quiet "You asked: …" echo above it. */
QLabel[objectName="askedEcho"] {{
    color: {_FG_MUTED};
    font-size: {_FS_MICRO}pt;
}}
"""


def dark_palette() -> QPalette:
    """Build the dark :class:`QPalette` that matches ``DARK_QSS``.

    A stylesheet is not a palette. Qt paints a great deal
    through palette roles that no QSS selector reaches, and
    for a long time this app set only the stylesheet -- so it
    ran on Qt's *light* default palette and had the seams to
    show for it:

    * ``AlternateBase`` was ``#f7f7f7``, and
      :class:`ui.memory_list.MemoryListWidget` turns alternating
      row colors on. Every other row of the memory list drew
      near-white inside a dark list.
    * ``PlaceholderText`` was black, so "Search your files" and
      the capture popup's prompt were black-on-``#353a42``:
      about 1.6:1, effectively invisible.
    * ``Highlight`` was Windows blue ``#308cc6``, which leaked
      into every widget the QSS selectors don't cover, next to
      the gold the ones they do cover use.
    * ``Link`` was pure ``#0000ff`` on a dark ground, and
      ``ToolTipBase`` was the old cream ``#ffffdc``.

    Returned rather than applied so a test can assert the roles
    without a live QApplication, and so a future non-global
    consumer (a preview widget, say) can set it on one widget.
    """
    pal = QPalette()

    def _set(role: QPalette.ColorRole, color: str) -> None:
        # Set the role on all three groups, then override the
        # disabled group below. Qt falls back to Active for a
        # group that was never set, but only per-role -- being
        # explicit is what keeps a disabled dialog from
        # inheriting a stray light default.
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pal.setColor(group, role, QColor(color))

    role = QPalette.ColorRole
    # Surfaces. Window is the deepest tier, Base the input/list
    # tier, AlternateBase the panel tier -- one step lighter than
    # Window so a striped list reads as texture, not as damage.
    _set(role.Window, _BG_WINDOW)
    _set(role.Base, _BG_INPUT)
    _set(role.AlternateBase, _BG_PANEL)
    _set(role.Button, _BG_INPUT)
    _set(role.ToolTipBase, _BG_PANEL)

    # Foregrounds.
    _set(role.WindowText, _FG_PRIMARY)
    _set(role.Text, _FG_PRIMARY)
    _set(role.ButtonText, _FG_PRIMARY)
    _set(role.ToolTipText, _FG_PRIMARY)
    _set(role.BrightText, "#ffffff")
    _set(role.PlaceholderText, _FG_PLACEHOLDER)

    # Selection. The same gold the stylesheet uses, so a widget
    # Qt paints from the palette and a widget the QSS reaches
    # cannot end up two different colors.
    _set(role.Highlight, _SELECTION)
    _set(role.HighlightedText, _SELECTION_TEXT)

    # Links pick up the accent rather than browser blue.
    _set(role.Link, _LINK)
    _set(role.LinkVisited, _LINK_VISITED)

    # 3D framing. Qt still draws bevels with these in a few
    # places (spin box buttons, some frames); pointing them at
    # the border tokens keeps those from flashing light grey.
    _set(role.Light, _BORDER_STRONG)
    _set(role.Midlight, _BORDER)
    _set(role.Mid, _BORDER)
    _set(role.Dark, _BG_WINDOW)
    _set(role.Shadow, "#15181c")

    # The disabled group, set last so it wins over the loop above.
    for disabled_role in (
        role.WindowText,
        role.Text,
        role.ButtonText,
        role.PlaceholderText,
    ):
        pal.setColor(
            QPalette.ColorGroup.Disabled, disabled_role, QColor(_FG_DISABLED)
        )
    pal.setColor(
        QPalette.ColorGroup.Disabled, role.Base, QColor(_BG_PANEL)
    )
    pal.setColor(
        QPalette.ColorGroup.Disabled, role.Button, QColor(_BG_PANEL)
    )
    # A disabled selection still has to be legible -- it is often
    # the row a user left selected before focus moved elsewhere.
    pal.setColor(
        QPalette.ColorGroup.Disabled, role.Highlight, QColor(_BG_RAISED)
    )
    pal.setColor(
        QPalette.ColorGroup.Disabled,
        role.HighlightedText,
        QColor(_FG_SECONDARY),
    )
    return pal


def apply_theme() -> None:
    """Apply the (dark) theme to the running QApplication.

    No-arg: there is only one theme, so the caller has
    nothing to choose between. Calling this on a
    QApplication that has no instance yet is a no-op --
    that can happen during very early test setup; the
    actual app always creates the QApplication before
    any UI code runs.

    Also sets a default application font that matches the
    stylesheet's 10pt body. Qt's Windows default is 8pt,
    which feels cramped inside the new padding values;
    bumping the default up keeps the visual rhythm
    consistent.

    Applies :func:`dark_palette` as well as the stylesheet.
    The palette goes on first, so the stylesheet is layered
    over a dark base rather than over Qt's light default --
    see that function's docstring for what was showing
    through the gaps.
    """
    app = QApplication.instance()
    if app is None:
        return
    app.setPalette(dark_palette())
    app.setStyleSheet(DARK_QSS)
    # A slightly larger default application font keeps the
    # stylesheet's 10pt from being overridden by Qt's
    # platform default (8pt on Windows). Setting the font
    # before setStyleSheet() means the stylesheet can still
    # override per-widget (e.g. our titleLabel bumps to
    # 13pt); this just gives us a sane baseline.
    base_font: QFont = app.font()
    if base_font.pointSize() < 10:
        base_font.setPointSize(10)
    app.setFont(base_font)
