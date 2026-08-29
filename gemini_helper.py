"""gemini_helper.py — Gemini API helpers for Portfolio Management.

Provides two features:
  1. portfolio_diagnosis(open_data, closed_data) → str
       Analyses risk diversification, performance, and sector concentration of held
       positions and returns Korean-language guidance (the prompt asks Gemini to
       respond in Korean, since this feature's output is meant for a Korean-speaking
       end user — see the prompt text in portfolio_diagnosis()).
  2. nl_to_filter(nl_query, columns) → dict | None
       Converts a natural-language filter query into a StockTable column-filter dict.

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
            "GOOGLE_API_KEY가 설정되지 않았습니다.\n"
            ".env 파일에 GOOGLE_API_KEY=<your-key>를 추가하세요."
        )

    try:
        from google import genai  # type: ignore
        _genai_client = genai.Client(api_key=api_key)
    except ImportError as e:
        raise RuntimeError(
            f"google-genai 패키지를 찾을 수 없습니다 (pip install google-genai): {e}"
        ) from e

    return _genai_client


_MODEL = "gemini-3.6-flash"


def _generate(prompt: str, *, json_mode: bool = False) -> str:
    """Call Gemini and return the text response."""
    client = _get_client()

    config_kwargs: dict[str, Any] = {}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    from google.genai import types as genai_types  # type: ignore

    response = client.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
    )
    return response.text or ""


# ---------------------------------------------------------------------------
# 1. Portfolio Diagnosis
# ---------------------------------------------------------------------------

def portfolio_diagnosis(open_data: list[dict], closed_data: list[dict]) -> str:
    """Analyse open/closed positions and return a Korean investment insight string.

    Parameters
    ----------
    open_data   : list of open-position records (from TradingHistoryTab._open_data)
    closed_data : list of closed-position records (from TradingHistoryTab._closed_data)

    Returns
    -------
    Korean multi-line string with portfolio insights.
    """
    if not open_data and not closed_data:
        return "분석할 포지션 데이터가 없습니다."

    # ── Build a compact summary dict for the prompt ──────────────────────────
    open_summary = []
    for r in open_data:
        buy_amt   = float(r.get("buy_amount", 0) or 0)
        curr_pl   = float(r.get("curr_pl",    0) or 0)
        curr_pct  = float(r.get("curr_pl_pct", 0) or 0)
        curr_days = int(r.get("curr_days",    0) or 0)
        open_summary.append({
            "종목명":  r.get("company", ""),
            "시장":    r.get("market",  ""),
            "매수금액": round(buy_amt),
            "현재P/L": round(curr_pl),
            "수익률":  f"{curr_pct:.1f}%",
            "보유일수": curr_days,
        })

    closed_summary = []
    for r in closed_data:
        pl_val  = float(r.get("pl",       0) or 0)
        pl_pct  = float(r.get("pl_pct",   0) or 0)
        days    = int(r.get("days_held",  0) or 0)
        closed_summary.append({
            "종목명":   r.get("company", ""),
            "실현P/L":  round(pl_val),
            "수익률":   f"{pl_pct:.1f}%",
            "보유일수": days,
        })

    # Market concentration (open positions only)
    from collections import Counter
    market_counter = Counter(r.get("market", "기타") for r in open_data)

    # Total buy amount and unrealized P/L
    total_buy = sum(float(r.get("buy_amount", 0) or 0) for r in open_data)
    total_pl  = sum(float(r.get("curr_pl",    0) or 0) for r in open_data)

    data_block = json.dumps(
        {
            "보유중_종목": open_summary,
            "청산완료_종목": closed_summary,
            "시장별_집중도": dict(market_counter),
            "총_매수금액": round(total_buy),
            "총_미실현손익": round(total_pl),
        },
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""당신은 한국 주식시장 전문 투자 어드바이저입니다.
아래 포트폴리오 데이터를 분석해 투자자에게 유용한 한국어 인사이트를 제공해 주세요.

# 포트폴리오 데이터
{data_block}

# 분석 요청
1. **리스크 분산도**: 시장/종목 집중도를 평가하고, 과집중 또는 분산 부족 여부를 지적해 주세요.
2. **성과 분석**: 수익 중인 종목과 손실 중인 종목의 비율, 평균 보유 기간을 평가해 주세요.
3. **투자 아이디어**: 현재 포트폴리오 구성을 바탕으로 구체적인 개선 방향 2~3가지를 제안해 주세요.

# 출력 형식
- 각 섹션은 이모지 헤더로 구분 (예: 📊 리스크 분산도)
- 간결하고 실용적으로, 총 400자 이내
- 투자 권유가 아닌 참고용 분석임을 마지막에 명시
"""

    try:
        return _generate(prompt)
    except Exception as e:
        logger.warning("portfolio_diagnosis API call failed: %s", e, exc_info=True)
        return f"⚠️ AI 분석 중 오류가 발생했습니다:\n{e}"


