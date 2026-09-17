"""data_fetcher.py — Facade module for the data package.

Re-exports the public market data fetchers, caching structures, and indicators
from the modular `data/` package so UI/thread callers can keep importing from
one place. Strategy code is *not* re-exported here: import it from the
`strategy` package directly (e.g. `from strategy.rebalance import ...`,
`from strategy.ma_cross import run_backtest_strategy`), see rebalance.md 11-5.

This module is a pure re-export: nothing in `data/` imports it back, and
tests patch the real implementation modules (`data.cache`, `data.market`,
`data.collectors.*`) rather than names on this facade (roadmap 6-2a).
"""

from data import (
    # Cache & Sessions
    start_date,
    _TD_PERIODS,
    _CHANGE_KEYS,
    _FDR_ONLY_TICKERS,
    _USD_KRW_CACHE,
    _INDEX_CLOSE_CACHE,
    _JP10Y_CACHE,
    _KR3Y_CACHE,
    _VKOSPI_CACHE,
    _HIST_CACHE,
    _HIST_CACHE_LOCK,
    _HIST_CACHE_MAX,
    _HIST_CACHE_STATS,
    _HIST_CACHE_LOG_INTERVAL,
    _YF_SESSION,
    _NAVER_SESSION,
    _KIS_SESSION,
    YFTlsAdapter,
    _get_yf_crumb,
    _log_hist_cache_stats,
    _hist_df_is_stale,
    _pdf_is_stale,
    safe_float,
    is_kr_code,

    # Indicators
    _to_polars,
    _compute_indicators,
    fetch_historical_changes,

    # Collectors — Naver
    _fast_kr_history,
    fetch_naver_realtime_prices,
    fetch_naver_realtime_index_prices,
    _fetch_naver_per_single,
    fetch_naver_per_batch,
    _fetch_naver_info,
    _fetch_kr_listing_naver,
    _get_kr3y_df,
    _fetch_index_investor_trend,
    _fetch_investor_trend_naver,
    fetch_quarterly_financials,

    # Collectors — Yahoo
    _YF_BULK_CACHE,
    yf_quote_batch,
    fetch_us_realtime_prices,
    fetch_wti_futures_curve,
    fetch_us_stock_data_bulk,
    fetch_us_market_data,

    # Collectors — KIS (한국투자증권)
    _KIS_TOKEN_CACHE,
    _KIS_KEYS_CACHE,
    _get_kis_keys,
    _get_kis_token,
    _get_kis_approval_key,
    fetch_kis_stock_info,
    fetch_kis_daily_ohlcv,
    fetch_account_deposit,
    fetch_investor_trend,
    fetch_kis_realtime_prices,
    is_krx_market_open,

    # Collectors — KRX
    VKOSPI_INDEX_NAME,
    _get_krx_auth_key,
    fetch_krx_derivative_index_day,
    fetch_vkospi,
    _load_vkospi_cache,
    _save_vkospi_cache,
    fetch_vkospi_history,
    _get_vkospi_pdf,
    _get_jp10y_df,

    # Market Aggregation
    INDEX_TICKERS,
    _INDEX_DISPLAY_NAMES,
    _INDEX_ORDER,
    get_stock_listing,
    _get_listing_with_norm,
    _fetch_kr_listing_fdr_fallback,
    get_historical_data,
    _fetch_historical_uncached,
    _build_kr_stock_res,
    fetch_kr_market_data,
    fetch_market_data,
    fetch_single_stock,
    get_usd_krw_rate,
    get_usd_krw_rate_for_date,
    get_index_close_for_date,
    fetch_index_mas,
    fetch_all_indices_mas,
    fetch_stock_ma_multi,
    fetch_indice_as_stock,
    fetch_major_indices_as_stocks,
)
