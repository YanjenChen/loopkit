---
description: Collect a loopkit debug report - run state, integrity, recent scoring jobs, logs and environment - and diagnose it.
argument-hint: "[--run NAME] [--transcripts] [--jobs N] [--out DIR]"
---

Run `loopkit report $ARGUMENTS` with the Bash tool and a 600000 ms timeout. It prints a
`REPORT <path>` line (the Markdown report) and an `ARCHIVE <path>` line (a `.tar.gz` with the
report and the copied files).

Then read the report and tell the user, in their language and briefly:

1. What state the run is in: batch progress, the last iterations, anything in progress.
2. What looks wrong, with evidence from the report: integrity problems, failed or stuck
   scoring jobs (their `worker.txt` and `score.txt` tails), a pending evaluation with a dead
   worker, uncommitted leftovers in the worktrees, missing settings or hook blocks.
3. The most likely cause and what to do about it. When it looks like a loopkit bug rather
   than a problem with the repository or the score script, say so and name the command or
   step that misbehaved.
4. Where the report and the archive are. The archive is what to attach to a GitHub issue;
   it holds paths, code excerpts and logs, so the user should look through it before sharing
   it publicly. Session transcripts are included only with `--transcripts`.

Do not change anything while diagnosing: no loopkit commands that write, no edits.
