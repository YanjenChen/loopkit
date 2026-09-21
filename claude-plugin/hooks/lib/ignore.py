"""Minimal gitignore-spec pattern matcher (vendored, zero deps).

Supports: directory patterns (dir/), globs (*.ext), negation (!pattern),
double-star (**/) for arbitrary depth, and comments (#).
Ported 1:1 from the original ignore.cjs. Characters other than * ? . / reach
the regex verbatim there, so js_regex_to_python() reproduces how a
non-unicode JavaScript RegExp reads them (Annex B escapes, [] and [^], $, .).
"""

import re
import warnings

from ar_hook_utils import JS_DOT, JS_NON_WS, JS_WS, JS_WS_CHARS, js_trim

_HEX = '0123456789abcdefABCDEF'
_OCTAL = '01234567'
_ASCII_LETTERS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'


def _is_capturing_group(source, i):
    """source[i] == '(' opens a capturing group: plain or (?<name>...)."""
    return (source[i + 1:i + 2] != '?'
            or (source[i + 2:i + 3] == '<' and source[i + 3:i + 4] not in ('=', '!')))


def _count_capturing_groups(source):
    count = 0
    i = 0
    in_class = False
    while i < len(source):
        c = source[i]
        if c == '\\':
            i += 2
            continue
        if in_class:
            in_class = c != ']'
        elif c == '[':
            in_class = True
        elif c == '(' and _is_capturing_group(source, i):
            count += 1
        i += 1
    return count


def _legacy_octal(source, i):
    """Annex B LegacyOctalEscapeSequence starting at source[i]; returns (char, length)."""
    digits = source[i]
    limit = 3 if digits in '0123' else 2
    while len(digits) < limit and source[i + len(digits):i + len(digits) + 1] in tuple(_OCTAL):
        digits += source[i + len(digits)]
    return chr(int(digits, 8)), len(digits)


def _translate_escape(source, i, in_class, groups, closed_groups):
    """Translate the escape at source[i] == '\\'; returns (python_regex, consumed_length)."""
    if i + 1 >= len(source):
        raise re.error('\\ at end of pattern')
    c = source[i + 1]
    if c in 'dDwW':
        return '\\' + c, 2  # compiled with re.ASCII, like JS
    if c == 's':
        return (JS_WS_CHARS if in_class else JS_WS), 2
    if c == 'S':
        return ('\\S' if in_class else JS_NON_WS), 2
    if c == 'b':
        return ('\\x08' if in_class else '\\b'), 2
    if c == 'B':
        return ('B' if in_class else '\\B'), 2
    if c in 'nrtfv':
        return '\\' + c, 2
    if c == 'c':
        letter = source[i + 2:i + 3]
        if letter and letter in _ASCII_LETTERS:
            return re.escape(chr(ord(letter) % 32)), 3
        return '\\\\', 1  # "\c" without a control letter matches a literal backslash
    if c == 'x':
        digits = source[i + 2:i + 4]
        if len(digits) == 2 and all(d in _HEX for d in digits):
            return '\\x' + digits, 4
        return 'x', 2
    if c == 'u':
        digits = source[i + 2:i + 6]
        if len(digits) == 4 and all(d in _HEX for d in digits):
            return '\\u' + digits, 6
        return 'u', 2
    if c == '0' and source[i + 2:i + 3] not in tuple('0123456789'):
        return '\\x00', 2
    if c in '0123456789':
        digits = c
        while source[i + 1 + len(digits):i + 2 + len(digits)] in tuple('0123456789'):
            digits += source[i + 1 + len(digits)]
        if not in_class and c != '0' and int(digits) <= groups:
            # Backreference. JS matches the empty string for a group that has not
            # closed yet (forward or self reference); Python rejects those.
            if int(digits) in closed_groups:
                return '\\' + digits, 1 + len(digits)
            return '(?:)', 1 + len(digits)
        if c in _OCTAL:
            char, length = _legacy_octal(source, i + 1)
            return re.escape(char), 1 + length
        return c, 2  # \8 and \9 are identity escapes
    # Any other escaped character is an identity escape.
    return re.escape(c), 2


def js_regex_to_python(source):
    """Translate a non-unicode JavaScript regex source into an equivalent Python regex."""
    groups = _count_capturing_groups(source)
    opened = 0
    open_stack = []  # capturing-group number, or None, for each open parenthesis
    closed_groups = set()
    out = []
    i = 0
    in_class = False
    while i < len(source):
        c = source[i]
        if c == '\\':
            translated, consumed = _translate_escape(source, i, in_class, groups, closed_groups)
            out.append(translated)
            i += consumed
            continue
        if in_class:
            if c == ']':
                in_class = False
                out.append(']')
            elif c in '[&~|':
                out.append('\\' + c)  # literal in JS; escaped to avoid Python set-operation syntax
            else:
                out.append(c)
            i += 1
            continue
        if source.startswith('[]', i):
            out.append('(?!)')  # empty class: never matches
            i += 2
        elif source.startswith('[^]', i):
            out.append('[\\s\\S]')  # negated empty class: any character
            i += 3
        elif c == '[':
            in_class = True
            out.append('[')
            i += 1
            if source[i:i + 1] == '^':
                out.append('^')
                i += 1
        elif c == '(':
            if _is_capturing_group(source, i):
                opened += 1
                open_stack.append(opened)
            else:
                open_stack.append(None)
            out.append(c)
            i += 1
        elif c == ')':
            if open_stack and open_stack[-1] is not None:
                closed_groups.add(open_stack[-1])
            if open_stack:
                open_stack.pop()
            out.append(c)
            i += 1
        elif c == '.':
            out.append(JS_DOT)
            i += 1
        elif c == '$':
            out.append('\\Z')  # no multiline flag: end of input only
            i += 1
        else:
            out.append(c)
            i += 1
    return ''.join(out)


class Ignore(object):
    def __init__(self):
        self._rules = []

    def add(self, patterns):
        lines = patterns if isinstance(patterns, list) else patterns.split('\n')
        for raw in lines:
            line = js_trim(raw)
            if not line or line.startswith('#'):
                continue
            negated = line.startswith('!')
            pattern = line[1:] if negated else line
            self._rules.append((negated, self._compile(pattern)))
        return self

    def ignores(self, path):
        candidate = path[1:] if path.startswith('/') else path
        ignored = False
        for negated, regex in self._rules:
            if regex.search(candidate):
                ignored = not negated
        return ignored

    @staticmethod
    def _compile(pattern):
        p = pattern + '**' if pattern.endswith('/') else pattern
        regex = ''
        i = 0
        while i < len(p):
            c = p[i]
            if c == '*':
                if p[i + 1:i + 2] == '*':
                    if p[i + 2:i + 3] == '/':
                        regex += '(?:.+/)?'
                        i += 3
                        continue
                    regex += '.*'
                    i += 2
                    continue
                regex += '[^/]*'
            elif c == '?':
                regex += '[^/]'
            elif c == '.':
                regex += '\\.'
            else:
                regex += c
            i += 1
        anchored = '/' in pattern and not pattern.startswith('**/')
        source = ('^' + regex + '(?:$|/)') if anchored else ('(?:^|/)' + regex + '(?:$|/)')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')  # e.g. FutureWarning for unusual class syntax
            return re.compile(js_regex_to_python(source), re.ASCII)


def ignore():
    return Ignore()
