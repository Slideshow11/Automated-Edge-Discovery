# Real Claude Executor Readiness Gate

**Version:** 0.1-draft
**Status:** Design only — not implemented
**Branch:** `docs/real-claude-executor-readiness-gate`
**Created:** 2026-05-21

---

## Preamble

This document defines the safety requirements, state machine additions, command constraints, input validation rules, and gate criteria that **must** be satisfied before AED is permitted to add a real Claude executor mode.

**Hard constraint:** This document does not implement `execution.mode="claude"`. It defines the design surface and readiness gate so that when a future PR is created to add real execution, reviewers have an explicit checklist to validate the implementation against.

---

## 1. Current Baseline

AED currently provides the following connected infrastructure:

| Component | Description |
|-----------|-------------|
| `plan_preview_eval_status.py` | Eval-status driven plan controller |
| `final_gate_status.py` | Final gate reporter (READY_TO_MERGE / HOLD_\*) |
| `verify_final_head_merge_command.py` | Merge command verifier with `--match-head-commit` |
| `scripts/local/bridge_to_execution_packet.py` | Bridge from approved plan to execution packet |
| `run_temp_worktree_execution.py` | Mock-only temp-worktree harness |
| PMG pre/post integration | Persistent Mutation Guard snapshot/compare |
| Durable artifact | `output_root/diff.patch` (written after worktree cleanup) |

The current execution mode is **`mode: "mock"` only**. Mock execution:
- Creates a disposable Git worktree at the approved base SHA
- Applies staged file edits via `apply_mock_edits()`
- Captures `git diff --cached --unified=3` output as `diff.patch`
- Validates against `allowed_files`, `forbidden_files`, `protected_gate_scripts`, `max_changed_files`
- Runs PMG pre/post comparison
- Returns `PATCH_READY_FOR_HUMAN_REVIEW` with `output_root/diff.patch` preserved

**Real Claude execution is absent.** No code path invokes Claude Code, Claude CLI, or any external LLM. The harness is hard-limited to `mode: "mock"` at Phase 5.

---

## 2. Real Executor Goal

The narrow future goal of `execution.mode="claude"`:

> Take an already-approved plan and execution packet, run Claude Code inside a disposable temp worktree that is isolated from the main repository, allow file edits only to paths within `allowed_files`, capture the resulting diff under `output_root/diff.patch`, validate the diff and PMG state, and stop at `PATCH_READY_FOR_HUMAN_REVIEW` for human review — with no autonomous promotion to merge, PR creation, or dispatch.

The executor is a **one-shot tool** inside the worktree. It receives a task description, runs Claude Code once, captures output, and exits. It does not loop, retry autonomously, or take further actions without human input.

---

## 3. Non-Goals

The following are explicitly **out of scope** for the real executor implementation:

- **Unattended execution** — human must be present and reviewing `diff.patch` before any further step
- **Autonomous PR creation** — no `gh pr create` from any script
- **Autonomous merge** — no `gh pr merge` from any script
- **Git push** — no `git push` from worktree or main
- **Dispatch** — no webhook dispatch, event emission, or callback triggers
- **Board updates** — no Linear, Jira, GitHub Projects, or Kanban board mutations
- **Hermes skill mutation** — no writing to `~/.hermes/skills/`
- **Audit append automation** — no writing to audit log from within executor
- **Package installs** — no `pip install`, `npm install`, `apt install`, or equivalent from within worktree
- **Dependency upgrades** — no `pip upgrade`, `poetry update`, `npm update`, or equivalent
- **Repair loops** — the executor must not self-heal, re-run on failure, or invoke itself
- **Editing main checkout** — all edits restricted to disposable worktree only
- **Multi-repo edits** — worktree is single-repo only
- **Live trading, deployment, or production operations** — completely out of scope

---

## 4. Required Claude Command Shape

The future real executor invocation **must** conform to the following shape. The exact command flags must be re-verified against current Claude Code documentation before any implementation PR is merged.

### Conceptual Invariant

