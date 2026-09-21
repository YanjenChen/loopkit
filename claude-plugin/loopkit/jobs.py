"""Scoring jobs: a detached worker checks a candidate out in the eval worktree and runs the score script.

Scoring can take longer than the 10-minute Bash tool limit, so it runs in a
separate process that outlives the command that started it. The foreground
command polls for a while and otherwise reports PENDING; `loopkit wait` resumes.

A job directory holds spec.json, status.json, result.json and score.txt.
"""

import fcntl
import json
import math
import numbers
import os
import signal
import subprocess
import sys
import time
import uuid

from . import config as config_mod, gitops, paths as paths_mod
from .util import LoopkitError, now_iso, read_json, write_json_atomic

MAX_RESULT_BYTES = 64 * 1024
DEFAULT_MAX_WAIT = 540
KILL_GRACE_S = 10


def max_wait_default():
    try:
        return float(os.environ.get('LOOPKIT_MAX_WAIT', DEFAULT_MAX_WAIT))
    except ValueError:
        return DEFAULT_MAX_WAIT


class Job(object):
    def __init__(self, run_paths, job_id):
        self.paths = run_paths
        self.id = job_id
        self.dir = os.path.join(run_paths.jobs, job_id)
        self.spec_path = os.path.join(self.dir, 'spec.json')
        self.status_path = os.path.join(self.dir, 'status.json')
        self.result_path = os.path.join(self.dir, 'result.json')
        self.log_path = os.path.join(self.dir, 'score.txt')
        self.raw_result_path = os.path.join(self.dir, 'score-result.json')

    def exists(self):
        return os.path.isfile(self.spec_path)

    def spec(self):
        return read_json(self.spec_path)

    def status(self):
        try:
            return read_json(self.status_path)
        except (OSError, ValueError):
            return {'state': 'unknown'}

    def result(self):
        try:
            return read_json(self.result_path)
        except (OSError, ValueError):
            return None

    def done(self):
        return os.path.isfile(self.result_path)

    def set_status(self, **fields):
        status = self.status()
        status.update(fields)
        write_json_atomic(self.status_path, status)

    def alive(self):
        pid = self.status().get('pid')
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def kill(self):
        status = self.status()
        for key in ('score_pgid', 'pid'):
            pgid = status.get(key)
            if pgid:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass


def create(run_paths, kind, **spec):
    job_id = '%s-%s-%s' % (time.strftime('%Y%m%d-%H%M%S'), kind, uuid.uuid4().hex[:6])
    job = Job(run_paths, job_id)
    os.makedirs(job.dir)
    spec['kind'] = kind
    spec['created'] = now_iso()
    write_json_atomic(job.spec_path, spec)
    write_json_atomic(job.status_path, {'state': 'created'})
    return job


def finish_without_scoring(job, reason):
    """Record an outcome without running the score script (e.g. a scope violation)."""
    write_json_atomic(job.result_path, {'outcome': 'fail', 'reason': reason, 'objectives': None,
                                        'constraints': None, 'extra': None, 'warnings': [], 'duration_s': 0})
    job.set_status(state='done', finished=now_iso())


def start(job):
    """Launch the detached worker for job."""
    bin_path = os.path.join(paths_mod.plugin_root(), 'bin', 'loopkit')
    log = open(os.path.join(job.dir, 'worker.txt'), 'ab')
    try:
        proc = subprocess.Popen(
            [sys.executable, '-I', bin_path, '_worker', job.paths.dir, job.id],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=job.paths.dir,
            start_new_session=True, close_fds=True)
    finally:
        log.close()
    job.set_status(state='started', pid=proc.pid, started=now_iso())
    return proc.pid


def wait(jobs, max_wait):
    """Wait until every job is done or max_wait seconds pass; returns True when all are done."""
    deadline = time.time() + max_wait
    while True:
        pending = [j for j in jobs if not j.done()]
        if not pending:
            return True
        for job in pending:
            # A worker that died without writing a result would otherwise hang the wait.
            if job.status().get('state') in ('started', 'running', 'waiting') and not job.alive() and not job.done():
                write_json_atomic(job.result_path, {'outcome': 'invalid', 'reason': 'invalid_result: scoring worker died',
                                                    'objectives': None, 'constraints': None, 'extra': None,
                                                    'warnings': [], 'duration_s': None})
                job.set_status(state='done', finished=now_iso())
        if time.time() >= deadline:
            return all(j.done() for j in jobs)
        time.sleep(0.5)


# -- the worker --------------------------------------------------------------

def _lock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def gpu_lock_paths(gpu):
    devices = sorted(d for d in (gpu or '').split(',') if d) or ['none']
    return [os.path.join(paths_mod.lock_dir(), 'gpu-%s.lock' % d.replace('/', '_')) for d in devices]


def score_env(info, variables):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('LOOPKIT_') and not k.startswith('GIT_') and k not in ('PYTHONPATH', 'PYTHONHOME')}
    # Bytecode caches written into a worktree would otherwise end up in candidates.
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(info['config']['score'].get('env', {}))
    env.update(variables)
    gpu = info.get('gpu')
    if gpu:
        env['CUDA_VISIBLE_DEVICES'] = gpu
    return env


