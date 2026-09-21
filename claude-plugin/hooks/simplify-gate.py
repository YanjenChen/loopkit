#!/usr/bin/env python3
"""UserPromptSubmit hook: warns or blocks shipping verbs when too many LOC changed.

Fails open on any error — never blocks legitimate work due to hook malfunction.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    block, exec_sync, fail_open, inject, is_enabled, js_trim, js_truthy, log, prop, run,
    safe_parse_stdin,
)

HOOK_NAME = 'simplify-gate'

SHIPPING_VERBS = ['ship', 'merge', 'deploy', 'pr', 'publish', 'release']

NEGATION_PHRASES = [
    "don't ship", 'never deploy', 'not ready to merge',
    "don't merge", "don't deploy", "don't publish",
    "don't release", 'no ship', 'no merge', 'no deploy',
]

WARN_THRESHOLD = 400
BLOCK_THRESHOLD = 800

_DIGITS = re.compile(r'^[0-9]+\Z')


def has_shipping_verb(prompt):
    lower = prompt.lower()

    for phrase in NEGATION_PHRASES:
        if phrase in lower:
            return False

    for verb in SHIPPING_VERBS:
        # re.ASCII keeps \b identical to JavaScript's ASCII word boundaries.
        if re.search(r'\b' + verb + r'\b', prompt, re.IGNORECASE | re.ASCII):
            return True

    return False


def pending_loc():
    diff = exec_sync('git', 'diff', 'HEAD', '--numstat')
    loc = 0
    for line in js_trim(diff).split('\n'):
        if not line:
            continue
        parts = line.split('\t')
        added = parts[0]
        removed = parts[1] if len(parts) > 1 else ''
        loc += (int(added) if _DIGITS.match(added) else 0) + (int(removed) if _DIGITS.match(removed) else 0)
    untracked = exec_sync('git', 'ls-files', '--others', '--exclude-standard', '-z')
    for file_name in untracked.split('\0'):
        if not file_name:
            continue
        with open(file_name, 'rb') as handle:
            loc += handle.read().count(b'\n')
    return loc


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)

    prompt = prop(stdin, 'prompt')
    prompt = prompt if js_truthy(prompt) and isinstance(prompt, str) else ''
    if not prompt:
        sys.exit(0)

    # Fast bail: no shipping verb detected
    if not has_shipping_verb(prompt):
        sys.exit(0)

    loc = 0
    try:
        loc = pending_loc()
    except Exception:
        fail_open(HOOK_NAME, 'git-inspection-error')

    if loc < WARN_THRESHOLD:
        sys.exit(0)

    log(HOOK_NAME, {'loc': loc, 'action': 'block' if loc > BLOCK_THRESHOLD else 'warn'})

    if loc > BLOCK_THRESHOLD:
        block('BLOCKED: %d lines changed exceeds %d LOC shipping threshold. '
              'Simplify before shipping. Use AR_DISABLE_SIMPLIFY_GATE=1 to override.'
              % (loc, BLOCK_THRESHOLD))

    # 400–800 range: warn but allow
    inject('WARNING: %d lines changed. Consider simplifying before shipping.' % loc)


if __name__ == '__main__':
    run(HOOK_NAME, main)
