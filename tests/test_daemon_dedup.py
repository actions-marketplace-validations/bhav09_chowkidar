"""Integration tests for daemon notification deduplication (TS02_TC_19)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chowkidar.config import Config
from chowkidar.registry.db import Registry
from chowkidar.sentinel.daemon import ChowkidarDaemon


@pytest.fixture
def project_env(tmp_path):
    chowkidar_home = tmp_path / ".chowkidar"
    chowkidar_home.mkdir()
    config = Config(chowkidar_home / "config.toml")
    config.save()

    registry = Registry(chowkidar_home / "registry.db")
    registry.init_db()

    project_path = str(tmp_path)
    sunset = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=14)).strftime("%Y-%m-%d")
    registry.upsert_model(
        model_id="openai/gpt-3.5-turbo",
        provider="openai",
        sunset_date=sunset,
        replacement="openai/gpt-4o-mini",
    )
    registry.watch_project(project_path)

    scan_result = MagicMock()
    scan_result.total_count = 1
    scan_result.all_models = [
        {
            "variable": "OPENAI_MODEL",
            "file": str(tmp_path / ".env"),
            "model": "gpt-3.5-turbo",
            "canonical": "openai/gpt-3.5-turbo",
            "source_type": "env",
        }
    ]

    return {
        "tmp_path": tmp_path,
        "chowkidar_home": chowkidar_home,
        "config": config,
        "registry": registry,
        "project_path": project_path,
        "scan_result": scan_result,
    }


def _make_daemon(env, registry):
    with patch("chowkidar.sentinel.daemon.CHOWKIDAR_HOME", env["chowkidar_home"]), \
         patch("chowkidar.sentinel.daemon.Registry", return_value=registry):
        return ChowkidarDaemon(env["config"])


@patch("chowkidar.sentinel.daemon.write_rules_for_project")
@patch("chowkidar.sentinel.daemon.get_project_advisory", return_value=[])
@patch("chowkidar.deployment.detect_deployment")
@patch("chowkidar.sentinel.daemon.scan_directory")
@patch("chowkidar.sentinel.daemon.notify", return_value=True)
@patch("chowkidar.report.generate_report", return_value="<html></html>")
def test_check_project_suppresses_duplicate_scan(
    mock_generate_report,
    mock_notify,
    mock_scan,
    mock_deploy,
    mock_advisory,
    mock_rules,
    project_env,
):
    env = project_env
    mock_scan.return_value = env["scan_result"]
    mock_deploy.return_value = MagicMock(state="none", signals=[])

    daemon = _make_daemon(env, env["registry"])
    daemon._check_project(env["project_path"])
    daemon._check_project(env["project_path"])

    mock_notify.assert_called_once()
    mock_generate_report.assert_called_once()

    rows = env["registry"].conn.execute(
        "SELECT delivery_status FROM notification_log WHERE project_path = ?",
        (env["project_path"],),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["delivery_status"] == "delivered"


@patch("chowkidar.sentinel.daemon.write_rules_for_project")
@patch("chowkidar.sentinel.daemon.get_project_advisory", return_value=[])
@patch("chowkidar.deployment.detect_deployment")
@patch("chowkidar.sentinel.daemon.scan_directory")
@patch("chowkidar.sentinel.daemon.notify", return_value=True)
@patch("chowkidar.report.generate_report", return_value="<html></html>")
def test_check_project_different_threshold_renotifies(
    mock_generate_report,
    mock_notify,
    mock_scan,
    mock_deploy,
    mock_advisory,
    mock_rules,
    project_env,
):
    env = project_env
    mock_scan.return_value = env["scan_result"]
    mock_deploy.return_value = MagicMock(state="none", signals=[])

    daemon = _make_daemon(env, env["registry"])
    daemon._check_project(env["project_path"])

    env["registry"].conn.execute(
        "UPDATE notification_log SET threshold = '30d' WHERE project_path = ?",
        (env["project_path"],),
    )
    env["registry"].conn.commit()

    sunset = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5)).strftime("%Y-%m-%d")
    env["registry"].upsert_model(
        model_id="openai/gpt-3.5-turbo",
        provider="openai",
        sunset_date=sunset,
        replacement="openai/gpt-4o-mini",
    )

    daemon._check_project(env["project_path"])

    assert mock_notify.call_count == 2


@patch("chowkidar.sentinel.daemon.write_rules_for_project")
@patch("chowkidar.sentinel.daemon.get_project_advisory", return_value=[])
@patch("chowkidar.deployment.detect_deployment")
@patch("chowkidar.sentinel.daemon.scan_directory")
@patch("chowkidar.sentinel.daemon.notify", return_value=True)
@patch("chowkidar.report.generate_report", return_value="<html></html>")
def test_folder_guard_skips_when_all_notified(
    mock_generate_report,
    mock_notify,
    mock_scan,
    mock_deploy,
    mock_advisory,
    mock_rules,
    project_env,
):
    env = project_env
    mock_scan.return_value = env["scan_result"]
    mock_deploy.return_value = MagicMock(state="none", signals=[])

    env["registry"].log_notification(
        env["project_path"],
        "openai/gpt-3.5-turbo",
        "15d",
        file_path=str(env["tmp_path"] / ".env"),
        variable_name="OPENAI_MODEL",
    )

    daemon = _make_daemon(env, env["registry"])
    daemon._check_project(env["project_path"])

    mock_notify.assert_not_called()
    mock_generate_report.assert_not_called()
