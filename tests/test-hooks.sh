#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/claude-plugin/hooks"

PASS=0
FAIL=0
TOTAL=0

# Test state
STDOUT=""
STDERR=""
EXIT_CODE=0

run_hook() {
  local hook="$1"
  local stdin_json="$2"
  local runner="${3:-python}"
  set +e
  local stdout_file stderr_file
  stdout_file=$(mktemp)
  stderr_file=$(mktemp)
  if [[ "$runner" == "runner" ]]; then
    printf '%s' "$stdin_json" | bash "$HOOKS_DIR/hook-runner.sh" "$HOOKS_DIR/$hook" >"$stdout_file" 2>"$stderr_file"
  else
    printf '%s' "$stdin_json" | python3 -B "$HOOKS_DIR/$hook" >"$stdout_file" 2>"$stderr_file"
  fi
  EXIT_CODE=$?
  STDOUT=$(cat "$stdout_file")
  STDERR=$(cat "$stderr_file")
  rm -f "$stdout_file" "$stderr_file"
  set -e
}

assert_exit() {
  local expected="$1"
  local test_name="$2"
  TOTAL=$((TOTAL + 1))
  if [[ "$EXIT_CODE" -eq "$expected" ]]; then
    printf '  PASS: %s\n' "$test_name"
    PASS=$((PASS + 1))
  else
    printf '  FAIL: %s (expected exit %d, got %d)\n' "$test_name" "$expected" "$EXIT_CODE"
    FAIL=$((FAIL + 1))
  fi
}

assert_contains() {
  local needle="$1"
  local test_name="$2"
  TOTAL=$((TOTAL + 1))
  if echo "$STDOUT" | grep -q "$needle"; then
    printf '  PASS: %s\n' "$test_name"
    PASS=$((PASS + 1))
  else
    printf '  FAIL: %s (stdout missing: %s)\n' "$test_name" "$needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_not_contains() {
  local needle="$1"
  local test_name="$2"
  TOTAL=$((TOTAL + 1))
  if ! echo "$STDOUT" | grep -q "$needle"; then
    printf '  PASS: %s\n' "$test_name"
    PASS=$((PASS + 1))
  else
    printf '  FAIL: %s (stdout should not contain: %s)\n' "$test_name" "$needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_stderr_contains() {
  local needle="$1"
  local test_name="$2"
  TOTAL=$((TOTAL + 1))
  if echo "$STDERR" | grep -q "$needle"; then
    printf '  PASS: %s\n' "$test_name"
    PASS=$((PASS + 1))
  else
    printf '  FAIL: %s (stderr missing: %s)\n' "$test_name" "$needle"
    FAIL=$((FAIL + 1))
  fi
}


# ============================================================================
# Fixture: a real loopkit run on a tiny repository
# ============================================================================
#
# Every loopkit hook acts only inside a run session. The suite creates a run
# with `loopkit run create` and plays its session: CLAUDE_PROJECT_DIR points at
# the agent worktree and the working directory is inside it.

FIXTURE="$(mktemp -d)"
cleanup_fixture() { chmod -R u+w "$FIXTURE" 2>/dev/null; rm -rf "$FIXTURE"; }
trap cleanup_fixture EXIT

export HOME="$FIXTURE/home"
export LOOPKIT_DATA_DIR="$FIXTURE/data"
export GIT_CONFIG_NOSYSTEM=1
mkdir -p "$HOME"
git config --global user.name "Hook Tests"
git config --global user.email "hook-tests@example.invalid"
git config --global init.defaultBranch main

USER_REPO="$FIXTURE/project"
mkdir -p "$USER_REPO/src" "$USER_REPO/.loopkit"
echo "print('hi')" > "$USER_REPO/src/app.py"
echo "# project" > "$USER_REPO/README.md"
cat > "$USER_REPO/.loopkit/score.py" <<'PY'
import json, os
json.dump({"schema": 1, "status": "ok", "objectives": {"t": 1.0}, "constraints": {}},
          open(os.environ["LOOPKIT_RESULT"], "w"))
PY
cat > "$USER_REPO/.loopkit/config.json" <<'JSON'
{"schema": 1, "objectives": [{"name": "t", "direction": "minimize", "tolerance": {"relative": 0.01}}],
 "score": {"command": ["python3", "-I", "${LOOPKIT_EVAL_DIR}/.loopkit/score.py"], "timeout_s": 30},
 "eval_assets": [".loopkit/score.py"], "scope": {"include": ["src/**"]}, "workflow": {"mode": "single"}}
