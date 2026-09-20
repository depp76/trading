# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A single-user PyQt6 desktop app for tracking a Korean/US equity portfolio, with four
top-level tabs: "Trading Universe" (KOSPI/KOSDAQ watchlist with live prices and indicators;
the US market code paths still exist but are commented out in the UI), "Trading History"
(manually-entered trade log backed by SQLite), "Total Assets" (weekly asset snapshots vs.
KOSPI and USD), and "Strategy" (roadmap 7-1) — a `QTabWidget` of sub-tabs behind a shared
"Today's Signals" summary bar: "Weekly Rebalance" (weekly factor-scoring rebalance signals plus
a walk-forward backtest), "Trend Following" (Donchian channel breakout backtest for one
ticker or a multi-ticker portfolio), and "MA Cross" (fast/slow MA golden-cross backtest for one
ticker). The strategy sub-tabs are signal generation and research only; nothing places orders.

The repo is a git repository (branch `master`). Commit or branch as usual; the old
`archive/backup_<timestamp>/` copy-before-editing convention is no longer needed.
`AutoBackupThread` still writes `archive/auto_<timestamp>/` snapshots of `portfolio.db` (via
the SQLite online backup API) and `custom_settings.json` on every start (last 7 kept);
`archive/` is gitignored.

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
.\.venv\Scripts\python.exe -m pytest src\tests -q      # ~225 tests, no network, ~10 s
.\.venv\Scripts\ruff.exe check src                      # pyflakes rules only (ruff.toml)
```

Run pytest from the repo root or from `src/` (`tests/conftest.py` puts `src/` on `sys.path`;
the `tests/` folders are packages so test files in different strategy folders may share
a basename).
Tests patch the implementation modules (`data.cache`, `data.history`, `data.fx`, `data.collectors.yahoo`),
never names on the `data_fetcher` facade. Widgets can be built and driven headlessly with
`QT_QPA_PLATFORM=offscreen` (the tests construct tabs and dialogs that way), but nothing can be
looked at, so for UI refactors write a throwaway characterisation script that dumps widget /
matplotlib-axes state before and after and diff the two (done for `StockMaDialog` and
`TradingHistoryTab._build_ui` on 2026-09-19); for non-trivial changes to fetch/backtest logic
write a throwaway script comparing old vs. new behaviour on random inputs
(`docs/history/changelog_optimization_2026-08-11.md` shows the pattern;
`docs/history/test_plan_2026-08-29.md` is an old manual-check list kept for reference).
Dev tooling is in `requirements-dev.txt`
(`-r requirements.txt` + pytest + ruff); runtime pins are in `requirements.txt`.

## Architecture

- **`src/main.py`** (~300 lines): `MainWindow` builds the five tabs, wires cross-tab
  signals, owns the 60-second `global_auto_timer` (the only auto-refresh timer; the "Auto
  Update" checkbox starts and stops it and everything downstream), the app stylesheet, and
  logging setup (root INFO; `app.log` gets INFO and above, the console WARNING and above).
- **`src/ui/`**: `universe_tab.py` (`UniverseTab`), `history_tab.py` (`TradingHistoryTab`),
  `assets_tab.py` (`TradingRecordTab`), `strategy_tab.py` (`StrategyTab`, roadmap 7-1 — the
  "Strategy" top-level tab: a `QTabWidget` hosting `auto_trading_tab.py` (`AutoTradingTab`),
  `trend_following_tab.py` (`TrendFollowingTab`) and `ma_cross_tab.py` (`MaCrossTab`), behind a
  "Today's Signals" summary bar driven by `threads.fetch_threads.StrategySummaryThread`),
  `widgets.py` (`StockTable`, `NumericItem`, `FilterPopup`, `GroupedHeaderView`), `delegates.py`
  (every custom-painted table cell: `CellDelegate` base + the Universe/History/Strategy
  delegates), `ma_chart.py` (`StockMaLauncherMixin`, the shared MA-chart dialog launcher),
  `colors.py` / `theme.py` (PROFIT/LOSS color rule, design tokens and the global QSS —
  buttons get their look from the `#primary`/`#danger` objectName roles, secondary labels
  from `#muted`/`#faint`; never a hardcoded hex for something `theme.py` has a rule for),
  `dialogs/` (one module per
  dialog group: `index_ma`, `stock_ma`, `trade_edit`, `trade_history`, `assets_graph`,
  `backtest_result`, `trend_following_chart`, `trend_following_portfolio`, `holdings_summary`,
  `stock_report`; import from
  `ui.dialogs`),
  `history_table.py` (cell factories, `fill_table_rows`, `SectionTable` + the column
  `SECTIONS` for the history grid), `history_calc.py` (pure P/L maths, no Qt:
  `compute_pl_fields`, `build_monthly_rows`, `summarize_positions`), `common.py`
  (`create_font` + the `FONT_*` point-size scale, `FONT_FAMILIES` / `FONT_FAMILY_CSS`,
  `apply_matplotlib_font`, status color constants,
  input validators, `atomic_save_json` /
  `safe_load_json`, `retire_thread`, `ThreadOwnerMixin`). Tabs never
  reference each other
  directly; `MainWindow` connects their signals (`status_message`, `refresh_started`,
  `auto_lightweight_tick`, `total_asset_updated`). The one exception is `AutoTradingTab`
  and `TrendFollowingTab` (now reached through `StrategyTab` rather than directly from
  `MainWindow`), which read `UniverseTab.all_data` on demand (`TrendFollowingTab` only to
  list watchlist tickers).
