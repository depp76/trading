# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A single-user PyQt6 desktop app for tracking a Korean/US equity portfolio, with four
top-level tabs: "Trading Universe" (KOSPI/KOSDAQ watchlist with live prices and indicators;
the US market code paths still exist but are commented out in the UI), "Trading History"
(manually-entered trade log backed by SQLite), "Total Assets" (weekly asset snapshots vs.
KOSPI and USD) and "Strategy" (research backtests; one sub-tab per strategy package, currently
only Trend Following). Nothing places orders.

**Strategy layer: reset on 2026-09-28 and rebuilt the same day from
`src/strategy/trend_following/trend_following.md` (spec v03).** The old packages (rebalance,
ma_cross, the first trend_following, shared base/metrics/costs, dialogs, five `QThread`s,
`tools/kr_trend_backtest.py`) were deleted (git `070f639` and earlier) and nothing of them was
reused. The new `strategy.trend_following` package implements spec sections 2-4 and 6 (cost
model 4-1, signals L1-L4 + exits, t+1-open portfolio engine with annual reset, BM1-BM4 on the
same engine, 6-4 metrics, 6-1 event study, the A0-A5/B matrix runner and its markdown report);
its section 9 lists the module <-> spec mapping and every implementation decision that
deviates from the text (current-constituent universe, year-end trim at the close, no weekly
re-weighting, no sector cap, KODEX 200TR as BM1 data, CD91 from ECOS/CSV/constant). Real-data
results have not been recorded in the spec yet: run the tab, save the report, then paste the
table under section 9 with the version number.

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
.\.venv\Scripts\python.exe -m pytest src\tests -q      # ~320 tests, no network, ~10 s
.\.venv\Scripts\ruff.exe check src                      # pyflakes rules only (ruff.toml)
```

Run pytest from the repo root or from `src/` (`tests/conftest.py` puts `src/` on `sys.path`;
the `tests/` folder is a package).
Tests patch the implementation modules (`data.cache`, `data.history`, `data.fx`, `data.collectors.yahoo`),
never names on the `data_fetcher` facade. Widgets can be built and driven headlessly with
`QT_QPA_PLATFORM=offscreen` (the tests construct tabs and dialogs that way), but nothing can be
looked at, so for UI refactors write a throwaway characterisation script that dumps widget /
matplotlib-axes state before and after and diff the two (done for `StockMaDialog` and
`TradingHistoryTab._build_ui` on 2026-09-19); for non-trivial changes to fetch logic
write a throwaway script comparing old vs. new behaviour on random inputs
(`docs/history/changelog_optimization_2026-08-11.md` shows the pattern;
`docs/history/test_plan_2026-08-29.md` is an old manual-check list kept for reference).
Dev tooling is in `requirements-dev.txt`
(`-r requirements.txt` + pytest + ruff); runtime pins are in `requirements.txt`.

## Architecture

- **`src/strategy/trend_following/`** (spec `trend_following.md` v03 in the same folder,
  facade `strategy.trend_following`): `config.py` (`StrategyParams`, `VARIANTS` A0-A5/B,
  `COST_MULTIPLIERS`, `PERIODS`, benchmark ETF codes), `costs.py` (`CostModel`/`TradeCost`,
  year-keyed tax table, 2023-01-25 tick-ladder reform, `scaled()`/`for_etf()`), `signals.py`
  (date x ticker arrays for L1-L4 and the exits; `regime_state` is the weekly-held L1 state),
  `dataset.py` (`PriceBook`/`Dataset`; `build_dataset` is pure and what tests feed,
  `load_dataset` fetches through `data.*`), `backtest.py` (`Engine` + `Policy`, t+1-open fills
  with sells first, cash interest, annual reset at the year's last close, per-year cost
  ledger; `TrendFollowingPolicy`), `benchmarks.py` (BM1-BM4 as policies on the same engine),
  `metrics.py` (6-4 scorecard incl. the BM1/BM3-relative block), `event_study.py` (6-1),
  `research.py` (`ResearchRequest` -> `run_research` -> `ResearchResult.to_markdown()`).
  Strategy code imports `data.*` directly (never `data_fetcher`); `data/` never imports it.
  Tests live in `src/tests/strategy/trend_following/` on synthetic price paths
  (`helpers.synthetic_dataset`).
- **`src/main.py`** (~250 lines): `MainWindow` builds the four tabs, wires cross-tab
  signals, owns the 60-second `global_auto_timer` (the only auto-refresh timer; the "Auto
  Update" checkbox starts and stops it and everything downstream), the app stylesheet, and
  logging setup (root INFO; `app.log` gets INFO and above, the console WARNING and above).
- **`src/ui/`**: `universe_tab.py` (`UniverseTab`), `history_tab.py` (`TradingHistoryTab`),
  `assets_tab.py` (`TradingRecordTab`), `strategy_tab.py` (`StrategyTab`, the sub-tab host
  that merges its children's `collect_threads_to_stop()`), `trend_following_tab.py`
  (`TrendFollowingTab`: request form -> `TrendFollowingResearchThread` -> scorecard table,
  rebased NAV chart, event-study table, "Save Report" to `reports/`),
  `widgets.py` (`StockTable`, `NumericItem`, `FilterPopup`, `GroupedHeaderView`), `delegates.py`
  (every custom-painted table cell: `CellDelegate` base + the Universe/History delegates),
  `ma_chart.py` (`StockMaLauncherMixin`, the MA-chart dialog launcher; `UniverseTab` is its
  only user now),
  `colors.py` / `theme.py` (PROFIT/LOSS color rule, design tokens and the global QSS —
  buttons get their look from the `#primary`/`#danger` objectName roles, secondary labels
  from `#muted`/`#faint`; never a hardcoded hex for something `theme.py` has a rule for; the
  `QCalendarWidget` rules have no current caller and are kept for the next `QDateEdit`),
  `dialogs/` (one module per
  dialog group: `index_ma`, `stock_ma`, `trade_edit`, `trade_history`, `assets_graph`,
  `holdings_summary`, `stock_report`; import from `ui.dialogs`),
  `history_table.py` (cell factories, `fill_table_rows`, `SectionTable` + the column
  `SECTIONS` for the history grid), `history_calc.py` (pure P/L maths, no Qt:
  `compute_pl_fields`, `build_monthly_rows`, `summarize_positions`), `common.py`
  (`create_font` + the `FONT_*` point-size scale, `FONT_FAMILIES` / `FONT_FAMILY_CSS`,
  `apply_matplotlib_font`, status color constants,
  input validators, `atomic_save_json` /
  `safe_load_json`, `retire_thread`, `ThreadOwnerMixin`). Tabs never
  reference each other
  directly; `MainWindow` connects their signals (`status_message`, `refresh_started`,
  `auto_lightweight_tick`, `total_asset_updated`).
