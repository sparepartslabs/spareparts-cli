from __future__ import annotations

import json
import pytest

from spareparts.cli import main
from spareparts.modules.ingest.clients import CoreClient, GitHubClient
from spareparts.modules.ingest.models import IngestionError, IssueEvent, validate_routes
from spareparts.modules.ingest.service import ROUTING_SCHEMA, explicit_routes, ingest_issue, render_summary, top_approver, writeback_marker


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
        self.writebacks = []

    def repositories(self, organization, limit):
        self.catalog_calls += 1
        return [{"repository_id": "R_core", "name": "core", "name_with_owner": "sparepartslabs/core", "description": "Core", "visibility": "public", "lifecycle_state": "active", "is_archived": False, "relationships": [], "metadata": {"topics": [], "language": "Python"}, "source_url": "https://github.com/sparepartslabs/core"}]

    def upsert_issue_summary(self, full_name, issue_number, marker, body):
        self.writebacks.append((full_name, issue_number, marker, body))
        if self.failure == "writeback":
            raise IngestionError("GitHub API returned HTTP 403")
        return {"action": "created", "comment_id": 9, "url": "https://example/comment/9"}

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
    def search_ontology(self, body):
        return {"repositories": self.ontology["repositories"] if self.ontology else []}


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
    event = event.__class__(**{**event.__dict__, "body": "Needs investigation"})
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

def test_issue_7_component_reference_maps_to_parent_repository():
    repos=[{"id":"R_sp","full_name":"sparepartslabs/spareparts","components":[{"path":"spareparts-www","name":"spareparts-www"}]}]
    routes=explicit_routes("Please change spareparts/spareparts-www", repos)
    assert routes == [{"repository_id":"R_sp","full_name":"sparepartslabs/spareparts","rationale":"Explicit reference to sparepartslabs/spareparts component spareparts-www","confidence":1.0,"match_kind":"explicit_component","matched_path":"spareparts-www"}]

def test_unique_bare_repo_matches_but_ambiguous_name_fails_closed():
    one=[{"id":"1","full_name":"org/widget"}]
    assert explicit_routes("fix widget", one)[0]["repository_id"] == "1"
    two=one+[{"id":"2","full_name":"other/widget"}]
    assert explicit_routes("fix widget", two) == []

def test_explicit_target_survives_empty_model_response(event):
    ont={"revision_id":"r","complete":True,"repositories":[{"id":"R_core","full_name":"sparepartslabs/core","components":[]}]}
    changed=event.__class__(**{**event.__dict__,"body":"Update sparepartslabs/core"})
    core=Core(ont); ingest_issue(changed, Provider(), GitHub(), core)
    link=core.submitted["affected_repositories"][0]
    assert link["repository_id"] == "R_core" and link["match_kind"] == "explicit_repository"


def test_component_collection_is_bounded_and_persists_no_raw_body():
    import base64
    requests=[]
    def transport(request):
        requests.append(request)
        if "/git/trees/" in request.full_url: return 200,{"tree":[{"type":"blob","path":"spareparts-www/README.md"},{"type":"blob","path":"other/package.json"}]}
        package=json.dumps({"name":"spareparts-www","description":"Customer-facing UI for browsing spare parts","dependencies":{"next":"latest","react":"latest"},"privateSecret":"never persist"}).encode()
        return 200,{"content":base64.b64encode(package).decode()}
    components=GitHubClient("token",transport).components("sparepartslabs/spareparts","main",max_components=1,max_requests=1,max_bytes=4096)
    assert len(components)==1 and components[0]["path"]=="other"
    serialized=json.dumps(components)
    assert len(requests)==2 and "never persist" not in serialized
    assert components[0]["description"] == "spareparts-www — Customer-facing UI for browsing spare parts"
    assert components[0]["frameworks"] == ["Next.js", "React"]

def test_readme_extracts_heading_and_first_prose_not_badge():
    import base64
    def transport(request):
        if "/git/trees/" in request.full_url: return 200,{"tree":[{"type":"blob","path":"spareparts-www/README.md"}]}
        text="# Spare Parts Web\n\n[![build](https://example/badge)](x)\n\nThe customer UI for finding and ordering replacement parts.\n\nSECRET=not-in-summary"
        return 200,{"content":base64.b64encode(text.encode()).decode()}
    component=GitHubClient("token",transport).components("sparepartslabs/spareparts","main",max_components=2,max_requests=2,max_bytes=4096)[0]
    assert component["description"] == "Spare Parts Web — The customer UI for finding and ordering replacement parts."
    assert "SECRET" not in component["description"]

def test_core_search_uses_exact_contract():
    requests=[]
    def transport(request): requests.append(request); return 200,{"revision":{},"fresh":True,"repositories":[]}
    CoreClient("https://core","key",transport).search_ontology({"query":"x","limit":5})
    assert requests[0].method=="POST" and requests[0].full_url.endswith("/ingestion/v1/ontology-context/search")
    assert json.loads(requests[0].data)=={"query":"x","limit":5}

