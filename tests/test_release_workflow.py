from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "release-range.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def commit(repo: Path, message: str) -> str:
    change = repo / "change.txt"
    with change.open("a") as handle:
        handle.write(f"{message}\n")
    git(repo, "add", "change.txt")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def fixture(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "tests@example.com")
    git(tmp_path, "config", "user.name", "Release Tests")
    return tmp_path


def resolve_range(repo: Path, tag: str) -> dict[str, str]:
    output = repo / "output.txt"
    subprocess.run([RESOLVER, tag, output], cwd=repo, check=True)
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_release_range_uses_previous_stable_tag(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    commit(repo, "feat: first")
    git(repo, "tag", "v0.1.0")
    git(repo, "tag", "v0")
    commit(repo, "feat: preview")
    git(repo, "tag", "v0.2.0-beta.1")
    commit(repo, "feat: current")
    git(repo, "tag", "v0.2.0")
    assert resolve_range(repo, "v0.2.0") == {"from": "v0.1.0", "to": "v0.2.0"}


def test_release_range_first_release_falls_back_to_root(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    root = commit(repo, "feat: first")
    commit(repo, "fix: follow-up")
    git(repo, "tag", "v0.1.0")
    assert resolve_range(repo, "v0.1.0") == {"from": root, "to": "v0.1.0"}


def test_release_range_rejects_invalid_or_empty_tags(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    commit(repo, "feat: only")
    git(repo, "tag", "v0.1.0")
    with pytest.raises(subprocess.CalledProcessError):
        resolve_range(repo, "not-a-version")
    with pytest.raises(subprocess.CalledProcessError):
        resolve_range(repo, "v0.1.0")


def test_release_workflow_is_anthropic_only_and_publishes_canonical_s3_markdown() -> None:
    source = WORKFLOW.read_text()
    assert "uses: sparepartslabs/spareparts-changelog@v0" in source
    assert "provider: anthropic" in source
    assert "instructions: ${{ vars.CHANGELOG_INSTRUCTIONS }}" in source
    assert "anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}" in source
    assert "openai-api-key" not in source and "gemini-api-key" not in source
    assert 'write-repository: "false"' in source
    assert "id-token: write" in source
    assert "uses: aws-actions/configure-aws-credentials@v6" in source
    assert "role-to-assume: ${{ vars.CHANGELOG_AWS_ROLE_ARN }}" in source
    assert "aws-region: ${{ vars.AWS_REGION || 'us-east-1' }}" in source
    assert 'publish-s3: "true"' in source
    assert "s3-bucket: ${{ vars.CHANGELOG_S3_BUCKET }}" in source
    assert "s3-key: releases/spareparts-cli/${{ steps.changelog-object.outputs.version }}.md" in source
    assert "version=${TAG#v}" in source
    assert 'publish-linkedin: "false"' in source
    assert "--notes-file release-notes.md --verify-tag" in source
    assert "--generate-notes" not in source
