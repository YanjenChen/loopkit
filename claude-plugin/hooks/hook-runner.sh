#!/usr/bin/env bash
# Wrapper to run the Python hook scripts with a clean environment.
# Silences shell profile noise that would corrupt JSON stdout, and keeps
# unrelated environment variables (credentials, tokens) away from the hooks.
# -I: isolated mode (no user site-packages, no PYTHON* variables, no cwd imports);
# -B: no .pyc files in the plugin; -X utf8: UTF-8 regardless of the locale.
exec env -i HOME="$HOME" PATH="$PATH" \
  TERM="${TERM:-}" SHELL="${SHELL:-}" USER="${USER:-}" LANG="${LANG:-}" \
  TMPDIR="${TMPDIR:-}" TEMP="${TEMP:-}" TMP="${TMP:-}" \
  CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}" \
  CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}" \
  CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-}" \
  CLAUDE_ENV_FILE="${CLAUDE_ENV_FILE:-}" \
  LOOPKIT_DATA_DIR="${LOOPKIT_DATA_DIR:-}" \
  LOOPKIT_DISABLE_SCOUT_BLOCK="${LOOPKIT_DISABLE_SCOUT_BLOCK:-}" \
  LOOPKIT_DISABLE_PRIVACY_BLOCK="${LOOPKIT_DISABLE_PRIVACY_BLOCK:-}" \
  LOOPKIT_DISABLE_DANGEROUS_CMD_BLOCK="${LOOPKIT_DISABLE_DANGEROUS_CMD_BLOCK:-}" \
  LOOPKIT_DISABLE_ITERATION_CONTEXT="${LOOPKIT_DISABLE_ITERATION_CONTEXT:-}" \
  LOOPKIT_DISABLE_SUBAGENT_CONTEXT="${LOOPKIT_DISABLE_SUBAGENT_CONTEXT:-}" \
  LOOPKIT_DISABLE_SESSION_INIT="${LOOPKIT_DISABLE_SESSION_INIT:-}" \
  LOOPKIT_DISABLE_STOP_NOTIFY="${LOOPKIT_DISABLE_STOP_NOTIFY:-}" \
  LOOPKIT_NOTIFY_WEBHOOK="${LOOPKIT_NOTIFY_WEBHOOK:-}" \
  python3 -I -B -X utf8 "$@"