- **`src/threads/`**: every network call the UI triggers runs in a `QThread` subclass here
  (`AllDataFetchThread`, `UniverseLightweightFetchThread`, `PositionPriceFetchThread`,
  `RealtimePriceThread`, `StockMaThread`, `IndexMaThread`, `SingleStockFetchThread`,
  `TickerValidateThread`, `AssetMetricsPreloadThread`, `AccountDepositThread`,
  `GeminiStockReportThread`, `AutoBackupThread`; `strategy_threads.py` holds
  `TrendFollowingResearchThread`, which loads the dataset and runs the research matrix).
  Never call `data_fetcher` functions from a
  slot on the UI thread; add a thread class instead. Connect `finished` signals to bound
  methods, not closures, so Qt queues them onto the UI thread; when a slot needs per-request
  context (which ticker, which market, which change mode), have the thread echo it back
  in the signal (`SingleStockFetchThread.finished(result, error, ticker)`,
  `StockMaThread.finished(..., market, change_mode)`) instead of capturing it in a lambda.
- **`src/data/`**: all external data access, layered bottom-up with no import cycles
  (module-level or lazy): `cache.py` (HTTP sessions, `_HIST_CACHE` LRU with
  `_HIST_CACHE_STATS` and the per-key `_HIST_CACHE_FETCHED_AT` stamps that
  `_hist_df_is_stale(df, fetched_at)` checks against `_HIST_CACHE_STALE_TTL` (30 min) so a
  frame without today's bar is not re-fetched on every weekday lookup, `_YF_BULK_CACHE`,
  `start_date()`, `is_kr_code()`, `is_us_market()`, `safe_float`) ->
  `frames.py` (`_to_polars`) -> `collectors/naver.py`, `kis.py`, `krx.py` -> `flows.py`
  (`get_investor_flows`: per-stock daily foreigner/institution/retail net shares from the
  Naver frgn page, cached per ticker under `cache/investor_flows/`), `rates.py`
  (`get_cd91_series`: ECOS with `ECOS_API_KEY`, else `cd91.csv`, else empty) -> `listing.py`
  (`get_stock_listing`, day-scoped single-flight cache), `history.py` (`get_historical_data`
  routing KR codes to Naver, bonds to cached series, else yfinance/yahooquery/FDR), `fx.py`
  (`get_usd_krw_rate`) -> `indicators.py` (`_compute_indicators`, `fetch_historical_changes`)
  -> `collectors/yahoo.py` (`yf_quote_batch` is the one Yahoo quote entry point, US bulk
  universe) -> `market.py` (per-market universe builds, single-stock lookup, index/MA
  series; re-exports the lower names for older callers). Keep new code in the lowest
  layer that has what it needs, never add a `from data.market import` below market.py, and
  never import a strategy package from here. Uses polars internally and converts to pandas
  only at library boundaries; reuse the module-level caches rather than adding parallel ones.
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
  optionally `GEMINI_MODEL` and `ECOS_API_KEY` (Bank of Korea ECOS, for the CD 91-day rate
  the Trend Following backtest uses as BM4; without it a `cd91.csv` at the root or a 3%
  constant is used).
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
`_migrate_asset_records_json` while `asset_records` is empty; left on disk afterwards), and
the rebuildable research caches under `cache/` (`investor_flows/<ticker>.json`, `cd91.json`).
All of these paths come from `src/paths.py`. They are runtime data,
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
- **Strategy rule (user direction, 2026-09-17; kept for the future re-introduction):** every
  strategy is its own sub-package `src/strategy/<name>/` and its design/spec document is
  saved as `<name>.md` inside that same folder. A sub-package has a `config.py`
  (parameters), `signals.py`, `backtest.py` and an `__init__.py` facade; tests go in
  `tests/strategy/<name>/`; docstrings and commit messages cite the md section numbers, and
  the md is updated in the same commit as the code. Strategy code imports from `data.*`;
  callers import strategy symbols from `strategy.<name>` directly, never via
  `data_fetcher`, and `data/` never imports `strategy`. Every strategy UI runs its work in
  a `QThread` under `src/threads/` and is signal generation / research only. There are no
  strategy docs at the repo root.
- `roadmap.md` is the running log of what was done and why (sections per phase, a priority
  matrix, and a dated change history). Add a row there for non-trivial changes.
