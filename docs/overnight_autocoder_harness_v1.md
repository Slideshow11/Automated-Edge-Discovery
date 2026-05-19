# AED Overnight Autocoder Harness v1

**Version:** 1
**Status:** Experimental — dry-run only, no real task execution

---

## Purpose

The Overnight Autocoder Harness v1 orchestrates safe AED unattended runs by
wrapping the autocoder controller and persistent mutation guard into a single
deterministic script. It is intended to be run by a scheduler (cron, systemd
timer, etc.) overnight. It does **not** execute real tasks, dispatch work, create
PRs, merge, or append audit entries.

v1 is strictly a **dry-run harness**. It validates the preconditions for an AED
run and produces a human-reviewable summary. A human must then authorize any
real execution.

---

## Scope

| Allowed | Forbidden |
|---------|-----------|
| Initialize controller state | Execute real tasks |
| Run persistent mutation guard snapshot/compare | Dispatch to production board |
| Record guard results in controller | Create PRs |
| Produce JSON and markdown summary | Merge PRs |
| Stop for human review | Append audit log |
| Verify clean repo state | Hermes create/dispatch |
| Block on any safety violation | Modify memory/profile |
| | Create skills |

---

## CLI

```bash
python3 scripts/local/run_overnight_autocoder_harness.py \
  --run-id <run_id> \
  --tasks-jsonl <tasks.jsonl> \
  --workspace /tmp/aed_runs/<run_id> \
  --integration-branch <branch> \
  --hermes-root /home/max/.hermes \
  --repo-root /home/max/Automated-Edge-Discovery \
  --mode dry-run
```

All arguments required except `--mode` (default: `dry-run`).

---

## Dry-Run Sequence

```
1. Verify repo working tree is clean
   → BLOCK if dirty

2. Verify workspace is not inside repo root
   → BLOCK if workspace ⊆ repo

3. Verify TASKS.jsonl exists and has valid JSON lines
   → BLOCK if missing or malformed

4. Initialize autocoder controller state
   → BLOCK if controller init fails

5. Check safety invariants in controller state
   → BLOCK if hermes_touched, dispatch_occurred, or production_board_touched

6. Take persistent mutation guard snapshot of Hermes state
   → BLOCK if guard snapshot fails

7. Record snapshot path in controller
   → BLOCK if record fails

8. For each task in TASKS.jsonl:
     record-task-result (TASK_READY, not_promoted) in dry-run mode
   → BLOCK if any task record fails

9. Compare Hermes state (post-run snapshot vs pre-run)
   → BLOCK if compare fails
   → BLOCK if recommendation == BLOCK

10. Record compare result in controller
    → BLOCK if record fails

11. Get next_action from controller
    → BLOCK if next_action is request_human

12. Produce OVERNIGHT_RUN_SUMMARY.json and .md under workspace

13. Exit with:
    - exit code 0 if recommendation == READY_FOR_REVIEW
    - exit code 2 if recommendation == BLOCK
```

---

## Output Files

All output files are written under `--workspace`:

| File | Content |
|------|---------|
| `CONTROLLER_STATE.json` | Full controller state |
| `persistent_state_before.json` | Guard snapshot (3711+ files of Hermes state) |
| `persistent_state_after.json` | Guard compare result (JSON) |
| `persistent_state_report.md` | Guard compare report (markdown) |
| `OVERNIGHT_RUN_SUMMARY.json` | Run summary (JSON) |
| `OVERNIGHT_RUN_SUMMARY.md` | Run summary (markdown, human-readable) |

---

## Summary JSON Schema

```json
{
  "run_id": "aed-overnight-001",
  "mode": "dry-run",
  "repo_head": "9ae6dcc...",
  "workspace": "/tmp/aed_runs/aed-overnight-001",
  "integration_branch": "integration/aed-overnight-001",
  "controller_state_path": "/tmp/aed_runs/aed-overnight-001/CONTROLLER_STATE.json",
  "persistent_mutation_guard": {
    "status": "clean",
    "blocked_changes_count": 0,
    "allowed_changes_count": 0
  },
  "tasks_seen": ["task-001", "task-002"],
  "tasks_recorded": ["task-001", "task-002"],
  "human_action_required": true,
  "recommendation": "READY_FOR_REVIEW",
  "blocked_reason": null,
  "dry_run_only": true,
  "no_real_work_executed": true,
  "timestamp": "2026-05-18T23:30:00Z"
}
```

`recommendation` values:
- `READY_FOR_REVIEW` — preconditions met, safe for human to review and authorize real execution
- `BLOCK` — a safety condition was violated; see `blocked_reason`

---

