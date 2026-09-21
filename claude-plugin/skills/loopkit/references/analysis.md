# Analysis templates

## Analyst persona (multi mode)

`/loopkit:init` expands each analyst the user describes in a sentence ("runtime: GPU kernels
and data movement") into a persona in the config. `/loopkit:iter` passes the persona to a
`loopkit:analyst` (or `loopkit:analyst-web`) subagent. A good persona has:

| Field | What to write |
|---|---|
| `focus` | The angle, one sentence: what this analyst is responsible for. |
| `questions` | 3 to 5 questions it must answer every iteration, specific to this code, e.g. "Which kernel dominates global placement time on the largest benchmark, and why?" |
| `evidence` | What each answer must cite: file:line, profiler output, score logs, extra values, numbers from `loopkit show`. |
| `red_flags` | What to look out for, e.g. "numerics that depend on thread scheduling", "changes that trade legality for wirelength", "parameters tuned to one benchmark". |
| `web` | `true` for a literature analyst, which also searches the web for known techniques. |

Example set for a GPU placer optimizing hpwl and runtime:

- **wirelength**: algorithms, parameters and numerics that affect HPWL. Questions: which terms
  dominate the objective near convergence; where the density penalty schedule is set and how
  it behaves on the largest design; what the last KEPT wirelength change did. Red flags:
  overfitting a parameter to one benchmark, legality regressions.
- **runtime**: GPU kernels, memory access and data movement. Questions: which kernel
  dominates; host-device transfers per iteration; occupancy and memory coalescing of the hot
  kernels. Red flags: non-deterministic reductions, float atomics that change results.
- **literature** (`web: true`): known GPU placement techniques (e.g. from DREAMPlace-style
  work) that fit the current code, with sources.

## Proposal format

Analysts return up to 3 proposals; single mode writes one in the same format:

```
- idea: <one sentence>
- rationale and evidence: <file:line, log excerpts, numbers, source URLs>
- expected impact: <per objective: direction and rough size>
- confidence: <0-100%>
- risk: <constraints, determinism, build>
- files: <files to change>
```

## Learned note

Every `loopkit record` takes a `--learned` note of at most 300 characters, in hypothesis form:

```
hypothesis: <what the change was expected to do and why>; result: <confirmed|disproven|inconclusive>; evidence: <numbers or log facts>
```

Example: `hypothesis: fusing the density and gradient kernels removes one global-memory pass;
result: confirmed; evidence: runtime -6.1%, hpwl same, kernel count 14 -> 13`.

Later iterations read these notes in `loopkit summary` to avoid repeating disproven ideas and
to build on confirmed ones.
