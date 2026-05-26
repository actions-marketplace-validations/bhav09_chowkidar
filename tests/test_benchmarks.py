"""Tests for model benchmark comparison, fuzzy matching, and dynamic syncing."""

import pytest
import respx
from httpx import Response

from chowkidar.registry.db import Registry
from chowkidar.benchmarks import (
    fuzzy_match_model,
    seed_benchmarks,
    sync_arena_benchmarks,
    get_benchmark_comparison,
)


@pytest.fixture
def registry(tmp_path):
    db_path = tmp_path / "test_benchmarks.db"
    reg = Registry(db_path)
    reg.init_db()
    # init_db calls seed_benchmarks internally, so let's start fresh and clear it for some tests if needed,
    # or just leverage it.
    yield reg
    reg.close()


def test_offline_seeding(registry):
    # Verify database was initialized with standard baseline scores
    row = registry.conn.execute(
        "SELECT arena_elo, mmlu, human_eval FROM model_benchmarks WHERE model_id = ?",
        ("openai/gpt-4o-mini",)
    ).fetchone()
    assert row is not None
    assert row["arena_elo"] == 1215
    assert row["mmlu"] == 82.0
    assert row["human_eval"] == 87.2


def test_fuzzy_matching_heuristics():
    known_ids = [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-3-opus-20240229",
        "anthropic/claude-3.5-sonnet-20241022",
        "google/gemini-1.5-pro",
    ]

    # Test exact / clean match
    assert fuzzy_match_model("gpt-4o-mini", "OpenAI", known_ids) == "openai/gpt-4o-mini"
    assert fuzzy_match_model("GPT-4o-Mini", "openai", known_ids) == "openai/gpt-4o-mini"

    # Test vendor filtering
    assert fuzzy_match_model("gpt-4o-mini", "Anthropic", known_ids) is None

    # Test trailing suffixes stripping
    assert fuzzy_match_model("claude-3.5-sonnet-thinking", "Anthropic", known_ids) == "anthropic/claude-3.5-sonnet-20241022"
    assert fuzzy_match_model("claude-3-opus-latest", "Anthropic", known_ids) == "anthropic/claude-3-opus-20240229"

    # Test date pattern stripping
    assert fuzzy_match_model("claude-3.5-sonnet-20241022", "Anthropic", known_ids) == "anthropic/claude-3.5-sonnet-20241022"


@respx.mock
@pytest.mark.asyncio
async def test_sync_arena_benchmarks(registry):
    # Setup some known models in registry so the fuzzy matching can associate them
    registry.conn.execute(
        "INSERT INTO models (id, provider) VALUES (?, ?)", ("openai/gpt-4o", "openai")
    )
    registry.conn.commit()

    latest_json_url = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data/latest.json"
    daily_json_url = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data/2026-05-25/text.json"

    # Mock latest pointer
    respx.get(latest_json_url).mock(
        return_value=Response(
            200,
            json={
                "date": "2026-05-25",
                "path": "2026-05-25",
            },
        )
    )

    # Mock daily text snapshot
    respx.get(daily_json_url).mock(
        return_value=Response(
            200,
            json={
                "models": [
                    {"model": "gpt-4o", "vendor": "OpenAI", "score": 1315},
                    {"model": "unknown-model", "vendor": "Unknown", "score": 1000},
                ]
            },
        )
    )

    updated = await sync_arena_benchmarks(registry)
    assert updated == 1

    # Verify updated ELO is stored
    row = registry.conn.execute(
        "SELECT arena_elo FROM model_benchmarks WHERE model_id = ?", ("openai/gpt-4o",)
    ).fetchone()
    assert row is not None
    assert row["arena_elo"] == 1315


