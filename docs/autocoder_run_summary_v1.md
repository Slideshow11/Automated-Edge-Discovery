# Autocoder Run Summary v1

**Status:** implemented
**Version:** 1
**Purpose:** Machine-readable and human-readable run-level aggregation for AED quarantine autocoder sessions.

---

## What It Is

The run summary is a **post-run aggregator** — not a task bundle, not an audit log, not a PR creator.

It reads the outputs of completed (or partially completed) task bundles and produces:

1. `RUN_SUMMARY.json` — machine-readable, structured summary
2. `RUN_SUMMARY.md` — human-readable, Telegram-friendly Markdown

It is **read-only**: no repo mutation, no git writes, no PR creation, no audit append, no Hermes calls, no dispatch, no production board mutation.

---

## CLI

```bash
python3 scripts/local/build_autocoder_run_summary.py \
  --run-id aed-run-2026-05-17-001 \
  --bundle-index /path/to/BUNDLE_INDEX.json \
  --bundle-root /path/to/bundles \
  --output-json /path/to/RUN_SUMMARY.json \
  --output-md /path/to/RUN_SUMMARY.md \
  [--repo /home/max/Automated-Edge-Discovery] \
  [--base-sha <sha>] \
  [--integration-branch integration/aed-run-2026-05-17-001] \
  [--expected-tasks-json '["task1","task2"]'] \
  [--allow-missing-bundles] \
  [--strict]
```

**Required flags:**
- `--run-id TEXT` — unique run identifier
- `--bundle-index PATH` — path to `BUNDLE_INDEX.json`
- `--bundle-root PATH` — root directory containing per-task bundle subdirectories
- `--output-json PATH` — path for JSON summary output
- `--output-md PATH` — path for Markdown summary output

**Optional flags:**
- `--repo PATH` — repo name or path (default: none)
- `--base-sha SHA` — base commit SHA (default: none)
- `--integration-branch TEXT` — integration branch name (default: none)
- `--expected-tasks-json JSON` — JSON array of expected task IDs (default: none)
- `--allow-missing-bundles` — missing bundle directories are warnings, not errors (default: False)
- `--strict` — missing optional bundle files are errors, not warnings (default: False)

**Exit codes:**
- `0` — summary produced successfully
- `1` — validation error or missing required argument
- `2` — hard safety failure (hermes_touched/dispatch_occurred/production_board_touched = true)

---

## Input Artifacts Read

The tool reads from the `bundle_root` directory, looking for per-task subdirectories matching the `task_id` values in `BUNDLE_INDEX.json`.

**Required input:**
- `BUNDLE_INDEX.json` — the bundle index produced by `build_quarantine_bundle_index.py`

**Per-task optional files read (all non-required):**

| File | Purpose |
|------|---------|
| `BUNDLE_STATUS.json` | Task status, safety booleans, changed files, promotion status |
| `scope_check.json` | Scope validation result |
| `violations_only.json` | Allowed scope violations (if any) |
| `local_gate.txt` | Local gate result (PASS/FAIL content) |
| `risk_notes.md` | Human risk notes |
| `proposed_pr_body.md` | Proposed PR body text |
| `FINAL_GATE.json` | Finalization guard output |
| `codex_review_summary.md` | Codex review summary |

Missing optional files produce **warnings**, not errors (unless `--strict` is set).

---

## JSON Output Schema

```json
{
  "summary_version": 1,
  "run_id": "aed-run-2026-05-17-001",
  "generated_at": "2026-05-17T02:00:00Z",
  "repo": "/home/max/Automated-Edge-Discovery",
  "base_sha": "51eb88ac7c6602774e2e522120515a943d14409c",
  "integration_branch": "integration/aed-run-2026-05-17-001",
  "bundle_index_path": "/path/to/BUNDLE_INDEX.json",
  "bundle_root": "/path/to/bundles",
  "task_count": 5,
  "tasks_attempted": 5,
  "tasks_ready": 3,
  "tasks_blocked": 1,
  "tasks_skipped": 1,
  "tasks_promoted": 0,
  "prs_opened": 0,
  "merge_ready_prs": 0,
  "human_action_required": true,
  "overall_status": "PARTIAL_READY",
  "safety_invariants": {
    "hermes_touched": false,
    "dispatch_occurred": false,
    "production_board_touched": false,
    "memory_or_profile_updated": false,
    "skills_created": false
  },
  "gate_summary": {
    "local_gate_passed": 3,
    "local_gate_failed": 1,
    "codex_clean": 0,
    "ci_green": 0,
    "finalization_guard_merge_ready": 0
  },
  "tasks": [
    {
      "task_id": "docs-example-001",
      "task_type": "docs_consistency",
      "risk_level": "low",
      "status": "TASK_READY",
      "promotion_status": "not_promoted",
      "bundle_path": "/path/to/bundles/docs-example-001",
      "clean_for_task": true,
      "allowed_scope_violations_count": 0,
      "scope_status": "clean",
      "local_gate_status": "passed",
      "codex_status": "not_run",
      "ci_status": "not_applicable",
      "finalization_status": "not_applicable",
      "changed_files_count": 0,
      "expected_outputs_present": false,
      "blocker_code": null,
      "blocker_summary": null,
      "human_action": "authorize_merge"
    }
  ],
  "blockers": [],
  "warnings": [
    {
      "task_id": "docs-example-001",
      "code": "bundle_file_warning",
      "message": "risk_notes.md: File not found"
    }
  ],
  "artifact_index": {
    "json_report": "/path/to/RUN_SUMMARY.json",
    "markdown_report": "/path/to/RUN_SUMMARY.md"
  }
}
```

---

## Status Enums

