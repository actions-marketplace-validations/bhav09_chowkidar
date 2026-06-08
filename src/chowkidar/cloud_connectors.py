"""Opt-in remote LLM connectors for cloud-native purpose classification.

Remote connectors classify model usage purpose only. Successor model selection
and capability validation remain deterministic via the local registry.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import Config
from .recommendations import classify_use_case

logger = logging.getLogger(__name__)

VALID_USE_CASES = frozenset({
    "coding",
    "agents/reasoning",
    "embeddings/search",
    "extraction/structured",
    "tests/eval",
    "chat/general",
})

USE_CASE_DISPLAY: dict[str, str] = {
    "coding": "software development and code generation",
    "agents/reasoning": "agent orchestration and complex reasoning",
    "embeddings/search": "embeddings generation",
    "extraction/structured": "structured data extraction and parsing",
    "tests/eval": "testing and evaluation fixtures",
    "chat/general": "general-purpose chat/text completion",
}

PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "google": "gemini-2.0-flash",
    "mistral": "mistral-small-latest",
}

API_KEY_ENV_VARS: dict[str, list[str]] = {
    "openai": ["CHOWKIDAR_OPENAI_API_KEY", "OPENAI_API_KEY"],
    "anthropic": ["CHOWKIDAR_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
    "google": ["CHOWKIDAR_GOOGLE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "mistral": ["CHOWKIDAR_MISTRAL_API_KEY", "MISTRAL_API_KEY"],
}

CLASSIFICATION_PROMPT = """\
You are an expert at classifying how AI models are used in software projects.

Given minimized code context for each model reference, classify the usage purpose.
Return ONLY a JSON object with a "classifications" array. Each element must have:
- "variable": variable name from input (string)
- "file": file path from input (string)
- "use_case": exactly one of: coding, agents/reasoning, embeddings/search, extraction/structured, tests/eval, chat/general
- "reasoning": one concise sentence explaining the classification (string)

Do NOT recommend replacement models. Classification only.

INPUT:
{payload_json}
"""

_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"(?:bearer\s+|key\s+|api_key\s*=\s*|token\s*=\s*|password\s*=\s*|credential\s*=\s*)(['\"]?)[a-zA-Z0-9_\-\.]{15,}\1", re.IGNORECASE),
    re.compile(r"[A-Za-z0-9_\-]{32,}"),
]


@dataclass
class ModelReference:
    variable: str
    file: str
    model: str
    context: str = ""


@dataclass
class PurposeClassification:
    variable: str
    file: str
    use_case: str
    reasoning: str = ""


class CloudAdvisoryConnector(Protocol):
    provider: str

    def classify_purposes(self, project: str, references: list[ModelReference]) -> list[PurposeClassification] | None:
        ...


def redact_secrets(text: str) -> str:
    """Mask high-entropy strings and credential patterns before remote transmission."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def extract_context_snippet(
    project_path: str,
    file_path: str,
    variable: str,
    model: str,
    context_lines: int = 3,
) -> str:
    """Extract a minimized code snippet around the model reference."""
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(project_path) / path

    if not path.exists() or not path.is_file():
        return f"# {Path(file_path).name}\n# variable={variable}, model={model}"

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"# {Path(file_path).name}\n# variable={variable}, model={model}"

    target_idx = None
    for idx, line in enumerate(lines):
        if model in line or variable in line:
            target_idx = idx
            break

    if target_idx is None:
        snippet = "\n".join(lines[: min(5, len(lines))])
    else:
        start = max(0, target_idx - context_lines)
        end = min(len(lines), target_idx + context_lines + 1)
        snippet = "\n".join(lines[start:end])

    header = f"# file: {Path(file_path).name}\n"
    return redact_secrets(header + snippet)


def use_case_to_display(use_case: str) -> str:
    return USE_CASE_DISPLAY.get(use_case, USE_CASE_DISPLAY["chat/general"])


def normalize_use_case(raw: str) -> str:
    cleaned = raw.strip().lower().replace("_", "/").replace(" ", "/")
    aliases = {
        "embeddings": "embeddings/search",
        "embedding": "embeddings/search",
        "search": "embeddings/search",
        "vision": "chat/general",
        "multimodal": "chat/general",
        "reasoning": "agents/reasoning",
        "agents": "agents/reasoning",
        "agent": "agents/reasoning",
        "extraction": "extraction/structured",
        "structured": "extraction/structured",
        "tests": "tests/eval",
        "eval": "tests/eval",
        "chat": "chat/general",
        "general": "chat/general",
    }
    if cleaned in VALID_USE_CASES:
        return cleaned
    if cleaned in aliases:
        return aliases[cleaned]
    for valid in VALID_USE_CASES:
        if valid in cleaned or cleaned in valid:
            return valid
    return "chat/general"


def resolve_api_key(provider: str) -> str | None:
    for env_var in API_KEY_ENV_VARS.get(provider, []):
        value = os.environ.get(env_var, "").strip()
        if value:
            return value
    return None


