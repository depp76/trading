"""paths.py — Single source of truth for runtime file locations.

Every state/cache/log file the app reads or writes lives at the repository
root (one level above src/). Resolving them from this module's own location
rather than the current working directory means the app behaves identically
no matter which folder it is launched from (roadmap 6-1d).
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def root_path(*parts: str) -> str:
    """Absolute path of a file/folder under the repository root."""
    return os.path.join(BASE_DIR, *parts)


ENV_FILE = root_path(".env")
APP_LOG_FILE = root_path("app.log")

# Trade log (SQLite, source of truth) + the legacy JSON pair it was migrated from
DB_FILE = root_path("portfolio.db")
LEGACY_CUSTOM_HISTORY_JSON = root_path("custom_history.json")
LEGACY_TRADE_OVERRIDES_JSON = root_path("trade_overrides.json")

# Per-tab state / caches
CUSTOM_SETTINGS_FILE = root_path("custom_settings.json")
UNIVERSE_CACHE_FILE = root_path("universe_cache.json")
TRADING_RECORD_FILE = root_path("trading_record.json")
VKOSPI_CACHE_FILE = root_path("vkospi_cache.json")
KIS_TOKEN_CACHE_FILE = root_path("kis_token_cache.json")

# Automatic backups (AutoBackupThread) go to archive/auto_<timestamp>/
ARCHIVE_DIR = root_path("archive")
