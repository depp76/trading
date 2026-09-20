"""ui/dialogs/trade_edit.py — Trade entry/edit dialogs for TradingHistoryTab (buy side, sell side, new trade).

Split out of the former single ui/dialogs.py (2026-09-17)."""
import logging
import datetime as _dt
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QFormLayout,
    QPushButton, QLineEdit, QComboBox, QMessageBox,
    QApplication,
)
from PyQt6.QtCore import Qt


logger = logging.getLogger(__name__)

from ui.common import (
    _fmt_num_edit,
    _validate_date_str,
    _normalize_date_str,
    _validate_positive_number,
    _mk_field_validator,
)
from threads.fetch_threads import TickerValidateThread


# ---------------------------------------------------------------------------
# BuyEditDialog — Edit buy details in TradingHistoryTab
# ---------------------------------------------------------------------------
class BuyEditDialog(QDialog):
    """Dialog for editing buy details in TradingHistoryTab."""
    def __init__(self, current_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Buy Information")
        self.setMinimumWidth(300)
        self.result_data = None

        layout = QFormLayout(self)

        self.buy_date_edit = QLineEdit(current_data.get("buy_date", ""))
        self.buy_date_edit.setPlaceholderText("YYYY-MM-DD")

        b_price = current_data.get("buy_price", 0.0)
        self.buy_price_edit = QLineEdit(f"{b_price:,.2f}" if b_price else "")

        b_qty = current_data.get("qty", 0.0)
        self.buy_qty_edit = QLineEdit(f"{b_qty:,.0f}" if b_qty else "")

        b_amt = current_data.get("buy_amount", 0.0)
        self.buy_amount_edit = QLineEdit(f"{b_amt:,.2f}" if b_amt else "")
        self.buy_amount_edit.setPlaceholderText("Auto-calculated if empty")

        layout.addRow("Buy Date:", self.buy_date_edit)
        layout.addRow("Buy Price:", self.buy_price_edit)
        layout.addRow("Quantity:", self.buy_qty_edit)
        layout.addRow("Buy Amount:", self.buy_amount_edit)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn.setObjectName("primary")
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)

        # ---1,000-separator auto-format ---
        self.buy_price_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_price_edit, t, decimal=True))
        self.buy_qty_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_qty_edit, t))
        self.buy_amount_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_amount_edit, t, decimal=True))

        # ---Real-time validation: red border + tooltip on bad input (roadmap 2-5) ---
        self._val_date = _mk_field_validator(
            self.buy_date_edit, _validate_date_str, "Invalid date format (YYYY-MM-DD)")
        self._val_price = _mk_field_validator(
            self.buy_price_edit, _validate_positive_number, "Enter a number greater than 0")
        self._val_qty = _mk_field_validator(
            self.buy_qty_edit, _validate_positive_number, "Enter a number greater than 0")
        self.buy_date_edit.textChanged.connect(self._val_date)
        self.buy_price_edit.textChanged.connect(self._val_price)
        self.buy_qty_edit.textChanged.connect(self._val_qty)
        # Validate pre-filled values immediately so existing bad data is flagged on open.
        self._val_date(); self._val_price(); self._val_qty()

        layout.addRow(btn_box)

    def on_save(self):
        date_ok = self._val_date()
        price_ok = self._val_price()
        qty_ok = self._val_qty()
        if not (date_ok and price_ok and qty_ok):
            QMessageBox.warning(self, "Input Error", "Please check the highlighted fields (shown with a red border).")
            return

        def to_f(val):
            try: return float(val.replace(',', '').replace('%', '').strip())
            except Exception: return 0.0

        b_price = to_f(self.buy_price_edit.text())
        b_qty = to_f(self.buy_qty_edit.text())
        b_amt = to_f(self.buy_amount_edit.text())

        self.result_data = {
            "buy_date": _normalize_date_str(self.buy_date_edit.text()),
            "buy_price": b_price,
            "qty": b_qty,
            "buy_amount": b_amt if b_amt > 0 else b_price * b_qty,
        }
        self.accept()