```python
# PSEUDOCODE — NOT IMPLEMENTED
result = subprocess.run(
    [
        "claude",
        "--permission-mode", "<verified-safe-mode>",
        # ... additional flags TBD and re-verified
    ],
    cwd=worktree_root,          # Subprocess layer — process cwd is worktree
    capture_output=True,
    timeout=timeout_seconds,
    # NO shell=True
    # NO string-split shell invocation
    # Environment variables minimized and scoped
)

# Post-invocation verification: confirm Claude's actual cwd is the worktree
actual_cwd = Path.cwd()
if not str(worktree_root) in str(actual_cwd.resolve()):
    return HOLD_CLAUDE_COMMAND_INVALID  # Claude did not respect cwd contract
```

### Required Constraints

| Constraint | Rationale |
|------------|-----------|
| `cwd` must be the temp worktree root | Isolation — Claude cannot reach into main repo |
| `shell=True` must never be used | Prevents injection |
| Args must be list-form (`split=False`) | Safe argument passing |
| `timeout` must be set and respected | Prevents runaway executor |
| `stdout` and `stderr` must be captured | Required for audit and forbidden-command scan |
| Environment minimized — no broad secret inheritance | Least privilege |
| Claude auth via minimal env var only | `ANTHROPIC_API_KEY` or equivalent, not inherited `GH_TOKEN` etc. |
| `output_root` path passed as file system argument, not embedded in prompt | Prevents prompt injection; output_root is written by harness after execution, not given to Claude as writable target |
| Packet path passed as file system argument, not embedded in prompt | Prevents prompt injection |
| Approved plan path passed as file system argument, not embedded in prompt | Prevents prompt injection; plan is read-only reference, not writable target |
| No `--no-input`, `--force`, `--auto-accept` flags unless explicitly reviewed | Prevents blind overwrites |
| No `gh pr create`, `gh pr merge`, `git push` commands allowed in any Claude instruction | Enforced by `do_not` field and forbidden-command scan |

### Important Disclaimer

> The **exact** command shape (flags, subcommands, permission modes) must be re-verified against the live Claude Code documentation at the time of implementation. The pseudocode above is a design contract, not an invocation guarantee. The `--permission-mode` flag name, its allowed values, and behavior are subject to change and must be verified in the actual Claude Code binary before any implementation.

---

## 5. Permission Mode Policy

Before any real executor invocation, the following must be satisfied:

1. **Live verification required** — `claude --permission-mode` behavior must be verified against the installed version by running `claude --help` or equivalent and inspecting the actual flag and mode names
2. **Mode must prevent broad writes** — The selected permission mode must block writes outside the working directory without explicit per-file approval
3. **No bypassPermissions mode** — `bypassPermissions` or equivalent super-user mode must not be used
4. **No auto-accept broad write mode** — No mode that auto-accepts all file modifications without asking
5. **Mode documented in a testable fixture** — The allowed mode string must be a named constant in a fixture file, so unit tests can assert against it
6. **Unit tests mock Claude** — No unit test may call the real Claude binary. All real-executor unit tests use a mock subprocess

---

## 6. Input Requirements

Future `execution.mode="claude"` requires the following fields in the execution packet. Missing or invalid fields must return a `HOLD_*` state before any Claude invocation occurs.

### Required Packet Fields

| Field | Required Value | Return State if Invalid |
|-------|----------------|------------------------|
| `packet_kind` | `"aed.temp_worktree.execution.v0"` or later | `HOLD_INVALID_PACKET` |
| `approval.approved_for_temp_worktree_execution` | `true` | `HOLD_PLAN_NOT_APPROVED` |
| `approval.approved_plan_sha256` | Matches SHA256 of `approved_plan_path` file | `HOLD_PLAN_HASH_MISMATCH` |
| `task.allowed_files` | Non-empty list | `HOLD_INVALID_PACKET` |
| `task.forbidden_files` | Non-empty list (default `PROTECTED_GATE_SCRIPTS` + system paths) | `HOLD_INVALID_PACKET` |
| `task.do_not` | Non-empty list of forbidden command patterns | `HOLD_INVALID_PACKET` |
| `base_sha` | Valid git SHA pointing to an existing commit | `HOLD_INVALID_PACKET` |
| `execution.output_root` | Absolute path **outside** repo (`/tmp/aed_runs/<run_id>/`) | `HOLD_OUTPUT_PATH_INSIDE_REPO` |
| `execution.worktree_path` | Under `/tmp/aed_runs/worktrees/<run_id>/` | `HOLD_WORKTREE_CREATE_FAILED` |
| `execution.mode` | `"claude"` (future) | `HOLD_EXECUTOR_NOT_ALLOWED` (currently only `"mock"` is allowed) |

