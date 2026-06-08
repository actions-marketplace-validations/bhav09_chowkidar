# Chowkidar — Enhanced Task Brief (ETB)

## Status: APPROVED (Phase 16 in progress; Phase 17 under review)

---

## Problem Statement

LLM providers are releasing and sun-setting models at an accelerating pace. Developers hard-code model identifiers in `.env` files and configs, then get blindsided when a model is deprecated — causing production failures, degraded outputs, or silent billing changes. There is no local, privacy-respecting tool that watches for these deprecations and alerts the developer proactively.

---

## Objective

A project-local, privacy-first Python CLI package and accompanying IDE integrations that:

1. Scans project files for LLM model identifiers.
2. Maintains a local registry of model deprecation/sunset dates (scraped from provider sources).
3. Uses a local SLM (via Ollama) to parse unstructured deprecation announcements into structured data.
4. Alerts the user via native OS notifications at configurable thresholds.
5. Writes IDE rules files (Cursor `.mdc`, Claude Code `CLAUDE.md`, Copilot `.github/copilot-instructions.md`, etc.) to passively instruct AI assistants to update deprecated models — zero config, works everywhere.
6. Exposes an MCP server as the power-user layer for real-time queries and interactive updates.

Everything stays on the local machine. Zero data exfiltration.

---

## Architecture (Four Layers)

### Layer 1 — The Scanner (Passive)
- Scans filesystem for `.env`, `.env.local`, `.env.*`, `docker-compose.yml`, `settings.py`, `constants.ts`, `.yaml`, `.toml`, `.json`, `pyproject.toml`.
- Uses format-aware parsers and regex to find model strings matching known patterns (e.g., `gpt-[0-9a-z.-]+`, `claude-[0-9a-z.-]+`, `gemini-[0-9a-z.-]+`, `mistral-[0-9a-z.-]+`).
- Maps variable names to their model string values.
- Normalizes model strings to canonical IDs (e.g., `gpt-4o-2024-08-06` → `openai/gpt-4o-2024-08-06`).

### Layer 2 — The Registry (Dynamic) + Local SLM
- Local SQLite database at `.chowkidar/registry.db` within the project root.
- Scraper runs periodically or on-demand:
  - OpenAI: `/v1/models` endpoint (structured `deprecation_date` field).
  - Anthropic: Release notes / docs pages (semi-structured scraping).
  - Google: Vertex AI / AI Studio deprecation schedules.
  - Mistral: API docs / changelog.
- Each model record includes: `sunset_date`, `replacement`, `replacement_confidence`, `breaking_changes`, `source_url`.
- Local SLM via Ollama parses unstructured "sunset announcement" blog posts into structured JSON when regex/heuristic parsing fails.

### Layer 3 — The Sentinel (Active)
- Background daemon process.
- Cross-references scanner results against registry periodically.
- Fires OS-native notifications at thresholds:
  - >90 days: No action.
  - 30 days: Low-priority desktop notification.
  - 7 days: Urgent desktop notification + terminal warning.
  - Sunset reached: Blocking warning via IDE rules + MCP.
- Notification deduplication: tracks `(model, project, threshold)` to avoid spam.
- Snooze support: `chowkidar snooze <model> --days N`.

### Layer 4 — IDE Integration (Rules + MCP)
- Primary mechanism: Write/update IDE rules files so AI assistants are passively aware of deprecations.
- Secondary mechanism: MCP server for real-time queries and interactive tool calls.

---

## Local SLM Integration (Ollama)

### Purpose
Parse unstructured provider blog posts, changelogs, and announcement pages into structured deprecation data when regex/heuristic parsing is insufficient.

### Installation Flow (`chowkidar setup`)
1. Check for Ollama: `which ollama` / check if `ollama` binary exists.
2. If missing: Prompt user and guide installation.
3. Start Ollama service: `ollama serve` (if not already running).
4. Pull model: `ollama pull gemma3:1b` (~815MB, one-time download).
5. Verify: Run a test prompt to confirm the model responds.