def resolve_model_name(config: Config, provider: str) -> str:
    configured = str(config.get("cloud_advisory_model", ""))
    default = PROVIDER_DEFAULT_MODELS.get(provider, "gpt-4o-mini")
    if not configured:
        return default
    if provider == "openai" and configured.startswith("gpt-"):
        return configured
    if provider == "anthropic" and configured.startswith("claude-"):
        return configured
    if provider == "google" and configured.startswith("gemini-"):
        return configured
    if provider == "mistral" and configured.startswith("mistral-"):
        return configured
    if configured == "gpt-4o-mini" and provider != "openai":
        return default
    return configured


def build_model_references(project_path: str, models: list[dict[str, str]]) -> list[ModelReference]:
    references: list[ModelReference] = []
    for model_info in models:
        variable = model_info.get("variable", "")
        file_path = model_info.get("file", "")
        model = model_info.get("model", "")
        context = extract_context_snippet(project_path, file_path, variable, model)
        references.append(ModelReference(variable=variable, file=file_path, model=model, context=context))
    return references


def parse_classification_response(response_text: str) -> list[PurposeClassification] | None:
    text = response_text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Cloud advisory response is not valid JSON")
        return None

    items = data.get("classifications") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    results: list[PurposeClassification] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        variable = item.get("variable")
        file_path = item.get("file")
        use_case = item.get("use_case")
        if not isinstance(variable, str) or not isinstance(file_path, str) or not isinstance(use_case, str):
            continue
        results.append(
            PurposeClassification(
                variable=variable,
                file=file_path,
                use_case=normalize_use_case(use_case),
                reasoning=str(item.get("reasoning", "")),
            )
        )
    return results or None


def build_classification_payload(project: str, references: list[ModelReference]) -> dict[str, Any]:
    return {
        "project": Path(project).name,
        "references": [
            {
                "variable": ref.variable,
                "file": ref.file,
                "model": ref.model,
                "context": ref.context,
            }
            for ref in references
        ],
    }


class _BaseConnector:
    provider: str

    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def classify_purposes(self, project: str, references: list[ModelReference]) -> list[PurposeClassification] | None:
        if not references:
            return []
        payload = build_classification_payload(project, references)
        prompt = CLASSIFICATION_PROMPT.format(payload_json=json.dumps(payload, indent=2))
        try:
            response_text = self._call_api(prompt)
        except Exception as exc:
            logger.warning("%s advisory classification failed: %s", self.provider, exc)
            return None
        return parse_classification_response(response_text)

    def _call_api(self, prompt: str) -> str:
        raise NotImplementedError


class OpenAIConnector(_BaseConnector):
    provider = "openai"

    def _call_api(self, prompt: str) -> str:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


class AnthropicConnector(_BaseConnector):
    provider = "anthropic"

    def _call_api(self, prompt: str) -> str:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 2048,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("content", [])
        if content and isinstance(content[0], dict):
            return str(content[0].get("text", ""))
        return ""


class GeminiConnector(_BaseConnector):
    provider = "google"

    def _call_api(self, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        response = httpx.post(
            url,
            params={"key": self.api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        if parts and isinstance(parts[0], dict):
            return str(parts[0].get("text", ""))
        return ""


class MistralConnector(_BaseConnector):
    provider = "mistral"

    def _call_api(self, prompt: str) -> str:
        response = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


_CONNECTOR_CLASSES: dict[str, type[_BaseConnector]] = {
    "openai": OpenAIConnector,
    "anthropic": AnthropicConnector,
    "google": GeminiConnector,
    "mistral": MistralConnector,
}


def get_cloud_connector(config: Config | None = None) -> CloudAdvisoryConnector | None:
    """Return a configured cloud connector when opt-in remote advisory is enabled."""
    cfg = config or Config()
    if not cfg.get("cloud_advisory_enabled", False):
        return None

    provider = str(cfg.get("cloud_advisory_provider", "openai")).lower()
    if provider not in _CONNECTOR_CLASSES:
        logger.warning("Unsupported cloud advisory provider: %s", provider)
        return None

    api_key = resolve_api_key(provider)
    if not api_key:
        logger.debug("Cloud advisory enabled but no API key found for provider '%s'", provider)
        return None

    model = resolve_model_name(cfg, provider)
    timeout = float(cfg.get("cloud_advisory_timeout_seconds", 5.0))
    return _CONNECTOR_CLASSES[provider](api_key=api_key, model=model, timeout=timeout)


def classify_with_heuristics(models: list[dict[str, str]]) -> dict[tuple[str, str], str]:
    """Tier-3 deterministic purpose classification keyed by (variable, file)."""
    result: dict[tuple[str, str], str] = {}
    for model_info in models:
        variable = model_info.get("variable", "")
        file_path = model_info.get("file", "")
        canonical = model_info.get("canonical", model_info.get("model", ""))
        result[(variable, file_path)] = classify_use_case(variable, file_path, canonical)
    return result
