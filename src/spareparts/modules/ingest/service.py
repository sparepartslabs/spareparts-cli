"""Issue ingestion orchestration with deterministic, testable boundaries."""

from __future__ import annotations

import hashlib
import json
import time
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
                    "confidence": {"type": "number"},
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

def explicit_routes(text: str, repositories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    folded=text.casefold(); by_name: dict[str,list[dict[str,Any]]]={}
    for repo in repositories: by_name.setdefault(str(repo["full_name"]).rsplit("/",1)[-1].casefold(), []).append(repo)
    found={}
    for repo in repositories:
        full=str(repo["full_name"]); short=full.rsplit("/",1)[-1]
        evidence=None
        if full.casefold() in folded: evidence=("explicit_repository",None)
        elif len(by_name[short.casefold()]) == 1 and re_search_token(folded, short.casefold()): evidence=("explicit_repository",None)
        for component in repo.get("components", []):
            path=str(component.get("path", "")).strip("/")
            prefixes=(f"{short}/{path}", f"{full}/{path}")
            if path and any(value.casefold() in folded for value in prefixes): evidence=("explicit_component",path)
        if evidence: found[repo["id"]]={"repository_id":repo["id"],"full_name":full,"rationale":f"Explicit reference to {full}"+(f" component {evidence[1]}" if evidence[1] else ""),"confidence":1.0,"match_kind":evidence[0],**({"matched_path":evidence[1]} if evidence[1] else {})}
    return list(found.values())

def re_search_token(text: str, token: str) -> bool:
    import re
    return re.search(r"(?<![a-z0-9_.-])"+re.escape(token)+r"(?![a-z0-9_.-])", text) is not None


def writeback_marker(source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode()).hexdigest()
    return f"<!-- spareparts:ingestion:{digest} -->"


def _summary_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value).split()).replace("@", "@\u200b")
    return text[:limit] + ("…" if len(text) > limit else "")


def render_summary(status: str, provider: str, ingestion_id: str | None, routes: list[dict[str, Any]], source_id: str) -> str:
    lines = [
        "## Spare Parts ingestion summary",
        "",
        f"**Status:** {status}  ",
        f"**Provider:** `{provider}`  ",
        f"**Ingestion ID:** `{ingestion_id or 'unavailable'}`",
        "",
    ]
    if not routes:
        lines.append("No affected repositories were identified with sufficient evidence.")
    else:
        lines.extend([f"### Affected repositories ({len(routes)})", ""])
        for route in routes:
            reviewer = route["reviewer"]
            if reviewer.get("login"):
                evidence = f"top approver `{reviewer['login']}` ({reviewer['approval_count']} approved reviews)"
            else:
                evidence = f"reviewer evidence unavailable (`{reviewer['unavailable_reason']}`)"
            lines.extend([
                f"- **`{route['full_name']}`** — {route['confidence']:.0%} confidence",
                f"  - {_summary_text(route['rationale'])}",
                f"  - {evidence}",
            ])
    lines.extend(["", writeback_marker(source_id)])
    return "\n".join(lines)


def ingest_issue(event: IssueEvent, provider: Any, github: Any, core: Any, *, refresh: bool = False, max_repositories: int = 100, writeback: bool = False, max_components: int = 100, max_component_requests: int = 4, max_component_bytes: int = 65536) -> dict[str, Any]:
    ontology = None if refresh else core.current_ontology(event.organization, max_repositories)
    if ontology is None:
        observed_at = now()
        catalog_started = time.perf_counter()
        request_count_before = int(getattr(github, "request_count", 0))
        repositories = github.repositories(event.organization, max_repositories)
        for repository in repositories:
            try: repository["components"] = github.components(repository["name_with_owner"], repository.get("metadata",{}).get("default_branch"), max_components=max_components, max_requests=max_component_requests, max_bytes=max_component_bytes)
            except (IngestionError, AttributeError): repository["components"] = []
        identity = json.dumps(repositories, sort_keys=True, separators=(",", ":"))
        revision_id = "github:" + event.organization + ":" + hashlib.sha256(identity.encode()).hexdigest()
        catalog_duration_ms = round((time.perf_counter() - catalog_started) * 1000)
        ontology = core.create_ontology({
            "revision_id": revision_id, "organization_id": event.organization_id,
            "organization_login": event.organization, "source": "github-rest", "observed_at": observed_at,
            "repositories": [{**repository, "observed_at": observed_at} for repository in repositories],
            "refresh": {"started_at": observed_at, "component_count": sum(len(repository.get("components", [])) for repository in repositories), "github_request_count": max(0, int(getattr(github, "request_count", 0)) - request_count_before), "catalog_duration_ms": catalog_duration_ms},
        })
    ontology = validate_ontology(ontology)
    issue_text=f"{event.title}\n{event.body}"
    explicit=explicit_routes(issue_text, ontology["repositories"])
    candidates=ontology["repositories"]
    try:
        search=core.search_ontology({"organization_login":event.organization,"query":issue_text,"limit":max_repositories,"component_limit":5,"stale_after_hours":24})
        if isinstance(search.get("repositories"),list) and search["repositories"]:
            candidates=validate_ontology({"revision_id":ontology["revision_id"],"complete":True,"repositories":search["repositories"]})["repositories"]
    except (IngestionError, AttributeError): pass
    try:
        raw = json.loads(provider.complete(routing_prompt(event, candidates), ROUTING_SCHEMA))
    except json.JSONDecodeError as err:
        raise IngestionError("provider returned invalid JSON") from err
    routes = validate_routes(raw, ontology["repositories"])
    evidence = {str(repo.get("id") or repo.get("repository_id")): repo for repo in candidates}
    for route in routes:
        candidate = evidence.get(route["repository_id"], {})
        kind = candidate.get("match_kind")
        if kind in ("semantic", "lexical"):
            route["match_kind"] = kind
            if isinstance(candidate.get("score"), (int, float)): route["score"] = float(candidate["score"])
            matched = candidate.get("matched_components")
            if isinstance(matched, list) and matched and isinstance(matched[0], dict) and matched[0].get("path"):
                route["matched_path"] = matched[0]["path"]
    merged={route["repository_id"]:route for route in routes}
    for route in explicit: merged[route["repository_id"]]=route
    routes=list(merged.values())
    partial = False
    for route in routes:
        try:
            route["reviewer"] = top_approver(github.approved_reviews(route["full_name"]))
        except IngestionError:
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
    result = {
        "status": status, "source_id": event.source_id, "provider": provider.label,
        "ontology_revision_id": ontology["revision_id"], "affected_repository_count": len(routes),
        "ingestion_id": response.get("id"),
    }
    if writeback:
        try:
            result["writeback"] = github.upsert_issue_summary(
                event.repository, event.issue_number, writeback_marker(event.source_id),
                render_summary(status, provider.label, response.get("id"), routes, event.source_id),
            )
        except IngestionError as err:
            raise IngestionError(f"ingestion {response.get('id') or 'unknown'} persisted but issue writeback failed: {err}") from err
    return result
