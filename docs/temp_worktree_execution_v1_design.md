# Temp-Worktree Execution v1 Design

**Version:** 1.0-draft
**Branch:** `docs/temp-worktree-execution-v1-design`
**Status:** Design only — no implementation

---

## 1. Purpose

The temp-worktree execution layer is the first safe code-editing capability for AED.

**Narrow v1 goal:** Given an approved plan and a worker packet, AED creates a disposable Git worktree, runs Claude Code to edit files only inside that worktree, captures the diff, validates it, and stops for human review before any changes reach the main repo checkout or any branch.

The execution path is:

```
approved plan + packet
    → create temp worktree at /tmp/aed_runs/worktrees/<run_id>/
    → run Claude (cwd = temp worktree only)
    → collect diff from temp worktree
    → validate diff against packet constraints
    → STOP for human diff review
    → (human decides next step — apply patch, discard, or rework)
```

**What this achieves:** Claude can propose real code changes against an approved plan, review the exact diff before anything is applied, and a human remains in full control of whether the diff proceeds anywhere.

**What this does not achieve:** Autonomous execution, automatic PR creation, automatic merge, unattended operation, or any path that bypasses human review at any stage.

---

## 2. Non-Goals

This design explicitly excludes:

| Excluded | Reason |
|----------|--------|
| Unattended execution | Human must review every diff before any action |
| Automatic PR creation | Human approves applying patch to a branch |
| Automatic merge | Human approves through existing final gate |
| Production dispatch | No dispatch until design is proven |
| Board updates | No Kanban/task mutations |
| Hermes skill creation | Skills are configuration, not editable by execution |
| Audit append automation | Audit only via approved workflow |
| Package installation | Blocked by constraint unless explicitly allowed |
| Live trading or deployment | Out of scope for AED v1 |
| Editing the main checkout | Main checkout is read-only during execution |
| Broad refactors | Scope is limited by approved plan |
| Multi-repo edits | Single-repo execution only |
| Autonomous repair loops | Repair requires explicit human approval |
| Changes to gate scripts | Gate scripts are protected unless explicitly allowed |
| Test modification | Tests are protected unless explicitly allowed in packet |
| Running tests by default | Tests only run if packet explicitly allows them |
| Claude self-repair | Claude cannot expand its own constraints |

---

## 3. Threat Model

The temp-worktree execution layer introduces a new execution context with its own threat surface. Claude Code runs with write capability inside a disposable worktree. The threat model maps each risk to a blocking state.

### 3.1 File-Boundary Threats

| Threat | Trigger | Blocking State |
|---------|---------|----------------|
| Claude edits forbidden files | Any forbidden file appears in diff | `HOLD_FORBIDDEN_FILE_TOUCHED` |
| Claude edits outside allowed_files | Any changed file not in allowed_files | `HOLD_OUTSIDE_ALLOWED_FILES` |
| Claude mutates main repo instead of temp worktree | Main repo git status changes post-execution | `HOLD_REPO_MUTATION` |
| Claude writes outside the temp worktree | File changed outside worktree root | `HOLD_REPO_MUTATION` |
| Claude modifies gate scripts | Any gate script (final_gate_status.py, verify_final_head_merge_command.py, etc.) in diff | `HOLD_GATE_SCRIPT_MODIFIED` |
| Claude modifies tests to pass falsely | Test file changed AND test outcomes changed to pass | `HOLD_TEST_POLICY_VIOLATION` |
| Claude deletes safety tests | Test file deleted or all assertions removed | `HOLD_TEST_POLICY_VIOLATION` |
| Claude modifies worker packet | packet file modified after approval | `HOLD_PACKET_TAMPERED` |
| Claude modifies approved plan | approved_plan.txt modified after approval | `HOLD_APPROVED_PLAN_TAMPERED` |
| Claude creates hidden dotfiles | `.env`, `.secrets`, SSH keys, tokens created | `HOLD_HIDDEN_FILE_CREATED` |
| Claude expands scope beyond approved plan | Diff includes files not mentioned in approved plan | `HOLD_SCOPE_CREEP` |

### 3.2 Constraint Violation Threats

| Threat | Trigger | Blocking State |
|---------|---------|----------------|
| Claude installs packages | package manager invoked (pip, npm, apt, etc.) | `HOLD_PACKAGE_INSTALL_ATTEMPT` |
| Claude runs network commands | curl, wget, requests, http client calls | `HOLD_NETWORK_COMMAND_ATTEMPTED` |
| Claude pushes or creates PRs | `git push`, `gh pr create`, `gh pr merge` | `HOLD_GIT_PUSH_ATTEMPTED` |
| Claude touches Hermes tree | Any change to `.hermes/` or Hermes config | `HOLD_HERMES_MUTATION` |
| Claude writes to audit log | Any write to audit log path | `HOLD_AUDIT_APPEND_ATTEMPTED` |
| Claude updates memory/profile | Any write to memory store or user profile | `HOLD_MEMORY_PROFILE_WRITE_ATTEMPTED` |
| Claude creates or modifies dispatch | Dispatch file creation or modification | `HOLD_DISPATCH_MUTATION_ATTEMPTED` |
| Claude modifies board | Kanban or task board API call | `HOLD_BOARD_MUTATION_ATTEMPTED` |
| Claude runs package installation during test | Test invokes pip, npm, etc. | `HOLD_PACKAGE_INSTALL_ATTEMPT` |
| Claude exceeds execution timeout | Elapsed time exceeds packet timeout | `HOLD_CLAUDE_TIMEOUT` |
| Claude crashes or produces error | Claude invocation returns non-zero | `HOLD_CLAUDE_ERROR` |

### 3.3 Diff Quality Threats

