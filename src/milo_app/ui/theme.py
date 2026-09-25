"""Everything about how MILO looks, in one place.

Change a token below and the whole app follows. The stylesheet at the bottom is ordinary Qt
stylesheet (CSS-like) syntax; every widget that matters has an objectName to target, e.g.
QWidget#TopBar, QLabel#SimTitle, QTableView#SimTable.
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from milo_app.config import ASSETS

# ---------------------------------------------------------------------------- tokens

COLORS = {
    "ground": "#ffffff",      # window background
    "surface": "#f6f6f6",     # panels, table headers
    "ink": "#000000",         # main text, the MILO wordmark
    "muted": "#6b6b6b",       # secondary text
    "line": "#e4e4e4",        # borders and dividers
    "select": "#000000",      # selected row background
    "select_ink": "#ffffff",  # selected row text
    "hover": "#efefef",
    "ok": "#1f8a4c",
    "warn": "#b26a00",
    "bad": "#c0392b",
    "fail": "#d62828",        # a run that failed: its ring and badge on the graph, its name in the lists
    "fail_soft": "#fdecea",   # behind a failed run's error in its panel
    "redone": "#9a9a9a",      # a failed run that a later run redid
}

FONT_FILES = ("alte-haas-grotesk-regular.ttf", "alte-haas-grotesk-bold.ttf")
WORDMARK_FONT_FILE = "cs_regular.ttf"
FONT_FALLBACK = "Segoe UI"  # used if the font files above are missing
# Characters the MILO font does not have (→, Greek letters like ρ and σ, ∝) are drawn from these instead.
GLYPH_FALLBACKS = ("Segoe UI", "Segoe UI Symbol", "Cambria Math")

SIZES = {
    "base": 13,        # px, body text
    "small": 11,
    "title": 18,       # title in the side panel
    "wordmark": 30,    # Counter-Strike has a short cap height; this reads like 18 px body text
    "splash": 80,      # MILO on the starting screen
    "radius": 6,
    "pad": 12,
    "latex": 15,       # pt, the typeset formula in the Ghosts tab, written with names
    "latex_symbols": 22,  # pt, the same written with symbols (shrinks to fit the page on one line)
    "latex_legend": 13,   # pt, the symbols in its legend
    "legend_row": 26,     # px, height of one legend row
    "drawer": 400,     # width of the side panel that slides over the graph
    "drawer_ms": 240,  # how long it takes to slide
    "sure_big": 34,    # a ghost's or relationship's confidence, at the top of its panel
    "headline": 15,    # what it changes / says, under that
    "confidence_bar": 6,
}

# The graph canvas. Simulations are black circles; descriptor nodes are white pills edged in their
# group's color, and their links take the same color.
GRAPH = {
    "canvas": "#ffffff",
    "sim_fill": "#000000",
    "sim_size": 40,            # diameter of a simulation circle
    "sim_label_px": 11,        # its short name, underneath
    "sim_label_max": 150,      # widest that name may run before it is cut with "…"
    # the little arrow that fades in where a node is clicked (double-click the node to open its panel)
    "handle_size": 22,
    "handle_fill": "#000000",
    "handle_ink": "#ffffff",
    "handle_nudge": 5,         # px it sits up and to the right of the pointer (kept inside the node)
    "handle_in_ms": 150,       # how long it takes to fade and grow in
    "handle_stay_ms": 600,     # how long it stays before fading
    "handle_out_ms": 500,      # how long it takes to fade away
    "desc_fill": "#ffffff",
    "desc_ink": "#000000",
    "desc_height": 30,
    "desc_max_width": 320,
    "desc_value_px": 12,
    "edge_width": 1.4,
    "edge_alpha": 110,         # 0-255, how strongly links show their group color
    # relationship nodes: a diamond between two descriptors
    "rel_fill": "#000000",
    "rel_size": 26,
    "rel_label_px": 10,
    "rel_edge": "#000000",
    "rel_edge_width": 2.0,
    # prediction (ghost) nodes: a simulation circle that has not run yet
    "ghost_fill": "#ffffff",
    "ghost_line": "#9a9a9a",
    "ghost_ink": "#6b6b6b",
    "ghost_opacity": 0.85,
    "dim": 0.12,               # opacity of nodes a search does not match
    # ghosts: a campaign's ghosts are tinted with its color (these alphas, 0-255)
    "ghost_tint_fill": 34,
    "ghost_tint_line": 170,
    "ghost_change_px": 10,     # the shorthand of what a ghost changes, under it
    # a run that failed: a red ring round its circle and a "!" badge (grey, with no badge, once redone)
    "fail_ring_width": 3.0,
    "fail_badge": 15,          # diameter of the "!" badge
    # campaign rings: a dotted ring in the campaign's color around its runs, turning slowly
    "ring_margin": 58,         # px past the farthest run's centre (its circle and its label)
    "ring_width": 2.4,
    "ring_gap": 3.2,           # space between dots, in pen widths
    "ring_grab": 9,            # px either side of the ring that grab it
    "ring_ms": 40,             # frame time of the spin
    "ring_speed": 0.12,        # how far the dots travel each frame, in pen widths
}

# The formula chart in the Ghosts tab: the runs as dots, the fitted line, its 90 % band.
CHART = {
    "height": 300,
    "left": 64, "right": 16, "top": 12, "bottom": 42,  # room for the axis labels
    "band": "#ececec",
    "line": "#000000",
    "point": "#000000",
    "dot": 9,                  # diameter of a run's dot
    "grid": "#f0f0f0",
}

# Each descriptor group switched on takes the next color here, in the order they were switched on.
# A ghost's confidence as a color: red when unsure, through amber, to green when sure (hue in degrees).
CONFIDENCE_HUES = (0, 125)
CONFIDENCE_SAT_VAL = (0.85, 0.72)


def confidence_color(confidence: float) -> str:
    from PySide6.QtGui import QColor

    c = min(max(float(confidence), 0.0), 1.0)
    low, high = CONFIDENCE_HUES
    return QColor.fromHsvF((low + (high - low) * c) / 360, *CONFIDENCE_SAT_VAL).name()


GROUP_COLORS = [
    "#e4572e", "#2e86ab", "#3bb273", "#f3a712", "#8e44ad",
    "#e84393", "#00a8a8", "#6c5ce7", "#a0522d", "#708090",
]

# ---------------------------------------------------------------------------- stylesheet

STYLESHEET = """
QWidget {{ background: {ground}; color: {ink}; }}
QToolTip {{ background: {ink}; color: {ground}; border: none; padding: 4px 6px; }}

