# Single-Task Autocoder Controller — Design

## 1. Objective

`run_autocoder_single_task.py` chains existing safe AED tools to execute one strict task packet through the verified six-stage pipeline, then stops at `PR_PREVIEW_READY` without pushing, opening PRs, merging, committing, staging files, or mutating main.

It is a **read-only orchestrator** that invokes existing approved tools via explicit `subprocess.run` calls with no `shell=True`.

## 2. Scope

### In scope
- Task packet validation
- Six-stage pipeline orchestration
- Artifact preservation at every stage
- Fail-closed behavior on any non-ready status
- Final review packet generation

### Out of scope (hard boundaries)
- Live Claude execution in v0
- Push to any remote
- PR creation
- Merge
- Commit
- Staging / `git add`
- Dispatch
- Board mutation
- Hermes mutation
- External audit log appends
- Memory/profile updates
- Package installation
- `shell=True` subprocess calls

## 3. Inputs

### Task Packet JSON
```json
{
  "packet_kind": "aed.autocoder.single_task.v0",
  "task_id": "string (unique identifier, used in output_root naming)",
  "goal": "string (human-readable task description)",
  "allowed_files": ["string"] or null (null = all allowed),
  "forbidden_files": ["string"] or null (null = none forbidden),
  "max_changed_files": integer or null (null = no limit),
  "required_tests": ["string"] or null,
  "output_root": "/tmp/aed_runs/autocoder_single_task_<task_id>",
  "branch_name": "string (local branch, created by controller)",
  "suggested_pr_title": "string",
  "suggested_pr_body": "string",
  "execution_mode": "mocked (v0) or claude (future)"
}
```

### Validation Rules
| Field | Rule |
|---|---|
| `packet_kind` | Must be `aed.autocoder.single_task.v0` |
| `task_id` | Non-empty string, alphanumeric + `-` + `_` |
| `goal` | Non-empty string, 10–1000 chars |
| `branch_name` | Must not exist locally (`git rev-parse --verify` fails) |
| `output_root` | Must be outside the repo (`output_root` must not be inside REPO_ROOT) |
| `allowed_files` | Null or list of non-empty strings |
| `forbidden_files` | Null or list of non-empty strings |
| `execution_mode` | Must be `mocked` in v0; `claude` rejected with `HOLD_FORBIDDEN_MUTATION_RISK` |

## 4. Six-Stage Pipeline

Each stage writes its output JSON/MD to `<output_root>/`.

### Stage 1 — Execution Packet Build
- Convert task packet → execution packet for `run_temp_worktree_execution.py`
- Inject `packet_kind = aed.temp_worktree.execution.v0`
- Set `execution_mode = mocked` (live Claude not used in v0)
- Write `execution_packet.json`
- **Requires**: task packet valid

### Stage 2 — Temp Worktree Execution
- Run `python3 scripts/local/run_temp_worktree_execution.py --packet-json <exec_packet> --output-json <output>/result.json --output-md <output>/result.md`
- Poll with `wait()` (blocking, no timeout in v0 — caller controls lifecycle)
- **Requires**: status == `PATCH_READY_FOR_HUMAN_REVIEW`
- On failure: write `final_status.json` with `HOLD_EXECUTION_NOT_PATCH_READY`, stop

### Stage 3 — Apply Readiness Verification
- Run `python3 scripts/local/verify_temp_worktree_apply_readiness.py --result-json <output>/result.json --diff-patch <output>/diff.patch --repo-root <repo> --output-json <output>/apply_readiness.json --output-md <output>/apply_readiness.md --require-pmg-clean`
- **Requires**: status == `APPLY_READY`
- On failure: write `final_status.json` with `HOLD_APPLY_NOT_READY`, stop

### Stage 4 — Apply Preview
- Run `python3 scripts/local/preview_temp_worktree_apply.py --result-json <output>/result.json --diff-patch <output>/diff.patch --apply-readiness-json <output>/apply_readiness.json --repo-root <repo> --output-json <output>/apply_preview.json --output-md <output>/apply_preview.md`
- **Requires**: status == `APPLY_PREVIEW_READY`
- On failure: write `final_status.json` with `HOLD_APPLY_PREVIEW_NOT_READY`, stop

### Stage 5 — Apply to Local Branch
- Run `python3 scripts/local/apply_temp_worktree_patch_to_branch.py --target-repo <repo> --result-json <output>/result.json --diff-patch <output>/diff.patch --apply-readiness-json <output>/apply_readiness.json --expected-base-sha <base_sha> --branch-name <branch_name> --output-json <output>/apply_to_branch.json --output-md <output>/apply_to_branch.md --allow-real-apply`
- **Requires**: status == `APPLY_TO_BRANCH_APPLIED`
- On failure: write `final_status.json` with `HOLD_APPLY_TO_BRANCH_FAILED`, stop

### Stage 6 — Applied Branch Verification
- Run `python3 scripts/local/verify_temp_worktree_applied_branch.py --repo-root <repo> --branch-name <branch_name> --expected-base-sha <base_sha> --result-json <output>/result.json --diff-patch <output>/diff.patch --apply-readiness-json <output>/apply_readiness.json --output-json <output>/applied_branch_verification.json --output-md <output>/applied_branch_verification.md`
- **Requires**: status == `APPLIED_BRANCH_READY`
- On failure: write `final_status.json` with `HOLD_APPLIED_BRANCH_NOT_READY`, stop