### Model Choice
- Default: `gemma3:1b` — small footprint (~815MB), good at structured extraction.
- Alternative: `qwen2.5:0.5b` (~400MB) for very constrained systems.
- Configurable: `chowkidar config slm_model <model_name>`.

---

## Database Schema

```sql
CREATE TABLE models (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    aliases TEXT,                  -- JSON array
    sunset_date TEXT,              -- ISO 8601 or NULL
    replacement TEXT,              -- successor model id
    replacement_confidence TEXT,   -- "high" | "medium" | "low"
    breaking_changes BOOLEAN DEFAULT 0,
    source_url TEXT,
    last_checked_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT NOT NULL,
    file_path TEXT NOT NULL,
    variable_name TEXT,
    model_value TEXT NOT NULL,
    model_id TEXT,
    last_scanned_at TEXT
);

CREATE TABLE notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT NOT NULL,
    model_id TEXT NOT NULL,
    threshold TEXT NOT NULL,
    notified_at TEXT DEFAULT (datetime('now')),
    snoozed_until TEXT
);

CREATE TABLE pinned_models (
    model_id TEXT PRIMARY KEY,
    reason TEXT,
    pinned_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE watched_projects (
    project_path TEXT PRIMARY KEY,
    added_at TEXT DEFAULT (datetime('now')),
    last_scanned_at TEXT
);
```

---

## Technical Stack

| Component | Library | Purpose |
|---|---|---|
| CLI | `typer` + `rich` | Commands, formatted output |
| Env parsing | `python-dotenv` | `.env` file read/write |
| Config parsing | `tomli`, `pyyaml` | `.toml`, `.yaml` support |
| HTTP client | `httpx` | Async provider scraping |
| Retry logic | `tenacity` | Exponential backoff for scraping |
| Database | `sqlite3` (stdlib) | Local registry |
| Local SLM | `ollama` (Python SDK) | Interface with Ollama for SLM inference |
| Notifications | `plyer` | Cross-platform desktop alerts |
| MCP server | `mcp` SDK | IDE integration (stdio) |
| Background | `schedule` | Periodic scan/sync loop |
| File safety | `filelock` | Concurrent `.env` access protection |
| Logging | `structlog` or stdlib | Structured logs with rotation |
| Testing | `pytest` + `respx` | Unit tests, HTTP mocking |

---

## Security & Privacy Constraints (Non-Negotiable)

1. Zero exfiltration: No `.env` content, API keys, or project paths sent externally.
2. Local registry: Deprecation data is downloaded to the user, never uploaded from.
3. Read-only defaults: File modification requires explicit `auto_update = true` in `.chowkidar/config.toml`.
4. Atomic writes: All file modifications use write-to-temp + `os.replace` pattern.
5. Automatic backups: `.env.bak` created before any modification.
6. File locking: `filelock` prevents concurrent write corruption.

---

## Completed Phases

- **Phase 1-9**: Shipped core scanner, registry, check, IDE rules writer, and daemon foundations.
- **Phase 10**: Automatic Workspace Watching & Service Robustness (completed).
- **Phase 11**: Native Report Notifications & Enhanced Click-throughs (completed).
- **Phase 13**: Autonomous CLI Monitoring & Repository Auto-Discovery (completed).
- **Phase 14**: Project-Local Setup & Configuration Isolation (completed).

---

## Phase 15: Reality Gap and Trust Roadmap (ACTIVE)

### Objectives

To establish production-grade trust, safety, and correctness across all user-facing features, resolving the gap between documented claims and actual system capability:

1. **Fix Claim vs. Reality Mismatches**: Align README and commands reference with the actual project-local layout, explicit stubs status of cloud adapters, exact `setup` and `update` commands behavior.
2. **Provider Freshness & Registry Trust**: Surface and track last-sync timestamps and provider status/failures so users know if deprecation lists are reliable and current.
3. **Exact Reference Inventory & Classification**: Enhance scans to separate mutable local variables from immutable source code, test fixtures, and docs.
4. **Family-Aware Recommendation Engine**: Guard against unsafe "string swap" migrations by validating model capabilities and family compatibility before making suggestions or executing updates.
5. **Safe Action Workflows**: Formalize notifications, CI gates, dry-runs, backups, and rollback capabilities for config updates.
6. **Developer Experience (DX) & CI/CD**: Setup in-repo testing, package version sync, and extension smoke checks.