JSON
git -C "$USER_REPO" init -q
git -C "$USER_REPO" add -A
git -C "$USER_REPO" commit -qm "project"
(cd "$USER_REPO" && python3 "$REPO_ROOT/claude-plugin/bin/loopkit" run create --gpu 2 >/dev/null)

RUN_DIR="$(find "$LOOPKIT_DATA_DIR" -mindepth 2 -maxdepth 2 -name r001 -type d)"
AGENT="$RUN_DIR/agent"
USER_COMMON="$USER_REPO/.git"
OUTSIDE="$FIXTURE/elsewhere"
mkdir -p "$OUTSIDE"

export CLAUDE_PROJECT_DIR="$AGENT"
cd "$AGENT"
# ============================================================================
# Test: scout-block.py
# ============================================================================

printf '\n--- Testing scout-block.py ---\n'

run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":"node_modules/express/index.js"}}'
assert_exit 2 "scout-block: blocks node_modules Read"

run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":"src/main.ts"}}'
assert_exit 0 "scout-block: allows normal file Read"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"npm test"}}'
assert_exit 0 "scout-block: allows build tool (npm)"

run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":".git/config"}}'
assert_exit 2 "scout-block: blocks .git access"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"echo '\''testing node_modules string'\''}}'
assert_exit 0 "scout-block: bash false positive prevention (string literal)"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"cat node_modules/foo/bar.js"}}'
assert_exit 2 "scout-block: blocks Bash with node_modules path arg"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"cat .git/HEAD"}}'
assert_exit 2 "scout-block: blocks Bash with .git path arg"

run_hook "scout-block.py" '{"tool_name":"Grep","tool_input":{"regex":"TODO","path":"src/"}}'
assert_exit 0 "scout-block: allows Grep on clean path"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"yarn build"}}'
assert_exit 0 "scout-block: allows build tool (yarn)"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"pnpm install"}}'
assert_exit 0 "scout-block: allows build tool (pnpm)"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"python script.py"}}'
assert_exit 0 "scout-block: allows build tool (python)"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"go run main.go"}}'
assert_exit 0 "scout-block: allows build tool (go)"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"rustc test.rs"}}'
assert_exit 0 "scout-block: allows build tool (rustc)"

run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":"dist/bundle.js"}}'
assert_exit 2 "scout-block: blocks dist directory"

run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":"coverage/index.html"}}'
assert_exit 2 "scout-block: blocks coverage directory"

run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":"build/output.o"}}'
assert_exit 2 "scout-block: blocks build directory"

run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":"node_modules\\express\\index.js"}}'
assert_exit 2 "scout-block: normalizes Windows path separators"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh deploy@prod cat node_modules/server.js"}}'
assert_exit 0 "scout-block: remote ssh path is not treated as local"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh deploy@prod true && cat node_modules/local.js"}}'
assert_exit 2 "scout-block: local operand after remote command is still inspected"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"scp deploy@prod:/srv/node_modules/app.js ./app.js"}}'
assert_exit 0 "scout-block: remote scp source is not treated as local"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh -i .ssh/id_rsa deploy@prod true"}}'
assert_exit 2 "scout-block: local SSH identity operand remains inspected"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh deploy@prod true | cat node_modules/local.js"}}'
assert_exit 2 "scout-block: local pipeline after remote command remains inspected"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"tsh ssh deploy@prod cat node_modules/server.js"}}'
assert_exit 0 "scout-block: remote tsh path is not treated as local"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"tsh ssh deploy@prod true && cat node_modules/local.js"}}'
assert_exit 2 "scout-block: local operand after tsh remains inspected"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"npm test && cat node_modules/private/file.js"}}'
assert_exit 2 "scout-block: build command does not exempt a later local command"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh -o IdentityFile=.ssh/id_rsa deploy@prod true"}}'
assert_exit 2 "scout-block: SSH IdentityFile option remains inspected"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh -F.ssh/config deploy@prod true"}}'
assert_exit 2 "scout-block: joined SSH config option remains inspected"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh -o '\''IdentityFile .ssh/id_rsa'\'' deploy@prod true"}}'
assert_exit 2 "scout-block: SSH space-style IdentityFile remains inspected"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"ssh -J jump -i .ssh/id_rsa deploy@prod true"}}'
assert_exit 2 "scout-block: SSH value options cannot hide later identity files"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"echo '\''node_modules/private/file.js'\''"}}'
assert_exit 0 "scout-block: quoted data is not mistaken for file access"

