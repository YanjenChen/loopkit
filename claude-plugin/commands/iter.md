---
description: One loopkit iteration - fixed head, the configured workflow in the middle, fixed tail. Run it repeatedly with /goal or /loop from a run's agent worktree.
argument-hint: "(stop conditions come from the /goal or /loop prompt)"
---

# /loopkit:iter

You are the run session of a loopkit run, working in the run's agent worktree. One
invocation is exactly one iteration: head, middle, tail. Never start a second iteration in
the same invocation.

Rules for the whole iteration:

- Only the framework writes git state and run data. Use `loopkit` commands for that; never
  run git commands that write. A hook blocks such commands.
- Run every `loopkit` command with the Bash tool's timeout set to 600000 ms. Never use
  `run_in_background`, and never leave a background task or subagent running when the turn
  ends.
- Edit files only inside this worktree and inside the scope that `loopkit checkout` prints.
- A command that prints `LOOPKIT-STOP | <reason>` ends the iteration: go to **Stop**.
- If the user sends a message during the run asking to stop (for example `/goal clear`, or
  "stop" in any language), run `loopkit batch stop --reason "<their words>"` at once and go to
  **Stop**. A slash command typed while you are working reaches you as text, not as a
  command, so this is how it takes effect.
- A command that prints `PENDING` is still scoring: run `loopkit wait` (again, if it prints
  PENDING again) until it finishes.
- `loopkit` is on the PATH while the plugin is enabled. If it is not, the `loopkit:loopkit`
  skill gives its full path.

## Head (fixed)

1. **Register the batch and check the stop conditions.**

   ```
   loopkit batch start --text "<prompt>" --conditions '<json>'
   ```

   - `--text` is the /goal or /loop prompt that drives this session, verbatim and the same
     in every iteration.
   - `--conditions` is your reading of the stop conditions written in that prompt, as JSON:
     `{"max_iters": 20, "plateau": 5, "targets": [{"objective": "hpwl", "op": "<=", "value": 1.0e6}], "mode": "any"}`.
     `max_iters` is a number of iterations; `plateau` is "stop after N iterations in a row
     without improvement" (no KEPT); `targets` are objective thresholds. Leave out what the
     prompt does not say. `mode` is `all` only when the prompt asks for all targets together.
     Objective names and directions are on the OBJECTIVES line of `loopkit summary`.
   - It also runs the integrity checks. Compare the printed `stop:` line with the prompt.
2. **Queue.** `loopkit queue` evaluates up to two of the user's commits and applies their
   promotions. HUMAN and PROMOTE lines report the results.
3. **Evolve state.** `loopkit summary` prints the front, every candidate with its idea and
   learned note, the ideas already tried from each parent, and the pattern analysis. Read it
   all before choosing.
4. **Parents.** Check out one parent, or two to merge:
   - `loopkit checkout`: the default parent, the front member used least often as a parent.
   - `loopkit checkout <A>`: start from another eligible candidate: BASELINE, KEPT,
     REVERTED, or a promoted human candidate. REVERTED candidates are valid starting points
     for ideas that were promising but not yet better.
   - `loopkit checkout <A> <B>`: merge B into A, for example to combine two front members
     that improved different objectives. Conflicts are left in the files for the tail.

   Use the default unless the evolve state gives you a concrete reason not to.

## Middle (the workflow in `.loopkit/config.json`)

Read `workflow` in `.loopkit/config.json` in this worktree.

**Analysis data**, for both modes:
- The evolve state and patterns from `loopkit summary`.
- The codebase summary (`KNOWLEDGE` line of `loopkit summary`). It describes c000, so verify
  against the current code.
- `loopkit lineage <id>`, `loopkit diff <a> <b>`, `loopkit show <id> --log`. To find the step
  that caused a regression, bisect along the lineage with `loopkit diff`.

### single

Analyze the parent's code and history yourself and choose one idea. Write it down in the
proposal format below before you implement it. `--proposed-by` is `agent`.

### multi

1. **Analysts.** In a single message, launch one subagent per entry of `workflow.analysts`:
   `loopkit:analyst`, or `loopkit:analyst-web` when the entry has `"web": true`. Run them in
   the foreground. Each prompt contains the analyst's persona from the config (name, focus,
   questions, evidence, red flags), the parent id(s), and the objectives with directions and
   the constraints. Do not pass one analyst's output to another.
2. **Decider.** Collect every proposal. Shuffle them, label them P1, P2, ... in the shuffled
   order, and strip the analyst names. Launch one `loopkit:decider` subagent with the
   labeled proposals, the objectives and constraints, `workflow.decider.principles`, and the
   full `loopkit summary` output. Keep the label-to-analyst mapping to yourself.
3. **Critic** (only when `workflow.critic` is true). Launch `loopkit:critic` with the chosen
   idea, its plan, the parent id(s) and the `loopkit summary` output. Then launch
   `loopkit:decider` once more with its earlier decision and the critique, and take its final
   decision.
4. Map the chosen label(s) back to analyst names for `--proposed-by`; list several names,
   comma-separated, when the decider merged proposals.

### Proposal format

```
- idea: <one sentence>
- rationale and evidence: <file:line, log excerpts, numbers>
- expected impact: <per objective>
- confidence: <0-100%>
- risk: <constraints, determinism, build>
- files: <files to change>
```

### Rules for the middle

- The result is exactly one idea for the tail.
- The middle does not modify files.
- Every subagent runs in the foreground and has finished before the tail starts.

## Tail (fixed)

1. **Implement** the idea in this worktree. If checkout reported conflicts, resolve them
   too; a pure merge without other changes is allowed. Your own changes must stay inside the
   scope; edits made only to resolve conflicts are exempt.
2. **Precheck** (when the config has one): `loopkit precheck` builds without scoring. Fix
   compile errors one at a time, crashes and build errors first, then run it again. Stop at
   the attempt limit it prints and continue anyway: a failing build is recorded as FAILED
   rather than blocking the run.
3. **Evaluate**: `loopkit evaluate` snapshots the worktree, checks the scope and scores the
   snapshot. It prints the files that changed and then the RESULT. If the list of changed
   files shows something you did not mean to change (build products, stray files), fix that
   and run `loopkit evaluate` again. The RESULT is not recorded yet.
4. **Record**:

   ```
   loopkit record --idea "<the idea>" --proposed-by "<agent, or analyst names>" \
     --learned "hypothesis: <...>; result: <confirmed|disproven|inconclusive>; evidence: <...>"
   ```

   `--learned` is your reading of the result, at most 300 characters: what you expected,
   whether the result confirmed it, and the evidence. Later iterations read it to avoid
   dead ends. `record` prints the ITER line, and a `LOOPKIT-STOP` line when the batch is
   done.
5. **Monitor**: push the new records, as described below.
6. End your reply with the ITER line, followed by the `LOOPKIT-STOP` line when there is one.
   Do not start another iteration.

## Monitor

`loopkit monitor push` writes the monitor's new documents to JSON files and prints a
`MONITOR <url>` line and a `WRITES <json>` line (or says the run has no monitor). Call the
Artifact tool with `action: "write_db"`, `url`: that URL, `db_op: "batch"`, and `writes`:
that JSON list, unchanged. Then run the `loopkit monitor ack <seq>` command it printed. If it
also printed a `MORE` line, repeat push, write and ack until it no longer does.

The monitor only displays the ledger. If the push fails, continue: the next iteration
pushes the missing records again.

## Stop

When any loopkit command prints `LOOPKIT-STOP | <reason>` (a stop condition was met, the user
stopped the batch, or an integrity check failed):

1. Push to the monitor, as above.
2. Under `/goal`: end your reply with the `LOOPKIT-STOP | <reason>` line. That completes the
   goal.
3. Under `/loop`: stop the loop. In self-paced mode, call ScheduleWakeup with `stop: true`
   instead of scheduling another iteration. For a fixed-interval loop, delete its cron job
   with CronDelete. Then end your reply with the `LOOPKIT-STOP | <reason>` line.
4. Do not start another iteration, even if the prompt asks for more. To run another batch,
   the user starts a new /goal or /loop with a different prompt.
