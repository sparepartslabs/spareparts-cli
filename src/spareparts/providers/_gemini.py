"""
Gemini, via `response_json_schema`.

Two schema fields exist and they are not interchangeable: `response_schema`
takes an OpenAPI-subset schema, `response_json_schema` takes real JSON Schema.
We use the latter, so the same schema dict serves all three vendors.

`additionalProperties` is stripped anyway. It carries no meaning here — the
decoder is already constrained to the schema — and passing a keyword a vendor
does not model is a 400 rather than an ignored field.

A blocked response is not an exception either: it comes back with candidates
absent or a `finish_reason` that is not STOP, and `response.text` is then empty
or None. Checking the reason first turns that into a sentence naming the cause.
"""

from __future__ import annotations

from typing import Any

from . import ProviderError

MAX_TOKENS = 16000


def _plain(schema: Any) -> Any:
    """The schema without keywords Gemini does not model."""
    if isinstance(schema, list):
        return [_plain(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    return {k: _plain(v) for k, v in schema.items() if k != "additionalProperties"}


class GeminiProvider:
    def __init__(self, model: str, credential: str | None = None):
        try:
            from google import genai
        except ImportError as err:
            raise ProviderError(
                "gemini support is not installed — `pip install spareparts-cli[gemini]`."
            ) from err

        self.label = f"gemini:{model}"
        self.model = model
        # Reads GEMINI_API_KEY, then GOOGLE_API_KEY. `resolve` has already
        # confirmed one of them is set.
        self._client = genai.Client(api_key=credential)

    def complete(self, prompt: str, schema: dict[str, Any]) -> str:
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=_plain(schema),
                    max_output_tokens=MAX_TOKENS,
                ),
            )
        except Exception as err:  # noqa: BLE001 — the vendor's words are the useful ones
            raise ProviderError(f"{self.label}: {err}") from err

        blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
        if blocked:
            raise ProviderError(f"{self.label} blocked the prompt ({blocked}).")

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            reason = getattr(candidates[0], "finish_reason", None)
            # `None` happens on some streaming-adjacent shapes; only an explicit
            # non-STOP reason is a failure.
            if reason is not None and getattr(reason, "name", str(reason)) != "STOP":
                name = getattr(reason, "name", str(reason))
                raise ProviderError(f"{self.label} stopped early ({name}).")

        text = getattr(response, "text", None) or ""
        if not text.strip():
            raise ProviderError(f"{self.label} returned nothing.")
        return text


def build(model: str, credential: str | None = None) -> GeminiProvider:
    return GeminiProvider(model, credential)
