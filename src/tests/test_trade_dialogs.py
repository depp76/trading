"""tests/test_trade_dialogs.py — BuyEditDialog / SellEditDialog / TradeEntryDialog
input validation and the amount = price x qty fallback, headless."""
import unittest
from unittest.mock import patch, MagicMock

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.dialogs.trade_edit import BuyEditDialog, SellEditDialog, TradeEntryDialog  # noqa: E402


class TestBuyEditDialog(unittest.TestCase):

    def test_blank_amount_falls_back_to_price_times_qty(self):
        dlg = BuyEditDialog({"buy_date": "2026-1-5", "buy_price": 70000.0, "qty": 10.0, "buy_amount": 0.0})
        dlg.buy_amount_edit.setText("")
        dlg.on_save()
        self.assertEqual(dlg.result_data["buy_amount"], 700000.0)
        self.assertEqual(dlg.result_data["buy_date"], "2026-01-05")   # normalised, zero-padded

    def test_explicit_amount_wins(self):
        dlg = BuyEditDialog({"buy_date": "2026-01-05", "buy_price": 70000.0, "qty": 10.0, "buy_amount": 0.0})
        dlg.buy_amount_edit.setText("699,500")
        dlg.on_save()
        self.assertEqual(dlg.result_data["buy_amount"], 699500.0)

    def test_invalid_field_blocks_save(self):
        dlg = BuyEditDialog({"buy_date": "not-a-date", "buy_price": 70000.0, "qty": 10.0})
        with patch("ui.dialogs.trade_edit.QMessageBox.warning") as warn:
            dlg.on_save()
        warn.assert_called_once()
        self.assertIsNone(dlg.result_data)


class TestSellEditDialog(unittest.TestCase):

    def test_blank_amount_falls_back_to_price_times_qty(self):
        dlg = SellEditDialog({"buy_date": "2026-01-05", "sell_date": "2026-02-05", "sell_price": 75000.0, "sell_qty": 10.0})
        dlg.sell_amount_edit.setText("")
        dlg.on_save()
        self.assertEqual(dlg.result_data["sell_amount"], 750000.0)

    def test_sell_before_buy_is_rejected(self):
        dlg = SellEditDialog({"buy_date": "2026-03-01", "sell_date": "2026-02-05", "sell_price": 75000.0, "sell_qty": 10.0})
        with patch("ui.dialogs.trade_edit.QMessageBox.warning") as warn:
            dlg.on_save()
        warn.assert_called_once()
        self.assertIsNone(dlg.result_data)

    def test_empty_sell_date_keeps_the_position_open(self):
        dlg = SellEditDialog({"buy_date": "2026-01-05"})
        dlg.on_save()
        self.assertEqual(dlg.result_data, {"sell_date": "", "sell_price": 0.0, "sell_qty": 0.0, "sell_amount": 0.0})

    def test_price_required_once_a_sell_date_is_entered(self):
        dlg = SellEditDialog({"buy_date": "2026-01-05"})
        dlg.sell_date_edit.setText("2026-02-05")
        with patch("ui.dialogs.trade_edit.QMessageBox.warning") as warn:
            dlg.on_save()
        warn.assert_called_once()
        self.assertIsNone(dlg.result_data)


class TestTradeEntryDialog(unittest.TestCase):

    def _dlg(self):
        dlg = TradeEntryDialog()
        dlg.ticker_edit.setText("005930")
        dlg.buy_date_edit.setText("2026-01-05")
        dlg.buy_price_edit.setText("70,000")
        dlg.qty_edit.setText("10")
        return dlg

    def test_missing_ticker_is_rejected_before_validation(self):
        dlg = self._dlg()
        dlg.ticker_edit.setText("")
        with patch("ui.dialogs.trade_edit.QMessageBox.warning") as warn, \
             patch("ui.dialogs.trade_edit.TickerValidateThread") as thread_cls:
            dlg.on_save()
        warn.assert_called_once()
        thread_cls.assert_not_called()

    def test_save_validates_the_ticker_off_thread_then_fills_the_record(self):
        dlg = self._dlg()
        with patch("ui.dialogs.trade_edit.TickerValidateThread") as thread_cls:
            thread_cls.return_value = MagicMock()
            dlg.on_save()
            thread_cls.assert_called_once_with("KOSPI", "005930")
            self.assertTrue(thread_cls.return_value.start.called)
            self.assertFalse(dlg.save_btn.isEnabled())   # locked while the lookup runs

        dlg._on_ticker_validated(True, "Samsung Electronics", "")
        self.assertTrue(dlg.save_btn.isEnabled())
        r = dlg.result_data
        self.assertEqual((r["ticker"], r["company"], r["market"]), ("005930", "Samsung Electronics", "KOSPI"))
        self.assertEqual(r["buy_amount"], 700000.0)      # blank amount -> price x qty
        self.assertEqual(r["sell_date"], "")

    def test_unknown_ticker_keeps_the_dialog_open(self):
        dlg = self._dlg()
        with patch("ui.dialogs.trade_edit.QMessageBox.warning") as warn:
            dlg._on_ticker_validated(False, "", "not found")
        warn.assert_called_once()
        self.assertIsNone(dlg.result_data)
        self.assertTrue(dlg.save_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
