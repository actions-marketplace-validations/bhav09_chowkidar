"""Advisory engine that infers LLM usage purpose and recommends context-aware replacements."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from .cloud_connectors import (
    build_model_references,
    classify_with_heuristics,
    get_cloud_connector,
    use_case_to_display,
)
from .config import CHOWKIDAR_HOME, Config
from .recommendations import build_recommendation, classify_use_case
from .registry.db import Registry
from .slm.client import SLMClient

logger = logging.getLogger(__name__)

CACHE_PATH = CHOWKIDAR_HOME / "advisory_cache.json"


def _load_cache() -> dict[str, Any]:
    """Load persistent advisory cache."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    """Save persistent advisory cache."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        logger.debug("Failed to save advisory cache: %s", e)


def calculate_context_hash(project_path: str, models: list[dict[str, str]], last_sync: str | None) -> str:
    """Calculate MD5 hash of inputs to determine cache validity."""
    sorted_models = sorted(models, key=lambda x: (x.get("variable", ""), x.get("file", ""), x.get("model", "")))
    serialized_models = json.dumps(sorted_models)
    data = f"{project_path}:{serialized_models}:{last_sync or ''}"
    return hashlib.md5(data.encode()).hexdigest()


def infer_purpose_heuristically(variable: str, model_id: str) -> str:
    """Infer LLM usage purpose using local deterministic heuristics."""
    v_lower = variable.lower()
    m_lower = model_id.lower()

    if "embed" in v_lower or "embedding" in v_lower or "ada" in m_lower:
        return "embeddings generation"
    if "rerank" in v_lower or "reranker" in v_lower:
        return "document reranking"
    if "vision" in v_lower or "img" in v_lower or "image" in v_lower or "visual" in v_lower or "vision" in m_lower:
        return "multimodal/vision analysis"
    if "audio" in v_lower or "speech" in v_lower or "tts" in v_lower or "whisper" in m_lower:
        return "audio speech-to-text/text-to-speech synthesis"
    if "moderation" in v_lower or "moderate" in v_lower or "moderation" in m_lower:
        return "text safety moderation filter"
    if "fallback" in v_lower or "secondary" in v_lower:
        return "secondary fallback chat completion"

    return "general-purpose chat/text completion"


def get_fallback_recommendation(canonical_id: str) -> tuple[str, str, str]:
    """Provide a reliable capability-matched fallback replacement and reason.

    Returns tuple of (recommended_model_id, confidence, reason).
    """
    provider = canonical_id.split("/")[0] if "/" in canonical_id else "other"
    model = canonical_id.split("/")[-1] if "/" in canonical_id else canonical_id

    if provider == "openai":
        if "gpt-3.5" in model:
            return (
                "openai/gpt-4o-mini",
                "high",
                "GPT-4o-mini is OpenAI's direct fast, cheap successor to GPT-3.5-turbo with 128k context.",
            )
        if "gpt-4-turbo" in model or "preview" in model:
            return (
                "openai/gpt-4o",
                "medium",
                "GPT-4o is OpenAI's flagship fast and highly capable successor to GPT-4-turbo.",
            )
        if "gpt-4" in model:
            return (
                "openai/gpt-4o",
                "medium",
                "GPT-4o is highly cost-optimized, significantly faster, and more capable than legacy GPT-4.",
            )
        if "ada" in model:
            return (
                "openai/text-embedding-3-small",
                "high",
                "Text-embedding-3-small is cheaper, smaller, and higher performing.",
            )
        return "openai/gpt-4o-mini", "low", "Recommended default lightweight and cost-effective successor."

    elif provider == "anthropic":
        if "claude-2" in model:
            return (
                "anthropic/claude-3-haiku-20240307",
                "medium",
                "Claude 3 Haiku is exponentially faster and significantly cheaper than Claude 2.",
            )
        if "opus" in model:
            return (
                "anthropic/claude-3.5-sonnet-20241022",
                "high",
                "Claude 3.5 Sonnet beats legacy Claude 3 Opus on most evals at a fraction of the cost.",
            )
        if "sonnet" in model:
            return (
                "anthropic/claude-3.5-sonnet-20241022",
                "high",
                "Claude 3.5 Sonnet is Anthropic's flagship recommended state-of-the-art model.",
            )
        return (
            "anthropic/claude-3-haiku-20240307",
            "low",
            "Recommended default highly cost-efficient Anthropic successor.",
        )

    elif provider == "google":
        if "gemini-1.0" in model:
            return (
                "google/gemini-1.5-flash",
                "high",
                "Gemini 1.5 Flash provides massive 1M token context window and 80%+ lower costs.",
            )
        if "pro" in model:
            return (
                "google/gemini-1.5-pro",
                "high",
                "Gemini 1.5 Pro offers massive context, multi-modal capabilities, and much faster inference.",
            )
        return "google/gemini-1.5-flash", "low", "Recommended default high-performance lightweight Google successor."

    elif provider == "mistral":
        if "large" in model:
            return (
                "mistral/mistral-large-latest",
                "high",
                "Mistral Large Latest is Mistral's premier flagship successor.",
            )
        return (
            "mistral/mistral-small-latest",
            "medium",
            "Mistral Small Latest is Mistral's highly cost-optimized alternative.",
        )

    return "openai/gpt-4o-mini", "low", "Generic highly-capable fallback model suggestion."


