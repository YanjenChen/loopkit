#!/usr/bin/env python3
"""UserPromptSubmit hook: in a run session, injects a short run status with each prompt.

Keeps the batch, the front and the latest iterations in view after context
compaction. Outside a run session it does nothing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from hook_utils import (  # noqa: E402
    inject, is_enabled, js_truthy, log, run, run_context, run_status_text, safe_parse_stdin,
)

HOOK_NAME = 'iteration-context'


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)
    run_paths = run_context(stdin)
    if run_paths is None:
        sys.exit(0)

    log(HOOK_NAME, {'action': 'inject'})
    inject('## loopkit run status\n%s' % run_status_text(run_paths), 'UserPromptSubmit')


if __name__ == '__main__':
    run(HOOK_NAME, main)
