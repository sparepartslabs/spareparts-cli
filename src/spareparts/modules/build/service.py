"""Fail-closed build planning and idempotent per-target publication."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .adapters import clean_environment, invoke
from .clients import Commands, CoreClient, GitHub
from .models import BuildError, Plan, Policy, Target, safe_changed_paths

TERMINAL = {"pr_opened", "no_change", "rejected", "retryable_failure", "permanent_failure"}
DEFAULT_GIT_USER_NAME = "Spare Parts Assembler"
DEFAULT_GIT_USER_EMAIL = "assembler@sparepartslabs.com"


def branch_for(issue: int, attempt_id: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9]", "", attempt_id)[:12].lower()
    if not token: raise BuildError("Core attempt ID cannot form a branch")
    return f"sp/build-{issue}-{token}"


def marker(source: str, issue: int, ingestion_id: str, target: str, attempt_id: str) -> str:
    return f"<!-- sp:build source={source} issue={issue} ingestion={ingestion_id} target={target} attempt={attempt_id} -->"


def prompt(plan: Plan, target: Target, branch: str) -> str:
    issue = plan.ingestion
    return (
        "Implement the requested issue in this checkout only. Follow repository instructions, keep scope minimal, and run useful tests. "
        "Do not commit, push, create pull requests, expose credentials, or modify .git/.github.\n"
        + json.dumps({"issue": {"repository": issue["source_repository"], "number": issue["issue_number"], "title": issue["issue_title"], "body": issue.get("issue_body") or ""}, "target": {"repository": target.name_with_owner, "rationale": target.rationale, "branch": branch}}, separators=(",", ":"))
    )


def validate_plan(plan: Plan, policy: Policy, github: GitHub) -> list[tuple[Target, str]]:
    policy.authorize(plan)
    resolved = []
    for target in plan.targets:
        metadata = github.metadata(target.name_with_owner)
        if metadata.get("fork") is True: raise BuildError(f"fork target rejected: {target.name_with_owner}")
        if metadata.get("archived") or metadata.get("disabled"): raise BuildError(f"inactive target rejected: {target.name_with_owner}")
        if str(metadata.get("node_id")) != target.repository_id: raise BuildError(f"target identity mismatch: {target.name_with_owner}")
        base = target.base_branch or metadata.get("default_branch")
        if not isinstance(base, str) or not base: raise BuildError(f"target has no usable base branch: {target.name_with_owner}")
        resolved.append((target, base))
    return resolved


def _git(commands: Commands, argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    result = commands(["git", *argv], cwd=cwd, timeout=300, env=env)
    if result.code: raise BuildError(f"git {argv[0]} failed: {result.stderr.strip()[:1000]}", "retryable_failure")
    return result.stdout.strip()


def _configure_git_identity(commands: Commands, checkout: Path, env: dict[str, str]) -> None:
    name = os.environ.get("BUILD_GIT_USER_NAME", "").strip() or DEFAULT_GIT_USER_NAME
    email = os.environ.get("BUILD_GIT_USER_EMAIL", "").strip() or DEFAULT_GIT_USER_EMAIL
    _git(commands, ["config", "--local", "user.name", name], checkout, env)
    _git(commands, ["config", "--local", "user.email", email], checkout, env)


def run_build(*, source_repository: str, issue_number: int, trigger_id: str, agent: str, model: str | None, workspace: Path, policy: Policy, core: CoreClient, github: GitHub, commands: Commands, dry_run: bool = False) -> dict[str, Any]:
    plan = Plan.parse(core.latest(source_repository, issue_number))
    if plan.ingestion["source_repository"].lower() != source_repository.lower() or plan.ingestion["issue_number"] != issue_number:
        raise BuildError("Core ingestion does not match requested issue")
    targets = validate_plan(plan, policy, github)
    results = []
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
        core.update_attempt(attempt_id, {"status": "running", "head_branch": branch})
        checkout = workspace / re.sub(r"[^A-Za-z0-9_.-]", "-", target.name_with_owner) / attempt_id
        try:
            if checkout.exists(): raise BuildError(f"target workspace already exists: {checkout}")
            checkout.parent.mkdir(parents=True, exist_ok=True)
            github.clone(target.name_with_owner, checkout)
            _git(commands, ["fetch", "origin", base], checkout, github.git_env)
            base_sha = _git(commands, ["rev-parse", f"origin/{base}^{{commit}}"], checkout, github.git_env)
            _git(commands, ["checkout", "-B", branch, base_sha], checkout, github.git_env)
            _configure_git_identity(commands, checkout, github.git_env)
            agent_summary = invoke(agent, model, prompt(plan, target, branch), checkout, commands, policy.timeout)
            paths = safe_changed_paths(_git(commands, ["diff", "--name-only", base_sha], checkout, github.git_env))
            if not paths:
                core.update_attempt(attempt_id, {"status": "no_change", "head_branch": branch, "summary": "Agent produced no repository changes."})
                results.append({"attempt_id": attempt_id, "repository": target.name_with_owner, "status": "no_change"})
                continue
            validations = []
            for argv in policy.validation_commands:
                result = commands(list(argv), cwd=checkout, timeout=policy.timeout, env=clean_environment())
                validations.append({"command": list(argv), "exit_code": result.code})
                if result.code: raise BuildError(f"validation failed: {argv[0]}", "rejected")
            _git(commands, ["add", "--", *paths], checkout, github.git_env)
            _git(commands, ["commit", "-m", f"feat: address {source_repository}#{issue_number}"], checkout, github.git_env)
            commit = _git(commands, ["rev-parse", "HEAD"], checkout, github.git_env)
            _git(commands, ["push", "--force-with-lease", "-u", "origin", branch], checkout, github.git_env)
            tag = marker(source_repository, issue_number, plan.ingestion["id"], target.name_with_owner, attempt_id)
            body = f"{tag}\n\nAddresses `{source_repository}#{issue_number}`.\n\nAgent: `{agent}`" + (f" / `{model}`" if model else "") + f"\n\nChanged paths: {', '.join(paths)}\n\nValidations: {json.dumps(validations, separators=(',', ':'))}"
            pr = github.publish_pr(target.name_with_owner, branch, base, f"Address {source_repository}#{issue_number}", body)
            safe_summary = (agent_summary or f"Changed {len(paths)} path(s).")[:1000]
            core.update_attempt(attempt_id, {"status": "pr_opened", "head_branch": branch, "commit_sha": commit, "pr_number": pr["number"], "pr_url": pr["url"], "summary": safe_summary})
            results.append({"attempt_id": attempt_id, "repository": target.name_with_owner, "status": "pr_opened", "commit_sha": commit, "pr_number": pr["number"], "pr_url": pr["url"]})
        except BuildError as error:
            state = error.category if error.category in TERMINAL else "permanent_failure"
            core.update_attempt(attempt_id, {"status": state, "head_branch": branch, "summary": str(error)[:1000], "failure_category": error.category})
            results.append({"attempt_id": attempt_id, "repository": target.name_with_owner, "status": state, "failure_category": error.category})
    status = "success" if all(item["status"] in ("pr_opened", "no_change", "queued") for item in results) else "partial_failure"
    if not dry_run:
        status_marker = f"<!-- sp:build-status source={source_repository} issue={issue_number} trigger={trigger_id} -->"
        lines = [status_marker, "", f"Build status: **{status}**", "", f"Agent: `{agent}`" + (f" / `{model}`" if model else ""), ""]
        for item in results:
            detail = f"- `{item['repository']}`: **{item['status']}**"
            if item.get("pr_url"): detail += f" ([PR]({item['pr_url']}))"
            lines.append(detail)
        github.publish_status(source_repository, issue_number, status_marker, "\n".join(lines))
    return {"status": status, "ingestion_id": plan.ingestion["id"], "targets": results}
