#!/usr/bin/env python3
"""PreToolUse hook: guards a loopkit run session.

Inside a run session (see hook_utils.run_context):
- destructive commands (forced push, rm -rf, hard reset, ...) are blocked;
- git may only read. Every subcommand that writes (commit, reset, checkout,
  config, update-ref, worktree, ...) is blocked wherever -C or cd points,
  because only the framework writes git state during a run;
- commands and file edits that would modify your working tree, the eval
  worktree, the run directory, the shared .git, or the agent worktree's
  .loopkit/ and .claude/ are blocked;
- commands that cannot be parsed, Bash with run_in_background, and the
  user-only loopkit commands (request-eval, promote, adopt, run, shim) are
  blocked;
- loopkit's analyst, decider and critic subagents are read-only: when the hook
  input names one of them, only read-only commands may run.
Outside a run session this hook does nothing. It fails open on internal errors.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from hook_utils import (  # noqa: E402
    block, is_enabled, js_basename, js_cwd, js_truthy, log, prop, run, run_context, safe_parse_stdin,
    shell_is_complete, shell_segments,
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


def command_label(command, rm_allowed=None):
    """Name of a destructive command in `command`, or None.

    rm_allowed(args) may accept a recursive forced removal, e.g. one confined to
    the agent worktree.
    """
    for words in shell_segments(command):
        executable = js_basename(words[0] if words else '')
        rest = words[1:]
        if executable == 'push' and any(is_force_arg(arg) for arg in rest):
            return 'forced push'
        if executable == 'rm':
            flags = ''.join(word for word in rest if word.startswith('-'))
            recursive = re.search(r'[rR]', flags) is not None or '--recursive' in flags
            forced = 'f' in flags or '--force' in flags
            if recursive and forced and not (rm_allowed and rm_allowed(rest)):
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


# -- git: read-only allowlist ---------------------------------------------------

GIT_READ_ONLY = {
    'status', 'log', 'show', 'diff', 'rev-parse', 'rev-list', 'ls-files', 'ls-tree', 'cat-file',
    'blame', 'annotate', 'grep', 'describe', 'shortlog', 'for-each-ref', 'show-ref', 'merge-base',
    'name-rev', 'diff-tree', 'diff-files', 'diff-index', 'whatchanged', 'version', 'help',
    'count-objects', 'var', 'check-ignore', 'check-attr', 'check-ref-format', 'show-branch',
    'range-diff', 'cherry', 'verify-commit', 'verify-tag',
}
_CONFIG_READ = {'--get', '--get-all', '--get-regexp', '--get-urlmatch', '--list', '-l'}
_BRANCH_LIST = {'-a', '-r', '-l', '--all', '--remotes', '--list', '-v', '-vv', '--verbose', '--show-current',
                '--no-color', '--color', '--column', '--no-column'}
# Global options that can run arbitrary code (config overrides, alternate exec path).
_GIT_CODE_OPTIONS = ('-c', '--config-env', '--exec-path')


def git_read_only(subcommand, args):
    if subcommand in GIT_READ_ONLY:
        return True
    first = args[0] if args else None
    if subcommand == 'config':
        return any(arg in _CONFIG_READ or arg.startswith('--get') for arg in args)
    if subcommand == 'branch':
        return all(arg in _BRANCH_LIST for arg in args)
    if subcommand in ('stash', 'notes'):
        return first in ('list', 'show')
    if subcommand == 'worktree':
        return first == 'list'
    if subcommand == 'remote':
        return first in (None, '-v', '--verbose', 'show', 'get-url')
    if subcommand == 'tag':
        return first in (None, '-l', '--list')
    if subcommand == 'reflog':
        return first in (None, 'show')
    if subcommand == 'submodule':
        return first in (None, 'status', 'summary')
    return False


def git_problem(words):
    for option in words[1:git_subcommand_index(words) if git_subcommand_index(words) > 0 else len(words)]:
        if any(option == name or option.startswith(name + '=') or (name == '-c' and option.startswith('-c'))
               for name in _GIT_CODE_OPTIONS):
            return 'git %s can run arbitrary code' % option.split('=')[0]
    index = git_subcommand_index(words)
    if index < 0 or index >= len(words):
        return None
    subcommand = words[index]
    if not git_read_only(subcommand, words[index + 1:]):
        return 'git %s writes git state; only the framework does git writes during a run' % subcommand
    return None


# -- paths ------------------------------------------------------------------------

READ_ONLY_TOOLS = {
    'cat', 'head', 'tail', 'less', 'more', 'ls', 'wc', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'stat',
    'file', 'du', 'df', 'diff', 'cmp', 'tree', 'uniq', 'cut', 'tr', 'nl', 'od', 'xxd', 'hexdump',
    'strings', 'md5sum', 'sha1sum', 'sha256sum', 'realpath', 'readlink', 'basename', 'dirname', 'echo',
    'printf', 'true', 'false', 'test', '[', 'pwd', 'which', 'type', 'jq', 'column', 'comm', 'paste',
    'fold', 'rev', 'tac', 'zcat', 'bzcat', 'xzcat', 'sort', 'sed', 'find', 'nvidia-smi', 'env',
}
_FIND_WRITES = {'-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fprint0', '-fprintf', '-fls'}
_USER_ONLY_LOOPKIT = {'request-eval', 'promote', 'adopt', 'run', 'shim'}


def loopkit_subcommand(args):
    """The subcommand of a loopkit invocation, skipping `--run NAME`."""
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == '--run':
            skip = True
        elif not arg.startswith('-'):
            return arg
    return None
# An absolute path at the start of a token or after = : quote ( , or whitespace, e.g. inside
# `python3 -c "open('/path')"`. A slash inside a relative path (lib/src/x) does not count.
_EMBEDDED_PATH = re.compile(r'(?:^|(?<=[=:\'"(,\s]))(?:~|/)[^\s\'",;()<>|&`=]*')
# Redirect tokens followed by a file target. Descriptor duplications (2>&1) are one token.
_REDIRECTS = ('>', '>>', '>|', '&>', '&>>', '>&')


def is_read_only(executable, args):
    if executable not in READ_ONLY_TOOLS:
        return False
    if executable == 'sed':
        return not any(arg.startswith('-i') or arg.startswith('--in-place') for arg in args)
    if executable == 'sort':
        return not any(arg == '-o' or arg.startswith('--output') for arg in args)
    if executable == 'find':
        return not any(arg in _FIND_WRITES for arg in args)
    if executable == 'env':
        return not any(not arg.startswith('-') and '=' not in arg for arg in args)
    return True


class Guard(object):
    def __init__(self, run_paths):
        info = run_paths.info()
        self.agent = os.path.realpath(run_paths.agent)
        self.inside_agent_protected = [os.path.join(self.agent, name) for name in ('.git', '.loopkit', '.claude')]
        self.protected = [os.path.realpath(p) for p in (
            info['repo']['toplevel'], run_paths.eval, info['repo']['common_dir'], run_paths.dir)]

    @staticmethod
    def _under(path, root):
        return path == root or path.startswith(root.rstrip('/') + '/')

    def is_protected(self, path, cwd):
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        path = os.path.realpath(path)
        if any(self._under(path, p) for p in self.inside_agent_protected):
            return True
        if self._under(path, self.agent):
            return False
        return any(self._under(path, root) for root in self.protected)

    def rm_inside_agent(self, args, cwd):
        """True when every rm target lies strictly inside the agent worktree and is not protected."""
        targets = [a for a in args if not a.startswith('-')]
        if not targets:
            return False
        for target in targets:
            path = os.path.expanduser(target)
            path = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
            if path == self.agent or not path.startswith(self.agent + '/'):
                return False
            if any(self._under(path, p) for p in self.inside_agent_protected):
                return False
        return True

    def path_candidates(self, token):
        """Paths a token may refer to: embedded absolute paths, or the token itself when it looks relative."""
        found = [m.group(0) for m in _EMBEDDED_PATH.finditer(token) if len(m.group(0)) > 1]
        value = token.split('=', 1)[1] if token.startswith('-') and '=' in token else token
        if value and not value.startswith(('/', '~', '-')) and ('/' in value or value.startswith('.')):
            found.append(value)
        return found

    def bash_problem(self, command, cwd):
        if not shell_is_complete(command):
            return 'the command could not be parsed; split it into simpler commands'
        for words in shell_segments(command):
            executable = js_basename(words[0])
            args = words[1:]
            for index, word in enumerate(words[:-1]):
                if word in _REDIRECTS and self.is_protected(words[index + 1], cwd):
                    return 'writes to %s, which the run must not modify' % words[index + 1]
            if executable in ('cd', 'pushd'):
                target = args[0] if args else os.path.expanduser('~')
                if target != '-':
                    target = os.path.expanduser(target)
                    cwd = os.path.normpath(target if os.path.isabs(target) else os.path.join(cwd, target))
                continue
            if executable == 'git':
                problem = git_problem(words)
                if problem:
                    return problem
                continue
            if executable == 'loopkit':
                sub = loopkit_subcommand(args)
                if sub in _USER_ONLY_LOOPKIT:
                    return 'loopkit %s is for the user only, not the run session' % sub
                continue
            if is_read_only(executable, args):
                continue
            if self.is_protected(cwd, cwd):
                return '%s would run inside %s, which the run must not modify' % (executable, cwd)
            for token in words:
                for path in self.path_candidates(token):
                    if self.is_protected(path, cwd):
                        return '%s touches %s, which the run must not modify' % (executable, path)
        return None


EDIT_TOOLS = {'Edit', 'MultiEdit', 'Write', 'NotebookEdit'}
READ_ONLY_AGENTS = ('analyst', 'analyst-web', 'decider', 'critic')
_LOOPKIT_READ = {'summary', 'show', 'lineage', 'diff', 'status', 'check', 'export'}


def is_read_only_agent(agent_type):
    if not isinstance(agent_type, str):
        return False
    plugin, _, name = agent_type.rpartition(':')
    return plugin == 'loopkit' and name in READ_ONLY_AGENTS


def read_only_problem(command):
    """Why a read-only subagent may not run command, or None."""
    for words in shell_segments(command):
        executable = js_basename(words[0])
        args = words[1:]
        for index, word in enumerate(words[:-1]):
            if word in _REDIRECTS and words[index + 1] != '/dev/null':
                return 'a read-only subagent cannot redirect output to %s' % words[index + 1]
        if executable in ('cd', 'pushd', 'popd'):
            continue
        if executable == 'git':
            problem = git_problem(words)
            if problem:
                return problem
            continue
        if executable == 'loopkit':
            sub = loopkit_subcommand(args)
            if sub not in _LOOPKIT_READ or '--ack' in args:
                return 'loopkit %s is not a read-only command' % sub
            continue
        if not is_read_only(executable, args):
            return '%s is not a read-only command; analysts, the decider and the critic only read' % executable
    return None


def main():
    if not is_enabled(HOOK_NAME):
        sys.exit(0)

    stdin = safe_parse_stdin(HOOK_NAME)
    if not js_truthy(stdin):
        sys.exit(0)
    run_paths = run_context(stdin)
    if run_paths is None:
        sys.exit(0)

    tool_name = prop(stdin, 'tool_name')
    tool_input = prop(stdin, 'tool_input')
    if not js_truthy(tool_input):
        sys.exit(0)
    cwd = prop(stdin, 'cwd')
    cwd = cwd if isinstance(cwd, str) and cwd else js_cwd()
    guard = Guard(run_paths)
    hint = ' Work inside the agent worktree %s; use loopkit commands for git and run data.' % guard.agent

    read_only_agent = is_read_only_agent(prop(stdin, 'agent_type'))
    if tool_name in EDIT_TOOLS:
        if read_only_agent:
            block('BLOCKED (loopkit run): %s is read-only and cannot use %s.' % (prop(stdin, 'agent_type'), tool_name))
        path = prop(tool_input, 'file_path') or prop(tool_input, 'notebook_path')
        if isinstance(path, str) and path and guard.is_protected(path, cwd):
            log(HOOK_NAME, {'action': 'block', 'tool': tool_name, 'category': 'protected-path'})
            block('BLOCKED (loopkit run): %s must not edit %s.%s' % (tool_name, path, hint))
        sys.exit(0)

    if tool_name != 'Bash':
        sys.exit(0)
    if prop(tool_input, 'run_in_background'):
        log(HOOK_NAME, {'action': 'block', 'tool': tool_name, 'category': 'background'})
        block('BLOCKED (loopkit run): run_in_background is not allowed in a run session; a turn must not '
              'end with background work. Long scoring already runs detached: use loopkit evaluate / wait.')
    command = prop(tool_input, 'command')
    command = command if isinstance(command, str) else ''
    label = command_label(command, lambda args: guard.rm_inside_agent(args, cwd))
    if label:
        log(HOOK_NAME, {'action': 'block', 'matched': label})
        block('BLOCKED (loopkit run): destructive command (%s).%s' % (label, hint))
    problem = guard.bash_problem(command, cwd)
    if not problem and read_only_agent:
        problem = read_only_problem(command)
    if problem:
        log(HOOK_NAME, {'action': 'block', 'category': 'run-guard'})
        block('BLOCKED (loopkit run): %s.%s' % (problem, hint))
    sys.exit(0)


if __name__ == '__main__':
    run(HOOK_NAME, main)
