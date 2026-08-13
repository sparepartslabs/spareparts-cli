"""Validated, credential-free values used by issue ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


class IngestionError(RuntimeError):
    """A safe, actionable ingestion error."""


@dataclass(frozen=True)
class IssueEvent:
    source_id: str
    action: str
    organization: str
    organization_id: str
    repository_id: str
    repository: str
    issue_number: int
    issue_node_id: str
    title: str
    body: str
    url: str
    actor: str

    @classmethod
    def from_payload(cls, payload: Any, delivery_id: str | None = None) -> "IssueEvent":
        if not isinstance(payload, dict):
            raise IngestionError("event file must contain a JSON object")
        action = _text(payload.get("action"), "action")
        repository = _mapping(payload.get("repository"), "repository")
        issue = _mapping(payload.get("issue"), "issue")
        sender = _mapping(payload.get("sender"), "sender")
        owner = _mapping(repository.get("owner"), "repository.owner")
        full_name = _text(repository.get("full_name"), "repository.full_name")
        number = issue.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise IngestionError("issue.number must be a positive integer")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        source_id = delivery_id or "sha256:" + hashlib.sha256(
            ("issues\n" + action + "\n" + full_name + "\n" + str(number) + "\n" + canonical).encode()
        ).hexdigest()
        return cls(
            source_id=source_id,
            action=action,
            organization=_text(owner.get("login"), "repository.owner.login"),
            organization_id=_text(owner.get("node_id") or owner.get("id"), "repository.owner.node_id"),
            repository_id=_text(repository.get("node_id") or repository.get("id"), "repository.node_id"),
            repository=full_name,
            issue_number=number,
            issue_node_id=_text(issue.get("node_id") or issue.get("id"), "issue.node_id"),
            title=_text(issue.get("title"), "issue.title", allow_empty=True),
            body=issue.get("body") if isinstance(issue.get("body"), str) else "",
            url=_text(issue.get("html_url"), "issue.html_url", allow_empty=True),
            actor=_text(sender.get("login"), "sender.login"),
        )

    def source(self) -> dict[str, Any]:
        return {
            "event": "issues",
            "action": self.action,
            "organization": self.organization,
            "repository_id": self.repository_id,
            "repository": self.repository,
            "issue_number": self.issue_number,
            "issue_node_id": self.issue_node_id,
            "issue_url": self.url,
            "actor": self.actor,
        }


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IngestionError(f"{name} must be an object")
    return value


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise IngestionError(f"{name} must be text")
    result = str(value).strip()
    if not result and not allow_empty:
        raise IngestionError(f"{name} must not be empty")
    return result


def validate_ontology(value: Any) -> dict[str, Any]:
    ontology = _mapping(value, "ontology")
    revision_id = ontology.get("revision_id") or ontology.get("id")
    repositories = ontology.get("repositories")
    if not isinstance(revision_id, str) or not revision_id or ontology.get("complete") is not True:
        raise IngestionError("Core returned no usable complete ontology revision")
    if not isinstance(repositories, list):
        raise IngestionError("ontology.repositories must be an array")
    seen: set[str] = set()
    for repository in repositories:
        repo = _mapping(repository, "ontology repository")
        repo_id = repo.get("id") or repo.get("repository_id")
        if not isinstance(repo_id, str) or not repo_id or repo_id in seen:
            raise IngestionError("ontology repository IDs must be unique non-empty strings")
        seen.add(repo_id)
        full_name = repo.get("full_name") or repo.get("name_with_owner")
        _text(full_name, "ontology repository full_name")
        repo["id"] = repo_id
        repo["full_name"] = full_name
        if not isinstance(repo.get("components", []), list): raise IngestionError("ontology repository components must be an array")
    ontology["revision_id"] = revision_id
    return ontology


def validate_routes(value: Any, repositories: list[dict[str, Any]], match_kind: str = "model") -> list[dict[str, Any]]:
    result = _mapping(value, "provider result")
    routes = result.get("affected_repositories")
    if not isinstance(routes, list):
        raise IngestionError("provider result must contain affected_repositories array")
    catalog = {repo["id"]: repo for repo in repositories}
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in routes:
        route = _mapping(item, "affected repository")
        repo_id = route.get("repository_id")
        if repo_id not in catalog:
            raise IngestionError(f"provider selected repository outside ontology: {repo_id!r}")
        if repo_id in seen:
            raise IngestionError(f"provider selected repository more than once: {repo_id!r}")
        confidence = route.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise IngestionError("repository confidence must be between 0 and 1")
        rationale = _text(route.get("rationale"), "repository rationale")
        seen.add(repo_id)
        validated.append({
            "repository_id": repo_id,
            "full_name": catalog[repo_id]["full_name"],
            "rationale": rationale,
            "confidence": float(confidence),
            "match_kind": match_kind,
        })
    return validated
