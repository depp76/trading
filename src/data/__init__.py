"""data - Pure data-access layer: market data, caches, and indicator computation.

Sub-modules: cache, indicators, market, collectors.{naver,yahoo,kis,krx}. Import them
directly (`from data.market import ...`); the one-stop re-export for UI/thread code is
`data_fetcher.py`. This package holds data access only: trading-strategy code (removed
on 2026-09-28, to be developed and validated later) is layered on top of it and must never
be imported from here.
"""