---

## Phase 16: Hardening Benchmark Reliability And Security (PENDING APPROVAL)

### Objectives
Establish high-grade reliability, security, and configurability for model benchmarks, HTML/Markdown reporting, and localhost integrations.

1. **Decouple Configuration**: Support fully configurable sync urls, timeout limits, retries, and enabling/disabling seeding without code-level edits.
2. **Externalize & Version Baseline Data**: Package initial baseline model benchmarks in JSON format with provenance fields.
3. **Resilience & Fault Tolerance**: Build robustness into public benchmark syncing by applying Exponential Backoff retry policies and validating schemas.
4. **HTML, Markdown, & report_server Security (Critical)**: Guard against XSS in generated reports through HTML escaping and prevent localhost-to-browser exploits in the report server via path constraints and Same-Origin/session token controls.
5. **Robust Model Matching & Presentation**: Scored canonical matching for benchmark mapping and centralized formatting for dry-runs, checks, and reports.

### Security Invariants & Guardrails
- **Input Validation**:constructed URLs must lie within the configured base URL; remote JSON keys/values are strictly validated (no shell injection, command injection, or script injection).
- **CORS & Access Controls**: Same-origin only, no wildcard origins, and authentication/path allowlist controls for sensitive endpoints.
- **Data Protection**: Local registry data and failure logs are sanitized before storage and escaped before display.

---

## Phase 17: Cross-Family Recommendations and Detailed Capability Reporting (PENDING APPROVAL)

### Objectives
Provide comprehensive and provider-agnostic replacement alternatives and precise capability differences in all generated report formats (Markdown, JSON, and HTML).

1. **Intelligent Cross-Family Selection**: Categorize models into cost-efficient (low-tier) and reasoning-heavy (high-tier) buckets. For any legacy/deprecated model, dynamically select the best alternative models from *different* provider families.
2. **Granular Capability Diffs**: Leverage existing model capability specifications to calculate exact deltas (context window, max output tokens, vision, tool use, json mode, streaming) between the current model and all suggested cross-family alternatives.
3. **Rich HTML Interactivity**: Redesign the HTML deprecation report to include expandable detailed panels for each flagged model. Under these panels, display a beautifully structured comparison grid showing capability diffs and alternative model cards.
4. **Comprehensive Markdown Reporting**: Enhance the Markdown report format with a detailed model-by-model appendix section outlining cross-family alternatives and their capability diffs.
5. **Robust Backwards-Compatible API**: Extend the `Recommendation` dataclass with a new `cross_family_recommendations` field, ensuring perfect serialization for JSON reporting and IDE/MCP tool integrations.

### Security Invariants & Guardrails
- **Strictly Offline**: All capability and alternative recommendation logic must remain 100% local/offline with zero external requests.
- **Input Escaping**: Ensure all dynamic model names, variable names, and capability values are strictly escaped in the HTML/Markdown outputs to prevent any injection vectors.


## Phase 18: Purpose-Aware Model Recommendations and Cost-Difference Percentage Analysis (COMPLETED)

### Objectives
Provide deeply personalized model migration suggestions tailored specifically to the model's exact usage purpose and context in the project codebase, complete with custom benchmarks and precise token pricing percentage calculations.

