"""Sanitized, coalesced huddle progress for source-issue writeback."""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import BuildError

_STATUS = re.compile(r"^\*\*Status\*\*:\s*(active|blocked|complete)\b", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class HuddleRow:
    repository: str
    spec: str
    stage: str


@dataclass(frozen=True)
class HuddleSnapshot:
    path: Path
    digest: str
    status: str
    rows: tuple[HuddleRow, ...]


def _rows(text: str) -> tuple[HuddleRow, ...]:
    rows: list[HuddleRow] = []
    in_table = False
    for line in text.splitlines():
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0].lower() == "repo" and cells[2].lower() == "spec":
            in_table = True
            continue
        if not in_table:
            continue
        if not line.lstrip().startswith("|") or len(cells) < 4:
            break
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(HuddleRow(cells[0][:200], cells[2][:300], cells[3][:200]))
    return tuple(rows)


def discover(root: Path) -> HuddleSnapshot | None:
    paths = sorted(root.glob(".sp/huddles/*/huddle.md"))
    if not paths:
        return None
    if len(paths) != 1:
        raise BuildError("workspace must contain exactly one huddle", "rejected")
    text = paths[0].read_text(encoding="utf-8", errors="replace")
    match = _STATUS.search(text)
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"
    return HuddleSnapshot(paths[0], hashlib.sha256(normalized.encode()).hexdigest(), match.group(1).lower() if match else "unknown", _rows(text))


def render(marker: str, snapshot: HuddleSnapshot | None, agent: str, model: str | None) -> str:
    lines = [marker, "", "## Spare Parts build huddle", "", f"Agent: `{agent}`" + (f" / `{model}`" if model else ""), ""]
    if snapshot is None:
        lines += ["**Status:** preparing", "", "Waiting for the workspace huddle to be created."]
        return "\n".join(lines)
    lines += [f"**Status:** {snapshot.status}", "", f"Huddle: `{snapshot.path.parent.name}`", ""]
    if snapshot.rows:
        lines += ["| Repository | Spec | Stage |", "|---|---|---|"]
        for row in snapshot.rows[:20]:
            values = [value.replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ") for value in (row.repository, row.spec, row.stage)]
            lines.append(f"| {values[0]} | {values[1]} | {values[2]} |")
    return "\n".join(lines)[:20000]


class HuddleMonitor:
    def __init__(self, root: Path, publish: Callable[[str], None], marker: str, agent: str, model: str | None, interval: float = 2.0):
        self.root, self.publish, self.marker = root, publish, marker
        self.agent, self.model, self.interval = agent, model, interval
        self._digest: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.warnings: list[str] = []

    def sync(self, *, initial: bool = False) -> HuddleSnapshot | None:
        try:
            snapshot = discover(self.root)
            digest = snapshot.digest if snapshot else "preparing"
            if initial or digest != self._digest:
                self.publish(render(self.marker, snapshot, self.agent, self.model))
                self._digest = digest
            return snapshot
        except (BuildError, OSError) as error:
            warning = str(error)[:500]
            if warning not in self.warnings:
                self.warnings.append(warning)
            return None

    def start(self) -> None:
        self.sync(initial=True)
        self._thread = threading.Thread(target=self._run, name="sp-huddle-progress", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.sync()

    def stop(self) -> HuddleSnapshot | None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(1.0, self.interval * 2))
        return self.sync()
