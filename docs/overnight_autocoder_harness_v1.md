# AED Overnight Autocoder Harness v1

**Version:** 1
**Status:** Experimental — dry-run and packet-prep modes, no real task execution

---

## Purpose

The Overnight Autocoder Harness v1 orchestrates safe AED unattended runs by
wrapping the autocoder controller and persistent mutation guard into a single
deterministic script. It is intended to be run by a scheduler (cron, systemd
timer, etc.) overnight.

Two modes are available:

| Mode | Description |
|------|-------------|
| `dry-run` | Verifies preconditions; simulates task processing; produces human-reviewable summary. No real execution. |
| `packet-prep` | Same safety preconditions as dry-run, but generates Claude Code worker packets for each dependency-satisfied task. No execution, no dispatch, no PR, no merge, no audit. |

Neither mode executes real tasks, dispatches work, creates PRs, merges, or appends audit entries.

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
| Generate worker packets (packet-prep only) | Create skills |
| Stop for human review (packet-prep only) | Execute Claude Code (packet-prep only) |

---

## CLI

```bash
# Dry-run mode
python3 scripts/local/run_overnight_autocoder_harness.py \
  --run-id <run_id> \
  --tasks-jsonl <tasks.jsonl> \
  --workspace /tmp/aed_runs/<run_id> \
  --integration-branch <branch> \
  --hermes-root /home/max/.hermes \
  --repo-root /home/max/Automated-Edge-Discovery \
  --mode dry-run

# Packet-prep mode
python3 scripts/local/run_overnight_autocoder_harness.py \
  --run-id <run_id> \
  --tasks-jsonl <tasks.jsonl> \
  --workspace /tmp/aed_runs/<run_id> \
  --integration-branch <branch> \
  --hermes-root /home/max/.hermes \
  --repo-root /home/max/Automated-Edge-Discovery \
  --mode packet-prep
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
     record-task-result (TASK_READY, not_promoted)
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

## Packet-Prep Sequence

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

8. Load all tasks from TASKS.jsonl

9. For each dependency-satisfied task (in dependency order):
   a. Build Claude Code worker packet via build_worker_packet.py
      → BLOCK if packet build fails
   b. Verify packet output paths are under workspace
      → BLOCK if any path escapes workspace
   c. Record packet path in summary
   d. record-task-result (TASK_READY, not_promoted)
      → BLOCK if record fails

10. Compare Hermes state (post-run snapshot vs pre-run)
    → BLOCK if compare fails
    → BLOCK if recommendation == BLOCK

11. Record compare result in controller
    → BLOCK if record fails

12. Get next_action from controller
    → BLOCK if next_action is request_human

13. Produce OVERNIGHT_RUN_SUMMARY.json and .md under workspace

14. Exit with:
    - exit code 0 if recommendation == READY_FOR_REVIEW
    - exit code 2 if recommendation == BLOCK
```

Dependency order: a task is processed only when all `depends_on` task IDs have
already been recorded. This produces a topologically sorted packet sequence.

---

## Worker Packets (packet-prep only)

For each dependency-satisfied task, the harness generates two files under
`<workspace>/worker_packets/`:

| File | Content |
|------|---------|
| `<task_id>.worker_packet.json` | Complete worker handoff packet (JSON) |
| `<task_id>.worker_packet.md` | Human-readable packet (markdown) |

The packet is built by `scripts/local/build_worker_packet.py` with:
- `--task-json` — temporary per-task JSON written then deleted
- `--controller-state` — the harness's `CONTROLLER_STATE.json`
- `--workspace` — the run workspace
- `--worker claude_code`
- `--output-json` / `--output-md` — paths under `<workspace>/worker_packets/`

Packets are NOT authority grants. They do not let Claude Code push, create PRs,
merge, append audit logs, dispatch, create boards, update memory/profile, or
create skills.

---

## Output Files

All output files are written under `--workspace`:

| File | Content |
|------|---------|
| `CONTROLLER_STATE.json` | Full controller state |
| `persistent_state_before.json` | Guard snapshot (pre-run Hermes state) |
| `persistent_state_after.json` | Guard compare result (JSON) |
| `persistent_state_report.md` | Guard compare report (markdown) |
| `OVERNIGHT_RUN_SUMMARY.json` | Run summary (JSON) |
| `OVERNIGHT_RUN_SUMMARY.md` | Run summary (markdown, human-readable) |
| `worker_packets/<task_id>.worker_packet.json` | Worker packet (packet-prep only) |
| `worker_packets/<task_id>.worker_packet.md` | Worker packet markdown (packet-prep only) |

---

## Summary JSON Schema

