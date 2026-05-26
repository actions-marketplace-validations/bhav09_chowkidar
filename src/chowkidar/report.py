"""Report generation — HTML, JSON, and Markdown deprecation reports."""

from __future__ import annotations

import json
import html
from datetime import datetime, timezone
from pathlib import Path

from .deployment import detect_deployment
from .pricing import compare_cost
from .recommendations import build_recommendation
from .registry.db import Registry
from .scanner import scan_directory


def generate_report(
    project_paths: list[Path],
    output_format: str = "markdown",
    registry: Registry | None = None,
    redact_paths: bool = False,
) -> str:
    """Generate a deprecation report across one or more projects."""
    if registry is None:
        registry = Registry()
        registry.init_db()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    projects_data: list[dict] = []

    for project_path in project_paths:
        scan_result = scan_directory(project_path)
        deployment = detect_deployment(project_path)
        models_data: list[dict] = []

        for m in scan_result.all_models:
            canonical = m["canonical"]
            record = registry.get_model(canonical)
            
            file_display = m["file"]
            if redact_paths and m["file"]:
                try:
                    file_display = str(Path(m["file"]).relative_to(project_path))
                except ValueError:
                    file_display = Path(m["file"]).name

            entry: dict = {
                "variable": m["variable"],
                "model": m["model"],
                "file": file_display,
                "canonical": canonical,
                "status": "active",
                "sunset_date": None,
                "days_until": None,
                "replacement": None,
                "cost_summary": None,
                "recommendation": None,
                "manual_review_required": False,
                "risk_summary": None,
            }

            if record and record.sunset_date:
                entry["sunset_date"] = record.sunset_date
                entry["replacement"] = record.replacement
                recommendation = build_recommendation(canonical, record, registry=registry, variable_name=m["variable"], file_path=m["file"])
                entry["recommendation"] = recommendation.to_dict()
                entry["manual_review_required"] = recommendation.manual_review_required
                risks = recommendation.commercial_risks + recommendation.future_risks + recommendation.privacy_risks
                entry["risk_summary"] = "; ".join(risks) if risks else recommendation.risk
                try:
                    sunset = datetime.fromisoformat(record.sunset_date)
                    entry["days_until"] = (sunset - now).days
                except ValueError:
                    pass

                if entry["days_until"] is not None:
                    if entry["days_until"] <= 0:
                        entry["status"] = "sunset"
                    elif entry["days_until"] <= 7:
                        entry["status"] = "critical"
                    elif entry["days_until"] <= 30:
                        entry["status"] = "warning"
                    else:
                        entry["status"] = "deprecating"

                if record.replacement:
                    cost = compare_cost(canonical, record.replacement)
                    if cost:
                        entry["cost_summary"] = cost.summary

            models_data.append(entry)

        projects_data.append({
            "path": f"[REDACTED]/{project_path.name}" if redact_paths else str(project_path),
            "name": project_path.name,
            "total_models": scan_result.total_count,
            "deployment": deployment.to_dict(),
            "models": models_data,
        })

    sync_statuses = registry.get_sync_statuses() if registry else {}

    if output_format == "json":
        return _render_json(projects_data, now, sync_statuses)
    elif output_format == "html":
        return _render_html(projects_data, now, sync_statuses)
    else:
        return _render_markdown(projects_data, now, sync_statuses)


def _render_json(projects_data: list[dict], now: datetime, sync_statuses: dict[str, dict] = None) -> str:
    return json.dumps({
        "generated_at": now.isoformat(),
        "sync_statuses": sync_statuses or {},
        "projects": projects_data,
    }, indent=2)


