"""gemini_helper.py — Gemini API helpers for Portfolio Management.

Provides three features:
  1. portfolio_diagnosis(open_data, closed_data) → str
       Analyses risk diversification, performance, and sector concentration of held
       positions and returns an investment insight string.
  2. nl_to_filter(nl_query, columns) → dict | None
       Converts a natural-language filter query into a StockTable column-filter dict.
  3. stock_report_summary(item) → str
       3-line AI briefing for one Trading Universe row (roadmap 2-1, review.md 2-1).

Uses GOOGLE_API_KEY from .env.
"""

import os
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy SDK import — google-genai is optional; errors surface gracefully.
# ---------------------------------------------------------------------------
_genai_client = None   # google.genai.Client instance (shared singleton)


def _get_client():
    """Return a cached Gemini client, or raise RuntimeError with a friendly message."""
    global _genai_client
    if _genai_client is not None:
        return _genai_client

    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set.\n"
            "Add GOOGLE_API_KEY=<your-key> to the .env file."
        )

    try:
        from google import genai  # type: ignore
        _genai_client = genai.Client(api_key=api_key)
    except ImportError as e:
        raise RuntimeError(
            f"google-genai package not found (pip install google-genai): {e}"
        ) from e

    return _genai_client


_DEFAULT_MODEL = "gemini-2.5-flash"
_MODEL = os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _generate(prompt: str, *, json_mode: bool = False, model: str = None) -> str:
    """Call Gemini and return the text response."""
    client = _get_client()

    config_kwargs: dict[str, Any] = {}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    from google.genai import types as genai_types  # type: ignore

    chosen_model = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL

    response = client.models.generate_content(
        model=chosen_model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
    )
    return response.text or ""


# ---------------------------------------------------------------------------
# 1. Portfolio Diagnosis
# ---------------------------------------------------------------------------