def test_benchmark_comparison_delta_math(registry):
    # Explicitly set mock scores for clean subtraction assertion
    registry.conn.execute(
        "INSERT OR REPLACE INTO model_benchmarks (model_id, arena_elo, mmlu, human_eval) VALUES (?, ?, ?, ?)",
        ("openai/gpt-3.5-turbo", 1100, 70.0, 50.0)
    )
    registry.conn.execute(
        "INSERT OR REPLACE INTO model_benchmarks (model_id, arena_elo, mmlu, human_eval) VALUES (?, ?, ?, ?)",
        ("openai/gpt-4o-mini", 1250, 82.5, 85.2)
    )
    registry.conn.commit()

    comparison = get_benchmark_comparison(registry, "openai/gpt-3.5-turbo", "openai/gpt-4o-mini")
    assert comparison is not None
    assert comparison.current_elo == 1100
    assert comparison.recommended_elo == 1250
    assert comparison.elo_delta == 150

    assert comparison.current_mmlu == 70.0
    assert comparison.recommended_mmlu == 82.5
    assert comparison.mmlu_delta == 12.5

    assert comparison.current_human_eval == 50.0
    assert comparison.recommended_human_eval == 85.2
    assert comparison.human_eval_delta == 35.2


def test_benchmark_comparison_partial_missing(registry):
    # Set model with partial data (e.g., only Elo)
    registry.conn.execute(
        "INSERT OR REPLACE INTO model_benchmarks (model_id, arena_elo, mmlu, human_eval) VALUES (?, ?, ?, ?)",
        ("openai/gpt-3.5-turbo", 1100, None, None)
    )
    registry.conn.execute(
        "INSERT OR REPLACE INTO model_benchmarks (model_id, arena_elo, mmlu, human_eval) VALUES (?, ?, ?, ?)",
        ("openai/gpt-4o-mini", None, 82.5, None)
    )
    registry.conn.commit()

    comparison = get_benchmark_comparison(registry, "openai/gpt-3.5-turbo", "openai/gpt-4o-mini")
    assert comparison is not None
    assert comparison.current_elo == 1100
    assert comparison.recommended_elo is None
    assert comparison.elo_delta is None

    assert comparison.current_mmlu is None
    assert comparison.recommended_mmlu == 82.5
    assert comparison.mmlu_delta is None

    assert comparison.current_human_eval is None
    assert comparison.recommended_human_eval is None
    assert comparison.human_eval_delta is None


def test_fuzzy_matching_collisions():
    known_ids = ["openai/gpt-4o", "openai/gpt-4", "openai/gpt-4o-mini"]
    
    # gpt-4 must not match gpt-4o or gpt-4o-mini
    assert fuzzy_match_model("gpt-4", "openai", known_ids) == "openai/gpt-4"
    assert fuzzy_match_model("gpt-4o", "openai", known_ids) == "openai/gpt-4o"


@respx.mock
@pytest.mark.asyncio
async def test_sync_arena_security_redirect(registry):
    latest_json_url = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data/latest.json"

    # Mock latest pointer to attempt redirect
    respx.get(latest_json_url).mock(
        return_value=Response(
            200,
            json={
                "date": "2026-05-25",
            },
        )
    )

    # Mock the leaderboard response to point to a completely different domain/origin
    malicious_text_url = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data/2026-05-25/text.json"
    respx.get(malicious_text_url).mock(
        return_value=Response(
            200,
            json={
                "models": []
            }
        )
    )

    from chowkidar.config import Config
    custom_config = Config()
    # Use a custom latest JSON URL that does not end with latest.json, forcing fallback to raw.githubusercontent.com
    custom_config.set("benchmarks_arena_url", "https://malicious-domain.com/latest_custom.json")

    # Mock latest pointer on different domain
    respx.get("https://malicious-domain.com/latest_custom.json").mock(
        return_value=Response(
            200,
            json={
                "date": "2026-05-25"
            }
        )
    )

    result = await sync_arena_benchmarks(registry, config=custom_config)
    # The constructed text leaderboard URL will fall back to raw.githubusercontent.com, which has a different domain!
    assert result.status == "failed"
    assert "Security Violation" in result.failure_reason
