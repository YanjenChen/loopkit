# loopkit

loopkit is a Claude Code plugin that lets `/goal` or `/loop` optimize the code of any repository, over and over. Each iteration analyzes the current code and its evolution history, proposes one change, implements it and scores it with your score script. Only versions that actually get better stay on the front.

It is built for long, unattended runs, such as optimizing a GPU placer (C++/CUDA/LibTorch/Python, CMake) overnight. It also works for coursework or any other project you can score.

- **Fixed-format iterations**: `/loopkit:iter` runs each iteration. The framework fixes its head and tail; the workflow in the middle is a single agent, or several analysts plus a decider.
- **Multiple objectives**: objectives are compared within a tolerance, and a Pareto front is maintained.
- **Keep developing alongside**: a run works in its own worktrees and data directory and never touches your working tree. Your own commits can be sent in for evaluation, and you decide whether they join the evolution.
- **Scoring integrity**: the score script and benchmarks are frozen into a snapshot when the run is created. A scope check, permission rules, hooks and tamper detection keep the agent away from what it must not change.
- **Ledger and monitor**: every result goes into an append-only, hash-chained ledger. The monitor is a Claude artifact that shows the front, the evolve tree and the details of every candidate.

## Requirements

- Claude Code 2.1.91 or newer (the plugin's `bin/` is added to the Bash PATH)
- git 2.31 or newer, Python 3.8 or newer; standard library only
- Linux or WSL; no Docker needed

## Installation

In Claude Code (for example the VS Code extension), run `/plugins`, add the marketplace in the Marketplaces tab, then install `loopkit` at user scope:

```
/plugin marketplace add YanjenChen/loopkit
/plugin install loopkit@loopkit
```

- **Developing loopkit itself**: add the local path (the root of this repository) instead: `/plugin marketplace add /path/to/loopkit`. Claude Code copies the plugin into `~/.claude/plugins/cache/` on install, so after changing `claude-plugin/`, update it:

  ```
  claude plugin marketplace update loopkit
  claude plugin update loopkit@loopkit      # add --scope project for a project-scope install
  ```

  Then restart the session.
- **Installed from GitHub**: after a push, run the same two commands.
- **Scope**: user scope is recommended. Project scope works too; the run's worktree enables loopkit by itself.

Runs live in `~/loopkit-runs/<repo>/<run>/` (set `LOOPKIT_DATA_DIR` to put them elsewhere), not in the plugin's data directory, so updating, reinstalling or uninstalling loopkit never deletes a run. To delete a run, use `loopkit run remove <name> --yes`.

## A run, step by step

1. **Set up**: in your repository, run `/loopkit:init <what to optimize>`, for example `/loopkit:init optimize hpwl and runtime`. init analyzes the repository, questions your request, generates the loopkit config (`.loopkit/config.json`) and the score script, trial-runs it, and publishes the monitor for you to confirm. Finally it creates the run and scores the baseline (c000).
2. **Start**: with the command init prints, open the run's agent worktree in a new VS Code window, start Claude Code there in auto mode, and paste one of the prompts init prints:

   ```
   /goal Run /loopkit:iter repeatedly (stop conditions: at most 20 iterations, 5 iterations in a row without improvement, or hpwl below 1.0e6; loopkit decides) until the output shows LOOPKIT-STOP. A single REVERTED or FAILED iteration does not mean the goal is impossible.
   /loop /loopkit:iter (stop conditions: at most 50 iterations, or 8 in a row without improvement; loopkit decides; stop the loop when the output shows LOOPKIT-STOP)
   ```

   loopkit decides the stop conditions: a number of iterations, a plateau (N iterations in a row without KEPT), and objective thresholds. When one is met, the framework prints `LOOPKIT-STOP`, and both `/goal` and `/loop` stop. To run another batch later, start again with a different prompt.

   To stop early, run `/loopkit:stop` in your own session: the run prints `LOOPKIT-STOP` within seconds and ends. You can also press Esc in the run's window to interrupt the current iteration and then enter `/goal clear`; a `/goal clear` typed while the agent is working only reaches it as an ordinary message.
3. **Keep developing**: carry on in your own working tree. To have a commit evaluated, run `/loopkit:request-eval <commit>` in your own session; it is scored at the head of the next iteration as `hNNN` and is only observed by default. To let it join the evolution, run `/loopkit:promote <hNNN>`.
4. **Watch**: follow progress on the monitor, or run `/loopkit:status`.
5. **Take results back**: `/loopkit:adopt <id>` creates a branch in your repository at that candidate, for you to review and merge.

To change the objectives or the scoring later, run `/loopkit:init` again. It creates a new run; the old run's ledger and refs are kept.

## One iteration

| Stage | What happens |
|---|---|
| Head (fixed) | integrity check → register and check the stop conditions → process your requests → print the evolve state → choose the parent (one, or two to merge) |
| Middle (configured) | single: the agent analyzes and picks one change itself. multi: read-only analysts propose in parallel, a decider judges them blind and picks one, and an optional critic challenges it |
| Tail (fixed) | implement → precheck (quick compile, self-repair) → score → record (one ITER line) → update the monitor |

Each iteration ends with one ITER line, for example:

```
ITER 7/20 | c012<-c009 | hpwl 1.0231e6 (-0.80% better) | runtime 41.200s (+1.2% same) | KEPT | front=3
```

## Commands

Slash commands for your own sessions:

| Command | What it does |
|---|---|
| `/loopkit:init <request>` | set up and create a run |
| `/loopkit:request-eval <commit>` | send one of your commits for evaluation |
| `/loopkit:promote <hNNN>` | let a human candidate join the evolution |
| `/loopkit:adopt <id>` | create a branch in your repository at a candidate |
| `/loopkit:status` | the state of a run |
| `/loopkit:stop` | end the current batch early |
| `/loopkit:report` | collect a debug report and diagnose it; attach the report's `.tar.gz` to a GitHub issue |

`/loopkit:iter` is only run by `/goal` or `/loop` in a run session.

The framework command is a Python program, `loopkit`, which does every git and ledger write. Common ones are `loopkit summary`, `loopkit show <id> --log`, `loopkit lineage <id>`, `loopkit diff <a> <b>`, `loopkit status`, `loopkit run list` and `loopkit check`. For the full list, see `loopkit --help` or the [skill reference](claude-plugin/skills/loopkit/SKILL.md).

Inside Claude Code, `loopkit` is already on the PATH. To use it from an ordinary terminal, run `python3 <plugin path>/bin/loopkit shim` once; it creates a shortcut at `~/.local/bin/loopkit`.

## The score script

init generates the score script from your request, and it is frozen into a snapshot when the run is created. The framework runs it in a clean eval worktree and reads only the result JSON it writes:

```json
{"schema": 1, "status": "ok",
 "objectives": {"hpwl": 1.0231e6, "runtime": 41.2},
 "constraints": {"legal": {"pass": true, "value": 0, "detail": "0 overlaps"}},
 "extra": {"gpu_mem_mb": 5120}}
```

Building, repeated measurements and aggregation are up to the script. For the full contract and the isolation checklist, see [score-contract.md](claude-plugin/skills/loopkit/references/score-contract.md); for every config field, see [config.md](claude-plugin/skills/loopkit/references/config.md).

## Safety and scoring integrity

The agent in a run session can run any command, and there is no Docker, so loopkit aims to stop accidents and common misuse and to detect tampering, not to guarantee absolute security:

1. **Isolation**: a run works in its own agent worktree; scoring happens in a separate eval worktree, using the snapshot in the run directory.
2. **Scope check**: only the config's scope may change. `.loopkit/`, `.claude/`, `.gitignore`, `.gitattributes`, `.gitmodules`, submodules, and symlinks pointing outside the tree never may.
3. **Permission rules**: the run's worktree has rules that forbid editing your repository, the eval worktree, run data and `.git`.
4. **Hooks**: in a run session, git may only read, because writes belong to the framework. Commands that would touch protected locations, commands that cannot be parsed, background runs and the commands meant for you alone are blocked. The hooks act only in run sessions; your ordinary sessions are not affected.
5. **Tamper detection**: at the head of every iteration and before every scoring, loopkit checks the eval-assets snapshot, the ledger's hash chain, the candidate refs, the queue, and git configuration that could run code during a checkout. Any problem stops the run.

## Development

```
python3 -m unittest discover -s tests -p 'test_loopkit_*.py'   # unit tests
bash tests/test-run-e2e.sh                                      # the full flow on a toy repository
bash tests/test-hooks.sh                                        # hooks
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License and credits

MIT, see [LICENSE](LICENSE).

loopkit is a fork of [uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch) by Udit Goenka (MIT), which builds on [Andrej Karpathy's autoresearch](https://github.com/karpathy/autoresearch).
