"""ui/common.py — Shared UI helpers, styling constants, validators, and atomic I/O.

Contains:
  - Font and styling factories (create_font, accent color constants)
  - QLineEdit auto-formatters and validation helpers
  - Atomic JSON persistence and safe loader functions
"""

import os
import json
import tempfile
import logging
import datetime as _dt
from typing import Any, Callable

from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtGui import QFont

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Font creation & typography
# ---------------------------------------------------------------------------
def create_font(
    size: int = 10,
    weight: QFont.Weight = QFont.Weight.Normal,
    style_name: str = None,
) -> QFont:
    """Create a unified Malgun Gothic Semilight font instance."""
    font = QFont()
    font.setFamilies(["Malgun Gothic Semilight", "맑은 고딕 Semilight", "Malgun Gothic"])
    font.setPointSize(size)
    if weight == QFont.Weight.Bold:
        font.setWeight(QFont.Weight.Bold)
    else:
        font.setWeight(QFont.Weight.Light)
        font.setStyleName(style_name or "Semilight")
    return font


# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------
_ACCENT_COLOR = "#0078d4"
_ACCENT_HOVER_COLOR = "#005a9e"
_HIST_KEYS = ["3d", "5d", "10d", "20d", "60d", "120d"]
_MARKET_ORDER = {"KOSPI": 0, "KOSDAQ": 1, "NASDAQ 100": 2, "S&P500": 3}
_FIELD_ERROR_STYLE = "border: 1px solid #e74c3c; background-color: #fdecea;"

# Shared font-family declaration for inline stylesheets (roadmap 6-2e); the
# same three-family fallback list create_font() uses. Splice it into a
# stylesheet string: "QLabel { " + FONT_FAMILY_CSS + " font-size: 9pt; }"
FONT_FAMILY_CSS = "font-family: 'Malgun Gothic Semilight', '맑은 고딕 Semilight', 'Malgun Gothic';"


# ---------------------------------------------------------------------------
# Input formatters & validators
# ---------------------------------------------------------------------------
def _fmt_num_edit(edit: QLineEdit, text: str, decimal: bool = False) -> None:
    """Re-format text with thousands commas and update QLineEdit in-place.
    Preserves cursor position. Supports optional decimal part.
    """
    raw = text.replace(',', '').strip()
    if not raw:
        return
    try:
        if decimal and '.' in raw:
            int_part, dec_part = raw.split('.', 1)
            int_part = int_part or '0'
            formatted = f"{int(int_part):,}.{dec_part}"
        else:
            formatted = f"{int(float(raw)):,}"
        if formatted != text:
            pos = edit.cursorPosition()
            delta = len(formatted) - len(text)
            edit.blockSignals(True)
            edit.setText(formatted)
            edit.setCursorPosition(max(0, pos + delta))
            edit.blockSignals(False)
    except (ValueError, OverflowError):
        pass


def _set_field_error(edit: QLineEdit, message: str = "") -> None:
    """Apply red-border error styling and tooltip on QLineEdit when message is truthy,
    or clear error styling when message is empty.
    """
    if message:
        edit.setStyleSheet(_FIELD_ERROR_STYLE)
        edit.setToolTip(message)
    else:
        edit.setStyleSheet("")
        edit.setToolTip("")


def _validate_date_str(text: str) -> bool:
    """True if text is a valid YYYY-MM-DD date.

    strptime's %Y-%m-%d accepts non-zero-padded month/day too (e.g. "2026-7-8"),
    so this validates parseability only -- callers that persist the value must
    run it through _normalize_date_str() first to get a consistent
    zero-padded form (portfolio.db's sell_date/buy_date columns previously
    ended up with a mix of both, which pandas parses fine but other tools may not).
    """
    text = text.strip()
    if not text:
        return False
    try:
        _dt.datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _normalize_date_str(text: str) -> str:
    """Reformats an already-valid YYYY-MM-DD date string to zero-padded form
    (e.g. "2026-7-8" -> "2026-07-08"). Returns the input stripped and
    unchanged if it doesn't parse -- callers should validate with
    _validate_date_str() first."""
    text = text.strip()
    try:
        return _dt.datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return text


def _validate_positive_number(text: str) -> bool:
    """True if text parses (after stripping ',' and '%') to a strictly positive float."""
    text = text.replace(",", "").replace("%", "").strip()
    if not text:
        return False
    try:
        return float(text) > 0
    except ValueError:
        return False


def _mk_field_validator(edit: QLineEdit, check_fn: Callable[[str], bool], error_msg: str) -> Callable[[], bool]:
    """Build a no-argument validator closure connected to QLineEdit.textChanged for live feedback."""
    def _run(_ignored=None) -> bool:
        ok = check_fn(edit.text())
        _set_field_error(edit, "" if ok else error_msg)
        return ok
    return _run


# ---------------------------------------------------------------------------
# QThread lifecycle helper
# ---------------------------------------------------------------------------
def retire_thread(owner, attr_name: str) -> None:
    """Abandon the QThread stored at ``owner.<attr_name>`` so a replacement can
    be started, without letting a still-running one be garbage-collected.

    If the thread is still running, its signals are blocked (so a late result
    cannot touch widgets the caller has moved on from) and the thread object
    is parked in ``owner._zombie_threads``, which collect_threads_to_stop()
    drains at shutdown. The attribute is then cleared. Replaces three
    hand-copied versions of this dance in the Universe and Trading History
    tabs (roadmap 6-2e).

    Uses blockSignals() rather than disconnect(): a wildcard disconnect()
    trips a benign but noisy "QObject::disconnect: wildcard call disconnects
    from destroyed signal" Qt warning when the connection's Python-slot side
    has no named C++ slot; blockSignals() gets the same "no late emissions
    reach the UI" effect without touching the connection list.
    """
    thread = getattr(owner, attr_name, None)
    if thread is None:
        return
    try:
        if thread.isRunning():
            thread.blockSignals(True)
            zombies = [t for t in getattr(owner, "_zombie_threads", []) if t.isRunning()]
            zombies.append(thread)
            owner._zombie_threads = zombies
    except RuntimeError:
        pass  # underlying C++ object already deleted
    setattr(owner, attr_name, None)


# ---------------------------------------------------------------------------
# Atomic file I/O helpers
# ---------------------------------------------------------------------------
def atomic_save_json(file_path: str, data: Any, indent: int = 2) -> None:
    """Atomically save data to a JSON file using a temporary file and atomic replace.
    Prevents corrupting or zeroing out the destination file on sudden exit.
    """
    abs_path = os.path.abspath(file_path)
    dir_name = os.path.dirname(abs_path)
    os.makedirs(dir_name, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(temp_path, abs_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        logger.error("Failed to atomically save JSON to %s: %s", file_path, e, exc_info=True)
        raise


def safe_load_json(file_path: str, default: Any = None) -> Any:
    """Safely load JSON data from file_path, returning default on error or missing file."""
    if not os.path.exists(file_path):
        return default if default is not None else {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load JSON from %s: %s", file_path, e, exc_info=True)
        return default if default is not None else {}
