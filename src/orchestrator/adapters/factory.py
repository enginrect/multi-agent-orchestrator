"""Adapter factory — creates agent adapters from configuration.

Maps config type names to concrete adapter classes:

    manual    → ManualAdapter (generates instructions, returns WAITING)
    stub      → StubAdapter (auto-completes, returns COMPLETED)
    command   → CommandAdapter (generic external command)
    codex-cli → CodexCommandAdapter (Codex CLI with smart prompts)
    claude-cli → ClaudeCommandAdapter (Claude CLI with smart prompts)
    cursor-cli → CursorCommandAdapter (manual fallback or custom command)

Config schema per agent::

    adapters:
      cursor:
        type: cursor-cli        # adapter type
        settings:               # passed to adapter constructor
          command: my-script
          timeout: 600
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.models import AgentRole
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.template_renderer import TemplateRenderer
from .base import AgentAdapter
from .claude_adapter import ClaudeCommandAdapter
from .codex import CodexCommandAdapter
from .command import CommandAdapter
from .cursor import CursorCommandAdapter
from .manual import ManualAdapter
from .stub import StubAdapter


ADAPTER_TYPES = {
    "manual",
    "stub",
    "command",
    "codex-cli",
    "claude-cli",
    "cursor-cli",
}


def create_adapter(
    role: AgentRole,
    adapter_config: dict[str, Any],
    store: FileStateStore,
    renderer: TemplateRenderer,
) -> AgentAdapter:
    """Create a single adapter from a config dict.

    Args:
        role: Agent role this adapter serves.
        adapter_config: Dict with ``type`` and optional ``settings``.
        store: File state store (needed by all adapters).
        renderer: Template renderer (needed by ManualAdapter).

    Returns:
        Configured AgentAdapter instance.

    Raises:
        ValueError: If the adapter type is unknown.
    """
    adapter_type = adapter_config.get("type", "manual")
    settings = adapter_config.get("settings", {})

    if adapter_type == "manual":
        return ManualAdapter(store, renderer)

    if adapter_type == "stub":
        return StubAdapter(store)

    if adapter_type == "command":
        return CommandAdapter(store, settings)

    if adapter_type == "codex-cli":
        return CodexCommandAdapter(store, settings)

    if adapter_type == "claude-cli":
        return ClaudeCommandAdapter(store, settings)

    if adapter_type == "cursor-cli":
        return CursorCommandAdapter(store, settings)

    raise ValueError(
        f"Unknown adapter type '{adapter_type}' for {role.value}. "
        f"Valid types: {', '.join(sorted(ADAPTER_TYPES))}"
    )


def create_adapters_from_config(
    adapters_config: dict[str, Any],
    store: FileStateStore,
    renderer: TemplateRenderer,
) -> dict[AgentRole, AgentAdapter]:
    """Create all adapters from the ``adapters`` section of the config.

    Args:
        adapters_config: Dict mapping role names to adapter config dicts.
            Example: ``{"cursor": {"type": "manual"}, "claude": {"type": "claude-cli"}}``
        store: File state store.
        renderer: Template renderer.

    Returns:
        Dict mapping AgentRole to configured AgentAdapter.
    """
    adapters: dict[AgentRole, AgentAdapter] = {}

    role_map = {r.value: r for r in AgentRole}

    for role_name, config in adapters_config.items():
        role = role_map.get(role_name)
        if role is None:
            continue
        adapters[role] = create_adapter(role, config, store, renderer)

    return adapters
