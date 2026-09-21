"""Run-level operations behind the CLI: create, head, checkout, evaluate, record, queue, reports."""

import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import (assets, batch as batch_mod, config as config_mod, gitops, integrity, jobs, ledger as ledger_mod,
               pareto, paths as paths_mod, queue as queue_mod, report, scope as scope_mod)
from .util import LoopkitError, now_iso, read_json, sha256_file, truncate, write_json_atomic

MIN_GIT = (2, 31)
RUN_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$')
LEARNED_MAX = 300
STOP = 'LOOPKIT-STOP'


def say(text=''):
    print(text, flush=True)


def stop_line(reason):
    say('%s | %s' % (STOP, reason))


# ---------------------------------------------------------------------------
# Creating a run
# ---------------------------------------------------------------------------

def _next_run_name(common):
    names = {p.name for p in paths_mod.list_runs(common)}
    number = 1
    while 'r%03d' % number in names:
        number += 1
    return 'r%03d' % number


def _deny_rules(repo_top, run_paths, common):
    def rule(path, tree=True):
        real = os.path.realpath(path)
        return 'Edit(/%s%s)' % (real, '/**' if tree else '')
    rules = [rule(repo_top), rule(run_paths.eval), rule(common)]
    for name in ('run.json', 'ledger.jsonl', 'ledger.jsonl.head', 'knowledge.md'):
        rules.append(rule(os.path.join(run_paths.dir, name), tree=False))
    for path in (run_paths.queue, run_paths.eval_assets, run_paths.build, run_paths.agent_build,
                 run_paths.artifacts, run_paths.work):
        rules.append(rule(path))
    rules.append(rule(os.path.join(run_paths.agent, '.loopkit')))
    rules.append(rule(os.path.join(run_paths.agent, '.claude')))
    return rules


def _commit_loopkit_dir(repo, name):
    """Commit .loopkit/ on the current branch if it changed; that commit becomes c000."""
    status = gitops.out(['status', '--porcelain', '--', '.loopkit'], repo)
    if not status:
        return False
    gitops.run(['add', '--', '.loopkit'], repo)
    env = None
    if gitops.run(['var', 'GIT_COMMITTER_IDENT'], repo, check=False).returncode != 0:
        env = gitops.LOOPKIT_IDENTITY
    gitops.run(['commit', '-q', '-m', 'loopkit: configure run %s' % name, '--', '.loopkit'], repo, env=env)
    return True


def create(cwd, name=None, gpu=None, knowledge=None, monitor_url=None, allow_danger=False, max_wait=None):
    if sys.version_info < (3, 8):
        raise LoopkitError('Python 3.8 or newer is required')
    if gitops.version() < MIN_GIT:
        raise LoopkitError('git %d.%d or newer is required' % MIN_GIT)
    repo = gitops.toplevel(cwd)
    common = gitops.common_dir(repo)
    config = config_mod.load(os.path.join(repo, config_mod.CONFIG_PATH))
    danger = config_mod.screen(config['score']['command'])
    if config.get('precheck'):
        danger += config_mod.screen(config['precheck']['command'])
    if danger and not allow_danger:
        raise LoopkitError('the score or precheck command looks dangerous (%s); confirm with the user and pass '
                           '--allow-danger' % '; '.join(danger))
    for path in paths_mod.FRAMEWORK_FILES:
        if gitops.is_tracked(repo, 'HEAD', path):
            raise LoopkitError('%s is tracked in this repository; loopkit needs it to be untracked' % path)
    name = name or _next_run_name(common)
    if not RUN_NAME.match(name) or name.endswith('.lock') or '..' in name:
        raise LoopkitError('run name %r must match %s' % (name, RUN_NAME.pattern))
    run_paths = paths_mod.RunPaths(os.path.join(paths_mod.repo_dir(common), name))
    if os.path.exists(run_paths.dir):
        raise LoopkitError('run %s already exists at %s' % (name, run_paths.dir))
    if gitops.list_refs(repo, 'refs/evolve/%s/' % name):
        raise LoopkitError('refs/evolve/%s/ already has refs; pick another run name' % name)

    committed = _commit_loopkit_dir(repo, name)
    c000 = gitops.resolve_commit(repo, 'HEAD')
    branch = gitops.symbolic_head(repo)
    gpu = gpu if gpu is not None else (config.get('run') or {}).get('gpu')

    os.makedirs(run_paths.dir)
    for directory in (run_paths.queue, run_paths.build, run_paths.agent_build, run_paths.artifacts, run_paths.jobs):
        os.makedirs(directory, exist_ok=True)
    reason = 'loopkit run %s' % name
    gitops.worktree_add(repo, run_paths.agent, c000, reason)
    gitops.worktree_add(repo, run_paths.eval, c000, reason)
    gitops.update_submodules(run_paths.agent, common)
    gitops.update_submodules(run_paths.eval, common)
    sources, warnings = assets.snapshot(repo, c000, config['eval_assets'], run_paths.eval_assets)
    if knowledge:
        shutil.copyfile(knowledge, run_paths.knowledge)

    info = {
        'schema': 1,
        'run': name,
        'created': now_iso(),
        'repo': {'toplevel': repo, 'common_dir': common, 'branch': branch, 'id': paths_mod.repo_id(common)},
        'c000': c000,
        'config': config,
        'gpu': gpu,
        'paths': {'run_dir': run_paths.dir, 'agent': run_paths.agent, 'eval': run_paths.eval,
                  'build': run_paths.build, 'agent_build': run_paths.agent_build, 'eval_assets': run_paths.eval_assets},
        'eval_assets': {'manifest_sha256': sha256_file(run_paths.manifest), 'sources': sources},
        'git_baseline': integrity.baseline(repo, common),
        'monitor_url': monitor_url,
        'plugin_root': paths_mod.plugin_root(),
    }
    write_json_atomic(run_paths.run_json, info)
    os.chmod(run_paths.run_json, 0o444)
    ledger = ledger_mod.Ledger(run_paths.ledger, name)
    ledger.event('run_created', run_json_sha256=sha256_file(run_paths.run_json), c000=c000)

    settings = {
        'permissions': {'deny': _deny_rules(repo, run_paths, common)},
        'env': dict({'PYTHONDONTWRITEBYTECODE': '1'}, **({'CUDA_VISIBLE_DEVICES': gpu} if gpu else {})),
    }
    write_json_atomic(os.path.join(run_paths.agent, paths_mod.SETTINGS_LOCAL), settings)
    write_json_atomic(os.path.join(run_paths.agent, paths_mod.MARKER),
                      {'run': name, 'run_dir': run_paths.dir, 'repo_id': info['repo']['id']})

    say('RUN %s created%s' % (name, ' (committed .loopkit/ on %s)' % report.short_ref(branch) if committed else ''))
    say('  c000:        %s' % c000)
    say('  run dir:     %s' % run_paths.dir)
    say('  agent tree:  %s' % run_paths.agent)
    say('  gpu:         %s' % (gpu or 'not set'))
    for warning in warnings:
        say('WARNING: %s' % warning)
    job = jobs.create(run_paths, 'baseline', commit=c000)
    write_json_atomic(os.path.join(run_paths.work, 'baseline.json'), {'job': job.id})
    jobs.start(job)
    Run(run_paths).await_baseline(max_wait if max_wait is not None else jobs.max_wait_default())
    return run_paths


