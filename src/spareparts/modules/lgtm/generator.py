"""
The question generator.

Three stages, and the middle one is the point:

  1. Propose — one call reads the hunks and writes candidate questions about
     what the change *does*: what a new guard prevents, which edit can affect
     existing rows, what the error path now returns.
  2. Verify — each candidate goes to an independent call that has never seen the
     proposer's reasoning and is told to refute it. A candidate survives only if
     that call agrees the stated answer is right, the question is answerable
     from the diff alone, and the distractors are plausible.
  3. Ground — the citation is checked against the parsed diff in code, and the
     option set is checked for the tells that make a question free.

Stage 2 exists because a question whose stated answer is wrong is worse than no
question: it fails someone who read correctly, which is the one failure this
tool cannot recover from. One model checking its own work is not that check — it
agrees with itself.

Everything is conservative in the same direction. A candidate that cannot be
confirmed is dropped, and a run with no surviving questions asks nothing. Asking
nothing is a fine outcome; asking something wrong is not.

Ported from `src/generator.ts` in `sparepartslabs/spareparts-lgtm`. The prompts
are the part worth keeping identical — a change to the wording here that isn't
made there produces two tools that disagree about the same diff.
"""

from __future__ import annotations

import concurrent.futures
import json
from dataclasses import dataclass
from typing import Any

from spareparts.providers import Provider, ProviderError

from . import prompts
from .config import Config
from .diff import FileDiff, is_grounded, parse_diff, render_for_prompt

#: How much diff the proposer sees. Beyond this, the change is not quizzed.
DIFF_BUDGET = 120_000

#: Over-generate, then let verification cull.
OVERSHOOT = 2


@dataclass
class Candidate:
    prompt: str
    options: list[str]
    correct: int
    file: str
    hunk: str
    #: Why this is the answer, in the proposer's words. Fed to the verifier.
    rationale: str


@dataclass
class Question:
    prompt: str
    options: list[str]
    file: str
    hunk: str


@dataclass
class Quiz:
    questions: list[Question]
    #: Index into `options` for each question, parallel to `questions`.
    correct: list[int]


@dataclass
class Skip:
    reason: str


PROPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "correct": {"type": "integer"},
                    "file": {"type": "string"},
                    "hunk": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["prompt", "options", "correct", "file", "hunk", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # True only if every check passes. Default to false when unsure.
        "sound": {"type": "boolean"},
        # Which check failed, for the logs. Empty when sound.
        "problem": {"type": "string"},
    },
    "required": ["sound", "problem"],
    "additionalProperties": False,
}

def generate_from_diff(
    proposer: Provider,
    diff: str,
    config: Config,
    verifier: Provider | None = None,
) -> Quiz | Skip:
    """
    Generate a verified quiz from a unified diff.

    `verifier` defaults to `proposer`, which is the weaker arrangement and the
    one to move off when a second key is available: a model asked to refute its
    own question is being asked to disagree with itself, and it mostly doesn't.
    Passing a different vendor here is the whole reason the provider seam
    exists.

    Never raises. Every failure is a `Skip` carrying a reason worth printing,
    because a generation problem should say so plainly rather than look like a
    quiz you passed.
    """
    verifier = verifier or proposer
    files = parse_diff(diff)
    if not files:
        return Skip("No reviewable changes in this diff.")

    text, included = render_for_prompt(files, DIFF_BUDGET)
    if not included:
        return Skip("Every file in this change is too large to quiz meaningfully.")

    try:
        candidates = _propose(proposer, text, config)
    except Exception as err:  # noqa: BLE001 — every failure is a skip, with its reason
        return Skip(str(err) or "Could not generate questions.")

    # Ground before verifying: a citation that names nothing real is free to
    # reject, and there is no point spending a verification call on it.
    grounded = [
        c for c in candidates if is_grounded(included, c.file, c.hunk) and well_formed(c)
    ]
    if not grounded:
        return Skip("Could not ground a question in this diff.")

    # Verified concurrently — each is independent, and someone is waiting.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(grounded)) as pool:
        verdicts = list(pool.map(lambda c: _verify(verifier, c, text), grounded))

    survivors = [c for c, v in zip(grounded, verdicts) if v[0]]
    if not survivors:
        return Skip("No question survived verification, so none was asked.")

    kept = survivors[: config.questions]
    return Quiz(
        questions=[
            Question(prompt=c.prompt, options=c.options, file=c.file, hunk=c.hunk)
            for c in kept
        ],
        correct=[c.correct for c in kept],
    )


def _propose(proposer: Provider, diff: str, config: Config) -> list[Candidate]:
    want = min(config.questions * OVERSHOOT, 8)
    response = proposer.complete(
        prompts.propose(diff, config.difficulty, want), PROPOSE_SCHEMA
    )

    raw = json.loads(response)
    questions = raw.get("questions") if isinstance(raw, dict) else None
    if not isinstance(questions, list):
        raise RuntimeError("Generator returned no questions.")
    return [c for c in (_as_candidate(q) for q in questions) if c is not None]


def _verify(verifier: Provider, candidate: Candidate, diff: str) -> tuple[bool, str]:
    try:
        response = verifier.complete(
            prompts.verify(
                question=candidate.prompt,
                options=candidate.options,
                correct=candidate.correct,
                cited=f"{candidate.file} {candidate.hunk}",
                rationale=candidate.rationale,
                diff=diff,
            ),
            VERIFY_SCHEMA,
        )
        raw = json.loads(response)
        problem = raw.get("problem") if isinstance(raw, dict) else None
        # Anything but an explicit `True` is a rejection. A verifier that fails
        # to answer must not be read as approval.
        sound = isinstance(raw, dict) and raw.get("sound") is True
        return sound, problem if isinstance(problem, str) else ""
    except Exception as err:  # noqa: BLE001 — a verifier that dies rejects
        return False, str(err) or "verification failed"


def _as_candidate(value: Any) -> Candidate | None:
    if not isinstance(value, dict):
        return None
    for key in ("prompt", "file", "hunk", "rationale"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            return None
    options = value.get("options")
    if not isinstance(options, list) or not options:
        return None
    if not all(isinstance(o, str) and o.strip() for o in options):
        return None
    correct = value.get("correct")
    if not isinstance(correct, int) or isinstance(correct, bool):
        return None
    return Candidate(
        prompt=value["prompt"],
        options=list(options),
        correct=correct,
        file=value["file"],
        hunk=value["hunk"],
        rationale=value["rationale"],
    )


def well_formed(candidate: Candidate) -> bool:
    """
    The structural tells that make a question free, checked in code because they
    are mechanical and a verifier's judgement shouldn't be spent on them.

    The length check is the one that matters: a correct option noticeably longer
    than its distractors is the oldest giveaway in multiple choice, and a model
    writing a careful correct answer beside two throwaway wrong ones produces it
    without meaning to.
    """
    options = candidate.options
    if len(options) < 3:
        return False
    if candidate.correct < 0 or candidate.correct >= len(options):
        return False

    normal = [o.strip().lower() for o in options]
    if len(set(normal)) != len(normal):
        return False

    lengths = [len(o.strip()) for o in options]
    answer = lengths[candidate.correct]
    longest_distractor = max(l for i, l in enumerate(lengths) if i != candidate.correct)
    # Half again as long as every distractor is a tell, not a coincidence.
    if answer > longest_distractor * 1.5:
        return False

    return True
