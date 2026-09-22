"""ui/theme.py — the app's single global QSS + design tokens (docs/ui.md
section 6, "Phase 0").

Before this module, `main.py`'s `app.setStyleSheet(...)` hardcoded a
different, older palette (grey #f0f0f0 window, solid-blue-fill buttons on
every screen, grey table headers) directly inline. That global sheet always
wins over anything a tab sets on itself, so it silently overrode every
attempt elsewhere in the app to follow docs/ui.md's palette -- coloring a
table cell did nothing for the button/header/tab chrome around it, since the
chrome came from this one block regardless. This module is now the single
place that chrome comes from; nothing else should call
`QWidget.setStyleSheet(...)` with a hardcoded hex color for something this
file already has a rule for -- add an objectName selector here instead (see
`QPushButton#primary` / `#danger` below).
"""
from ui.colors import DANGER  # noqa: F401 -- re-exported; #danger buttons use this hue

BG          = "#fbfbfd"   # app background
SURFACE     = "#ffffff"   # cards, table body
ZEBRA       = "#fafbfe"   # even rows
HDR_BG      = "#f3f5fe"   # table header
GRP_BG      = "#f8f9fe"   # two-row group header, top row
TEXT        = "#1c1e2c"
TEXT_SUB    = "#595d6c"
TEXT_MUTED  = "#75798c"
TEXT_FAINT  = "#9397ab"
TEXT_EMPTY  = "#c3c6d4"   # "-" / disabled
LINE_STRONG = "#cfd3e5"
LINE        = "#e4e7f5"
LINE_SOFT   = "#f0f1f8"
ACCENT      = "#9184d9"
ACCENT_TEXT = "#5d5294"
ACCENT_BG   = "#f5f4ff"


def app_qss(font_css: str) -> str:
    """The whole app's QSS, built from the tokens above (docs/ui.md 1.1-1.10).
    Called once at startup from main.py.

    Two rules aren't in docs/ui.md's own reference snippet, added here out of
    necessity rather than invention:
      - QPushButton:checked -- several buttons in the app are toggles
        (Universe's "Target" filter, Trading History's "Sort by Date").
        Without a :checked rule they'd have no way to show their active
        state under this stylesheet.
      - QFrame#DashboardCard -- Trading History's KPI cards were the one
        widget-specific style in the docs/ui.md 6.2 violation list with no
        existing selector to fall back on (buttons/inputs/tables all have
        one above); this gives it a themed one instead of leaving it on its
        own hardcoded hex.
    """
    return f"""
    QMainWindow, QWidget#Page {{ background-color: {BG}; }}

    /* Buttons: neutral outline by default. Accent is opt-in via objectName,
       never picked per function (docs/ui.md 1.6: button color means
       primary/secondary/danger, never "which feature this is"). */
    QPushButton {{
        background: {SURFACE}; color: {TEXT_SUB};
        border: 1px solid {LINE_STRONG}; border-radius: 6px;
        padding: 5px 12px; font-weight: 400; {font_css}
    }}
    QPushButton:hover    {{ background: {HDR_BG}; }}
    QPushButton:pressed  {{ background: {LINE}; }}
    QPushButton:disabled {{ color: {TEXT_EMPTY}; border-color: {LINE_SOFT}; }}
    QPushButton:checked {{
        background: {ACCENT_BG}; color: {ACCENT_TEXT}; border-color: {ACCENT}; font-weight: 600;
    }}
    QPushButton#primary {{
        background: {SURFACE}; color: {ACCENT_TEXT}; border: 1px solid {ACCENT};
    }}
    QPushButton#primary:hover {{ background: {ACCENT_BG}; }}
    QPushButton#danger  {{ color: {DANGER}; border-color: #f0c4c1; }}
    QPushButton#danger:hover {{ background: #fdf0ef; }}

    QTableWidget, QTableView {{
        background-color: {SURFACE};
        alternate-background-color: {ZEBRA};
        gridline-color: {LINE_SOFT};
        selection-background-color: {ACCENT_BG};
        selection-color: {TEXT};
        border: 1px solid {LINE}; border-radius: 8px;
        {font_css}
    }}
    QTableWidget::item, QTableView::item {{ padding: 2px 8px; }}
    QTableWidget::item:hover {{ background: {ACCENT_BG}; }}

    QHeaderView::section {{
        background-color: {HDR_BG}; color: {TEXT_MUTED};
        border: none; border-bottom: 1px solid {LINE_STRONG};
        border-right: 1px solid {LINE};
        padding: 5px 8px; font-weight: 600; {font_css}
    }}

    QTabWidget::pane {{ border: 1px solid {LINE}; background: {SURFACE}; border-radius: 8px; }}
    QTabBar::tab {{
        background: transparent; color: {TEXT_MUTED};
        border: 1px solid transparent; border-radius: 6px;
        padding: 5px 12px; margin-right: 2px; font-weight: 400; {font_css}
    }}
    QTabBar::tab:selected {{ background: {ACCENT_BG}; color: {ACCENT_TEXT}; border-color: #c9c2f3; }}
    QTabBar::tab:hover:!selected {{ background: {HDR_BG}; }}

    /* Top-level tabs only (Trading Universe/Trading History/Total
       Assets/Strategy), not StrategyTab's nested sub-tab bar: 120% of the
       10pt app base size, bold (user direction, 2026-09-20). */
    QTabWidget#MainTabs QTabBar::tab {{ font-size: 12pt; font-weight: 700; }}

    QLineEdit, QComboBox {{
        background: {SURFACE}; color: {TEXT};
        border: 1px solid {LINE_STRONG}; border-radius: 6px;
        padding: 4px 9px; {font_css}
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
    QLineEdit[readOnly="true"] {{ background: transparent; border: none; color: {TEXT}; }}

    QFrame#DashboardCard {{
        background-color: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px;
    }}

    /* Sizes in pt so they scale with DPI like every create_font() call
       (docs/ui.md 6.4: mockup px x 0.75); the ui.common FONT_* scale. */
    QLabel#kpiLabel {{ color: {TEXT_FAINT}; font-size: 8pt; font-weight: 600; }}
    QLabel#kpiValue {{ color: {TEXT}; font-size: 13pt; }}
    QLabel#kpiSub   {{ color: {TEXT_FAINT}; font-size: 8pt; }}

    /* Secondary text: subtitles, status lines, footnotes. Tabs used to set
       these with per-label hex (#7f8c8d / #888 / #777) -- an objectName
       keeps them on one token. */
    QLabel#muted {{ color: {TEXT_MUTED}; }}
    QLabel#faint {{ color: {TEXT_FAINT}; }}
    """
