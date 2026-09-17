---
trigger: always_on
glob:
description: Coding and convention rules for Portfolio Management (kept in sync with CLAUDE.md)
---

# Portfolio Management Coding Rules

Authoritative reference: `CLAUDE.md` (identical content in `AGENTS.md`). This file is the
short version for agents that read `.agents/rules/`.

1. **English-only for code & UI**: new or modified comments, docstrings, UI labels, menus,
   tooltips, dialogs and log messages are written in English. Existing Korean strings stay
   unless explicitly asked.

2. **Layout**: all code is under `src/`. `src/data/` is the pure data-access layer and never
   imports `strategy`; every trading strategy is its own sub-package `src/strategy/<name>/`
   with its spec saved as `<name>.md` in the same folder and tests in
   `src/tests/strategy/<name>/`. UI/thread code imports data functions from `data_fetcher`
   (the single re-export facade) and strategy symbols from `strategy.<name>` directly.
   Runtime file paths come from `src/paths.py`.

3. **Threads**: network calls run in a `QThread` subclass under `src/threads/`, never in a
   slot on the UI thread. Connect `finished` signals to bound methods, not closures. Replace a
   running thread with `ui.common.retire_thread(self, "<attr>")`.

4. **UI conventions**: fonts via `create_font()`; inline stylesheets splice `FONT_FAMILY_CSS`;
   bulk table population wrapped in `setUpdatesEnabled(False)` / `finally:
   setUpdatesEnabled(True)`.

5. **Verification** (run before finishing):
   - `.\.venv\Scripts\python.exe -m pytest src\tests -q`
   - `.\.venv\Scripts\ruff.exe check src`
   - import smoke test where relevant. GUI cannot be exercised headlessly.

6. **Version control**: the repo is git-managed; no `archive/backup_*` copies before edits.
   Record non-trivial changes in `roadmap.md` (change history) and update the strategy `.md`
   in the same commit as the code it describes.
