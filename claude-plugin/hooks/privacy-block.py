#!/usr/bin/env python3
"""PreToolUse hook: escalates clear sensitive-file access to the host permission UI.

Fails open on any error — never blocks legitimate work due to hook malfunction.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    ask_permission, inject, is_enabled, is_remote_operand, js_basename, js_truthy, log, prop,
    run, safe_parse_stdin, shell_segments,
)

HOOK_NAME = 'privacy-block'

SENSITIVE_PATTERNS = [
    '.env',
    '.env.local',
    '.env.production',
    '.env.development',
    '.pem',
    '.key',
    '.p12',
    '.pfx',
    'id_rsa',
    'id_ed25519',
    '.ssh/',
    'credentials.json',
    'credentials.yaml',
    'secret',
    'api_key',
    'apikey',
    '.aws/credentials',
]

ALLOWED_EXCEPTIONS = [
    '.env.example',
    '.env.sample',
    '.env.template',
    '.env.test',
]


def basename(file_path):
    return js_basename(file_path).lower()


def is_sensitive(file_path):
    if not isinstance(file_path, str) or not file_path:
        return False
    normalized = file_path.replace('\\', '/').lower()
    base = basename(file_path)

    # Check exceptions first
    for exc in ALLOWED_EXCEPTIONS:
        if base == exc or normalized.endswith('/' + exc):
            return False

    # Check sensitive patterns
    for pattern in SENSITIVE_PATTERNS:
        lp = pattern.lower()
        if (base == lp
                or normalized.endswith('/' + lp)
                or normalized.endswith(lp)
                or ('/' + lp + '/') in normalized
                or lp in base):
            return True

    return False


CLEAR_OPERATIONS = {
    'cat', 'head', 'tail', 'less', 'more', 'grep', 'cp', 'mv', 'scp', 'rsync',
    'curl', 'wget', 'tee', 'sed', 'awk', 'chmod', 'chown', 'rm', 'truncate',
    'source', '.',
}
_OPTION_PREFIX = re.compile(r'^(?:--upload-file|-T|--data-binary|--data|--data-raw|--output|-o)=?')
_REDIRECT = re.compile(r'^(?:>|>>|<|<<)\Z')


def bash_sensitivity(command):
    for tokens in shell_segments(command):
        executable = js_basename(tokens[0] if tokens else '').lower()
        if executable in CLEAR_OPERATIONS:
            operands = tokens[2:] if executable in ('grep', 'sed', 'awk') else tokens[1:]
            for token in operands:
                operand = _OPTION_PREFIX.sub('', token, count=1)
                if (operand and not operand.startswith('-') and not is_remote_operand(operand)
                        and is_sensitive(operand)):
                    return 'clear'
        for i in range(len(tokens) - 1):
            if _REDIRECT.match(tokens[i]) and is_sensitive(tokens[i + 1]):
                return 'clear'

    lower = command.lower()
    if any(pattern.lower() in lower for pattern in SENSITIVE_PATTERNS):
        return 'ambiguous'
    return 'none'


STRUCTURED_TOOLS = {'Read', 'Edit', 'Write', 'Glob', 'Grep'}


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)

    tool_name = prop(stdin, 'tool_name')
    tool_input = prop(stdin, 'tool_input')
    if not js_truthy(tool_input):
        sys.exit(0)

    if tool_name in STRUCTURED_TOOLS:
        raw_path = prop(tool_input, 'file_path')
        if not js_truthy(raw_path):
            raw_path = prop(tool_input, 'path')
        if not js_truthy(raw_path):
            raw_path = ''

        if is_sensitive(raw_path):
            log(HOOK_NAME, {'action': 'ask', 'tool': tool_name, 'category': 'sensitive-file'})
            ask_permission('This operation targets a potentially sensitive file. '
                           'Confirm access in the host permission prompt.')

        sys.exit(0)

    if tool_name == 'Bash':
        command = prop(tool_input, 'command')
        command = command if js_truthy(command) else ''
        sensitivity = bash_sensitivity(command)
        if sensitivity == 'clear':
            log(HOOK_NAME, {'action': 'ask', 'tool': tool_name, 'category': 'sensitive-command'})
            ask_permission('This command clearly accesses a potentially sensitive file. '
                           'Confirm access in the host permission prompt.')
        if sensitivity == 'ambiguous':
            log(HOOK_NAME, {'action': 'warn', 'tool': tool_name, 'category': 'ambiguous-sensitive-text'})
            inject('WARNING: The command contains sensitive-looking text. '
                   'Confirm that it does not expose credentials.')

    sys.exit(0)


if __name__ == '__main__':
    run(HOOK_NAME, main)