def portfolio_diagnosis(open_data: list[dict], closed_data: list[dict]) -> str:
    """Analyse open/closed positions and return an investment insight string.

    Parameters
    ----------
    open_data   : list of open-position records (from TradingHistoryTab._open_data)
    closed_data : list of closed-position records (from TradingHistoryTab._closed_data)

    Returns
    -------
    Multi-line string with portfolio insights.
    """
    if not open_data and not closed_data:
        return "No position data to analyse."

    # ── Build a compact summary dict for the prompt ──────────────────────────
    open_summary = []
    for r in open_data:
        buy_amt   = float(r.get("buy_amount", 0) or 0)
        curr_pl   = float(r.get("curr_pl",    0) or 0)
        curr_pct  = float(r.get("curr_pl_pct", 0) or 0)
        curr_days = int(r.get("curr_days",    0) or 0)
        open_summary.append({
            "name":        r.get("company", ""),
            "market":      r.get("market",  ""),
            "buy_amount":  round(buy_amt),
            "current_pl":  round(curr_pl),
            "return_pct":  f"{curr_pct:.1f}%",
            "days_held":   curr_days,
        })

    closed_summary = []
    for r in closed_data:
        pl_val  = float(r.get("pl",       0) or 0)
        pl_pct  = float(r.get("pl_pct",   0) or 0)
        days    = int(r.get("days_held",  0) or 0)
        closed_summary.append({
            "name":         r.get("company", ""),
            "realized_pl":  round(pl_val),
            "return_pct":   f"{pl_pct:.1f}%",
            "days_held":    days,
        })

    # Market concentration (open positions only)
    from collections import Counter
    market_counter = Counter(r.get("market", "Other") for r in open_data)

    # Total buy amount and unrealized P/L
    total_buy = sum(float(r.get("buy_amount", 0) or 0) for r in open_data)
    total_pl  = sum(float(r.get("curr_pl",    0) or 0) for r in open_data)

    data_block = json.dumps(
        {
            "open_positions":       open_summary,
            "closed_positions":     closed_summary,
            "market_concentration": dict(market_counter),
            "total_buy_amount":     round(total_buy),
            "total_unrealized_pl":  round(total_pl),
        },
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""You are a professional investment advisor specialising in the Korean stock market.
Analyse the portfolio data below and provide insights useful to the investor.

# Portfolio Data
{data_block}

# Analysis Request
1. **Risk diversification**: Evaluate market/stock concentration and flag over-concentration or under-diversification.
2. **Performance**: Evaluate the ratio of winning vs. losing positions and the average holding period.
3. **Investment ideas**: Suggest 2-3 concrete improvements based on the current portfolio composition.

# Output Format
- Separate each section with an emoji header (e.g. 📊 Risk Diversification)
- Concise and practical, under 400 characters total
- State at the end that this is reference-only analysis, not investment advice
"""

    try:
        return _generate(prompt)
    except Exception as e:
        logger.warning("portfolio_diagnosis API call failed: %s", e, exc_info=True)
        return f"⚠️ An error occurred during AI analysis:\n{e}"


# ---------------------------------------------------------------------------
# 2. Natural-Language → Column Filter
# ---------------------------------------------------------------------------

# Column metadata that Gemini needs to map user queries correctly.
_COLUMN_METADATA = """
StockTable has the following columns (0-based index):
- 0  Name        : stock name (string)
- 2  Market      : market (KOSPI / KOSDAQ / NASDAQ 100 / S&P500)
- 3  Ticker      : ticker code (string)
- 4  MarketCap   : market cap in 100M KRW (number)
- 5  tPER        : trailing PER (number)
- 6  fPER        : forward PER (number)
- 7  Price       : current price (number)
- 8  Div20       : MA20 divergence % (number, base 100. e.g. 95.0 = -5% vs MA20)
- 9  Div50       : MA50 divergence % (number, base 100)
- 10 High52W     : 52-week high (number)
- 11 HighDiff    : % difference vs. 52-week high (number, negative = below the high)
- 12 Low52W      : 52-week low (number)
- 13 LowDiff     : % difference vs. 52-week low (number, positive = above the low)
- 14 Chg3D       : 3-day return % (number)
- 15 Chg5D       : 5-day return % (number)
- 16 Chg10D      : 10-day return % (number)
- 17 Chg20D      : 20-day return % (number)
- 18 Chg60D      : 60-day return % (number)
- 19 Chg120D     : 120-day return % (number)
"""

# JSON schema description for the structured response.
_NL_FILTER_SCHEMA = """
Return format (JSON):
{
  "text_filter": "text to put in the search box (stock name/ticker search, empty string if none)",
  "conditions": [
    {
      "col": <column index (int)>,
      "op":  "<  |  <=  |  ==  |  >=  |  >  |  contains",
      "val": <comparison value (number or string)>
    }
  ],
  "explanation": "one-line summary of the filter conditions"
}

Note: numeric columns take numbers; val for the Market column (2) is one of
"KOSPI", "KOSDAQ", "NASDAQ 100", "S&P500".
conditions may be empty (when using text_filter only).
"""


def stock_report_summary(item: dict) -> str:
    """3-line AI briefing for one Trading Universe row.

    Built from the metrics StockTable already has in memory (price, PER,
    MA20/50 divergence, 52-week high/low position, 3D~120D returns) rather
    than fetching a fresh OHLCV history -- the divergence/52-week fields
    already encode where price sits relative to recent structure, which is
    enough for a short support/resistance-flavored comment without an extra
    network round trip per click.
    """
    name = item.get("name", "")
    ticker = item.get("ticker", "")
    market = item.get("market", "")
    if not name and not ticker:
        return "No stock data available."

    price = item.get("usd_price") if item.get("currency") == "$" and "usd_price" in item else item.get("price", 0)
    changes = item.get("changes", {}) or {}

    data_block = json.dumps(
        {
            "name": name,
            "ticker": ticker,
            "market": market,
            "price": price,
            "per_trailing": item.get("trailing_per"),
            "per_forward": item.get("forward_per"),
            "ma20_divergence": changes.get("ma20_div"),
            "ma50_divergence": changes.get("ma50_div"),
            "high_52w": changes.get("52w_high"),
            "high_52w_diff_pct": changes.get("52w_high_diff"),
            "low_52w": changes.get("52w_low"),
            "low_52w_diff_pct": changes.get("52w_low_diff"),
            "return_3d": changes.get("3d"),
            "return_5d": changes.get("5d"),
            "return_10d": changes.get("10d"),
            "return_20d": changes.get("20d"),
            "return_60d": changes.get("60d"),
            "return_120d": changes.get("120d"),
        },
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""You are a professional analyst covering the Korean stock market.
Write a short briefing based on this stock's current metrics.

# Stock Data
{data_block}

# Writing Request
1. Summarise the recent trend and current position (vs. 52-week high/low, MA divergence) in one sentence.
2. A short one-sentence comment on valuation (PER).
3. Mention, in one sentence, reference support/resistance levels inferable from the data above
   (52-week high/low, moving averages, etc.).

# Output Format
- Exactly 3 lines, each starting with an emoji (e.g. 📈, 💰, 🎯)
- Under 200 characters total
- Briefly note at the end of the last line that this is reference-only analysis, not investment advice
"""

    try:
        return _generate(prompt)
    except Exception as e:
        logger.warning("stock_report_summary API call failed for ticker=%s: %s", ticker, e, exc_info=True)
        return f"⚠️ An error occurred while generating the AI report:\n{e}"


def nl_to_filter(nl_query: str) -> dict | None:
    """Convert a natural-language filter query to a structured filter spec.

    Returns
    -------
    dict with keys:
        text_filter : str          — search bar text
        conditions  : list[dict]   — [{col, op, val}, ...]
        explanation : str          — human-readable summary
    or None on failure.
    """
    prompt = f"""You are a stock screener query parser.
Convert the user's natural-language filter request into structured JSON, using the
column information below.

{_COLUMN_METADATA}

{_NL_FILTER_SCHEMA}

User input: "{nl_query}"
"""

    try:
        raw = _generate(prompt, json_mode=True)
        data = json.loads(raw)
        # Basic validation
        if not isinstance(data, dict):
            raise ValueError("Response is not a dict")
        data.setdefault("text_filter", "")
        data.setdefault("conditions", [])
        data.setdefault("explanation", "")
        return data
    except Exception as e:
        logger.warning("nl_to_filter API call failed: %s", e, exc_info=True)
        return None
