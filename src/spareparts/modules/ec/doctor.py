"""Environment diagnostics for engineering-context integrations."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from spareparts.modules.ec import projects


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None


def _contains_linear(path: Path) -> bool:
    try:
        return path.is_file() and "linear" in path.read_text(encoding="utf-8").lower()
    except (OSError, UnicodeDecodeError):
        return False


def linear_mcp_files(root: Path, home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    candidates = [
        root / ".mcp.json",
        root / ".cursor/mcp.json",
        root / ".vscode/mcp.json",
        home / ".claude.json",
        home / ".cursor/mcp.json",
        home / ".gemini/settings.json",
        home / ".config/opencode/opencode.json",
    ]
    return [path for path in candidates if _contains_linear(path)]


def checks(root: Path) -> list[Check]:
    results: list[Check] = []
    configured = projects.work_management_config(root)
    provider = None
    if configured is None:
        results.append(Check("Huddle store", "missing", "No provider configured.", "Run: sp ec project setup"))
    else:
        _, config = configured
        provider = str(config.get("provider", "unknown"))
        results.append(Check("Huddle store", "ready", f"Provider: {provider}"))

    gh = shutil.which("gh")
    if gh is None:
        results.append(Check("GitHub Projects", "missing", "GitHub CLI not found.", "Install gh and authenticate."))
    else:
        auth = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, check=False)
        if auth.returncode == 0:
            if provider == "github":
                try:
                    github = projects.find_config(root)
                    if github is None:
                        raise projects.ProjectError("GitHub Project URL is missing")
                    view = projects.project_view(github[1])
                    detail = f"Project accessible: {view.get('title', github[1].url)}"
                    results.append(Check("GitHub Projects", "available", detail))
                except projects.ProjectError as error:
                    results.append(Check("GitHub Projects", "blocked", str(error), "Run: gh auth refresh -s project"))
            else:
                results.append(Check("GitHub Projects", "available", "GitHub CLI authenticated."))
        else:
            results.append(Check("GitHub Projects", "blocked", "GitHub CLI is not authenticated.", "Run: gh auth login"))

    mcp_files = linear_mcp_files(root)
    if mcp_files:
        results.append(Check("Linear", "available", f"Linear MCP configuration found in {mcp_files[0]}."))
    elif os.environ.get("LINEAR_API_KEY"):
        results.append(Check("Linear", "available", "LINEAR_API_KEY is set."))
    else:
        results.append(Check("Linear", "missing", "No Linear MCP configuration or LINEAR_API_KEY found.", "Configure Linear MCP or set LINEAR_API_KEY."))
    return results


def render(root: Path) -> str:
    rows = checks(root)
    width = max(len(row.name) for row in rows)
    lines = ["Engineering context doctor", ""]
    for row in rows:
        lines.append(f"  {row.name.ljust(width)}  {row.status.ljust(9)}  {row.detail}")
        if row.fix:
            lines.append(f"  {' '.ljust(width)}             {row.fix}")
    return "\n".join(lines)
