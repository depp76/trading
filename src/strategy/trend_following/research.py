"""strategy/trend_following/research.py — Runs the 6-2 comparison matrix
(A0..A5, B) against the 6-3 benchmarks under the 4-1 cost scenarios and the
6-1 event study, and renders the report the spec asks to keep per version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

from strategy.trend_following.backtest import BacktestResult, run_backtest
from strategy.trend_following.benchmarks import run_benchmarks
from strategy.trend_following.config import (
    SPEC_VERSION, COST_MULTIPLIERS, PERIODS, VARIANTS, StrategyParams,
)
from strategy.trend_following.costs import CostModel
from strategy.trend_following.dataset import ResearchCancelled
from strategy.trend_following.event_study import run_event_study, HORIZONS
from strategy.trend_following.metrics import summarize_run
from strategy.trend_following.signals import compute_features

BENCHMARK_LABELS = {
    "BM1": "BM1: KOSPI 200 TR buy & hold",
    "BM2": "BM2: universe equal weight",
    "BM3": "BM3: KOSPI 200 ETF above MA200",
    "BM4": "BM4: risk-free (CD 91d)",
}


@dataclass(frozen=True)
class ResearchRequest:
    start: date
    end: date | None = None
    params: StrategyParams = field(default_factory=StrategyParams)
    variant_ids: tuple[str, ...] = tuple(VARIANTS)
    cost_multipliers: tuple[float, ...] = COST_MULTIPLIERS
    cost_model: CostModel = field(default_factory=CostModel)
    include_flows: bool = False
    run_backtests: bool = True
    run_event_study: bool = True


@dataclass
class RunSummary:
    id: str
    label: str
    kind: str                 # "strategy" | "benchmark"
    cost_mult: float
    metrics: dict
    nav: np.ndarray
    dates: list[date]
    notes: list[str] = field(default_factory=list)


@dataclass
class ResearchResult:
    spec_version: str
    created_at: datetime
    request: ResearchRequest
    dataset_info: dict
    runs: list[RunSummary]
    event_study: list[dict]
    warnings: list[str]

    def runs_at(self, mult: float, kind: str | None = None) -> list[RunSummary]:
        return [r for r in self.runs if abs(r.cost_mult - mult) < 1e-9 and (kind is None or r.kind == kind)]

    def to_markdown(self) -> str:
        return render_markdown(self)


def _say(progress, msg):
    if progress is not None:
        progress(msg)


def _check_stop(should_stop) -> None:
    if should_stop is not None and should_stop():
        raise ResearchCancelled("cancelled")


def run_research(ds, req: ResearchRequest, progress=None, should_stop=None) -> ResearchResult:
    """`should_stop` (no-arg callable) is polled before every backtest and the
    event study; True raises ResearchCancelled."""
    p = req.params
    warnings: list[str] = []
    _say(progress, "Computing signals...")
    feats = compute_features(ds, p)

    if not ds.info.get("bm_is_total_return", True):
        warnings.append("BM1/BM3 use a price-only ETF series (KODEX 200TR unavailable): benchmarks understate "
                        "total return by the dividend yield (6-3 forbids price indices).")
    if ds.bm is None:
        warnings.append("No benchmark ETF history: BM1 and BM3 were skipped.")
    if not feats.has_flows:
        warnings.append("Investor-flow data not loaded: A3/A4/A5 (F, FX) and the flow groups of the event study "
                        "were skipped.")
    else:
        if feats.flow_coverage < 0.5:
            warnings.append(f"Investor-flow coverage is only {feats.flow_coverage:.0%} of date x ticker cells.")
        warnings.append("Retail flow is a proxy: the Naver frgn source has only foreigner and institution, so "
                        "retail = -(FI) and 'other corporations' (buybacks, block deals) are folded into it; the "
                        "event study's FI net-sell group is that proxy, not an independent retail signal.")
    warnings.append(ds.info.get("universe", ""))

    runs: list[RunSummary] = []
    if req.run_backtests:
        for mult in req.cost_multipliers:
            cm = req.cost_model.scaled(mult)
            _check_stop(should_stop)
            _say(progress, f"Benchmarks at {mult:g}x costs...")
            bms = run_benchmarks(ds, feats, p, cm)
            bm1, bm3 = bms.get("BM1"), bms.get("BM3")
            for vid in req.variant_ids:
                v = VARIANTS[vid]
                if v.needs_flows and not feats.has_flows:
                    continue
                _check_stop(should_stop)
                _say(progress, f"Backtest {vid} at {mult:g}x costs...")
                res: BacktestResult = run_backtest(ds, feats, p, v, cm)
                runs.append(RunSummary(vid, v.label, "strategy", mult, summarize_run(res, bm1, bm3),
                                       res.nav, res.dates, res.notes))
            for bid, res in bms.items():
                runs.append(RunSummary(bid, BENCHMARK_LABELS.get(bid, bid), "benchmark", mult,
                                       summarize_run(res, bm1, bm3), res.nav, res.dates, res.notes))

    events: list[dict] = []
    if req.run_event_study:
        _check_stop(should_stop)
        _say(progress, "Event study...")
        events = run_event_study(ds, feats, p)

    info = dict(ds.info)
    info["flow_coverage"] = feats.flow_coverage
    info["regime_on_share"] = float(np.mean(feats.regime_on[ds.start_idx:])) if ds.stocks.T > ds.start_idx else 0.0
    return ResearchResult(SPEC_VERSION, datetime.now(), req, info, runs, events, [w for w in warnings if w])


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _pct(x, digits=1) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "-"
    return f"{x * 100:.{digits}f}%"


def _num(x, digits=2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "-"
    return f"{x:.{digits}f}"


def _won(x) -> str:
    return "-" if x is None else f"{x:,.0f}"


SUMMARY_COLUMNS = [
    ("ID", lambda r: r.id),
    ("CAGR", lambda r: _pct(r.metrics["cagr"])),
    ("Vol", lambda r: _pct(r.metrics["vol"])),
    ("Sharpe", lambda r: _num(r.metrics["sharpe"])),
    ("MDD", lambda r: _pct(r.metrics["mdd"])),
    ("Calmar", lambda r: _num(r.metrics["calmar"])),
    ("Trades", lambda r: str(r.metrics["n_trades"])),
    ("Win", lambda r: _pct(r.metrics["win_rate"], 0)),
    ("Hold(d)", lambda r: _num(r.metrics["avg_holding_days"], 0)),
    ("Turnover", lambda r: _num(r.metrics["turnover"], 1)),     # round trips per year (6-4)
    ("Cost/yr", lambda r: _pct(r.metrics["cost_pct_per_year"], 2)),
    ("Cash", lambda r: _pct(r.metrics["avg_cash_weight"], 0)),
    ("vs BM1", lambda r: _pct(r.metrics.get("bm1_excess_cagr"))),
    ("IR", lambda r: _num(r.metrics.get("bm1_ir"))),
    ("Beta", lambda r: _num(r.metrics.get("bm1_beta"))),
    ("Up cap", lambda r: _pct(r.metrics.get("bm1_up_capture"), 0)),
    ("Down cap", lambda r: _pct(r.metrics.get("bm1_down_capture"), 0)),
] + [(label, (lambda r, k=label: _pct(r.metrics["periods"].get(k)))) for label, _, _ in PERIODS]


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def render_markdown(result: ResearchResult) -> str:
    req = result.request
    p = req.params
    out: list[str] = []
    out.append(f"# Trend Following backtest report — spec {result.spec_version}")
    out.append("")
    out.append(f"- Generated: {result.created_at:%Y-%m-%d %H:%M}")
    out.append(f"- Window: {result.dataset_info.get('eval_start', '')} → {result.dataset_info.get('eval_end', '')}")
    out.append(f"- Universe: {result.dataset_info.get('n_tickers', 0)} KOSPI names "
               f"(requested top {result.dataset_info.get('universe_size_requested', p.universe_size)}), "
               f"liquidity floor {p.min_avg_trading_value / 1e8:,.0f}억 KRW (20d avg value)")
    out.append(f"- Benchmark ETF: {result.dataset_info.get('bm_ticker', '-')} "
               f"({'total return' if result.dataset_info.get('bm_is_total_return') else 'PRICE ONLY'}); "
               f"risk-free: {result.dataset_info.get('rf_source', '-')}")
    out.append(f"- Flows loaded: {result.dataset_info.get('has_flows', False)} "
               f"(coverage {_pct(result.dataset_info.get('flow_coverage', 0.0), 0)}); "
               f"KOSPI risk-on share of sessions: {_pct(result.dataset_info.get('regime_on_share', 0.0), 0)}")
    out.append(f"- Seed {p.seed_krw:,.0f} KRW, annual reset {p.annual_reset}, max positions {p.max_positions}, "
               f"risk/trade {_pct(p.risk_per_trade)}, single-name cap {_pct(p.max_weight, 0)}, "
               f"check weekday {p.check_weekday}")
    cm = req.cost_model
    tax_desc = ", ".join(f"{y}: {_pct(v.get('KOSPI', 0.0), 2)}" for y, v in sorted(cm.tax_table.items()))
    out.append(f"- Costs (1x): commission {_pct(cm.commission_rate, 3)}, slippage {_pct(cm.slippage_rate, 2)}, "
               f"tax by year {{{tax_desc}}}, ETF slippage 0.02%, tick rounding {cm.round_to_tick}, "
               f"limit-move block {cm.block_limit_up_down}")
    if result.warnings:
        out.append("")
        out.append("## Warnings")
        out += [f"- {w}" for w in result.warnings]

    for mult in req.cost_multipliers:
        rows = result.runs_at(mult)
        if not rows:
            continue
        out.append("")
        out.append(f"## Results at {mult:g}x costs" + (" (base case)" if abs(mult - 1.0) < 1e-9 else ""))
        out.append("")
        out.append(_table([h for h, _ in SUMMARY_COLUMNS],
                          [[fn(r) for _, fn in SUMMARY_COLUMNS] for r in rows]))

    strat_1x = result.runs_at(1.0, "strategy")
    if strat_1x:
        out.append("")
        out.append("## 6-4 verdict (1x costs)")
        out.append("")
        out.append(_table(
            ["ID", "Sharpe & Calmar > BM1", "Sharpe & Calmar > BM3", "Up/Down capture shape (60-80% / 30-50%)",
             "Period wins vs BM1"],
            [[r.id,
              "yes" if r.metrics.get("bm1_beats_risk_adjusted") else "no",
              "yes" if r.metrics.get("bm3_beats_risk_adjusted") else "no",
              _shape_ok(r.metrics),
              ", ".join(f"{k}:{'W' if v else ('L' if v is False else '-')}"
                        for k, v in (r.metrics.get("bm1_period_win") or {}).items())]
             for r in strat_1x]))

    out.append("")
    out.append("## Cost sensitivity (CAGR / Sharpe by multiplier)")
    out.append("")
    ids = []
    for r in result.runs:
        if r.id not in ids:
            ids.append(r.id)
    mults = list(req.cost_multipliers)
    out.append(_table(["ID"] + [f"{m:g}x" for m in mults],
                      [[i] + [_cell_for(result, i, m) for m in mults] for i in ids]))

    out.append("")
    out.append("## Cost breakdown (1x, whole window)")
    out.append("")
    out.append(_table(["ID", "Commission", "Tax", "Slippage", "Total", "Turnover/yr", "Banked", "Topped up"],
                      [[r.id, _won(r.metrics["cost_commission"]), _won(r.metrics["cost_tax"]),
                        _won(r.metrics["cost_slippage"]), _won(r.metrics["cost_total"]),
                        _num(r.metrics["turnover"], 1), _won(r.metrics["banked"]), _won(r.metrics["topped_up"])]
                       for r in result.runs_at(1.0)]))

    yearly_rows = result.runs_at(1.0)
    if yearly_rows:
        years = sorted({y for r in yearly_rows for y in r.metrics["yearly"]})
        out.append("")
        out.append("## Yearly returns (1x)")
        out.append("")
        out.append(_table(["ID"] + [str(y) for y in years],
                          [[r.id] + [_pct(r.metrics["yearly"].get(y)) for y in years] for r in yearly_rows]))

    if result.event_study:
        out.append("")
        out.append("## 6-1 event study: forward returns after Donchian-20 breakouts (from next open)")
        out.append("")
        headers = ["Group", "n"] + [f"{s} {h}d" for h in HORIZONS for s in ("mean", "hit", "excess")]
        rows = []
        for e in result.event_study:
            row = [e["group"], str(e.get("n", 0))]
            for h in HORIZONS:
                row += [_pct(e.get(f"mean_{h}")), _pct(e.get(f"hit_{h}"), 0), _pct(e.get(f"excess_{h}"))]
            rows.append(row)
        out.append(_table(headers, rows))

    out.append("")
    out.append("## Variants")
    out.append("")
    out += [f"- {VARIANTS[v].label}" for v in req.variant_ids if v in VARIANTS]
    out.append("")
    return "\n".join(out)


def _shape_ok(m: dict) -> str:
    up, down = m.get("bm1_up_capture"), m.get("bm1_down_capture")
    if up is None or down is None or not (np.isfinite(up) and np.isfinite(down)):
        return "-"
    ok = 0.6 <= up <= 0.8 and 0.3 <= down <= 0.5
    return f"{'yes' if ok else 'no'} ({_pct(up, 0)} / {_pct(down, 0)})"


def _cell_for(result: ResearchResult, run_id: str, mult: float) -> str:
    for r in result.runs_at(mult):
        if r.id == run_id:
            return f"{_pct(r.metrics['cagr'])} / {_num(r.metrics['sharpe'])}"
    return "-"
