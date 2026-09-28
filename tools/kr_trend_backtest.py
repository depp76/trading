"""tools/kr_trend_backtest.py — Run the KR Donchian 20/10 portfolio backtest
(src/strategy/trend_following/trend_following.md 3) on real data from the command line.

    .\\.venv\\Scripts\\python.exe tools\\kr_trend_backtest.py                 # base case, 2017-
    .\\.venv\\Scripts\\python.exe tools\\kr_trend_backtest.py --compare       # 4장 1단계 table
    .\\.venv\\Scripts\\python.exe tools\\kr_trend_backtest.py --sensitivity   # 4장 2단계 grid
    .\\.venv\\Scripts\\python.exe tools\\kr_trend_backtest.py --walk-forward  # 4장 3단계
    .\\.venv\\Scripts\\python.exe tools\\kr_trend_backtest.py --refresh       # re-download the cache

The candidate population is the KR stock list in universe_cache.json (the Trading
Universe: KOSPI 300 + KOSDAQ 150, trend_following.md 2-1). Daily OHLCV comes from
data.history.get_historical_data() (Naver) and is cached as parquet under
cache/kr_trend/ (gitignored) so repeated runs do not re-download 450 histories.
Results are printed as markdown tables and written to reports/kr_trend_<timestamp>.md.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import polars as pl

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

from paths import UNIVERSE_CACHE_FILE, root_path  # noqa: E402
from strategy.trend_following import (  # noqa: E402
    KrTrendConfig, abnormal_return_rows, compare_assumptions, kr_universe_candidates,
    parameter_sensitivity, run_kr_trend, walk_forward_years,
)

CACHE_DIR = root_path("cache", "kr_trend")
REPORT_DIR = root_path("reports")
WARMUP_START = "2015-06-01"       # 250 listed days + 60-day trading value before 2017-01-01 selection
INDEX_TICKERS = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"}


def load_universe_codes() -> list:
    with open(UNIVERSE_CACHE_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    return kr_universe_candidates(data)


def fetch_histories(codes, start, refresh=False, workers=8) -> dict:
    from data.history import get_historical_data
    os.makedirs(CACHE_DIR, exist_ok=True)
    out, todo = {}, []
    for code in codes:
        path = os.path.join(CACHE_DIR, f"{code.replace('^', 'IDX_')}.parquet")
        if not refresh and os.path.exists(path):
            out[code] = pl.read_parquet(path)
        else:
            todo.append((code, path))
    if todo:
        print(f"downloading {len(todo)} histories from {start} ...", flush=True)
        t0 = time.time()

        def _one(item):
            code, path = item
            df = get_historical_data(code, start)
            if not df.is_empty():
                df.write_parquet(path)
            return code, df

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for n, fut in enumerate(as_completed([ex.submit(_one, it) for it in todo]), 1):
                code, df = fut.result()
                out[code] = df
                if n % 50 == 0:
                    print(f"  {n}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    return out


def md_table(rows, cols, fmt=None) -> str:
    fmt = fmt or {}
    head = "| " + " | ".join(c for c, _ in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    lines = [head, sep]
    for r in rows:
        cells = []
        for _, key in cols:
            v = r.get(key) if isinstance(key, str) else key(r)
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(fmt.get(key, "{:.2f}").format(v) if isinstance(key, str) else f"{v:.2f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def years_table(res) -> str:
    cols = [("연도", "year"), ("연초 자산", "capital_start"), ("연말 평가액", "value_end"),
            ("수익률 %", "return_pct"), ("비용 후 %", "return_net_pct"), ("인출", "withdrawal"), ("투입", "topup"),
            ("MDD %", "mdd_pct"), ("트레이드", "n_trades"), ("승률 %", "win_rate_pct"), ("손익비", "profit_factor"),
            ("평균 보유일", "avg_days_held"), ("평균 종목수", "avg_n_positions"), ("평균 현금 %", "avg_cash_pct"),
            ("총비용", "total_cost"), ("0주 스킵", "n_skipped_zero_qty"),
            ("KOSPI %", "kospi_return_pct"), ("KOSDAQ %", "kosdaq_return_pct"), ("초과 %", "excess_vs_kospi_pct")]
    money = "{:,.0f}"
    fmt = {k: money for k in ("capital_start", "value_end", "withdrawal", "topup", "total_cost")}
    rows = [dict(r, year=f"{r['year']}{'' if r['complete'] else ' (YTD)'}") for r in res["years"]]
    return md_table(rows, cols, fmt)


def summary_lines(res) -> str:
    s = res["summary"]
    items = [
        ("누적 인출액", f"{s['cumulative_withdrawal']:,.0f}"), ("누적 추가 투입액", f"{s['cumulative_topup']:,.0f}"),
        ("최종 평가액", f"{s['final_equity']:,.0f}"), ("순손익", f"{s['net_pnl']:,.0f}"),
        ("연수익률 평균/중앙값/최소/최대 %",
         f"{s['annual_return_mean_pct']:.2f} / {s['annual_return_median_pct']:.2f} / {s['annual_return_min_pct']:.2f} / {s['annual_return_max_pct']:.2f}"),
        ("플러스 연도", f"{s['n_positive_years']} / {s['n_years']}"),
        ("KOSPI 대비 초과 연도", f"{s['n_years_beat_kospi']} / {s['n_years_with_kospi']}"),
        ("Sharpe (시간가중 일별, √252)", f"{s['sharpe']:.2f}"), ("연중 MDD 최댓값 %", f"{s['max_year_mdd_pct']:.2f}"),
        ("리스크 게이트 (Sharpe ≥ 1.5, MDD ≤ 15%)", "PASS" if s["passes_risk_gate"] else "FAIL"),
        ("트레이드 수 / 승률 % / 손익비", f"{s['n_trades']} / {s['win_rate_pct']:.1f} / {s['profit_factor']:.2f}"),
        ("연말 부분 청산 / 20% 축소 매도", f"{s['n_harvest_sales']} / {s['n_trim_sales']}"),
        ("총비용", f"{s['total_cost']:,.0f}"), ("기간", f"{s['start_date']} ~ {s['end_date']} ({s['n_days']}일, {s['n_tickers']}종목)"),
    ]
    return "\n".join(f"- {k}: {v}" for k, v in items) + "\n- 매년 초과수익을 인출하므로 복리 효과 없음."


def comparison_table(rows) -> str:
    cols = [("비교", "name"), ("연수익 중앙값 %", "median_return_pct"), ("최저 연도 %", "min_return_pct"),
            ("최저 연도", "min_return_year"), ("Sharpe", "sharpe"), ("MDD 최대 %", "max_year_mdd_pct"),
            ("플러스 연도", "n_positive_years"), ("KOSPI 초과 연도", "n_years_beat_kospi"),
            ("트레이드", "n_trades"), ("순손익", "net_pnl"), ("게이트", lambda r: "PASS" if r["passes_risk_gate"] else "FAIL")]
    return md_table(rows, cols, {"net_pnl": "{:,.0f}"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--refresh", action="store_true", help="re-download the parquet cache")
    ap.add_argument("--compare", action="store_true", help="run the 4장 1단계 assumption comparison")
    ap.add_argument("--sensitivity", action="store_true", help="run the 4장 2단계 parameter grid")
    ap.add_argument("--walk-forward", action="store_true", help="run the 4장 3단계 yearly walk-forward")
    ap.add_argument("--limit", type=int, default=0, help="only the first N codes (smoke test)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    codes = load_universe_codes()
    if args.limit:
        codes = codes[:args.limit]
    print(f"{len(codes)} KR codes from universe_cache.json", flush=True)
    frames = fetch_histories(codes + list(INDEX_TICKERS.values()), WARMUP_START, args.refresh, args.workers)
    kospi, kosdaq = frames.pop("^KS11"), frames.pop("^KQ11")
    histories = {c: df for c, df in frames.items() if df is not None and not df.is_empty()}
    print(f"{len(histories)} histories loaded; KOSPI rows {kospi.height}", flush=True)

    bad = abnormal_return_rows(histories)
    print(f"3-1 data check: {len(bad)} rows with |daily return| > 35%", flush=True)

    cfg = KrTrendConfig()
    benchmarks = {"KOSPI": kospi, "KOSDAQ": kosdaq}
    t0 = time.time()
    res = run_kr_trend(histories, kospi, cfg, args.start, args.end, benchmarks)
    print(f"base run: {time.time() - t0:.1f}s", flush=True)

    parts = [f"# KR Donchian 20/10 backtest — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"config: `{cfg.label()}`  start={args.start} end={args.end or 'latest'}", "",
             "## 연도별 표 (3-3)", "", years_table(res), "", "## 누적 요약 (3-3)", "", summary_lines(res), ""]
    if bad:
        parts += ["## 3-1 데이터 점검 — |일간 수익률| > 35% 행 (상위 30)", "",
                  md_table(bad[:30], [("종목", "ticker"), ("일자", "date"), ("수익률 %", "return_pct")]), ""]
    members_2017 = res["members"].get(int(args.start[:4]), [])
    parts += [f"편입 후보 {args.start[:4]}: {len(members_2017)}종목", ""]

    if args.compare:
        t0 = time.time()
        rows = compare_assumptions(histories, kospi, cfg, args.start, args.end, benchmarks=benchmarks)
        print(f"comparison: {time.time() - t0:.1f}s", flush=True)
        parts += ["## 4장 1단계 — 가정별 영향 비교", "", comparison_table(rows), ""]
    if args.sensitivity:
        t0 = time.time()
        rows = parameter_sensitivity(histories, kospi, cfg, args.start, args.end, benchmarks=benchmarks)
        print(f"sensitivity: {time.time() - t0:.1f}s", flush=True)
        parts += ["## 4장 2단계 — 파라미터 민감도", "", comparison_table(rows), ""]
    if args.walk_forward:
        t0 = time.time()
        wf = walk_forward_years(histories, kospi, cfg, args.start, args.end, first_oos_year=int(args.start[:4]) + 2,
                                benchmarks=benchmarks)
        print(f"walk-forward: {time.time() - t0:.1f}s", flush=True)
        fold_cols = [("OOS 연도", "year"), ("선택 파라미터", "chosen_label"), ("IS Sharpe", "is_metric"),
                     ("20/10 선택", lambda f: "Y" if f["is_base"] else "N"), ("OOS 수익률 %", "oos_return_pct"),
                     ("OOS MDD %", "oos_mdd_pct")]
        oos = wf["oos"] or {}
        parts += ["## 4장 3단계 — 연 단위 앵커드 워크포워드", "", md_table(wf["folds"], fold_cols), "",
                  f"OOS 합산: Sharpe {oos.get('sharpe', 0):.2f}, 연변동성 {oos.get('annual_vol_pct', 0):.2f}%, "
                  f"MDD {oos.get('max_drawdown_pct', 0):.2f}%, 20/10 선택 {wf['n_base_chosen']}/{len(wf['folds'])}회", ""]

    report = "\n".join(parts)
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = os.path.join(REPORT_DIR, f"kr_trend_{datetime.now():%Y%m%d_%H%M%S}.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(report)
    sys.stdout.reconfigure(encoding="utf-8")
    print(report)
    print(f"\nreport written to {out}")


if __name__ == "__main__":
    main()
