"""Adaptive local SLM selector based on system hardware resources and installed models."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from pathlib import Path

from ..config import Config

logger = logging.getLogger(__name__)

# Configured list of supported model candidates in order of preference per tier
# RAM thresholds in GB:
# Tiny: < 6GB RAM -> Qwen 0.5B (approx 390MB)
# Small: 6GB - 12GB RAM -> Gemma 1B (approx 815MB) or Qwen 1.5B
# Medium: 12GB - 24GB RAM -> Gemma 4B (approx 2.6GB) or Qwen 3B
# Large: > 24GB RAM -> Qwen 7B (approx 4.7GB) or Gemma 8B (approx 5GB)
SLM_TIERS = {
    "tiny": {
        "models": ["qwen2.5:0.5b", "qwen2.5:1.5b", "gemma3:1b"],
        "min_ram_gb": 0.0,
        "required_disk_gb": 1.0,
    },
    "small": {
        "models": ["gemma3:1b", "qwen2.5:1.5b", "qwen2.5:0.5b"],
        "min_ram_gb": 6.0,
        "required_disk_gb": 1.8,
    },
    "medium": {
        "models": ["gemma3:4b", "qwen2.5:3b", "gemma3:1b"],
        "min_ram_gb": 12.0,
        "required_disk_gb": 4.5,
    },
    "large": {
        "models": ["qwen2.5:7b", "gemma3:4b", "gemma3:1b"],
        "min_ram_gb": 24.0,
        "required_disk_gb": 8.0,
    },
}


def get_system_ram_gb() -> float:
    """Detect total system RAM in GB. Returns 8.0 as fallback if detection fails."""
    system = platform.system()
    try:
        if system == "Darwin":
            # macOS
            res = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0:
                return int(res.stdout.strip()) / (1024 ** 3)
        elif system == "Linux":
            # Linux meminfo
            mem_path = Path("/proc/meminfo")
            if mem_path.exists():
                for line in mem_path.read_text().splitlines():
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1]) / (1024 ** 2)  # kb to GB
        elif system == "Windows":
            # Windows wmic / powershell
            res = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "(Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum).Sum"
                ],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0 and res.stdout.strip():
                return int(res.stdout.strip()) / (1024 ** 3)
    except Exception as e:
        logger.debug("Failed to detect system RAM: %s", e)

    return 8.0  # Safe default fallback


def get_free_disk_gb() -> float:
    """Return free disk space in GB for CHOWKIDAR_HOME's drive."""
    try:
        usage = shutil.disk_usage(Path.home())
        return usage.free / (1024 ** 3)
    except Exception as e:
        logger.debug("Failed to check disk usage: %s", e)
        return 10.0  # Assume enough space as safe fallback


def get_installed_ollama_models() -> list[str]:
    """Retrieve list of currently installed Ollama models."""
    try:
        # Avoid forcing dependencies or SDK if not loaded yet; use command line or SDK if possible
        from .client import _get_ollama
        ollama = _get_ollama()
        if ollama is not None:
            models = ollama.list()
            return [m.model for m in models.models] if models.models else []
    except Exception as e:
        logger.debug("Ollama Python SDK list call failed: %s", e)

    # CLI fallback
    if shutil.which("ollama") is not None:
        try:
            res = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0:
                models = []
                for line in res.stdout.splitlines()[1:]:  # skip header
                    if line.strip():
                        models.append(line.split()[0])
                return models
        except Exception:
            pass

    # Filesystem fallback
    try:
        import os
        from pathlib import Path
        env_models = os.environ.get("OLLAMA_MODELS")
        if env_models:
            base_dir = Path(env_models)
        else:
            base_dir = Path.home() / ".ollama" / "models"
            
        manifests_root = base_dir / "manifests"
        models = []
        if manifests_root.exists():
            for registry_dir in manifests_root.iterdir():
                if not registry_dir.is_dir():
                    continue
                for namespace_dir in registry_dir.iterdir():
                    if not namespace_dir.is_dir():
                        continue
                    namespace = namespace_dir.name
                    for model_dir in namespace_dir.iterdir():
                        if not model_dir.is_dir():
                            continue
                        model_base = model_dir.name
                        for tag_file in model_dir.iterdir():
                            if tag_file.is_file() and not tag_file.name.startswith("."):
                                tag = tag_file.name
                                if namespace == "library":
                                    models.append(f"{model_base}:{tag}")
                                else:
                                    models.append(f"{namespace}/{model_base}:{tag}")
            return models
    except Exception as e:
        logger.debug("Ollama filesystem fallback list failed: %s", e)

    return []


