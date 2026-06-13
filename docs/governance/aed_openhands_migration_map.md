# AED → OpenHands Migration Map

> Translates every AED governance rule into an OpenHands
> implementation target. The intent is a clean migration with no
> drift, no duplication, and no forgotten governance rules.

## 1. Migration objective

Move AED governance enforcement from a heterogeneous mix of operator
prompts, ad-hoc scripts, CI checks, and prompt-only rules into a
mandatory OpenHands broker that wraps every file, shell, git,
GitHub, Codex, report, and merge operation. The current 30-rule
inventory (`aed_rules_inventory.md`) becomes a typed policy
declarative file consumed by the broker at startup.

**This is preparation, not implementation.** This document is the
proposed target architecture. No OpenHands code is being written in
this PR; a follow-on PR will introduce the plugin skeleton, the SDK
runner, and the safe tool wrappers.

## 2. Design principle

> **AED policy must not be an optional model-called tool. It must be
> a mandatory broker around file, shell, git, GitHub, Codex, report,
> and merge operations.**

Concretely:

- Every `Bash`, `FileEdit`, `FileRead`, `IPythonRunCell`, and
  `WebSearch` call in OpenHands is intercepted by an
  `AEDPreToolUse` hook.
- The hook consults the typed policy declaration
  (`aed_policy/policy.py`) and either:
  - **Allows** the call (and logs it).
  - **Refuses** the call (and returns a structured `AEDDecision`
    that the model must follow).
  - **Allows-with-condition** (e.g., requires an `auth.phrase`
    field in the tool call payload).
- Every `AgentFinishAction` is intercepted by an `AEDStopGate` hook
  that runs the lifecycle state machine and refuses to finalize if
  the task is in a `HOLD_*` state.
- Every allowed call is logged to `AEDLifecycleStateStore` so
  resume-checkpoint (AED-RULE-022) works without re-running completed
  mutations.

The model is never the final enforcement. Prompts are hints; the
broker is the law.

## 3. OpenHands target architecture

```
┌───────────────────────────────────────────────────────────────┐
│                     OpenHands Agent Loop                     │
│  (model decides what to do; emits Action / tool calls)       │
└─────────────────────┬─────────────────────────────────────────┘
                      │
                      ▼
┌───────────────────────────────────────────────────────────────┐
│                  AEDPreToolUse Hook (mandatory)              │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  AEDPolicy (typed declarative)                          │  │
│  │  - rule IDs                                            │  │
│  │  - tool → rule map                                     │  │
│  │  - per-rule enforcement strength                       │  │
│  │  - per-tool forbidden patterns                          │  │
│  │  - per-call auth.phrase requirements                    │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────┬───────────────────┬─────────────────────┬─────────────┘
       │                   │                     │
       ▼                   ▼                     ▼
   allow+log          refuse (AEDDecision)    allow+condition
       │                   │                     │
       ▼                   ▼                     ▼
┌───────────────────────────────────────────────────────────────┐
│                Safe Tool Wrappers (mandatory)                │
│  - AEDFileTool       (read/write under /tmp/aed_runs/...)    │
│  - AEDTerminalTool   (bounded subshell)                      │
│  - AEDGitTool        (refuses primary-worktree mutation)     │
│  - AEDGitHubTool     (refuses --admin, --auto, force-push)   │
│  - AEDCodexPingTool  (pre-post scan + de-dup)                │
│  - AEDCodexClassifierTool (read-only classifier)             │
│  - AEDAuditTool      (append-only audit)                     │
│  - AEDReportTool     (structured lifecycle packet)           │
└─────────────────────┬─────────────────────────────────────────┘
                      │
                      ▼
┌───────────────────────────────────────────────────────────────┐
│                AEDLifecycleStateStore                        │
│  - per-task ledger (PR #, head, completed phases,            │
│    remaining permitted mutations, already-performed)          │
│  - resume-checkpoint state                                   │
│  - audit append (append-only)                                │
└───────────────────────────────────────────────────────────────┐
                      │
                      ▼
┌───────────────────────────────────────────────────────────────┐
│                AEDStopGate Hook (mandatory)                  │
│  - rejects AgentFinishAction when lifecycle is in HOLD_*     │
│  - requires MERGE_READY_AWAITING_HUMAN_AUTHORIZATION +       │
│    explicit auth.phrase for the merge                        │
└───────────────────────────────────────────────────────────────┘
```

