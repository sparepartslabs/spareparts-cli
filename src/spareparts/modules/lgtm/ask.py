"""
Asking the questions in a terminal.

The GitHub version seals its answer key with an HMAC, because there the person
answering can read the comment the key would sit in. None of that is here, and
pretending otherwise would be theatre: locally the answers are in the same
process, on the same machine, belonging to the same person. `sp lgtm` is a
self-check you choose to run, not a gate — so it keeps the honest parts (real
questions, verified answers, unlimited attempts, a citation you can open) and
drops the parts that only make sense against an adversary.

The one thing local can do that GitHub cannot: `?` prints the cited hunk. "Go
and look again" is the point of the tool, and here the code is right there.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .diff import FileDiff
from .generator import Quiz

LETTERS = "ABCDEFGH"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


@dataclass
class Styler:
    enabled: bool

    def __call__(self, code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if self.enabled else text


def make_styler(stream=sys.stdout) -> Styler:
    return Styler(enabled=stream.isatty())


class NoTerminal(RuntimeError):
    """There is nobody to ask. Distinct from being asked and refusing."""


def open_terminal():
    """
    Something to read answers from, or `NoTerminal`.

    Git hooks run with stdin closed, so `input()` would hit EOF on the first
    question and the whole quiz would read as "quit" — answered nothing, blamed
    the person. The hook script attaches /dev/tty, but that is not always
    possible: a GUI client, a rebase, CI. Those cases have to be told apart
    from a refusal, because one of them means "skip" and the other means "no".
    """
    if sys.stdin.isatty():
        return sys.stdin
    try:
        return open("/dev/tty")
    except OSError as err:
        raise NoTerminal("no terminal is attached") from err


def _find_hunk(files: list[FileDiff], path: str, header: str) -> str | None:
    file = next((f for f in files if f.path == path), None)
    if file is None:
        return None
    key = header.strip()
    for hunk in file.hunks:
        if hunk.header.startswith(key[:20]) or key.startswith(hunk.header[:20]):
            return "\n".join([hunk.header, *hunk.lines])
    return None


#: Lines of hunk `?` will print before it stops and points at the file instead.
#: A hunk is usually a dozen lines, but a new or renamed file arrives as one
#: `@@ -0,0 +1,657 @@`, and scrolling 657 lines past someone is not showing
#: them anything.
MAX_HUNK_LINES = 40


def _print_hunk(files: list[FileDiff], path: str, header: str, style: Styler) -> None:
    body = _find_hunk(files, path, header)
    if body is None:
        print(style(_DIM, f"  (couldn't re-find {path} {header})"))
        return

    lines = body.split("\n")
    clipped = len(lines) - MAX_HUNK_LINES

    print()
    print(style(_BOLD, f"  {path}"))
    for line in lines[:MAX_HUNK_LINES]:
        if line.startswith("+"):
            print("  " + style(_GREEN, line))
        elif line.startswith("-"):
            print("  " + style(_RED, line))
        else:
            print("  " + style(_DIM, line))
    if clipped > 0:
        # The file is checked out — say where to look rather than pretending
        # the terminal is a good place to read 600 lines.
        print()
        print(style(_DIM, f"  … {clipped} more lines. Open {path} to read the rest."))
    print()


def _prompt(stream, message: str) -> str:
    """`input()` reads stdin and nothing else; a hook needs /dev/tty."""
    if stream is sys.stdin:
        return input(message)
    sys.stdout.write(message)
    sys.stdout.flush()
    line = stream.readline()
    if line == "":
        raise EOFError
    return line.rstrip("\n")


def _ask_one(
    index: int,
    total: int,
    quiz: Quiz,
    files: list[FileDiff],
    style: Styler,
    stream,
) -> int | None:
    """
    Ask question `index`. Returns the chosen option, or None if they gave up.

    Re-prompts on anything unparseable rather than counting it as an answer —
    a stray keystroke must never be graded as a wrong reading.
    """
    question = quiz.questions[index]
    print()
    print(style(_BOLD, f"{index + 1}/{total}  {question.prompt}"))
    print(style(_DIM, f"       {question.file} · {question.hunk}"))
    print()
    for i, option in enumerate(question.options):
        print(f"  {style(_BOLD, LETTERS[i] + '.')} {option}")
    print()

    valid = LETTERS[: len(question.options)]
    while True:
        try:
            raw = _prompt(
                stream, f"  Answer [{'/'.join(valid)}], ? to see the hunk, q to quit: "
            )
        except EOFError:
            # No one is there — piped input that ran out. Not an answer.
            print()
            return None

        answer = raw.strip().lower()
        if answer in ("q", "quit"):
            return None
        if answer == "?":
            _print_hunk(files, question.file, question.hunk, style)
            continue
        if len(answer) == 1 and answer.upper() in valid:
            return valid.index(answer.upper())
        print(style(_DIM, "  Not one of the options — try again."))


def run_quiz(quiz: Quiz, files: list[FileDiff], style: Styler, stream=None) -> bool:
    """
    Ask everything, then re-ask what was wrong. Returns whether it ended
    confirmed.

    Wrong answers are never announced per question. Being told mid-quiz costs
    the only useful signal the last question has — you would start answering the
    grader instead of the diff. Grading happens once, at the end of a pass, and
    the retry pass says only *which* ones and *where to look*.
    """
    stream = stream if stream is not None else sys.stdin
    total = len(quiz.questions)
    remaining = list(range(total))
    answers: dict[int, int] = {}

    while remaining:
        for i in remaining:
            choice = _ask_one(i, total, quiz, files, style, stream)
            if choice is None:
                print(style(_DIM, "\n  Left without finishing — nothing recorded.\n"))
                return False
            answers[i] = choice

        wrong = [i for i in remaining if answers[i] != quiz.correct[i]]
        if not wrong:
            print()
            print(style(_GREEN, "  ✅ Confirmed — you read it."))
            print()
            return True

        where = ", ".join(sorted({quiz.questions[i].file for i in wrong}))
        print()
        print(
            style(
                _BOLD,
                f"  Not quite on {len(wrong)} of {len(remaining)}"
                if len(remaining) > 1
                else "  Not quite.",
            )
        )
        print(style(_DIM, f"  Worth another look at {where}. No limit on tries."))
        remaining = wrong
