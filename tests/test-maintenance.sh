#!/bin/bash
# Maintenance contract: deterministic transforms of the canonical .claude/ source.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PASS=0; FAIL=0; TOTAL=0
pass() { printf '  PASS: %s\n' "$1"; PASS=$((PASS + 1)); TOTAL=$((TOTAL + 1)); }
fail() { printf '  FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); TOTAL=$((TOTAL + 1)); }

TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/autoresearch-maintenance-XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

copy_checkout() {
  local dst="$1"
  mkdir -p "$dst"
  (cd "$REPO_ROOT" && tar --exclude='./.git' -cf - .) \
    | (cd "$dst" && tar -xf -)
}

printf '\n--- transform: deterministic generated distributions ---\n'
SNAPSHOT="$TMP_ROOT/transform"
copy_checkout "$SNAPSHOT"
(
  cd "$SNAPSHOT" || exit 1
  git init -q
  git config user.name test
  git config user.email test@example.com
  git add -A && git commit -qm baseline
  bash scripts/transform.sh >/dev/null
  git diff --quiet && [[ -z "$(git status --porcelain)" ]]
) && pass "transform is idempotent with no generated drift" || fail "transform is idempotent with no generated drift"

(
  cd "$SNAPSHOT" || exit 1
  printf '\ncanonical sentinel\n' >> .claude/skills/autoresearch/scripts/orchestrate.sh
  printf '\nclaude sentinel\n' >> claude-plugin/commands/autoresearch.md
  printf '\nhook sentinel\n' >> claude-plugin/hooks/session-init.cjs
  bash scripts/transform.sh >/dev/null
  git diff --quiet && [[ -z "$(git status --porcelain)" ]]
) && pass "transform repairs drift in generated Claude surfaces" || fail "transform repairs drift in generated Claude surfaces"

for skill in \
  .claude/skills/autoresearch \
  claude-plugin/skills/autoresearch; do
  for helper in orchestrate.sh score-regression.sh; do
    generated="$REPO_ROOT/$skill/scripts/$helper"
    [[ -x "$generated" ]] && cmp -s "$REPO_ROOT/scripts/$helper" "$generated" \
      && pass "$skill bundles canonical $helper" \
      || fail "$skill bundles canonical $helper"
  done
done

printf '\n=== Results: %d/%d passed ===' "$PASS" "$TOTAL"
if [[ "$FAIL" -gt 0 ]]; then printf ' (%d FAILED)\n' "$FAIL"; exit 1; else printf ' (all passed)\n'; exit 0; fi
