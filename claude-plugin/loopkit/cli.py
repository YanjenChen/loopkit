"""The `loopkit` command line."""

import argparse
import json
import os
import sys

from . import __version__, integrity, jobs, paths as paths_mod, run as run_mod
from .util import LoopkitError


def _max_wait(args):
    return args.max_wait if args.max_wait is not None else jobs.max_wait_default()


def cmd_run_create(args):
    run_mod.create(os.getcwd(), name=args.name, gpu=args.gpu, knowledge=args.knowledge,
                   monitor_url=args.monitor_url, allow_danger=args.allow_danger, max_wait=_max_wait(args))


def cmd_run_list(args):
    from . import gitops
    runs = paths_mod.list_runs(gitops.common_dir(os.getcwd()))
    if not runs:
        print('no runs for this repository')
    for run_paths in runs:
        run = run_mod.Run(run_paths)
        data = run.status_data(run.records())
        state = 'stopped: %s' % data['stopped'] if data['stopped'] else ('batch %s' % data['batch'] if data['batch'] else 'no batch yet')
        print('%s | created %s | %s | front %s | %s' % (
            run.name, run.info.get('created'), state, ' '.join(data['front']), run_paths.dir))


def cmd_run_remove(args):
    if not args.yes:
        raise LoopkitError('removing a run deletes its worktrees, refs and ledger; pass --yes to confirm')
    try:
        run = run_mod.resolve(os.getcwd(), args.name)
    except LoopkitError:
        run_mod.remove_partial(os.getcwd(), args.name)
        return
    run.remove()


def session_run(args):
    return run_mod.resolve(os.getcwd(), args.run, session_only=True)


def user_run(args):
    return run_mod.resolve(os.getcwd(), args.run)


def cmd_check(args):
    run = user_run(args)
    records, problems = run.read()
    report = integrity.check(run.paths, run.info, records, problems, run.git_cwd, fix=False)
    for problem in report.problems:
        print('PROBLEM: %s' % problem)
    for key in sorted(report.warnings):
        print('WARNING: %s changed since the last recorded state' % key)
    if report.ok:
        print('INTEGRITY ok')


def cmd_monitor_html(args):
    from . import config as config_mod, gitops, monitor
    cwd = os.getcwd()
    try:
        run = run_mod.resolve(cwd, args.run, session_only=True) if args.run or paths_mod.find_marker(cwd)[1] else None
    except LoopkitError:
        run = None
    if run is not None:
        config, name = run.config, run.name
        repo_name = os.path.basename(run.info['repo']['toplevel'])
    else:
        repo = gitops.toplevel(cwd)
        config = config_mod.load(os.path.join(repo, config_mod.CONFIG_PATH))
        name = run_mod._next_run_name(gitops.common_dir(repo))
        repo_name = os.path.basename(repo)
    sample = monitor.sample_data(config, name) if args.sample else None
    html = monitor.render(args.title or monitor.default_title(repo_name, name), sample)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(html)
    print('MONITOR page written to %s%s' % (args.out, ' (with sample data)' if sample else ''))
    print('PUBLISH with the Artifact tool: file_path %s, icon "chart", capabilities %s' % (
        args.out, json.dumps(monitor.DB_CAPABILITIES)))


def cmd_monitor_push(args):
    from . import monitor
    run = session_run(args)
    url = run.info.get('monitor_url')
    if not url:
        print('MONITOR none: this run has no monitor')
        return
    writes, ack, pushed, more = monitor.push_plan(run)
    if ack <= pushed:
        print('MONITOR up to date (seq %d); pushing the status only' % ack)
    print('MONITOR %s' % url)
    print('WRITES %s' % json.dumps(writes))
    print('NEXT: Artifact action "write_db", url above, db_op "batch", writes above; then `loopkit monitor ack %d`'
          % ack)
    if more:
        print('MORE: further records remain; run `loopkit monitor push` again after the ack')


def cmd_monitor_ack(args):
    run = session_run(args)
    run.export(ack=args.seq)


def cmd_report(args):
    from . import debug
    cwd = os.getcwd()
    try:
        run = run_mod.resolve(cwd, args.run)
    except LoopkitError:
        run = None
    directory, tarball = debug.collect(cwd, run, args.out, args.transcripts, args.jobs)
    print('REPORT %s' % os.path.join(directory, 'report.md'))
    print('ARCHIVE %s' % tarball)
    if run is None:
        print('NOTE: no run found here; the report covers the environment and repository only')


