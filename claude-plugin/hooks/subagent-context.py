#!/usr/bin/env python3
"""SubagentStart hook: in a run session, tells subagents where the run's analysis data lives.

Analysts, the decider and the critic start without the main session's context;
this gives them the run status and the read-only commands and files to use.
Outside a run session it does nothing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from hook_utils import (  # noqa: E402
    inject, is_enabled, js_truthy, log, run, run_context, run_status_text, safe_parse_stdin,
)

HOOK_NAME = 'subagent-context'


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)
    run_paths = run_context(stdin)
    if run_paths is None:
        sys.exit(0)

    knowledge = run_paths.knowledge if os.path.isfile(run_paths.knowledge) else '(none)'
    text = '\n'.join([
        '## loopkit run context (for subagents)',
        run_status_text(run_paths),
        '',
        'Analysis data (read-only):',
        '- codebase summary of c000: %s' % knowledge,
        '- `loopkit summary`: every candidate, ideas tried per parent, pattern analysis',
        '- `loopkit show <id> [--log]`, `loopkit lineage <id>`, `loopkit diff <a> <b>`',
        '- the code under analysis: %s' % run_paths.agent,
        'Do not modify files or run commands that write; the main session implements the chosen idea.',
    ])
    log(HOOK_NAME, {'action': 'inject'})
    inject(text, 'SubagentStart')


if __name__ == '__main__':
    run(HOOK_NAME, main)
