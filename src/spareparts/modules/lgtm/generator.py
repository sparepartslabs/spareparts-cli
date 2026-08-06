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

import anthropic

from .config import Config
from .diff import FileDiff, is_grounded, parse_diff, render_for_prompt

MODEL = "claude-opus-5"

#: How much diff the proposer sees. Beyond this, the change is not quizzed.
DIFF_BUDGET = 120_000

#: Over-generate, then let verification cull.
OVERSHOOT = 2

MAX_CONTINUATIONS = 2


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

DIFFICULTY_GUIDANCE = {
    "easy": (
        "Distractors should be clearly unrelated to what this hunk does — a "
        "reviewer who read the change should rule them out immediately."
    ),
    "medium": (
        "Distractors should be true statements about this change that do not "
        "answer the question asked. Skimming the diff is not enough to rule "
        "them out."
    ),
    "hard": (
        "Distractors should be the plausible misreading: the behaviour BEFORE "
        "the change, the branch not taken, an adjacent call site, or an effect "
        "that looks right but happens one layer away. Only someone who read "
        "this hunk closely can rule them out."
    ),
}


def _propose_prompt(diff: str, config: Config, want: int) -> str:
    return "\n".join(
        [
            "You are writing a short comprehension check for someone who has just",
            "read this change and is about to accept it. The goal is to distinguish",
            "someone who read the change from someone who skimmed the file list.",
            "",
            f"Write up to {want} multiple-choice questions. Fewer is fine. Zero is fine",
            "if nothing in this diff is worth asking about.",
            "",
            "What to ask about, in priority order:",
            "1. Behaviour changes — what the code now does that it did not before.",
            "2. Error and edge paths — what a new guard prevents, what a changed",
            "   catch block now swallows or rethrows.",
            "3. Risk — a migration that touches existing rows, a changed default, a",
            "   security-relevant edit, a widened permission.",
            "",
            "Hard rules:",
            "- The question MUST be answerable from the diff shown, and nothing else.",
            "  If answering needs knowledge of code not in this diff, do not ask it.",
            "- Never ask about statistics: which file has the most lines added, how",
            "  many files changed, the order of files. That is trivia — someone who",
            "  read the change carefully would not know it, and someone who read",
            "  nothing could look it up in seconds.",
            "- Never ask about naming, formatting, or style.",
            "- Exactly one option may be correct. The others must be clearly wrong to",
            "  someone who read the hunk, and not obviously wrong to someone who did",
            "  not.",
            "- Give 3 options. Keep them the same rough length and shape — a longest",
            "  or most-detailed option that is always the answer gives the game away.",
            "- `file` must be a path shown below. `hunk` must be the exact `@@ ... @@`",
            "  header of the hunk you drew the question from, copied verbatim.",
            "- `rationale` explains why the correct option is correct, citing the",
            "  specific lines. It is not shown to the person answering.",
            "",
            DIFFICULTY_GUIDANCE[config.difficulty],
            "",
            "The diff:",
            "",
            diff,
        ]
    )


def _verify_prompt(candidate: Candidate, diff: str) -> str:
    marked = [
        f"  {'*' if i == candidate.correct else ' '} {option}"
        for i, option in enumerate(candidate.options)
    ]
    return "\n".join(
        [
            "You are checking a comprehension question written by someone else for a",
            "code reviewer. Your job is to find a reason it should NOT be used. Assume",
            "it is flawed and look for the flaw. Only conclude it is sound if you",
            "genuinely cannot find one.",
            "",
            "Mark it unsound if ANY of these is true:",
            "- The stated correct answer is not actually correct according to the diff.",
            "- Another option is also defensibly correct.",
            "- The question cannot be answered from the diff alone.",
            "- It asks about statistics, counts, file ordering, naming, or formatting",
            "  rather than about what the change does.",
            "- A reviewer who read this change carefully could still get it wrong —",
            "  because it turns on an obscure detail, or is ambiguously worded.",
            "- Someone who did NOT read the diff could pick the right option anyway:",
            "  the correct option is the longest or most detailed, the distractors are",
            "  nonsense, or the answer is inferable from the question wording.",
            "- The cited hunk does not contain what the question claims.",
            "",
            "Be decisive. A wrong question fails a reviewer who did their job, which",
            "is far worse than asking one fewer question. When in doubt, unsound.",
            "",
            f"Question: {candidate.prompt}",
            *marked,
            "(* marks the claimed answer)",
            f"Cited: {candidate.file} {candidate.hunk}",
            f"Author's rationale: {candidate.rationale}",
            "",
            "The diff:",
            "",
            diff,
        ]
    )


def generate_from_diff(
    client: anthropic.Anthropic, diff: str, config: Config
) -> Quiz | Skip:
    """
    Generate a verified quiz from a unified diff.

    Never raises. Every failure is a `Skip` carrying a reason worth printing,
    because a generation problem should say so plainly rather than look like a
    quiz you passed.
    """
    files = parse_diff(diff)
    if not files:
        return Skip("No reviewable changes in this diff.")

    text, included = render_for_prompt(files, DIFF_BUDGET)
    if not included:
        return Skip("Every file in this change is too large to quiz meaningfully.")

    try:
        candidates = _propose(client, text, config)
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
        verdicts = list(pool.map(lambda c: _verify(client, c, text), grounded))

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


def _propose(
    client: anthropic.Anthropic, diff: str, config: Config
) -> list[Candidate]:
    want = min(config.questions * OVERSHOOT, 8)
    response = _complete(client, _propose_prompt(diff, config, want), PROPOSE_SCHEMA)

    raw = json.loads(response)
    questions = raw.get("questions") if isinstance(raw, dict) else None
    if not isinstance(questions, list):
        raise RuntimeError("Generator returned no questions.")
    return [c for c in (_as_candidate(q) for q in questions) if c is not None]


def _verify(
    client: anthropic.Anthropic, candidate: Candidate, diff: str
) -> tuple[bool, str]:
    try:
        response = _complete(client, _verify_prompt(candidate, diff), VERIFY_SCHEMA)
        raw = json.loads(response)
        problem = raw.get("problem") if isinstance(raw, dict) else None
        # Anything but an explicit `True` is a rejection. A verifier that fails
        # to answer must not be read as approval.
        sound = isinstance(raw, dict) and raw.get("sound") is True
        return sound, problem if isinstance(problem, str) else ""
    except Exception as err:  # noqa: BLE001 — a verifier that dies rejects
        return False, str(err) or "verification failed"


def _complete(
    client: anthropic.Anthropic, prompt: str, schema: dict[str, Any]
) -> str:
    """One structured call, with the server-tool pause loop handled."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    for _ in range(MAX_CONTINUATIONS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=messages,
        )

        if response.stop_reason == "refusal":
            category = getattr(response, "stop_details", None)
            category = getattr(category, "category", None) or "unspecified"
            raise RuntimeError(f"Declined ({category}).")
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Generation was truncated.")

        text = "".join(b.text for b in response.content if b.type == "text")
        if not text.strip():
            raise RuntimeError("Generator returned nothing.")
        return text

    raise RuntimeError("Generation did not finish.")


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
