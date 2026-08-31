"""data/collectors/kiwoom.py — Kiwoom REST API client for quotes, balance, and investor trends."""
import os
import threading
import time
from datetime import datetime
import pandas as pd
import logging

from data.cache import _KIWOOM_SESSION
from data.collectors.naver import _fetch_investor_trend_naver, _fetch_index_investor_trend

logger = logging.getLogger(__name__)

_KIWOOM_TOKEN_CACHE: dict = {"token": None, "expires": 0}
_KIWOOM_KEYS_CACHE: dict = {}
_KIWOOM_TOKEN_LOCK = threading.Lock()


def _get_kiwoom_keys():
    """Reads appkey and secretkey from the Kiwoom MCP folder, using cache to avoid disk I/O."""
    if "appkey" in _KIWOOM_KEYS_CACHE:
        return _KIWOOM_KEYS_CACHE["appkey"], _KIWOOM_KEYS_CACHE["secretkey"]

    key_dir = os.environ.get("KIWOOM_KEY_PATH", r"D:\Source Code\Kiwoom MCP")
    appkey_path = os.path.join(key_dir, "45573900_appkey.txt")
    secret_path = os.path.join(key_dir, "45573900_secretkey.txt")
    if not os.path.exists(appkey_path) or not os.path.exists(secret_path):
        raise FileNotFoundError(f"API Key files not found in {key_dir}")
    with open(appkey_path, "r", encoding="utf-8") as f:
        appkey = f.read().strip()
    with open(secret_path, "r", encoding="utf-8") as f:
        appsecret = f.read().strip()

    _KIWOOM_KEYS_CACHE["appkey"] = appkey
    _KIWOOM_KEYS_CACHE["secretkey"] = appsecret
    return appkey, appsecret


def _get_kiwoom_token():
    """Returns a cached or newly-fetched Kiwoom REST API token."""
    with _KIWOOM_TOKEN_LOCK:
        now = time.time()
        if _KIWOOM_TOKEN_CACHE["token"] and now < _KIWOOM_TOKEN_CACHE["expires"]:
            appkey, appsecret = _get_kiwoom_keys()
            return _KIWOOM_TOKEN_CACHE["token"], appkey, appsecret

        appkey, appsecret = _get_kiwoom_keys()
        token_url = "https://api.kiwoom.com/oauth2/token"
        body = {"grant_type": "client_credentials", "appkey": appkey, "secretkey": appsecret}
        res = _KIWOOM_SESSION.post(token_url, headers={"content-type": "application/json;charset=UTF-8"}, json=body, timeout=5)
        res.raise_for_status()
        res_data = res.json()
        token = res_data.get("access_token") or res_data.get("token")
        if not token:
            msg = res_data.get("return_msg", "Unknown error")
            code = res_data.get("return_code", "N/A")
            if str(code) == "3" and "8050" in msg:
                raise ValueError(f"Kiwoom Securities designated terminal authentication failed (8050 error).\nPlease register 'Designated PC' in OpenAPI details on the Kiwoom homepage and try again.\n(Message: {msg})")
            raise ValueError(f"Failed to issue Kiwoom Securities API token: {msg} (Error Code: {code})")
        _KIWOOM_TOKEN_CACHE["token"] = token
        _KIWOOM_TOKEN_CACHE["expires"] = now + 3500  # ~1 hour
        return token, appkey, appsecret


def _kiwoom_parse_price(val_str):
    """Parses Kiwoom price strings that may have +/- prefix."""
    if not val_str or not val_str.strip():
        return 0
    return abs(int(val_str.replace("+", "").replace("-", "").replace(",", "").strip()))


def _kiwoom_parse_signed_int(val: str) -> int:
    """Parses a signed integer string from Kiwoom API (may include +/- prefix)."""
    if not val or not val.strip():
        return 0
    try:
        cleaned = val.replace(",", "").strip()
        return int(cleaned)
    except ValueError:
        return 0


def fetch_kiwoom_stock_info(code, token=None, appkey=None, appsecret=None):
    """Fetches current price, name, market cap via Kiwoom ka10007."""
    if not token:
        token, appkey, appsecret = _get_kiwoom_token()
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "ka10007"
    }
    r = _KIWOOM_SESSION.post("https://api.kiwoom.com/api/dostk/mrkcond",
                      headers=headers, json={"stk_cd": code}, timeout=5)
    r.raise_for_status()
    d = r.json()
    if d.get("return_code", -1) != 0:
        return None
    price = _kiwoom_parse_price(d.get("cur_prc", ""))
    flo_stkcnt = int((d.get("flo_stkcnt", "0") or "0").strip())
    marcap = flo_stkcnt * 1000 * price if price > 0 else 0
    return {
        "name": d.get("stk_nm", ""),
        "price": price,
        "market_cap": marcap,
        "open": _kiwoom_parse_price(d.get("open_pric", "")),
        "high": _kiwoom_parse_price(d.get("high_pric", "")),
        "low": _kiwoom_parse_price(d.get("low_pric", "")),
    }


