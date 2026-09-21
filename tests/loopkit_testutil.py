"""Shared fixtures for the loopkit unit tests: temp git repos and configs."""

import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'claude-plugin'))

GIT_ENV = {
    'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
    'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid',
    'GIT_CONFIG_NOSYSTEM': '1',
}

BASE_CONFIG = {
    'schema': 1,
    'source': 'optimize hpwl and runtime',
    'objectives': [
        {'name': 'hpwl', 'direction': 'minimize', 'tolerance': {'relative': 0.001}, 'unit': ''},
        {'name': 'runtime', 'direction': 'minimize', 'tolerance': {'relative': 0.02}, 'unit': 's'},
    ],
    'constraints': [{'name': 'legal', 'description': 'no overlaps'}],
    'extra': [],
    'score': {'command': ['python3', '-I', '${LOOPKIT_EVAL_DIR}/.loopkit/score.py'], 'timeout_s': 60, 'env': {}},
    'eval_assets': ['.loopkit/score.py'],
    'scope': {'include': ['src/**'], 'exclude': ['src/vendor/**']},
    'workflow': {'mode': 'single'},
    'run': {'gpu': None},
}


def config(**overrides):
    data = copy.deepcopy(BASE_CONFIG)
    data.update(overrides)
    return data


def git(cwd, *args, **kwargs):
    env = dict(os.environ)
    env.update(GIT_ENV)
    return subprocess.run(['git'] + list(args), cwd=cwd, env=env, check=kwargs.get('check', True),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()


def write(root, path, content, mode=None):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w') as f:
        f.write(content)
    if mode is not None:
        os.chmod(full, mode)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='loopkit-test-')
        self.addCleanup(self._cleanup)
        self._env = dict(os.environ)
        os.environ['LOOPKIT_DATA_DIR'] = os.path.join(self.tmp, 'data')
        os.environ.update(GIT_ENV)

    def _cleanup(self):
        os.environ.clear()
        os.environ.update(self._env)
        for directory, dirnames, filenames in os.walk(self.tmp):
            try:
                os.chmod(directory, 0o755)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_repo(self, name='repo', files=None):
        repo = os.path.join(self.tmp, name)
        os.makedirs(repo)
        git(repo, 'init', '-q', '-b', 'main')
        for path, content in (files or {'src/a.py': 'a = 1\n', 'README.md': 'hi\n'}).items():
            write(repo, path, content)
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'init')
        return repo
