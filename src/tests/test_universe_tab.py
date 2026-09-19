"""tests/test_universe_tab.py — UniverseTab behaviour that does not need the
network: the startup re-fetch of user-added tickers renders once per batch."""
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])


def _no_disk(_path, default=None):
    return default


def _stock(ticker, cap):
    return {"ticker": ticker, "name": f"S{ticker}", "market": "KOSPI", "market_cap": cap, "price": 1.0, "changes": {}}


class TestStartupAddedTickersRenderOnce(unittest.TestCase):

    def _tab(self):
        from ui.universe_tab import UniverseTab
        with patch("ui.universe_tab.safe_load_json", side_effect=_no_disk):
            tab = UniverseTab()
        tab.custom_settings = {"added": [], "deleted": []}
        return tab

    def test_startup_arrivals_are_batched_into_one_render(self):
        tab = self._tab()
        with patch.object(tab, "load_custom_settings"), patch.object(tab, "save_custom_settings"), \
             patch.object(tab, "_reload_table") as reload, patch.object(tab, "filter_table") as filt:
            for t, cap in (("100001", 5), ("100002", 50), ("100003", 20)):
                tab._handle_single_stock_loaded(_stock(t, cap), "", is_startup=True, ticker_hint=t)
            # Nothing rendered yet; the single-shot timer is armed instead.
            self.assertEqual(reload.call_count, 0)
            self.assertTrue(tab._startup_render_timer.isActive())

            tab._startup_render_timer.stop()
            tab._render_startup_batch()
            self.assertEqual(reload.call_count, 1)
            self.assertEqual(filt.call_count, 1)
        # Sorted by market cap descending, like on_finished_all.
        self.assertEqual([d["ticker"] for d in tab.all_data], ["100002", "100003", "100001"])

    def test_user_add_still_renders_immediately(self):
        tab = self._tab()
        with patch.object(tab, "load_custom_settings"), patch.object(tab, "save_custom_settings"), \
             patch.object(tab, "_reload_table") as reload, patch.object(tab, "filter_table"), \
             patch.object(tab, "update_total_status"):
            tab._handle_single_stock_loaded(_stock("100009", 1), "", is_startup=False, ticker_hint="100009")
            self.assertEqual(reload.call_count, 1)
            self.assertFalse(tab._startup_render_timer.isActive())


if __name__ == "__main__":
    unittest.main()