def test_selected_semantic_candidate_persists_score_and_component(event):
    ont={"revision_id":"r","complete":True,"repositories":[{"id":"R_core","full_name":"sparepartslabs/core","components":[]}]}
    core=Core(ont)
    core.search_ontology=lambda body:{"repositories":[{"repository_id":"R_core","name_with_owner":"sparepartslabs/core","components":[],"match_kind":"semantic","score":0.88,"matched_components":[{"path":"api"}]}]}
    provider=Provider({"affected_repositories":[{"repository_id":"R_core","rationale":"API match","confidence":0.8}]})
    ingest_issue(event.__class__(**{**event.__dict__,"body":"Needs API work"}),provider,GitHub(),core)
    link=core.submitted["affected_repositories"][0]
    assert link["match_kind"]=="semantic" and link["score"]==0.88 and link["matched_path"]=="api"

def test_selected_lexical_candidate_persists_evidence(event):
    ont={"revision_id":"r","complete":True,"repositories":[{"id":"R_core","full_name":"sparepartslabs/core","components":[]}]}
    core=Core(ont); core.search_ontology=lambda body:{"repositories":[{"id":"R_core","full_name":"sparepartslabs/core","match_kind":"lexical","score":0.6,"matched_components":[{"path":"api"}]}]}
    provider=Provider({"affected_repositories":[{"repository_id":"R_core","rationale":"API terms","confidence":0.7}]})
    ingest_issue(event.__class__(**{**event.__dict__,"body":"Needs API work"}),provider,GitHub(),core)
    link=core.submitted["affected_repositories"][0]
    assert link["match_kind"]=="lexical" and link["score"]==0.6 and link["matched_path"]=="api"


def test_missing_ontology_builds_complete_catalog(event):
    core = Core()
    github = GitHub()
    result = ingest_issue(event, Provider(), github, core)
    assert result["ontology_revision_id"] == "rev-new"
    assert core.created["organization_login"] == "sparepartslabs"
    assert core.created["revision_id"].startswith("github:sparepartslabs:")
    assert github.catalog_calls == 1
    assert core.created["refresh"]["component_count"] == 0
    assert core.created["refresh"]["github_request_count"] == 0
    assert core.created["refresh"]["catalog_duration_ms"] >= 0


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
        return 200, {"total_count": 1, "repositories": [{"node_id": "R_1", "full_name": "org/repo", "private": True}]}
    client = GitHubClient("runtime-token", transport)
    repos = client.repositories("org", 10)
    assert repos[0]["visibility"] == "private"
    assert repos[0]["repository_id"] == "R_1"
    assert "/installation/repositories?" in requests[0].full_url
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


def test_writeback_is_opt_in_and_happens_after_core_persistence(event):
    github = GitHub()
    core = Core(ontology())
    result = ingest_issue(event, Provider(), github, core, writeback=True)
    assert core.submitted is not None
    assert result["writeback"]["action"] == "created"
    assert github.writebacks[0][0:2] == ("sparepartslabs/distributor", 42)
    assert writeback_marker(event.source_id) in github.writebacks[0][3]


def test_ingestion_without_writeback_does_not_mutate_issue(event):
    github = GitHub()
    ingest_issue(event, Provider(), github, Core(ontology()))
    assert github.writebacks == []


def test_summary_contains_routes_and_safe_reviewer_evidence(event):
    routes = [{"full_name": "sparepartslabs/core", "confidence": 0.91, "rationale": "Owns the API", "reviewer": {"login": "ike", "approval_count": 3, "observed_at": "now"}}]
    summary = render_summary("accepted", "anthropic:claude-opus-5", "ing-1", routes, event.source_id)
    assert "sparepartslabs/core" in summary
    assert "91% confidence" in summary
    assert "Owns the API" in summary
    assert "ike" in summary and "3 approved reviews" in summary
    assert "delivery-1" not in summary
    assert writeback_marker(event.source_id) in summary


def test_summary_explicitly_reports_empty_routing(event):
    summary = render_summary("accepted", "openai:gpt-5.5", "ing-1", [], event.source_id)
    assert "No affected repositories" in summary


def test_writeback_failure_reports_persisted_ingestion(event):
    with pytest.raises(IngestionError, match="ingestion ing-1 persisted but issue writeback failed"):
        ingest_issue(event, Provider(), GitHub(failure="writeback"), Core(ontology()), writeback=True)


def test_github_writeback_updates_only_marked_bot_comment():
    requests = []
    marker = writeback_marker("delivery-1")
    def transport(request):
        requests.append(request)
        if "/comments?" in request.full_url:
            return 200, [
                {"id": 1, "body": marker, "user": {"login": "someone", "type": "User"}},
                {"id": 2, "body": marker, "user": {"login": "spare-parts[bot]", "type": "Bot"}},
            ]
        return 200, {"id": 2, "html_url": "https://example/2"}
    result = GitHubClient("token", transport).upsert_issue_summary("org/repo", 42, marker, "new body")
    assert result["action"] == "updated"
    assert requests[-1].method == "PATCH"
    assert requests[-1].full_url.endswith("/repos/org/repo/issues/comments/2")
    assert json.loads(requests[-1].data) == {"body": "new body"}
    assert all(not request.full_url.endswith("/installation") for request in requests)


def test_github_writeback_creates_when_no_owned_marker_exists():
    requests = []
    def transport(request):
        requests.append(request)
        if "/comments?" in request.full_url:
            return 200, [{"id": 1, "body": "marker", "user": {"login": "someone", "type": "User"}}]
        return 201, {"id": 3, "html_url": "https://example/3"}
    result = GitHubClient("token", transport).upsert_issue_summary("org/repo", 42, "marker", "body")
    assert result["action"] == "created"
    assert requests[-1].method == "POST"
    assert all(request.get_method() != "PATCH" for request in requests)