def cmd_shim(args):
    target_dir = os.path.expanduser(args.dir)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, 'loopkit')
    fallback = os.path.join(paths_mod.plugin_root(), 'bin', 'loopkit')
    script = '''#!/usr/bin/env python3
"""loopkit shim: runs the currently installed loopkit plugin."""
import json, os, sys
root = None
config = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
try:
    with open(os.path.join(config, 'plugins', 'installed_plugins.json')) as f:
        for key, installs in json.load(f).get('plugins', {}).items():
            if key.split('@')[0] == 'loopkit' and installs:
                root = installs[0].get('installPath')
                break
except Exception:
    pass
entry = os.path.join(root, 'bin', 'loopkit') if root else %r
if not os.path.isfile(entry):
    entry = %r
os.execv(sys.executable, [sys.executable, '-I', entry] + sys.argv[1:])
''' % (fallback, fallback)
    with open(path, 'w') as f:
        f.write(script)
    os.chmod(path, 0o755)
    print('SHIM written to %s (make sure %s is on your PATH)' % (path, target_dir))


def build_parser():
    parser = argparse.ArgumentParser(prog='loopkit', description='loopkit: evolve code with /goal and /loop.')
    parser.add_argument('--version', action='version', version='loopkit %s' % __version__)
    parser.add_argument('--run', help='run name (default: the run worktree you are in, or the newest run)')
    sub = parser.add_subparsers(dest='command', metavar='<command>')
    sub.required = True

    def run_option(p):
        # Also accepted after the subcommand, so `/loopkit:<cmd> $ARGUMENTS` can pass it through.
        p.add_argument('--run', default=argparse.SUPPRESS, help='run name')
        return p

    def add(name, func, help_text, **kwargs):
        p = run_option(sub.add_parser(name, help=help_text, **kwargs))
        p.set_defaults(func=func)
        return p

    def max_wait(p):
        p.add_argument('--max-wait', type=float, help='seconds to wait before reporting PENDING (default 540)')

    run_p = sub.add_parser('run', help='create, list or remove runs')
    run_sub = run_p.add_subparsers(dest='run_command', metavar='<run-command>')
    run_sub.required = True
    p = run_sub.add_parser('create', help='create a run from .loopkit/config.json and score c000')
    p.add_argument('--name')
    p.add_argument('--gpu', help='CUDA_VISIBLE_DEVICES for this run (overrides run.gpu)')
    p.add_argument('--knowledge', help='codebase summary to store as knowledge.md')
    p.add_argument('--monitor-url', help='URL of the monitor artifact')
    p.add_argument('--allow-danger', action='store_true', help='the user confirmed the flagged commands')
    max_wait(p)
    p.set_defaults(func=cmd_run_create)
    p = run_sub.add_parser('list', help='list the runs of this repository')
    p.set_defaults(func=cmd_run_list)
    p = run_sub.add_parser('remove', help='delete a run: worktrees, refs, ledger')
    p.add_argument('name')
    p.add_argument('--yes', action='store_true')
    p.set_defaults(func=cmd_run_remove)

    config_p = sub.add_parser('config', help='validate the loopkit config')
    config_sub = config_p.add_subparsers(dest='config_command', metavar='<config-command>')
    config_sub.required = True
    p = config_sub.add_parser('check', help='validate .loopkit/config.json and screen its commands')
    p.add_argument('--path')
    p.set_defaults(func=lambda a: run_mod.config_check(os.getcwd(), a.path))

    p = add('trial', lambda a: run_mod.trial(os.getcwd(), a.repeat, a.gpu, a.timeout),
            'run the score script on the current working tree (init)')
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--gpu')
    p.add_argument('--timeout', type=float)

    batch_p = sub.add_parser('batch', help='register the stop conditions of this /goal or /loop')
    batch_sub = batch_p.add_subparsers(dest='batch_command', metavar='<batch-command>')
    batch_sub.required = True
    p = run_option(batch_sub.add_parser('start', help='head of every iteration: integrity, batch, stop check'))
    p.add_argument('--text', required=True, help='the /goal or /loop prompt, verbatim')
    p.add_argument('--conditions', required=True, help='structured stop conditions as JSON')
    p.set_defaults(func=lambda a: session_run(a).batch_start(a.text, a.conditions))
    p = run_option(batch_sub.add_parser('stop', help='stop the running batch now (LOOPKIT-STOP)'))
    p.add_argument('--reason', help='why, recorded in the ledger')
    p.set_defaults(func=lambda a: user_run(a).batch_stop(a.reason))

    p = add('queue', lambda a: session_run(a).process_queue(_max_wait(a)), 'process human requests')
    max_wait(p)
    p = add('summary', lambda a: session_run(a).summary(a.full), 'print the evolve state')
    p.add_argument('--full', action='store_true', help='do not truncate ideas and learned notes')
    p = add('checkout', lambda a: session_run(a).checkout(a.parent, a.merge), 'choose parents and reset the worktree')
    p.add_argument('parent', nargs='?')
    p.add_argument('merge', nargs='?', help='second parent to three-way merge in')
    add('precheck', lambda a: session_run(a).precheck(), 'quick compile check of the agent worktree')
    p = add('evaluate', lambda a: session_run(a).evaluate(_max_wait(a)), 'snapshot, scope check and score')
    max_wait(p)
    p = add('wait', lambda a: session_run(a).wait(_max_wait(a)), 'keep waiting for pending scoring')
    max_wait(p)
    p = add('record', lambda a: session_run(a).record(a.idea, a.proposed_by, a.learned), 'record this iteration')
    p.add_argument('--idea', required=True)
    p.add_argument('--proposed-by', required=True, help='"agent" or analyst names, comma separated')
    p.add_argument('--learned', required=True, help='hypothesis; result; evidence (<= 300 characters)')
    p = add('export', lambda a: session_run(a).export(a.since, a.pending, a.ack), 'ledger records for the monitor')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--since', type=int)
    group.add_argument('--pending', action='store_true', help='everything after the last --ack')
    group.add_argument('--ack', type=int, help='mark records up to this seq as pushed')
    p = add('show', lambda a: user_run(a).show(a.id, a.log), 'one candidate in full')
    p.add_argument('id')
    p.add_argument('--log', action='store_true', help='append the tail of the score log')
    p = add('lineage', lambda a: user_run(a).lineage(a.id), 'ancestors of a candidate')
    p.add_argument('id')
    p = add('diff', lambda a: user_run(a).diff(a.a, a.b, a.stat), 'code difference between two candidates')
    p.add_argument('a')
    p.add_argument('b')
    p.add_argument('--stat', action='store_true')
    add('check', cmd_check, 'run the integrity checks without recording anything')
    p = add('request-eval', lambda a: user_run(a).request_eval(os.getcwd(), a.commit, a.note),
            'queue one of your commits for evaluation (you only)')
    p.add_argument('commit')
    p.add_argument('--note')
    p = add('promote', lambda a: user_run(a).promote(a.id), 'let a human candidate join the evolution (you only)')
    p.add_argument('id')
    p = add('adopt', lambda a: user_run(a).adopt(os.getcwd(), a.id, a.branch), 'create a branch at a candidate (you only)')
    p.add_argument('id')
    p.add_argument('--branch')
    add('status', lambda a: user_run(a).status(), 'show the state of a run')

    monitor_p = sub.add_parser('monitor', help='the monitor page and pushing records to it')
    monitor_sub = monitor_p.add_subparsers(dest='monitor_command', metavar='<monitor-command>')
    monitor_sub.required = True
    p = run_option(monitor_sub.add_parser('html', help='write the monitor page'))
    p.add_argument('--out', required=True)
    p.add_argument('--sample', action='store_true', help='embed sample data for the setup preview')
    p.add_argument('--title')
    p.set_defaults(func=cmd_monitor_html)
    p = run_option(monitor_sub.add_parser('push', help='write the documents to push and print the write list'))
    p.set_defaults(func=cmd_monitor_push)
    p = run_option(monitor_sub.add_parser('ack', help='mark records up to SEQ as pushed'))
    p.add_argument('seq', type=int)
    p.set_defaults(func=cmd_monitor_ack)
    p = add('report', cmd_report, 'collect a debug report (run state, logs, environment)')
    p.add_argument('--out', help='directory for the report (default: the run directory)')
    p.add_argument('--transcripts', action='store_true', help='include the run session transcripts')
    p.add_argument('--jobs', type=int, default=5, help='number of recent scoring jobs to include')
    p = add('shim', cmd_shim, 'install a `loopkit` command for your terminal')
    p.add_argument('--dir', default='~/.local/bin')
    p = sub.add_parser('_worker')
    p.add_argument('run_dir')
    p.add_argument('job_id')
    p.set_defaults(func=lambda a: jobs.run_worker(a.run_dir, a.job_id))
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except LoopkitError as exc:
        sys.stdout.flush()
        print('loopkit: error: %s' % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0
