# The score script contract

The framework checks a candidate out cleanly in the run's eval worktree and runs
`score.command` there. Everything about building, measuring, repeating and aggregating is the
script's business; the framework only reads the result file it writes.

## Environment

| Variable | Value |
|---|---|
| working directory | the eval worktree root, holding the candidate's code |
| `LOOPKIT_RESULT` | the file to write the result JSON to |
| `LOOPKIT_EVAL_DIR` | the run's snapshot of the eval assets (score script, benchmarks) |
| `LOOPKIT_BUILD_DIR` | a build directory outside the worktree, the same for the whole run, so builds are incremental |
| `LOOPKIT_WORKTREE` | the eval worktree |
| `CUDA_VISIBLE_DEVICES` | the run's GPU |
| `PYTHONDONTWRITEBYTECODE` | `1` |
| `score.env` | the config's extra variables |

stdout and stderr go to the score log (`loopkit show <id> --log`); they are never parsed.

## Result

```json
{
  "schema": 1,
  "status": "ok",
  "reason": null,
  "objectives": {"hpwl": 1.0231e6, "runtime": 41.2},
  "constraints": {"legal": {"pass": true, "value": 0, "detail": "0 overlaps"}},
  "extra": {"hpwl_samples": [1.0229e6, 1.0231e6, 1.0236e6], "gpu_mem_mb": 5120}
}
```

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | 1 |
| `status` | yes | `ok`: measured. `fail`: could not measure (build failed, crash, the script's own timeout). |
| `reason` | with `fail` | one line, e.g. `build_failed: nvcc error in placer.cu:120` |
| `objectives` | with `ok` | exactly the config's objective names; finite numbers (units live in the config) |
| `constraints` | with `ok` | every constraint of the config; `pass` (boolean) is required, `value` and `detail` are recorded |
| `extra` | no | anything else; only recorded |

Minimal failure: `{"schema": 1, "status": "fail", "reason": "build_failed: ..."}`.

## How the framework reads it

- `status: fail` -> FAILED, with the script's `reason`.
- a constraint with `pass: false` -> FAILED, reason `constraint:<name>`.
- otherwise the candidate is compared against the front: KEPT or REVERTED (human candidates:
  OBSERVED).
- non-zero exit code, no result file, invalid JSON, wrong fields, or a file over 64 KB ->
  FAILED, reason `invalid_result: <what>`. That points at the score script, not the candidate.
- running past `score.timeout_s` -> FAILED, reason `score_timeout`.

## Isolation checklist

A candidate controls the code the script builds and runs. The script must make it impossible
for that code to fake its own score:

- Run the script with `python3 -I` and never import candidate code into the score script.
- Recompute objectives and constraints from the candidate's outputs, with checkers from the
  snapshot (`$LOOPKIT_EVAL_DIR`), instead of trusting numbers the candidate prints.
- Build checkers that need compiling from the snapshot's sources.
- The scope includes the build files, so pin every build option that affects correctness on
  the command line: deterministic switches, assertions, and options that change numerics such
  as fast-math.
- Build out of tree into `$LOOPKIT_BUILD_DIR`; in-tree build products must be ignored by
  `.gitignore`, or they end up in candidates.
- For noisy measurements such as runtime, repeat and aggregate inside the script (e.g. the
  median of 3), and record the samples in `extra`.

## Template

```python
"""loopkit score script for <project>."""
import json, os, statistics, subprocess, sys, time

EVAL = os.environ['LOOPKIT_EVAL_DIR']
BUILD = os.environ['LOOPKIT_BUILD_DIR']
RESULT = os.environ['LOOPKIT_RESULT']


def finish(data):
    data.setdefault('schema', 1)
    with open(RESULT, 'w') as f:
        json.dump(data, f)
    sys.exit(0)


# 1. Build the candidate out of tree, pinning correctness-relevant options.
build = subprocess.run(['cmake', '-S', '.', '-B', BUILD, '-DCMAKE_BUILD_TYPE=Release',
                        '-DPLACER_DETERMINISTIC=ON', '-DCMAKE_CXX_FLAGS=-fno-fast-math'])
if build.returncode == 0:
    build = subprocess.run(['cmake', '--build', BUILD, '-j'])
if build.returncode != 0:
    finish({'status': 'fail', 'reason': 'build_failed: exit %d' % build.returncode})

# 2. Run it on the snapshot's benchmarks, timing it ourselves.
samples = []
for _ in range(3):
    start = time.time()
    run = subprocess.run([os.path.join(BUILD, 'placer'), os.path.join(EVAL, 'benchmarks/adaptec1'),
                          '--out', os.path.join(BUILD, 'out.pl')])
    samples.append(time.time() - start)
    if run.returncode != 0:
        finish({'status': 'fail', 'reason': 'placer_crashed: exit %d' % run.returncode})

# 3. Recompute the metrics from the output with the snapshot's checker.
check = subprocess.run([sys.executable, '-I', os.path.join(EVAL, 'tools/check.py'),
                        os.path.join(BUILD, 'out.pl')], capture_output=True, text=True)
metrics = json.loads(check.stdout)
finish({
    'status': 'ok',
    'objectives': {'hpwl': metrics['hpwl'], 'runtime': statistics.median(samples)},
    'constraints': {'legal': {'pass': metrics['overlaps'] == 0, 'value': metrics['overlaps']}},
    'extra': {'runtime_samples': samples},
})
```
