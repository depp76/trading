"""ui/dialogs — Dialog windows, one module per dialog group (split from the former
single ui/dialogs.py on 2026-09-17). Import from this package, e.g.
`from ui.dialogs import StockMaDialog`."""
from ui.dialogs.index_ma import IndexMaDialog
from ui.dialogs.stock_ma import StockMaDialog
from ui.dialogs.trade_edit import BuyEditDialog, SellEditDialog, TradeEntryDialog
from ui.dialogs.trade_history import StockTradeHistoryDialog
from ui.dialogs.assets_graph import TotalAssetsGraphDialog
from ui.dialogs.backtest_result import BacktestResultDialog

__all__ = [
    "IndexMaDialog", "StockMaDialog",
    "BuyEditDialog", "SellEditDialog", "TradeEntryDialog",
    "StockTradeHistoryDialog", "TotalAssetsGraphDialog", "BacktestResultDialog",
]
