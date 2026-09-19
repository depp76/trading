"""threads/fetch_threads.py — Background QThread classes (Phase 3-1 split)

Split out from: main.py (2026-08-29 feat/3-1-modularize)
Contains:
  IndexMaThread, StockMaThread,
  SingleStockFetchThread, AllDataFetchThread,
  UniverseLightweightFetchThread, PositionPriceFetchThread,
  AutoBackupThread, RebalanceBacktestThread, TrendFollowingBacktestThread,
  TrendFollowingPortfolioThread,
  GeminiFilterThread, GeminiDiagnosisThread, AccountDepositThread
"""
import os
import shutil
import datetime as _dt
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

from paths import BASE_DIR, DB_FILE, CUSTOM_SETTINGS_FILE, ARCHIVE_DIR
from data_fetcher import (
    fetch_market_data,
    fetch_single_stock,
    fetch_all_indices_mas,
    fetch_stock_ma_multi,
    _fetch_naver_info,
    get_usd_krw_rate,
    get_historical_data,
    start_date,
    INDEX_TICKERS,
)

logger = logging.getLogger(__name__)

_AUTO_BACKUP_FILES = [DB_FILE, CUSTOM_SETTINGS_FILE]
_AUTO_BACKUP_MAX_KEEP = 7  # keep only the most recent N automatic backups


# ---------------------------------------------------------------------------
# Index MA20 fetch thread
# ---------------------------------------------------------------------------
class IndexMaThread(QThread):
    finished = pyqtSignal(object)  # dict {label: (df|None, err)}

    def run(self):
        results = fetch_all_indices_mas()
        self.finished.emit(results)


# ---------------------------------------------------------------------------
# Stock MA fetch thread
# ---------------------------------------------------------------------------
class StockMaThread(QThread):
    """Background thread: fetch MA20 + MA50 for a single stock in one call."""
    # ticker, name, df|None, error, investor_data, market, change_mode -- the last
    # two echo the constructor args so slots can be plain bound methods instead of
    # closures capturing per-request context (CLAUDE.md threads rule).
    finished = pyqtSignal(str, str, object, str, list, str, str)

    def __init__(self, ticker, name, market, change_mode='pct'):
        super().__init__()
        self.ticker = ticker
        self.name = name
        self.market = market
        self.change_mode = change_mode

    def run(self):
        df, err = fetch_stock_ma_multi(self.ticker, self.market, windows=(5, 10, 20, 60))

        # --- Add Equal Weight Index for KOSPI ---
        if self.ticker in ("KS11", "^KS11") and df is not None and not df.is_empty():
            try:
                import FinanceDataReader as fdr
                import polars as pl
                from data_fetcher import _to_polars
                start_date = df.get_column("Date")[0].strftime("%Y-%m-%d")
                ew_pd = fdr.DataReader("252650", start_date)
                if not ew_pd.empty:
                    ew_df = _to_polars(ew_pd)
                    if not ew_df.is_empty() and "Close" in ew_df.columns:
                        ew_df = ew_df.select([pl.col("Date"), pl.col("Close").alias("EqualWeight")])
                        df = df.join(ew_df, on="Date", how="left")
            except Exception as e:
                logger.warning("Error fetching KODEX 200 EW: %s", e, exc_info=True)
        # ----------------------------------------

        investor_data = []
        if self.market in ("KOSPI", "KOSDAQ") or (self.market == "Index" and self.ticker in ("KS11", "KQ11", "^KS11", "^KQ11")):
            from data_fetcher import fetch_investor_trend
            investor_data = fetch_investor_trend(self.ticker, days=60)
        elif self.ticker == "CL=F":
            from data_fetcher import fetch_wti_futures_curve
            investor_data = fetch_wti_futures_curve()
        self.finished.emit(self.ticker, self.name, df, err or "", investor_data, self.market, self.change_mode)