run_hook "scout-block.py" '{"tool_name":"Bash","tool_input":{"command":"grep '\''node_modules/pkg/index.js'\'' src/main.js"}}'
assert_exit 0 "scout-block: grep pattern text is not mistaken for file access"

run_hook "scout-block.py" '{"broken json' "runner"
assert_exit 0 "scout-block: malformed input fails open"
assert_contains "guardrail unavailable" "scout-block: malformed input emits visible diagnostic"
assert_not_contains "broken json" "scout-block: diagnostic redacts raw input"

run_hook "scout-block.py" '' "runner"
assert_exit 0 "scout-block: unavailable stdin fails open"
assert_contains "guardrail unavailable" "scout-block: unavailable stdin emits visible diagnostic"

LOOPKIT_DISABLE_SCOUT_BLOCK=1 run_hook "scout-block.py" '{"tool_name":"Read","tool_input":{"file_path":"node_modules/anything"}}'
assert_exit 0 "scout-block: disabled via env var"

# ============================================================================
# Test: privacy-block.py (run session)
# ============================================================================

printf '\n--- Testing privacy-block.py ---\n'

privacy_blocks() {  # privacy_blocks <json> <name>
  run_hook "privacy-block.py" "$1"
  assert_exit 2 "$2"
}

privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":".env"}}' "privacy-block: blocks .env read"
assert_stderr_contains "BLOCKED" "privacy-block: explains the block"
privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":"~/.ssh/id_rsa"}}' "privacy-block: blocks SSH key"
privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":"/home/someone/.ssh/config"}}' "privacy-block: blocks any file under .ssh/"
privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":"config/.ssh/known_hosts"}}' "privacy-block: blocks nested .ssh/ directory"
privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":"credentials.json"}}' "privacy-block: blocks credentials"
privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":"config/api_key.js"}}' "privacy-block: blocks api-key path"
privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":".env.local"}}' "privacy-block: blocks .env.local"
privacy_blocks '{"tool_name":"Edit","tool_input":{"file_path":"secret_key.pem"}}' "privacy-block: blocks pem edit"
privacy_blocks '{"tool_name":"MultiEdit","tool_input":{"file_path":"secrets/id_rsa"}}' "privacy-block: blocks MultiEdit of a key"
privacy_blocks '{"tool_name":"Read","tool_input":{"file_path":".aws/credentials"}}' "privacy-block: blocks cloud credentials"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"cat .env"}}' "privacy-block: blocks clear Bash read"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"echo ok; cat .env"}}' "privacy-block: compound command"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"grep token .env"}}' "privacy-block: grep of sensitive file"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"ssh deploy@prod true && cat .env"}}' "privacy-block: local read after remote command"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"sudo cat .env"}}' "privacy-block: sudo wrapper"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"env MODE=check command cat .env"}}' "privacy-block: env/command wrappers"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"scp .env deploy@prod:/tmp/.env"}}' "privacy-block: scp of a local secret"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"cp credentials.json /tmp/config-copy"}}' "privacy-block: copy"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"sh -c '\''cat credentials.json'\''"}}' "privacy-block: nested shell"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"source credentials.json"}}' "privacy-block: source"
privacy_blocks '{"tool_name":"Bash","tool_input":{"command":"(cat credentials.json)"}}' "privacy-block: subshell"

run_hook "privacy-block.py" '{"tool_name":"Read","tool_input":{"file_path":".env.example"}}'
assert_exit 0 "privacy-block: allows .env.example"
run_hook "privacy-block.py" '{"tool_name":"Read","tool_input":{"file_path":".env.test"}}'
assert_exit 0 "privacy-block: allows .env.test"
run_hook "privacy-block.py" '{"tool_name":"Read","tool_input":{"file_path":"src/config.ts"}}'
assert_exit 0 "privacy-block: allows normal file"
run_hook "privacy-block.py" '{"tool_name":"Bash","tool_input":{"command":"scp deploy@prod:/etc/app.conf ./app.conf"}}'
assert_exit 0 "privacy-block: remote-only scp path is not a local secret"
run_hook "privacy-block.py" '{"tool_name":"Bash","tool_input":{"command":"grep maybe_secret README.md"}}'
assert_exit 0 "privacy-block: ambiguous text only warns"
assert_contains "WARNING" "privacy-block: ambiguous text warning"

