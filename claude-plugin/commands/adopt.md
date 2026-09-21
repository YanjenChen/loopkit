---
description: Create a branch in this repository at a loopkit candidate, to review and merge it yourself.
argument-hint: "<id> [--branch NAME] [--run NAME]"
---

Run `loopkit adopt $ARGUMENTS` with the Bash tool in this repository and show its output.
Without `--branch`, the branch is `loopkit/<run>/<id>`. Only the branch is created; the working
tree is not touched.

This command is for the user's own sessions. If this session is a loopkit run session (its
working directory is a run's agent worktree), do not run it.

Afterwards, offer to show the diff against the current branch (`git diff HEAD...<branch>`)
and the candidate's record (`loopkit show <id>`).
