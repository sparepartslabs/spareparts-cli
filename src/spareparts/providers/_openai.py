"""
OpenAI, via the Responses API and a strict json_schema format.

`strict: true` is the point of using this shape rather than asking for JSON in
the prompt — the schema is enforced by the decoder rather than hoped for. It
also constrains what the schema may say: every property must be listed in
`required`, and `additionalProperties` must be false. Ours already satisfy both,
and `_strictify` enforces it rather than trusting that, because a schema that
quietly fails strict validation surfaces as an unhelpful 400.

A refusal arrives as a `refusal` content part rather than an exception, so it is
checked for explicitly. Reading `output_text` without looking would turn a
refusal into "returned nothing".
"""

from __future__ import annotations

from typing import Any

from . import ProviderError

MAX_TOKENS = 16000


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Every object requires all its properties and forbids extras, recursively."""
    if not isinstance(schema, dict):
        return schema

    out = dict(schema)
    if out.get("type") == "object":
        properties = out.get("properties")
        if isinstance(properties, dict):
            out["properties"] = {k: _strictify(v) for k, v in properties.items()}
            out["required"] = list(properties)
            out["additionalProperties"] = False
    if "items" in out:
        out["items"] = _strictify(out["items"])
    return out


class OpenAIProvider:
    def __init__(self, model: str):
        try:
            import openai
        except ImportError as err:
            raise ProviderError(
                "openai support is not installed — `pip install spareparts-cli[openai]`."
            ) from err

        self.label = f"openai:{model}"
        self.model = model
        self._client = openai.OpenAI()

    def complete(self, prompt: str, schema: dict[str, Any]) -> str:
        try:
            response = self._client.responses.create(
                model=self.model,
                input=prompt,
                max_output_tokens=MAX_TOKENS,
                reasoning={"effort": "high"},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "result",
                        "strict": True,
                        "schema": _strictify(schema),
                    }
                },
            )
        except Exception as err:  # noqa: BLE001 — the vendor's words are the useful ones
            raise ProviderError(f"{self.label}: {err}") from err

        for item in getattr(response, "output", []) or []:
            for part in getattr(item, "content", []) or []:
                if getattr(part, "type", None) == "refusal":
                    reason = getattr(part, "refusal", "") or "unspecified"
                    raise ProviderError(f"{self.label} declined ({reason}).")

        if getattr(response, "status", None) == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or "unspecified"
            raise ProviderError(f"{self.label}: response was incomplete ({reason}).")

        text = getattr(response, "output_text", "") or ""
        if not text.strip():
            raise ProviderError(f"{self.label} returned nothing.")
        return text


def build(model: str) -> OpenAIProvider:
    return OpenAIProvider(model)