def get_max_safe_model_size_gb(ram_gb: float) -> float:
    """Determine the maximum safe model size in GB based on system RAM."""
    if ram_gb < 6.0:
        return 2.0
    elif ram_gb < 12.0:
        return 4.0
    elif ram_gb < 24.0:
        return 10.0
    else:
        return 20.0


def get_model_metadata(model_name: str) -> dict[str, any]:
    """Run 'ollama show' for a model and parse its metadata."""
    try:
        res = subprocess.run(
            ["ollama", "show", model_name],
            capture_output=True, text=True, timeout=5
        )
        if res.returncode != 0:
            return {}
        
        metadata = {
            "context_length": None,
            "parameters": None,
            "capabilities": [],
            "architecture": None,
        }
        
        lines = res.stdout.splitlines()
        current_section = None
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            
            # Identify sections
            if stripped == "Model":
                current_section = "Model"
                continue
            elif stripped == "Capabilities":
                current_section = "Capabilities"
                continue
            elif stripped in ("Parameters", "License", "System"):
                current_section = stripped
                continue
                
            # We are within a section
            if current_section == "Model":
                parts = [p.strip() for p in line.split("  ") if p.strip()]
                if len(parts) >= 2:
                    key = parts[0].lower()
                    val = parts[1]
                    if key == "context length":
                        try:
                            metadata["context_length"] = int(val)
                        except ValueError:
                            pass
                    elif key == "parameters":
                        metadata["parameters"] = val
                    elif key == "architecture":
                        metadata["architecture"] = val
            elif current_section == "Capabilities":
                metadata["capabilities"].append(stripped.lower())
                
        return metadata
    except Exception as e:
        logger.debug("Failed to get model metadata via ollama show for %s: %s", model_name, e)
        return {}


def get_model_size_from_manifest(model: str) -> float | None:
    """Retrieve model file size in GB from local manifest file if it exists."""
    import json
    import os
    from pathlib import Path

    try:
        # Extract tag and namespace
        if ":" in model:
            model_part, tag = model.split(":", 1)
        else:
            model_part, tag = model, "latest"
            
        if "/" in model_part:
            namespace, base = model_part.split("/", 1)
        else:
            namespace, base = "library", model_part
            
        env_models = os.environ.get("OLLAMA_MODELS")
        if env_models:
            base_dir = Path(env_models)
        else:
            base_dir = Path.home() / ".ollama" / "models"
            
        manifest_file = base_dir / "manifests" / "registry.ollama.ai" / namespace / base / tag
        if manifest_file.exists():
            data = json.loads(manifest_file.read_text())
            for layer in data.get("layers", []):
                if layer.get("mediaType") == "application/vnd.ollama.image.model":
                    size_bytes = layer.get("size", 0)
                    return size_bytes / (1024 ** 3)
    except Exception as e:
        logger.debug("Failed to parse manifest size for %s: %s", model, e)
    return None


def parse_parameter_size_from_name(model_name: str) -> float | None:
    """Try to parse the parameter count (in Billions) from a model name or tag."""
    import re
    match = re.search(r'(\d+(?:\.\d+)?)[bB]', model_name)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def is_arbitrary_model_eligible(model_name: str, ram_gb: float, free_disk_gb: float) -> tuple[bool, str]:
    """Evaluate if an arbitrary globally installed model is suitable and safe to reuse."""
    # 1. Fetch metadata via filesystem manifest size first
    size_gb = get_model_size_from_manifest(model_name)
    
    # 2. Get max safe limit based on system RAM
    max_safe_size = get_max_safe_model_size_gb(ram_gb)
    
    if size_gb is not None:
        if size_gb > max_safe_size:
            return False, f"Model size ({size_gb:.1f} GB) exceeds safe hardware limit for your RAM ({max_safe_size:.1f} GB limit)"
    else:
        # If we couldn't get manifest size, try to parse parameters from the name
        params = parse_parameter_size_from_name(model_name)
        if params is not None:
            estimated_size = params * 0.7
            if estimated_size > max_safe_size:
                return False, f"Model parameters ({params:.1f}B, est. {estimated_size:.1f} GB) exceed safe hardware limit ({max_safe_size:.1f} GB limit)"
                
    # 3. Query capabilities and context length via 'ollama show'
    metadata = get_model_metadata(model_name)
    if metadata:
        arch = (metadata.get("architecture") or "").lower()
        if "embed" in arch or "bert" in arch:
            return False, f"Model architecture '{arch}' is suitable for embeddings only, not text completion"
            
        caps = metadata.get("capabilities") or []
        if caps and "completion" not in caps:
            return False, "Model does not support text completion capability"
            
        ctx = metadata.get("context_length")
        if ctx is not None and ctx < 2048:
            return False, f"Model context length ({ctx}) is below the required 2048 tokens minimum"
            
    # Standard security & sanity checks on the model name
    model_lower = model_name.lower()
    if "embed" in model_lower or "rerank" in model_lower or "bge-" in model_lower:
        return False, "Model name suggests it is an embedding/reranking model only"
        
    return True, "Passed strict capability and hardware-safety checks"


