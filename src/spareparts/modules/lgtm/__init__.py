"""
`sp lgtm` — prove you read a diff before you merge it.

The local half of LGTM (`sparepartslabs/spareparts-lgtm`, which runs the same
three-stage generator as a GitHub Action against a pull request). What that one
does to a reviewer after they approve, this one does to you, on a range you name,
before you merge.

The differences are deliberate and both directions:

  - No gate. There is no check run and nothing to block; the exit code is the
    only output a script can read, and `--no-verify` is always one flag away.
    This is a self-check, and the docs should never call it more than that.
  - No sealed answer key. See `ask.py`.
  - The code is checked out, so `?` shows you the hunk instead of linking it.
    The revisit path — the part the phone version can't have — is stronger here
    than on GitHub.

Exit codes: 0 confirmed, 1 not confirmed (wrong, or left early), 2 could not ask
(no API key, git failed, nothing quizzable). A hook that blocks should treat
only 1 as a failure — a tool that can't run must not stop a commit.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from spareparts.providers import ProviderError, resolve

from .ask import make_styler, run_quiz
from .config import load_config
from .diff import parse_diff
from .generator import Quiz, Skip, generate_from_diff
from .git import GitError, changed_files, default_range, repo_root, unified_diff
from .screen import screen

EXIT_CONFIRMED = 0
EXIT_NOT_CONFIRMED = 1
EXIT_COULD_NOT_ASK = 2


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "revspec",
        nargs="?",
        help="What to review, e.g. `main...HEAD` or `abc123..def456`. "
        "Defaults to what this branch adds since it left the default branch.",
    )
    parser.add_argument(
        "-n",
        "--questions",
        type=int,
        help="How many questions to ask (1-5). Overrides .github/lgtm.yml.",
    )
    parser.add_argument(
        "-d",
        "--difficulty",
        choices=("easy", "medium", "hard"),
        help="How hard the distractors are. Overrides .github/lgtm.yml.",
    )
    parser.add_argument(
        "-p",
        "--provider",
        help="Who writes the questions: anthropic, openai, gemini — "
        "optionally `vendor:model`. Overrides .github/lgtm.yml.",
    )
    parser.add_argument(
        "--verifier",
        help="Who tries to refute them. Defaults to the proposer; naming a "
        "different vendor is a stronger check than a model marking itself.",
    )
    parser.add_argument(
        "--model",
        help="Model for the proposer, overriding the vendor's default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be quizzed and stop, without calling the model.",
    )


def run(args: argparse.Namespace) -> int:
    style = make_styler()

    try:
        root = repo_root()
        revspec = args.revspec or default_range()
        files = changed_files(revspec)
    except GitError as err:
        print(f"sp lgtm: {err}", file=sys.stderr)
        return EXIT_COULD_NOT_ASK

    loaded = load_config(root)
    config = loaded.config
    for problem in loaded.problems:
        print(f"sp lgtm: {problem}", file=sys.stderr)

    # Flags beat the file: you are standing at the terminal and the file is a
    # repo-wide default someone else may have written.
    if args.questions is not None:
        config = replace(config, questions=max(1, min(5, args.questions)))
    if args.difficulty is not None:
        config = replace(config, difficulty=args.difficulty)

    screened = screen(files, config)
    if screened.reason:
        print(f"sp lgtm: {screened.reason}")
        return EXIT_COULD_NOT_ASK

    if args.dry_run:
        print(f"Would quiz {len(screened.paths)} file(s) in {revspec}:")
        for path in screened.paths:
            print(f"  {path}")
        print(f"\n{config.questions} question(s), {config.difficulty}.")
        # A query, not a verdict. It answered what it was asked, so it succeeded
        # — nobody puts --dry-run in a hook, and failing here would only make
        # the flag annoying to use from a shell that checks `$?`.
        return EXIT_CONFIRMED

    # Both resolved before any work, so a bad provider name or a missing key is
    # a sentence now rather than after a minute of proposing.
    try:
        proposer = resolve(args.provider or config.provider, args.model)
        verifier_spec = args.verifier or config.verifier
        verifier = resolve(verifier_spec) if verifier_spec else proposer
    except ProviderError as err:
        print(f"sp lgtm: {err}", file=sys.stderr)
        return EXIT_COULD_NOT_ASK

    diff = unified_diff(revspec, screened.paths)

    marking = (
        f"{proposer.label} proposing, {verifier.label} verifying"
        if verifier is not proposer
        else f"{proposer.label}, marking its own work"
    )
    print(f"Reading {revspec} ({len(screened.paths)} files) — {marking}…", flush=True)
    result = generate_from_diff(proposer, diff, config, verifier)

    if isinstance(result, Skip):
        print(f"sp lgtm: {result.reason}")
        return EXIT_COULD_NOT_ASK

    assert isinstance(result, Quiz)
    confirmed = run_quiz(result, parse_diff(diff), style)
    return EXIT_CONFIRMED if confirmed else EXIT_NOT_CONFIRMED
