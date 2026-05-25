# PR #321 Review-Comment Gate Process Gap

**PR**: [#321 — gate: add PR review comment harvest check](https://github.com/Slideshow11/Automated-Edge-Discovery/pull/321)  
**Merged**: 2026-05-25T21:56:29Z  
**Merge commit**: `0d16e1b8a09de5fe8cbdeed71b78e241e54c9f67`  
**Author**: Slideshow11  
**Status**: Merged — unresolved P1 blockers on record

---

## What happened

PR #321 introduced `check_pr_review_comments.py` and its test suite, plus `docs/pr_review_comment_gate.md`.

Before merging, the review-comment gate was run manually and returned `REVIEW_COMMENTS_BLOCKED`:

```
status=REVIEW_COMMENTS_BLOCKED
blockers=2  stale=2  resolved=0  findings=5
```

Two **current-head P1 blockers** remained unresolved at merge time:

| finding_id | thread | line | state |
|---|---|---|---|
| `codex-a8fb356eb34a` | `PRRT_kwDOSHFpYM6EmV3q` | 709 | unresolved |
| `codex-10fe1115e477` | `PRRT_kwDOSHFpYM6EmV3q` | — | unresolved |

The PR was merged at **21:56:29 UTC**. The gate was run post-merge at **21:57:21 UTC** and correctly returned BLOCKED.

---

## Root cause analysis

### Immediate cause
The GitHub thread `PRRT_kwDOSHFpYM6EmV3q` was never marked resolved in GitHub's data layer (`isResolved: false` in GraphQL), even though a human clicked Resolve in the GitHub UI.

### Finding classification
Both P1s are the **same false-positive concern** surfaced twice (inline comment + per-review comment):

> *"Waiver validation trusts `--reported-head-sha` as authoritative, but this value is never checked against the PR's actual `headRefOid` before waivers are applied."*

This concern is already addressed in the code:

1. **Lines 486–492** (`gh_pr_view()`): Fetches live `headRefOid` from GitHub and compares it against `--reported-head-sha`.
2. **Lines 493–531** (fail-fast): `if head_sha_mismatch: return EXIT_INCONCLUSIVE` exits before `load_waiver()` (line 572) is ever reached when SHA mismatch is detected.
3. **Test `test_head_mismatch_load_waiver_never_called`** (line 41 in `tests/test_check_pr_review_comments.py`): Mocks `load_waiver` to raise `AssertionError` if called. When SHA mismatch is present, the test passes — proving `load_waiver` is structurally unreachable on mismatch.

### Systemic cause
`check_pr_review_comments.py` was **manual and not enforced as a required GitHub status check**. The repo is private and uses GitHub's free tier, so branch protection with required checks is not available. PR #321 was merged through the GitHub UI without gate verification.

---

## Verification at merge time

| Check | Result |
|---|---|
| CI (`ab3ee3b`) | ✅ 8/8 green |
| PMG (fresh compare) | ✅ clean, 0 blocked |
| review-comment gate | ❌ BLOCKED (post-merge audit) |
| Thread `PRRT_kwDOSHFpYM6EmV3q` | ❌ isResolved=false in GraphQL |

PMG was taken fresh (before-snapshot immediately followed by compare) and confirmed zero Hermes mutations from PR #321 code.

---

## Required future workflow

To prevent recurrence, every PR that touches `scripts/local/check_pr_review_comments.py` or that uses the review-comment gate must run:

```bash
python3 scripts/local/check_pr_review_comments.py \
  --repo Slideshow11/Automated-Edge-Discovery \
  --pr-number <PR_NUMBER> \
  --reported-head-sha "$(gh pr view <PR_NUMBER> --json headRefOid --jq .headRefOid)" \
  --output-json /tmp/review_comment_gate.json \
  --output-md /tmp/review_comment_gate.md

# Gate must return REVIEW_COMMENTS_CLEAN before final_gate_status.py and verify_final_head_merge_command.py
```

**PR #322 adds a `review-comment-gate` CI job** in `.github/workflows/ci.yml`. Once GitHub Pro is available (enabling branch protection rules), mark `review-comment-gate` as a required check.

### CI job constraints
- Runs on `pull_request` events targeting `main`  
- Uses `gh` CLI (pre-installed on Actions runners, authenticated via `GITHUB_TOKEN`)  
- Uses `pull_request.head.sha` as `--reported-head-sha`  
- Exit 0 = CLEAN, exit 1 = BLOCKED, exit 2 = INCONCLUSIVE  
- Does **not** use `--enable-real-claude-executor`, does not run live Claude  
- Does **not** mutate Hermes state

---

## Thread resolution status

Thread `PRRT_kwDOSHFpYM6EmV3q` still shows `isResolved: false` in GitHub GraphQL as of the post-merge audit. A re-resolution in the GitHub UI is required to clear the finding from the PR record. The duplicate finding `codex-10fe1115e477` (per-review comment source) clears automatically with the thread.

**URL**: https://github.com/Slideshow11/Automated-Edge-Discovery/pull/321#discussion_r3299358509

---

## Files changed by PR #321

| File | Change |
|---|---|
| `scripts/local/check_pr_review_comments.py` | New — review-comment harvest gate |
| `tests/test_check_pr_review_comments.py` | New — 42 tests (all passing) |
| `docs/pr_review_comment_gate.md` | New — gate design documentation |

---

## Related PRs

- **PR #322** (this PR) — process gap documentation + CI wiring for review-comment gate
