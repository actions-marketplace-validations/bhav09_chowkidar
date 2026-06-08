"""GitHub Action entrypoint for Chowkidar."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from chowkidar.config import Config
from chowkidar.registry.db import Registry
from chowkidar.scanner import scan_directory
from chowkidar.scanner.patterns import is_model_string
from chowkidar.scanner import _normalize_with_framework
from chowkidar.sentinel.webhook import send_webhook


def run_action() -> None:
    parser = argparse.ArgumentParser(description="Chowkidar GitHub Action")
    parser.path_args = parser.add_argument(
        "--path",
        default=".",
        help="Path to scan",
    )
    args = parser.parse_args()

    # Read environment variables
    secrets_json = os.environ.get("CHOWKIDAR_SECRETS_JSON", "{}")
    vars_json = os.environ.get("CHOWKIDAR_VARS_JSON", "{}")
    slack_webhook = os.environ.get("CHOWKIDAR_SLACK_WEBHOOK", "")
    discord_webhook = os.environ.get("CHOWKIDAR_DISCORD_WEBHOOK", "")
    auto_update = os.environ.get("CHOWKIDAR_AUTO_UPDATE", "false").lower() == "true"
    gate = os.environ.get("CHOWKIDAR_GATE", "false").lower() == "true"
    environment = os.environ.get("CHOWKIDAR_ENVIRONMENT", "production")

    print(f"--- Chowkidar LLM Deprecation Watchdog (CI/CD Action) ---")
    print(f"Path: {args.path}")
    print(f"Environment: {environment}")
    print(f"Auto-Update: {auto_update}")
    print(f"Gate: {gate}")

    # Initialize Registry
    registry = Registry()
    registry.init_db()

    # 1. Scan Repository Files
    target_path = Path(args.path).resolve()
    scan_result = scan_directory(target_path)
    all_models = scan_result.all_models

    # 2. In-Memory Secrets & Variables Scanning
    virtual_entries = []
    for source_name, json_str in [("secrets", secrets_json), ("vars", vars_json)]:
        if json_str:
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                     for k, v in data.items():
                         # Redact values in logs, but scan them in-memory
                         if isinstance(v, str) and is_model_string(v):
                             virtual_entries.append({
                                 "file": f"GitHub {source_name}",
                                 "variable": k,
                                 "model": v,
                                 "canonical": _normalize_with_framework(v),
                                 "source_type": "in_memory",
                             })
            except Exception as e:
                print(f"::error::Failed to parse {source_name}_json: {e}")

    all_models.extend(virtual_entries)

    if not all_models:
        print("No active LLM model references found in files or in-memory variables.")
        registry.close()
        return

    deprecated_count = 0
    critical_count = 0
    sunset_count = 0

    print(f"Auditing {len(all_models)} model references...")

    for m in all_models:
        canonical = m["canonical"]
        record = registry.get_model(canonical)

        # Redact the model value if it's from secrets/in-memory to prevent leakage in logs
        display_model = m["model"]
        if m.get("source_type") == "in_memory":
            display_model = "[REDACTED]"

        if record is None:
            continue

        if record.sunset_date is None:
            continue

        deprecated_count += 1
        try:
            sunset = datetime.fromisoformat(record.sunset_date)
            if sunset.tzinfo is not None:
                days_until = (sunset - datetime.now(timezone.utc)).days
            else:
                days_until = (sunset - datetime.now()).days
        except ValueError:
            days_until = None

        status = "deprecating"
        urgency = "low"
        if days_until is not None:
            if days_until <= 0:
                status = "SUNSET"
                urgency = "critical"
                sunset_count += 1
            elif days_until <= 7:
                status = "critical"
                urgency = "critical"
                critical_count += 1
            elif days_until <= 30:
                status = "warning"
                urgency = "normal"
                critical_count += 1

        # Print GitHub Workflow Annotation
        is_virtual = m.get("source_type") == "in_memory"
        location = f"{m['file']}"
        if not is_virtual:
            # Try to find relative path
            try:
                location = str(Path(m["file"]).relative_to(target_path))
            except Exception:
                pass

        annotation_type = "warning" if urgency != "critical" else "error"
        msg = f"[Chowkidar] Model '{display_model}' ({canonical}) in {location} -> {m['variable']} is {status}. Sunset: {record.sunset_date} ({days_until} days left)."
        
        if is_virtual:
            print(f"::{annotation_type}::{msg}")
        else:
            print(f"::{annotation_type} file={location},line=1::{msg}")

    # 3. Webhook Alerting
    if (sunset_count > 0 or critical_count > 0 or deprecated_count > 0) and (slack_webhook or discord_webhook):
        title = f"Chowkidar LLM Deprecation Alert [{environment.upper()}]"
        message = (
            f"Chowkidar has detected deprecated LLM models in the *{environment}* environment:\n"
            f"• *Sunset/Expired*: {sunset_count}\n"
            f"• *Critical (<30 days)*: {critical_count}\n"
            f"• *Total Deprecated*: {deprecated_count}\n\n"
            f"Please review the GitHub Action annotations or run `chowkidar check` locally to apply fixes."
        )
        if slack_webhook:
            send_webhook(slack_webhook, title, message, "critical" if sunset_count > 0 else "normal", "slack")
        if discord_webhook:
            send_webhook(discord_webhook, title, message, "critical" if sunset_count > 0 else "normal", "discord")

    registry.close()

    # 4. CI Build Gate
    if gate and (sunset_count > 0 or critical_count > 0):
        print(f"\n::error::[Chowkidar Gate] Build failed because {sunset_count + critical_count} critical/sunset models were found.")
        sys.exit(1)

    print("\nChowkidar scan completed successfully.")


if __name__ == "__main__":
    run_action()