# ---------------------------------------------------------------------------
# Resolving a run
# ---------------------------------------------------------------------------

def resolve(cwd, name=None, session_only=False):
    """The run for this command: an explicit name, the run worktree we are in, or the newest run."""
    if not name:
        root, marker = paths_mod.find_marker(cwd)
        if marker and marker.get('run_dir'):
            run_paths = paths_mod.RunPaths(marker['run_dir'])
            if run_paths.exists() and os.path.realpath(run_paths.agent) == os.path.realpath(root):
                return Run(run_paths)
        if session_only:
            raise LoopkitError('not inside a loopkit run worktree (pass --run NAME)')
    common = gitops.common_dir(cwd)
    runs = paths_mod.list_runs(common)
    if name:
        for run_paths in runs:
            if run_paths.name == name:
                return Run(run_paths)
        raise LoopkitError('no run named %s for this repository' % name)
    if not runs:
        raise LoopkitError('this repository has no loopkit runs; run /loopkit:init first')
    return Run(runs[-1])


class Run(object):
    def __init__(self, run_paths):
        self.paths = run_paths
        self.info = run_paths.info()
        self.config = self.info['config']
        self.name = self.info['run']
        self.common = self.info['repo']['common_dir']
        self.git_cwd = run_paths.eval
        self.ledger = ledger_mod.Ledger(run_paths.ledger, self.name)

    # -- state -------------------------------------------------------------

    def read(self):
        return self.ledger.read()

    def records(self):
        return self.ledger.records()

    def evolution(self, records=None):
        return pareto.Evolution(self.config, records if records is not None else self.records())

    def _state(self, name):
        path = os.path.join(self.paths.work, name)
        try:
            return read_json(path)
        except (OSError, ValueError):
            return None

    def _save_state(self, name, data):
        path = os.path.join(self.paths.work, name)
        if data is None:
            try:
                os.unlink(path)
            except OSError:
                pass
        else:
            write_json_atomic(path, data)

    def checkout_state(self, required=True):
        state = self._state('checkout.json')
        if state is None and required:
            raise LoopkitError('no checkout for this iteration; run `loopkit checkout` first')
        return state

    def _require_baseline(self, records):
        c000 = ledger_mod.candidate_map(records).get('c000')
        if c000 is None:
            raise LoopkitError('c000 is still being scored; run `loopkit wait`')
        if c000.get('status') != 'BASELINE':
            raise LoopkitError('c000 failed (%s); fix the score script or config and create a new run' % c000.get('reason'))

    def _integrity_stopped(self, records):
        events = ledger_mod.events(records, 'integrity')
        if events:
            return 'integrity: run stopped at seq %d (%s)' % (events[0]['seq'], '; '.join(events[0].get('problems', []))[:300])
        return None

    def _running_batch(self, records):
        current = batch_mod.current(records)
        if current is None:
            raise LoopkitError('no batch has started; run `loopkit batch start` first')
        return current

    def integrity_gate(self):
        """Run the tamper checks; returns True when the run may continue."""
        records, problems = self.read()
        stopped = self._integrity_stopped(records)
        if stopped:
            stop_line(stopped)
            return False
        result = integrity.check(self.paths, self.info, records, problems, self.git_cwd)
        for note in result.fixed:
            say('NOTE: %s' % note)
        if result.warnings:
            self.ledger.event('integrity_warning', changed=sorted(result.warnings), hashes=result.warnings)
            say('WARNING: git %s changed in the shared repository (recorded; the run continues)' %
                ' and '.join('hooks' if k.startswith('hooks') else 'config' for k in sorted(result.warnings)))
        if result.problems:
            self.ledger.event('integrity', problems=result.problems)
            current = batch_mod.current(self.records())
            if current and not current['stopped']:
                self.ledger.event('batch_stop', batch=current['batch'], reason='integrity')
            stop_line('integrity: %s' % '; '.join(result.problems)[:500])
            return False
        return True

    # -- head: batch start ---------------------------------------------------

    def batch_start(self, text, conditions_raw):
        records = self.records()
        self._require_baseline(records)
        if not self.integrity_gate():
            return
        records = self.records()
        conditions, warnings = batch_mod.parse_conditions(conditions_raw, self.config)
        decision, current = batch_mod.decide_start(records, text, conditions)
        if decision == 'stopped':
            say('BATCH %d | already stopped (%s) | to run another batch, change the prompt text' % (
                current['batch'], current['stopped']))
            stop_line(current['stopped'])
            return
        if decision == 'start':
            number = (current['batch'] + 1) if current else 1
            self.ledger.event('batch_start', batch=number, text=text, conditions=conditions)
            records = self.records()
            current = batch_mod.current(records)
            say('BATCH %d | started | stop: %s' % (number, batch_mod.describe(conditions)))
        else:
            done = batch_mod.iterations(records, current['batch'])
            max_iters = current['conditions'].get('max_iters')
            say('BATCH %d | continues | iter %d%s done | stop: %s' % (
                current['batch'], done, '/%d' % max_iters if max_iters else '', batch_mod.describe(current['conditions'])))
            if current['conditions'] != conditions:
                say('NOTE: this prompt parses to %s; the batch keeps its registered conditions' % batch_mod.describe(conditions))
        for warning in warnings:
            say('WARNING: %s' % warning)
        leftover = self.checkout_state(required=False)
        if leftover:
            say('NOTE: an unfinished iteration from %s (parents %s) will be discarded by the next checkout' % (
                leftover.get('created'), '+'.join(leftover.get('parents', []))))
        self._check_stop(records, current)

    def _check_stop(self, records, current):
        done = batch_mod.iterations(records, current['batch'])
        reason = batch_mod.check(current['conditions'], done, self.evolution(records))
        if reason:
            self.ledger.event('batch_stop', batch=current['batch'], reason=reason)
            stop_line(reason)
        return reason

    # -- head: queue -------------------------------------------------------

    def process_queue(self, max_wait):
        records = self.records()
        self._require_baseline(records)
        current = batch_mod.current(records)
        if current is None or current['stopped']:
            say('QUEUE | not processed: no running batch')
            return
        if not self.integrity_gate():
            return
        state = self._state('queue-jobs.json') or {'jobs': []}
        active = {entry['n']: entry['job'] for entry in state['jobs']}
        pending = queue_mod.pending(self.paths)
        evals = []
        for request in pending:
            if request.get('kind') == 'promote':
                self._apply_promote(request)
            elif request.get('kind') == 'eval':
                evals.append(request)
        started = 0
        for request in evals:
            if request['n'] in active:
                continue
            if len(active) >= queue_mod.MAX_EVALS_PER_ITER:
                break
            job = jobs.create(self.paths, 'human', commit=request['sha'], request=request['n'])
            jobs.start(job)
            active[request['n']] = job.id
            started += 1
        state = {'jobs': [{'n': n, 'job': job_id} for n, job_id in sorted(active.items())]}
        self._save_state('queue-jobs.json', state)
        waiting = len(evals) - len(active)
        if not active:
            say('QUEUE | empty')
            return
        say('QUEUE | evaluating %d request(s)%s' % (len(active), ', %d more wait for later iterations' % waiting if waiting > 0 else ''))
        self._await_queue(max_wait)

    def _await_queue(self, max_wait):
        state = self._state('queue-jobs.json') or {'jobs': []}
        entries = state['jobs']
        job_objs = [jobs.Job(self.paths, e['job']) for e in entries]
        jobs.wait(job_objs, max_wait)
        remaining = []
        for entry, job in zip(entries, job_objs):
            if job.done():
                self._finalize_human(entry['n'], job)
            else:
                remaining.append(entry)
        self._save_state('queue-jobs.json', {'jobs': remaining} if remaining else None)
        if remaining:
            say('PENDING | %d human evaluation(s) still running | run: loopkit wait' % len(remaining))
        return not remaining

    def _find_request(self, number):
        for request in queue_mod.pending(self.paths):
            if request['n'] == number:
                return request
        return None

    def _finalize_human(self, number, job):
        records = self.records()
        request = self._find_request(number)
        existing = [c for c in ledger_mod.candidates(records) if c.get('request') == number]
        req_ref = queue_mod.req_ref(self.name, number)
        if existing or request is None:
            if request is not None:
                self._cleanup_request(request, req_ref)
            return
        result = job.result()
        reason = jobs.failed_reason(result)
        by_id = ledger_mod.candidate_map(records)
        c000 = by_id['c000']
        evo = self.evolution(records)
        status = 'FAILED' if reason else 'OBSERVED'
        on_front = False
        if not reason:
            on_front = evo.judge(result['objectives'])[0] == 'KEPT'
        cid = ledger_mod.next_id(records, 'h')
        ref = 'refs/evolve/%s/%s' % (self.name, cid)
        stale = gitops.list_refs(self.git_cwd, ref)
        if stale:
            gitops.delete_ref(self.git_cwd, ref, stale[ref])
        gitops.create_ref(self.git_cwd, ref, request['sha'])
        files = [c['path'] for c in gitops.diff_tree(self.git_cwd, c000['sha'], request['sha'])]
        record = self.ledger.append('candidate', {
            'id': cid, 'by': 'human', 'iter': None, 'batch': None, 'parents': [], 'sha': request['sha'],
            'origin': {'ref': request.get('ref'), 'sha': request['sha'], 'note': request.get('note', '')},
            'idea': request.get('note', ''), 'proposed_by': ['human'],
            'files': files[:scope_mod.MAX_FILES], 'files_total': len(files),
            'objectives': result.get('objectives'), 'parent_objectives': c000.get('objectives'),
            'constraints': result.get('constraints'), 'extra': result.get('extra'),
            'status': status, 'reason': reason, 'on_front': on_front,
            'duration_s': result.get('duration_s'), 'cost_usd': None, 'request': number,
            'warnings': result.get('warnings') or None,
        })
        self._store_artifacts(cid, job)
        self._cleanup_request(request, req_ref)
        say(report.human_line(self.config, record))

    def _cleanup_request(self, request, req_ref):
        refs = gitops.list_refs(self.git_cwd, req_ref)
        if req_ref in refs:
            gitops.delete_ref(self.git_cwd, req_ref, refs[req_ref])
        queue_mod.mark_done(self.paths, request)

    def _apply_promote(self, request):
        records = self.records()
        cid = request.get('id')
        record = ledger_mod.candidate_map(records).get(cid)
        problem = None
        if not record or record.get('by') != 'human':
            problem = '%s is not a human candidate' % cid
        elif record.get('status') != 'OBSERVED':
            problem = '%s is %s' % (cid, record.get('status'))
        elif cid in ledger_mod.promoted_ids(records):
            problem = '%s is already promoted' % cid
        if problem:
            self.ledger.event('request_rejected', request=request['n'], kind='promote', id=cid, reason=problem)
            say('PROMOTE %s | rejected: %s' % (cid, problem))
        else:
            verdict, detail = self.evolution(records).judge(record['objectives'])
            on_front = verdict == 'KEPT'
            self.ledger.event('promote', request=request['n'], id=cid, on_front=on_front,
                              detail=None if on_front else detail)
            say('PROMOTE %s | promoted | front:%s%s' % (cid, 'yes' if on_front else 'no', '' if on_front else ' (%s)' % detail))
        queue_mod.mark_done(self.paths, request)

    # -- head: checkout ------------------------------------------------------

    def checkout(self, first=None, second=None):
        records = self.records()
        self._require_baseline(records)
        stopped = self._integrity_stopped(records)
        if stopped:
            stop_line(stopped)
            return
        current = self._running_batch(records)
        if current['stopped']:
            stop_line(current['stopped'])
            return
        evo = self.evolution(records)
        first = first or evo.default_parent()
        chosen = [first] + ([second] if second else [])
        for cid in chosen:
            if not evo.eligible_parent(cid):
                record = evo.candidates.get(cid)
                why = 'unknown candidate' if not record else (
                    'unpromoted human candidate' if record.get('by') == 'human' and record.get('status') == 'OBSERVED'
                    else record.get('status'))
                raise LoopkitError('%s cannot be a parent (%s)' % (cid, why))
        if second and second == first:
            raise LoopkitError('the two parents must differ')
        self._discard_iteration()
        shas = [evo.candidates[c]['sha'] for c in chosen]
        gitops.reset_worktree(self.paths.agent, shas[0], self.common, keep=paths_mod.FRAMEWORK_FILES)
        conflicts = gitops.merge_into(self.paths.agent, shas[1]) if second else []
        base_tree = self._snapshot()
        state = {
            'parents': chosen, 'parent_shas': shas,
            'parent_trees': [gitops.tree_of(self.git_cwd, s) for s in shas],
            'base_tree': base_tree, 'conflicts': conflicts, 'created': now_iso(),
            'batch': current['batch'], 'precheck_attempts': 0, 'job': None, 'tree': None,
        }
        self._save_state('checkout.json', state)
        say('CHECKOUT %s%s' % ('+'.join(chosen), ' | merged into the worktree' if second else ''))
        if conflicts:
            say('CONFLICTS (resolve them while implementing): %s' % ', '.join(conflicts))
        scope = self.config['scope']
        say('SCOPE include: %s%s' % (', '.join(scope['include']),
                                    ' | exclude: %s' % ', '.join(scope.get('exclude', [])) if scope.get('exclude') else ''))

    def _snapshot(self):
        return gitops.snapshot_tree(self.paths.agent, os.path.join(self.paths.work, 'snapshot.idx'),
                                    exclude=paths_mod.FRAMEWORK_FILES)

    def _discard_iteration(self):
        state = self.checkout_state(required=False)
        if state and state.get('job'):
            job = jobs.Job(self.paths, state['job'])
            if not job.done():
                job.kill()
        self._save_state('checkout.json', None)

    # -- tail: precheck --------------------------------------------------------

    def precheck(self):
        state = self.checkout_state()
        precheck = self.config.get('precheck')
        if not precheck:
            say('PRECHECK | skipped: no precheck command configured')
            return
        state['precheck_attempts'] = state.get('precheck_attempts', 0) + 1
        self._save_state('checkout.json', state)
        limit = precheck.get('max_fix_attempts', 3)
        variables = {'LOOPKIT_AGENT_BUILD_DIR': self.paths.agent_build, 'LOOPKIT_WORKTREE': self.paths.agent}
        argv = config_mod.expand(precheck['command'], variables)
        env = jobs.score_env(self.info, variables)
        os.makedirs(self.paths.agent_build, exist_ok=True)
        log_path = os.path.join(self.paths.work, 'precheck.txt')
        started = time.time()
        with open(log_path, 'wb') as log:
            try:
                proc = subprocess.Popen(argv, cwd=self.paths.agent, env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as exc:
                raise LoopkitError('cannot run precheck command: %s' % exc)
            try:
                code = proc.wait(timeout=precheck.get('timeout_s', 540))
            except subprocess.TimeoutExpired:
                jobs._terminate(proc)
                code = 'timeout'
        with open(log_path, encoding='utf-8', errors='replace') as f:
            tail = f.readlines()[-120:]
        if tail:
            say(''.join(tail).rstrip('\n'))
        ok = code == 0
        say('PRECHECK %s%s | %.1fs | attempt %d/%d | log: %s' % (
            'ok' if ok else 'failed', '' if ok else ' (exit %s)' % code, time.time() - started,
            state['precheck_attempts'], limit, log_path))
        if not ok and state['precheck_attempts'] >= limit:
            say('NOTE: max_fix_attempts reached; run `loopkit evaluate` anyway so the iteration is recorded')

    # -- tail: evaluate ------------------------------------------------------

    def evaluate(self, max_wait):
        state = self.checkout_state()
        records = self.records()
        current = self._running_batch(records)
        if current['stopped']:
            stop_line(current['stopped'])
            return
        if not self.integrity_gate():
            return
        tree = self._snapshot()
        if tree == state['base_tree'] and len(state['parents']) == 1:
            raise LoopkitError('nothing changed since `loopkit checkout`; implement the idea first')
        if state.get('job'):
            old = jobs.Job(self.paths, state['job'])
            if not old.done():
                old.kill()
        allowed = scope_mod.Scope(self.config['scope']['include'], self.config['scope'].get('exclude', []))
        check = scope_mod.check(self.git_cwd, state['base_tree'], tree, allowed,
                                state.get('conflicts', []), state.get('parent_trees', []))
        job = jobs.create(self.paths, 'agent', tree=tree, parent_shas=state['parent_shas'], parents=state['parents'])
        if check['violations']:
            jobs.finish_without_scoring(job, scope_mod.describe(check['violations']))
        else:
            jobs.start(job)
        state.update(job=job.id, tree=tree, files=check['files'])
        self._save_state('checkout.json', state)
        say(self._changed_line(state['base_tree'], tree))
        self._await_agent_job(job, max_wait)

    def _changed_line(self, base_tree, tree, limit=12):
        changes = gitops.diff_tree(self.git_cwd, base_tree, tree)
        marks = {'A': '+', 'D': '-'}
        shown = ['%s%s' % (marks.get(c['status'], ''), c['path']) for c in changes[:limit]]
        more = ' (+%d more)' % (len(changes) - limit) if len(changes) > limit else ''
        return 'CHANGED (%d): %s%s' % (len(changes), ', '.join(shown) or 'nothing (pure merge)', more)

    def _await_agent_job(self, job, max_wait):
        if not jobs.wait([job], max_wait):
            say('PENDING | scoring is still running (job %s) | run: loopkit wait' % job.id)
            return False
        state = self.checkout_state()
        records = self.records()
        status, reason, result = self._verdict(job.result(), records)
        parent = ledger_mod.candidate_map(records)[state['parents'][0]]
        parts = ['RESULT %s' % status]
        if result.get('objectives') and status != 'FAILED':
            parts += report.objective_parts(self.config, result['objectives'], parent.get('objectives'))
        parts.append(reason if reason else 'front would be %d' % self._front_size_after(result, records))
        say(' | '.join(parts))
        for warning in result.get('warnings') or []:
            say('WARNING: %s' % warning)
        say('NEXT: loopkit record --idea "..." --proposed-by "..." --learned "..."')
        return True

    def _verdict(self, result, records):
        reason = jobs.failed_reason(result)
        if reason:
            return 'FAILED', reason, result
        verdict, detail = self.evolution(records).judge(result['objectives'])
        if verdict == 'KEPT':
            return 'KEPT', None, result
        return 'REVERTED', detail, result

    def _front_size_after(self, result, records):
        evo = self.evolution(records)
        _, removed = evo.judge(result['objectives'])
        return len(evo.front) - len(removed) + 1

    # -- tail: record ----------------------------------------------------------

    def record(self, idea, proposed_by, learned):
        state = self.checkout_state()
        if not state.get('job'):
            raise LoopkitError('nothing evaluated yet; run `loopkit evaluate` first')
        job = jobs.Job(self.paths, state['job'])
        if not job.done():
            raise LoopkitError('scoring is still running; run `loopkit wait` first')
        idea = ' '.join((idea or '').split())
        learned = ' '.join((learned or '').split())
        if not idea:
            raise LoopkitError('--idea is required')
        if not learned:
            raise LoopkitError('--learned is required')
        if len(learned) > LEARNED_MAX:
            raise LoopkitError('--learned is %d characters; keep it within %d' % (len(learned), LEARNED_MAX))
        names = [n.strip() for n in (proposed_by or '').split(',') if n.strip()]
        allowed = {'agent'} | {a['name'] for a in self.config['workflow'].get('analysts', [])}
        unknown = [n for n in names if n not in allowed]
        if not names or unknown:
            raise LoopkitError('--proposed-by must name %s' % ', '.join(sorted(allowed)))
        records = self.records()
        current = self._running_batch(records)
        if current['stopped']:
            stop_line(current['stopped'])
            return
        status, reason, result = self._verdict(job.result(), records)
        cid = ledger_mod.next_id(records, 'c')
        ref = 'refs/evolve/%s/%s' % (self.name, cid)
        stale = gitops.list_refs(self.git_cwd, ref)
        if stale:  # left behind by an interrupted record
            gitops.delete_ref(self.git_cwd, ref, stale[ref])
        message = 'loopkit %s %s: %s\n\nparents: %s\nstatus: %s%s\n' % (
            self.name, cid, truncate(idea, 72), ' '.join(state['parents']), status,
            (' (%s)' % reason) if reason else '')
        commit = gitops.commit_tree(self.git_cwd, state['tree'], state['parent_shas'], message)
        gitops.create_ref(self.git_cwd, ref, commit)
        parent = ledger_mod.candidate_map(records)[state['parents'][0]]
        batch_iter = batch_mod.iterations(records, current['batch']) + 1
        files = state.get('files') or []
        fields = {
            'id': cid, 'by': 'agent', 'iter': int(cid[1:]), 'batch': current['batch'], 'batch_iter': batch_iter,
            'parents': state['parents'], 'sha': commit, 'idea': idea, 'proposed_by': names,
            'files': files[:scope_mod.MAX_FILES], 'files_total': len(files),
            'objectives': result.get('objectives'), 'parent_objectives': parent.get('objectives'),
            'constraints': result.get('constraints'), 'extra': result.get('extra'),
            'status': status, 'reason': reason, 'on_front': status == 'KEPT', 'learned': learned,
            'duration_s': result.get('duration_s'), 'cost_usd': None,
        }
        if state.get('conflicts'):
            fields['conflicts'] = state['conflicts']
        if result.get('warnings'):
            fields['warnings'] = result['warnings']
        record = self.ledger.append('candidate', fields)
        self._store_artifacts(cid, job)
        self._save_state('checkout.json', None)
        records = self.records()
        evo = self.evolution(records)
        max_iters = current['conditions'].get('max_iters')
        say(report.iter_line(self.config, record, batch_iter, max_iters, len(evo.front)))
        self._check_stop(records, current)

    def _store_artifacts(self, cid, job):
        target = self.paths.artifact_dir(cid)
        os.makedirs(target, exist_ok=True)
        for name in ('score.txt', 'result.json', 'score-result.json', 'spec.json'):
            source = os.path.join(job.dir, name)
            if os.path.exists(source):
                shutil.copyfile(source, os.path.join(target, name))

    # -- waiting ---------------------------------------------------------------

    def await_baseline(self, max_wait):
        state = self._state('baseline.json')
        if not state:
            return True
        job = jobs.Job(self.paths, state['job'])
        if not jobs.wait([job], max_wait):
            say('PENDING | c000 is still being scored (job %s) | run: loopkit wait' % job.id)
            return False
        result = job.result()
        reason = jobs.failed_reason(result)
        c000 = self.info['c000']
        ref = 'refs/evolve/%s/c000' % self.name
        if not gitops.list_refs(self.git_cwd, ref):
            gitops.create_ref(self.git_cwd, ref, c000)
        record = self.ledger.append('candidate', {
            'id': 'c000', 'by': 'baseline', 'iter': 0, 'batch': None, 'parents': [], 'sha': c000,
            'idea': 'baseline', 'proposed_by': [], 'files': [], 'files_total': 0,
            'objectives': result.get('objectives'), 'parent_objectives': None,
            'constraints': result.get('constraints'), 'extra': result.get('extra'),
            'status': 'FAILED' if reason else 'BASELINE', 'reason': reason, 'on_front': not reason,
            'duration_s': result.get('duration_s'), 'cost_usd': None,
            'warnings': result.get('warnings') or None,
        })
        self._store_artifacts('c000', job)
        self._save_state('baseline.json', None)
        say(report.record_line(self.config, record))
        for warning in result.get('warnings') or []:
            say('WARNING: %s' % warning)
        if reason:
            say('ERROR: c000 must pass every constraint and produce valid objectives; see %s' %
                report.log_path(self.paths, 'c000'))
            say('       fix the score script or config, then remove this run (loopkit run remove %s --yes) and re-run init' % self.name)
        else:
            self._print_next()
        return True

    def _print_next(self):
        example = self.config['objectives'][0]
        below = '低於' if example['direction'] == 'minimize' else '高於'
        say('NEXT')
        say('  1. Open the agent worktree in a new VS Code window:  code %s' % self.paths.agent)
        say('  2. Start Claude Code there in auto mode and paste one prompt (fill in the stop conditions):')
        say('     /goal 重複執行 /loopkit:iter（停止條件：<例如：最多 20 輪或 %s %s <目標值>>，由 loopkit 判斷），'
            '直到輸出出現 LOOPKIT-STOP。單一輪 REVERTED 或 FAILED 不代表目標不可能達成。' % (example['name'], below))
        say('     /loop /loopkit:iter（停止條件：<例如：最多 50 輪>，由 loopkit 判斷；輸出出現 LOOPKIT-STOP 時停止 loop）')

    def wait(self, max_wait):
        deadline = time.time() + max_wait
        did = False
        if self._state('baseline.json'):
            did = True
            if not self.await_baseline(max(0, deadline - time.time())):
                return
        if self._state('queue-jobs.json'):
            did = True
            if not self._await_queue(max(0, deadline - time.time())):
                return
        state = self.checkout_state(required=False)
        if state and state.get('job'):
            job = jobs.Job(self.paths, state['job'])
            did = True
            self._await_agent_job(job, max(0, deadline - time.time()))
        if not did:
            say('NOTHING PENDING')

    # -- reports -----------------------------------------------------------------

    def summary(self, full=False):
        records = self.records()
        say('RUN %s | %s' % (self.name, self.paths.dir))
        if os.path.isfile(self.paths.knowledge):
            say('KNOWLEDGE %s (describes c000)' % self.paths.knowledge)
        stopped = self._integrity_stopped(records)
        if stopped:
            say('STOPPED BY %s' % stopped)
        say(report.summary(self.config, records, queue_mod.pending(self.paths), full=full))

    def show(self, cid, log=False):
        record = ledger_mod.candidate_map(self.records()).get(cid)
        if not record:
            raise LoopkitError('no candidate %s' % cid)
        say(report.show(record, report.log_path(self.paths, cid) if log else None))

    def lineage(self, cid):
        text = report.lineage(self.records(), cid)
        if text is None:
            raise LoopkitError('no candidate %s' % cid)
        say(text)

    def diff(self, a, b, stat=False):
        by_id = ledger_mod.candidate_map(self.records())
        for cid in (a, b):
            if cid not in by_id:
                raise LoopkitError('no candidate %s' % cid)
        say(gitops.diff_text(self.git_cwd, by_id[a]['sha'], by_id[b]['sha'], stat=stat))

    def status_data(self, records):
        current = batch_mod.current(records)
        pending = queue_mod.pending(self.paths)
        evo = self.evolution(records)
        counts = {}
        for record in ledger_mod.candidates(records):
            counts[record.get('status')] = counts.get(record.get('status'), 0) + 1
        return {
            'run': self.name,
            'batch': current['batch'] if current else None,
            'batch_iter': batch_mod.iterations(records, current['batch']) if current else 0,
            'max_iters': (current['conditions'] or {}).get('max_iters') if current else None,
            'conditions': batch_mod.describe(current['conditions']) if current else None,
            'stopped': (self._integrity_stopped(records) or (current or {}).get('stopped')),
            'counts': counts,
            'front': list(evo.front),
            'queue': {'eval': sum(1 for r in pending if r.get('kind') == 'eval'),
                      'promote': sum(1 for r in pending if r.get('kind') == 'promote')},
            'integrity_warnings': len(ledger_mod.events(records, 'integrity_warning')),
            'updated': records[-1]['ts'] if records else None,
        }

    def export(self, since=None, pending=False, ack=None):
        if ack is not None:
            self._save_state('monitor.json', {'pushed_seq': ack, 'at': now_iso()})
            say('ACK %d' % ack)
            return
        records = self.records()
        if pending:
            since = (self._state('monitor.json') or {}).get('pushed_seq', 0)
        data = report.export(self.config, records, since or 0, self.status_data(records))
        data['monitor_url'] = self.info.get('monitor_url')
        say(json.dumps(data, ensure_ascii=False, indent=1))

    def status(self):
        records = self.records()
        data = self.status_data(records)
        say('RUN %s | %s' % (self.name, self.paths.dir))
        say('  agent worktree: %s' % self.paths.agent)
        say('  c000:           %s' % self.info['c000'][:12])
        say('  gpu:            %s' % (self.info.get('gpu') or 'not set'))
        say('  monitor:        %s' % (self.info.get('monitor_url') or 'none'))
        if data['batch']:
            say('  batch:          %d, iter %d%s (%s)%s' % (
                data['batch'], data['batch_iter'], '/%d' % data['max_iters'] if data['max_iters'] else '',
                data['conditions'], ' STOPPED: %s' % data['stopped'] if data['stopped'] else ''))
        else:
            say('  batch:          none yet')
        say('  candidates:     %s' % ', '.join('%s %d' % kv for kv in sorted(data['counts'].items())))
        say('  front:          %s' % ' '.join(data['front']))
        say('  queue:          %d eval, %d promote' % (data['queue']['eval'], data['queue']['promote']))

    # -- user side ---------------------------------------------------------------

    def request_eval(self, cwd, commit, note):
        request = queue_mod.request_eval(self.paths, gitops.toplevel(cwd), commit, note)
        say('QUEUED request %d: evaluate %s (%s) in run %s at the head of its next iteration' % (
            request['n'], request['sha'][:7], report.short_ref(request['ref']), self.name))

    def promote(self, cid):
        request = queue_mod.request_promote(self.paths, cid)
        say('QUEUED request %d: promote %s in run %s at the head of its next iteration' % (request['n'], cid, self.name))

    def adopt(self, cwd, cid, branch=None):
        record = ledger_mod.candidate_map(self.records()).get(cid)
        if not record:
            raise LoopkitError('no candidate %s' % cid)
        branch = branch or 'loopkit/%s/%s' % (self.name, cid)
        repo = gitops.toplevel(cwd)
        if gitops.run(['rev-parse', '--verify', '--quiet', 'refs/heads/%s' % branch], repo, check=False).returncode == 0:
            raise LoopkitError('branch %s already exists' % branch)
        gitops.run(['branch', branch, record['sha']], repo)
        say('ADOPTED %s as branch %s (%s); review and merge it yourself' % (cid, branch, record['sha'][:7]))

    def remove(self):
        for name in os.listdir(self.paths.jobs) if os.path.isdir(self.paths.jobs) else []:
            job = jobs.Job(self.paths, name)
            if not job.done():
                job.kill()
        repo = self.info['repo']['toplevel']
        cwd = repo if os.path.isdir(repo) else self.git_cwd
        for worktree in (self.paths.agent, self.paths.eval):
            gitops.worktree_remove(cwd, worktree)
        for ref, sha in gitops.list_refs(cwd, 'refs/evolve/%s/' % self.name).items():
            gitops.delete_ref(cwd, ref, sha)
        for directory, dirnames, filenames in os.walk(self.paths.dir):
            os.chmod(directory, 0o755)
        os.chmod(self.paths.run_json, 0o644)
        shutil.rmtree(self.paths.dir)
        say('REMOVED run %s (worktrees, refs and %s)' % (self.name, self.paths.dir))


# ---------------------------------------------------------------------------
# Init helpers: config check and trial runs in the user's working tree
# ---------------------------------------------------------------------------

def config_check(cwd, path=None):
    repo = gitops.toplevel(cwd)
    path = path or os.path.join(repo, config_mod.CONFIG_PATH)
    config = config_mod.load(path)
    say('CONFIG ok | %d objective(s), %d constraint(s), workflow %s' % (
        len(config['objectives']), len(config.get('constraints', [])), config['workflow']['mode']))
    findings = [('score.command', r) for r in config_mod.screen(config['score']['command'])]
    if config.get('precheck'):
        findings += [('precheck.command', r) for r in config_mod.screen(config['precheck']['command'])]
    for asset in config['eval_assets']:
        full = asset if os.path.isabs(asset) else os.path.join(repo, asset)
        if os.path.isfile(full) and os.path.getsize(full) < 1 << 20:
            with open(full, encoding='utf-8', errors='replace') as f:
                findings += [(asset, r) for r in config_mod.screen(f.read())]
    for where, reason in findings:
        say('DANGER %s: %s' % (where, reason))
    if not findings:
        say('SCREEN ok | no dangerous commands found')


def trial(cwd, repeat=1, gpu=None, timeout=None):
    """Run the score script on the current working tree, as init does before a run exists."""
    repo = gitops.toplevel(cwd)
    config = config_mod.load(os.path.join(repo, config_mod.CONFIG_PATH))
    base = os.path.join(paths_mod.repo_dir(gitops.common_dir(repo)), '_trial')
    build = os.path.join(base, 'build')
    os.makedirs(build, exist_ok=True)
    gpu = gpu if gpu is not None else (config.get('run') or {}).get('gpu')
    info = {'config': config, 'gpu': gpu}
    samples = []
    for index in range(repeat):
        result_path = os.path.join(base, 'result.json')
        log_path = os.path.join(base, 'trial-%d.txt' % (index + 1))
        try:
            os.unlink(result_path)
        except OSError:
            pass
        variables = {'LOOPKIT_RESULT': result_path, 'LOOPKIT_EVAL_DIR': repo, 'LOOPKIT_BUILD_DIR': build,
                     'LOOPKIT_WORKTREE': repo}
        argv = config_mod.expand(config['score']['command'], variables)
        started = time.time()
        with open(log_path, 'wb') as log:
            proc = subprocess.Popen(argv, cwd=repo, env=jobs.score_env(info, variables), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout or config['score'].get('timeout_s', 1800))
            except subprocess.TimeoutExpired:
                jobs._terminate(proc)
                code = 'timeout'
        duration = time.time() - started
        if code == 'timeout':
            result = {'outcome': 'fail', 'reason': 'score_timeout'}
        elif code != 0:
            result = {'outcome': 'invalid', 'reason': 'invalid_result: score command exited with %s' % code}
        else:
            result = jobs.interpret(config, result_path, duration)
        head = 'TRIAL %d/%d | %.1fs' % (index + 1, repeat, duration)
        if result['outcome'] != 'ok':
            say('%s | %s | %s | log: %s' % (head, result['outcome'].upper(), result['reason'], log_path))
            continue
        samples.append(result['objectives'])
        constraints = ', '.join('%s %s' % (k, 'pass' if v['pass'] else 'FAIL') for k, v in sorted(result['constraints'].items()))
        say('%s | ok | %s | constraints: %s | log: %s' % (
            head, ' '.join('%s=%r' % (k, v) for k, v in result['objectives'].items()), constraints or 'none', log_path))
        for warning in result.get('warnings') or []:
            say('WARNING: %s' % warning)
    if len(samples) >= 2:
        for objective in config['objectives']:
            values = sorted(s[objective['name']] for s in samples)
            median = values[len(values) // 2]
            spread = (values[-1] - values[0]) / abs(median) if median else float('inf')
            say('SPREAD %s | min %r | median %r | max %r | relative spread %.4g%%' % (
                objective['name'], values[0], median, values[-1], spread * 100))