| Threat | Trigger | Blocking State |
|---------|---------|----------------|
| Diff contains binary artifacts | Binary file in diff output | `HOLD_BINARY_ARTIFACT_IN_DIFF` |
| Diff contains unexpectedly large files | Any file >1MB in diff | `HOLD_LARGE_FILE_IN_DIFF` |
| Diff is empty | No changes captured | `HOLD_EMPTY_DIFF` |
| Diff is not human-readable | Diff cannot be parsed by standard tools | `HOLD_DIFF_UNREADABLE` |
| Dependency file changed without policy | package.json, requirements.txt, go.mod, etc. modified | `HOLD_DEPENDENCY_FILE_CHANGED` |
| Generated file not excluded | __pycache__, .pyc, node_modules in diff | `HOLD_GENERATED_ARTIFACT_IN_DIFF` |
| Diff validation fails | File validation rules not satisfied | `HOLD_DIFF_VALIDATION_FAILED` |

---

## 4. Worktree Lifecycle

The worktree lifecycle defines every step from packet approval to final output.

### 4.1 Pre-Execution Phase

```
Step 1 — Capture main HEAD
    - Record current main SHA (must be clean, must match packet.base_sha)
    - Verify git status on main is clean (no staged, no unstaged, no untracked)
    - Verify main is at packet.base_sha
    - If dirty: block with HOLD_MAIN_DIRTY
    - If SHA mismatch: block with HOLD_BASE_SHA_MISMATCH

Step 2 — Create worktree directory
    - Root: /tmp/aed_runs/worktrees/<run_id>/
    - Must be outside the main repo checkout
    - Must not exist before creation
    - If creation fails: block with HOLD_WORKTREE_CREATE_FAILED
    - Record worktree_path in execution packet

Step 3 — Create Git worktree
    - git worktree add <worktree_path> <base_sha>
    - Worktree is created from exact base SHA (the SHA the approved plan was authored against)
    - Worktree branch name: refs/worktrees/<run_id>
    - If git worktree add fails: block with HOLD_WORKTREE_CREATE_FAILED

Step 4 — Store execution packet outside repo
    - /tmp/aed_runs/<run_id>/execution_packet.json (read-only copy of approved packet)
    - /tmp/aed_runs/<run_id>/approved_plan.txt (read-only copy of approved plan)
    - /tmp/aed_runs/<run_id>/base_sha.txt
    - /tmp/aed_runs/<run_id>/worktree_path.txt
    - These files are immutable once written; any subsequent modification triggers HOLD_PACKET_TAMPERED

Step 5 — Pre-execution PMG snapshot
    - Run PMG (Persistent Mutation Guard) against the main repo
    - Snapshot stored at /tmp/aed_runs/<run_id>/pmg_snapshot.json
    - This establishes the baseline: nothing from the main repo can be claimed as "was already like that"

Step 6 — Pre-execution git status capture
    - git status on main repo → /tmp/aed_runs/<run_id>/pre_main_git_status.txt (must be clean)
    - git status on worktree → /tmp/aed_runs/<run_id>/pre_worktree_git_status.txt
    - Any divergence here blocks execution

Step 7 — Human approval marker validation
    - Read approved_for_temp_worktree_execution from packet
    - Validate approved_plan_sha256 matches actual SHA-256 of approved_plan.txt
    - If not present or SHA mismatch: block with HOLD_PLAN_NOT_APPROVED
    - Record approved_by and approved_at in execution summary

Step 8 — Allowed-files validation
    - If allowed_files is empty: block with HOLD_ALLOWED_FILES_EMPTY unless packet explicitly allows all-files mode
    - Verify allowed_files paths are relative and do not escape the worktree root
    - If any allowed_file path resolves outside worktree: block with HOLD_ALLOWED_FILES_ESCAPE

Step 9 — Forbidden-files validation
    - Build the forbidden files list from packet
    - If any forbidden file is also in allowed_files: block with HOLD_CONFLICTING_CONSTRAINT
    - Forbidden files are relative paths; they must be validated against worktree after creation

Step 10 — Execution timeout
    - Record execution start timestamp
    - Timeout is packet.execution_timeout (default 300 seconds)
    - If timeout exceeded: block with HOLD_CLAUDE_TIMEOUT
```

### 4.2 Execution Phase

```
Step 11 — Invoke Claude
    - Working directory: <worktree_path>
    - Environment: PATH, HOME, LANG cleared to minimal safe set
    - Allowed to read: allowed_files + any file in allowed_files subtree
    - NOT allowed to: git push, package install, network, Hermes, boards, audit
    - Claude is given:
        (a) The task description
        (b) The approved plan
        (c) The packet constraints
        (d) Instruction to edit ONLY inside current directory
        (e) Instruction that git push, package install are blocked
    - Claude runs with --dangerously-skip-prompt if available (for automation) or interactive prompt that requires explicit continuation
    - If Claude returns non-zero: block with HOLD_CLAUDE_ERROR

Step 12 — Capture worktree git status
    - git -C <worktree_path> status --short → post_worktree_git_status.txt
    - git -C <worktree_path> diff --stat → diff_stats.txt
    - git -C <worktree_path> diff → diff.patch (unified format)

Step 13 — Capture main repo git status
    - git status on main repo → post_main_git_status.txt
    - If main repo is dirty: block with HOLD_REPO_MUTATION

Step 14 — Collect changed files list
    - Parse git diff --name-only in worktree
    - Output: changed_files.json (array of file paths)

Step 15 — Capture execution logs
    - stdout from Claude → stdout.log
    - stderr from Claude → stderr.log
    - Combined execution summary → execution_summary.md
```

### 4.3 Post-Execution Validation Phase

