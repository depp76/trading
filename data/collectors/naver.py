"""data/collectors/naver.py — Naver Finance scraping and API fetchers."""
import json
import re
import io
import time
import ast
from datetime import datetime, date as _date
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import pandas as pd
import polars as pl
import logging

from data.cache import _KR3Y_CACHE, _MISC_CACHE_LOCK, _NAVER_SESSION, _pdf_is_stale, safe_float

logger = logging.getLogger(__name__)


def _fast_kr_history(ticker: str, start: str) -> pl.DataFrame:
    try:
        start_str = start.replace("-", "")
        end_str = datetime.now().strftime("%Y%m%d")
        url = f"https://api.finance.naver.com/siseJson.naver?symbol={ticker}&requestType=1&startTime={start_str}&endTime={end_str}&timeframe=day"
        
        res = None
        for attempt in range(3):
            try:
                res = _NAVER_SESSION.get(url, timeout=8)
                res.raise_for_status()
                break
            except Exception as e:
                if attempt == 2:
                    raise e
                time.sleep(1)
                
        text = res.text.replace('\n', '').replace('\t', '').replace(' ', '')
        text = re.sub(r'\'', '"', text)
        
        try:
            data = json.loads(text.strip())
        except Exception:
            logger.debug("json.loads failed for Naver history response, retrying with ast.literal_eval")
            data = ast.literal_eval(text.strip())

        if len(data) > 1:
            rows = []
            for row in data[1:]:
                try:
                    ymd = int(row[0])
                    d_obj = _date(ymd // 10000, (ymd % 10000) // 100, ymd % 100)
                    rows.append({
                        "Date": d_obj,
                        "Open": float(row[1]),
                        "High": float(row[2]),
                        "Low": float(row[3]),
                        "Close": float(row[4]),
                        "Volume": float(row[5])
                    })
                except Exception:
                    pass
            if rows:
                df = pl.DataFrame(rows)
                return df.unique(subset=["Date"], keep="last").sort("Date")
    except Exception as e:
        logger.debug("_fast_kr_history failed for ticker=%s: %s", ticker, e)
    return pl.DataFrame()


def fetch_naver_realtime_prices(tickers: list) -> dict:
    """Batch-fetch real-time prices for Korean stocks using Naver mobile polling API."""
    if not tickers:
        return {}
    results = {}
    chunk_size = 50
    chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]

    def _fetch_chunk(chunk):
        codes_str = ",".join(chunk)
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{codes_str}"
        try:
            r = _NAVER_SESSION.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                for item in data.get('datas', []):
                    code = item.get('itemCode', '')
                    price_str = str(item.get('closePrice', '0')).replace(',', '')
                    try:
                        results[code] = float(price_str)
                    except ValueError:
                        pass
        except Exception:
            logger.debug("Naver realtime prices fetch error", exc_info=True)

    with ThreadPoolExecutor(max_workers=min(len(chunks) or 1, 8)) as exe:
        list(exe.map(_fetch_chunk, chunks))
    return results


def fetch_naver_realtime_index_prices(symbols=("KOSPI", "KOSDAQ")) -> dict:
    """Batch-fetch real-time prices for Korean indices using Naver mobile polling API.

    Returns a dict mapping symbol aliases (e.g. 'KOSPI', '^KS11', 'KS11', 'KOSDAQ', '^KQ11', 'KQ11')
    to current index prices.
    """
    if not symbols:
        return {}
    query_codes = set()
    for s in symbols:
        s_clean = str(s).strip().upper()
        if s_clean in ("KOSPI", "^KS11", "KS11"):
            query_codes.add("KOSPI")
        elif s_clean in ("KOSDAQ", "^KQ11", "KQ11"):
            query_codes.add("KOSDAQ")

    results = {}
    for code in query_codes:
        url = f"https://polling.finance.naver.com/api/realtime/domestic/index/{code}"
        try:
            r = _NAVER_SESSION.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("datas", []):
                    item_code = item.get("itemCode", "").upper()
                    raw_val = item.get("closePriceRaw")
                    if raw_val is not None:
                        val = float(str(raw_val).replace(",", ""))
                        results[item_code] = val
                        if item_code == "KOSPI":
                            results["^KS11"] = val
                            results["KS11"] = val
                        elif item_code == "KOSDAQ":
                            results["^KQ11"] = val
                            results["KQ11"] = val
        except Exception:
            logger.debug("Failed to fetch Naver realtime index price for %s", code, exc_info=True)
    return results


