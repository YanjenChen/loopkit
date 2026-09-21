#!/usr/bin/env python3
"""PreToolUse hook: blocks destructive bash commands.

Regular `git push` is allowed — only force-push variants and hard-destructive ops are blocked.
Fails open on any error — never blocks legitimate work due to hook malfunction.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    block, is_enabled, js_basename, js_truthy, log, prop, run, safe_parse_stdin, shell_segments,
)

HOOK_NAME = 'dangerous-cmd-block'

_GIT_OPTIONS_WITH_VALUE = [
    '-C', '-c', '--exec-path', '--git-dir', '--work-tree', '--namespace',
    '--super-prefix', '--config-env',
]


def git_subcommand_index(words):
    index = 1
    while index < len(words):
        option = words[index]
        if option == '--':
            return index + 1
        if not option.startswith('-'):
            return index
        if option in _GIT_OPTIONS_WITH_VALUE:
            index += 2
        else:
            # -Cpath, -cname=value, and --option=value forms carry their value inline.
            index += 1
    return -1


_DELETE_FLAG = re.compile(r'^-[^-]*[dD]')
_FORCE_BRANCH_FLAG = re.compile(r'^-[^-]*[fD]')
_FORCE_CLEAN_FLAG = re.compile(r'^-[^-]*f')


def is_force_arg(arg):
    return arg == '-f' or arg.startswith('--force')


def command_label(command):
    for words in shell_segments(command):
        executable = js_basename(words[0] if words else '')
        rest = words[1:]
        if executable == 'push' and any(is_force_arg(arg) for arg in rest):
            return 'forced push'
        if executable == 'rm':
            flags = ''.join(word for word in rest if word.startswith('-'))
            recursive = re.search(r'[rR]', flags) is not None or '--recursive' in flags
            forced = 'f' in flags or '--force' in flags
            if recursive and forced:
                return 'recursive forced removal'
        if executable != 'git':
            continue
        subcommand_index = git_subcommand_index(words)
        subcommand = words[subcommand_index] if 0 <= subcommand_index < len(words) else None
        args = words[subcommand_index + 1:]
        if subcommand == 'push' and any(is_force_arg(arg) for arg in args):
            return 'forced git push'
        if subcommand == 'reset' and '--hard' in args:
            return 'hard git reset'
        if subcommand == 'clean' and any(_FORCE_CLEAN_FLAG.match(arg) or arg == '--force' for arg in args):
            return 'forced git clean'
        if subcommand == 'branch':
            flags = [arg for arg in args if arg.startswith('-')]
            has_delete = any(arg == '--delete' or _DELETE_FLAG.match(arg) for arg in flags)
            has_force = any(arg == '--force' or _FORCE_BRANCH_FLAG.match(arg) for arg in flags)
            if has_delete and has_force:
                return 'forced branch deletion'
        if subcommand in ('checkout', 'restore') and '.' in args:
            return 'git %s of working tree' % subcommand
    return None


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin) or prop(stdin, 'tool_name') != 'Bash':
        sys.exit(0)

    tool_input = prop(stdin, 'tool_input')
    command = prop(tool_input, 'command') if js_truthy(tool_input) else None
    command = command if js_truthy(command) else ''

    label = command_label(command)
    if label:
        log(HOOK_NAME, {'action': 'block', 'matched': label})
        block('BLOCKED: Destructive command detected (%s). '
              'This command is blocked during autoresearch sessions.' % label)

    sys.exit(0)


if __name__ == '__main__':
    run(HOOK_NAME, main)
