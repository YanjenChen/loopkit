#!/usr/bin/env python3
"""SessionStart hook: computes project context and persists session state.

Injects additionalContext with project root, branch, and paths.
Fails open on any error — never blocks session startup.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    exec_sync, get_session_id, inject, is_enabled, iso_now, js_cwd, js_join, js_tmpdir, js_trim,
    js_truthy, log, now_ms, run, safe_parse_stdin, save_session_state,
)

HOOK_NAME = 'session-init'
SESSION_MAX_AGE_MS = 24 * 60 * 60 * 1000  # 24 hours


def resolve_git_root():
    try:
        return js_trim(exec_sync('git', 'rev-parse', '--show-toplevel'))
    except Exception:
        return js_cwd()


def resolve_git_branch():
    try:
        return js_trim(exec_sync('git', 'rev-parse', '--abbrev-ref', 'HEAD'))
    except Exception:
        return ''


def prune_stale_session_files():
    try:
        now = now_ms()
        tmp = js_tmpdir()
        for entry in os.listdir(tmp):
            if not entry.startswith('ar-session-') or not entry.endswith('.json'):
                continue
            file_path = js_join(tmp, entry)
            try:
                if now - os.stat(file_path).st_mtime_ns / 1e6 > SESSION_MAX_AGE_MS:
                    os.unlink(file_path)
            except Exception:
                pass  # skip files we can't stat or delete
    except Exception:
        pass  # operating-system temp directory unreadable — skip


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)

    project_root = resolve_git_root()
    git_branch = resolve_git_branch()

    state = {
        'projectRoot': project_root,
        'plansPath': js_join(project_root, 'plans'),
        'reportsPath': js_join(project_root, 'plans', 'reports'),
        'gitBranch': git_branch,
        'sessionId': get_session_id(stdin),
        'iterationCount': 0,
        'startedAt': iso_now(),
    }

    save_session_state(stdin, state)
    prune_stale_session_files()

    log(HOOK_NAME, {'projectRoot': project_root, 'gitBranch': git_branch})

    inject(
        '## Session initialized\n'
        '- Project: %s\n'
        '- Branch: %s\n'
        '- Plans: %s\n'
        '- Reports: %s' % (project_root, git_branch, state['plansPath'], state['reportsPath'])
    )


if __name__ == '__main__':
    run(HOOK_NAME, main)