def _render_markdown(projects_data: list[dict], now: datetime, sync_statuses: dict[str, dict] = None) -> str:
    lines: list[str] = [
        "# Chowkidar Deprecation Report",
        f"*Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
    ]

    if sync_statuses:
        lines.append("### Provider Sync Status")
        lines.append("| Provider | Last Success | Last Failure | Failure Reason |")
        lines.append("|----------|--------------|--------------|----------------|")
        for prov, stat in sync_statuses.items():
            success = stat.get("last_success_at") or "never"
            failure = stat.get("last_failure_at") or "never"
            reason = stat.get("failure_reason") or "-"
            lines.append(f"| {prov} | {success} | {failure} | {reason} |")
        lines.append("")

    for proj in projects_data:
        lines.append(f"## {proj['name']}")
        lines.append(f"Path: `{proj['path']}`")
        deployment = proj.get("deployment", {})
        lines.append(
            f"Deployment signals: **{deployment.get('state', 'none')}** "
            f"(confidence {deployment.get('confidence', 0.0)})"
        )
        lines.append("")

        deprecated = [m for m in proj["models"] if m["status"] != "active"]
        if not deprecated:
            lines.append("No deprecated models found.")
            lines.append("")
            continue

        lines.append("| Variable | Model | Status | Sunset | Days | Replacement | Cost | Review |")
        lines.append("|----------|-------|--------|--------|------|-------------|------|--------|")
        for m in deprecated:
            days = str(m["days_until"]) if m["days_until"] is not None else "?"
            repl = m["replacement"] or "-"

            # Format Elo benchmarks if available
            rec = m.get("recommendation")
            if rec and rec.get("benchmark_comparison"):
                from .benchmarks import format_benchmark_delta_markdown
                suffix = format_benchmark_delta_markdown(rec["benchmark_comparison"])
                repl = f"{repl}{suffix}"

            cost = m.get("cost_summary", "") or "-"
            review = "required" if m.get("manual_review_required") else "not required"
            lines.append(f"| {m['variable']} | {m['model']} | {m['status']} "
                         f"| {m['sunset_date'] or '-'} | {days} | {repl} | {cost} | {review} |")
        lines.append("")

    # Generate Cross-Family Recommendations Appendix if any are found
    has_alternatives = False
    appendix_lines = [
        "---",
        "## Appendix: Cross-Family Alternative Recommendations",
        "For models facing deprecation, Chowkidar matches legacy identifiers with equivalent-tier active models from other provider families.",
        ""
    ]

    for proj in projects_data:
        deprecated = [m for m in proj["models"] if m["status"] != "active"]
        for m in deprecated:
            rec = m.get("recommendation")
            if rec and rec.get("cross_family_recommendations"):
                has_alternatives = True
                appendix_lines.append(f"### Alternatives for `{m['variable']}` ({m['model']})")
                appendix_lines.append(f"* **File / Location**: `{m['file']}`")
                appendix_lines.append("")
                
                for alt in rec["cross_family_recommendations"]:
                    appendix_lines.append(f"#### Provider: **{alt['provider'].upper()}** — `{alt['model']}`")
                    appendix_lines.append(f"*{alt['reason']}*")
                    appendix_lines.append("")
                    appendix_lines.append("| Capability | Old Value | New Value | Delta |")
                    appendix_lines.append("|------------|-----------|-----------|-------|")
                    
                    for d in alt.get("capability_diffs", []):
                        label = d["label"]
                        old_val = d["old_value"]
                        new_val = d["new_value"]
                        ctype = d["change_type"]
                        appendix_lines.append(f"| {label} | {old_val} | {new_val} | {ctype} |")
                    appendix_lines.append("")

    if has_alternatives:
        lines.extend(appendix_lines)

    return "\n".join(lines)


