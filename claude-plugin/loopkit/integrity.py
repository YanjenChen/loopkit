"""Tamper detection, run at the head of every iteration and before every scoring.

Problems stop the run (an `integrity` event plus LOOPKIT-STOP). Changes that the
user may have made on purpose in the shared .git (other config keys, hooks)
only produce an `integrity_warning` event.
"""

import hashlib
import os

from . import assets, gitops, ledger as ledger_mod, queue as queue_mod
from .util import sha256_file

# Config keys that can run code when the framework checks out or merges.
DANGEROUS_CONFIG = (r'^(filter\..*|merge\..*\.driver|core\.fsmonitor|core\.hookspath|'
                    r'core\.attributesfile|core\.worktree)$')


def dangerous_config(repo_cwd):
    return gitops.config_regexp(repo_cwd, DANGEROUS_CONFIG)


def local_config_hash(repo_cwd):
    return hashlib.sha256(gitops.local_config(repo_cwd).encode('utf-8')).hexdigest()


def hooks_hash(common_dir):
    digest = hashlib.sha256()
    hooks = os.path.join(common_dir, 'hooks')
    try:
        names = sorted(os.listdir(hooks))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(hooks, name)
        if name.endswith('.sample') or not os.path.isfile(path):
            continue
        digest.update(name.encode('utf-8') + b'\0' + sha256_file(path).encode() + b'\0')
    return digest.hexdigest()


def baseline(repo_cwd, common_dir):
    return {
        'dangerous_config': dangerous_config(repo_cwd),
        'local_config_sha256': local_config_hash(repo_cwd),
        'hooks_sha256': hooks_hash(common_dir),
    }


class Report(object):
    def __init__(self):
        self.problems = []
        self.warnings = {}   # what changed -> new hash
        self.fixed = []

    @property
    def ok(self):
        return not self.problems


def check(paths, info, records, ledger_problems, repo_cwd, fix=True):
    report = Report()
    report.problems.extend(ledger_problems)
    run = info['run']

    created = ledger_mod.events(records, 'run_created')
    if not created:
        report.problems.append('ledger has no run_created event')
    elif created[0].get('run_json_sha256') != sha256_file(paths.run_json):
        report.problems.append('run.json was modified')

    report.problems.extend(assets.verify(paths.eval_assets, info['eval_assets']['manifest_sha256']))

    # Candidate refs must match the ledger exactly.
    prefix = 'refs/evolve/%s/' % run
    refs = gitops.list_refs(repo_cwd, prefix)
    by_id = {r['id']: r['sha'] for r in ledger_mod.candidates(records)}
    next_ids = {ledger_mod.next_id(records, 'c'), ledger_mod.next_id(records, 'h')}
    req_refs = {}
    for name, sha in sorted(refs.items()):
        tail = name[len(prefix):]
        if tail.startswith('req/'):
            req_refs[tail[4:]] = (name, sha)
        elif tail in by_id:
            if by_id[tail] != sha:
                report.problems.append('ref %s points to %s, ledger says %s' % (tail, sha[:7], by_id[tail][:7]))
        elif tail in next_ids and fix:
            # A record step was interrupted after creating the ref: drop it.
            gitops.delete_ref(repo_cwd, name, sha)
            report.fixed.append('removed dangling ref %s' % tail)
        else:
            report.problems.append('unexpected ref %s' % name)
    for cid in sorted(by_id):
        if prefix + cid not in refs:
            report.problems.append('ref for %s is missing' % cid)

    # Eval requests and their pinning refs must match.
    requested = {c.get('request') for c in ledger_mod.candidates(records) if c.get('by') == 'human'}
    for request in queue_mod.pending(paths):
        if request.get('kind') == 'invalid':
            report.problems.append('queue file %s is unreadable' % os.path.basename(request['path']))
            continue
        if request.get('kind') != 'eval':
            continue
        key = str(request['n'])
        pinned = req_refs.pop(key, None)
        if request['n'] in requested:
            # Processed, but cleanup was interrupted: finish it.
            if fix:
                if pinned:
                    gitops.delete_ref(repo_cwd, pinned[0], pinned[1])
                queue_mod.mark_done(paths, request)
                report.fixed.append('finished cleanup of request %d' % request['n'])
        elif pinned is None:
            # request-eval writes the file, then the ref: re-pin a request caught in between.
            sha = request.get('sha') or ''
            if fix and gitops.run(['cat-file', '-e', '%s^{commit}' % sha], repo_cwd, check=False).returncode == 0:
                queue_mod.pin(repo_cwd, run, request['n'], sha)
                report.fixed.append('pinned queued request %d' % request['n'])
            else:
                report.problems.append('queued request %d has no pinning ref' % request['n'])
        elif pinned[1] != request.get('sha'):
            report.problems.append('queued request %d does not match its pinning ref' % request['n'])
    for key, (name, sha) in sorted(req_refs.items()):
        # A request's cleanup was interrupted after its file moved to done/: drop the ref.
        if fix:
            gitops.delete_ref(repo_cwd, name, sha)
            report.fixed.append('removed stale request ref %s' % key)

    # Git configuration that can execute code must not change during a run.
    recorded = info.get('git_baseline', {})
    current = dangerous_config(repo_cwd)
    if current != recorded.get('dangerous_config', []):
        added = sorted(set(current) - set(recorded.get('dangerous_config', [])))
        removed = sorted(set(recorded.get('dangerous_config', [])) - set(current))
        detail = ', '.join(['+' + k.split('=')[0] for k in added] + ['-' + k.split('=')[0] for k in removed])
        report.problems.append('git config that can run code changed: %s' % detail)

    last_warning = {}
    for event in ledger_mod.events(records, 'integrity_warning'):
        last_warning.update(event.get('hashes') or {})
    common = gitops.common_dir(repo_cwd)
    for key, value in (('local_config_sha256', local_config_hash(repo_cwd)),
                       ('hooks_sha256', hooks_hash(common))):
        known = last_warning.get(key, recorded.get(key))
        if value != known:
            report.warnings[key] = value
    return report
