"""Machine-readable JSONL run logger.

Writes one JSON object per line to ``<task-dir>/run.log``. Each entry
includes a timestamp, event type, and event-specific data. Useful for
debugging adapter invocations, timing analysis, and audit trails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..domain.models import _now_iso

RUN_LOG_FILENAME = "run.log"


class RunLogger:
    """Append-only JSONL logger scoped to a task directory."""

    def __init__(self, task_dir: Path) -> None:
        self._path = task_dir / RUN_LOG_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def log(self, event: str, **data: Any) -> None:
        entry = {"timestamp": _now_iso(), "event": event, **data}
        with open(self._path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def read_entries(self) -> list[dict[str, Any]]:
        """Read all log entries (for testing and debugging)."""
        if not self._path.is_file():
            return []
        entries = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries
