"""data/rebalance/signals.py — compute_weekly_rebalance_signals orchestrator (trading.md 11-2/8-I).

Combines factors -> classify into the weekly signal computation used by both
the live UI (ui/auto_trading_tab.py) and the walk-forward backtest. Sector-cap
(8-A) / regime-overlay (8-B) post-processing hooks land here, not in
classify.py, once implemented.
"""
from datetime import datetime

from data.rebalance.config import RebalanceConfig
from data.rebalance.factors import _extract_live_candidates, _score_and_rank
from data.rebalance.classify import _classify_buy_sell_hold, _DEFAULT_TOP_N_BY_MARKET


def compute_weekly_rebalance_signals(
    universe_data: list,
    current_holdings=None,
    top_n_by_market: dict = None,
    band_multiplier: float = RebalanceConfig().band_multiplier,
) -> dict:
    """Factor-score and rank the Trading Universe for weekly rebalancing.

    top_n_by_market: per-market buy-candidate cap, e.g. {"KOSPI": 10,
    "KOSDAQ": 10} (trading.md 3-5) — defaults to _DEFAULT_TOP_N_BY_MARKET.
    """
    top_n_by_market = top_n_by_market or _DEFAULT_TOP_N_BY_MARKET
    candidates = _extract_live_candidates(universe_data)
    excluded_count = len(universe_data) - len(candidates)
    ranked = _score_and_rank(candidates)
    classification = _classify_buy_sell_hold(ranked, current_holdings, top_n_by_market, band_multiplier)

    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "top_n_by_market": top_n_by_market,
        "excluded_count": excluded_count,
        "ranked": ranked,
        **classification,
    }
