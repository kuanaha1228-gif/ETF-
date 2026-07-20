from __future__ import annotations

import os
import platform
from pathlib import Path


APP_DIR_NAME = "ETFPlanAssistant"


def app_data_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        root = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / APP_DIR_NAME


def default_database_path() -> Path:
    return app_data_dir() / "data" / "etf_assistant.sqlite"


def default_log_dir() -> Path:
    return app_data_dir() / "logs"


def default_backup_dir() -> Path:
    return app_data_dir() / "backups"

