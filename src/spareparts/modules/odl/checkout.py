"""Read-only deterministic Git checkout provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .models import require_git_sha


@dataclass(frozen=True)
class RepositorySelection:
    repository: str
    rationale: str
    requested_branch: str
    base_commit_sha: str

    def to_dict(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "rationale": self.rationale,
            "requested_branch": self.requested_branch,
            "base_commit_sha": self.base_commit_sha,
        }


def resolve_base_commit(repository_path: str | Path, revision: str) -> str:
    """Resolve *revision* to the exact commit without modifying the checkout."""
    path = Path(repository_path).resolve()
    if not revision.strip():
        raise ValueError("revision must be non-empty")
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--verify", f"{revision}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ValueError(f"unable to resolve Git revision {revision!r}")
    return require_git_sha(result.stdout.strip(), "base_commit_sha")


def select_repository(
    repository_path: str | Path, *, repository: str, rationale: str, requested_branch: str
) -> RepositorySelection:
    if not repository.strip() or not rationale.strip() or not requested_branch.strip():
        raise ValueError("repository, rationale, and requested_branch must be non-empty")
    return RepositorySelection(
        repository=repository,
        rationale=rationale,
        requested_branch=requested_branch,
        base_commit_sha=resolve_base_commit(repository_path, requested_branch),
    )
