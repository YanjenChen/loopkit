# System Architecture

## Overview

Autoresearch v2.2.2 is a modular, markdown-driven autonomous iteration framework. The core architectural shift from v2.0.x is the **thin SKILL.md + self-contained command files** pattern: the skill file is a routing table; all protocol is embedded in 13 self-contained command files. Only the invoked command file loads per invocation, reducing token cost by ~95%.

As of v2.2.0, bare `/autoresearch` is overloaded: a `Metric:`/`Verify:` config runs the classic metric loop unchanged, while a free-form natural-language goal dispatches an **autonomous orchestrator** that classifies the goal, derives a success predicate, and loops the right subcommands until it holds. All routing decisions live in one deterministic seam, `claude-plugin/skills/autoresearch/scripts/orchestrate.sh` (mirroring the `score-regression.sh` pattern next to it), bounded by plateau detection and a hard cycle ceiling.

v2.2.1 hardens that seam with four orchestrator-safety additions: `screen-cmd` gains destructive-command coverage (netcat exfiltration, raw block-device writes across SD/eMMC·RAID·device-mapper families, `mkfs`, `find -delete`, `shred`, zero-`truncate`, recursive zero-mode `chmod`, curl/wget-into-interpreter via xargs) — including path-qualified invocations like `/sbin/mkfs.ext4`; the derived Success predicate is **pinned** verbatim into `orchestrator-state.json` and re-screened on resume via the new `screen-state-predicate` subcommand (extraction honors escaped quotes so a poisoned predicate cannot truncate the screen); a new `validate-state` subcommand gates the ledger (required fields + coarse types) before routing; and `next-hop` routes a high-impact accepted change through an independent **verify** hop (`pending_verify`) before declaring `DONE`. The seam now exposes eight subcommands: `classify`, `next-hop`, `units`, `plateau`, `screen-cmd`, `verdict`, `validate-state`, `screen-state-predicate`.

Claude Code is the only supported platform, and the plugin manager is the only install path. `claude-plugin/` is the single source of truth: it holds the commands, the skill (routing table, references, and the runtime helpers `orchestrate.sh` and `score-regression.sh` under `skills/autoresearch/scripts/`), and the hooks, and it is edited directly with no sync or generation step. The root `.claude-plugin/marketplace.json` exposes it to the plugin manager (`"source": "./claude-plugin"`).

## Component Diagram

```mermaid
graph TB
    subgraph "Claude Code Runtime"
        CC[Claude Code CLI]
        PS[Plugin System]
    end

    subgraph "Marketplace"
        MKT[.claude-plugin/marketplace.json\nsource: ./claude-plugin]
    end

    subgraph "Plugin — claude-plugin/, single source of truth"
        SKILL[skills/autoresearch/SKILL.md\nthin routing table]
        CMD[commands/autoresearch.md]
        CMDS[commands/autoresearch/*.md\n12 self-contained subcommand files]
        REF[skills/autoresearch/references/\nshared routing and review references]
        RT[skills/autoresearch/scripts/\norchestrate.sh + score-regression.sh]
        HK[hooks/\nhooks.json + 9 hook .py files]
    end

    CC --> PS --> MKT
    MKT --> SKILL & CMD & CMDS & HK
    SKILL -.routing only.-> CMDS
    CMDS --> REF
    SKILL & CMDS --> RT
```

## Data Flow — Core Autoresearch Loop

```mermaid
flowchart TD
    A[User invokes /autoresearch] --> B{Config complete?}
    B -- No --> C[AskUserQuestion batched setup]
    C --> D[Establish Baseline — Iteration 0]
    B -- Yes --> D
    D --> E[Write TSV header + metric_direction comment]
    E --> F[Read git log + last TSV rows as memory]
    F --> G[Make ONE focused change]
    G --> H[git commit — experiment: description]
    H --> I[Run Verify command → extract number]
    I --> J{Metric improved?}
    J -- Yes --> K{Guard passes?}
    K -- Yes --> L[keep — commit stays]
    K -- No --> M[rework up to 2x]
    M -- Still fails --> N[discard — git revert]
    J -- No --> N
    I -- Crash --> O[fix up to 3x]
    O -- Fixed --> I
    O -- Unfixable --> N
    N --> P[Log row to TSV]
    L --> P
    P --> Q{Eval checkpoint?}
    Q -- Yes --> R[Print 5-line checkpoint]
    Q -- No --> S{More iterations?}
    R --> S
    S -- Yes --> F
    S -- No --> T[Print summary + write handoff.json]
    T --> U{--chain?}
    U -- Yes --> V[Invoke next command]
    U -- No --> W[Done]
```

## Directory Structure

