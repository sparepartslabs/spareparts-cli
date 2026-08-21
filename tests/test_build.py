from __future__ import annotations

import json
from pathlib import Path

import pytest

from spareparts.cli import main
from spareparts.modules.build.adapters import invoke
from spareparts.modules.build.clients import CommandResult, Commands as SubprocessCommands, CoreClient, GitHub as GitHubClient
from spareparts.modules.build.models import BuildError, Plan, Policy, Target, classify_changed_paths, safe_changed_paths
from spareparts.modules.build.progress import HuddleMonitor, discover
from spareparts.modules.build.service import PreparedTarget, _changed_paths, branch_for, marker, run_build, validate_plan


def plan(targets=None):
    return {
        "ingestion": {"id": "ing-1", "source_repository": "sparepartslabs/distributor", "issue_number": 7, "issue_title": "Build it", "issue_body": "Change core", "ontology_revision_id": "rev-1", "status": "accepted"},
        "targets": targets if targets is not None else [{"repository_id": "R_core", "name_with_owner": "sparepartslabs/core", "base_branch": "main", "rationale": "Core change", "confidence": 0.9}],
    }


def policy(**changes):
    values = {"allowed_orgs": frozenset({"sparepartslabs"}), "allowed_repositories": frozenset(), "max_fanout": 3, "validation_commands": (("pytest", "-q"),), "timeout": 30}
    values.update(changes)
    return Policy(**values)


class Core:
    def __init__(self, document=None, status="queued"):
        self.document = document or plan()
        self.status = status
        self.created = []
        self.updated = []

    def latest(self, repository, issue): return self.document
    def create_attempt(self, body):
        self.created.append(body)
        return {"id": f"attempt-{len(self.created)}", "status": self.status, "pr_url": "https://pr/existing"}
    def update_attempt(self, attempt, body):
        self.updated.append((attempt, body))
        return {"id": attempt, **body}


class GitHub:
    def __init__(self, metadata=None):
        self.info = metadata or {"node_id": "R_core", "default_branch": "main", "fork": False, "archived": False, "disabled": False}
        self.clones = []
        self.published = []
        self.statuses = []
        self.git_env = {"PATH": "/bin", "GIT_TERMINAL_PROMPT": "0"}

    def metadata(self, repository):
        return self.info.get(repository, self.info) if isinstance(self.info.get(repository), dict) else self.info
    def clone(self, repository, path):
        self.clones.append((repository, path)); path.mkdir(parents=True)
    def publish_pr(self, repository, branch, base, title, body):
        self.published.append((repository, branch, base, title, body))
        return {"number": 9, "url": "https://github/pr/9"}
    def publish_status(self, repository, issue, marker, body):
        self.statuses.append((repository, issue, marker, body))


class Commands:
    def __init__(self, *, diff="src/x.py", untracked="", validation=0, huddle_status="complete", incomplete_tasks=False):
        self.calls = []
        self.diff = diff
        self.untracked = untracked
        self.validation = validation
        self.huddle_status = huddle_status
        self.incomplete_tasks = incomplete_tasks

    def _write_huddle(self, root):
        repositories = sorted(path for path in Path(root).iterdir() if path.is_dir() and path.name != ".sp")
        rows = []
        for repository in repositories:
            tasks = repository / "specs/001-build/tasks.md"
            tasks.parent.mkdir(parents=True, exist_ok=True)
            tasks.write_text("- [ ] T001 unfinished\n" if self.incomplete_tasks else "- [x] T001 complete\n", encoding="utf-8")
            rows.append(f"| {repository.name} | role | specs/001-build | implemented |")
        huddle = Path(root) / ".sp/huddles/001-build/huddle.md"
        huddle.parent.mkdir(parents=True, exist_ok=True)
        huddle.write_text("# Huddle: Build\n\n**Status**: " + self.huddle_status + "\n\n## Repo Breakdown\n\n| Repo | Role | Spec | Stage |\n|---|---|---|---|\n" + "\n".join(rows) + "\n", encoding="utf-8")

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[:3] == ["git", "diff", "--name-only"]: return CommandResult(0, self.diff, "")
        if argv[:3] == ["git", "ls-files", "--others"]: return CommandResult(0, self.untracked, "")
        if argv[:3] == ["git", "rev-parse", "origin/main^{commit}"]: return CommandResult(0, "base-sha", "")
        if argv[:3] == ["git", "rev-parse", "HEAD"]: return CommandResult(0, "commit-sha", "")
        if argv and argv[0] in ("codex", "claude"):
            self._write_huddle(kwargs["cwd"])
            return CommandResult(0, "agent summary", "")
        if argv and argv[0] == "pytest": return CommandResult(self.validation, "", "failed" if self.validation else "")
        return CommandResult(0, "", "")