LOOPKIT_DISABLE_PRIVACY_BLOCK=1 run_hook "privacy-block.py" '{"tool_name":"Read","tool_input":{"file_path":".env"}}'
assert_exit 0 "privacy-block: disabled via env var"

# ============================================================================
# Test: dangerous-cmd-block.py (run session)
# ============================================================================

printf '\n--- Testing dangerous-cmd-block.py ---\n'

bash_json() { python3 -c 'import json, sys; print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))' "$1"; }
tool_json() { python3 -c 'import json, sys; print(json.dumps({"tool_name": sys.argv[1], "tool_input": {sys.argv[2]: sys.argv[3]}}))' "$1" "$2" "$3"; }
cmd_blocked() { run_hook "dangerous-cmd-block.py" "$(bash_json "$1")"; assert_exit 2 "dangerous-cmd-block: blocks $2"; }
cmd_allowed() { run_hook "dangerous-cmd-block.py" "$(bash_json "$1")"; assert_exit 0 "dangerous-cmd-block: allows $2"; }

# Destructive commands, and the wrappers that must not hide them.
cmd_blocked "git push --force" "git push --force"
cmd_blocked "git push -f origin main" "git push -f"
cmd_blocked "git reset --hard HEAD~1" "git reset --hard"
cmd_blocked "rm -rf /" "rm -rf /"
cmd_blocked "git clean -fd" "git clean -fd"
cmd_blocked "git branch -D feature" "git branch -D"
cmd_blocked "git checkout . " "git checkout ."
cmd_blocked "git restore ." "git restore ."
cmd_blocked "rm -rf ." "rm -rf ."
cmd_blocked "push --force origin" "push --force"
cmd_blocked "/bin/rm -r -f build" "path-qualified rm -r -f"
cmd_blocked "git -C /tmp reset --hard HEAD" "-C cannot hide a hard reset"
cmd_blocked "git --no-pager clean -fd" "display options cannot hide a forced clean"
cmd_blocked $'echo ok\ngit push --force origin main' "a newline cannot hide a force push"
cmd_blocked "env MODE=check git reset --hard HEAD" "env wrapper"
cmd_blocked "sudo git reset --hard HEAD" "sudo wrapper"
cmd_blocked "sh -c 'git clean -fd'" "shell wrapper"
cmd_blocked "bash -lc 'git reset --hard HEAD'" "clustered shell flags"
cmd_blocked "env -S 'git reset --hard HEAD'" "env split-string"
cmd_blocked "printf '%s\n' main | xargs git reset --hard" "xargs"
cmd_blocked "find . -exec git reset --hard HEAD {} +" "find -exec"
cmd_blocked "if true; then git reset --hard HEAD; fi" "shell keywords"
cmd_blocked "echo foo#bar; git reset --hard HEAD" "hash inside a word"
cmd_blocked "eval 'git reset --hard HEAD'" "eval"
cmd_blocked "echo \$(git reset --hard HEAD)" "command substitution"
cmd_blocked 'echo `git reset --hard HEAD`' "backticks"
cmd_blocked "echo ok & git reset --hard HEAD" "background operator"
cmd_blocked $'cat <<\'TEXT\'\nhello\nTEXT\ngit reset --hard HEAD' "a command after a heredoc"
cmd_allowed "echo rm -rf /" "echoed text"
cmd_allowed "rm -r safe && echo --force" "flags from separate commands"
cmd_allowed "true # ; git reset --hard HEAD" "comment text"
cmd_allowed $'cat <<\'TEXT\'\ngit reset --hard HEAD\nTEXT' "heredoc body text"
cmd_allowed "echo 'safe; rm -rf /'" "quoted command text"

