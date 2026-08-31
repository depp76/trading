---
trigger: always_on
glob:
description: Coding and convention rules for Portfolio Management
---

# Portfolio Management Coding Rules

1. **English-Only for Code & UI**:
   - All newly added or modified source code comments, docstrings, UI labels, menus, buttons, tooltips, dialogs, and logging messages MUST be written in **English**.
   - Do NOT introduce new Korean strings or comments in code files (even for financial terms).
   - Existing legacy Korean text already in the codebase (e.g. 예수금/평가손익) is left as-is unless explicitly requested to change.

2. **Backup Convention**:
   - Before making substantial edits to core code files, create a backup copy in `archive/backup_<yyyyMMdd_HHmmss>/`.

3. **Font & Styling Consistency**:
   - Font handling must use `create_font()` to ensure consistent "Malgun Gothic Semilight" across UI widgets.
   - For bulk table population (`StockTable`, `TradingHistoryTab`, `TradingRecordTab`), wrap updates with `setUpdatesEnabled(False)` and `finally: setUpdatesEnabled(True)`.

4. **Testing & Verification**:
   - Always verify changes with `pytest tests/ -v` and smoke test imports before completion.
