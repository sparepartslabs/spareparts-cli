from __future__ import annotations
import json
import os
from spareparts.cli import main
from spareparts.modules.ingest.clients import spareparts_api_key
from spareparts.modules.intake.clients import IntakeClient
from spareparts.modules.intake.models import Claim
from spareparts.modules.intake.service import issue_event, resume_persisted
from spareparts.providers import resolve_with_credential

def claim_payload():
    return {"job_id":"00000000-0000-0000-0000-000000000001","event":{"delivery_id":"d1","action":"opened","source_repository":"sparepartslabs/turbo","issue_number":1,"issue_node_id":"I1","issue_title":"Test","actor_login":"ike"},"provider":"anthropic","model":None,"provider_credential":"provider-canary","github_token":"github-canary","lease_id":"00000000-0000-0000-0000-000000000002","configuration_version":1,"completion_token":"completion-canary","timeout_seconds":900}

def test_top_level_and_intake_help_need_no_provider_sdk(capsys):
    assert main(["--help"])==0
    assert "intake" in capsys.readouterr().out
    try: main(["intake","--help"])
    except SystemExit as error: assert error.code==0
    assert "worker" in capsys.readouterr().out

def test_missing_bootstrap_is_terminal_and_safe(monkeypatch,capsys):
    monkeypatch.delenv("INTAKE_BOOTSTRAP_TOKEN",raising=False)
    assert main(["intake","worker","--job-id","job-1","--core-url","https://core.example"])==3
    captured=capsys.readouterr()
    assert "required" in captured.err

def test_claim_model_redacts_credentials():
    claim=Claim.from_payload(claim_payload())
    shown=repr(claim)
    assert "provider-canary" not in shown and "github-canary" not in shown and "completion-canary" not in shown

def test_worker_client_uses_bootstrap_then_completion_bearer():
    requests=[]
    def transport(request):
        requests.append(request)
        if request.full_url.endswith("/claim"): return 200,claim_payload()
        return 204,{}
    client=IntakeClient("https://core.example",transport)
    claim=client.claim(claim_payload()["job_id"],"bootstrap-canary")
    client.complete(claim,{"ingestion_id":"00000000-0000-0000-0000-000000000003","writeback":{"action":"created"}})
    assert requests[0].get_header("Authorization")=="Bearer bootstrap-canary"
    assert requests[1].get_header("Authorization")=="Bearer completion-canary"
    body=json.loads(requests[1].data)
    assert body["configuration_version"]==1 and body["writeback_status"]=="created"

def test_canonical_api_key_precedes_legacy():
    assert spareparts_api_key({"SPAREPARTS_API_KEY":"canonical","SPAREPARTS_INGEST_KEY":"legacy"})=="canonical"
    assert spareparts_api_key({"SPAREPARTS_INGEST_KEY":"legacy"})=="legacy"
    assert spareparts_api_key({})==""

def test_claimed_provider_credential_is_removed_from_environment(monkeypatch):
    seen={}
    class Module:
        @staticmethod
        def build(model, credential=None):
            seen["credential"]=credential
            return object()
    import spareparts.providers as providers
    monkeypatch.setattr(providers.importlib,"import_module",lambda name: Module)
    monkeypatch.delenv("ANTHROPIC_API_KEY",raising=False)
    resolve_with_credential("anthropic","provider-canary","model-test")
    assert seen["credential"]=="provider-canary"
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_persisted_ingestion_resumes_at_writeback_without_provider():
    claim=Claim.from_payload(claim_payload())
    event=issue_event(claim)
    class Core:
        def latest_issue(self,repository,number):
            return {"ingestion":{"id":"00000000-0000-0000-0000-000000000003","source_event_id":"d1","status":"accepted","provider":"anthropic","model":"claude-test","ontology_revision_id":"rev-1","affected_repositories":[{"repository_id":"R1","rationale":"explicit","confidence":1.0,"reviewer":{"unavailable_reason":"no_approved_reviews"}}]},"targets":[{"repository_id":"R1","name_with_owner":"sparepartslabs/turbo"}]}
    class GitHub:
        def upsert_issue_summary(self,*args):
            return {"action":"updated"}
    result=resume_persisted(event,Core(),GitHub())
    assert result["resumed"] is True
    assert result["writeback"]["action"]=="updated"
    assert result["affected_repository_count"]==1
