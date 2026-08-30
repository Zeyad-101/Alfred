"""Theme application for Alfred.

Alfred is dark-mode only. The single :func:`apply_theme`
function applies the bundled dark palette to the running
QApplication; there is no longer a "light" alternative to
switch to. A future re-introduction of theme support would
replace this no-arg function with a parameterized one -- for
now, the no-arg form encodes the "dark-only" decision at the
call site, so a developer who wants to add a light theme has
to actively choose to extend the API.

Design
------
The theme is a *token system*, not a pile of colours. Every
value a surface can use is named once below and referenced by
name from the stylesheet; a rule that wants a colour, a size,
a gap or a radius picks one off the list rather than typing a
number. That is the whole discipline, and it is what makes
"is this consistent?" a question you can answer by reading
rather than by squinting.

* **Three warm background tiers** -- ``#14120f`` (the window
  ground, deepest), ``#1c1915`` (panels and cards, middle),
  ``#252019`` (inputs and list rows, lightest). Every tier is
  a warm near-black rather than a blue-grey: the palette is
  lamplight and leather, which is the register the butler is
  in. Layers recede; nothing is flat.
* **One accent** -- butler gold (``#c9a24b``), matching the
  pocket-watch on the mascot sprite, used for selection,
  focus, links and the single primary action per surface.
  Restraint is the point: the gold means "this one", so it
  cannot also mean "hover", "border" and "heading".
* **One destructive colour** -- ``#b5544a``, and only on
  things that destroy data (empty trash, delete permanently,
  the Settings danger zone). It appears nowhere else, so it
  keeps its meaning.
* **A three-tier type scale** -- 15px semibold for section
  headers and dialog titles, 13px for body and list rows,
  11px for secondary and meta text, plus exactly one display
  step (22px gold) spent on the dashboard greeting.
* **An 8px spacing base** -- padding and gaps come from
  4 / 8 / 12 / 16 / 24. The 5s, 7s, 11s and 13s that used to
  be scattered through the sheet are gone.
* **Two radii** -- 6px on controls (buttons, inputs, rows),
  10px on containers (cards, menus, the speech bubble). Two
  steps read as a decision; five read as an accident.
* **Distinct hover *and* pressed on everything interactive**
  -- hover is one step of light (``_BG_RAISED``), pressed is
  one step *down* plus a gold edge, so the two never look
  like the same state. The selected sidebar row is a muted
  gold wash with a gold edge bar rather than a solid gold
  block: a nav rail should not hold the loudest element on
  the screen.

Adding a new widget family in the future means adding a
selector here; a Qt stylesheet that targets only what we use
is easier to reason about than a generic dark-everything
override.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


# --- Palette tokens -----------------------------------------------------
# The ten values below are the palette. Everything else here is either one
# of them, or a measured variant of one of them that exists because an
# accessibility floor demanded a step the ten do not contain -- and each
# of those says so, in place, with the number that forced it.

# Backgrounds (deepest to lightest).
_BG_WINDOW = "#14120f"     # bg-base:   the QMainWindow / QDialog ground
_BG_PANEL = "#1c1915"      # bg-panel:  sidebar, cards, menus, bubble
_BG_INPUT = "#252019"      # bg-input:  line edits, list rows, dropdowns

# The hover "lift". Deliberately the same value as _BORDER: one step of
# light above the input fill is all a hover needs, and reusing the step
# that already exists beats inventing an eleventh colour for it.
_BG_RAISED = "#332c22"

# Accent (butler gold), matching the mascot's pocket-watch so the app and
# the character read as one product.
_ACCENT = "#c9a24b"        # accent-gold
_ACCENT_HOVER = "#dbb562"  # accent-gold-hover
# The pressed step. Not in the ten: a filled gold button needs a state
# *below* its resting one, and reusing the hover token would make press
# and hover read as the same gesture. The accent at 84% value.
_ACCENT_PRESSED = "#a9843c"
_ACCENT_TEXT = "#14120f"   # ink on top of an accent fill == bg-base
# The accent at low opacity, for the one place a gold fill would be too
# loud: the selected nav row. rgba rather than a pre-mixed hex so it
# composites over whichever tier it lands on.
_ACCENT_WASH = "rgba(201, 162, 75, 0.16)"
_ACCENT_WASH_HOVER = "rgba(201, 162, 75, 0.24)"

# Text.
_FG_PRIMARY = "#ede7da"    # text-primary
_FG_SECONDARY = "#9c9382"  # text-secondary
# Two tiers the ten do not contain, both measured rather than picked.
# WCAG 1.4.3 asks 4.5:1 of body text and every size in Alfred's scale is
# below the large-text threshold, so the strict floor applies to all of
# them -- including the quiet ones, which are still real prose (the
# "You asked: ..." echo in the bubble, the editor timestamps).
# _FG_MUTED is the tier below secondary: 4.92:1 on the panel, still
# visibly quieter than secondary's 5.76:1, so the hierarchy survives.
_FG_MUTED = "#8f8776"
# Placeholder text is a fourth tier because it is the one quiet ink drawn
# on the *input* fill rather than on a panel. That ground is lighter, so
# _FG_MUTED would measure 4.54:1 there and read as content rather than as
# a prompt; this lands at 4.92:1 on the input, a step under secondary's
# 5.32:1 on the same ground.
_FG_PLACEHOLDER = "#968d7b"
# Disabled text is explicitly exempt from the contrast floor (1.4.3
# excludes inactive components) and looking inactive is the whole job, so
# this one stays low on purpose: 2.43:1 on the panel.
_FG_DISABLED = "#5c574b"   # text-disabled

# Borders.
# border-subtle: dividers, card edges, menu outlines -- decorative
# boundaries between surfaces that are already told apart by their fill,
# so no contrast floor applies to them.
_BORDER = "#332c22"
# Input and button outlines are UI *components*, which WCAG 1.4.11 holds
# to 3:1, and nothing in the ten clears it against the near-black ground:
# border-subtle measures 1.36:1 and text-disabled 2.60:1. This is the
# same warm neutral one step further up, at 3.38:1. It is the scrollbar
# handle fill too, which is a component as well.
_BORDER_STRONG = "#6e685a"

# Destructive, used only where data dies: empty trash, delete
# permanently, the Settings danger zone.
_DANGER = "#b5544a"        # danger
# The same red as *text* has to clear 4.5:1, and danger measures 3.61:1
# on the panel, so red prose (an inline validation error, the danger-zone
# note) uses this lighter tint of the identical hue: 5.86:1.
_DANGER_TEXT = "#d08076"
_DANGER_WASH = "rgba(181, 84, 74, 0.18)"

# Selection: list rows, and text selection inside inputs.
_SELECTION = "#c9a24b"
_SELECTION_TEXT = "#14120f"

# Links in rendered markdown. Visited goes paler rather than darker: the
# link a reader already followed is the one they are most likely to want
# again, so it reads as spent, not as dimmed. 8.79:1 on the preview fill.
_LINK = "#c9a24b"
_LINK_VISITED = "#cdbe9a"

# --- Type scale ---------------------------------------------------------
# Three tiers in px, plus one display step. Every font size in the view
# modules maps onto one of these through an objectName; adding a size
# means adding a step here *and* a selector below, which is deliberately
# more friction than reaching for a magic number.
_FS_META = 11      # timestamps, captions, notes, the "You asked" echo
_FS_BODY = 13      # everything unmarked: prose, list rows, buttons
_FS_HEADER = 15    # section headers, dialog and panel titles
# The one display step, spent on the one display line: the dashboard
# greeting. A second thing this size would mean neither is the headline.
_FS_DISPLAY = 22

# One family everywhere. Segoe UI is the Windows system face and the app
# is Windows-first; the fallbacks matter only when the theme is rendered
# elsewhere. Code blocks in rendered markdown are the single deliberate
# exception -- see MARKDOWN_PREVIEW_QSS.
_FONT_FAMILY = '"Segoe UI", "Inter", "Noto Sans", sans-serif'

# --- Spacing and shape --------------------------------------------------
# An 8px base with a 4px half-step for control interiors. No length in the
# sheet below is off this list.
_SP_HALF = 4
_SP_1 = 8
_SP_1_5 = 12
_SP_2 = 16
_SP_3 = 24

# Two radii: controls, and containers.
_RADIUS_CTL = 6
_RADIUS_BOX = 10

# The focus ring, kept as its own token rather than spelled _ACCENT at
# each site: it is the one visual element that must never be tuned away
# for aesthetic reasons, so it is named for its job. 1px of gold measures
# 7.79:1 on the window ground, clearing 1.4.11 without a glow -- and
# because every focusable rule below already reserves a 1px border, the
# ring never moves a layout when it appears.
_FOCUS = "#c9a24b"


DARK_QSS = f"""
/* --- base typography ------------------------------------------------ */

