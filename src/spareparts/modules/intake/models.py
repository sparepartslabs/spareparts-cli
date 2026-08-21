"""Validated, secret-safe values for one private intake run."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

class IntakeError(RuntimeError):
    def __init__(self, message: str, *, category: str = "internal_failure", retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable

@dataclass(frozen=True, repr=False)
class Claim:
    job_id: str
    event: dict[str, Any]
    provider: str
    model: str | None
    provider_credential: str
    github_token: str
    lease_id: str
    configuration_version: int
    completion_token: str
    timeout_seconds: int

    def __repr__(self) -> str:
        return f"Claim(job_id={self.job_id!r}, provider={self.provider!r}, credentials=[REDACTED])"

    @classmethod
    def from_payload(cls, value: Any) -> "Claim":
        if not isinstance(value, dict):
            raise IntakeError("Core claim response was not an object", category="invalid_claim")
        required=("job_id","event","provider","provider_credential","github_token","lease_id","configuration_version","completion_token","timeout_seconds")
        if any(key not in value for key in required) or not isinstance(value["event"],dict):
            raise IntakeError("Core claim response was invalid", category="invalid_claim")
        if value["provider"] not in ("openai","anthropic","gemini"):
            raise IntakeError("Core claim provider is unsupported", category="unsupported_provider")
        if not isinstance(value["provider_credential"],str) or not isinstance(value["github_token"],str):
            raise IntakeError("Core claim credentials were invalid", category="invalid_claim")
        return cls(
            job_id=str(value["job_id"]),event=value["event"],provider=value["provider"],
            model=value.get("model") if isinstance(value.get("model"),str) else None,
            provider_credential=value["provider_credential"],github_token=value["github_token"],
            lease_id=str(value["lease_id"]),configuration_version=int(value["configuration_version"]),
            completion_token=str(value["completion_token"]),timeout_seconds=int(value["timeout_seconds"]),
        )