# ---------------------------------------------------------------------------
# Single stock fetch thread
# ---------------------------------------------------------------------------
class SingleStockFetchThread(QThread):
    """Background thread to fetch a single stock without blocking the UI.

    finished(result | None, error, ticker): the requested ticker is echoed so
    callers that fan out several lookups can tell the results apart in a bound
    method slot."""
    finished = pyqtSignal(object, str, str)

    def __init__(self, market, ticker):
        super().__init__()
        self.market = market
        self.ticker = ticker

    def run(self):
        result, error = fetch_single_stock(self.market, self.ticker)
        self.finished.emit(result, error or "", self.ticker)


# ---------------------------------------------------------------------------
# Ticker validation thread (TradeEntryDialog)
# ---------------------------------------------------------------------------
class TickerValidateThread(QThread):
    """Resolve a ticker's display name for TradeEntryDialog without blocking the UI.

    Tries fetch_single_stock first, then falls back to yahooquery, then (for
    KOSPI/KOSDAQ) Naver — the same three-source chain TradeEntryDialog.on_save()
    used to run synchronously on the UI thread.
    """
    finished = pyqtSignal(bool, str, str)  # is_valid, company, error

    def __init__(self, market, ticker):
        super().__init__()
        self.market = market
        self.ticker = ticker

    def run(self):
        market, ticker = self.market, self.ticker
        is_valid = False
        company = ticker
        err = ""
        try:
            res, err = fetch_single_stock(market, ticker)
            if res is not None:
                is_valid = True
                company = res.get("name", ticker)

            if not is_valid or company == ticker or company.upper() == ticker.upper():
                try:
                    from yahooquery import Ticker as YQTicker
                    yf_sym = ticker
                    if market == "KOSPI":
                        yf_sym = f"{ticker}.KS"
                    elif market == "KOSDAQ":
                        yf_sym = f"{ticker}.KQ"
                    elif "." in ticker:
                        yf_sym = ticker.replace(".", "-")

                    qt = YQTicker(yf_sym).quote_type
                    if qt and isinstance(qt, dict) and yf_sym in qt and isinstance(qt[yf_sym], dict):
                        fetched = qt[yf_sym].get('longName') or qt[yf_sym].get('shortName')
                        if fetched:
                            company = fetched
                            is_valid = True
                except Exception:
                    logger.debug("yahooquery company-name lookup failed for ticker=%s", ticker, exc_info=True)

            if (not is_valid or company == ticker or company.upper() == ticker.upper()) and market in ("KOSPI", "KOSDAQ"):
                try:
                    n_nv, _ = _fetch_naver_info(ticker)
                    if n_nv:
                        company = n_nv
                        is_valid = True
                except Exception:
                    logger.debug("Naver company-name lookup failed for ticker=%s", ticker, exc_info=True)
        except Exception as e:
            err = str(e)

        self.finished.emit(is_valid, company, err or "")


# ---------------------------------------------------------------------------
# Asset-tab metrics preload thread (USD/KRW + KOSPI history warm-up)
# ---------------------------------------------------------------------------
class AssetMetricsPreloadThread(QThread):
    """Warms TradingRecordTab's USD/KRW-rate and KOSPI-close caches off the UI thread.

    get_usd_krw_rate_for_date()/get_index_close_for_date() each trigger a full
    network fetch (fdr.DataReader('USD/KRW') / get_historical_data("KS11", ...))
    on their first call per session (or once their cache goes stale), and are
    cheap polars lookups after that — calling the same underlying fetchers here
    just pays that one-time cost in the background instead of freezing the
    window at tab construction.
    """
    finished = pyqtSignal()

    def run(self):
        try:
            get_usd_krw_rate()
        except Exception:
            logger.debug("AssetMetricsPreloadThread: USD/KRW warm-up failed", exc_info=True)
        try:
            get_historical_data("KS11", start_date())
        except Exception:
            logger.debug("AssetMetricsPreloadThread: KOSPI warm-up failed", exc_info=True)
        self.finished.emit()


