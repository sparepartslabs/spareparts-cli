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

#: `pre-push` — the last point the work is still yours alone.
#:
#: LGTM's premise used to be stated as "the person answering did not write the
#: code". That framing is dated: when a model wrote the diff, nobody in the
#: loop wrote it, and the author is as much a reader as any reviewer. The
#: useful question is not who typed it but *where the last cheap moment to
#: catch it is* — and that is before it leaves the machine, not after a
#: reviewer has spent attention on it.
#:
#: pre-push is that moment, and it is left of everything the Action can do.
#: It also matches how the cost lands: a minute per push, not per commit, so
#: the ten-commit afternoon is quizzed once rather than ten times.
#:
#: pre-commit remains available and is a fair proofreading pass — it catches
#: what your change touched that you did not notice — but it charges a minute
#: every time you save a checkpoint, which is how hooks get uninstalled.
DEFAULT_HOOK = "pre-push"

MARKER = "# installed by `sp lgtm install`"


@dataclass
class Result:
    path: Path
    action: str  # "installed" | "replaced" | "removed"


#: How each hook names the work it is about to let through.
#:
#: They could not be less alike, and using one for the other is silent: at
#: pre-push nothing is staged, so a `--staged` pre-push hook quizzes an empty
#: diff, exits "nothing to ask about", and passes every push forever while
#: looking installed.
_SUBJECT = {
    # The index is the only place the pending commit exists.
    "pre-commit": "--staged",
    # Whereas a push already has commits, and git says which on stdin.
    #
    # Unquoted on purpose: when `range` is empty — a branch the remote has
    # never seen — it must vanish so the CLI falls back to its own default,
    # not arrive as an empty argument that parses as a revspec of "".
    "pre-push": "$range",
}


def _range_block() -> str:
    """
    Work out what is about to be pushed, from what git puts on stdin.

    git feeds a pre-push hook one line per ref:

        <local ref> <local sha> <remote ref> <remote sha>

    This has to be read *before* /dev/tty is attached, because it arrives on
    the same stdin the quiz later needs — consume it first, then swap.

    An all-zero remote sha means the branch is new there, so there is no
    `remote..local` range to take; the empty string falls through to the CLI's
    own default (what this branch adds since it left the default branch), which
    is the right answer for a first push. An all-zero *local* sha is a branch
    deletion, which has nothing to read.
    """
    return """# Read git's ref list before /dev/tty takes over stdin.
range=""
found=""
while read -r _local_ref local_sha _remote_ref remote_sha; do
  case "$local_sha" in *[!0]*) ;; *) continue ;; esac
  case "$remote_sha" in
    *[!0]*) range="$remote_sha..$local_sha" ;;
    *) range="" ;;
  esac
  found=1
  break
done

# `found` is not the same question as `range`. An empty range means "new
# branch, use the default" and must go ahead; no ref at all — a push that only
# deletes a remote branch, or pushes nothing — means there is nothing to read,
# and falling through would quiz the current branch for no reason.
[ -n "$found" ] || exit 0"""


def script(executable: str, blocking: bool, hook: str = DEFAULT_HOOK) -> str:
    """
    The hook, as POSIX sh.

    Deliberately not `set -e`: every failure here must be inspected and turned
    into a decision, because the one unacceptable outcome is a hook that blocks
    for a reason that has nothing to do with the diff.
    """
    verb = "Commit" if hook == "pre-commit" else "Push"
    gate = (
        # Only "answered wrong" (1) blocks. "Could not ask" (2) — no API key, a
        # vendor outage, nothing quizzable — must never cost anyone a push.
        'if [ "$status" -eq 1 ]; then\n'
        f'  echo "sp lgtm: not confirmed. {verb} with --no-verify to override." >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
        if blocking
        else f"# Advisory: the answer is reported, the {verb.lower()} proceeds either way.\n"
        "exit 0"
    )

    prelude = f"\n{_range_block()}\n" if hook == "pre-push" else ""
    subject = _SUBJECT[hook]

    return f"""#!/bin/sh
{MARKER}. Remove it with `sp lgtm uninstall`.

# Escape hatches, in order of how often you will want them:
#   SP_LGTM_SKIP=1 git {verb.lower()} ...   skip this once
#   git {verb.lower()} --no-verify ...      skip every hook
[ -n "${{SP_LGTM_SKIP:-}}" ] && exit 0
{prelude}

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

{executable} lgtm {subject} < /dev/tty
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

    path.write_text(script(_executable(), blocking, hook), encoding="utf-8")
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
