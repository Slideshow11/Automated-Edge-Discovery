# PR Review Comment Gate

**Date:** 2026-05-25
**PR:** #321
**Classification:** GATE_INFRASTRUCTURE

---

## 1. Summary

Adds `scripts/local/check_pr_review_comments.py` — a local gate script that fetches
GitHub PR review feedback from all four Codex/comment endpoints, classifies P0/P1/P2
findings, and fails closed if unresolved blockers exist. This addresses the PR #320
process gap where `final_gate_status.py` reported `READY_TO_MERGE` even though
GitHub/Codex inline P1 comments were still visible on the PR.

---

## 2. Why This Gate Exists

`final_gate_status.py` has a `codex_exact_head` check that verifies the reviewed SHA
matches the reported head SHA. However, this only proves that a Codex review was run
at that SHA — it does **not** prove that the inline findings from that review were
resolved, waived, or addressed.

PR #320 demonstrated this gap clearly:
- All structural checks in `final_gate_status.py` passed (`READY_TO_MERGE`)
- Two P1 inline comments from `chatgpt-codex-connector[bot]` were still visible on the PR
- Both P1 findings were real and required remediation

The missing check was: **have all Codex/automated-review inline findings been resolved
before we merge?**

---

## 3. Required Endpoints

The script fetches all four GitHub comment/review sources:

| Source | Endpoint |
|--------|----------|
| Issue comments | `repos/{repo}/issues/{pr}/comments` |
| Inline PR review comments | `repos/{repo}/pulls/{pr}/comments` |
| PR reviews (top-level) | `repos/{repo}/pulls/{pr}/reviews` |
| Per-review comments | `repos/{repo}/pulls/{pr}/reviews/{id}/comments` |

`gh pr view --comments` alone is insufficient — it only surfaces a subset.

---

## 4. Exit Statuses

| Exit Code | Status | Meaning |
|-----------|--------|---------|
| 0 | `REVIEW_COMMENTS_CLEAN` | No blockers. Safe to proceed to `final_gate_status.py` |
| 1 | `REVIEW_COMMENTS_BLOCKED` | Unresolved P0/P1/P2 findings remain. Do not merge |
| 2 | `REVIEW_COMMENTS_INCONCLUSIVE` | API errors or ambiguous state. Do not assume clean |

---

## 5. Severity Rules

| Severity | Blocking? | Waiver Allowed? |
|----------|-----------|----------------|
| P0 | Always | **No** |
| P1 | Always | **No** |
| P2 | By default | Yes — requires explicit waiver |
| P3 | No | Yes |
| `UNSPECIFIED_BLOCKING` | Always | No |
| `UNSPECIFIED_INFO` | No | No |

Severity is extracted in priority order:
1. Explicit `P0`/`P1`/`P2`/`P3` token in comment text
2. `High` → P1, `Medium` → P2, `Low` → P3
3. No severity keyword + blocking words (`shell=True`, `stale`, `malformed`, etc.) → `UNSPECIFIED_BLOCKING`
4. No severity and no blocking words → `UNSPECIFIED_INFO`

---

## 6. Finding IDs

Every finding is assigned a deterministic, stable ID:

```
codex-<12-char-sha256>
```

The ID is derived from: `user | path | line | severity | normalized_body_prefix[:200]`

**`source_kind` is intentionally excluded** so the same finding from different endpoints
(e.g. `inline_review_comment` + `per_review_comment`) produces the same ID and is correctly
deduplicated into one merged record with a `sources` list.

Same finding harvested twice → same ID. IDs enable stable waiver matching.

## 7. Live Head SHA Verification (P1-B)

Before applying any waivers, the script fetches the PR's live `headRefOid` via `gh pr view`
and compares it to `--reported-head-sha`. If they differ:
- Status becomes `REVIEW_COMMENTS_INCONCLUSIVE` (exit 2)
- No waivers are applied
- `live_head_sha` and `head_sha_mismatch` are included in the output JSON
- This prevents stale waiver replay against an outdated SHA

## 8. API Failure Handling (P1-A)

If **any** endpoint fails (rate limit, network error, invalid JSON, gh non-zero exit):
- Status becomes `REVIEW_COMMENTS_INCONCLUSIVE` (exit 2)
- The script **never** returns `REVIEW_COMMENTS_CLEAN` with partial/incomplete data
- API errors are surfaced in `api_errors` in the output JSON

This fail-closed behavior ensures partial harvests cannot mask unresolved blockers.

## 9. Waiver Rules

### Format

```json
{
  "pr_number": 320,
  "reported_head_sha": "c45dc1c9f9afd13a6c3e93f666dbaabaef8d1863",
  "waivers": [
    {
      "finding_id": "codex-abc123def456",
      "severity": "P2",
      "status": "WAIVED_NON_BLOCKING",
      "reason": "acceptable test-only risk",
      "evidence": "smoke passes, no production impact",
      "expires_after_pr": 321,
      "body_prefix": "P2: validate literal base SHA"
    }
  ]
}
```

### Rules
- **P0/P1 waivers are not supported in v0** — they always block
- **P2 waivers** require: `finding_id` (or `severity` + `body_prefix`), `reason`, `evidence`, exact `reported_head_sha` match
- **Stale SHA** (waiver SHA ≠ reported SHA) → waiver is invalid and the finding blocks
- **Missing waiver file** = no waivers applied
- Waiver matching order: exact `finding_id` first, then `severity` + `body_prefix` fallback

