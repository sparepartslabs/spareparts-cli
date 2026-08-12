from __future__ import annotations

import json
import pytest

from spareparts.cli import main
from spareparts.modules.ingest.clients import CoreClient, GitHubClient
from spareparts.modules.ingest.models import IngestionError, IssueEvent, validate_routes
from spareparts.modules.ingest.service import ROUTING_SCHEMA, ingest_issue, top_approver


@pytest.fixture
def payload():
    return {
        "action": "opened",
        "repository": {
            "id": 5, "node_id": "R_source", "full_name": "sparepartslabs/distributor",
            "owner": {"id": 10, "node_id": "O_spareparts", "login": "sparepartslabs"},
        },
        "issue": {"id": 42, "node_id": "I_42", "number": 42, "title": "Route this", "body": "Touches Core", "html_url": "https://example/42"},
        "sender": {"login": "octocat"},
    }


@pytest.fixture
def event(payload):
    return IssueEvent.from_payload(payload, "delivery-1")


class Provider:
    label = "openai:test-model"

    def __init__(self, result=None):
        self.result = result if result is not None else {"affected_repositories": []}
        self.prompts = []

    def complete(self, prompt, schema):
        self.prompts.append((prompt, schema))
        return json.dumps(self.result)


class GitHub:
    def __init__(self, reviews=None, failure=None):
        self.reviews = reviews or []
        self.failure = failure
        self.catalog_calls = 0

    def repositories(self, organization, limit):
        self.catalog_calls += 1
        return [{"repository_id": "R_core", "name": "core", "name_with_owner": "sparepartslabs/core", "description": "Core", "visibility": "public", "lifecycle_state": "active", "is_archived": False, "relationships": [], "metadata": {"topics": [], "language": "Python"}, "source_url": "https://github.com/sparepartslabs/core"}]

    def approved_reviews(self, full_name):
        if self.failure:
            raise IngestionError(self.failure)
        return self.reviews


class Core:
    def __init__(self, ontology=None):
        self.ontology = ontology
        self.created = None
        self.submitted = None

    def current_ontology(self, organization, limit):
        return self.ontology

    def create_ontology(self, body):
        self.created = body
        self.ontology = {"revision_id": "rev-new", "complete": True, "repositories": body["repositories"]}
        return self.ontology

    def submit(self, body):
        self.submitted = body
        return {"id": "ing-1", "duplicate": False}


def ontology():
    return {"revision_id": "rev-1", "complete": True, "repositories": [{"id": "R_core", "full_name": "sparepartslabs/core", "description": "Core"}]}


def test_top_level_help_lists_ingest(capsys):
    assert main(["--help"]) == 0
    assert "ingest" in capsys.readouterr().out


def test_ingest_help_needs_no_provider_sdk(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["ingest", "--help"])
    assert caught.value.code == 0
    assert "issue" in capsys.readouterr().out


def test_event_prefers_delivery_identity(payload):
    assert IssueEvent.from_payload(payload, "github-delivery").source_id == "github-delivery"


def test_event_fingerprint_is_stable_and_action_sensitive(payload):
    first = IssueEvent.from_payload(payload).source_id
    assert IssueEvent.from_payload(payload).source_id == first
    assert IssueEvent.from_payload({**payload, "action": "edited"}).source_id != first


def test_event_rejects_missing_nested_context(payload):
    with pytest.raises(IngestionError, match="sender"):
        IssueEvent.from_payload({key: value for key, value in payload.items() if key != "sender"})


def test_empty_routes_are_valid(event):
    core = Core(ontology())
    result = ingest_issue(event, Provider(), GitHub(), core)
    assert result["status"] == "accepted"
    assert core.submitted["affected_repositories"] == []


def test_routing_schema_uses_anthropic_compatible_number_constraints():
    confidence = ROUTING_SCHEMA["properties"]["affected_repositories"]["items"]["properties"]["confidence"]
    assert confidence == {"type": "number"}


