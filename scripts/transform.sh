#!/usr/bin/env bash
# Sync the canonical Claude Code source (.claude/) into the checked-in plugin (claude-plugin/).
# Run after any change to .claude/ source files or to the root runtime helpers in scripts/.
#
# Usage:
#   ./scripts/transform.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CLAUDE_SKILLS="$REPO_ROOT/.claude/skills/autoresearch"
CLAUDE_COMMANDS="$REPO_ROOT/.claude/commands"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)  printf 'Usage: %s\n' "$0"; exit 0 ;;
    *)          printf 'Unknown flag: %s\n' "$1" >&2; exit 1 ;;
  esac
  shift
done

die() { printf 'Error: %s\n' "$1" >&2; exit 1; }

[[ -d "$CLAUDE_SKILLS" ]] || die "Source not found: $CLAUDE_SKILLS"
[[ -d "$CLAUDE_COMMANDS" ]] || die "Source not found: $CLAUDE_COMMANDS"

# Root helpers are the maintained runtime. Skill-local copies are generated
# package resources so standalone installs never depend on a source checkout.
sync_runtime_helpers() {
  local dst helper
  for dst in "$CLAUDE_SKILLS" "$REPO_ROOT/claude-plugin/skills/autoresearch"; do
    rm -rf "$dst/scripts"
    mkdir -p "$dst/scripts"
    for helper in orchestrate.sh score-regression.sh; do
      cp "$REPO_ROOT/scripts/$helper" "$dst/scripts/$helper"
      chmod +x "$dst/scripts/$helper"
    done
  done

  printf 'Runtime: synced canonical helpers to all skill distributions\n'
}

# Claude's checked-in plugin is generated from the canonical .claude surface too.
transform_claude() {
  local dst_skills="$REPO_ROOT/claude-plugin/skills/autoresearch"
  local dst_commands="$REPO_ROOT/claude-plugin/commands"

  rm -rf "$dst_skills" "$dst_commands/autoresearch" "$dst_commands/autoresearch.md"
  mkdir -p "$REPO_ROOT/claude-plugin/skills" "$dst_commands"
  cp -R "$CLAUDE_SKILLS" "$dst_skills"
  cp -R "$CLAUDE_COMMANDS/autoresearch" "$dst_commands/autoresearch"
  cp "$CLAUDE_COMMANDS/autoresearch.md" "$dst_commands/autoresearch.md"

  printf 'Claude: transformed %s → %s\n' ".claude/" "claude-plugin/"
}

# --- Claude Plugin Hooks Transform ---

transform_hooks() {
  local src_hooks="$REPO_ROOT/.claude/hooks/autoresearch"
  local dst_hooks="$REPO_ROOT/claude-plugin/hooks"

  [[ -d "$src_hooks" ]] || { printf 'Hooks: no source at %s, skipping\n' "$src_hooks"; return; }

  rm -rf "$dst_hooks"
  mkdir -p "$dst_hooks/lib"

  # Copy all hook files
  for f in "$src_hooks"/*.cjs "$src_hooks"/*.sh "$src_hooks"/*.json; do
    [[ -f "$f" ]] || continue
    cp "$f" "$dst_hooks/$(basename "$f")"
  done

  # Copy .ckignore baseline
  [[ -f "$src_hooks/.ckignore" ]] && cp "$src_hooks/.ckignore" "$dst_hooks/.ckignore"

  # Copy lib directory
  for f in "$src_hooks"/lib/*.cjs; do
    [[ -f "$f" ]] || continue
    cp "$f" "$dst_hooks/lib/$(basename "$f")"
  done

  # Ensure runner is executable
  [[ -f "$dst_hooks/node-hook-runner.sh" ]] && chmod +x "$dst_hooks/node-hook-runner.sh"

  printf 'Hooks: transformed %s → claude-plugin/hooks/\n' ".claude/hooks/autoresearch/"
}

# --- Main ---

transform_claude
sync_runtime_helpers
transform_hooks

printf 'Transform complete.\n'
