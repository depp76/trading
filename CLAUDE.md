# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A single-user PyQt6 desktop app for tracking a Korean/US equity portfolio, with four tabs:
"Trading Universe" (KOSPI/KOSDAQ watchlist with live prices and indicators; the US market
code paths still exist but are commented out in the UI), "Trading History" (manually-entered
trade log backed by SQLite), "Total Assets" (weekly asset snapshots vs. KOSPI and USD), and
"Auto Trading" (weekly factor-scoring rebalance signals plus a walk-forward backtest; signal
generation only, it never places orders).

The repo is a git repository (branch `master`). Commit or branch as usual; the old
`archive/backup_<timestamp>/` copy-before-editing convention is no longer needed.
`AutoBackupThread` still writes `archive/auto_<timestamp>/` snapshots of `portfolio.db` and
`custom_settings.json` on every start (last 7 kept); `archive/` is gitignored.

## Running

All source lives under `src/`. Runtime files (`.env`, `portfolio.db`, the cache/state JSON
files, `app.log`, `archive/`) live at the repo root and are resolved through `src/paths.py`
(absolute paths derived from the module location), so the app behaves the same from any
working directory:

```powershell
.\.venv\Scripts\python.exe src\main.py
```

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest src\tests -q      # ~120 tests, no network, ~10 s
.\.venv\Scripts\ruff.exe check src                      # pyflakes rules only (ruff.toml)
```

Run pytest from the repo root or from `src/` (`tests/conftest.py` puts `src/` on `sys.path`).
Tests patch the implementation modules (`data.cache`, `data.market`, `data.collectors.yahoo`),
never names on the `data_fetcher` facade. GUI behaviour cannot be exercised headlessly here;
for non-trivial changes to fetch/backtest logic write a throwaway script comparing old vs.
new behaviour on random inputs (see `changelog_optimization.md` for the pattern), and see
`test_plan.md` for the manual checks. Dev tooling is in `requirements-dev.txt`
(`-r requirements.txt` + pytest + ruff); runtime pins are in `requirements.txt`.

## Architecture

- **`src/main.py`** (~300 lines): `MainWindow` builds the four tabs, wires cross-tab
  signals, owns the 60-second `global_auto_timer` (the only auto-refresh timer; the "Auto
  Update" checkbox starts and stops it and everything downstream), the app stylesheet, and
  logging setup (root INFO; `app.log` gets INFO and above, the console WARNING and above).
- **`src/ui/`**: `universe_tab.py` (`UniverseTab`), `history_tab.py` (`TradingHistoryTab`),
  `assets_tab.py` (`TradingRecordTab`), `auto_trading_tab.py` (`AutoTradingTab`),
  `widgets.py` (`StockTable`, `FilterPopup`, `GroupedHeaderView`), `dialogs.py` (charts and
  trade edit dialogs), `common.py` (`create_font`, `FONT_FAMILY_CSS`, input validators,
  `atomic_save_json` / `safe_load_json`, `retire_thread`). Tabs never reference each other
  directly; `MainWindow` connects their signals (`status_message`, `refresh_started`,
  `auto_lightweight_tick`, `total_asset_updated`). The one exception is `AutoTradingTab`,
  which reads `UniverseTab.all_data` on demand.
- **`src/threads/`**: every network call the UI triggers runs in a `QThread` subclass here
  (`AllDataFetchThread`, `UniverseLightweightFetchThread`, `PositionPriceFetchThread`,
  `RealtimePriceThread`, `StockMaThread`, `AccountDepositThread`, `RebalanceBacktestThread`,
  the Gemini threads, `AutoBackupThread`). Never call `data_fetcher` functions from a slot on
  the UI thread; add a thread class instead. Connect `finished` signals to bound methods,
  not closures, so Qt queues them onto the UI thread.
- **`src/data/`**: all external data access, split by concern.
  `cache.py` (HTTP sessions, `_HIST_CACHE` LRU with `_HIST_CACHE_STATS`, `start_date()`,
  `is_kr_code()`, `safe_float`), `indicators.py` (polars indicator maths,
  `fetch_historical_changes`), `market.py` (listing lookup, `get_historical_data`, market
  aggregation, index MAs), `collectors/` (`naver.py`; `yahoo.py`, where `yf_quote_batch`
  is the one Yahoo quote entry point; `kis.py`; `krx.py`). Pure data access only: uses
  polars internally and converts to pandas only at library boundaries, reuses the
  module-level caches rather than adding parallel ones, and never imports `strategy`.
- **`src/strategy/`**: all trading-strategy logic (`strategy/rebalance/rebalance.md` 11-5).
  **Rule (user direction, 2026-09-17): every strategy is its own sub-package
  `src/strategy/<name>/` and its design/spec document is saved as `<name>.md` inside that
  same folder.** A sub-package has a `config.py` (parameters), `signals.py`, `backtest.py`
  and an `__init__.py` facade; tests go in `tests/strategy/<name>/`; docstrings and commit
  messages cite the md section numbers, and the md is updated in the same commit as the
  code. Current members: `rebalance/` (weekly factor scoring, classification, walk-forward
  backtest; `rebalance.md`), `ma_cross/` (single-stock MA20/MA60 golden-cross backtest;
  `ma_cross.md`), and `trend_following/` (Donchian breakout; `__init__.py` scaffold only,
  full spec in `trend_following.md`, code not yet written). Strategy code
  imports from `data.*`; callers import strategy symbols from `strategy.<name>` directly,
  never via `data_fetcher`.
- **`src/data_fetcher.py`**: a pure re-export facade over `data/` (data access only, no
  strategy symbols) so UI and thread code import from one place. Nothing in `data/`
  imports it back; keep it that way.
- **`src/trade_db.py`**: SQLite persistence (`portfolio.db`, WAL mode). Prefer
  `upsert_trades()` for batches. Do not generate `orig_key` values in callers:
  `upsert_trade()` without a key claims a collision-free one inside the INSERT and writes it
  back into the record.
- **`src/gemini_helper.py`**: Gemini calls for the AI filter and diagnosis features
  (`GOOGLE_API_KEY` in `.env`; the prompts are intentionally Korean).

### External dependencies / credentials

- `.env` (repo root, loaded via `paths.ENV_FILE`): `KRX_AUTH_KEY`, `GOOGLE_API_KEY`, and
  optionally `GEMINI_MODEL`.
- KIS (한국투자증권) Open API keys are **not** in this repo: `data/collectors/kis.py` reads
  `kis_appkey.txt`, `kis_secretkey.txt` and `kis_account.txt` (10 digits: 8-digit CANO plus
  2-digit product code) from `KIS_KEY_PATH` (default `D:\Source Code\Trading MCP`). KIS calls
  fail without that folder. The issued access token is cached in plain text in
  `kis_token_cache.json` (gitignored).
- The KRX VKOSPI endpoint is disabled in code (`_VKOSPI_API_DISABLED`, persistent 403 since
  2026-08-28); `vkospi_cache.json` serves the history until KRX lifts the block.

### Local state / cache files (gitignored)

`portfolio.db` (source of truth for trades), `custom_settings.json`, `universe_cache.json`,
`trading_record.json`, `vkospi_cache.json`, `kis_token_cache.json`, `app.log`, `archive/`,
plus the legacy `custom_history.json` / `trade_overrides.json` pair (read once by
`_migrate_legacy_json`). All of these paths come from `src/paths.py`. They are runtime data,
not fixtures. Trading History principal/deposit/withdrawal live in `QSettings`
(scope "PortfolioManagement"/"PortfolioManagement"; migrated once from the old
"MyCompany"/"PortfolioManager" scope).

## Conventions

- New or edited menus, labels, and comments are written in English (user direction,
  2026-08-29). Existing Korean strings stay unless asked.
- Fonts go through `create_font()`; inline stylesheets splice `FONT_FAMILY_CSS` instead of
  repeating the font-family list.
- Bulk table repaints are wrapped in `setUpdatesEnabled(False)` / `finally:
  setUpdatesEnabled(True)`.
- Replacing a possibly-running `QThread` stored on a widget goes through
  `ui.common.retire_thread(self, "<attr>")`; every tab exposes `collect_threads_to_stop()`
  for `MainWindow.closeEvent`.
- KR-vs-US ticker routing uses `is_kr_code()`; the daily-history lookback start is
  `start_date()` (a function, not an import-time constant).
- Strategy specs live next to their code as `src/strategy/<name>/<name>.md` (see the
  `src/strategy/` rule above). `rebalance.md` also holds the multi-agent development
  methodology (ch. 12) and the manual-trading baseline (ch. 10); it was the root
  `trading.md` until 2026-09-17. The other specs are `strategy/ma_cross/ma_cross.md` and
  `strategy/trend_following/trend_following.md`; there are no strategy docs at the repo root.
- `roadmap.md` is the running log of what was done and why (sections per phase, a priority
  matrix, and a dated change history). Add a row there for non-trivial changes.
