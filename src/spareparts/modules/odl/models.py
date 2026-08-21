"""Typed, transport-neutral ODL provenance facts owned by the CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Mapping


class EventType(StrEnum):
    REPOSITORIES_SELECTED = "repositories.selected"
    ARTIFACT_RECORDED = "artifact.recorded"
    VALIDATION_RECORDED = "validation.recorded"
    COMMIT_RECORDED = "commit.recorded"
    PULL_REQUEST_RECORDED = "pull_request.recorded"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Identity:
    kind: str
    identity: str

    def __post_init__(self) -> None:
        _nonempty("kind", self.kind)
        _nonempty("identity", self.identity)


@dataclass(frozen=True)
class AppendEvent:
    event_id: str
    run_id: str
    occurred_at: datetime
    event_type: EventType
    actor: Identity
    source: Identity
    payload: Mapping[str, Any]
    schema_version: str = "1"
    attempt_id: str | None = None

    def __post_init__(self) -> None:
        _nonempty("event_id", self.event_id)
        _nonempty("run_id", self.run_id)
        _nonempty("schema_version", self.schema_version)
        if self.attempt_id is not None:
            _nonempty("attempt_id", self.attempt_id)
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        validate_safe_payload(self.payload)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["occurred_at"] = self.occurred_at.isoformat()
        result["event_type"] = self.event_type.value
        if self.attempt_id is None:
            result.pop("attempt_id")
        return result


_FORBIDDEN_KEYS = {
    "authorization", "auth_payload", "credentials", "github_token", "password",
    "provider_auth", "raw_output", "secret", "stderr", "stdout", "terminal_output", "token",
}
_CREDENTIAL_VALUE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{12,}|gh[opusr]_[a-z0-9]{12,}|sk-[a-z0-9_-]{12,})"
)


def validate_safe_payload(value: Any, path: str = "payload") -> None:
    """Reject unsafe field names and recognizable unredacted credential values."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            normalized = key.lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS or any(
                marker in normalized for marker in (
                    "secret", "credential", "token", "password", "authorization",
                    "auth_payload", "terminal_output", "raw_output", "stdout", "stderr",
                )
            ):
                raise ValueError(f"unsafe provenance field: {path}.{key}")
            validate_safe_payload(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            validate_safe_payload(child, f"{path}[{index}]")
    elif isinstance(value, str) and _CREDENTIAL_VALUE.search(value):
        raise ValueError(f"possible unredacted credential in {path}")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} is not JSON-compatible")


def require_git_sha(value: str, field: str = "commit_sha") -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise ValueError(f"{field} must be a 40-character Git SHA")
    return value.lower()


def _nonempty(field: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
