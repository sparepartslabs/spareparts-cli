"""Issue ingestion orchestration with deterministic, testable boundaries."""

from __future__ import annotations

import json
import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .models import IngestionError, IssueEvent, validate_ontology, validate_routes

ROUTING_SCHEMA = {
    "type": "object",
    "properties": {
        "affected_repositories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repository_id": {"type": "string"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["repository_id", "rationale", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["affected_repositories"],
    "additionalProperties": False,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def top_approver(reviews: list[dict[str, Any]], observed_at: str | None = None) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for review in reviews:
        review_id = str(review.get("id", ""))
        user = review.get("user")
        if not review_id or review_id in seen or review.get("state") != "APPROVED" or not isinstance(user, dict):
            continue
        seen.add(review_id)
        login = user.get("login")
        user_type = str(user.get("type", "")).lower()
        if not isinstance(login, str) or not login or user_type == "bot" or login.lower().endswith("[bot]"):
            continue
        counts[login] += 1
    timestamp = observed_at or now()
    if not counts:
        return {"unavailable_reason": "no_approved_reviews"}
    login, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return {"login": login, "approval_count": count, "observed_at": timestamp}


def routing_prompt(event: IssueEvent, repositories: list[dict[str, Any]]) -> str:
    catalog = [{"id": repo["id"], "full_name": repo["full_name"], "description": repo.get("description", ""), "topics": repo.get("topics", []), "language": repo.get("language")} for repo in repositories]
    return (
        "Identify only organization repositories materially affected by this GitHub issue. "
        "An empty list is correct when evidence is insufficient. Repository IDs must come exactly from the catalog.\n"
        + json.dumps({"issue": {"title": event.title, "body": event.body, "repository": event.repository}, "repository_catalog": catalog}, separators=(",", ":"))
    )


def ingest_issue(event: IssueEvent, provider: Any, github: Any, core: Any, *, refresh: bool = False, max_repositories: int = 100) -> dict[str, Any]:
    ontology = None if refresh else core.current_ontology(event.organization, max_repositories)
    if ontology is None:
        observed_at = now()
        repositories = github.repositories(event.organization, max_repositories)
        identity = json.dumps(repositories, sort_keys=True, separators=(",", ":"))
        revision_id = "github:" + event.organization + ":" + hashlib.sha256(identity.encode()).hexdigest()
        ontology = core.create_ontology({
            "revision_id": revision_id, "organization_id": event.organization_id,
            "organization_login": event.organization, "source": "github-rest", "observed_at": observed_at,
            "repositories": [{**repository, "observed_at": observed_at} for repository in repositories],
        })
    ontology = validate_ontology(ontology)
    try:
        raw = json.loads(provider.complete(routing_prompt(event, ontology["repositories"]), ROUTING_SCHEMA))
    except json.JSONDecodeError as err:
        raise IngestionError("provider returned invalid JSON") from err
    routes = validate_routes(raw, ontology["repositories"])
    partial = False
    for route in routes:
        try:
            route["reviewer"] = top_approver(github.approved_reviews(route["full_name"]))
        except IngestionError as err:
            partial = True
            route["reviewer"] = {"unavailable_reason": "github_evidence_unavailable"}
    vendor, _, model = provider.label.partition(":")
    status = "partial" if partial else "accepted"
    response = core.submit({
        "source_event_id": event.source_id, "event_name": "issues", "action": event.action,
        "organization_login": event.organization, "source_repository": event.repository,
        "issue_number": event.issue_number, "issue_node_id": event.issue_node_id,
        "issue_title": event.title, "issue_body": event.body, "actor_login": event.actor, "provider": vendor,
        "model": model, "ontology_revision_id": ontology["revision_id"], "status": status,
        "affected_repositories": routes,
    })
    return {
        "status": status, "source_id": event.source_id, "provider": provider.label,
        "ontology_revision_id": ontology["revision_id"], "affected_repository_count": len(routes),
        "ingestion_id": response.get("id"),
    }
