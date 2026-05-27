# Codex Remediation Loop — Design

## Purpose

The Codex Remediation Loop is a guarded, read-only task processor that reads a
structured corpus of Codex review findings and produces task packets (mock v0)
or executes remediations (future full mode). It never runs live Claude,
never invokes the autocoder batch controller, and never merges anything.

v0 is **mock-plan-only**: it reads a corpus, validates every safety constraint,
classifies each task, writes task packets and loop status files under a
user-specified output root, and exits.

---

## CLI Interface

```bash
python3 scripts/local/run_codex_remediation_loop.py \
  --corpus corpus/codex-remediation-pr314-320.json \
  --output-root /tmp/aed_runs/codex-remediation-wave1 \
  --mode mock-plan-only
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--corpus` | Yes | Path to corpus JSON file |
| `--output-root` | Yes | Directory for task packets and loop status |
| `--mode` | Yes | Execution mode (v0: only `mock-plan-only`) |
| `--repo-root` | No | Override repo root (default: auto-detected) |

---

## Output Schema

### `output_root/loop_status.json`

```json
{
  "loop_status_kind": "aed.codex_remediation.loop_status.v0",
  "loop_runner_version": "0.1.0",
  "corpus_id": "codex-remediation-pr314-320",
  "corpus_version": "0.1.0",
  "mode": "mock-plan-only",
  "base_sha_policy": "current_main",
  "status": "LOOP_COMPLETE_MOCK_PLAN_ONLY",
  "total_tasks": 7,
  "tasks_passed": 7,
  "tasks_failed": 0,
  "classifications": {
    "needs_regression_test": 3,
    "false_positive_has_evidence": 1,
    "docs_fixed_has_evidence": 2,
    "needs_human_review": 1
  },
  "stop_conditions": [ ... ],
  "hard_stops": [ ... ],
  "generated_at": "2026-05-26T00:00:00Z"
}
```

### `output_root/tasks/<task_id>/task_packet.json`

```json
{
  "packet_kind": "aed.codex_remediation.task_packet.v0",
  "loop_runner_version": "0.1.0",
  "task_id": "rgr-314-task-id-path-traversal",
  "wave": 1,
  "source_pr": 314,
  "finding_id": "codex-f23c1e3c82d9",
  "severity": "P1",
  "classification": "needs_regression_test",
  "task_category": "already_fixed_needs_regression_test",
  "action_type": "add_regression_test",
  "target_file": "tests/test_run_autocoder_batch.py",
  "allowed_files": [ ... ],
  "forbidden_files": [ ... ],
  "safety_notes": [ ... ],
  "success_criteria": "Test exists and PASSES ...",
  "deliverable": "New test function ...",
  "finding_summary": "task_id used directly ...",
  "current_main_status": "Fixed in commit e60e3b5 ...",
  "generated_at": "2026-05-26T00:00:00Z"
}
```

Additional files per task:
- `classification_reason.txt` — human-readable explanation of the classification
- `safety_notes_verified.txt` — empty marker confirming safety_notes were validated

---

## Task Classifications

| Classification | Meaning |
|---|---|
| `needs_regression_test` | `already_fixed_needs_regression_test` — test expected, packet emitted for human review |
| `false_positive_has_evidence` | `false_positive_with_evidence` — no code change needed |
| `docs_fixed_has_evidence` | `docs_only_fixed` — governance gap was already fixed |
| `needs_human_review` | Unknown/unclassified — human must decide |

---

## Hard Stops (v0 — enforced, trigger immediate exit)

Any of the following causes the loop to exit with code 1 before processing any task:

