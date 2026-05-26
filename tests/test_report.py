"""Tests for the report generation module."""

import json

import pytest

from chowkidar.registry.db import Registry
from chowkidar.report import generate_report


@pytest.fixture
def project_with_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('MODEL="gpt-3.5-turbo"\n')
    return tmp_path


@pytest.fixture
def registry(tmp_path):
    db_path = tmp_path / "report_test.db"
    reg = Registry(db_path=db_path)
    reg.init_db()
    reg.upsert_model(
        model_id="openai/gpt-3.5-turbo",
        provider="openai",
        sunset_date="2025-01-01",
        replacement="openai/gpt-4o-mini",
    )
    return reg


def test_markdown_report(project_with_env, registry):
    report = generate_report([project_with_env], "markdown", registry)
    assert "gpt-3.5-turbo" in report
    assert "Deprecation Report" in report


def test_json_report(project_with_env, registry):
    report = generate_report([project_with_env], "json", registry)
    data = json.loads(report)
    assert "projects" in data
    assert len(data["projects"]) == 1
    assert data["projects"][0]["models"][0]["model"] == "gpt-3.5-turbo"


def test_html_report(project_with_env, registry):
    report = generate_report([project_with_env], "html", registry)
    assert "<html" in report
    assert "gpt-3.5-turbo" in report
    assert "Chowkidar" in report
    assert "highlight-var" in report
    assert "highlight-model" in report
    assert "Open in Editor" in report


def test_report_empty_project(tmp_path, registry):
    report = generate_report([tmp_path], "markdown", registry)
    assert "No deprecated models" in report


def test_report_multiple_projects(project_with_env, tmp_path, registry):
    other = tmp_path / "other_proj"
    other.mkdir()
    (other / ".env").write_text('M="gpt-4o"\n')
    report = generate_report([project_with_env, other], "markdown", registry)
    assert "Deprecation Report" in report

def test_report_with_sync_status(project_with_env, registry):
    registry.log_sync_success("openai")
    registry.log_sync_failure("anthropic", "Connection Timeout")
    
    report_json = generate_report([project_with_env], "json", registry)
    data = json.loads(report_json)
    assert "sync_statuses" in data
    assert "openai" in data["sync_statuses"]
    assert data["sync_statuses"]["openai"]["last_success_at"] is not None
    assert data["sync_statuses"]["openai"]["failure_reason"] is None
    assert "anthropic" in data["sync_statuses"]
    assert data["sync_statuses"]["anthropic"]["failure_reason"] == "Connection Timeout"

    report_md = generate_report([project_with_env], "markdown", registry)
    assert "Provider Sync Status" in report_md
    assert "Connection Timeout" in report_md

    report_html = generate_report([project_with_env], "html", registry)
    assert "Provider Sync Status" in report_html
    assert "Connection Timeout" in report_html


def test_report_html_xss_escaping(project_with_env, registry):
    # Log a sync failure with special HTML characters (that aren't stripped by tag removal)
    import html
    malicious_reason = "Error with characters: & \" '"
    registry.log_sync_failure("anthropic", malicious_reason)
    
    report_html = generate_report([project_with_env], "html", registry)
    expected = html.escape(malicious_reason)
    assert expected in report_html


def test_report_path_redaction(project_with_env, registry):
    report_redacted = generate_report([project_with_env], "markdown", registry, redact_paths=True)
    # The absolute parent paths (like /var/folders/ or /Users/) should be redacted
    assert str(project_with_env) not in report_redacted
    assert "[REDACTED]" in report_redacted
