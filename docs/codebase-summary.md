# Codebase Summary

## Overview

Autoresearch v2.2.2 is a modular autonomous iteration framework for Claude Code, distributed as a Claude Code plugin. `claude-plugin/` is the single source of truth: commands, skill, runtime helpers, and hooks are edited there directly, with no sync or generation step. The root `.claude-plugin/marketplace.json` exposes it to the plugin manager (`"source": "./claude-plugin"`). There is no compiled code and near-zero runtime dependencies.

## File Inventory

| Directory | Purpose | Primary Types |
|-----------|---------|---------------|
| `claude-plugin/` | Claude Code plugin — the single source of truth, edited directly | `.md`, `.json`, `.cjs`, `.sh` |
| `claude-plugin/commands/` | Core loop + 12 subcommand files (self-contained) | `.md` |
| `claude-plugin/skills/autoresearch/` | Thin routing SKILL.md + 4 reference files + runtime helpers (`scripts/`) | `.md`, `.sh` |
| `claude-plugin/hooks/` | Hook guardrails, registered via `hooks.json` when the plugin is enabled | `.cjs`, `.json`, `.sh` |
| `.claude-plugin/` | Marketplace manifest pointing at `./claude-plugin` | `.json` |
| `guide/` | User-facing documentation and tutorials | `.md` |
| `guide/scenario/` | Real-world scenario walkthroughs (10 domains) | `.md` |
| `docs/` | Project documentation | `.md` |
| `tests/` | Shell test suites and fixtures | `.sh` |
| Root | README, LICENSE, COMPARISON, CONTRIBUTING | `.md` |

## Key Files

| File | Purpose |
|------|---------|
| `claude-plugin/skills/autoresearch/SKILL.md` | Thin routing table (41 lines) — loaded by Claude Code per invocation |
| `claude-plugin/commands/autoresearch.md` | Core loop command — self-contained protocol, ~110 lines |
| `claude-plugin/commands/autoresearch/evals.md` | NEW: one-shot TSV analysis — trends, plateaus, regressions |
| `claude-plugin/skills/autoresearch/references/predict-personas.md` | 5 default expert personas used by predict subcommand |
| `claude-plugin/skills/autoresearch/references/reason-judge-protocol.md` | Blind judge scoring protocol for reason subcommand |
| `claude-plugin/skills/autoresearch/references/security-checklist.md` | STRIDE + OWASP checklist used by security subcommand |
| `claude-plugin/skills/autoresearch/scripts/orchestrate.sh` | Orchestrator routing seam (classify, next-hop, plateau, screening) |
| `claude-plugin/skills/autoresearch/scripts/score-regression.sh` | Regression scoring backend for the regression subcommand |
| `claude-plugin/hooks/hooks.json` | Hook registrations, loaded automatically when the plugin is enabled |
| `claude-plugin/.claude-plugin/plugin.json` | Claude Code plugin metadata — version 2.2.2 |
| `.claude-plugin/marketplace.json` | Plugin marketplace entry used by `/plugin marketplace add` |
| `README.md` | Project README with installation, usage, FAQ |
| `COMPARISON.md` | Karpathy's autoresearch vs Claude Autoresearch |
| `CONTRIBUTING.md` | Contribution guidelines |

## Subcommand Registry

| Command | Loop Shape | Default Iterations |
|---------|-----------|-------------------|
| `/autoresearch` | commit → verify → keep/discard | 25 |
| `/autoresearch:plan` | one-shot wizard | N/A |
| `/autoresearch:debug` | hypothesis iteration | 15 |
| `/autoresearch:fix` | commit → verify → revert (error count) | 20 |
| `/autoresearch:security` | attack vector iteration | 15 |
| `/autoresearch:scenario` | 12-dimension exploration | 20 |
| `/autoresearch:predict` | one-shot 5-persona debate | N/A |
| `/autoresearch:learn` | doc → validate → fix loop | 10 |
| `/autoresearch:reason` | adversarial refinement | 8 |
| `/autoresearch:probe` | round-based interrogation | 15 |
| `/autoresearch:improve` | saturation research + PRD generation | 15 |
| `/autoresearch:evals` | one-shot TSV analysis | N/A |
| `/autoresearch:regression` | baseline vs candidate stability gate | N/A |

## Key Dependencies

| Dependency | Type | Purpose |
|------------|------|---------|
| Claude Code CLI | Runtime (host) | Plugin system, skill loading, command registration |
| Git | Runtime (system) | State management, rollback, memory, staleness detection |
| Bash/Zsh | Runtime (system) | Shell scripts, verify/guard commands |
| Node.js 18+ | Runtime (system) | Hook guardrails — `node` must be on the PATH of the shell Claude Code uses; nothing checks this at install time |

No `package.json`, `requirements.txt`, `Cargo.toml`, or Python wrapper CLI. The v2.0.x Python wrapper (`autoresearch_cli.py`) was removed in v2.1.0.

## Output Directories

All subcommands write to `autoresearch/{subcommand}-{YYMMDD}-{HHMM}/`:

| Output File | Written By |
|-------------|-----------|
| `*-results.tsv` | All looping subcommands |
| `handoff.json` | All subcommands (chain integration) |
| `evals-summary.md` | evals command, or any command with `--evals` flag |
| `evals-summary.json` | evals command with `--format json` |
| `security-report.md` | security subcommand |
| `scenario-results.md` | scenario subcommand |
| `predict-report.md` | predict subcommand |
| `learn/` subdirectory | learn subcommand: `learn-results.tsv`, `summary.md`, `validation-report.md` |
| `probe-spec.md`, `constraints.tsv` | probe subcommand |
| `research-findings.md`, `improvement-plan.md`, `prd-*.md` | improve subcommand |

TSV files include a `# metric_direction: higher_is_better|lower_is_better` comment on line 1. Status values: `baseline`, `keep`, `keep (reworked)`, `discard`, `crash`, `no-op`, `hook-blocked`, `metric-error`.

There is no CI. The main verification routes are three local test suites:

- `bash tests/test-hooks.sh` — hook contracts, fail-open behavior, and redacted diagnostics
- `bash tests/test-orchestrator.sh` — orchestrator routing seam (`claude-plugin/skills/autoresearch/scripts/orchestrate.sh`)
- `bash tests/test-regression.sh` — regression scoring (`claude-plugin/skills/autoresearch/scripts/score-regression.sh`) and spec contract

See also: [Project Overview](project-overview-pdr.md) | [System Architecture](system-architecture.md) | [Code Standards](code-standards.md)