# ---------------------------------------------------------------------------
# All-market data fetch thread (full load)
# ---------------------------------------------------------------------------
class AllDataFetchThread(QThread):
    market_loaded = pyqtSignal(str, list)
    market_progress = pyqtSignal(str, int, int)
    finished_all = pyqtSignal(list)

    def run(self):
        from data_fetcher import fetch_major_indices_as_stocks

        all_data = []
        try:
            # ---Step 1: Fetch indices first (fast, one concurrent call per index) ---
            try:
                self.market_progress.emit("Indices", 0, len(INDEX_TICKERS))
                indices_data = fetch_major_indices_as_stocks()
                self.market_loaded.emit("Indices", indices_data)
                all_data.extend(indices_data)
            except Exception as e:
                logger.warning("Error fetching indices: %s", e, exc_info=True)

            # ---Step 2: Fetch all 4 markets IN PARALLEL ---
            markets_config = [
                ("KOSPI",  300),
                ("KOSDAQ", 150),
                # ("NASDAQ 100", 150),
                # ("S&P500", 510),
            ]

            for market, top_n in markets_config:
                self.market_progress.emit(market, 0, top_n)

            def fetch_market_task(market, top_n):
                def progress_cb(current, total, m=market):
                    self.market_progress.emit(m, current, total)
                return market, fetch_market_data(market, top_n, progress_cb)

            market_results = {}
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(fetch_market_task, m, n): m
                    for m, n in markets_config
                }
                for future in as_completed(futures):
                    try:
                        market, data = future.result()
                        market_results[market] = data
                        self.market_loaded.emit(market, data)
                    except Exception as e:
                        market = futures[future]
                        logger.warning("Error fetching %s: %s", market, e, exc_info=True)
                        market_results[market] = []

            for market, _ in markets_config:
                all_data.extend(market_results.get(market, []))
            del market_results  # release the intermediate dict to free memory
        except Exception as e:
            logger.warning("Error inside AllDataFetchThread.run: %s", e, exc_info=True)
        finally:
            self.finished_all.emit(all_data)


# ---------------------------------------------------------------------------
# Lightweight universe refresh thread (periodic 60-s tick)
# ---------------------------------------------------------------------------
class UniverseLightweightFetchThread(QThread):
    finished_all = pyqtSignal(list)
    status_message = pyqtSignal(str)  # short progress text for MainWindow's status bar

    def __init__(self, current_data: list):
        super().__init__()
        # Shallow per-item copy is enough: run() only reassigns top-level keys
        # (price/changes/usd_price) on each item, never mutates nested values
        # in place — a full deepcopy of ~hundreds of nested "changes" dicts
        # every 60s tick is unnecessary work.
        self.current_data = [dict(item) for item in current_data]

    def run(self):
        from data_fetcher import (
            fetch_naver_realtime_prices,
            fetch_naver_realtime_index_prices,
            fetch_us_realtime_prices,
            fetch_historical_changes,
        )

        try:
            kr_tickers = []
            us_yf_tickers = []
            kr_index_tickers = []
            for item in self.current_data:
                ticker = item.get("ticker", "")
                if not ticker:
                    continue

                if item.get("is_index", False):
                    if ticker in ("^KS11", "KS11", "KOSPI", "^KQ11", "KQ11", "KOSDAQ"):
                        kr_index_tickers.append(ticker)
                    elif ticker.startswith("^") or ticker == "CL=F":
                        us_yf_tickers.append(ticker)
                elif item.get("market") in ("KOSPI", "KOSDAQ"):
                    kr_tickers.append(ticker)
                else:
                    us_yf_tickers.append(ticker)

            prices_dict = {}
            if kr_tickers:
                self.status_message.emit(f"Fetching KR quotes... (Naver, {len(kr_tickers)} tickers)")
                prices_dict.update(fetch_naver_realtime_prices(kr_tickers))
            if kr_index_tickers:
                prices_dict.update(fetch_naver_realtime_index_prices(kr_index_tickers))
            if us_yf_tickers:
                self.status_message.emit("Waiting for Yahoo Finance response...")
                prices_dict.update(fetch_us_realtime_prices(us_yf_tickers))

            updated_count = 0
            for item in self.current_data:
                ticker = item.get("ticker", "")
                if not ticker or ticker not in prices_dict:
                    continue

                new_price = prices_dict[ticker]
                if item.get("price", 0.0) != new_price:
                    item["price"] = new_price
                    try:
                        chg_mode = item.get("change_mode", "pct")
                        chg = fetch_historical_changes(ticker, new_price, mode=chg_mode)
                        item["changes"] = chg
                        if item.get("is_index", False):
                            item["usd_price"] = new_price
                        updated_count += 1
                    except Exception:
                        logger.warning(
                            "UniverseLightweightFetchThread: change-rate fetch failed for ticker=%s",
                            ticker, exc_info=True,
                        )
        except Exception as e:
            logger.warning("Error in UniverseLightweightFetchThread: %s", e, exc_info=True)
        finally:
            self.finished_all.emit(self.current_data)


