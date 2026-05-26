"""Unified model replacement recommendations and risk validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .capabilities import CapabilityDiff, diff_capabilities
from .pricing import compare_cost
from .registry.db import ModelRecord, Registry
from .scanner.patterns import normalize_model_id


BLOCKING_CAPABILITY_CHANGES = {"degraded", "lost"}


@dataclass
class Recommendation:
    current_model: str
    recommended_model: str | None
    confidence: str
    source: str
    reason: str
    risk: str
    cost_summary: str | None = None
    capability_diffs: list[dict[str, str]] = field(default_factory=list)
    commercial_risks: list[str] = field(default_factory=list)
    future_risks: list[str] = field(default_factory=list)
    privacy_risks: list[str] = field(default_factory=list)
    manual_review_required: bool = False
    auto_write_allowed: bool = False
    benchmark_comparison: dict[str, Any] | None = None
    cross_family_recommendations: list[dict[str, Any]] = field(default_factory=list)
    use_case: str = "chat/general"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_non_deprecated_replacement(
    current_model: str,
    replacement: str,
    registry: Registry | None,
) -> tuple[str | None, list[str]]:
    """Follow provider replacement chains until the successor is not deprecated.

    Returns the final safe successor and explanatory notes. If the chain ends at
    a deprecated model without a further replacement, returns None.
    """
    if registry is None:
        return replacement, []

    notes: list[str] = []
    visited = {normalize_model_id(current_model)}
    candidate = replacement

    for _ in range(10):
        candidate_id = normalize_model_id(candidate)
        if candidate_id in visited:
            notes.append("Replacement chain contains a cycle; manual review is required.")
            return None, notes
        visited.add(candidate_id)

        candidate_record = registry.get_model(candidate_id)
        if candidate_record is None or candidate_record.sunset_date is None:
            return candidate, notes

        if not candidate_record.replacement:
            notes.append(f"Replacement {candidate_id} is also deprecated and has no validated successor.")
            return None, notes

        notes.append(
            f"Replacement {candidate_id} is also deprecated; traced forward to {candidate_record.replacement}."
        )
        candidate = candidate_record.replacement

    notes.append("Replacement chain exceeded the maximum safe depth; manual review is required.")
    return None, notes


def classify_use_case(variable_name: str | None, file_path: str | None, model_id: str) -> str:
    """Classify the model reference into a highly relevant use case:
    'coding', 'agents/reasoning', 'embeddings/search', 'extraction/structured', 'tests/eval', 'chat/general'
    """
    var_lower = (variable_name or "").lower()
    file_lower = (file_path or "").lower()
    model_lower = model_id.lower()

    # 1. Embeddings & Search
    if "embed" in var_lower or "embed" in model_lower or "retriever" in var_lower or "rag" in var_lower or "vector" in var_lower:
        return "embeddings/search"

    # 2. Coding
    if "code" in var_lower or "coder" in var_lower or "programming" in var_lower or "codegen" in var_lower or "copilot" in var_lower:
        return "coding"
    if any(k in file_lower for k in ["code", "coder", "programming", "copilot"]):
        return "coding"

    # 3. Agents & Deep Reasoning
    if "agent" in var_lower or "reason" in var_lower or "thought" in var_lower or "planner" in var_lower or "orchestrator" in var_lower or "router" in var_lower or "complex" in var_lower:
        return "agents/reasoning"
    if any(k in model_lower for k in ["/o1", "/o3-mini", "r1", "/deepseek-reasoner", "claude-3-opus", "sonnet"]):
        return "agents/reasoning"

    # 4. Data Extraction & Structured Parsing
    if "json" in var_lower or "extract" in var_lower or "parse" in var_lower or "structured" in var_lower or "schema" in var_lower:
        return "extraction/structured"

    # 5. Tests & Evaluation
    if "test" in file_lower or "eval" in file_lower or "test" in var_lower or "fixture" in var_lower or "mock" in var_lower:
        return "tests/eval"

    return "chat/general"


def build_recommendation(
    current_model: str,
    record: ModelRecord | None,
    fallback: tuple[str, str, str] | None = None,
    registry: Registry | None = None,
    variable_name: str | None = None,
    file_path: str | None = None,
) -> Recommendation:
    """Build a validated recommendation from registry data plus optional fallback advice."""
    current_canonical = normalize_model_id(current_model)
    use_case = classify_use_case(variable_name, file_path, current_canonical)
    source = "none"
    recommended: str | None = None
    confidence = "none"
    reason = "No validated replacement is available."
    risk = "Manual review required before changing this model."

    if record and record.replacement:
        recommended = record.replacement
        confidence = record.replacement_confidence or "medium"
        source = "provider_registry"
        reason = f"Provider registry lists {record.replacement} as the successor."
        risk = "Verify application prompts and response expectations before production use."
        resolved, chain_notes = _resolve_non_deprecated_replacement(current_canonical, recommended, registry)
        if chain_notes:
            reason = f"{reason} {' '.join(chain_notes)}"
        if resolved is None:
            recommended = None
            confidence = "none"
            reason = f"No validated non-deprecated replacement is available. {' '.join(chain_notes)}"
            risk = "Manual review required before changing this model."
        else:
            recommended = resolved
    elif fallback is not None:
        recommended, confidence, reason = fallback
        source = "local_fallback"
        risk = "Fallback recommendation is heuristic. Manual review is required."

    recommendation = Recommendation(
        current_model=current_canonical,
        recommended_model=recommended,
        confidence=confidence,
        source=source,
        reason=reason,
        risk=risk,
        use_case=use_case,
    )

    if record and record.breaking_changes:
        recommendation.commercial_risks.append("Provider marks this migration as having breaking changes.")
        recommendation.manual_review_required = True

    if record and record.privacy_tier and record.privacy_tier != "unknown":
        recommendation.privacy_risks.append(f"Provider privacy tier: {record.privacy_tier}.")

    # Always generate cross-family recommendations, even if no primary replacement is recommended
    recommendation.cross_family_recommendations = get_cross_family_alternatives(current_canonical, use_case)

    if not recommended:
        recommendation.manual_review_required = True
        return recommendation

    # Load benchmark comparison if registry is provided
    if registry:
        from .benchmarks import BenchmarkService
        service = BenchmarkService(registry)
        comp = service.get_comparison(current_canonical, recommended)
        if comp:
            recommendation.benchmark_comparison = comp.to_dict()

    cost = compare_cost(current_canonical, recommended)
    if cost:
        recommendation.cost_summary = cost.summary
        if cost.input_delta_pct > 25 or cost.output_delta_pct > 25:
            recommendation.commercial_risks.append("Replacement may materially increase token costs.")

    capability_diffs = diff_capabilities(current_canonical, recommended)
    recommendation.capability_diffs = [_capability_diff_to_dict(d) for d in capability_diffs]
    blocking = [d for d in capability_diffs if d.change_type in BLOCKING_CAPABILITY_CHANGES]
    if blocking:
        labels = ", ".join(d.label for d in blocking)
        recommendation.manual_review_required = True
        recommendation.commercial_risks.append(f"Replacement loses or reduces capabilities: {labels}.")

    if not capability_diffs:
        recommendation.future_risks.append("Capability data is unavailable or incomplete for this model pair.")
        recommendation.manual_review_required = True

    if confidence not in {"high", "medium"}:
        recommendation.manual_review_required = True

    recommendation.auto_write_allowed = not recommendation.manual_review_required
    return recommendation


def get_cross_family_alternatives(current_model: str, use_case: str | None = None) -> list[dict[str, Any]]:
    """Generate latest-model recommendations from other provider families with capability diffs, custom-tailored to the use case."""
    current_canonical = normalize_model_id(current_model)
    parts = current_canonical.split("/")
    current_provider = parts[0] if len(parts) > 1 else "unknown"

    # 1. Determine tier (low vs high)
    model_lower = current_canonical.lower()
    low_tier_keywords = ["mini", "haiku", "flash", "small", "instant", "nano", "3.5-turbo", "tiny", "bison"]
    is_low_tier = any(k in model_lower for k in low_tier_keywords)
    tier = "low" if is_low_tier else "high"

    # 2. Define specialized recommendations per use case to fit the project purpose
    if use_case == "embeddings/search":
        latest_providers = {
            "openai": {
                "low": ("openai/text-embedding-3-small", "Text Embedding 3 Small is highly cost-optimized and perfect for local indexing and RAG."),
                "high": ("openai/text-embedding-3-large", "Text Embedding 3 Large offers premium high-dimensional search representations for enterprise RAG."),
            }
        }
    elif use_case == "coding":
        latest_providers = {
            "openai": {
                "low": ("openai/gpt-4o-mini", "GPT-4o-mini is OpenAI's fast, highly cost-efficient developer model with solid coding syntax understanding."),
                "high": ("openai/gpt-4o", "GPT-4o is OpenAI's flagship multimodal model with strong developer and codegen features."),
            },
            "anthropic": {
                "low": ("anthropic/claude-3.5-haiku-20241022", "Claude 3.5 Haiku is Anthropic's fastest model, highly proficient in code generation and refactoring."),
                "high": ("anthropic/claude-3.5-sonnet-20241022", "Claude 3.5 Sonnet is the gold standard for software engineering, agent tasks, and complex codegen."),
            },
            "google": {
                "low": ("google/gemini-2.5-flash", "Gemini 2.5 Flash is ultra-fast for scanning large context codebases."),
                "high": ("google/gemini-2.5-pro", "Gemini 2.5 Pro offers an enormous 2M token context window, perfect for full-project repository analysis."),
            },
            "qwen (open-source)": {
                "low": ("qwen/qwen-2.5-coder-32b-instruct", "Qwen 2.5 Coder 32B is the leading open-source model optimized specifically for developers, outperforming many commercial models."),
                "high": ("qwen/qwen-2.5-coder-32b-instruct", "Qwen 2.5 Coder 32B is Qwen's flagship specialized programming assistant for local deployment."),
            },
            "deepseek (open-source)": {
                "low": ("deepseek/deepseek-v3", "DeepSeek V3 is a highly efficient open-source model offering incredible developer velocity and code generation."),
                "high": ("deepseek/deepseek-r1", "DeepSeek R1 is a flagship open-source reasoning model, exceptional for tracing complex logic bugs and algorithm design."),
            }
        }
    elif use_case == "agents/reasoning":
        latest_providers = {
            "openai": {
                "low": ("openai/o3-mini", "O3-mini is OpenAI's ultra-fast reasoning model with deep structural plan-and-solve intelligence."),
                "high": ("openai/o1", "O1 is OpenAI's premium complex reasoning and math-heavy planning model."),
            },
            "anthropic": {
                "low": ("anthropic/claude-3.5-haiku-20241022", "Claude 3.5 Haiku is fast, cheap, and exceptional at structured step-by-step logic workflows."),
                "high": ("anthropic/claude-3.5-sonnet-20241022", "Claude 3.5 Sonnet offers industry-leading instruction following, perfect for multi-agent coordination."),
            },
            "google": {
                "low": ("google/gemini-2.5-flash", "Gemini 2.5 Flash is ultra-fast with strong tool-calling, ideal for high-throughput agent loops."),
                "high": ("google/gemini-2.5-pro", "Gemini 2.5 Pro offers deep reasoning and a massive 2M token context to ingest full agent history logs."),
            },
            "deepseek (open-source)": {
                "low": ("deepseek/deepseek-v3", "DeepSeek V3 is a high-efficiency mixture-of-experts model perfect for low-latency agent routing."),
                "high": ("deepseek/deepseek-r1", "DeepSeek R1 provides state-of-the-art open-source chain-of-thought reasoning, rivaling commercial reasoning models."),
            },
            "meta (open-source)": {
                "low": ("meta/llama-3.1-8b-instruct", "Llama 3.1 8B is perfect for high-speed, lightweight local agent execution loops."),
                "high": ("meta/llama-3.3-70b-instruct", "Llama 3.3 70B is a highly capable reasoning model, ideal for hosting private enterprise orchestrators."),
            }
        }
    elif use_case == "extraction/structured":
        latest_providers = {
            "openai": {
                "low": ("openai/gpt-4o-mini", "GPT-4o-mini offers excellent, low-cost structured JSON schema outputs and fast parsing."),
                "high": ("openai/gpt-4o", "GPT-4o is highly reliable for strict schema constraints and complex structured fields extraction."),
            },
            "google": {
                "low": ("google/gemini-2.5-flash", "Gemini 2.5 Flash is exceptionally fast and native-JSON-capable for bulk document extraction."),
                "high": ("google/gemini-2.5-pro", "Gemini 2.5 Pro handles extremely long PDFs and documents up to 2M tokens for complex data extraction."),
            },
            "deepseek (open-source)": {
                "low": ("deepseek/deepseek-v3", "DeepSeek V3 is highly capable at native JSON structures with low latency and low costs."),
                "high": ("deepseek/deepseek-r1", "DeepSeek R1 offers deep reasoning to extract data from highly unstructured, ambiguous source material."),
            }
        }
    elif use_case == "tests/eval":
        latest_providers = {
            "openai": {
                "low": ("openai/gpt-4o-mini", "GPT-4o-mini is incredibly cheap and fast, making it ideal for mock fixtures and continuous integration tests."),
                "high": ("openai/gpt-4o", "GPT-4o is highly capable for running extensive assertion generation and test matrix evaluation."),
            },
            "anthropic": {
                "low": ("anthropic/claude-3.5-haiku-20241022", "Claude 3.5 Haiku is fast and perfect for high-speed local development test verification."),
                "high": ("anthropic/claude-3.5-sonnet-20241022", "Claude 3.5 Sonnet offers exceptional code structure logic, useful for generating mock datasets."),
            },
            "google": {
                "low": ("google/gemini-2.5-flash", "Gemini 2.5 Flash is ultra-fast with huge context, excellent for feeding large test trace files."),
                "high": ("google/gemini-2.5-pro", "Gemini 2.5 Pro offers massive context to run comprehensive full-repo logic testing."),
            },
            "meta (open-source)": {
                "low": ("meta/llama-3.1-8b-instruct", "Llama 3.1 8B is ultra-fast and can be run locally for free, ideal for running test suites offline."),
                "high": ("meta/llama-3.3-70b-instruct", "Llama 3.3 70B is a powerful local test evaluator with great reasoning capabilities."),
            }
        }
    else:  # chat/general
        latest_providers = {
            "openai": {
                "low": ("openai/gpt-4o-mini", "GPT-4o-mini is OpenAI's flagship fast, cheap, and highly capable model for standard assistant chat."),
                "high": ("openai/gpt-4o", "GPT-4o is OpenAI's premium flagship multimodal general chat and reasoning model."),
            },
            "anthropic": {
                "low": ("anthropic/claude-3.5-haiku-20241022", "Claude 3.5 Haiku is Anthropic's fastest, highly cost-effective conversational assistant."),
                "high": ("anthropic/claude-3.5-sonnet-20241022", "Claude 3.5 Sonnet is Anthropic's state-of-the-art general reasoning and highly creative chat model."),
            },
            "google": {
                "low": ("google/gemini-2.5-flash", "Gemini 2.5 Flash is Google's ultra-fast model with massive 1M token context for long chat histories."),
                "high": ("google/gemini-2.5-pro", "Gemini 2.5 Pro is Google's state-of-the-art model offering extremely deep general reasoning."),
            },
            "mistral": {
                "low": ("mistral/mistral-small-latest", "Mistral Small is highly cost-optimized with excellent general multilingual and chat capabilities."),
                "high": ("mistral/mistral-large-latest", "Mistral Large is Mistral's premier top-tier general assistant for complex reasoning."),
            },
            "meta (open-source)": {
                "low": ("meta/llama-3.1-8b-instruct", "Llama 3.1 8B Instruct is Meta's highly efficient and fast open-source general assistant model."),
                "high": ("meta/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct is Meta's state-of-the-art general-purpose open-source assistant."),
            },
            "deepseek (open-source)": {
                "low": ("deepseek/deepseek-v3", "DeepSeek V3 is a highly optimized, high-efficiency open-source general assistant."),
                "high": ("deepseek/deepseek-r1", "DeepSeek R1 is DeepSeek's flagship open-source reasoning assistant."),
            },
            "qwen (open-source)": {
                "low": ("qwen/qwen-2.5-coder-32b-instruct", "Qwen 2.5 Coder 32B Instruct is a premier, specialized open-source model with exceptional coding and reasoning performance."),
                "high": ("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B Instruct is Qwen's flagship high-capability general-purpose open-source model."),
            }
        }

    alternatives = []
    for provider, tier_data in latest_providers.items():
        if provider == current_provider:
            continue
        
        if tier not in tier_data:
            actual_tier = "low" if "low" in tier_data else list(tier_data.keys())[0]
        else:
            actual_tier = tier

        alt_model, reason = tier_data[actual_tier]
        
        # Calculate capability differences using existing diff_capabilities logic
        capability_diffs = diff_capabilities(current_canonical, alt_model)
        diff_list = [_capability_diff_to_dict(d) for d in capability_diffs]
        
        # Calculate price comparison difference in percentage!
        cost_info = compare_cost(current_canonical, alt_model)
        cost_summary = cost_info.summary if cost_info else "No pricing data available"
        
        alternatives.append({
            "provider": provider,
            "model": alt_model,
            "reason": reason,
            "capability_diffs": diff_list,
            "cost_summary": cost_summary,
        })
    
    return alternatives


def _capability_diff_to_dict(diff: CapabilityDiff) -> dict[str, str]:
    return {
        "field": diff.field,
        "label": diff.label,
        "old_value": diff.old_value,
        "new_value": diff.new_value,
        "change_type": diff.change_type,
    }
