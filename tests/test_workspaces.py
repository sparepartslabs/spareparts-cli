import json
import subprocess
from io import BytesIO
from pathlib import Path

from spareparts.modules.ec import workspaces


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_configure_preserves_other_integrations(tmp_path):
    path = tmp_path / ".sp/integrations.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"work_management": {"provider": "github"}}))

    workspaces.configure(tmp_path, "workspace_123", "http://localhost:8000/")

    document = json.loads(path.read_text())
    assert document["work_management"]["provider"] == "github"
    assert document["spareparts_workspace"] == {
        "workspace_id": "workspace_123",
        "api_url": "http://localhost:8000",
        "sync": "prompt",
    }


def test_payload_captures_git_actor_and_trajectory(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    artifact = repo / "specs/001-food/spec.md"
    artifact.parent.mkdir(parents=True)
    (repo / ".git").mkdir()
    artifact.write_text("# Food tracking\n\n**Status**: implemented\n")
    huddle = tmp_path / ".sp/huddles/007-food/huddle.md"
    huddle.parent.mkdir(parents=True)
    huddle.write_text(
        "# Huddle: Food\n\n| Repo | Role | Spec | Stage |\n"
        "|---|---|---|---|\n"
        "| `repo` (`api`) | API | `specs/001-food` | implemented |\n"
    )

    answers = {
        ("config", "--get", "remote.origin.url"): "git@github.com:acme/food.git",
        ("config", "--get", "user.name"): "Ada Lovelace",
        ("config", "--get", "user.email"): "ada@example.com",
        ("log", "-1", "--format=%H", "--", str(artifact)): "abc123",
        ("branch", "--show-current"): "main",
        ("status", "--porcelain", "--", str(artifact)): "",
    }
    monkeypatch.setattr(workspaces, "_git", lambda args, cwd: answers.get(tuple(args)))

    artifact_id, body = workspaces.payload(
        artifact, tmp_path, event="landed_on_main", main_commit="def456"
    )

    assert len(artifact_id) == 32
    assert body["actor"] == {"name": "Ada Lovelace", "email": "ada@example.com"}
    assert body["repository"] == "acme/food"
    assert body["status"] == "implemented"
    assert body["huddle_id"] == "007-food"
    assert body["event"] == "landed_on_main"
    assert body["git"]["source_commit"] == "abc123"
    assert body["git"]["main_commit"] == "def456"
    assert body["git"]["dirty"] is False


def test_huddle_status_uses_the_leading_canonical_token(tmp_path):
    artifact = tmp_path / ".sp/huddles/003-board/huddle.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Huddle: Board\n\n**Status**: complete (merged 2026-08-09)\n")

    _, body = workspaces.payload(artifact, tmp_path, event="synced")

    assert body["status"] == "complete"


def test_payload_caps_status_to_api_limit(tmp_path):
    artifact = tmp_path / "specs/001-food/spec.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(f"# Food tracking\n\n**Status**: {'x' * 140}\n")

    _, body = workspaces.payload(artifact, tmp_path, event="synced")

    assert body["status"] == "x" * 120


def test_artifact_discovery_includes_huddles_and_spec_kit_outputs(tmp_path):
    paths = [
        tmp_path / ".sp/huddles/001-food/huddle.md",
        tmp_path / "api/specs/002-food/spec.md",
        tmp_path / "api/specs/002-food/plan.md",
        tmp_path / "api/specs/002-food/tasks.md",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n")

    assert workspaces.artifacts(tmp_path) == sorted(paths)


def test_landed_at_ref_requires_the_exact_committed_content(tmp_path):
    repo = tmp_path / "repo"
    artifact = repo / "specs/001/spec.md"
    artifact.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    artifact.write_text("# Original\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add spec"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()

    assert workspaces._landed_at_ref(artifact, tmp_path, commit) is True
    artifact.write_text("# Changed after main\n")
    assert workspaces._landed_at_ref(artifact, tmp_path, commit) is False
    huddle = tmp_path / ".sp/huddles/001/huddle.md"
    huddle.parent.mkdir(parents=True)
    huddle.write_text("# Huddle\n")
    assert workspaces._landed_at_ref(huddle, tmp_path, commit) is False


def test_pull_huddles_downloads_all_and_authenticates(tmp_path, monkeypatch):
    workspaces.configure(tmp_path, "workspace_123", "https://example.test/")
    monkeypatch.setenv("SPAREPARTS_READ_KEY", "read_secret")
    seen = {}

    def urlopen(request, timeout):
        seen["request"] = request
        return _Response(json.dumps({"huddles": [
            {"huddle_id": "001-alpha", "content": "# Huddle: Alpha\n"},
            {"huddle_id": "002-beta", "content": "# Huddle: Beta\n"},
        ]}).encode())

    monkeypatch.setattr(workspaces.urllib.request, "urlopen", urlopen)
    results = workspaces.pull_huddles(tmp_path)

    assert [result["action"] for result in results] == ["created", "created"]
    assert (tmp_path / ".sp/huddles/001-alpha/huddle.md").read_text() == "# Huddle: Alpha\n"
    assert seen["request"].full_url == "https://example.test/traces/v1/huddles"
    assert seen["request"].get_header("Authorization") == "Bearer read_secret"
    assert seen["request"].get_header("X-spareparts-workspace-id") == "workspace_123"


def test_pull_huddles_preserves_existing_files_unless_forced(tmp_path, monkeypatch):
    workspaces.configure(tmp_path, "workspace_123")
    monkeypatch.setenv("SPAREPARTS_READ_KEY", "read_secret")
    path = tmp_path / ".sp/huddles/001-alpha/huddle.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Local edits\n")
    monkeypatch.setattr(workspaces, "_fetch_huddles", lambda *args: [
        {"huddle_id": "001-alpha", "content": "# Remote\n"}
    ])

    assert workspaces.pull_huddles(tmp_path)[0]["action"] == "skipped"
    assert path.read_text() == "# Local edits\n"
    assert workspaces.pull_huddles(tmp_path, force=True)[0]["action"] == "updated"
    assert path.read_text() == "# Remote\n"


def test_pull_huddles_rejects_unsafe_ids(tmp_path, monkeypatch):
    workspaces.configure(tmp_path, "workspace_123")
    monkeypatch.setenv("SPAREPARTS_READ_KEY", "read_secret")
    monkeypatch.setattr(workspaces, "_fetch_huddles", lambda *args: [
        {"huddle_id": "../../escape", "content": "bad"}
    ])

    try:
        workspaces.pull_huddles(tmp_path)
    except workspaces.WorkspaceSyncError as error:
        assert "invalid huddle_id" in str(error)
    else:
        raise AssertionError("unsafe huddle ID was accepted")
