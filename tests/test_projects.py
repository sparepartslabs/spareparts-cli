from __future__ import annotations

import json
from pathlib import Path

import pytest

from spareparts.modules.ec import projects


def test_configure_and_find_project(tmp_path):
    path = projects.configure(
        tmp_path, "https://github.com/orgs/sparepartslabs/projects/1"
    )
    assert path == tmp_path / ".sp/integrations.json"
    root, project = projects.find_config(tmp_path / "repo/specs/001/spec.md")
    assert root == tmp_path
    assert project.owner == "sparepartslabs"
    assert project.number == 1


def test_invalid_project_url_is_rejected():
    with pytest.raises(projects.ProjectError, match="GitHub Project URL"):
        projects.parse_url("https://github.com/sparepartslabs/repo")


def test_sync_creates_then_updates_by_marker(tmp_path):
    projects.configure(tmp_path, "https://github.com/orgs/sparepartslabs/projects/1")
    huddle = tmp_path / ".sp/huddles/001-project-sync/huddle.md"
    huddle.parent.mkdir(parents=True)
    huddle.write_text("# Huddle: Project sync\n\n**Status**: active\n", encoding="utf-8")
    calls: list[list[str]] = []
    listed_items: list[dict] = []

    def runner(arguments: list[str]) -> str:
        calls.append(arguments)
        if arguments[2] == "view":
            return json.dumps({"id": "PROJECT_ID", "title": "Huddles"})
        if arguments[2] == "item-list":
            return json.dumps({"items": listed_items})
        if arguments[2] == "item-create":
            return json.dumps({"id": "ITEM_ID"})
        if arguments[2] == "item-edit":
            return json.dumps({"id": "ITEM_ID"})
        raise AssertionError(arguments)

    created = projects.sync_huddle(huddle, runner=runner)
    assert created["action"] == "create"
    create_call = next(call for call in calls if call[2] == "item-create")
    body = create_call[create_call.index("--body") + 1]
    assert "<!-- sp:huddle:.sp/huddles/001-project-sync/huddle.md -->" in body

    listed_items.append({"id": "ITEM_ID", "content": {"body": body}})
    calls.clear()
    updated = projects.sync_huddle(huddle, runner=runner)
    assert updated["action"] == "update"
    assert any(call[2] == "item-edit" for call in calls)


def test_sync_dry_run_does_not_write(tmp_path):
    projects.configure(tmp_path, "https://github.com/orgs/sparepartslabs/projects/1")
    huddle = tmp_path / ".sp/huddles/001-dry/huddle.md"
    huddle.parent.mkdir(parents=True)
    huddle.write_text("# Huddle: Dry run\n", encoding="utf-8")

    def runner(arguments: list[str]) -> str:
        if arguments[2] == "view":
            return json.dumps({"id": "PROJECT_ID"})
        if arguments[2] == "item-list":
            return json.dumps({"items": []})
        raise AssertionError("dry run attempted a write")

    result = projects.sync_huddle(huddle, dry_run=True, runner=runner)
    assert result["action"] == "create"
