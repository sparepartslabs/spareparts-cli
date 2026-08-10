from __future__ import annotations
import argparse
from pathlib import Path
import pytest
from spareparts.cli import main
from spareparts.modules import plugin
from spareparts.modules.plugin.catalog import load_catalog
from spareparts.modules.plugin.installer import InstallError, InstallResult

def test_top_level_help_and_list(capsys):
    assert main(["--help"]) == 0; assert "plugin" in capsys.readouterr().out
    assert main(["plugin","list"]) == 0
    output=capsys.readouterr().out; assert "lgtm" in output and "lgtm@sparepartslabs" in output

def test_unknown_plugin_is_usage_error(capsys):
    with pytest.raises(SystemExit) as error: main(["plugin","install","unknown"])
    assert error.value.code == 2; assert "invalid choice" in capsys.readouterr().err

@pytest.mark.parametrize("outcome", ["installed","refreshed","unchanged"])
def test_success_output(monkeypatch, capsys, outcome):
    entry=load_catalog()[0]
    monkeypatch.setattr(plugin,"install",lambda entry,refresh=False: InstallResult(entry,outcome,__import__("pathlib").Path("/tmp/root")))
    assert main(["plugin","install","lgtm"] + (["--refresh"] if outcome=="refreshed" else [])) == 0
    output=capsys.readouterr().out; assert outcome in output and entry.install_identity in output and entry.version in output and "new Codex session" in output

def test_install_error_goes_to_stderr(monkeypatch, capsys):
    def fail(*args,**kwargs): raise InstallError("download","offline")
    monkeypatch.setattr(plugin,"install",fail)
    assert main(["plugin","install","lgtm"]) == 1
    captured=capsys.readouterr(); assert captured.out == "" and "download: offline" in captured.err

@pytest.mark.parametrize("phase", ["codex availability","marketplace registration","plugin installation"])
def test_codex_phase_errors_are_exit_one(monkeypatch, capsys, phase):
    def fail(*args,**kwargs): raise InstallError(phase,"boom")
    monkeypatch.setattr(plugin,"install",fail)
    assert main(["plugin","install","lgtm"]) == 1
    captured=capsys.readouterr(); assert captured.out == "" and f"{phase}: boom" in captured.err

def _native_source(): return "---\ndescription: Run LGTM\n---\n\nReview $ARGUMENTS with LGTM.\n"

def test_native_all_uses_exact_ec_agent_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin,"prepare",lambda entry: tmp_path/"cache"); monkeypatch.setattr(plugin,"command_asset",lambda root,entry: _native_source())
    assert main(["plugin","install","lgtm","--all","--dir",str(tmp_path)]) == 0
    expected={"claude":tmp_path/".claude/commands/lgtm.md","codex":tmp_path/".agents/skills/lgtm/SKILL.md","cursor":tmp_path/".cursor/commands/lgtm.md","copilot":tmp_path/".github/prompts/lgtm.prompt.md","gemini":tmp_path/".gemini/commands/lgtm.toml","opencode":tmp_path/".opencode/command/lgtm.md"}
    assert set(expected) == set(plugin.ec_installer.AGENTS)
    for agent,path in expected.items():
        rendered=path.read_text(); assert "Review" in rendered and plugin.ec_installer.AGENTS[agent].arg in rendered

def test_native_repeat_preserves_then_force_replaces(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(plugin,"prepare",lambda entry: tmp_path/"cache"); monkeypatch.setattr(plugin,"command_asset",lambda root,entry: _native_source())
    args=["plugin","install","lgtm","--agent","claude","--dir",str(tmp_path)]
    assert main(args) == 0; target=tmp_path/".claude/commands/lgtm.md"; target.write_text("user content")
    assert main(args) == 0; assert target.read_text() == "user content"; assert "unchanged" in capsys.readouterr().out
    assert main(args+["--force"]) == 0; assert "Review $ARGUMENTS" in target.read_text()

def test_native_repeatable_agents_only_write_selected(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin,"prepare",lambda entry: tmp_path/"cache"); monkeypatch.setattr(plugin,"command_asset",lambda root,entry: _native_source())
    assert main(["plugin","install","lgtm","--agent","cursor","--agent","gemini","--dir",str(tmp_path)]) == 0
    assert (tmp_path/".cursor/commands/lgtm.md").exists() and (tmp_path/".gemini/commands/lgtm.toml").exists()
    assert not (tmp_path/".agents/skills/lgtm/SKILL.md").exists()

def test_native_all_and_agent_are_usage_error(capsys):
    with pytest.raises(SystemExit) as error: main(["plugin","install","lgtm","--all","--agent","codex"])
    assert error.value.code == 2 and "not allowed with argument" in capsys.readouterr().err
