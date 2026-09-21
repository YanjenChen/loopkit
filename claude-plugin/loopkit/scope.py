"""Scope check: which files a candidate changed, and whether it was allowed to."""

import posixpath
import re

from . import gitops

PROTECTED_ROOT_DIRS = ('.loopkit/', '.claude/')
PROTECTED_ROOT_FILES = ('.gitmodules',)
PROTECTED_BASENAMES = ('.gitignore', '.gitattributes')
MODE_SYMLINK = '120000'
MODE_GITLINK = '160000'
MAX_FILES = 50

_CONFLICT_START = re.compile(rb'^<{7}(?: |$)', re.M)
_CONFLICT_END = re.compile(rb'^>{7}(?: |$)', re.M)


def glob_to_regex(pattern):
    """Translate a scope glob: ** spans directories, * and ? stay within one path segment.

    A pattern without a slash matches the basename at any depth; a trailing
    slash means everything under that directory.
    """
    if pattern.endswith('/'):
        pattern += '**'
    anchored = '/' in pattern.rstrip('/')
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith('**/', i):
            out.append('(?:.*/)?')
            i += 3
        elif pattern.startswith('**', i):
            out.append('.*')
            i += 2
        elif c == '*':
            out.append('[^/]*')
            i += 1
        elif c == '?':
            out.append('[^/]')
            i += 1
        elif c == '[':
            end = pattern.find(']', i + 1)
            if end < 0:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1:end]
                if body.startswith('!'):
                    body = '^' + body[1:]
                out.append('[%s]' % body.replace('\\', '\\\\'))
                i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    body = ''.join(out).lstrip('/') if pattern.startswith('/') else ''.join(out)
    prefix = '' if anchored else '(?:.*/)?'
    return re.compile('^%s%s$' % (prefix, body), re.S)


class Scope(object):
    def __init__(self, include, exclude=()):
        self.include = [glob_to_regex(p) for p in include]
        self.exclude = [glob_to_regex(p) for p in exclude]

    def allows(self, path):
        return any(r.match(path) for r in self.include) and not any(r.match(path) for r in self.exclude)


def is_protected(path):
    if any(path == d.rstrip('/') or path.startswith(d) for d in PROTECTED_ROOT_DIRS):
        return True
    if path in PROTECTED_ROOT_FILES:
        return True
    return posixpath.basename(path) in PROTECTED_BASENAMES


def symlink_escapes(path, target):
    """True when a symlink at path points to an absolute path or outside the tree."""
    if target.startswith('/') or target.startswith('\\') or re.match(r'^[A-Za-z]:', target):
        return True
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    return resolved == '..' or resolved.startswith('../')


def check(cwd, base_tree, new_tree, scope, conflicts=(), parent_trees=()):
    """Compare new_tree against base_tree.

    conflicts: paths left conflicted by a merge; editing them is exempt from
    the scope rule, and a protected one may only resolve to a parent's version.
    Returns {'files': [...], 'violations': [(path, reason), ...]}.
    """
    conflicts = set(conflicts)
    changes = gitops.diff_tree(cwd, base_tree, new_tree)
    violations = []
    for change in changes:
        path = change['path']
        if MODE_GITLINK in (change['old_mode'], change['new_mode']):
            violations.append((path, 'submodule'))
            continue
        if is_protected(path):
            if path in conflicts and _matches_a_parent(cwd, path, change, parent_trees):
                continue
            violations.append((path, 'protected'))
            continue
        if change['new_mode'] == MODE_SYMLINK:
            target = gitops.cat_blob(cwd, change['new_sha']).decode('utf-8', 'surrogateescape')
            if symlink_escapes(path, target):
                violations.append((path, 'symlink'))
                continue
        if path not in conflicts and not scope.allows(path):
            violations.append((path, 'out_of_scope'))
    for path in sorted(conflicts):
        entry = gitops.blob_at(cwd, new_tree, path)
        if entry and entry[0] not in (MODE_GITLINK, MODE_SYMLINK):
            data = gitops.cat_blob(cwd, entry[1])
            if _CONFLICT_START.search(data) and _CONFLICT_END.search(data):
                violations.append((path, 'unresolved_conflict'))
    return {'files': [c['path'] for c in changes], 'violations': violations}


def _matches_a_parent(cwd, path, change, parent_trees):
    for tree in parent_trees:
        entry = gitops.blob_at(cwd, tree, path)
        if entry is None and set(change['new_sha']) == {'0'}:
            return True
        if entry and entry == (change['new_mode'], change['new_sha']):
            return True
    return False


def describe(violations, limit=3):
    """A one-line reason such as `scope:out_of_scope src/x.py; protected .gitignore`."""
    parts = ['%s %s' % (reason, path) for path, reason in violations[:limit]]
    if len(violations) > limit:
        parts.append('+%d more' % (len(violations) - limit))
    return 'scope: ' + '; '.join(parts)