# ---------------------------------------------------------------------------
# SellEditDialog — Edit sell details in TradingHistoryTab
# ---------------------------------------------------------------------------
class SellEditDialog(QDialog):
    """Dialog for editing sell details in TradingHistoryTab."""
    def __init__(self, current_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Sell Information")
        self.setMinimumWidth(300)
        self.result_data = None
        self._buy_date_str = current_data.get("buy_date", "")
        self._orig_qty = float(current_data.get("qty", 0.0))

        layout = QFormLayout(self)

        self.sell_date_edit = QLineEdit(current_data.get("sell_date", ""))
        self.sell_date_edit.setPlaceholderText("YYYY-MM-DD (Leave empty if Open)")

        s_price = current_data.get("sell_price", 0.0)
        self.sell_price_edit = QLineEdit(f"{s_price:,.0f}" if s_price else "")

        s_qty = current_data.get("sell_qty", 0.0)
        self.sell_qty_edit = QLineEdit(f"{s_qty:,.0f}" if s_qty else "")

        s_amt = current_data.get("sell_amount", 0.0)
        self.sell_amount_edit = QLineEdit(f"{s_amt:,.0f}" if s_amt else "")
        self.sell_amount_edit.setPlaceholderText("Auto-calculated if empty")

        layout.addRow("Sell Date:", self.sell_date_edit)
        layout.addRow("Sell Price:", self.sell_price_edit)
        layout.addRow("Sell Quantity:", self.sell_qty_edit)
        layout.addRow("Sell Amount:", self.sell_amount_edit)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn.setObjectName("primary")
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)

        # ---1,000-separator auto-format ---
        self.sell_price_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.sell_price_edit, t))
        self.sell_qty_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.sell_qty_edit, t))
        self.sell_amount_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.sell_amount_edit, t))

        # ---Real-time validation (roadmap 2-5) ---
        # sell_date may be left empty (position stays open); price/qty are only
        # required once a sell_date is actually entered.
        def _validate_sell_date(text: str) -> bool:
            text = text.strip()
            return True if not text else _validate_date_str(text)

        def _validate_sell_amount_field(text: str) -> bool:
            text = text.replace(",", "").replace("%", "").strip()
            required = bool(self.sell_date_edit.text().strip())
            if not text:
                return not required
            try:
                return float(text) > 0
            except ValueError:
                return False

        self._val_sell_date = _mk_field_validator(
            self.sell_date_edit, _validate_sell_date, "Invalid date format (YYYY-MM-DD)")
        self._val_sell_price = _mk_field_validator(
            self.sell_price_edit, _validate_sell_amount_field, "A number greater than 0 is required when a sell date is entered")
        self._val_sell_qty = _mk_field_validator(
            self.sell_qty_edit, _validate_sell_amount_field, "A number greater than 0 is required when a sell date is entered")
        self.sell_date_edit.textChanged.connect(self._val_sell_date)
        self.sell_date_edit.textChanged.connect(self._val_sell_price)
        self.sell_date_edit.textChanged.connect(self._val_sell_qty)
        self.sell_price_edit.textChanged.connect(self._val_sell_price)
        self.sell_qty_edit.textChanged.connect(self._val_sell_qty)
        self._val_sell_date(); self._val_sell_price(); self._val_sell_qty()

        layout.addRow(btn_box)

    def on_save(self):
        date_ok = self._val_sell_date()
        price_ok = self._val_sell_price()
        qty_ok = self._val_sell_qty()
        if not (date_ok and price_ok and qty_ok):
            QMessageBox.warning(self, "Input Error", "Please check the highlighted fields (shown with a red border).")
            return

        sell_date_str = _normalize_date_str(self.sell_date_edit.text())
        if sell_date_str and self._buy_date_str:
            try:
                buy_d = _dt.datetime.strptime(self._buy_date_str, "%Y-%m-%d").date()
                sell_d = _dt.datetime.strptime(sell_date_str, "%Y-%m-%d").date()
                if buy_d > sell_d:
                    QMessageBox.warning(
                        self, "Input Error",
                        f"Buy date ({self._buy_date_str}) cannot be later than sell date ({sell_date_str}).",
                    )
                    return
            except ValueError:
                pass  # buy_date on the record predates this validation; skip the cross-check

        def to_f(val):
            try: return float(val.replace(',', '').replace('%', '').strip())
            except Exception: return 0.0

        s_price = to_f(self.sell_price_edit.text())
        s_qty = to_f(self.sell_qty_edit.text())
        s_amt = to_f(self.sell_amount_edit.text())

        # A sell quantity beyond what was actually bought bypasses the
        # partial-sell proration in history_calc.compute_pl_fields (its
        # s_qty < b_qty branch), so pl gets computed as the full sell_amount
        # minus the full buy_amount -- inflating realized P/L with proceeds
        # from shares that were never owned (review_agy.md #2).
        if self._orig_qty > 0 and s_qty > self._orig_qty:
            QMessageBox.warning(
                self, "Input Error",
                f"Sell quantity ({s_qty:,.0f}) cannot exceed the holding quantity ({self._orig_qty:,.0f}).",
            )
            return

        self.result_data = {
            "sell_date": sell_date_str,
            "sell_price": s_price,
            "sell_qty": s_qty,
            "sell_amount": s_amt if s_amt > 0 else s_price * s_qty,
        }
        self.accept()