def _render_html(projects_data: list[dict], now: datetime, sync_statuses: dict[str, dict] = None) -> str:
    status_colors = {
        "sunset": "#dc3545",
        "critical": "#fd7e14",
        "warning": "#ffc107",
        "deprecating": "#6c757d",
        "active": "#28a745",
    }

    rows_html: list[str] = []
    projects_html: list[str] = []
    
    prov_rows = ""
    if sync_statuses:
        for prov, stat in sync_statuses.items():
            success = stat.get("last_success_at") or "never"
            failure = stat.get("last_failure_at") or "never"
            reason = stat.get("failure_reason") or "-"
            prov_rows += f"<tr><td>{html.escape(prov)}</td><td>{html.escape(success)}</td><td>{html.escape(failure)}</td><td>{html.escape(reason)}</td></tr>"
    else:
        prov_rows = '<tr><td colspan="4" style="text-align:center;">No sync status recorded.</td></tr>'
    
    for proj in projects_data:
        dep = proj.get("deployment", {})
        state = dep.get("state", "none")
        conf = dep.get("confidence", 0.0)
        signals = dep.get("signals", [])
        
        dep_color = "#6c757d"
        if state == "likely":
            dep_color = "#dc3545"
        elif state == "possible":
            dep_color = "#ffc107"
        elif state == "confirmed":
            dep_color = "#28a745"
            
        signals_li = ""
        if signals:
            signals_li = '<ul style="margin: 0.5rem 0; padding-left: 1.2rem;">' + "".join(
                f"<li><code>{html.escape(s.get('adapter', '').upper())}</code>: {html.escape(str(s.get('evidence', '')))} (strength {html.escape(str(s.get('strength', '')))}) in <code>{html.escape(Path(s.get('file_path', '')).name)}</code></li>"
                for s in signals
            ) + "</ul>"
        else:
            signals_li = "<p style='margin:0; font-style:italic;'>No local deployment signals detected.</p>"
            
        projects_html.append(
            f'<div style="border: 1px solid var(--border-color); border-radius: 6px; padding: 1rem; margin: 1rem 0; background-color: var(--header-bg);">'
            f'  <h3 style="margin: 0 0 0.5rem 0; font-size: 1.15em;">Project: <span style="color: var(--highlight-var-color);">{html.escape(proj["name"])}</span></h3>'
            f'  <p style="margin: 0.25rem 0; font-size: 0.9em;">Local Path: <code>{html.escape(proj["path"])}</code></p>'
            f'  <p style="margin: 0.25rem 0; font-size: 0.9em;">'
            f'    Deployment Signals: <span class="badge" style="background-color: {dep_color}; color: #ffffff; font-weight: bold;">{html.escape(state)}</span> '
            f'    (confidence score: <b>{conf}</b>)'
            f'  </p>'
            f'  <div style="font-size: 0.85em; color: #6c757d; margin-top: 0.5rem; border-top: 1px solid var(--border-color); padding-top: 0.5rem;">'
            f'    {signals_li}'
            f'  </div>'
            f'</div>'
        )

        for m in proj["models"]:
            if m["status"] == "active":
                continue
            color = status_colors.get(m["status"], "#6c757d")
            days = str(m["days_until"]) if m["days_until"] is not None else "?"
            repl = m["replacement"] or "-"
            rec = m.get("recommendation")
            if rec and rec.get("benchmark_comparison"):
                from .benchmarks import format_benchmark_delta_html
                repl = format_benchmark_delta_html(rec["benchmark_comparison"], repl)
            else:
                repl = f"<b>{html.escape(repl)}</b>"

            cost = m.get("cost_summary", "") or "-"
            review = "Yes" if m.get("manual_review_required") else "No"
            risk = m.get("risk_summary") or "-"
            
            action_btn = ""
            if m["file"]:
                action_btn += (
                    f'<button class="btn-action btn-open-editor" data-file-path="{html.escape(m["file"])}" style="margin-right: 0.5rem; margin-bottom: 0.25rem;">'
                    f'✏️ Open in Editor</button>'
                )
            
            detail_id = f"details-{proj['name']}-{m['variable']}".replace(".", "-").replace("[", "-").replace("]", "-")
            if rec and rec.get("cross_family_recommendations"):
                action_btn += (
                    f'<button class="btn-action btn-toggle-details" data-target="{html.escape(detail_id)}" style="background-color: #198754; border-color: #198754; margin-bottom: 0.25rem;">'
                    f'📋 Hide Alternatives</button>'
                )
            elif not action_btn:
                action_btn = "-"

            rows_html.append(
                f"<tr>"
                f"<td>{html.escape(proj['name'])}</td>"
                f"<td><code class='highlight-var'>{html.escape(m['variable'])}</code></td>"
                f"<td><code class='highlight-model'>{html.escape(m['model'])}</code></td>"
                f'<td><span class="badge" style="background-color:{color};'
                f'color:#ffffff;font-weight:bold">{html.escape(m["status"])}</span></td>'
                f"<td>{html.escape(m['sunset_date'] or '-')}</td>"
                f"<td>{html.escape(days)}</td>"
                f"<td>{repl}</td>"
                f"<td>{html.escape(cost)}</td>"
                f"<td>{html.escape(review)}</td>"
                f"<td>{html.escape(risk)}</td>"
                f"<td>{action_btn}</td>"
                f"</tr>"
            )

            # Collapsible details row showing cross-family recommendations
            if rec and rec.get("cross_family_recommendations"):
                alts_html = []
                
                # Use case specific headers & benchmarks info
                use_case = rec.get("use_case", "chat/general")
                if use_case == "coding":
                    uc_label = "Coding & Software Engineering"
                    uc_badge_color = "#6f42c1"  # purple
                    uc_benchmarks = "Prioritized Benchmarks: <b>HumanEval</b> (pass@1 python accuracy) and <b>SWE-bench</b>."
                elif use_case == "agents/reasoning":
                    uc_label = "Agents & Deep Reasoning"
                    uc_badge_color = "#fd7e14"  # orange
                    uc_benchmarks = "Prioritized Benchmarks: <b>MATH</b>, <b>GPQA</b> (Graduate-Level Q&A), and <b>MMLU-Pro</b>."
                elif use_case == "embeddings/search":
                    uc_label = "Embeddings & Vector Search"
                    uc_badge_color = "#20c997"  # teal
                    uc_benchmarks = "Prioritized Benchmarks: <b>MTEB</b> (Massive Text Embedding Benchmark)."
                elif use_case == "extraction/structured":
                    uc_label = "Data Extraction & Parsing"
                    uc_badge_color = "#0dcaf0"  # cyan
                    uc_benchmarks = "Prioritized Benchmarks: <b>JSON Mode / Tool Schema accuracy</b>."
                elif use_case == "tests/eval":
                    uc_label = "Testing & Mock Evaluation"
                    uc_badge_color = "#0d6efd"  # blue
                    uc_benchmarks = "Prioritized Benchmarks: <b>Cost/million</b> and <b>Inference latency</b>."
                else:
                    uc_label = "General Conversation & Chat"
                    uc_badge_color = "#6c757d"  # gray
                    uc_benchmarks = "Prioritized Benchmarks: <b>LMSYS Chatbot Arena Elo</b> and <b>MMLU</b>."

                # First, if there's a primary provider recommended replacement, show its detailed capabilities
                if m["replacement"] and m["replacement"] != "-":
                    primary_provider = m["replacement"].split("/")[0] if "/" in m["replacement"] else "provider"
                    primary_diffs = []
                    for d in rec.get("capability_diffs", []):
                        label = html.escape(d["label"])
                        old_val = html.escape(d["old_value"])
                        new_val = html.escape(d["new_value"])
                        ctype = d["change_type"]
                        
                        if ctype in ("improved", "gained"):
                            color_style = "color: #198754; font-weight: bold;"
                            arrow = "──&gt;"
                        elif ctype in ("degraded", "lost"):
                            color_style = "color: #dc3545; font-weight: bold;"
                            arrow = "──&gt;"
                        else:
                            color_style = "color: #6c757d;"
                            arrow = "=="
                        
                        primary_diffs.append(
                            f'<li style="margin: 0.25rem 0; {color_style}">'
                            f'{label}: {old_val} {arrow} {new_val} ({ctype})'
                            f'</li>'
                        )
                    primary_diffs_html = "".join(primary_diffs) if primary_diffs else "<li style='color: #6c757d;'>No capability changes</li>"
                    
                    primary_cost = m.get("cost_summary") or "-"
                    if "saves" in primary_cost.lower():
                        cost_badge_color = "#198754"
                    elif "costs" in primary_cost.lower() or "more" in primary_cost.lower():
                        cost_badge_color = "#dc3545"
                    else:
                        cost_badge_color = "#6c757d"

                    primary_card = (
                        f'<div class="alt-card" style="border: 2px solid #198754; border-radius: 6px; padding: 1rem; background-color: var(--bg-color); box-shadow: 0 2px 4px rgba(25,135,84,0.1); position: relative;">'
                        f'  <div style="position: absolute; top: -10px; right: 10px; background-color: #198754; color: white; font-size: 0.7em; font-weight: bold; padding: 0.2rem 0.5rem; border-radius: 4px;">PRIMARY SUCCESSOR</div>'
                        f'  <h4 style="margin: 0 0 0.5rem 0; font-size: 1.05em; display: flex; align-items: center; gap: 0.5rem;">'
                        f'    <span class="badge" style="background-color: #198754; color: white; padding: 0.2em 0.5em; border-radius: 4px; font-size: 0.75em;">{html.escape(primary_provider.upper())}</span>'
                        f'    <code style="color: var(--highlight-model-color); font-weight: bold;">{html.escape(m["replacement"])}</code>'
                        f'  </h4>'
                        f'  <p style="font-size: 0.9em; margin: 0 0 0.5rem 0; line-height: 1.4; color: var(--text-color); opacity: 0.85;">{html.escape(rec.get("reason", "Official provider recommended successor model."))}</p>'
                        f'  <p style="font-size: 0.85em; margin: 0 0 0.75rem 0; color: var(--text-color);">💰 Cost: <span class="badge" style="background-color: {cost_badge_color}; color: white; padding: 0.25em 0.5em; border-radius: 4px; font-size: 0.85em; text-transform: none; font-weight: bold;">{html.escape(primary_cost)}</span></p>'
                        f'  <div style="font-size: 0.85em; border-top: 1px dashed var(--border-color); padding-top: 0.5rem;">'
                        f'    <strong style="display: block; margin-bottom: 0.25rem; font-size: 0.9em;">Capability Shifts:</strong>'
                        f'    <ul style="margin: 0; padding-left: 1.2rem; list-style-type: square;">'
                        f'      {primary_diffs_html}'
                        f'    </ul>'
                        f'  </div>'
                        f'</div>'
                    )
                    alts_html.append(primary_card)

                for alt in rec["cross_family_recommendations"]:
                    diff_items = []
                    for d in alt.get("capability_diffs", []):
                        label = html.escape(d["label"])
                        old_val = html.escape(d["old_value"])
                        new_val = html.escape(d["new_value"])
                        ctype = d["change_type"]
                        
                        if ctype in ("improved", "gained"):
                            color_style = "color: #198754; font-weight: bold;"
                            arrow = "──&gt;"
                        elif ctype in ("degraded", "lost"):
                            color_style = "color: #dc3545; font-weight: bold;"
                            arrow = "──&gt;"
                        else:
                            color_style = "color: #6c757d;"
                            arrow = "=="
                        
                        diff_items.append(
                            f'<li style="margin: 0.25rem 0; {color_style}">'
                            f'{label}: {old_val} {arrow} {new_val} ({ctype})'
                            f'</li>'
                        )
                    
                    diffs_list_html = "".join(diff_items) if diff_items else "<li style='color: #6c757d;'>No capability changes</li>"
                    
                    alt_cost = alt.get("cost_summary", "No pricing data")
                    if "saves" in alt_cost.lower():
                        alt_cost_color = "#198754"
                    elif "costs" in alt_cost.lower() or "more" in alt_cost.lower():
                        alt_cost_color = "#dc3545"
                    else:
                        alt_cost_color = "#6c757d"

                    alt_card = (
                        f'<div class="alt-card" style="border: 1px solid var(--border-color); border-radius: 6px; padding: 1rem; background-color: var(--bg-color); box-shadow: 0 1px 3px rgba(0,0,0,0.05);">'
                        f'  <h4 style="margin: 0 0 0.5rem 0; font-size: 1.05em; display: flex; align-items: center; gap: 0.5rem;">'
                        f'    <span class="badge" style="background-color: #0d6efd; color: white; padding: 0.2em 0.5em; border-radius: 4px; font-size: 0.75em;">{html.escape(alt["provider"].upper())}</span>'
                        f'    <code style="color: var(--highlight-model-color);">{html.escape(alt["model"])}</code>'
                        f'  </h4>'
                        f'  <p style="font-size: 0.9em; margin: 0 0 0.5rem 0; line-height: 1.4; color: var(--text-color); opacity: 0.85;">{html.escape(alt["reason"])}</p>'
                        f'  <p style="font-size: 0.85em; margin: 0 0 0.75rem 0; color: var(--text-color);">💰 Cost: <span class="badge" style="background-color: {alt_cost_color}; color: white; padding: 0.25em 0.5em; border-radius: 4px; font-size: 0.85em; text-transform: none; font-weight: bold;">{html.escape(alt_cost)}</span></p>'
                        f'  <div style="font-size: 0.85em; border-top: 1px dashed var(--border-color); padding-top: 0.5rem;">'
                        f'    <strong style="display: block; margin-bottom: 0.25rem; font-size: 0.9em;">Capability Shifts:</strong>'
                        f'    <ul style="margin: 0; padding-left: 1.2rem; list-style-type: square;">'
                        f'      {diffs_list_html}'
                        f'    </ul>'
                        f'  </div>'
                        f'</div>'
                    )
                    alts_html.append(alt_card)
                
                grid_html = "".join(alts_html)
                
                details_row = (
                    f'<tr id="{html.escape(detail_id)}" class="details-row" style="display: table-row; background-color: var(--header-bg);">'
                    f'  <td colspan="11" style="padding: 1.5rem; border-top: none;">'
                    f'    <div style="border-left: 4px solid #198754; padding-left: 1rem;">'
                    f'      <h3 style="margin: 0 0 0.75rem 0; font-size: 1.1em; color: #198754; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">'
                    f'        🔄 Use-Case Aware Alternatives for <code>{html.escape(m["model"])}</code>'
                    f'        <span class="badge" style="background-color: {uc_badge_color}; color: white; font-size: 0.65em; padding: 0.25em 0.6em; border-radius: 4px; text-transform: none; font-weight: bold;">Use Case: {html.escape(uc_label)}</span>'
                    f'      </h3>'
                    f'      <p style="font-size: 0.9em; margin: 0 0 1rem 0; color: #6c757d;">'
                    f'        Chowkidar has analyzed your usage in the codebase and identified this reference\'s specific context and purpose. '
                    f'        {uc_benchmarks}'
                    f'      </p>'
                    f'      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.25rem;">'
                    f'        {grid_html}'
                    f'      </div>'
                    f'    </div>'
                    f'  </td>'
                    f'</tr>'
                )
                rows_html.append(details_row)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chowkidar Deprecation Report</title>
