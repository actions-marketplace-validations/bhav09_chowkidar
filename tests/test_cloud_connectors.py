"""Tests for cloud advisory connectors and hybrid classification helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from chowkidar.advisor import get_project_advisory
from chowkidar.cloud_connectors import (
    AnthropicConnector,
    GeminiConnector,
    MistralConnector,
    ModelReference,
    OpenAIConnector,
    build_classification_payload,
    build_model_references,
    extract_context_snippet,
    get_cloud_connector,
    normalize_use_case,
    parse_classification_response,
    redact_secrets,
    resolve_api_key,
)
from chowkidar.config import Config
from chowkidar.registry.db import Registry


def test_redact_secrets_masks_api_keys():
    text = "api_key = 'sk-abcdefghijklmnopqrstuvwxyz1234567890'"
    redacted = redact_secrets(text)
    assert "sk-abc" not in redacted
    assert "[REDACTED]" in redacted


def test_extract_context_snippet_reads_surrounding_lines(tmp_path):
    source = tmp_path / "src" / "chat.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "line0\nline1\nline2\nline3\nline4\n"
        "model = os.environ.get('CHAT_MODEL', 'gpt-3.5-turbo')\n"
        "line6\nline7\nline8\n"
    )

    snippet = extract_context_snippet(str(tmp_path), "src/chat.py", "CHAT_MODEL", "gpt-3.5-turbo")
    assert "CHAT_MODEL" in snippet
    assert "line3" in snippet
    assert "line4" in snippet
    assert "line6" in snippet
    assert "line0" not in snippet


def test_extract_context_snippet_redacts_secrets(tmp_path):
    source = tmp_path / ".env"
    source.write_text("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234567890\nMODEL=gpt-4o\n")

    snippet = extract_context_snippet(str(tmp_path), ".env", "MODEL", "gpt-4o")
    assert "sk-abc" not in snippet
    assert "[REDACTED]" in snippet


def test_build_classification_payload_batches_references():
    refs = [
        ModelReference("CHAT_MODEL", "src/chat.py", "gpt-3.5-turbo", "context snippet"),
        ModelReference("EMBED_MODEL", "src/search.py", "text-embedding-ada-002", "embed context"),
    ]
    payload = build_classification_payload("/my/project", refs)
    assert payload["project"] == "project"
    assert len(payload["references"]) == 2
    assert payload["references"][0]["variable"] == "CHAT_MODEL"


def test_parse_classification_response_validates_shape():
    raw = json.dumps({
        "classifications": [
            {
                "variable": "CODE_MODEL",
                "file": "src/agent.py",
                "use_case": "coding",
                "reasoning": "Variable name indicates codegen usage.",
            }
        ]
    })
    parsed = parse_classification_response(raw)
    assert parsed is not None
    assert parsed[0].use_case == "coding"


def test_normalize_use_case_aliases():
    assert normalize_use_case("embeddings") == "embeddings/search"
    assert normalize_use_case("agents/reasoning") == "agents/reasoning"
    assert normalize_use_case("unknown-purpose") == "chat/general"


def test_resolve_api_key_prefers_chowkidar_env(monkeypatch):
    monkeypatch.setenv("CHOWKIDAR_OPENAI_API_KEY", "chowkidar-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert resolve_api_key("openai") == "chowkidar-key"


def test_get_cloud_connector_disabled_by_default(tmp_path):
    config = Config(tmp_path / "config.toml")
    assert get_cloud_connector(config) is None


def test_get_cloud_connector_missing_api_key(tmp_path, monkeypatch):
    config = Config(tmp_path / "config.toml")
    config.set("cloud_advisory_enabled", True)
    monkeypatch.delenv("CHOWKIDAR_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_cloud_connector(config) is None


@respx.mock
def test_openai_connector_batches_and_parses(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOWKIDAR_OPENAI_API_KEY", "test-key")
    config = Config(tmp_path / "config.toml")
    config.set("cloud_advisory_enabled", True)
    config.set("cloud_advisory_provider", "openai")

    response_body = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "classifications": [{
                        "variable": "CHAT_MODEL",
                        "file": "src/chat.py",
                        "use_case": "coding",
                        "reasoning": "Codegen context.",
                    }]
                })
            }
        }]
    }
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=response_body)
    )

    connector = get_cloud_connector(config)
    assert connector is not None
    refs = [ModelReference("CHAT_MODEL", "src/chat.py", "gpt-3.5-turbo", "model = 'gpt-3.5-turbo'")]
    result = connector.classify_purposes("/my/project", refs)

    assert route.called
    request = route.calls[0].request
    body = json.loads(request.content.decode())
    assert body["model"] == "gpt-4o-mini"
    assert result is not None
    assert result[0].use_case == "coding"


@respx.mock
def test_openai_connector_timeout_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOWKIDAR_OPENAI_API_KEY", "test-key")
    config = Config(tmp_path / "config.toml")
    config.set("cloud_advisory_enabled", True)
    config.set("cloud_advisory_timeout_seconds", 0.001)

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    connector = OpenAIConnector(api_key="test-key", model="gpt-4o-mini", timeout=0.001)
    refs = [ModelReference("CHAT_MODEL", "src/chat.py", "gpt-3.5-turbo", "context")]
    assert connector.classify_purposes("/my/project", refs) is None


@respx.mock
def test_anthropic_connector_handles_5xx(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOWKIDAR_ANTHROPIC_API_KEY", "test-key")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(503, text="service unavailable")
    )

    connector = AnthropicConnector(api_key="test-key", model="claude-3-5-haiku-20241022", timeout=5.0)
    refs = [ModelReference("CHAT_MODEL", "src/chat.py", "gpt-3.5-turbo", "context")]
    assert connector.classify_purposes("/my/project", refs) is None


@respx.mock
def test_gemini_connector_parses_response(monkeypatch):
    monkeypatch.setenv("CHOWKIDAR_GOOGLE_API_KEY", "test-key")
    response_body = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "classifications": [{
                            "variable": "EMBED_MODEL",
                            "file": "src/search.py",
                            "use_case": "embeddings/search",
                            "reasoning": "Embedding model reference.",
                        }]
                    })
                }]
            }
        }]
    }
    respx.post(url__regex=r"https://generativelanguage\.googleapis\.com/.*").mock(
        return_value=httpx.Response(200, json=response_body)
    )

    connector = GeminiConnector(api_key="test-key", model="gemini-2.0-flash", timeout=5.0)
    refs = [ModelReference("EMBED_MODEL", "src/search.py", "text-embedding-ada-002", "context")]
    result = connector.classify_purposes("/my/project", refs)
    assert result is not None
    assert result[0].use_case == "embeddings/search"


@respx.mock
def test_mistral_connector_parses_response(monkeypatch):
    monkeypatch.setenv("CHOWKIDAR_MISTRAL_API_KEY", "test-key")
    response_body = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "classifications": [{
                        "variable": "AGENT_MODEL",
                        "file": "src/agent.py",
                        "use_case": "agents/reasoning",
                        "reasoning": "Agent orchestration context.",
                    }]
                })
            }
        }]
    }
    respx.post("https://api.mistral.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=response_body)
    )

    connector = MistralConnector(api_key="test-key", model="mistral-small-latest", timeout=5.0)
    refs = [ModelReference("AGENT_MODEL", "src/agent.py", "mistral-large", "context")]
    result = connector.classify_purposes("/my/project", refs)
    assert result is not None
    assert result[0].use_case == "agents/reasoning"


@patch("chowkidar.advisor._load_cache")
@patch("chowkidar.advisor._save_cache")
@patch("chowkidar.advisor.get_cloud_connector")
def test_get_project_advisory_uses_cloud_classification(mock_get_connector, mock_save, mock_load, tmp_path):
    db_path = tmp_path / "registry.db"
    registry = Registry(db_path)
    registry.init_db()
    registry.upsert_model(
        model_id="openai/gpt-3.5-turbo",
        provider="openai",
        sunset_date="2025-09-01",
        replacement="openai/gpt-4o-mini",
        replacement_confidence="high",
    )

    mock_connector = mock_get_connector.return_value
    from chowkidar.cloud_connectors import PurposeClassification

    mock_connector.classify_purposes.return_value = [
        PurposeClassification("CODE_MODEL", "src/agent.py", "coding", "Codegen usage.")
    ]

    source = tmp_path / "src" / "agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("CODE_MODEL = 'gpt-3.5-turbo'\n")

    models = [{
        "variable": "CODE_MODEL",
        "model": "gpt-3.5-turbo",
        "canonical": "openai/gpt-3.5-turbo",
        "file": "src/agent.py",
    }]
    mock_load.return_value = {}
    config = Config(tmp_path / "config.toml")
    config.set("cloud_advisory_enabled", True)

    result = get_project_advisory(str(tmp_path), models, registry, config)

    assert len(result) == 1
    assert result[0]["classification_source"] == "cloud"
    assert result[0]["use_case"] == "coding"
    assert result[0]["purpose"] == "software development and code generation"
    assert result[0]["recommended_model_canonical"] == "openai/gpt-4o-mini"
    mock_save.assert_called_once()
    registry.close()


@patch("chowkidar.advisor._load_cache")
@patch("chowkidar.advisor._save_cache")
@patch("chowkidar.advisor.get_cloud_connector", return_value=None)
@patch("chowkidar.advisor.SLMClient")
def test_get_project_advisory_falls_back_to_slm(mock_slm_cls, _mock_connector, mock_save, mock_load, tmp_path):
    db_path = tmp_path / "registry.db"
    registry = Registry(db_path)
    registry.init_db()
    registry.upsert_model(
        model_id="openai/gpt-3.5-turbo",
        provider="openai",
        sunset_date="2025-09-01",
        replacement="openai/gpt-4o-mini",
        replacement_confidence="high",
    )

    slm = mock_slm_cls.return_value
    slm.classify_purposes.return_value = {("EMBED_MODEL", ".env"): "embeddings/search"}

    models = [{
        "variable": "EMBED_MODEL",
        "model": "gpt-3.5-turbo",
        "canonical": "openai/gpt-3.5-turbo",
        "file": ".env",
    }]
    mock_load.return_value = {}
    config = Config(tmp_path / "config.toml")
    config.set("slm_enabled", True)

    result = get_project_advisory(str(tmp_path), models, registry, config)

    assert result[0]["classification_source"] == "slm"
    assert result[0]["use_case"] == "embeddings/search"
    registry.close()
