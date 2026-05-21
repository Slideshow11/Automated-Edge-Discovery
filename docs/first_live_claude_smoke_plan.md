# First Live-Claude Smoke Plan v0

**Status:** DESIGN ONLY — do not execute
**Branch:** `docs/first-live-claude-smoke-plan-v0`
**Created:** 2026-05-21

---

## Overview

This document defines the first live-Claude smoke test for PR #287's real executor
implementation. It is **planning only** — no live Claude execution occurs in this PR.

The smoke validates that `execution.mode="claude"` with `--enable-real-claude-executor`
produces a real Claude invocation inside a disposable temp worktree, with all guards
(PMG, command contract, forbidden patterns) functioning correctly.

---

## Smoke Objective

Perform one tiny documentation-only edit using the real Claude CLI inside a
disposable Git worktree. The edit modifies exactly one allowed file with content
approved by a human before execution. No patch is applied automatically — a human
reviews the output before any decision.

---

## Target File

**Primary:** `docs/live_smoke_scratch.md` (created during smoke)

This file is used exclusively for the smoke. It is created by the harness
in the worktree, contains no existing content, and is removed from the review
discussion after the smoke succeeds.

Alternative: `docs/temp_worktree_execution_v1_design.md` (append a comment)
if `live_smoke_scratch.md` is not desirable.

**Decision:** Use `docs/live_smoke_scratch.md` — new file, harmless, easy to review.

---

## Execution Packet

```json
{
  "packet_kind": "aed.temp_worktree.execution.v0",
  "run_id": "first_live_claude_smoke_001",
  "task_id": "SMOKE-001",
  "base_sha": "<current main SHA at time of execution>",
  "approved_plan_path": "/tmp/aed_runs/first_live_claude_smoke_plan_v0/approved_plan.md",
  "approved_plan_sha256": "<sha256 of approved_plan.md>",
  "approval": {
    "approved_for_temp_worktree_execution": true,
    "approved_by": "human",
    "approved_plan_sha256": "<sha256 of approved_plan.md>",
    "approved_at": "<ISO-8601 timestamp within 24h>",
    "max_changed_files": 1
  },
  "task": {
    "description": "First live Claude smoke: append a single line to docs/live_smoke_scratch.md",
    "allowed_files": ["docs/live_smoke_scratch.md"],
    "forbidden_files": [
      ".git/",
      ".github/",
      "scripts/local/final_gate_status.py",
      "scripts/local/verify_final_head_merge_command.py",
      "scripts/local/check_persistent_mutation_guard.py",
      "scripts/local/run_temp_worktree_execution.py",
      "scripts/local/check_real_executor_readiness.py",
      "scripts/local/check_real_claude_env_preflight.py",
      "scripts/local/audit_claude_invocation.py",
      "scripts/local/run_plan_preview.py",
      "scripts/local/plan_preview_eval_status.py",
      "/home/max/.hermes/",
      "audit/",
      "boards/",
      "memory/",
      "profile/"
    ],
    "do_not": [
      "git push", "gh pr create", "gh pr merge", "gh api",
      "pip install", "npm install", "apt install", "yum install", "brew install",
      "sudo", "dispatch", "board", "Hermes", "audit", "memory", "profile",
      "repair loop", "|", "&&", "||"
    ]
  },
  "execution": {
    "mode": "claude",
    "timeout_seconds": 60,
    "output_root": "/tmp/aed_runs/first_live_claude_smoke_001"
  }
}
```

---

## Execution Settings

| Setting | Value |
|---------|-------|
| `execution.mode` | `"claude"` |
| `--enable-real-claude-executor` | Required at manual execution time |
| `max_changed_files` | `1` |
| `timeout_seconds` | `60` |
| repair loop | disabled |
| tests | disabled during live run |
| output_root | `/tmp/aed_runs/first_live_claude_smoke_001` |
| worktree | `/tmp/aed_runs/worktrees/first_live_claude_smoke_001` |
| PMG target | `~/.hermes` |

---

## Preflight Checklist

Complete all items before issuing the `--enable-real-claude-executor` flag:

### Repository State

- [ ] `git status --short` is clean (no staged, no unstaged modifications)
- [ ] No uncommitted changes anywhere in the repo

### Readiness Checker

- [ ] `python3 scripts/local/check_real_executor_readiness.py --output-json /tmp/readiness.json --output-md /tmp/readiness.md`
- [ ] Status must be `READY_TO_IMPLEMENT_REAL_EXECUTOR`
- [ ] `real_executor_allowed` must be `false` (verifier only authorizes, does not enable)
- [ ] No missing checklist items

### Environment Preflight

- [ ] `python3 scripts/local/check_real_claude_env_preflight.py` (without `--allow-claude-help-probe`)
- [ ] All checks pass (Claude binary check is informational only)
- [ ] No errors reported

### PMG Snapshot

- [ ] Choose PMG snapshot path: `/tmp/aed_runs/first_live_claude_smoke_plan_v0/pmg_pre_smoke_snapshot.json`
- [ ] `python3 scripts/local/check_persistent_mutation_guard.py snapshot --root ~/.hermes --output /tmp/aed_runs/first_live_claude_smoke_plan_v0/pmg_pre_smoke_snapshot.json`
- [ ] Snapshot written successfully

