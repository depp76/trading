"""threads/realtime.py — RealtimePriceThread (Phase 3-1 분리)

분리 출처: main.py L176-198 (2026-08-29 feat/3-1-modularize)
"""
from PyQt6.QtCore import QThread, pyqtSignal


class RealtimePriceThread(QThread):
    prices_fetched = pyqtSignal(dict)
    status_message = pyqtSignal(str)  # short progress text for MainWindow's status bar

    def __init__(self, kr_tickers, us_tickers):
        super().__init__()
        self.kr_tickers = kr_tickers
        self.us_tickers = us_tickers

    def run(self):
        prices = {}
        if self.kr_tickers:
            self.status_message.emit(f"국내 시세 조회 중… (Naver, {len(self.kr_tickers)}종목)")
            from data_fetcher import fetch_naver_realtime_prices
            prices.update(fetch_naver_realtime_prices(self.kr_tickers))
        if self.us_tickers:
            self.status_message.emit("Yahoo Finance 응답 대기 중…")
            from data_fetcher import fetch_us_realtime_prices, get_usd_krw_rate
            fx_rate = get_usd_krw_rate()
            us_prices = fetch_us_realtime_prices(self.us_tickers)
            for ticker, price in us_prices.items():
                prices[ticker] = price * fx_rate
        self.prices_fetched.emit(prices)
