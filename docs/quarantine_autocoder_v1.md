# Quarantine Autocoder v1 — Phase 1 (Dry-Run Only)

> **Status**: Phase 1 dry-run implementation. No real operations are performed.

## Overview

The Quarantine Autocoder is a multi-phase tool for safely scaffolding candidate PRs in an isolated bundle directory, prior to any agent execution, Hermes touch, or Kanban dispatch.

**Phase 1 is dry-run only.** It produces a bundle scaffold — placeholders for all files that would be generated in later phases. It does not:
- Apply any patch
- Execute any agent
- Touch Hermes
- Dispatch any Kanban task
- Create any PR
- Perform any import

## Bundle Format

When run with `--dry-run`, the tool creates a bundle directory containing:

| File | Description |
|------|-------------|
| `BUNDLE_STATUS.json` | Safety invariants and phase marker |
| `base_sha.txt` | 40-char hex commit SHA provided as `--base-sha` |
| `candidate_id.txt` | Safe slug identifier provided as `--candidate-id` |
| `objective.md` | Objective description provided as `--objective` |
| `changed_files.txt` | Placeholder — no git diff run in Phase 1 |
| `diff.patch` | Placeholder — no diff computed in Phase 1 |
| `scope_check.json` | Placeholder — no git log run in Phase 1 |
| `safety_grep.txt` | Placeholder — no filesystem scan in Phase 1 |
| `local_gate.txt` | Placeholder — no compileall/pytest in Phase 1 |
| `codex_review_summary.md` | Placeholder — no Codex run in Phase 1 |
| `risk_notes.md` | Phase 1 disclaimer and metadata |
| `proposed_pr_body.md` | Phase 1 PR body scaffold (placeholder) |
| `import_command.sh` | Non-executable commented instructions only |

## Safety Invariants

All Phase 1 bundles must have these values in `BUNDLE_STATUS.json`:

```json
{
  "dry_run": true,
  "dispatch_occurred": false,
  "hermes_touched": false,
  "production_board_touched": false,
  "pr_created": false,
  "import_performed": false
}
```

## Validation Rules

Phase 1 enforces the following constraints:

| Rule | Behavior |
|------|----------|
| `--dry-run` required | Refuses to run without this flag |
| `base_sha` must be 40-char hex | Rejects invalid SHA format |
| `candidate_id` must be safe slug | Rejects slashes, spaces, special chars |
| `source_repo` cannot be `/` | Rejects filesystem root |
| `bundle_dir` cannot be inside `.git` | Rejects `.git` directory |
| `bundle_dir` cannot be repo root | Rejects using AED repo root |
| Repeated run requires `--force` | Rejects overwriting existing bundle without flag |

## Usage

```bash
python scripts/local/run_quarantine_autocoder_dry_run.py \
  --source-repo /path/to/repo \
  --bundle-dir /tmp/candidate-bundle \
  --base-sha 367ecdb1fab8a18dfef3dd7529c701492277c4f7 \
  --candidate-id candidate-001 \
  --objective "Fix nil pointer in scope checker" \
  --dry-run
```

## Phase Roadmap

- **Phase 1** (this implementation): Dry-run bundle scaffold. No-op. All files are placeholders.
- **Phase 2** (future): Real scope check, safety grep, local gate execution, Codex review.
- **Phase 3** (future): Real agent execution, patch application, PR creation and merge.

## Safety Constraints

Phase 1 `import_command.sh` is:
- Non-executable by default (mode 0o644)
- Contains only commented instructions
- Contains NO executable calls to:
  - `hermes kanban create` / `hermes kanban dispatch`
  - `gh pr create` / `gh pr merge`
  - `git push` / `git commit`
  - `telegram`, `send_message`
  - `memory.update`, `skill_manage`, `fact_store`
  - `delegate_task`, `cronjob`

These strings may appear in documentation and tests as **forbidden examples**, never as executable behavior.

## Relationship to Other AED Components

- This tool does NOT use the audit appender.
- This tool does NOT create Kanban tasks.
- This tool does NOT touch the production `aed` board.
- This tool does NOT write to the ledger.
- This tool is designed to be called from a supervised wrapper in later phases.

## Implementation Notes

- Uses standard library only (no external dependencies beyond Python 3.10+).
- All validation happens before any filesystem write.
- `BUNDLE_STATUS.json` is written first as a consistency check.
- The tool returns exit code 0 on success, non-zero on any validation failure.