```json
{
  "run_id": "aed-overnight-001",
  "mode": "packet-prep",
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
  "worker_packets_created": [
    "/tmp/aed_runs/aed-overnight-001/worker_packets/task-001.worker_packet.json",
    "/tmp/aed_runs/aed-overnight-001/worker_packets/task-002.worker_packet.json"
  ],
  "worker_packets_count": 2,
  "claude_code_executed": false,
  "human_action_required": true,
  "recommendation": "READY_FOR_REVIEW",
  "blocked_reason": null,
  "dry_run_only": true,
  "no_real_work_executed": true,
  "timestamp": "2026-05-18T23:30:00Z"
}
```

New fields in `packet-prep` mode:
- `worker_packets_created` — array of absolute paths to generated `.worker_packet.json` files
- `worker_packets_count` — integer count of packets generated
- `claude_code_executed` — always `false` in both modes (packet-prep generates packets but never invokes Claude Code)

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
| Worker packet build fails (packet-prep) | `packet_build_failed:{task_id}` |
| Worker packet output path escapes workspace (packet-prep) | `packet_path_outside_workspace:{path}` |
| Any task record fails | `task_record_failed:{task_id}` |
| Guard compare fails | `guard_compare_failed` |
| Guard compare recommendation == BLOCK | `persistent_mutation_guard_blocked` |
| Record compare fails | `record_compare_failed` |
| Controller next_action is request_human | `controller_requests_human:{reason}` |

---

## Persistent Mutation Guard Integration

The harness runs two guard commands in both modes:

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
11. **Packet path containment**: In packet-prep mode, any worker packet output
    path that escapes the workspace triggers BLOCK.
12. **Task promotion blocked**: In packet-prep mode, tasks are recorded as
    `TASK_READY` with `promotion_status=not_promoted`. Packets do not grant
    merge authority.

---

## Relationship to Other Tools

- **`autocoder_run_controller.py`**: Called by the harness to initialize state,
  record task results, record guard snapshot/compare, and compute next actions.
- **`check_persistent_mutation_guard.py`**: Called by the harness to snapshot
  and compare Hermes state before and after the (simulated) run.
- **`build_worker_packet.py`**: Called by the harness in packet-prep mode to
  generate worker packets for each dependency-satisfied task. Never called
  with the intent to execute the packet.
- **`build_autocoder_run_summary.py`**: Not called by the harness. The harness
  produces its own `OVERNIGHT_RUN_SUMMARY.md` from scratch.
- **`append_merge_action_audit.py`**: Not called by the harness. Audit append
  is reserved for human operators after real execution.

---

## v1 Limitations

- Only `dry-run` and `packet-prep` modes are implemented. Real task execution
  requires a separate authorized run step.
- No scheduling integration (cron, systemd timer) is provided. The operator
  invokes the harness manually or via an external scheduler.
- The harness does not retry failed steps. A failure blocks the entire run.
- No email/webhook notification is produced on BLOCK. The operator must check
  the summary files after each run.
- packet-prep does not execute Claude Code — packets are generated but must be
  reviewed and executed manually (or via a future authorized run step).

---

## Usage Example

```bash
# Create TASKS.jsonl for the overnight run
cat > /tmp/aed_runs/overnight-001/TASKS.jsonl <<'EOF'
{"task_id": "task-001", "task_type": "docs", "depends_on": [], "blocks": [],
 "allowed_files": ["README.md"], "objective": "Update README",
 "existing_code_reuse": {"enabled": true, "instructions": []},
 "dependency_context": {"enabled": false}}
{"task_id": "task-002", "task_type": "docs", "depends_on": ["task-001"], "blocks": [],
 "allowed_files": ["CONTRIBUTING.md"], "objective": "Update CONTRIBUTING",
 "existing_code_reuse": {"enabled": false},
 "dependency_context": {"enabled": false}}
EOF

# Run packet-prep to generate worker packets
python3 scripts/local/run_overnight_autocoder_harness.py \
  --run-id overnight-001 \
  --tasks-jsonl /tmp/aed_runs/overnight-001/TASKS.jsonl \
  --workspace /tmp/aed_runs/overnight-001 \
  --integration-branch integration/overnight-001 \
  --hermes-root /home/max/.hermes \
  --repo-root /home/max/Automated-Edge-Discovery \
  --mode packet-prep

# Inspect result
cat /tmp/aed_runs/overnight-001/OVERNIGHT_RUN_SUMMARY.md

# Review generated packets
ls /tmp/aed_runs/overnight-001/worker_packets/

# If recommendation is READY_FOR_REVIEW, human reviews packets and proceeds
# If recommendation is BLOCK, operator resolves the blocking condition first
```