1. **Context-Driven Use Case Classification**: Automatically analyze file paths, names, and code variables to classify model references into distinct use cases (`coding`, `agents/reasoning`, `embeddings/search`, `extraction/structured`, `tests/eval`, `chat/general`).
2. **Specialized Use Case Recommendations**: Map alternative models directly based on the classified project use case rather than generic tiers. E.g., suggest Qwen 2.5 Coder and Claude 3.5 Sonnet for `coding` tasks, or DeepSeek R1 and OpenAI O1 for `agents/reasoning`.
3. **Use Case Relevant Benchmarks**: Highlight and contextualize the specific industry benchmarks relevant to the classified use case (e.g., HumanEval/SWE-bench for coding, GPQA/MATH for reasoning, MTEB for vector embeddings).
4. **Token Cost-Difference Percentage Comparison**: Expand baseline pricing definitions to include leading open-source and commercial models. Dynamically calculate input/output token price variations in percentage terms, displaying clear, colored badges (e.g., "saves ~69%" or "costs ~120% more").
5. **Interactive UI Visualization**: Update the generated HTML/Markdown reports with custom Use Case badges, targeted benchmark descriptions, and cost-impact badges for both primary and alternative model cards.

### Security Invariants & Guardrails
- **Zero Online Leakage**: All pricing calculations and classification heuristics must execute 100% locally and offline.
- **Fail-safe Fallbacks**: Fall back gracefully to `chat/general` if variables or files cannot be parsed, ensuring robust report generation.


---

## Phase 19: Provider Sync Status — Model Detection Summary & UX Overhaul (PENDING APPROVAL)

### Problem (Root Cause)

The Provider Sync Status section exists in all report formats (HTML, Markdown, JSON) and the CLI `status` command. Currently it shows **only operational sync health** (last success/failure timestamps). A user looking at this section cannot answer the questions they actually care about:

- "Which providers am I exposed to?"
- "How many models do I use from each provider — and which are deprecated?"
- "Where in my codebase are these models referenced?"
- "Which specific versions/snapshots am I on?"

This creates a **disconnect** between sync health and actionable intelligence. The user must mentally cross-reference the Provider Sync Status with the deprecated models table below it.

### UX Issues Identified (User Perspective)

| # | Issue | Impact |
|---|-------|--------|
| 1 | No per-provider model count | Can't assess provider concentration/risk |
| 2 | No model family grouping | Can't see GPT-4 family vs GPT-3.5 family breakdown |
| 3 | No version summary | Can't tell if using old snapshots or latest |
| 4 | No file/location summary | Can't identify where models are scattered |
| 5 | No deprecation exposure indicator | Can't see "3/5 OpenAI models are deprecated" at a glance |
| 6 | Raw ISO timestamps | "2025-05-27T14:30:00" is not human-scannable — needs "2h ago" |
| 7 | No source type breakdown | env vs config vs source code have different migration difficulty |
| 8 | No health/severity badge per provider | All providers look the same regardless of risk |
| 9 | Sync status disconnected from scan results | Two separate mental models for the user |
| 10 | No "freshness" indicator | Can't tell if sync data is stale without mental math |

### Objectives

Transform Provider Sync Status from a "sync health check" into a **Provider Intelligence Summary** that gives the user instant situational awareness.

1. **Per-Provider Model Inventory**: Show total models detected per provider, with counts split by status (active / deprecating / warning / critical / sunset).
2. **Model Family Grouping**: Group detected models by family within each provider (e.g., OpenAI → GPT-4 family: 2 models, GPT-3.5 family: 1 model).
3. **Version Summary**: Show the specific model versions detected per family (e.g., `gpt-4o-2024-08-06`, `gpt-3.5-turbo`).
4. **Detection Location**: Show which files each model was found in, with source type (env/config/source).
5. **Human-Readable Timestamps**: Convert ISO timestamps to relative time ("2h ago", "3 days ago") with full timestamp on hover/title.
6. **Health Badge Per Provider**: Color-coded badge: green (all active), yellow (some deprecating), orange (warnings), red (critical/sunset models present).
7. **Freshness Indicator**: Show sync staleness with clear visual cue (fresh < 24h, aging 24-72h, stale > 72h).

### Design (All Surfaces)

