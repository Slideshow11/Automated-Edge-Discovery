# Codex Remediation Backlog Scan — After Wave 3 Closeout

**Scan date:** 2026-05-28T08:58:42-04:00
**main HEAD:** 988826869694b738d9196ffd14b6fd58e1bd73f5
**Method:** Terminal-only bounded commands (git grep, python3 JSON, sed). No search_files, no read_file.

---

## 1. Closed Candidates Confirmed

All Wave 1, Wave 2, and Wave 3 candidates are closed:

| Candidate | Classification | PR / Commit |
|---|---|---|
| `rgr-314-stop-on-first-hold-bool` | CLOSED_FIXED_WITH_TEST | PR #343 (`b87d91b`) |
| `rgr-314-task-id-path-traversal` | CLOSED_ALREADY_FIXED_WITH_TEST | PR #344 (`d47e6ca`) |
| `rgr-320-batch-ok-subprocess-rc` | CLOSED_ALREADY_FIXED_WITH_TEST | PR #345 (`529a443`) |
| `rgr-320-no-newline-marker` | CLOSED_FALSE_POSITIVE_WITH_EVIDENCE | Wave 2 closeout |
| `rgr-319-output-root-null-normalization` | CLOSED_FIXED_WITH_TEST | PR #334 |
| `rgr-317-repo-root-propagation` | CLOSED_ALREADY_FIXED_WITH_TEST | PR #338 |
| `rgr-320-base-sha-catfile` | CLOSED_ALREADY_FIXED_WITH_TEST | Wave 3 docs (existing test) |
| `doc-323-applied-status-name` | CLOSED_DOCS_ONLY_FIXED | PR #323 |
| `doc-323-enable-real-claude-executor-claim` | CLOSED_DOCS_ONLY_FIXED | PR #323 |

Wave 3 closeout PR (#346) merged at `9888268`.

---

## 2. Full Candidate Status Table

| Candidate | Status | Source file |
|---|---|---|
| `doc-323-applied-status-name` | CLOSED_DOCS_ONLY_FIXED (PR #323) | corpus/codex-remediation-pr314-320.json |
| `doc-323-enable-real-claude-executor-claim` | CLOSED_DOCS_ONLY_FIXED (PR #323) | corpus/codex-remediation-pr314-320.json |
| `rgr-314-stop-on-first-hold-bool` | CLOSED_FIXED_WITH_TEST via PR #343 | corpus/codex-remediation-pr314-320.json |
| `rgr-314-task-id-path-traversal` | CLOSED_ALREADY_FIXED_WITH_TEST via PR #344 | corpus/codex-remediation-pr314-320.json |
| `rgr-317-repo-root-propagation` | CLOSED_ALREADY_FIXED_WITH_TEST via PR #338 | corpus/codex-remediation-wave2-pr314-320.json |
| `rgr-319-output-root-null-normalization` | CLOSED_FIXED_WITH_TEST via PR #334 | corpus/codex-remediation-wave2-pr314-320.json |
| `rgr-320-batch-ok-subprocess-rc` | CLOSED_ALREADY_FIXED_WITH_TEST via PR #345 | corpus/codex-remediation-pr314-320.json |
| `rgr-320-base-sha-catfile` | CLOSED_ALREADY_FIXED_WITH_TEST (existing test) | corpus/codex-remediation-pr314-320.json |
| `rgr-320-no-newline-marker` | CLOSED_FALSE_POSITIVE_WITH_EVIDENCE (Wave 2) | corpus/codex-remediation-pr314-320.json |

**All 9 candidates from the two corpus files are closed. Zero NEEDS_TRIAGE candidates remain.**

---

## 3. rgr-320-base-sha-catfile — Corrected Status

> **Note:** This candidate was initially flagged as `NEEDS_TRIAGE` in an earlier draft of this document. That classification was incorrect and has been corrected.

The corpus entry for `rgr-320-base-sha-catfile` claimed Codex misidentified `cat-file` usage. Codex inspection confirms:
- `validate_corpus_targets` at `run_autocoder_eval_corpus.py:171` uses `["git", "-C", str(repo), "cat-file", "-e", f"{base_sha}:{f}"]` — correct `sha:path` format
- `resolve_base_sha` at `run_autocoder_eval_corpus.py:84` uses `rev-parse --verify` — correct
- Existing regression test `test_catfile_sha_path_correct_format` at `test_run_autocoder_eval_corpus.py:445` covers this claim

This candidate was already addressed in Wave 3 docs (`docs/codex_remediation_wave3_candidate_classification.md`, `docs/codex_remediation_wave2_closeout.md`). No duplicate audit is needed.

---

## 4. Top Next Candidates

**None remain from this scan.** All 9 candidates from the two corpus files are closed. No open remediation tasks remain from this backlog scan.

---

## 5. Recommended Next Candidate to Audit First

No remaining candidate from this scan. Future corpus/notes scanning work should start from a fresh corpus file or new Codex findings, not from the existing Wave 1/2/3 corpus entries which are fully closed.

---

## 6. Operational Note

For AED repo audits, use terminal-only commands (git grep, python3, sed) instead of search_files/read_file. This avoids potential blocking when the AED workspace is in a certain state. All inspection in this scan used bounded terminal commands with explicit timeouts and output caps.

---

## 7. Explicit Statements

- **No production code changed.**
- **No tests changed.**
- **No repair executed.**
- **No search_files or read_file used** — all inspection via git grep, python3 JSON parsing, and sed with output caps.
- **No live Claude used.**
- **No autocoder batch executed.**
- **Hermes memory/profile/config not touched.**
- **No GitHub review threads resolved by script or API.**
- **No modification of production code or tests.**

---

## 8. Checkpoint Log

```
START 2026-05-28T08:58:42-04:00
file_inventory_exit=0
corpus_inventory_exit=0
docs_evidence_grep_exit=0
candidate_status_exit=0
catfile_grep_exit=0
catfile_test_grep_exit=0
compileall_exit=0
```