## 4. Rule → OpenHands target mapping

The following table maps each `AED-RULE-NNN` from
`aed_rules_inventory.md` to its OpenHands target. Many rules share
the same target (e.g., most classifier rules share
`AEDCodexClassifierTool`); the table lists the *primary* target
unless otherwise noted.

| Rule ID | OpenHands target | Notes |
|---------|------------------|-------|
| AED-RULE-001 | `AEDGitTool` PreToolUse | Refuse mutation when `cwd == primary_worktree_path` and `auth.phrase` absent |
| AED-RULE-002 | `AEDGitTool` PreToolUse | Same hook; rule differentiates via "no fetch+merge" pattern |
| AED-RULE-003 | `AEDWorkspaceProvisioner` | Reject execution outside `/tmp/aed_runs/worktrees/` |
| AED-RULE-004 | `AEDPolicy` SHA validator | Format check on every SHA field |
| AED-RULE-005 | `AEDGitHubTool` PreToolUse | Re-fetch `gh pr view --json headRefOid` before merge-authorizing calls |
| AED-RULE-006 | `AEDMergeTool` argparse + `AEDPolicy` forbidden-flag list | Defense in depth |
| AED-RULE-007 | `AEDPolicy` `auth.phrase` gate | Required field on `AEDMergeTool` |
| AED-RULE-008 | `AEDGitHubTool` `resolveReviewThread` gate | Per-thread `auth.phrase` + stale-policy check |
| AED-RULE-009 | `AEDCodexClassifierTool` | Existing classifier |
| AED-RULE-010 | `AEDCodexPingTool` de-dup by `(pr, head)` | |
| AED-RULE-011 | `AEDCodexClassifierTool` | Existing clean-pass logic |
| AED-RULE-012 | `AEDCodexClassifierTool` | Existing non-head filter |
| AED-RULE-013 | `AEDCodexClassifierTool` | Existing inventory check |
| AED-RULE-014 | `AEDCodexClassifierTool` | Existing inventory check |
| AED-RULE-015 | `AEDCodexClassifierTool` | Existing inventory check |
| AED-RULE-016 | `AEDCodexClassifierTool` | Existing nested-pagination preserve |
| AED-RULE-017 | `AEDPolicy` ISO 8601 + tz validator | Per-timestamp field |
| AED-RULE-018 | `AEDCI` (post-tool-use CI monitor) | Block merge while non-pass |
| AED-RULE-019 | `AEDPolicy` post-diff scope-guard invocation | Reuse `scope_guard.py` |
| AED-RULE-020 | `AEDAuditTool` (append-only) | OS-level append-only file mode |
| AED-RULE-021 | `AEDGitHubTool` protected-PR list | Read from `schemas/aed_protected_prs.json` (proposed) |
| AED-RULE-022 | `AEDLifecycleStateStore` resume-checkpoint | Reject duplicate mutations |
| AED-RULE-023 | `AEDCodexPingTool` pre-post scan | Move scan logic to a shared helper |
| AED-RULE-024 | `AEDPolicy` forbidden-operation list + tool-level rejection | Multi-target |
| AED-RULE-025 | `AEDPolicy` bounded-poll flag | Required on every polling tool call |
| AED-RULE-026 | `AEDReportTool` template | Requires `post_ping_findings` array |
| AED-RULE-027 | `AEDLifecycleStateStore` transition validator | Schema-backed |
| AED-RULE-028 | `AEDCodexClassifierTool` | Existing formal clean-pass branch |
| AED-RULE-029 | `AEDCodexClassifierTool` | Existing per-poll reset |
| AED-RULE-030 | (meta) | Drives the migration's scope |

## 5. Proposed plugin layout

A new OpenHands plugin at `aed-governance` (lives in
`openhands/aed-governance/` or the repo's `.plugin/` directory in a
follow-on PR):

