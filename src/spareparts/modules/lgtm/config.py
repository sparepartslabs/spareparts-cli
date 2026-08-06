"""
`.github/lgtm.yml`, so a repo that already runs the LGTM Action is configured
for `sp lgtm` too, with no second file to keep in sync.

Validation is total and never raises: a malformed field falls back to its
default and is reported. A typo must not silently change how hard the questions
are, and must not stop the command from running either — so the caller takes the
defaults, runs, and says what it ignored.

Several fields in that file are meaningful only to the Action (they describe
what it posts to a pull request). They are parsed and ignored here rather than
reported as errors: the file is shared, and complaining about a field that is
correct for its real consumer would be noise.

Ported from `src/config.ts` in `sparepartslabs/spareparts-lgtm`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = ".github/lgtm.yml"

DIFFICULTIES = ("easy", "medium", "hard")
MIN_QUESTIONS = 1
MAX_QUESTIONS = 5


@dataclass(frozen=True)
class Config:
    #: How many questions to ask. Clamped to 1..5.
    questions: int = 2
    #: easy — distractors clearly unrelated; confirms you know the broad shape.
    #: medium — distractors are true statements that don't answer the question.
    #: hard — distractors are the plausible misreading: the behaviour before the
    #:   change, the branch not taken, the adjacent call site.
    difficulty: str = "medium"
    #: Globs never worth quizzing, on top of the built-in generated-file set.
    exempt_paths: tuple[str, ...] = ()
    #: Who writes the questions. "anthropic", "openai:gpt-5", etc. None means
    #: the default vendor.
    provider: str | None = None
    #: Who tries to refute them. None means the proposer does its own marking,
    #: which is the weaker arrangement — see `generate_from_diff`.
    verifier: str | None = None


DEFAULTS = Config()

#: Keys that belong to the Action and mean nothing locally. Accepted silently.
ACTION_ONLY = frozenset(
    {"surfaceReading", "webConcepts", "answerQuestions", "enforce", "exemptReviewers"}
)


@dataclass
class LoadedConfig:
    config: Config = DEFAULTS
    #: Human-readable notes about anything ignored. Empty means a clean parse.
    problems: list[str] = field(default_factory=list)


def parse_config(raw: Any) -> LoadedConfig:
    problems: list[str] = []
    if raw is None:
        return LoadedConfig(DEFAULTS, problems)
    if not isinstance(raw, dict):
        return LoadedConfig(
            DEFAULTS, [f"`{CONFIG_PATH}` is not a mapping — using defaults."]
        )

    questions = DEFAULTS.questions
    if "questions" in raw:
        value = raw["questions"]
        # `bool` is an `int` in Python, and `questions: true` is a mistake, not
        # a request for one question.
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append("`questions` must be a whole number — using the default.")
        elif value < MIN_QUESTIONS or value > MAX_QUESTIONS:
            # Clamped rather than rejected: someone who asked for 10 wants
            # "lots", and the ceiling exists because reading carefully still
            # shouldn't owe five minutes of quiz.
            questions = min(MAX_QUESTIONS, max(MIN_QUESTIONS, value))
            problems.append(
                f"`questions` must be between {MIN_QUESTIONS} and {MAX_QUESTIONS}"
                f" — using {questions}."
            )
        else:
            questions = value

    difficulty = DEFAULTS.difficulty
    if "difficulty" in raw:
        value = raw["difficulty"]
        if isinstance(value, str) and value in DIFFICULTIES:
            difficulty = value
        else:
            problems.append(
                f"`difficulty` must be one of {', '.join(DIFFICULTIES)}"
                f" — using {difficulty}."
            )

    exempt_paths = DEFAULTS.exempt_paths
    if raw.get("exemptPaths") is not None:
        value = raw["exemptPaths"]
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            exempt_paths = tuple(value)
        else:
            problems.append("`exemptPaths` must be a list of strings — ignored.")

    # `provider` and `verifier` are validated by `spareparts.providers`, which
    # owns the list of vendors. Checking the spelling here too would mean two
    # places to update when a vendor is added, and they would drift.
    providers: dict[str, str | None] = {"provider": None, "verifier": None}
    for key in providers:
        if raw.get(key) is None:
            continue
        if isinstance(raw[key], str) and raw[key].strip():
            providers[key] = raw[key].strip()
        else:
            problems.append(f"`{key}` must be a provider name — ignored.")

    for key in raw:
        if key in ACTION_ONLY:
            continue
        if key not in {"questions", "difficulty", "exemptPaths", "provider", "verifier"}:
            problems.append(f"`{key}` is not an LGTM setting — ignored.")

    return LoadedConfig(
        replace(
            DEFAULTS,
            questions=questions,
            difficulty=difficulty,
            exempt_paths=exempt_paths,
            provider=providers["provider"],
            verifier=providers["verifier"],
        ),
        problems,
    )


def load_config(repo_root: Path) -> LoadedConfig:
    """Read `.github/lgtm.yml` if it's there. Its absence is not a problem."""
    path = repo_root / CONFIG_PATH
    if not path.is_file():
        return LoadedConfig(DEFAULTS, [])
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as err:
        return LoadedConfig(DEFAULTS, [f"`{CONFIG_PATH}` could not be read ({err}) — using defaults."])
    return parse_config(raw)


def _glob_to_regexp(glob: str) -> re.Pattern[str]:
    """
    Enough glob for path exemptions, without a dependency.

    `**` crosses directories, `*` does not. The case that matters most is
    `**/package-lock.json`, which must match the file at the repo root as well
    as in a workspace — so `**/` is zero-or-more directories, not one-or-more.
    Getting that wrong silently quizzes people about lockfiles.
    """
    out = ""
    i = 0
    while i < len(glob):
        char = glob[i]
        if char == "*":
            if glob[i + 1 : i + 2] == "*":
                if glob[i + 2 : i + 3] == "/":
                    out += "(?:.*/)?"  # `**/` — zero or more directories.
                    i += 3
                else:
                    out += ".*"
                    i += 2
            else:
                out += "[^/]*"
                i += 1
            continue
        out += re.escape(char)
        i += 1
    return re.compile(f"^{out}$")


def matches_any(path: str, globs: tuple[str, ...] | list[str]) -> bool:
    return any(_glob_to_regexp(g).match(path) for g in globs)
