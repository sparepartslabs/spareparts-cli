"""
Just enough unified-diff parsing to hold the generator honest.

Every question must cite the file and hunk it was drawn from, and a question
that cannot be grounded is dropped. A prompt asking the model to cite its source
gets citations; it does not get *correct* citations. So the citation is checked
here, against the actual diff: a question naming a file the change didn't touch,
or a hunk header that doesn't appear in that file, is dropped before it can
reach anyone.

This is the difference between the model promising it read the diff and the code
confirming it.

Ported from the TypeScript in `sparepartslabs/spareparts-lgtm` (`src/diff.ts`).
Behaviour is intended to match line for line; if you fix a bug here, fix it
there too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


@dataclass
class Hunk:
    #: The `@@ -a,b +c,d @@` line, verbatim. Used as the citation key.
    header: str
    #: Body lines, including the leading " ", "+", or "-".
    lines: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    #: Post-image path (the `+++ b/...` side), or the pre-image if deleted.
    path: str
    hunks: list[Hunk] = field(default_factory=list)


def parse_diff(diff: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None

    for line in diff.split("\n"):
        file_match = FILE_RE.match(line)
        if file_match:
            current = FileDiff(path=file_match.group(2))
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue

        if HUNK_RE.match(line):
            hunk = Hunk(header=line.strip())
            current.hunks.append(hunk)
            continue

        # `--- a/x` and `+++ b/x` precede the first hunk; skip them so they
        # aren't mistaken for content. Once a hunk is open, every line is its.
        if hunk is None:
            continue
        if line.startswith("\\"):  # "\ No newline at end of file"
            continue
        hunk.lines.append(line)

    return files


def changed_lines(file: FileDiff) -> int:
    """Added and removed lines only — what the change actually changed."""
    return sum(
        1
        for hunk in file.hunks
        for line in hunk.lines
        if line.startswith("+") or line.startswith("-")
    )


def _hunk_key(header: str) -> str | None:
    match = HUNK_RE.match(header.strip())
    return match.group(0) if match else None


def is_grounded(files: list[FileDiff], path: str, header: str) -> bool:
    """
    Does this (file, hunk) citation name something that exists?

    The hunk match is a prefix comparison on the `@@ ... @@` portion: models
    reproduce the ranges reliably but often drop or reword the trailing section
    heading git appends, and failing a correct question over that would be
    pedantry. The ranges are what identify the hunk.
    """
    file = next((f for f in files if f.path == path), None)
    if file is None:
        return False
    key = _hunk_key(header)
    if key is None:
        return False
    return any(_hunk_key(h.header) == key for h in file.hunks)


def render_for_prompt(files: list[FileDiff], budget: int) -> tuple[str, list[FileDiff]]:
    """
    The diff, trimmed to what is worth asking about, with each hunk labelled so
    the model has a citation key to return.

    Files are dropped rather than truncated — a half-included file produces
    questions about code the reader can't see in the citation.

    Returns (rendered text, the files actually included).
    """
    out: list[str] = []
    included: list[FileDiff] = []
    used = 0

    # Most-changed first: if the budget runs out, it runs out on the files least
    # likely to carry the point of the change.
    for file in sorted(files, key=changed_lines, reverse=True):
        block = "\n".join(
            [f"### {file.path}"]
            + ["\n".join([h.header, *h.lines]) for h in file.hunks]
        )
        if used + len(block) > budget:
            continue
        out.append(block)
        included.append(file)
        used += len(block)

    return "\n\n".join(out), included
