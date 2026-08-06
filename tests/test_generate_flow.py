"""
The three stages wired together, against a stubbed client.

This is where a port goes wrong: not in the arithmetic, which is easy to test,
but in whether propose → ground → verify still refuses in all the places the
original refused. Every test here is a refusal except the first.
"""

from __future__ import annotations

import json
from typing import Any

from spareparts.modules.lgtm.config import DEFAULTS
from spareparts.modules.lgtm.generator import (
    VERIFY_SCHEMA,
    Quiz,
    Skip,
    generate_from_diff,
)
from spareparts.providers import ProviderError

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


def _is_verify(schema: dict) -> bool:
    """
    Which stage this is, by schema rather than by prompt text.

    Matching on wording made these tests break the moment the prompts moved
    into the shared file and a sentence rewrapped — a false failure about
    something the tests do not care about.
    """
    return schema is VERIFY_SCHEMA


class StubProvider:
    """Answers propose and verify differently, and records what it was asked."""

    def __init__(
        self,
        questions: list[dict[str, Any]],
        verdict: dict[str, Any],
        label: str = "stub:model",
    ):
        self.label = label
        self.questions = questions
        self.verdict = verdict
        self.calls: list[str] = []

    def complete(self, prompt: str, schema: dict[str, Any]) -> str:
        is_verify = _is_verify(schema)
        self.calls.append("verify" if is_verify else "propose")
        return json.dumps(self.verdict if is_verify else {"questions": self.questions})


# The old name, so the tests below read the same as before the provider seam.
StubClient = StubProvider


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


def test_a_provider_that_refuses_is_a_skip_not_a_crash():
    class Refusing(StubProvider):
        def complete(self, prompt, schema):
            raise ProviderError("stub:model declined (unspecified).")

    result = generate_from_diff(Refusing([], {}), DIFF, DEFAULTS)
    assert isinstance(result, Skip)
    assert "declined" in result.reason


def test_a_verifier_that_throws_rejects_rather_than_approves():
    class HalfBroken(StubProvider):
        def complete(self, prompt, schema):
            if _is_verify(schema):
                raise ProviderError("network died")
            return super().complete(prompt, schema)

    result = generate_from_diff(HalfBroken([GOOD_QUESTION], SOUND), DIFF, DEFAULTS)
    assert isinstance(result, Skip)


def test_a_separate_verifier_does_the_marking():
    # The arrangement the provider seam exists for: one vendor proposes, a
    # different one is asked to refute.
    proposer = StubProvider([GOOD_QUESTION], SOUND, label="a:one")
    verifier = StubProvider([], SOUND, label="b:two")

    result = generate_from_diff(proposer, DIFF, DEFAULTS, verifier)
    assert isinstance(result, Quiz)
    assert proposer.calls == ["propose"]
    assert verifier.calls == ["verify"]


def test_a_separate_verifier_can_veto_what_the_proposer_wrote():
    proposer = StubProvider([GOOD_QUESTION], SOUND, label="a:one")
    verifier = StubProvider([], {"sound": False, "problem": "ambiguous"}, label="b:two")

    assert isinstance(generate_from_diff(proposer, DIFF, DEFAULTS, verifier), Skip)