QWidget {{
    background-color: {_BG_WINDOW};
    color: {_FG_PRIMARY};
    font-family: {_FONT_FAMILY};
    font-size: {_FS_BODY}px;
}}

/* Headings opt in by objectName rather than by widget class, so a label
   is a title because someone said so -- explicit beats implicit. Every
   rule in this section picks its size off the three-tier scale; that is
   the only reason the scale is worth having. */
QLabel[objectName="titleLabel"] {{
    font-size: {_FS_HEADER}px;
    font-weight: 600;
    color: {_FG_PRIMARY};
}}

QLabel[objectName="sectionHeader"] {{
    font-size: {_FS_HEADER}px;
    font-weight: 600;
    color: {_FG_PRIMARY};
    letter-spacing: 0.4px;
}}

QLabel[objectName="muted"] {{
    font-size: {_FS_BODY}px;
    color: {_FG_SECONDARY};
}}

/* The signature. The dashboard greeting is the one line in Alfred that
   is allowed to be big *and* gold: it is the app saying good evening in
   the butler's own colour, and it is the reason no other heading needs
   an accent to feel like part of the same product. The display *step* is
   shared with the dashboard counts; the gold is not, which is what keeps
   one line per screen unmistakably the headline. */
QLabel[objectName="displayLabel"] {{
    font-size: {_FS_DISPLAY}px;
    font-weight: 600;
    color: {_ACCENT};
}}

