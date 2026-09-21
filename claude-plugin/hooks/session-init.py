#!/usr/bin/env python3
"""SessionStart hook: in a run session, injects the run's state and exports its environment.

Fires on startup, resume, clear and compaction, so a fresh or compacted context
learns which run it is working on. CUDA_VISIBLE_DEVICES and
PYTHONDONTWRITEBYTECODE are also written to CLAUDE_ENV_FILE so every Bash
command of the session uses the run's GPU. Outside a run session it does
nothing. Fails open on any error — never blocks session startup.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from hook_utils import (  # noqa: E402
    inject, is_enabled, js_truthy, log, prop, run, run_context, run_status_text, safe_parse_stdin,
)

HOOK_NAME = 'session-init'


def export_env(run_paths):
    env_file = os.environ.get('CLAUDE_ENV_FILE')
    if not env_file:
        return
    gpu = run_paths.info().get('gpu')
    lines = ['export PYTHONDONTWRITEBYTECODE=1']
    if gpu:
        lines.append("export CUDA_VISIBLE_DEVICES='%s'" % gpu.replace("'", ''))
    with open(env_file, 'a', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)
    run_paths = run_context(stdin)
    if run_paths is None:
        sys.exit(0)

    export_env(run_paths)
    log(HOOK_NAME, {'action': 'inject', 'category': str(prop(stdin, 'source') or 'startup')})
    inject('## loopkit run session\n%s\n\n'
           'Each iteration is `/loopkit:iter`. Only loopkit commands write git state and run data; '
           'edit files only inside the agent worktree.' % run_status_text(run_paths), 'SessionStart')


if __name__ == '__main__':
    run(HOOK_NAME, main)
