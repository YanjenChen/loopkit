# Contributing to Autoresearch

Whether you're fixing a typo, adding examples, creating a new sub-command, or improving the loop protocol — this guide will get you up and running.

## Quick Start

Autoresearch is a Claude Code plugin: Markdown files that Claude Code discovers from the plugin's `skills/` and `commands/` directories, plus shell runtime helpers and Python hooks. Everything lives in `claude-plugin/`, the single source of truth. No build step, no compilation, no sync step — edit a file in `claude-plugin/`, reload the plugin, see your changes.

```bash
# Clone the repo
git clone https://github.com/YanjenChen/loopkit.git
cd loopkit
```

Then, in Claude Code:

1. **Add your clone as a local marketplace.** In `/plugins` (Marketplaces tab), add the repo root — the folder containing `.claude-plugin/marketplace.json` — as a local-directory marketplace.
2. **Install the plugin** from that marketplace.
3. **Edit and reload.** Plugins from a local-directory marketplace load in place from that folder, so edit files in `claude-plugin/` directly and run `/reload-plugins` to pick up each change.

The hook guardrails need Python 3.8 or newer, with `python3` on the PATH of the shell Claude Code uses.

## Repository Structure (v2.2.2)

```
autoresearch/
├── claude-plugin/                                 ← THE PLUGIN — single source of truth, edit here
│   ├── .claude-plugin/plugin.json                 ← Plugin manifest
│   ├── skills/autoresearch/
│   │   ├── SKILL.md                               ← Thin routing table
│   │   ├── references/                            ← Shared routing and review references
│   │   └── scripts/                               ← Runtime helpers: orchestrate.sh, score-regression.sh
│   ├── commands/
│   │   ├── autoresearch.md                        ← Core loop (self-contained, ~110 lines)
│   │   └── autoresearch/                          ← 12 subcommand files (self-contained)
│   └── hooks/                                     ← Hook guardrails (hooks.json, .py hooks, lib/)
├── .claude-plugin/marketplace.json                ← Plugin marketplace entry (source: ./claude-plugin)
├── tests/                                         ← Shell test suites + fixtures
├── guide/                                         ← Guides — one per command + advanced patterns
├── docs/                                          ← Project docs (architecture, changelog, standards)
├── COMPARISON.md                                  ← Karpathy vs Claude Autoresearch
└── CONTRIBUTING.md                                ← You are here
```

### What Each File Does