def test_unknown_repository_is_rejected():
    with pytest.raises(IngestionError, match="outside ontology"):
        validate_routes({"affected_repositories": [{"repository_id": "R_bad", "rationale": "guess", "confidence": 0.8}]}, ontology()["repositories"])


def test_missing_ontology_builds_complete_catalog(event):
    core = Core()
    github = GitHub()
    result = ingest_issue(event, Provider(), github, core)
    assert result["ontology_revision_id"] == "rev-new"
    assert core.created["organization_login"] == "sparepartslabs"
    assert core.created["revision_id"].startswith("github:sparepartslabs:")
    assert github.catalog_calls == 1


def test_existing_ontology_skips_catalog_refresh(event):
    github = GitHub()
    ingest_issue(event, Provider(), github, Core(ontology()))
    assert github.catalog_calls == 0


def test_routing_and_reviewer_are_submitted_without_secrets(event):
    provider = Provider({"affected_repositories": [{"repository_id": "R_core", "rationale": "Issue names Core", "confidence": 0.9}]})
    github = GitHub([{"id": 1, "state": "APPROVED", "user": {"login": "ike", "type": "User"}}])
    core = Core(ontology())
    result = ingest_issue(event, provider, github, core)
    assert result["affected_repository_count"] == 1
    assert core.submitted["provider"] == "openai"
    assert core.submitted["model"] == "test-model"
    assert core.submitted["affected_repositories"][0]["reviewer"]["login"] == "ike"
    serialized = json.dumps(core.submitted)
    assert "TOKEN" not in serialized and "secret" not in serialized


def test_top_approver_filters_bots_states_duplicates_and_breaks_ties():
    reviews = [
        {"id": 1, "state": "APPROVED", "user": {"login": "zoe", "type": "User"}},
        {"id": 2, "state": "APPROVED", "user": {"login": "amy", "type": "User"}},
        {"id": 2, "state": "APPROVED", "user": {"login": "amy", "type": "User"}},
        {"id": 3, "state": "COMMENTED", "user": {"login": "amy", "type": "User"}},
        {"id": 4, "state": "APPROVED", "user": {"login": "robot[bot]", "type": "Bot"}},
    ]
    evidence = top_approver(reviews, "2026-08-11T00:00:00Z")
    assert evidence == {"login": "amy", "approval_count": 1, "observed_at": "2026-08-11T00:00:00Z"}


def test_no_approval_is_distinctly_unavailable():
    assert top_approver([], "now") == {"unavailable_reason": "no_approved_reviews"}


def test_review_permission_failure_preserves_link_as_partial(event):
    provider = Provider({"affected_repositories": [{"repository_id": "R_core", "rationale": "Core API", "confidence": 1}]})
    core = Core(ontology())
    result = ingest_issue(event, provider, GitHub(failure="GitHub API returned HTTP 403"), core)
    assert result["status"] == "partial"
    assert core.submitted["affected_repositories"][0]["reviewer"]["unavailable_reason"] == "github_evidence_unavailable"


def test_github_catalog_is_bounded_and_normalized():
    requests = []
    def transport(request):
        requests.append(request)
        return 200, [{"node_id": "R_1", "full_name": "org/repo", "private": True}]
    client = GitHubClient("runtime-token", transport)
    repos = client.repositories("org", 10)
    assert repos[0]["visibility"] == "private"
    assert repos[0]["repository_id"] == "R_1"
    assert "per_page=10" in requests[0].full_url


def test_core_client_contract_headers_and_paths():
    requests = []
    def transport(request):
        requests.append(request)
        return (404, {}) if request.method == "GET" else (201, {"id": "x"})
    client = CoreClient("https://core.example/", "core-token", transport)
    assert client.current_ontology("org", 20) is None
    assert requests[0].get_header("X-api-key") == "core-token"
    assert requests[0].get_header("Authorization") is None
    assert "/ingestion/v1/ontology-context?" in requests[0].full_url