/* Display *size* without the gold, for the one other place a line needs
   to be big -- the app's own name on the About page. Sharing the step
   keeps the type scale at three tiers plus one display step; withholding
   the accent keeps the dashboard greeting the only gold headline. */
QLabel[objectName="displayTitle"] {{
    font-size: {_FS_DISPLAY}px;
    font-weight: 600;
    color: {_FG_PRIMARY};
}}

/* The quiet line under it. */
QLabel[objectName="subheadLabel"] {{
    font-size: {_FS_BODY}px;
    color: {_FG_SECONDARY};
}}

/* A numeral that is the point of its card (the dashboard counts). The
   number is the data; the label under it is the legend, so only one of
   them is loud. */
QLabel[objectName="statValue"] {{
    font-size: {_FS_DISPLAY}px;
    font-weight: 600;
    color: {_FG_PRIMARY};
}}

QLabel[objectName="statLabel"] {{
    font-size: {_FS_META}px;
    color: {_FG_SECONDARY};
    letter-spacing: 0.4px;
}}

/* The meta tier: timestamps, captions, the capture bubble's echo of what
   you asked. Everything small and quiet in the app comes through here or
   through noteLabel, which is the same size with an italic voice. */
QLabel[objectName="microLabel"] {{
    font-size: {_FS_META}px;
    color: {_FG_MUTED};
}}

QLabel[objectName="noteLabel"] {{
    font-size: {_FS_META}px;
    color: {_FG_MUTED};
    font-style: italic;
}}

/* Inline validation. Red *prose* uses the lighter tint (see
   _DANGER_TEXT): the flat danger colour is a chrome colour and does not
   clear the text contrast floor. */
QLabel[objectName="errorLabel"] {{
    font-size: {_FS_META}px;
    color: {_DANGER_TEXT};
}}

QLabel[objectName="dangerNote"] {{
    font-size: {_FS_META}px;
    color: {_DANGER_TEXT};
    font-style: italic;
}}

/* "This needs a restart to take effect" and its kin: not destructive, so
   not red -- gold, which in this theme means "look here". */
QLabel[objectName="warningNote"] {{
    font-size: {_FS_META}px;
    color: {_ACCENT};
}}

/* A related-entry chip in the editor's "see also" row. A pill on the
   input tier, not the light-grey badge it used to be. */
QLabel[objectName="chipLabel"] {{
    background-color: {_BG_INPUT};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER};
    border-radius: {_RADIUS_CTL}px;
    padding: {_SP_HALF}px {_SP_1}px;
    font-size: {_FS_META}px;
}}

/* The capture popup's one input. It is the primary surface of the whole
   app -- most sessions never open the main window -- so it sits at the
   header step rather than at body. The vertical padding is load-bearing:
   the popup sizes itself from this widget. */
