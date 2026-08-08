"""Reconcile an install target's .gitignore with the constitution working area rules.

The .sp/ directory splits into what a clone can reproduce and what it cannot.
Regenerable output (scripts/, templates/) and per-run state (execute/,
feature.json, and the trace store) are ignored. Seeded-once content
(memory/, which holds the repo's constitution) stays trackable, because
install_scaffold() never overwrites it and so nothing can reconstruct it.

This module ensures the ignored half's rules are present in the target's root
.gitignore, append-only, and never touches a rule the user wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import subprocess

COMMENT = "# Spare Parts engineering context: regenerable output and per-run state."

RULES: tuple[str, ...] = (
    "/.sp/execute/",
    "/.sp/feature.json",
    # Deliberately unanchored, matching at any depth: the blitz-sdk writes its
    # trace store relative to whichever project directory holds project configuration,
    # which is not always the repo root. An anchored rule would silently miss
    # it. The trailing * covers the SQLite -wal and -shm sidecars.
    "**/.sp/traces.db*",
    # Regenerable output: install_scaffold() refreshes both from the package
    # under --force, and nothing detects on either, so a clone reproduces them
    # exactly by running init. The test is regenerable AND not an input to the
    # thing that regenerates it. memory/ fails the first half: it is seeded
    # once and never overwritten, so nothing can reconstruct it. A rendered
    # agent command directory fails the second: it is what AGENTS[...] detects
    # on, so ignoring it means init finds no agent and installs nothing.
    # Neither belongs here. See specs/006-init-ignore-generated/spec.md.
    "/.sp/scripts/",
    "/.sp/templates/",
)


def _git(dest: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(dest), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass(frozen=True)
class BlanketIgnore:
    """An ignore rule that hides the constitution itself, wherever git found it."""

    source: str
    line: int
    pattern: str


@dataclass(frozen=True)
class Result:
    status: str  # created | appended | already_present | not_a_repo | failed
    path: Path | None = None
    added: tuple[str, ...] = ()
    blanket: BlanketIgnore | None = None
    error: str | None = None


def _missing_rules(text: str) -> tuple[str, ...]:
    """Rules absent from ``text``, in rule-set order.

    Exact-line match against stripped, non-comment lines. A commented-out rule
    ignores nothing, so it counts as absent and the real rule is emitted; the
    commented line is left alone. An equivalent-but-differently-spelled rule
    (``.sp/execute/``, ``/.sp/execute``) is not equal in text and so is
    not recognised, and a redundant rule is appended. That is the accepted cost
    of having no pattern-semantics engine here.
    """
    present = {
        stripped
        for stripped in (line.strip() for line in text.splitlines())
        if stripped and not stripped.startswith("#")
    }
    return tuple(rule for rule in RULES if rule not in present)


def ensure_rules(dest: Path) -> Result:
    """Ensure ``dest/.gitignore`` carries the working-area rules. Never raises.

    There is deliberately no ``force`` parameter. ``--force`` refreshes rendered
    commands and packaged templates; it must never rewrite a user's .gitignore.
    Leaving the parameter unthreaded is what makes that true by construction
    rather than by a branch someone can later invert.
    """
    if _git(dest, "rev-parse", "--git-dir") is None:
        return Result(status="not_a_repo")

    # Blanket detection is orthogonal to status and runs at every return below,
    # always after any write, so it reflects the post-emission state. A repo can
    # be already_present and still hide its own constitution, which is exactly the
    # case worth warning about.
    path = dest / ".gitignore"
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else None
    except (OSError, UnicodeDecodeError) as exc:
        return Result(
            status="failed", path=path, blanket=_blanket_ignore(dest), error=str(exc)
        )

    missing = _missing_rules(existing or "")
    if not missing:
        # Not opened for writing at all: no re-emitted comment, no added
        # trailing newline, mtime untouched.
        return Result(
            status="already_present", path=path, blanket=_blanket_ignore(dest)
        )

    block = "\n".join((COMMENT, *missing)) + "\n"
    if existing is None:
        text = block
    else:
        # Append-only, in "a" mode: prior bytes are preserved because they are
        # never read into a rewrite. If the user's last line has no trailing
        # newline, lead with the newline that terminates it so their rule is
        # never joined to the comment.
        lead = "" if existing.endswith("\n") or not existing else "\n"
        text = f"{lead}\n{block}" if existing else block

    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        return Result(
            status="failed", path=path, blanket=_blanket_ignore(dest), error=str(exc)
        )

    return Result(
        status="created" if existing is None else "appended",
        path=path,
        added=missing,
        blanket=_blanket_ignore(dest),
    )


def _blanket_ignore(dest: Path) -> BlanketIgnore | None:
    """The rule hiding the constitution itself, if any.

    ``--no-index`` is required, not polish: once the constitution has been
    force-added and tracked, plain check-ignore reports it as not ignored even
    with the blanket pattern still in place. The question here is what the
    rules say, not what the index happens to hold.

    Asking git rather than reading .gitignore means a rule in a nested ignore
    file, .git/info/exclude, or a global excludesfile is found too, and the
    source git reports is the one named in the warning.
    """
    out = _git(
        dest, "check-ignore", "-v", "--no-index", ".sp/memory/constitution.md"
    )
    if not out:
        return None
    # "<source>:<line>:<pattern>\t<path>"; a pattern may itself contain ":".
    fields = out.split("\t", 1)[0].split(":", 2)
    if len(fields) != 3:
        return None
    source, line, pattern = fields
    if not line.isdigit() or not pattern:
        # A warning naming the wrong rule is worse than no warning.
        return None
    return BlanketIgnore(source=source, line=int(line), pattern=pattern)