def test_top_level_help_lists_build(capsys):
    assert main(["--help"]) == 0
    assert "build" in capsys.readouterr().out


def test_build_help_does_not_load_agent_binaries(capsys):
    with pytest.raises(SystemExit) as caught: main(["build", "--help"])
    assert caught.value.code == 0
    assert "issue" in capsys.readouterr().out


def test_plan_requires_eligible_status():
    value = plan(); value["ingestion"]["status"] = "retryable"
    with pytest.raises(BuildError, match="not build-eligible"): Plan.parse(value)


def test_policy_requires_allowlist_and_caps_fanout():
    parsed = Plan.parse(plan())
    with pytest.raises(BuildError, match="allowed-org"): policy(allowed_orgs=frozenset()).authorize(parsed)
    with pytest.raises(BuildError, match="maximum"): policy(max_fanout=0).authorize(parsed)


def test_repository_allowlist_is_additional():
    with pytest.raises(BuildError, match="repository is not allowed"):
        policy(allowed_repositories=frozenset({"sparepartslabs/other"})).authorize(Plan.parse(plan()))


def test_validate_plan_rejects_fork_and_identity_mismatch():
    parsed = Plan.parse(plan())
    with pytest.raises(BuildError, match="fork"): validate_plan(parsed, policy(), GitHub({"node_id": "R_core", "fork": True}))
    with pytest.raises(BuildError, match="identity mismatch"): validate_plan(parsed, policy(), GitHub({"node_id": "R_other", "default_branch": "main"}))


def test_validate_plan_resolves_missing_base_from_github():
    value = plan(); value["targets"][0]["base_branch"] = None
    resolved = validate_plan(Plan.parse(value), policy(), GitHub())
    assert resolved[0][1] == "main"


def test_changed_paths_reject_control_files():
    with pytest.raises(BuildError, match="forbidden"): safe_changed_paths(".github/workflows/pwn.yml")


def test_change_classifier_keeps_product_and_excludes_local_control():
    product, control = classify_changed_paths(["src/new.py", "specs/001/tasks.md", ".sp/feature.json"])
    assert product == ["src/new.py"]
    assert control == [".sp/feature.json", "specs/001/tasks.md"]


