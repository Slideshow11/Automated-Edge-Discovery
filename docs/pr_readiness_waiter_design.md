# PR Readiness Waiter — Design Document

## Overview

`wait_for_pr_ready.py` is a read-only/scripted tool that automates waiting for CI
and PR readiness gates to complete, collects evidence, and produces structured reports.
It does not merge, push, commit, or mutate any external system. It is a polling and
reporting tool only.

**Hard constraint**: This tool must never merge, push, commit, add, resolve review
threads, invoke live Claude, run autocoder batch, mutate Hermes, or install packages.
All outputs are reports and logs.

## Non-Goals

- No merge automation. A separate guarded merge tool (not designed here) would own that.
- No autonomous agent invocation. This is a scripted waiter, not an agent.
- No Hermes mutation. Memory, profile, config, and skills are read-only.
- No shell=True. All subprocess calls use `shell=False`.

## Proposed Interface

```bash
python3 scripts/local/wait_for_pr_ready.py \
  --pr-number 335 \
  --timeout-minutes 30 \
  --poll-seconds 30 \
  --require-review-comments-clean \
  --require-pmg \
  --require-final-gates \
  --output-json /tmp/aed_runs/pr335_wait/status.json \
  --output-md /tmp/aed_runs/pr335_wait/status.md
```

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--pr-number` | Yes | — | PR number to poll |
| `--timeout-minutes` | No | 30 | Max wait time before HOLD_TIMEOUT |
| `--poll-seconds` | No | 30 | Seconds between CI status polls |
| `--require-review-comments-clean` | No | False | Run check_pr_review_comments.py after CI green |
| `--require-pmg` | No | False | Run PMG compare |
| `--require-final-gates` | No | False | Run final_gate_status.py |
| `--require-merge-ready` | No | False | Run verify_final_head_merge_command.py |
| `--required-checks` | No | (see default list) | Override default required CI check names |
| `--output-json` | Yes | — | Path to JSON report |
| `--output-md` | No | — | Path to Markdown report |

## Statuses

The tool emits one of the following terminal statuses:

| Status | Meaning |
|---|---|
| `READY_FOR_FINAL_GATES` | CI green, review comments clean, ready for final_gate_status |
| `READY_TO_MERGE_CANDIDATE` | All gates passed, merge command can be formed |
| `HOLD_CI_PENDING` | CI still running, within timeout |
| `HOLD_CI_FAILED` | A required CI check failed, cancelled, skipped, missing, or unknown |
| `HOLD_REVIEW_COMMENTS_BLOCKED` | Open blocking review comments found |
| `HOLD_REVIEW_COMMENTS_INCONCLUSIVE` | Could not determine review comment state |
| `HOLD_PMG_DIRTY` | PMG compare found Hermes mutations |
| `HOLD_HEAD_CHANGED` | PR head SHA changed during polling |
| `HOLD_TIMEOUT` | Timed out before all checks completed |
| `ERROR_TOOLING` | A required tool (gh, check_pr_review_comments.py, etc.) failed |

## Design Decisions

### 1. Re-read PR head SHA before every major stage

Before polling CI, before running review-comment gate, before running PMG, and
before running final gates — always re-read the live head SHA via `gh pr view`.
If the reported head SHA differs from the previously recorded head SHA, the tool
immediately stops with `HOLD_HEAD_CHANGED`. This prevents operating on a stale
commit while the PR force-pushes.

```python
def get_live_head_sha(pr_number: int) -> str:
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--json", "headRefOid", "--jq", ".headRefOid"],
        capture_output=True, text=True, shell=False
    )
    return result.stdout.strip()
```

### 2. Fail closed on any unexpected CI check state

All required CI checks must be in state `pass`. The following are treated as failures
even if the overall CI would otherwise green-light the PR:

- Any check with state `failure` → `HOLD_CI_FAILED`
- Any check with state `cancelled` → `HOLD_CI_FAILED`
- Any required check with state `skipped` → `HOLD_CI_FAILED`
- Any required check absent from the list → treated as **pending** (keep polling);
  fail-closed as `HOLD_TIMEOUT` only when the deadline is reached with the check
  still missing. This prevents false `HOLD_CI_FAILED` during CI workflow startup
  when pr-gate-live-smoke has not yet posted results.
- Any check with an unknown state (not pass/failure/cancelled/skipped/pending) →
  `ERROR_TOOLING` with detail logged

The `pending` state is not a failure — it triggers another poll cycle.

Default required checks for this repository:
- `test (3.11)` or `test` (any Python version)
- `review-comment-gate`
- `validator`
- `governance-validators`
- `pr-gate-live-smoke`

The waiter must fail closed if any default required check is missing from the poll
result, pending past the timeout, failed, cancelled, skipped when required, or
returned with an unknown conclusion. The default list is the minimum gate set for
this repository — it is not an optional example. Future implementations may support
repo-specific check configuration via `--required-checks`, but the AED default must
always include `pr-gate-live-smoke`.

### 3. Review-comment gate runs only after CI is green

Running `check_pr_review_comments.py` against a PR with failing CI produces
inconclusive results. The tool waits for all required CI checks to pass before
calling `check_pr_review_comments.py`. If review-comment gate fails, the tool
emits `HOLD_REVIEW_COMMENTS_BLOCKED` or `HOLD_REVIEW_COMMENTS_INCONCLUSIVE`.

### 4. PMG compare is optional and gated behind `--require-pmg`

PMG snapshot is taken at tool start (not at PR creation time) because Hermes state
can change between PR creation and the merge-ready moment. The compare captures
all Hermes mutations made during the wait, not just since PR creation.

```python
# Phase 1: Snapshot
call_check_pmg_snapshot("--root", HERMES_ROOT, "--output", before_json)