# ---------------------------------------------------------------------------
# 2. Natural-Language → Column Filter
# ---------------------------------------------------------------------------

# Column metadata that Gemini needs to map user queries correctly.
_COLUMN_METADATA = """
StockTable에는 다음 컬럼(0-based 인덱스)이 있습니다:
- 0  Name        : 종목명 (문자열)
- 2  Market      : 시장 (KOSPI / KOSDAQ / NASDAQ 100 / S&P500)
- 3  Ticker      : 종목코드 (문자열)
- 4  MarketCap   : 시가총액 억원 (숫자)
- 5  tPER        : 트레일링 PER (숫자)
- 6  fPER        : 포워드 PER (숫자)
- 7  Price       : 현재가 (숫자)
- 8  Div20       : MA20 이격도 % (숫자, 100 기준. 예: 95.0 = MA20 대비 -5%)
- 9  Div50       : MA50 이격도 % (숫자, 100 기준)
- 10 High52W     : 52주 고가 (숫자)
- 11 HighDiff    : 52주 고가 대비 차이 % (숫자, 음수=고점 아래)
- 12 Low52W      : 52주 저가 (숫자)
- 13 LowDiff     : 52주 저가 대비 차이 % (숫자, 양수=저점 위)
- 14 Chg3D       : 3일 수익률 % (숫자)
- 15 Chg5D       : 5일 수익률 % (숫자)
- 16 Chg10D      : 10일 수익률 % (숫자)
- 17 Chg20D      : 20일 수익률 % (숫자)
- 18 Chg60D      : 60일 수익률 % (숫자)
- 19 Chg120D     : 120일 수익률 % (숫자)
"""

# JSON schema description for the structured response.
_NL_FILTER_SCHEMA = """
반환 형식 (JSON):
{
  "text_filter": "검색창에 넣을 텍스트 (종목명/티커 검색, 없으면 빈 문자열)",
  "conditions": [
    {
      "col": <컬럼 인덱스(int)>,
      "op":  "<  |  <=  |  ==  |  >=  |  >  |  contains",
      "val": <비교값 (숫자 또는 문자열)>
    }
  ],
  "explanation": "필터 조건 요약 (한국어, 1줄)"
}

주의: 숫자형 컬럼은 숫자로, Market 컬럼(2)의 val은 "KOSPI", "KOSDAQ", "NASDAQ 100", "S&P500" 중 하나.
conditions가 비어 있어도 됩니다 (text_filter만 사용 시).
"""


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
    prompt = f"""당신은 주식 스크리너 쿼리 파서입니다.
사용자의 자연어 필터 요청을 아래 컬럼 정보를 참고하여 구조화된 JSON으로 변환하세요.

{_COLUMN_METADATA}

{_NL_FILTER_SCHEMA}

사용자 입력: "{nl_query}"
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
