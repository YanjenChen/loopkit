---
description: Set up a loopkit run for this repository - objectives, score script, config, monitor, worktrees and the scored baseline - so /goal or /loop can start.
argument-hint: "<what to optimize, e.g. 優化hpwl與runtime>"
---

# /loopkit:init

Set up everything a loopkit run needs, working in the user's repository. When you finish,
the user opens the run's agent worktree and starts `/goal` or `/loop` there.

The user's request: $ARGUMENTS

Talk to the user in the language they write in. Ask with AskUserQuestion only when the answer
changes the setup, and batch the questions. Invoke the `loopkit:loopkit` skill first: its
reference describes the config fields and the score script contract in detail.

Everything below happens in this repository. Only step 7 creates the run; before that, nothing
outside `.loopkit/` changes.

## 1. Input

Take from the request whatever it states, and propose the rest in step 3:
- objectives, with direction and tolerance
- constraints
- extra values to record
- the middle workflow: `single` (one agent does everything), or `multi` (named analysts, each
  with a focus, plus a decider with decision principles, and optionally a critic)
- the GPU to use

## 2. Analyze the repository

- Find the build, run and verification tools, the benchmarks, and the source directories that
  make sense as the scope.
- Write a codebase summary to `.loopkit/knowledge.md`: architecture, modules, dependencies,
  hot spots, build system. Analysts read it every iteration instead of exploring from
  scratch, so make it accurate and dense, and cite paths.
- Check the environment: `git --version` (2.31 or newer), `python3 --version` (3.8 or newer),
  and `nvidia-smi -L` when a GPU is involved.
- Interrogate the request before going on. Look for objectives that conflict without a stated
  priority, constraints with no existing checker, a scope that is too wide or too narrow,
  measurements that will be noisy, and anything that could make a candidate look better
  without being better. Ask the user about each problem you find.

## 3. Generate

- **`.loopkit/config.json`**, every field filled in. Propose what the user did not say:
  directions from names and code; tolerances from the trial runs in step 4; units; the
  constraints that existing checkers support (propose them, never add them silently);
  eval assets; deterministic switches in `score.env`; a compile-only precheck and its attempt
  limit; the scope (sources plus build files, excluding third-party code and benchmarks); the
  workflow; the GPU (suggest one the user does not develop on).
- In multi mode, expand each analyst the user described into a full persona in the config:
  `focus`, `questions` (what it must answer), `evidence` (what it must cite: file:line,
  profiling data, logs), `red_flags` (what to watch for). Set `"web": true` for a literature
  analyst.
- **`.loopkit/score.py`**, following the score script contract. The names in its output
  must match the config exactly.

## 4. Trial-run and check

- `loopkit config check` validates the config and flags dangerous commands in the score and
  precheck commands and the score script, such as `rm -rf`, `curl | sh` or writes to disk
  devices. Show every flagged item to the user.
- `loopkit trial --repeat 3` (Bash timeout 600000 ms) runs the score script on the current
  code. It checks that the JSON matches the contract and the names match the config, and it
  reports whether the constraints pass and how much each objective varies. Base the proposed
  tolerances on that spread. If the score takes longer than a few minutes, use `--repeat 1`
  and discuss noise with the user instead.
- Check the scoring isolation:
  - the score script runs with `python3 -I` and never imports candidate code;
  - it recomputes objectives and constraints from the candidate's outputs, with checkers
    built from the snapshot, instead of trusting numbers the candidate prints;
  - it builds out of tree into `$LOOPKIT_BUILD_DIR`, and pins the build options that affect
    correctness (deterministic switches, assertions, fast-math and similar) on the command
    line, because the scope includes the build files.
- Check that in-tree build products and caches are ignored by `.gitignore`. Otherwise they
  end up in candidates and trip the scope check.

## 5. Monitor

`loopkit monitor html --sample --out <scratch dir>/monitor.html` writes the monitor page,
shaped by the config (a metric-by-iteration chart for one objective, a Pareto front for
several), with clearly labeled sample data in every block, human candidates included.
Publish that file with the Artifact tool, using the icon and `capabilities` the command
prints (they let the page read its database, and only editors write to it). Note the URL.
The user judges the layout from the sample; the sample disappears once the run pushes its
first records.

## 6. Confirm

Show the user, item by item: the config (summarized, then the full file on request), the
score script, the trial results, the knowledge summary, and the monitor URL. Adjust and
repeat steps 3 to 5 until the user confirms everything.

## 7. Create the run

```
loopkit run create --gpu <gpu> --knowledge .loopkit/knowledge.md --monitor-url <url>
```

Run it with the Bash timeout at 600000 ms. Add `--allow-danger` only when the user confirmed
the flagged commands in step 4. `run create`:
1. commits `.loopkit/` on the current branch; that commit is c000;
2. creates the run directory, the agent and eval worktrees at c000, and the build
   directories;
3. snapshots the eval assets;
4. scores c000 in the eval worktree (PENDING: run `loopkit --run <name> wait`). c000 must pass
   every constraint;
5. writes the ledger and the c000 ref;
6. writes the agent worktree's permission rules, GPU environment and run marker.

Then push c000 to the monitor, which replaces its sample data:
`loopkit monitor push --run <name>`, the Artifact `write_db` call as described in
`/loopkit:iter`, and `loopkit monitor ack <seq> --run <name>`.

## 8. Next steps

`run create` prints them. Show them to the user:
- open the agent worktree in a new VS Code window (`code <agent worktree>`) and start Claude
  Code there in auto mode;
- paste the `/goal` or `/loop` prompt and fill in the stop conditions;
- from their own sessions: `/loopkit:request-eval <commit>` to have a commit evaluated,
  `/loopkit:promote <hNNN>` to let it join the evolution, `/loopkit:status`,
  `/loopkit:stop` to end the running batch early, and `/loopkit:adopt <id>` to get a
  candidate back as a branch.

To change objectives or the scoring later, run `/loopkit:init` again: that creates a new run,
because scores from different scoring setups cannot be compared. Old runs keep their ledger
and refs.
