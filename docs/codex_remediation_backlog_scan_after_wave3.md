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
| `rgr-320-base-sha-catfile` | NEEDS_TRIAGE | corpus/codex-remediation-pr314-320.json |
| `rgr-320-batch-ok-subprocess-rc` | CLOSED_ALREADY_FIXED_WITH_TEST via PR #345 | corpus/codex-remediation-pr314-320.json |
| `rgr-320-no-newline-marker` | CLOSED_FALSE_POSITIVE_WITH_EVIDENCE (Wave 2) | corpus/codex-remediation-pr314-320.json |

---

## 3. NEEDS_TRIAGE Candidate: rgr-320-base-sha-catfile

### What it claims
Corpus entry claims Codex misidentified `cat-file` usage — `validate_corpus_targets` uses `cat-file -e sha:path` correctly for file existence; `resolve_base_sha` uses `rev-parse --verify` correctly. No bug. Regression test needed.

### Evidence so far
- `validate_corpus_targets` at `run_autocoder_eval_corpus.py:171` uses `["git", "-C", str(repo), "cat-file", "-e", f"{base_sha}:{f}"]` — correct format `sha:path`
- `resolve_base_sha` at `run_autocoder_eval_corpus.py:84` uses `rev-parse --verify` — correct
- Existing regression test `test_catfile_sha_path_correct_format` at `test_run_autocoder_eval_corpus.py:445` confirms the sha:path format

### Likely classification
`ALREADY_FIXED_WITH_TEST` — the corpus entry itself documents no bug; regression test exists. This is a docs-only evidence audit, not a repair.

### Recommended next action
Evidence-only audit confirming test coverage is sufficient. One docs-only PR to close the loop.

---

## 4. Top Next Candidates

1. **`rgr-320-base-sha-catfile`** — only remaining NEEDS_TRIAGE from corpus scan
   - Priority: LOW (no bug, regression test exists)
   - Recommended action: evidence-only audit PR

2. **Remaining corpus entries not listed above** — none found. All 9 candidates from the two corpus files are closed or triaged.

---

## 5. Recommended Next Candidate to Audit First

**`rgr-320-base-sha-catfile`** — evidence-only audit to confirm `ALREADY_FIXED_WITH_TEST` and close the last open item from the Wave 1 corpus.

Scope: confirm test `test_catfile_sha_path_correct_format` covers the claim; produce one evidence doc; open one docs-only PR.

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