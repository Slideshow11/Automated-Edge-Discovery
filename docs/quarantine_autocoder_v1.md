# Quarantine Autocoder v1

> **Status**: Phase 2 (dry-run read-only trace collection).
> Phase 1 produced placeholder bundles. Phase 2 adds real read-only evidence collection.
> No agent execution, no patch application, no Hermes touch, no dispatch in any phase.

## Overview

The Quarantine Autocoder is a multi-phase tool for safely scaffolding candidate PRs in an isolated bundle directory, prior to any agent execution, Hermes touch, or Kanban dispatch.

**Phase 1 (dry-run only, placeholder):** Produces a bundle scaffold — all files are placeholders.

**Phase 2 (dry-run only, read-only traces):** Adds real read-only evidence collection via optional `--collect-*` flags. All git operations are read-only. No patch, no agent, no Hermes, no dispatch.

**Phase 3+ (future):** Real agent execution, patch application, PR creation and merge.

## Usage

### Phase 1 — Placeholder Bundle

```bash
python scripts/local/run_quarantine_autocoder_dry_run.py \
  --source-repo /path/to/repo \
  --bundle-dir /tmp/candidate-bundle \
  --base-sha 367ecdb1fab8a18dfef3dd7529c701492277c4f7 \
  --candidate-id candidate-001 \
  --objective "Fix nil pointer in scope checker" \
  --dry-run
```

### Phase 2 — Read-Only Trace Collection

```bash
python scripts/local/run_quarantine_autocoder_dry_run.py \
  --source-repo /path/to/repo \
  --bundle-dir /tmp/candidate-bundle \
  --base-sha 367ecdb1fab8a18dfef3dd7529c701492277c4f7 \
  --candidate-id candidate-001 \
  --objective "Fix nil pointer in scope checker" \
  --dry-run \
  --collect-scope \
  --collect-safety-grep \
  --collect-local-gate-preview \
  --collect-git-diff
```

### Collection Flags

| Flag | Effect |
|------|--------|
| `--collect-scope` | Run read-only git scope check: `git diff --name-only`, `git rev-parse HEAD`. Populates `scope_check.json`. |
| `--collect-safety-grep` | Scan `.py` files for forbidden mutation commands. Populates `safety_grep.txt`. |
| `--collect-local-gate-preview` | List local gate commands without executing them. Populates `local_gate.txt`. |
| `--collect-git-diff` | Run `git diff <base-sha>..HEAD`. Populates `diff.patch` and `changed_files.txt`. |

Without any `--collect-*` flags, Phase 2 produces the same placeholder bundle as Phase 1.

## Bundle Format

| File | Phase 1 | Phase 2 (with --collect-*) |
|------|---------|---------------------------|
| `BUNDLE_STATUS.json` | Safety booleans (Phase 1) | Safety booleans (Phase 2) + `read_only_collections` |
| `base_sha.txt` | Base SHA | Base SHA |
| `candidate_id.txt` | Candidate ID | Candidate ID |
| `objective.md` | Objective | Objective |
| `changed_files.txt` | Placeholder | Real `git diff --name-only` output (with `--collect-git-diff`) |
| `diff.patch` | Placeholder | Real `git diff <base>..HEAD` output (with `--collect-git-diff`) |
| `scope_check.json` | Placeholder | Real scope check with file count + list + HEAD (with `--collect-scope`) |
| `safety_grep.txt` | Placeholder | Scan results: patterns checked, files scanned, executable vs policy matches (with `--collect-safety-grep`) |
| `local_gate.txt` | Placeholder | Preview of commands that would run — NOT executed in Phase 2 (with `--collect-local-gate-preview`) |
| `codex_review_summary.md` | Placeholder | Placeholder (Codex not run in Phase 2) |
| `risk_notes.md` | Phase 1 disclaimer | Phase 2 disclaimer + which collectors ran |
| `proposed_pr_body.md` | Phase 1 scaffold | Phase 2 scaffold |
| `import_command.sh` | Non-executable, commented | Non-executable, commented |

## Phase 2 Bundle Status (`BUNDLE_STATUS.json`)