def fetch_kiwoom_daily_ohlcv(code, token=None, appkey=None, appsecret=None):
    """Fetches ~30 days of daily OHLCV via Kiwoom ka10005. Returns pd.DataFrame."""
    if not token:
        token, appkey, appsecret = _get_kiwoom_token()
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "ka10005"
    }
    r = _KIWOOM_SESSION.post("https://api.kiwoom.com/api/dostk/mrkcond",
                      headers=headers, json={"stk_cd": code}, timeout=5)
    r.raise_for_status()
    d = r.json()
    items = d.get("stk_ddwkmm", [])
    if not items:
        return None
    rows = []
    for item in items:
        try:
            dt = pd.to_datetime(item["date"], format="%Y%m%d")
            rows.append({
                "Date": dt,
                "Open": _kiwoom_parse_price(item.get("open_pric", "")),
                "High": _kiwoom_parse_price(item.get("high_pric", "")),
                "Low": _kiwoom_parse_price(item.get("low_pric", "")),
                "Close": _kiwoom_parse_price(item.get("close_pric", "")),
                "Volume": int(item.get("trde_qty", "0") or "0"),
            })
        except Exception:
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    return df


def fetch_account_deposit(pwd: str = "") -> float:
    """
    Fetches the account deposit (available cash balance) using Kiwoom REST API kt00001.
    Reuses cached token and API keys.
    """
    access_token, appkey, appsecret = _get_kiwoom_token()

    # ── Step 1: Retrieve account number list (ka00001) → verify actual 10-digit acnt_no ──────
    # Fallback account number (8-digit account + "01" suffix), used only if the
    # ka00001 lookup below fails. Read from env rather than hardcoded so the
    # account number isn't committed to source control.
    acnt_no = os.environ.get("KIWOOM_ACCOUNT_NO", "")
    try:
        list_headers = {
            "content-type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {access_token}",
            "appkey": appkey,
            "secretkey": appsecret,
            "api-id": "ka00001",
        }
        list_res = _KIWOOM_SESSION.post(
            "https://api.kiwoom.com/api/dostk/acnt",
            headers=list_headers,
            json={},
            timeout=5,
        )
        if list_res.status_code == 200:
            list_data = list_res.json()
            logger.debug("[ka00001 Account list response] keys=%s", list(list_data.keys()))
            acct_list = list_data.get("acnt_list") or list_data.get("acctList") or []
            if acct_list:
                first = acct_list[0]
                acnt_no = (
                    first.get("acnt_no")
                    or first.get("acno")
                    or first.get("acctNo")
                    or acnt_no
                )
    except Exception:
        logger.warning("[ka00001] Account list lookup failed (ignoring)", exc_info=True)

    # ── Step 2: Fetch available cash deposit (kt00001) ─────────────────────────────────────
    inquire_url = "https://api.kiwoom.com/api/dostk/acnt"
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {access_token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "kt00001",
    }
    params = {
        "acnt_no": acnt_no,
        "pwd": pwd,
        "qry_tp": "1",
    }
    logger.debug("[kt00001 Request] qry_tp=1")
    dep_res = _KIWOOM_SESSION.post(inquire_url, headers=headers, json=params, timeout=5)
    dep_res.raise_for_status()
    dep_data = dep_res.json()
    logger.debug("[kt00001 Response] return_code=%s keys=%s", dep_data.get("return_code"), list(dep_data.keys()))

    if str(dep_data.get("return_code", "-1")) == "0":
        dep_keys = ["d2_entra", "d2_entr", "pymn_alow_amt", "ord_alow_amt", "entr"]
        deposit_str = "0"

        for k in dep_keys:
            if k in dep_data:
                val_str = str(dep_data[k]).strip()
                if val_str:
                    deposit_str = val_str
                    val_int = 0.0
                    try:
                        val_int = float(deposit_str.replace(",", ""))
                    except Exception:
                        logger.debug(
                            "Deposit field '%s' value not parseable as float: %r",
                            k, deposit_str, exc_info=True,
                        )

                    if k in ("d2_entra", "d2_entr") and val_int == 0.0:
                        pymn_str = str(dep_data.get("pymn_alow_amt", "0") or "0").strip()
                        try:
                            pymn_val = float(pymn_str.replace(",", ""))
                        except Exception:
                            pymn_val = 0.0
                        if pymn_val > 10.0:
                            continue
                    break

        return float(deposit_str.replace(",", "").strip())
    else:
        msg = dep_data.get("return_msg", "Unknown error")
        code = dep_data.get("return_code", "N/A")
        raise Exception(f"[kt00001] API Error (Code: {code}): {msg}")


