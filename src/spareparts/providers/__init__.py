"""
One structured call, from any of three vendors.

Everything `sp` asks a model to do has the same shape: here is a prompt, here is
a JSON Schema, return an object matching it. That is the entire interface, and
it is deliberately narrow — no streaming, no tools, no conversation. A module
that needs more than this should say so out loud rather than widen the seam
quietly.

The three vendors express it differently:

    Anthropic   output_config={"format": {"type": "json_schema", ...}}
    OpenAI      text={"format": {"type": "json_schema", "strict": true, ...}}
    Gemini      config.response_json_schema + response_mime_type

so each adapter lives in its own module and this one only knows how to pick.

Why more than one at all: `lgtm`'s generator proposes a question with one call
and then asks a second call to refute it. Two calls to the same model is a
weaker check than it looks — a model agrees with itself. Being able to propose
with one vendor and refute with another is the strongest version of that check
available, and it is the reason this package exists rather than a `--model`
flag.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """Anything that stopped a provider from answering. Always human-readable."""


class Provider(Protocol):
    #: e.g. "anthropic:claude-opus-5". Printed, and used to tell a proposer
    #: from a verifier in output.
    label: str

    def complete(self, prompt: str, schema: dict[str, Any]) -> str:
        """Return a JSON string matching `schema`, or raise ProviderError."""


@dataclass(frozen=True)
class Vendor:
    name: str
    module: str
    #: The model used when nobody names one.
    #:
    #: Only the Anthropic default has been verified against a live API by this
    #: repo. The other two are conservative picks — if a vendor has retired or
    #: renamed one, the failure is loud (the vendor's own "model not found"
    #: reaches the terminal unchanged) and the fix is `--model` or the
    #: `model:` key in `.github/lgtm.yml`. A default that must be guessed is
    #: better guessed low than high.
    default_model: str
    #: Checked before the call, so a missing key is a sentence rather than a
    #: stack trace from inside a vendor SDK.
    env_keys: tuple[str, ...]


VENDORS: tuple[Vendor, ...] = (
    Vendor("anthropic", "spareparts.providers._anthropic", "claude-opus-5", ("ANTHROPIC_API_KEY",)),
    Vendor("openai", "spareparts.providers._openai", "gpt-5", ("OPENAI_API_KEY",)),
    Vendor("gemini", "spareparts.providers._gemini", "gemini-2.5-pro", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
)

_BY_NAME = {v.name: v for v in VENDORS}

DEFAULT_VENDOR = "anthropic"


def available() -> list[str]:
    """Vendors whose key is set. Used to explain what the user could pick."""
    return [v.name for v in VENDORS if any(os.environ.get(k) for k in v.env_keys)]


def resolve(spec: str | None, model: str | None = None) -> Provider:
    """
    Turn `"openai"` or `"openai:gpt-5"` or None into something callable.

    `model` is the flag form and wins over the `vendor:model` form, because a
    flag is typed now and the spec may have come out of a config file.
    """
    spec = spec or DEFAULT_VENDOR
    name, _, spec_model = spec.partition(":")
    name = name.strip().lower()

    vendor = _BY_NAME.get(name)
    if vendor is None:
        known = ", ".join(v.name for v in VENDORS)
        raise ProviderError(f"Unknown provider {name!r}. Known providers: {known}.")

    if not any(os.environ.get(k) for k in vendor.env_keys):
        keys = " or ".join(vendor.env_keys)
        others = [n for n in available() if n != name]
        hint = f" You have a key for: {', '.join(others)}." if others else ""
        raise ProviderError(f"{vendor.name} needs {keys} set.{hint}")

    chosen = model or spec_model or vendor.default_model
    return importlib.import_module(vendor.module).build(chosen)
