# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project overview

A single-user PyQt6 desktop app for tracking a Korean/US equity portfolio: a "Trading Universe"
watchlist (KOSPI/KOSDAQ/NASDAQ 100/S&P 500), a manually-entered "Trading History" trade log, and
a "Total Assets" performance tab. Not a git repository — there is no `git`, so treat file edits
as final (no diff/revert safety net) and rely on the `archive/` backup convention below when
making risky changes.

## Running

All application source code lives under `src/` (mirroring the structure described below);
run from the repository root so relative paths to `.env`, `portfolio.db`, and the cache/state
JSON files (which stay at the repo root, not under `src/`) still resolve correctly.

```powershell
.\.venv\Scripts\python.exe src\main.py
```

There is no build step, linter config, or test suite in this repo. Verification is done ad hoc:
`py_compile` / `ast.parse` for syntax, and one-off smoke-test scripts (see
`CHANGELOG_optimization.md` for examples of the pattern used previously). When making non-trivial
changes to `data_fetcher.py` logic (e.g. `run_backtest_strategy`), write a throwaway script that
compares old vs. new behavior against random inputs before/after — do not assume correctness from
reading alone, since GUI behavior can't be exercised headlessly in this environment.

Because there's no VCS, before editing any of the three core files, copy the originals to
`archive/backup_<yyyyMMdd_HHmmss>/` first (matches the existing `archive/backup_2026*` folders).

## Architecture

Three files hold essentially all application logic:

- **`src/main.py`** (~5,900 lines) — PyQt6 UI: `MainWindow` (tabs), `StockTable`/`GroupedHeaderView`
  (Universe grid with per-column filter popups), `TradingHistoryTab` (trade entry/edit dialogs,
  backed by `trade_db`), `TradingRecordTab` (assets-over-time table + matplotlib graphs), plus
  several `QThread` subclasses (`AllDataFetchThread`, `UniverseLightweightFetchThread`,
  `PositionPriceFetchThread`, `RealtimePriceThread`, `IndexMaThread`, `StockMaThread`) that call
  into `data_fetcher.py` off the UI thread and emit signals back to update widgets. A
  60-second `QTimer` (`global_auto_timer`) drives auto-refresh of live prices/indices when the
  "Auto Update" checkbox is on.
- **`src/data_fetcher.py`** (~2,150 lines) — all external data access: market listings and OHLCV via
  `pykrx`/`FinanceDataReader`/`yfinance`/`yahooquery`, real-time quotes via Naver and Yahoo
  (`yf_quote_batch` is the shared batching/crumb/retry helper — reuse it rather than adding a new
  direct `yfinance` call site), Korea Investment & Securities (한국투자증권, KIS) Open API for
  account deposit, KR quotes/OHLCV, investor trend, and real-time KR prices (WebSocket),
  KRX derivatives API for VKOSPI. Uses `polars` internally for indicator/backtest computation
  (`_to_polars`, `_compute_indicators`, `run_backtest_strategy`) and converts to `pandas` at the
  boundary because upstream libraries only speak pandas. Has module-level caches
  (`_HIST_CACHE` as an `OrderedDict` LRU, `_YF_BULK_CACHE`, `_KIS_TOKEN_CACHE`,
  `_KIS_KEYS_CACHE`) — reuse these rather than adding parallel caching.
- **`src/trade_db.py`** — SQLite persistence (`portfolio.db`, WAL mode) for the trade log, replacing
  the older `custom_history.json` + `trade_overrides.json` pair (still read once, on first run,
  by `_migrate_legacy_json` for backward compatibility). Prefer `upsert_trades()` (batched,
  single transaction) over looping `upsert_trade()` when writing more than one record.

### External dependencies / credentials

- `.env` holds `KRX_AUTH_KEY` (KRX derivatives/VKOSPI API), loaded via `python-dotenv`.
- KIS (한국투자증권) Open API keys are **not** in this repo: `_get_kis_keys()` in
  `src/data/collectors/kis.py` reads them from `D:\Source Code\Trading MCP\kis_appkey.txt` /
  `kis_secretkey.txt` (real/실전투자 credentials) — an external sibling-project folder,
  renamed from `Kiwoom MCP` since it now holds keys for more than one brokerage API.
  The account number is read the same way, from `kis_account.txt` in the same folder
  (10 digits: 8-digit CANO + 2-digit product code) via `_get_kis_account()`, required
  for the deposit/balance lookup. Code touching KIS calls will fail without that
  folder present.
- `register_secret.py` is a standalone CLI helper for pushing secrets to Google Cloud Secret
  Manager (`gcloud secrets create/versions add`) — unrelated to the app's runtime secret loading.

### Local state / cache files (gitignored or otherwise not source)

`portfolio.db` (trade log, source of truth), `custom_settings.json` (Universe tab
added/deleted/highlighted tickers), `universe_cache.json`, `vkospi_cache.json`,
`custom_history.json` / `trade_overrides.json` (legacy, pre-SQLite), `trading_record.json`. These
are runtime data, not fixtures — don't treat their current contents as sample/test data to design
around.

## Conventions seen in this codebase

- New/edited menus, labels, and comments should be written in English (per user direction,
  2026-08-29) — do not introduce new Korean UI strings or comments even for financial terms.
  Existing Korean text already in the codebase (e.g. 예수금/평가손익) is left as-is unless asked
  to change it; this rule governs new and modified code going forward.
- Font handling goes through `create_font()` in `main.py` to keep Malgun Gothic Semilight
  consistent — don't set `QFont` directly in new widgets.
- Large table widgets (`StockTable`, `TradingRecordTab`'s tables) wrap bulk repaints in
  `setUpdatesEnabled(False)` / `finally: setUpdatesEnabled(True)` to avoid flicker/slowness —
  follow this pattern for any new bulk table population.
