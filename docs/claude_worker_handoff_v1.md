# Claude Code Worker Handoff Packet v1

**Version:** 1
**Status:** Experimental — bounded worker handoff, no authority grant
**Packet Kind:** `aed.worker.packet.v1`

---

## Purpose

The Claude Code Worker Handoff Packet v1 is a structured scope document that
allows Humphry (the AED controller and repo operator) to delegate implementation
work to Claude Code while maintaining bounded control.

The packet tells Claude Code:

1. Exactly what task to implement
2. Which files are allowed
3. Which files are forbidden
4. What tests to run
5. What context files to read
6. What it must not do
7. What evidence it must return

The packet is **NOT** an authority grant. It does not let Claude Code push,
create PRs, merge, append audit logs, dispatch Hermes/kanban, create boards,
update memory/profile, or create skills.

---

## Packet Schema

```json
{
  "packet_kind": "aed.worker.packet.v1",
  "packet_version": 1,
  "generated_at": "2026-05-18T00:00:00Z",
  "worker": "claude_code",
  "task_id": "...",
  "objective": "...",
  "task_type": "...",
  "risk_level": "low",
  "allowed_files": [],
  "forbidden_files": [],
  "expected_outputs": [],
  "context_files": [],
  "tests_to_run": [],
  "do_not": [
    "do not push",
    "do not create PR",
    "do not merge",
    "do not append audit log",
    "do not dispatch",
    "do not touch production board",
    "do not update memory or profile",
    "do not create skills"
  ],
  "required_return": [
    "changed_files",
    "test_results",
    "blockers",
    "risk_notes",
    "scope_notes"
  ],
  "controller_context": {
    "run_id": "...",
    "integration_branch": "...",
    "current_task_id": "...",
    "next_action": "run_task"
  },
  "safety_invariants": {
    "hermes_touched": false,
    "dispatch_occurred": false,
    "production_board_touched": false,
    "memory_or_profile_updated": false,
    "skills_created": false
  },
  "reuse_check": {
    "instructions": [
      "1. Search for existing helpers, services, and utilities",
      "2. List any reusable code candidates",
      "3. Avoid parallel implementations unless justified",
      "4. Note any service-layer extraction opportunity"
    ],
    "enforced": false
  },
  "recommended_worker_reason": "..."
}
```

---

## Field Reference

### `packet_kind`
Always `aed.worker.packet.v1`.

### `packet_version`
Always `1`.

### `worker`
Worker name. v1 supports only `claude_code`.

### `task_id`
Unique task identifier from the controller run.

### `objective`
Human-readable description of what to implement.

### `task_type`
One of: `impl`, `docs`, `test`, `debug`, `unknown`.

### `risk_level`
One of: `low`, `medium`, `high`. Derived from `task_type` and file count.
- `docs` → `low`
- `impl` with ≤5 allowed files → `low`
- `impl` with >5 allowed files → `medium`

### `allowed_files`
List of file paths Claude Code may edit. Derived from the task's `allowed_files`
field. Must be non-empty.

### `forbidden_files`
List of file paths Claude Code must not edit. Derived from the task's
`forbidden_files` field. May be empty.

### `expected_outputs`
List of expected artifact descriptions (e.g. new files, modified files).
Preserved from task JSON if present.

### `context_files`
List of file paths Claude Code should read before starting. Preserved from
task JSON if present.

### `tests_to_run`
List of shell commands to run as validation. Preserved from task JSON if present.

### `do_not`
Hard constraints. These actions are always forbidden regardless of packet scope.
The worker must not:
- Push branches
- Create PRs
- Merge PRs
- Append audit logs
- Dispatch Hermes or Kanban
- Touch the production board
- Update memory or profile
- Create skills

### `required_return`
Fields the worker must include in its completion report:
- `changed_files` — list of files modified
- `test_results` — output summary of tests run
- `blockers` — any blockers encountered
- `risk_notes` — risk observations
- `scope_notes` — scope compliance notes

### `controller_context`
Binds the worker packet to the parent controller run:
- `run_id` — from `CONTROLLER_STATE.json` (if provided)
- `integration_branch` — from `CONTROLLER_STATE.json` (if provided)
- `current_task_id` — the task being delegated
- `next_action` — always `run_task` in v1

### `safety_invariants`
Hard safety flags. All default to `false`. Any `true` value in this packet
indicates the controller detected a safety boundary violation and is halting.

### `reuse_check`
Instructions for the worker to search for reusable code before implementing.
`enforced: false` means the check is advisory only — the worker is responsible
for following it.

### `recommended_worker_reason`
Human-readable reason for the worker recommendation:
- `docs task, Claude Code optional` — for `task_type == "docs"`
- `multi-file implementation or debugging task, use Claude Code` — for
  `len(allowed_files) > 1`
- `single-file task, Claude Code optional` — for single-file impl tasks

---

## CLI Syntax

```bash
python3 scripts/local/build_worker_packet.py \
  --task-json /tmp/task.json \
  --controller-state /tmp/CONTROLLER_STATE.json \
  --bundle-index /tmp/BUNDLE_INDEX.json \
  --workspace /tmp/aed_run \
  --worker claude_code \
  --output-json /tmp/worker_packet.json \
  --output-md /tmp/worker_packet.md
```