```json
{
  "phase": "Phase 2",
  "dry_run": true,
  "agent_executed": false,
  "patch_applied": false,
  "dispatch_occurred": false,
  "hermes_touched": false,
  "production_board_touched": false,
  "pr_created": false,
  "import_performed": false,
  "read_only_collections": {
    "collect_scope": false,
    "collect_safety_grep": true,
    "collect_local_gate_preview": false,
    "collect_git_diff": true
  }
}
```

## Phase 2 `scope_check.json` (with `--collect-scope`)

```json
{
  "source_repo": "/path/to/repo",
  "bundle_dir": "/tmp/candidate-bundle",
  "base_sha": "367ecdb1...",
  "current_head": "abc123...",
  "files_changed_count": 5,
  "changed_files": ["file_a.py", "file_b.py"],
  "bundle_dir_outside_repo_root": false,
  "bundle_dir_inside_git": false,
  "scope_clean": true
}
```

## Phase 2 `safety_grep.txt` (with `--collect-safety-grep`)

Distinguishes policy mentions (comments/docstrings) from executable usage:

```json
{
  "patterns_checked": ["hermes kanban create", "gh pr merge", ...],
  "files_scanned": 42,
  "forbidden_executable_matches": {
    "scripts/bad.py": [
      {"pattern": "gh pr merge", "line": 10, "text": "os.system(\"gh pr merge\")"}
    ]
  },
  "forbidden_policy_mentions": {
    "docs/policy.md": [
      {"pattern": "hermes kanban create", "line": 5, "text": "# hermes kanban create is forbidden"}
    ]
  },
  "total_executable_matches": 1,
  "total_policy_mentions": 1,
  "clean": false
}
```

Policy mentions (lines starting with `#` or docstrings) are recorded separately from executable matches.

## Phase 2 `local_gate.txt` (with `--collect-local-gate-preview`)

```json
{
  "phase": "Phase 2 (read-only preview — no execution)",
  "note": "Phase 2 does NOT execute pytest, compileall, ...",
  "preview_commands": [
    {
      "command": "python3 -m compileall engine scripts",
      "purpose": "Syntax/compile check",
      "executed_in_phase2": false
    },
    {
      "command": "PYTHONPATH=. python3 -m pytest tests/test_run_quarantine_autocoder_dry_run.py -q",
      "purpose": "Quarantine autocoder unit tests",
      "executed_in_phase2": false
    }
  ],
  "local_gate_passed": null,
  "compiles": null,
  "tests_pass": null
}
```

All `executed_in_phase2` values are `false`. No pytest, compileall, or governance scripts are run in Phase 2.

## Safety Invariants

All phases enforce:

| Rule | Behavior |
|------|----------|
| `--dry-run` required | Refuses to run without this flag |
| `base_sha` must be 40-char hex | Rejects invalid SHA format |
| `candidate_id` must be safe slug | Rejects slashes, spaces, special chars |
| `source_repo` cannot be `/` | Rejects filesystem root |
| `bundle_dir` cannot be inside `.git` | Rejects `.git` directory (resolved symlinks included) |
| `bundle_dir` cannot be repo root | Rejects using AED repo root |
| `--force` cleans stale files | Removes all entries in bundle dir before writing |
| Repeated run requires `--force` | Rejects overwriting existing bundle without flag |

## Safety Constraints — Forbidden Commands

`import_command.sh` is:
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

## What Phase 2 Does NOT Do

Phase 2 does NOT:
- Execute any agent
- Apply any patch
- Run pytest, compileall, or governance validators (preview only)
- Touch Hermes
- Dispatch any Kanban task
- Create any PR
- Perform any import
- Execute any GitHub mutation commands

All git operations (`git diff`, `git status`, `git rev-parse`) are read-only.

## Relationship to Other AED Components

- This tool does NOT use the audit appender.
- This tool does NOT create Kanban tasks.
- This tool does NOT touch the production `aed` board.
- This tool does NOT write to the ledger.
- This tool is designed to be called from a supervised wrapper in later phases.
- Phase 2 does NOT run Codex — that is a future Phase 3+ item.

## Implementation Notes

- Uses standard library only (no external dependencies beyond Python 3.10+).
- All validation happens before any filesystem write.
- `BUNDLE_STATUS.json` is written first as a consistency check.
- The tool returns exit code 0 on success, non-zero on any validation failure.
- Phase 2 collection flags default to `False` — no read-only operations run unless explicitly enabled.