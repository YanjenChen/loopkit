"""Unit tests for the git-backed modules: gitops, scope, assets, queue, integrity."""

import json
import os

from loopkit_testutil import TempDirTest, config, git, write

from loopkit import assets, gitops, integrity, ledger as ledger_mod, paths as paths_mod, queue as queue_mod, scope
from loopkit.util import LoopkitError, sha256_file, write_json_atomic


class GitopsTest(TempDirTest):
    def test_snapshot_includes_untracked_and_excludes_framework_files(self):
        repo = self.make_repo()
        write(repo, 'src/a.py', 'a = 2\n')
        write(repo, 'src/new.py', 'n = 1\n')
        write(repo, '.claude/settings.local.json', '{}')
        write(repo, '.claude/loopkit-run.json', '{}')
        tree = gitops.snapshot_tree(repo, os.path.join(self.tmp, 'idx'), exclude=paths_mod.FRAMEWORK_FILES)
        names = [e[3] for e in gitops.ls_tree(repo, tree)]
        self.assertEqual(sorted(names), ['README.md', 'src/a.py', 'src/new.py'])
        # The real index is untouched.
        self.assertIn('src/a.py', git(repo, 'status', '--porcelain'))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'idx')))

    def test_snapshot_respects_gitignore(self):
        repo = self.make_repo(files={'.gitignore': '*.o\n', 'src/a.c': 'x'})
        write(repo, 'src/a.o', 'obj')
        tree = gitops.snapshot_tree(repo, os.path.join(self.tmp, 'idx'))
        self.assertNotIn('src/a.o', [e[3] for e in gitops.ls_tree(repo, tree)])

    def test_reset_worktree_discards_everything_but_kept_files(self):
        repo = self.make_repo()
        head = git(repo, 'rev-parse', 'HEAD')
        wt = os.path.join(self.tmp, 'wt')
        gitops.worktree_add(repo, wt, head, 'test')
        write(wt, 'src/a.py', 'changed\n')
        write(wt, 'junk.txt', 'junk')
        write(wt, '.claude/settings.local.json', '{}')
        gitops.reset_worktree(wt, head, gitops.common_dir(repo), keep=paths_mod.FRAMEWORK_FILES)
        with open(os.path.join(wt, 'src/a.py')) as f:
            self.assertEqual(f.read(), 'a = 1\n')
        self.assertFalse(os.path.exists(os.path.join(wt, 'junk.txt')))
        self.assertTrue(os.path.exists(os.path.join(wt, '.claude/settings.local.json')))
        self.assertIn('locked', git(repo, 'worktree', 'list', '--porcelain'))
        gitops.worktree_remove(repo, wt)
        self.assertFalse(os.path.exists(wt))

    def test_merge_conflict_markers(self):
        repo = self.make_repo(files={'src/a.py': 'x = 0\n'})
        base = git(repo, 'rev-parse', 'HEAD')
        write(repo, 'src/a.py', 'x = 1\n')
        git(repo, 'commit', '-qam', 'one')
        one = git(repo, 'rev-parse', 'HEAD')
        git(repo, 'checkout', '-q', base)
        write(repo, 'src/a.py', 'x = 2\n')
        git(repo, 'commit', '-qam', 'two')
        two = git(repo, 'rev-parse', 'HEAD')
        wt = os.path.join(self.tmp, 'wt')
        gitops.worktree_add(repo, wt, one, 'test')
        conflicts = gitops.merge_into(wt, two)
        self.assertEqual(conflicts, ['src/a.py'])
        with open(os.path.join(wt, 'src/a.py')) as f:
            self.assertIn('<<<<<<<', f.read())
        # Reset clears the merge state.
        gitops.reset_worktree(wt, one, gitops.common_dir(repo))
        self.assertEqual(git(wt, 'status', '--porcelain'), '')

    def test_create_ref_refuses_overwrite(self):
        repo = self.make_repo()
        head = git(repo, 'rev-parse', 'HEAD')
        gitops.create_ref(repo, 'refs/evolve/r/c000', head)
        with self.assertRaises(LoopkitError):
            gitops.create_ref(repo, 'refs/evolve/r/c000', head)

    def test_git_hooks_are_disabled(self):
        repo = self.make_repo()
        marker = os.path.join(self.tmp, 'hook-ran')
        write(repo, '.git/hooks/post-checkout', '#!/bin/sh\ntouch %s\n' % marker, mode=0o755)
        head = git(repo, 'rev-parse', 'HEAD')
        gitops.worktree_add(repo, os.path.join(self.tmp, 'wt'), head, 'test')
        self.assertFalse(os.path.exists(marker))