#### JSON Output
```json
{
  "sync_statuses": {
    "openai": {
      "last_success_at": "2025-05-27T14:30:00",
      "last_failure_at": null,
      "failure_reason": null,
      "last_checked_at": "2025-05-27T14:30:00",
      "freshness": "fresh",
      "models_detected": 3,
      "models_deprecated": 1,
      "health": "warning",
      "families": {
        "GPT-4": {
          "models": ["gpt-4o", "gpt-4o-mini"],
          "deprecated_count": 0
        },
        "GPT-3.5": {
          "models": ["gpt-3.5-turbo"],
          "deprecated_count": 1
        }
      },
      "detections": [
        {"model": "gpt-4o", "file": ".env", "variable": "OPENAI_MODEL", "source_type": "env", "status": "active"},
        {"model": "gpt-3.5-turbo", "file": ".env", "variable": "FALLBACK_MODEL", "source_type": "env", "status": "sunset"}
      ]
    }
  }
}
```

#### Markdown Output
```
### Provider Sync Status

#### OpenAI — 🟡 Warning
- **Sync**: ✅ 2h ago | **Models**: 3 detected, 1 deprecated
- **Families**: GPT-4 (2 models, all active) · GPT-3.5 (1 model, 1 sunset)
- **Detections**:
  | Model | File | Variable | Source | Status |
  |-------|------|----------|--------|--------|
  | gpt-4o | .env | OPENAI_MODEL | env | active |
  | gpt-3.5-turbo | .env | FALLBACK_MODEL | env | sunset |

#### Anthropic — 🟢 Healthy
- **Sync**: ✅ 2h ago | **Models**: 1 detected, 0 deprecated
- **Families**: Claude 3.5 (1 model, all active)
```

#### HTML Output
- Card-based layout per provider (replacing the flat table)
- Color-coded header bar (green/yellow/orange/red) based on provider health
- Collapsible detection details (model table shown on expand)
- Relative timestamps with tooltip showing full ISO date
- Badge showing "3 models · 1 deprecated" at a glance
- Family pills/tags showing grouping

#### CLI (`chowkidar status`)
- Rich table enhanced with additional columns: Models Detected, Deprecated, Health
- Colored health badge per provider row

### Model Family Classification Logic

Parse canonical model IDs into families using prefix/pattern rules:
- `openai/gpt-4*` → "GPT-4" family
- `openai/gpt-3.5*` → "GPT-3.5" family
- `openai/o1*`, `openai/o3*`, `openai/o4*` → "O-series" family
- `anthropic/claude-3.5*` → "Claude 3.5" family
- `anthropic/claude-3-*` → "Claude 3" family
- `anthropic/claude-*-4*` → "Claude 4" family
- `google/gemini-2.5*` → "Gemini 2.5" family
- `google/gemini-2.0*` → "Gemini 2.0" family
- `google/gemini-1.5*` → "Gemini 1.5" family
- `mistral/mistral-large*` → "Mistral Large" family
- `mistral/mistral-small*` → "Mistral Small" family
- `mistral/codestral*` → "Codestral" family

### Implementation Plan

| Step | What | Files Changed |
|------|------|---------------|
| 1 | Add `classify_model_family()` function | `scanner/patterns.py` |
| 2 | Add helper `relative_time()` for human timestamps | `report.py` (or new `utils.py`) |
| 3 | Extend `generate_report()` to compute per-provider summaries from scan data | `report.py` |
| 4 | Redesign `_render_markdown()` Provider Sync Status section | `report.py` |
| 5 | Redesign `_render_html()` Provider Sync Status section (card layout) | `report.py` |
| 6 | Extend `_render_json()` with enriched sync_statuses structure | `report.py` |
| 7 | Update CLI `status` command table | `cli.py` |
| 8 | Update tests to cover new structure | `tests/test_report.py` |

### Tradeoffs

