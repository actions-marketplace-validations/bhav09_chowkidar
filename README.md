# Chowkidar

[![PyPI Version](https://img.shields.io/badge/pypi-v0.9.2-blue)](https://pypi.org/project/chowkidar/0.9.2/)
[![GitHub Release](https://img.shields.io/badge/release-v0.9.2-blue)](https://github.com/bhav09/chowkidar/releases/latest)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/chowkidar?period=total&units=INTERNATIONAL_SYSTEM&left_color=GREY&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/chowkidar)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Chowkidar** is a secure, local-first LLM model deprecation watchdog. It scans your codebase and configuration files for LLM model references, cross-references them with a locally cached deprecation database, and alerts you before models sunset.

Everything runs on your machine. Zero data exfiltration.

---

## Core Features

Chowkidar is packed with features designed to establish production-grade trust, safety, and correctness across all user-facing workflows:

### 1. Multi-Format Code & Config Scanner
- Scans and parses model strings in `.env`, JSON, YAML, TOML, `docker-compose.yml`, and source code files.
- Features intelligent pattern matching that normalizes model strings to canonical IDs (e.g., `gpt-4o-2024-08-06` → `openai/gpt-4o-2024-08-06`).

### 2. Local SQLite Deprecation Registry
- Maintains a local SQLite database at `.chowkidar/registry.db` within the project root.
- Periodically syncs deprecation and sunset schedules from major providers (OpenAI, Anthropic, Google Gemini, Mistral) securely and locally.

### 3. Local SLM Integration (Ollama)
- Integrates with local Ollama instances (`gemma3:1b`, `qwen2.5:0.5b`, or custom installed models).
- Uses local SLM to parse complex unstructured deprecation blog posts and enrich migration recommendations (purpose, reason, risk, confidence) when models are expiring.

### 4. Context-Driven Use Case Classification
- Automatically analyzes file paths, variable names, and code context to classify model references into distinct use cases (`coding`, `agents/reasoning`, `embeddings/search`, `extraction/structured`, `tests/eval`, `chat/general`).

### 5. Specialized Use Case Recommendations
- Suggests highly targeted alternative models tailored to your specific use case (e.g., Qwen 2.5 Coder and Claude 3.5 Sonnet for `coding` tasks; DeepSeek R1 and OpenAI O1 for `agents/reasoning`).

### 6. Unified Risk & Capability Analysis
- Compares critical model capabilities (context window, max output tokens, vision, tool use, JSON mode, streaming) between old and recommended models to prevent regression.
- Provides detailed capability deltas (improved, same, degraded, gained, lost).

### 7. Token Cost-Difference Percentage Comparison
- Features a built-in FinOps pricing engine with baseline pricing definitions for leading open-source and commercial models.
- Dynamically calculates input/output token price variations in percentage terms, displaying clear, colored badges (e.g., "saves ~69%" or "costs ~120% more").

### 8. Provider Sync Status & Intelligence Summary
- Displays a comprehensive summary of provider sync health, sync freshness, and per-provider model inventory.
- Groups detected models by family, showing specific versions, detection locations, and relative timestamps ("2h ago", "3 days ago").
- Color-coded health badges per provider based on deprecation risk (green, yellow, orange, red).

### 9. Interactive Reports (HTML, Markdown, JSON)
- Generates beautiful, self-contained interactive HTML reports with expandable detailed panels, capability comparison grids, and cost-impact badges.
- Generates clean Markdown reports with a detailed model-by-model appendix, and structured JSON output for tool integration.

### 10. Background Daemon & OS-Native Services
- Periodically monitors your repositories (every 4 hours) for deprecations.
- Installs as an OS-native service (launchd on macOS, systemd on Linux, Task Scheduler on Windows) for silent background monitoring.

### 11. Notification-First Governance
- Fires native OS desktop alerts and webhooks (Slack, Discord, generic) at configurable thresholds (30 days, 15 days, 7 days, and 1 day before sunset).

### 12. Notification Deduplication & Cooldown
- Tracks `(model, project, threshold, file, variable)` to avoid duplicate alerts and spam within a configurable cooldown window (default 24 hours).

### 13. Alert Silencing & Overrides (Pinning & Snoozing)
- Permanently suppress notifications for a specific model ID with `pin`.
- Temporarily mute alerts for a model ID for a specified number of days with `snooze`.

### 14. Safe Config Updates (Atomic Writes & Backups)
- Safely auto-updates structured configuration files with atomic writes (write-to-temp + `os.replace` pattern), automatic backups (`.env.bak`), and system-level `filelock` to prevent concurrent write corruption.

### 15. Deployment Signal Detector
- Analyzes repository evidence (CI, Docker, Kubernetes, Vercel, Terraform) to flag likely deployed environments, preventing blind or risky local writes.

### 16. Cloud Environment Adapters
- Explicit, contract-ready adapter interface designed for dry-running, updating, and verifying remote secret/config stores on Vercel, Kubernetes, AWS Secrets/SSM, GCP Secret Manager, and Azure Key Vault.

### 17. AI-Assistant Rules Integration
- Generates zero-config rule instructions (`.mdc`, `CLAUDE.md`, etc.) to guide Cursor, Claude Code, Copilot, and Windsurf, enabling AI editors to auto-discover deprecation instructions.

### 18. Model Context Protocol (MCP) Server
- Launches a stdio-based MCP server that auto-configures itself for Cursor (`.cursor/mcp.json`), VS Code (`.vscode/mcp.json`), Claude Code (`.claude/settings.json`), and Windsurf (`.windsurf/mcp.json`).

### 19. Interactive Terminal TUI Dashboard
- Launches an interactive terminal-based TUI to visualize model deprecation risk across all watched repositories.

### 20. CI/CD Gate Integration
- Integrates with CI/CD systems or git pre-commit hooks to block builds if critical or sunset-passed models are found.

### 21. Shell Warnings Hook
- Installs a lightweight shell hook that displays quick model deprecation warning alerts on directory changes (`cd`).

### 22. Migration Testing & Output Comparison
- Executes dry-run completions on both old and new model candidates to compare prompt response outputs and prevent regressions.

### 23. Lifespan Prediction
- Uses historical release and sunset data to estimate the deprecation probability and lifespan of models in use.

---

## Installation & Project Setup

```bash
# 1. Install chowkidar in your project directory
pip install chowkidar

# 2. Run the idempotent project-scoped setup
chowkidar setup
```

### Project-Scoped Monitoring

The `chowkidar setup` command provides a zero-friction setup that configures everything for your project:
1. **Config & Database**: Creates your config and database files under `.chowkidar/` inside your project root.
2. **Initial Scan & Sync**: Syncs provider deprecation tables and performs an immediate first-time scan on the repository to initialize alerts. (Note: IDE rule files are generated and updated automatically by the background daemon during monitoring cycles, or manually via `chowkidar rules write`).

You can customize behavior inside `.chowkidar/config.toml` or via the CLI:
```bash
# Change directory scan depth
chowkidar config discover_max_depth 5
```

---

## Top 10 CLI Commands

Below are the 10 most relevant commands for daily use.

### 1. `chowkidar setup`
Project-scoped configuration, database initialization, provider sync, and initial repository scan.

### 2. `chowkidar sync`
Fetches and updates the local deprecation registry from providers.

### 3. `chowkidar scan`
Locates all LLM model references within your code and configuration files.

### 4. `chowkidar check`
Cross-references detected model strings against the deprecation registry.

### 5. `chowkidar status`
Displays watched projects, sync freshness, and background daemon health.

### 6. `chowkidar watch`
Registers a project path with the background daemon for periodic scans.

### 7. `chowkidar daemon`
Starts the background monitoring loop (sends alerts at 30, 15, 7, and 1 day before expiry).

### 8. `chowkidar update`
Previews (via `--dry-run`) or applies safe updates of deprecated model strings in structured configuration files (such as `.env`, JSON, YAML, TOML, and `docker-compose.yml`).

### 9. `chowkidar mcp`
Launches the stdio MCP server for active IDE-level AI assistant queries.

### 10. `chowkidar report`
Generates comprehensive Markdown, JSON, or interactive HTML reports.

See [COMMANDS.md](COMMANDS.md) for the complete reference containing all available CLI commands.

---

## Editor Integration

### Passive AI Rules (Zero-Config)
AI editors auto-discover instructions in your project workspace. Chowkidar outputs non-destructive rule tables:
- **Cursor**: `.cursor/rules/chowkidar-alerts.mdc`
- **Claude Code**: `.claude/rules/chowkidar-alerts.md`
- **VS Code / Copilot**: `.github/copilot-instructions.md`
- **Windsurf**: `.windsurfrules`

### MCP Server (Active)
Configure the stdio MCP server in your IDE's configuration file:
```json
{
  "mcpServers": {
    "chowkidar": {
      "command": "chowkidar",
      "args": ["mcp"]
    }
  }
}
```

---

## Security & Local Safety

- **Privacy First**: No code, project paths, keys, or configurations are ever sent to external APIs.
- **Safe Writes**: Modifying configuration files requires setting `auto_update = true` in your config. Every update atomic-writes via a temp file and saves a `.chowkidar.bak` file for automatic rollback.
- **Concurrent-Safe**: Uses system-level `filelock` to protect files from concurrent daemon/CLI writes.

---

## License

MIT
