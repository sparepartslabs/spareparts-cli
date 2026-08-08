"""GitHub Projects configuration and huddle synchronization."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class ProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubProject:
    owner: str
    number: int
    url: str


Runner = Callable[[list[str]], str]
_PROJECT_URL = re.compile(
    r"^https://github\.com/(?:orgs|users)/(?P<owner>[^/]+)/projects/(?P<number>\d+)/?$"
)


def parse_url(url: str) -> GitHubProject:
    match = _PROJECT_URL.fullmatch(url.strip())
    if not match:
        raise ProjectError(
            "GitHub Project URL must look like "
            "https://github.com/orgs/OWNER/projects/NUMBER"
        )
    return GitHubProject(
        owner=match.group("owner"),
        number=int(match.group("number")),
        url=url.rstrip("/"),
    )


def _run(arguments: list[str]) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ProjectError(result.stderr.strip() or f"{' '.join(arguments)} failed")
    return result.stdout


def config_path(root: Path) -> Path:
    return root / ".sp" / "integrations.json"


def configure(root: Path, url: str) -> Path:
    project = parse_url(url)
    path = config_path(root)
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectError(f"cannot read {path}: {error}") from error
    document["github_projects"] = {
        "url": project.url,
        "sync": "prompt",
        "transport": "auto",
    }
    document["work_management"] = {
        "provider": "github",
        "url": project.url,
        "sync": "prompt",
        "transport": "auto",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def configure_linear(
    root: Path, workspace: str, team: str | None, transport: str = "auto"
) -> Path:
    path = config_path(root)
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectError(f"cannot read {path}: {error}") from error
    document["work_management"] = {
        "provider": "linear",
        "workspace": workspace.strip().strip("/"),
        "team": team.strip() if team else None,
        "sync": "prompt",
        "transport": transport,
    }
    document.pop("github_projects", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def work_management_config(start: Path) -> tuple[Path, dict] | None:
    directory = start if start.is_dir() else start.parent
    for root in (directory, *directory.parents):
        path = config_path(root)
        if not path.exists():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectError(f"invalid integrations configuration in {path}: {error}") from error
        config = document.get("work_management")
        if isinstance(config, dict):
            return root, config
        github = document.get("github_projects")
        if isinstance(github, dict):
            return root, {"provider": "github", **github}
    return None


def find_config(start: Path) -> tuple[Path, GitHubProject] | None:
    directory = start if start.is_dir() else start.parent
    for root in (directory, *directory.parents):
        path = config_path(root)
        if not path.exists():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            work_management = document.get("work_management", {})
            if work_management.get("provider") == "github":
                url = work_management["url"]
            else:
                url = document["github_projects"]["url"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ProjectError(f"invalid GitHub Projects configuration in {path}: {error}") from error
        return root, parse_url(url)
    return None


def project_view(project: GitHubProject, runner: Runner = _run) -> dict:
    output = runner(
        [
            "gh", "project", "view", str(project.number),
            "--owner", project.owner, "--format", "json",
        ]
    )
    return json.loads(output)


def _marker(root: Path, huddle: Path) -> str:
    try:
        relative = huddle.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ProjectError(f"{huddle} is outside configured workspace {root}") from error
    return f"<!-- sp:huddle:{relative.as_posix()} -->"


def _title(text: str, huddle: Path) -> str:
    for line in text.splitlines():
        if line.startswith("# Huddle:"):
            return line.removeprefix("# Huddle:").strip()
    return huddle.parent.name


def sync_huddle(
    huddle: Path, *, dry_run: bool = False, runner: Runner = _run
) -> dict:
    configured = find_config(huddle)
    if configured is None:
        raise ProjectError("no .sp/integrations.json with github_projects configuration found")
    root, project = configured
    text = huddle.read_text(encoding="utf-8")
    marker = _marker(root, huddle)
    title = _title(text, huddle)
    body = f"{text.rstrip()}\n\n{marker}\n"

    view = project_view(project, runner)
    items = json.loads(
        runner(
            [
                "gh", "project", "item-list", str(project.number),
                "--owner", project.owner, "--limit", "1000", "--format", "json",
            ]
        )
    ).get("items", [])
    existing = next(
        (
            item for item in items
            if marker in str(item.get("content", {}).get("body", ""))
        ),
        None,
    )
    action = "update" if existing else "create"
    if dry_run:
        return {"action": action, "project": project.url, "title": title}

    if existing:
        runner(
            [
                "gh", "project", "item-edit",
                "--id", existing["id"], "--project-id", view["id"],
                "--title", title, "--body", body,
            ]
        )
        item_id = existing["id"]
    else:
        created = json.loads(
            runner(
                [
                    "gh", "project", "item-create", str(project.number),
                    "--owner", project.owner, "--title", title, "--body", body,
                    "--format", "json",
                ]
            )
        )
        item_id = created["id"]
    return {
        "action": action,
        "project": project.url,
        "project_id": view["id"],
        "item_id": item_id,
        "title": title,
    }