def run_worker(run_dir, job_id):
    run_paths = paths_mod.RunPaths(run_dir)
    job = Job(run_paths, job_id)
    info = run_paths.info()
    spec = job.spec()
    locks = []
    try:
        job.set_status(state='waiting', pid=os.getpid())
        locks.append(_lock(os.path.join(run_paths.work, 'eval.lock')))
        for path in gpu_lock_paths(info.get('gpu')):
            locks.append(_lock(path))
        job.set_status(state='running', running=now_iso())
        # Holding the eval lock, no other git process uses the eval worktree: a lock file is stale.
        gitops.remove_stale_index_lock(run_paths.eval)
        common = info['repo']['common_dir']
        commit = spec.get('commit')
        if not commit:
            message = 'loopkit %s: evaluation snapshot\n' % info['run']
            commit = gitops.commit_tree(run_paths.eval, spec['tree'], spec.get('parent_shas', []), message)
        gitops.reset_worktree(run_paths.eval, commit, common, clean_ignored=True)
        result = score(info, run_paths, job)
    except Exception as exc:  # the job must always end with a result
        result = {'outcome': 'invalid', 'reason': 'invalid_result: %s' % str(exc).splitlines()[0][:300],
                  'objectives': None, 'constraints': None, 'extra': None, 'warnings': [], 'duration_s': None}
    finally:
        for fd in reversed(locks):
            os.close(fd)
    write_json_atomic(job.result_path, result)
    job.set_status(state='done', finished=now_iso())


def score(info, run_paths, job):
    """Run the score script in the eval worktree; returns the job result."""
    config = info['config']
    variables = {
        'LOOPKIT_RESULT': job.raw_result_path,
        'LOOPKIT_EVAL_DIR': run_paths.eval_assets,
        'LOOPKIT_BUILD_DIR': run_paths.build,
        'LOOPKIT_WORKTREE': run_paths.eval,
    }
    argv = config_mod.expand(config['score']['command'], variables)
    env = score_env(info, variables)
    timeout = config['score'].get('timeout_s', 1800)
    try:
        os.unlink(job.raw_result_path)
    except OSError:
        pass
    os.makedirs(run_paths.build, exist_ok=True)
    started = time.time()
    with open(job.log_path, 'ab') as log:
        log.write(('$ %s\n' % ' '.join(argv)).encode('utf-8'))
        log.flush()
        try:
            proc = subprocess.Popen(argv, cwd=run_paths.eval, env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            return _invalid('cannot run score command: %s' % exc, time.time() - started)
        job.set_status(score_pgid=proc.pid)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            return {'outcome': 'fail', 'reason': 'score_timeout', 'objectives': None, 'constraints': None,
                    'extra': None, 'warnings': [], 'duration_s': round(time.time() - started, 3)}
    duration = round(time.time() - started, 3)
    if code != 0:
        return _invalid('score command exited with %d' % code, duration)
    return interpret(config, job.raw_result_path, duration)


def _terminate(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=KILL_GRACE_S)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()


def _invalid(reason, duration):
    return {'outcome': 'invalid', 'reason': 'invalid_result: %s' % reason, 'objectives': None,
            'constraints': None, 'extra': None, 'warnings': [], 'duration_s': round(duration, 3) if duration else duration}


def _finite(value):
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value)


def interpret(config, result_path, duration):
    """Validate the score script's result file against the config."""
    try:
        size = os.path.getsize(result_path)
    except OSError:
        return _invalid('no result file was written', duration)
    if size > MAX_RESULT_BYTES:
        return _invalid('result file is larger than 64 KB', duration)
    try:
        with open(result_path, encoding='utf-8') as f:
            data = json.load(f, parse_constant=lambda name: float('nan'))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return _invalid('result is not valid JSON (%s)' % str(exc)[:120], duration)
    return validate_result(config, data, duration)


def validate_result(config, data, duration=None):
    if not isinstance(data, dict) or data.get('schema') != 1:
        return _invalid('result must be an object with "schema": 1', duration)
    status = data.get('status')
    if status == 'fail':
        reason = data.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            return _invalid('status "fail" needs a reason', duration)
        return {'outcome': 'fail', 'reason': ' '.join(reason.split())[:300], 'objectives': None,
                'constraints': None, 'extra': data.get('extra') if isinstance(data.get('extra'), dict) else None,
                'warnings': [], 'duration_s': duration}
    if status != 'ok':
        return _invalid('status must be "ok" or "fail"', duration)
    objectives = data.get('objectives')
    expected = [o['name'] for o in config['objectives']]
    if not isinstance(objectives, dict) or sorted(objectives) != sorted(expected):
        return _invalid('objectives must be exactly: %s' % ', '.join(expected), duration)
    for name in expected:
        if not _finite(objectives[name]):
            return _invalid('objective %s is not a finite number' % name, duration)
    constraints = data.get('constraints', {})
    if not isinstance(constraints, dict):
        return _invalid('constraints must be an object', duration)
    for item in config.get('constraints', []):
        entry = constraints.get(item['name'])
        if not isinstance(entry, dict) or not isinstance(entry.get('pass'), bool):
            return _invalid('constraint %s needs {"pass": true|false}' % item['name'], duration)
    warnings = []
    extra = data.get('extra', {})
    if not isinstance(extra, dict):
        warnings.append('extra is not an object; ignored')
        extra = {}
    declared = {e['name'] for e in config.get('extra', [])}
    missing = sorted(declared - set(extra))
    unknown = sorted(set(extra) - declared)
    if missing:
        warnings.append('extra is missing: %s' % ', '.join(missing))
    if unknown:
        warnings.append('extra has undeclared: %s' % ', '.join(unknown))
    failed = [item['name'] for item in config.get('constraints', []) if not constraints[item['name']]['pass']]
    return {'outcome': 'ok', 'reason': ('constraint:%s' % failed[0]) if failed else None,
            'objectives': {name: objectives[name] for name in expected},
            'constraints': constraints, 'extra': extra, 'warnings': warnings,
            'constraints_pass': not failed, 'duration_s': duration}


def failed_reason(result):
    """The FAILED reason for a job result, or None when the candidate can be compared."""
    if result['outcome'] != 'ok':
        return result['reason'] or 'failed'
    if not result.get('constraints_pass', True):
        return result['reason']
    return None
