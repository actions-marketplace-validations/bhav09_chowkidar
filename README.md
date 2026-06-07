# Chowkidar

[![PyPI Version](https://img.shields.io/badge/pypi-v0.9.3-blue)](https://pypi.org/project/chowkidar/0.9.3/)
[![GitHub Release](https://img.shields.io/badge/release-v0.9.3-blue)](https://github.com/bhav09/chowkidar/releases/latest)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/chowkidar?period=total&units=INTERNATIONAL_SYSTEM&left_color=GREY&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/chowkidar)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Chowkidar** is a secure, local-first LLM model deprecation watchdog. It scans your codebase and configuration files for LLM model references, cross-references them with a locally cached deprecation database, and alerts you before models sunset.

Everything runs on your machine. Zero data exfiltration.

## Core Features

Chowkidar is a production-grade intelligence platform that empowers developers to make correct, risk-aware decisions and seamlessly automate LLM model migrations:

### 1. Automated Model Discovery & Normalization
- Automatically detects and normalizes active LLM model references across your project.
- Ensures all references are mapped to canonical provider IDs for accurate tracking and analysis.

### 2. Local-First Deprecation Intelligence
- Maintains a secure, local-first database of provider sunset schedules to provide instant offline answers.
- Keeps you informed of upcoming deprecations without ever uploading your project paths or configurations.

### 3. AI-Powered Migration Advisory
- Leverages local Small Language Models (SLMs) to parse unstructured deprecation announcements and enrich recommendations with deep contextual reasoning.
- Provides clear explanations of why a model is deprecating, the risks of staying, and the confidence level of the proposed successor.

### 4. Intelligent Use-Case Classification
- Automatically classifies how each model is used (e.g., coding, reasoning, extraction, chat) to ensure replacement recommendations align with your actual workloads.

### 5. Precision Replacement Matching
- Maps deprecating models to specialized, high-performance successors tailored specifically to your project's needs.

### 6. Capability Regression Guard
- Compares critical model features (context size, output tokens, vision, tools, streaming) to guarantee that migrations never degrade system capabilities.
- Highlights exact capability deltas so you can make informed architectural decisions.

### 7. FinOps Cost-Impact Analytics
- Features a built-in pricing engine with baseline pricing definitions for leading open-source and commercial models.
- Calculates precise input/output token price deltas in percentage terms, giving you immediate financial visibility into migration decisions.

### 8. Provider Risk & Concentration Intelligence
- Groups models by family and version to visualize provider dependencies, exposure levels, and sync freshness.
- Displays color-coded health badges per provider based on deprecation risk.

### 9. Multi-Format Executive & Technical Reporting
- Generates beautiful interactive HTML dashboards, clean Markdown summaries, and structured JSON for comprehensive decision-making.

### 10. Continuous Background Monitoring
- Runs silently as an OS-native service to continuously watch your repositories and keep deprecation risks visible.

### 11. Proactive Multi-Channel Alerting
- Delivers native OS desktop notifications and webhook alerts (Slack/Discord) at critical thresholds (30, 15, 7, and 1 day) before sunset.

### 12. Smart Alert Deduplication
- Suppresses repeat alerts within cooldown windows to prevent notification fatigue while keeping critical issues highlighted.

### 13. Granular Alert Control (Pinning & Snoozing)
- Allows you to temporarily snooze or permanently pin specific models with documented reasons for custom governance.

### 14. Atomic Configuration Migrations
- Safely applies updates with atomic writes, automatic backups, and system-level file locking to prevent configuration corruption.

### 15. Deployment Environment Safeguard
- Detects CI, Docker, Kubernetes, and cloud signals to prevent accidental local updates from breaking deployed environments.

### 16. Enterprise Cloud Secret Adapters
- Provides a contract-ready interface to dry-run, update, and verify remote secrets across Vercel, AWS, GCP, Azure, and Kubernetes.

### 17. Zero-Config AI Editor Rules
- Auto-generates context rules (`.mdc`, `CLAUDE.md`, etc.) so that AI editors (Cursor, Claude Code, Copilot, Windsurf) automatically avoid deprecated models.

### 18. Active IDE-Level MCP Integration
- Exposes a stdio-based MCP server that auto-configures itself to provide real-time deprecation intelligence directly to your AI assistants.

### 19. Terminal-Based TUI Dashboard
- Provides an interactive, keyboard-driven dashboard to visualize deprecation risk across all watched repositories.

### 20. CI/CD Build Gates
- Integrates with CI pipelines or pre-commit hooks to block builds if critical or sunset-passed models are found.

### 21. Shell Directory Change Warnings
- Installs a lightweight shell hook that alerts you directly in your terminal when entering a directory with deprecated models.

### 22. Comparative Output Testing
- Runs dry-run completions on old and new candidates to compare prompt response outputs and prevent regression.

### 23. Predictive Lifespan Analytics
- Estimates model deprecation probability and remaining lifespan using historical release and sunset patterns.

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

## Security & Local Safety

- **Privacy First**: No code, project paths, keys, or configurations are ever sent to external APIs.
- **Safe Writes**: Modifying configuration files requires setting `auto_update = true` in your config. Every update atomic-writes via a temp file and saves a `.chowkidar.bak` file for automatic rollback.
- **Concurrent-Safe**: Uses system-level `filelock` to protect files from concurrent daemon/CLI writes.

## License

MIT