### PMG Precondition

| Field | Required Value | Return State if Missing |
|-------|----------------|------------------------|
| PMG pre-snapshot | Must complete and write to `output_root/pmg_snapshot.json` with `status: clean` | `HOLD_PMG_SNAPSHOT_FAILED` |

---

## 7. Safety Gates Before Claude Invocation

The following gates must all pass before the real executor subprocess is spawned. Each gate returns a `HOLD_*` state and halts execution.

### Path Isolation (Canonicalization Required)

All path validation in this design must use **canonical resolved paths** (not string-prefix checks) to prevent symlink escape, submodule bypass, and relative-path confusion:

| Path Field | Validation Required |
|------------|---------------------|
| `output_root` | Must resolve to a path outside `REPO_ROOT` via `Path.resolve()` |
| `worktree_path` | Must resolve to a path outside `REPO_ROOT` via `Path.resolve()` |
| `allowed_files` entries | Must resolve to inside `worktree_root` via `Path.resolve()`; reject any `..` or absolute path that escapes worktree |
| `forbidden_files` entries | Must resolve to outside `worktree_root` or be in system-protected paths |
| Symlink handling | Both `allowed_files` and `forbidden_files` must resolve symlinks before comparison |
| Submodule handling | Paths inside Git submodules must be treated as outside worktree unless explicitly allowed |

String-prefix matching alone (e.g., `path.startswith(str(worktree_root))`) is insufficient and must not be used for any security-critical path check.

### Git and Repo State Gates

1. **Main git status clean** — `git status --short` in main repo returns empty (no staged, no unstaged, no untracked). If dirty: `HOLD_MAIN_DIRTY`
2. **Output root outside repo** — `output_root` must not be inside `REPO_ROOT`. If inside: `HOLD_OUTPUT_PATH_INSIDE_REPO`
3. **Worktree path outside repo** — `worktree_root` must not be inside `REPO_ROOT`. If inside: `HOLD_WORKTREE_CREATE_FAILED`
4. **Base SHA exists** — `git rev-parse <base_sha>` succeeds in main. If not: `HOLD_INVALID_PACKET`
5. **Main HEAD at base_sha** — `git rev-parse HEAD` in main equals `packet.base_sha`. If not: `HOLD_MAIN_DIRTY`

### Approval and Plan Gates

6. **Human approval marker valid** — `approval.approved_for_temp_worktree_execution == true` and `approved_by == "human"`. If not: `HOLD_PLAN_NOT_APPROVED`
7. **Approved plan hash matches** — SHA256 of `approved_plan_path` file equals `approval.approved_plan_sha256`. If not: `HOLD_PLAN_HASH_MISMATCH`
8. **Allowed files non-empty** — `task.allowed_files` has at least one entry. If empty: `HOLD_INVALID_PACKET`
9. **Forbidden files include system paths** — `forbidden_files` must at minimum include:
   - `.git` (directory)
   - `.github` (directory)
   - Hermes home paths (`~/.hermes/`, `~/.config/hermes/`)
   - Audit log paths (if used)
   - Board/API token paths
   - Protected gate scripts: `scripts/local/final_gate_status.py`, `scripts/local/verify_final_head_merge_command.py`, `scripts/local/check_persistent_mutation_guard.py`, `scripts/local/plan_preview_eval_status.py`

### Protected Gate Script Policy

