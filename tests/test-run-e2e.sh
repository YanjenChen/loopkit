#!/usr/bin/env bash
# End-to-end test of the loopkit CLI on a toy repository.
#
# The toy repo has a fake "placer" (src/placer.py) whose parameters in
# src/params.json change hpwl, runtime and legality, a submodule, and a score
# script that recomputes the metrics from the placer's output. The test plays
# the run session's agent: it edits the agent worktree and drives
# checkout / precheck / evaluate / record, plus the user-side commands.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LK=(python3 "$ROOT/claude-plugin/bin/loopkit")
TMP="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-e2e.XXXXXX")"

cleanup() {
  chmod -R u+w "$TMP" 2>/dev/null
  rm -rf "$TMP"
}
trap cleanup EXIT

export HOME="$TMP/home"
export CLAUDE_CONFIG_DIR="$TMP/home/.claude"
export LOOPKIT_DATA_DIR="$TMP/data"
export LOOPKIT_MAX_WAIT=60
export GIT_CONFIG_NOSYSTEM=1
mkdir -p "$HOME"
git config --global user.name "E2E User"
git config --global user.email "e2e@example.invalid"
git config --global init.defaultBranch main
git config --global protocol.file.allow always

PASS=0
FAIL=0
OUT=""

pass() { PASS=$((PASS + 1)); printf '  PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '  FAIL: %s\n' "$1"; printf '        output: %s\n' "$(printf '%s' "$OUT" | tail -n 8 | sed 's/^/        | /')"; }

# lk <dir> <args...>: run loopkit in dir, capturing stdout+stderr into OUT.
lk() {
  local dir="$1"; shift
  OUT="$(cd "$dir" && "${LK[@]}" "$@" 2>&1)"
  CODE=$?
}

has()     { if printf '%s' "$OUT" | grep -qF -- "$1"; then pass "$2"; else fail "$2 (missing: $1)"; fi; }
lacks()   { if printf '%s' "$OUT" | grep -qF -- "$1"; then fail "$2 (unexpected: $1)"; else pass "$2"; fi; }
code_is() { if [ "$CODE" -eq "$1" ]; then pass "$2"; else fail "$2 (exit $CODE, want $1)"; fi; }
same()    { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1', want '$2')"; fi; }

set_params() {  # set_params <dir> <spread> <sleep>
  printf '{"spread": %s, "sleep": %s}\n' "$2" "$3" > "$1/src/params.json"
}

# ---------------------------------------------------------------------------
printf '\n--- toy repository ---\n'

SUB="$TMP/sublib"
mkdir -p "$SUB" && git -C "$SUB" init -q && echo "lib" > "$SUB/lib.txt"
git -C "$SUB" add -A && git -C "$SUB" commit -qm "sublib"

REPO="$TMP/placer"
mkdir -p "$REPO/src" "$REPO/bench" "$REPO/.loopkit"
cat > "$REPO/src/placer.py" <<'EOF'
"""A fake placer: cell i goes to x = i * spread; sleep simulates work.

It reports its work as a deterministic runtime, so the test does not depend on machine load.
"""
import json, os, sys, time

params = json.load(open(os.path.join(os.path.dirname(__file__), 'params.json')))
time.sleep(params['sleep'])
with open(sys.argv[1], 'w') as out:
    out.write('runtime %r\n' % params['sleep'])
    for i in range(10):
        out.write('c%d %r\n' % (i, i * params['spread']))
EOF
set_params "$REPO" 1.5 0.4
printf 'c0 c1\nc1 c2\nc2 c3\nc3 c9\n' > "$REPO/bench/nets.txt"
echo "snapshot-v1" > "$REPO/bench/marker"
echo "# toy placer" > "$REPO/README.md"
printf '*.o\n__pycache__/\n' > "$REPO/.gitignore"
git -C "$REPO" init -q
git -C "$REPO" submodule add -q "$SUB" vendor/sublib
git -C "$REPO" add -A && git -C "$REPO" commit -qm "toy placer"

cat > "$REPO/.loopkit/score.py" <<'EOF'
"""Score the fake placer: recompute hpwl and legality from its output."""
import json, os, subprocess, sys

eval_dir = os.environ['LOOPKIT_EVAL_DIR']
build = os.environ['LOOPKIT_BUILD_DIR']
result_path = os.environ['LOOPKIT_RESULT']
placement = os.path.join(build, 'place.txt')

def write(data):
    with open(result_path, 'w') as f:
        json.dump(data, f)

proc = subprocess.run([sys.executable, 'src/placer.py', placement])
if proc.returncode != 0:
    write({'schema': 1, 'status': 'fail', 'reason': 'placer_crashed: exit %d' % proc.returncode})
    sys.exit(0)
x = {}
for line in open(placement):
    name, pos = line.split()
    x[name] = float(pos)
runtime = x.pop('runtime')
hpwl = 0.0
for line in open(os.path.join(eval_dir, 'bench', 'nets.txt')):
    a, b = line.split()
    hpwl += abs(x[a] - x[b])
xs = sorted(x.values())
overlaps = sum(1 for p, q in zip(xs, xs[1:]) if q - p < 1.0)
write({'schema': 1, 'status': 'ok', 'reason': None,
       'objectives': {'hpwl': hpwl, 'runtime': runtime},
       'constraints': {'legal': {'pass': overlaps == 0, 'value': overlaps, 'detail': '%d overlaps' % overlaps}},
       'extra': {'cuda': os.environ.get('CUDA_VISIBLE_DEVICES'),
                 'marker': open(os.path.join(eval_dir, 'bench', 'marker')).read().strip(),
                 'submodule': os.path.exists('vendor/sublib/lib.txt')}})
EOF
cat > "$REPO/.loopkit/config.json" <<'EOF'
{
  "schema": 1,
  "source": "optimize hpwl and runtime",
  "objectives": [
    {"name": "hpwl", "direction": "minimize", "tolerance": {"relative": 0.001}, "unit": ""},
    {"name": "runtime", "direction": "minimize", "tolerance": {"absolute": 0.08}, "unit": "s"}
  ],
  "constraints": [{"name": "legal", "description": "no overlaps"}],
  "extra": [{"name": "cuda"}, {"name": "marker"}, {"name": "submodule"}],
  "score": {"command": ["python3", "-I", "${LOOPKIT_EVAL_DIR}/.loopkit/score.py"], "timeout_s": 60, "env": {}},
  "eval_assets": [".loopkit/score.py", "bench/"],
  "scope": {"include": ["src/**"], "exclude": ["src/vendor/**"]},
  "workflow": {"mode": "multi", "analysts": [{"name": "wirelength", "focus": "hpwl"}, {"name": "runtime", "focus": "speed"}],
               "decider": {"principles": "prefer both"}},
  "precheck": {"command": ["python3", "-m", "py_compile", "src/placer.py"], "max_fix_attempts": 2},
  "run": {"gpu": "0"}
}
EOF

# ---------------------------------------------------------------------------
printf '\n--- init helpers ---\n'

lk "$REPO" config check
has "CONFIG ok" "config check validates the config"
has "SCREEN ok" "config check screens the commands"

lk "$REPO" trial --repeat 2
has "TRIAL 1/2 | " "trial runs the score script"
has "SPREAD hpwl" "trial reports the spread over repeats"
lacks "invalid_result" "trial result is valid"

# ---------------------------------------------------------------------------
printf '\n--- run create ---\n'

lk "$REPO" run create --gpu 3 --monitor-url https://example.invalid/monitor
code_is 0 "run create exits 0"
has "RUN r001 created (committed .loopkit/ on main)" "run create commits .loopkit/ as c000"
has "BASELINE c000 | hpwl 13.500" "c000 is scored"
same "$(git -C "$REPO" log -1 --format=%s)" "loopkit: configure run r001" "c000 commit is on the user's branch"
same "$(git -C "$REPO" status --porcelain)" "" "user working tree is clean after run create"

RUN_DIR="$(find "$LOOPKIT_DATA_DIR" -maxdepth 2 -name r001 -type d)"
AGENT="$RUN_DIR/agent"
[ -f "$AGENT/src/placer.py" ] && pass "agent worktree exists" || fail "agent worktree exists"
[ -f "$AGENT/vendor/sublib/lib.txt" ] && pass "submodule checked out in agent worktree" || fail "submodule checked out in agent worktree"
grep -qF "Edit(/$REPO/**)" "$AGENT/.claude/settings.local.json" && pass "deny rule for the user's repo" || fail "deny rule for the user's repo"
grep -qF '"CUDA_VISIBLE_DEVICES": "3"' "$AGENT/.claude/settings.local.json" && pass "GPU env in agent settings" || fail "GPU env in agent settings"
[ ! -w "$RUN_DIR/run.json" ] && pass "run.json is read-only" || fail "run.json is read-only"
[ -f "$RUN_DIR/ledger.jsonl" ] && pass "ledger lives in the run directory" || fail "ledger lives in the run directory"

lk "$AGENT" show c000
has '"cuda": "3"' "score script sees CUDA_VISIBLE_DEVICES from --gpu"
has '"submodule": true' "eval worktree has the submodule"

# ---------------------------------------------------------------------------
printf '\n--- iterations: KEPT, REVERTED, FAILED ---\n'

GOAL='/goal 重複執行 /loopkit:iter（停止條件：最多 8 輪，由 loopkit 判斷）'
lk "$AGENT" batch start --text "$GOAL" --conditions '{"max_iters": 8}'
has "BATCH 1 | started | stop: max_iters=8" "batch starts"
lk "$AGENT" batch start --text "$GOAL" --conditions '{"max_iters": 8}'
has "BATCH 1 | continues | iter 0/8 done" "same prompt continues the batch"
lk "$AGENT" queue
has "QUEUE | empty" "empty queue"

lk "$AGENT" checkout
has "CHECKOUT c000" "default parent is c000"
lk "$AGENT" evaluate
has "nothing changed since" "evaluate refuses an unchanged worktree"
set_params "$AGENT" 1.5 0.1
lk "$AGENT" precheck
has "PRECHECK ok" "precheck passes"
lk "$AGENT" evaluate
has "CHANGED (1): src/params.json" "evaluate lists the changed files (no bytecode from precheck)"
has "RESULT KEPT | hpwl 13.500 (+0.0% same) | runtime" "faster candidate would be KEPT"
lk "$AGENT" record --idea "cut the sleep" --proposed-by runtime --learned "hypothesis: sleep dominates; result: confirmed; evidence: runtime drop"
has "ITER 1/8 | c001<-c000 | hpwl 13.500 (+0.0% same) | runtime" "ITER line for c001"
has "| KEPT | front=1" "c001 is KEPT and replaces c000 on the front"
same "$(git -C "$REPO" ls-tree -r --name-only refs/evolve/r001/c001 | grep -c pycache)" "0" "candidate commit has no bytecode cache"

lk "$AGENT" checkout
has "CHECKOUT c001" "default parent moves to c001"
echo "# comment" >> "$AGENT/src/placer.py"
lk "$AGENT" evaluate
has "RESULT REVERTED" "no-op change is REVERTED"
has "same_as:c001" "REVERTED reason is same_as"
lk "$AGENT" record --idea "add a comment" --proposed-by agent --learned "hypothesis: none; result: inconclusive; evidence: -"
has "ITER 2/8 | c002<-c001" "ITER line for c002"

lk "$AGENT" checkout c001
set_params "$AGENT" 0.5 0.1
lk "$AGENT" evaluate
has "RESULT FAILED | constraint:legal" "overlapping placement fails the constraint"
lk "$AGENT" record --idea "squeeze cells" --proposed-by wirelength --learned "hypothesis: smaller spread; result: disproven; evidence: overlaps"
has "ITER 3/8 | c003<-c001 | FAILED | constraint:legal" "FAILED ITER line omits objectives"

lk "$AGENT" checkout c003
has "c003 cannot be a parent (FAILED)" "FAILED candidates cannot be parents"

lk "$AGENT" checkout c001
echo "changed" >> "$AGENT/README.md"
echo "*.tmp" >> "$AGENT/.gitignore"
ln -s /etc/passwd "$AGENT/src/link"
lk "$AGENT" evaluate
has "+src/link" "CHANGED marks new files"
has "RESULT FAILED | scope:" "scope violations fail without scoring"
has "protected .gitignore" "protected path is reported"
has "out_of_scope README.md" "out-of-scope file is reported"
has "symlink src/link" "escaping symlink is reported"
lk "$AGENT" record --idea "touch everything" --proposed-by agent --learned "hypothesis: x; result: disproven; evidence: scope"
has "ITER 4/8 | c004<-c001 | FAILED | scope:" "scope FAILED is recorded"

lk "$AGENT" record --idea "again" --proposed-by agent --learned "x"
has "no checkout for this iteration" "record needs a fresh checkout"

# ---------------------------------------------------------------------------
printf '\n--- merge with conflict ---\n'

lk "$AGENT" checkout c000
set_params "$AGENT" 1.2 0.4
lk "$AGENT" evaluate
has "RESULT KEPT" "tighter spread from c000 is KEPT (hpwl better, runtime worse)"
lk "$AGENT" record --idea "tighter spread" --proposed-by wirelength --learned "hypothesis: spread; result: confirmed; evidence: hpwl"
has "ITER 5/8 | c005<-c000" "c005 recorded"
has "front=2" "front has two members"

lk "$AGENT" checkout c005 c001
has "CHECKOUT c005+c001 | merged" "merge checkout"
has "CONFLICTS (resolve them while implementing): src/params.json" "conflict is reported"
grep -q '<<<<<<<' "$AGENT/src/params.json" && pass "conflict markers left in the file" || fail "conflict markers left in the file"
lk "$AGENT" evaluate
has "unresolved_conflict src/params.json" "unresolved conflict fails"
set_params "$AGENT" 1.2 0.1
lk "$AGENT" evaluate
has "RESULT KEPT" "resolved merge is KEPT"
lk "$AGENT" record --idea "combine spread and speed" --proposed-by "wirelength,runtime" --learned "hypothesis: merge; result: confirmed; evidence: both"
has "ITER 6/8 | c006<-c005+c001" "merge ITER line shows both parents"
same "$(git -C "$REPO" rev-list --parents -n1 refs/evolve/r001/c006 | wc -w)" "3" "merge commit has two parents"

# ---------------------------------------------------------------------------
printf '\n--- snapshot isolation and human candidates ---\n'

sed -i 's/hpwl += abs(x\[a\] - x\[b\])/hpwl += 0/' "$REPO/.loopkit/score.py"
echo "snapshot-v2" > "$REPO/bench/marker"
set_params "$REPO" 1.1 0.05
git -C "$REPO" commit -qam "human: tighter and faster (and a tampered score script)"
HUMAN_SHA="$(git -C "$REPO" rev-parse HEAD)"

lk "$REPO" request-eval HEAD --note "try 1.1"
has "QUEUED request 1: evaluate ${HUMAN_SHA:0:7} (main) in run r001" "request-eval queues the commit"
same "$(git -C "$REPO" rev-parse refs/evolve/r001/req/1)" "$HUMAN_SHA" "request ref pins the commit"
lk "$REPO" request-eval HEAD
has "already queued as request 1" "duplicate request is rejected"

lk "$AGENT" queue
has "HUMAN h001 | main@${HUMAN_SHA:0:7} | hpwl 9.9000" "human candidate is scored with the snapshot score script"
has "| OBSERVED | front:yes" "h001 is OBSERVED and would be on the front"
lk "$AGENT" show h001
has '"marker": "snapshot-v1"' "benchmarks come from the snapshot, not the user's tree"
lk "$AGENT" summary
has "h001 OBSERVED main@" "summary lists the human candidate"
lacks "h001 OBSERVED promoted" "h001 is not promoted yet"
lk "$AGENT" checkout h001
has "unpromoted human candidate" "unpromoted human cannot be a parent"
lk "$REPO" promote h001
has "QUEUED request 2: promote h001" "promote is queued"
lk "$AGENT" queue
has "PROMOTE h001 | promoted | front:yes" "promote is applied at the next head"
lk "$AGENT" checkout h001
has "CHECKOUT h001" "promoted human can be a parent"
lk "$AGENT" summary
has "*front*" "summary marks front members"
has "kept by proposer:" "summary includes pattern analysis"

# ---------------------------------------------------------------------------
printf '\n--- crash recovery and PENDING ---\n'

set_params "$AGENT" 1.1 0.02
lk "$AGENT" evaluate
git -C "$REPO" update-ref refs/evolve/r001/c007 "$HUMAN_SHA"
lk "$AGENT" record --idea "faster on top of human" --proposed-by agent --learned "hypothesis: sleep; result: confirmed; evidence: runtime"
has "ITER 7/8 | c007<-h001" "record replaces a ref left by an interrupted record"
[ "$(git -C "$REPO" rev-parse refs/evolve/r001/c007)" != "$HUMAN_SHA" ] && pass "c007 ref points at the recorded commit" || fail "c007 ref points at the recorded commit"

git -C "$REPO" update-ref refs/evolve/r001/c008 "$HUMAN_SHA"
lk "$AGENT" batch start --text "$GOAL" --conditions '{"max_iters": 8}'
has "NOTE: removed dangling ref c008" "head removes a dangling next-id ref"

lk "$AGENT" checkout
set_params "$AGENT" 1.1 3
lk "$AGENT" evaluate --max-wait 0.5
has "PENDING | scoring is still running" "slow scoring reports PENDING"
lk "$AGENT" record --idea "x" --proposed-by agent --learned "x"
has "scoring is still running" "record waits for scoring"
lk "$AGENT" wait --max-wait 60
has "RESULT REVERTED" "wait delivers the result"
lk "$AGENT" record --idea "slow version" --proposed-by agent --learned "hypothesis: sleep 3; result: disproven; evidence: slow"
has "ITER 8/8" "eighth iteration recorded"
has "LOOPKIT-STOP | max_iters 8/8" "max_iters stops the batch"

lk "$AGENT" batch start --text "$GOAL" --conditions '{"max_iters": 8}'
has "already stopped" "the same prompt after STOP stays stopped"
has "LOOPKIT-STOP | max_iters 8/8" "STOP is printed again"
lk "$AGENT" checkout
has "LOOPKIT-STOP" "checkout refuses after STOP"

lk "$AGENT" batch start --text "second batch" --conditions '{"max_iters": 3, "targets": [{"objective": "hpwl", "op": "<=", "value": 17}]}'
has "BATCH 2 | started" "a different prompt starts a new batch"
has "LOOPKIT-STOP | target hpwl<=17.000 by" "a target already met stops immediately"

lk "$AGENT" batch start --text "third batch" --conditions '{"max_iters": 1}'
has "BATCH 3 | started" "third batch"
lk "$AGENT" checkout
echo "# c" >> "$AGENT/src/placer.py"
lk "$AGENT" evaluate
lk "$AGENT" record --idea "comment" --proposed-by agent --learned "x"
has "ITER 1/1" "iteration count restarts per batch"
has "LOOPKIT-STOP | max_iters 1/1" "per-batch max_iters"

# ---------------------------------------------------------------------------
printf '\n--- export, adopt, status ---\n'

lk "$AGENT" export --since 0
has '"derived"' "export includes derived fields"
has '"front"' "export includes the front per record"
has '"latest_seq"' "export includes latest_seq"
LATEST="$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["latest_seq"])')"
lk "$AGENT" export --ack "$LATEST"
has "ACK $LATEST" "ack records the pushed seq"
lk "$AGENT" export --pending
same "$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["records"]))')" "0" "nothing pending after ack"

lk "$AGENT" monitor ack 0
lk "$AGENT" monitor push
has "MONITOR https://example.invalid/monitor" "monitor push names the monitor"
WRITES="$(printf '%s' "$OUT" | sed -n 's/^WRITES //p')"
same "$(printf '%s' "$WRITES" | python3 -c 'import json,sys; w=json.load(sys.stdin); print(w[-1]["collection"]+"/"+w[-1]["doc_id"], all(__import__("os").path.isfile(x["file_path"]) for x in w))')" "meta/current True" "monitor push writes the documents as files"
same "$(printf '%s' "$WRITES" | python3 -c 'import json,sys; w=json.load(sys.stdin); d=json.load(open(w[0]["file_path"])); print(w[0]["doc_id"], d["first_seq"], "derived" in d["records"][0])')" "chunk-00000 1 True" "the first chunk starts at seq 1 with derived fields"
ACK="$(printf '%s' "$OUT" | sed -n 's/.*loopkit monitor ack \([0-9]*\).*/\1/p')"
lk "$AGENT" monitor ack "$ACK"
has "ACK $ACK" "monitor ack records the pushed seq"
lk "$AGENT" monitor push
has "up to date" "nothing new after the ack"
same "$(printf '%s' "$OUT" | sed -n 's/^WRITES //p' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')" "1" "an up-to-date push only refreshes meta"
lk "$REPO" monitor html --sample --out "$TMP/monitor.html"
has "with sample data" "monitor html embeds sample data"
grep -q '"sample":true' "$TMP/monitor.html" && pass "the sample is marked as sample" || fail "the sample is marked as sample"
grep -q '/\*__LOOPKIT_SAMPLE__\*/' "$TMP/monitor.html" && fail "the sample placeholder is replaced" || pass "the sample placeholder is replaced"
grep -qF '"Artifact"' "$AGENT/.claude/settings.local.json" && pass "the run session may push to the monitor without a prompt" || fail "the run session may push to the monitor without a prompt"

lk "$REPO" adopt c006
has "ADOPTED c006 as branch loopkit/r001/c006" "adopt creates a branch"
same "$(git -C "$REPO" rev-parse loopkit/r001/c006)" "$(git -C "$REPO" rev-parse refs/evolve/r001/c006)" "branch points at c006"

lk "$REPO" status
has "RUN r001" "status shows the run"
has "front:" "status shows the front"

# ---------------------------------------------------------------------------
printf '\n--- integrity ---\n'

git -C "$REPO" config user.name "Changed Name"
lk "$AGENT" batch start --text "fourth" --conditions '{"max_iters": 2}'
has "WARNING: git config changed" "other config changes only warn"
has "BATCH 4 | started" "the run continues after a warning"

git -C "$REPO" config filter.evil.smudge "touch $TMP/pwned"
lk "$AGENT" batch start --text "fourth" --conditions '{"max_iters": 2}'
has "LOOPKIT-STOP | integrity: git config that can run code changed: +filter.evil.smudge" "filter config stops the run"
git -C "$REPO" config --unset filter.evil.smudge
lk "$AGENT" batch start --text "fourth" --conditions '{"max_iters": 2}'
has "LOOPKIT-STOP | integrity: run stopped at seq" "an integrity stop is permanent"
lk "$AGENT" check
has "INTEGRITY ok" "check reports the current state without writing"

# A second run for asset tampering.
git -C "$REPO" revert --no-edit HEAD >/dev/null
lk "$REPO" run create --name r002
has "RUN r002 created" "second run"
has "BASELINE c000 | hpwl 13.500" "second run scores the reverted score script"
R2="$(dirname "$RUN_DIR")/r002"
chmod u+w "$R2/eval-assets/bench" "$R2/eval-assets/bench/marker"
echo "tampered" > "$R2/eval-assets/bench/marker"
lk "$R2/agent" batch start --text "go" --conditions '{"max_iters": 1}'
has "LOOPKIT-STOP | integrity: eval-assets changed: bench/marker" "tampered assets stop the run"

lk "$REPO" run list
has "r001 |" "run list shows r001"
lk "$REPO" run remove r002 --yes
has "REMOVED run r002" "run remove"
[ ! -d "$R2" ] && pass "run directory removed" || fail "run directory removed"
same "$(git -C "$REPO" for-each-ref refs/evolve/r002/ | wc -l)" "0" "run refs removed"

# ---------------------------------------------------------------------------
printf '\n=== Results: %d/%d passed ===\n' "$PASS" "$((PASS + FAIL))"
[ "$FAIL" -eq 0 ]
