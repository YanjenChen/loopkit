---
description: Stop the running loopkit batch now. The run session prints LOOPKIT-STOP within seconds, so /goal completes and /loop stops.
argument-hint: "[--reason \"...\"] [--run NAME]"
---

Run `loopkit batch stop $ARGUMENTS` with the Bash tool in this repository and show its output.
Without `--run`, it stops the repository's newest run.

The stop is recorded in the run's ledger. If the run session is waiting for a score, it
notices within a few seconds, discards the unrecorded iteration, prints `LOOPKIT-STOP`, and
ends: `/goal` sees the marker and completes, and `/loop` stops scheduling. To run again,
start a new `/goal` or `/loop` in the run's window with a different prompt.