```
.plugin/
  plugin.json                    # OpenHands plugin manifest
skills/
  aed-governance/
    SKILL.md                     # AED-specific prompt hints (operator-facing)
hooks/
  hooks.json                     # OpenHands hook manifest
  aed_pre_tool_use.py            # AEDPreToolUse hook
  aed_stop_gate.py               # AEDStopGate hook
  aed_post_tool_log.py           # AEDPostToolUse logger
commands/
  aed-status.md                  # /aed-status lifecycle summary
  aed-continue.md                # /aed-continue resume-checkpoint
  aed-wait-codex.md              # /aed-wait-codex bounded Codex wait
  aed-fix.md                     # /aed-fix code-fix turn
  aed-merge.md                   # /aed-merge guarded merge
  aed-resolve-threads.md         # /aed-resolve-threads with auth
  aed-ping.md                    # /aed-ping with pre-post scan
```

## 6. Proposed SDK runner layout

A standalone runner that can be invoked from a CI job or from the
operator's terminal without OpenHands running in interactive mode:

```
aed_run.py                        # entry point: python aed_run.py <task>
aed_policy/
  __init__.py
  policy.py                       # typed rule list + per-tool map
  action_types.py                 # TypedDict for each tool call
  decisions.py                    # AEDDecision + decision reason codes
  run_state.py                    # AEDLifecycleStateStore (per-task ledger)
aed_tools/                        # safe tool wrappers (also used in plugin)
  file.py                         # AEDFileTool
  terminal.py                     # AEDTerminalTool
  git.py                          # AEDGitTool
  github.py                       # AEDGitHubTool
  codex_ping.py                   # AEDCodexPingTool
  codex_classifier.py             # AEDCodexClassifierTool
  audit.py                        # AEDAuditTool
  report.py                       # AEDReportTool
aed_report_builder.py             # structured lifecycle packet builder
aed_delegate_openhands.py        # optional: spawn OpenHands sub-process
```

## 7. Safe tool inventory

Each safe tool wraps one OpenHands / Humphry / SDK primitive and
applies the relevant rules. None of them are optional at runtime.

| Tool | Wraps | Rules it enforces |
|------|-------|-------------------|
| `AEDFileTool` | `FileReadAction`, `FileEditAction`, `FileWriteAction` | 003 (path), 020 (no audit-log mutation) |
| `AEDTerminalTool` | `BashAction`, `IPythonRunCellAction` | 001, 002, 024, 025, 030 (prompt-derived) |
| `AEDGitTool` | `BashAction` matching `git *` | 001, 002, 004, 021, 024, 030 |
| `AEDGitHubTool` | `BashAction` matching `gh *` | 005, 006, 008, 021, 024 |
| `AEDCodexPingTool` | `BashAction` matching `gh pr comment *` | 010, 023, 024 |
| `AEDCodexClassifierTool` | `BashAction` matching `audit_codex_response_for_pr.py *` | 009, 011, 012, 013, 014, 015, 016, 028, 029 |
| `AEDAuditTool` | `BashAction` matching `append_merge_action_audit.py *` | 020 |
| `AEDReportTool` | template invocation | 026, 027 |
| `AEDCI` | `BashAction` matching `gh pr checks *` | 018 |
| `AEDMergeTool` | `BashAction` matching `gh pr merge *` | 005, 006, 007, 018, 024 |

## 8. Hook inventory

| Hook | Trigger | Responsibility |
|------|---------|----------------|
| `AEDPreToolUse` | every `Action` | consult `AEDPolicy`, return `AEDDecision` |
| `AEDStopGate` | `AgentFinishAction` | refuse finalize when lifecycle is `HOLD_*`; require `auth.phrase` for merge |
| `AEDPostToolUse` | every allowed `Action` | log to `AEDLifecycleStateStore` and (where applicable) to `AEDAuditTool` |

## 9. State-store inventory

`AEDLifecycleStateStore` persists per-task state in
`/tmp/aed_runs/worktrees/<task>/.aed_state.json`:

```json
{
  "task_id": "...",
  "pr_number": 403,
  "pr_url": "...",
  "expected_head_sha": "...",
  "current_lifecycle_state": "HOLD_NEW_CODEX_THREAD",
  "completed_phases": ["phase-1", "phase-2"],
  "remaining_permitted_mutations": ["codex_ping", "thread_resolve"],
  "already_performed_mutations": [
    {"tool": "AEDCodexPingTool", "args": {...}, "result_sha": "..."}
  ],
  "protected_prs": [384, 386, 397, 398, 399, 400, 401, 402]
}
```