### Required arguments

| Argument | Description |
|----------|-------------|
| `--task-json` | Path to a single task JSON file (task object, not array) |
| `--workspace` | Absolute path to the run workspace |
| `--worker` | Worker name (only `claude_code` supported in v1) |
| `--output-json` | Path to write the packet JSON |
| `--output-md` | Path to write the packet markdown |

### Optional arguments

| Argument | Description |
|----------|-------------|
| `--controller-state` | Path to `CONTROLLER_STATE.json` (fills `controller_context`) |
| `--bundle-index` | Path to `BUNDLE_INDEX.json` (available but not embedded in v1) |

---

## Markdown Output Format

The markdown output is directly pasteable into Claude Code:

```markdown
# Claude Code Worker Packet

Task: <task_id>
Worker: claude_code
Objective: ...

## Allowed files
- `scripts/local/my_helper.py`
- `tests/test_my_helper.py`

## Forbidden files
_engine/_
_schemas/_

## Expected outputs
- New file: `scripts/local/my_helper.py`

## Required tests
- `PYTHONPATH=. python3 -m pytest tests/test_my_helper.py -q`

## Context files to read
- `scripts/local/build_worker_packet.py`

## Reuse check
- 1. Search for existing helpers, services, and utilities
- 2. List any reusable code candidates
- 3. Avoid parallel implementations unless justified
- 4. Note any service-layer extraction opportunity

## Hard constraints
- Do not push.
- Do not create PR.
- Do not merge.
- Do not append audit log.
- Do not dispatch.
- Do not touch production board.
- Do not update memory or profile.
- Do not create skills.

## Return format
Return the following fields when done:
- `changed_files`
- `test_results`
- `blockers`
- `risk_notes`
- `scope_notes`

## Controller context
- **run_id:** `aed-run-001`
- **integration_branch:** `integration/aed-run-001`
- **current_task_id:** `test-task-001`
- **next_action:** `run_task`

**Packet generated:** `2026-05-18T00:00:00Z`
```

---

## Worker Selection Rules (v1)

v1 supports only `claude_code` as a worker. The packet sets a
`recommended_worker_reason` field to guide the controller:

| Condition | `recommended_worker_reason` |
|-----------|---------------------------|
| `task_type == "docs"` | `docs task, Claude Code optional` |
| `len(allowed_files) > 1` | `multi-file implementation or debugging task, use Claude Code` |
| otherwise | `single-file task, Claude Code optional` |

---

## Safety Invariants

The packet carries five safety invariants, all defaulting to `false`:

| Field | Meaning if `true` |
|-------|------------------|
| `hermes_touched` | Hermes create/dispatch was called — halt |
| `dispatch_occurred` | Kanban dispatch was triggered — halt |
| `production_board_touched` | Production board was modified — halt |
| `memory_or_profile_updated` | Memory or user profile was modified — report only |
| `skills_created` | A new skill was created — report only |

Hard invariants (`hermes_touched`, `dispatch_occurred`,
`production_board_touched`) cause the controller to transition to
`RUN_FAILED_SAFETY` and halt further automated actions.

---

## What the Packet Does NOT Do

The worker packet is a scope document, not an authority grant. It does not:

- ✅ Tell Claude Code **what to implement** (objective + allowed files)
- ✅ Tell Claude Code **what not to touch** (forbidden files)
- ✅ Tell Claude Code **which tests to run** (tests_to_run)
- ✅ Tell Claude Code **what context to read** (context_files)
- ❌ Push to the repository
- ❌ Create or merge PRs
- ❌ Append to the audit log
- ❌ Dispatch Hermes or Kanban
- ❌ Update memory or profile
- ❌ Create skills

---

## v1 Scope Boundaries

| Allowed | Forbidden |
|---------|-----------|
| Read task JSON | Write to source code |
| Read CONTROLLER_STATE.json | Push branches |
| Read BUNDLE_INDEX.json | Create PRs |
| Write packet JSON and MD | Merge PRs |
| Emit markdown for clipboard | Append audit log |
| | Dispatch Hermes/Kanban |
| | Update memory/profile |
| | Create skills |

---

## Relationship to Other Tools

- **`autocoder_run_controller.py`** — produces `CONTROLLER_STATE.json` which
  `build_worker_packet.py` consumes via `--controller-state`.

- **`build_autocoder_run_summary.py`** — invoked after all tasks in a run are
  complete; not used during worker handoff.

- **`append_merge_action_audit.py`** — invoked by Humphry after PR merge; not
  called during worker handoff.

- **`aed_executor_packet.py`** — produces an executor plan for a full PR from
  a roadmap candidate. The worker packet is a more bounded task-scoped derivative
  intended for per-task delegation to Claude Code.

---

## Future Work

- `opensrc` worker support (next PR after this one)
- Worker protocol version negotiation
- Bidirectional result reporting schema
- Enforcement of `reuse_check` (currently advisory only)