from __future__ import annotations

import json
from pathlib import Path

import pytest

from spareparts.modules.build.clients import CommandResult
from spareparts.modules.build.models import BuildError, Plan, RepositoryScopeRequest, Target, classify_changed_paths
from spareparts.modules.build.service import PreparedTarget, run_build, workspace_prompt
from test_build import Commands, Core, GitHub, plan, policy


class ScopeCommands(Commands):
    def __init__(self, request):
        super().__init__()
        self.request = request

    def __call__(self, argv, **kwargs):
        if argv and argv[0] in ("codex", "claude"):
            self.calls.append((argv, kwargs))
            self._write_huddle(kwargs["cwd"])
            request = Path(kwargs["cwd"]) / ".sp/repository-scope-request.json"
            request.parent.mkdir(parents=True, exist_ok=True)
            request.write_text(json.dumps(self.request), encoding="utf-8")
            return CommandResult(0, "agent summary", "")
        return super().__call__(argv, **kwargs)


def test_scope_request_parser_is_strict_and_bounded(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"repositories": [{"repository": "sparepartslabs/extra", "rationale": "Shared API lives here"}]}))
    parsed = RepositoryScopeRequest.load(path, {"sparepartslabs/core"})
    assert parsed is not None
    assert parsed.repositories[0].repository == "sparepartslabs/extra"
    for value, message in (
        ({"repositories": [{"repository": "sparepartslabs/core", "rationale": "same"}]}, "already authorized"),
        ({"repositories": [{"repository": "bad", "rationale": "invalid"}]}, "OWNER/REPOSITORY"),
        ({"repositories": [{"repository": "sparepartslabs/extra", "rationale": ""}]}, "rationale"),
    ):
        path.write_text(json.dumps(value))
        with pytest.raises(BuildError, match=message):
            RepositoryScopeRequest.load(path, {"sparepartslabs/core"})


def test_scope_request_rejects_duplicate_and_over_cap(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"repositories": [
        {"repository": "sparepartslabs/extra", "rationale": "one"},
        {"repository": "SparePartsLabs/Extra", "rationale": "two"},
    ]}))
    with pytest.raises(BuildError, match="duplicate"):
        RepositoryScopeRequest.load(path, {"sparepartslabs/core"})
    path.write_text(json.dumps({"repositories": [{"repository": "sparepartslabs/extra", "rationale": "one"}]}))
    with pytest.raises(BuildError, match="maximum"):
        RepositoryScopeRequest.load(path, {f"owner/repo-{index}" for index in range(10)})


def test_valid_scope_request_stops_before_publication(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    core, github = Core(), GitHub()
    commands = ScopeCommands({"repositories": [{"repository": "sparepartslabs/extra", "rationale": "Shared API lives here"}]})
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=core, github=github, commands=commands)
    assert result["status"] == "scope_expansion_requested"
    assert result["scope_expansion"]["repositories"][0]["repository"] == "sparepartslabs/extra"
    assert result["targets"][0]["status"] == "retryable_failure"
    assert result["targets"][0]["failure_category"] == "repository_scope_insufficient"
    assert github.published == []
    assert not any(call[0][:2] in (["git", "commit"], ["git", "push"]) for call in commands.calls)


def test_workspace_prompt_documents_scope_retry(tmp_path):
    target = Target("R_core", "sparepartslabs/core", "main", "Core change", 0.9)
    prepared = [PreparedTarget(target, "main", "attempt", "branch", tmp_path / "checkout")]
    prompt = workspace_prompt(Plan.parse(plan()), prepared, "codex")
    assert ".sp/repository-scope-request.json" in prompt
    assert "Status to blocked" in prompt
    product, control = classify_changed_paths([".sp/repository-scope-request.json"])
    assert product == [] and control == [".sp/repository-scope-request.json"]
