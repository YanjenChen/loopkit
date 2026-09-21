---
description: Queue one of your commits for evaluation by the loopkit run. It is scored at the head of the run's next iteration and shows up as hNNN on the monitor.
argument-hint: "<commit> [--note \"...\"] [--run NAME]"
---

Run `loopkit request-eval $ARGUMENTS` with the Bash tool in this repository and show its
output. With no commit given, use `HEAD`.

This command is for the user's own sessions. If this session is a loopkit run session (its
working directory is a run's agent worktree), do not run it: only the user sends commits to
a run.

The commit is evaluated with the run's snapshot of the score script and benchmarks, and is
only observed: it does not join the front and cannot be a parent until the user runs
`/loopkit:promote <hNNN>`.