10. **No protected gate script in `allowed_files` by default** — unless `approval.approved_by == "human" AND approval.allow_gate_script_override == true`. If violated: `HOLD_FORBIDDEN_FILE_TOUCHED`

### PMG Gate

11. **PMG pre-snapshot completed** — `output_root/pmg_snapshot.json` exists and has `status: clean`. If missing or dirty: `HOLD_PMG_SNAPSHOT_FAILED`

### Timeout Gate

12. **Execution timeout set** — `execution.timeout_seconds` must be > 0 and <= maximum allowed (e.g., 600s). If not set or excessive: `HOLD_INVALID_PACKET`

### Package Install Policy

13. **No package install policy in packet** — `execution.allow_package_install == true` must not be set. If set: `HOLD_INVALID_PACKET` (package installs are forbidden in AED by default)

---

## 8. Runtime Monitoring

When `execution.mode="claude"` is active, the following must be captured for audit and validation:

| Metric | Field |
|--------|-------|
| Executor start time | `executor_start_time` |
| Executor end time | `executor_end_time` |
| Timeout seconds | `execution.timeout_seconds` from packet |
| Actual elapsed seconds | `executor_elapsed_seconds` |
| Exit code | `executor_exit_code` |
| Captured stdout | `executor_stdout` (stored to file in `output_root/`) |
| Captured stderr | `executor_stderr` (stored to file in `output_root/`) |
| Claude transcript/log path | `executor_transcript_path` (stored in `output_root/`) |
| Worktree git status before | `worktree_git_status_before` |
| Worktree git status after | `worktree_git_status_after` |
| Main git status before | `main_git_status_before` |
| Main git status after | `main_git_status_after` |
| PMG snapshot path | `pmg_snapshot_path` |
| PMG compare JSON path | `pmg_compare_json_path` |
| PMG compare MD path | `pmg_compare_md_path` |
| PMG compare status | `pmg_status` |
| Changed files list | `changed_files` |
| Staged diff | `diff_text` (written to `output_root/diff.patch`) |
| Unstaged diff | `worktree_git_status_diff` (captured separately) |

All runtime artifacts must be written to `output_root/`, not to the worktree, and must be preserved even if worktree cleanup fails.

---

## 9. Post-Execution Validation

After the Claude subprocess exits, the following validation gates must all pass before returning `PATCH_READY_FOR_HUMAN_REVIEW`:

### Git and PMG Gates

1. **Main repo clean** — `main_git_status_after` must be clean (only untracked allowed). If dirty: `HOLD_REPO_MUTATION`
2. **PMG compare clean** — `pmg_status` must be `"clean"`. If blocked or dirty: `HOLD_EXTERNAL_MUTATION`

### File Constraint Gates

3. **Changed files subset of allowed_files** — every entry in `changed_files` must be in `task.allowed_files`. If any outside: `HOLD_OUTSIDE_ALLOWED_FILES`
4. **No forbidden files touched** — no entry in `changed_files` may appear in `task.forbidden_files`. If any touched: `HOLD_FORBIDDEN_FILE_TOUCHED`
5. **No protected gate scripts touched** — no entry in `changed_files` may be a protected gate script. If any touched: `HOLD_FORBIDDEN_FILE_TOUCHED`
6. **Max changed files respected** — `len(changed_files) <= approval.max_changed_files`. If exceeded: `HOLD_TOO_MANY_FILES_CHANGED`

### Diff Gates

7. **Non-empty diff when files changed** — if `changed_files` is non-empty and `diff_text` is empty: `HOLD_DIFF_VALIDATION_FAILED`
8. **diff.patch under output_root** — `output_root/diff.patch` must exist and be non-empty if `changed_files` is non-empty. Must survive worktree cleanup
9. **No generated binary artifacts** — `diff.patch` content must not contain binary diff markers (`Binary files ... differ`) unless explicitly allowed by policy
10. **No large unexpected files** — if file size of any changed file exceeds reasonable threshold (e.g., 1MB default, configurable), block with `HOLD_DIFF_VALIDATION_FAILED` unless explicitly allowed

