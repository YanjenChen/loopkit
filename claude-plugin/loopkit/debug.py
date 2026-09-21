"""`loopkit report`: collect what is needed to debug a run into one directory and tarball.

Every section is collected independently: a failure is written into the report
instead of stopping it. The report holds paths, code excerpts, ideas and logs;
session transcripts are included only on request.
"""

import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time

from . import __version__, config as config_mod, gitops, integrity, jobs, ledger as ledger_mod
from . import paths as paths_mod, queue as queue_mod, report as report_mod
from .util import read_json

TAIL_WORKER = 30
TAIL_SCORE = 60
_PROJECT_KEY = re.compile(r'[^a-zA-Z0-9]')


class Report(object):
    def __init__(self, directory):
        self.dir = directory
        self.lines = []
        self.files = []

    def h(self, level, title):
        self.lines += ['', '#' * level + ' ' + title, '']

    def text(self, value):
        self.lines.append(value)

    def kv(self, rows):
        self.lines += ['| | |', '|---|---|']
        for key, value in rows:
            self.lines.append('| %s | %s |' % (key, str(value).replace('|', '\\|').replace('\n', ' ')))

    def block(self, content, lang=''):
        content = content.rstrip('\n') if content else '(empty)'
        self.lines += ['```' + lang, content, '```']

    def section(self, level, title, func):
        self.h(level, title)
        try:
            func()
        except Exception as exc:  # a broken section must not stop the report
            self.text('*could not collect: %s: %s*' % (type(exc).__name__, exc))

    def copy(self, source, name=None):
        if not os.path.isfile(source):
            return
        target = os.path.join(self.dir, 'files', name or os.path.basename(source))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        self.files.append(os.path.relpath(target, self.dir))


def _cmd(argv, cwd=None, timeout=20):
    try:
        proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return proc.stdout.decode('utf-8', 'replace').strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 'unavailable (%s)' % exc


def _tail(path, lines):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return ''.join(f.readlines()[-lines:])
    except OSError:
        return '(missing)'


def _json_file(path):
    try:
        return json.dumps(read_json(path), ensure_ascii=False, indent=1)
    except (OSError, ValueError):
        return '(missing)'


def project_key(path):
    return _PROJECT_KEY.sub('-', path)


