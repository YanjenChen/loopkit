#!/usr/bin/env python3
"""PreToolUse hook: blocks file access to directories matching .ckignore patterns.

Fails open on any error — never blocks legitimate work due to hook malfunction.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from ar_hook_utils import (  # noqa: E402
    JS_DOT, JS_WS, block, is_enabled, is_remote_operand, js_basename, js_cwd, js_join,
    js_relative, js_truthy, log, prop, run, safe_parse_stdin, shell_segments,
)
from ignore import ignore  # noqa: E402

HOOK_NAME = 'scout-block'

BASELINE_PATTERNS = [
    'node_modules/',
    '__pycache__/',
    '.git/',
    'dist/',
    'build/',
    'out/',
    'coverage/',
    '.next/',
    '.nuxt/',
    'venv/',
    '.venv/',
    'env/',
    '.terraform/',
    '.aws/',
    '.ssh/',
    '*.log',
]


def find_project_root(start_dir):
    directory = start_dir
    for _ in range(20):
        if os.path.exists(js_join(directory, '.git')):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return start_dir


def load_ckignore(project_root):
    ig = ignore()
    ig.add(BASELINE_PATTERNS)
    try:
        with open(js_join(project_root, '.ckignore'), encoding='utf-8', errors='replace') as handle:
            ig.add(handle.read())
    except Exception:
        pass  # no .ckignore — baseline only
    return ig


def relative_to_root(file_path, project_root):
    if os.path.isabs(file_path):
        abs_path = file_path
    else:
        abs_path = js_join(js_cwd(), file_path)  # path.resolve(process.cwd(), filePath)
    rel = js_relative(project_root, abs_path)
    # If the path escapes the project root, use the absolute path for matching.
    return (abs_path if rel.startswith('..') else rel).replace('\\', '/')


_SSH_LOCAL_PATH_OPTIONS = {'-i', '-F', '-E', '-S'}
_SSH_OPTIONS_WITH_VALUE = {'-B', '-b', '-c', '-D', '-e', '-I', '-J', '-L', '-l', '-m', '-O', '-p', '-Q', '-R', '-W', '-w'}
# re.ASCII keeps case-insensitive matching to ASCII letters, as in a non-unicode JS regex.
_SSH_ATTACHED_PATH = re.compile('^-[iFES]' + JS_DOT + '+')
_SSH_PATH_OPTION = re.compile(
    '^(?:IdentityFile|UserKnownHostsFile|GlobalKnownHostsFile|CertificateFile)(?:=|' + JS_WS + '+)('
    + JS_DOT + '+)\\Z', re.IGNORECASE | re.ASCII)
_SSH_ATTACHED_PATH_OPTION = re.compile(
    r'^-o(?:IdentityFile|UserKnownHostsFile|GlobalKnownHostsFile|CertificateFile)=', re.IGNORECASE | re.ASCII)
_FILE_EXTENSION = re.compile(r'\.[a-z]{1,6}\Z')


def extract_path_tokens(command):
    """Extract path-like tokens from a bash command string."""
    tokens = []
    for segment_tokens in shell_segments(command):
        executable = js_basename(segment_tokens[0] if segment_tokens else '')
        if executable in ('ssh', 'tsh'):
            i = 1
            while i < len(segment_tokens):
                token = segment_tokens[i]
                has_next = i + 1 < len(segment_tokens) and segment_tokens[i + 1]
                if token in _SSH_LOCAL_PATH_OPTIONS and has_next:
                    i += 1
                    tokens.append(segment_tokens[i])
                elif _SSH_ATTACHED_PATH.match(token):
                    tokens.append(token[2:])
                elif token == '-o' and has_next:
                    i += 1
                    match = _SSH_PATH_OPTION.match(segment_tokens[i])
                    if match:
                        tokens.append(match.group(1))
                elif _SSH_ATTACHED_PATH_OPTION.match(token):
                    tokens.append(token[token.index('=') + 1:])
                elif token in _SSH_OPTIONS_WITH_VALUE:
                    i += 1
                elif not token.startswith('-'):
                    break
                i += 1
            continue
        if executable in ('echo', 'printf'):
            continue
        candidates = segment_tokens[2:] if executable in ('grep', 'sed', 'awk') else segment_tokens
        for token in candidates:
            if not is_remote_operand(token):
                tokens.append(token)

    def looks_like_path(token):
        if not token or token.startswith('-'):
            return False
        # A token looks like a path if it contains '/' or starts with '.' or has a file extension
        return '/' in token or token.startswith('.') or _FILE_EXTENSION.search(token) is not None

    return [token for token in tokens if looks_like_path(token)]


def check_path(file_path, ig, project_root):
    if not isinstance(file_path, str) or not file_path:
        return None
    rel = relative_to_root(file_path, project_root)
    if ig.ignores(rel):
        return rel
    return None


def block_message(matched):
    return ("BLOCKED: Access to '%s' denied by .ckignore\n\n"
            'To allow, add to .ckignore: !%s' % (matched, matched))


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

    project_root = find_project_root(js_cwd())
    ig = load_ckignore(project_root)

    # Bash: check every path-like argument (build commands pass unless they name a blocked path)
    if tool_name == 'Bash':
        command = prop(tool_input, 'command')
        command = command if js_truthy(command) else ''
        for token in extract_path_tokens(command):
            matched = check_path(token, ig, project_root)
            if matched:
                log(HOOK_NAME, {'action': 'block', 'tool': tool_name, 'path': token, 'matched': matched})
                block(block_message(matched))
        sys.exit(0)

    # Structured tools: Read, Edit, Write, Glob, Grep
    if tool_name in STRUCTURED_TOOLS:
        file_path = prop(tool_input, 'file_path')
        if not js_truthy(file_path):
            file_path = prop(tool_input, 'path')
        if not js_truthy(file_path):
            file_path = ''
        matched = check_path(file_path, ig, project_root)
        if matched:
            log(HOOK_NAME, {'action': 'block', 'tool': tool_name, 'path': file_path, 'matched': matched})
            block(block_message(matched))

    sys.exit(0)


if __name__ == '__main__':
    run(HOOK_NAME, main)