### Dependency and Test Policy Gates

11. **No dependency files changed unless allowed** — `package.json`, `poetry.lock`, `requirements.txt`, `Pipfile`, `*.gemfile` changes must require `execution.allow_dependency_change == true` in packet. If changed without flag: `HOLD_DIFF_VALIDATION_FAILED`
12. **No tests weakened or removed** — any change to `test_*.py` or `*_test.py` files that reduces test coverage or removes assertions must be blocked. If detected: `HOLD_DIFF_VALIDATION_FAILED`
13. **No hidden dotfile changes** — changes to files starting with `.` (dotfiles) must be explicitly in `allowed_files`. If not: `HOLD_OUTSIDE_ALLOWED_FILES`

---

## 10. Forbidden Command Detection

Before invoking Claude, the following must be scanned and blocked if evidence is found:

### Pre-Invocation Scan (Packet and Plan)

The following fields must be scanned for forbidden patterns before the subprocess is spawned:

| Scan Target | Forbidden Patterns | Return State |
|-------------|-------------------|--------------|
| `task.do_not` | Must be non-empty and contain at minimum: `git push`, `gh pr create`, `gh pr merge`, `dispatch`, `board`, `install`, `upgrade` | `HOLD_INVALID_PACKET` if empty |
| `task.description` | Prompt injection indicators ( URLs with exfiltration intent, etc.) | `HOLD_INVALID_PACKET` |
| `approved_plan_path` contents | Same forbidden patterns if plan is a script | `HOLD_PLAN_HASH_MISMATCH` |

### Post-Invocation Scan (stdout/stderr/transcript)

After the subprocess completes, the following must be scanned in `executor_stdout`, `executor_stderr`, and `executor_transcript_path`:

