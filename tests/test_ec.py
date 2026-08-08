from __future__ import annotations

from pathlib import Path
import subprocess

from spareparts.cli import main
from spareparts.modules.ec import installer


def _repo(root: Path, name: str, *, claude: bool = True) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if claude:
        (repo / ".claude").mkdir()
    return repo


def test_install_uses_sp_and_constitution_names(tmp_path):
    repo = _repo(tmp_path, "repo")
    assert main(["ec", "install", "--dir", str(repo)]) == 0
    assert (repo / ".sp/memory/constitution.md").exists()
    assert (repo / ".claude/commands/constitution.md").exists()
    assert not (repo / ".blitz").exists()
    assert not (repo / ".claude/commands/playbook.md").exists()
    ignored = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "/.sp/scripts/" in ignored
    assert "/.sp/templates/" in ignored
    assert "/.sp/memory/" not in ignored


def test_install_migrates_blitz_and_playbook(tmp_path):
    repo = _repo(tmp_path, "repo")
    old = repo / ".blitz/memory/playbook.md"
    old.parent.mkdir(parents=True)
    old.write_text("# Team rules\n", encoding="utf-8")
    assert main(["ec", "install", "--dir", str(repo)]) == 0
    constitution = repo / ".sp/memory/constitution.md"
    assert constitution.read_text(encoding="utf-8") == "# Team rules\n"
    assert not (repo / ".blitz").exists()


def test_workspace_install_keeps_huddle_workspace_only(tmp_path):
    first = _repo(tmp_path, "first")
    second = _repo(tmp_path, "second")
    assert main(["ec", "install", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / ".claude/commands/huddle.md").exists()
    assert (tmp_path / ".sp/memory/constitution.md").exists()
    assert not (first / ".claude/commands/huddle.md").exists()
    assert not (second / ".claude/commands/huddle.md").exists()


def test_render_rewrites_spec_kit_paths_and_references():
    rendered = installer.render("plan", "claude")
    assert ".specify" not in rendered
    assert ".sp/" in rendered
    assert "__SPECKIT_COMMAND" not in rendered


def test_install_warns_when_blanket_rule_hides_constitution(tmp_path, capsys):
    repo = _repo(tmp_path, "repo")
    (repo / ".gitignore").write_text(".sp/\n", encoding="utf-8")
    assert main(["ec", "install", "--dir", str(repo)]) == 0
    output = capsys.readouterr().out
    assert "ignores the .sp working area" in output
    assert ".sp/memory/constitution.md must remain trackable" in output