The store is the single source of truth for resume-checkpoint
(AED-RULE-022). It is itself an append-only file (audit
chain-of-custody).

## 10. Report artifacts

`AEDReportTool` produces a structured lifecycle packet (the
classifier's existing `aed.codex_response.classifier.v0` schema is
the v0 form; the v1 form adds a `post_ping_findings` array and an
`older_anchored_findings` array per AED-RULE-026):

```json
{
  "packet_kind": "aed.lifecycle.report.v1",
  "schema_version": 1,
  "status": "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
  "pr_number": 403,
  "expected_head_sha": "...",
  "post_ping_findings": [],
  "older_anchored_findings": [
    {"thread_id": "...", "db_id": ..., "severity": "P2", "title": "..."}
  ],
  "ci_status": {"governance-validators": "pass", "...": "pass"},
  "auth": {"phrase": "I authorize guarded squash merge of PR #N at exact head <sha>"},
  "audit": {"entry_sha": "..."}
}
```

## 11. Prototype sequence

The OpenHands migration is staged so each step is a small, scoped
PR with its own closeout:

1. **Docs inventory PR (this PR, #403).** Canonical rule list,
   enforcement matrix, migration map. Docs-only.
2. **AED policy engine skeleton.** Standalone `aed_run.py` that
   loads `aed_policy/policy.py` and rejects a hand-crafted set of
   bad tool calls. No OpenHands integration yet.
3. **OpenHands plugin skeleton.** `.plugin/plugin.json` and
   `hooks/hooks.json` with `AEDPreToolUse` returning `allow` for
   every call. No policy yet.
4. **Safe tool wrappers.** `AEDFileTool`, `AEDTerminalTool`,
   `AEDGitTool` (read-only first, then mutating-with-`auth.phrase`).
5. **Humphry command bridge.** `/aed-status`, `/aed-continue`,
   `/aed-wait-codex`, `/aed-fix`, `/aed-merge` implemented as
   OpenHands commands that route through the safe tool wrappers.
6. **Full PR lifecycle runner.** End-to-end demo on a small
   docs-only PR with one PR-cycle.

After step 6, soft rules (AED-RULE-001, -002, -003, -007, -021,
-023 pre-post, -026, -030) should all be hard-enforced.

## 12. Non-goals for the first OpenHands PR

The migration explicitly does NOT do the following in its first
implementation PR:

- **Do not fork OpenHands yet.** The migration uses the published
  OpenHands plugin contract. A fork is a much larger commitment
  and requires separate authorization.
- **Do not replace Humphry yet.** Humphry continues to own the
  operator-facing command surface; OpenHands runs alongside it via
  the plugin bridge. A future PR may migrate the operator surface
  once the OpenHands path is proven.
- **Do not automate merge without human exact-head authorization.**
  Every merge still requires the exact authorization phrase from
  `docs/merge_authorization_guard.md`. The OpenHands broker makes
  this gate harder to bypass, not easier.
- **Do not relax any AED rule.** The OpenHands broker is
  *additive* enforcement. Existing rules stay; prompts become
  backstops, not primary controls.
- **Do not rely on prompts as final enforcement.** Every rule that
  is currently prompt-only is promoted to a hard rule by the
  migration. Prompts become hints, not laws.

## 13. Open questions

1. Should the OpenHands plugin live in this repo
   (`.plugin/aed-governance/`) or in a separate repo
   (`Slideshow11/aed-openhands`)? A separate repo lets the plugin
   version independently of the AED docs; an in-repo plugin keeps
   the policy and the implementation in lockstep.
2. Should the `AEDLifecycleStateStore` be a single file per task, or
   a shared sqlite database across tasks? The single-file form is
   simpler to reason about; the shared form enables cross-task
   audit chains.
3. Should the safe tool wrappers be implemented as Python
   decorators over the existing OpenHands tool classes, or as
   standalone classes that subclass the tool interfaces?
4. Should the policy declaration be a Python module
   (`aed_policy/policy.py`) or a JSON/YAML declarative file
   (`aed_policy/policy.json`)? A Python module allows programmatic
   rule construction; a declarative file is easier to audit but
   less expressive.
