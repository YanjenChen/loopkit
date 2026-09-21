"""The request queue: human evaluation requests and promote requests.

Requests are written by the user (request-eval, promote) and consumed by the
run at the head of the next iteration. Each file is written atomically. An
eval request pins its commit with refs/evolve/<run>/req/<n> so gc cannot drop
it; the ref is created before the file becomes visible.
"""

import os
import re

from . import gitops, ledger as ledger_mod
from .util import LoopkitError, now_iso, read_json, write_json_atomic

_FILE = re.compile(r'^req-(\d{4,})\.json$')
MAX_EVALS_PER_ITER = 2


def req_ref(run_name, number):
    return 'refs/evolve/%s/req/%d' % (run_name, number)


def _files(directory):
    found = []
    try:
        names = os.listdir(directory)
    except OSError:
        return found
    for name in names:
        match = _FILE.match(name)
        if match:
            found.append((int(match.group(1)), os.path.join(directory, name)))
    found.sort()
    return found


def pending(paths):
    """Pending requests, oldest first: list of dicts with 'n' and 'path' added."""
    requests = []
    for number, path in _files(paths.queue):
        try:
            data = read_json(path)
        except (OSError, ValueError):
            data = {'kind': 'invalid'}
        data['n'] = number
        data['path'] = path
        requests.append(data)
    return requests


def done_numbers(paths):
    return {number for number, _ in _files(paths.queue_done)}


def _next_number(paths):
    numbers = [n for n, _ in _files(paths.queue)] + list(done_numbers(paths))
    return (max(numbers) + 1) if numbers else 1


def _write(paths, number, data):
    os.makedirs(paths.queue, exist_ok=True)
    write_json_atomic(os.path.join(paths.queue, 'req-%04d.json' % number), data)


def request_eval(paths, repo_cwd, commit, note=None):
    """Queue a human commit for evaluation. Rejects duplicates immediately."""
    info = paths.info()
    sha = gitops.resolve_commit(repo_cwd, commit)
    records = ledger_mod.Ledger(paths.ledger, paths.name).records()
    for record in ledger_mod.candidates(records):
        if record.get('sha') == sha or (record.get('origin') or {}).get('sha') == sha:
            raise LoopkitError('%s was already evaluated as %s' % (sha[:7], record['id']))
    for request in pending(paths):
        if request.get('kind') == 'eval' and request.get('sha') == sha:
            raise LoopkitError('%s is already queued as request %d' % (sha[:7], request['n']))
    ref = gitops.ref_containing(repo_cwd, sha)
    number = _next_number(paths)
    gitops.create_ref(repo_cwd, req_ref(info['run'], number), sha)
    data = {'kind': 'eval', 'sha': sha, 'ref': ref, 'note': note or '', 'requested': now_iso()}
    _write(paths, number, data)
    data['n'] = number
    return data


def request_promote(paths, candidate_id):
    records = ledger_mod.Ledger(paths.ledger, paths.name).records()
    record = ledger_mod.candidate_map(records).get(candidate_id)
    if not record or record.get('by') != 'human':
        raise LoopkitError('%s is not a human candidate of this run' % candidate_id)
    if candidate_id in ledger_mod.promoted_ids(records):
        raise LoopkitError('%s is already promoted' % candidate_id)
    if record.get('status') != 'OBSERVED':
        raise LoopkitError('%s is %s; only OBSERVED candidates can be promoted' % (candidate_id, record.get('status')))
    for request in pending(paths):
        if request.get('kind') == 'promote' and request.get('id') == candidate_id:
            raise LoopkitError('%s is already queued for promotion (request %d)' % (candidate_id, request['n']))
    number = _next_number(paths)
    data = {'kind': 'promote', 'id': candidate_id, 'requested': now_iso()}
    _write(paths, number, data)
    data['n'] = number
    return data


def mark_done(paths, request):
    os.makedirs(paths.queue_done, exist_ok=True)
    os.replace(request['path'], os.path.join(paths.queue_done, os.path.basename(request['path'])))
