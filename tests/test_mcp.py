"""Tests for MCP server readiness and IDE configuration (TS03_TC_29)."""

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from chowkidar.cli import app
from chowkidar.ide.mcp_config import (
    CHOWKIDAR_SERVER_NAME,
    build_server_entry,
    check_mcp_readiness,
    configure_mcp_for_editor,
    configure_mcp_for_project,
    is_mcp_configured,
)


runner = CliRunner()


def test_mcp_sdk_importable():
    from mcp.server.fastmcp import FastMCP  # noqa: F401


def test_configure_mcp_cursor(tmp_path):
    (tmp_path / ".cursor").mkdir()
    written = configure_mcp_for_editor(tmp_path, "cursor")
    assert written == ".cursor/mcp.json"

    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert CHOWKIDAR_SERVER_NAME in data["mcpServers"]
    assert data["mcpServers"][CHOWKIDAR_SERVER_NAME]["args"] == ["mcp"]


def test_configure_mcp_vscode(tmp_path):
    (tmp_path / ".vscode").mkdir()
    written = configure_mcp_for_editor(tmp_path, "copilot")
    assert written == ".vscode/mcp.json"

    data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert CHOWKIDAR_SERVER_NAME in data["servers"]
    assert data["servers"][CHOWKIDAR_SERVER_NAME]["type"] == "stdio"


def test_configure_mcp_claude_code(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"other_setting": True}),
        encoding="utf-8",
    )
    written = configure_mcp_for_editor(tmp_path, "claude_code")
    assert written == ".claude/settings.json"

    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert data["other_setting"] is True
    assert CHOWKIDAR_SERVER_NAME in data["mcpServers"]


def test_configure_mcp_merge_preserves_existing(tmp_path):
    (tmp_path / ".cursor").mkdir()
    existing = {
        "mcpServers": {
            "other-server": {"command": "npx", "args": ["other-mcp"]},
        }
    }
    (tmp_path / ".cursor" / "mcp.json").write_text(json.dumps(existing), encoding="utf-8")

    configure_mcp_for_editor(tmp_path, "cursor")
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "other-server" in data["mcpServers"]
    assert CHOWKIDAR_SERVER_NAME in data["mcpServers"]


def test_configure_mcp_for_project_detects_cursor(tmp_path):
    (tmp_path / ".cursor").mkdir()
    written = configure_mcp_for_project(tmp_path)
    assert ".cursor/mcp.json" in written


def test_is_mcp_configured(tmp_path):
    (tmp_path / ".cursor").mkdir()
    configure_mcp_for_editor(tmp_path, "cursor")
    configured = is_mcp_configured(tmp_path)
    assert ".cursor/mcp.json" in configured


def test_check_mcp_readiness_with_home(tmp_path):
    chowkidar_home = tmp_path / ".chowkidar"
    chowkidar_home.mkdir()
    (chowkidar_home / "config.toml").write_text("auto_update = false\n", encoding="utf-8")
    (chowkidar_home / "registry.db").write_bytes(b"")

    with patch("chowkidar.config.CHOWKIDAR_HOME", chowkidar_home):
        result = check_mcp_readiness(tmp_path)
    assert result["home_exists"] is True
    assert result["sdk_installed"] is True


def test_mcp_verify_exits_zero_when_ready(tmp_path, monkeypatch):
    chowkidar_home = tmp_path / ".chowkidar"
    chowkidar_home.mkdir()
    (chowkidar_home / "config.toml").write_text("auto_update = false\n", encoding="utf-8")

    from chowkidar.registry.db import Registry
    registry = Registry(chowkidar_home / "registry.db")
    registry.init_db()
    registry.close()

    (tmp_path / ".cursor").mkdir()
    configure_mcp_for_editor(tmp_path, "cursor")
    monkeypatch.chdir(tmp_path)

    with patch("chowkidar.cli.CHOWKIDAR_HOME", chowkidar_home), \
         patch("chowkidar.config.CHOWKIDAR_HOME", chowkidar_home), \
         patch("chowkidar.registry.db.CHOWKIDAR_HOME", chowkidar_home):
        result = runner.invoke(app, ["mcp", "--verify"], catch_exceptions=False)

    assert result.exit_code == 0
    combined = (result.stderr or "") + (result.output or "")
    assert "ready" in combined.lower()


def test_mcp_auto_update_error_message():
    from chowkidar.mcp_server.server import update_model_env

    with patch("chowkidar.mcp_server.server._get_config") as mock_cfg:
        mock_cfg.return_value.get.return_value = False
        result = update_model_env("/tmp/.env", "OPENAI_MODEL", "gpt-4o-mini")
    assert "chowkidar config auto_update true" in result
    assert "config set" not in result


def test_build_server_entry_includes_cwd(tmp_path):
    entry = build_server_entry(tmp_path)
    assert entry["cwd"] == str(tmp_path.resolve())
    assert entry["args"] == ["mcp"]


def test_mcp_server_stays_running_briefly():
    proc = subprocess.Popen(
        [sys.executable, "-m", "chowkidar", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        time.sleep(1)
        assert proc.poll() is None
        assert proc.stdout.read(0) == b""
    finally:
        proc.terminate()
        proc.wait(timeout=5)