```
Step 16 — Post-execution PMG compare
    - Run PMG compare against main repo
    - Compare stored at /tmp/aed_runs/<run_id>/pmg_compare.json
    - Any mutation in main repo blocks with HOLD_EXTERNAL_MUTATION

Step 17 — File-boundary validation
    - For each file in changed_files.json:
        - Must be inside worktree root (else HOLD_REPO_MUTATION)
        - Must be in allowed_files (else HOLD_OUTSIDE_ALLOWED_FILES)
        - Must not be in forbidden_files (else HOLD_FORBIDDEN_FILE_TOUCHED)
        - Must not be a gate script unless explicitly allowed (else HOLD_GATE_SCRIPT_MODIFIED)
        - Must not be a test file unless packet allows tests (else HOLD_TEST_POLICY_VIOLATION)

Step 18 — Diff quality validation
    - diff.patch must not be empty (else HOLD_EMPTY_DIFF)
    - No binary files in diff (else HOLD_BINARY_ARTIFACT_IN_DIFF)
    - No single file >1MB in diff (else HOLD_LARGE_FILE_IN_DIFF)
    - No generated artifacts (else HOLD_GENERATED_ARTIFACT_IN_DIFF)
    - No dependency files changed unless packet.dependency_policy allows (else HOLD_DEPENDENCY_FILE_CHANGED)
    - No test weakening (else HOLD_TEST_POLICY_VIOLATION)

Step 19 — Constraint-violation detection
    - Parse stdout.log and stderr.log for:
        - pip, npm, yarn, apt, brew, go get, cargo install → HOLD_PACKAGE_INSTALL_ATTEMPT
        - curl, wget, http, requests, urllib → HOLD_NETWORK_COMMAND_ATTEMPTED
        - git push, gh pr create, gh pr merge → HOLD_GIT_PUSH_ATTEMPTED
        - .hermes/, hermes config → HOLD_HERMES_MUTATION
        - audit log path → HOLD_AUDIT_APPEND_ATTEMPTED
        - memory store, profile → HOLD_MEMORY_PROFILE_WRITE_ATTEMPTED
        - dispatch file → HOLD_DISPATCH_MUTATION_ATTEMPTED
        - board API → HOLD_BOARD_MUTATION_ATTEMPTED

Step 20 — Human review gate
    - Output: /tmp/aed_runs/<run_id>/validation_result.json
    - State: PATCH_READY_FOR_HUMAN_REVIEW if all validations pass
    - Any validation failure: corresponding HOLD_* state
    - AED stops here and waits for human decision
    - Human can: accept patch, reject patch, request rework
    - No automatic application of patch to main repo
```

### 4.4 Cleanup Phase

```
Step 21 — Worktree cleanup (on reject or discard)
    - If human rejects: delete worktree
        - git worktree remove <worktree_path> --force
        - rm -rf /tmp/aed_runs/worktrees/<run_id>/
        - Delete /tmp/aed_runs/<run_id>/ (all artifacts)
    - If human accepts: preserve worktree for manual patch application
        - Worktree remains at /tmp/aed_runs/worktrees/<run_id>/
        - Human reviews diff and applies manually or via separate tool

Step 22 — Worktree cleanup (on failure)
    - If any HOLD_* state is reached before PATCH_READY_FOR_HUMAN_REVIEW:
        - Delete worktree
        - Preserve execution_packet.json, validation_result.json, execution_summary.md
        - Preserve logs for debugging
```

---

## 5. Required Packet Fields

The worker packet for temp-worktree execution must contain all fields below. Partial packets block execution.

