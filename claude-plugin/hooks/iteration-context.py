#!/usr/bin/env python3
"""UserPromptSubmit hook: every 5th prompt, injects the tail of the active results TSV."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    find_recent_tsv, increment_counter, inject, is_enabled, js_cwd, js_relative, js_truthy,
    load_session_state, log, now_ms, prop, read_tsv_tail, run, safe_parse_stdin,
    save_session_state,
)

HOOK_NAME = 'iteration-context'

AR_COMMANDS = [
    'autoresearch', '/autoresearch:', 'loop', 'debug', 'fix', 'scenario',
    'predict', 'learn', 'reason', 'probe', 'security',
]


def has_ar_command(prompt):
    if not prompt or not isinstance(prompt, str):
        return False
    lower = prompt.lower()
    return any(cmd in lower for cmd in AR_COMMANDS)


def format_rows(header, rows):
    lines = []
    if header:
        lines.append(header)
    lines.extend(rows)
    return '\n'.join(lines)


def relative_path(cwd, abs_path):
    try:
        return js_relative(cwd, abs_path)
    except Exception:
        return abs_path


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)

    load_session_state(stdin)
    iteration_count = increment_counter(stdin, 'iterationCount')

    # Throttle: only inject every 5th prompt
    if iteration_count % 5 != 0:
        log('iteration-context', {'action': 'skip', 'iterationCount': iteration_count})
        sys.exit(0)

    # Mark injection time
    fresh_state = load_session_state(stdin)
    fresh_state['lastContextInjection'] = int(now_ms())
    save_session_state(stdin, fresh_state)

    cwd = js_cwd()
    tsv_path = find_recent_tsv(cwd, 30)

    if not tsv_path:
        log('iteration-context', {'action': 'skip', 'iterationCount': iteration_count, 'reason': 'no-tsv'})
        sys.exit(0)

    tsv = read_tsv_tail(tsv_path, 3)
    if not tsv:
        log('iteration-context', {'action': 'skip', 'iterationCount': iteration_count, 'reason': 'tsv-unreadable'})
        sys.exit(0)

    rel_tsv = relative_path(cwd, tsv_path)
    row_block = format_rows(tsv['header'], tsv['rows'])

    text = '## Active iteration state\n**TSV:** %s\n**Iteration:** %s | **Rows:** %d\n\n%s' % (
        rel_tsv, iteration_count, tsv['total'], row_block)

    # Append loop state info when prompt contains autoresearch command content
    prompt = prop(stdin, 'prompt')
    if has_ar_command(prompt if js_truthy(prompt) else ''):
        text += '\n\n**Loop state:** active — %d iterations recorded, last 3 rows above' % tsv['total']

    log('iteration-context', {'action': 'inject', 'iterationCount': iteration_count, 'tsvRows': tsv['total']})
    inject(text)


if __name__ == '__main__':
    run(HOOK_NAME, main)
