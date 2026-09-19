"""data_fetcher.py — Facade module for the data package.

Re-exports the names UI and thread code actually import, so they keep one
import location while `data/` stays free to move things between its layers.
Strategy code is *not* re-exported here: import it from the `strategy`
package directly (rebalance.md 11-5).

This module is the single re-export list (data/__init__.py holds none), and
tests patch the real implementation modules (`data.cache`, `data.history`,
`data.fx`, `data.collectors.*`) rather than names on this facade (roadmap
6-2a). Trimmed on 2026-09-19 from 79 names (most of them private cache
internals nobody outside `data/` used) to the ones below; add a name here
only when a caller outside `data/` needs it.
"""

from data.cache import (
    start_date,
    is_kr_code,
    is_us_market,
    safe_float,
    _USD_KRW_CACHE,       # tests reset the FX session cache through the facade
    _HIST_CACHE_STATS,    # tests assert the counters are shared with data.cache
    _YF_SESSION,          # tests patch the Yahoo session's .get
)

from data.frames import _to_polars

from data.indicators import fetch_historical_changes

from data.collectors.naver import (
    fetch_naver_realtime_prices,
    fetch_naver_realtime_index_prices,
    _fetch_naver_info,
)

from data.collectors.yahoo import (
    yf_quote_batch,
    fetch_us_realtime_prices,
    fetch_wti_futures_curve,
)

from data.collectors.kis import (
    fetch_account_deposit,
    fetch_investor_trend,
    fetch_kis_realtime_prices,
)

from data.listing import _get_listing_with_norm

from data.history import get_historical_data

from data.fx import (
    get_usd_krw_rate,
    get_usd_krw_rate_for_date,
)

from data.market import (
    INDEX_TICKERS,
    fetch_market_data,
    fetch_single_stock,
    get_index_close_for_date,
    fetch_all_indices_mas,
    fetch_stock_ma_multi,
    fetch_major_indices_as_stocks,
)