def test_agent_adapters_are_normalized(monkeypatch, tmp_path):
    commands = Commands()
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("SPAREPARTS_INGEST_KEY", "core-secret")
    assert invoke("codex", "gpt-x", "prompt", tmp_path, commands, 10) == "agent summary"
    argv, kwargs = commands.calls[-1]
    assert argv == ["codex", "exec", "--approve-for-me", "--skip-git-repo-check", "--model", "gpt-x", "-"]
    assert "secret" not in repr(argv) + repr(kwargs.get("input_text"))
    assert kwargs["env"]["OPENAI_API_KEY"] == "secret"
    assert "GITHUB_TOKEN" not in kwargs["env"] and "SPAREPARTS_INGEST_KEY" not in kwargs["env"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-two")
    assert invoke("claude", None, "prompt", tmp_path, commands, 10) == "agent summary"
    assert commands.calls[-1][0][:4] == ["claude", "--print", "--permission-mode", "acceptEdits"]
    assert "OPENAI_API_KEY" not in commands.calls[-1][1]["env"]



def test_claude_failure_uses_stdout_when_stderr_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")

    def commands(*args, **kwargs):
        return CommandResult(1, "useful Claude failure", "")

    with pytest.raises(BuildError, match="claude failed with exit 1: useful Claude failure"):
        invoke("claude", None, "prompt", tmp_path, commands, 10)


def test_github_status_flattens_paginated_comment_pages():
    calls = []
    marker = "<!-- managed -->"

    def commands(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["gh", "api"] and "--slurp" in argv:
            return CommandResult(0, json.dumps([[{"id": 1, "body": "old"}], [{"id": 2, "body": marker}]]), "")
        return CommandResult(0, "", "")

    GitHubClient(commands, "token").publish_status("owner/source", 7, marker, "updated")
    assert calls[0][-2:] == ["--paginate", "--slurp"]
    assert calls[1][:5] == ["gh", "api", "--method", "PATCH", "repos/owner/source/issues/comments/2"]


def test_github_status_rejects_non_page_slurp_output():
    def commands(argv, **kwargs):
        return CommandResult(0, json.dumps([{"id": 1, "body": "not wrapped"}]), "")

    with pytest.raises(BuildError, match="comments were invalid"):
        GitHubClient(commands, "token").publish_status("owner/source", 7, "marker", "body")


def test_core_contract_uses_api_key_and_exact_routes():
    requests = []
    def transport(request):
        requests.append(request)
        if request.method == "GET": return 200, plan()
        return 201, {"id": "attempt-1"}
    core = CoreClient("https://core", "key", transport)
    core.latest("sparepartslabs/distributor", 7)
    core.create_attempt({"x": 1})
    core.update_attempt("attempt-1", {"status": "running"})
    assert "/ingestion/v1/issues/latest?" in requests[0].full_url
    assert requests[1].full_url.endswith("/ingestion/v1/build-attempts")
    assert requests[2].full_url.endswith("/ingestion/v1/build-attempts/attempt-1")
    assert all(request.get_header("X-api-key") == "key" for request in requests)


def test_dry_run_is_bounded_and_does_not_clone(tmp_path):
    core, github = Core(), GitHub()
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=core, github=github, commands=Commands(), dry_run=True)
    assert result["status"] == "success"
    assert result["targets"][0]["dry_run"] is True
    assert github.clones == [] and github.statuses == [] and core.updated == []


def test_terminal_replay_does_not_clone_or_update(tmp_path):
    core, github = Core(status="pr_opened"), GitHub()
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=core, github=github, commands=Commands())
    assert result["targets"][0]["resumed"] is True
    assert github.clones == [] and core.updated == []
    assert "sp:build-status" in github.statuses[0][2]


def test_final_status_writeback_failure_preserves_successful_result(tmp_path):
    class FailingStatusGitHub(GitHub):
        def publish_status(self, repository, issue, marker, body):
            raise BuildError("GitHub issue comments were invalid", "retryable_failure")

    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=Core(status="pr_opened"), github=FailingStatusGitHub(), commands=Commands())
    assert result["status"] == "success"
    assert result["targets"][0]["status"] == "pr_opened"
    assert result["progress_warnings"] == ["Final issue status writeback failed."]


def test_no_change_writes_terminal_state(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    core = Core()
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=core, github=GitHub(), commands=Commands(diff=""))
    assert result["targets"][0]["status"] == "no_change"
    assert core.updated[-1][1]["status"] == "no_change"


def test_valid_change_commits_pushes_and_opens_marker_pr(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    core, github, commands = Core(), GitHub(), Commands()
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model="gpt-x", workspace=tmp_path, policy=policy(), core=core, github=github, commands=commands)
    target = result["targets"][0]
    assert target["status"] == "pr_opened" and target["pr_number"] == 9
    assert "<!-- sp:build " in github.published[0][4]
    assert "[sparepartslabs/distributor#7](https://github.com/sparepartslabs/distributor/issues/7)" in github.published[0][4]
    assert "`sparepartslabs/distributor#7`" not in github.published[0][4]
    assert ["git", "push", "--force-with-lease", "-u", "origin", branch_for(7, "attempt-1")] in [call[0] for call in commands.calls]
    assert core.updated[-1][1]["commit_sha"] == "commit-sha"
    assert any("https://github/pr/9" in status[3] for status in github.statuses)


def test_build_configures_default_repository_local_git_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    commands = Commands()
    run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=Core(), github=GitHub(), commands=commands)
    calls = [call[0] for call in commands.calls]
    assert ["git", "config", "--local", "user.name", "Spare Parts Assembler"] in calls
    assert ["git", "config", "--local", "user.email", "assembler@sparepartslabs.com"] in calls


def test_build_allows_git_identity_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("BUILD_GIT_USER_NAME", "Custom Builder")
    monkeypatch.setenv("BUILD_GIT_USER_EMAIL", "builder@example.com")
    commands = Commands()
    run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=Core(), github=GitHub(), commands=commands)
    calls = [call[0] for call in commands.calls]
    assert ["git", "config", "--local", "user.name", "Custom Builder"] in calls
    assert ["git", "config", "--local", "user.email", "builder@example.com"] in calls


