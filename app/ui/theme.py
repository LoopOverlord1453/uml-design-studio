"""The live dark theme colour palette and the Qt style sheet.

Saturated, lively accents are used on a dark navy background; the diagram
canvas and the code editor share one palette so the interface looks like a
single product.
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtGui import QColor, QFont, QFontDatabase


#: The DARK palette (the default). The keys must be THE SAME in both
#: palettes; a missing key leaves the old colour in place on a theme
#: change -- apply_theme() checks for that.
DARK = {
    # surfaces
    "WINDOW": "#20242F",
    "PANEL": "#252A38",
    "PANEL_DARK": "#1C202B",
    "EDITOR_BG": "#171A22",
    "CANVAS_BG": "#191D27",
    "GUTTER_BG": "#1C202B",
    "BORDER": "#141821",
    # Separator lines must be DISTINGUISHABLE from the panel background;
    # with #3A4356 the ratio was 1.44 and the lines disappeared.
    "BORDER_LIGHT": "#454F66",
    "HOVER": "#323A4E",
    "SELECTION": "#2456A6",
    "CURRENT_LINE": "#232838",
    # text
    "TEXT": "#D6DBE6",
    "TEXT_DIM": "#8A93A6",
    "TEXT_BRIGHT": "#EDF1F8",
    "GUTTER_TEXT": "#5A6377",
    # the generated code panel (IT FOLLOWS THE THEME)
    #
    # The panel used to use the classic Visual Studio scheme and stayed white
    # REGARDLESS of the interface theme; in the dark theme half the screen
    # glared white. It now changes with the theme and the scheme has been
    # simplified: ONLY comments are green, the rest of the code is in the
    # plain text colour.
    "CODE_BG": "#171A22",
    "CODE_TEXT": "#DCE3EF",
    "CODE_COMMENT": "#6A9955",
    # The syntax scheme -- for a DARK background. The number of tones is
    # deliberately limited: a separate colour per token type makes the code
    # mottled and REDUCES readability. There are five meaning groups:
    # keyword, type, string, number/constant, preprocessor.
    "CODE_KEYWORD": "#569CD6",
    "CODE_TYPE": "#4EC9B0",
    "CODE_STRING": "#CE9178",
    "CODE_NUMBER": "#B5CEA8",
    "CODE_PREPROC": "#C586C0",
    "CODE_FUNCTION": "#DCDCAA",
    "CODE_CURRENT_LINE": "#1F2430",
    "CODE_GUTTER_BG": "#12151C",
    "CODE_GUTTER_TEXT": "#8994AC",
    "CODE_GUTTER_CUR": "#EDF1F8",
    # The background of the warning / error band in the code panel.
    # RED is written on it; with #4B2B2B the ratio was 4.13 (below 4.5).
    "BANNER_ERROR_BG": "#3A2020",
    "BANNER_WARN_BG": "#4A4326",
    # accents
    "ACCENT": "#4D9FFF",  # selection / focus blue
    "ACCENT_DARK": "#2456A6",
    "ORANGE": "#FF9F43",  # keyword
    "YELLOW": "#FFD166",  # function name
    "GREEN": "#3DDC84",  # string / success
    "BLUE": "#6FB3FF",  # number
    "PURPLE": "#B983FF",  # constant / macro
    "OLIVE": "#C7D66D",  # preprocessor
    "DOC_GREEN": "#5FBF87",  # documentation comment
    "RED": "#FF5C5C",  # error
    "WARN": "#FFB020",  # warning
    "INFO": "#4D9FFF",  # information
    "CYAN": "#35D0BA",  # secondary accent
    # diagram
    "STATE_FILL": "#2A3145",
    "STATE_FILL_ALT": "#273041",  # the body of a composite state
    "STATE_HEADER": "#3B6FD4",  # the title strip of a simple state
    "STATE_HEADER_ALT": "#2E9E8F",  # the title strip of a composite state
    "STATE_BORDER": "#5B6B8F",
    "STATE_TEXT": "#D6DBE6",
    "STATE_TITLE": "#FFFFFF",
    "STATE_SELECTED": "#4D9FFF",
    "STATE_HOVER": "#8FA7D9",
    "STATE_ERROR": "#FF5C5C",
    "GRID_MINOR": "#1F2431",
    "GRID_MAJOR": "#262C3C",
    "TRANSITION": "#9FB0CC",
    "TRANSITION_SEL": "#4D9FFF",
    # The background of a transition label: the same as the CANVAS colour. Its
    # purpose is to mask the arrow beneath it, NOT to look like a little box.
    "LABEL_BG": "#191D27",
    "LABEL_TEXT": "#F2F5FA",
    "SIM_ACTIVE": "#2EE59D",  # the halo of the active state in simulation
    "PSEUDO_FILL": "#E8ECF4",
    # INITIAL and FINAL are drawn in ONE COLOUR, as in UML 2.5.1 §14.2.4.7:
    # initial a filled circle, final two nested circles. The standard shows
    # them BLACK; in the dark theme plain black would be invisible on the dark
    # canvas (1.2:1), so the same INK is converted to a light tone. In the
    # light theme it really is black.
    "INITIAL_FILL": "#E8ECF4",  # the initial pseudostate (ink)
    "FINAL_RING": "#E8ECF4",  # the ring and the core of the final state
    "CHOICE_FILL": "#FFB020",  # the choice/junction diamond
    "HISTORY_FILL": "#B983FF",  # the history pseudostates
    "TERMINATE_FILL": "#FF5C5C",  # the terminate pseudostate
    # class diagram
    "CLASS_HEADER": "#7E57C2",  # the class title strip
    "IFACE_HEADER": "#00897B",  # the <<interface>> title strip
    "ABSTRACT_HEADER": "#5C6BC0",  # the abstract class title strip
    "CLASS_FILL": "#2A3145",
    "CLASS_TEXT": "#D6DBE6",
    # git panel
    "GIT_ADD": "#3DDC84",  # added line text
    "GIT_ADD_BG": "#1B3A2A",  # added line background
    "GIT_DEL": "#FF7A7A",  # removed line text
    "GIT_DEL_BG": "#3A1F24",  # removed line background
    "GIT_HUNK": "#6FB3FF",  # the @@ header
    "GIT_META": "#8A93A6",  # the diff/index headers
    "GIT_NODE": "#EDF1F8",  # the core of a commit node
    "GIT_HEAD_RING": "#FFD166",  # the HEAD ring
    "GIT_REF_BRANCH": "#2E7D5B",  # the background of a branch label
    "GIT_REF_REMOTE": "#4A5568",  # the background of a remote branch label
    "GIT_REF_TAG": "#7A5C1E",  # the background of a tag
    "GIT_REF_HEAD": "#2456A6",  # the background of the HEAD label
    "GIT_STAGED": "#3DDC84",  # the mark of a staged file
    "GIT_UNSTAGED": "#FF9F43",  # the mark of an unstaged file
    "GIT_CONFLICT": "#FF5C5C",  # a conflict
    # the CONTRAST token: the colour of text written on a background. A fixed
    # "#FFFFFF" CANNOT be right in both themes; it is kept per surface.
    "ON_ACCENT": "#FFFFFF",  # text on an accent background
    "SELECTED_TEXT": "#FFFFFF",  # the text of a selected list row
    "SELECTION_TEXT": "#FFFFFF",  # the colour of a selection inside a text field
    "ALT_ROW": "#20242F",  # the background of alternate rows in striped lists
    "SCROLL_HANDLE": "#4E5254",  # the scroll bar handle
    "SCROLL_HANDLE_HOVER": "#5E6365",
}

#: The LIGHT palette. Its key set is IDENTICAL to DARK.
#:
#: The colours were not inverted mechanically from the dark palette: a
#: direct inversion makes the accents unreadable on white (the #3DDC84
#: green, for instance, vanishes on white). Every accent was matched
#: separately to a tone carrying THE SAME MEANING with enough contrast.
LIGHT = {
    # surfaces
    "WINDOW": "#EEF1F6",
    "PANEL": "#F7F9FC",
    "PANEL_DARK": "#E7EBF2",
    "EDITOR_BG": "#FFFFFF",
    "CANVAS_BG": "#F6F8FC",
    "GUTTER_BG": "#EDF0F6",
    "BORDER": "#C7CEDB",
    "BORDER_LIGHT": "#AEB8C9",
    "HOVER": "#DCE3EF",
    "SELECTION": "#B7D4FF",
    "CURRENT_LINE": "#EDF2FB",
    # text
    "TEXT": "#1F2733",
    "TEXT_DIM": "#5E6A7D",
    "TEXT_BRIGHT": "#0C1119",
    "GUTTER_TEXT": "#98A2B3",
    # the generated code panel (see the note in DARK)
    "CODE_BG": "#FFFFFF",
    "CODE_TEXT": "#14181F",
    "CODE_COMMENT": "#007A1F",
    # The syntax scheme -- for a LIGHT background. The same five meaning
    # groups, in tones that pass 4.5:1 contrast on white.
    "CODE_KEYWORD": "#0033B3",
    "CODE_TYPE": "#00627A",
    "CODE_STRING": "#A31515",
    "CODE_NUMBER": "#1750EB",
    "CODE_PREPROC": "#7A3E9D",
    "CODE_FUNCTION": "#795E26",
    "CODE_CURRENT_LINE": "#F1F4FA",
    "CODE_GUTTER_BG": "#F3F5F9",
    "CODE_GUTTER_TEXT": "#5A6478",
    "CODE_GUTTER_CUR": "#0C1119",
    # In the light theme the band must be LIGHT too; with the dark theme
    # values hard-coded, dark brown patches appeared in a white interface.
    "BANNER_ERROR_BG": "#FDECEC",
    "BANNER_WARN_BG": "#FFF6E0",
    # accents
    "ACCENT": "#1668D6",
    "ACCENT_DARK": "#0F4EA8",
    "ORANGE": "#B45309",
    "YELLOW": "#8A6100",
    "GREEN": "#137A46",
    "BLUE": "#1D5FBF",
    "PURPLE": "#6B34C4",
    "OLIVE": "#5E6B12",
    "DOC_GREEN": "#1B6B47",
    "RED": "#C62828",
    "WARN": "#A65B00",
    "INFO": "#1668D6",
    "CYAN": "#0A6E63",
    # diagram
    # The diagram in the light theme: a white body plus a saturated title
    # strip. The title tones were darkened so that the WHITE text on them
    # passes 4.5:1; the difference between body and canvas was left just
    # light enough to separate the boxes from the background.
    "STATE_FILL": "#FFFFFF",
    "STATE_FILL_ALT": "#EEF3FB",
    "STATE_HEADER": "#2563C7",
    "STATE_HEADER_ALT": "#12766B",
    "STATE_BORDER": "#8494B0",
    "STATE_TEXT": "#1F2733",
    "STATE_TITLE": "#FFFFFF",
    "STATE_SELECTED": "#1668D6",
    "STATE_HOVER": "#5B7FBF",
    "STATE_ERROR": "#C62828",
    "GRID_MINOR": "#E9EDF5",
    "GRID_MAJOR": "#D9E0EC",
    "TRANSITION": "#44506A",
    "TRANSITION_SEL": "#1668D6",
    # In the light theme white boxes looked like patches "with no clear edge"
    # on the light grey canvas; the background matches the canvas, text PITCH BLACK.
    "LABEL_BG": "#F6F8FC",
    "LABEL_TEXT": "#000000",
    # It has to be DARK enough to read on a light background: the active state
    # label in simulation is written on a light grey panel and is its key fact.
    "SIM_ACTIVE": "#0A6B3A",
    "PSEUDO_FILL": "#2A3145",
    # In the light theme the ink is BLACK (see the note in DARK).
    "INITIAL_FILL": "#0B0F16",
    "FINAL_RING": "#0B0F16",
    "CHOICE_FILL": "#C98000",
    "HISTORY_FILL": "#6B34C4",
    "TERMINATE_FILL": "#C62828",
    # class diagram
    "CLASS_HEADER": "#5E35B1",
    "IFACE_HEADER": "#00695C",
    "ABSTRACT_HEADER": "#3949AB",
    "CLASS_FILL": "#FFFFFF",
    "CLASS_TEXT": "#1F2733",
    # git panel
    "GIT_ADD": "#137A46",
    "GIT_ADD_BG": "#DFF5E8",
    "GIT_DEL": "#C62828",
    "GIT_DEL_BG": "#FBE3E3",
    "GIT_HUNK": "#1D5FBF",
    "GIT_META": "#5E6A7D",
    "GIT_NODE": "#0C1119",
    "GIT_HEAD_RING": "#B8860B",
    "GIT_REF_BRANCH": "#BFE7D2",
    "GIT_REF_REMOTE": "#D7DDE8",
    "GIT_REF_TAG": "#F2E2B8",
    "GIT_REF_HEAD": "#C8DEFB",
    "GIT_STAGED": "#137A46",
    "GIT_UNSTAGED": "#B45309",
    "GIT_CONFLICT": "#C62828",
    # In the light theme the selection background is DARK blue (ACCENT_DARK), so
    # the text on it stays white; but the striped row background and the scroll
    # handle pull towards light tones -- copied from the dark palette they would
    # make dark patches on white and the rows would be unreadable.
    "ON_ACCENT": "#FFFFFF",
    "SELECTED_TEXT": "#FFFFFF",
    # In text fields the selection background is LIGHT blue (SELECTION); writing
    # white on it erases the text, so a DARK foreground is used here.
    "SELECTION_TEXT": "#0C1119",
    "ALT_ROW": "#F0F3F9",
    "SCROLL_HANDLE": "#B4BECD",
    "SCROLL_HANDLE_HOVER": "#93A0B3",
}


#: The available themes (the key stored in the settings -> the display name).
THEMES = {"dark": "Dark", "light": "Light"}

_active = ["dark"]


class C:
    """The LIVE theme colours.

    ``apply_theme()`` fills the attributes in. No fixed value is KEPT in the
    class body: so that the two palettes carrying the same key set can be
    verified in one place (the check below), and so that a theme change
    updates a SINGLE source.
    """


def active_theme() -> str:
    return _active[0]


def apply_theme(name: str) -> str:
    """Switches the palette; returns the name of the active theme.

    An unknown name falls back to the DARK theme -- the application must not
    refuse to start over a corrupt value in the settings file.
    """
    if name not in THEMES:
        name = "dark"
    palette = LIGHT if name == "light" else DARK
    for key, value in palette.items():
        setattr(C, key, value)
    _active[0] = name
    return name


# The two palettes must carry the same keys: a missing key leaves the colour
# of the PREVIOUS theme on a theme change and makes an unreadable patch in
# the interface. That is a bug you could only notice at run time; it is
# caught at import time.
_eksik_light = sorted(set(DARK) - set(LIGHT))
_eksik_dark = sorted(set(LIGHT) - set(DARK))
if _eksik_light or _eksik_dark:
    raise RuntimeError(
        "Tema paletleri ayrismis - LIGHT'ta eksik: %s | DARK'ta eksik: %s"
        % (_eksik_light, _eksik_dark))

# A malformed colour value is SILENTLY ignored by Qt: the widget is painted
# black or transparent and the reason shows up nowhere. It happened once
# ("#4A5costs"), so the format is validated at import time.
_bozuk = sorted(
    "%s.%s = %r" % (ident, map_key, val)
    for ident, palet in (("DARK", DARK), ("LIGHT", LIGHT))
    for map_key, val in palet.items()
    if not (isinstance(val, str) and len(val) == 7 and val[0] == "#"
            and all(ch in "0123456789abcdefABCDEF" for ch in val[1:])))
if _bozuk:
    raise RuntimeError("Invalid colour value: %s" % ", ".join(_bozuk))

apply_theme("dark")


def qc(name: str, alpha: int = 255) -> QColor:
    col = QColor(name)
    col.setAlpha(alpha)
    return col


# --------------------------------------------------------------------------- #
#   Fonts  --  JetBrains Mono
# --------------------------------------------------------------------------- #
#
# The whole interface is set in JetBrains Mono. The font DOES NOT HAVE TO BE
# INSTALLED ON THE SYSTEM: when present, the .ttf files under `assets/fonts/`
# are loaded into the application (QFontDatabase.addApplicationFont). That
# gives the same look in a packaged EXE as well.
#
# Without the files the application DOES NOT CRASH; the fallback chain below
# takes over and the interface keeps working. The font is decoration, not a

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "assets", "fonts")

#: Preference order: JetBrains first, then the sensible platform equivalents.
MONO_STACK = ("JetBrains Mono", "JetBrains Mono NL", "Cascadia Mono",
              "Consolas", "DejaVu Sans Mono", "Courier New")
UI_STACK = ("JetBrains Mono", "JetBrains Sans", "Inter",
            "Segoe UI Variable Text", "Segoe UI", "Noto Sans")

_fonts_loaded = False
_loaded_families: List[str] = []


def load_bundled_fonts() -> List[str]:
    """Loads the `assets/fonts/*.ttf|*.otf` files into the application.

    Runs once; returns the loaded family names. It must be called AFTER the
    QApplication has been created -- before that there is no font database.
    """
    # `_loaded_families` is modified IN PLACE (append), never rebound; so the
    # global declaration is only for `_fonts_loaded`.
    global _fonts_loaded
    if _fonts_loaded:
        return _loaded_families

    _fonts_loaded = True
    root = os.path.normpath(FONT_DIR)
    if not os.path.isdir(root):
        return _loaded_families

    for name in sorted(os.listdir(root)):
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        fid = QFontDatabase.addApplicationFont(os.path.join(root, name))
        if fid < 0:
            continue
        for aile in QFontDatabase.applicationFontFamilies(fid):
            if aile not in _loaded_families:
                _loaded_families.append(aile)
    return _loaded_families


def _first_available(stack) -> Optional[str]:
    families = QFontDatabase.families()
    for name in stack:
        if name in families:
            return name
    return None


#: The offset applied to EVERY font size in the interface (points).
#:
#: The user: "shrink the fonts a bit, make it like astah uml, let the
#: canvas be big." The sizes are written out at more than 100 call sites;
#: a single offset shrinks them all WITHOUT BREAKING THE PROPORTIONS.
FONT_DELTA = -1

#: The point size never falls below this (the legibility limit).
FONT_MIN_PT = 7


def scaled_pt(size: int) -> int:
    """Returns the point size at the call site, scaled for the interface."""
    return max(FONT_MIN_PT, size + FONT_DELTA)


def mono_font(size: int = 11) -> QFont:
    """The monospaced font for code and tables (JetBrains Mono)."""
    load_bundled_fonts()
    size = scaled_pt(size)
    name = _first_available(MONO_STACK)
    f = QFont(name or "monospace", size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True)
    return f


def ui_font(size: int = 9) -> QFont:
    """The interface font (JetBrains Mono; a fallback chain when absent)."""
    load_bundled_fonts()
    size = scaled_pt(size)
    name = _first_available(UI_STACK)
    if name is None:
        return QFont("sans-serif", size)
    return QFont(name, size)


def font_report() -> str:
    """For Help > About: which font is actually being used."""
    load_bundled_fonts()
    return "UI: %s   Mono: %s" % (_first_available(UI_STACK) or "sans-serif",
                                  _first_available(MONO_STACK) or "monospace")


STYLESHEET = """
* {{
    outline: 0;
}}

