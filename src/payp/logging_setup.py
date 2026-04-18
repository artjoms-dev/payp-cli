"""File logging for payp.

Writes the root logger to ``~/.payp/logs/payp.log`` with rotation, so
failures (connection errors, unexpected exceptions) leave a trail the
user can inspect after the fact. The console stays clean - we only log
to file, and show a short summary plus the log path in the UI.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from payp.config import global_dir

LOG_FILENAME = "payp.log"
MAX_BYTES = 2_000_000
BACKUP_COUNT = 3


def logs_dir() -> Path:
    d = global_dir() / "logs"
    d.mkdir(exist_ok=True)
    return d


def log_file_path() -> Path:
    return logs_dir() / LOG_FILENAME


def _install_excepthook() -> None:
    """Route uncaught exceptions through the root logger.

    Preserves default behavior for KeyboardInterrupt (so Ctrl-C still
    exits cleanly without spamming the log), and chains to the previous
    hook so pytest / debuggers / IDEs still see what happened.
    """
    prev = sys.excepthook

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            prev(exc_type, exc_value, tb)
            return
        logging.getLogger("payp").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, tb),
        )
        prev(exc_type, exc_value, tb)

    sys.excepthook = _hook


def setup_logging(level: int = logging.INFO) -> Path:
    """Attach a rotating file handler to the root logger.

    Idempotent - safe to call multiple times. Returns the log file path
    so callers can show it in the UI.
    """
    path = log_file_path()
    root = logging.getLogger()
    already_attached = any(
        isinstance(h, RotatingFileHandler)
        and Path(getattr(h, "baseFilename", "")) == path
        for h in root.handlers
    )
    if not already_attached:
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)
    if root.level == logging.WARNING or root.level > level:
        root.setLevel(level)
    _install_excepthook()
    return path