```json
{
  "packet_kind": "aed.temp_worktree_execution.v1",

  "run_id": "string (required, unique per execution)",
  "task_id": "string (required, references parent task or ticket)",

  "task": {
    "description": "string (required, human-readable task description)",
    "allowed_files": ["string (required, non-empty unless all-files mode approved)"],
    "forbidden_files": ["string (optional, defaults to [])"],
    "do_not": ["string (optional, constraint statements)"]
  },

  "approved_plan_path": "string (required, path to plan file outside repo)",
  "approved_plan_sha256": "string (required, SHA-256 of approved_plan_path content)",
  "base_sha": "string (required, exact Git SHA worktree is created from)",

  "dependency_policy": {
    "allowed": false,
    "allowed_packages": []
  },

  "test_policy": {
    "allowed": false,
    "allowed_commands": []
  },

  "execution_timeout": 300,

  "output_root": "/tmp/aed_runs/<run_id>/",

  "human_approval": {
    "approved_for_temp_worktree_execution": true,
    "approved_by": "human",
    "approved_plan_sha256": "string (must match computed SHA-256)",
    "approved_at": "ISO-8601 timestamp",
    "max_changed_files": 10
  }
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `packet_kind` | string | Yes | Must be `aed.temp_worktree_execution.v1` |
| `run_id` | string | Yes | Unique identifier for this execution run |
| `task_id` | string | Yes | Parent task or ticket reference |
| `task.description` | string | Yes | Human-readable description |
| `task.allowed_files` | array | Yes | Files Claude may edit; non-empty unless `all_files_allowed` is true |
| `task.forbidden_files` | array | No | Files Claude may not touch |
| `task.do_not` | array | No | Behavioral constraints |
| `approved_plan_path` | string | Yes | Path to plan file |
| `approved_plan_sha256` | string | Yes | SHA-256 of plan file for tamper detection |
| `base_sha` | string | Yes | Git SHA for worktree creation |
| `dependency_policy.allowed` | boolean | Yes | Whether dependency changes are permitted |
| `dependency_policy.allowed_packages` | array | No | Explicitly allowed packages |
| `test_policy.allowed` | boolean | Yes | Whether test execution is permitted |
| `test_policy.allowed_commands` | array | No | Explicitly allowed test commands |
| `execution_timeout` | integer | Yes | Max seconds; default 300 |
| `output_root` | string | Yes | Root path for all execution artifacts |
| `human_approval.approved_for_temp_worktree_execution` | boolean | Yes | Must be `true`; absence blocks |
| `human_approval.approved_by` | string | Yes | Must be `"human"` |
| `human_approval.approved_plan_sha256` | string | Yes | Must match computed SHA-256 |
| `human_approval.approved_at` | string | Yes | ISO-8601 timestamp of approval |
| `human_approval.max_changed_files` | integer | No | Soft limit for human review |

---

## 6. Human Approval Marker

The human approval marker is the gate that allows execution to begin. Absence or mismatch of any field blocks execution at Step 7 (Human Approval Validation).

```json
{
  "approved_for_temp_worktree_execution": true,
  "approved_by": "human",
  "approved_plan_sha256": "<SHA-256 of approved_plan.txt content>",
  "approved_at": "2026-05-20T22:00:00Z",
  "max_changed_files": 10
}
```

**Validation rules:**

| Rule | Failure State |
|------|---------------|
| `approved_for_temp_worktree_execution` must be `true` | `HOLD_PLAN_NOT_APPROVED` |
| `approved_by` must be `"human"` | `HOLD_PLAN_NOT_APPROVED` |
| `approved_plan_sha256` must match computed SHA-256 of `approved_plan_path` content | `HOLD_APPROVED_PLAN_TAMPERED` |
| `approved_at` must be a valid ISO-8601 timestamp within 24 hours of execution start | `HOLD_APPROVAL_EXPIRED` |
| `max_changed_files` must be a positive integer | `HOLD_INVALID_APPROVAL_MARKER` |

**The marker is not a policy override.** It does not allow forbidden files to be touched. It does not allow editing outside allowed_files. It does not allow package installation. It only confirms a human reviewed and approved the plan before the execution window opened.

---

## 7. States

All states are machine-readable via the execution result JSON. States prefixed `HOLD_*` are blocking states that prevent `PATCH_READY_FOR_HUMAN_REVIEW`.

### Execution Lifecycle States

| State | Meaning |
|-------|---------|
| `EXECUTION_DESIGN_ONLY` | Design exists; no implementation |
| `EXECUTION_READY_FOR_HUMAN_APPROVAL` | Packet valid, all pre-checks passed, waiting for human approval marker |
| `HOLD_PLAN_NOT_APPROVED` | `approved_for_temp_worktree_execution` is absent or false |
| `HOLD_APPROVED_PLAN_TAMPERED` | SHA-256 of approved plan does not match |
| `HOLD_APPROVAL_EXPIRED` | Approval timestamp is stale |
| `HOLD_MAIN_DIRTY` | Main repo git status is not clean before execution |
| `HOLD_BASE_SHA_MISMATCH` | Main repo is not at `packet.base_sha` |
| `HOLD_WORKTREE_CREATE_FAILED` | `git worktree add` failed or directory creation failed |
| `HOLD_ALLOWED_FILES_EMPTY` | `allowed_files` is empty and all-files mode not approved |
| `HOLD_CONFLICTING_CONSTRAINT` | A file appears in both allowed_files and forbidden_files |
| `HOLD_CLAUDE_TIMEOUT` | Execution exceeded `execution_timeout` |
| `HOLD_CLAUDE_ERROR` | Claude invocation returned non-zero |
| `HOLD_REPO_MUTATION` | Main repo git status changed during execution |
| `HOLD_EXTERNAL_MUTATION` | PMG detected mutation in main repo post-execution |
| `HOLD_FORBIDDEN_FILE_TOUCHED` | Diff contains a forbidden file |
| `HOLD_OUTSIDE_ALLOWED_FILES` | Diff contains a file not in allowed_files |
| `HOLD_GATE_SCRIPT_MODIFIED` | Diff modifies a gate script |
| `HOLD_TEST_POLICY_VIOLATION` | Diff modifies tests or test outcomes without authorization |
| `HOLD_PACKET_TAMPERED` | Packet file was modified after approval |
| `HOLD_APPROVED_PLAN_TAMPERED` | Plan file was modified after approval |
| `HOLD_HIDDEN_FILE_CREATED` | Diff creates a hidden file (.env, .secrets, etc.) |
| `HOLD_SCOPE_CREEP` | Diff includes files not mentioned in approved plan |
| `HOLD_PACKAGE_INSTALL_ATTEMPT` | Execution logs show package manager invocation |
| `HOLD_NETWORK_COMMAND_ATTEMPTED` | Execution logs show network command invocation |
| `HOLD_GIT_PUSH_ATTEMPTED` | Execution logs show git push or PR creation |
| `HOLD_HERMES_MUTATION` | Execution logs show Hermes path touched |
| `HOLD_AUDIT_APPEND_ATTEMPTED` | Execution logs show audit log write |
| `HOLD_MEMORY_PROFILE_WRITE_ATTEMPTED` | Execution logs show memory or profile write |
| `HOLD_DISPATCH_MUTATION_ATTEMPTED` | Execution logs show dispatch file creation |
| `HOLD_BOARD_MUTATION_ATTEMPTED` | Execution logs show board mutation |
| `HOLD_BINARY_ARTIFACT_IN_DIFF` | Diff contains binary file |
| `HOLD_LARGE_FILE_IN_DIFF` | Diff contains file >1MB |
| `HOLD_EMPTY_DIFF` | No changes captured |
| `HOLD_DIFF_UNREADABLE` | Diff cannot be parsed |
| `HOLD_DEPENDENCY_FILE_CHANGED` | Dependency file changed without policy allowance |
| `HOLD_GENERATED_ARTIFACT_IN_DIFF` | Diff contains __pycache__, .pyc, node_modules, etc. |
| `HOLD_DIFF_VALIDATION_FAILED` | General diff validation failure |
| `PATCH_READY_FOR_HUMAN_REVIEW` | All validations passed; diff ready for human review |
| `PATCH_REJECTED` | Human rejected the diff |
| `PATCH_ACCEPTED` | Human accepted the diff (manual apply, not auto-merge) |

### State Transition Diagram

```
EXECUTION_DESIGN_ONLY
       │
       ▼