QWidget#TopBar {{ border-bottom: 1px solid {line}; }}
QLabel#Status {{ color: {muted}; font-size: {small}px; }}

QLineEdit {{ border: 1px solid {line}; border-radius: {radius}px; padding: 6px 10px; }}
QLineEdit:focus {{ border-color: {ink}; }}

QPushButton {{ border: 1px solid {ink}; border-radius: {radius}px; padding: 6px 14px; background: {ground}; }}
QPushButton:hover {{ background: {ink}; color: {ground}; }}

QTableView {{ border: none; gridline-color: transparent; selection-background-color: {select};
              selection-color: {select_ink}; alternate-background-color: {ground}; }}
QTableView::item {{ padding: 6px 8px; border-bottom: 1px solid {line}; }}
QTableView::item:hover {{ background: {hover}; }}
QTableView::item:selected {{ background: {select}; color: {select_ink}; }}
QHeaderView::section {{ background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {line};
                        padding: 6px 8px; font-size: {small}px; }}

QSplitter::handle {{ background: {line}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

QFrame#Drawer {{ background: {ground}; border-left: 1px solid {line}; }}
QWidget#Detail {{ background: {ground}; }}
QLabel#KindLabel {{ color: {muted}; font-size: {small}px; }}
QPushButton#IconButton {{ border: none; padding: 2px 6px; color: {muted}; }}
QPushButton#IconButton:hover {{ background: {hover}; color: {ink}; }}
QPushButton#LinkButton {{ border: none; border-radius: {radius}px; padding: 5px 8px; text-align: left; }}
QPushButton#LinkButton:hover {{ background: {hover}; color: {ink}; }}
QLabel#SimTitle {{ font-size: {title}px; font-weight: bold; }}
QLabel#SimId {{ color: {muted}; font-size: {small}px; }}
QLabel#FieldName {{ color: {muted}; font-size: {small}px; }}
QLabel#SureBig {{ font-size: {sure_big}px; font-weight: bold; }}
QLabel#Headline {{ font-size: {headline}px; font-weight: bold; }}
QLabel#ChangeValue {{ color: {ink}; }}
QLabel#ChangeSure {{ font-size: {small}px; font-weight: bold; }}
QLabel#Problems {{ color: {warn}; font-size: {small}px; }}
QLabel#RunError {{ background: {fail_soft}; color: {fail}; border-radius: {radius}px; padding: 8px 10px;
                   font-size: {small}px; }}
