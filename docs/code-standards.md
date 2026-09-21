# Code Standards

## Language and Format

This project is entirely **markdown-based** with shell script helpers. There is no compiled code. Standards cover markdown authoring, skill definition patterns, and shell scripting conventions.

## File Naming

- **kebab-case** for all file names: `security-checklist.md`, `transform.sh`
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

Each command file (`.claude/commands/autoresearch/*.md`) is **self-contained**:
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

Reference files (`.claude/skills/autoresearch/references/`) are for **shared content only**:
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
  - `claude-plugin/.claude-plugin/plugin.json` — Claude Code canonical manifest (e.g. `2.2.2`)
  - `.claude-plugin/marketplace.json` — marketplace entry (top-level and plugin `version`)
- Version also appears in SKILL.md frontmatter and README badges
- There is no release script; bump every touchpoint together by hand

## Shell Script Standards

- Shebang: `#!/usr/bin/env bash`
- Quote all variables: `"$VAR"` not `$VAR`
- `set -euo pipefail` for strict error handling
- Scripts live in `scripts/`; the skill-local `scripts/` copies of `orchestrate.sh` and `score-regression.sh` are generated, never edited by hand
- `scripts/transform.sh` is the single sync step for `claude-plugin/` and the skill-local runtime helpers; do not maintain separate sync scripts

## Plugin Distribution

Source of truth is `.claude/`. To update the checked-in Claude Code plugin (`claude-plugin/`):
1. Edit canonical files in `.claude/commands/`, `.claude/skills/`, or `.claude/hooks/autoresearch/`
2. Run `scripts/transform.sh` to regenerate `claude-plugin/` and the skill-local runtime helpers
3. Commit all generated files together

Do not hand-edit `claude-plugin/` files directly.

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
