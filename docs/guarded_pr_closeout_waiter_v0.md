# Guarded PR Closeout Waiter v0

`scripts/local/guarded_pr_closeout_waiter.py` is a dry-run-by-default helper
for safely continuing AED PR closeout after a patch push.

It waits for the exact expected PR head, CI, Codex review-thread state, stale
thread eligibility, and final merge-command verification. It does not bypass
review gates, branch protection, or merge safety checks.

## CLI

```bash
python3 scripts/local/guarded_pr_closeout_waiter.py \
  --repo Slideshow11/Automated-Edge-Discovery \
  --pr-number 385 \
  --expected-head <sha> \
  --base-ref main \
  --max-wait-minutes 45 \
  --poll-seconds 60 \
  --output-json /tmp/aed_runs/pr385_closeout_waiter.json \
  --output-md /tmp/aed_runs/pr385_closeout_waiter.md \
  --trigger-codex-review \
  --allow-stale-thread-resolution
```

By default this reports only. To merge after all gates pass:

```bash
python3 scripts/local/guarded_pr_closeout_waiter.py ... --merge-if-ready
```

## Statuses

- `CLOSEOUT_READY_TO_MERGE`
- `CLOSEOUT_MERGED`
- `HOLD_CI_PENDING`
- `HOLD_CI_FAILED`
- `HOLD_CURRENT_HEAD_THREADS`
- `HOLD_CODEX_REVIEW_PENDING`
- `HOLD_STALE_THREAD_NOT_ELIGIBLE`
- `HOLD_HEAD_CHANGED`
- `HOLD_PR_NOT_OPEN`
- `HOLD_PR_NOT_MERGEABLE`
- `HOLD_FINAL_GATE_FAILED`
- `HOLD_MERGE_COMMAND_NOT_VERIFIED`
- `ERROR_TOOL_FAILURE`

## Safety Invariants

- The PR head must exactly equal `--expected-head`.
- Total waiting is bounded by `--max-wait-minutes`.
- Current-head review threads are never resolved by the waiter.
- Outdated unresolved threads are resolved only when
  `--allow-stale-thread-resolution` is set and
  `check_stale_review_thread_resolution.py --base-ref main` returns
  `ELIGIBLE_STALE_THREAD_RESOLUTION`.
- Codex re-review is requested only when `--trigger-codex-review` is set, by
  posting exactly `@codex review`.
- Merge is disabled unless `--merge-if-ready` is explicitly passed.
- Merge uses the command verified by `verify_final_head_merge_command.py`, and
  the command must contain `--match-head-commit <expected-head>`.
- `--admin` and `--auto` are rejected.
- The helper never edits workflows, branch protection, reviews, or comments
  other than the optional `@codex review` request.

## Outputs

The JSON and Markdown reports include:

- PR number and repository
- expected and final head SHA
- CI state and failing job summary when available
- review-thread summary
- stale-check and final-gate actions taken
- verified merge command when ready
- next action

## Intended Use

This helper is meant for the gap between patch push and guarded merge. For
example, PR #385 repeatedly needed a human to come back after CI and Codex
re-review. The waiter can keep polling boundedly, request one Codex review when
configured, resolve only officially eligible stale threads, and stop with a
structured report whenever a gate remains blocked.