```
claude-plugin/                             # Single source of truth — edited directly
├── .claude-plugin/plugin.json             # Claude Code metadata — v2.2.2
├── commands/
│   ├── autoresearch.md                    # Core loop command — self-contained, 110 lines
│   └── autoresearch/
│       ├── debug.md                       # Hypothesis iteration loop
│       ├── evals.md                       # One-shot TSV analysis (NEW in v2.1.0)
│       ├── fix.md                         # Error-count reduction loop
│       ├── learn.md                       # Doc generation loop
│       ├── plan.md                        # Goal-to-config wizard
│       ├── predict.md                     # 5-persona one-shot debate
│       ├── improve.md                     # Product improvement research + PRD generation
│       ├── probe.md                       # Requirement interrogation loop
│       ├── reason.md                      # Adversarial refinement loop
│       ├── regression.md                  # Baseline/candidate stability gate
│       ├── scenario.md                    # 12-dimension edge case loop
│       └── security.md                    # STRIDE + OWASP loop
├── skills/autoresearch/
│   ├── SKILL.md                           # Routing table only — 41 lines
│   ├── references/
│   │   ├── predict-personas.md            # 5 default expert personas
│   │   ├── reason-judge-protocol.md       # Blind judge scoring protocol
│   │   ├── security-checklist.md          # STRIDE + OWASP checklist
│   │   └── orchestrator-routing.md        # Goal archetypes and routing contract
│   └── scripts/
│       ├── orchestrate.sh                 # Orchestrator routing seam
│       └── score-regression.sh            # Regression scoring backend
└── hooks/                                 # Hook system
    ├── hooks.json                         # Auto-registration
    ├── hook-runner.sh                     # Shell wrapper
    ├── .ckignore                          # Baseline blocked patterns
    ├── lib/                               # Shared modules
    └── [9 hook .py files]

.claude-plugin/marketplace.json            # Plugin marketplace entry — source: ./claude-plugin
```

## Hook System Architecture

Autoresearch includes defense-in-depth hook guardrails. They are registered automatically from the plugin's `hooks/hooks.json` when the plugin is enabled; there is no other registration path. Each entry runs a `.py` hook through `hook-runner.sh` (`python3 -I -B -X utf8` under a whitelisted `env -i` environment), so the hooks need Python 3.8 or newer, with `python3` on the PATH of the shell Claude Code uses. They use the Python standard library only. Nothing checks this at install time.

### Hook Lifecycle

```mermaid
graph LR
    subgraph "Safety Gates — PreToolUse"
        SB[scout-block]
        PB[privacy-block]
        DCB[dangerous-cmd-block]
    end

    subgraph "Context Injection"
        IC[iteration-context<br/>UserPromptSubmit]
        SC[subagent-context<br/>SubagentStart]
        DRR[dev-rules-reminder<br/>UserPromptSubmit]
    end

    subgraph "Quality + Notifications"
        SG[simplify-gate<br/>UserPromptSubmit]
        SI[session-init<br/>SessionStart]
        SN[stop-notify<br/>SessionEnd]
    end

    SI -->|creates| STATE["OS temp/ar-session-{hash}.json"]
    IC -->|reads/writes| STATE
    SC -->|reads| STATE
    DRR -->|reads| STATE
    SN -->|reads + cleans| STATE
```

### State Management

Hooks share `ar-session-{hash}.json` through the operating-system temporary directory (`TMPDIR`, then `TMP`, then `TEMP`, falling back to `/tmp`; hash = md5 of cwd + session_id). It is created by `session-init`, consumed by context injection hooks, and cleaned up by `stop-notify`. The hook runner preserves `TMPDIR`, `TEMP`, and `TMP` for native Windows with Git Bash as well as macOS and Linux.

### Plugin Distribution

Hooks ship inside the plugin. `hooks.json` addresses every hook through `${CLAUDE_PLUGIN_ROOT}`, so the registrations work wherever the plugin manager places the plugin:

```
claude-plugin/
├── .claude-plugin/plugin.json    # v2.2.2
├── hooks/                        # auto-registers via hooks.json
│   ├── hooks.json
│   ├── hook-runner.sh
│   ├── lib/
│   │   ├── ar_hook_utils.py
│   │   └── ignore.py
│   └── [9 hook files]
├── commands/
└── skills/
```

## Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| Thin SKILL.md routing table (41 lines) | ~95% token reduction vs monolith v2.0.x SKILL.md (813 lines) |
| Self-contained command files | Each file embeds full protocol — no reference file loading unless needed |
| Focused shared references | Only routing, personas, judge protocol, and security material shared across command boundaries warrants a reference |
| No autoresearch-command-spec.json | JSON spec removed; command contracts live in individual command files |
| `claude-plugin/` as the single source of truth | Commands, skill, runtime helpers, and hooks are edited in place with no sync or generation step, so there are no copies to drift; the marketplace entry points straight at it |
| TSV with `# metric_direction` comment | Enables evals command to auto-detect direction without user prompt |
| 8 TSV status values | baseline, keep, discard, crash, no-op, hook-blocked, metric-error, keep (reworked) |
| handoff.json for chain integration | Structured handoff between subcommands; evals reads `*-results.tsv` directly |
| Hook system with fail-open design | Hooks never block Claude due to crashes; safety without fragility, with visible redacted diagnostics on failure paths |
| Session state via OS temp file | Hooks are subprocesses and cannot share environment state; the OS temp directory persists the bounded session record across hook calls |
| Iteration-based throttling (every 5th) | Autoresearch is loop-driven; time-based throttling doesn't match iteration cadence |

## Integration Points

- **Claude Code Plugin System** — installed from `.claude-plugin/marketplace.json`; commands in `claude-plugin/commands/`, skill in `claude-plugin/skills/`
- **Claude Code Hook System** — 9 hooks auto-registered via `claude-plugin/hooks/hooks.json` when the plugin is enabled
- **Git** — memory, rollback, staleness detection, changelog generation
- **Shell** — verify and guard commands are user-defined shell expressions
- **MCP servers** — any MCP server configured in the host environment is available during loops

See also: [Project Overview](project-overview-pdr.md) | [Codebase Summary](codebase-summary.md) | [Code Standards](code-standards.md)