class SubmoduleTest(TempDirTest):
    def test_stale_gitmodules_entry_and_uninitialized_submodule(self):
        repo = self.make_repo()
        write(repo, '.gitmodules', '[submodule "gone"]\n\tpath = vendor/gone\n\turl = https://example.invalid/gone.git\n')
        git(repo, 'add', '.gitmodules')
        git(repo, 'commit', '-qm', 'stale submodule entry')
        head = git(repo, 'rev-parse', 'HEAD')
        git(repo, 'update-index', '--add', '--cacheinfo', '160000,%s,vendor/lib' % head)
        write(repo, '.gitmodules', '[submodule "gone"]\n\tpath = vendor/gone\n\turl = https://example.invalid/gone.git\n'
              '[submodule "lib"]\n\tpath = vendor/lib\n\turl = https://example.invalid/lib.git\n')
        git(repo, 'add', '.gitmodules')
        git(repo, 'commit', '-qm', 'uninitialized submodule')
        wt = os.path.join(self.tmp, 'wt')
        gitops.worktree_add(repo, wt, git(repo, 'rev-parse', 'HEAD'), 'test')
        self.assertEqual(gitops.gitlinks(wt), ['vendor/lib'])
        warnings = gitops.update_submodules(wt, gitops.common_dir(repo))
        self.assertEqual(warnings, ['submodule vendor/lib is not initialized in your checkout; it stays empty in the run'])


class ScopeTest(TempDirTest):
    def test_glob(self):
        allowed = scope.Scope(['src/**/*.cu', '**/CMakeLists.txt', 'cmake/', '*.h'], ['src/third_party/**'])
        for path in ('src/a.cu', 'src/x/y/a.cu', 'CMakeLists.txt', 'src/CMakeLists.txt', 'cmake/x.cmake', 'inc/a.h'):
            self.assertTrue(allowed.allows(path), path)
        for path in ('src/a.cpp', 'src/third_party/a.cu', 'other/a.cu', 'cmakefoo/x'):
            self.assertFalse(allowed.allows(path), path)

    def test_protected(self):
        for path in ('.loopkit/config.json', '.claude/settings.json', '.gitignore', 'src/.gitignore', '.gitattributes', '.gitmodules'):
            self.assertTrue(scope.is_protected(path), path)
        self.assertFalse(scope.is_protected('src/.claude/x'))

    def test_symlink_escape(self):
        self.assertTrue(scope.symlink_escapes('src/a', '/etc/passwd'))
        self.assertTrue(scope.symlink_escapes('src/a', '../../x'))
        self.assertFalse(scope.symlink_escapes('src/a', '../README.md'))

    def _tree(self, repo):
        return gitops.snapshot_tree(repo, os.path.join(self.tmp, 'idx'))

    def test_check(self):
        repo = self.make_repo(files={'src/a.py': 'a\n', 'src/vendor/v.py': 'v\n', 'README.md': 'r\n', '.gitignore': ''})
        base = self._tree(repo)
        allowed = scope.Scope(['src/**'], ['src/vendor/**'])
        write(repo, 'src/a.py', 'b\n')
        write(repo, 'src/new.py', 'n\n')
        self.assertEqual(scope.check(repo, base, self._tree(repo), allowed)['violations'], [])
        write(repo, 'README.md', 'changed\n')
        write(repo, 'src/vendor/v.py', 'changed\n')
        write(repo, '.gitignore', 'x\n')
        os.symlink('/etc/passwd', os.path.join(repo, 'src/link'))
        result = scope.check(repo, base, self._tree(repo), allowed)
        self.assertEqual(sorted(result['violations']), [
            ('.gitignore', 'protected'), ('README.md', 'out_of_scope'), ('src/link', 'symlink'),
            ('src/vendor/v.py', 'out_of_scope')])
        self.assertIn('src/new.py', result['files'])

    def test_deleting_out_of_scope_file_is_a_violation(self):
        repo = self.make_repo()
        base = self._tree(repo)
        os.unlink(os.path.join(repo, 'README.md'))
        result = scope.check(repo, base, self._tree(repo), scope.Scope(['src/**']))
        self.assertEqual(result['violations'], [('README.md', 'out_of_scope')])

    def test_conflicts_are_exempt_but_must_be_resolved(self):
        repo = self.make_repo(files={'README.md': '<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n'})
        base = self._tree(repo)
        allowed = scope.Scope(['src/**'])
        result = scope.check(repo, base, self._tree(repo), allowed, conflicts=['README.md'])
        self.assertEqual(result['violations'], [('README.md', 'unresolved_conflict')])
        write(repo, 'README.md', 'a\nb\n')
        result = scope.check(repo, base, self._tree(repo), allowed, conflicts=['README.md'])
        self.assertEqual(result['violations'], [])

    def test_protected_conflict_must_take_a_parent_version(self):
        repo = self.make_repo(files={'.gitignore': 'a\n'})
        parent_a = self._tree(repo)
        write(repo, '.gitignore', 'b\n')
        parent_b = self._tree(repo)
        write(repo, '.gitignore', '<<<<<<< x\na\n=======\nb\n>>>>>>> y\n')
        base = self._tree(repo)
        write(repo, '.gitignore', 'b\n')
        ok = scope.check(repo, base, self._tree(repo), scope.Scope(['src/**']), ['.gitignore'], [parent_a, parent_b])
        self.assertEqual(ok['violations'], [])
        write(repo, '.gitignore', 'c\n')
        bad = scope.check(repo, base, self._tree(repo), scope.Scope(['src/**']), ['.gitignore'], [parent_a, parent_b])
        self.assertEqual(bad['violations'], [('.gitignore', 'protected')])

    def test_submodule_change_is_a_violation(self):
        repo = self.make_repo()
        base = self._tree(repo)
        head = git(repo, 'rev-parse', 'HEAD')
        git(repo, 'update-index', '--add', '--cacheinfo', '160000,%s,src/sub' % head)
        tree = git(repo, 'write-tree')
        result = scope.check(repo, base, tree, scope.Scope(['src/**']))
        self.assertEqual(result['violations'], [('src/sub', 'submodule')])


