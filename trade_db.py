"""
trade_db.py – SQLite-backed trading history database.

Replaces the fragmented JSON approach (custom_history.json + trade_overrides.json)
with a single, ACID-compliant SQLite database (portfolio.db).

Schema
------
trades
  id           INTEGER  PRIMARY KEY AUTOINCREMENT
  orig_key     TEXT     UNIQUE  – "Company_BuyDate_Qty" natural key (backwards-compat)
  company      TEXT     NOT NULL
  market       TEXT
  ticker       TEXT
  buy_date     TEXT     NOT NULL  – YYYY-MM-DD
  buy_price    REAL
  qty          REAL
  buy_amount   REAL
  sell_date    TEXT               – YYYY-MM-DD or ''
  sell_price   REAL
  sell_qty     REAL
  sell_amount  REAL
  is_custom    INTEGER  DEFAULT 1 – 1 = manually entered, 0 = imported from XLS
  created_at   TEXT               – ISO-8601 timestamp
  updated_at   TEXT               – ISO-8601 timestamp
"""

import sqlite3
import json
import os
import datetime as _dt
from pathlib import Path

_DB_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.db")
_CUSTOM_JSON   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_history.json")
_OVERRIDES_JSON= os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_overrides.json")
_BASE_DIR      = os.path.dirname(os.path.abspath(__file__))


