"""
Reading a diff out of the local repository.

The Action gets its diff from the GitHub API. Here it comes from `git diff`,
which is the one genuine advantage of the local version: the code the question
is about is already checked out, so "go and look again" is a real instruction
rather than a link.

Nothing in here shells out with user text in a shell string — every call is an
argv list, so a branch named `; rm -rf /` is just an unknown ref.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """git said no. The message is git's own, trimmed."""


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    additions: int
    deletions: int


def _git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def repo_root(start: Path | None = None) -> Path:
    try:
        return Path(_git(["rev-parse", "--show-toplevel"], cwd=start).strip())
    except GitError as err:
        # git's own words here are "fatal: not a git repository (or any of the
        # parent directories)", which says what failed but not what to do about
        # it. The overwhelmingly likely cause is being one directory up.
        if "not a git repository" in str(err):
            raise GitError(
                f"{Path.cwd()} is not a git repository. "
                "`sp lgtm` reads the diff from the repo you are standing in — "
                "cd into it first."
            ) from err
        raise


def default_range(cwd: Path | None = None) -> str:
    """
    What to review when nobody said.

    `<base>...HEAD` — the three-dot form, so it means "what this branch adds
    since it diverged", not "how this branch differs from base right now". The
    two differ the moment base moves, and the second one would ask you about
    other people's commits.
    """
    for candidate in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        try:
            base = _git(["rev-parse", "--verify", "--quiet", candidate], cwd=cwd).strip()
        except GitError:
            continue
        if base:
            return f"{candidate}...HEAD"
    raise GitError(
        "Could not find a base branch to compare against. "
        "Pass a range explicitly, e.g. `sp lgtm main...HEAD`."
    )


def changed_files(revspec: str, cwd: Path | None = None) -> list[ChangedFile]:
    """Per-file line counts, for the cheap screen before any model call."""
    return parse_numstat(_git(["diff", "--numstat", "-z", "--no-ext-diff", revspec], cwd=cwd))


def parse_numstat(out: str) -> list[ChangedFile]:
    """
    Parse `git diff --numstat -z`.

    `-z` is not optional. Without it git compacts a rename into the single
    pseudo-path `src/{app.ts => handlers.ts}`, which is not a file: pass it back
    to `git diff -- <path>` and it matches nothing, so a renamed file silently
    drops out of the diff the questions are written from. With `-z` a rename
    arrives as an empty path field followed by the old and new paths as separate
    records, and we keep the new one.

    Records are NUL-separated: `add\\trem\\tpath\\0`, or for a rename
    `add\\trem\\t\\0old\\0new\\0`.
    """
    fields = [f for f in out.split("\0") if f != ""]
    files: list[ChangedFile] = []
    i = 0
    while i < len(fields):
        parts = fields[i].split("\t")
        i += 1
        if len(parts) != 3:
            continue
        added, removed, name = parts
        if name == "":
            # A rename: the next two records are the old and new paths.
            if i + 1 >= len(fields):
                break
            name = fields[i + 1]
            i += 2
        # git prints "-" for binary files. They have no lines to read, so they
        # count as zero rather than being dropped — the screen decides.
        files.append(
            ChangedFile(
                filename=name,
                additions=int(added) if added.isdigit() else 0,
                deletions=int(removed) if removed.isdigit() else 0,
            )
        )
    return files


def unified_diff(
    revspec: str, paths: list[str] | None = None, cwd: Path | None = None
) -> str:
    args = ["diff", "--no-color", "--no-ext-diff", "-U3", revspec]
    if paths:
        args += ["--", *paths]
    return _git(args, cwd=cwd)