class AssetsTest(TempDirTest):
    def test_snapshot_and_verify(self):
        repo = self.make_repo(files={'.loopkit/score.py': 'print(1)\n', 'bench/a.txt': 'a', 'bench/b.txt': 'b'})
        write(repo, 'bench/untracked.txt', 'u')
        outside = os.path.join(self.tmp, 'external.dat')
        write(self.tmp, 'external.dat', 'ext')
        head = git(repo, 'rev-parse', 'HEAD')
        write(repo, '.loopkit/score.py', 'print(2)\n')  # uncommitted edits must not leak in
        dest = os.path.join(self.tmp, 'assets')
        sources, warnings = assets.snapshot(repo, head, ['.loopkit/score.py', 'bench/', 'bench/untracked.txt', outside], dest)
        with open(os.path.join(dest, '.loopkit/score.py')) as f:
            self.assertEqual(f.read(), 'print(1)\n')
        self.assertTrue(os.path.exists(os.path.join(dest, '_abs', outside.lstrip('/'))))
        self.assertTrue(os.path.exists(os.path.join(dest, 'bench/untracked.txt')))
        manifest_sha = sha256_file(os.path.join(dest, assets.MANIFEST_NAME))
        self.assertEqual(assets.verify(dest, manifest_sha), [])
        # Read-only: a plain write fails.
        with self.assertRaises(OSError):
            open(os.path.join(dest, 'bench/a.txt'), 'w')
        assets.make_writable(dest)
        write(dest, 'bench/a.txt', 'tampered')
        problems = assets.verify(dest, manifest_sha)
        self.assertEqual(problems, ['eval-assets changed: bench/a.txt'])

    def test_missing_asset(self):
        repo = self.make_repo()
        with self.assertRaises(LoopkitError):
            assets.snapshot(repo, 'HEAD', ['nope/'], os.path.join(self.tmp, 'assets'))

    def test_untracked_files_in_a_tracked_directory_are_copied(self):
        repo = self.make_repo(files={'.gitignore': 'bench/*.big\n', 'bench/small.txt': 's'})
        write(repo, 'bench/design.big', 'large benchmark')
        head = git(repo, 'rev-parse', 'HEAD')
        dest = os.path.join(self.tmp, 'assets')
        sources, warnings = assets.snapshot(repo, head, ['bench/'], dest)
        self.assertTrue(os.path.isfile(os.path.join(dest, 'bench/design.big')))
        self.assertEqual(sources[0]['untracked_files'], 1)
        self.assertTrue(any('untracked' in w for w in warnings))

    def test_missing(self):
        repo = self.make_repo(files={'bench/a.txt': 'a'})
        write(repo, 'local.txt', 'x')
        self.assertEqual(assets.missing(repo, ['bench/', 'bench/a.txt', 'local.txt', 'nope', '/no/such/file']),
                         ['nope', '/no/such/file'])