# ---------------------------------------------------------------------------
# Position price fetch thread (Trading History tab)
# ---------------------------------------------------------------------------
class PositionPriceFetchThread(QThread):
    """
    Background thread: resolves company names to KRX codes,
    fetches real-time prices from Naver Finance, and emits P/L results.

    Signal prices_ready: list of dicts
    """
    prices_ready = pyqtSignal(list)
    status_message = pyqtSignal(str)  # short progress text, forwarded to MainWindow's status bar

    def __init__(self, names: list, tickers: list, markets: list, buy_prices: list, qtys: list, buy_amts: list, is_open: list = None, skip_fetch: list = None):
        super().__init__()
        self._names      = names
        self._tickers    = tickers
        self._markets    = markets
        self._buy_prices = buy_prices
        self._qtys       = qtys
        self._buy_amts   = buy_amts
        self._is_open    = is_open if is_open is not None else [True] * len(names)
        # skip_fetch[i]=True - skip price/history fetch (name-resolve only, e.g. stale closed positions)
        self._skip_fetch = skip_fetch if skip_fetch is not None else [False] * len(names)

    def run(self):
        import re
        from data_fetcher import (
            fetch_naver_realtime_prices, get_usd_krw_rate, fetch_historical_changes,
            _get_listing_with_norm, is_us_market,
        )

        # ---Build name - KRX code mapping ---
        name_to_info: dict = {}
        self.status_message.emit("Looking up ticker codes...")
        try:
            # Use cached listings with pre-computed NameNorm (no extra .copy() or regex per call)
            df_krx = _get_listing_with_norm('KRX-DESC')
            df_etf = _get_listing_with_norm('ETF/KR')

            for i, name in enumerate(self._names):
                if not name:
                    continue
                if name in name_to_info:
                    continue

                ticker = self._tickers[i]
                market = self._markets[i]

                if ticker and market and not is_us_market(market):
                    name_to_info[name] = {"code": ticker, "market": market, "name": name}
                    continue

                if is_us_market(market):
                    continue

                search_term = re.sub(r'[\s_]+', '', name.upper())

                row = df_krx[df_krx['NameNorm'] == search_term]
                if row.empty:
                    row = df_krx[df_krx['NameNorm'].str.contains(search_term, regex=False, na=False)]

                if row.empty:
                    row = df_etf[df_etf['NameNorm'] == search_term]
                if row.empty:
                    row = df_etf[df_etf['NameNorm'].str.contains(search_term, regex=False, na=False)]

                if not row.empty:
                    code_col = 'Symbol' if 'Symbol' in row.columns else 'Code'
                    code = str(row.iloc[0][code_col])
                    if code.isdigit():
                        code = code.zfill(6)
                    market = row.iloc[0].get('Market', 'KRX') if 'Market' in row.columns else 'ETF'
                    if "KOSDAQ GLOBAL" in market:
                        market = "KOSDAQ"
                    name_to_info[name] = {"code": code, "market": market}
        except Exception as e:
            logger.warning("[PositionPriceFetch] KRX/ETF listing error: %s", e, exc_info=True)

        # ---Fetch real-time prices via Naver (KRX) ---
        unique_codes = list({v["code"] for v in name_to_info.values()})
        price_map: dict = {}
        if unique_codes:
            self.status_message.emit(f"Fetching KR quotes... (Naver, {len(unique_codes)} tickers)")
            try:
                price_map = fetch_naver_realtime_prices(unique_codes)
            except Exception as e:
                logger.warning("[PositionPriceFetch] Naver price error: %s", e, exc_info=True)

        # ---Fetch real-time prices via YahooQuery (US/Other) ---
        us_names = []
        yq_safe  = []
        seen_us: set = set()
        for i, name in enumerate(self._names):
            if not name:
                continue
            if name not in name_to_info and name not in seen_us:
                seen_us.add(name)
                us_names.append(name)
                t = self._tickers[i]
                yq_safe.append(t.upper() if t else name.upper())

        us_price_map: dict = {}
        us_price_map_usd: dict = {}
        if us_names:
            self.status_message.emit("Waiting for Yahoo Finance response...")
            try:
                from data_fetcher import yf_quote_batch
                fx_rate = get_usd_krw_rate()
                prices = yf_quote_batch(yq_safe, timeout=15, chunk_size=30)

                # We always resolve the name to info, even if closed!
                for name, safe_name in zip(us_names, yq_safe):
                    data = prices.get(safe_name)
                    if isinstance(data, dict):
                        # fallback: just assign US market
                        resolved_name = data.get("longName") or data.get("shortName")
                        name_to_info[name] = {"code": safe_name, "market": "US", "name": resolved_name}
                        if 'regularMarketPrice' in data:
                            raw_usd_price = data['regularMarketPrice']
                            us_price_map_usd[name] = raw_usd_price
                            us_price_map[name] = raw_usd_price * fx_rate
            except Exception as e:
                logger.warning("[PositionPriceFetch] US price error: %s", e, exc_info=True)

        # ---Calculate P/L per position ---
        def _compute_pl(i, name):
            if not name:
                return {
                    "index":       i,
                    "ticker":      "",
                    "market":      "",
                    "name":        "",
                    "curr_price":  0.0,
                    "curr_pl":     0.0,
                    "curr_pl_pct": 0.0,
                    "wk1":         0.0,
                    "wk2":         0.0,
                    "mth1":        0.0,
                }

            info = name_to_info.get(name)
            if info:
                code = info["code"]
                market = info["market"]
                resolved_name = info.get("name")
            else:
                code = ""
                market = ""
                resolved_name = None

            curr_price = 0.0
            curr_pl = 0.0
            curr_pl_pct = 0.0
            wk1 = 0.0
            wk2 = 0.0
            mth1 = 0.0

            is_us = is_us_market(market)

            # ---Current price (KRW for display) ---
            if info and not is_us:
                curr_price = price_map.get(code, 0.0)
            elif info and is_us:
                curr_price = us_price_map.get(name, 0.0)

            # ---Raw USD price for historical comparison ---
            # fetch_historical_changes uses the ticker's historical Close (USD)
            # so we must pass the USD price, not the KRW-converted one.
            if is_us:
                curr_price_for_hist = us_price_map_usd.get(name, 0.0)
            else:
                curr_price_for_hist = curr_price

            # ---P/L (open positions only) ---
            if self._is_open[i]:
                qty     = self._qtys[i]
                buy_amt = self._buy_amts[i]

                if curr_price > 0 and qty > 0:
                    eval_val    = curr_price * qty
                    curr_pl     = eval_val - buy_amt
                    curr_pl_pct = (curr_pl / buy_amt * 100) if buy_amt else 0.0

            # ---Past % changes (open AND closed, USD-safe) ---
            if curr_price_for_hist > 0 and not self._skip_fetch[i]:
                try:
                    fetch_code = code if code else name
                    changes = fetch_historical_changes(fetch_code, curr_price_for_hist)
                    wk1  = changes.get("5d",  0.0)
                    wk2  = changes.get("10d", 0.0)
                    mth1 = changes.get("20d", 0.0)
                except Exception as exc:
                    logger.warning("Error fetching trend for %s: %s", name, exc, exc_info=True)

            return {
                "index":       i,
                "ticker":      code,
                "market":      market,
                "name":        resolved_name,
                "curr_price":  curr_price,
                "curr_pl":     curr_pl,
                "curr_pl_pct": curr_pl_pct,
                "wk1":         wk1,
                "wk2":         wk2,
                "mth1":        mth1,
            }

        max_workers = min(10, max(1, len(self._names)))
        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            results = list(exe.map(lambda arg: _compute_pl(*arg), enumerate(self._names)))

        self.prices_ready.emit(results)


