# Code Standards

## Language and Format

This project is entirely **markdown-based** with shell script helpers. There is no compiled code. Standards cover markdown authoring, skill definition patterns, and shell scripting conventions.

## File Naming

- **kebab-case** for all file names: `security-checklist.md`, `score-regression.sh`
- Names should be descriptive enough that an LLM understands purpose without reading content
- Command files match their command name: `debug.md` for `/autoresearch:debug`

## SKILL.md Pattern (v2.2.2)

The main skill file is a **thin routing table only** — not a protocol document:
- YAML frontmatter: `name`, `description`, `version`
- Safety invariants section (applies to all subcommands)
- Subcommand table: command, purpose, default iterations
- Universal flags table
- No workflow protocol, no setup gate questions, no phase diagrams

Target: ~41 lines. All protocol lives in the command files.

## Command File Pattern (v2.2.2)

Each command file (`claude-plugin/commands/autoresearch/*.md`) is **self-contained**:
- YAML frontmatter: `name`, `description`, `argument-hint`
- `EXECUTE IMMEDIATELY` header — no deliberation before reading
- Parse Arguments section — extract all flags inline
- Setup section — AskUserQuestion batched call
- Precondition checks
- Full loop or workflow protocol with numbered phases
- TSV logging format
- Chain handoff section

Target: 94–120 lines per command file. Never split protocol across files unless content is truly shared across 3+ commands.

## Reference Files Pattern (v2.2.2)

Reference files (`claude-plugin/skills/autoresearch/references/`) are for **shared content only**:
- Loaded explicitly by the command file that needs them
- Must be referenced by 3+ commands to justify existence as a reference
- Current 4 references: `orchestrator-routing.md`, `predict-personas.md`, `reason-judge-protocol.md`, `security-checklist.md`

Do not create per-command workflow reference files. That was the v2.0.x pattern (13 files). v2.2.x embeds protocol directly.

## TSV Logging Format

All looping commands write results in TSV format:

```
# metric_direction: higher_is_better|lower_is_better
iteration	timestamp	commit	metric	delta	guard	guard-metric	status	description
0	{ts}	{sha}	{n}	0.0	-	-	baseline	initial state
```

Status values (8 total): `baseline`, `keep`, `keep (reworked)`, `discard`, `crash`, `no-op`, `hook-blocked`, `metric-error`

The `# metric_direction` comment on line 1 enables the evals command to auto-detect direction. Never omit it.

## Version Management

- Version tracked in the plugin manifests:
  - `claude-plugin/.claude-plugin/plugin.json` — Claude Code plugin manifest (e.g. `2.2.2`)
  - `.claude-plugin/marketplace.json` — marketplace entry (top-level and plugin `version`)
- Version also appears in `claude-plugin/skills/autoresearch/SKILL.md` frontmatter (`version:`) and the README and guide version badges
- There is no release script; bump every touchpoint together by hand

## Shell Script Standards

- Shebang: `#!/usr/bin/env bash`
- Quote all variables: `"$VAR"` not `$VAR`
- `set -euo pipefail` for strict error handling
- Runtime helpers (`orchestrate.sh`, `score-regression.sh`) live only in `claude-plugin/skills/autoresearch/scripts/` and are edited there directly; there are no other copies
- Skill and command text resolves `scripts/...` relative to the installed skill directory, never the caller's working directory

## Plugin Distribution

Source of truth is `claude-plugin/`; the root `.claude-plugin/marketplace.json` points at it (`"source": "./claude-plugin"`). To change the plugin:
1. Edit files in `claude-plugin/commands/`, `claude-plugin/skills/autoresearch/`, or `claude-plugin/hooks/` directly — there is no sync or transform step
2. Test locally: add the repo root (the folder containing `.claude-plugin/marketplace.json`) as a local-directory marketplace in `/plugins` (Marketplaces tab) and install the plugin. It loads in place from that folder, so run `/reload-plugins` after each edit
3. Run `bash tests/test-hooks.sh`, `bash tests/test-orchestrator.sh`, and `bash tests/test-regression.sh`

Hooks are registered only through `claude-plugin/hooks/hooks.json`, which Claude Code loads when the plugin is enabled.

## Documentation Standards

- Each doc file max **200 lines**
- README max 300 lines
- Include "See also" cross-reference links at the bottom of each file
- Use tables for structured comparisons
- Use Mermaid diagrams for architecture and flow visualization
- Keep content factual and specific to the codebase

## Commit Message Format

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `release:`
- No AI references in commit messages
- Keep commits focused on actual changes
- Experiment commits from loops use `experiment: {description}` prefix

See also: [Project Overview](project-overview-pdr.md) | [System Architecture](system-architecture.md) | [Codebase Summary](codebase-summary.md)