## BLOCK Conditions

The harness blocks and exits code 2 if any of the following occur:

| Condition | blocked_reason |
|-----------|---------------|
| Repo working tree is dirty | `repo_not_clean` |
| Workspace is inside repo root | `workspace_in_repo` |
| TASKS.jsonl missing or malformed | `tasks_file_invalid` |
| Controller init fails | `controller_init_failed` |
| Safety invariant already true | `safety_invariant_violated` |
| Guard snapshot fails | `guard_snapshot_failed` |
| Record snapshot fails | `record_snapshot_failed` |
| Any task record fails | `task_record_failed` |
| Guard compare fails | `guard_compare_failed` |
| Guard compare recommendation == BLOCK | `persistent_mutation_guard_blocked` |
| Record compare fails | `record_compare_failed` |
| Controller next_action is request_human | `controller_requests_human:{reason}` |

---

## Persistent Mutation Guard Integration

The harness runs two guard commands:

**Snapshot (pre-run):**
```bash
python3 scripts/local/check_persistent_mutation_guard.py snapshot \
  --root /home/max/.hermes \
  --output <workspace>/persistent_state_before.json
```

**Compare (post-run):**
```bash
python3 scripts/local/check_persistent_mutation_guard.py compare \
  --root /home/max/.hermes \
  --before <workspace>/persistent_state_before.json \
  --output-json <workspace>/persistent_state_after.json \
  --output-md <workspace>/persistent_state_report.md
```

Both paths are recorded in the controller state via:
- `record-persistent-guard-snapshot`
- `record-persistent-guard-compare`

---

## Safety Properties

1. **Repo dirty → BLOCK**: Working tree must be clean before starting.
2. **Workspace isolation**: Workspace must not be inside the repo to prevent
   accidental file creation in the repo.
3. **Safety invariants checked**: Controller's `safety_invariants` block any
   pre-existing violation (hermes_touched, dispatch_occurred, production_board_touched).
4. **Guard BLOCK is fatal**: If the persistent mutation guard returns BLOCK,
   the harness stops immediately and does not proceed to summary generation.
5. **Controller request_human is fatal**: If the controller's next action is
   `request_human`, the harness blocks.
6. **No dispatch**: The harness never calls kanban dispatch or Hermes create/dispatch.
7. **No audit append**: The harness never calls `append_merge_action_audit.py`.
8. **No PR creation**: The harness never calls `gh pr create`.
9. **No merge**: The harness never calls `gh pr merge`.
10. **No skill creation**: The harness never calls `skill_manage create`.

---

## Relationship to Other Tools

- **`autocoder_run_controller.py`**: Called by the harness to initialize state,
  record task results, record guard snapshot/compare, and compute next actions.
- **`check_persistent_mutation_guard.py`**: Called by the harness to snapshot
  and compare Hermes state before and after the (simulated) run.
- **`build_autocoder_run_summary.py`**: Not called by the harness. The harness
  produces its own `OVERNIGHT_RUN_SUMMARY.md` from scratch.
- **`append_merge_action_audit.py`**: Not called by the harness. Audit append
  is reserved for human operators after real execution.

---

## v1 Limitations

- Only `dry-run` mode is implemented. Real task execution requires a separate
  authorized run step.
- No scheduling integration (cron, systemd timer) is provided. The operator
  invokes the harness manually or via an external scheduler.
- The harness does not retry failed steps. A failure blocks the entire run.
- No email/webhook notification is produced on BLOCK. The operator must check
  the summary files after each run.

---

## Usage Example

```bash
# Create TASKS.jsonl for the overnight run
cat > /tmp/aed_runs/overnight-001/TASKS.jsonl <<'EOF'
{"task_id": "task-001", "task_type": "docs_consistency", "depends_on": [], "blocks": []}
{"task_id": "task-002", "task_type": "docs_consistency", "depends_on": ["task-001"], "blocks": []}
EOF

# Run the dry-run harness
python3 scripts/local/run_overnight_autocoder_harness.py \
  --run-id overnight-001 \
  --tasks-jsonl /tmp/aed_runs/overnight-001/TASKS.jsonl \
  --workspace /tmp/aed_runs/overnight-001 \
  --integration-branch integration/overnight-001 \
  --hermes-root /home/max/.hermes \
  --repo-root /home/max/Automated-Edge-Discovery \
  --mode dry-run

# Inspect result
cat /tmp/aed_runs/overnight-001/OVERNIGHT_RUN_SUMMARY.md

# If recommendation is READY_FOR_REVIEW, human reviews and triggers real run
# If recommendation is BLOCK, operator resolves the blocking condition first
```