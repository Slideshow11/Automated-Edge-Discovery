# Quarantine Task Manifest v1

## Overview

The quarantine task manifest (`TASKS.jsonl`) defines a batch of overnight candidate tasks for AED. Each task is a planned bundle — not yet executed, not yet a PR.

**v1 invariant: no agent execution, no patch generation, no PR creation.**

The manifest drives the production of `BUNDLE_INDEX.json`, which is the morning-review artifact.

## Why Manifest-Driven Overnight Work?

AED's overnight/autonomous pipeline needs a human-reviewable definition of work before any agent runs. The manifest serves as:

1. **Intent declaration** — what to do, at what risk level, with what scope constraints
2. **Audit trail** — tasks are defined before execution, making post-hoc review possible
3. **Batch review** — reviewers can see all planned tasks in one `BUNDLE_INDEX.json` before any work begins
4. **Tooling interface** — future phases can iterate over the index and call the quarantine bundle generator per task

## One Task = One Bundle = One Possible PR

```
TASKS.jsonl (manifest of planned tasks)
    → build_quarantine_bundle_index.py (scaffold)
    → BUNDLE_INDEX.json (batch index)
        → (future phase) quarantine bundle generator per task
        → (future phase) review bundle → promotion decision
        → (future phase) PR creation
```

Each `task_id` maps to a `bundle_path` in the index. The bundle for a task is not created in v1 — v1 only records the planned path.

## TASKS.jsonl Format

Each line is a JSON object:

```jsonl
{"task_id":"docs-stale-refs-001","objective":"Find and fix stale references in docs/*.md","task_type":"docs_consistency","risk_level":"low","allowed_files":["docs/"],"forbidden_files":["scripts/"],"expected_outputs":["docs/stale_refs_report.md"],"priority":"medium","notes":"Check all .md files including subdirs","reviewer_hint":"Focus on internal links","base_sha":"ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0"}
{"task_id":"test-gap-cli-001","objective":"Identify missing test coverage for CLI argument parsing","task_type":"test_gap","risk_level":"medium","allowed_files":["scripts/local/"],"forbidden_files":["scripts/prod/deploy.py"],"expected_outputs":["test_gap_cli_report.md"],"priority":"low"}
```

### Required Fields

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Unique identifier. Safe slug: a-z, A-Z, 0-9, `_`, `-`. |
| `objective` | string | Human-readable description of what to do. Non-empty. |
| `task_type` | string | One of the allowed task type enum. |
| `risk_level` | string | One of: `low`, `medium`, `high`. |
| `allowed_files` | list[string] | Files/directories the task may touch. Non-empty. |
| `forbidden_files` | list[string] | Files the task must not touch. Must be present (may be empty). |
| `expected_outputs` | list[string] | Files/paths the task is expected to produce. Non-empty. |

### Optional Fields

| Field | Type | Description |
|---|---|---|
| `base_sha` | string | Override the index-level `base_sha` for this task. |
| `priority` | string | Any value; not validated by v1. |
| `notes` | string | Internal context for the task. |
| `reviewer_hint` | string | Hint for the morning reviewer. |
| `promotion_target` | string | Proposed branch name if the bundle is promoted to a PR. |

### Optional Dependency and Promotion Fields

The following fields control task ordering, promotion grouping, and parallel execution:

| Field | Type | Description |
|---|---|---|
| `depends_on` | list[string] | Task IDs that must complete and be promoted before this task can be promoted. |
| `blocks` | list[string] | Task IDs that this task blocks from promotion until this task is resolved. |
| `promotion_group` | string | Logical group name for tasks that should be reviewed and merged together. |
| `pr_group` | string | Groups tasks into a single PR when promotion is authorized. |
| `can_run_in_parallel` | bool | If `true`, this task has no `depends_on` and may run concurrently with other parallel-capable tasks at the same dependency level. |
| `integration_order` | int | Sort key within a promotion group. Lower values are promoted earlier. |
| `promotion_target` | string | Proposed branch name if the bundle is promoted to a PR. |

**JSON example:**

```json
{
  "task_id": "docs-task-dependency-example-001",
  "task_type": "docs_consistency",
  "depends_on": ["docs-autocoder-run-summary-example-001"],
  "blocks": [],
  "promotion_group": "docs-task-manifest",
  "pr_group": "autocoder-docs",
  "can_run_in_parallel": false,
  "integration_order": 2,
  "promotion_target": "integration/aed-patch-rehearsal-003"
}
```

**Dependency resolution rules:**

1. A task with `depends_on` cannot be promoted until all dependencies have `promotion_status = promoted_to_integration`.
2. A task with `blocks` prevents the blocked task from being promoted until the blocking task is resolved (`TASK_READY` or `TASK_BLOCKED`).
3. `promotion_group` controls which tasks are reviewed together; `pr_group` groups them into a single PR when merging.
4. `can_run_in_parallel: true` is only valid for tasks with no `depends_on` entries.
5. `integration_order` is a tiebreaker when multiple tasks are ready for promotion at the same level.

### Allowed `task_type` Values

