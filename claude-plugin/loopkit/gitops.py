"""Git plumbing used by the framework. The framework is the only git writer in a run.

Every call disables hooks and fsmonitor, so repository configuration cannot run
code during framework operations.
"""

import os
import shutil
import subprocess

from .util import LoopkitError

SAFE_CONFIG = ['-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false']
LOOPKIT_IDENTITY = {
    'GIT_AUTHOR_NAME': 'loopkit',
    'GIT_AUTHOR_EMAIL': 'loopkit@localhost',
    'GIT_COMMITTER_NAME': 'loopkit',
    'GIT_COMMITTER_EMAIL': 'loopkit@localhost',
}
_STRIP_ENV = (
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_COMMON_DIR',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_PREFIX', 'GIT_CONFIG_PARAMETERS',
    'GIT_CONFIG_COUNT', 'GIT_EXTERNAL_DIFF', 'GIT_DIFF_OPTS', 'GIT_EDITOR', 'GIT_SEQUENCE_EDITOR',
)
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'


class GitError(LoopkitError):
    pass


def _env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV and not k.startswith('GIT_CONFIG_KEY_') and not k.startswith('GIT_CONFIG_VALUE_')}
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['LC_ALL'] = 'C'
    if extra:
        env.update(extra)
    return env


def run(args, cwd, env=None, check=True, input_data=None, config=None):
    """Run git; returns CompletedProcess with bytes stdout/stderr."""
    argv = ['git'] + SAFE_CONFIG
    for key, value in (config or {}).items():
        argv += ['-c', '%s=%s' % (key, value)]
    argv += list(args)
    proc = subprocess.run(argv, cwd=cwd, env=_env(env), input=input_data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise GitError('git %s failed (%d): %s' % (' '.join(args[:3]), proc.returncode,
                                                   proc.stderr.decode('utf-8', 'replace').strip()[:2000]))
    return proc


def out(args, cwd, env=None, config=None):
    return run(args, cwd, env=env, config=config).stdout.decode('utf-8', 'surrogateescape').strip()


def version():
    text = subprocess.run(['git', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode()
    parts = text.split()[-1].split('.')
    numbers = []
    for part in parts[:3]:
        digits = ''.join(ch for ch in part if ch.isdigit())
        numbers.append(int(digits or 0))
    return tuple(numbers)


def toplevel(cwd):
    proc = run(['rev-parse', '--show-toplevel'], cwd, check=False)
    if proc.returncode != 0:
        raise LoopkitError('%s is not inside a git work tree' % cwd)
    return os.path.realpath(proc.stdout.decode().strip())


def common_dir(cwd):
    return os.path.realpath(out(['rev-parse', '--path-format=absolute', '--git-common-dir'], cwd))


def git_dir(cwd):
    return os.path.realpath(out(['rev-parse', '--path-format=absolute', '--git-dir'], cwd))


def resolve_commit(cwd, rev):
    proc = run(['rev-parse', '--verify', '--quiet', '%s^{commit}' % rev], cwd, check=False)
    if proc.returncode != 0:
        raise LoopkitError('%r is not a commit' % rev)
    return proc.stdout.decode().strip()


def tree_of(cwd, rev):
    return out(['rev-parse', '%s^{tree}' % rev], cwd)


def symbolic_head(cwd):
    proc = run(['symbolic-ref', '-q', 'HEAD'], cwd, check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def is_tracked(cwd, rev, path):
    proc = run(['cat-file', '-e', '%s:%s' % (rev, path)], cwd, check=False)
    return proc.returncode == 0


# -- refs ------------------------------------------------------------------

def list_refs(cwd, prefix):
    text = out(['for-each-ref', '--format=%(refname) %(objectname)', prefix], cwd)
    refs = {}
    for line in text.splitlines():
        name, _, sha = line.partition(' ')
        refs[name] = sha
    return refs


def create_ref(cwd, ref, sha):
    """Create ref; fails if it already exists."""
    run(['update-ref', '-m', 'loopkit', ref, sha, ''], cwd)


def delete_ref(cwd, ref, expected_sha=None):
    args = ['update-ref', '-d', ref]
    if expected_sha:
        args.append(expected_sha)
    run(args, cwd)


def ref_containing(cwd, sha):
    """A branch (or other ref) that contains sha, preferring the current branch."""
    head = symbolic_head(cwd)
    if head and run(['merge-base', '--is-ancestor', sha, head], cwd, check=False).returncode == 0:
        return head
    text = out(['for-each-ref', '--contains', sha, '--format=%(refname)', 'refs/heads', 'refs/remotes', 'refs/tags'], cwd)
    lines = [l for l in text.splitlines() if l]
    return lines[0] if lines else None


# -- objects ---------------------------------------------------------------

def commit_tree(cwd, tree, parents, message):
    args = ['commit-tree', tree]
    for parent in parents:
        args += ['-p', parent]
    proc = run(args, cwd, env=LOOPKIT_IDENTITY, input_data=message.encode('utf-8'))
    return proc.stdout.decode().strip()


def cat_blob(cwd, sha):
    return run(['cat-file', 'blob', sha], cwd).stdout


def ls_tree(cwd, tree):
    """Entries of a tree, recursively: list of (mode, type, sha, path)."""
    data = run(['ls-tree', '-r', '-z', '--full-tree', tree], cwd).stdout
    entries = []
    for item in data.split(b'\0'):
        if not item:
            continue
        meta, _, path = item.partition(b'\t')
        mode, kind, sha = meta.decode().split(' ')
        entries.append((mode, kind, sha, path.decode('utf-8', 'surrogateescape')))
    return entries


def blob_at(cwd, tree, path):
    """(mode, sha) of path in tree, or None."""
    data = run(['ls-tree', '-z', '--full-tree', tree, '--', path], cwd).stdout
    for item in data.split(b'\0'):
        if not item:
            continue
        meta, _, name = item.partition(b'\t')
        if name.decode('utf-8', 'surrogateescape') == path:
            mode, _, sha = meta.decode().split(' ')
            return mode, sha
    return None


def diff_tree(cwd, old, new):
    """Changed entries between two trees: list of dicts with path, status, modes and blobs."""
    data = run(['diff-tree', '-r', '-z', '--no-renames', '--no-commit-id', old, new], cwd).stdout
    parts = data.split(b'\0')
    changes = []
    index = 0
    while index < len(parts) - 1:
        meta = parts[index].decode()
        if not meta.startswith(':'):
            index += 1
            continue
        path = parts[index + 1].decode('utf-8', 'surrogateescape')
        old_mode, new_mode, old_sha, new_sha, status = meta[1:].split(' ')
        changes.append({
            'path': path, 'status': status[0],
            'old_mode': old_mode, 'new_mode': new_mode, 'old_sha': old_sha, 'new_sha': new_sha,
        })
        index += 2
    return changes


def diff_text(cwd, old, new, stat=False):
    args = ['diff', '--no-color', '--no-ext-diff']
    if stat:
        args.append('--stat')
    return run(args + [old, new], cwd).stdout.decode('utf-8', 'replace')


# -- worktrees -------------------------------------------------------------

def worktree_add(repo, path, commit, lock_reason):
    run(['worktree', 'add', '--detach', '--lock', '--reason', lock_reason, path, commit], repo)


def worktree_remove(repo, path):
    run(['worktree', 'unlock', path], repo, check=False)
    run(['worktree', 'remove', '--force', '--force', path], repo, check=False)
    run(['worktree', 'prune'], repo, check=False)


def submodules(worktree):
    """(name, path) pairs from the worktree's .gitmodules."""
    if not os.path.isfile(os.path.join(worktree, '.gitmodules')):
        return []
    proc = run(['config', '-f', '.gitmodules', '--get-regexp', r'^submodule\..*\.path$'], worktree, check=False)
    pairs = []
    for line in proc.stdout.decode('utf-8', 'replace').splitlines():
        key, _, path = line.partition(' ')
        name = key[len('submodule.'):-len('.path')]
        pairs.append((name, path))
    return pairs


def update_submodules(worktree, common):
    """Check out each submodule at the commit the index records.

    Submodules the main checkout already initialized are cloned from its
    .git/modules copy, so no network access is needed.
    """
    for name, path in submodules(worktree):
        local = os.path.join(common, 'modules', name)
        config = {}
        if os.path.isdir(local):
            config = {'submodule.%s.url' % name: local, 'protocol.file.allow': 'always'}
        run(['submodule', 'update', '--init', '--recursive', '--force', '--', path], worktree, config=config)


def reset_worktree(worktree, commit, common, keep=(), clean_ignored=False):
    """Make worktree exactly `commit`: discard edits, merge state and untracked files."""
    run(['reset', '-q', '--hard'], worktree, check=False)
    run(['checkout', '-q', '-f', '--detach', commit], worktree)
    run(['reset', '-q', '--hard', commit], worktree)
    args = ['clean', '-q', '-ffd']
    if clean_ignored:
        args.append('-x')
    for path in keep:
        args += ['-e', path]
    run(args, worktree)
    update_submodules(worktree, common)


def merge_into(worktree, other):
    """Three-way merge `other` into the worktree without committing; returns conflicted paths."""
    proc = run(['merge', '--no-commit', '--no-ff', '--no-edit', '-q', other], worktree,
               env=LOOPKIT_IDENTITY, check=False)
    conflicts = out(['diff', '--name-only', '--diff-filter=U', '-z'], worktree)
    paths = [p for p in conflicts.split('\0') if p]
    if proc.returncode != 0 and not paths:
        raise GitError('merge failed: %s' % proc.stderr.decode('utf-8', 'replace').strip()[:2000])
    return paths


def snapshot_tree(worktree, index_path, exclude=()):
    """Write the worktree's current content (tracked and untracked, not ignored) as a tree.

    A temporary index keeps the worktree's own index untouched. It is seeded
    from the worktree's index when there is one, so unchanged files are not
    rehashed.
    """
    env = {'GIT_INDEX_FILE': index_path}
    try:
        os.unlink(index_path)
    except OSError:
        pass
    real_index = os.path.join(git_dir(worktree), 'index')
    if os.path.isfile(real_index):
        shutil.copyfile(real_index, index_path)
    else:
        run(['read-tree', 'HEAD'], worktree, env=env)
    run(['add', '-A', '--', '.'], worktree, env=env)
    # Naming ignored paths in an add pathspec is an error, so drop framework files afterwards:
    # restore HEAD's version when HEAD tracks one, otherwise remove it from the index.
    head_tree = tree_of(worktree, 'HEAD')
    for path in exclude:
        entry = blob_at(worktree, head_tree, path)
        if entry:
            run(['update-index', '--add', '--cacheinfo', '%s,%s,%s' % (entry[0], entry[1], path)], worktree, env=env)
        else:
            run(['rm', '--cached', '-q', '--ignore-unmatch', '--', path], worktree, env=env)
    tree = out(['write-tree'], worktree, env=env)
    try:
        os.unlink(index_path)
    except OSError:
        pass
    return tree


def config_regexp(cwd, pattern):
    """All values of config keys matching pattern, across scopes: sorted list of 'key=value'."""
    proc = run(['config', '--get-regexp', pattern], cwd, check=False)
    items = []
    for line in proc.stdout.decode('utf-8', 'replace').splitlines():
        key, _, value = line.partition(' ')
        items.append('%s=%s' % (key.lower(), value))
    return sorted(items)


def local_config(cwd):
    proc = run(['config', '--local', '--list'], cwd, check=False)
    return proc.stdout.decode('utf-8', 'replace')