# ---------------------------------------------------------------------------
# Account deposit fetch thread (Trading History tab "Fetch" button)
# ---------------------------------------------------------------------------
class AccountDepositThread(QThread):
    """Background thread: KIS inquire-balance via data_fetcher.fetch_account_deposit().

    The REST call used to run synchronously on the UI thread from the Fetch
    button handler, freezing the window whenever KIS was slow (roadmap 6-1c).
    """
    finished = pyqtSignal(float, str)  # deposit (KRW), error message ("" on success)

    def run(self):
        from data_fetcher import fetch_account_deposit
        try:
            self.finished.emit(float(fetch_account_deposit()), "")
        except Exception as e:
            logger.warning("Account deposit fetch failed", exc_info=True)
            self.finished.emit(0.0, str(e))


# ---------------------------------------------------------------------------
# Auto-backup thread (runs on app start)
# ---------------------------------------------------------------------------
class AutoBackupThread(QThread):
    """Copies portfolio.db + custom_settings.json into archive/auto_<timestamp>/ on
    every app start, then prunes old automatic backups beyond _AUTO_BACKUP_MAX_KEEP.

    Runs off the UI thread so startup is never blocked by disk I/O. This does not
    replace the manual archive/backup_<timestamp>/ convention used before editing
    main.py/data_fetcher.py/trade_db.py — it's a safety net for normal runtime use.
    """
    backup_done = pyqtSignal(str)  # short status message for the status bar

    def run(self):
        try:
            timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            dest_dir = os.path.join(ARCHIVE_DIR, f"auto_{timestamp}")

            existing = [f for f in _AUTO_BACKUP_FILES if os.path.exists(f)]
            if not existing:
                return  # nothing to back up yet (e.g. very first run)

            if DB_FILE in existing:
                try:
                    import trade_db
                    trade_db.checkpoint_wal()
                except Exception:
                    logger.warning(
                        "WAL checkpoint before backup failed -- proceeding with the "
                        "copy anyway, but it may miss the most recent commits",
                        exc_info=True,
                    )

            os.makedirs(dest_dir, exist_ok=True)
            for fpath in existing:
                shutil.copy2(fpath, os.path.join(dest_dir, os.path.basename(fpath)))

            self._prune_old_backups()
            rel_dest = os.path.relpath(dest_dir, BASE_DIR)
            logger.info("Auto-backup completed: %s (%s)", rel_dest,
                        ", ".join(os.path.basename(f) for f in existing))
            self.backup_done.emit(f"Auto-backup complete: {rel_dest}")
        except Exception:
            logger.warning("Auto-backup failed", exc_info=True)

    @staticmethod
    def _prune_old_backups():
        """Keep only the _AUTO_BACKUP_MAX_KEEP most recent archive/auto_* folders."""
        archive_dir = ARCHIVE_DIR
        if not os.path.isdir(archive_dir):
            return
        auto_dirs = sorted(
            d for d in os.listdir(archive_dir)
            if d.startswith("auto_") and os.path.isdir(os.path.join(archive_dir, d))
        )
        excess = len(auto_dirs) - _AUTO_BACKUP_MAX_KEEP
        for old_dir in auto_dirs[:max(excess, 0)]:
            try:
                shutil.rmtree(os.path.join(archive_dir, old_dir))
            except Exception:
                logger.warning("Failed to remove old auto-backup dir=%s", old_dir, exc_info=True)


