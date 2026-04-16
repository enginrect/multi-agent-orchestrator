"""Template loading and rendering for artifact files.

Templates are plain markdown files with ``{{ variable }}`` placeholders.
We use a minimal substitution approach (no Jinja2 dependency) to keep
the orchestrator lightweight.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


class TemplateRenderer:
    """Load and render artifact templates from a directory."""

    def __init__(self, template_dir: str | Path) -> None:
        self.template_dir = Path(template_dir)

    def list_templates(self) -> list[str]:
        if not self.template_dir.is_dir():
            return []
        return sorted(f.name for f in self.template_dir.iterdir() if f.is_file())

    def load_raw(self, template_name: str) -> str:
        """Load a template file as raw text."""
        path = self.template_dir / template_name
        if not path.is_file():
            raise FileNotFoundError(f"Template not found: {path}")
        return path.read_text()

    def render(self, template_name: str, variables: Optional[dict] = None) -> str:
        """Render a template with variable substitution.

        Replaces ``<!-- placeholder -->`` HTML comments and ``{{ var }}``
        mustache-style placeholders with values from ``variables``.
        Unmatched placeholders are left as-is.
        """
        content = self.load_raw(template_name)
        if not variables:
            return content

        for key, value in variables.items():
            # Replace <!-- key --> style
            content = re.sub(
                rf"<!--\s*{re.escape(key)}\s*-->",
                str(value),
                content,
            )
            # Replace {{ key }} style
            content = re.sub(
                rf"\{{\{{\s*{re.escape(key)}\s*\}}\}}",
                str(value),
                content,
            )
        return content
