"""ui/delegates.py — the app's custom-painted table cells (docs/ui.md 6.3).

Mini bars, range bars, sparklines and marker+badge identity cells need
QStyledItemDelegate + QPainter: Qt has no CSS-grid/minmax or cell-level
border-radius equivalent, and a QWidget per cell for 300+ rows is exactly
the "per-row cellWidget" pattern docs/ui.md flags as a performance and
visual-noise problem. Every delegate here reads its content from
Qt.ItemDataRole.UserRole (a plain dict) rather than the item's display
text, since none of these cells are single-line text.

They used to live next to their tables (ui/widgets.py, ui/history_table.py),
each with its own copy of the selection/zebra background fill and
badge-drawing code. CellDelegate is that shared part.

  Trading Universe:  IdentityDelegate, RangeBarDelegate, TrendDelegate
  Trading History:   TradeStateDelegate
"""
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPen

from ui.theme import ACCENT, ACCENT_TEXT, ACCENT_BG, TEXT, TEXT_FAINT, LINE, LINE_SOFT


class CellDelegate(QStyledItemDelegate):
    """Base for every custom-painted cell: the background fill that honors
    selection, an explicit BackgroundRole (Trading History sets its zebra
    stripe per item) or the view's alternate-row feature, plus the badge
    and marker primitives the identity-style cells share."""

    MARKER_W = 3
    MARKER_H = 18
    BADGE_PAD = 14

    @staticmethod
    def paint_background(painter, option, index=None):
        rect = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(ACCENT_BG))
            return
        bg = index.data(Qt.ItemDataRole.BackgroundRole) if index is not None else None
        if bg:
            painter.fillRect(rect, bg)
        elif option.features & option.ViewItemFeature.Alternate:
            painter.fillRect(rect, option.palette.alternateBase())
        else:
            painter.fillRect(rect, option.palette.base())

    @staticmethod
    def badge_font(base: QFont, delta: int = -2) -> QFont:
        f = QFont(base)
        f.setPointSize(max(6, base.pointSize() + delta))
        f.setBold(True)
        return f

    @classmethod
    def badge_width(cls, font: QFont, label: str) -> int:
        return QFontMetrics(font).horizontalAdvance(label) + cls.BADGE_PAD

    @staticmethod
    def draw_badge(painter, rect: QRect, label: str, fg: str, border: str, bg: str, font: QFont):
        painter.setPen(QPen(QColor(border)))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor(fg))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    @classmethod
    def draw_marker(cls, painter, cell: QRect, x: int, color: str):
        painter.fillRect(x, cell.y() + (cell.height() - cls.MARKER_H) // 2, cls.MARKER_W, cls.MARKER_H, QColor(color))

    META_GAP = 8

    @staticmethod
    def draw_name_and_meta(painter, option, x: int, w: int, name: str, meta: str):
        """One line: the name, then the faint "ticker · market" meta to its
        right (user direction 2026-09-19 -- it used to sit on a second line
        under the name). The meta keeps its full width; the name elides
        first when the cell is too narrow for both."""
        rect = option.rect
        name_font = QFont(option.font)
        name_fm = QFontMetrics(name_font)
        line_rect = QRect(x, rect.y(), w, rect.height())
        if not meta:
            painter.setFont(name_font)
            painter.setPen(QColor(TEXT))
            painter.drawText(line_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             name_fm.elidedText(name, Qt.TextElideMode.ElideRight, w))
            return

        meta_font = QFont(option.font)
        meta_font.setPointSize(max(6, option.font.pointSize() - 1))
        meta_fm = QFontMetrics(meta_font)
        meta_w = min(meta_fm.horizontalAdvance(meta), w)
        name_w = max(10, w - meta_w - CellDelegate.META_GAP)
        elided = name_fm.elidedText(name, Qt.TextElideMode.ElideRight, name_w)
        drawn_w = name_fm.horizontalAdvance(elided)

        painter.setFont(name_font)
        painter.setPen(QColor(TEXT))
        painter.drawText(QRect(x, rect.y(), name_w, rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        painter.setFont(meta_font)
        painter.setPen(QColor(TEXT_FAINT))
        meta_x = x + drawn_w + CellDelegate.META_GAP
        painter.drawText(QRect(meta_x, rect.y(), max(0, x + w - meta_x), rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, meta)


# ---------------------------------------------------------------------------
# Trading Universe (ui/widgets.py StockTable)
# ---------------------------------------------------------------------------
# docs/ui.md 1.7: one vocabulary for the highlight states custom_settings.json
# stores as "On"/"Tg" (kept as-is; the places that display it -- this
# button, the toolbar's blank/Port/Target status filter button and the row
# context menu -- agree on the Port/Target wording). The "-" (blank) state is
# drawn too: the per-row status button keeps one fixed size in every state
# (user direction 2026-09-29) so clicking it cycles - -> On -> Tg without the
# cell reflowing. (label, text, border, fill); fill None = outline only.
STATUS_BUTTON = {
    "-": ("", TEXT_FAINT, LINE, None),
    "On": ("Port", ACCENT_TEXT, ACCENT, ACCENT_BG),
    "Tg": ("Target", "#ffffff", ACCENT, ACCENT),
}
STATUS_MARKER = {"On": ACCENT, "Tg": ACCENT}


class IdentityDelegate(CellDelegate):
    """Column 0: status marker + name with the "ticker · market" meta to
    its right + a fixed-size status button, replacing the old separate
    Name/Market/Ticker columns and the per-row Pf button (docs/ui.md 2.2,
    1.7). The button is painted here and hit-tested by StockTable's mouse
    handlers through status_button_rect()."""

    STATUS_BTN_W = 56
    STATUS_BTN_H = 18
    STATUS_BTN_MARGIN = 6

    @classmethod
    def status_button_rect(cls, cell: QRect) -> QRect:
        """Where the status button sits inside an identity cell rect."""
        return QRect(cell.right() + 1 - cls.STATUS_BTN_W - cls.STATUS_BTN_MARGIN,
                     cell.y() + (cell.height() - cls.STATUS_BTN_H) // 2, cls.STATUS_BTN_W, cls.STATUS_BTN_H)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        self.paint_background(painter, option, index)

        data = index.data(Qt.ItemDataRole.UserRole) or {}
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        meta = data.get("meta", "")
        status = data.get("status", "-")

        self.draw_marker(painter, rect, rect.x() + 4, STATUS_MARKER.get(status, LINE))

        btn_rect = self.status_button_rect(rect)
        text_x = rect.x() + 4 + self.MARKER_W + 8
        text_w = max(10, btn_rect.x() - 6 - text_x)
        self.draw_name_and_meta(painter, option, text_x, text_w, name, meta)

        label, fg, border, fill = STATUS_BUTTON.get(status, STATUS_BUTTON["-"])
        painter.setPen(QPen(QColor(border)))
        painter.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(btn_rect, 4, 4)
        if label:
            painter.setPen(QColor(fg))
            painter.setFont(self.badge_font(option.font))
            painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), option.rect.height())


class RangeBarDelegate(CellDelegate):
    """52W Range column: a thin track with a position marker, plus
    low/position%/high labels underneath (docs/ui.md 2.3), replacing the
    four separate 52W High/High Diff/Low/Low Diff columns."""

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        self.paint_background(painter, option, index)

        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            painter.restore()
            return

        pad = 10
        track_y = rect.y() + 6
        track_h = 5
        track_x = rect.x() + pad
        track_w = max(1, rect.width() - 2 * pad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(LINE_SOFT))
        painter.drawRoundedRect(track_x, track_y, track_w, track_h, 2, 2)

        pos = max(0.0, min(1.0, data["pos"]))
        marker_x = track_x + int(pos * track_w) - 1
        painter.setBrush(QColor(data["color"]))
        painter.drawRoundedRect(marker_x, track_y - 1, 2, track_h + 2, 1, 1)

        label_font = QFont(option.font)
        label_font.setPointSize(max(6, option.font.pointSize() - 2))
        painter.setFont(label_font)
        label_y = track_y + track_h + 3
        label_rect = QRect(track_x, label_y, track_w, rect.bottom() - label_y)
        fm = QFontMetrics(label_font)

        painter.setPen(QColor(TEXT_FAINT))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, data["low"])
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, data["high"])
        painter.setPen(QColor(data["color"]))
        pct_text = data["pos_label"]
        pct_w = fm.horizontalAdvance(pct_text)
        painter.drawText(track_x + (track_w - pct_w) // 2, label_rect.y() + fm.ascent(), pct_text)

        painter.restore()


class TrendDelegate(CellDelegate):
    """Trend column: a small polyline of the 3D/20D/60D/120D momentum
    readings (docs/ui.md 2.2's "1Y" sparkline) -- honest about what data
    backs it (four real change-% points, not a year of daily closes, which
    this app doesn't fetch per watchlist row), rather than faking a smooth
    year-long chart."""

    def paint(self, painter, option, index):
        painter.save()
        self.paint_background(painter, option, index)
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data or len(data["points"]) < 2:
            painter.restore()
            return

        rect = option.rect
        pad_x, pad_y = 8, 5
        x0, y0 = rect.x() + pad_x, rect.y() + pad_y
        w = max(1, rect.width() - 2 * pad_x)
        h = max(1, rect.height() - 2 * pad_y)

        pts = data["points"]
        lo, hi = min(pts), max(pts)
        span = (hi - lo) or 1.0
        n = len(pts)
        coords = [
            (x0 + w * i / (n - 1), y0 + h - (v - lo) / span * h)
            for i, v in enumerate(pts)
        ]

        pen = QPen(QColor(data["color"]))
        pen.setWidthF(1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.restore()


# ---------------------------------------------------------------------------
# Trading History (ui/history_table.py)
# ---------------------------------------------------------------------------
class TradeStateDelegate(CellDelegate):
    """Company column: a left status marker + an Open/Closed badge painted at
    the cell's right edge (docs/ui.md 1.7, issue #5) -- replaces the old
    bg_open mint background, which shared its color channel with the zebra
    stripe and disappeared under print/color-blind conditions. Reads
    Qt.ItemDataRole.UserRole = {"state": "Open"|"Closed"} set by
    fill_table_rows(); paints plain text (via the base class) when absent."""

    _BADGE = {
        "Open":   ("Open", ACCENT_TEXT, ACCENT, ACCENT_BG),
        "Closed": ("Closed", TEXT_FAINT, LINE, "transparent"),
    }
    _MARKER = {"Open": ACCENT, "Closed": LINE}

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            super().paint(painter, option, index)
            return

        painter.save()
        rect = option.rect
        self.paint_background(painter, option, index)

        state = data.get("state", "Closed")
        self.draw_marker(painter, rect, rect.x() + 4, self._MARKER[state])

        label, fg, border, bg = self._BADGE[state]
        font = self.badge_font(option.font)
        bw, bh = self.badge_width(font, label), 15
        bx = rect.right() - bw - 6

        text_x = rect.x() + 4 + self.MARKER_W + 8
        text_w = max(10, bx - text_x - 4)
        self.draw_name_and_meta(painter, option, text_x, text_w, index.data(Qt.ItemDataRole.DisplayRole) or "", "")

        self.draw_badge(painter, QRect(bx, rect.y() + (rect.height() - bh) // 2, bw, bh), label, fg, border, bg, font)
        painter.restore()