### Packet Review

- [ ] Human reviews `intended_packet_preview.json` artifact
- [ ] `approved_plan.md` content reviewed and approved
- [ ] `allowed_files.txt` contains exactly one file: `docs/live_smoke_scratch.md`
- [ ] `forbidden_files.txt` includes all protected gate scripts
- [ ] `do_not.txt` includes all prohibited operations
- [ ] `execution.mode` is `"claude"`
- [ ] `timeout_seconds` is `60`
- [ ] `max_changed_files` is `1`

### TTY Requirement

- [ ] Live run must occur in an interactive TTY session
- [ ] User is present and monitoring the execution
- [ ] User has confirmed readiness to abort if needed

### Explicit Approval

- [ ] User sends a separate message explicitly approving the live run
- [ ] Message includes: "I approve running live Claude smoke for first_live_claude_smoke_001"
- [ ] Do not execute without this explicit approval

---

## Abort Rules

Abort immediately (do not pass `--enable-real-claude-executor`) if any of the
following are true:

- [ ] Git status is dirty
- [ ] PMG is dirty (compare shows `status != "clean"`)
- [ ] No interactive TTY available
- [ ] Claude binary check fails (informational — still abort if user not present)
- [ ] `check_real_executor_readiness.py` returns any `HOLD_*` state
- [ ] `check_real_claude_env_preflight.py` reports an error (not informational warning)
- [ ] Command contract validation fails (checked by harness before subprocess)
- [ ] `allowed_files` contains more than one file
- [ ] `forbidden_files` does not include all protected gate scripts
- [ ] `output_root` is inside the AED repo
- [ ] `timeout_seconds` is missing or `<= 0`
- [ ] Any proposed command element includes: `git push`, `gh pr`, `gh api`, `dispatch`,
      `pip install`, `npm install`, `apt install`, `yum install`, `brew install`,
      `board`, `Hermes`, `audit`, `memory`, `profile`, `sudo`
- [ ] User has not sent explicit approval message

---

## Post-Run Checklist

After the live run completes (regardless of outcome):

- [ ] Inspect `result.json` — check `status` field
- [ ] If `status` is `PATCH_READY_FOR_HUMAN_REVIEW`: diff is ready for review
- [ ] Inspect `diff.patch` — verify only `docs/live_smoke_scratch.md` is changed
- [ ] Inspect `claude_stdout.txt` — confirm Claude produced output
- [ ] Inspect `claude_stderr.txt` — check for warnings or errors
- [ ] Inspect `claude_transcript.md` — review full execution transcript
- [ ] Run `python3 scripts/local/audit_claude_invocation.py` on the output directory
- [ ] Run `python3 scripts/local/check_persistent_mutation_guard.py compare --root ~/.hermes --before <snapshot> --output-json <compare.json> --output-md <compare.md>`
- [ ] Confirm main repo `git status --short` is clean after worktree cleanup
- [ ] Do **not** apply the patch automatically
- [ ] Human decides: discard, apply, or defer

---

## Smoke Design Principles

1. **Minimal scope** — one file, one edit, one outcome
2. **Defensive defaults** — all gates enabled, no shortcuts
3. **Human in the loop** — explicit approval required before live execution
4. **No automatic patch application** — human reviews everything before deciding
5. **Abort on any anomaly** — any deviation from expected triggers immediate abort
6. **TTY required** — live execution requires an interactive session with a present user
7. **PMG protected** — Hermes home tree is snapshotted and compared
8. **Contract validated** — command contract is validated before any subprocess call
9. **No side effects** — no PR creation, no merge, no push, no dispatch, no board updates
10. **No repair loop** — if something fails, abort and investigate

---

## Artifact Locations

All artifacts are under `/tmp/aed_runs/first_live_claude_smoke_plan_v0/`:

| File | Purpose |
|------|---------|
| `approved_plan.md` | Human-approved plan for the smoke |
| `allowed_files.txt` | List of allowed files (exactly 1) |
| `forbidden_files.txt` | List of forbidden files and paths |
| `do_not.txt` | Operations prohibited during smoke |
| `intended_packet_preview.json` | Preview of the execution packet |
| `pmg_pre_smoke_snapshot.json` | PMG snapshot before smoke (created during preflight) |

---

## Validation Commands

Run these locally before opening a PR (design-only branch, no live execution):

```bash
# Tests
pytest tests/test_run_temp_worktree_execution.py -q
pytest tests/test_audit_claude_invocation.py -q
pytest tests/test_check_real_executor_readiness.py tests/test_check_real_claude_env_preflight.py -q

# Compile check
python3 -m compileall scripts/local tests -q
```

---

## Merge Requirements

After opening the PR:

1. CI must be green on the design-only branch
2. One Codex review (design-only, no code execution needed)
3. `final_gate_status.py` must return `READY_TO_MERGE`
4. `verify_final_head_merge_command.py --require-pmg` must return `MERGE_READY_CANDIDATE`
5. Merge with `--match-head-commit` only

---

**IMPORTANT:** This document is DESIGN ONLY. No live Claude execution is performed
by opening or merging this PR. The first live smoke execution requires a separate
explicit approval message from a human, and must follow the preflight checklist
exactly.