QLineEdit[objectName="captureInput"] {{
    font-size: {_FS_HEADER}px;
    padding: {_SP_1}px {_SP_1_5}px;
}}

/* --- main window / dialogs / generic panels ------------------------- */

QMainWindow, QDialog {{
    background-color: {_BG_WINDOW};
}}

/* Frames are grouping devices, not surfaces: they show the ground behind
   them unless they opt into being a card. */
QFrame, QWidget#centralWidget {{
    background-color: transparent;
}}

/* Cards: the dashboard sections, the capture popup's answer panel. The
   container radius, one tier up from the ground, one hairline of border
   -- that is the whole vocabulary for "this is a thing on a surface". */
QFrame[objectName="card"] {{
    background-color: {_BG_PANEL};
    border: 1px solid {_BORDER};
    border-radius: {_RADIUS_BOX}px;
    padding: {_SP_1_5}px;
}}

/* --- inputs --------------------------------------------------------- */

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
QComboBox, QDateEdit, QDateTimeEdit {{
    background-color: {_BG_INPUT};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER_STRONG};
    border-radius: {_RADIUS_CTL}px;
    padding: {_SP_1}px {_SP_1_5}px;
    selection-background-color: {_SELECTION};
    selection-color: {_SELECTION_TEXT};
}}

QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover,
QDoubleSpinBox:hover, QComboBox:hover, QDateEdit:hover,
QDateTimeEdit:hover {{
    background-color: {_BG_RAISED};
}}

/* Focus is a 1px gold border replacing a 1px neutral one, so it costs no
   layout: nothing moves when you tab into a field. */
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QDateEdit:focus, QDateTimeEdit:focus {{
    background-color: {_BG_INPUT};
    border: 1px solid {_FOCUS};
}}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled {{
    color: {_FG_DISABLED};
    background-color: {_BG_PANEL};
    border-color: {_BORDER};
}}

/* No ::drop-down rule on purpose. Styling that sub-control makes Qt stop
   painting its own arrow, and replacing it would mean shipping an icon --
   so the well stays native and the affordance stays visible. */

/* The popup is a menu, so it takes the container tier and radius rather
   than the input tier it drops out of. */
QComboBox QAbstractItemView {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER};
    border-radius: {_RADIUS_BOX}px;
    padding: {_SP_HALF}px;
    outline: none;
    selection-background-color: {_SELECTION};
    selection-color: {_SELECTION_TEXT};
}}

/* --- buttons -------------------------------------------------------- */

QPushButton {{
    background-color: {_BG_INPUT};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER_STRONG};
    border-radius: {_RADIUS_CTL}px;
    padding: {_SP_1}px {_SP_2}px;
    font-weight: 500;
}}

/* Hover lifts one tier and leaves the outline alone. Reaching for the
   accent on hover is the mistake that makes a gold theme look like a
   Christmas tree: every button under the cursor claims to be the primary
   action. Lightening is enough of an answer to "yes, I see you". */
QPushButton:hover {{
    background-color: {_BG_RAISED};
}}

/* Pressed goes the other way -- one tier *down*, with a gold edge. Down
   is the direction a physical button moves, and because hover went up
   the two states can never be mistaken for one another. */
QPushButton:pressed {{
    background-color: {_BG_PANEL};
    border-color: {_ACCENT};
}}

QPushButton:disabled {{
    color: {_FG_DISABLED};
    background-color: {_BG_PANEL};
    border-color: {_BORDER};
}}

/* Keyboard focus, on every button including the primary and destructive
   ones. The app had no button focus style at all before, which meant a
   keyboard user tabbing through the toolbar could not tell where they
   were -- the one accessibility gap worth calling critical, because it
   makes the UI unoperable rather than merely unattractive. A 1px gold
   border replaces the 1px neutral one, so unlike the 2px ring it grew
   out of it needs no padding compensation to avoid a jump. ``:focus`` is
   used rather than ``:focus-visible`` (which Qt's QSS subset does not
   implement), so a mouse click shows the ring too -- a far smaller cost
   than an invisible focus. */
QPushButton:focus {{
    border: 1px solid {_FOCUS};
}}

/* The one button per surface that does the thing. The accent fill is why
   the accent is not spent on hovers and headings. */
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

