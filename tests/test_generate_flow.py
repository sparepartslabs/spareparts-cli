"""
The three stages wired together, against a stubbed client.

This is where a port goes wrong: not in the arithmetic, which is easy to test,
but in whether propose → ground → verify still refuses in all the places the
original refused. Every test here is a refusal except the first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spareparts.modules.lgtm.config import DEFAULTS
from spareparts.modules.lgtm.generator import Quiz, Skip, generate_from_diff

DIFF = """diff --git a/src/charge.ts b/src/charge.ts
--- a/src/charge.ts
+++ b/src/charge.ts
@@ -10,6 +10,9 @@ export function charge(cents: number) {
   if (cents <= 0) return;
+  if (cents > MAX) {
+    throw new Error('too much');
+  }
   post(cents);
"""

GOOD_QUESTION = {
    "prompt": "What does the new guard prevent?",
    "options": ["A charge above MAX", "A charge below zero", "A duplicate charge"],
    "correct": 0,
    "file": "src/charge.ts",
    "hunk": "@@ -10,6 +10,9 @@",
    "rationale": "the new branch throws when cents > MAX",
}


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list[_Block]
    stop_reason: str = "end_turn"


class StubClient:
    """Answers propose and verify differently, and records what it was asked."""

    def __init__(self, questions: list[dict[str, Any]], verdict: dict[str, Any]):
        self.questions = questions
        self.verdict = verdict
        self.calls: list[str] = []
        self.messages = self

    def create(self, **kwargs) -> _Response:
        prompt = kwargs["messages"][0]["content"]
        is_verify = "Your job is to find a reason it should NOT be used" in prompt
        self.calls.append("verify" if is_verify else "propose")
        payload = self.verdict if is_verify else {"questions": self.questions}
        return _Response(content=[_Block(text=json.dumps(payload))])


SOUND = {"sound": True, "problem": ""}


def test_a_sound_grounded_question_survives():
    client = StubClient([GOOD_QUESTION], SOUND)
    result = generate_from_diff(client, DIFF, DEFAULTS)
    assert isinstance(result, Quiz)
    assert len(result.questions) == 1
    assert result.correct == [0]
    assert result.questions[0].file == "src/charge.ts"


def test_an_unsound_verdict_drops_the_question():
    client = StubClient([GOOD_QUESTION], {"sound": False, "problem": "ambiguous"})
    assert isinstance(generate_from_diff(client, DIFF, DEFAULTS), Skip)


def test_a_verifier_that_does_not_say_true_is_not_approval():
    # The safety default: anything but an explicit `true` is a rejection.
    for verdict in ({"problem": ""}, {"sound": "yes"}, {"sound": 1}, {"sound": None}):
        client = StubClient([GOOD_QUESTION], verdict)
        assert isinstance(generate_from_diff(client, DIFF, DEFAULTS), Skip), verdict


def test_an_invented_citation_never_reaches_the_verifier():
    bad = {**GOOD_QUESTION, "file": "src/imaginary.ts"}
    client = StubClient([bad], SOUND)
    assert isinstance(generate_from_diff(client, DIFF, DEFAULTS), Skip)
    # Grounding is free; spending a verification call on it would not be.
    assert client.calls == ["propose"]


def test_an_invented_hunk_never_reaches_the_verifier():
    bad = {**GOOD_QUESTION, "hunk": "@@ -900,1 +900,1 @@"}
    client = StubClient([bad], SOUND)
    assert isinstance(generate_from_diff(client, DIFF, DEFAULTS), Skip)
    assert client.calls == ["propose"]


def test_the_giveaway_length_is_filtered_before_verification():
    tell = {
        **GOOD_QUESTION,
        "options": [
            "It rejects any charge above MAX by throwing before the post call",
            "Nothing",
            "It logs",
        ],
    }
    client = StubClient([tell], SOUND)
    assert isinstance(generate_from_diff(client, DIFF, DEFAULTS), Skip)
    assert client.calls == ["propose"]


def test_more_survivors_than_asked_for_are_trimmed():
    second = {**GOOD_QUESTION, "prompt": "And what does it throw?"}
    client = StubClient([GOOD_QUESTION, second], SOUND)
    result = generate_from_diff(client, DIFF, DEFAULTS)
    assert isinstance(result, Quiz)
    # Over-generate, verify all, keep `questions`. Both were verified.
    assert len(result.questions) == 2
    assert client.calls.count("verify") == 2


def test_an_empty_diff_never_calls_the_model():
    client = StubClient([GOOD_QUESTION], SOUND)
    assert isinstance(generate_from_diff(client, "", DEFAULTS), Skip)
    assert client.calls == []


def test_a_refusal_is_a_skip_not_a_crash():
    class Refusing(StubClient):
        def create(self, **kwargs):
            return _Response(content=[], stop_reason="refusal")

    result = generate_from_diff(Refusing([], {}), DIFF, DEFAULTS)
    assert isinstance(result, Skip)
    assert "Declined" in result.reason


def test_truncation_is_a_skip_not_a_crash():
    class Truncating(StubClient):
        def create(self, **kwargs):
            return _Response(content=[], stop_reason="max_tokens")

    assert isinstance(generate_from_diff(Truncating([], {}), DIFF, DEFAULTS), Skip)


def test_a_verifier_that_throws_rejects_rather_than_approves():
    class HalfBroken(StubClient):
        def create(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            if "Your job is to find a reason it should NOT be used" in prompt:
                raise RuntimeError("network died")
            return super().create(**kwargs)

    result = generate_from_diff(HalfBroken([GOOD_QUESTION], SOUND), DIFF, DEFAULTS)
    assert isinstance(result, Skip)
