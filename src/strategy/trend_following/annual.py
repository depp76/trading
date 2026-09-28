"""strategy/trend_following/annual.py — Year-end harvest, loss-year top-up and the
annual report of the KR Donchian portfolio strategy (trend_following.md 2-5, 3-3).

Pure functions over plain numbers so the engine loop stays small and the arithmetic
is testable on its own (tests/strategy/trend_following/test_annual.py):

  plan_year_end_harvest(cash, holdings, base, cost_model, mode)  -> HarvestPlan
  topup_amount(value_end, base, topup_on_loss)                   -> float
  annual_summary(years, base, final_equity)                       -> dict (3-3 누적 요약)
"""
from dataclasses import dataclass, field
import math
import statistics

from strategy.costs import TransactionCostModel


@dataclass
class HarvestPlan:
    """What the year-end harvest sells and withdraws (2-5 steps 1-3).

    value         V, the pre-harvest valuation (cash + holdings at the close)
    excess        X = max(V - base, 0)
    sells         {ticker: shares sold at the close}
    gross         sum of shares * close over `sells`
    costs         sell fees/taxes on `gross` (no slippage: closing-auction single price)
    withdrawal    X - costs, the cash moved out of the portfolio; after applying the plan the
                  portfolio is worth exactly `base` (rounding only shifts value between
                  cash and stock). Reduced when the cash leg would otherwise go negative.
    cash_after    portfolio cash after the sells and the withdrawal (>= 0)
    """
    value: float
    excess: float = 0.0
    sells: dict = field(default_factory=dict)
    gross: float = 0.0
    costs: float = 0.0
    withdrawal: float = 0.0
    cash_after: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self.excess <= 0 or (not self.sells and self.withdrawal <= 0)


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def plan_year_end_harvest(cash: float, holdings: dict, base: float, cost_model: TransactionCostModel,
                          mode: str = "pro_rata") -> HarvestPlan:
    """Build the year-end harvest for `holdings` ({ticker: (qty, close)}).

    mode "pro_rata"   cash and every position are cut by the same fraction X / V (2-5 step 2)
    mode "cash_first" the excess is taken from cash first; only the remainder is sold, spread
                      pro rata over the positions (trend_following.md 4 alternative)
    mode "none"       nothing is sold or withdrawn (re-invest; V is still reported)
    Shares are integers: sold = round(qty * fraction), so the actual stock sold differs from
    the fraction by rounding. The withdrawal is fixed at X - costs and the rounding difference
    stays in portfolio cash, which keeps the post-harvest portfolio at exactly `base`.
    """
    stock_value = sum(float(q) * float(px) for q, px in holdings.values())
    value = float(cash) + stock_value
    plan = HarvestPlan(value=value, cash_after=float(cash))
    excess = value - float(base)
    if mode == "none" or excess <= 0:
        return plan
    plan.excess = excess

    if mode == "cash_first":
        from_cash = min(float(cash), excess)
        remainder = excess - from_cash
        frac = remainder / stock_value if stock_value > 0 and remainder > 0 else 0.0
    else:
        frac = excess / value if value > 0 else 0.0

    gross, costs, sells = 0.0, 0.0, {}
    if frac > 0:
        for ticker, (qty, px) in holdings.items():
            sold = _round_half_up(float(qty) * frac)
            sold = max(0, min(int(qty), sold))
            if sold <= 0:
                continue
            g = sold * float(px)
            sells[ticker] = sold
            gross += g
            costs += cost_model.sell_cost(g)

    withdrawal = excess - costs
    cash_after = float(cash) + (gross - costs) - withdrawal
    if cash_after < 0:                       # rounding sold too little stock to fund the withdrawal
        withdrawal += cash_after
        cash_after = 0.0
    if withdrawal < 0:
        withdrawal = 0.0
    plan.sells, plan.gross, plan.costs = sells, gross, costs
    plan.withdrawal, plan.cash_after = withdrawal, cash_after
    return plan


def topup_amount(value_end: float, base: float, topup_on_loss: bool = True) -> float:
    """2-5 loss year: cash added on the next year's first trading day to restore `base`."""
    if not topup_on_loss:
        return 0.0
    return max(float(base) - float(value_end), 0.0)


def annual_summary(years: list, base: float, final_equity: float) -> dict:
    """3-3 cumulative summary over the per-year rows produced by engine.run_kr_trend().

    net_pnl = cumulative withdrawals - cumulative top-ups + final equity - base. The annual
    return statistics use complete years only (an in-progress YTD row is excluded) and are
    simple, not compounded: the capital is reset every year (no compounding, 3-3).
    """
    complete = [y for y in years if y.get("complete")]
    rets = [y["return_pct"] for y in complete]
    withdrawals = sum(y.get("withdrawal", 0.0) for y in years)
    topups = sum(y.get("topup", 0.0) for y in years)
    beat = [y for y in complete if y.get("excess_vs_kospi_pct") is not None and y["excess_vs_kospi_pct"] > 0]
    with_bench = [y for y in complete if y.get("excess_vs_kospi_pct") is not None]
    return {
        "base_capital": float(base),
        "n_years": len(complete),
        "cumulative_withdrawal": withdrawals,
        "cumulative_topup": topups,
        "final_equity": float(final_equity),
        "net_pnl": withdrawals - topups + float(final_equity) - float(base),
        "annual_return_mean_pct": statistics.fmean(rets) if rets else 0.0,
        "annual_return_median_pct": statistics.median(rets) if rets else 0.0,
        "annual_return_min_pct": min(rets) if rets else 0.0,
        "annual_return_max_pct": max(rets) if rets else 0.0,
        "n_positive_years": sum(1 for r in rets if r > 0),
        "n_years_beat_kospi": len(beat),
        "n_years_with_kospi": len(with_bench),
        "max_year_mdd_pct": max((y["mdd_pct"] for y in years), default=0.0),
        "compounding": False,
    }