# ---------------------------------------------------------------------------
# Weekly rebalance walk-forward backtest thread (rebalance.md section 6)
# ---------------------------------------------------------------------------
class RebalanceBacktestThread(QThread):
    """Background thread: strategy.rebalance.run_rebalance_backtest(). Always run
    off the UI thread -- it fetches full history for every ticker in the
    given universe, which for a large universe and a 5-year lookback can
    take a while even with _HIST_CACHE reuse on repeat runs."""
    progress = pyqtSignal(int, int)      # done, total tickers fetched so far
    finished = pyqtSignal(object, str)   # result dict | None, error message ("" on success)

    def __init__(
        self,
        tickers,
        lookback_years,
        top_n_by_market,
        band_multiplier,
        initial_capital,
        market_by_ticker=None,
        buy_fee_rate: float = 0.00015,
        sell_fee_rate: float = 0.00015,
        sell_tax_rate: float = 0.0018,
    ):
        super().__init__()
        self.tickers = tickers
        self.lookback_years = lookback_years
        self.top_n_by_market = top_n_by_market
        self.band_multiplier = band_multiplier
        self.initial_capital = initial_capital
        self.market_by_ticker = market_by_ticker
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.sell_tax_rate = sell_tax_rate

    def run(self):
        from strategy.rebalance import run_rebalance_backtest
        try:
            result = run_rebalance_backtest(
                self.tickers,
                lookback_years=self.lookback_years,
                top_n_by_market=self.top_n_by_market,
                band_multiplier=self.band_multiplier,
                initial_capital=self.initial_capital,
                market_by_ticker=self.market_by_ticker,
                buy_fee_rate=self.buy_fee_rate,
                sell_fee_rate=self.sell_fee_rate,
                sell_tax_rate=self.sell_tax_rate,
                progress_callback=lambda done, total: self.progress.emit(done, total),
            )
            self.finished.emit(result, "")
        except Exception as e:
            logger.warning("Rebalance backtest failed", exc_info=True)
            self.finished.emit(None, str(e))


