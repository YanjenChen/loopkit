# Contributing to Autoresearch

Whether you're fixing a typo, adding examples, creating a new sub-command, or improving the loop protocol — this guide will get you up and running.

## Quick Start

Autoresearch is Markdown files that Claude Code discovers from `skills/` and `commands/` directories. No build step, no compilation — edit a `.md` file, invoke the skill, see your changes.

```bash
# 1. Clone the repo
git clone https://github.com/uditgoenka/autoresearch.git
cd autoresearch

# 2. Install via guided installer
./scripts/install.sh --global   # all projects (~/.claude by default)
./scripts/install.sh --local    # current project only (./.claude)

# 3. Or symlink for live editing (recommended for development)
ln -s $(pwd)/.claude/skills/autoresearch ~/.claude/skills/autoresearch
ln -s $(pwd)/.claude/commands/autoresearch ~/.claude/commands/autoresearch
ln -s $(pwd)/.claude/commands/autoresearch.md ~/.claude/commands/autoresearch.md
```

### Plugin Sync

The canonical source is `.claude/`. After making changes, run the transform to sync the checked-in Claude Code plugin (`claude-plugin/`) and the skill-local runtime helpers:

```bash
./scripts/transform.sh
```

## Repository Structure (v2.2.2)

```
autoresearch/
├── .claude/                                       ← CANONICAL SOURCE — edit here first
│   ├── skills/autoresearch/
│   │   ├── SKILL.md                               ← Thin routing table
│   │   └── references/                            ← Shared routing and review references
│   └── commands/
│       ├── autoresearch.md                        ← Core loop (self-contained, ~110 lines)
│       └── autoresearch/                          ← 12 subcommand files (self-contained)
├── claude-plugin/                                 ← Distribution package (Claude Code plugin install)
├── .claude-plugin/marketplace.json                ← Plugin marketplace entry
├── scripts/
│   ├── install.sh                                 ← Guided installer (Claude Code)
│   ├── transform.sh                               ← .claude/ → claude-plugin/ sync + skill-local helpers
│   ├── orchestrate.sh                             ← Orchestrator routing seam (canonical copy)
│   └── score-regression.sh                        ← Regression scoring backend (canonical copy)
├── tests/                                         ← Shell test suites + fixtures
├── guide/                                         ← Guides — one per command + advanced patterns
├── docs/                                          ← Project docs (architecture, changelog, standards)
├── COMPARISON.md                                  ← Karpathy vs Claude Autoresearch
└── CONTRIBUTING.md                                ← You are here
```

### What Each File Does

| File | Purpose | Edit when... |
|------|---------|-------------|
| `.claude/skills/autoresearch/SKILL.md` | Thin routing table — subcommand list, defaults, universal flags | Adding subcommands, changing defaults |
| `.claude/commands/autoresearch.md` | Core loop — self-contained instructions (~110 lines) | Changing loop behavior |
| `.claude/commands/autoresearch/*.md` | Subcommand files — each self-contained with full instructions | Modifying any subcommand |
| `references/security-checklist.md` | STRIDE + OWASP checklist (loaded by security command) | Adding security checks |
| `references/predict-personas.md` | 5 expert personas (loaded by predict command) | Adding/modifying personas |
| `references/reason-judge-protocol.md` | Adversarial refinement protocol (loaded by reason command) | Changing judge/critic behavior |
| `scripts/transform.sh` | Canonical transform (.claude/ → claude-plugin/, plus skill-local copies of the runtime helpers) | Adding new commands, reference files, or generated helper updates |
| `claude-plugin/` | Distribution package — synced from .claude/ by `scripts/transform.sh` | Don't edit directly — edit .claude/ |

## What to Contribute

### High-Value

| Type | Examples | Difficulty |
|------|----------|-----------|
| **New domain examples** | Add to `guide/examples-by-domain.md` | Easy |
| **Verification script templates** | Reusable verify/guard commands for common metrics | Easy |
| **Bug fixes** | Loop edge cases, incorrect behavior | Medium |
| **New sub-commands** | `/autoresearch:refactor`, `/autoresearch:test` | Medium |
| **OWASP/STRIDE additions** | New security checks | Medium |
| **Protocol improvements** | Better stuck-detection, smarter ideation | Hard |
| **MCP integration patterns** | Database, API, analytics verification examples | Hard |

### Low-Value (Please Don't)

- Reformatting or restructuring files without functional changes
- Adding comments to explain obvious things
- Whitespace-only changes

## Adding a New Sub-Command

