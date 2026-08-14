"""Validated build-runner contracts with no credentials."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


class BuildError(RuntimeError):
    """Safe build failure carrying a stable category."""

    def __init__(self, message: str, category: str = "permanent_failure"):
        super().__init__(message)
        self.category = category


_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def repository_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _REPO.fullmatch(value):
        raise BuildError(f"{field} must be OWNER/REPOSITORY")
    return value


@dataclass(frozen=True)
class Target:
    repository_id: str
    name_with_owner: str
    base_branch: str | None
    rationale: str
    confidence: float

    @classmethod
    def parse(cls, value: Any) -> "Target":
        if not isinstance(value, dict):
            raise BuildError("Core target must be an object")
        repository_id = value.get("repository_id")
        confidence = value.get("confidence")
        if not isinstance(repository_id, str) or not repository_id:
            raise BuildError("Core target repository_id is required")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise BuildError("Core target confidence must be between 0 and 1")
        base = value.get("base_branch")
        if base is not None and (not isinstance(base, str) or not base.strip()):
            raise BuildError("Core target base_branch must be text or null")
        return cls(repository_id, repository_name(value.get("name_with_owner"), "Core target name"), base, str(value.get("rationale") or "").strip(), float(confidence))


@dataclass(frozen=True)
class Plan:
    ingestion: dict[str, Any]
    targets: tuple[Target, ...]

    @classmethod
    def parse(cls, value: Any) -> "Plan":
        if not isinstance(value, dict) or not isinstance(value.get("ingestion"), dict) or not isinstance(value.get("targets"), list):
            raise BuildError("Core latest ingestion response was invalid", "retryable_failure")
        ingestion = value["ingestion"]
        for field in ("id", "source_repository", "issue_number", "issue_title", "ontology_revision_id"):
            if field not in ingestion:
                raise BuildError(f"Core ingestion missing {field}", "retryable_failure")
        if ingestion.get("status") not in ("accepted", "partial"):
            raise BuildError("latest ingestion is not build-eligible")
        return cls(ingestion, tuple(Target.parse(item) for item in value["targets"]))


@dataclass(frozen=True)
class Policy:
    allowed_orgs: frozenset[str]
    allowed_repositories: frozenset[str]
    max_fanout: int
    validation_commands: tuple[tuple[str, ...], ...]
    timeout: int = 1800

    def authorize(self, plan: Plan) -> None:
        if not self.allowed_orgs:
            raise BuildError("at least one --allowed-org is required")
        if not plan.targets:
            raise BuildError("build plan has no targets")
        if len(plan.targets) > self.max_fanout:
            raise BuildError(f"build plan has {len(plan.targets)} targets; maximum is {self.max_fanout}")
        seen: set[str] = set()
        for target in plan.targets:
            owner = target.name_with_owner.split("/", 1)[0].lower()
            name = target.name_with_owner.lower()
            if owner not in self.allowed_orgs:
                raise BuildError(f"target organization is not allowed: {target.name_with_owner}")
            if self.allowed_repositories and name not in self.allowed_repositories:
                raise BuildError(f"target repository is not allowed: {target.name_with_owner}")
            if target.repository_id in seen:
                raise BuildError(f"duplicate target repository: {target.name_with_owner}")
            seen.add(target.repository_id)


def safe_changed_paths(text: str) -> list[str]:
    paths = [line.strip() for line in text.splitlines() if line.strip()]
    for value in paths:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] in ((".git",), (".github",)):
            raise BuildError(f"agent changed forbidden path: {value}", "rejected")
    return sorted(set(paths))


_LOCAL_CONTROL = (
    (".sp",),
    ("specs",),
    (".agents", "skills"),
    (".claude", "commands"),
    (".codex",),
    (".cursor", "commands"),
    (".gemini", "commands"),
)
_CREDENTIAL_NAMES = {".env", ".env.local", "credentials", "credentials.json", "secrets.json"}


def classify_changed_paths(values: list[str]) -> tuple[list[str], list[str]]:
    """Return publishable and local-control paths, rejecting unsafe paths."""
    product: set[str] = set()
    control: set[str] = set()
    for value in values:
        if not value or any(ord(character) < 32 for character in value):
            raise BuildError("agent produced an invalid changed path", "rejected")
        path = PurePosixPath(value)
        lowered = tuple(part.lower() for part in path.parts)
        if path.is_absolute() or ".." in path.parts or lowered[:1] in ((".git",), (".github",)):
            raise BuildError(f"agent changed forbidden path: {value}", "rejected")
        if any(lowered[: len(prefix)] == prefix for prefix in _LOCAL_CONTROL):
            control.add(value)
            continue
        name = path.name.lower()
        if name in _CREDENTIAL_NAMES or name.endswith((".pem", ".key", ".p12", ".pfx")):
            raise BuildError(f"agent changed credential-like path: {value}", "rejected")
        product.add(value)
    return sorted(product), sorted(control)
