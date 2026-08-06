"""
The cheap pre-filter, run on the file list before any model call.

Its job is to settle the cases where quizzing is wrong no matter what a model
would produce: a 4,000-file dependency bump, a change of nothing but lockfiles,
one confined to paths the repo exempted. Deciding those from the file list keeps
the common skip fast, free, and deterministic.

It deliberately does NOT decide whether a diff is *interesting*. That needs the
hunks, and it belongs to the generator — which is allowed to conclude that a
quizzable-looking change has nothing worth asking about.

Ported from `src/questions.ts` in `sparepartslabs/spareparts-lgtm`.
"""

from __future__ import annotations

from .config import Config, matches_any
from .git import ChangedFile

#: Files nobody reads line by line; quizzing them teaches the wrong lesson.
GENERATED = (
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/Cargo.lock",
    "**/go.sum",
    "**/Package.resolved",
    "**/*.pbxproj",
    "**/*.snap",
    "**/dist/**",
    "**/build/**",
    "**/vendor/**",
    "**/*.generated.*",
)

MAX_FILES = 60


class Screened:
    """Either a reason to stop, or the paths worth sending on."""

    def __init__(self, reason: str | None, paths: list[str]):
        self.reason = reason
        self.paths = paths


def screen(files: list[ChangedFile], config: Config) -> Screened:
    if not files:
        return Screened("Nothing changed in that range.", [])
    if len(files) > MAX_FILES:
        return Screened(
            f"That range touches {len(files)} files — too large to quiz meaningfully.",
            [],
        )

    quizzable = [
        f
        for f in files
        if not matches_any(f.filename, GENERATED)
        and not matches_any(f.filename, config.exempt_paths)
    ]
    if not quizzable:
        return Screened(
            "Everything in that range is generated or exempt — nothing to ask about.", []
        )

    if sum(f.additions + f.deletions for f in quizzable) == 0:
        return Screened("That range changes no lines in reviewable files.", [])

    return Screened(None, [f.filename for f in quizzable])
