"""data/collectors/kis.py — Korea Investment & Securities (KIS) REST/WebSocket
client for quotes, daily OHLCV, account balance, investor trend, and real-time prices.

Replaces data/collectors/kiwoom.py. Real (live) trading environment only.
"""
import os
import json
import threading
import time
import logging
from datetime import datetime, timedelta, timezone, time as dt_time

import pandas as pd

from paths import KIS_TOKEN_CACHE_FILE
from data.cache import _KIS_SESSION
from data.collectors.naver import _fetch_investor_trend_naver, _fetch_index_investor_trend

logger = logging.getLogger(__name__)

_KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
_KIS_WS_URL = "ws://ops.koreainvestment.com:21000"

_KIS_KEYS_CACHE: dict = {}
_KIS_TOKEN_CACHE: dict = {"token": None, "expires": 0}
_KIS_APPROVAL_CACHE: dict = {"approval_key": None, "expires": 0}
_KIS_TOKEN_LOCK = threading.Lock()
_KIS_APPROVAL_LOCK = threading.Lock()

_KIS_TOKEN_CACHE_PATH = KIS_TOKEN_CACHE_FILE

# Proactively reissue the access token / approval_key this long before their actual
# expiry, rather than waiting until they're about to lapse. Since _get_kis_token()
# and _get_kis_approval_key() are called on every REST/WS call (e.g. the 60s
# real-time price loop), any call inside this window naturally triggers a refresh —
# no separate scheduler needed.
_KIS_TOKEN_REFRESH_MARGIN_SEC = 6 * 3600
_KIS_APPROVAL_ASSUMED_LIFETIME_SEC = 24 * 3600

_KST = timezone(timedelta(hours=9))


def is_krx_market_open() -> bool:
    """Check if the Korean stock market (KRX) is currently open for regular trading.
    Regular trading hours: Monday through Friday, 09:00 - 15:30 KST.
    """
    now_kst = datetime.now(_KST)
    if now_kst.weekday() >= 5:  # Saturday (5) or Sunday (6)
        return False
    current_time = now_kst.time()
    return dt_time(9, 0) <= current_time <= dt_time(15, 30)


def _get_kis_keys():
    """Reads appkey and appsecret from the KIS_KEY_PATH folder (default: the
    Trading MCP sibling folder, an external-secrets location shared across
    brokerage-API projects on this machine), using an in-memory cache to avoid
    repeated disk I/O."""
    if "appkey" in _KIS_KEYS_CACHE:
        return _KIS_KEYS_CACHE["appkey"], _KIS_KEYS_CACHE["appsecret"]

    key_dir = os.environ.get("KIS_KEY_PATH", r"D:\Source Code\Trading MCP")
    appkey_path = os.path.join(key_dir, "kis_appkey.txt")
    secret_path = os.path.join(key_dir, "kis_secretkey.txt")
    if not os.path.exists(appkey_path) or not os.path.exists(secret_path):
        raise FileNotFoundError(f"KIS API key files not found in {key_dir}")
    with open(appkey_path, "r", encoding="utf-8") as f:
        appkey = f.read().strip()
    with open(secret_path, "r", encoding="utf-8") as f:
        appsecret = f.read().strip()

    _KIS_KEYS_CACHE["appkey"] = appkey
    _KIS_KEYS_CACHE["appsecret"] = appsecret
    return appkey, appsecret


def _get_kis_account():
    """Reads the account number (10 digits: 8-digit CANO + 2-digit product code)
    from kis_account.txt in the KIS_KEY_PATH folder, and splits it into
    (cano, acnt_prdt_cd).

    Re-read from disk on every call rather than cached forever: unlike appkey/
    appsecret (used on every request but never edited live), this file is the one
    most likely to get corrected in place after a first-time typo, and KIS's deposit
    API has no equivalent to the old Kiwoom flow's live ka00001 account-list lookup
    to catch that — the file read itself is a few bytes and negligible overhead next
    to the network call each caller makes right after this.
    """
    key_dir = os.environ.get("KIS_KEY_PATH", r"D:\Source Code\Trading MCP")
    account_path = os.path.join(key_dir, "kis_account.txt")
    if not os.path.exists(account_path):
        raise FileNotFoundError(f"KIS account number file not found: {account_path}")
    with open(account_path, "r", encoding="utf-8") as f:
        acct = f.read().strip().replace("-", "").replace(" ", "")
    if len(acct) != 10 or not acct.isdigit():
        raise ValueError(
            f"{account_path} must contain 10 digits (8-digit CANO + 2-digit "
            f"product code, e.g. 1234567801 or 12345678-01) — found {acct!r}."
        )
    return acct[:8], acct[8:]