# Git may only read during a run.
cmd_blocked "git push origin feature-branch" "a plain git push"
cmd_blocked "git add ." "git add"
cmd_blocked "git commit -m test" "git commit"
cmd_blocked "git merge feature" "git merge"
cmd_blocked "git stash" "git stash"
cmd_blocked "git config user.name x" "git config writes"
cmd_blocked "git update-ref refs/evolve/r001/c009 HEAD" "git update-ref"
cmd_blocked "git worktree remove ../eval" "git worktree remove"
cmd_blocked "git branch tmp" "creating a branch"
cmd_blocked "git -c core.pager=less log" "git -c config overrides"
cmd_blocked "cd $USER_REPO && git reset --hard" "cd into the user's repo, then reset"
cmd_blocked "git -C $USER_REPO checkout ." "git -C into the user's repo"
cmd_blocked "git -C $USER_REPO commit -am x" "git -C commit elsewhere"
cmd_allowed "git status" "git status"
cmd_allowed "git log --oneline" "git log"
cmd_allowed "git diff --cached" "git diff"
cmd_allowed "git show HEAD:src/app.py" "git show"
cmd_allowed "git config --get user.name" "git config --get"
cmd_allowed "git branch -a" "listing branches"
cmd_allowed "git stash list" "git stash list"
cmd_allowed "git -C $USER_REPO log -1" "reading the user's repo with git"
cmd_allowed "ls -la" "safe commands"

# Paths the run must not modify.
cmd_blocked "echo x > $USER_REPO/notes.txt" "redirect into the user's repo"
cmd_blocked "cp src/app.py $RUN_DIR/eval/src/app.py" "copy into the eval worktree"
cmd_blocked "python3 -c \"open('$RUN_DIR/ledger.jsonl', 'a').write('x')\"" "embedded ledger path"
cmd_blocked "sed -i s/a/b/ $USER_REPO/README.md" "sed -i in the user's repo"
cmd_blocked "cd $USER_REPO && make" "running a build inside the user's repo"
cmd_blocked "touch .loopkit/x" "writing the agent worktree's .loopkit/"
cmd_blocked "rm $USER_COMMON/index" "writing the shared .git"
cmd_blocked "cat src/app.py > $RUN_DIR/work/checkout.json" "redirect into run data"
cmd_allowed "cat $USER_REPO/README.md" "reading the user's repo"
cmd_allowed "sed s/a/b/ $USER_REPO/README.md" "sed without -i"
cmd_allowed "touch src/new.py" "writing inside the agent worktree"
cmd_allowed "python3 -m py_compile src/app.py" "running tools inside the agent worktree"
cmd_allowed "cat $RUN_DIR/work/precheck.txt" "reading run data"

# loopkit commands: the run session may not act as the user.
cmd_blocked "loopkit promote h001" "loopkit promote"
cmd_blocked "loopkit request-eval HEAD" "loopkit request-eval"
cmd_blocked "loopkit adopt c003" "loopkit adopt"
cmd_blocked "loopkit run remove r001 --yes" "loopkit run remove"
cmd_allowed "loopkit summary" "loopkit summary"
cmd_allowed "loopkit record --idea 'x' --proposed-by agent --learned 'see $USER_REPO'" "loopkit record with a path in its text"

# Unparseable commands and background runs.
cmd_blocked 'echo "unterminated' "an unterminated quote"
cmd_blocked 'echo $(date' "an unterminated substitution"
run_hook "dangerous-cmd-block.py" '{"tool_name":"Bash","tool_input":{"command":"sleep 60","run_in_background":true}}'
assert_exit 2 "dangerous-cmd-block: blocks run_in_background"
assert_stderr_contains "run_in_background" "dangerous-cmd-block: explains the background block"

# File-editing tools.
run_hook "dangerous-cmd-block.py" "$(tool_json Edit file_path "$USER_REPO/src/app.py")"
assert_exit 2 "dangerous-cmd-block: Edit in the user's repo is blocked"
run_hook "dangerous-cmd-block.py" "$(tool_json Write file_path "$AGENT/.claude/settings.local.json")"
assert_exit 2 "dangerous-cmd-block: Write to the agent's .claude/ is blocked"
run_hook "dangerous-cmd-block.py" "$(tool_json NotebookEdit notebook_path "$RUN_DIR/eval/x.ipynb")"
assert_exit 2 "dangerous-cmd-block: NotebookEdit in the eval worktree is blocked"
run_hook "dangerous-cmd-block.py" "$(tool_json MultiEdit file_path "$AGENT/src/app.py")"
assert_exit 0 "dangerous-cmd-block: MultiEdit inside the agent worktree is allowed"
run_hook "dangerous-cmd-block.py" "$(tool_json Edit file_path src/app.py)"
assert_exit 0 "dangerous-cmd-block: relative Edit inside the agent worktree is allowed"
run_hook "dangerous-cmd-block.py" '{"tool_name":"Read","tool_input":{"file_path":"foo"}}'
assert_exit 0 "dangerous-cmd-block: other tools pass through"

