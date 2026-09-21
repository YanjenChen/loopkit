"""Shared helpers for the loopkit hook scripts.

The hooks were ported from a Node.js implementation; the js_* helpers keep the
JavaScript semantics they relied on (truthiness, path.basename, path.join,
process.cwd, String.trim, regex \\s and .).

Every loopkit hook acts only inside a loopkit run session (see run_context);
in any other session it exits without output.
Standard library only; compatible with Python 3.8+.
"""

import datetime
import hashlib
import json
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# JavaScript-compatibility helpers
# ---------------------------------------------------------------------------

# ECMAScript WhiteSpace + LineTerminator: what String.prototype.trim() removes
# and what the regex class \s matches.
_JS_WHITESPACE_CODES = (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004,
    0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF)
_JS_WHITESPACE = ''.join(chr(code) for code in _JS_WHITESPACE_CODES)

# Regex building blocks with JavaScript semantics (Python's \s and . differ).
JS_WS_CHARS = ''.join('\\u%04x' % code for code in _JS_WHITESPACE_CODES)  # for use inside [...]
JS_WS = '[' + JS_WS_CHARS + ']'                                         # JS \s
JS_NON_WS = '[^' + JS_WS_CHARS + ']'                                    # JS \S
JS_DOT = '[^\\n\\r\\u2028\\u2029]'                                        # JS . (no s flag)


def js_trim(text):
    return text.strip(_JS_WHITESPACE)


def js_truthy(value):
    """JavaScript truthiness: {} and [] are truthy, unlike in Python."""
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value != 0 and value == value  # NaN is falsy
    if isinstance(value, str):
        return value != ''
    return True


def js_str(value):
    """String conversion as done by JavaScript string concatenation."""
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, float):
        if value != value:
            return 'NaN'
        if value in (float('inf'), float('-inf')):
            return 'Infinity' if value > 0 else '-Infinity'
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    if isinstance(value, (int, str)):
        return str(value)
    if isinstance(value, list):
        return ','.join('' if item is None else js_str(item) for item in value)
    return '[object Object]'


def prop(obj, key):
    """Property access that yields None (undefined) for non-objects."""
    return obj.get(key) if isinstance(obj, dict) else None


def js_basename(path):
    """POSIX path.basename: trailing slashes are ignored."""
    stripped = path.rstrip('/')
    if not stripped:
        return ''
    return stripped[stripped.rfind('/') + 1:]


def js_join(*parts):
    """path.join: joins and normalizes ('..' and '.' segments are resolved)."""
    return os.path.normpath(os.path.join(*parts))


def js_cwd():
    """process.cwd(): the working directory decoded as UTF-8 with U+FFFD for bad bytes."""
    return os.getcwdb().decode('utf-8', errors='replace')


def js_tmpdir():
    """os.tmpdir() on POSIX: TMPDIR, then TMP, then TEMP, then /tmp."""
    path = os.environ.get('TMPDIR') or os.environ.get('TMP') or os.environ.get('TEMP') or '/tmp'
    if len(path) > 1 and path.endswith('/'):
        path = path[:-1]
    return path


def js_homedir():
    """os.homedir(), except that an empty HOME falls back to the passwd entry.

    Node would return '' there and write logs into the project directory; the
    fallback deliberately keeps the project tree clean instead.
    """
    home = os.environ.get('HOME')
    if home:
        return home
    import pwd  # POSIX only; imported lazily so the module loads everywhere
    return pwd.getpwuid(os.getuid()).pw_dir


def js_relative(from_path, to_path):
    """path.relative: '' (not '.') when both paths are the same."""
    rel = os.path.relpath(to_path, from_path)
    return '' if rel == '.' else rel


def sorted_listdir(path):
    """fs.readdirSync: libuv returns directory entries sorted by name."""
    return sorted(os.listdir(path))


def now_ms():
    return time.time() * 1000