def _fetch_naver_per_single(code: str) -> tuple:
    """Fetch trailing and forward PER for a single stock via Naver mobile integration API.

    Returns (code, trailing_per, forward_per).
    """
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/integration"
        r = _NAVER_SESSION.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            tper = None
            fper = None
            for item in data.get('totalInfos', []):
                key = item.get('key', '')
                val = item.get('value', '')
                if not val or val == '-':
                    continue
                num_str = re.sub(r'[^\d.]', '', val)
                if not num_str:
                    continue
                try:
                    fval = round(float(num_str), 1)
                except ValueError:
                    continue

                if key == 'PER':
                    tper = fval
                elif key == '추정PER':
                    fper = fval
            return code, tper, fper
    except Exception:
        logger.debug("_fetch_naver_per_single failed for code=%s", code, exc_info=True)
    return code, None, None


def fetch_naver_per_batch(codes: list, max_workers: int = 30) -> tuple:
    """Concurrently fetch trailing and forward PER for a list of Korean stock codes.

    Returns (trailing_per_dict, forward_per_dict).
    """
    if not codes:
        return {}, {}
    tper_map = {}
    fper_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(_fetch_naver_per_single, c): c for c in codes}
        for fut in as_completed(futures):
            try:
                code, tper, fper = fut.result()
                if tper is not None:
                    tper_map[code] = tper
                if fper is not None:
                    fper_map[code] = fper
            except Exception:
                pass
    return tper_map, fper_map


def _fetch_naver_info(code: str) -> tuple:
    """Fallback: fetch market cap and real name from Naver Finance item integration API or page."""
    # First attempt: mobile integration API (clean JSON, reliable)
    try:
        url_api = f"https://m.stock.naver.com/api/stock/{code}/integration"
        r = _NAVER_SESSION.get(url_api, timeout=5)
        if r.status_code == 200:
            data = r.json()
            name = data.get("stockName", "").strip()
            marcap = 0
            for item in data.get("totalInfos", []):
                if item.get("code") == "marketValue" or item.get("key") == "시총":
                    val = item.get("value", "")
                    if "조" in val:
                        parts = val.split("조")
                        jo = int(re.sub(r'[^\d]', '', parts[0]) or 0)
                        eok = int(re.sub(r'[^\d]', '', parts[1]) or 0) if len(parts) > 1 else 0
                        total_eok = jo * 10000 + eok
                    else:
                        cleaned = re.sub(r'[^\d]', '', val)
                        total_eok = int(cleaned) if cleaned else 0
                    marcap = total_eok * 100_000_000
                    break
            if name or marcap > 0:
                return name, marcap
    except Exception:
        logger.debug("Naver integration info fetch failed for code=%s", code, exc_info=True)

    # Second attempt: mobile basic API
    try:
        url_basic = f"https://m.stock.naver.com/api/stock/{code}/basic"
        r = _NAVER_SESSION.get(url_basic, timeout=5)
        if r.status_code == 200:
            data = r.json()
            name = data.get("stockName", "").strip()
            if name:
                return name, 0
    except Exception:
        logger.debug("Naver basic info fetch failed for code=%s", code, exc_info=True)

    # Third attempt: HTML scraping fallback
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        r = _NAVER_SESSION.get(url, timeout=5)
        name = ""
        n_match = re.search(r'<title>(.*?)[\s]*:', r.text)
        if n_match:
            name = n_match.group(1).strip()
        m = re.search(r'id=\"_market_sum\">([\s\n\t]*)(.*?)[\s\n\t]*</em>', r.text, re.DOTALL)
        marcap = 0
        if m:
            txt = m.group(2).replace(',', '').replace('\n', '').replace('\t', '').strip()
            if '조' in txt:  # '조' = Korean unit for 1 trillion (10^12)
                parts = txt.split('조')
                jo = int(re.sub(r'[^\d]', '', parts[0]) or 0)
                eok = int(re.sub(r'[^\d]', '', parts[1]) or 0) if len(parts) > 1 else 0
                total_eok = jo * 10000 + eok
            else:
                cleaned = re.sub(r'[^\d]', '', txt)
                total_eok = int(cleaned) if cleaned else 0
            marcap = total_eok * 100_000_000
        return name, marcap
    except Exception:
        logger.debug("Naver info fetch failed for code=%s", code, exc_info=True)
    return "", 0


