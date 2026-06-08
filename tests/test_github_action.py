"""Tests for the Chowkidar GitHub Action integration."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from chowkidar.github_action import run_action


@pytest.fixture
def mock_registry():
    with patch("chowkidar.github_action.Registry") as mock_cls:
        mock_reg = MagicMock()
        mock_cls.return_value = mock_reg
        
        # Mock get_model to return a deprecated/sunset model record
        mock_record = MagicMock()
        mock_record.sunset_date = "2026-05-01T00:00:00Z"
        mock_reg.get_model.return_value = mock_record
        
        yield mock_reg


@pytest.fixture
def mock_scan_directory():
    with patch("chowkidar.github_action.scan_directory") as mock:
        mock_result = MagicMock()
        mock_result.all_models = [
            {
                "file": ".env",
                "variable": "OPENAI_MODEL",
                "model": "gpt-3.5-turbo",
                "canonical": "openai/gpt-3.5-turbo",
                "source_type": "env",
            }
        ]
        mock.return_value = mock_result
        yield mock


def test_github_action_runs_successfully(mock_registry, mock_scan_directory, capsys):
    # Set up environment variables
    env_patch = {
        "CHOWKIDAR_SECRETS_JSON": '{"MY_SECRET_KEY": "gpt-3.5-turbo"}',
        "CHOWKIDAR_VARS_JSON": '{"MY_VAR": "gpt-4o"}',
        "CHOWKIDAR_GATE": "false",
        "CHOWKIDAR_ENVIRONMENT": "staging",
    }
    
    with patch.dict(os.environ, env_patch), \
         patch.object(sys, "argv", ["github_action.py", "--path", "."]):
        run_action()
        
        captured = capsys.readouterr()
        
        # Verify console output
        assert "Chowkidar LLM Deprecation Watchdog" in captured.out
        assert "Environment: staging" in captured.out
        assert "Auditing" in captured.out
        
        # Verify GitHub workflow annotations are printed
        assert "::error file=.env,line=1::" in captured.out
        assert "::error::[Chowkidar] Model '[REDACTED]'" in captured.out


def test_github_action_gates_build(mock_registry, mock_scan_directory):
    env_patch = {
        "CHOWKIDAR_GATE": "true",
    }
    
    with patch.dict(os.environ, env_patch), \
         patch.object(sys, "argv", ["github_action.py", "--path", "."]), \
         pytest.raises(SystemExit) as exc_info:
        run_action()
        
    assert exc_info.value.code == 1