---

## 10. Required Workflow

For every AED PR, **after every push** and **before running `final_gate_status.py`**:

```bash
python3 scripts/local/check_pr_review_comments.py \
  --repo Slideshow11/Automated-Edge-Discovery \
  --pr-number <PR_NUMBER> \
  --reported-head-sha <HEAD_SHA> \
  --output-json /tmp/pr_review_status.json \
  --output-md /tmp/pr_review_status.md

# Inspect output
# Fix or waive findings
# Then only then run:
python3 scripts/local/final_gate_status.py ...
```

---

## 11. Updated Final Gate Checklist

The canonical pre-merge sequence (per `docs/pr314_batch_controller_gate_process_gap.md`):

1. ✅ Push PR branch
2. ⏳ Run `check_pr_review_comments.py` — **NEW**
3. ✅ Review and address findings (fix or waive P2)
4. ✅ Run `final_gate_status.py`
5. ✅ Run `verify_final_head_merge_command.py`
6. ✅ Merge

---

## 12. Safety Properties

- **No `shell=True`** — all `gh` invocations use list-argv `subprocess.run`
- **No live Claude** — script only calls `gh` CLI and Python stdlib
- **No `--enable-real-claude-executor`** — not used anywhere
- **Fail-closed on API errors** — ambiguous state returns exit 2, not 0
- **Deterministic output** — same harvest on same PR state always produces same findings
- **No Hermes mutations** — no memory, fact_store, profile, or skill writes
- **No external side effects** — writes only to user-specified `--output-json/--output-md`

---

## 13. Resolved Review Threads (GraphQL)

GitHub review threads can be resolved by human reviewers via the GitHub UI or API. Once a thread is resolved, the findings within it no longer block the gate — even if they are P0/P1 severity — because a human has explicitly reviewed and closed that conversation.

**Implementation**: The gate fetches `reviewThreads` via the GitHub GraphQL API (`gh api graphql`). Each finding's `url` field is matched to its corresponding thread comment entry. The thread's `isResolved` field is attached to the finding.

**Behavior**:
- **Unresolved thread + current-head P0/P1** → `REVIEW_COMMENTS_BLOCKED` (exit 1) — blocks normally
- **Resolved thread + current-head P0/P1** → reported as `resolved_non_blockers`, does **not** block, visible in JSON/markdown output
- **Missing thread metadata + current-head P0/P1** → fail closed: treated as blocker (`REVIEW_COMMENTS_BLOCKED`)
- **GraphQL API failure** → `REVIEW_COMMENTS_INCONCLUSIVE` (exit 2) — cannot determine resolution state
- **Stale findings** → tracked separately; resolution state is secondary to the stale/current-head classification

**Read-only**: The gate never resolves, dismisses, or modifies GitHub conversations. It only reads thread state to inform blocking decisions.

**Note on P1 waivers**: P1 findings cannot be waived. If a current-head P1 is a false positive, the correct resolution path is for a human to resolve the GitHub review thread in the UI, or for Codex to re-review and change its assessment.

---

## 14. Stale vs Current-Head Findings

**Note**: Section numbering was updated after adding "Resolved Review Threads" support.
"Stale vs Current-Head Findings" was previously section 13 and is now section 14.

Review comments in GitHub are attached to specific commit SHAs. A finding's `commit_id` (12-char prefix) tells us which commit the comment was made on.

**Current-head findings**: `commit_id` matches the live PR head SHA. These represent issues that exist in the current state of the PR branch and must be fixed or explicitly waived before the gate can pass.

**Stale findings**: `commit_id` does not match the live PR head SHA. These represent issues found on an older commit that has since been superseded by new commits. Stale P0/P1 findings do **not** indefinitely block the gate — instead, the gate returns `REVIEW_COMMENTS_INCONCLUSIVE` (exit 2), which requires an exact-head Codex re-review to clear. This prevents both silent ignoring and indefinite blocking.

**Key rules**:
- Current-head P0/P1 always block (exit 1)
- Stale P0/P1 → `REVIEW_COMMENTS_INCONCLUSIVE` (exit 2), not `CLEAN`
- Waivers apply only to current-head findings — stale findings cannot be waived because they attach to a superseded commit
- A finding without `commit_id` is treated as current-head (pre-v1 compat)
- Stale findings are clearly tagged `(STALE)` in JSON (`is_stale_head: true`) and markdown output

**Why not automatically mark stale findings as fixed?**
Because "the commit is different" does not prove "the issue was actually fixed." The only way to definitively clear a stale finding is for Codex to re-review the current HEAD and either confirm the issue is gone or acknowledge the fix. Until that happens, the status is `INCONCLUSIVE`, requiring human attention.

---

## 15. Future Improvements (Out of Scope for v0)

- P0/P1 waiver support with explicit human authorization
- Automatic "fixed in later commit" detection via git history
- `final_gate_status.py` integration via `--review-comments-status-json` flag
- ~~Ignore resolved conversations~~ — **Implemented in v1** via GitHub GraphQL `reviewThreads` API