# Phase 2: Poll CI

# Phase 3: Compare (if --require-pmg)
call_check_pmg_compare("--root", HERMES_ROOT, "--before", before_json,
                        "--output-json", pmg_json, "--output-md", pmg_md)
```

### 5. Final gates run in sequence, each can hold

The sequence is:
1. Re-read head SHA (detect change)
2. Poll CI (wait for all required checks green or failed)
3. Run `check_pr_review_comments.py` (if `--require-review-comments-clean`)
4. Run PMG compare (if `--require-pmg`)
5. Run `final_gate_status.py` (if `--require-final-gates`)
6. Run `verify_final_head_merge_command.py` (if `--require-merge-ready`)

Each stage emits a hold status if conditions are not met. The tool does not
continue to later stages when an earlier stage holds.

### 6. JSON and Markdown reports always written

Even on `ERROR_TOOLING` or `HOLD_TIMEOUT`, the tool writes its JSON and Markdown
report so the operator can inspect what happened. The report includes:
- Final status
- All CI check states at time of last poll
- Review-comment gate result (if run)
- PMG result (if run)
- Final gate results (if run)
- Head SHA at each stage
- Timestamp at each stage
- The exact next safe action as a string

```json
{
  "status": "READY_TO_MERGE_CANDIDATE",
  "pr_number": 335,
  "head_sha": "0ce5bdb5745c2ebafa801e439d26eb95031b64c3",
  "ci_checks": {...},
  "review_comment_gate": {...},
  "pmg_compare": {...},
  "final_gate_status": {...},
  "merge_ready_verifier": {...},
  "next_safe_action": "gh pr merge 335 --squash --delete-branch --match-head-commit 0ce5bdb5745c2ebafa801e439d26eb95031b64c3",
  "tool_version": 1
}
```

### 7. Explicit next safe action

The JSON report always includes `next_safe_action` as a human-readable string.
This is the only "actionable" output and it is a recommendation only — the tool
does not execute it.

For `READY_TO_MERGE_CANDIDATE`:
```
gh pr merge {pr_number} --squash --delete-branch --match-head-commit {head_sha}
```

For `HOLD_*`:
```
Stop and resolve: {reason}. Do not merge yet.
```

For `ERROR_TOOLING`:
```
Investigate tooling error in logs. Do not merge until resolved.
```

### 8. No shell=True anywhere

All `subprocess.run` calls use `shell=False`. Arguments are passed as list items,
never interpolated into shell strings. `gh pr merge` is never called by this tool.

### 9. Repo-context independence

The waiter must work from any working directory. All `gh` commands use explicit
`--repo owner/name` so the tool works when invoked from `/tmp`, a hermes cron
job, or any non-repo context. Local scripts (PMG, final_gate_status,
check_pr_review_comments) run with `cwd=REPO_ROOT` so they also work from
anywhere.

Two CLI arguments support this:
- `--repo` — GitHub repository in `'owner/name'` form (default:
  `Slideshow11/Automated-Edge-Discovery`)
- `--repo-root` — absolute path to the AED repository root (default:
  auto-detected from script location: `<script>/../../../`)

### 10. Gate semantics — HARD stops vs. merge candidates

`final_gate_status.py` returns `HOLD_*` statuses (e.g., `HOLD_CI_RED`,
`HOLD_REVIEW_COMMENTS_BLOCKED`, `HOLD_PMG_DIRTY`). These are **hard stops**:
the PR must not be merged while any `HOLD_*` is active. `HOLD_CI_RED` means CI
has failed and is not a candidate for merge.

`verify_final_head_merge_command.py` returns `MERGE_READY_CANDIDATE` only when
ALL of its prerequisite conditions are satisfied. A `HOLD_*` from
`final_gate_status.py` is not a `MERGE_READY_CANDIDATE`.

**Process deviation (PR #338)**: PR #338 was manually merged despite
`final_gate_status.py` returning `HOLD_CI_RED`. The CI failure on PR #338 was
a pre-existing infrastructure issue (not a code defect in the changed file),
but the manual override bypassed a designed gate. This pattern must not repeat.
If `final_gate_status.py` returns any `HOLD_*` status, a separate documented
human override process (with a written decision record) is required before the
merge proceeds. The waiter itself never merges and never bypasses gates.

## File Layout

```
scripts/local/wait_for_pr_ready.py   # The tool (design, not yet implemented)
docs/pr_readiness_waiter_design.md   # This document
```

## Open Questions

1. Should `--require-final-gates` imply `--require-review-comments-clean`? The
   `final_gate_status.py` script already includes review-comment gate as a check,
   but running it before CI is green wastes time.

2. Should there be a `--max-retries` on individual tool calls (check_pr_review_comments.py,
   final_gate_status.py) rather than failing immediately? Currently the design fails
   immediately on tool error to keep the operator in control.

3. Should the tool support webhooks instead of polling? The current design uses
   polling (every 30 seconds by default). Webhooks would reduce latency but add
   complexity. Deferred to future iteration.

4. Should `--require-merge-ready` be the default? It adds little cost (one more
   tool call) and provides the full merge command. The concern is that it runs
   `verify_final_head_merge_command.py` which checks PMG — if PMG state is dirty
   from unrelated Hermes activity, it could hold a ready PR.