def _fetch_kr_listing_naver(market, top_n):
    """Primary KR market-listing source: fetch top stocks sorted by market cap
    via Naver Mobile JSON API. Returns list of dicts: [{'Code', 'Name', 'Marcap'}]."""
    page_size = 100
    needed_pages = (top_n + page_size - 1) // page_size

    def _fetch_page(pg):
        try:
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={pg}&pageSize={page_size}"
            r = _NAVER_SESSION.get(url, timeout=8)
            if r.status_code != 200:
                logger.warning("Naver marketValue API returned status %d for %s page %d", r.status_code, market, pg)
                return []
            data = r.json()
            stocks = data.get("stocks", [])
            rows = []
            for s in stocks:
                code = str(s.get("itemCode", "")).strip().zfill(6)
                name = str(s.get("stockName", "")).strip()
                if not code or not name:
                    continue
                # marketValueRaw is the exact market capitalization in KRW
                raw_marcap = s.get("marketValueRaw")
                if raw_marcap is not None:
                    marcap = int(raw_marcap)
                else:
                    # Fallback to marketValue in 100M KRW (억)
                    mv_str = str(s.get("marketValue", "0")).replace(",", "")
                    cleaned = re.sub(r'[^\d]', '', mv_str)
                    marcap = int(cleaned) * 100_000_000 if cleaned else 0
                rows.append({"Code": code, "Name": name, "Marcap": marcap})
            return rows
        except Exception:
            logger.warning("Naver listing API fetch failed for market=%s page=%d", market, pg, exc_info=True)
            return []

    results_list = []
    with ThreadPoolExecutor(max_workers=min(needed_pages, 5)) as exe:
        for rows in exe.map(_fetch_page, range(1, needed_pages + 1)):
            results_list.extend(rows)

    return results_list[:top_n]


def _get_kr3y_df():
    """Fetch KR 3-year bond yield from Naver."""
    with _MISC_CACHE_LOCK:
        cached = _KR3Y_CACHE["df"]
        if cached is not None and not _pdf_is_stale(cached):
            return cached
        try:
            url_base = 'https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd=IRR_GOVT03Y&page='

            def fetch_page(p):
                res = _NAVER_SESSION.get(url_base + str(p), timeout=5)
                df = pd.read_html(io.StringIO(res.text))[0]
                return df.dropna(subset=[df.columns[1]])

            # Matches the page ceiling of the inline scraper this replaced (data/market.py,
            # pre-refactor) — that version scraped up to 39 pages with an early stop once it
            # crossed the caller's `start` date; fetching a fixed 20 pages here silently
            # truncated any lookback longer than ~200 rows. The result is day-cached, so the
            # extra pages are a one-time cost per day, not per call.
            with ThreadPoolExecutor(max_workers=10) as exe:
                dfs = list(exe.map(fetch_page, range(1, 40)))

            hist = pd.concat(dfs, ignore_index=True)
            hist.rename(columns={hist.columns[0]: 'Date', hist.columns[1]: 'Close'}, inplace=True)
            hist['Date'] = pd.to_datetime(hist['Date'].astype(str).str.replace('.', '-'))
            hist['Close'] = pd.to_numeric(hist['Close'], errors='coerce')
            hist = (hist.dropna(subset=['Date', 'Close'])
                        .sort_values('Date')
                        .set_index('Date'))
            _KR3Y_CACHE["df"] = hist
            return hist
        except Exception as e:
            logger.error("KR3Y fetch failed", exc_info=True)
            return cached


