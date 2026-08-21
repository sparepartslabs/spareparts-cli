"""Fail-closed, huddle-driven multi-repository build orchestration."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters import clean_environment, invoke
from .clients import Commands, CoreClient, GitHub
from .models import BuildError, Plan, Policy, Target, classify_changed_paths
from .progress import HuddleMonitor, HuddleRow, HuddleSnapshot, discover

TERMINAL = {"pr_opened", "no_change", "rejected", "retryable_failure", "permanent_failure"}
DEFAULT_GIT_USER_NAME = "Spare Parts Assembler"
DEFAULT_GIT_USER_EMAIL = "assembler@sparepartslabs.com"


@dataclass(frozen=True)
class PreparedTarget:
    target: Target
    base: str
    attempt_id: str
    branch: str
    checkout: Path


def branch_for(issue: int, attempt_id: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9]", "", attempt_id)[:12].lower()
    if not token:
        raise BuildError("Core attempt ID cannot form a branch")
    return f"sp/build-{issue}-{token}"


def marker(source: str, issue: int, ingestion_id: str, target: str, attempt_id: str) -> str:
    return f"<!-- sp:build source={source} issue={issue} ingestion={ingestion_id} target={target} attempt={attempt_id} -->"


def progress_marker(source: str, issue: int, trigger_id: str) -> str:
    return f"<!-- sp:huddle-progress source={source} issue={issue} trigger={trigger_id} -->"


def workspace_prompt(plan: Plan, prepared: list[PreparedTarget], agent: str) -> str:
    issue = plan.ingestion
    payload = {
        "issue": {
            "repository": issue["source_repository"],
            "number": issue["issue_number"],
            "title": issue["issue_title"],
            "body": issue.get("issue_body") or "",
        },
        "targets": [
            {
                "repository": item.target.name_with_owner,
                "checkout": item.checkout.name,
                "rationale": item.target.rationale,
                "branch": item.branch,
            }
            for item in prepared
        ],
    }
    huddle_command = "/huddle" if agent == "claude" else "$huddle"
    return (
        f"Use the installed workspace huddle command `{huddle_command}` to implement this request across every listed checkout. "
        "Create or resume exactly one workspace huddle, settle cross-repository contracts, and drive each repository through specify, plan, tasks, and implement. "
        "Update the huddle after every lifecycle transition and set its canonical Status to complete only after all target tasks and useful validations are complete. "
        "Keep specs, huddles, .sp state, and generated agent commands local. Do not commit, push, create pull requests, expose credentials, or modify .git/.github.\n"
        + json.dumps(payload, separators=(",", ":"))
    )


def validate_plan(plan: Plan, policy: Policy, github: GitHub) -> list[tuple[Target, str]]:
    policy.authorize(plan)
    resolved = []
    for target in plan.targets:
        metadata = github.metadata(target.name_with_owner)
        if metadata.get("fork") is True:
            raise BuildError(f"fork target rejected: {target.name_with_owner}")
        if metadata.get("archived") or metadata.get("disabled"):
            raise BuildError(f"inactive target rejected: {target.name_with_owner}")
        if str(metadata.get("node_id")) != target.repository_id:
            raise BuildError(f"target identity mismatch: {target.name_with_owner}")
        base = target.base_branch or metadata.get("default_branch")
        if not isinstance(base, str) or not base:
            raise BuildError(f"target has no usable base branch: {target.name_with_owner}")
        resolved.append((target, base))
    return resolved


def _git(commands: Commands, argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    result = commands(["git", *argv], cwd=cwd, timeout=300, env=env)
    if result.code:
        raise BuildError(f"git {argv[0]} failed: {result.stderr.strip()[:1000]}", "retryable_failure")
    return result.stdout.strip()


def _configure_git_identity(commands: Commands, checkout: Path, env: dict[str, str]) -> None:
    name = os.environ.get("BUILD_GIT_USER_NAME", "").strip() or DEFAULT_GIT_USER_NAME
    email = os.environ.get("BUILD_GIT_USER_EMAIL", "").strip() or DEFAULT_GIT_USER_EMAIL
    _git(commands, ["config", "--local", "user.name", name], checkout, env)
    _git(commands, ["config", "--local", "user.email", email], checkout, env)


def _install_engineering_context(agent: str, workspace: Path, commands: Commands) -> None:
    result = commands(["sp", "ec", "install", "--agent", agent, "--dir", str(workspace)], cwd=workspace, timeout=300, env=clean_environment())
    if result.code:
        raise BuildError(f"sp ec install failed: {result.stderr.strip()[:1000]}", "retryable_failure")


def _nul_values(text: str) -> list[str]:
    return [value for value in text.split("\0") if value]


def _changed_paths(item: PreparedTarget, base_sha: str, commands: Commands, env: dict[str, str]) -> tuple[list[str], list[str]]:
    tracked = _git(commands, ["diff", "--name-only", "-z", "--no-renames", base_sha], item.checkout, env)
    untracked = _git(commands, ["ls-files", "--others", "--exclude-standard", "-z"], item.checkout, env)
    product, control = classify_changed_paths(_nul_values(tracked) + _nul_values(untracked))
    for value in product:
        candidate = item.checkout / PurePosixPath(value)
        if candidate.is_symlink():
            raise BuildError(f"agent changed unsafe symlink: {value}", "rejected")
    if product:
        staged = _git(commands, ["ls-files", "--stage", "--", *product], item.checkout, env)
        if any(line.startswith("160000 ") for line in staged.splitlines()):
            raise BuildError("agent changed a submodule path", "rejected")
    return product, control


def _matching_row(snapshot: HuddleSnapshot, item: PreparedTarget) -> HuddleRow:
    names = {item.target.name_with_owner.lower(), item.target.name_with_owner.rsplit("/", 1)[-1].lower(), item.checkout.name.lower()}
    matches = [row for row in snapshot.rows if row.repository.strip().strip("`").lower() in names]
    if len(matches) != 1:
        raise BuildError(f"complete huddle must contain one row for {item.target.name_with_owner}", "rejected")
    return matches[0]


def _require_complete(snapshot: HuddleSnapshot | None, prepared: list[PreparedTarget]) -> None:
    if snapshot is None or snapshot.status != "complete":
        raise BuildError("agent did not produce a complete workspace huddle", "rejected")
    for item in prepared:
        row = _matching_row(snapshot, item)
        if not any(token in row.stage.lower() for token in ("implemented", "complete")):
            raise BuildError(f"huddle target is not implemented: {item.target.name_with_owner}", "rejected")
        spec_text = row.spec.strip().strip("`").split()[0]
        spec = PurePosixPath(spec_text)
        if spec.is_absolute() or ".." in spec.parts or spec.parts[:1] != ("specs",):
            raise BuildError(f"huddle target has invalid spec path: {item.target.name_with_owner}", "rejected")
        tasks = item.checkout / spec / "tasks.md"
        if not tasks.is_file():
            raise BuildError(f"huddle target tasks are missing: {item.target.name_with_owner}", "rejected")
        if re.search(r"^- \[ \] ", tasks.read_text(encoding="utf-8", errors="replace"), re.MULTILINE):
            raise BuildError(f"huddle target has incomplete tasks: {item.target.name_with_owner}", "rejected")


def _failure(core: CoreClient, prepared: list[PreparedTarget], error: BuildError) -> list[dict[str, Any]]:
    state = error.category if error.category in TERMINAL else "permanent_failure"
    results = []
    for item in prepared:
        core.update_attempt(item.attempt_id, {"status": state, "head_branch": item.branch, "summary": str(error)[:1000], "failure_category": error.category})
        results.append({"attempt_id": item.attempt_id, "repository": item.target.name_with_owner, "status": state, "failure_category": error.category, "summary": str(error)[:1000]})
    return results


def run_build(*, source_repository: str, issue_number: int, trigger_id: str, agent: str, model: str | None, workspace: Path, policy: Policy, core: CoreClient, github: GitHub, commands: Commands, dry_run: bool = False) -> dict[str, Any]:
    plan = Plan.parse(core.latest(source_repository, issue_number))
    if plan.ingestion["source_repository"].lower() != source_repository.lower() or plan.ingestion["issue_number"] != issue_number:
        raise BuildError("Core ingestion does not match requested issue")
    targets = validate_plan(plan, policy, github)
    results: list[dict[str, Any]] = []
    prepared: list[PreparedTarget] = []

    for target, base in targets:
        attempt = core.create_attempt({"trigger_delivery_id": trigger_id, "ingestion_id": plan.ingestion["id"], "target_repository_id": target.repository_id, "target_repository": target.name_with_owner, "base_branch": base, "agent": agent, "model": model})
        attempt_id, state = attempt["id"], attempt.get("status")
        if state in TERMINAL:
            results.append({"attempt_id": attempt_id, "repository": target.name_with_owner, "status": state, "resumed": True, "pr_url": attempt.get("pr_url")})
            continue
        branch = branch_for(issue_number, attempt_id)
        if dry_run:
            results.append({"attempt_id": attempt_id, "repository": target.name_with_owner, "status": "queued", "head_branch": branch, "dry_run": True})
            continue
        checkout = workspace / re.sub(r"[^A-Za-z0-9_.-]", "-", target.name_with_owner)
        prepared.append(PreparedTarget(target, base, attempt_id, branch, checkout))

    if dry_run:
        return {"status": "success", "ingestion_id": plan.ingestion["id"], "targets": results}

    huddle_marker = progress_marker(source_repository, issue_number, trigger_id)
    monitor = HuddleMonitor(workspace, lambda body: github.publish_status(source_repository, issue_number, huddle_marker, body), huddle_marker, agent, model)
    agent_summary = ""
    try:
        for item in prepared:
            core.update_attempt(item.attempt_id, {"status": "running", "head_branch": item.branch})
            if item.checkout.exists():
                raise BuildError(f"target workspace already exists: {item.checkout}")
            item.checkout.parent.mkdir(parents=True, exist_ok=True)
            github.clone(item.target.name_with_owner, item.checkout)
            _git(commands, ["fetch", "origin", item.base], item.checkout, github.git_env)
            base_sha = _git(commands, ["rev-parse", f"origin/{item.base}^{{commit}}"], item.checkout, github.git_env)
            _git(commands, ["checkout", "-B", item.branch, base_sha], item.checkout, github.git_env)
            _configure_git_identity(commands, item.checkout, github.git_env)
        if prepared:
            _install_engineering_context(agent, workspace, commands)
            monitor.start()
            try:
                agent_summary = invoke(agent, model, workspace_prompt(plan, prepared, agent), workspace, commands, policy.timeout)
            finally:
                monitor.stop()
            snapshot = discover(workspace)
            _require_complete(snapshot, prepared)
    except BuildError as error:
        if monitor._thread and monitor._thread.is_alive():
            monitor.stop()
        results.extend(_failure(core, prepared, error))
        prepared = []

    for item in prepared:
        try:
            base_sha = _git(commands, ["rev-parse", f"origin/{item.base}^{{commit}}"], item.checkout, github.git_env)
            paths, _control = _changed_paths(item, base_sha, commands, github.git_env)
            if not paths:
                core.update_attempt(item.attempt_id, {"status": "no_change", "head_branch": item.branch, "summary": "Huddle completed with no publishable repository changes."})
                results.append({"attempt_id": item.attempt_id, "repository": item.target.name_with_owner, "status": "no_change"})
                continue
            validations = []
            for argv in policy.validation_commands:
                validation = commands(list(argv), cwd=item.checkout, timeout=policy.timeout, env=clean_environment())
                validations.append({"command": list(argv), "exit_code": validation.code})
                if validation.code:
                    raise BuildError(f"validation failed: {argv[0]}", "rejected")
            _git(commands, ["add", "-A", "--", *paths], item.checkout, github.git_env)
            _git(commands, ["commit", "-m", f"feat: address {source_repository}#{issue_number}"], item.checkout, github.git_env)
            commit = _git(commands, ["rev-parse", "HEAD"], item.checkout, github.git_env)
            _git(commands, ["push", "--force-with-lease", "-u", "origin", item.branch], item.checkout, github.git_env)
            tag = marker(source_repository, issue_number, plan.ingestion["id"], item.target.name_with_owner, item.attempt_id)
            source_issue_url = f"https://github.com/{source_repository}/issues/{issue_number}"
            body = f"{tag}\n\nSource issue: [{source_repository}#{issue_number}]({source_issue_url}).\n\nAgent: `{agent}`" + (f" / `{model}`" if model else "") + f"\n\nChanged paths: {', '.join(paths)}\n\nValidations: {json.dumps(validations, separators=(',', ':'))}"
            pr = github.publish_pr(item.target.name_with_owner, item.branch, item.base, f"Address {source_repository}#{issue_number}", body)
            summary = (agent_summary or f"Changed {len(paths)} path(s).")[:1000]
            core.update_attempt(item.attempt_id, {"status": "pr_opened", "head_branch": item.branch, "commit_sha": commit, "pr_number": pr["number"], "pr_url": pr["url"], "summary": summary})
            results.append({"attempt_id": item.attempt_id, "repository": item.target.name_with_owner, "status": "pr_opened", "commit_sha": commit, "pr_number": pr["number"], "pr_url": pr["url"]})
        except BuildError as error:
            results.extend(_failure(core, [item], error))

    status = "success" if all(item["status"] in ("pr_opened", "no_change", "queued") for item in results) else "partial_failure"
    status_marker = f"<!-- sp:build-status source={source_repository} issue={issue_number} trigger={trigger_id} -->"
    lines = [status_marker, "", f"Build status: **{status}**", "", f"Agent: `{agent}`" + (f" / `{model}`" if model else ""), ""]
    for item in results:
        detail = f"- `{item['repository']}`: **{item['status']}**"
        if item.get("pr_url"):
            detail += f" ([PR]({item['pr_url']}))"
        if item.get("summary") and item["status"] not in ("pr_opened", "no_change"):
            detail += f" — {item['summary'][:300]}"
        lines.append(detail)
    if monitor.warnings:
        lines += ["", "Progress warnings: " + "; ".join(monitor.warnings)[:500]]
    try:
        github.publish_status(source_repository, issue_number, status_marker, "\n".join(lines))
    except BuildError:
        monitor.warnings.append("Final issue status writeback failed.")
    return {"status": status, "ingestion_id": plan.ingestion["id"], "targets": results, "progress_warnings": monitor.warnings}
