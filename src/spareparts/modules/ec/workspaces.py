"""Synchronize engineering-context artifacts to a Spare Parts API workspace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class WorkspaceSyncError(RuntimeError):
    pass


_DEFAULT_API_URL = "https://api.sparepartslabs.com"
_ARTIFACT_NAMES = {
    "huddle.md": "huddle",
    "spec.md": "spec",
    "plan.md": "plan",
    "tasks.md": "tasks",
}
_STATUS_MAX_LENGTH = 120
_HUDDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _config_path(root: Path) -> Path:
    return root / ".sp" / "integrations.json"


def configure(root: Path, workspace_id: str, api_url: str = _DEFAULT_API_URL) -> Path:
    path = _config_path(root)
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceSyncError(f"cannot read {path}: {error}") from error
    document["spareparts_workspace"] = {
        "workspace_id": workspace_id,
        "api_url": api_url.rstrip("/"),
        "sync": "prompt",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def find_config(start: Path) -> tuple[Path, dict[str, Any]] | None:
    directory = start if start.is_dir() else start.parent
    for root in (directory, *directory.parents):
        path = _config_path(root)
        if not path.exists():
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8")).get("spareparts_workspace")
        except (OSError, json.JSONDecodeError) as error:
            raise WorkspaceSyncError(f"invalid integrations configuration in {path}: {error}") from error
        if isinstance(config, dict) and config.get("workspace_id"):
            return root, config
    return None


def artifacts(root: Path) -> list[Path]:
    found: set[Path] = set()
    huddles = root / ".sp" / "huddles"
    if huddles.exists():
        found.update(huddles.glob("*/huddle.md"))
    for name in ("spec.md", "plan.md", "tasks.md"):
        found.update(path for path in root.glob(f"**/specs/*/{name}") if ".git" not in path.parts)
    return sorted(found)


def _git(args: list[str], cwd: Path) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _repo_root(path: Path, workspace: Path) -> Path | None:
    for directory in (path.parent, *path.parent.parents):
        if (directory / ".git").exists():
            return directory
        if directory == workspace:
            break
    return workspace if (workspace / ".git").exists() else None


def _landed_at_ref(path: Path, workspace: Path, ref: str) -> bool:
    repo = _repo_root(path, workspace)
    if repo is None:
        return False
    relative = path.resolve().relative_to(repo.resolve()).as_posix()
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative}"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout == path.read_bytes()


def _repository(repo: Path | None) -> str | None:
    if repo is None:
        return None
    remote = _git(["config", "--get", "remote.origin.url"], repo)
    if not remote:
        return repo.name
    match = re.search(r"(?:github\.com[:/])(.+?)(?:\.git)?$", remote)
    return match.group(1) if match else remote


def _title(content: str, path: Path) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].removeprefix("Huddle:").strip()
    return path.parent.name


def _status(content: str) -> str | None:
    match = re.search(r"^\*\*Status\*\*:\s*(.+)$", content, re.MULTILINE | re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).strip()
    canonical = value.split(maxsplit=1)[0].lower()
    return canonical if canonical in {"active", "blocked", "complete"} else value[:_STATUS_MAX_LENGTH]


def _linked_huddle(path: Path, root: Path) -> str | None:
    huddles = root / ".sp" / "huddles"
    if not huddles.exists():
        return None
    target = path.resolve()
    for huddle in huddles.glob("*/huddle.md"):
        for line in huddle.read_text(encoding="utf-8").splitlines():
            columns = [column.strip() for column in line.split("|")]
            if len(columns) < 5 or not columns[1] or not columns[3]:
                continue
            repo_match = re.search(r"`([^`]+)`", columns[1])
            spec_match = re.search(r"`([^`]+)`", columns[3])
            repo_name = repo_match.group(1) if repo_match else columns[1].strip("`")
            spec_path = spec_match.group(1) if spec_match else columns[3].strip("`")
            candidate = (root / repo_name / spec_path).resolve()
            if candidate == target or candidate == target.parent:
                return huddle.parent.name
    return None


def payload(path: Path, root: Path, *, event: str, main_commit: str | None = None) -> tuple[str, dict[str, Any]]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise WorkspaceSyncError(f"{path} is outside configured workspace {root}") from error
    if not path.is_file():
        raise WorkspaceSyncError(f"artifact does not exist: {path}")

    content = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    repo = _repo_root(path, root)
    repository = _repository(repo)
    git_cwd = repo or root
    actor_name = _git(["config", "--get", "user.name"], git_cwd)
    actor_email = _git(["config", "--get", "user.email"], git_cwd)
    source_commit = _git(["log", "-1", "--format=%H", "--", str(path)], git_cwd) if repo else None
    branch = _git(["branch", "--show-current"], git_cwd) if repo else None
    dirty = bool(_git(["status", "--porcelain", "--", str(path)], git_cwd)) if repo else None
    kind = _ARTIFACT_NAMES.get(path.name, "other")
    huddle_id = path.parent.name if kind == "huddle" else _linked_huddle(path, root)
    artifact_id = hashlib.sha256(f"{repository or 'workspace'}:{relative}".encode()).hexdigest()[:32]
    revision_id = hashlib.sha256(
        f"{artifact_id}:{event}:{content_hash}:{source_commit or ''}:{main_commit or ''}".encode()
    ).hexdigest()
    return artifact_id, {
        "revision_id": revision_id,
        "kind": kind,
        "title": _title(content, path),
        "source_path": relative,
        "content": content,
        "content_hash": content_hash,
        "status": _status(content),
        "repository": repository,
        "huddle_id": huddle_id,
        "event": event,
        "actor": {"name": actor_name, "email": actor_email},
        "git": {
            "repository": repository,
            "branch": branch,
            "source_commit": source_commit,
            "main_commit": main_commit,
            "dirty": dirty,
        },
    }


def _send(
    api_url: str,
    key: str,
    workspace_id: str,
    artifact_id: str,
    body: dict[str, Any],
) -> dict:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/traces/v1/artifacts/{artifact_id}",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "x-spareparts-workspace-id": workspace_id,
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise WorkspaceSyncError(f"workspace sync failed ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise WorkspaceSyncError(f"workspace sync failed: {error.reason}") from error


def _fetch_huddles(api_url: str, key: str, workspace_id: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/traces/v1/huddles",
        headers={"authorization": f"Bearer {key}", "x-spareparts-workspace-id": workspace_id},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise WorkspaceSyncError(f"huddle pull failed ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise WorkspaceSyncError(f"huddle pull failed: {error.reason}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkspaceSyncError(f"huddle pull returned invalid JSON: {error}") from error

    huddles = document.get("huddles") if isinstance(document, dict) else document
    if not isinstance(huddles, list) or not all(isinstance(item, dict) for item in huddles):
        raise WorkspaceSyncError("huddle pull returned an invalid response")
    return huddles


def pull_huddles(start: Path, *, force: bool = False, dry_run: bool = False) -> list[dict[str, str]]:
    """Download every huddle visible to the configured workspace."""
    configured = find_config(start)
    if configured is None:
        raise WorkspaceSyncError("no .sp/integrations.json with spareparts_workspace configuration found")
    root, config = configured
    key = os.environ.get("SPAREPARTS_READ_KEY")
    if not key:
        raise WorkspaceSyncError("SPAREPARTS_READ_KEY is required")
    remote = _fetch_huddles(config.get("api_url", _DEFAULT_API_URL), key, str(config["workspace_id"]))

    results: list[dict[str, str]] = []
    for item in remote:
        huddle_id = item.get("huddle_id") or item.get("id")
        content = item.get("content")
        if not isinstance(huddle_id, str) or not _HUDDLE_ID.fullmatch(huddle_id):
            raise WorkspaceSyncError("huddle pull returned an invalid huddle_id")
        if not isinstance(content, str):
            raise WorkspaceSyncError(f"huddle {huddle_id!r} has no content")
        path = root / ".sp" / "huddles" / huddle_id / "huddle.md"
        relative = path.relative_to(root).as_posix()
        if path.exists() and not force:
            results.append({"action": "skipped", "path": relative})
            continue
        action = "updated" if path.exists() else "created"
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        results.append({"action": action, "path": relative})
    return results


def sync(
    start: Path,
    paths: list[Path] | None = None,
    *,
    event: str = "synced",
    main_commit: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    configured = find_config(start)
    if configured is None:
        raise WorkspaceSyncError("no .sp/integrations.json with spareparts_workspace configuration found")
    root, config = configured
    selected = artifacts(root) if paths is None else paths
    if event == "landed_on_main":
        if not main_commit:
            raise WorkspaceSyncError("main reconciliation requires --ref")
        selected = [
            path for path in selected if _landed_at_ref(path, root, main_commit)
        ]
    if not selected:
        raise WorkspaceSyncError(f"no artifacts found for {event}")
    key = os.environ.get("SPAREPARTS_INGEST_KEY")
    if not dry_run and not key:
        raise WorkspaceSyncError("SPAREPARTS_INGEST_KEY is required")

    results = []
    for path in selected:
        artifact_id, body = payload(path, root, event=event, main_commit=main_commit)
        if dry_run:
            results.append({"artifact_id": artifact_id, "path": body["source_path"], "event": event})
        else:
            results.append(
                _send(
                    config.get("api_url", _DEFAULT_API_URL),
                    key or "",
                    str(config["workspace_id"]),
                    artifact_id,
                    body,
                )
            )
    return results
