"""Workflow definition: artifact sequence, phase logic, and next-step resolution.

This module encodes the Cursor → Claude → Codex review workflow as data.
The artifact sequence and phase rules are the canonical reference for
what files are expected and in what order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import AgentRole, ArtifactSpec, ReviewOutcome, Task, TaskState


# ---------------------------------------------------------------------------
# Artifact catalog
# ---------------------------------------------------------------------------

CYCLE_1_ARTIFACTS: list[ArtifactSpec] = [
    ArtifactSpec(
        name="scope",
        filename_pattern="00-scope.md",
        author=AgentRole.CURSOR,
        required=True,
        description="Task objective, acceptance criteria, file scope, risks.",
    ),
    ArtifactSpec(
        name="cursor_implementation",
        filename_pattern="01-cursor-implementation.md",
        author=AgentRole.CURSOR,
        required=True,
        description="Implementation summary, files changed, validation results.",
    ),
    ArtifactSpec(
        name="claude_review",
        filename_pattern="02-claude-review-cycle-1.md",
        author=AgentRole.CLAUDE,
        required=True,
        description="Claude's review findings and fixes applied.",
    ),
    ArtifactSpec(
        name="cursor_response",
        filename_pattern="03-cursor-response-cycle-1.md",
        author=AgentRole.CURSOR,
        required=False,
        description="Cursor's response to Claude's findings (only if changes requested).",
    ),
    ArtifactSpec(
        name="codex_review",
        filename_pattern="04-codex-review-cycle-1.md",
        author=AgentRole.CODEX,
        required=True,
        description="Codex's final review and validation results.",
    ),
    ArtifactSpec(
        name="final_approval",
        filename_pattern="05-final-approval.md",
        author=AgentRole.CODEX,
        required=False,
        description="Final sign-off (only if approved).",
    ),
]

CYCLE_2_ARTIFACTS: list[ArtifactSpec] = [
    ArtifactSpec(
        name="claude_review_c2",
        filename_pattern="06-claude-review-cycle-2.md",
        author=AgentRole.CLAUDE,
        required=True,
        description="Claude's cycle-2 re-review.",
    ),
    ArtifactSpec(
        name="cursor_response_c2",
        filename_pattern="07-cursor-response-cycle-2.md",
        author=AgentRole.CURSOR,
        required=False,
        description="Cursor's response to cycle-2 findings (only if changes requested).",
    ),
    ArtifactSpec(
        name="codex_review_c2",
        filename_pattern="08-codex-review-cycle-2.md",
        author=AgentRole.CODEX,
        required=True,
        description="Codex's cycle-2 final review.",
    ),
    ArtifactSpec(
        name="final_approval_c2",
        filename_pattern="09-final-approval.md",
        author=AgentRole.CODEX,
        required=False,
        description="Cycle-2 final sign-off (only if approved).",
    ),
]

ALL_ARTIFACTS: list[ArtifactSpec] = CYCLE_1_ARTIFACTS + CYCLE_2_ARTIFACTS


def get_artifacts_for_cycle(cycle: int) -> list[ArtifactSpec]:
    if cycle == 1:
        return CYCLE_1_ARTIFACTS
    elif cycle == 2:
        return CYCLE_1_ARTIFACTS + CYCLE_2_ARTIFACTS
    raise ValueError(f"Unsupported cycle number: {cycle}")


# ---------------------------------------------------------------------------
# Next-step resolution
# ---------------------------------------------------------------------------

@dataclass
class NextStep:
    """What should happen next for a task."""

    agent: AgentRole
    artifact: str
    template: str
    instruction: str
    state_after: TaskState


def resolve_next_step(task: Task) -> Optional[NextStep]:
    """Determine the next action for a task based on its current state and cycle.

    Returns None if the task is in a terminal state.
    """
    state = task.state
    cycle = task.cycle

    if state == TaskState.INITIALIZED:
        return NextStep(
            agent=AgentRole.CURSOR,
            artifact="00-scope.md",
            template="00-scope.md",
            instruction=(
                "Create the task scope document. Define the objective, "
                "acceptance criteria, files to create/modify/delete, "
                "out-of-scope items, and risks."
            ),
            state_after=TaskState.CURSOR_IMPLEMENTING,
        )

    if state == TaskState.CURSOR_IMPLEMENTING:
        return NextStep(
            agent=AgentRole.CURSOR,
            artifact="01-cursor-implementation.md",
            template="01-cursor-implementation.md",
            instruction=(
                "Implement the changes in the target repository. "
                "Run validation, then write the implementation handoff document "
                "listing all files changed and validation results."
            ),
            state_after=TaskState.CLAUDE_REVIEWING,
        )

    if state == TaskState.CLAUDE_REVIEWING:
        if cycle == 1:
            artifact = "02-claude-review-cycle-1.md"
        else:
            artifact = "06-claude-review-cycle-2.md"
        return NextStep(
            agent=AgentRole.CLAUDE,
            artifact=artifact,
            template="02-claude-review.md",
            instruction=(
                f"Review the implementation (cycle {cycle}). "
                "Read 00-scope.md and the implementation doc. "
                "Write structured findings. Apply minor fixes directly. "
                "Set status to approved, minor-fixes-applied, or changes-requested."
            ),
            state_after=TaskState.CODEX_REVIEWING,  # optimistic; engine adjusts
        )

    if state == TaskState.CURSOR_REWORKING:
        if cycle == 1:
            artifact = "03-cursor-response-cycle-1.md"
            state_after = TaskState.CODEX_REVIEWING
        else:
            artifact = "07-cursor-response-cycle-2.md"
            state_after = TaskState.CODEX_REVIEWING
        return NextStep(
            agent=AgentRole.CURSOR,
            artifact=artifact,
            template="03-cursor-response.md",
            instruction=(
                f"Address the review findings from cycle {cycle}. "
                "For each finding, describe the resolution. "
                "Run validation again and update the response document."
            ),
            state_after=state_after,
        )

    if state == TaskState.CODEX_REVIEWING:
        if cycle == 1:
            artifact = "04-codex-review-cycle-1.md"
        else:
            artifact = "08-codex-review-cycle-2.md"
        return NextStep(
            agent=AgentRole.CODEX,
            artifact=artifact,
            template="04-codex-review.md",
            instruction=(
                f"Final review (cycle {cycle}). Read all prior artifacts. "
                "Run validation commands. If approved, also create the "
                "final-approval document. If changes needed and this is "
                f"cycle {cycle} of {task.max_cycles}, escalate to human."
            ),
            state_after=TaskState.APPROVED,  # optimistic; engine adjusts
        )

    return None


# ---------------------------------------------------------------------------
# Artifact-to-state mapping
# ---------------------------------------------------------------------------

ARTIFACT_STATE_MAP: dict[str, TaskState] = {
    "00-scope.md": TaskState.CURSOR_IMPLEMENTING,
    "01-cursor-implementation.md": TaskState.CLAUDE_REVIEWING,
    "02-claude-review-cycle-1.md": TaskState.CODEX_REVIEWING,
    "03-cursor-response-cycle-1.md": TaskState.CODEX_REVIEWING,
    "04-codex-review-cycle-1.md": TaskState.APPROVED,
    "05-final-approval.md": TaskState.APPROVED,
    "06-claude-review-cycle-2.md": TaskState.CODEX_REVIEWING,
    "07-cursor-response-cycle-2.md": TaskState.CODEX_REVIEWING,
    "08-codex-review-cycle-2.md": TaskState.APPROVED,
    "09-final-approval.md": TaskState.APPROVED,
}
