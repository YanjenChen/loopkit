# The loopkit config: `.loopkit/config.json`

`/loopkit:init` writes it and the user confirms it; `loopkit run create` commits `.loopkit/`
as c000 and freezes a copy in the run's `run.json`. JSON has no comments, so every item takes
a `description`.

## Example

The user said only "優化hpwl與runtime"; init filled in the rest and the user confirmed:

```json
{
  "schema": 1,
  "source": "優化hpwl與runtime",
  "objectives": [
    {"name": "hpwl", "direction": "minimize", "tolerance": {"relative": 0.001}, "unit": "",
     "description": "total half-perimeter wirelength of the final placement"},
    {"name": "runtime", "direction": "minimize", "tolerance": {"relative": 0.02}, "unit": "s",
     "description": "wall-clock time of global placement"}
  ],
  "constraints": [
    {"name": "legal", "description": "no overlapping cells; every cell on a legal row"}
  ],
  "extra": [
    {"name": "gpu_mem_mb", "description": "peak GPU memory"}
  ],
  "score": {
    "command": ["python3", "-I", "${LOOPKIT_EVAL_DIR}/.loopkit/score.py"],
    "timeout_s": 1800,
    "env": {"CUBLAS_WORKSPACE_CONFIG": ":4096:8", "PLACER_DETERMINISTIC": "1"}
  },
  "eval_assets": [".loopkit/score.py", "benchmarks/ispd2005/"],
  "scope": {
    "include": ["src/**/*.cu", "src/**/*.cpp", "src/**/*.h", "**/CMakeLists.txt", "cmake/**"],
    "exclude": ["src/third_party/**"]
  },
  "workflow": {
    "mode": "multi",
    "analysts": [
      {"name": "wirelength", "focus": "algorithms, parameters and numerics that affect HPWL",
       "questions": ["Which terms of the objective dominate HPWL on the largest benchmark?"],
       "evidence": ["file:line of the density and wirelength gradients", "score logs"],
       "red_flags": ["changes that trade legality for wirelength"]},
      {"name": "runtime", "focus": "GPU kernels, memory access and data movement"},
      {"name": "literature", "focus": "known GPU placement techniques", "web": true}
    ],
    "decider": {"principles": "prefer proposals expected to improve both objectives; after 3 iterations without KEPT, prefer a riskier proposal"},
    "critic": true
  },
  "precheck": {
    "command": ["cmake", "--build", "${LOOPKIT_AGENT_BUILD_DIR}", "--target", "placer"],
    "max_fix_attempts": 3
  },
  "run": {"gpu": "1"}
}
```

## Fields

| Field | Meaning |
|---|---|
| `schema` | Format version, 1. |
| `source` | The user's request, verbatim, for traceability. |
| `objectives` | Ordered list; ITER lines and the monitor keep this order. `name` matches `[A-Za-z_][A-Za-z0-9_]*`; `direction` is `minimize` or `maximize`; `tolerance` is `{"relative": r}` (fraction of the larger value) or `{"absolute": a}`. Differences within the tolerance count as "same". |
| `constraints` | Pass/fail conditions the score script decides. A candidate that fails one is FAILED and never compared. |
| `extra` | Values that are only recorded. Missing or undeclared extras only warn. |
| `score.command` | Argument list (no shell) run in the eval worktree root. `${LOOPKIT_EVAL_DIR}`, `${LOOPKIT_BUILD_DIR}`, `${LOOPKIT_RESULT}` and `${LOOPKIT_WORKTREE}` are expanded. Use `["bash", "-c", "..."]` if you need a shell. |
| `score.timeout_s` | Beyond this the script is killed and the candidate FAILED with `score_timeout`. |
| `score.env` | Extra environment, e.g. deterministic switches. `LOOPKIT_*` and `CUDA_VISIBLE_DEVICES` are set by the framework. `PYTHONPATH` is not inherited; set it here if the scorer needs it. |
| `eval_assets` | Paths frozen into the run's snapshot. Tracked paths are taken from c000; untracked paths from the working tree; absolute paths are copied and appear under `${LOOPKIT_EVAL_DIR}/_abs/<path>`. |
| `scope` | Files the agent may change: matching an `include` glob and no `exclude` glob. `**` spans directories, `*` stays within one; a pattern without `/` matches the file name at any depth; a trailing `/` means the whole directory. `.loopkit/`, `.claude/`, `.gitignore`, `.gitattributes`, `.gitmodules` and submodules are always protected. |
| `workflow.mode` | `single` or `multi`. |
| `workflow.analysts` | multi: `name` (letters, digits, `_`, `-`; not `agent`), `focus`, optional `questions`, `evidence`, `red_flags` (string or list), and `web: true` for a literature analyst. |
| `workflow.decider.principles` | multi: how to choose, in the user's words. |
| `workflow.critic` | multi, optional: attack the chosen idea before implementing it (two more subagent calls per iteration). |
| `precheck` | Optional compile-only check in the agent worktree, using `${LOOPKIT_AGENT_BUILD_DIR}`. `max_fix_attempts` (default 3), `timeout_s` (default 540). |
| `run.gpu` | Default `CUDA_VISIBLE_DEVICES` for scoring and for the run session; `run create --gpu` overrides it; `null` leaves it unset. |

## How init fills what the user did not say

Everything is proposed and confirmed by the user before it is written:

- **direction**: from the name and the code (hpwl, runtime: minimize).
- **tolerance**: from the spread of `loopkit trial --repeat N` on the baseline. A deterministic
  objective gets a small relative tolerance; a noisy one (runtime) a larger one, above the
  observed spread.
- **unit and description**: from the tools and their output.
- **constraints**: existing checkers in the repository (e.g. a legality checker), proposed,
  never added silently.
- **extra**: empty unless proposed.
- **eval assets**: the score script and every benchmark it reads.
- **deterministic switches**: the project's and the frameworks' (PyTorch, cuBLAS) switches, in
  `score.env`.
- **precheck**: a compile-only build command, and an attempt limit.
- **scope**: the sources plus the build files (`CMakeLists.txt`, `cmake/`), excluding
  third-party code and benchmarks. Because build files are in scope, the score script must pin
  the build options that affect correctness on its command line.
- **workflow**: ask single or multi; in multi mode, suggest one analyst per objective.
- **GPU**: ask which card; suggest one the user does not develop on.