<style>
  :root {{
    --bg-color: #ffffff;
    --text-color: #1a1a2e;
    --border-color: #dee2e6;
    --header-bg: #f8f9fa;
    --row-even-bg: #f8f9fa;
    --highlight-var-color: #0d6efd;
    --highlight-model-color: #d63384;
    --btn-bg: #0d6efd;
    --btn-hover: #0b5ed7;
    --toast-success: #198754;
    --toast-error: #dc3545;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg-color: #121212;
      --text-color: #e0e0e0;
      --border-color: #333333;
      --header-bg: #1a1a1a;
      --row-even-bg: #181a1b;
      --highlight-var-color: #58a6ff;
      --highlight-model-color: #ff7b72;
      --btn-bg: #21262d;
      --btn-hover: #30363d;
    }}
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 1200px;
    margin: 2rem auto;
    padding: 0 1.5rem;
    color: var(--text-color);
    background-color: var(--bg-color);
    line-height: 1.5;
  }}
  h1 {{
    border-bottom: 3px solid #e63946;
    padding-bottom: 0.5rem;
    margin-bottom: 0.5rem;
  }}
  .meta {{
    color: #6c757d;
    font-size: 0.9em;
    margin-bottom: 2rem;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 1.5rem 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }}
  th, td {{
    padding: 0.75rem 1rem;
    border: 1px solid var(--border-color);
    text-align: left;
  }}
  th {{
    background-color: var(--header-bg);
    font-weight: 600;
  }}
  tr:nth-child(even) {{
    background-color: var(--row-even-bg);
  }}
  code {{
    background-color: rgba(175, 184, 193, 0.2);
    padding: 0.2rem 0.4rem;
    border-radius: 4px;
    font-size: 0.9em;
  }}
  .highlight-var {{
    color: var(--highlight-var-color);
    font-weight: 600;
  }}
  .highlight-model {{
    color: var(--highlight-model-color);
    font-weight: 600;
  }}
  .badge {{
    display: inline-block;
    padding: 0.25em 0.6em;
    font-size: 0.75em;
    font-weight: 700;
    line-height: 1;
    text-align: center;
    white-space: nowrap;
    vertical-align: baseline;
    border-radius: 0.25rem;
    text-transform: uppercase;
  }}
  .btn-action {{
    background-color: var(--btn-bg);
    color: #ffffff;
    border: 1px solid var(--border-color);
    padding: 0.4rem 0.8rem;
    font-size: 0.85em;
    border-radius: 4px;
    cursor: pointer;
    font-weight: 500;
    transition: background-color 0.2s;
  }}
  .btn-action:hover {{
    background-color: var(--btn-hover);
  }}
  /* Toast styles */
  .toast {{
    position: fixed;
    bottom: 20px;
    right: 20px;
    padding: 1rem 1.5rem;
    border-radius: 4px;
    color: #ffffff;
    font-weight: 500;
    opacity: 0;
    transition: opacity 0.3s ease-in-out;
    z-index: 1000;
  }}
  .toast-success {{
    background-color: var(--toast-success);
  }}
  .toast-error {{
    background-color: var(--toast-error);
  }}
