from dataclasses import replace

from spareparts.modules.lgtm.config import DEFAULTS
from spareparts.modules.lgtm.git import ChangedFile
from spareparts.modules.lgtm.screen import MAX_FILES, screen


def file(name: str, added: int = 5, removed: int = 1) -> ChangedFile:
    return ChangedFile(filename=name, additions=added, deletions=removed)


def test_empty_range_stops():
    assert screen([], DEFAULTS).reason


def test_too_many_files_stops():
    files = [file(f"src/f{i}.ts") for i in range(MAX_FILES + 1)]
    assert screen(files, DEFAULTS).reason


def test_lockfile_only_change_stops():
    result = screen([file("package-lock.json"), file("web/yarn.lock")], DEFAULTS)
    assert result.reason
    assert result.paths == []


def test_exempt_paths_from_config_are_honoured():
    config = replace(DEFAULTS, exempt_paths=("docs/**",))
    assert screen([file("docs/a.md")], config).reason


def test_a_zero_line_change_stops():
    assert screen([file("src/a.ts", 0, 0)], DEFAULTS).reason


def test_real_code_passes_and_drops_the_lockfile():
    result = screen([file("src/charge.ts"), file("package-lock.json")], DEFAULTS)
    assert result.reason is None
    assert result.paths == ["src/charge.ts"]
