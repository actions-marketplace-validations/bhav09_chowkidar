"""Unit tests for local advisor module and SLM model selector."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chowkidar.advisor import (
    calculate_context_hash,
    get_fallback_recommendation,
    get_project_advisory,
    infer_purpose_heuristically,
)
from chowkidar.config import Config
from chowkidar.recommendations import build_recommendation
from chowkidar.registry.db import ModelRecord, Registry
from chowkidar.slm.selector import select_best_slm


def test_infer_purpose_heuristically():
    assert infer_purpose_heuristically("MY_EMBEDDING_VAR", "openai/text-embedding-ada-002") == "embeddings generation"
    assert infer_purpose_heuristically("RERANK_MODEL_NAME", "cohere/rerank") == "document reranking"
    assert infer_purpose_heuristically("VISION_MODEL_VAR", "openai/gpt-4o") == "multimodal/vision analysis"
    assert infer_purpose_heuristically("SPEECH_TO_TEXT", "openai/whisper-1") == "audio speech-to-text/text-to-speech synthesis"
    assert infer_purpose_heuristically("MODERATION_VAR", "openai/text-moderation-latest") == "text safety moderation filter"
    assert infer_purpose_heuristically("FALLBACK_MODEL", "openai/gpt-4o-mini") == "secondary fallback chat completion"
    assert infer_purpose_heuristically("DEFAULT_LLM", "openai/gpt-4o") == "general-purpose chat/text completion"


def test_get_fallback_recommendation():
    # OpenAI
    rec, conf, reason = get_fallback_recommendation("openai/gpt-3.5-turbo-0301")
    assert rec == "openai/gpt-4o-mini"
    assert conf == "high"

    rec, conf, reason = get_fallback_recommendation("openai/gpt-4-0613")
    assert rec == "openai/gpt-4o"
    assert conf == "medium"

    # Anthropic
    rec, conf, reason = get_fallback_recommendation("anthropic/claude-2.1")
    assert rec == "anthropic/claude-3-haiku-20240307"
    assert conf == "medium"

    rec, conf, reason = get_fallback_recommendation("anthropic/claude-3-opus-20240229")
    assert rec == "anthropic/claude-3.5-sonnet-20241022"
    assert conf == "high"

    # Google
    rec, conf, reason = get_fallback_recommendation("google/gemini-1.0-pro")
    assert rec == "google/gemini-1.5-flash"
    assert conf == "high"

    # Other
    rec, conf, reason = get_fallback_recommendation("unrecognized/model")
    assert rec == "openai/gpt-4o-mini"
    assert conf == "low"


def test_recommendation_blocks_capability_loss():
    record = ModelRecord(
        id="openai/gpt-4o",
        provider="openai",
        aliases=[],
        sunset_date="2026-05-22",
        replacement="openai/o3-mini",
        replacement_confidence="high",
        breaking_changes=False,
        source_url=None,
        current_snapshot=None,
        privacy_tier="unknown",
        last_checked_at=None,
        created_at=None,
    )

    recommendation = build_recommendation("openai/gpt-4o", record)

    assert recommendation.manual_review_required
    assert not recommendation.auto_write_allowed
    assert any(r["change_type"] == "lost" for r in recommendation.capability_diffs)


def test_recommendation_traces_deprecated_successor(tmp_path):
    registry = Registry(db_path=tmp_path / "recommendations.db")
    registry.init_db()
    registry.upsert_model(
        model_id="openai/gpt-3.5-turbo",
        provider="openai",
        sunset_date="2025-09-01",
        replacement="openai/gpt-4-turbo-preview",
        replacement_confidence="high",
    )
    registry.upsert_model(
        model_id="openai/gpt-4-turbo-preview",
        provider="openai",
        sunset_date="2025-06-06",
        replacement="openai/gpt-4o",
        replacement_confidence="high",
    )
    registry.upsert_model(
        model_id="openai/gpt-4o",
        provider="openai",
        sunset_date=None,
        replacement=None,
        replacement_confidence="high",
    )

    current = registry.get_model("openai/gpt-3.5-turbo")
    recommendation = build_recommendation("openai/gpt-3.5-turbo", current, registry=registry)

    assert recommendation.recommended_model == "openai/gpt-4o"
    assert "also deprecated" in recommendation.reason
    registry.close()


@patch("chowkidar.slm.selector.get_system_ram_gb")
@patch("chowkidar.slm.selector.get_free_disk_gb")
@patch("chowkidar.slm.selector.get_installed_ollama_models")
def test_select_best_slm(mock_installed, mock_disk, mock_ram, tmp_path):
    config = Config(tmp_path / "config.toml")

    # 1. Respect user explicitly configured non-default model
    config.set("slm_model", "custom-model:latest")
    model, reason = select_best_slm(config)
    assert model == "custom-model:latest"
    assert "User explicitly" in reason

    # Reset config for resource tests
    config.set("slm_model", "gemma3:1b")

    # 2. Match installed models first (reusing already installed)
    mock_installed.return_value = ["qwen2.5:1.5b", "some-other-model"]
    model, reason = select_best_slm(config)
    assert model == "qwen2.5:1.5b"
    assert "Reusing" in reason

    # 3. High system profile (RAM: 32GB, Disk: 50GB)
    mock_installed.return_value = []
    mock_ram.return_value = 32.0
    mock_disk.return_value = 50.0
    model, reason = select_best_slm(config)
    assert model == "qwen2.5:7b"
    assert "High system config" in reason

    # 4. Medium system profile (RAM: 16GB, Disk: 20GB)
    mock_ram.return_value = 16.0
    mock_disk.return_value = 20.0
    model, reason = select_best_slm(config)
    assert model == "gemma3:4b"
    assert "Medium system config" in reason

    # 5. Standard system profile (RAM: 8GB, Disk: 10GB)
    mock_ram.return_value = 8.0
    mock_disk.return_value = 10.0
    model, reason = select_best_slm(config)
    assert model == "gemma3:1b"
    assert "Standard system config" in reason

    # 6. Constrained resources profile (RAM: 4GB, Disk: 5GB)
    mock_ram.return_value = 4.0
    mock_disk.return_value = 5.0
    model, reason = select_best_slm(config)
    assert model == "qwen2.5:0.5b"
    assert "Constrained system resources" in reason

    # 7. Reuse eligible arbitrary installed model (e.g., gemma4:e4b on 16GB RAM)
    mock_installed.return_value = ["gemma4:e4b"]
    mock_ram.return_value = 16.0
    mock_disk.return_value = 50.0
    with patch("chowkidar.slm.selector.get_model_size_from_manifest", return_value=9.6), \
         patch("chowkidar.slm.selector.get_model_metadata", return_value={
             "architecture": "gemma4",
             "context_length": 131072,
             "capabilities": ["completion", "vision"]
         }):
        model, reason = select_best_slm(config)
        assert model == "gemma4:e4b"
        assert "Reusing globally installed model 'gemma4:e4b'" in reason

    # 8. Reject ineligible arbitrary model (e.g. size too large for 8GB RAM)
    mock_installed.return_value = ["large-model:70b"]
    mock_ram.return_value = 8.0
    mock_disk.return_value = 50.0
    with patch("chowkidar.slm.selector.get_model_size_from_manifest", return_value=42.0), \
         patch("chowkidar.slm.selector.get_model_metadata", return_value={
             "architecture": "llama",
             "context_length": 8192,
             "capabilities": ["completion"]
         }):
        model, reason = select_best_slm(config)
        # Should fall back to standard selection for 8GB RAM (which is gemma3:1b)
        assert model == "gemma3:1b"
        assert "Standard system config" in reason

    # 9. Reject arbitrary model with insufficient context length (e.g., < 2048)
    mock_installed.return_value = ["old-model:latest"]
    mock_ram.return_value = 16.0
    mock_disk.return_value = 50.0
    with patch("chowkidar.slm.selector.get_model_size_from_manifest", return_value=3.0), \
         patch("chowkidar.slm.selector.get_model_metadata", return_value={
             "architecture": "llama",
             "context_length": 1024,
             "capabilities": ["completion"]
         }):
        model, reason = select_best_slm(config)
        # Should fall back to standard selection (gemma3:4b for 16GB RAM)
        assert model == "gemma3:4b"
        assert "Medium system config" in reason


@patch("chowkidar.advisor._load_cache")
@patch("chowkidar.advisor._save_cache")
def test_get_project_advisory_cache_hit(mock_save, mock_load):
    mock_registry = MagicMock(spec=Registry)
    mock_registry.last_sync_time.return_value = "2026-05-21T00:00:00"
    
    models = [{"variable": "MODEL_VAR", "model": "gpt-3.5-turbo", "canonical": "openai/gpt-3.5-turbo", "file": ".env"}]
    ctx_hash = calculate_context_hash("/my/project", models, "2026-05-21T00:00:00")
    
    # Mock cache hit
    cached_advice = [{"variable": "MODEL_VAR", "model": "gpt-3.5-turbo", "purpose": "cached purpose"}]
    mock_load.return_value = {ctx_hash: cached_advice}

    result = get_project_advisory("/my/project", models, mock_registry)
    assert result == cached_advice
    mock_save.assert_not_called()


@patch("chowkidar.advisor._load_cache")
@patch("chowkidar.advisor._save_cache")
def test_get_project_advisory_generates_local_advice(mock_save, mock_load, tmp_path):
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

    models = [{"variable": "MODEL_VAR", "model": "gpt-3.5-turbo", "canonical": "openai/gpt-3.5-turbo", "file": ".env"}]
    mock_load.return_value = {}

    config = Config(tmp_path / "config.toml")
    result = get_project_advisory("/my/project", models, registry, config)

    assert len(result) == 1
    assert result[0]["variable"] == "MODEL_VAR"
    assert result[0]["purpose"] == "general-purpose chat/text completion"
    assert result[0]["recommended_model_canonical"] == "openai/gpt-4o-mini"
    mock_save.assert_called_once()
    registry.close()