def collect(cwd, run, out_dir=None, transcripts=False, job_count=5):
    """Write the report; returns (directory, tarball)."""
    stamp = time.strftime('%Y%m%d-%H%M%S')
    if out_dir:
        base = os.path.abspath(os.path.expanduser(out_dir))
    elif run is not None:
        base = os.path.join(run.paths.dir, 'reports')
    else:
        base = os.path.join(paths_mod.data_root(), '_reports')
    directory = os.path.join(base, 'report-%s' % stamp)
    os.makedirs(directory)
    rep = Report(directory)
    rep.text('# loopkit debug report')
    rep.text('')
    rep.text('Generated %s from `%s`. It contains paths, code excerpts, ideas and logs of this machine and '
             'run; review it before sharing it publicly.%s' % (
                 time.strftime('%Y-%m-%d %H:%M:%S %z'), cwd,
                 '' if transcripts else ' Session transcripts are not included (use --transcripts).'))

    rep.section(2, 'Environment', lambda: _environment(rep))
    rep.section(2, 'Repository', lambda: _repository(rep, cwd))
    if run is None:
        rep.h(2, 'Run')
        rep.text('No run found for this directory. The trial logs of init, if any, are included below.')
        rep.section(3, 'Trial logs', lambda: _trial_logs(rep, cwd))
    else:
        _run_sections(rep, run, transcripts, job_count)

    rep.h(2, 'Files in this report')
    rep.block('\n'.join(rep.files) or '(none)')
    with open(os.path.join(directory, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(rep.lines).lstrip('\n') + '\n')
    tarball = directory + '.tar.gz'
    with tarfile.open(tarball, 'w:gz') as tar:
        tar.add(directory, arcname=os.path.basename(directory))
    return directory, tarball


def _environment(rep):
    installs = []
    for key, entries in paths_mod._installed_plugins().items():
        if key.partition('@')[0] == paths_mod.PLUGIN_NAME:
            for entry in entries if isinstance(entries, list) else []:
                installs.append('%s scope=%s version=%s sha=%s%s' % (
                    key, entry.get('scope'), entry.get('version'), (entry.get('gitCommitSha') or '')[:10],
                    (' project=%s' % entry['projectPath']) if entry.get('projectPath') else ''))
    env = ', '.join('%s=%s' % (k, v) for k, v in sorted(os.environ.items()) if k.startswith('LOOPKIT_')) or 'none'
    rep.kv([
        ('loopkit', __version__),
        ('plugin root (this code)', paths_mod.plugin_root()),
        ('installed plugins', '; '.join(installs) or 'not found in installed_plugins.json'),
        ('Claude Code', _cmd(['claude', '--version'])),
        ('git', '.'.join(str(n) for n in gitops.version())),
        ('python', '%s (%s)' % (platform.python_version(), sys.executable)),
        ('platform', platform.platform()),
        ('data root', paths_mod.data_root()),
        ('LOOPKIT_* environment', env),
        ('CUDA_VISIBLE_DEVICES', os.environ.get('CUDA_VISIBLE_DEVICES', '(unset)')),
    ])
    rep.text('')
    rep.text('GPUs (`nvidia-smi -L`):')
    rep.block(_cmd(['nvidia-smi', '-L']))


def _repository(rep, cwd):
    top = gitops.toplevel(cwd)
    common = gitops.common_dir(cwd)
    rep.kv([
        ('worktree', top),
        ('git common dir', common),
        ('repo id', paths_mod.repo_id(common)),
        ('HEAD', gitops.out(['rev-parse', 'HEAD'], top)),
        ('branch', gitops.symbolic_head(top) or 'detached'),
        ('runs', ', '.join(p.name for p in paths_mod.list_runs(common)) or 'none'),
    ])
    config_path = os.path.join(top, config_mod.CONFIG_PATH)
    if os.path.isfile(config_path):
        try:
            config_mod.load(config_path)
            rep.text('\n`.loopkit/config.json` in this worktree is valid.')
        except Exception as exc:
            rep.text('\n`.loopkit/config.json` in this worktree is invalid:')
            rep.block(str(exc))


def _trial_logs(rep, cwd):
    trial = os.path.join(paths_mod.repo_dir(gitops.common_dir(cwd)), '_trial')
    logs = sorted(glob.glob(os.path.join(trial, 'trial-*.txt')))
    if not logs:
        rep.text('none')
    for log in logs:
        rep.text('`%s` (last %d lines):' % (log, TAIL_SCORE))
        rep.block(_tail(log, TAIL_SCORE))
        rep.copy(log, 'trial/' + os.path.basename(log))


def _run_sections(rep, run, transcripts, job_count):
    paths = run.paths
    info = run.info
    rep.h(2, 'Run %s' % run.name)
    rep.kv([
        ('run dir', paths.dir),
        ('agent worktree', paths.agent),
        ('created', info.get('created')),
        ('c000', info.get('c000')),
        ('gpu', info.get('gpu') or '(unset)'),
        ('monitor', info.get('monitor_url') or 'none'),
        ('created by plugin', info.get('plugin_root')),
    ])
    records, ledger_problems = run.read()

    def integrity_section():
        result = integrity.check(paths, info, records, ledger_problems, run.git_cwd, fix=False)
        rep.text('Problems: %s' % ('; '.join(result.problems) or 'none'))
        rep.text('')
        rep.text('Changed since the last recorded state: %s' % (', '.join(sorted(result.warnings)) or 'nothing'))
        events = ledger_mod.events(records, 'integrity') + ledger_mod.events(records, 'integrity_warning')
        if events:
            rep.block('\n'.join(json.dumps(e, ensure_ascii=False) for e in events[-10:]), 'json')

    rep.section(3, 'Integrity (read-only check)', integrity_section)
    rep.section(3, 'Status', lambda: rep.block(json.dumps(run.status_data(records), ensure_ascii=False, indent=1), 'json'))
    rep.section(3, 'Summary', lambda: rep.block(report_mod.summary(run.config, records, queue_mod.pending(paths))))

    def batches():
        keys = ('seq', 'ts', 'event', 'batch', 'id', 'request', 'text', 'conditions', 'reason', 'on_front')
        rows = [json.dumps({k: e[k] for k in keys if e.get(k) is not None},
                           ensure_ascii=False) for e in ledger_mod.events(records)
                if e.get('event') in ('batch_start', 'batch_stop', 'promote', 'request_rejected')]
        rep.block('\n'.join(rows) or '(none)', 'json')

    rep.section(3, 'Batches and requests', batches)

    def state():
        for name in ('checkout.json', 'queue-jobs.json', 'baseline.json', 'monitor.json'):
            path = os.path.join(paths.work, name)
            rep.text('`work/%s`:' % name)
            rep.block(_json_file(path), 'json')
            rep.copy(path, 'work/' + name)
        pending = queue_mod.pending(paths)
        rep.text('Queued requests: %s' % (', '.join('%d %s' % (r['n'], r.get('kind')) for r in pending) or 'none'))

    rep.section(3, 'Iteration in progress', state)

    def recent_jobs():
        job_dirs = sorted(glob.glob(os.path.join(paths.jobs, '*')), key=os.path.getmtime)[-job_count:]
        if not job_dirs:
            rep.text('none')
        for job_dir in reversed(job_dirs):
            job = jobs.Job(paths, os.path.basename(job_dir))
            status = job.status()
            result = job.result() or {}
            rep.h(4, job.id)
            rep.kv([
                ('state', status.get('state')),
                ('started / finished', '%s / %s' % (status.get('started'), status.get('finished'))),
                ('worker alive', job.alive()),
                ('outcome', '%s %s' % (result.get('outcome'), result.get('reason') or '')),
            ])
            rep.text('')
            rep.text('worker.txt (last %d lines):' % TAIL_WORKER)
            rep.block(_tail(os.path.join(job_dir, 'worker.txt'), TAIL_WORKER))
            rep.text('score.txt (last %d lines):' % TAIL_SCORE)
            rep.block(_tail(job.log_path, TAIL_SCORE))
            for name in ('spec.json', 'status.json', 'result.json', 'score-result.json', 'worker.txt', 'score.txt'):
                rep.copy(os.path.join(job_dir, name), 'jobs/%s/%s' % (job.id, name))

    rep.section(3, 'Recent scoring jobs', recent_jobs)

    def worktrees():
        rep.text('`git worktree list`:')
        rep.block(gitops.out(['worktree', 'list'], run.git_cwd))
        for label, path in (('agent', paths.agent), ('eval', paths.eval)):
            status = gitops.run(['status', '--short', '--untracked-files=all'], path, check=False)
            lines = status.stdout.decode('utf-8', 'replace').splitlines()
            rep.text('%s worktree: HEAD %s, %d changed or untracked path(s)%s' % (
                label, gitops.out(['rev-parse', '--short', 'HEAD'], path), len(lines), ':' if lines else ''))
            if lines:
                rep.block('\n'.join(lines[:60]) + ('\n... %d more' % (len(lines) - 60) if len(lines) > 60 else ''))
        refs = gitops.list_refs(run.git_cwd, 'refs/evolve/%s/' % run.name)
        rep.text('refs/evolve/%s/: %d ref(s)' % (run.name, len(refs)))
        rep.block('\n'.join('%s %s' % (sha[:10], name) for name, sha in sorted(refs.items())[-40:]))

    rep.section(3, 'Worktrees and refs', worktrees)

    def session_files():
        for rel in (paths_mod.SETTINGS_LOCAL, paths_mod.MARKER):
            path = os.path.join(paths.agent, rel)
            rep.text('`%s` in the agent worktree:' % rel)
            rep.block(_json_file(path), 'json')
            rep.copy(path, 'agent/' + rel.replace('/', '_'))

    rep.section(3, 'Run session settings', session_files)

    def hook_log():
        key = hashlib.md5(paths.agent.encode('utf-8')).hexdigest()[:12]
        log = os.path.join(os.path.expanduser('~'), '.claude', 'hooks', '.logs', key, 'hook-log.jsonl')
        rep.text('`%s` (last 40 entries; metadata only):' % log)
        rep.block(_tail(log, 40))

    rep.section(3, 'Hook log of the run session', hook_log)

    def transcript_section():
        folder = os.path.join(paths_mod.claude_config_dir(), 'projects', project_key(os.path.realpath(paths.agent)))
        if not os.path.isdir(folder):
            folder = os.path.join(paths_mod.claude_config_dir(), 'projects', project_key(paths.agent))
        files = sorted(glob.glob(os.path.join(folder, '*.jsonl')), key=os.path.getmtime)
        if not files:
            rep.text('No run-session transcripts under `%s`.' % folder)
            return
        rep.text('`%s`:' % folder)
        rep.block('\n'.join('%s  %8d bytes  %s' % (time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(f))),
                                                   os.path.getsize(f), os.path.basename(f)) for f in files))
        if transcripts:
            for f in files[-3:]:
                rep.copy(f, 'transcripts/' + os.path.basename(f))
            rep.text('The %d most recent transcript(s) are included under files/transcripts/.' % min(3, len(files)))

    rep.section(3, 'Run session transcripts', transcript_section)

    for name in ('run.json', 'ledger.jsonl', 'ledger.jsonl.head', 'knowledge.md'):
        rep.copy(os.path.join(paths.dir, name))
