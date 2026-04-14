"""Configuration loading from YAML files.

Provides a simple config object with defaults that can be overridden
by a YAML file or environment variables. Supports adapter configuration
for per-agent adapter selection and agent ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

SUPPORTED_AGENTS = ("cursor", "claude", "codex")
DEFAULT_AGENT_ORDER = ["cursor", "claude", "codex"]
MIN_AGENTS = 2
MAX_AGENTS = 3


@dataclass
class AgentsConfig:
    """Agent ordering and enablement configuration.

    Attributes:
        enabled: Ordered list of enabled agents. The order determines
            which agent acts at each stage. Minimum 2, maximum 3.
    """

    enabled: list[str] = field(default_factory=lambda: list(DEFAULT_AGENT_ORDER))

    def validate(self) -> list[str]:
        """Return list of validation error strings, empty if valid."""
        errors: list[str] = []
        if len(self.enabled) < MIN_AGENTS:
            errors.append(
                f"At least {MIN_AGENTS} agents must be enabled "
                f"(got {len(self.enabled)})"
            )
        if len(self.enabled) > MAX_AGENTS:
            errors.append(
                f"At most {MAX_AGENTS} agents may be enabled "
                f"(got {len(self.enabled)})"
            )
        for agent in self.enabled:
            if agent not in SUPPORTED_AGENTS:
                errors.append(
                    f"Unsupported agent '{agent}'. "
                    f"Supported: {', '.join(SUPPORTED_AGENTS)}"
                )
        if len(self.enabled) != len(set(self.enabled)):
            errors.append("Duplicate agents in enabled list")
        return errors

    @property
    def implementer(self) -> str:
        """First agent in order — creates the implementation."""
        return self.enabled[0]

    @property
    def reviewers(self) -> list[str]:
        """Remaining agents — review in order."""
        return self.enabled[1:]


@dataclass
class GitHubConfig:
    """GitHub-specific configuration."""

    repo: str = ""
    base_branch: str = "main"
    branch_pattern: str = "{type}/issue-{issue}/{agent}/cycle-{cycle}"
    pr_title_pattern: str = "[{type}][Issue #{issue}][{agent}] {summary}"
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class OrchestratorConfig:
    """Runtime configuration for the orchestrator.

    Attributes:
        workspace_dir: Path to workspace (contains active/ and archive/).
        template_dir: Path to artifact templates.
        max_cycles: Maximum review cycles before escalation.
        default_target_repo: Default target repository path.
        adapters: Per-agent adapter configuration. Maps role name
            (cursor/claude/codex) to adapter config dict with ``type``
            and optional ``settings``.
        agents: Agent ordering and enablement.
        github: GitHub-specific configuration for the GitHub-native workflow.
    """

    workspace_dir: str = "./workspace"
    template_dir: str = "./templates/artifacts"
    max_cycles: int = 2
    default_target_repo: str = ""
    adapters: dict[str, Any] = field(default_factory=dict)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)

    @classmethod
    def load(cls, config_path: Optional[str | Path] = None) -> OrchestratorConfig:
        """Load configuration from a YAML file, falling back to defaults."""
        config = cls()
        if config_path is None:
            return config

        path = Path(config_path)
        if not path.is_file():
            return config

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        if "workspace_dir" in data:
            config.workspace_dir = data["workspace_dir"]
        if "template_dir" in data:
            config.template_dir = data["template_dir"]
        if "max_cycles" in data:
            config.max_cycles = int(data["max_cycles"])
        if "default_target_repo" in data:
            config.default_target_repo = data["default_target_repo"]
        if "adapters" in data and isinstance(data["adapters"], dict):
            config.adapters = data["adapters"]
        if "agents" in data and isinstance(data["agents"], dict):
            ag = data["agents"]
            enabled = ag.get("enabled", list(DEFAULT_AGENT_ORDER))
            config.agents = AgentsConfig(enabled=enabled)
        if "github" in data and isinstance(data["github"], dict):
            gh = data["github"]
            config.github = GitHubConfig(
                repo=gh.get("repo", ""),
                base_branch=gh.get("base_branch", "main"),
                branch_pattern=gh.get(
                    "branch_pattern",
                    "{type}/issue-{issue}/{agent}/cycle-{cycle}",
                ),
                pr_title_pattern=gh.get(
                    "pr_title_pattern",
                    "[{type}][Issue #{issue}][{agent}] {summary}",
                ),
                labels=gh.get("labels", {}),
            )

        return config
