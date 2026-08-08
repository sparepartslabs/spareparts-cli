from __future__ import annotations

import json

from spareparts.cli import main
from spareparts.modules.ec import doctor


def test_linear_mcp_detection_names_config_file(tmp_path):
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"linear": {"command": "linear-mcp"}}}),
        encoding="utf-8",
    )
    assert doctor.linear_mcp_files(tmp_path, home=tmp_path / "home") == [config]


def test_doctor_gives_actionable_missing_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    output = doctor.render(tmp_path)
    assert "Run: sp ec project setup" in output
    assert "Install gh and authenticate" in output
    assert "Configure Linear MCP or set LINEAR_API_KEY" in output


def test_noninteractive_linear_setup(tmp_path, capsys):
    result = main(
        [
            "ec", "project", "setup",
            "--provider", "linear",
            "--url", "https://linear.app/spare-parts-labs",
            "--team", "Engineering",
            "--transport", "mcp",
            "--dir", str(tmp_path),
        ]
    )
    assert result == 0
    document = json.loads(
        (tmp_path / ".sp/integrations.json").read_text(encoding="utf-8")
    )
    assert document["work_management"]["workspace"] == "spare-parts-labs"
    assert "sp ec doctor" in capsys.readouterr().out
