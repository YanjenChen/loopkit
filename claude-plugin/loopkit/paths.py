"""Where loopkit keeps things: the data root (~/loopkit-runs), per-repo directories, and run directories."""

import hashlib
import os

from .util import LoopkitError, read_json, sanitize_name

PLUGIN_NAME = 'loopkit'

# Files the framework writes into the agent worktree. They are never part of a candidate.
SETTINGS_LOCAL = '.claude/settings.local.json'
MARKER = '.claude/loopkit-run.json'
FRAMEWORK_FILES = (SETTINGS_LOCAL, MARKER)


def plugin_root():
    """The claude-plugin/ directory this package was loaded from."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def claude_config_dir():
    return os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(os.path.expanduser('~'), '.claude')


def _installed_plugins():
    try:
        return read_json(os.path.join(claude_config_dir(), 'plugins', 'installed_plugins.json')).get('plugins', {})
    except (OSError, ValueError, AttributeError):
        return {}


def plugin_id():
    """This plugin's id as Claude Code records it: loopkit@<marketplace>."""
    here = os.path.realpath(plugin_root())
    fallback = None
    for key, installs in _installed_plugins().items():
        name, _, marketplace = key.partition('@')
        if name != PLUGIN_NAME or not marketplace:
            continue
        for install in installs if isinstance(installs, list) else []:
            path = install.get('installPath') if isinstance(install, dict) else None
            if path and os.path.realpath(path) == here:
                return key
        fallback = fallback or key
    return fallback or '%s@%s' % (PLUGIN_NAME, PLUGIN_NAME)


DEFAULT_DATA_DIR = '~/loopkit-runs'


def data_root():
    """Where runs live: ~/loopkit-runs, or LOOPKIT_DATA_DIR.

    Outside the plugin's own data directory on purpose: easy to find, and
    untouched when the plugin is updated, reinstalled or uninstalled.
    """
    value = os.environ.get('LOOPKIT_DATA_DIR')
    return os.path.abspath(os.path.expanduser(value or DEFAULT_DATA_DIR))


def repo_id(common_dir):
    """A stable id for a repository, derived from its git common dir: placer-1a2b3c4d."""
    common = os.path.realpath(common_dir)
    base = os.path.basename(common)
    if base == '.git':
        name = os.path.basename(os.path.dirname(common))
    else:
        name = base[:-4] if base.endswith('.git') else base
    name = sanitize_name(name)[:40] or 'repo'
    return '%s-%s' % (name, hashlib.sha256(common.encode('utf-8')).hexdigest()[:8])


def repo_dir(common_dir):
    return os.path.join(data_root(), repo_id(common_dir))


def lock_dir():
    return os.path.join(data_root(), 'locks')


class RunPaths(object):
    """The layout of one run directory: ~/loopkit-runs/<repo>/<run>/."""

    def __init__(self, run_dir):
        self.dir = os.path.abspath(run_dir)
        self.name = os.path.basename(self.dir)
        self.run_json = os.path.join(self.dir, 'run.json')
        self.ledger = os.path.join(self.dir, 'ledger.jsonl')
        self.queue = os.path.join(self.dir, 'queue')
        self.queue_done = os.path.join(self.queue, 'done')
        self.eval_assets = os.path.join(self.dir, 'eval-assets')
        self.manifest = os.path.join(self.eval_assets, 'MANIFEST.sha256')
        self.agent = os.path.join(self.dir, 'agent')
        self.eval = os.path.join(self.dir, 'eval')
        self.build = os.path.join(self.dir, 'build')
        self.agent_build = os.path.join(self.dir, 'agent-build')
        self.knowledge = os.path.join(self.dir, 'knowledge.md')
        self.artifacts = os.path.join(self.dir, 'artifacts')
        # Transient state for the iteration in progress; the ledger stays the only history.
        self.work = os.path.join(self.dir, 'work')
        self.jobs = os.path.join(self.work, 'jobs')
        self.checkout_state = os.path.join(self.work, 'checkout.json')
        self.monitor_state = os.path.join(self.work, 'monitor.json')

    def exists(self):
        return os.path.isfile(self.run_json)

    def info(self):
        try:
            return read_json(self.run_json)
        except (OSError, ValueError):
            raise LoopkitError('run %s has no readable run.json (%s)' % (self.name, self.run_json))

    def artifact_dir(self, candidate_id):
        return os.path.join(self.artifacts, candidate_id)


def list_runs(common_dir):
    """Runs of this repository, oldest first."""
    base = repo_dir(common_dir)
    runs = []
    try:
        names = os.listdir(base)
    except OSError:
        return runs
    for name in names:
        paths = RunPaths(os.path.join(base, name))
        if paths.exists():
            try:
                created = paths.info().get('created', '')
            except LoopkitError:
                created = ''
            runs.append((created, name, paths))
    runs.sort(key=lambda item: (item[0], item[1]))
    return [paths for _, _, paths in runs]


def find_marker(start):
    """Walk up from start looking for the run marker; returns (worktree_root, marker_data) or (None, None)."""
    current = os.path.abspath(start)
    while True:
        candidate = os.path.join(current, MARKER)
        if os.path.isfile(candidate):
            try:
                return current, read_json(candidate)
            except (OSError, ValueError):
                return None, None
        parent = os.path.dirname(current)
        if parent == current:
            return None, None
        current = parent