QPushButton[objectName="primaryButton"]:disabled {{
    background-color: {_BG_PANEL};
    color: {_FG_DISABLED};
    border-color: {_BORDER};
}}

/* Destructive actions -- emptying the trash, deleting an entry for good,
   the Settings danger zone. Outlined rather than filled: a red block is
   easy to hit by accident, and these buttons sit next to harmless ones.
   Hover reddens (an 18%-opacity wash, the deepest tint that still lets
   the label clear 4.5:1 on top of it -- a solid red fill cannot, which is
   why press darkens instead of filling). */
QPushButton[objectName="dangerButton"] {{
    background-color: {_BG_INPUT};
    color: {_DANGER_TEXT};
    border: 1px solid {_DANGER};
}}

QPushButton[objectName="dangerButton"]:hover {{
    background-color: {_DANGER_WASH};
}}

/* Press goes down a tier and brightens the edge, mirroring the neutral
   button's direction so the gesture feels the same everywhere. */
QPushButton[objectName="dangerButton"]:pressed {{
    background-color: {_BG_PANEL};
    border-color: {_DANGER_TEXT};
}}

QPushButton[objectName="dangerButton"]:disabled {{
    color: {_FG_DISABLED};
    background-color: {_BG_PANEL};
    border-color: {_BORDER};
}}

/* --- lists / trees / tables ----------------------------------------- */

QListWidget, QTreeWidget, QTreeView, QTableWidget {{
    background-color: {_BG_INPUT};
    color: {_FG_PRIMARY};
    /* ``MemoryListWidget`` turns on alternating row colors, and Qt reads
       that colour from the *stylesheet* here rather than from the palette
       when a sheet is present -- omitting it is how a list ends up with
       invisible stripes. */
    alternate-background-color: {_BG_PANEL};
    border: 1px solid {_BORDER};
    border-radius: {_RADIUS_CTL}px;
    outline: none;
    selection-background-color: {_SELECTION};
    selection-color: {_SELECTION_TEXT};
}}

QListWidget::item, QTreeWidget::item {{
    padding: {_SP_1}px;
    border-radius: {_RADIUS_CTL}px;
}}

QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {_BG_RAISED};
}}

QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {_SELECTION};
    color: {_SELECTION_TEXT};
}}

/* A focused row inside a list that does not have focus itself still has
   to be findable, so it keeps a gold hairline instead of a fill. */
QListWidget::item:focus, QTreeWidget::item:focus {{
    border: 1px solid {_FOCUS};
}}

QListWidget:focus, QTreeWidget:focus, QTreeView:focus,
QTableWidget:focus {{
    border: 1px solid {_FOCUS};
}}

/* The dashboard's due-today list lives inside a card, so it drops the
   fill and border the rule above gives every other list -- otherwise it
   draws a second box inside the card's. */
QListWidget#dashboardTaskList {{
    background-color: transparent;
    border: none;
    padding: 0;
}}

QHeaderView::section {{
    background-color: {_BG_PANEL};
    color: {_FG_SECONDARY};
    border: none;
    border-right: 1px solid {_BORDER};
    border-bottom: 1px solid {_BORDER};
    padding: {_SP_HALF}px {_SP_1}px;
    font-size: {_FS_META}px;
    font-weight: 600;
}}

/* --- nav rails ------------------------------------------------------- */
/* Two lists in the app are navigation rather than content: the main
   window's view switcher and the Settings dialog's category list. They
   share these rules because they mean the same thing -- "you are here" --
   and a settings dialog that highlights its rows differently from the
   window behind it looks like two products. Content lists keep the
   stronger selection from the generic rule above: picking a note is a
   choice about data, and it should look like the loudest thing on screen
   in a way that being on a screen never should. */
QListWidget#sidebarList, QListWidget#categoryList {{
    background-color: {_BG_PANEL};
    border: none;
    border-right: 1px solid {_BORDER};
    border-radius: 0;
    padding: {_SP_1}px {_SP_HALF}px;
}}

/* The transparent left edge is load-bearing: the selected rule below
   swaps its colour in, and reserving the width here is what stops the
   label shifting 3px sideways the moment a row is chosen. (Edge weights
   -- hairlines, this bar -- are not spacing and are not on the 8px
   scale; they are the thinnest mark that reads at all.) */