# loopkit's analysis subagents are read-only.
agent_json() { python3 -c 'import json, sys; print(json.dumps({"tool_name": sys.argv[1], "agent_type": sys.argv[2], "tool_input": {"command": sys.argv[3], "file_path": sys.argv[3]}}))' "$1" "$2" "$3"; }
run_hook "dangerous-cmd-block.py" "$(agent_json Bash loopkit:analyst "touch src/new.py")"
assert_exit 2 "dangerous-cmd-block: an analyst cannot write inside the agent worktree"
run_hook "dangerous-cmd-block.py" "$(agent_json Bash loopkit:decider "grep -rn foo src | head")"
assert_exit 0 "dangerous-cmd-block: the decider can search"
run_hook "dangerous-cmd-block.py" "$(agent_json Bash loopkit:analyst-web "loopkit lineage c000")"
assert_exit 0 "dangerous-cmd-block: an analyst can query loopkit"
run_hook "dangerous-cmd-block.py" "$(agent_json Bash loopkit:critic "loopkit export --ack 5")"
assert_exit 2 "dangerous-cmd-block: the critic cannot ack monitor exports"
run_hook "dangerous-cmd-block.py" "$(agent_json Bash loopkit:analyst "python3 profile.py > out.txt")"
assert_exit 2 "dangerous-cmd-block: an analyst cannot write files through redirects"
run_hook "dangerous-cmd-block.py" "$(agent_json Write loopkit:analyst "src/app.py")"
assert_exit 2 "dangerous-cmd-block: an analyst cannot use Write"
run_hook "dangerous-cmd-block.py" "$(agent_json Bash general-purpose "touch src/new.py")"
assert_exit 0 "dangerous-cmd-block: other subagents follow the normal run rules"

LOOPKIT_DISABLE_DANGEROUS_CMD_BLOCK=1 run_hook "dangerous-cmd-block.py" "$(bash_json "git push --force")"
assert_exit 0 "dangerous-cmd-block: disabled via env var"

# ============================================================================
# Test: run-session detection
# ============================================================================

printf '\n--- Testing run-session detection ---\n'

PROJECT_KEY="$(python3 -c 'import re, sys; print(re.sub(r"[^a-zA-Z0-9]", "-", sys.argv[1]))' "$AGENT")"
TRANSCRIPT="$HOME/.claude/projects/$PROJECT_KEY/session.jsonl"
COMMIT_IN_OUTSIDE="$(python3 -c 'import json, sys; print(json.dumps({"tool_name": "Bash", "cwd": sys.argv[1], "transcript_path": sys.argv[2], "tool_input": {"command": "git commit -m x"}}))' "$OUTSIDE" "$TRANSCRIPT")"
COMMIT_NO_TRANSCRIPT="$(python3 -c 'import json, sys; print(json.dumps({"tool_name": "Bash", "cwd": sys.argv[1], "tool_input": {"command": "git commit -m x"}}))' "$OUTSIDE")"
COMMIT_IN_AGENT="$(python3 -c 'import json, sys; print(json.dumps({"tool_name": "Bash", "cwd": sys.argv[1], "tool_input": {"command": "git commit -m x"}}))' "$AGENT/src")"

(cd "$OUTSIDE" && run_hook "dangerous-cmd-block.py" "$COMMIT_IN_OUTSIDE"; exit "$EXIT_CODE") && RC=0 || RC=$?
EXIT_CODE=$RC; assert_exit 2 "detection: CLAUDE_PROJECT_DIR marks a run session even after cd elsewhere"

(unset CLAUDE_PROJECT_DIR; cd "$OUTSIDE" && run_hook "dangerous-cmd-block.py" "$COMMIT_IN_OUTSIDE"; exit "$EXIT_CODE") && RC=0 || RC=$?
EXIT_CODE=$RC; assert_exit 2 "detection: the transcript's project directory marks a run session"

(unset CLAUDE_PROJECT_DIR; cd "$OUTSIDE" && run_hook "dangerous-cmd-block.py" "$COMMIT_IN_AGENT"; exit "$EXIT_CODE") && RC=0 || RC=$?
EXIT_CODE=$RC; assert_exit 2 "detection: a cwd inside the agent worktree marks a run session"

