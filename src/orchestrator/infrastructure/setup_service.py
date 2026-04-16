"""Setup service for morch — interactive agent configuration and auto-detection.

Provides:
- Auto-detection of agent CLI commands (cursor, claude, codex)
- Interactive setup flow for configuring agent command paths
- Persistent configuration in ~/.morch/config.yaml
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

MORCH_CONFIG_DIR = Path.home() / ".morch"
MORCH_CONFIG_FILE = MORCH_CONFIG_DIR / "config.yaml"

AGENT_COMMANDS = {
    "cursor": {
        "binary": "cursor",
        "check_args": ["--version"],
        "install_hint": "Install Cursor: https://www.cursor.com/",
    },
    "claude": {
        "binary": "claude",
        "check_args": ["--version"],
        "install_hint": "Install Claude Code: npm install -g @anthropic-ai/claude-code",
    },
    "codex": {
        "binary": "codex",
        "check_args": ["--version"],
        "install_hint": "Install Codex: npm install -g @openai/codex",
    },
}


@dataclass
class AgentDetectionResult:
    """Result of detecting a single agent CLI."""

    name: str
    installed: bool
    path: str = ""
    version: str = ""
    authenticated: bool = False
    auth_message: str = ""
    install_hint: str = ""


@dataclass
class SetupConfig:
    """Persistent setup configuration."""

    agent_paths: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> SetupConfig:
        """Load from ~/.morch/config.yaml, returning defaults if missing."""
        if not MORCH_CONFIG_FILE.is_file():
            return cls()
        try:
            data = yaml.safe_load(MORCH_CONFIG_FILE.read_text()) or {}
            return cls(agent_paths=data.get("agent_paths", {}))
        except Exception:
            return cls()

    def save(self) -> None:
        """Persist to ~/.morch/config.yaml."""
        MORCH_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {"agent_paths": self.agent_paths}
        MORCH_CONFIG_FILE.write_text(yaml.dump(data, default_flow_style=False))


def detect_agent(name: str) -> AgentDetectionResult:
    """Detect a single agent CLI.

    Checks:
    - Whether the binary exists on PATH
    - Its version
    - Whether it appears authenticated

    Distinguishes "not installed" from "installed but not authenticated".
    """
    info = AGENT_COMMANDS.get(name)
    if not info:
        return AgentDetectionResult(name=name, installed=False)

    binary = info["binary"]
    path = shutil.which(binary)

    if not path:
        config = SetupConfig.load()
        custom_path = config.agent_paths.get(name, "")
        if custom_path and Path(custom_path).is_file():
            path = custom_path

    if not path:
        return AgentDetectionResult(
            name=name,
            installed=False,
            install_hint=info.get("install_hint", ""),
        )

    version = ""
    try:
        proc = subprocess.run(
            [path, *info["check_args"]],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            version = proc.stdout.strip().split("\n")[0]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    authenticated = False
    auth_message = ""

    if name == "cursor":
        authenticated = True
        auth_message = "Auth managed by desktop app"
    elif name == "claude":
        try:
            proc = subprocess.run(
                [path, "auth", "status"],
                capture_output=True, text=True, timeout=10,
            )
            authenticated = proc.returncode == 0
            auth_message = "authenticated" if authenticated else "not authenticated"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            auth_message = "auth check failed"
    elif name == "codex":
        import os
        import json
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        auth_file = Path.home() / ".codex" / "auth.json"
        has_login = False
        if auth_file.is_file():
            try:
                data = json.loads(auth_file.read_text())
                if isinstance(data, dict):
                    tokens = data.get("tokens")
                    if isinstance(tokens, dict) and tokens.get("access_token"):
                        has_login = True
                    elif data.get("OPENAI_API_KEY"):
                        has_login = True
            except (json.JSONDecodeError, OSError):
                pass
        authenticated = has_key or has_login
        if has_login and has_key:
            auth_message = "authenticated (login + API key)"
        elif has_login:
            auth_message = "authenticated (login session)"
        elif has_key:
            auth_message = "authenticated (API key)"
        else:
            auth_message = "not authenticated"

    return AgentDetectionResult(
        name=name,
        installed=True,
        path=path,
        version=version,
        authenticated=authenticated,
        auth_message=auth_message,
        install_hint=info.get("install_hint", ""),
    )


def detect_all_agents() -> list[AgentDetectionResult]:
    """Detect all supported agent CLIs."""
    return [detect_agent(name) for name in AGENT_COMMANDS]


def run_setup(interactive: bool = True) -> SetupConfig:
    """Run the agent setup flow.

    If interactive, prompts the user for paths when auto-detection fails.
    If non-interactive, just auto-detects and saves.

    Returns the resulting SetupConfig.
    """
    config = SetupConfig.load()
    results = detect_all_agents()

    print("morch setup — Agent Configuration\n")
    print("Detecting agent CLIs...\n")

    for result in results:
        if result.installed:
            icon = "+" if result.authenticated else "~"
            auth_str = f" ({result.auth_message})" if result.auth_message else ""
            version_str = f" v{result.version}" if result.version else ""
            print(f"  [{icon}] {result.name}: {result.path}{version_str}{auth_str}")
            config.agent_paths[result.name] = result.path
        else:
            print(f"  [!] {result.name}: not found")
            if result.install_hint:
                print(f"      hint: {result.install_hint}")

            if interactive:
                custom = input(f"      Enter path for {result.name} (or press Enter to skip): ").strip()
                if custom and Path(custom).is_file():
                    config.agent_paths[result.name] = custom
                    print(f"      Set: {custom}")
                elif custom:
                    print(f"      Warning: '{custom}' is not a file, skipping")

    config.save()
    print(f"\nConfiguration saved to: {MORCH_CONFIG_FILE}")
    return config