EXECUTION_READY_FOR_HUMAN_APPROVAL
       │
       ▼ (if human_approval valid)
  ┌────────────────────────────────────────┐
  │  Worktree Creation + Claude Execution   │
  └────────────────────────────────────────┘
       │
       ├── HOLD_* (any) ──────────────────→ STOP (worktree deleted)
       │
       ▼
PATCH_READY_FOR_HUMAN_REVIEW
       │
       ├── Human Reject → PATCH_REJECTED ──→ STOP (worktree deleted)
       │
       └── Human Accept → PATCH_ACCEPTED ──→ STOP (worktree preserved for manual apply)
```

---

## 8. Execution Constraints

These constraints are enforced at the worktree level and by parsing execution logs. They are not configurable per-execution except where explicitly noted.

### 8.1 Worktree Isolation

| Constraint | Enforcement |
|------------|-------------|
| Claude runs only inside temp worktree | Working directory set to worktree root; Claude cannot escape |
| Main repo git status must not change | Captured pre/post; any change blocks with `HOLD_REPO_MUTATION` |
| Output path must be outside main repo | Validated before execution; output_root must be under `/tmp/aed_runs/` |
| Allowed_files must not be empty unless all-files mode explicitly approved | Checked at Step 8; `HOLD_ALLOWED_FILES_EMPTY` if violated |
| Forbidden files must not be touched | Checked at Step 17; `HOLD_FORBIDDEN_FILE_TOUCHED` if violated |
| No changes to gate scripts | Checked at Step 17; `HOLD_GATE_SCRIPT_MODIFIED` if violated |

### 8.2 Behavioral Constraints

| Constraint | Enforcement |
|------------|-------------|
| No package installation | Execution logs parsed for pip, npm, yarn, apt, brew, go get, cargo install → `HOLD_PACKAGE_INSTALL_ATTEMPT` |
| No git push, gh pr create, gh pr merge | Execution logs parsed → `HOLD_GIT_PUSH_ATTEMPTED` |
| No audit log append | Execution logs parsed for audit log paths → `HOLD_AUDIT_APPEND_ATTEMPTED` |
| No Hermes tree changes | Execution logs parsed for `.hermes/` paths → `HOLD_HERMES_MUTATION` |
| No memory/profile writes | Execution logs parsed for memory store or profile paths → `HOLD_MEMORY_PROFILE_WRITE_ATTEMPTED` |
| No board mutations | Execution logs parsed for board API calls → `HOLD_BOARD_MUTATION_ATTEMPTED` |
| No dispatch file creation | Execution logs parsed for dispatch path writes → `HOLD_DISPATCH_MUTATION_ATTEMPTED` |
| No network commands | Execution logs parsed for curl, wget, http clients → `HOLD_NETWORK_COMMAND_ATTEMPTED` |

### 8.3 Test Constraints

| Constraint | Enforcement |
|------------|-------------|
| Tests do not run unless `test_policy.allowed = true` | Checked at Step 17; test file diff triggers `HOLD_TEST_POLICY_VIOLATION` unless allowed |
| Allowed test commands must be enumerated | Any test run outside `test_policy.allowed_commands` → `HOLD_TEST_POLICY_VIOLATION` |
| No package installs during tests | Logs parsed during test execution → `HOLD_PACKAGE_INSTALL_ATTEMPT` |
| No network-dependent tests | Logs parsed; network calls during test → `HOLD_NETWORK_COMMAND_ATTEMPTED` |

### 8.4 Diff Constraints

| Constraint | Enforcement |
|------------|-------------|
| Every changed file must be in allowed_files | Step 17 validation; `HOLD_OUTSIDE_ALLOWED_FILES` if violated |
| No forbidden file changed | Step 17 validation; `HOLD_FORBIDDEN_FILE_TOUCHED` if violated |
| No dependency file changed unless dependency_policy allows | Step 18 validation; `HOLD_DEPENDENCY_FILE_CHANGED` if violated |
| No test weakening | Step 18 validation; `HOLD_TEST_POLICY_VIOLATION` if test assertion removed |
| No deletion of safety tests | Step 18 validation; `HOLD_TEST_POLICY_VIOLATION` if test file deleted |
| No binary artifacts in diff | Step 18 validation; `HOLD_BINARY_ARTIFACT_IN_DIFF` if violated |
| No files >1MB in diff | Step 18 validation; `HOLD_LARGE_FILE_IN_DIFF` if violated |
| No generated artifacts (__pycache__, .pyc, node_modules) | Step 18 validation; `HOLD_GENERATED_ARTIFACT_IN_DIFF` if violated |
| No hidden dotfile creation (.env, .secrets) | Step 17 validation; `HOLD_HIDDEN_FILE_CREATED` if violated |
| No changes outside temp worktree | Step 16 PMG compare; `HOLD_REPO_MUTATION` if main repo changed |

---

## 9. Diff Validation

Diff validation runs after execution and before human review. All checks must pass for the state to become `PATCH_READY_FOR_HUMAN_REVIEW`.

### 9.1 File-Boundary Checks

For each file in `changed_files.json`:

1. **Inside worktree**: `changed_file` must be a descendant of `worktree_path`. Any file outside blocks with `HOLD_REPO_MUTATION`.
2. **In allowed_files**: `changed_file` must be in `packet.task.allowed_files`. Any file outside blocks with `HOLD_OUTSIDE_ALLOWED_FILES`.
3. **Not in forbidden_files**: `changed_file` must not match any pattern in `packet.task.forbidden_files`. Any match blocks with `HOLD_FORBIDDEN_FILE_TOUCHED`.
4. **Not a gate script**: `changed_file` must not match any gate script path (scripts/local/final_gate_status.py, scripts/local/verify_final_head_merge_command.py, scripts/local/check_persistent_mutation_guard.py, scripts/local/plan_preview_eval_status.py). Any match blocks with `HOLD_GATE_SCRIPT_MODIFIED`.
5. **Not a test file unless explicitly allowed**: If `test_policy.allowed = false`, any test file in `changed_files` blocks with `HOLD_TEST_POLICY_VIOLATION`.

### 9.2 Diff Quality Checks

1. **Non-empty**: `diff.patch` must contain at least one hunk. Empty diff blocks with `HOLD_EMPTY_DIFF`.
2. **Human-readable**: `diff.patch` must parse as valid unified diff. Unparseable diff blocks with `HOLD_DIFF_UNREADABLE`.
3. **No binary files**: Any file detected as binary (non-text) in the diff blocks with `HOLD_BINARY_ARTIFACT_IN_DIFF`.
4. **No files >1MB**: Any single file >1MB in the diff blocks with `HOLD_LARGE_FILE_IN_DIFF`.
5. **No generated artifacts**: `__pycache__/`, `*.pyc`, `node_modules/`, `.tox/`, `.pytest_cache/`, `.mypy_cache/` in diff blocks with `HOLD_GENERATED_ARTIFACT_IN_DIFF`.

### 9.3 Dependency Policy Checks

If `dependency_policy.allowed = false`:
- Any change to `requirements.txt`, `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`, `poetry.lock`, `package.json`, `yarn.lock`, `npm-shrinkwrap.json`, `go.mod`, `go.sum`, `Cargo.toml`, `Cargo.lock`, `Gemfile`, `Gemfile.lock` → `HOLD_DEPENDENCY_FILE_CHANGED`

If `dependency_policy.allowed = true`:
- Changes must be limited to `dependency_policy.allowed_packages`; any other package added → `HOLD_DEPENDENCY_FILE_CHANGED`

### 9.4 Test Policy Checks

If `test_policy.allowed = true`:
- Only commands in `test_policy.allowed_commands` may be executed
- Test results must be captured; any test that fails must be reflected in `validation_result.json` with `test_failures_detected: true`
- Failed tests do NOT automatically block; they are reported to human for review
- Package installs during test execution still blocked

If `test_policy.allowed = false`:
- Any test file changed → `HOLD_TEST_POLICY_VIOLATION`
- Any test command in logs → `HOLD_TEST_POLICY_VIOLATION`

### 9.5 Scope-Creep Check

The approved plan defines the scope. Any file in `changed_files` not mentioned in the approved plan text blocks with `HOLD_SCOPE_CREEP`. This is a hard check: it blocks `PATCH_READY_FOR_HUMAN_REVIEW` and requires human review of whether the scope expansion was intentional and approved.

---

## 10. Test Policy

### 10.1 Default Behavior

By default, Claude does **not** run tests during temp-worktree execution. The `test_policy` in the packet is `{"allowed": false, "allowed_commands": []}` unless explicitly set otherwise.

### 10.2 When Tests Are Allowed

Tests may only run if:
1. `packet.test_policy.allowed = true`
2. `packet.test_policy.allowed_commands` is a non-empty array of exact command strings
3. The test commands do not include package installation
4. The test commands do not make network calls

Example allowed test policy:
```json
"test_policy": {
  "allowed": true,
  "allowed_commands": [
    "pytest tests/test_run_plan_preview.py -q",
    "pytest tests/test_verify_final_head_merge_command.py -q"
  ]
}
```

### 10.3 Test Execution Logging

When tests run:
- Exact command recorded in `validation_result.json`
- stdout and stderr captured to `test_stdout.log` and `test_stderr.log`
- Exit code recorded
- Duration recorded
- All test output preserved for human review

### 10.4 Failure Handling

Failing tests do **not** trigger an automatic repair loop. The state becomes `PATCH_READY_FOR_HUMAN_REVIEW` with `test_failures_detected: true` in `validation_result.json`. The human reviews both the diff and the test failures together.

Repair (re-running Claude with a fix request) requires a new `human_approval` marker and is subject to the repair loop policy (Section 11).

### 10.5 Prohibited Test Behaviors

Regardless of `test_policy`:
- Claude may not modify test files unless `allowed_files` explicitly includes test files
- Claude may not delete tests
- Claude may not weaken assertions to make failing tests pass
- Claude may not add new test files unless explicitly allowed
- Claude may not run `pytest --tb=no` or similar flags that hide failures
- Claude may not run test collection only (`pytest --collect-only`) as a substitute for actual test execution

---

## 11. Repair Loop Policy

### 11.1 Default: No Repair Loop

The default `max_repair_attempts` is **0**. No repair loop runs without explicit human approval for each attempt.

### 11.2 Human-Approved Repair Loop

To initiate a repair loop:

1. Human reviews the `validation_result.json` and `diff.patch`
2. Human decides the failure is fixable by Claude without expanding scope
3. Human creates a new packet with:
   - Same `run_id` with `-repair-1`, `-repair-2` suffix
   - Same `allowed_files`, `forbidden_files`, `do_not`, `base_sha`
   - New `approved_plan_sha256` for the repair plan
   - New `human_approval` marker
   - `max_repair_attempts: 1` (or explicit count)

### 11.3 Repair Constraints

| Constraint | Rule |
|------------|------|
| allowed_files cannot expand | Repair cannot edit files not in original `allowed_files` |
| forbidden_files cannot shrink | Repair cannot undo a forbidden_files protection |
| Tests cannot be modified | Test files may not change unless explicitly in `allowed_files` |
| Packet constraints cannot change | `base_sha`, `approved_plan_sha256` of original packet cannot change |
| Repair attempts are bounded | `max_repair_attempts` is capped at 3 |
| Each repair re-validates | Every repair attempt produces a new `validation_result.json` |
| Repair does not auto-accept | Human must review each repair's diff |

### 11.4 Repair vs. New Execution

A repair is scoped to fixing the specific failure in the existing worktree. It is not a mechanism to expand scope or re-run the full task. If the human determines the original plan was insufficient, a new execution packet (new `run_id`) is required.

---

## 12. Output Artifacts

All artifacts are written to `/tmp/aed_runs/<run_id>/` (outside the main repo) and preserved for human review. On `PATCH_REJECTED` or unrecoverable `HOLD_*` states, the worktree is deleted but the artifacts remain for debugging.

```
/tmp/aed_runs/<run_id>/
├── execution_packet.json        # Immutable copy of packet as approved
├── approved_plan.txt             # Immutable copy of approved plan
├── base_sha.txt                  # SHA worktree was created from
├── worktree_path.txt             # Absolute path to worktree
├── pre_main_git_status.txt      # Main repo status before (should be clean)
├── pre_worktree_git_status.txt   # Worktree status before execution
├── post_main_git_status.txt     # Main repo status after (must be clean)
├── post_worktree_git_status.txt  # Worktree status after execution
├── diff.patch                    # Unified diff of all changes
├── changed_files.json            # Array of changed file paths
├── validation_result.json        # Full validation outcome with HOLD_* reason if blocked
├── execution_summary.md          # Human-readable execution summary
├── pmg_snapshot.json             # PMG snapshot before execution
├── pmg_compare.json              # PMG compare result after execution
├── stdout.log                    # Claude stdout (or test stdout if tests run)
├── stderr.log                    # Claude stderr (or test stderr if tests run)
└── test_results/                 # (optional) Per-command test results if tests run
    ├── <command>.stdout
    ├── <command>.stderr
    └── <command>.exit_code
