"""Synchronize engineering-context artifacts to a Spare Parts API node."""

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


class NodeSyncError(RuntimeError):
    pass


_DEFAULT_API_URL = "https://api.sparepartslabs.com"
_ARTIFACT_NAMES = {
    "huddle.md": "huddle",
    "spec.md": "spec",
    "plan.md": "plan",
    "tasks.md": "tasks",
}
_STATUS_MAX_LENGTH = 120


def _config_path(root: Path) -> Path:
    return root / ".sp" / "integrations.json"


def configure(root: Path, node_id: str, api_url: str = _DEFAULT_API_URL) -> Path:
    path = _config_path(root)
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as error:
        raise NodeSyncError(f"cannot read {path}: {error}") from error
    document["spareparts_node"] = {
        "node_id": node_id,
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
            config = json.loads(path.read_text(encoding="utf-8")).get("spareparts_node")
        except (OSError, json.JSONDecodeError) as error:
            raise NodeSyncError(f"invalid integrations configuration in {path}: {error}") from error
        if isinstance(config, dict) and config.get("node_id"):
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
    return match.group(1).strip()[:_STATUS_MAX_LENGTH] if match else None


def _linked_huddle(path: Path, root: Path) -> str | None:
    huddles = root / ".sp" / "huddles"
    if not huddles.exists():
        return None
    target = path.resolve()
    for huddle in huddles.glob("*/huddle.md"):
        for line in huddle.read_text(encoding="utf-8").splitlines():
            columns = [column.strip().strip("`") for column in line.split("|")]
            if len(columns) < 5 or not columns[1] or not columns[3]:
                continue
            candidate = root / columns[1] / columns[3]
            if candidate.resolve() == target:
                return huddle.parent.name
    return None


def payload(path: Path, root: Path, *, event: str, main_commit: str | None = None) -> tuple[str, dict[str, Any]]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise NodeSyncError(f"{path} is outside configured workspace {root}") from error
    if not path.is_file():
        raise NodeSyncError(f"artifact does not exist: {path}")

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
    node_id: str,
    artifact_id: str,
    body: dict[str, Any],
) -> dict:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/traces/v1/artifacts/{artifact_id}",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "x-spareparts-node-id": node_id,
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise NodeSyncError(f"node sync failed ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise NodeSyncError(f"node sync failed: {error.reason}") from error


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
        raise NodeSyncError("no .sp/integrations.json with spareparts_node configuration found")
    root, config = configured
    selected = artifacts(root) if paths is None else paths
    if event == "landed_on_main":
        if not main_commit:
            raise NodeSyncError("main reconciliation requires --ref")
        selected = [
            path for path in selected if _landed_at_ref(path, root, main_commit)
        ]
    if not selected:
        raise NodeSyncError(f"no artifacts found for {event}")
    key = os.environ.get("SPAREPARTS_INGEST_KEY")
    if not dry_run and not key:
        raise NodeSyncError("SPAREPARTS_INGEST_KEY is required")

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
                    str(config["node_id"]),
                    artifact_id,
                    body,
                )
            )
    return results