### 1. Create the command file

```
.claude/commands/autoresearch/yourcommand.md
```

Self-contained file with: YAML frontmatter (`name`, `description`, `argument-hint`), argument parsing, setup gate, loop/phases, output, chain handoff. Target: 80-120 lines.

### 2. Register in SKILL.md

Add one row to the subcommands table:
```markdown
| `/autoresearch:yourcommand` | Description | Default iterations |
```

### 3. Create reference file (only if needed by 3+ commands)

Only create a reference in `references/` if shared by multiple commands. Single-command logic stays in the command file.

### 4. Run transform + update docs

```bash
./scripts/transform.sh   # sync claude-plugin/ + skill-local runtime helpers
```

Update: README.md (commands table), guide/ (new guide file), COMPARISON.md (subcommand count).

## Commit Messages

[Conventional commits](https://www.conventionalcommits.org/):

| Prefix | When |
|--------|------|
| `feat:` | New feature or sub-command |
| `fix:` | Bug fix |
| `docs:` | Documentation-only |
| `refactor:` | Restructuring without behavior change |
| `chore:` | Maintenance, version bumps |

## Pull Request Guidelines

1. **One PR = one feature.** Don't bundle unrelated changes.
2. **Branch from `master`.** Target `master` as base.
3. **Run `scripts/transform.sh`** after any changes to `.claude/`.
4. **Run the test suites locally** — there is no CI (see Testing).
5. **Update docs** — README, guide, COMPARISON as needed.
6. **Don't bump the version.** Maintainers handle versioning.

## Testing

Test changes by hand in a real Claude Code session:

1. Symlink your working tree (see Quick Start)
2. Open Claude Code in a real project
3. Invoke the command (`/autoresearch`, `/autoresearch:plan`, etc.)
4. Verify behavior matches your changes
5. Try edge cases — wrong metric? 0 files in scope? Guard always fails?

The repo also includes shell-based test suites for the generated plugin and the hook/runtime contracts. There is no CI, so run them locally:

- `bash scripts/transform.sh` — regenerate `claude-plugin/` and the skill-local runtime helpers
- `bash tests/test-hooks.sh` — hook contracts and fail-open behavior
- `bash tests/test-orchestrator.sh` — orchestrator routing seam (`scripts/orchestrate.sh`)
- `bash tests/test-regression.sh` — regression scoring (`scripts/score-regression.sh`) and spec contract
- `bash tests/test-maintenance.sh` — transform determinism (no generated drift)

## Getting Help

- **Questions?** Open an [issue](https://github.com/uditgoenka/autoresearch/issues)
- **Ideas?** Open an issue with `[Idea]` prefix
- **Discussion?** Tag [@uditgoenka](https://github.com/uditgoenka) in your PR

Thanks for contributing!

## Hook Development

### Adding a New Hook

1. Create `.claude/hooks/autoresearch/{name}.cjs`
2. Use the shared library: `require('./lib/ar-hook-utils.cjs')`
3. Follow the pattern:
   ```js
   'use strict';
   const { isEnabled, safeParseStdin, log, block, allow, inject } = require('./lib/ar-hook-utils.cjs');
   try {
     if (!isEnabled('hook-name')) process.exit(0);
     const stdin = safeParseStdin();
     if (!stdin) process.exit(0);
     // ... hook logic ...
     process.exit(0);
   } catch {
     process.exit(0); // fail-open
   }
   ```
4. Register in `hooks.json` under the correct event
5. Run `bash scripts/transform.sh` to update the plugin distribution and bundled runtime helpers
6. Run `bash tests/test-hooks.sh` to verify

### Hook Rules

- **Fail-open:** Always wrap in try/catch, always exit 0 on error, and emit a visible redacted diagnostic when available
- **No console.log:** Corrupts stdout JSON. Use `process.stderr.write()` for debug
- **No external deps:** Pure Node.js builtins only (exception: vendored `lib/ignore.cjs`)
- **Exit codes:** 0 = allow/inject, 2 = block. No other exit codes
- **State:** Use the OS temporary directory via `loadSessionState()` / `saveSessionState()`; this is not a repo path

### Testing Hooks

```bash
# Syntax check
node --check .claude/hooks/autoresearch/my-hook.cjs

# Manual test
echo '{"tool_name":"Read","tool_input":{"file_path":"test.txt"}}' | node .claude/hooks/autoresearch/my-hook.cjs
echo "Exit code: $?"

# Full test suite
bash tests/test-hooks.sh
```