| Decision | Chosen | Alternative | Reason |
|----------|--------|-------------|--------|
| Where to compute summaries | In `generate_report()` at render time | Store in DB | Keeps DB schema simple; summaries are derived from scan + registry data |
| Family classification | Pattern-based in `patterns.py` | LLM-based | Deterministic, fast, no external dependency |
| Timestamp format | Relative with full ISO on hover | Always ISO | Much better UX; hover preserves precision |
| HTML layout | Cards per provider | Enhanced flat table | Cards scale better, more scannable |
| Backward compat (JSON) | Additive fields only | Breaking rename | Existing consumers continue to work |

### Security Invariants
- All enrichment is computed from local scan + registry data only (zero network)
- HTML output continues to escape all dynamic values
- No new user input surfaces (purely derived data)

### Testing Plan
- Unit test: `classify_model_family()` with known model IDs
- Unit test: `relative_time()` with various deltas
- Integration test: full report generation with enriched sync status
- Regression: existing `test_report_with_sync_status` still passes
- New test: verify JSON schema has new fields
- New test: verify HTML contains provider cards with health badges
- New test: verify Markdown contains family grouping

---

## Phase 20: QA Hardening — Notification Dedup + MCP Readiness (APPROVED)

### Objectives

1. Fix duplicate notification/report generation for identical deprecation events within cooldown (TS02_TC_19).
2. Make MCP server reliably startable and auto-configured per detected IDE (TS03_TC_29).

### Invariants

- Identical `(project, model, threshold, file, variable)` within cooldown → at most one delivered notification.
- `chowkidar mcp --verify` exits 0 when `.chowkidar/` exists, mcp SDK installed, and IDE config written.
- MCP stdio transport never writes non-JSON to stdout.

### Acceptance criteria

- TS02_TC_19 automated test passes.
- TS03_TC_29 automated test passes.
- Full pytest suite green.


---

## Phase 21: Chowkidar Cloud Ecosystem Integration (PENDING APPROVAL)

### Objectives

1. **Concrete Cloud Environment Adapters**: Fully implement the AWS, GCP, Azure, Vercel, and Kubernetes adapters to read, dry-run, write, and verify secrets.
2. **Official GitHub Action**: Create a native GitHub Action (`action.yml` and `github_action.py`) for scheduled and PR-driven repository scans.
3. **The Gitignored Env Solution**: Implement in-memory secrets and variables scanning, automated decryption of encrypted env files (`.env.vault`/`.env.enc`), and OIDC-federated cloud secret auditing to handle gitignored configuration files in CI/CD.
4. **Dockerization**: Package Chowkidar in a production-ready Docker container and provide Kubernetes CronJob manifests.
5. **Interactive Slack Block Kit**: Upgrade Slack notifications to rich, interactive Block Kit payloads with snooze, pin, and auto-fix actions.

### Invariants & Guardrails

- **Cryptographic Safety**: Gitignored secrets and variables parsed in CI/CD must be processed entirely in-memory and never written to disk or logged.
- **Secret Redaction Engine**: Automatic masking of sensitive API keys and credentials in all logs, tracebacks, and reports.
- **OIDC-Federated Auditing**: Support credential-less cloud authentication using OpenID Connect for AWS, GCP, and Azure in GitHub Actions.
- **Least-Privilege Kubernetes RBAC**: Bounded Kubernetes Role/RoleBinding restricting secret access to specific namespaces.
- **Slack Socket Mode Support**: Secure, WebSocket-based interactive callbacks for private/firewalled cloud daemons without exposing public HTTP endpoints.
- **Multi-Environment Isolation**: Partition and track deprecation risks separately per environment (e.g., development, staging, production).
- **Explicit Cloud Consent**: Cloud adapters must only write/update secrets if `auto_update = true` is explicitly configured.
- **Regression Safety**: The existing 185 tests must remain fully functional and green.

### Acceptance Criteria

- All cloud adapters (AWS, GCP, Azure, Vercel, Kubernetes) are fully implemented and covered by unit tests.
- GitHub Action integration parses in-memory secrets/variables and is covered by unit tests.
- Slack Block Kit payloads and Socket Mode callbacks are validated and covered by unit tests.
- Secret Redaction Engine is fully tested and verified.
- Full pytest suite is green.