# ---------------------------------------------------------------------------
# Trend-following (Donchian) backtest thread (trend_following.md 4)
# ---------------------------------------------------------------------------
class TrendFollowingBacktestThread(QThread):
    """Background thread: strategy.trend_following.run_backtest_for_ticker().
    The history fetch can take a few seconds on a cold cache, so it never runs
    on the UI thread (ui/trend_following_tab.py)."""
    finished = pyqtSignal(object, str)   # result dict | None, error message ("" on success)

    def __init__(self, ticker: str, start: str, config):
        super().__init__()
        self.ticker = ticker
        # not `self.start`: that would shadow QThread.start()
        self.start_date = start
        self.config = config

    def run(self):
        from strategy.trend_following import run_backtest_for_ticker
        try:
            result = run_backtest_for_ticker(self.ticker, self.start_date, self.config)
            self.finished.emit(result, "")
        except Exception as e:
            logger.warning("Trend-following backtest failed for %s", self.ticker, exc_info=True)
            self.finished.emit(None, str(e))


# ---------------------------------------------------------------------------
# Trend-following v3 portfolio / IS-OOS validation thread (trend_following.md 3 v3, 5)
# ---------------------------------------------------------------------------
class TrendFollowingPortfolioThread(QThread):
    """Background thread for the multi-instrument work: fetches every ticker's
    history once, then either runs the equal-sleeve portfolio backtest
    (mode="portfolio") or the holdout + yearly walk-forward validation over
    DEFAULT_GRID (mode="validate"). Validation is grid x instruments backtests,
    so it can take a minute on a cold cache."""
    progress = pyqtSignal(str)            # short status text
    finished = pyqtSignal(object, str)    # result dict | None, error message ("" on success)

    def __init__(self, tickers: list, start: str, config, mode: str = "portfolio",
                 oos_first_year: int = None, oos_last_year: int = None):
        super().__init__()
        self.tickers = list(tickers)
        self.start_date = start
        self.config = config
        self.mode = mode
        self.oos_first_year = oos_first_year
        self.oos_last_year = oos_last_year

    def run(self):
        from datetime import date
        from data.history import get_historical_data
        from strategy.trend_following import (
            run_portfolio_backtest, holdout_validation, walk_forward_validation, yearly_folds, DEFAULT_GRID,
        )
        try:
            histories = {}
            for i, t in enumerate(self.tickers, start=1):
                self.progress.emit(f"Fetching history {i}/{len(self.tickers)}: {t}")
                histories[t] = get_historical_data(t, self.start_date)

            if self.mode == "portfolio":
                self.progress.emit("Running portfolio backtest...")
                result = run_portfolio_backtest(histories, self.config)
                result["tickers"] = self.tickers
            else:
                last = self.oos_last_year or date.today().year
                first = self.oos_first_year or max(last - 4, 2000)
                self.progress.emit(f"Walk-forward validation {first}-{last} over {len(DEFAULT_GRID)} configs...")
                wf = walk_forward_validation(histories, yearly_folds(first, last), grid=DEFAULT_GRID, base=self.config)
                self.progress.emit(f"Holdout validation (split {first}-01-01)...")
                ho = holdout_validation(histories, split_date=date(first, 1, 1), grid=DEFAULT_GRID, base=self.config)
                n_inst = sum(1 for df in histories.values() if df is not None and not df.is_empty())
                result = {"walkforward": wf, "holdout": ho, "n_instruments": n_inst, "tickers": self.tickers}
            self.finished.emit(result, "")
        except Exception as e:
            logger.warning("Trend-following %s failed", self.mode, exc_info=True)
            self.finished.emit(None, str(e))


