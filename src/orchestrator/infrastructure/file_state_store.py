"""File-based state persistence.

Each task is stored as a ``state.yaml`` file inside its task directory.
This is the single source of truth for task state. All reads and writes
go through this module.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import yaml

from ..domain.errors import TaskAlreadyExistsError, TaskNotFoundError
from ..domain.models import Task

STATE_FILENAME = "state.yaml"


class FileStateStore:
    """Manages task state as YAML files on disk.

    Directory layout::

        workspace_dir/
        ├── active/
        │   └── <task-name>/
        │       ├── state.yaml
        │       ├── 00-scope.md
        │       └── ...
        └── archive/
            └── <task-name>/
    """

    def __init__(self, workspace_dir: str | Path) -> None:
        self.workspace = Path(workspace_dir)
        self.active_dir = self.workspace / "active"
        self.archive_dir = self.workspace / "archive"

    def ensure_workspace(self) -> None:
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Task directory operations
    # ------------------------------------------------------------------

    def task_dir(self, task_name: str, archived: bool = False) -> Path:
        base = self.archive_dir if archived else self.active_dir
        return base / task_name

    def task_exists(self, task_name: str) -> bool:
        return (self.task_dir(task_name) / STATE_FILENAME).is_file()

    def task_is_archived(self, task_name: str) -> bool:
        return (self.task_dir(task_name, archived=True) / STATE_FILENAME).is_file()

    def create_task_dir(self, task_name: str) -> Path:
        if self.task_exists(task_name):
            raise TaskAlreadyExistsError(task_name)
        task_path = self.task_dir(task_name)
        task_path.mkdir(parents=True, exist_ok=True)
        return task_path

    # ------------------------------------------------------------------
    # State read/write
    # ------------------------------------------------------------------

    def save_task(self, task: Task) -> Path:
        """Write task state to state.yaml."""
        task_path = self.task_dir(task.name)
        task_path.mkdir(parents=True, exist_ok=True)
        state_file = task_path / STATE_FILENAME
        with open(state_file, "w") as f:
            yaml.dump(
                task.to_dict(),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        return state_file

    def load_task(self, task_name: str) -> Task:
        """Load task state from state.yaml."""
        state_file = self.task_dir(task_name) / STATE_FILENAME
        if not state_file.is_file():
            archived_file = self.task_dir(task_name, archived=True) / STATE_FILENAME
            if archived_file.is_file():
                state_file = archived_file
            else:
                raise TaskNotFoundError(task_name)
        with open(state_file) as f:
            data = yaml.safe_load(f)
        return Task.from_dict(data)

    # ------------------------------------------------------------------
    # Artifact file operations
    # ------------------------------------------------------------------

    def artifact_path(self, task_name: str, artifact_filename: str) -> Path:
        return self.task_dir(task_name) / artifact_filename

    def artifact_exists(self, task_name: str, artifact_filename: str) -> bool:
        return self.artifact_path(task_name, artifact_filename).is_file()

    def list_artifacts(self, task_name: str) -> list[str]:
        task_path = self.task_dir(task_name)
        if not task_path.is_dir():
            return []
        return sorted(
            f.name
            for f in task_path.iterdir()
            if f.is_file() and f.name != STATE_FILENAME
        )

    def write_artifact(
        self, task_name: str, artifact_filename: str, content: str
    ) -> Path:
        path = self.artifact_path(task_name, artifact_filename)
        path.write_text(content)
        return path

    # ------------------------------------------------------------------
    # Archive operations
    # ------------------------------------------------------------------

    def archive_task(self, task_name: str) -> Path:
        """Move task from active/ to archive/."""
        src = self.task_dir(task_name)
        if not src.is_dir():
            raise TaskNotFoundError(task_name)
        dst = self.task_dir(task_name, archived=True)
        if dst.exists():
            raise TaskAlreadyExistsError(f"{task_name} (in archive)")
        shutil.move(str(src), str(dst))
        return dst

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_active_tasks(self) -> list[str]:
        if not self.active_dir.is_dir():
            return []
        return sorted(
            d.name
            for d in self.active_dir.iterdir()
            if d.is_dir() and (d / STATE_FILENAME).is_file()
        )

    def list_archived_tasks(self) -> list[str]:
        if not self.archive_dir.is_dir():
            return []
        return sorted(
            d.name
            for d in self.archive_dir.iterdir()
            if d.is_dir() and (d / STATE_FILENAME).is_file()
        )
