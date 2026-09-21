---
description: Let an observed human candidate (hNNN) join the loopkit run's evolution, so it can enter the front and become a parent.
argument-hint: "<hNNN> [--run NAME]"
---

Run `loopkit promote $ARGUMENTS` with the Bash tool in this repository and show its output.

This command is for the user's own sessions. If this session is a loopkit run session (its
working directory is a run's agent worktree), do not run it.

Only OBSERVED candidates, which passed every constraint, can be promoted. The run applies the
promotion at the head of its next iteration.