def _fetch_index_investor_trend(market: str, days: int = 60) -> list:
    """Fetch daily investor trend for KOSPI / KOSDAQ indices using Naver Mobile API."""
    url_price = f"https://m.stock.naver.com/api/index/{market}/price?pageSize={days}&page=1"
    try:
        res = _NAVER_SESSION.get(url_price, timeout=5)
        res.raise_for_status()
        price_data = res.json()
    except Exception as e:
        logger.error("Error fetching %s investor trend dates", market, exc_info=True)
        return []

    results = []

    def _fetch_day(item):
        date_str = item.get("localTradedAt", "")
        if not date_str:
            return None
        bizdate = date_str.replace("-", "")
        close_price = int(float(item.get("closePrice", "0").replace(",", "")))

        url_trend = f"https://m.stock.naver.com/api/index/{market}/trend?bizdate={bizdate}"
        try:
            r = _NAVER_SESSION.get(url_trend, timeout=5)
            r.raise_for_status()
            t_data = r.json()

            def _parse_val(val_str):
                if not val_str:
                    return 0
                return int(str(val_str).replace(',', '').replace('+', ''))

            foreign = _parse_val(t_data.get("foreignValue", "0"))
            inst = _parse_val(t_data.get("institutionalValue", "0"))
            retail = _parse_val(t_data.get("personalValue", "0"))

            return {
                "Date": date_str.replace("-", "."),
                "Close": close_price,
                "Foreigner": foreign,
                "Institution": inst,
                "Retail": retail,
            }
        except Exception:
            logger.debug("Investor trend day fetch failed for market=%s date=%s", market, date_str, exc_info=True)
            return None

    with ThreadPoolExecutor(max_workers=10) as exe:
        raw = list(exe.map(_fetch_day, price_data))
    results = [r for r in raw if r]
    results.sort(key=lambda x: x["Date"], reverse=True)
    return results


