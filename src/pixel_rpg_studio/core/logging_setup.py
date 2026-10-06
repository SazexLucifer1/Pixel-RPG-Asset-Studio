"""Application logging: rotating file log in the app data dir plus console."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from pixel_rpg_studio.core import paths

LOG_FILE_NAME = "studio.log"
_configured = False


def log_file_path() -> Path:
    return paths.logs_dir() / LOG_FILE_NAME


def setup_logging(level: str = "INFO", console: bool = True) -> Path:
    """Configure the root logger. Safe to call multiple times."""
    global _configured
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    log_path = log_file_path()
    if not _configured:
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        if console:
            stream = logging.StreamHandler()
            stream.setFormatter(fmt)
            root.addHandler(stream)
        _configured = True
    return log_path


def read_log_tail(max_lines: int = 400) -> str:
    path = log_file_path()
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])