QLabel#RunRedone {{ background: {surface}; color: {muted}; border-radius: {radius}px; padding: 8px 10px;
                    font-size: {small}px; }}
QLabel#TestSummary {{ background: {surface}; border-radius: {radius}px; padding: 8px 10px; font-size: {small}px; }}
QLabel#Track {{ background: {surface}; border-radius: {radius}px; padding: 10px 12px; }}
QLabel#Empty {{ color: {muted}; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {line}; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 8px 14px; border: none;
                border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {ink}; border-bottom: 2px solid {ink}; }}

QWidget#Descriptors {{ background: transparent; }}
QWidget#Descriptors QLineEdit {{ background: {ground}; }}
QWidget#Descriptors QTreeWidget, QWidget#Descriptors QTreeWidget::viewport {{ background: transparent; }}
QLabel#PanelTitle {{ color: {muted}; font-size: {small}px; }}
QTreeWidget {{ background: {surface}; border: none; }}
QTreeWidget::item {{ padding: 3px 0; }}
QTreeWidget::item:hover {{ background: {hover}; }}
QTreeWidget::item:selected {{ background: transparent; color: {ink}; }}
QTreeWidget::indicator {{ width: 14px; height: 14px; border-radius: 3px; }}
QTreeWidget::indicator:unchecked {{ border: 1.5px solid {muted}; background: {ground}; }}
QTreeWidget::indicator:unchecked:hover {{ border-color: {ink}; }}
QTreeWidget::indicator:checked {{ border: 1.5px solid {ink}; background: {ink}; image: url({check}); }}

QWidget#TypeBar {{ border-bottom: 1px solid {line}; }}
QPushButton#Chip {{ border: 1px solid {line}; border-radius: 12px; padding: 4px 12px; color: {muted}; }}
QPushButton#Chip:hover {{ border-color: {ink}; background: {ground}; color: {ink}; }}
QPushButton#Chip:checked {{ background: {ink}; border-color: {ink}; color: {ground}; }}

QLabel#GraphHint {{ background: {surface}; color: {muted}; border: 1px solid {line}; border-radius: 12px;
                     padding: 4px 12px; font-size: {small}px; }}

QWidget#Splash {{ background: {ground}; }}
QLabel#SplashDetail {{ color: {muted}; }}
QLabel#SplashError {{ color: {bad}; font-size: {small}px; }}

QStatusBar {{ background: {surface}; color: {muted}; font-size: {small}px; border-top: 1px solid {line}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {line}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {line}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
"""


_family = FONT_FALLBACK
_wordmark_family = FONT_FALLBACK


def _quality_font(f: QFont) -> QFont:
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    return f


def apply(app: QApplication) -> None:
    global _family, _wordmark_family
    for name in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / name))
        if font_id >= 0:
            _family = QFontDatabase.applicationFontFamilies(font_id)[0]
    _wordmark_family = _family
    wordmark_id = QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / WORDMARK_FONT_FILE))
    if wordmark_id >= 0:
        _wordmark_family = QFontDatabase.applicationFontFamilies(wordmark_id)[0]
    font = _quality_font(_with_fallbacks(QFont(_family)))
    font.setPixelSize(SIZES["base"])
    app.setFont(font)  # the base font; stylesheet rules below only change sizes where they say so
    app.setStyle("Fusion")  # a neutral base the stylesheet fully controls
    check = (ASSETS / "check.svg").as_posix()  # the tick drawn in a checked box
    app.setStyleSheet(STYLESHEET.format(check=check, **COLORS, **SIZES))


# Qt stylesheets cannot space letters, so the two MILO wordmarks are styled here instead.
# Spacing is extra pixels between letters.
WORDMARKS = {"wordmark": 5, "splash": 14}


def _with_fallbacks(f: QFont) -> QFont:
    f.setFamilies([f.family(), *(name for name in GLYPH_FALLBACKS if name != f.family())])
    return f


def font(px: int, bold: bool = False) -> QFont:
    f = _quality_font(_with_fallbacks(QFont(_family)))
    f.setPixelSize(px)
    f.setBold(bold)
    # Hinting snaps letters to the pixel grid at 100 %; on the zoomable graph that grid no longer
    # lines up and words break apart ("am orphous"). Unhinted text stays even at every zoom.
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def wordmark_font(key: str) -> QFont:
    font = _quality_font(QFont(_wordmark_family))
    font.setPixelSize(SIZES[key])
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, WORDMARKS[key])
    return font
