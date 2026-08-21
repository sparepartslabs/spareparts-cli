"""Provider-neutral adapters for pinned coding-agent CLIs."""

from __future__ import annotations

import os
from pathlib import Path

from .clients import Commands
from .models import BuildError

_SECRETS = {
    "SPAREPARTS_INGEST_KEY", "GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY",
}


def clean_environment(*allowed: str) -> dict[str, str]:
    """Keep normal process settings while exposing only explicitly allowed credentials."""
    result = {key: value for key, value in os.environ.items() if key not in _SECRETS}
    for key in allowed:
        if os.environ.get(key):
            result[key] = os.environ[key]
    return result


def invoke(agent: str, model: str | None, prompt: str, checkout: Path, commands: Commands, timeout: int) -> str:
    if agent == "codex":
        if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("CODEX_HOME"):
            raise BuildError("Codex needs OPENAI_API_KEY or an ephemeral CODEX_HOME")
        argv = ["codex", "exec", "--approve-for-me", "--sandbox", "workspace-write", "--skip-git-repo-check"]
        if model: argv += ["--model", model]
        argv += ["-"]
        environment = clean_environment("OPENAI_API_KEY")
    elif agent == "claude":
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise BuildError("Claude needs ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN")
        argv = ["claude", "--print", "--permission-mode", "acceptEdits"]
        if model: argv += ["--model", model]
        argv += [prompt]
        environment = clean_environment("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
    else:
        raise BuildError(f"unknown build agent: {agent}")
    result = commands(argv, cwd=checkout, timeout=timeout, input_text=prompt if agent == "codex" else None, env=environment)
    if result.code:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise BuildError(f"{agent} failed with exit {result.code}: {detail[-1000:]}", "retryable_failure")
    return result.stdout.strip()[:4000]
