from datetime import datetime, timezone
import hashlib
import subprocess

import pytest

from spareparts.modules.odl import (
    EventType, Identity, MemoryReporter, ProvenanceRecorder, ValidationStatus,
    resolve_base_commit, select_repository,
)


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repository(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README").write_text("fixture\n")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "-qm", "fixture")
    return _git(tmp_path, "rev-parse", "HEAD")


def _recorder():
    reporter = MemoryReporter()
    recorder = ProvenanceRecorder(
        reporter, run_id="opaque-run", attempt_id="opaque-attempt",
        actor=Identity("cli", "spareparts-cli"), source=Identity("command", "sp build"),
        clock=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
    )
    return reporter, recorder


def test_resolves_exact_base_sha_without_mutating_checkout(tmp_path):
    sha = _repository(tmp_path)
    selection = select_repository(
        tmp_path, repository="org/repo", rationale="issue target", requested_branch="HEAD"
    )
    assert selection.base_commit_sha == sha
    assert resolve_base_commit(tmp_path, "HEAD") == sha
    with pytest.raises(ValueError, match="unable to resolve"):
        resolve_base_commit(tmp_path, "missing-revision")


def test_records_repository_selection_with_opaque_ids(tmp_path):
    _repository(tmp_path)
    reporter, recorder = _recorder()
    receipt = recorder.repositories_selected("opaque-event", [select_repository(
        tmp_path, repository="org/repo", rationale="selected by issue", requested_branch="HEAD"
    )])
    assert receipt.sequence == 1
    event = reporter.events[0]
    assert event.event_type is EventType.REPOSITORIES_SELECTED
    assert event.to_dict()["event_id"] == "opaque-event"
    assert "sequence" not in event.to_dict()


def test_artifact_hashes_content_but_never_reports_it():
    reporter, recorder = _recorder()
    content = b"artifact bytes"
    recorder.artifact_recorded(
        "artifact-event", stage="tasks", logical_path="specs/001/tasks.md",
        media_type="text/markdown", content=content,
    )
    payload = reporter.events[0].payload
    assert payload["sha256"] == hashlib.sha256(content).hexdigest()
    assert "content" not in payload


def test_records_safe_validation_commit_and_pull_request():
    reporter, recorder = _recorder()
    recorder.validation_recorded(
        "validation-event", command_label="pytest focused", status=ValidationStatus.PASSED,
        duration_ms=42, summary="12 tests passed", counts={"passed": 12},
    )
    recorder.commit_recorded("commit-event", repository="org/repo", commit_sha="a" * 40)
    recorder.pull_request_recorded(
        "pr-event", repository="org/repo", number=9, url="https://example.invalid/pr/9",
        head="feature", base="main", status="open",
    )
    assert [event.event_type for event in reporter.events] == [
        EventType.VALIDATION_RECORDED, EventType.COMMIT_RECORDED, EventType.PULL_REQUEST_RECORDED
    ]


@pytest.mark.parametrize("summary", ["Bearer abcdefghijklmnop", "sk-abcdefghijklmnop", "ghp_abcdefghijklmnop"])
def test_rejects_unredacted_credential_values(summary):
    reporter, recorder = _recorder()
    with pytest.raises(ValueError, match="credential"):
        recorder.validation_recorded(
            "event", command_label="test", status=ValidationStatus.FAILED,
            duration_ms=1, summary=summary,
        )
    assert reporter.events == []


def test_rejects_raw_output_and_invalid_values():
    _, recorder = _recorder()
    with pytest.raises(ValueError):
        recorder._append("event", EventType.VALIDATION_RECORDED, {"terminal_output": "oops"})
    with pytest.raises(ValueError):
        recorder.commit_recorded("event", repository="org/repo", commit_sha="short")
    with pytest.raises(ValueError):
        recorder.validation_recorded(
            "event", command_label="test", status=ValidationStatus.PASSED,
            duration_ms=-1, summary="invalid",
        )


def test_cli_owned_event_enum_excludes_runner_lifecycle_transitions():
    assert "stage.started" not in {event.value for event in EventType}
    assert "attempt.completed" not in {event.value for event in EventType}