| File | Purpose | Edit when... |
|------|---------|-------------|
| `claude-plugin/skills/autoresearch/SKILL.md` | Thin routing table — subcommand list, defaults, universal flags | Adding subcommands, changing defaults |
| `claude-plugin/commands/autoresearch.md` | Core loop — self-contained instructions (~110 lines) | Changing loop behavior |
| `claude-plugin/commands/autoresearch/*.md` | Subcommand files — each self-contained with full instructions | Modifying any subcommand |
| `references/security-checklist.md` | STRIDE + OWASP checklist (loaded by security command) | Adding security checks |
| `references/predict-personas.md` | 5 expert personas (loaded by predict command) | Adding/modifying personas |
| `references/reason-judge-protocol.md` | Adversarial refinement protocol (loaded by reason command) | Changing judge/critic behavior |
| `claude-plugin/skills/autoresearch/scripts/orchestrate.sh` | Orchestrator routing seam | Changing orchestrator routing, screening, or plateau logic |
| `claude-plugin/skills/autoresearch/scripts/score-regression.sh` | Regression scoring backend | Changing regression scoring or verdicts |
| `claude-plugin/hooks/` | Hook guardrails and their `hooks.json` registration | Adding or changing hooks (see Hook Development) |

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
claude-plugin/commands/autoresearch/yourcommand.md
```

Self-contained file with: YAML frontmatter (`name`, `description`, `argument-hint`), argument parsing, setup gate, loop/phases, output, chain handoff. Target: 80-120 lines.

### 2. Register in SKILL.md

Add one row to the subcommands table:
```markdown
| `/autoresearch:yourcommand` | Description | Default iterations |
```

### 3. Create reference file (only if needed by 3+ commands)

Only create a reference in `references/` if shared by multiple commands. Single-command logic stays in the command file.

### 4. Reload + update docs

Run `/reload-plugins` in a session with your clone installed as a local-directory marketplace (see Quick Start), then invoke the new command.

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
3. **Edit `claude-plugin/` directly.** There is no sync step and no generated copy.
4. **Run the test suites locally** — there is no CI (see Testing).
5. **Update docs** — README, guide, COMPARISON as needed.
6. **Don't bump the version.** Maintainers handle versioning.

## Testing

Test changes by hand in a real Claude Code session:

1. Install your clone as a local-directory marketplace plugin (see Quick Start) and run `/reload-plugins` after each edit
2. Open Claude Code in a real project
3. Invoke the command (`/autoresearch`, `/autoresearch:plan`, etc.)
4. Verify behavior matches your changes
5. Try edge cases — wrong metric? 0 files in scope? Guard always fails?

The repo also includes three shell-based test suites for the plugin's hook and runtime contracts. They need `python3` on the PATH. There is no CI, so run them locally:

- `bash tests/test-hooks.sh` — hook contracts and fail-open behavior
- `bash tests/test-orchestrator.sh` — orchestrator routing seam (`claude-plugin/skills/autoresearch/scripts/orchestrate.sh`)
- `bash tests/test-regression.sh` — regression scoring (`claude-plugin/skills/autoresearch/scripts/score-regression.sh`) and spec contract

## Getting Help

- **Questions?** Open an [issue](https://github.com/uditgoenka/autoresearch/issues)
- **Ideas?** Open an issue with `[Idea]` prefix
- **Discussion?** Tag [@uditgoenka](https://github.com/uditgoenka) in your PR

Thanks for contributing!

## Hook Development

### Adding a New Hook

1. Create `claude-plugin/hooks/{name}.py`
2. Use the shared library `lib/ar_hook_utils.py`: put `lib/` on `sys.path`, then import from `ar_hook_utils`
3. Follow the pattern:
   ```python
   #!/usr/bin/env python3
   """PreToolUse hook: one-line summary. Fails open on any error."""

   import os
   import sys

   sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

   from ar_hook_utils import (  # noqa: E402
       ask_permission, block, inject, is_enabled, js_truthy, log, prop, run, safe_parse_stdin,
   )

   HOOK_NAME = 'hook-name'


   def main():
       if not is_enabled(HOOK_NAME):
           sys.exit(0)
       stdin = safe_parse_stdin(HOOK_NAME)
       if not js_truthy(stdin):
           sys.exit(0)
       command = prop(prop(stdin, 'tool_input'), 'command')
       # ... hook logic, e.g. log(HOOK_NAME, {'action': 'block'}) then block(reason) ...
       # block(reason) exits 2; ask_permission(reason) and inject(text) exit 0
       sys.exit(0)


   if __name__ == '__main__':
       run(HOOK_NAME, main)  # fail-open: any unexpected exception exits 0 with a diagnostic
   ```
4. Register in `claude-plugin/hooks/hooks.json` under the correct event, invoking it through `hook-runner.sh` like the existing entries
5. Run `/reload-plugins` to load the new registration
6. Run `bash tests/test-hooks.sh` to verify

### Hook Rules

- **Fail-open:** Always run the hook body through `run(HOOK_NAME, main)`, so any error exits 0 and emits a visible redacted diagnostic
- **No print():** Stray stdout corrupts the JSON output. Write debug output to `sys.stderr`
- **No external deps:** Python standard library only, compatible with Python 3.8+ (exception: vendored `lib/ignore.py`)
- **Exit codes:** 0 = allow/inject, 2 = block. No other exit codes
- **State:** Use the OS temporary directory via `load_session_state()` / `save_session_state()`; this is not a repo path

### Testing Hooks

```bash
# Syntax check (compiles without writing __pycache__)
python3 -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' claude-plugin/hooks/my-hook.py

# Manual test
echo '{"tool_name":"Read","tool_input":{"file_path":"test.txt"}}' | python3 -B claude-plugin/hooks/my-hook.py
echo "Exit code: $?"

# Full test suite
bash tests/test-hooks.sh
```