QListWidget#sidebarList::item, QListWidget#categoryList::item {{
    padding: {_SP_1}px {_SP_1_5}px;
    border-left: 3px solid transparent;
    border-radius: {_RADIUS_CTL}px;
    margin: {_SP_HALF}px;
    color: {_FG_SECONDARY};
}}

QListWidget#sidebarList::item:hover,
QListWidget#categoryList::item:hover {{
    background-color: {_BG_RAISED};
    color: {_FG_PRIMARY};
}}

/* Where you are, said quietly. The old rule filled the row with solid
   gold, which made the nav rail the loudest thing on every screen and
   left the accent unable to mean "primary action" anywhere else. Three
   restrained cues carry it instead -- a 16%-opacity gold wash, gold
   semibold text, and a gold bar on the leading edge -- and each survives
   on its own, so the state does not depend on hue alone. */
QListWidget#sidebarList::item:selected,
QListWidget#categoryList::item:selected {{
    background-color: {_ACCENT_WASH};
    border-left: 3px solid {_ACCENT};
    color: {_ACCENT};
    font-weight: 600;
}}

QListWidget#sidebarList::item:selected:hover,
QListWidget#categoryList::item:selected:hover {{
    background-color: {_ACCENT_WASH_HOVER};
}}

/* The sidebar's own id selector sets ``border: none``, which outranks the
   generic ``QListWidget:focus`` rule above (an id beats a pseudo-class in
   Qt's CSS2 specificity), so the keyboard ring has to be restated here or
   the sidebar -- the first tab stop in the window -- would be the one
   list with no visible focus. Drawn on the row rather than the frame: a
   full-height outline around a borderless rail looks like a mistake. */
QListWidget#sidebarList::item:focus,
QListWidget#categoryList::item:focus {{
    border: 1px solid {_FOCUS};
    border-left: 3px solid {_FOCUS};
}}

/* Alternating rows are meaningless on a six-item nav rail, and the
   generic list rule would otherwise stripe it. */
QListWidget#sidebarList, QListWidget#categoryList {{
    alternate-background-color: {_BG_PANEL};
}}

/* --- status / menu --------------------------------------------------- */

QStatusBar {{
    background-color: {_BG_PANEL};
    color: {_FG_SECONDARY};
    border-top: 1px solid {_BORDER};
    font-size: {_FS_META}px;
}}

QStatusBar::item {{
    border: none;
}}

QMenuBar {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    border-bottom: 1px solid {_BORDER};
    padding: {_SP_HALF}px;
}}

QMenuBar::item {{
    padding: {_SP_HALF}px {_SP_1}px;
    border-radius: {_RADIUS_CTL}px;
    background: transparent;
}}

QMenuBar::item:selected {{
    background-color: {_BG_RAISED};
}}

QMenuBar::item:pressed {{
    background-color: {_ACCENT_WASH};
    color: {_ACCENT};
}}

QMenu {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    border: 1px solid {_BORDER};
    border-radius: {_RADIUS_BOX}px;
    padding: {_SP_HALF}px;
}}

QMenu::item {{
    padding: {_SP_1}px {_SP_2}px;
    border-radius: {_RADIUS_CTL}px;
}}

QMenu::item:selected {{
    background-color: {_ACCENT_WASH};
    color: {_ACCENT};
}}

QMenu::item:disabled {{
    color: {_FG_DISABLED};
}}

QMenu::separator {{
    height: 1px;
    background-color: {_BORDER};
    margin: {_SP_HALF}px {_SP_1}px;
}}

/* --- group / tab / splitter ----------------------------------------- */

QGroupBox {{
    background-color: {_BG_PANEL};
    border: 1px solid {_BORDER};
    border-radius: {_RADIUS_BOX}px;
    margin-top: {_SP_2}px;
    padding: {_SP_2}px {_SP_1_5}px {_SP_1_5}px {_SP_1_5}px;
    color: {_FG_PRIMARY};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 {_SP_1}px;
    color: {_FG_SECONDARY};
    font-size: {_FS_META}px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}

QCheckBox, QRadioButton {{
    background: transparent;
    color: {_FG_PRIMARY};
    spacing: {_SP_1}px;
    padding: {_SP_HALF}px 0;
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: {_FG_DISABLED};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: {_SP_2}px;
    height: {_SP_2}px;
    border-radius: {_SP_HALF}px;
    border: 1px solid {_BORDER_STRONG};
    background: {_BG_INPUT};
}}

QRadioButton::indicator {{
    border-radius: {_SP_1}px;
}}

/* A checked box is filled *and* its border changes, so the state never
   rests on hue alone (some checked boxes here sit next to destructive
   actions -- the "I understand this will overwrite" confirmation on the
   Backups page is one). */
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {_ACCENT};
    border-color: {_ACCENT};
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {_ACCENT};
}}