def test_validation_failure_never_pushes(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    core, commands = Core(), Commands(validation=1)
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=core, github=GitHub(), commands=commands)
    assert result["targets"][0]["status"] == "rejected"
    assert not any(call[0][:2] == ["git", "push"] for call in commands.calls)


def test_two_targets_install_context_and_invoke_one_workspace_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    targets = [
        {"repository_id": "R_core", "name_with_owner": "sparepartslabs/core", "base_branch": "main", "rationale": "API", "confidence": 0.8},
        {"repository_id": "R_web", "name_with_owner": "sparepartslabs/spareparts", "base_branch": "main", "rationale": "UI", "confidence": 0.9},
    ]
    github = GitHub({
        "sparepartslabs/core": {"node_id": "R_core", "default_branch": "main"},
        "sparepartslabs/spareparts": {"node_id": "R_web", "default_branch": "main"},
    })
    commands = Commands()
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=Core(plan(targets)), github=github, commands=commands)
    calls = [call[0] for call in commands.calls]
    assert len([argv for argv in calls if argv and argv[0] == "codex"]) == 1
    assert len([argv for argv in calls if argv[:3] == ["sp", "ec", "install"]]) == 1
    install = next(call for call in commands.calls if call[0][:3] == ["sp", "ec", "install"])
    assert "GITHUB_TOKEN" not in install[1]["env"] and "SPAREPARTS_INGEST_KEY" not in install[1]["env"]
    assert [item["status"] for item in result["targets"]] == ["pr_opened", "pr_opened"]


def test_untracked_product_files_are_staged(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    commands = Commands(diff="styles/docs.css\0", untracked="content/docs/ontology.md\0")
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=Core(), github=GitHub(), commands=commands)
    assert result["targets"][0]["status"] == "pr_opened"
    assert ["git", "add", "-A", "--", "content/docs/ontology.md", "styles/docs.css"] in [call[0] for call in commands.calls]


def test_incomplete_huddle_tasks_reject_publication(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    commands = Commands(incomplete_tasks=True)
    result = run_build(source_repository="sparepartslabs/distributor", issue_number=7, trigger_id="delivery", agent="codex", model=None, workspace=tmp_path, policy=policy(), core=Core(), github=GitHub(), commands=commands)
    assert result["targets"][0]["status"] == "rejected"
    assert "incomplete tasks" in result["targets"][0]["summary"]
    assert not any(call[0][:2] == ["git", "push"] for call in commands.calls)


def test_huddle_progress_is_coalesced(tmp_path):
    bodies = []
    monitor = HuddleMonitor(tmp_path, bodies.append, "<!-- progress -->", "claude", None, interval=0.01)
    assert monitor.sync(initial=True) is None
    assert monitor.sync() is None
    Commands(huddle_status="active")._write_huddle(tmp_path)
    assert monitor.sync().status == "active"
    Commands(huddle_status="complete")._write_huddle(tmp_path)
    snapshot = monitor.sync()
    monitor.sync()
    assert snapshot is not None and snapshot.status == "complete"
    assert len(bodies) == 3
    assert discover(tmp_path) == snapshot


def test_real_git_inventory_includes_untracked_and_excludes_specs(tmp_path):
    commands = SubprocessCommands()
    assert commands(["git", "init", "-q"], cwd=tmp_path).code == 0
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    assert commands(["git", "add", "tracked.txt"], cwd=tmp_path).code == 0
    assert commands(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"], cwd=tmp_path).code == 0
    base = commands(["git", "rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    tracked.write_text("after\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")
    tasks = tmp_path / "specs/001/tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [x] done\n", encoding="utf-8")
    item = PreparedTarget(Target("R", "sparepartslabs/core", "main", "", 1.0), "main", "attempt", "branch", tmp_path)
    product, control = _changed_paths(item, base, commands, {})
    assert product == ["new.txt", "tracked.txt"]
    assert control == ["specs/001/tasks.md"]


def test_marker_and_branch_are_deterministic():
    assert branch_for(7, "ABC-def") == branch_for(7, "ABC-def")
    assert marker("o/s", 7, "ing", "o/t", "a") == marker("o/s", 7, "ing", "o/t", "a")
