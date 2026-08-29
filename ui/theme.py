"""ui/theme.py — Light/dark theme palette and persistence (roadmap 3-6).

Design: the theme choice is read once at process startup (main.py's
``if __name__ == "__main__":`` block, via `init_theme()`, before
`QApplication` or any widget is constructed) and every widget across
main.py/ui/*.py builds its stylesheet strings from `c(...)` lookups against
whichever palette is active. There is no live, in-place re-theming of
already-built widgets: dozens of widgets set their own literal QSS strings
at construction time (rather than going through one shared app-level
stylesheet), so an accurate live re-theme would mean re-running most of the
UI-construction code anyway. The toggle in the UI instead saves the new
choice to custom_settings.json and asks for a restart to apply it — the
same "settings take effect after restart" pattern already used by
`custom_settings.json`'s other settings.
"""
import json
import os

_SETTINGS_FILE = "custom_settings.json"

# ---------------------------------------------------------------------------
# Palettes — semantic names only. Keep accent/button/P&L colors (already
# self-contained: colored background + explicit white text, or saturated
# enough to read on either background) unchanged between themes; only
# backgrounds, borders, and default/neutral text flip.
# ---------------------------------------------------------------------------
LIGHT = {
    "window_bg":      "#f0f0f0",
    "panel_bg":       "#ffffff",
    "panel_bg_alt":   "#f9f9f9",
    "card_bg":        "#ffffff",
    "input_bg":       "#ffffff",
    "table_bg":       "#ffffff",
    "table_alt_bg":   "#f9f9f9",
    "table_open_bg":  "#edfbf0",   # mint tint for open/current-holding rows
    "table_summary_bg": "#fff5e6",
    "header_bg":      "#e0e0e0",
    "header_bg_alt":  "#f0f2f5",
    "border":         "#d0d0d0",
    "border_light":   "#e4e4e4",
    "border_input":   "#c0c0c0",
    "gridline":       "#d0d0d0",
    "text":           "#1a1a2e",
    "text_secondary": "#444444",
    "text_muted":     "#7f8c8d",
    "text_faint":     "#999999",
    "tab_bg":         "#e0e0e0",
    "tab_selected_bg": "#ffffff",
    "accent":         "#0078d4",
    "accent_hover":   "#005a9e",
    "error_bg":       "#fdecea",
    "error_border":   "#e74c3c",
    # Bare status/heading text colors (no own background box) — kept as
    # separate keys from the button-background colors of the same hue, which
    # stay fixed between themes since they carry their own explicit
    # background + white text and read fine on either page background.
    "success_text":   "#107c10",
    "danger_text":    "#c0392b",
    "info_text":      "#0a3d62",
    "heading_text":   "#0078d4",
}

DARK = {
    "window_bg":      "#1e1e1e",
    "panel_bg":       "#2b2b2b",
    "panel_bg_alt":   "#333333",
    "card_bg":        "#2b2b2b",
    "input_bg":       "#3a3a3a",
    "table_bg":       "#242424",
    "table_alt_bg":   "#2b2b2b",
    "table_open_bg":  "#1f3327",   # dark mint tint for open/current-holding rows
    "table_summary_bg": "#3a3223",
    "header_bg":      "#3a3a3a",
    "header_bg_alt":  "#333333",
    "border":         "#4a4a4a",
    "border_light":   "#3a3a3a",
    "border_input":   "#5a5a5a",
    "gridline":       "#454545",
    "text":           "#e8e8e8",
    "text_secondary": "#cccccc",
    "text_muted":     "#a0a0a0",
    "text_faint":     "#8a8a8a",
    "tab_bg":         "#333333",
    "tab_selected_bg": "#2b2b2b",
    "accent":         "#0078d4",
    "accent_hover":   "#3399ff",
    "error_bg":       "#4a2020",
    "error_border":   "#e74c3c",
    "success_text":   "#4cd964",
    "danger_text":    "#ff6b6b",
    "info_text":      "#5dade2",
    "heading_text":   "#3399ff",
}

_active_theme_name = "light"
_active = LIGHT


def _read_theme_from_settings() -> str:
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("theme", "light")
            return name if name in ("light", "dark") else "light"
    except Exception:
        pass
    return "light"


def init_theme() -> str:
    """Read the saved theme choice and make it the active one. Must be called
    once, before any widget is built — main.py's __main__ block does this
    right after QApplication is created."""
    global _active_theme_name, _active
    _active_theme_name = _read_theme_from_settings()
    _active = DARK if _active_theme_name == "dark" else LIGHT
    return _active_theme_name


def is_dark() -> bool:
    return _active_theme_name == "dark"


def current_theme_name() -> str:
    return _active_theme_name


def c(key: str) -> str:
    """Look up a semantic color for the currently active theme."""
    return _active[key]


def save_theme_choice(name: str) -> None:
    """Persist the chosen theme name to custom_settings.json (roadmap 3-6).
    Reads-modifies-writes so it doesn't clobber the added/deleted/highlights
    keys UniverseTab also keeps in the same file."""
    name = "dark" if name == "dark" else "light"
    data = {}
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    data["theme"] = name
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def build_qt_palette():
    """QApplication.setPalette() target (roadmap 3-6). Themes native widget
    chrome (menus, scrollbars, disabled-state text, selection highlight) that
    the app-level QSS in main.py doesn't reach. A no-op (returns the
    already-default palette) in light mode."""
    from PyQt6.QtGui import QPalette, QColor

    pal = QPalette()
    if not is_dark():
        return pal

    text        = QColor(_active["text"])
    window_bg   = QColor(_active["window_bg"])
    base_bg     = QColor(_active["input_bg"])
    alt_bg      = QColor(_active["panel_bg_alt"])
    panel_bg    = QColor(_active["panel_bg"])
    accent      = QColor(_active["accent"])
    faint       = QColor(_active["text_faint"])

    pal.setColor(QPalette.ColorRole.Window, window_bg)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base_bg)
    pal.setColor(QPalette.ColorRole.AlternateBase, alt_bg)
    pal.setColor(QPalette.ColorRole.ToolTipBase, panel_bg)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, panel_bg)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ff5555"))
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, faint)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, faint)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, faint)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, faint)
    return pal


def apply_matplotlib_style() -> None:
    """roadmap 3-6: switch matplotlib's global style so every Figure created
    afterward (MA/backtest/total-assets charts) picks up dark_background."""
    import matplotlib.pyplot as plt
    plt.style.use('dark_background' if is_dark() else 'default')
    # plt.style.use() resets rcParams wholesale, so re-apply the app's own
    # font settings that main.py configures right after this call.
    plt.rcParams['font.family'] = ['Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic', 'sans-serif']
    plt.rcParams['axes.unicode_minus'] = False
