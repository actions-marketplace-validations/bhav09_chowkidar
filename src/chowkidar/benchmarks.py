"""Public LLM Benchmarks comparison, storage, and synchronization."""

from __future__ import annotations

import re
import logging
import json
import httpx
from pathlib import Path
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry.db import Registry
    from .config import Config

logger = logging.getLogger(__name__)


def load_baseline_benchmarks() -> dict[str, dict[str, float | int]]:
    """Load and validate baseline benchmark data from packaged JSON."""
    data_path = Path(__file__).parent / "data" / "model_benchmarks.json"
    if not data_path.exists():
        logger.error("Baseline benchmarks JSON file not found at: %s", data_path)
        return {}
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        
        # Schema validation
        if not isinstance(payload, dict) or "benchmarks" not in payload:
            logger.error("Invalid baseline benchmarks schema: missing 'benchmarks' key.")
            return {}
        
        benchmarks = payload["benchmarks"]
        if not isinstance(benchmarks, dict):
            logger.error("Invalid baseline benchmarks schema: 'benchmarks' must be a dictionary.")
            return {}
            
        validated: dict[str, dict[str, float | int]] = {}
        for model_id, scores in benchmarks.items():
            if not isinstance(scores, dict):
                continue
            arena_elo = scores.get("arena_elo")
            mmlu = scores.get("mmlu")
            human_eval = scores.get("human_eval")
            
            validated[model_id] = {
                "arena_elo": int(arena_elo) if arena_elo is not None else None,
                "mmlu": float(mmlu) if mmlu is not None else None,
                "human_eval": float(human_eval) if human_eval is not None else None,
            }
        return validated
    except Exception as e:
        logger.error("Failed to load and validate baseline benchmarks JSON: %s", e)
        return {}


@dataclass
class BenchmarkComparison:
    current_model: str
    recommended_model: str
    current_elo: int | None
    recommended_elo: int | None
    elo_delta: int | None
    current_mmlu: float | None
    recommended_mmlu: float | None
    mmlu_delta: float | None
    current_human_eval: float | None
    recommended_human_eval: float | None
    human_eval_delta: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_model": self.current_model,
            "recommended_model": self.recommended_model,
            "current_elo": self.current_elo,
            "recommended_elo": self.recommended_elo,
            "elo_delta": self.elo_delta,
            "current_mmlu": self.current_mmlu,
            "recommended_mmlu": self.recommended_mmlu,
            "mmlu_delta": self.mmlu_delta,
            "current_human_eval": self.current_human_eval,
            "recommended_human_eval": self.recommended_human_eval,
            "human_eval_delta": self.human_eval_delta,
        }