/* Sub-control first, pseudo-state second. Written the other way round
   (``QCheckBox:focus::indicator``) Qt applies the border to the whole
   widget instead of the box, and unconditionally -- every checkbox in the
   app was drawn inside a gold rectangle whether it had focus or not. */
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{
    border: 2px solid {_FOCUS};
}}

/* A *checked* box is filled with the accent, and a gold ring on a gold
   fill is no ring at all -- keyboard focus vanished on exactly the boxes
   most worth confirming (the "I understand this will overwrite all my
   current data" gate on the Backups page is a checked box the user is
   about to act on).
   The first attempt used _ACCENT_TEXT, the ink that sits on the accent
   everywhere else, and was still invisible: that ink is _BG_WINDOW, and
   the ring has to contrast with what surrounds the box as well as with
   what it encloses, so a #14120f ring on a #14120f ground just made the
   gold square 2px smaller. The lightest ink in the palette clears both --
   15.2:1 on the ground, 2.0:1 on the fill. Indicators are fixed-size, so
   the 2px here costs no layout the way it would on a button. */
QCheckBox::indicator:checked:focus,
QRadioButton::indicator:checked:focus {{
    border: 2px solid {_FG_PRIMARY};
}}

QTabWidget::pane {{
    background-color: {_BG_PANEL};
    border: 1px solid {_BORDER};
    border-radius: {_RADIUS_BOX}px;
    top: -1px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {_FG_SECONDARY};
    border: 1px solid transparent;
    /* Reserved for the selected tab's gold edge, so selecting one does
       not nudge the whole bar down by 2px. */
    border-top: 3px solid transparent;
    border-top-left-radius: {_RADIUS_CTL}px;
    border-top-right-radius: {_RADIUS_CTL}px;
    padding: {_SP_1}px {_SP_2}px;
    margin-right: {_SP_HALF}px;
}}

QTabBar::tab:hover {{
    background-color: {_BG_RAISED};
    color: {_FG_PRIMARY};
}}

/* The selected tab joins its pane: same fill, and a gold bar on the top
   edge -- the same "leading edge" cue the sidebar uses for the same
   meaning, rotated to suit a horizontal rail. */
QTabBar::tab:selected {{
    background-color: {_BG_PANEL};
    color: {_FG_PRIMARY};
    border-color: {_BORDER};
    border-top: 3px solid {_ACCENT};
    border-bottom-color: {_BG_PANEL};
    font-weight: 600;
}}

QTabBar::tab:focus {{
    border-color: {_FOCUS};
}}

/* The splitter handle reads as the gap between two panels rather than as
   part of either one. Hover lightens; only a drag in progress goes gold,
   which is the one moment the handle is the thing you are operating. */
QSplitter::handle {{
    background-color: {_BG_WINDOW};
}}

QSplitter::handle:hover {{
    background-color: {_BG_RAISED};
}}

QSplitter::handle:pressed {{
    background-color: {_ACCENT};
}}

QSplitter::handle:horizontal {{
    width: {_SP_HALF}px;
    margin: 0 1px;
}}

QSplitter::handle:vertical {{
    height: {_SP_HALF}px;
    margin: 1px 0;
}}

/* --- scrollbars ----------------------------------------------------- */

QScrollBar:vertical {{
    background: {_BG_PANEL};
    width: {_SP_1_5}px;
    margin: 0;
    border: none;
}}

/* The handle is a UI component, so it takes the 3:1 border token rather
   than a decorative grey. Same three-state ladder as everything else:
   neutral, lighter on hover, gold while dragging. */
QScrollBar::handle:vertical {{
    background: {_BORDER_STRONG};
    min-height: {_SP_3}px;
    border-radius: {_SP_HALF}px;
    /* A groove inset, not layout spacing: 4px here would leave a 4px
       handle inside a 12px bar, which is a hairline, not a grip. */
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {_FG_SECONDARY};
}}