def _fetch_investor_trend_naver(ticker: str, days: int = 60) -> list:
    """Fallback: scrape net-purchase data from Naver Finance frgn.naver page."""
    if isinstance(ticker, str) and ticker.isdigit():
        ticker = ticker.zfill(6)
    elif isinstance(ticker, int):
        ticker = f"{ticker:06d}"

    url_base = f"https://finance.naver.com/item/frgn.naver?code={ticker}&page="
    num_pages = (days // 20) + 2

    def _fetch_page(page):
        page_rows = []
        try:
            res = _NAVER_SESSION.get(url_base + str(page), timeout=5)
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")
            tables = soup.select("table.type2")
            if len(tables) < 2:
                return page_rows
            for tr in tables[1].select("tr"):
                tds = tr.select("td")
                if len(tds) >= 9:
                    date = tds[0].text.strip()
                    if not date:
                        continue
                    try:
                        vol = int(tds[4].text.strip().replace(",", ""))
                        if vol == 0:
                            continue
                        inst = int(tds[5].text.strip().replace(",", ""))
                        foreign = int(tds[6].text.strip().replace(",", ""))
                        close_price = int(tds[1].text.strip().replace(",", ""))
                        retail = -(inst + foreign)
                        page_rows.append({
                            "Date": date,
                            "Close": close_price,
                            "Foreigner": foreign,
                            "Institution": inst,
                            "Retail": retail,
                            "InvestmentTrust": 0,
                            "PrivateEquity": 0,
                        })
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning("[_fetch_investor_trend_naver] Error for ticker=%s page=%d", ticker, page, exc_info=True)
        return page_rows

    rows = []
    with ThreadPoolExecutor(max_workers=min(num_pages, 8)) as exe:
        for page_rows in exe.map(_fetch_page, range(1, num_pages + 1)):
            rows.extend(page_rows)
            if len(rows) >= days:
                break

    return rows[:days]


def fetch_quarterly_financials(ticker: str, market: str):
    """Fetches the last 12 quarters (3 years) of income statement.
    Korean stocks: Uses Naver Finance Mobile API.
    US stocks: Uses YahooQuery.
    """
    try:
        if market in ("KOSPI", "KOSDAQ"):
            t_str = ticker.zfill(6)
            url = f'https://m.stock.naver.com/api/stock/{t_str}/finance/quarter'
            r = _NAVER_SESSION.get(url, timeout=5)
            if r.status_code != 200:
                return []
            data = r.json()
            if 'financeInfo' not in data or not data['financeInfo']:
                return []

            info = data['financeInfo']
            keys = []
            periods = []
            for p in info.get('trTitleList', []):
                keys.append(p['key'])
                title = p.get('title', '').replace('.', '-')
                if title.endswith('-'):
                    title = title[:-1]
                if len(title) == 7:  # YYYY-MM
                    title += '-31'   # roughly end of month
                periods.append(title)

            rev_dict, op_dict, net_dict = {}, {}, {}
            for row in info.get('rowList', []):
                if row.get('title') == '매출액':
                    rev_dict = row.get('columns', {})
                elif row.get('title') == '영업이익':
                    op_dict = row.get('columns', {})
                elif row.get('title') == '당기순이익':
                    net_dict = row.get('columns', {})

            rows = []
            for i, k in enumerate(keys):
                def parse_val(d_col):
                    if k not in d_col:
                        return 0.0
                    v_str = d_col[k].get('value')
                    if not v_str or v_str.strip() == '-':
                        return 0.0
                    return float(v_str.replace(',', '')) * 100000000

                rev = parse_val(rev_dict)
                op = parse_val(op_dict)
                net = parse_val(net_dict)

                op_margin = (op / rev * 100) if rev else 0.0
                net_margin = (net / rev * 100) if rev else 0.0

                rows.append({
                    'Date': periods[i],
                    'TotalRevenue': rev,
                    'OperatingIncome': op,
                    'NetIncome': net,
                    'OpMargin': op_margin,
                    'NetMargin': net_margin
                })

            rows.sort(key=lambda x: x['Date'], reverse=True)
            return rows[:12]

        else:
            from yahooquery import Ticker
            t_str = ticker.replace(".", "-")
            yq_ticker = Ticker(t_str)
            df = yq_ticker.income_statement("q")

            if isinstance(df, dict) or df is None or getattr(df, 'empty', True):
                return []

            if 'periodType' in df.columns:
                df = df[df['periodType'] == '3M']

            if 'asOfDate' in df.columns:
                df = df.sort_values(by='asOfDate', ascending=False).head(12)

            rows = []
            for idx, row in df.iterrows():
                d_val = row.get('asOfDate', '')
                if hasattr(d_val, 'strftime'):
                    d_val = d_val.strftime("%Y-%m-%d")
                else:
                    d_val = str(d_val)[:10]

                def get_val(key):
                    v = row.get(key, 0)
                    if pd.isna(v):
                        return 0.0
                    return float(v)

                rev = get_val('TotalRevenue')
                if rev == 0.0:
                    rev = get_val('OperatingRevenue')
                op = get_val('OperatingIncome')
                net = get_val('NetIncome')
                if net == 0.0:
                    net = get_val('NetIncomeCommonStockholders')

                op_margin = (op / rev * 100) if rev else 0.0
                net_margin = (net / rev * 100) if rev else 0.0

                rows.append({
                    'Date': str(d_val),
                    'TotalRevenue': rev,
                    'OperatingIncome': op,
                    'NetIncome': net,
                    'OpMargin': op_margin,
                    'NetMargin': net_margin
                })

            return rows
    except Exception as e:
        logger.error("Quarterly financials fetch failed for ticker=%s", ticker, exc_info=True)
        return []