def _kis_oauth_post(path: str, json_payload: dict, max_attempts: int = 3):
    """POST to a KIS OAuth endpoint (token/approval-key issuance) with a short
    retry for transient network errors, mirroring naver._fast_kr_history's
    retry pattern (roadmap 2026-09-18, review.md 3-3).

    Only smooths over connection errors/timeouts and real HTTP error statuses.
    KIS returns issuance failures (bad keys, rate-limited) as HTTP 200 with an
    error payload, which _get_kis_token()/_get_kis_approval_key() still raise
    on immediately after this returns -- retrying those instantly would just
    fail again given KIS's ~once/minute-per-appkey issuance limit.
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            res = _KIS_SESSION.post(
                f"{_KIS_BASE_URL}{path}",
                headers={"content-type": "application/json; charset=utf-8"},
                json=json_payload,
                timeout=5,
            )
            res.raise_for_status()
            return res
        except Exception as e:
            last_exc = e
            if attempt == max_attempts - 1:
                raise
            logger.debug(
                "KIS OAuth POST %s failed (attempt %d/%d), retrying: %s",
                path, attempt + 1, max_attempts, e,
            )
            time.sleep(1)
    raise last_exc


def _load_kis_token_cache() -> dict:
    if os.path.exists(_KIS_TOKEN_CACHE_PATH):
        try:
            with open(_KIS_TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.debug("Failed to load KIS token cache file, starting fresh", exc_info=True)
    return {}


def _save_kis_token_cache(cache: dict):
    try:
        temp_path = _KIS_TOKEN_CACHE_PATH + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(temp_path, _KIS_TOKEN_CACHE_PATH)
    except Exception:
        logger.warning("KIS token cache save error", exc_info=True)


def _get_kis_token():
    """Returns a cached or newly-fetched KIS REST API access token.

    KIS rate-limits token issuance to roughly once per minute per appkey, so the
    token is persisted to disk (kis_token_cache.json) in addition to the in-memory
    cache, to survive app restarts within the ~24h token lifetime. The cached
    "expires" is set _KIS_TOKEN_REFRESH_MARGIN_SEC (6h) before the token's real
    expiry, so any call made within that window transparently reissues a fresh
    token instead of risking one that expires mid-session.
    """
    with _KIS_TOKEN_LOCK:
        now = time.time()
        if _KIS_TOKEN_CACHE["token"] and now < _KIS_TOKEN_CACHE["expires"]:
            appkey, appsecret = _get_kis_keys()
            return _KIS_TOKEN_CACHE["token"], appkey, appsecret

        appkey, appsecret = _get_kis_keys()

        disk_cache = _load_kis_token_cache()
        if disk_cache.get("token") and now < disk_cache.get("expires", 0):
            _KIS_TOKEN_CACHE["token"] = disk_cache["token"]
            _KIS_TOKEN_CACHE["expires"] = disk_cache["expires"]
            return disk_cache["token"], appkey, appsecret

        res = _kis_oauth_post(
            "/oauth2/tokenP",
            {"grant_type": "client_credentials", "appkey": appkey, "appsecret": appsecret},
        )
        data = res.json()
        token = data.get("access_token")
        if not token:
            reason = data.get("error_description") or data.get("msg1") or str(data)
            raise ValueError(
                f"Failed to issue KIS access token: {reason} "
                "(check kis_appkey.txt/kis_secretkey.txt in the KIS_KEY_PATH folder are "
                "correct and current, that the account is registered for real/live "
                "Open API access, and that token issuance hasn't been rate-limited — "
                "KIS allows roughly one issuance per minute per appkey)."
            )
        expires_in = int(data.get("expires_in", 86400))
        expires = now + max(expires_in - _KIS_TOKEN_REFRESH_MARGIN_SEC, 60)
        _KIS_TOKEN_CACHE["token"] = token
        _KIS_TOKEN_CACHE["expires"] = expires
        _save_kis_token_cache({"token": token, "expires": expires})
        return token, appkey, appsecret


def _get_kis_approval_key():
    """Returns a cached or newly-fetched approval_key for WebSocket subscriptions
    (valid ~24h; separate from the REST access token)."""
    with _KIS_APPROVAL_LOCK:
        now = time.time()
        if _KIS_APPROVAL_CACHE["approval_key"] and now < _KIS_APPROVAL_CACHE["expires"]:
            return _KIS_APPROVAL_CACHE["approval_key"]

        appkey, appsecret = _get_kis_keys()
        res = _kis_oauth_post(
            "/oauth2/Approval",
            {"grant_type": "client_credentials", "appkey": appkey, "secretkey": appsecret},
        )
        data = res.json()
        approval_key = data.get("approval_key")
        if not approval_key:
            raise ValueError(f"Failed to issue KIS approval_key: {data}")
        _KIS_APPROVAL_CACHE["approval_key"] = approval_key
        _KIS_APPROVAL_CACHE["expires"] = now + max(
            _KIS_APPROVAL_ASSUMED_LIFETIME_SEC - _KIS_TOKEN_REFRESH_MARGIN_SEC, 60
        )
        return approval_key


def _kis_headers(tr_id: str) -> dict:
    token, appkey, appsecret = _get_kis_token()
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": tr_id,
        "custtype": "P",
    }


def fetch_kis_stock_info(code: str):
    """Fetches current price, name, market cap via KIS inquire-price (FHKST01010100).

    Confirmed against live output (2026-09-13): this TR does not return a Korean
    stock name field, so `name` is always "" here. Not an issue for current
    callers (the app already has ticker->name from get_stock_listing()).
    """
    headers = _kis_headers("FHKST01010100")
    r = _KIS_SESSION.get(
        f"{_KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=headers,
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        timeout=5,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("rt_cd") != "0":
        return None
    out = d.get("output", {})
    price = int(out.get("stck_prpr", "0") or "0")
    market_cap = 0
    try:
        # hts_avls is in 100M KRW units.
        market_cap = int(float(out.get("hts_avls", "0") or "0") * 100_000_000)
    except (ValueError, TypeError):
        pass
    return {
        "name": out.get("hts_kor_isnm", ""),
        "price": price,
        "market_cap": market_cap,
        "open": int(out.get("stck_oprc", "0") or "0"),
        "high": int(out.get("stck_hgpr", "0") or "0"),
        "low": int(out.get("stck_lwpr", "0") or "0"),
    }


def fetch_kis_daily_ohlcv(code: str, days: int = 30):
    """Fetches recent daily OHLCV via KIS inquire-daily-itemchartprice (FHKST03010100).
    Returns pd.DataFrame."""
    headers = _kis_headers("FHKST03010100")
    end = datetime.now()
    start = end - timedelta(days=int(days * 1.6) + 10)  # pad for weekends/holidays
    r = _KIS_SESSION.get(
        f"{_KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        headers=headers,
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        },
        timeout=5,
    )
    r.raise_for_status()
    d = r.json()
    items = d.get("output2", [])
    if not items:
        return None
    rows = []
    for item in items:
        try:
            dt = pd.to_datetime(item["stck_bsop_date"], format="%Y%m%d")
            rows.append({
                "Date": dt,
                "Open": int(item.get("stck_oprc", "0") or "0"),
                "High": int(item.get("stck_hgpr", "0") or "0"),
                "Low": int(item.get("stck_lwpr", "0") or "0"),
                "Close": int(item.get("stck_clpr", "0") or "0"),
                "Volume": int(item.get("acml_vol", "0") or "0"),
            })
        except Exception:
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    return df.tail(days).reset_index(drop=True)


def fetch_account_deposit() -> float:
    """Fetches the account deposit (cash balance) via KIS inquire-balance (TTTC8434R).
    Unlike the old Kiwoom flow, KIS balance inquiry needs no account password/OTP."""
    cano, acnt_prdt_cd = _get_kis_account()
    headers = _kis_headers("TTTC8434R")
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    r = _KIS_SESSION.get(
        f"{_KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=headers, params=params, timeout=5,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("rt_cd") != "0":
        raise Exception(f"[inquire-balance] KIS API error: {d.get('msg1', d)}")
    output2 = d.get("output2", [])
    if not output2:
        return 0.0
    row = output2[0]

    def _to_float(s):
        try:
            return float(str(s).replace(",", "").strip() or "0")
        except (ValueError, TypeError):
            return 0.0

    primary = _to_float(row.get("dnca_tot_amt", "0"))
    secondary = _to_float(row.get("prvs_rcdl_excc_amt", "0"))
    # Mirrors the old Kiwoom flow's rule: don't trust a "0" in the primary deposit
    # field when a secondary field shows a real balance — a plain truthy-OR chain
    # would never even look at the secondary field here, since the string "0" is
    # truthy in Python even though it's numerically zero.
    if primary == 0.0 and secondary > 0.0:
        return secondary
    return primary


def fetch_investor_trend(ticker: str, days: int = 60) -> list:
    """Fetches investor net-purchase data via KIS inquire-investor (FHKST01010900).

    Falls back to Naver scraping if the KIS call fails or returns nothing.

    Confirmed against live output (2026-09-13): this TR only breaks investors into
    Foreigner/Institution/Retail (frgn_ntby_qty/orgn_ntby_qty/prsn_ntby_qty) — unlike
    Kiwoom's ka10059, it has no separate InvestmentTrust/PrivateEquity sub-fields, so
    those two columns are always 0 here (StockMaDialog already hides zero-only columns).
    """
    if isinstance(ticker, str) and ticker.isdigit():
        ticker = ticker.zfill(6)
    elif isinstance(ticker, int):
        ticker = f"{ticker:06d}"

    if ticker in ("KS11", "KQ11", "KOSPI", "KOSDAQ", "^KS11", "^KQ11"):
        market_str = "KOSPI" if ticker in ("KS11", "KOSPI", "^KS11") else "KOSDAQ"
        return _fetch_index_investor_trend(market_str, days)

    try:
        headers = _kis_headers("FHKST01010900")
        r = _KIS_SESSION.get(
            f"{_KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=headers,
            params={"FID_INPUT_ISCD": ticker, "FID_COND_MRKT_DIV_CODE": "J"},
            timeout=8,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("rt_cd") != "0":
            raise Exception(d.get("msg1", "unknown error"))
        items = d.get("output", [])
    except Exception:
        logger.warning("[fetch_investor_trend] KIS inquire-investor failed, falling back to Naver", exc_info=True)
        return _fetch_investor_trend_naver(ticker, days)

    rows = []
    for item in items[:days]:
        dt_raw = str(item.get("stck_bsop_date", "")).strip()
        if len(dt_raw) < 8:
            continue
        date_str = f"{dt_raw[:4]}.{dt_raw[4:6]}.{dt_raw[6:8]}"
        try:
            close_price = abs(int(item.get("stck_clpr", "0") or "0"))
        except ValueError:
            close_price = 0

        def _signed(key):
            try:
                return int(str(item.get(key, "0") or "0").replace(",", "").strip())
            except ValueError:
                return 0

        rows.append({
            "Date": date_str,
            "Close": close_price,
            "Foreigner": _signed("frgn_ntby_qty"),
            "Institution": _signed("orgn_ntby_qty"),
            "Retail": _signed("prsn_ntby_qty"),
            "InvestmentTrust": _signed("ivtr_ntby_qty"),
            "PrivateEquity": _signed("orgn_prvt_ntby_qty"),
        })

    if not rows:
        logger.warning("[fetch_investor_trend] KIS returned no rows, falling back to Naver")
        return _fetch_investor_trend_naver(ticker, days)

    return rows


# Hybrid realtime path: the WebSocket session is opened fresh on every 60 s
# refresh (connect, subscribe, wait for ticks, unsubscribe, close), which only
# pays off once there are enough tickers to amortise it. Below this count a
# handful of REST quotes (parallel, ~0.3 s) beats a handshake plus up to
# _KIS_WS_TIMEOUT s of waiting for a tick that a thin stock may never print.
_KIS_WS_MIN_TICKERS = 4
_KIS_WS_TIMEOUT = 3.0


def fetch_kis_realtime_prices(tickers: list, timeout: float = _KIS_WS_TIMEOUT) -> dict:
    """Opens a short-lived KIS WebSocket session, subscribes to real-time trade ticks
    (H0STCNT0) for each KR ticker, and collects the first tick price seen per ticker
    within `timeout` seconds. Tickers with no tick in that window (e.g. no trades yet,
    or market closed) fall back to a per-ticker REST quote via fetch_kis_stock_info.

    Meant for small ticker sets (open/recently-closed positions), not bulk watchlists.
    Fewer than _KIS_WS_MIN_TICKERS tickers, or a closed market, skip the WebSocket
    entirely and go straight to REST.
    """
    prices: dict = {}
    if not tickers:
        return prices

    # Outside regular KRX market hours (e.g. weekends, nights), no ticks are generated.
    # Bypass the WebSocket connection and immediately use REST fallback to avoid timeout delays.
    if not is_krx_market_open():
        logger.debug("[fetch_kis_realtime_prices] KRX is closed; bypassing WebSocket and using REST fallback")
        return _kis_rest_price_fallback(tickers, {})

    if len(tickers) < _KIS_WS_MIN_TICKERS:
        logger.debug("[fetch_kis_realtime_prices] %d ticker(s): REST is cheaper than a WebSocket session", len(tickers))
        return _kis_rest_price_fallback(tickers, {})

    try:
        import websocket  # websocket-client
    except ImportError:
        logger.warning("[fetch_kis_realtime_prices] websocket-client not installed, using REST fallback for all tickers")
        return _kis_rest_price_fallback(tickers, {})

    try:
        approval_key = _get_kis_approval_key()
        ws = websocket.create_connection(_KIS_WS_URL, timeout=timeout)
    except Exception:
        logger.warning("[fetch_kis_realtime_prices] WebSocket connect failed, using REST fallback", exc_info=True)
        return _kis_rest_price_fallback(tickers, {})

    try:
        for code in tickers:
            sub = {
                "header": {"approval_key": approval_key, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
                "body": {"input": {"tr_id": "H0STCNT0", "tr_key": code}},
            }
            ws.send(json.dumps(sub))

        deadline = time.time() + timeout
        pending = set(tickers)
        while pending and time.time() < deadline:
            ws.settimeout(max(0.1, deadline - time.time()))
            try:
                frame = ws.recv()
            except Exception:
                break

            if isinstance(frame, str) and frame.startswith("{"):
                # JSON control frame — echo PINGPONG to keep the connection alive.
                try:
                    msg = json.loads(frame)
                    if msg.get("header", {}).get("tr_id") == "PINGPONG":
                        ws.send(frame)
                except Exception:
                    logger.debug("[fetch_kis_realtime_prices] ignoring unparseable control frame", exc_info=True)
                continue

            # Pipe-delimited real-time data frame: "0|H0STCNT0|<count>|field^field^..."
            # Field order per KIS docs: [0]=stock code, [2]=current price (execution price). Only the first
            # record's fields are read here (data_count>1 frames are rare for a single
            # subscribed ticker); confirmed against a live capture before relying on this.
            parts = frame.split("|")
            if len(parts) < 4 or parts[1] != "H0STCNT0":
                continue
            fields = parts[3].split("^")
            if len(fields) < 3:
                continue
            code = fields[0]
            if code in pending:
                try:
                    prices[code] = float(fields[2])
                    pending.discard(code)
                except (ValueError, IndexError):
                    continue
    finally:
        try:
            for code in tickers:
                unsub = {
                    "header": {"approval_key": approval_key, "custtype": "P", "tr_type": "2", "content-type": "utf-8"},
                    "body": {"input": {"tr_id": "H0STCNT0", "tr_key": code}},
                }
                ws.send(json.dumps(unsub))
        except Exception:
            logger.debug("[fetch_kis_realtime_prices] unsubscribe failed (connection likely gone)", exc_info=True)
        try:
            ws.close()
        except Exception:
            logger.debug("[fetch_kis_realtime_prices] ws.close() failed", exc_info=True)

    missing = [t for t in tickers if t not in prices]
    if missing:
        prices.update(_kis_rest_price_fallback(missing, prices))
    return prices


def _kis_rest_price_fallback(tickers: list, existing: dict) -> dict:
    out = {}
    if not tickers:
        return out

    def _fetch_one(code):
        try:
            info = fetch_kis_stock_info(code)
            # `is not None` (not a truthy check on price): a halted or not-yet-traded
            # ticker can legitimately report price 0, and that's still a real answer —
            # `if info.get("price")` would silently drop it as if the fetch had failed.
            if info is not None:
                return code, float(info.get("price", 0))
        except Exception:
            logger.debug("[fetch_kis_realtime_prices] REST fallback failed for %s", code, exc_info=True)
        return code, None

    if len(tickers) == 1:
        c, p = _fetch_one(tickers[0])
        if p is not None:
            out[c] = p
        return out

    from concurrent.futures import ThreadPoolExecutor, as_completed
    max_workers = min(len(tickers), 5)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, code): code for code in tickers}
        for future in as_completed(futures):
            try:
                c, p = future.result()
                if p is not None:
                    out[c] = p
            except Exception:
                logger.debug("[fetch_kis_realtime_prices] REST fallback worker for %s raised", futures[future], exc_info=True)
    return out
