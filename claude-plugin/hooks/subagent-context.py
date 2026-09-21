#!/usr/bin/env python3
"""SubagentStart hook: injects project, branch, and latest iteration state into subagents."""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    find_recent_tsv, inject, is_enabled, js_cwd, js_join, js_relative, js_str, js_trim,
    js_truthy, load_session_state, log, prop, read_tsv_tail, run, safe_parse_stdin,
)

HOOK_NAME = 'subagent-context'

_COLUMN_SPLIT = re.compile(r'\t|\|')
_NUMBER = re.compile(r'^-?[0-9]+(?:\.[0-9]+)?\Z')


def relative_path(cwd, abs_path):
    try:
        return js_relative(cwd, abs_path)
    except Exception:
        return abs_path


def summarize_last_row(header, row):
    if not row:
        return 'none'
    # Try to extract status + first numeric metric from the row
    header_cols = _COLUMN_SPLIT.split(header) if header else []
    row_cols = _COLUMN_SPLIT.split(row)
    parts = []
    for i, val in enumerate(row_cols):
        col = (header_cols[i] if i < len(header_cols) else '').lower()
        if 'status' in col or 'result' in col or 'pass' in col or 'fail' in col:
            parts.append(js_trim(val))
        elif _NUMBER.match(js_trim(val)) and len(parts) < 3:
            label = js_trim(header_cols[i]) + '=' if i < len(header_cols) and header_cols[i] else ''
            parts.append(label + js_trim(val))
    return ', '.join(parts) if parts else ', '.join(row_cols[:3])


def or_default(value, default):
    return js_str(value if js_truthy(value) else default)


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)

    state = load_session_state(stdin)
    cwd = js_cwd()
    tsv_path = find_recent_tsv(cwd, 30)

    if not tsv_path:
        log('subagent-context', {'action': 'skip', 'reason': 'no-active-tsv'})
        sys.exit(0)

    tsv = read_tsv_tail(tsv_path, 1)
    rel_tsv = relative_path(cwd, tsv_path)
    latest_summary = summarize_last_row(tsv['header'], tsv['rows'][0] if tsv['rows'] else None) if tsv else 'none'

    text = '\n'.join([
        '## Autoresearch context (for subagent)',
        '- Project: %s' % or_default(state.get('projectRoot'), cwd),
        '- Branch: %s' % or_default(state.get('gitBranch'), 'unknown'),
        '- Plans: %s' % or_default(state.get('plansPath'), js_join(cwd, 'plans')),
        '- Reports: %s' % or_default(state.get('reportsPath'), js_join(cwd, 'plans', 'reports')),
        '- Active TSV: %s' % rel_tsv,
        '- Iteration: %s' % or_default(state.get('iterationCount'), 0),
        '- Latest: %s' % latest_summary,
    ])

    log('subagent-context', {'action': 'inject', 'subagentType': or_default(prop(stdin, 'subagent_type'), 'unknown')})
    inject(text)


if __name__ == '__main__':
    run(HOOK_NAME, main)