- **`src/threads/`**: every network call the UI triggers runs in a `QThread` subclass here
  (`AllDataFetchThread`, `UniverseLightweightFetchThread`, `PositionPriceFetchThread`,
  `RealtimePriceThread`, `StockMaThread`, `AccountDepositThread`, `RebalanceBacktestThread`,
  `TrendFollowingBacktestThread`, `MaCrossBacktestThread`, `TrendFollowingPortfolioThread`,
  `StrategySummaryThread`,
  the Gemini threads, `AutoBackupThread`). Never call `data_fetcher` functions from a slot on
  the UI thread; add a thread class instead. Connect `finished` signals to bound methods,
  not closures, so Qt queues them onto the UI thread; when a slot needs per-request
  context (which ticker, which market, which change mode), have the thread echo it back
  in the signal (`SingleStockFetchThread.finished(result, error, ticker)`,
  `StockMaThread.finished(..., market, change_mode)`) instead of capturing it in a lambda.
- **`src/data/`**: all external data access, layered bottom-up with no import cycles
  (module-level or lazy): `cache.py` (HTTP sessions, `_HIST_CACHE` LRU with
  `_HIST_CACHE_STATS`, `_YF_BULK_CACHE`, `start_date()`, `is_kr_code()`, `is_us_market()`,
  `safe_float`) ->
  `frames.py` (`_to_polars`) -> `collectors/naver.py`, `kis.py`, `krx.py` -> `listing.py`
  (`get_stock_listing`, day-scoped single-flight cache), `history.py` (`get_historical_data`
  routing KR codes to Naver, bonds to cached series, else yfinance/yahooquery/FDR), `fx.py`
  (`get_usd_krw_rate`) -> `indicators.py` (`_compute_indicators`, `fetch_historical_changes`)
  -> `collectors/yahoo.py` (`yf_quote_batch` is the one Yahoo quote entry point, US bulk
  universe) -> `market.py` (per-market universe builds, single-stock lookup, index/MA
  series; re-exports the lower names for older callers). Keep new code in the lowest
  layer that has what it needs, never add a `from data.market import` below market.py, and
  never import `strategy`. Uses polars internally and converts to pandas only at library
  boundaries; reuse the module-level caches rather than adding parallel ones.
- **`src/strategy/`**: all trading-strategy logic (`strategy/rebalance/rebalance.md` 11-5).
  **Rule (user direction, 2026-09-17): every strategy is its own sub-package
  `src/strategy/<name>/` and its design/spec document is saved as `<name>.md` inside that
  same folder.** A sub-package has a `config.py` (parameters), `signals.py`, `backtest.py`
  and an `__init__.py` facade; tests go in `tests/strategy/<name>/`; docstrings and commit
  messages cite the md section numbers, and the md is updated in the same commit as the
  code. Current members: `rebalance/` (weekly factor scoring, classification, walk-forward
  backtest; `rebalance.md`), `ma_cross/` (single-stock fast/slow MA golden-cross backtest:
  `config.py` with `MaCrossConfig`, `signals.py`, `backtest.py`; UI in `ui/ma_cross_tab.py`;
  `ma_cross.md`), and `trend_following/` (Donchian channel breakout with optional v2
  overlays — regime MA filter, ATR stop, volatility-target sizing, all off by default:
  `config.py`, `signals.py` with the no-lookahead `donchian_signal`, `backtest.py` with
  `run_backtest` / `run_backtest_for_ticker`, `portfolio.py` with the equal-sleeve
  `run_portfolio_backtest`, `validation.py` with `holdout_validation` /
  `walk_forward_validation`; spec and real-data results in `trend_following.md`; UI for
  single-instrument, portfolio and validation runs in `ui/trend_following_tab.py`). Strategy code
  imports from `data.*`; callers import strategy symbols from `strategy.<name>` directly,
  never via `data_fetcher`.
- **`src/data_fetcher.py`**: the single re-export facade over `data/` (data access only, no
  strategy symbols) so UI and thread code import from one place; `data/__init__.py` itself
  re-exports nothing. Nothing in `data/` imports the facade back; keep it that way. It
  lists only the names callers outside `data/` use (about 30; trimmed from 79 on
  2026-09-19), so add a name there when a new UI/thread caller needs it rather than
  importing `data.*` directly.