### Overall Status

| Value | Meaning |
|-------|---------|
| `RUN_READY` | All attempted tasks are TASK_READY; no blockers |
| `PARTIAL_READY` | Some tasks ready, some blocked; human action needed |
| `BLOCKED` | All attempted tasks are blocked; no tasks ready |
| `FAILED_VALIDATION` | Bundle index or bundle contents failed validation |
| `NO_TASKS` | Bundle index has zero tasks |
| `INVALID_INPUT` | Bundle index missing or unreadable |

### Task Status

| Value | Meaning |
|-------|---------|
| `TASK_READY` | Task completed cleanly; ready for promotion |
| `TASK_BLOCKED` | Task had violations or blockers; not ready |
| `TASK_SKIPPED` | Task was skipped (not attempted) |
| `TASK_FAILED_VALIDATION` | Task bundle had malformed files in strict mode |
| `TASK_NOT_EVALUATED` | Bundle directory missing or status unavailable |

### Promotion Status

| Value | Meaning |
|-------|---------|
| `not_promoted` | Task completed but not yet promoted |
| `promoted_to_integration` | Task changes merged to integration branch |
| `blocked_from_promotion` | Task ready but promotion blocked |
| `not_applicable` | Task was skipped or not evaluated |

### Human Action

| Value | Meaning |
|-------|---------|
| `none` | No human action needed |
| `review_report` | Review the run summary report |
| `authorize_merge` | Authorize merge for merge-ready PRs |
| `resolve_blocker` | Resolve blockers in blocked tasks |
| `inspect_ci` | Inspect CI failures |
| `rerun_required` | Task needs to be re-run |

---

## Safety Invariants

### Hard-Fail Booleans (exit code 2)

If **any** task bundle has the following booleans set to `true`, the tool **aborts** with exit code 2 and prints a hard safety failure message:

- `hermes_touched: true` — Hermes create/dispatch occurred
- `dispatch_occurred: true` — Worker dispatch occurred
- `production_board_touched: true` — Production Kanban board was modified

### Report-Only Booleans (warning, no exit code)

The following are surfaced as warnings but do not cause hard failure (future modes may allow them):

- `pr_created: true` — A PR was opened
- `import_performed: true` — An external import occurred
- `patch_applied: true` — A patch was applied to the repo

---

## Validation Rules

1. `BUNDLE_INDEX.json` must exist and be a valid JSON object with `tasks` as a list
2. Bundle root directory must exist
3. Bundle subdirectory paths must not escape the bundle root (path traversal check)
4. Task IDs must be unique within the bundle index
5. Missing bundle directories: warning in non-strict mode, error in strict mode
6. Malformed JSON in optional bundle files: warning in non-strict, error in strict
7. Missing `BUNDLE_STATUS.json` in bundle: warning in non-strict, error in strict
8. Expected task IDs from `--expected-tasks-json` must all appear in the bundle index
9. All safety booleans in `BUNDLE_STATUS.json` are checked; hard-fail booleans cause exit code 2

---

## Markdown Output Structure

```
# AED Autocoder Run Summary

**Run ID:** `aed-run-2026-05-17-001`
**Overall Status:** `PARTIAL_READY`
**Generated:** 2026-05-17T02:00:00Z
**Repo:** /home/max/Automated-Edge-Discovery
**Base SHA:** 51eb88ac...
**Integration Branch:** integration/aed-run-2026-05-17-001

---

## Task Counts

| Metric | Count |
|--------|-------|
| Tasks in index | 5 |
| Tasks attempted | 5 |
| TASK_READY | 3 |
| TASK_BLOCKED | 1 |
| TASK_SKIPPED | 1 |
| ...

## Safety Invariants

| Boolean | Value |
|---------|-------|
| `hermes_touched` | ✅ false |
| `dispatch_occurred` | ✅ false |
| ...

## Gate Summary

| Gate | Count |
|------|-------|
| Local gate passed | 3 |
| ...

## Task Table

| Task ID | Type | Risk | Status | Promotion | Scope | Local Gate | Codex | CI | Blocker |
|---------|------|------|--------|-----------|-------|------------|-------|-----|--------|
| `docs-001` | docs_consistency | low | TASK_READY | not_promoted | clean | passed | not_run | not_applicable | — |
| ...

## Blockers

- [blocked-task-001]: Modified files outside allowed scope

## Warnings

- [task-002]: scope_check.json: File not found

## Recommended Next Action

**Action:** `resolve_blocker`
**Human intervention required:** yes

Blocked tasks: `blocked-task-001`. Resolve blockers before proceeding.

---

## Artifact Index

- **JSON report:** `/path/to/RUN_SUMMARY.json`
- **Markdown report:** `/path/to/RUN_SUMMARY.md`
```

---

## Relationship to Other Tools

| Tool | Purpose |
|------|---------|
| `build_quarantine_bundle_index.py` | Creates task bundles from TASKS.jsonl (dry-run scaffold) |
| `build_autocoder_run_summary.py` | Aggregates completed bundles into run summary |
| `append_merge_action_audit.py` | Appends audit log entries (after real PRs are merged) |
| `validate_merge_action_audit_log.py` | Validates audit log JSONL consistency |

The run summary does **not** append to the audit log. It is a read-only report tool that complements the audit log by providing a run-level view of what happened during an autocoder session.

---

## Future Enhancements (v2 backlog)

- `--output-jira` flag to produce Jira-format blocker summary
- Integration with `gh pr list` to cross-reference PR numbers in bundle status
- `violations_only.json` schema validation against quarantine task manifest schema
- `--summarize-from-git-log` mode to reconstruct run from git history
- Prometheus metrics export for monitoring dashboards