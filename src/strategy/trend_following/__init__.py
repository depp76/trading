"""strategy.trend_following — Trend Following strategy (spec: trend_following.md
in this folder, currently v03).

Facade: UI and thread code import from here; the modules underneath are
  config      parameters, the 6-2 variant matrix, cost scenarios
  costs       4-1 cost model (tax table, tick sizes, commission, slippage)
  signals     layer signals L1..L4 and the exit conditions as date x ticker arrays
  dataset     aligned price / flow / rate arrays (build_dataset is pure, load_dataset fetches)
  backtest    portfolio engine (t+1 fills, interest, annual reset, ledger) + the strategy policy
  benchmarks  BM1..BM4 on the same engine
  metrics     6-4 scorecard
  event_study 6-1 breakout event study
  research    the matrix runner and the markdown report
"""
from strategy.trend_following.config import (
    SPEC_VERSION, SPEC_FILE, StrategyParams, Variant, VARIANTS, COST_MULTIPLIERS, PERIODS,
    BM_TR_TICKER, BM_PRICE_FALLBACK_TICKER,
)
from strategy.trend_following.costs import CostModel, TradeCost, DEFAULT_TAX_TABLE, tick_size, snap_to_tick
from strategy.trend_following.signals import Features, compute_features, regime_state, weekly_check_days
from strategy.trend_following.dataset import Dataset, PriceBook, ResearchCancelled, build_dataset, load_dataset
from strategy.trend_following.backtest import (
    BacktestResult, Trade, Engine, Policy, TrendFollowingPolicy, run_backtest,
)
from strategy.trend_following.benchmarks import run_benchmarks, run_bm1, run_bm2, run_bm3, run_bm4
from strategy.trend_following.metrics import summarize_run
from strategy.trend_following.event_study import run_event_study
from strategy.trend_following.research import (
    ResearchRequest, ResearchResult, RunSummary, run_research, render_markdown,
)

__all__ = [
    "SPEC_VERSION", "SPEC_FILE", "StrategyParams", "Variant", "VARIANTS", "COST_MULTIPLIERS", "PERIODS",
    "BM_TR_TICKER", "BM_PRICE_FALLBACK_TICKER",
    "CostModel", "TradeCost", "DEFAULT_TAX_TABLE", "tick_size", "snap_to_tick",
    "Features", "compute_features", "regime_state", "weekly_check_days",
    "Dataset", "PriceBook", "ResearchCancelled", "build_dataset", "load_dataset",
    "BacktestResult", "Trade", "Engine", "Policy", "TrendFollowingPolicy", "run_backtest",
    "run_benchmarks", "run_bm1", "run_bm2", "run_bm3", "run_bm4",
    "summarize_run", "run_event_study",
    "ResearchRequest", "ResearchResult", "RunSummary", "run_research", "render_markdown",
]
