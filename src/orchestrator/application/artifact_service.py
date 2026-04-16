"""Artifact validation and management.

Checks whether expected artifacts exist, detects missing required files,
and reads review outcomes from artifact content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..domain.models import ReviewOutcome, Task
from ..domain.workflow import CYCLE_1_ARTIFACTS, CYCLE_2_ARTIFACTS, ArtifactSpec
from ..infrastructure.file_state_store import FileStateStore


@dataclass
class ArtifactStatus:
    """Status of a single expected artifact."""

    filename: str
    expected: bool
    exists: bool
    required: bool
    author: str


@dataclass
class ValidationResult:
    """Result of validating all artifacts for a task."""

    task_name: str
    cycle: int
    artifacts: list[ArtifactStatus]
    missing_required: list[str]
    is_valid: bool


class ArtifactService:
    """Validates and inspects artifact files for a task."""

    def __init__(self, store: FileStateStore) -> None:
        self.store = store

    def validate(self, task: Task) -> ValidationResult:
        """Check all expected artifacts for the current cycle."""
        specs = list(CYCLE_1_ARTIFACTS)
        if task.cycle >= 2:
            specs.extend(CYCLE_2_ARTIFACTS)

        artifacts: list[ArtifactStatus] = []
        missing_required: list[str] = []

        for spec in specs:
            filename = spec.filename()
            exists = self.store.artifact_exists(task.name, filename)
            status = ArtifactStatus(
                filename=filename,
                expected=True,
                exists=exists,
                required=spec.required,
                author=spec.author.value,
            )
            artifacts.append(status)
            if spec.required and not exists:
                missing_required.append(filename)

        return ValidationResult(
            task_name=task.name,
            cycle=task.cycle,
            artifacts=artifacts,
            missing_required=missing_required,
            is_valid=len(missing_required) == 0,
        )

    def read_review_outcome(
        self, task_name: str, artifact_filename: str
    ) -> Optional[ReviewOutcome]:
        """Parse the Status field from a review artifact.

        Looks for a line like ``**Status**: approved`` or ``**Status**: changes-requested``.
        """
        if not self.store.artifact_exists(task_name, artifact_filename):
            return None

        path = self.store.artifact_path(task_name, artifact_filename)
        content = path.read_text()

        match = re.search(
            r"\*\*Status\*\*:\s*(approved|changes-requested|minor-fixes-applied)",
            content,
            re.IGNORECASE,
        )
        if not match:
            return None

        value = match.group(1).lower().strip()
        try:
            return ReviewOutcome(value)
        except ValueError:
            return None

    def list_existing(self, task_name: str) -> list[str]:
        """Return sorted list of artifact filenames in the task directory."""
        return self.store.list_artifacts(task_name)
