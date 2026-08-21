"""Reporter boundary and helpers for CLI-owned ODL facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Callable, Mapping, Protocol

from .checkout import RepositorySelection
from .models import AppendEvent, EventType, Identity, ValidationStatus, require_git_sha


@dataclass(frozen=True)
class AppendReceipt:
    event_id: str
    sequence: int


class Reporter(Protocol):
    def append(self, event: AppendEvent) -> AppendReceipt: ...


class MemoryReporter:
    """Deterministic reporter for tests and offline composition."""

    def __init__(self) -> None:
        self.events: list[AppendEvent] = []

    def append(self, event: AppendEvent) -> AppendReceipt:
        self.events.append(event)
        return AppendReceipt(event_id=event.event_id, sequence=len(self.events))


class ProvenanceRecorder:
    def __init__(
        self,
        reporter: Reporter,
        *,
        run_id: str,
        actor: Identity,
        source: Identity,
        attempt_id: str | None = None,
        schema_version: str = "1",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.reporter = reporter
        self.run_id = run_id
        self.actor = actor
        self.source = source
        self.attempt_id = attempt_id
        self.schema_version = schema_version
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def repositories_selected(self, event_id: str, selections: list[RepositorySelection]) -> AppendReceipt:
        if not selections:
            raise ValueError("at least one repository selection is required")
        return self._append(event_id, EventType.REPOSITORIES_SELECTED, {"repositories": [s.to_dict() for s in selections]})

    def artifact_recorded(
        self, event_id: str, *, stage: str, logical_path: str, media_type: str,
        content: bytes, safe_uri: str | None = None,
    ) -> AppendReceipt:
        payload: dict[str, Any] = {
            "stage": stage, "logical_path": logical_path, "media_type": media_type,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if safe_uri is not None:
            payload["safe_uri"] = safe_uri
        return self._append(event_id, EventType.ARTIFACT_RECORDED, payload)

    def validation_recorded(
        self, event_id: str, *, command_label: str, status: ValidationStatus,
        duration_ms: int, summary: str, counts: Mapping[str, int] | None = None,
    ) -> AppendReceipt:
        if duration_ms < 0 or any(value < 0 for value in (counts or {}).values()):
            raise ValueError("validation duration and counts must be non-negative")
        payload: dict[str, Any] = {
            "command_label": command_label, "status": status.value,
            "duration_ms": duration_ms, "summary": summary,
        }
        if counts is not None:
            payload["counts"] = dict(counts)
        return self._append(event_id, EventType.VALIDATION_RECORDED, payload)

    def commit_recorded(self, event_id: str, *, repository: str, commit_sha: str) -> AppendReceipt:
        return self._append(event_id, EventType.COMMIT_RECORDED, {
            "repository": repository, "commit_sha": require_git_sha(commit_sha),
        })

    def pull_request_recorded(
        self, event_id: str, *, repository: str, number: int, url: str,
        head: str, base: str, status: str,
    ) -> AppendReceipt:
        if number <= 0:
            raise ValueError("pull request number must be positive")
        return self._append(event_id, EventType.PULL_REQUEST_RECORDED, {
            "repository": repository, "number": number, "url": url,
            "head": head, "base": base, "status": status,
        })

    def _append(self, event_id: str, event_type: EventType, payload: Mapping[str, Any]) -> AppendReceipt:
        return self.reporter.append(AppendEvent(
            event_id=event_id, run_id=self.run_id, attempt_id=self.attempt_id,
            occurred_at=self.clock(), event_type=event_type, actor=self.actor,
            source=self.source, payload=payload, schema_version=self.schema_version,
        ))
