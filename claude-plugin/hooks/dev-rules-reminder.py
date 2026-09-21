#!/usr/bin/env python3
"""UserPromptSubmit hook: every 5th prompt, reminds the agent of the plan and standards paths."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    inject, is_enabled, js_cwd, js_join, js_truthy, load_session_state, log, now_ms, run,
    safe_parse_stdin,
)

HOOK_NAME = 'dev-rules-reminder'


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)

    state = load_session_state(stdin)

    # Skip if iteration-context already injected this same turn (within 2 seconds)
    last_injection = state.get('lastContextInjection')
    if js_truthy(last_injection) and (now_ms() - last_injection) < 2000:
        log('dev-rules-reminder', {'action': 'skip', 'reason': 'iteration-context-fired'})
        sys.exit(0)

    # Only inject on every 5th iteration, same cadence as iteration-context
    iteration_count = state.get('iterationCount')
    if (iteration_count if js_truthy(iteration_count) else 0) % 5 != 0:
        sys.exit(0)

    plans_path = state.get('plansPath')
    if not js_truthy(plans_path):
        plans_path = js_join(js_cwd(), 'plans')

    text = '\n'.join([
        '## Dev context',
        '- Plan: %s (check for active plan.md)' % plans_path,
        '- Standards: docs/code-standards.md',
    ])

    log('dev-rules-reminder', {'action': 'inject', 'iterationCount': state.get('iterationCount')})
    inject(text)


if __name__ == '__main__':
    run(HOOK_NAME, main)