def _resolve_purpose_map(
    project_path: str,
    models: list[dict[str, str]],
    config: Config,
) -> tuple[dict[tuple[str, str], str], str]:
    """Multi-tier purpose classification: cloud connectors, local SLM, then heuristics."""
    references = build_model_references(project_path, models)

    connector = get_cloud_connector(config)
    if connector is not None:
        try:
            classifications = connector.classify_purposes(project_path, references)
            if classifications:
                purpose_map = {(item.variable, item.file): item.use_case for item in classifications}
                logger.debug("Cloud advisory classified %d model references", len(purpose_map))
                return purpose_map, "cloud"
        except Exception as exc:
            logger.warning("Cloud advisory connector failed, falling back: %s", exc)

    if config.get("slm_enabled", False):
        slm = SLMClient(config)
        slm_map = slm.classify_purposes(project_path, references)
        if slm_map:
            logger.debug("Local SLM classified %d model references", len(slm_map))
            return slm_map, "slm"

    return classify_with_heuristics(models), "heuristic"


def generate_local_advice(
    models: list[dict[str, str]],
    registry: Registry,
    purpose_map: dict[tuple[str, str], str] | None = None,
    classification_source: str = "heuristic",
) -> list[dict[str, Any]]:
    """Generate advice using deterministic registry validation and optional classified purposes."""
    advice_list = []
    for m in models:
        canonical = m["canonical"]
        variable = m["variable"]
        file_path = m["file"]
        file_name = Path(file_path).name
        key = (variable, file_path)

        if purpose_map and key in purpose_map:
            use_case = purpose_map[key]
            purpose = use_case_to_display(use_case)
        else:
            use_case = classify_use_case(variable, file_path, canonical)
            purpose = infer_purpose_heuristically(variable, canonical)

        record = registry.get_model(canonical)
        fallback = None if record and record.sunset_date and record.replacement else get_fallback_recommendation(canonical)
        recommendation = build_recommendation(
            canonical,
            record,
            fallback,
            registry=registry,
            variable_name=variable,
            file_path=file_path,
            use_case=use_case,
        )
        rec_model = recommendation.recommended_model
        confidence = recommendation.confidence
        reason = recommendation.reason
        if recommendation.cost_summary:
            reason = f"{reason} Migration {recommendation.cost_summary}."
        risk_parts = [recommendation.risk]
        risk_parts.extend(recommendation.commercial_risks)
        risk_parts.extend(recommendation.future_risks)
        risk_parts.extend(recommendation.privacy_risks)
        risk = " ".join(risk_parts)

        advice_list.append({
            "variable": variable,
            "file": file_name,
            "model": m["model"],
            "purpose": purpose,
            "recommended_model": rec_model.split("/")[-1] if rec_model and "/" in rec_model else rec_model,
            "recommended_model_canonical": rec_model,
            "confidence": confidence,
            "reason": reason,
            "risk": risk,
            "source_type": m.get("source_type", "env"),
            "classification_source": classification_source,
            "use_case": use_case,
            "manual_review_required": recommendation.manual_review_required,
            "auto_write_allowed": recommendation.auto_write_allowed,
            "capability_diffs": recommendation.capability_diffs,
            "commercial_risks": recommendation.commercial_risks,
            "future_risks": recommendation.future_risks,
            "privacy_risks": recommendation.privacy_risks,
        })
    return advice_list


def get_project_advisory(
    project_path: str,
    models: list[dict[str, str]],
    registry: Registry,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """Orchestrate advisory generation using cache and multi-tier classification."""
    cfg = config or Config()
    last_sync = registry.last_sync_time()
    context_hash = calculate_context_hash(project_path, models, last_sync)

    cache = _load_cache()
    if context_hash in cache:
        logger.debug("Advisory cache hit for project '%s'", project_path)
        return cache[context_hash]

    logger.debug("Advisory cache miss. Generating new recommendations...")
    purpose_map, classification_source = _resolve_purpose_map(project_path, models, cfg)
    advice = generate_local_advice(models, registry, purpose_map, classification_source)
    cache[context_hash] = advice
    _save_cache(cache)
    return advice
