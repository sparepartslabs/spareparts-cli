"""
Anthropic, via `output_config.format`.

The pause loop is the only fiddly part: a response can come back
`stop_reason == "pause_turn"` with the turn unfinished, and the continuation is
to send the assistant content back and ask again. Not handling it looks like an
empty response rather than an incomplete one.
"""

from __future__ import annotations

from typing import Any

from . import ProviderError

MAX_CONTINUATIONS = 2
MAX_TOKENS = 16000


class AnthropicProvider:
    def __init__(self, model: str):
        try:
            import anthropic
        except ImportError as err:
            raise ProviderError(
                "anthropic support is not installed — `pip install spareparts-cli[anthropic]`."
            ) from err

        self.label = f"anthropic:{model}"
        self.model = model
        self._client = anthropic.Anthropic()

    def complete(self, prompt: str, schema: dict[str, Any]) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        for _ in range(MAX_CONTINUATIONS + 1):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    output_config={
                        "effort": "high",
                        "format": {"type": "json_schema", "schema": schema},
                    },
                    messages=messages,
                )
            except Exception as err:  # noqa: BLE001 — the vendor's words are the useful ones
                raise ProviderError(f"{self.label}: {err}") from err

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) or "unspecified"
                raise ProviderError(f"{self.label} declined ({category}).")
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason == "max_tokens":
                raise ProviderError(f"{self.label}: response was truncated.")

            text = "".join(b.text for b in response.content if b.type == "text")
            if not text.strip():
                raise ProviderError(f"{self.label} returned nothing.")
            return text

        raise ProviderError(f"{self.label}: the turn never finished.")


def build(model: str) -> AnthropicProvider:
    return AnthropicProvider(model)