### Stage 7 — PR Preview
- Run `python3 scripts/local/preview_applied_branch_pr.py --repo-root <repo> --applied-branch-json <output>/applied_branch_verification.json --branch-name <branch_name> --base-branch main --expected-base-sha <base_sha> --output-json <output>/pr_preview.json --output-md <output>/pr_preview.md --suggested-pr-title "<title>" --suggested-pr-body "<body>"`
- **Requires**: status == `PR_PREVIEW_READY`
- On failure: write `final_status.json` with `HOLD_PR_PREVIEW_NOT_READY`, stop

### Stage 8 — Final Review Packet
- Write `final_review_packet.json` and `final_review_packet.md`
- Write `final_status.json` with `SINGLE_TASK_READY_FOR_HUMAN_REVIEW`
- **Stop** — no push, no PR, no merge

## 5. Output Structure

```
/tmp/aed_runs/autocoder_single_task_<task_id>/
├── task_packet.json              # input
├── execution_packet.json         # stage 1
├── result.json                   # stage 2
├── result.md
├── diff.patch                    # from stage 2
├── apply_readiness.json          # stage 3
├── apply_readiness.md
├── apply_preview.json            # stage 4
├── apply_preview.md
├── apply_to_branch.json          # stage 5
├── apply_to_branch.md
├── applied_branch_verification.json  # stage 6
├── applied_branch_verification.md
├── pr_preview.json               # stage 7
├── pr_preview.md
├── final_review_packet.json      # stage 8
├── final_review_packet.md
└── final_status.json             # always written
```

## 6. Status Taxonomy

| Status | Meaning |
|---|---|
| `SINGLE_TASK_READY_FOR_HUMAN_REVIEW` | All 8 stages complete, human may approve follow-on actions |
| `HOLD_TASK_PACKET_INVALID` | Task packet failed validation |
| `HOLD_EXECUTION_NOT_PATCH_READY` | Stage 2 did not return `PATCH_READY_FOR_HUMAN_REVIEW` |
| `HOLD_APPLY_NOT_READY` | Stage 3 did not return `APPLY_READY` |
| `HOLD_APPLY_PREVIEW_NOT_READY` | Stage 4 did not return `APPLY_PREVIEW_READY` |
| `HOLD_APPLY_TO_BRANCH_FAILED` | Stage 5 did not return `APPLY_TO_BRANCH_APPLIED` |
| `HOLD_APPLIED_BRANCH_NOT_READY` | Stage 6 did not return `APPLIED_BRANCH_READY` |
| `HOLD_PR_PREVIEW_NOT_READY` | Stage 7 did not return `PR_PREVIEW_READY` |
| `HOLD_FORBIDDEN_MUTATION_RISK` | Execution mode `claude` or other risky input detected |
| `HOLD_OUTPUT_PATH_INSIDE_REPO` | `output_root` is inside the repo |
| `HOLD_UNKNOWN` | Unexpected exception or status value |

## 7. Failure Behavior

1. **Fail closed**: on first non-ready status, write `final_status.json/md` and stop.
2. **Artifact preservation**: all stage outputs are written before the failure is recorded.
3. **Cleanup**: if a local branch was created and stage 5+ failed, write the cleanup command as text in `final_status.json` (do not auto-delete).
4. **No retry**: v0 never retries live Claude automatically.
5. **No widening**: `allowed_files` is never expanded on failure.

## 8. Safety Boundaries

The controller must **never**:
- Run with `--enable-real-claude-executor` or live Claude in v0
- Call `git push` directly or via tool
- Call `gh pr create`
- Call `gh pr merge`
- Call `git commit` or `git stage`
- Call `git add`
- Modify main branch
- Dispatch work items
- Touch boards
- Mutate Hermes skills
- Append to external audit logs
- Update memory/profile
- Install packages via pip/apt/etc.
- Use `shell=True` in any subprocess call

## 9. Relationship to Batch Controller

Batch orchestration is designed separately in `docs/autocoder_batch_controller_design.md` and must call the single-task controller sequentially in v0. The single-task controller is the atomic unit of execution; the batch controller is a thin orchestrator that invokes it per task.

## 10. Subprocess Call Pattern

All tool invocations use explicit argv lists with no shell interpolation:
```python
subprocess.run(
    ["python3", str(SCRIPT_DIR / "run_temp_worktree_execution.py"),
     "--packet-json", str(exec_packet_path),
     "--output-json", str(result_json),
     "--output-md", str(result_md)],
    cwd=str(REPO_ROOT),
    capture_output=True,
    text=True,
    timeout=300,
)
```

## 11. Next Implementation PR

Implement `scripts/local/run_autocoder_single_task.py` following this design. The implementation:
1. Validates task packet against rules in §3
2. Builds execution packet from task packet (§4, stage 1)
3. Sequentially runs stages 2–8 with fail-closed gating
4. Writes artifacts at every stage
5. Writes `final_status.json` and `final_review_packet.json/md` at end
6. Uses no `shell=True`, no live Claude, no mutating operations