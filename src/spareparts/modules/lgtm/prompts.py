"""
The shared prompts, loaded from `prompts/lgtm-questions.v1.json`.

That file is byte-identical in this repo and in
`sparepartslabs/spareparts-lgtm`, and both load it at runtime. The reason is
narrow and specific: the CLI and the Action write questions about the same
diffs, and two tools disagreeing about the same diff is worse than either being
imperfect. Wording that lives in two source files drifts — it already had, in
three places, before this file existed.

The prompts are data, so the file is data. Nothing here knows what a pull
request is.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

FILENAME = "lgtm-questions.v1.json"

#: SHA-256 of the prompts file, pinned so an edit cannot pass unnoticed. A test
#: asserts this matches. When it fails, the edit was deliberate — update the
#: constant here AND copy the file to the other repo, which pins the same
#: value. See PROMPTS.md.
PROMPTS_SHA256 = "2ecbb0aff807f1034985c7b784708ce3cdb6d7dbff14e035c69e8078b6501410"

#: Inside the package rather than at the repo root, so it survives a wheel
#: build. The Action's copy sits at `prompts/lgtm-questions.v1.json`; the paths
#: differ because the repo layouts do, the bytes do not.
PROMPTS_PATH = files("spareparts") / "prompts" / FILENAME


class PromptsError(RuntimeError):
    """The prompts file is missing or malformed. Never a silent fallback."""


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    try:
        raw = PROMPTS_PATH.read_bytes()
    except OSError as err:
        raise PromptsError(f"Could not read {PROMPTS_PATH}: {err}") from err
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise PromptsError(f"{PROMPTS_PATH} is not valid JSON: {err}") from err

    for key in ("propose", "verify", "difficultyGuidance"):
        if key not in data:
            raise PromptsError(f"{PROMPTS_PATH} has no `{key}`.")
    return data


def file_sha256() -> str:
    return hashlib.sha256(PROMPTS_PATH.read_bytes()).hexdigest()


def _render(lines: list[str], values: dict[str, str]) -> str:
    text = "\n".join(lines)
    for name, value in values.items():
        text = text.replace(f"{{{{{name}}}}}", value)
    return text


def propose(diff: str, difficulty: str, want: int) -> str:
    data = load()
    guidance = data["difficultyGuidance"].get(difficulty)
    if guidance is None:
        # `config` has already clamped this to a known value; reaching here
        # means the shared file and the config disagree about the vocabulary,
        # which is a drift bug and must not be papered over with a default.
        raise PromptsError(f"No guidance for difficulty {difficulty!r}.")
    return _render(
        data["propose"],
        {"want": str(want), "difficultyGuidance": guidance, "diff": diff},
    )


def verify(
    question: str, options: list[str], correct: int, cited: str, rationale: str, diff: str
) -> str:
    marked = "\n".join(
        f"  {'*' if i == correct else ' '} {option}" for i, option in enumerate(options)
    )
    return _render(
        load()["verify"],
        {
            "question": question,
            "options": marked,
            "cited": cited,
            "rationale": rationale,
            "diff": diff,
        },
    )
