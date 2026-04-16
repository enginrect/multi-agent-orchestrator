"""Structured application logger for morch.

Provides a configured Python logger with both stdout and file handlers.
Log messages include agent identity, phase information, and structured
context for debugging multi-agent workflows.

Usage:
    from orchestrator.infrastructure.logger import get_logger
    
    log = get_logger(__name__)
    log.info("Starting workflow", extra={"agent": "cursor", "phase": "implement"})
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional  # noqa: F401 — kept for callers


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_FORMAT_DETAILED = (
    "%(asctime)s [%(levelname)s] %(name)s "
    "[%(agent)s/%(phase)s] %(message)s"
)

MORCH_LOG_DIR = Path.home() / ".morch" / "logs"
MORCH_LOG_FILE = MORCH_LOG_DIR / "morch.log"

_initialized = False


class MorchLogFilter(logging.Filter):
    """Inject default agent/phase fields when missing."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "agent"):
            record.agent = "-"
        if not hasattr(record, "phase"):
            record.phase = "-"
        return True


def _get_log_level() -> int:
    """Read log level from MORCH_LOG_LEVEL env var, default INFO."""
    level_str = os.environ.get("MORCH_LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_str, logging.INFO)


def _ensure_log_dir() -> None:
    """Create the log directory if it doesn't exist."""
    MORCH_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _init_logging() -> None:
    """Initialize the morch logging system (called once)."""
    global _initialized
    if _initialized:
        return

    level = _get_log_level()
    root_logger = logging.getLogger("orchestrator")
    root_logger.setLevel(level)

    log_filter = MorchLogFilter()

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(max(level, logging.WARNING))
    console_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    console_handler.addFilter(log_filter)
    root_logger.addHandler(console_handler)

    try:
        _ensure_log_dir()
        file_handler = logging.FileHandler(str(MORCH_LOG_FILE), encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT_DETAILED))
        file_handler.addFilter(log_filter)
        root_logger.addHandler(file_handler)
    except OSError:
        pass

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger for the given module name.
    
    Ensures the logging system is initialized on first call.
    Returns a child logger under the 'orchestrator' namespace.
    """
    _init_logging()
    if not name.startswith("orchestrator"):
        name = f"orchestrator.{name}"
    return logging.getLogger(name)
