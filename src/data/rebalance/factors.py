"""data/rebalance/factors.py — Raw factor extraction and z-score ranking (trading.md 11-2)."""
import numpy as np

# factor_name -> (extractor(universe_item) -> float | None, higher_is_better)
_REBALANCE_FACTORS = {
    "value_per":         (lambda it: it.get("trailing_per"), False),
    "ma20_momentum":     (lambda it: (it.get("changes") or {}).get("ma20_div"), True),
    "ma50_momentum":     (lambda it: (it.get("changes") or {}).get("ma50_div"), True),
    "ma20_slope_1w":     (lambda it: (it.get("changes") or {}).get("ma20_roc_1w"), True),
    "high52w_proximity": (lambda it: (it.get("changes") or {}).get("52w_high_diff"), True),
    "ret_20d":           (lambda it: (it.get("changes") or {}).get("20d"), True),
    "ret_60d":           (lambda it: (it.get("changes") or {}).get("60d"), True),
}
_REBALANCE_MIN_FACTORS = 3


def _extract_live_candidates(universe_data: list) -> list:
    """Step 1 of the rebalance pipeline: raw factor extraction from live
    Trading Universe rows. Skips index rows, entries with no ticker, and
    entries with fewer than _REBALANCE_MIN_FACTORS valid factors."""
    candidates = []
    for item in universe_data:
        if item.get("is_index"):
            continue
        ticker = item.get("ticker")
        if not ticker:
            continue
        raw = {}
        for fname, (extractor, _higher_better) in _REBALANCE_FACTORS.items():
            try:
                v = extractor(item)
                if v is not None:
                    v_float = float(v)
                    # Non-positive PER indicates net loss or invalid ratio; treat as None
                    # to prevent loss-making companies from scoring artificially high in value
                    if fname == "value_per" and v_float <= 0:
                        raw[fname] = None
                    else:
                        raw[fname] = v_float
                else:
                    raw[fname] = None
            except (TypeError, ValueError):
                raw[fname] = None
        if sum(1 for v in raw.values() if v is not None) < _REBALANCE_MIN_FACTORS:
            continue
        candidates.append({
            "ticker": ticker,
            "name": item.get("name", ticker),
            "market": item.get("market", ""),
            "raw": raw,
        })
    return candidates


def _score_and_rank(candidates: list) -> list:
    """Steps 2+3 of the rebalance pipeline: z-score each factor across
    `candidates` (sign-flipped so higher is always better), average into one
    composite score per stock, sort, and rank.
    """
    zscores = {fname: {} for fname in _REBALANCE_FACTORS}
    for fname, (_extractor, higher_better) in _REBALANCE_FACTORS.items():
        values = np.array(
            [c["raw"][fname] for c in candidates if c["raw"].get(fname) is not None],
            dtype=float,
        )
        if values.size == 0:
            continue
        mean = float(values.mean())
        std = float(values.std())
        for c in candidates:
            v = c["raw"].get(fname)
            if v is None:
                continue
            z = 0.0 if std == 0 else (v - mean) / std
            zscores[fname][c["ticker"]] = z if higher_better else -z

    ranked = []
    for c in candidates:
        z_vals = [
            zscores[fname][c["ticker"]]
            for fname in _REBALANCE_FACTORS
            if c["ticker"] in zscores[fname]
        ]
        score = float(np.mean(z_vals)) if z_vals else float("-inf")
        ranked.append({
            "ticker": c["ticker"],
            "name": c["name"],
            "market": c["market"],
            "score": score,
            "factors": {fname: zscores[fname].get(c["ticker"]) for fname in _REBALANCE_FACTORS},
            "raw": c["raw"],
        })

    ranked.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i

    # Per-market rank (1-based, within each market group) — used to classify
    # buy candidates market-by-market (e.g. top 10 KOSPI + top 10 KOSDAQ)
    # instead of one global cutoff that a single market could dominate.
    market_counts: dict = {}
    for r in ranked:
        m = r["market"]
        market_counts[m] = market_counts.get(m, 0) + 1
        r["market_rank"] = market_counts[m]

    return ranked