def iso_now():
    """new Date().toISOString()"""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (now.microsecond // 1000)


def _reject_constant(name):
    # JSON.parse rejects NaN / Infinity; so do we.
    raise ValueError('invalid JSON constant: ' + name)


def parse_json(text):
    return json.loads(text, parse_constant=_reject_constant)


_LONE_SURROGATE = re.compile('[\ud800-\udfff]')


def _dumps(obj, **kwargs):
    """JSON.stringify: raw non-ASCII, lone surrogates escaped as \\uXXXX."""
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    text = _LONE_SURROGATE.sub(lambda match: '\\u%04x' % ord(match.group()), text)
    return text.encode('utf-8')


def _to_utf8(text):
    """Encode like Node writes strings: lone surrogates become U+FFFD."""
    return _LONE_SURROGATE.sub(chr(0xFFFD), text).encode('utf-8')


def md5_prefix(text):
    return hashlib.md5(_to_utf8(text)).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Hook plumbing
# ---------------------------------------------------------------------------

def is_enabled(hook_name):
    env_key = 'LOOPKIT_DISABLE_' + hook_name.replace('-', '_').upper()
    return not os.environ.get(env_key)


def safe_parse_stdin(hook_name):
    try:
        data = sys.stdin.buffer.read().decode('utf-8', errors='replace')
        return parse_json(data)
    except Exception:
        remediation = 'Retry the action or inspect the hook installation.'
        log(hook_name or 'unknown', {
            'action': 'fail-open',
            'category': 'invalid-input',
            'remediation': remediation,
        })
        output({
            'systemMessage': 'loopkit %s guardrail unavailable: invalid input. %s'
                             % (hook_name or 'unknown', remediation)
        })
        return None


_SAFE_LOG_KEYS = ('action', 'tool', 'loc', 'duration', 'iterations', 'category', 'remediation')


def log(hook_name, entry):
    try:
        # Runtime logs live under the user's global ~/.claude and contain only
        # bounded metadata. Raw paths, commands, tool inputs, and secrets are
        # intentionally excluded.
        project_key = md5_prefix(js_cwd())
        log_dir = js_join(js_homedir(), '.claude', 'hooks', '.logs', project_key)
        os.makedirs(log_dir, exist_ok=True)
        record = {'ts': iso_now(), 'hook': hook_name}
        for key in _SAFE_LOG_KEYS:
            if entry and key in entry:
                record[key] = entry[key]
        with open(js_join(log_dir, 'hook-log.jsonl'), 'ab') as handle:
            handle.write(_dumps(record, separators=(',', ':')) + b'\n')
    except Exception:
        pass  # fail-open


# ---------------------------------------------------------------------------
# Shell command parsing
# ---------------------------------------------------------------------------

_REMOTE_OPERAND = re.compile('^[^/:' + JS_WS_CHARS + ']+:')
_WINDOWS_DRIVE = re.compile(r'^[a-zA-Z]:[\\/]')


def is_remote_operand(token):
    """host:path or user@host:path operand (scp/rsync style), not a local path."""
    if _WINDOWS_DRIVE.match(token):
        return False
    # JS: /^[^/\s:]+(?:@[^/\s:]+)?:/ — '@' is already in the class, so the
    # optional group is redundant; this linear form cannot backtrack.
    return _REMOTE_OPERAND.match(token) is not None


_HEREDOC_DELIMITER = re.compile('''(?:['"]([^'"]+)['"]|([^''' + JS_WS_CHARS + ''';|&]+))''')


def tokenize_shell(command):
    segments, substitutions, _ = tokenize_shell_checked(command)
    return segments, substitutions


def shell_is_complete(command):
    """False when the command ends inside a quote, $(...) or backticks."""
    return tokenize_shell_checked(command)[2]


def tokenize_shell_checked(command):
    complete = [True]
    segments = []
    substitutions = []
    tokens = []
    token = []  # characters of the token being built; joined on push (linear time)

    def push_token():
        if token:
            tokens.append(''.join(token))
            del token[:]

    def push_segment():
        push_token()
        if tokens:
            segments.append(list(tokens))
            del tokens[:]

    quote = ''
    line_start = True
    heredoc_delimiter = ''
    length = len(command)
    i = -1
    while True:
        i += 1
        if i >= length:
            break
        char = command[i]
        if heredoc_delimiter and line_start:
            line_end = command.find('\n', i)
            end = length if line_end == -1 else line_end
            if js_trim(command[i:end]) == heredoc_delimiter:
                heredoc_delimiter = ''
            i = length if line_end == -1 else line_end
            line_start = True
            continue
        if char == '\\' and quote != "'":
            if i + 1 < length:
                i += 1
                token.append(command[i])
            continue
        if char in ("'", '"'):
            if not quote:
                quote = char
            elif quote == char:
                quote = ''
            else:
                token.append(char)
            continue
        if char == '$' and command[i + 1:i + 2] == '(' and quote != "'":
            depth = 1
            inner = []
            inner_quote = ''
            i += 2
            while i < length and depth > 0:
                current = command[i]
                if current == '\\' and inner_quote != "'" and i + 1 < length:
                    i += 1
                    inner.append(current + command[i])
                elif current in ("'", '"'):
                    if not inner_quote:
                        inner_quote = current
                    elif inner_quote == current:
                        inner_quote = ''
                    inner.append(current)
                elif not inner_quote and current == '(':
                    depth += 1
                    inner.append(current)
                elif not inner_quote and current == ')':
                    depth -= 1
                    if depth == 0:
                        break
                    inner.append(current)
                else:
                    inner.append(current)
                i += 1
            if depth > 0:
                complete[0] = False
            i -= 1
            substitutions.append(''.join(inner))
            token.append('$()')
            continue
        if char == '`' and quote != "'":
            inner = []
            i += 1
            while i < length and command[i] != '`':
                if command[i] == '\\' and i + 1 < length:
                    inner.append(command[i] + command[i + 1])
                    i += 1
                else:
                    inner.append(command[i])
                i += 1
            if i >= length:
                complete[0] = False
            substitutions.append(''.join(inner))
            token.append('``')
            continue
        if not quote and char in ('\t', ' '):
            push_token()
            continue
        if not quote and char == '#' and not token:
            push_segment()
            line_end = command.find('\n', i)
            if line_end == -1:
                break
            i = line_end - 1
            line_start = True
            continue
        if not quote and char == '&' and command[i + 1:i + 2] == '>':
            # `&>file` and `&>>file` redirect stdout and stderr; they are not separators.
            push_token()
            redirect = '&>'
            i += 1
            if command[i + 1:i + 2] == '>':
                i += 1
                redirect += '>'
            tokens.append(redirect)
            continue
        if not quote and char in ('\n', ';', '|', '&', '(', ')'):
            push_segment()
            if char == '|' and command[i + 1:i + 2] == '|':
                i += 1
            if char == '&' and command[i + 1:i + 2] == '&':
                i += 1
            line_start = char == '\n'
            continue
        if not quote and char in ('<', '>'):
            push_token()
            redirect = char
            while command[i + 1:i + 2] == char:
                i += 1
                redirect += command[i]
            if command[i + 1:i + 2] == '&':
                # `2>&1`, `>&2`, `<&3`, `>&-` duplicate a descriptor: keep it one token.
                # `>& file` (no descriptor) sends both streams to a file.
                i += 1
                redirect += '&'
                while command[i + 1:i + 2] and command[i + 1] in '0123456789-':
                    i += 1
                    redirect += command[i]
            tokens.append(redirect)
            if redirect == '<<':
                next_index = i + 1
                while command[next_index:next_index + 1] in ('\t', ' '):
                    next_index += 1
                match = _HEREDOC_DELIMITER.match(command, next_index)
                if match:
                    heredoc_delimiter = match.group(1) or match.group(2)
            continue
        token.append(char)
        line_start = False
    push_segment()
    if quote:
        complete[0] = False
    return segments, substitutions, complete[0]


_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_SUDO_OPTIONS_WITH_VALUE = {
    '-C', '-D', '-g', '-h', '-p', '-R', '-r', '-T', '-t', '-U', '-u', '--chdir',
    '--close-from', '--group', '--host', '--prompt', '--role', '--type', '--user',
}


def unwrap_command(tokens):
    index = 0
    count = len(tokens)
    while index < count:
        if _ASSIGNMENT.match(tokens[index]):
            index += 1
            continue
        executable = js_basename(tokens[index]).lower()
        if executable in ('command', 'builtin', 'nohup', 'exec'):
            index += 1
            while index < count and tokens[index].startswith('-'):
                index += 1
            continue
        if executable == 'env':
            index += 1
            while index < count and tokens[index]:
                option = tokens[index]
                if _ASSIGNMENT.match(option):
                    index += 1
                elif option in ('-S', '--split-string'):
                    return []
                elif option in ('-u', '--unset', '-C', '--chdir'):
                    index += 2
                elif option.startswith('-'):
                    index += 1
                else:
                    break
            continue
        if executable == 'sudo':
            index += 1
            while index < count and tokens[index].startswith('-'):
                option = tokens[index]
                index += 1
                if option in _SUDO_OPTIONS_WITH_VALUE:
                    index += 1
            continue
        break
    return tokens[index:]


_SHELL_KEYWORD = re.compile(r'^(?:if|then|elif|else|fi|while|until|do|done|for|case|esac|select|time|!|\{|\})\Z')
_SHELL_COMMAND_FLAG = re.compile(r'^-[^-]*c')
_XARGS_OPTIONS_WITH_VALUE = {
    '-a', '--arg-file', '-d', '--delimiter', '-E', '-I', '--replace', '-L', '--max-lines',
    '-n', '--max-args', '-P', '--max-procs', '-s', '--max-chars',
}


def _find_index(items, predicate):
    for index, item in enumerate(items):
        if predicate(index, item):
            return index
    return -1


def shell_segments(command, depth=0):
    if depth > 8 or not isinstance(command, str):
        return []
    parsed_segments, substitutions = tokenize_shell(command)
    segments = []
    for original in parsed_segments:
        if js_basename(original[0] if original else '').lower() == 'env':
            split_index = _find_index(original, lambda _i, item: item in ('-S', '--split-string'))
            if split_index >= 0 and split_index + 1 < len(original) and original[split_index + 1]:
                segments.extend(shell_segments(original[split_index + 1], depth + 1))
                continue
        tokens = unwrap_command(original)
        if not tokens:
            continue
        while tokens and _SHELL_KEYWORD.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            continue
        segments.append(tokens)
        executable = js_basename(tokens[0]).lower()
        if executable in ('sh', 'bash', 'dash', 'zsh', 'ksh'):
            command_index = _find_index(
                tokens,
                lambda index, item: index > 0 and (_SHELL_COMMAND_FLAG.match(item) is not None or item == '--command'))
            if command_index >= 0 and command_index + 1 < len(tokens) and tokens[command_index + 1]:
                segments.extend(shell_segments(tokens[command_index + 1], depth + 1))
        if executable == 'xargs':
            command_index = 1
            while command_index < len(tokens) and tokens[command_index].startswith('-'):
                option = tokens[command_index]
                command_index += 1
                if option in _XARGS_OPTIONS_WITH_VALUE:
                    command_index += 1
            if command_index < len(tokens) and tokens[command_index]:
                segments.append(tokens[command_index:])
        if executable == 'find':
            for i in range(1, len(tokens)):
                if tokens[i] in ('-exec', '-execdir') and i + 1 < len(tokens) and tokens[i + 1]:
                    end = _find_index(tokens, lambda index, item, start=i: index > start and item in (';', '+'))
                    segments.append(tokens[i + 1:] if end == -1 else tokens[i + 1:end])
        if executable == 'eval' and len(tokens) > 1 and tokens[1]:
            segments.extend(shell_segments(' '.join(tokens[1:]), depth + 1))
    for substitution in substitutions:
        segments.extend(shell_segments(substitution, depth + 1))
    return segments


# ---------------------------------------------------------------------------
# Run context: is this session a loopkit run session?
# ---------------------------------------------------------------------------

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

_PROJECT_KEY = re.compile(r'[^a-zA-Z0-9]')


def _run_from_marker(start):
    from loopkit import paths as lk_paths
    root, marker = lk_paths.find_marker(start)
    if not marker or not marker.get('run_dir'):
        return None
    run_paths = lk_paths.RunPaths(marker['run_dir'])
    if run_paths.exists() and os.path.realpath(run_paths.agent) == os.path.realpath(root):
        return run_paths
    return None


def _run_from_project_key(key):
    """Match ~/.claude/projects/<key>/ (the session's start directory, sanitized) to a run's agent worktree."""
    from loopkit import paths as lk_paths
    for data in (lk_paths.data_root(),):
        try:
            repos = sorted_listdir(data)
        except OSError:
            continue
        for repo in repos:
            try:
                names = sorted_listdir(os.path.join(data, repo))
            except OSError:
                continue
            for name in names:
                run_paths = lk_paths.RunPaths(os.path.join(data, repo, name))
                if not run_paths.exists():
                    continue
                for path in {run_paths.agent, os.path.realpath(run_paths.agent)}:
                    if _PROJECT_KEY.sub('-', path) == key:
                        return run_paths
    return None


def run_context(stdin):
    """The RunPaths of the loopkit run this session works in, or None.

    Checked in order: CLAUDE_PROJECT_DIR, the project directory encoded in the
    transcript path, and the hook's cwd. The first two do not change when the
    agent cds elsewhere, so a `cd` cannot switch the run rules off.
    """
    try:
        project = os.environ.get('CLAUDE_PROJECT_DIR')
        if project:
            found = _run_from_marker(project)
            if found:
                return found
        transcript = prop(stdin, 'transcript_path') if js_truthy(stdin) else None
        if isinstance(transcript, str) and transcript:
            found = _run_from_project_key(os.path.basename(os.path.dirname(transcript)))
            if found:
                return found
        cwd = prop(stdin, 'cwd') if js_truthy(stdin) else None
        return _run_from_marker(cwd if isinstance(cwd, str) and cwd else js_cwd())
    except Exception:
        return None


def run_status_text(run_paths, recent=3):
    """A short status of the run for injected context."""
    from loopkit import batch as lk_batch, ledger as lk_ledger, pareto as lk_pareto, report as lk_report
    from loopkit import queue as lk_queue
    info = run_paths.info()
    config = info['config']
    records = lk_ledger.Ledger(run_paths.ledger, info['run']).records()
    evo = lk_pareto.Evolution(config, records)
    lines = ['loopkit run %s | agent worktree %s' % (info['run'], run_paths.agent)]
    current = lk_batch.current(records)
    if current:
        done = lk_batch.iterations(records, current['batch'])
        max_iters = (current['conditions'] or {}).get('max_iters')
        lines.append('batch %d: iter %d%s | stop: %s%s' % (
            current['batch'], done, '/%d' % max_iters if max_iters else '',
            lk_batch.describe(current['conditions']),
            ' | STOPPED (%s)' % current['stopped'] if current['stopped'] else ''))
    else:
        lines.append('no batch yet')
    front = ', '.join('%s %s' % (m, lk_report._values(config, evo.candidates[m])) for m in evo.front)
    lines.append('front (%d): %s' % (len(evo.front), front or 'empty'))
    agents = [r for r in lk_ledger.candidates(records) if r.get('by') == 'agent'][-recent:]
    for record in agents:
        lines.append('  %s<-%s %s %s' % (record['id'], '+'.join(record.get('parents') or []), record.get('status'),
                                         (record.get('reason') or '')[:80]))
    pending = lk_queue.pending(run_paths)
    if pending:
        lines.append('queue: %d request(s) waiting for the next iteration head' % len(pending))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Hook output
# ---------------------------------------------------------------------------

def output(obj):
    sys.stdout.buffer.write(_dumps(obj, separators=(',', ':')))
    sys.stdout.buffer.flush()


def block(reason):
    sys.stderr.buffer.write(_to_utf8(reason + '\n'))
    sys.stderr.buffer.flush()
    output({})
    sys.exit(2)


def allow(extra=None):
    output(extra if js_truthy(extra) else {})
    sys.exit(0)


def inject(text, event):
    """Add text to the model's context for a UserPromptSubmit, SessionStart or SubagentStart hook."""
    output({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}})
    sys.exit(0)


def ask_permission(reason):
    output({
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'ask',
            'permissionDecisionReason': reason,
        }
    })
    sys.exit(0)


def fail_open(hook_name, category):
    remediation = 'Retry the action or inspect the hook installation.'
    log(hook_name, {'action': 'fail-open', 'category': category, 'remediation': remediation})
    output({
        'systemMessage': 'loopkit %s guardrail unavailable: %s. %s' % (hook_name, category, remediation)
    })
    sys.exit(0)


def run(hook_name, main):
    """Run a hook body; any unexpected error fails open (SystemExit passes through)."""
    try:
        main()
    except Exception:
        fail_open(hook_name, 'internal-error')