def select_best_slm(config: Config | None = None) -> tuple[str, str]:
    """Select the best local SLM based on system resources and pre-installed models.

    Returns tuple of (model_name, decision_reason).
    """
    config = config or Config()

    # Check if a model is explicitly pinned/configured by the user
    user_configured = config.get("slm_model")
    if user_configured and user_configured != "gemma3:1b":
        # If the user explicitly set some other model, respect it
        return user_configured, f"User explicitly configured model '{user_configured}'"

    ram_gb = get_system_ram_gb()
    free_disk = get_free_disk_gb()
    installed = get_installed_ollama_models()

    logger.debug("System RAM: %.1f GB, Free Disk: %.1f GB", ram_gb, free_disk)
    logger.debug("Installed Ollama models: %s", installed)

    # 1. Prefer pre-installed models that we know work well
    # Group all candidate models across all tiers
    all_candidates = []
    for tier in ["large", "medium", "small", "tiny"]:
        for m in SLM_TIERS[tier]["models"]:
            if m not in all_candidates:
                all_candidates.append(m)

    for candidate in all_candidates:
        # Check if the exact candidate or a matching tag name is installed
        if any(candidate in inst or inst in candidate for inst in installed):
            # Resolve the installed name
            installed_name = next(
                inst for inst in installed if candidate in inst or inst in candidate
            )
            return installed_name, f"Reusing already-installed compatible model '{installed_name}'"

    # If the user has some other models installed that we didn't list but might be compatible,
    # let's look for any generic qwen2.5 or gemma3 models
    for inst in installed:
        inst_lower = inst.lower()
        has_compat = (
            "gemma3:1b" in inst_lower or
            "gemma3:4b" in inst_lower or
            "qwen2.5:0.5b" in inst_lower or
            "qwen2.5:1.5b" in inst_lower
        )
        if has_compat:
            return inst, f"Reusing existing installed compatible model '{inst}'"

    # 3. Check arbitrary installed models with a strict capability/hardware bar
    eligible_arbitrary_models = []
    for inst in installed:
        # Skip if already handled by the compatible checks above
        is_known = any(c in inst or inst in c for c in all_candidates)
        if is_known:
            continue
            
        eligible, reason = is_arbitrary_model_eligible(inst, ram_gb, free_disk)
        if eligible:
            size_gb = get_model_size_from_manifest(inst) or parse_parameter_size_from_name(inst) or 0.0
            eligible_arbitrary_models.append((inst, size_gb))
            
    if eligible_arbitrary_models:
        # Sort by size descending to pick the most capable eligible model
        eligible_arbitrary_models.sort(key=lambda x: x[1], reverse=True)
        best_arbitrary = eligible_arbitrary_models[0][0]
        return best_arbitrary, f"Reusing globally installed model '{best_arbitrary}' passing strict capability and hardware-safety checks"

    # 4. No pre-installed compatible models found. Select candidate based on system hardware resources
    if ram_gb >= 24.0 and free_disk >= SLM_TIERS["large"]["required_disk_gb"]:
        selected = SLM_TIERS["large"]["models"][0]
        reason = (
            f"High system config detected (RAM: {ram_gb:.1f}GB, Free Disk: {free_disk:.1f}GB). "
            f"Selecting high-tier model '{selected}'."
        )
    elif ram_gb >= 12.0 and free_disk >= SLM_TIERS["medium"]["required_disk_gb"]:
        selected = SLM_TIERS["medium"]["models"][0]
        reason = (
            f"Medium system config detected (RAM: {ram_gb:.1f}GB, Free Disk: {free_disk:.1f}GB). "
            f"Selecting medium-tier model '{selected}'."
        )
    elif ram_gb >= 6.0 and free_disk >= SLM_TIERS["small"]["required_disk_gb"]:
        selected = SLM_TIERS["small"]["models"][0]
        reason = (
            f"Standard system config detected (RAM: {ram_gb:.1f}GB, Free Disk: {free_disk:.1f}GB). "
            f"Selecting standard model '{selected}'."
        )
    else:
        selected = SLM_TIERS["tiny"]["models"][0]
        reason = (
            f"Constrained system resources detected (RAM: {ram_gb:.1f}GB, Free Disk: {free_disk:.1f}GB). "
            f"Selecting tiny lightweight model '{selected}'."
        )

    return selected, reason