- `docs_consistency` — stale links, formatting, internal references
- `test_gap` — missing test coverage for existing code
- `fixture_schema_alignment` — JSON schema validation for fixtures
- `dependency_hygiene` — unused imports, dead code, venv artifacts
- `ci_hygiene` — GitHub Actions workflow consistency
- `safety_grep_audit` — review of forbidden command usage
- `repo_map` — directory structure, file organization
- `design_note` — documentation of design decisions
- `other` — catch-all for uncategorized tasks

### Allowed `risk_level` Values

- `low` — read-only, no code changes expected
- `medium` — may touch code but not production systems
- `high` — may touch production-adjacent code or infrastructure

## BUNDLE_INDEX.json Format

Produced by `build_quarantine_bundle_index.py` from a `TASKS.jsonl` manifest.

```json
{
  "index_version": 1,
  "generated_at": "2026-05-16T03:30:00Z",
  "repo": "Slideshow11/Automated-Edge-Discovery",
  "base_sha": "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
  "bundle_root": "/path/to/bundles",
  "dry_run": true,
  "task_count": 2,
  "tasks": [
    {
      "task_id": "docs-stale-refs-001",
      "objective": "Find and fix stale references in docs/*.md",
      "task_type": "docs_consistency",
      "risk_level": "low",
      "allowed_files": ["docs/"],
      "forbidden_files": [],
      "expected_outputs": ["docs/stale_refs_report.md"],
      "bundle_path": "/path/to/bundles/docs-stale-refs-001",
      "status": "planned",
      "promotion_recommendation": "not_evaluated",
      "process_score_status": "not_evaluated",
      "priority": "medium",
      "notes": "Check all .md files including subdirs",
      "reviewer_hint": "Focus on internal links",
      "base_sha": "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0"
    }
  ],
  "agent_executed": false,
  "patch_applied": false,
  "dispatch_occurred": false,
  "hermes_touched": false,
  "production_board_touched": false,
  "pr_created": false,
  "import_performed": false
}
```

### Safety Booleans

All false by design in v1 — no agent has run, no patch applied, no dispatch, no Hermes touched.

| Field | Always |
|---|---|
| `agent_executed` | `false` |
| `patch_applied` | `false` |
| `dispatch_occurred` | `false` |
| `hermes_touched` | `false` |
| `production_board_touched` | `false` |
| `pr_created` | `false` |
| `import_performed` | `false` |
| `dry_run` | `true` |

### Task Status Values

| Status | Meaning |
|---|---|
| `planned` | Task defined in manifest, no bundle generated yet |
| `bundled` | Bundle scaffold created for this task |
| `reviewed` | Bundle reviewed, promotion decision pending |
| `promoted` | Task promoted to a branch/PR |
| `skipped` | Task intentionally skipped |

### Promotion Recommendation Values

| Value | Meaning |
|---|---|
| `not_evaluated` | v1 — no evaluation performed |
| `promote` | Bundle reviewed, recommend creating PR |
| `skip` | Bundle reviewed, recommend skipping |
| `blocked` | Bundle reviewed, blocked by safety/scope |

## Morning Review Relationship

The `BUNDLE_INDEX.json` is the morning review artifact. Reviewer workflow:

1. Open `BUNDLE_INDEX.json`
2. For each task entry with `status: planned`:
   - Check `allowed_files` and `forbidden_files` scope
   - Check `expected_outputs` for plausibility
   - Check `risk_level` for comfort
3. For tasks to proceed: run future-phase bundle generator per task
4. After bundle review: update `status`, `promotion_recommendation`, `process_score_status`

## v1 Constraints

**What v1 does:**
- Validates `TASKS.jsonl` format and field constraints
- Checks for duplicate `task_id`
- Validates `task_type` and `risk_level` enums
- Validates 40-char hex `base_sha`
- Produces `BUNDLE_INDEX.json` with safety booleans
- Stores `status: planned` and `promotion_recommendation: not_evaluated` for each task

**What v1 does NOT do:**
- Execute agents
- Generate patches
- Create bundles (future phase)
- Create PRs
- Call Hermes
- Dispatch Kanban tasks
- Send Telegram messages
- Update memory or skills

## Future Phases

| Phase | Description |
|---|---|
| Phase 2 | Run `run_quarantine_autocoder_dry_run.py` per task in index to generate bundles |
| Phase 3 | Morning review: evaluate bundles, update `status` and `promotion_recommendation` |
| Phase 4 | Promote bundles to branches, create PRs via `verify_final_head_merge_command.py` |

## Validation Rules

`build_quarantine_bundle_index.py` enforces:

1. `--dry-run` is required (refuses without it)
2. `task_id` must be unique across the manifest
3. `task_id` must be a safe slug (no `/`, `\`, `..`, `;`, spaces)
4. `objective` must be non-empty
5. `allowed_files` must be non-empty
6. `forbidden_files` must be present (may be empty list)
7. `expected_outputs` must be non-empty
8. `task_type` must be a known enum value
9. `risk_level` must be a known enum value
10. `base_sha` (index-level or per-task) must be a valid 40-char hex string
11. No duplicate `task_id` entries

## Example Usage

```bash
python3 scripts/local/build_quarantine_bundle_index.py \
  --tasks-jsonl /path/to/TASKS.jsonl \
  --bundle-root /path/to/bundles \
  --repo Slideshow11/Automated-Edge-Discovery \
  --base-sha ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0 \
  --output-index /tmp/BUNDLE_INDEX.json \
  --dry-run
```