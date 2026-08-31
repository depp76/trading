"""data — Modular market data, indicator computation, backtesting, and portfolio rebalancing package."""

from data.cache import (
    _START_DATE,
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
    _HIST_CACHE_HITS,
    _HIST_CACHE_MISSES,
    _HIST_CACHE_LOG_INTERVAL,
    _YF_SESSION,
    _NAVER_SESSION,
    _KIWOOM_SESSION,
    YFTlsAdapter,
    _get_yf_crumb,
    _log_hist_cache_stats,
    _hist_df_is_stale,
    _pdf_is_stale,
    safe_float,
)

from data.indicators import (
    _to_polars,
    _compute_indicators,
    fetch_historical_changes,
)

from data.collectors.naver import (
    _fast_kr_history,
    fetch_naver_realtime_prices,
    _fetch_naver_per_single,
    fetch_naver_per_batch,
    _fetch_naver_info,
    _fetch_kr_listing_naver,
    _get_kr3y_df,
    _fetch_index_investor_trend,
    _fetch_investor_trend_naver,
    fetch_quarterly_financials,
)

from data.collectors.yahoo import (
    _YF_BULK_CACHE,
    yf_quote_batch,
    fetch_us_realtime_prices,
    fetch_wti_futures_curve,
    fetch_us_stock_data_bulk,
    fetch_us_market_data,
)

from data.collectors.kiwoom import (
    _KIWOOM_TOKEN_CACHE,
    _KIWOOM_KEYS_CACHE,
    _get_kiwoom_keys,
    _get_kiwoom_token,
    _kiwoom_parse_price,
    _kiwoom_parse_signed_int,
    fetch_kiwoom_stock_info,
    fetch_kiwoom_daily_ohlcv,
    fetch_account_deposit,
    fetch_investor_trend,
)

from data.collectors.krx import (
    VKOSPI_INDEX_NAME,
    _get_krx_auth_key,
    fetch_krx_derivative_index_day,
    fetch_vkospi,
    _load_vkospi_cache,
    _save_vkospi_cache,
    fetch_vkospi_history,
    _get_vkospi_pdf,
    _get_jp10y_df,
)

from data.market import (
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

from data.backtest import (
    run_backtest_strategy,
    run_backtest_for_stock,
    run_bulk_backtest_chunk,
)

from data.rebalance import (
    _REBALANCE_FACTORS,
    _REBALANCE_MIN_FACTORS,
    _extract_live_candidates,
    _score_and_rank,
    _classify_buy_sell_hold,
    compute_weekly_rebalance_signals,
    _rebalance_friday_dates,
    _compute_historical_factor_series,
    _factor_snapshot_as_of,
    _run_walkforward_simulation,
    _summarize_backtest,
    run_rebalance_backtest,
)