(unset CLAUDE_PROJECT_DIR; cd "$OUTSIDE" && run_hook "dangerous-cmd-block.py" "$COMMIT_NO_TRANSCRIPT"; exit "$EXIT_CODE") && RC=0 || RC=$?
EXIT_CODE=$RC; assert_exit 0 "detection: an ordinary session is not a run session"

(cd "$OUTSIDE" && printf '%s' "$COMMIT_IN_OUTSIDE" | bash "$HOOKS_DIR/hook-runner.sh" "$HOOKS_DIR/dangerous-cmd-block.py" >/dev/null 2>&1) && RC=0 || RC=$?
EXIT_CODE=$RC; assert_exit 2 "detection: works through hook-runner.sh"

# ============================================================================
# Test: iteration-context.py, subagent-context.py, session-init.py
# ============================================================================

printf '\n--- Testing context hooks ---\n'

run_hook "iteration-context.py" '{"session_id":"s1","prompt":"/loopkit:iter"}'
assert_exit 0 "iteration-context: returns 0"
assert_contains "loopkit run status" "iteration-context: injects the run status"
assert_contains '"hookEventName":"UserPromptSubmit"' "iteration-context: uses the UserPromptSubmit contract"
assert_contains "front (1): c000" "iteration-context: shows the front"
LOOPKIT_DISABLE_ITERATION_CONTEXT=1 run_hook "iteration-context.py" '{"session_id":"s1"}'
assert_not_contains "loopkit run status" "iteration-context: disabled via env var"

run_hook "subagent-context.py" '{"session_id":"s1","agent_type":"loopkit:analyst"}'
assert_exit 0 "subagent-context: returns 0"
assert_contains "loopkit run context (for subagents)" "subagent-context: injects the run context"
assert_contains "loopkit summary" "subagent-context: points to the analysis commands"
assert_contains '"hookEventName":"SubagentStart"' "subagent-context: uses the SubagentStart contract"

ENV_FILE="$FIXTURE/claude-env"
: > "$ENV_FILE"
CLAUDE_ENV_FILE="$ENV_FILE" run_hook "session-init.py" '{"session_id":"s1","source":"startup"}' "runner"
assert_exit 0 "session-init: returns 0"
assert_contains "loopkit run session" "session-init: injects the run session context"
assert_contains '"hookEventName":"SessionStart"' "session-init: uses the SessionStart contract"
TOTAL=$((TOTAL + 1))
if grep -q "export CUDA_VISIBLE_DEVICES='2'" "$ENV_FILE" && grep -q "PYTHONDONTWRITEBYTECODE=1" "$ENV_FILE"; then
  printf '  PASS: %s\n' "session-init: exports the run GPU through CLAUDE_ENV_FILE"
  PASS=$((PASS + 1))
else
  printf '  FAIL: %s\n' "session-init: exports the run GPU through CLAUDE_ENV_FILE"
  FAIL=$((FAIL + 1))
fi

# ============================================================================
# Test: stop-notify.py
# ============================================================================

printf '\n--- Testing stop-notify.py ---\n'

run_hook "stop-notify.py" '{"session_id":"s1"}' "runner"
assert_exit 0 "stop-notify: returns 0"
assert_contains "terminalSequence" "stop-notify: contains terminal notification"
assert_contains "Run session ended" "stop-notify: notification names the run session"

STDOUT=$(python3 -B "$REPO_ROOT/tests/fixtures/hooks/webhook_smoke.py" "$HOOKS_DIR/stop-notify.py")
assert_contains "webhook received" "stop-notify: waits for HTTP webhook completion"

HTTPS_MARKER="$FIXTURE/https-webhook.json"
python3 -B "$REPO_ROOT/tests/fixtures/hooks/https_webhook_stub.py" \
  "$HOOKS_DIR/stop-notify.py" "$HTTPS_MARKER" <<<'{"session_id":"https-webhook-smoke"}' >/dev/null
STDOUT=$(cat "$HTTPS_MARKER")
assert_contains "loopkit run session ended" "stop-notify: selects and awaits the HTTPS client"
STDOUT=$(python3 -B "$REPO_ROOT/tests/fixtures/hooks/https_webhook_stub.py" \
  "$HOOKS_DIR/stop-notify.py" "$HTTPS_MARKER" error <<<'{"session_id":"https-webhook-error"}')