def fetch_investor_trend(ticker: str, days: int = 60) -> list:
    """Fetches investor net-purchase data via Kiwoom REST API (ka10059).

    Falls back to Naver scraping if Kiwoom token is unavailable.
    """
    if isinstance(ticker, str) and ticker.isdigit():
        ticker = ticker.zfill(6)
    elif isinstance(ticker, int):
        ticker = f"{ticker:06d}"

    if ticker in ("KS11", "KQ11", "KOSPI", "KOSDAQ", "^KS11", "^KQ11"):
        market_str = "KOSPI" if ticker in ("KS11", "KOSPI", "^KS11") else "KOSDAQ"
        return _fetch_index_investor_trend(market_str, days)

    try:
        token, appkey, appsecret = _get_kiwoom_token()
    except Exception:
        logger.warning("[fetch_investor_trend] Kiwoom token failed, falling back to Naver", exc_info=True)
        return _fetch_investor_trend_naver(ticker, days)

    url = "https://api.kiwoom.com/api/dostk/stkinfo"
    base_headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "secretkey": appsecret,
        "api-id": "ka10059",
    }
    stk_cd = str(ticker).zfill(6)

    rows = []
    cont_yn = "N"
    next_key = ""
    today_str = datetime.now().strftime("%Y%m%d")

    while len(rows) < days:
        headers = {**base_headers}
        if cont_yn == "Y":
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        body = {
            "dt": today_str,
            "stk_cd": stk_cd,
            "amt_qty_tp": "2",
            "trde_tp": "0",
            "unit_tp": "1",
        }

        try:
            res = _KIWOOM_SESSION.post(url, headers=headers, json=body, timeout=8)
            res.raise_for_status()
            data = res.json()
        except Exception:
            logger.warning("[fetch_investor_trend] Kiwoom ka10059 request error", exc_info=True)
            break

        if str(data.get("return_code", -1)) != "0":
            logger.warning("[fetch_investor_trend] Kiwoom ka10059 error: %s", data.get('return_msg'))
            break

        items = data.get("stk_invsr_orgn", [])
        if not items:
            break

        for item in items:
            dt_raw = item.get("dt", "").strip()
            if not dt_raw or len(dt_raw) < 8:
                continue
            date_str = f"{dt_raw[:4]}.{dt_raw[4:6]}.{dt_raw[6:8]}"

            close_str = item.get("cur_prc", "0").replace("+", "").replace("-", "").replace(",", "")
            try:
                close_price = abs(int(close_str))
            except ValueError:
                close_price = 0

            acc_qty_str = item.get("acc_trde_qty", "0").replace(",", "").strip()
            try:
                acc_qty = abs(int(acc_qty_str))
            except ValueError:
                acc_qty = 0

            if acc_qty == 0 and close_price == 0:
                continue

            foreigner = _kiwoom_parse_signed_int(item.get("frgnr_invsr", "0"))
            institution = _kiwoom_parse_signed_int(item.get("orgn", "0"))
            retail = _kiwoom_parse_signed_int(item.get("ind_invsr", "0"))
            invst_trust = _kiwoom_parse_signed_int(item.get("invst_trust", "0"))
            pe_fund = _kiwoom_parse_signed_int(
                item.get("pe_fund") or item.get("pvt_eqt_fund") or item.get("prvt_eqt_fund") or item.get("pef", "0")
            )

            rows.append({
                "Date": date_str,
                "Close": close_price,
                "Foreigner": foreigner,
                "Institution": institution,
                "Retail": retail,
                "InvestmentTrust": invst_trust,
                "PrivateEquity": pe_fund,
            })

            if len(rows) >= days:
                break

        resp_headers = res.headers
        cont_yn = resp_headers.get("cont-yn", "N")
        next_key = resp_headers.get("next-key", "")
        if cont_yn != "Y":
            break

    if not rows:
        logger.warning("[fetch_investor_trend] Kiwoom returned no rows or failed, falling back to Naver")
        return _fetch_investor_trend_naver(ticker, days)

    return rows
