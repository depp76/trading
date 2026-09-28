"""tests/test_ui_smoke.py — build every top-level tab headlessly under the
real app stylesheet, render it, and fail on any Qt stylesheet parse warning.

Qt reports a bad QSS rule only as a runtime qWarning ("Could not parse
stylesheet of object ...") and then silently drops that whole sheet, so a
typo in one widget's setStyleSheet() ships as "this card lost its border"
rather than a test failure. This is the check that would have caught the
RailCard case (roadmap 14차): a literal "}}" plus a four-corner
border-radius shorthand Qt does not support.

Nothing here touches the network or the repo's runtime JSON/DB files: the
tabs' disk loads and background warm-up threads are patched out.
"""
import contextlib
import unittest
from unittest.mock import patch

from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.common import FONT_FAMILY_CSS  # noqa: E402
from ui.theme import app_qss  # noqa: E402


def _no_disk(_path, default=None):
    return default


def _universe(n=40):
    # Index / yield / commodity rows share the table with equities (their
    # change_mode picks the price/change unit).
    rows = [
        {"ticker": "KS11", "name": "KOSPI", "market": "Index", "is_index": True, "price": 2500.12,
         "market_cap": float("inf"), "changes": {"1d": 1.2, "3d": 0.5, "20d": -2.0, "60d": 4.0, "120d": 6.0},
         "change_mode": "pct", "index_order": 1},
        {"ticker": "KR3YT", "name": "KR 3Y", "market": "Index", "is_index": True, "is_bond": True, "price": 3.12,
         "currency": "%", "market_cap": float("inf"), "changes": {"1d": -2.0, "3d": 1.0}, "change_mode": "bp", "index_order": 5},
        {"ticker": "^VIX", "name": "VIX", "market": "Index", "is_index": True, "price": 18.4,
         "market_cap": float("inf"), "changes": {"1d": 0.8, "3d": -1.1}, "change_mode": "abs", "index_order": 6},
        {"ticker": "CL=F", "name": "WTI", "market": "Index", "is_index": True, "price": 88.5, "usd_price": 88.5,
         "currency": "$", "market_cap": float("inf"), "changes": {"1d": -0.7}, "change_mode": "abs", "index_order": 7},
    ]
    for i in range(n):
        rows.append({
            "ticker": f"{100000 + i}", "name": f"Stock{i}", "market": "KOSPI" if i % 2 == 0 else "KOSDAQ",
            "market_cap": 1_000_000_000_000 - i * 1_000_000_000, "is_index": False,
            "trailing_per": 10.0 + i, "price": 50000 + i * 100,
            "changes": {
                "1d": (i % 7) - 3.0, "3d": (i % 9) - 4.0, "20d": (i % 15) - 7.0, "60d": (i % 25) - 12.0,
                "120d": (i % 30) - 15.0, "ma20_div": 100.0 + (i % 10) - 5, "ma50_div": 100.0 + (i % 8) - 4,
                "ma20_roc_1w": (i % 5) - 2.0, "52w_high_diff": -float(i % 40),
                "52w_low": 40000.0, "52w_high": 70000.0,
            },
        })
    return rows


@contextlib.contextmanager
def _capture_qt_messages():
    messages = []
    previous = qInstallMessageHandler(lambda _mode, _ctx, msg: messages.append(msg))
    try:
        yield messages
    finally:
        qInstallMessageHandler(previous)


@contextlib.contextmanager
def _all_tabs_patched():
    # save_asset_records/upsert_trade(s)/delete_trade are stubbed even
    # though this smoke test doesn't currently call any of them -- a save
    # slipping into the real trade_db.portfolio.db from a test is exactly
    # how the live Total Assets snapshots got overwritten with test data
    # once before (roadmap.md 2026-09-20); cheap insurance against a repeat
    # if this fixture grows to cover an editing action later.
    with patch("ui.universe_tab.safe_load_json", side_effect=_no_disk), \
         patch("ui.assets_tab.trade_db.load_asset_records", return_value=[]), \
         patch("ui.assets_tab.trade_db.save_asset_records"), \
         patch("ui.assets_tab.TradingRecordTab._start_metrics_preload"), \
         patch("ui.assets_tab.TradingRecordTab._schedule_daily_sync"), \
         patch("trade_db.get_open_trades", return_value=[]), \
         patch("trade_db.load_all_trades", return_value=[]), \
         patch("trade_db.upsert_trade"), \
         patch("trade_db.upsert_trades"), \
         patch("trade_db.delete_trade"):
        yield


class TestEveryTabBuildsUnderTheAppStylesheet(unittest.TestCase):

    def setUp(self):
        app.setStyleSheet(app_qss(FONT_FAMILY_CSS))

    def tearDown(self):
        app.setStyleSheet("")

    def _build_all(self):
        from ui.universe_tab import UniverseTab
        from ui.history_tab import TradingHistoryTab
        from ui.assets_tab import TradingRecordTab
        from ui.strategy_tab import StrategyTab
        u = UniverseTab()
        h = TradingHistoryTab()
        a = TradingRecordTab()
        s = StrategyTab()
        return u, h, a, s

    def test_no_stylesheet_parse_warnings_and_everything_renders(self):
        with _capture_qt_messages() as messages, _all_tabs_patched():
            u, h, a, s = self._build_all()
            u.all_data = _universe()
            u._reload_table()            # exercises every StockTable cell type, index rows included
            u.filter_table()
            for tab in (u, h, a, s):
                tab.resize(1400, 800)
                tab.show()
                app.processEvents()
                pm = QPixmap(1400, 800)
                tab.render(pm)
            h._settings_save_timer.stop()

        qss_warnings = [m for m in messages if "stylesheet" in m.lower()]
        self.assertEqual(qss_warnings, [])


if __name__ == "__main__":
    unittest.main()