</style>
</head>
<body>
<h1>Chowkidar Deprecation Report</h1>
<p class="meta">Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}</p>

<h2>Provider Sync Status</h2>
<table>
<thead>
<tr>
  <th>Provider</th>
  <th>Last Success</th>
  <th>Last Failure</th>
  <th>Failure Reason</th>
</tr>
</thead>
<tbody>
{prov_rows}
</tbody>
</table>

<h2>Workspace / Project Summary</h2>
{"".join(projects_html)}

<h2>Deprecated Models</h2>
<table>
<thead>
<tr>
  <th>Project</th>
  <th>Variable</th>
  <th>Model</th>
  <th>Status</th>
  <th>Sunset Date</th>
  <th>Days Left</th>
  <th>Replacement</th>
  <th>Cost Impact</th>
  <th>Manual Review</th>
  <th>Risk</th>
  <th>Action</th>
</tr>
</thead>
<tbody>
{"".join(rows_html) if rows_html else
 '<tr><td colspan="11" style="text-align:center;">No deprecated models found.</td></tr>'}
</tbody>
</table>
<p class="meta">Report by Chowkidar — local-first LLM deprecation watchdog</p>

<div id="toast" class="toast"></div>

<script>
async function openInEditor(filePath) {{
  const toast = document.getElementById("toast");
  let url = `/open-editor?path=${{encodeURIComponent(filePath)}}`;
  if (window.location.protocol === "file:") {{
    url = `http://127.0.0.1:51731/open-editor?path=${{encodeURIComponent(filePath)}}`;
  }}
  try {{
    const response = await fetch(url);
    const data = await response.json();
    if (data.success) {{
      showToast("Successfully opened " + filePath.split(/[\\\\/]/).pop() + " in default editor!", true);
    }} else {{
      showToast("Failed to open: " + data.message, false);
    }}
  }} catch (err) {{
    if (window.location.protocol === "file:") {{
      let success = false;
      for (let port = 51732; port <= 51740; port++) {{
        try {{
          const fallbackUrl = `http://127.0.0.1:${{port}}/open-editor?path=${{encodeURIComponent(filePath)}}`;
          const response = await fetch(fallbackUrl);
          const data = await response.json();
          if (data.success) {{
            showToast("Successfully opened " + filePath.split(/[\\\\/]/).pop() + " in default editor!", true);
            success = true;
            break;
          }}
        }} catch (e) {{
          // try next port
        }}
      }}
      if (!success) {{
        showToast("Error: Local report server is not running. Run 'chowkidar report --format html' to start it.", false);
      }}
    }} else {{
      showToast("Error: " + err.message, false);
    }}
  }}
}}

function showToast(message, isSuccess) {{
  const toast = document.getElementById("toast");
  toast.innerText = message;
  toast.className = "toast " + (isSuccess ? "toast-success" : "toast-error");
  toast.style.opacity = "1";
  setTimeout(() => {{
    toast.style.opacity = "0";
  }}, 3000);
}}

document.addEventListener("DOMContentLoaded", () => {{
  document.querySelectorAll(".btn-open-editor").forEach(btn => {{
    btn.addEventListener("click", () => {{
      const filePath = btn.getAttribute("data-file-path");
      if (filePath) {{
        openInEditor(filePath);
      }}
    }});
  }});

  document.querySelectorAll(".btn-toggle-details").forEach(btn => {{
    btn.addEventListener("click", () => {{
      const targetId = btn.getAttribute("data-target");
      const targetRow = document.getElementById(targetId);
      if (targetRow) {{
        if (targetRow.style.display === "none") {{
          targetRow.style.display = "table-row";
          btn.innerText = "📋 Hide Alternatives";
        }} else {{
          targetRow.style.display = "none";
          btn.innerText = "📋 View Alternatives";
        }}
      }}
    }});
  }});
}});
</script>
</body>
</html>"""
