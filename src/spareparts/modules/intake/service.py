"""One-job private intake orchestration."""
from __future__ import annotations
from typing import Any
from spareparts.modules.ingest.clients import CoreClient,GitHubClient
from spareparts.modules.ingest.models import IngestionError,IssueEvent
from spareparts.modules.ingest.service import ingest_issue,render_summary,writeback_marker
from spareparts.providers import ProviderError,resolve_with_credential
from .clients import IntakeClient
from .models import Claim,IntakeError

def issue_event(claim:Claim)->IssueEvent:
    value=claim.event
    repository=str(value.get("source_repository") or "")
    if "/" not in repository:
        raise IntakeError("claimed source repository is invalid",category="invalid_event")
    owner=repository.split("/",1)[0]
    return IssueEvent(
        source_id=str(value.get("delivery_id") or claim.job_id),action=str(value.get("action") or ""),
        organization=owner,organization_id=f"owner:{owner}",repository_id=f"repository:{repository}",
        repository=repository,issue_number=int(value.get("issue_number") or 0),
        issue_node_id=str(value.get("issue_node_id") or ""),title=str(value.get("issue_title") or ""),
        body=str(value.get("issue_body") or ""),url="",actor=str(value.get("actor_login") or ""),
    )

def resume_persisted(event:IssueEvent,core:CoreClient,github:GitHubClient)->dict[str,Any]|None:
    context=core.latest_issue(event.repository,event.issue_number)
    if not context or not isinstance(context.get("ingestion"),dict): return None
    ingestion=context["ingestion"]
    if ingestion.get("source_event_id")!=event.source_id: return None
    names={target.get("repository_id"):target.get("name_with_owner") for target in context.get("targets",[]) if isinstance(target,dict)}
    routes=[]
    for raw in ingestion.get("affected_repositories",[]):
        if not isinstance(raw,dict) or not names.get(raw.get("repository_id")): return None
        routes.append({**raw,"full_name":names[raw["repository_id"]]})
    label=f'{ingestion.get("provider","unknown")}:{ingestion.get("model","unknown")}'
    writeback=github.upsert_issue_summary(event.repository,event.issue_number,writeback_marker(event.source_id),render_summary(str(ingestion.get("status") or "accepted"),label,str(ingestion.get("id") or ""),routes,event.source_id))
    return {"status":ingestion.get("status") or "accepted","source_id":event.source_id,"provider":label,"ontology_revision_id":ingestion.get("ontology_revision_id"),"affected_repository_count":len(routes),"ingestion_id":ingestion.get("id"),"writeback":writeback,"resumed":True}

def execute(client:IntakeClient,job_id:str,bootstrap_token:str)->dict[str,Any]:
    claim=client.claim(job_id,bootstrap_token)
    try:
        event=issue_event(claim)
        github=GitHubClient(claim.github_token)
        core=CoreClient(client.base_url,claim.completion_token)
        result=resume_persisted(event,core,github)
        if result is None:
            provider=resolve_with_credential(claim.provider,claim.provider_credential,claim.model)
            result=ingest_issue(event,provider,github,core,writeback=True)
        client.complete(claim,result)
        return {"job_id":claim.job_id,"status":"completed",**result}
    except ProviderError as error:
        category="provider_failure"
        try: client.fail(claim,category,True)
        except IntakeError: pass
        raise IntakeError(str(error),category=category,retryable=True) from error
    except IngestionError as error:
        message=str(error)
        retryable=message.startswith("network request failed") or "writeback failed" in message
        category="writeback_failure" if "writeback failed" in message else "ingestion_failure"
        try: client.fail(claim,category,retryable)
        except IntakeError: pass
        raise IntakeError(message,category=category,retryable=retryable) from error