```

### 12.1 validation_result.json Schema

```json
{
  "run_id": "string",
  "final_state": "PATCH_READY_FOR_HUMAN_REVIEW | HOLD_*",
  "blocked_by": "string (state that caused HOLD_*)",
  "validation": {
    "file_boundary": { "passed": true, "failures": [] },
    "diff_quality": { "passed": true, "failures": [] },
    "dependency_policy": { "passed": true, "failures": [] },
    "test_policy": { "passed": true, "failures": [] },
    "constraint_violation": { "passed": true, "failures": [] },
    "scope_creep": { "passed": true, "warnings": [] }
  },
  "changed_files": ["string"],
  "forbidden_files_touched": [],
  "allowed_files_violated": [],
  "gate_scripts_modified": [],
  "test_failures_detected": false,
  "test_results": [],
  "execution_duration_seconds": 0,
  "timestamp": "ISO-8601"
}
```

---

## 13. Human Approval Gates

There are three mandatory human approval gates before any change reaches the main repo checkout or any branch.

### Gate 1 — Plan Approval (Pre-Execution)

**Trigger:** Before temp-worktree execution begins
**Required:** Valid `human_approval` marker in packet
**Validation:** SHA-256 match, timestamp within 24h, `approved_for_temp_worktree_execution = true`, `approved_by = "human"`
**Failure:** `HOLD_PLAN_NOT_APPROVED` or `HOLD_APPROVED_PLAN_TAMPERED` blocks execution

### Gate 2 — Diff Review (Post-Execution)

**Trigger:** After all validations pass; state is `PATCH_READY_FOR_HUMAN_REVIEW`
**Required:** Human reviews `diff.patch`, `validation_result.json`, `execution_summary.md`, and `stdout.log`/`stderr.log`
**Options:**
- **Accept:** Diff is manually applied to a branch by human; no automatic PR creation
- **Reject:** Worktree deleted; execution ends
- **Request Repair:** Human issues a repair packet (new approval marker required)

**No automatic progression.** AED does not advance to Gate 3 without explicit human action.

### Gate 3 — Apply to Branch (Optional, Human-Managed)

If human accepts the diff:
- Human applies the patch to a branch manually (via `git apply`, `cherry-pick`, or manual editing)
- Human creates PR through existing AED process (open PR → CI → Codex review → final_gate → merge)
- No automation of branch application, PR creation, or merge

### Gate 4 — Final Gate (Existing AED Process)

Once a PR is open from the applied patch, the existing `final_gate_status.py` and `verify_final_head_merge_command.py` process applies. This is the existing AED final gate, unchanged by the temp-worktree execution layer.

---

## 14. Integration with Existing Tools

The temp-worktree execution layer is additive and does not modify existing AED components. Integration points:

### 14.1 run_plan_preview.py

**Current role:** Generates plan previews from task packets. Does not execute.
**Future integration:** `run_plan_preview.py` output becomes the `approved_plan.txt` for temp-worktree execution. The plan is reviewed by human; if approved, the execution packet is constructed with the plan SHA-256 and execution proceeds.

**Interface:** `run_plan_preview.py --packet <packet.json> --output <plan.md>` → `plan.md` (human reviews) → human creates execution packet referencing `plan.md`.

### 14.2 plan_preview_eval_status.py

**Current role:** Aggregates multiple plan-preview trials into a final state (`READY_FOR_MANUAL_PLAN_PREVIEW`, `HOLD_*`).
**Future integration:** For execution-class tasks, `plan_preview_eval_status.py` output must be `READY_FOR_MANUAL_PLAN_PREVIEW` before an execution packet is created. Execution only proceeds when the plan itself is clean (no scope violations, no TP/FP errors, all git clean-to-clean).

**Interface:** Run `plan_preview_eval_status.py` with trials; if final_state is `READY_FOR_MANUAL_PLAN_PREVIEW` and `blocked_true_positive_count = 0` and `blocked_likely_false_positive_count = 0`, the plan is clean enough to approve for execution.

### 14.3 final_gate_status.py

**Current role:** Validates open PRs (CI, Codex, PMG, git status).
**Future integration:** After human accepts a diff and applies it to a branch, `final_gate_status.py` runs on the resulting PR through the normal AED process.

**Interface:** `final_gate_status.py --pr <number>` — unchanged.

### 14.4 verify_final_head_merge_command.py

**Current role:** Emits and validates the exact `gh pr merge` command with `--match-head-commit`.
**Future integration:** After `final_gate_status.py` passes and PMG is clean, `verify_final_head_merge_command.py --require-pmg` authorizes merge through the existing gate.

**Interface:** `verify_final_head_merge_command.py --pr <number> --require-pmg` — unchanged.

### 14.5 check_persistent_mutation_guard.py (PMG)

**Current role:** Takes a snapshot and compare to detect mutations in the main repo.
**Future integration:** PMG runs before execution (snapshot) and after execution (compare). Any mutation in main repo blocks with `HOLD_EXTERNAL_MUTATION`.

**Interface:** `check_persistent_mutation_guard.py snapshot --output <path>` and `check_persistent_mutation_guard.py compare --baseline <path> --output <path>` — unchanged.

---

## 15. First Implementation After Design

After this design is reviewed and merged, the smallest safe first implementation PR:

### Scope

- **One script:** `scripts/local/temp_worktree_execution.py` (controller only, no Claude invocation)
- **One fixture packet:** JSON file in `tests/fixtures/temp_worktree/`
- **Unit tests:** Test the controller's pre-flight validation, state machine, and diff validation logic
- **Mocked execution:** Claude is mocked in unit tests; no real Claude runs in tests
- **No real execution:** The script validates against a fixture packet with a mock diff
- **No PR creation, no merge**
- **One-file allowed_files task:** Packet allows editing exactly one file; diff is one file
- **Manual live smoke:** After PR merge, developer runs the script manually against a real task to verify the full flow

### Implementation Checklist

1. `scripts/local/temp_worktree_execution.py` — state machine, pre-flight checks, diff validation
2. `tests/fixtures/temp_worktree/execution_packet_valid.json` — valid packet fixture
3. `tests/fixtures/temp_worktree/execution_packet_no_approval.json` — missing approval marker fixture
4. `tests/fixtures/temp_worktree/execution_packet_tampered_plan.json` — SHA mismatch fixture
5. `tests/test_temp_worktree_execution.py` — unit tests for all states and transitions
6. `tests/fixtures/temp_worktree/diff_valid.patch` — valid diff fixture
7. `tests/fixtures/temp_worktree/diff_forbidden_file.patch` — diff touching forbidden file
8. `tests/fixtures/temp_worktree/diff_outside_allowed.patch` — diff outside allowed_files
9. README comment at top of `temp_worktree_execution.py` linking to this design doc

### What Is NOT in First Implementation

- No real Claude invocation (mocked only)
- No git worktree creation (mocked in tests)
- No PMG integration (stubbed in tests)
- No human approval marker parsing beyond basic JSON field checks
- No repair loop
- No test execution
- No output artifact writing (stubs only)
- No integration with `run_plan_preview.py` or `plan_preview_eval_status.py`

### Second Implementation PR (After First)

- Real git worktree creation/deletion (with cleanup on failure)
- PMG pre/post snapshots
- Real execution log parsing
- Real constraint-violation detection in logs

### Third Implementation PR (After Second)

- Integration with `run_plan_preview.py` as plan source
- Human approval marker in packet validation
- Full output artifact writing

---

## 16. Not-Ready Checklist

The following must be true before the first implementation PR is opened:

| Item | Status | Notes |
|------|--------|-------|
| Design document reviewed by at least one human | Required | Design must be approved before implementation |
| `plan_preview_eval_status.py` stable under new aggregation semantics | Required | Dogfood confirmed stable in post-#273 run |
| `final_gate_status.py` reliable for open PRs | Required | Currently works for open PRs; post-merge mode not yet needed |
| Diff validator test plan exists | Required | Must have test cases for all HOLD_* states before implementation |
| PMG pre/post execution sequence defined | Required | Snapshot before, compare after; documented in Section 4 |
| Human approval marker format defined | Required | Documented in Section 6 |
| Forbidden action detection test plan exists | Required | Must have log-parsing test cases for each blocked command |
| State machine test plan exists | Required | All 30+ states must have transition tests |
| Worktree cleanup test plan exists | Required | On HOLD_* states, worktree must be deleted |
| Constraint-violation detection test plan exists | Required | pip, npm, curl, git push, Hermes paths, audit paths |
| Repair loop design reviewed | Required | Repair loop requires explicit human approval per attempt |
| Gate script list defined | Required | final_gate_status.py, verify_final_head_merge_command.py, check_persistent_mutation_guard.py, plan_preview_eval_status.py |

---

## Validation

### Test Suite

```bash
pytest tests/test_run_plan_preview.py tests/test_final_gate_status.py tests/test_plan_preview_eval_status.py -q
python3 -m compileall scripts/local tests -q
```

### Process

1. Open PR from `docs/temp-worktree-execution-v1-design` against `main`
2. CI must be green (237 tests pass)
3. Codex exact-head review: focus on whether the design preserves existing safety boundaries
4. Run `final_gate_status.py --pr <number>` for PR status
5. Run `verify_final_head_merge_command.py --pr <number> --require-pmg` for merge authorization
6. Merge with `--match-head-commit <SHA>`

---

*Design document: `docs/temp_worktree_execution_v1_design.md`*
*Branch: `docs/temp-worktree-execution-v1-design`*
*SHA of design doc: calculated at commit time*