- **`src/trade_db.py`**: SQLite persistence (`portfolio.db`, WAL mode): the `trades` table and,
  since 2026-09-19, `asset_records` (the Total Assets tab's weekly snapshots —
  `load_asset_records()` / `save_asset_records()`, a full replace per save). Prefer
  `upsert_trades()` for trade batches. Do not generate `orig_key` values in callers:
  `upsert_trade()` without a key claims a collision-free one inside the INSERT and writes it
  back into the record. `init_db()` runs before any tab is built (`MainWindow`) because
  `TradingRecordTab` reads the DB in its constructor. `backup_to()` is the online-backup
  entry point `AutoBackupThread` uses.
- **`tools/register_secret.py`**: standalone CLI for pushing secrets to Google Cloud Secret
  Manager; unrelated to the app runtime. **`docs/history/`**: dated one-off documents.
- **`src/gemini_helper.py`**: Gemini calls for the per-stock AI report feature
  (`GOOGLE_API_KEY` in `.env`); the AI filter (2026-09-19) and portfolio diagnosis
  (2026-09-19) features were removed. Prompts were rewritten from
  Korean to English in the 2026-09-18 source-code-wide English-only pass; the stock-report
  prompt explicitly asks for a Korean-language reply (the app's end user is Korean-speaking),
  so keep that line if the prompt is edited.

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

`portfolio.db` (source of truth for trades and asset snapshots), `custom_settings.json`,
`universe_cache.json`, `vkospi_cache.json`, `kis_token_cache.json`, `app.log`, `archive/`,
plus the legacy `custom_history.json` / `trade_overrides.json` pair (read once by
`_migrate_legacy_json`) and the legacy `trading_record.json` (read once by
`_migrate_asset_records_json` while `asset_records` is empty; left on disk afterwards). All of
these paths come from `src/paths.py`. They are runtime data,
not fixtures. Trading History principal/deposit/withdrawal live in `QSettings`
(scope "PortfolioManagement"/"PortfolioManagement"; migrated once from the old
"MyCompany"/"PortfolioManager" scope).

## Conventions

- All comments, docstrings, log/exception messages, and test code are written in English
  (user direction, 2026-08-29 for new/edited code; extended 2026-09-18 to a one-time sweep
  converting the remaining Korean comments/strings across `src/` to English). The exceptions
  are literal values that must match real external data verbatim, which would silently break
  if translated: Naver/KRX API field names and Korean-formatted numbers
  (`data/collectors/naver.py`, `data/collectors/krx.py`), and the `'맑은 고딕 Semilight'`
  font-family fallback name (`ui/common.py`). `gemini_helper.py`'s prompts were included in
  the sweep — see the note on that file above for the response-language consequence.
- UI 변경은 `docs/ui.md` 의 전역 규칙을 따른다.
- One font family app-wide, Malgun Gothic Semilight (user direction, 2026-09-19): every
  widget goes through `create_font()` (sizes from the `FONT_*` scale in `ui/common.py`),
  numeric cells included — there is no separate monospace/tabular face — and matplotlib
  charts get the same family from `apply_matplotlib_font()` at startup. Inline stylesheets
  splice `FONT_FAMILY_CSS` instead of repeating the family list, and size in `pt`, not `px`.
- Bulk table repaints are wrapped in `setUpdatesEnabled(False)` / `finally:
  setUpdatesEnabled(True)`.
- Every tab mixes in `ui.common.ThreadOwnerMixin` and registers each worker it starts with
  `self._track_thread(thread, "<attr>")` (the optional attr retires the previous thread
  stored there via `retire_thread` and stores the new one); the mixin's
  `collect_threads_to_stop()` is what `MainWindow.closeEvent` calls, so a thread that is
  not tracked will not be stopped at shutdown.
- KR-vs-US position labels go through `is_us_market()` (one set, shared by the price
  thread and the Trading History summary).
- KR-vs-US ticker routing uses `is_kr_code()`; the daily-history lookback start is
  `start_date()` (a function, not an import-time constant).
- Strategy specs live next to their code as `src/strategy/<name>/<name>.md` (see the
  `src/strategy/` rule above). `rebalance.md` also holds the multi-agent development
  methodology (ch. 12) and the manual-trading baseline (ch. 10); it was the root
  `trading.md` until 2026-09-17. The other specs are `strategy/ma_cross/ma_cross.md` and
  `strategy/trend_following/trend_following.md`; there are no strategy docs at the repo root.
- `roadmap.md` is the running log of what was done and why (sections per phase, a priority
  matrix, and a dated change history). Add a row there for non-trivial changes.
