import builtins

import pytest

from spareparts.modules.lgtm.ask import Styler, run_quiz
from spareparts.modules.lgtm.diff import parse_diff
from spareparts.modules.lgtm.generator import Question, Quiz

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

PLAIN = Styler(enabled=False)


def quiz(n: int = 2) -> Quiz:
    questions = [
        Question(
            prompt=f"Question {i}",
            options=["first", "second", "third"],
            file="src/charge.ts",
            hunk="@@ -10,6 +10,9 @@",
        )
        for i in range(n)
    ]
    return Quiz(questions=questions, correct=[0] * n)


@pytest.fixture
def answers(monkeypatch):
    def feed(*responses: str):
        it = iter(responses)

        def fake_input(_prompt: str = "") -> str:
            try:
                return next(it)
            except StopIteration:
                raise EOFError

        monkeypatch.setattr(builtins, "input", fake_input)

    return feed


def files():
    return parse_diff(DIFF)


def test_all_correct_confirms(answers):
    answers("a", "a")
    assert run_quiz(quiz(), files(), PLAIN) is True


def test_case_does_not_matter(answers):
    answers("A", " a ")
    assert run_quiz(quiz(), files(), PLAIN) is True


def test_wrong_then_right_confirms(answers):
    # Unlimited attempts is the design: getting it wrong sends you back to the
    # code, it does not end the run.
    answers("b", "a", "a")
    assert run_quiz(quiz(), files(), PLAIN) is True


def test_only_the_wrong_question_is_re_asked(answers, capsys):
    answers("a", "b", "a")
    assert run_quiz(quiz(), files(), PLAIN) is True
    out = capsys.readouterr().out
    # Three prompts total across two passes: the second pass asks one question.
    assert out.count("Question 1") == 2
    assert out.count("Question 0") == 1


def test_quitting_is_not_confirmation(answers):
    answers("a", "q")
    assert run_quiz(quiz(), files(), PLAIN) is False


def test_running_out_of_input_is_not_confirmation(answers):
    answers("a")
    assert run_quiz(quiz(), files(), PLAIN) is False


def test_garbage_is_re_asked_not_graded(answers):
    # A stray keystroke must never be recorded as a wrong reading.
    answers("z", "", "9", "a")
    assert run_quiz(quiz(1), files(), PLAIN) is True


def test_question_mark_shows_the_cited_hunk(answers, capsys):
    answers("?", "a")
    assert run_quiz(quiz(1), files(), PLAIN) is True
    out = capsys.readouterr().out
    assert "throw new Error('too much')" in out


def test_the_revisit_note_points_at_the_file(answers, capsys):
    answers("b", "a")
    run_quiz(quiz(1), files(), PLAIN)
    assert "src/charge.ts" in capsys.readouterr().out
