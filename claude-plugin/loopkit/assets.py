"""The eval-assets snapshot: score script and benchmarks, frozen when the run is created."""

import os
import shutil
import stat

from . import gitops
from .util import LoopkitError, sha256_bytes, sha256_file, write_atomic

MANIFEST_NAME = 'MANIFEST.sha256'
ABS_PREFIX = '_abs'


def _dest_for(asset):
    if os.path.isabs(asset):
        return os.path.join(ABS_PREFIX, os.path.normpath(asset).lstrip('/'))
    return os.path.normpath(asset)


def _write_blob(repo, entry, dest_root, rel_path, warnings):
    mode, kind, sha, _ = entry
    target = os.path.join(dest_root, rel_path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if kind == 'commit':
        warnings.append('%s is a submodule; its content is not snapshotted' % rel_path)
        return
    data = gitops.cat_blob(repo, sha)
    if mode == '120000':
        os.symlink(data.decode('utf-8', 'surrogateescape'), target)
        return
    with open(target, 'wb') as f:
        f.write(data)
    os.chmod(target, 0o755 if mode == '100755' else 0o644)


def missing(repo, assets):
    """Assets that are neither tracked at HEAD nor present on disk."""
    tracked = {e[3] for e in gitops.ls_tree(repo, 'HEAD')} if gitops.run(
        ['rev-parse', '--verify', '--quiet', 'HEAD'], repo, check=False).returncode == 0 else set()
    result = []
    for asset in assets:
        if os.path.isabs(asset):
            if not os.path.lexists(asset):
                result.append(asset)
            continue
        norm = os.path.normpath(asset).replace(os.sep, '/').strip('/')
        if os.path.lexists(os.path.join(repo, norm)):
            continue
        if norm in tracked or any(path.startswith(norm + '/') for path in tracked):
            continue
        result.append(asset)
    return result


def _copy_untracked(repo, norm, tracked_paths, dest):
    """Copy files under repo/norm that git does not track (e.g. ignored benchmarks); returns the count."""
    root = os.path.join(repo, norm)
    copied = 0
    if not os.path.isdir(root) or os.path.islink(root):
        return 0
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != '.git']
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(directory, d))]:
            full = os.path.join(directory, name)
            rel = os.path.relpath(full, repo).replace(os.sep, '/')
            if rel in tracked_paths:
                continue
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target, follow_symlinks=False)
            copied += 1
    return copied


def snapshot(repo, commit, assets, dest):
    """Copy each asset into dest: tracked paths from `commit`, others from disk.

    Returns (sources, warnings); sources records where every asset came from.
    """
    os.makedirs(dest, exist_ok=True)
    tree_entries = gitops.ls_tree(repo, commit)
    sources = []
    warnings = []
    for asset in assets:
        rel_dest = _dest_for(asset)
        if not os.path.isabs(asset):
            norm = os.path.normpath(asset).replace(os.sep, '/').strip('/')
            prefix = norm + '/'
            matched = [e for e in tree_entries if e[3] == norm or e[3].startswith(prefix)]
            if matched:
                for entry in matched:
                    _write_blob(repo, entry, dest, entry[3], warnings)
                # A tracked directory can also hold untracked or ignored files (large benchmarks).
                untracked = _copy_untracked(repo, norm, {e[3] for e in matched}, dest)
                if untracked:
                    warnings.append('%d untracked file(s) under %s were copied from your working tree' % (untracked, asset))
                sources.append({'asset': asset, 'from': 'git', 'commit': commit, 'files': len(matched),
                                'untracked_files': untracked})
                continue
            source = os.path.join(repo, asset)
            origin = 'worktree'
        else:
            source = asset
            origin = 'absolute'
        if not os.path.lexists(source):
            raise LoopkitError('eval asset %r does not exist (looked for %s)' % (asset, source))
        target = os.path.join(dest, rel_dest)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(source) and not os.path.islink(source):
            shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
        sources.append({'asset': asset, 'from': origin, 'path': os.path.abspath(source), 'dest': rel_dest})
    manifest = compute_manifest(dest)
    write_atomic(os.path.join(dest, MANIFEST_NAME), manifest)
    make_read_only(dest)
    return sources, warnings


def compute_manifest(root):
    """`<sha256>  <path>` for every file under root (symlinks hashed by target), sorted by path."""
    lines = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(directory, d))]:
            full = os.path.join(directory, name)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            if rel == MANIFEST_NAME:
                continue
            if os.path.islink(full):
                digest = sha256_bytes(('symlink:' + os.readlink(full)).encode('utf-8', 'surrogateescape'))
            else:
                digest = sha256_file(full)
            lines.append('%s  %s' % (digest, rel))
    lines.sort(key=lambda line: line[66:])
    return '\n'.join(lines) + ('\n' if lines else '')


def verify(root, expected_manifest_sha):
    """Problems with the snapshot: manifest file changed, or files differ from it."""
    problems = []
    manifest_path = os.path.join(root, MANIFEST_NAME)
    try:
        with open(manifest_path, 'rb') as f:
            recorded = f.read()
    except OSError:
        return ['eval-assets manifest is missing']
    if sha256_bytes(recorded) != expected_manifest_sha:
        problems.append('eval-assets manifest was modified')
    current = compute_manifest(root).encode('utf-8')
    if current != recorded:
        old = dict(_parse(recorded))
        new = dict(_parse(current))
        changed = sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))
        shown = ', '.join(changed[:5]) + (' (+%d more)' % (len(changed) - 5) if len(changed) > 5 else '')
        problems.append('eval-assets changed: %s' % shown)
    return problems


def _parse(manifest_bytes):
    for line in manifest_bytes.decode('utf-8', 'surrogateescape').splitlines():
        digest, _, path = line.partition('  ')
        yield path, digest


def make_read_only(root):
    for directory, dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(directory, name)
            if not os.path.islink(full):
                mode = os.stat(full).st_mode
                os.chmod(full, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    for directory, dirnames, filenames in os.walk(root, topdown=False):
        mode = os.stat(directory).st_mode
        os.chmod(directory, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def make_writable(root):
    for directory, dirnames, filenames in os.walk(root):
        os.chmod(directory, os.stat(directory).st_mode | stat.S_IWUSR)
        for name in filenames:
            full = os.path.join(directory, name)
            if not os.path.islink(full):
                os.chmod(full, os.stat(full).st_mode | stat.S_IWUSR)