class RunFixture(TempDirTest):
    """A minimal run directory created by hand, without the CLI."""

    def make_run(self):
        self.repo = self.make_repo(files={'.loopkit/score.py': 'print(1)\n', 'src/a.py': 'a\n'})
        self.head = git(self.repo, 'rev-parse', 'HEAD')
        common = gitops.common_dir(self.repo)
        self.paths = paths_mod.RunPaths(os.path.join(paths_mod.repo_dir(common), 'r001'))
        os.makedirs(self.paths.queue)
        _, _ = assets.snapshot(self.repo, self.head, ['.loopkit/score.py'], self.paths.eval_assets)
        self.info = {
            'schema': 1, 'run': 'r001', 'created': 'now', 'c000': self.head, 'config': config(),
            'eval_assets': {'manifest_sha256': sha256_file(self.paths.manifest)},
            'git_baseline': integrity.baseline(self.repo, common),
        }
        write_json_atomic(self.paths.run_json, self.info)
        self.ledger = ledger_mod.Ledger(self.paths.ledger, 'r001')
        self.ledger.event('run_created', run_json_sha256=sha256_file(self.paths.run_json))
        gitops.create_ref(self.repo, 'refs/evolve/r001/c000', self.head)
        self.ledger.append('candidate', {'id': 'c000', 'by': 'baseline', 'sha': self.head, 'status': 'BASELINE',
                                         'parents': [], 'objectives': {'hpwl': 1.0, 'runtime': 1.0}})

    def check(self, fix=True):
        records, problems = self.ledger.read()
        return integrity.check(self.paths, self.info, records, problems, self.repo, fix=fix)


class QueueTest(RunFixture):
    def test_request_eval_pins_and_dedupes(self):
        self.make_run()
        write(self.repo, 'src/a.py', 'b\n')
        git(self.repo, 'commit', '-qam', 'human')
        sha = git(self.repo, 'rev-parse', 'HEAD')
        request = queue_mod.request_eval(self.paths, self.repo, 'HEAD', note='try')
        self.assertEqual((request['n'], request['sha'], request['ref']), (1, sha, 'refs/heads/main'))
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/evolve/r001/req/1'), sha)
        with self.assertRaises(LoopkitError):
            queue_mod.request_eval(self.paths, self.repo, sha)
        with self.assertRaises(LoopkitError):
            queue_mod.request_eval(self.paths, self.repo, self.head)  # already c000
        self.assertEqual([r['n'] for r in queue_mod.pending(self.paths)], [1])
        self.assertEqual(self.check().problems, [])

    def test_promote_validation(self):
        self.make_run()
        with self.assertRaises(LoopkitError):
            queue_mod.request_promote(self.paths, 'c000')
        self.ledger.append('candidate', {'id': 'h001', 'by': 'human', 'sha': self.head, 'status': 'FAILED'})
        with self.assertRaises(LoopkitError):
            queue_mod.request_promote(self.paths, 'h001')