# ---------------------------------------------------------------------------
# Gemini AI Background Threads
# ---------------------------------------------------------------------------
class GeminiFilterThread(QThread):
    """Background thread to convert natural language query into filter conditions via Gemini."""
    finished = pyqtSignal(object, str)  # result dict | None, error_message ("" on success)

    def __init__(self, nl_query: str):
        super().__init__()
        self.nl_query = nl_query

    def run(self):
        try:
            import gemini_helper
            result = gemini_helper.nl_to_filter(self.nl_query)
            if result is None:
                self.finished.emit(None, "AI conversion failed or returned empty result.")
            else:
                self.finished.emit(result, "")
        except Exception as e:
            logger.warning("Gemini filter query failed", exc_info=True)
            self.finished.emit(None, str(e))


class GeminiDiagnosisThread(QThread):
    """Background thread to perform AI portfolio diagnosis via Gemini."""
    finished = pyqtSignal(str, str)  # result_text, error_message ("" on success)

    def __init__(self, open_data: list, closed_data: list):
        super().__init__()
        self.open_data = open_data
        self.closed_data = closed_data

    def run(self):
        try:
            import gemini_helper
            result_text = gemini_helper.portfolio_diagnosis(self.open_data, self.closed_data)
            self.finished.emit(result_text, "")
        except Exception as e:
            logger.warning("Gemini portfolio diagnosis failed", exc_info=True)
            self.finished.emit("", str(e))


class GeminiStockReportThread(QThread):
    """Background thread for the per-stock AI report (roadmap 2-1, review.md 2-1)."""
    finished = pyqtSignal(str, str, str, str)  # ticker, name, result_text, error_message ("" on success)

    def __init__(self, item: dict):
        super().__init__()
        self.item = item

    def run(self):
        ticker = self.item.get("ticker", "")
        name = self.item.get("name", ticker)
        try:
            import gemini_helper
            result_text = gemini_helper.stock_report_summary(self.item)
            self.finished.emit(ticker, name, result_text, "")
        except Exception as e:
            logger.warning("Gemini stock report failed for ticker=%s", ticker, exc_info=True)
            self.finished.emit(ticker, name, "", str(e))