| Forbidden Pattern | Action if Found |
|------------------|-----------------|
| `git push` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `git -C ... push` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `gh pr create` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `gh pr merge` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `gh workflow run` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `gh api .../dispatches` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `repository_dispatch`, `workflow_dispatch` in logs/transcript | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `gh project` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `gh issue edit`, `gh pr edit` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `dispatch` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| Board/mutation API calls | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| Hermes skill path writes (`~/.hermes/skills/`) | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| Audit log appends | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| Memory/profile updates (`~/.hermes/memory/`, `~/.hermes/profile/`) | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `pip install`, `pip upgrade` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `npm install`, `yarn add`, `pnpm add`, `bun add` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `poetry add`, `pipenv install`, `uv pip install` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `apt install`, `apt-get install` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `brew install`, `brew upgrade` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `cargo install`, `cargo update` | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| Network install commands (`curl ... \| sh`, `wget ... -O- \|`, direct API mutation calls) | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `chmod 777` or broad executable changes | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| Secret exfiltration patterns (base64 of `$HOME`, env exports, etc.) | `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |

Forbidden command detection must use **normalized token/argv matching**, not simple substring scan. Commands must be parsed as token arrays (split on whitespace), and the first token must be checked against the normalized token list. Substring containment in multi-line stdout/stderr is acceptable as a secondary scan, but token-array matching is the primary gate.

### Post-Invocation File Scan

After execution, the following must be validated against the diff:

| Check | Action if Violated |
|-------|---------------------|
| No `~/.hermes/` paths in changed files | `HOLD_FORBIDDEN_FILE_TOUCHED` |
| No `.github/workflows/` modifications unless explicitly allowed | `HOLD_OUTSIDE_ALLOWED_FILES` |
| No `.git/` directory modifications | `HOLD_FORBIDDEN_FILE_TOUCHED` |
| No modification of the current AED repo's own source files outside allowed scope | `HOLD_REPO_MUTATION` |

---

## 11. New States for Future Real Executor

The following states are required for future `execution.mode="claude"` implementation. Existing states are preserved.

### New Real-Executor States

| State | Trigger |
|-------|---------|
| `HOLD_REAL_EXECUTOR_NOT_ENABLED` | `execution.mode == "claude"` but feature flag disabled |
| `HOLD_CLAUDE_PERMISSION_MODE_UNVERIFIED` | `--permission-mode` flag or value not verified against live Claude binary |
| `HOLD_CLAUDE_COMMAND_INVALID` | Command shape invalid (shell=True, missing cwd, missing timeout, etc.) |
| `HOLD_CLAUDE_TIMEOUT` | Subprocess timed out before completing |
| `HOLD_CLAUDE_NONZERO_EXIT` | Exit code != 0 and not explicitly allowed |
| `HOLD_CLAUDE_EMPTY_OUTPUT` | Both stdout and stderr empty when changes are expected |
| `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` | Forbidden command pattern detected in transcript or changed files |
| `HOLD_CLAUDE_TRANSCRIPT_MISSING` | Transcript log file not created (if required by policy) |
| `HOLD_RUNTIME_POLICY_VIOLATION` | Any runtime policy violation not covered by above states |
| `HOLD_POST_EXEC_VALIDATION_FAILED` | Post-execution validation (Section 9) failed |
| `PATCH_READY_FOR_HUMAN_REVIEW` | All gates passed; human reviews diff.patch |

### Retained Existing States

| State | Description |
|-------|-------------|
| `HOLD_INVALID_PACKET` | Packet missing required fields |
| `HOLD_PLAN_NOT_APPROVED` | No valid human approval marker |
| `HOLD_PLAN_HASH_MISMATCH` | Approved plan SHA mismatch |
| `HOLD_MAIN_DIRTY` | Main repo has staged/unstaged changes |
| `HOLD_OUTPUT_PATH_INSIDE_REPO` | output_root inside repo |
| `HOLD_WORKTREE_CREATE_FAILED` | Worktree creation failed |
| `HOLD_EXECUTOR_NOT_ALLOWED` | execution.mode not in allowed set |
| `HOLD_EXECUTOR_FAILED` | Executor subprocess crashed |
| `HOLD_REPO_MUTATION` | Main repo mutated during execution |
| `HOLD_FORBIDDEN_FILE_TOUCHED` | Forbidden file touched |
| `HOLD_OUTSIDE_ALLOWED_FILES` | File changed outside allowed_files |
| `HOLD_TOO_MANY_FILES_CHANGED` | Exceeds max_changed_files |
| `HOLD_DIFF_VALIDATION_FAILED` | Non-empty changed_files with empty diff |
| `HOLD_PMG_SNAPSHOT_FAILED` | PMG pre-snapshot failed |
| `HOLD_PMG_COMPARE_FAILED` | PMG compare failed |
| `HOLD_EXTERNAL_MUTATION` | PMG detected Hermes tree mutation |

---

## 12. Testing Strategy

Future implementation tests must conform to the following requirements:

### Mock-Only Rule

> **No unit test may call the real Claude binary.** All unit and integration tests that exercise `execution.mode="claude"` must use a mock subprocess. Tests that require a real Claude binary must be classified as "live smoke tests" and run manually, never in CI.

### Required Unit Test Coverage

| Test | Description | Expected Outcome |
|------|-------------|------------------|
| `test_claude_timeout` | Subprocess times out | Returns `HOLD_CLAUDE_TIMEOUT` |
| `test_claude_nonzero_exit` | Exit code != 0 | Returns `HOLD_CLAUDE_NONZERO_EXIT` |
| `test_claude_empty_output` | stdout and stderr both empty | Returns `HOLD_CLAUDE_EMPTY_OUTPUT` |
| `test_claude_forbidden_command_in_transcript` | Mock stdout contains `git push` | Returns `HOLD_CLAUDE_FORBIDDEN_ATTEMPT` |
| `test_claude_forbidden_file_touch` | Changed file is in forbidden_files | Returns `HOLD_FORBIDDEN_FILE_TOUCHED` |
| `test_claude_outside_allowed_files` | Changed file not in allowed_files | Returns `HOLD_OUTSIDE_ALLOWED_FILES` |
| `test_claude_pmg_dirty_after` | PMG compare returns blocked | Returns `HOLD_EXTERNAL_MUTATION` |
| `test_claude_main_dirty_after` | Main git status non-clean | Returns `HOLD_REPO_MUTATION` |
| `test_claude_no_shell_true` | Source inspection | `shell=True` not found in harness |
| `test_claude_no_pr_commands_in_executable_path` | Source inspection | No `gh pr create`, `gh pr merge` in command construction |
| `test_claude_diff_patch_persists_after_worktree_cleanup` | Run with worktree cleanup | `output_root/diff.patch` exists after worktree removed |
| `test_claude_permission_mode_verified` | Mock subprocess with verified permission mode | Executor proceeds |
| `test_claude_permission_mode_unverified` | Permission mode not verified | Returns `HOLD_CLAUDE_PERMISSION_MODE_UNVERIFIED` |

### Test Environment Requirements

| Requirement | Rationale |
|-------------|-----------|
| Temp git repos for worktree tests | Avoid polluting main repo |
| Isolated worktree per test | Prevent cross-test contamination |
| Mock filesystem fixtures for forbidden file tests | Ensure predictable error conditions |
| In-memory subprocess mock for transcript tests | Allow injection of forbidden patterns |

---

## 13. Live Smoke Policy

When `execution.mode="claude"` is eventually enabled, the **first live smoke test** must satisfy all of the following:

| Requirement | Reason |
|-------------|--------|
| Exactly **one file** changed | Minimize blast radius |
| File is **documentation only** (`.md` file) | No code execution risk |
| `allowed_files` contains exactly one doc file | Tight constraint |
| `max_changed_files = 1` | Enforce single file |
| **No test execution** during smoke | Avoid side effects |
| **No package install** | Prevent environment pollution |
| **No repair loop** | Executor runs once, stops |
| **Short timeout** (e.g., 60s max) | Quick failure if wrong |
| **Human watching terminal** | Immediate intervention possible |
| **Output reviewed before any further step** | Human gate |
| **No PR creation** after smoke | Explicit constraint |
| **No merge** after smoke | Explicit constraint |
| **Interactive TTY required** — CI/non-interactive environments explicitly prohibited for live mode | Prevents automated unattended invocation; `HOLD_REAL_EXECUTOR_NOT_ENABLED` must be returned if `sys.stdout.isatty()` is False |
| Smoke task recorded in design doc after approval | Audit trail |

**Definition:** A "live smoke test" is a manual, human-supervised execution of the real executor against a real Claude binary outside of any automated test pipeline.

---

## 14. Rollback and Cleanup

### Worktree Cleanup

When execution completes (success or failure), the worktree must be cleaned up:

```bash
git worktree remove <worktree_root> --force 2>/dev/null
rm -rf <worktree_root>
```

If cleanup fails:
- Return `HOLD_WORKTREE_CLEANUP_FAILED`
- **Do not** attempt to clean up main repo
- **Do not** raise exceptions that might trigger repair loops
- Report the failure in the result and next_action

### Preservation Requirements

The following must **always** be preserved regardless of worktree cleanup outcome:

| Artifact | Path |
|----------|------|
| Execution result JSON | `output_root/result.json` |
| Execution result Markdown | `output_root/result.md` |
| Executor stdout log | `output_root/executor_stdout.log` |
| Executor stderr log | `output_root/executor_stderr.log` |
| Claude transcript | `output_root/executor_transcript.log` |
| PMG snapshot JSON | `output_root/pmg_snapshot.json` |
| PMG compare JSON | `output_root/pmg_compare.json` |
| PMG compare Markdown | `output_root/pmg_compare.md` |
| diff.patch | `output_root/diff.patch` |
| Execution packet | `output_root/packet.json` |

### Main Repo Protection

- **Never** run cleanup commands against the main repo
- **Never** attempt `git clean -fdx` on main
- If worktree cleanup fails, report and halt — do not escalate to main cleanup

---

## 15. Readiness Checklist Before Implementation

Implementation of `execution.mode="claude"` **must not begin** until all of the following are satisfied:

### Pre-Implementation Checklist

- [ ] **This design document reviewed** — at least one human has read and approved Section 1–15
- [ ] **Current mock harness tests green** — `pytest tests/test_run_temp_worktree_execution.py -q` passes
- [ ] **PMG live smoke green** — PMG snapshot/compare works against live Hermes tree
- [ ] **Bridge smoke green** — `bridge_to_execution_packet.py` produces valid packets end-to-end
- [ ] **final_gate_status.py usable** — returns `READY_TO_MERGE` for a clean mock-only PR
- [ ] **Permission mode re-verified** — `claude --permission-mode` flag name, values, and behavior confirmed against installed Claude Code binary
- [ ] **Exact command shape approved** — the invocation in Section 4 reviewed and signed off by human
- [ ] **Unit test plan approved** — Section 12 test cases reviewed and accepted
- [ ] **First live smoke task selected** — documentation-only single-file task identified and approved
- [ ] **User explicitly approves** — `execution.mode="claude"` addition explicitly authorized by human decision
- [ ] **Feature flag designed** — `--enable-real-claude-executor` or equivalent gate designed and agreed
- [ ] **Interactive TTY confirmation required** — live mode requires `sys.stdout.isatty()` == True; CI/non-interactive environments blocked
- [ ] **No autonomous promotion** — confirms that `PATCH_READY_FOR_HUMAN_REVIEW` never auto-advances to merge, PR, or dispatch

---

## 16. Recommended First Implementation PR After Design

When the checklist in Section 15 is complete and a future implementation PR is created, it must follow this minimum scope:

### Minimum Viable Implementation PR

1. **Feature flag required** — Real executor disabled by default. Add `--enable-real-claude-executor` flag to `run_temp_worktree_execution.py` CLI. Without flag, returns `HOLD_REAL_EXECUTOR_NOT_ENABLED`
2. **Unit tests mock Claude** — All new tests use mock subprocess. Real Claude binary never invoked in CI
3. **No live Claude in CI** — CI runs only mock mode and static analysis. Live smoke is manual only
4. **One live manual smoke only after merge** — First live smoke executed by human after PR merge, never as part of the PR itself
5. **Documentation-only allowed file** — First smoke allowed file must be a `.md` doc file, not code, not tests, not config
6. **No repair loop** — Executor runs once, returns state, halts. No retry logic by default
7. **No PR creation** — No `gh pr create` anywhere in implementation or tests
8. **No merge** — No `gh pr merge` anywhere in implementation or tests
9. **All new states from Section 11 implemented** — At minimum the states listed in Section 11
10. **Forbidden command scan implemented** — At minimum the patterns listed in Section 10
11. **Permission mode verification stub** — `HOLD_CLAUDE_PERMISSION_MODE_UNVERIFIED` state implemented and triggered when verification step is skipped
12. **output_root/diff.patch contract fulfilled** — Diff artifact written to preserved output_root, survives worktree cleanup

### Out of Scope for First Implementation PR

- Autonomous repair loops
- Live Claude in CI
- Multi-file allowed file lists for first smoke
- Non-documentation allowed files for first smoke
- Auto-PR creation
- Auto-merge
- Board integration
- Hermes skill mutation

---

## Appendix: Related Scripts and Files

| File | Role |
|------|------|
| `scripts/local/run_temp_worktree_execution.py` | Mock-only execution harness (unchanged by this design) |
| `scripts/local/check_persistent_mutation_guard.py` | PMG tool |
| `scripts/local/final_gate_status.py` | Final gate reporter |
| `scripts/local/verify_final_head_merge_command.py` | Merge command verifier |
| `scripts/local/bridge_to_execution_packet.py` | Plan-to-packet bridge |
| `scripts/local/plan_preview_eval_status.py` | Plan controller |
| `tests/test_run_temp_worktree_execution.py` | Mock harness tests (mock-only) |
| `docs/real_claude_executor_readiness_gate.md` | This document |

---

*This document is design-only. No implementation of `execution.mode="claude"` is present in this PR. Real Claude execution remains disabled pending explicit human authorization and completion of the readiness checklist.*