"""
Installing `sp lgtm` as a git hook.

Three things make this less obvious than writing a file:

1. **A hook has no stdin.** Git runs it with stdin closed, so a quiz would hit
   EOF on the first question and read as "quit" — answered nothing, blamed the
   person. The script attaches `/dev/tty`, and bails out quietly when there
   isn't one (a GUI client, a rebase, CI). See `ask.open_terminal`.

2. **A hook that blocks by default is a hook people delete.** Generation is
   three model calls and about a minute. The default is therefore advisory: it
   asks, it tells you, and the commit proceeds either way. `--blocking` is
   opt-in and the help says what it costs.

3. **`sp` is probably not on PATH where git runs.** A GUI client's PATH is not
   your shell's, and `sp` usually lives in a virtualenv. The script hard-codes
   the absolute interpreter path it was installed from.

Which hook is a real choice, not a default worth hiding — see `DEFAULT_HOOK`.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from .git import GitError, hooks_dir

HOOKS = ("pre-commit", "pre-push")

#: `pre-commit` is what people ask for and what this defaults to.
#:
#: It is worth knowing what it does and does not do. LGTM's premise is that the
#: person answering did not write the code — that is the whole point of quizzing
#: a reviewer. At pre-commit time the author is you, on your own working tree,
#: seconds after typing it. Answering questions about your own uncommitted work
#: is a proofreading pass, not a comprehension check, and it is a fair use of
#: the tool: it catches the change you made without noticing what else it
#: touched. It is simply not the same thing the Action does.
#:
#: `pre-push` is the closer analogue — the moment you are about to hand work to
#: someone else, and the moment a merge commit brings in code you did not
#: write. `sp lgtm install --hook pre-push` gets that, and costs a minute per
#: push rather than a minute per commit.
DEFAULT_HOOK = "pre-commit"

MARKER = "# installed by `sp lgtm install`"


@dataclass
class Result:
    path: Path
    action: str  # "installed" | "replaced" | "removed"


def script(executable: str, blocking: bool) -> str:
    """
    The hook, as POSIX sh.

    Deliberately not `set -e`: every failure here must be inspected and turned
    into a decision, because the one unacceptable outcome is a hook that blocks
    a commit for a reason that has nothing to do with the diff.
    """
    gate = (
        # Only "answered wrong" (1) blocks. "Could not ask" (2) — no API key, a
        # vendor outage, nothing quizzable — must never cost someone a commit.
        'if [ "$status" -eq 1 ]; then\n'
        '  echo "sp lgtm: not confirmed. Commit with --no-verify to override." >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
        if blocking
        else "# Advisory: the answer is reported, the commit proceeds either way.\n"
        "exit 0"
    )

    return f"""#!/bin/sh
{MARKER}. Remove it with `sp lgtm uninstall`.

# Escape hatches, in order of how often you will want them:
#   SP_LGTM_SKIP=1 git commit ...   skip this once
#   git commit --no-verify ...      skip every hook
[ -n "${{SP_LGTM_SKIP:-}}" ] && exit 0

# Git runs hooks with stdin closed. Without a terminal there is nobody to ask,
# and that is a skip, not a failure — a rebase, a GUI client, or CI must not be
# blocked by a question nobody can see.
#
# The test opens /dev/tty rather than asking whether it is readable: with no
# controlling terminal the device node still exists and still looks readable,
# and only the open fails. `[ -r /dev/tty ]` therefore passes and then the
# redirect below dies with "Device not configured" — a scary line printed
# during an otherwise fine commit.
#
# The subshell matters as much as the test does. Redirections are applied left
# to right, so in `: < /dev/tty 2>/dev/null` the failing open happens *before*
# stderr is silenced and the warning escapes anyway. Redirecting the subshell
# silences it first, and the open happens inside.
if ! (exec < /dev/tty) 2>/dev/null; then
  exit 0
fi

{executable} lgtm --staged < /dev/tty
status=$?

{gate}
"""


def install(
    hook: str = DEFAULT_HOOK,
    blocking: bool = False,
    force: bool = False,
    cwd: Path | None = None,
) -> Result:
    """
    Write the hook, refusing to clobber one we did not write.

    Someone else's pre-commit hook is usually load-bearing — a formatter, a
    linter, a secret scanner. Overwriting it silently to install a quiz would be
    a poor trade, so an unrecognised hook is an error with the path in it.
    """
    if hook not in HOOKS:
        raise GitError(f"Unknown hook {hook!r}. Choose one of: {', '.join(HOOKS)}.")

    directory = hooks_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / hook

    replaced = False
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
        ours = MARKER in existing
        if not ours and not force:
            raise GitError(
                f"{path} already exists and was not written by sp. "
                "Move it aside, or pass --force to replace it."
            )
        replaced = True

    path.write_text(script(_executable(), blocking), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return Result(path, "replaced" if replaced else "installed")


def uninstall(hook: str = DEFAULT_HOOK, cwd: Path | None = None) -> Result | None:
    """Remove our hook. Returns None if there was nothing of ours to remove."""
    path = hooks_dir(cwd) / hook
    if not path.exists():
        return None
    if MARKER not in path.read_text(encoding="utf-8", errors="replace"):
        raise GitError(f"{path} was not written by sp — leaving it alone.")
    path.unlink()
    return Result(path, "removed")


def _executable() -> str:
    """
    An absolute path to `sp`, because a hook's PATH is not your shell's.

    Prefers the `sp` script beside the running interpreter; falls back to
    running the module through that interpreter, which works from anywhere
    including a `python -m` invocation.
    """
    candidate = Path(sys.executable).with_name("sp")
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return _quote(str(candidate))
    return f"{_quote(sys.executable)} -m spareparts"


def _quote(path: str) -> str:
    return f'"{path}"' if " " in path else path