# ── Connection helper ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    """Return a connection with row_factory and WAL mode enabled."""
    conn = sqlite3.connect(_DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Schema initialisation ──────────────────────────────────────────────────────

def init_db() -> None:
    """Create the trades table if it doesn't exist, then run all migrations."""
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                orig_key     TEXT    UNIQUE,
                company      TEXT    NOT NULL,
                market       TEXT    DEFAULT '',
                ticker       TEXT    DEFAULT '',
                buy_date     TEXT    NOT NULL,
                buy_price    REAL    DEFAULT 0.0,
                qty          REAL    DEFAULT 0.0,
                buy_amount   REAL    DEFAULT 0.0,
                sell_date    TEXT    DEFAULT '',
                sell_price   REAL    DEFAULT 0.0,
                sell_qty     REAL    DEFAULT 0.0,
                sell_amount  REAL    DEFAULT 0.0,
                is_custom    INTEGER DEFAULT 1,
                created_at   TEXT    DEFAULT (datetime('now','localtime')),
                updated_at   TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_buy_date ON trades(buy_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_sell_date ON trades(sell_date)")
        conn.commit()
        # 1. Legacy JSON migration (custom_history.json + trade_overrides.json)
        _migrate_legacy_json(conn)
    finally:
        conn.close()


# ── Migration from JSON ────────────────────────────────────────────────────────

def _migrate_legacy_json(conn: sqlite3.Connection) -> None:
    """One-time migration: import custom_history.json + trade_overrides.json into DB.
    Skips records that already exist (idempotent)."""

    # ── Load overrides to patch buy-side records from JSON ──────────────────
    overrides: dict = {}
    if os.path.exists(_OVERRIDES_JSON):
        try:
            with open(_OVERRIDES_JSON, "r", encoding="utf-8") as f:
                overrides = json.load(f)
        except Exception as e:
            print(f"[trade_db] Warning: could not read trade_overrides.json: {e}")

    # ── Load custom_history.json ─────────────────────────────────────────────
    custom_records: list = []
    if os.path.exists(_CUSTOM_JSON):
        try:
            with open(_CUSTOM_JSON, "r", encoding="utf-8") as f:
                custom_records = json.load(f)
        except Exception as e:
            print(f"[trade_db] Warning: could not read custom_history.json: {e}")

    # ── First: import override entries (these are the canonical closed trades) ─
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    imported = 0
    for key, ov in overrides.items():
        co = ov.get("company", "")
        if not co:
            continue
        try:
            conn.execute("""
                INSERT OR IGNORE INTO trades
                    (orig_key, company, market, ticker,
                     buy_date, buy_price, qty, buy_amount,
                     sell_date, sell_price, sell_qty, sell_amount,
                     is_custom, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
            """, (
                key, co,
                ov.get("market", ""), ov.get("ticker", ""),
                ov.get("buy_date", ""), float(ov.get("buy_price", 0)),
                float(ov.get("qty", 0)), float(ov.get("buy_amount", 0)),
                ov.get("sell_date", ""), float(ov.get("sell_price", 0)),
                float(ov.get("sell_qty", 0)), float(ov.get("sell_amount", 0)),
                now, now,
            ))
            imported += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            print(f"[trade_db] Migration error for override '{key}': {e}")

    # ── Second: import custom_history.json (open positions not yet in DB) ─────
    for rec in custom_records:
        co = rec.get("company", "")
        if not co:
            continue
        buy_date = rec.get("buy_date", "")
        qty = float(rec.get("qty", 0))
        key = rec.get("orig_key") or f"{co}_{buy_date}_{qty}"

        # If this record already has an override entry, skip (already imported above)
        if key in overrides:
            continue

        try:
            conn.execute("""
                INSERT OR IGNORE INTO trades
                    (orig_key, company, market, ticker,
                     buy_date, buy_price, qty, buy_amount,
                     sell_date, sell_price, sell_qty, sell_amount,
                     is_custom, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
            """, (
                key, co,
                rec.get("market", ""), rec.get("ticker", ""),
                buy_date, float(rec.get("buy_price", 0)),
                qty, float(rec.get("buy_amount", 0)),
                rec.get("sell_date", ""), float(rec.get("sell_price", 0)),
                float(rec.get("sell_qty", 0)), float(rec.get("sell_amount", 0)),
                now, now,
            ))
            imported += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            print(f"[trade_db] Migration error for custom trade '{key}': {e}")

    conn.commit()
    if imported:
        print(f"[trade_db] Migrated {imported} legacy record(s) into portfolio.db")


# ── CRUD helpers ───────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict matching the in-memory record format."""
    d = dict(row)
    # Derived / runtime fields (computed elsewhere, not stored)
    d.setdefault("pl", 0.0)
    d.setdefault("pl_pct", 0.0)
    d.setdefault("days_held", 0)
    d.setdefault("curr_days", 0)
    d.setdefault("curr_price", 0.0)
    d.setdefault("curr_pl", 0.0)
    d.setdefault("curr_pl_pct", 0.0)
    d.setdefault("curr_pct_pl", 0.0)
    d.setdefault("position_w", 0.0)
    d.setdefault("wk1", 0.0)
    d.setdefault("wk2", 0.0)
    d.setdefault("mth1", 0.0)
    return d


def load_all_trades() -> list[dict]:
    """Return all trades sorted by buy_date ascending."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY buy_date ASC, id ASC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def upsert_trade(record: dict) -> str:

    """Insert or update a trade. Returns the orig_key of the saved record."""
    co = record.get("company", "")
    buy_date = record.get("buy_date", "")
    qty = float(record.get("qty", 0))
    key = record.get("orig_key") or f"{co}_{buy_date}_{qty}"
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        conn.execute("""
            INSERT INTO trades
                (orig_key, company, market, ticker,
                 buy_date, buy_price, qty, buy_amount,
                 sell_date, sell_price, sell_qty, sell_amount,
                 is_custom, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
            ON CONFLICT(orig_key) DO UPDATE SET
                company     = excluded.company,
                market      = excluded.market,
                ticker      = excluded.ticker,
                buy_date    = excluded.buy_date,
                buy_price   = excluded.buy_price,
                qty         = excluded.qty,
                buy_amount  = excluded.buy_amount,
                sell_date   = excluded.sell_date,
                sell_price  = excluded.sell_price,
                sell_qty    = excluded.sell_qty,
                sell_amount = excluded.sell_amount,
                updated_at  = excluded.updated_at
        """, (
            key, co,
            record.get("market", ""), record.get("ticker", ""),
            buy_date, float(record.get("buy_price", 0)),
            qty, float(record.get("buy_amount", 0)),
            record.get("sell_date", ""), float(record.get("sell_price", 0)),
            float(record.get("sell_qty", 0)), float(record.get("sell_amount", 0)),
            now, now,
        ))
        conn.commit()
        return key
    finally:
        conn.close()


def upsert_trades(records: list[dict]) -> list[str]:
    """Insert or update many trades in a single connection/transaction.
    Returns the list of orig_keys that were saved, in input order.
    """
    if not records:
        return []

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    keys: list[str] = []
    rows = []
    for record in records:
        co = record.get("company", "")
        buy_date = record.get("buy_date", "")
        qty = float(record.get("qty", 0))
        key = record.get("orig_key") or f"{co}_{buy_date}_{qty}"
        keys.append(key)
        rows.append((
            key, co,
            record.get("market", ""), record.get("ticker", ""),
            buy_date, float(record.get("buy_price", 0)),
            qty, float(record.get("buy_amount", 0)),
            record.get("sell_date", ""), float(record.get("sell_price", 0)),
            float(record.get("sell_qty", 0)), float(record.get("sell_amount", 0)),
            now, now,
        ))

    conn = _connect()
    try:
        conn.executemany("""
            INSERT INTO trades
                (orig_key, company, market, ticker,
                 buy_date, buy_price, qty, buy_amount,
                 sell_date, sell_price, sell_qty, sell_amount,
                 is_custom, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
            ON CONFLICT(orig_key) DO UPDATE SET
                company     = excluded.company,
                market      = excluded.market,
                ticker      = excluded.ticker,
                buy_date    = excluded.buy_date,
                buy_price   = excluded.buy_price,
                qty         = excluded.qty,
                buy_amount  = excluded.buy_amount,
                sell_date   = excluded.sell_date,
                sell_price  = excluded.sell_price,
                sell_qty    = excluded.sell_qty,
                sell_amount = excluded.sell_amount,
                updated_at  = excluded.updated_at
        """, rows)
        conn.commit()
        return keys
    finally:
        conn.close()


def delete_trade(orig_key: str) -> bool:
    """Delete a trade by its orig_key. Returns True if a row was deleted."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM trades WHERE orig_key = ?", (orig_key,))
        conn.commit()
        deleted = conn.execute("SELECT changes()").fetchone()[0]
        return deleted > 0
    finally:
        conn.close()


def get_trade(orig_key: str) -> dict | None:
    """Fetch a single trade by orig_key."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM trades WHERE orig_key = ?", (orig_key,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_open_trades() -> list[dict]:
    """Return trades with no sell_date (open positions)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE (sell_date IS NULL OR sell_date = '') ORDER BY buy_date ASC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_closed_trades() -> list[dict]:
    """Return trades with a sell_date (closed positions)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE sell_date IS NOT NULL AND sell_date != '' ORDER BY sell_date ASC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def db_path() -> str:
    """Return the absolute path to the database file."""
    return _DB_FILE