QWidget {{
    background: {panel};
    color: {text};
    selection-background-color: {sel};
    selection-color: {seltextfield};
}}

QMainWindow, QDialog {{
    background: {panel};
}}

/* --------------------------------------------------------- menu / toolbar */
QMenuBar {{
    background: {panel};
    border-bottom: 1px solid {border};
    padding: 2px 4px;
}}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
    border-radius: 4px;
}}
QMenuBar::item:selected {{ background: {hover}; }}
QMenuBar::item:pressed  {{ background: {accentdark}; }}

QMenu {{
    background: {paneldark};
    border: 1px solid {borderlight};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 28px 6px 24px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background: {accentdark}; color: {onaccent}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator {{
    height: 1px;
    background: {borderlight};
    margin: 5px 8px;
}}

QToolBar {{
    background: {panel};
    border: none;
    border-bottom: 1px solid {border};
    padding: 3px 6px;
    spacing: 3px;
}}
QToolBar::separator {{
    width: 1px;
    background: {borderlight};
    margin: 5px 6px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px 7px;
    color: {text};
}}
/* The three strips were reduced to one line; unless the menu buttons are
   kept narrow, the diagram tools spill over into the overflow button. */
QToolBar#topBar QToolButton {{ padding: 3px 5px; margin: 0 1px; }}
QToolBar#topBar QToolButton::menu-indicator {{ image: none; width: 0; }}
/* THE SELECTED TOOL MUST BE OBVIOUS: the user asked to "see the tool they
   picked". Not just the background -- BOLD text distinguishes it too. */
QToolBar#topBar QToolButton:checked {{ font-weight: 700; }}
QToolButton:hover  {{ background: {hover}; border-color: {borderlight}; }}
QToolButton:pressed{{ background: {accentdark}; }}
QToolButton:checked{{
    background: {accentdark};
    border-color: {accent};
    color: {onaccent};
}}
QToolButton:disabled {{ color: {dim}; }}

/* ------------------------------------------------------------------ dock */
QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: {textbright};
    font-weight: 600;
}}
QDockWidget::title {{
    background: {paneldark};
    padding: 6px 10px;
    border-bottom: 1px solid {border};
}}

QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical   {{ height: 3px; }}
QSplitter::handle:hover      {{ background: {accentdark}; }}

/* ------------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: 1px solid {border};
    background: {editor};
    top: -1px;
}}
QTabBar {{ background: {paneldark}; }}
QTabBar::tab {{
    background: {paneldark};
    color: {dim};
    padding: 7px 16px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 1px;
}}
QTabBar::tab:hover {{ color: {text}; background: {hover}; }}
QTabBar::tab:selected {{
    color: {textbright};
    background: {editor};
    border-bottom: 2px solid {orange};
}}

/* ------------------------------------------------------------------ inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {editor};
    border: 1px solid {borderlight};
    border-radius: 4px;
    padding: 4px 6px;
    color: {textbright};
    selection-background-color: {sel};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus {{
    border-color: {accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{
    color: {dim};
    background: {paneldark};
}}

QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {text};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {paneldark};
    border: 1px solid {borderlight};
    selection-background-color: {accentdark};
    color: {textbright};
    padding: 2px;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background: {panel};
    border: none;
    width: 14px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {hover}; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {borderlight};
    border-radius: 3px;
    background: {editor};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}

QPushButton {{
    background: {panel};
    border: 1px solid {borderlight};
    border-radius: 4px;
    padding: 6px 14px;
    color: {text};
}}
QPushButton:hover {{ background: {hover}; border-color: {accent}; }}
QPushButton:pressed {{ background: {accentdark}; }}
QPushButton:default {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {dim}; border-color: {border}; }}

/* ------------------------------------------------------------------ lists */
QTreeWidget, QListWidget, QTableWidget {{
    background: {editor};
    border: 1px solid {border};
    alternate-background-color: {altrow};
    color: {text};
}}
QTreeWidget::item, QListWidget::item {{
    padding: 4px 2px;
    border: none;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{
    background: {hover};
    color: {textbright};
}}
QTreeWidget::item:selected, QListWidget::item:selected,
QTreeWidget::item:selected:active, QListWidget::item:selected:active,
QTreeWidget::item:selected:!active, QListWidget::item:selected:!active {{
    background: {accentdark};
    color: {seltext};
    font-weight: bold;
}}
QHeaderView::section {{
    background: {paneldark};
    color: {dim};
    padding: 5px 8px;
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
}}

/* ---------------------------------------------------------------- scroll bar */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {scroll};
    min-height: 28px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {scrollhover}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {scroll};
    min-width: 28px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {scrollhover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -------------------------------------------------------------- status bar */
QStatusBar {{
    background: {paneldark};
    border-top: 1px solid {border};
    color: {dim};
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ padding: 0 8px; background: transparent; }}

/* The build progress bar (at the far right of the status bar).

   IT HAS NO TEXT, and that is deliberate: a percentage would run over both
   the FILLED and the EMPTY part, so ONE colour cannot be readable on both
   backgrounds (in the dark theme bright text stayed at 2.4:1 over the blue
   fill; a colour chosen for the fill was unreadable on the empty track).
   Which step is running is already written on the LEFT of the status bar. */
QProgressBar {{
    background: {editor};
    border: 1px solid {border};
    border-radius: 3px;
}}
QProgressBar::chunk {{
    background: {accent};
    border-radius: 2px;
}}

QGroupBox {{
    border: 1px solid {border};
    border-radius: 5px;
    margin-top: 14px;
    padding-top: 8px;
    color: {dim};
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}}

QLabel {{ background: transparent; }}
QToolTip {{
    background: {paneldark};
    color: {textbright};
    border: 1px solid {accent};
    padding: 5px 8px;
}}

QGraphicsView {{
    background: {canvas};
    border: none;
}}
"""


def stylesheet() -> str:
    return STYLESHEET.format(
        panel=C.PANEL, paneldark=C.PANEL_DARK, editor=C.EDITOR_BG,
        canvas=C.CANVAS_BG, border=C.BORDER, borderlight=C.BORDER_LIGHT,
        hover=C.HOVER, sel=C.SELECTION, text=C.TEXT, textbright=C.TEXT_BRIGHT,
        dim=C.TEXT_DIM, accent=C.ACCENT, accentdark=C.ACCENT_DARK,
        orange=C.ORANGE, onaccent=C.ON_ACCENT, seltext=C.SELECTED_TEXT,
        altrow=C.ALT_ROW, scroll=C.SCROLL_HANDLE,
        scrollhover=C.SCROLL_HANDLE_HOVER, seltextfield=C.SELECTION_TEXT,
    )