class IntegrityTest(RunFixture):
    def test_clean_run(self):
        self.make_run()
        report = self.check()
        self.assertEqual((report.problems, report.warnings, report.fixed), ([], {}, []))

    def test_tampered_assets_and_run_json(self):
        self.make_run()
        assets.make_writable(self.paths.eval_assets)
        write(self.paths.eval_assets, '.loopkit/score.py', 'print("hacked")\n')
        info = dict(self.info, c000='0' * 40)
        write_json_atomic(self.paths.run_json, info)
        problems = self.check().problems
        self.assertIn('run.json was modified', problems)
        self.assertIn('eval-assets changed: .loopkit/score.py', problems)

    def test_moved_missing_and_unexpected_refs(self):
        self.make_run()
        write(self.repo, 'src/a.py', 'b\n')
        git(self.repo, 'commit', '-qam', 'x')
        other = git(self.repo, 'rev-parse', 'HEAD')
        git(self.repo, 'update-ref', 'refs/evolve/r001/c000', other)
        git(self.repo, 'update-ref', 'refs/evolve/r001/c042', other)
        problems = self.check().problems
        self.assertTrue(any('ref c000 points to' in p for p in problems), problems)
        self.assertIn('unexpected ref refs/evolve/r001/c042', problems)
        git(self.repo, 'update-ref', '-d', 'refs/evolve/r001/c000')
        self.assertIn('ref for c000 is missing', self.check().problems)

    def test_dangling_next_ref_is_removed(self):
        self.make_run()
        git(self.repo, 'update-ref', 'refs/evolve/r001/c001', self.head)
        report = self.check()
        self.assertEqual(report.problems, [])
        self.assertEqual(report.fixed, ['removed dangling ref c001'])
        self.assertEqual(gitops.list_refs(self.repo, 'refs/evolve/r001/c001'), {})

    def test_queue_and_request_refs(self):
        self.make_run()
        write_json_atomic(os.path.join(self.paths.queue, 'req-0001.json'), {'kind': 'eval', 'sha': self.head})
        # Caught between request-eval's file write and its ref: re-pinned, not a problem.
        report = self.check()
        self.assertEqual((report.problems, report.fixed), ([], ['pinned queued request 1']))
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/evolve/r001/req/1'), self.head)
        git(self.repo, 'update-ref', '-d', 'refs/evolve/r001/req/1')
        write_json_atomic(os.path.join(self.paths.queue, 'req-0001.json'), {'kind': 'eval', 'sha': '1' * 40})
        self.assertIn('queued request 1 has no pinning ref', self.check().problems)
        os.unlink(os.path.join(self.paths.queue, 'req-0001.json'))
        git(self.repo, 'update-ref', 'refs/evolve/r001/req/7', self.head)
        report = self.check()
        self.assertEqual((report.problems, report.fixed), ([], ['removed stale request ref 7']))

    def test_interrupted_request_cleanup_is_finished(self):
        self.make_run()
        git(self.repo, 'update-ref', 'refs/evolve/r001/req/1', self.head)
        write_json_atomic(os.path.join(self.paths.queue, 'req-0001.json'), {'kind': 'eval', 'sha': self.head})
        git(self.repo, 'update-ref', 'refs/evolve/r001/h001', self.head)
        self.ledger.append('candidate', {'id': 'h001', 'by': 'human', 'sha': self.head, 'status': 'OBSERVED', 'request': 1})
        report = self.check()
        self.assertEqual(report.problems, [])
        self.assertEqual(report.fixed, ['finished cleanup of request 1'])
        self.assertTrue(os.path.exists(os.path.join(self.paths.queue_done, 'req-0001.json')))

    def test_dangerous_config_stops_other_config_warns(self):
        self.make_run()
        git(self.repo, 'config', 'user.name', 'Someone')
        report = self.check()
        self.assertEqual(report.problems, [])
        self.assertEqual(list(report.warnings), ['local_config_sha256'])
        self.ledger.event('integrity_warning', hashes=report.warnings)
        self.assertEqual(self.check().warnings, {})
        git(self.repo, 'config', 'filter.evil.smudge', 'touch /tmp/x')
        problems = self.check().problems
        self.assertEqual(problems, ['git config that can run code changed: +filter.evil.smudge'])

    def test_hooks_change_warns(self):
        self.make_run()
        write(self.repo, '.git/hooks/pre-commit', '#!/bin/sh\n', mode=0o755)
        self.assertEqual(list(self.check().warnings), ['hooks_sha256'])

    def test_ledger_tamper(self):
        self.make_run()
        with open(self.paths.ledger) as f:
            text = f.read()
        with open(self.paths.ledger, 'w') as f:
            f.write(text.replace('BASELINE', 'KEPT'))
        self.assertIn('ledger last line was modified', self.check().problems)

    def test_ledger_truncation(self):
        self.make_run()
        with open(self.paths.ledger) as f:
            lines = f.readlines()
        with open(self.paths.ledger, 'w') as f:
            f.writelines(lines[:-1])
        self.assertTrue(any('truncated' in p for p in self.check().problems))