assert_contains "terminalSequence" "stop-notify: webhook errors fail open"
STDOUT=$(python3 -B "$REPO_ROOT/tests/fixtures/hooks/https_webhook_stub.py" \
  "$HOOKS_DIR/stop-notify.py" "$HTTPS_MARKER" timeout <<<'{"session_id":"https-webhook-timeout"}')
assert_contains "terminalSequence" "stop-notify: webhook timeout is bounded"

# ============================================================================
# Test: ordinary sessions are untouched
# ============================================================================

printf '\n--- Testing ordinary sessions ---\n'

ORDINARY_TOTAL=0
ordinary() {  # ordinary <hook> <json>
  local stdout rc
  set +e
  stdout=$(cd "$OUTSIDE" && env -u CLAUDE_PROJECT_DIR python3 -B "$HOOKS_DIR/$1" <<<"$2" 2>/dev/null)
  rc=$?
  set -e
  TOTAL=$((TOTAL + 1))
  if [[ "$rc" -eq 0 && -z "$stdout" ]]; then
    printf '  PASS: %s\n' "ordinary session: $1 stays silent for $3"
    PASS=$((PASS + 1))
  else
    printf '  FAIL: %s (exit %s, stdout %s)\n' "ordinary session: $1 stays silent for $3" "$rc" "$stdout"
    FAIL=$((FAIL + 1))
  fi
}
ordinary dangerous-cmd-block.py "$(bash_json "cd $USER_REPO && git reset --hard")" "cd + git reset --hard"
ordinary dangerous-cmd-block.py "$(bash_json "git -C $USER_REPO checkout .")" "git -C checkout ."
ordinary dangerous-cmd-block.py "$(bash_json "git config user.name x")" "git config"
ordinary dangerous-cmd-block.py '{"tool_name":"Bash","tool_input":{"command":"sleep 1","run_in_background":true}}' "run_in_background"
ordinary dangerous-cmd-block.py "$(bash_json "loopkit promote h001")" "loopkit promote"
ordinary privacy-block.py '{"tool_name":"Read","tool_input":{"file_path":".env"}}' ".env read"
ordinary scout-block.py '{"tool_name":"Read","tool_input":{"file_path":"node_modules/x/index.js"}}' "node_modules read"
ordinary iteration-context.py '{"session_id":"x","prompt":"hello"}' "a prompt"
ordinary subagent-context.py '{"session_id":"x"}' "a subagent"
ordinary session-init.py '{"session_id":"x"}' "session start"
ordinary stop-notify.py '{"session_id":"x"}' "session end"

# ============================================================================
# Test: hook runtime logs land in global ~/.claude, not the project repo
# ============================================================================

printf '\n--- Testing hook log location (global, not per-project) ---\n'

LOG_FILE="$(find "$HOME/.claude/hooks/.logs" -name 'hook-log.jsonl' 2>/dev/null | head -1)"
TOTAL=$((TOTAL + 1))
if [[ -n "$LOG_FILE" && "$(basename "$(dirname "$LOG_FILE")")" =~ ^[0-9a-f]{12}$ ]] &&
   ! grep -q '"cwd"\|"projectRoot"\|"projectName"\|"path"\|"command"' "$LOG_FILE"; then
  printf '  PASS: %s\n' "hook log: global, hashed project dir, raw paths redacted"
  PASS=$((PASS + 1))
else
  printf '  FAIL: %s (path=%s)\n' "hook log: global, hashed project dir, raw paths redacted" "$LOG_FILE"
  FAIL=$((FAIL + 1))
fi
TOTAL=$((TOTAL + 1))
if [[ -z "$(git -C "$AGENT" status --porcelain -- . ':!.claude')" ]]; then
  printf '  PASS: %s\n' "hook log: the agent worktree stays clean"
  PASS=$((PASS + 1))
else
  printf '  FAIL: %s\n' "hook log: the agent worktree stays clean"
  FAIL=$((FAIL + 1))
fi

# ============================================================================
# Summary
# ============================================================================

cd /

printf '\n=== Results: %d/%d passed ===' "$PASS" "$TOTAL"
if [[ "$FAIL" -gt 0 ]]; then
  printf ' (%d FAILED)\n' "$FAIL"
  exit 1
else
  printf ' (all passed)\n'
  exit 0
fi
