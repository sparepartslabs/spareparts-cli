import json
import subprocess
from pathlib import Path

from spareparts.modules.ec import nodes


def test_configure_preserves_other_integrations(tmp_path):
    path = tmp_path / ".sp/integrations.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"work_management": {"provider": "github"}}))

    nodes.configure(tmp_path, "node_123", "http://localhost:8000/")

    document = json.loads(path.read_text())
    assert document["work_management"]["provider"] == "github"
    assert document["spareparts_node"] == {
        "node_id": "node_123",
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
        "| repo | API | specs/001-food/spec.md | implemented |\n"
    )

    answers = {
        ("config", "--get", "remote.origin.url"): "git@github.com:acme/food.git",
        ("config", "--get", "user.name"): "Ada Lovelace",
        ("config", "--get", "user.email"): "ada@example.com",
        ("log", "-1", "--format=%H", "--", str(artifact)): "abc123",
        ("branch", "--show-current"): "main",
        ("status", "--porcelain", "--", str(artifact)): "",
    }
    monkeypatch.setattr(nodes, "_git", lambda args, cwd: answers.get(tuple(args)))

    artifact_id, body = nodes.payload(
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

    assert nodes.artifacts(tmp_path) == sorted(paths)


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

    assert nodes._landed_at_ref(artifact, tmp_path, commit) is True
    artifact.write_text("# Changed after main\n")
    assert nodes._landed_at_ref(artifact, tmp_path, commit) is False
    huddle = tmp_path / ".sp/huddles/001/huddle.md"
    huddle.parent.mkdir(parents=True)
    huddle.write_text("# Huddle\n")
    assert nodes._landed_at_ref(huddle, tmp_path, commit) is False
