"""data - Pure data-access layer: market data, caches, and indicator computation.

Sub-modules: cache, indicators, market, collectors.{naver,yahoo,kis,krx}. Import them
directly (`from data.market import ...`); the one-stop re-export for UI/thread code is
`data_fetcher.py`. Trading-strategy code lives in the top-level `strategy` package and
imports from here; this package never imports `strategy` (rebalance.md 11-5).
"""