# ---------------------------------------------------------------------------
# TradeEntryDialog — Enter a new trade into TradingHistoryTab
# ---------------------------------------------------------------------------
class TradeEntryDialog(QDialog):
    """Dialog for entering a new trade into TradingHistoryTab."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Trade")
        self.setMinimumWidth(300)
        self.result_data = None
        self._validate_thread = None

        layout = QFormLayout(self)

        self.market_combo = QComboBox()
        self.market_combo.addItems(["KOSPI", "KOSDAQ", "NASDAQ", "NASDAQ 100", "NYSE", "AMEX"])

        self.ticker_edit = QLineEdit()
        self.ticker_edit.setPlaceholderText("Enter Ticker (e.g. QQQM)")

        self.buy_date_edit = QLineEdit(datetime.now().strftime("%Y-%m-%d"))
        self.buy_date_edit.setPlaceholderText("YYYY-MM-DD")
        self.buy_price_edit = QLineEdit()
        self.qty_edit = QLineEdit()
        self.buy_amount_edit = QLineEdit()
        self.buy_amount_edit.setPlaceholderText("Auto-calculated if empty")

        layout.addRow("Market:", self.market_combo)
        layout.addRow("Ticker:", self.ticker_edit)
        layout.addRow("Buy Date:", self.buy_date_edit)
        layout.addRow("Buy Price:", self.buy_price_edit)
        layout.addRow("Buy Quantity:", self.qty_edit)
        layout.addRow("Buy Amount:", self.buy_amount_edit)

        # ---1,000-separator auto-format ---
        self.buy_price_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_price_edit, t, decimal=True))
        self.qty_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.qty_edit, t))
        self.buy_amount_edit.textEdited.connect(
            lambda t: _fmt_num_edit(self.buy_amount_edit, t, decimal=True))

        # ---Real-time validation (roadmap 2-5) ---
        self._val_date = _mk_field_validator(
            self.buy_date_edit, _validate_date_str, "Invalid date format (YYYY-MM-DD)")
        self._val_price = _mk_field_validator(
            self.buy_price_edit, _validate_positive_number, "Enter a number greater than 0")
        self._val_qty = _mk_field_validator(
            self.qty_edit, _validate_positive_number, "Enter a number greater than 0")
        self.buy_date_edit.textChanged.connect(self._val_date)
        self.buy_price_edit.textChanged.connect(self._val_price)
        self.qty_edit.textChanged.connect(self._val_qty)
        self._val_date(); self._val_price(); self._val_qty()

        btn_box = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.on_save)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        self.save_btn.setObjectName("primary")
        btn_box.addWidget(self.save_btn)
        btn_box.addWidget(self.cancel_btn)

        layout.addRow(btn_box)

    def on_save(self):
        market = self.market_combo.currentText()
        ticker = self.ticker_edit.text().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Error", "Ticker is required.")
            return

        date_ok = self._val_date()
        price_ok = self._val_price()
        qty_ok = self._val_qty()
        if not (date_ok and price_ok and qty_ok):
            QMessageBox.warning(self, "Input Error", "Please check the highlighted fields (shown with a red border).")
            return

        # Ticker validation hits the network (fetch_single_stock -> yahooquery ->
        # Naver fallback chain), so it runs on TickerValidateThread instead of
        # blocking the UI thread here. Buttons stay disabled and closeEvent()
        # ignores close requests until _on_ticker_validated() fires, so the
        # dialog can't be destroyed while the thread still holds a connection to it.
        self.save_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        thread = TickerValidateThread(market, ticker)
        thread.finished.connect(self._on_ticker_validated)
        self._validate_thread = thread
        thread.start()

    def _on_ticker_validated(self, is_valid: bool, company: str, err: str):
        QApplication.restoreOverrideCursor()
        self.save_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)

        if not is_valid:
            ticker = self.ticker_edit.text().strip().upper()
            QMessageBox.warning(
                self, "Input Error",
                f"Ticker is not valid (Could not find stock information):\n{ticker}\n\nDetails: {err or ''}",
            )
            return

        def to_f(val):
            try: return float(val.replace(',', '').replace('%', '').strip())
            except Exception: return 0.0

        b_price = to_f(self.buy_price_edit.text())
        qty = to_f(self.qty_edit.text())
        b_amt = to_f(self.buy_amount_edit.text())

        self.result_data = {
            "market": self.market_combo.currentText(),
            "ticker": self.ticker_edit.text().strip().upper(),
            "company": company,
            "buy_date": _normalize_date_str(self.buy_date_edit.text()),
            "buy_price": b_price,
            "qty": qty,
            "buy_amount": b_amt if b_amt > 0 else b_price * qty,
            "sell_date": "",
            "sell_price": 0.0,
            "sell_qty": 0.0,
            "sell_amount": 0.0,
        }
        self.accept()

    def closeEvent(self, event):
        """Block window-close (title-bar X, Alt+F4, ...) while a validation
        thread is still running -- see the comment in on_save()."""
        if self._validate_thread is not None and self._validate_thread.isRunning():
            event.ignore()
            return
        super().closeEvent(event)
