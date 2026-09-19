"""gemini_helper.py — Gemini API helpers for Portfolio Management.

Provides one feature:
  stock_report_summary(item) → str
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

    chosen_model = model or _MODEL

    response = client.models.generate_content(
        model=chosen_model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
    )
    return response.text or ""


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
- Write the briefing in Korean (the reader is a Korean-speaking investor)
- Exactly 3 lines, each starting with an emoji (e.g. 📈, 💰, 🎯)
- Under 200 characters total
- Briefly note at the end of the last line that this is reference-only analysis, not investment advice
"""

    try:
        return _generate(prompt)
    except Exception as e:
        logger.warning("stock_report_summary API call failed for ticker=%s: %s", ticker, e, exc_info=True)
        return f"⚠️ An error occurred while generating the AI report:\n{e}"
