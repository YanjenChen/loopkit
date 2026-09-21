---
name: loopkit
description: Reference for loopkit, the framework that evolves a repository's code with /goal or /loop running /loopkit:iter. Use it to look up the loopkit config and score-script formats, the `loopkit` CLI, where a run keeps its files, how candidates, the Pareto front and stop conditions work, and how to troubleshoot a run.
---

# loopkit

loopkit optimizes the code of a repository in a loop. `/loopkit:init` sets up a **run**; the
user then starts `/goal` or `/loop` in the run's agent worktree, and each turn runs one
`/loopkit:iter`:

- **head** (fixed): integrity check, stop conditions, the user's queued requests, evolve
  state, parent choice;
- **middle** (configured): one agent, or analysts plus a decider (plus a critic), choose one
  idea;
- **tail** (fixed): implement, precheck, evaluate, record, update the monitor.

The framework (`loopkit` CLI) does every git and ledger write; the agent only edits files.

## Terms

- **candidate**: a scored code version. Agent candidates are c000 (baseline), c001, ...;
  commits the user sends in are human candidates h001, ....
- **objective**: a value to optimize, with a direction and a tolerance; **constraint**: a
  pass/fail condition; **extra**: recorded only.
- **front**: the constraint-passing candidates that no other candidate dominates. A new
  candidate is KEPT when it beats every front member in at least one objective by more than
  the tolerance; otherwise REVERTED. FAILED means it failed a constraint, the scope check, or
  the score script. Human candidates are OBSERVED until the user promotes them.
- **batch**: one /goal or /loop execution, with its own stop conditions.
- **ledger**: the append-only, hash-chained record of the run; the only source of truth.

## The CLI

`loopkit` is on the Bash PATH while the plugin is enabled. If it is not, use
`python3 ${CLAUDE_PLUGIN_ROOT}/bin/loopkit`. Scoring can outlast the Bash limit: commands that
score (`run create`, `queue`, `evaluate`, `wait`) wait up to about 9 minutes and then print
`PENDING`; run them with a 600000 ms timeout and continue with `loopkit wait`.

| Command | Who | What |
|---|---|---|
| `config check`, `trial [--repeat N]` | init | validate the config; run the score script on the working tree |
| `monitor html [--sample] --out F` | init | write the monitor page |
| `run create [--gpu G] [--knowledge F] [--monitor-url U]` | init | create the run and score c000 |
| `batch start --text T --conditions J` | run session | integrity check, register the batch, stop check |
| `queue` | run session | score queued human commits, apply promotions |
| `summary [--full]` | run session, analysts | evolve state and pattern analysis |
| `checkout [A [B]]` | run session | reset the agent worktree to parent A, optionally merge B |
| `precheck` | run session | compile-only build of the agent worktree |
| `evaluate`, `wait` | run session | snapshot, scope check, score; keep waiting |
| `record --idea --proposed-by --learned` | run session | commit the candidate to the ledger, print ITER |
| `monitor push`, `monitor ack SEQ` | run session | documents for the monitor, and marking them pushed |
| `export [--since N / --pending]` | anyone | the ledger with derived fields, as JSON |
| `show ID [--log]`, `lineage ID`, `diff A B` | anyone | inspect candidates |
| `request-eval COMMIT`, `promote hNNN`, `adopt ID` | the user only | human candidates; get a candidate back as a branch |
| `status`, `run list`, `run remove NAME --yes`, `check` | the user | inspect, clean up, verify integrity |

`--run NAME` selects a run; by default a command uses the run whose agent worktree it runs
in, or the repository's newest run.

## Where things are

A run lives in `${CLAUDE_PLUGIN_DATA}/<repo>/<run>/`: `run.json` (frozen setup), `ledger.jsonl`,
`queue/`, `eval-assets/` (the score script and benchmark snapshot), `agent/` and `eval/`
(worktrees), `build/` and `agent-build/`, `knowledge.md`, `artifacts/<id>/` (score logs and
results) and `work/` (the iteration in progress). The repository's `.git` holds only the
candidate commits and `refs/evolve/<run>/...`. Uninstalling the plugin deletes
`${CLAUDE_PLUGIN_DATA}`, and every run with it.

## References

- Config format, field by field, and how init fills gaps:
  `${CLAUDE_PLUGIN_ROOT}/skills/loopkit/references/config.md`
- Score script contract, isolation checklist and a template:
  `${CLAUDE_PLUGIN_ROOT}/skills/loopkit/references/score-contract.md`
- Analyst persona template, proposal format and the learned-note format:
  `${CLAUDE_PLUGIN_ROOT}/skills/loopkit/references/analysis.md`
- The monitor page and how records reach it:
  `${CLAUDE_PLUGIN_ROOT}/skills/loopkit/references/monitor.md`

## Troubleshooting

- `LOOPKIT-STOP | integrity: ...`: something changed that the run depends on: the
  eval-assets snapshot, the ledger, a candidate ref, a queued request, or git configuration
  that can run code. The run stays stopped. `loopkit check` shows the current state. Create a
  new run once the cause is understood.
- `c000 failed`: the score script failed on the baseline or a constraint failed. Fix the
  score script or the config, `loopkit run remove <name> --yes`, and create the run again.
- A candidate FAILED with `scope: ...`: the agent changed a file outside the scope, a
  protected file (`.loopkit/`, `.claude/`, `.gitignore`, `.gitattributes`, `.gitmodules`), a
  submodule, or added a symlink that points outside the tree.
- A candidate FAILED with `invalid_result: ...`: the score script exited non-zero, wrote no
  result, or wrote one that does not match the contract. `loopkit show <id> --log` shows its
  log.
