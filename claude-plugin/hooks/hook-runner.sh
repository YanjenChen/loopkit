#!/usr/bin/env bash
# Wrapper to run the Python hook scripts with a clean environment.
# Silences shell profile noise that would corrupt JSON stdout, and keeps
# unrelated environment variables (credentials, tokens) away from the hooks.
# -I: isolated mode (no user site-packages, no PYTHON* variables, no cwd imports);
# -B: no .pyc files in the plugin; -X utf8: UTF-8 regardless of the locale.
exec env -i HOME="$HOME" PATH="$PATH" \
  TERM="$TERM" SHELL="$SHELL" USER="$USER" LANG="$LANG" \
  TMPDIR="${TMPDIR:-}" TEMP="${TEMP:-}" TMP="${TMP:-}" \
  AR_DISABLE_SCOUT_BLOCK="${AR_DISABLE_SCOUT_BLOCK:-}" \
  AR_DISABLE_PRIVACY_BLOCK="${AR_DISABLE_PRIVACY_BLOCK:-}" \
  AR_DISABLE_DANGEROUS_CMD_BLOCK="${AR_DISABLE_DANGEROUS_CMD_BLOCK:-}" \
  AR_DISABLE_ITERATION_CONTEXT="${AR_DISABLE_ITERATION_CONTEXT:-}" \
  AR_DISABLE_SUBAGENT_CONTEXT="${AR_DISABLE_SUBAGENT_CONTEXT:-}" \
  AR_DISABLE_SESSION_INIT="${AR_DISABLE_SESSION_INIT:-}" \
  AR_DISABLE_STOP_NOTIFY="${AR_DISABLE_STOP_NOTIFY:-}" \
  AR_NOTIFY_WEBHOOK="${AR_NOTIFY_WEBHOOK:-}" \
  python3 -I -B -X utf8 "$@"