1. `corpus_kind` is not `aed.codex_remediation.corpus.v0`
2. `execution_mode` is not `mocked` for the wave being processed
3. `task_id` contains `/`, `\`, or `..` (path traversal risk)
4. `allowed_file` is an absolute path (must be relative)
5. `allowed_file` does not exist at current main HEAD and is not declared as `new_file`
6. Any `forbidden_file` has changed vs current main HEAD
7. `safety_notes` contains any forbidden pattern:
   - `live_claude`, `--enable-real-claude-executor`
   - `gh pr merge`, `git merge`, `git push`, `git commit`, `git add`
   - `fact_store`, `memory_store`, `skill_manage`, `delegate_task`, `_run_subagent`
   - `resolveReviewThread`, `deleteReview`, `dismissReview`
   - `shell=True`
8. `--mode` is not `mock-plan-only`
9. `--output-root` is null or empty

---

## Stop Conditions (documented, not enforced in v0)

These are the conditions that would block execution in future full mode.
They are documented in `loop_status.json` / `loop_status.md` for transparency.

1. **current-head P0/P1/P2 review finding** — Codex has an active blocking finding on the current HEAD
2. **unresolved stale P0/P1/P2** — old findings from prior commits remain unresolved
3. **REVIEW_COMMENTS_BLOCKED** — `check_pr_review_comments.py` returns BLOCKED
4. **REVIEW_COMMENTS_INCONCLUSIVE** — cannot determine status
5. **CI not green** — GitHub Actions workflow not passing
6. **PMG dirty** — Hermes persistent state was mutated
7. **final_gate_status.py not READY_TO_MERGE** — any pre-merge gate is not green
8. **verify_final_head_merge_command.py not MERGE_READY_CANDIDATE** — merge command structure invalid
9. **changed files outside allowed_files** — task modified files not in its allowed list
10. **any request to resolve GitHub threads** — task attempts to resolve/mutate review threads
11. **any live Claude flag unless explicitly authorized** — task tries to enable live Claude
12. **any Hermes memory/profile/config mutation attempt** — task tries to write to Hermes

---

## Safety Invariants (never violated)

- No live Claude invocation
- No `--enable-real-claude-executor`
- No `shell=True`
- No GitHub API mutation calls (no `gh pr merge`, review thread mutation, etc.)
- No Hermes memory/profile/config writes
- No `git push`, `git merge`, `git commit`, `git add` from the runner
- No invocation of `run_autocoder_batch.py`, `run_autocoder_single_task.py`, or `run_autocoder_eval_corpus.py`
- All output written only under `output_root` (no repo file mutations)

---

## Corpus Schema (aed.codex_remediation.corpus.v0)

```json
{
  "corpus_kind": "aed.codex_remediation.corpus.v0",
  "corpus_version": "0.1.0",
  "corpus_id": "codex-remediation-pr314-320",
  "description": "...",
  "source_audit_doc": "docs/codex_note_retrospective_audit_pr314_320.md",
  "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
  "base_sha_policy": "current_main",
  "wave_definitions": {
    "1": {
      "description": "...",
      "task_ids": ["task-001", "task-002"],
      "execution_mode": "mocked"
    }
  },
  "tasks": [
    {
      "task_id": "task-001",
      "wave": 1,
      "source_pr": 314,
      "finding_id": "codex-xxx",
      "severity": "P1",
      "classification": "FIXED_ALREADY",
      "finding_summary": "...",
      "current_main_status": "Fixed in commit xxx",
      "task_category": "already_fixed_needs_regression_test",
      "action": {
        "type": "add_regression_test",
        "target_file": "tests/test_xxx.py",
        "allowed_files": ["tests/test_xxx.py"],
        "forbidden_files": [".hermes/**", "skills/**"],
        "permitted_new_files": [],
        "test_type": "unit",
        "test_pattern": "test_xxx",
        "success_criteria": "...",
        "deliverable": "..."
      },
      "safety_notes": [
        "No live Claude execution",
        "No Hermes mutation",
        "No git push/merge"
      ]
    }
  ]
}
```

---

## Future Modes (not implemented in v0)

### `live-plan-only`
- Reads corpus, validates all safety constraints, generates task packets, but does NOT execute fixes
- Produces the same outputs as v0
- Same hard stops as v0

### `live-execute`
- Executes remediations task by task
- Enforces all stop conditions before each task
- Pauses on HOLD states and waits for human resolution
- Does NOT auto-merge

### `live-full`
- Full autonomous loop: execute + PR creation + merge on READY_TO_MERGE
- Requires all stop conditions green before merge
- Requires explicit `--allow-live-mode` flag
- Requires PMG clean pre- and post-merge snapshot comparison

---

## Test Plan

| Test | Description |
|---|---|
| `test_valid_wave1_corpus_creates_task_packets_and_status` | Full valid corpus produces all output files with correct schema |
| `test_unsafe_task_id_with_path_traversal_rejected` | `../` in task_id → exit 1 |
| `test_absolute_allowed_file_rejected` | `/etc/passwd` in allowed_files → exit 1 |
| `test_missing_allowed_file_rejected_unless_declared_new` | File not on main not declared new → exit 1 |
| `test_mock_plan_only_does_not_modify_repo_files` | Git status unchanged after run |
| `test_output_root_null_rejected` | Empty output-root → exit 1 |
| `test_no_controller_subprocess_invoked_in_mock_mode` | No batch controller spawned |
| `test_no_shell_true_in_source` | `shell=True` absent from source |
| `test_stop_conditions_documented_in_md` | Stop conditions appear in `loop_status.md` |
| `test_status_json_includes_task_counts_and_classification` | JSON has correct counts and classifications |
| `test_forbidden_pattern_in_safety_notes_rejected` | `--enable-real-claude-executor` in safety_notes → exit 1 |
| `test_unsupported_mode_rejected` | `--mode live` → exit 1 |
| `test_invalid_corpus_kind_rejected` | Wrong corpus_kind → exit 1 |
| `test_task_classification_false_positive` | `false_positive_with_evidence` → `false_positive_has_evidence` |
| `test_task_classification_docs_fixed` | `docs_only_fixed` → `docs_fixed_has_evidence` |