QScrollBar::handle:vertical:pressed {{
    background: {_ACCENT};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

QScrollBar:horizontal {{
    background: {_BG_PANEL};
    height: {_SP_1_5}px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: {_BORDER_STRONG};
    min-width: {_SP_3}px;
    border-radius: {_SP_HALF}px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {_FG_SECONDARY};
}}

QScrollBar::handle:horizontal:pressed {{
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
    border-radius: {_RADIUS_CTL}px;
    padding: {_SP_HALF}px {_SP_1}px;
    font-size: {_FS_META}px;
}}

/* --- capture popup speech bubble ------------------------------------- */
/* The popup itself goes transparent so the rounded bubble inside it is
   what the user sees floating next to the companion. The dialog keeps its
   own background off via the id selector, which outranks the generic
   QDialog rule above. */

QDialog[objectName="capturePopup"] {{
    background: transparent;
}}

/* The bubble floats over the desktop rather than over one of Alfred's own
   surfaces, so its hairline uses the 3:1 border token: the wallpaper
   behind it could be any colour, and a subtle edge would dissolve into
   the light ones. ``ui.capture_popup`` imports this fill and edge to
   paint the bubble's tail, which a stylesheet cannot reach. */
QFrame[objectName="bubbleFrame"] {{
    background-color: {_BG_PANEL};
    border: 1px solid {_BORDER_STRONG};
    border-radius: {_RADIUS_BOX}px;
}}

/* Alfred's reply -- the one line the user actually reads. */
QLabel[objectName="assistantSays"] {{
    color: {_FG_PRIMARY};
    font-size: {_FS_HEADER}px;
    padding: {_SP_HALF}px;
}}

/* The quiet "You asked: ..." echo above it. */
QLabel[objectName="askedEcho"] {{
    color: {_FG_MUTED};
    font-size: {_FS_META}px;
}}
"""


# Rendered-markdown CSS for the editor's preview pane. Kept here, next to
# the tokens, rather than inline in ``ui.editor_panel``: the preview is one
# more surface in the theme, and it used to carry the app's last
# light-theme leftovers -- ``#f4f4f4`` code blocks and ``#ccc`` table
# rules, which is what a note looked like when Alfred still had a light
# mode. This is a QTextDocument default stylesheet, not a Qt stylesheet:
# it styles the HTML the markdown renderer emits, so it speaks a small
# subset of CSS and is applied through ``document().setDefaultStyleSheet``.
MARKDOWN_PREVIEW_QSS = f"""
a {{ color: {_LINK}; }}
h1, h2, h3, h4 {{ color: {_FG_PRIMARY}; }}
/* The raised tier, not the panel tier: the preview pane is itself drawn
   on the input fill, so a code block tinted one step *down* would be
   invisible on the surface it is supposed to stand out from. */
pre {{
    background-color: {_BG_RAISED};
    color: {_FG_PRIMARY};
    padding: {_SP_1}px;
    font-family: "Consolas", "Cascadia Mono", monospace;
}}
code {{
    background-color: {_BG_RAISED};
    color: {_FG_PRIMARY};
    font-family: "Consolas", "Cascadia Mono", monospace;
}}
blockquote {{ color: {_FG_SECONDARY}; }}
table {{ border-collapse: collapse; }}
th, td {{
    border: 1px solid {_BORDER_STRONG};
    padding: {_SP_HALF}px {_SP_1}px;
}}
th {{ background-color: {_BG_PANEL}; color: {_FG_PRIMARY}; }}
hr {{ border: 1px solid {_BORDER}; }}
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
      the capture popup's prompt were black on the input tier:
      about 1.5:1, effectively invisible.
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
    # Shadow is the window ground rather than an eleventh colour:
    # there is no tier below the deepest one.
    _set(role.Light, _BORDER_STRONG)
    _set(role.Midlight, _BORDER)
    _set(role.Mid, _BORDER)
    _set(role.Dark, _BG_WINDOW)
    _set(role.Shadow, _BG_WINDOW)

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

    Also sets a default application font matching the
    stylesheet's 13px body (Qt's Windows default is 8pt,
    around 11px, which feels cramped inside the padding
    values the sheet uses). The stylesheet still wins
    per-widget; this only moves the baseline for anything
    no selector reaches.

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
    base_font: QFont = app.font()
    if base_font.pointSize() < 10:
        base_font.setPointSize(10)
    app.setFont(base_font)