class BenchmarkService:
    """Service boundary for benchmark metrics and calculations."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def get_comparison(self, current_id: str, recommended_id: str) -> BenchmarkComparison | None:
        return get_benchmark_comparison(self.registry, current_id, recommended_id)


def seed_benchmarks(registry: Registry) -> None:
    """Pre-populate the database with baseline industry-standard model scores."""
    try:
        baseline = load_baseline_benchmarks()
        for model_id, scores in baseline.items():
            registry.conn.execute(
                """INSERT INTO model_benchmarks (model_id, arena_elo, mmlu, human_eval)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(model_id) DO UPDATE SET
                     mmlu = COALESCE(excluded.mmlu, model_benchmarks.mmlu),
                     human_eval = COALESCE(excluded.human_eval, model_benchmarks.human_eval),
                     arena_elo = COALESCE(model_benchmarks.arena_elo, excluded.arena_elo)""",
                (model_id, scores["arena_elo"], scores["mmlu"], scores["human_eval"]),
            )
        registry.conn.commit()
    except Exception as e:
        logger.error("Failed to seed baseline model benchmarks: %s", e)


def fuzzy_match_model(arena_model: str, vendor: str | None, canonical_ids: list[str]) -> str | None:
    """Map a Chatbot Arena model string and optional vendor to our canonical database IDs using scored exact-first matching."""
    arena_lower = arena_model.lower().strip()
    cleaned_arena = arena_lower
    for suffix in [" (thinking-minimal)", "-thinking", "-high", "-preview", "-latest", "-20241022", "-20250514"]:
        cleaned_arena = cleaned_arena.replace(suffix, "")
    
    # Strip any date snapshots from the model ID to enable stable lineage mapping
    cleaned_arena = re.sub(r'-\d{8}', '', cleaned_arena)
    cleaned_arena = re.sub(r'-\d{4}-\d{2}-\d{2}', '', cleaned_arena)
    
    # Pass 1: Check for exact normalized matches on vendor/provider + model string
    for cid in canonical_ids:
        parts = cid.split("/")
        prov, cid_model = parts[0], parts[-1]
        
        if vendor:
            v_lower = vendor.lower()
            if v_lower == "openai" and prov != "openai":
                continue
            if v_lower == "anthropic" and prov != "anthropic":
                continue
            if v_lower == "google" and prov != "google":
                continue
            if v_lower == "mistral" and prov != "mistral":
                continue
                
        cid_cleaned = cid_model.lower()
        cid_cleaned = re.sub(r'-\d{8}', '', cid_cleaned)
        cid_cleaned = re.sub(r'-\d{4}-\d{2}-\d{2}', '', cid_cleaned)
        
        if cid_cleaned == cleaned_arena:
            return cid

    # Pass 2: Check for precise collision-safe token matches
    arena_tokens = [t for t in re.split(r'[-_./\s]', cleaned_arena) if t]
    
    for cid in canonical_ids:
        parts = cid.split("/")
        prov, cid_model = parts[0], parts[-1]
        
        if vendor:
            v_lower = vendor.lower()
            if v_lower == "openai" and prov != "openai":
                continue
            if v_lower == "anthropic" and prov != "anthropic":
                continue
            if v_lower == "google" and prov != "google":
                continue
            if v_lower == "mistral" and prov != "mistral":
                continue
                
        cid_cleaned = cid_model.lower()
        cid_cleaned = re.sub(r'-\d{8}', '', cid_cleaned)
        cid_cleaned = re.sub(r'-\d{4}-\d{2}-\d{2}', '', cid_cleaned)
        
        cid_tokens = [t for t in re.split(r'[-_./\s]', cid_cleaned) if t]
        
        # Check token compatibility to prevent collisions
        # "gpt-4" shouldn't match "gpt-4o"
        if "gpt-4" in arena_tokens and "gpt-4o" in cid_tokens:
            continue
        if "gpt-4" in cid_tokens and "gpt-4o" in arena_tokens:
            continue
        if "gpt-4o-mini" in arena_tokens and "gpt-4o" in cid_tokens and "mini" not in cid_tokens:
            continue
        if "gpt-4o" in arena_tokens and "gpt-4o-mini" in cid_tokens:
            continue
        if "claude-3" in arena_tokens and "claude-3.5" in cid_tokens:
            continue
        if "claude-3.5" in arena_tokens and "claude-3" in cid_tokens:
            continue

        # Use full substring match only if we have token level alignment or substantial length match
        if len(cleaned_arena) >= 5 and len(cid_cleaned) >= 5:
            # Check prefix/suffix word alignment to prevent "gpt-4" matching "gpt-4o"
            if cleaned_arena.startswith("gpt-4") and not cleaned_arena.startswith("gpt-4o") and cid_cleaned.startswith("gpt-4o"):
                continue
            if cid_cleaned.startswith("gpt-4") and not cid_cleaned.startswith("gpt-4o") and cleaned_arena.startswith("gpt-4o"):
                continue
                
            if cleaned_arena in cid_cleaned or cid_cleaned in cleaned_arena:
                return cid
                
    return None


class BenchmarkSyncResult(int):
    """Backward-compatible integer subclass that carries sync status metadata."""
    def __new__(cls, val: int, status: str = "success", failure_reason: str | None = None):
        obj = super().__new__(cls, val)
        obj.status = status
        obj.failure_reason = failure_reason
        obj.updated_count = val
        return obj


async def sync_arena_benchmarks(registry: Registry, config: Config | None = None) -> BenchmarkSyncResult:
    """Fetch latest Chatbot Arena ELO data from the automated daily repo tracker with retries and safety checks."""
    import tenacity
    from urllib.parse import urlparse
    from .config import Config

    if config is None:
        config = Config()

    if not config.get("benchmarks_enabled", True):
        logger.info("Benchmarks synchronization is disabled in configuration.")
        return BenchmarkSyncResult(0, "success")

    latest_url = config.get("benchmarks_arena_url", "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data/latest.json")
    timeout = config.get("benchmarks_sync_timeout_seconds", 10.0)
    attempts = config.get("benchmarks_retry_attempts", 3)
    backoff_min = config.get("benchmarks_retry_backoff_min", 2.0)
    backoff_max = config.get("benchmarks_retry_backoff_max", 30.0)
    max_payload_mb = config.get("benchmarks_max_payload_mb", 5)

    updated_count = 0
    failure_reason = None

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(attempts),
        wait=tenacity.wait_exponential(min=backoff_min, max=backoff_max),
        reraise=True
    )
    async def _fetch_url(url: str) -> httpx.Response:
        # Enforce content length limit using streaming if possible, or simple head check/limit
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            
            # Content length validation
            content_len = resp.headers.get("Content-Length")
            if content_len and int(content_len) > max_payload_mb * 1024 * 1024:
                raise ValueError(f"Payload exceeds limit of {max_payload_mb}MB")
            if len(resp.content) > max_payload_mb * 1024 * 1024:
                raise ValueError(f"Payload exceeds limit of {max_payload_mb}MB")
            return resp

    try:
        # Step 1: Fetch latest.json pointer
        resp = await _fetch_url(latest_url)
        latest_data = resp.json()
        if not isinstance(latest_data, dict):
            raise ValueError("latest.json payload must be a dictionary")

        latest_date = latest_data.get("date")
        if not latest_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(latest_date)):
            raise ValueError("Invalid or missing date in latest.json")

        # Step 2: Build and validate text.json URL
        if latest_url.endswith("latest.json"):
            base_url = latest_url[:-11]
            text_leaderboard_url = f"{base_url}{latest_date}/text.json"
        else:
            text_leaderboard_url = f"https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data/{latest_date}/text.json"

        # Security check: Constructed URL must have the same origin as the latest.json base url
        latest_parsed = urlparse(latest_url)
        leaderboard_parsed = urlparse(text_leaderboard_url)
        if latest_parsed.netloc != leaderboard_parsed.netloc or latest_parsed.scheme != leaderboard_parsed.scheme:
            raise PermissionError("Security Violation: Benchmark redirect escaped allowed origin.")

        # Step 3: Fetch text.json leaderboard snapshot
        leaderboard_resp = await _fetch_url(text_leaderboard_url)
        leaderboard_data = leaderboard_resp.json()
        if not isinstance(leaderboard_data, dict) or "models" not in leaderboard_data:
            raise ValueError("Leaderboard is missing 'models' field")

        models = leaderboard_data["models"]
        if not isinstance(models, list):
            raise ValueError("Leaderboard 'models' must be a list")

        # Get list of all known canonical IDs from the database to map onto
        known_ids = [row["id"] for row in registry.conn.execute("SELECT id FROM models").fetchall()]

        for m in models:
            if not isinstance(m, dict):
                continue
            model_name = m.get("model")
            vendor = m.get("vendor")
            elo = m.get("score")

            # Validate payload fields
            if not model_name or not isinstance(model_name, str) or len(model_name) > 100:
                continue
            if vendor is not None and (not isinstance(vendor, str) or len(vendor) > 100):
                continue
            if elo is None:
                continue

            try:
                elo_int = int(elo)
                if not (500 <= elo_int <= 3000):  # Reasonable Elo score bounds
                    continue
            except (ValueError, TypeError):
                continue

            matched_id = fuzzy_match_model(model_name, vendor, known_ids)
            if matched_id:
                registry.conn.execute(
                    """INSERT INTO model_benchmarks (model_id, arena_elo, last_updated)
                       VALUES (?, ?, datetime('now'))
                       ON CONFLICT(model_id) DO UPDATE SET
                         arena_elo = excluded.arena_elo,
                         last_updated = excluded.last_updated""",
                    (matched_id, elo_int),
                )
                updated_count += 1

        registry.conn.commit()
        registry.log_sync_success("benchmark:arena")
        logger.info("Successfully synchronized %d Chatbot Arena ELO benchmarks.", updated_count)
        return BenchmarkSyncResult(updated_count, "success")

    except Exception as e:
        failure_reason = str(e)
        registry.log_sync_failure("benchmark:arena", failure_reason)
        logger.error("Error synchronizing Chatbot Arena ELO benchmarks: %s", failure_reason)
        return BenchmarkSyncResult(0, "failed", failure_reason)


def get_benchmark_comparison(registry: Registry, current_id: str, recommended_id: str) -> BenchmarkComparison | None:
    """Compare general, coding, and knowledge benchmarks between current and recommended models."""
    try:
        cur_row = registry.conn.execute(
            "SELECT arena_elo, mmlu, human_eval FROM model_benchmarks WHERE model_id = ?", (current_id,)
        ).fetchone()
        
        rep_row = registry.conn.execute(
            "SELECT arena_elo, mmlu, human_eval FROM model_benchmarks WHERE model_id = ?", (recommended_id,)
        ).fetchone()
        
        cur_elo = cur_row["arena_elo"] if cur_row and cur_row["arena_elo"] is not None else None
        cur_mmlu = cur_row["mmlu"] if cur_row and cur_row["mmlu"] is not None else None
        cur_he = cur_row["human_eval"] if cur_row and cur_row["human_eval"] is not None else None
        
        rep_elo = rep_row["arena_elo"] if rep_row and rep_row["arena_elo"] is not None else None
        rep_mmlu = rep_row["mmlu"] if rep_row and rep_row["mmlu"] is not None else None
        rep_he = rep_row["human_eval"] if rep_row and rep_row["human_eval"] is not None else None
        
        # If we have absolutely no benchmark data for either model, return None
        if cur_elo is None and cur_mmlu is None and cur_he is None and rep_elo is None and rep_mmlu is None and rep_he is None:
            return None
            
        elo_delta = (rep_elo - cur_elo) if cur_elo is not None and rep_elo is not None else None
        mmlu_delta = (rep_mmlu - cur_mmlu) if cur_mmlu is not None and rep_mmlu is not None else None
        he_delta = (rep_he - cur_he) if cur_he is not None and rep_he is not None else None
        
        return BenchmarkComparison(
            current_model=current_id,
            recommended_model=recommended_id,
            current_elo=cur_elo,
            recommended_elo=rep_elo,
            elo_delta=elo_delta,
            current_mmlu=cur_mmlu,
            recommended_mmlu=rep_mmlu,
            mmlu_delta=mmlu_delta,
            current_human_eval=cur_he,
            recommended_human_eval=rep_he,
            human_eval_delta=he_delta,
        )
    except Exception as e:
        logger.error("Error retrieving benchmark comparison for %s -> %s: %s", current_id, recommended_id, e)
        return None


def format_benchmark_delta_cli(comp_dict: dict[str, Any] | None) -> str:
    """Format benchmark ELO delta for Rich CLI display."""
    if not comp_dict:
        return ""
    rep_elo = comp_dict.get("recommended_elo")
    elo_delta = comp_dict.get("elo_delta")
    if rep_elo is None:
        return ""
    if elo_delta is not None and elo_delta > 0:
        return f" [green](+{elo_delta} Elo)[/green]"
    elif elo_delta is not None:
        return f" (Elo: {rep_elo})"
    return f" (Elo: {rep_elo})"


def format_benchmark_delta_markdown(comp_dict: dict[str, Any] | None) -> str:
    """Format benchmark ELO delta for Markdown report display."""
    if not comp_dict:
        return ""
    rep_elo = comp_dict.get("recommended_elo")
    elo_delta = comp_dict.get("elo_delta")
    if rep_elo is None:
        return ""
    if elo_delta is not None and elo_delta > 0:
        return f" (Elo: {rep_elo}, +{elo_delta} ELO boost!)"
    elif elo_delta is not None:
        return f" (Elo: {rep_elo}, {elo_delta} ELO)"
    return f" (Elo: {rep_elo})"


def format_benchmark_delta_html(comp_dict: dict[str, Any] | None, replacement_name: str) -> str:
    """Format benchmark metrics (Elo, MMLU, HumanEval) for HTML report display."""
    import html
    escaped_name = html.escape(replacement_name)
    if not comp_dict:
        return f"<b>{escaped_name}</b>"
    
    rep_elo = comp_dict.get("recommended_elo")
    elo_delta = comp_dict.get("elo_delta")
    
    if rep_elo is None:
        return f"<b>{escaped_name}</b>"
        
    delta_str = ""
    if elo_delta is not None and elo_delta > 0:
        delta_str = f" <span style='color: #28a745; font-size: 0.85em; font-weight: bold;'>({elo_delta:+} ELO)</span>"
    elif elo_delta is not None:
        delta_str = f" <span style='color: #6c757d; font-size: 0.85em;'>({elo_delta:+} ELO)</span>"
        
    html_str = f"<div><b>{escaped_name}</b></div><div style='font-size: 0.8em; color: #555;'>Elo: {rep_elo}{delta_str}</div>"
    
    sub_parts = []
    if comp_dict.get("current_mmlu") is not None or comp_dict.get("recommended_mmlu") is not None:
        mmlu_delta = comp_dict.get("mmlu_delta")
        m_delta_str = f" ({mmlu_delta:+.1f}%)" if mmlu_delta is not None else ""
        sub_parts.append(f"MMLU: {comp_dict.get('recommended_mmlu') or '?'}{m_delta_str}")
    if comp_dict.get("current_human_eval") is not None or comp_dict.get("recommended_human_eval") is not None:
        he_delta = comp_dict.get("human_eval_delta")
        h_delta_str = f" ({he_delta:+.1f}%)" if he_delta is not None else ""
        sub_parts.append(f"HEval: {comp_dict.get('recommended_human_eval') or '?'}{h_delta_str}")
        
    if sub_parts:
        html_str += f"<div style='font-size: 0.75em; color: #777;'>{', '.join(sub_parts)}</div>"
        
    return html_str
