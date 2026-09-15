"""data/rebalance/classify.py — Buy/sell/hold classification from ranked candidates (trading.md 11-2).

Pure ranking-based classification — knows nothing about sector or regime
constraints (those land as post-processing hooks in data/rebalance/signals.py,
trading.md 8-A/8-B), so future sector-cap/regime work never needs to touch
this file.
"""
from data.rebalance.config import RebalanceConfig

_DEFAULT_TOP_N_BY_MARKET = RebalanceConfig().top_n_by_market


def _classify_buy_sell_hold(ranked: list, current_holdings, top_n_by_market: dict, band_multiplier: float) -> dict:
    """Step 4 of the rebalance pipeline: classify a ranked universe into
    buy/sell/hold given a set of currently-held tickers.

    top_n_by_market caps buy candidates per market (e.g. {"KOSPI": 10,
    "KOSDAQ": 10}) using each stock's rank within its own market
    (`market_rank`, set by _score_and_rank) rather than one global cutoff —
    otherwise the more volatile market (KOSDAQ) tends to dominate a combined
    top-N. A held ticker whose market isn't in top_n_by_market (out of this
    algorithm's scope) is always flagged as a sell candidate, matching the
    existing "unranked -> sell" fallback below.
    """
    current_holdings = set(current_holdings or [])
    rank_by_ticker = {r["ticker"]: r for r in ranked}
    sell_threshold_by_market = {m: int(n * band_multiplier) for m, n in top_n_by_market.items()}

    buy_candidates = [
        r for r in ranked
        if r["ticker"] not in current_holdings
        and r["market"] in top_n_by_market
        and r["market_rank"] <= top_n_by_market[r["market"]]
    ]
    hold = [
        r for r in ranked
        if r["ticker"] in current_holdings
        and r["market"] in sell_threshold_by_market
        and r["market_rank"] <= sell_threshold_by_market[r["market"]]
    ]
    sell_candidates = [
        r for r in ranked
        if r["ticker"] in current_holdings
        and (r["market"] not in sell_threshold_by_market
             or r["market_rank"] > sell_threshold_by_market[r["market"]])
    ]
    for ticker in current_holdings:
        if ticker not in rank_by_ticker:
            sell_candidates.append({
                "ticker": ticker, "name": ticker, "market": "",
                "score": None, "factors": {}, "raw": {}, "rank": None, "market_rank": None,
            })

    return {
        "sell_threshold_by_market": sell_threshold_by_market,
        "buy_candidates": buy_candidates,
        "sell_candidates": sell_candidates,
        "hold": hold,
    }
