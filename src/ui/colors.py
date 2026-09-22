"""ui/colors.py — Single source of truth for profit/loss color semantics and
heatmap intensity (docs/ui.md 1.1, 1.2).

The whole app follows one rule: PROFIT (blue) = gain/up, LOSS (red) =
loss/down (user direction, 2026-09-22, overriding the earlier Korean market
convention of red=up/blue=down). Before this module, widgets.py used
blue for large positive change rates and history_table.py used red for
positive P/L, so the same color flipped meaning within a single Universe row
and across tabs. Any cell that previously picked a background from a
hardcoded threshold ladder should compute it via heatmap_bg() instead.
"""
from PyQt6.QtGui import QColor

PROFIT = "#3b6fc4"   # gain, up
LOSS = "#d1453b"      # loss, down
FLAT = "#75798c"      # zero / not applicable

# Danger button hue (delete/destructive actions) -- kept separate from
# PROFIT/LOSS so it doesn't flip meaning when the price-direction convention
# changes (2026-09-22: PROFIT/LOSS swapped to blue=up/red=down, but "danger"
# must stay red regardless of which one currently means "up").
DANGER = "#d1453b"

# Strategy tab (Auto Trading) "recommended action" badges -- a different axis
# from PROFIT/LOSS (price direction). The Strategy Redesign mockup picked
# green/red for these, but red already means "price up" app-wide (see
# heatmap_bg below); reusing it for a Sell badge would flip meaning within
# the same row, exactly what PROFIT/LOSS was introduced to stop. Sell uses
# an amber instead of PROFIT's red so it never collides with a price cell's
# color in the same row.
ACTION_BUY = "#2e7d5b"   # rebalance buy candidate
ACTION_SELL = "#b7791f"  # rebalance sell candidate

# Caution text/marks that are neither a price direction nor a trade action:
# skipped tickers in a backtest, an ATR-stop exit, a drawdown over the gate.
WARN = "#c77c1f"

# MA divergence (price / MA x 100): within +-MA_DIV_NEUTRAL_PCT of 100 is
# "neutral". The MA chart's three background bands (StockMaDialog) and the
# Universe table's MA20 Div cell both read this one number so the chart's
# overheated/depressed zones and the table's coloring agree (docs/ui.md
# MA Chart Redesign issue #6: the two used to have different thresholds).
MA_DIV_NEUTRAL_PCT = 2.0

# Moving-average lines: one accent ramp, darkest for the shortest window,
# so period order reads as lightness instead of five competing hues
# (MA Chart Redesign issue #2). Close is neutral ink (ui.theme.TEXT).
MA_RAMP = {"MA5": "#6d5fb8", "MA10": "#9184d9", "MA20": "#b5abfc", "MA50": "#d8d5f2"}

# Pre-allocated QColor instances -- avoids re-parsing the hex string on every
# cell of every row (_populate_row/fill_table_rows run per cell per refresh).
QC_PROFIT = QColor(PROFIT)
QC_LOSS = QColor(LOSS)
QC_FLAT = QColor(FLAT)


def fg_for(value: float) -> QColor:
    """Foreground color for a signed value: PROFIT above zero, LOSS below,
    FLAT at exactly zero."""
    if value > 0:
        return QC_PROFIT
    if value < 0:
        return QC_LOSS
    return QC_FLAT


def heatmap_bg(value: float, scale: float):
    """Background color for a momentum/change-rate cell (docs/ui.md 1.2).

    Replaces the per-column if-ladders that used to hardcode their own
    thresholds (Div(20): 120/110/105/102/98/93, Div(50): 130/110/107/103/98/90,
    change rate: +-30/+-15/0) with one shared formula:
    alpha = 0.04 + min(|value|/scale, 1) * 0.20, colored PROFIT above zero and
    LOSS below. `scale` is the column's own "full intensity" distance from
    zero (docs/ui.md 1.2: momentum columns share scale=10).

    Returns None for value == 0 (no highlight).
    """
    if value == 0:
        return None
    color = QColor(QC_PROFIT if value > 0 else QC_LOSS)
    alpha = 0.04 + min(abs(value) / scale, 1.0) * 0.20
    color.setAlphaF(alpha)
    return color
