"""IDE-aware MCP server configuration for Chowkidar."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .detector import detect_editors, ensure_editor_dirs

CHOWKIDAR_SERVER_NAME = "chowkidar"

EDITOR_MCP_PATHS: dict[str, tuple[Path, str]] = {
    "cursor": (Path(".cursor") / "mcp.json", "mcpServers"),
    "copilot": (Path(".vscode") / "mcp.json", "servers"),
    "claude_code": (Path(".claude") / "settings.json", "mcpServers"),
    "windsurf": (Path(".windsurf") / "mcp.json", "mcpServers"),
}


def resolve_chowkidar_command() -> tuple[str, list[str]]:
    """Return command and args to start the MCP server."""
    binary = shutil.which("chowkidar")
    if binary:
        return binary, ["mcp"]

    scripts_name = "chowkidar.exe" if sys.platform == "win32" else "chowkidar"
    venv_binary = Path(sys.executable).parent / scripts_name
    if venv_binary.exists():
        return str(venv_binary), ["mcp"]

    return sys.executable, ["-m", "chowkidar", "mcp"]


def build_server_entry(project_root: Path) -> dict[str, Any]:
    """Build the stdio MCP server entry for Chowkidar."""
    command, args = resolve_chowkidar_command()
    entry: dict[str, Any] = {
        "command": command,
        "args": args,
        "cwd": str(project_root.resolve()),
    }
    return entry


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _merge_server_config(
    existing: dict[str, Any],
    root_key: str,
    server_entry: dict[str, Any],
    *,
    editor: str,
) -> dict[str, Any]:
    merged = dict(existing)
    if root_key not in merged or not isinstance(merged[root_key], dict):
        merged[root_key] = {}

    entry = dict(server_entry)
    if editor == "copilot":
        entry["type"] = "stdio"

    merged[root_key][CHOWKIDAR_SERVER_NAME] = entry
    return merged


def configure_mcp_for_editor(project_path: Path, editor: str) -> str | None:
    """Write or merge Chowkidar MCP config for a single detected editor."""
    if editor not in EDITOR_MCP_PATHS:
        return None

    rel_path, root_key = EDITOR_MCP_PATHS[editor]
    ensure_editor_dirs(project_path, editor)
    config_path = project_path / rel_path
    existing = _read_json(config_path)
    server_entry = build_server_entry(project_path)
    merged = _merge_server_config(existing, root_key, server_entry, editor=editor)
    _write_json_atomic(config_path, merged)
    return str(rel_path)


def configure_mcp_for_project(project_path: Path) -> list[str]:
    """Configure MCP for all detected editors in the project."""
    project_path = project_path.resolve()
    editors = detect_editors(project_path)
    if not editors:
        editors = ["cursor", "claude_code"]

    written: list[str] = []
    for editor in editors:
        try:
            rel = configure_mcp_for_editor(project_path, editor)
            if rel:
                written.append(rel)
        except Exception:
            continue
    return written


def is_mcp_configured(project_path: Path) -> list[str]:
    """Return relative paths of IDE config files that include Chowkidar."""
    project_path = project_path.resolve()
    configured: list[str] = []
    for editor, (rel_path, root_key) in EDITOR_MCP_PATHS.items():
        config_path = project_path / rel_path
        if not config_path.exists():
            continue
        data = _read_json(config_path)
        servers = data.get(root_key, {})
        if isinstance(servers, dict) and CHOWKIDAR_SERVER_NAME in servers:
            configured.append(str(rel_path))
    return configured


def check_mcp_readiness(project_path: Path | None = None) -> dict[str, Any]:
    """Check whether Chowkidar MCP can start and is configured."""
    from ..config import CHOWKIDAR_HOME, Config

    project_root = (project_path or Path.cwd()).resolve()
    checks: dict[str, Any] = {
        "ready": True,
        "errors": [],
        "warnings": [],
        "sdk_installed": False,
        "home_exists": CHOWKIDAR_HOME.exists(),
        "registry_exists": (CHOWKIDAR_HOME / "registry.db").exists(),
        "binary": resolve_chowkidar_command()[0],
        "configured_files": is_mcp_configured(project_root),
    }

    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        checks["sdk_installed"] = True
    except ImportError:
        checks["ready"] = False
        checks["errors"].append("mcp SDK not installed. Run: pip install chowkidar")

    if not checks["home_exists"]:
        checks["ready"] = False
        checks["errors"].append("Chowkidar home not initialized. Run: chowkidar setup")

    Config.ensure_home()
    if not checks["registry_exists"]:
        checks["warnings"].append("Registry database missing. Run: chowkidar setup && chowkidar sync")

    if not checks["configured_files"]:
        checks["warnings"].append(
            "No IDE MCP config found. Run: chowkidar setup (auto-detects IDE) or see README."
        )

    if not shutil.which("chowkidar") and checks["binary"] == sys.executable:
        checks["warnings"].append(
            "chowkidar not on PATH; MCP config uses python -m chowkidar.cli"
        )

    return checks
