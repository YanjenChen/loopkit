# Contributing to loopkit

## Setup

1. Clone this repository.
2. In Claude Code, run `/plugins`, add the local path (the repository root) in the Marketplaces tab, and install `loopkit` at user scope.
3. Installing copies the plugin into `~/.claude/plugins/cache/`. After changing and committing `claude-plugin/`, bump the version in `plugin.json` and `marketplace.json`, run `claude plugin marketplace update loopkit` and `claude plugin update loopkit@loopkit`, and restart the session.

loopkit needs git 2.31 or newer and Python 3.8 or newer. The framework and the hooks use only the Python standard library; keep it that way, and keep the syntax 3.8-compatible.

## Layout

```
.claude-plugin/marketplace.json   the marketplace
claude-plugin/
  .claude-plugin/plugin.json      the plugin
  bin/loopkit                     the CLI entry point (Claude Code adds bin/ to the Bash PATH)
  loopkit/                        the framework: ledger, git plumbing, scope, front, scoring, reports, monitor
  commands/                       /loopkit:init, iter, request-eval, promote, adopt, status, stop, report
  agents/                         analyst, analyst-web, decider, critic (loopkit:<name>)
  skills/loopkit/                 reference: config, score script contract, analysis templates, monitor
  monitor/monitor.html            the monitor page template
  hooks/                          hooks that act only in run sessions
tests/
  test_loopkit_core.py            unit tests of the pure modules
  test_loopkit_git.py             modules that use git (snapshots, scope, assets, queue, integrity)
  test-run-e2e.sh                 the full flow on a toy repository
  test-hooks.sh                   hooks
```

## Design rules

- **The ledger is the only source of truth.** Every state must be derivable from the ledger; `work/` holds only the transient state of the iteration in progress.
- **Only the framework writes git and the ledger.** The agent only edits files. New write operations go into `loopkit/`; command files never teach the agent to run git commands that write.
- **Hooks act only in run sessions.** The plugin is installed at user scope, so its hooks run in every session the user has. When a hook finds it is not in a run session, it must allow the call and print nothing.
- **Output for the agent is readable and greppable.** The formats of the ITER, HUMAN, RESULT, PENDING and `LOOPKIT-STOP` lines are an interface: change the command files and the tests with them.

## Tests

There is no CI. Run all three suites locally before sending a change:

```
python3 -m unittest discover -s tests -p 'test_loopkit_*.py'
bash tests/test-run-e2e.sh
bash tests/test-hooks.sh
```

The command files, agents and monitor can only be verified in real Claude Code sessions: install from the local marketplace, run `/loopkit:init` in a toy repository (`bash tests/fixtures/make-toy-placer.sh <dir>` creates one), then open the agent worktree and run a few short `/goal` and `/loop` batches.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`.
