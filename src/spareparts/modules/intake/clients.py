"""Bounded Core worker exchange client."""
from __future__ import annotations
import json
import urllib.error
import urllib.request
from typing import Any, Callable
from .models import Claim, IntakeError

Transport=Callable[[urllib.request.Request],tuple[int,Any]]

def _transport(request: urllib.request.Request) -> tuple[int,Any]:
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            raw=response.read()
            return response.status,json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw=error.read()
        try: body=json.loads(raw) if raw else {}
        except json.JSONDecodeError: body={}
        return error.code,body
    except (urllib.error.URLError,TimeoutError) as error:
        raise IntakeError("network request failed",category="network_failure",retryable=True) from error

class IntakeClient:
    def __init__(self,base_url:str,transport:Transport=_transport):
        if not base_url: raise IntakeError("--core-url is required",category="invalid_configuration")
        self.base_url=base_url.rstrip("/")
        self.transport=transport
    def _request(self,method:str,path:str,token:str,body:Any=None)->tuple[int,Any]:
        data=json.dumps(body,separators=(",",":")).encode() if body is not None else None
        request=urllib.request.Request(self.base_url+path,method=method,data=data,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"})
        return self.transport(request)
    def claim(self,job_id:str,bootstrap_token:str)->Claim:
        status,body=self._request("POST",f"/internal/intake-jobs/{job_id}/claim",bootstrap_token)
        if status<200 or status>=300:
            raise IntakeError(f"Core claim returned HTTP {status}",category="claim_rejected",retryable=status>=500)
        return Claim.from_payload(body)
    def complete(self,claim:Claim,result:dict[str,Any])->None:
        body={"lease_id":claim.lease_id,"configuration_version":claim.configuration_version,
              "ingestion_id":result.get("ingestion_id"),"writeback_status":(result.get("writeback") or {}).get("action")}
        status,_=self._request("POST",f"/internal/intake-jobs/{claim.job_id}/complete",claim.completion_token,body)
        if status<200 or status>=300:
            raise IntakeError(f"Core completion returned HTTP {status}",category="completion_failure",retryable=status>=500)
    def fail(self,claim:Claim,category:str,retryable:bool)->None:
        body={"lease_id":claim.lease_id,"category":category,"retryable":retryable}
        status,_=self._request("POST",f"/internal/intake-jobs/{claim.job_id}/fail",claim.completion_token,body)
        if status<200 or status>=300:
            raise IntakeError(f"Core failure report returned HTTP {status}",category="failure_report_failed",retryable=True)
