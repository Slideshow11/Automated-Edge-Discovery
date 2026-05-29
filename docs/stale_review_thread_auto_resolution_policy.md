# Stale Review-Thread Auto-Resolution Policy

**Date:** 2026-05-29
**Classification:** POLICY_DOCUMENT
**Status:** ACTIVE

---

## 1. Purpose

This document defines the sole permitted case and the mandatory preconditions under which an automated agent may resolve a stale GitHub PR review thread without human intervention. It exists to reconcile two goals:

- **Autocoder velocity:** Resolving outdated threads manually is a bottleneck that delays autocoder merges.
- **Safety:** Blindly resolving all threads destroys the review signal and can mask real regressions.

The policy is derived from the successful handling of PR #355, where one outdated Codex review thread was safely resolved after verifying 14 conditions.

---

## 2. Why Manual Resolution Is Incompatible With the Autocoder Goal

When an autocoder workflow files a PR, the PR author is a bot (`chatgpt-codex-connector[bot]`). Resolving review threads requires someone with write access to the repository to click "Resolve" in the GitHub UI or call the API. A human doing this manually for every stale thread is:

- A bottleneck that defeats the purpose of autocoder batching.
- Error-prone: humans cannot efficiently track which of dozens of threads on a fast-moving PR are stale vs. live.
- Inconsistent: a human may resolve a thread that an autocoder later re-opens by re-fixing the same issue.

An automated policy enables the autocoder to stay autonomous while maintaining safety boundaries.

---

## 3. Why Blindly Resolving Comments Is Unsafe

Resolving all unresolved review threads or all threads with the bot's handle is catastrophically unsafe:

- A thread flagged as P0/P1 may represent a real regression that is still present in the diff.
- A thread may be marked outdated by the bot but the underlying issue may still exist in a different code path.
- A `REQUEST_CHANGES` review must not be dismissed; dismissing it removes the human safety net.
- Resolving multiple threads in one pass multiplies risk: if any single resolution is wrong, the PR merges with multiple live regressions.

---

## 4. The Allowed Case

**An outdated review thread may be auto-resolved when and only when:**

The current PR head SHA no longer contains the flagged issue. The specific comment body cites a pattern (e.g., a variable name, a function signature, a file path) that is present in the old diff but absent from the current diff — and the current diff contains the replacement/fix if one was implied.

This is **thread-level, not PR-level**: one thread at a time, with verification between each resolution.

---

## 5. Forbidden Cases

Resolution is **never permitted** when any of the following are true:

| Condition | Reason |
|-----------|--------|
| Thread is not outdated | The flagged issue is still present in the current diff |
| Thread contains a P0/P1/P2 finding still present | Real regression; must be fixed, not resolved |
| Review is `REQUEST_CHANGES` type | Dismissing would remove human safety net |
| Thread contains an unresolved human reviewer comment | Human signal must not be overwritten |
| CI is red | Unresolved failures indicate the PR is not safe to merge |
| PMG is dirty | Production mutation guard failed; repo state is not clean |
| Current diff still contains the flagged pattern | Issue not actually fixed |
| Multiple unrelated unresolved threads | Risk compounding: resolve one at a time |
| No specific pattern in comment body to verify against | Cannot prove thread is stale without a concrete reference |
| Changed files extend beyond expected scope | Policy applies only to the files the PR touches |

---

## 6. Required Pre-Resolution Conditions

All 14 of the following must be true before resolving a single thread:

| # | Condition | Verification method |
|---|-----------|---------------------|
| 1 | PR is open | GitHub API: `GET /repos/{owner}/{repo}/pulls/{pr}` — `state == "open"` |
| 2 | Exact head SHA verified | `gh pr view --json headRefOid` matches reported SHA |
| 3 | Target thread exists | GitHub API: `GET /repos/{owner}/{repo}/pulls/{pr}/comments` — thread ID confirmed |
| 4 | Thread is outdated | Comment body cites a pattern absent from current diff |
| 5 | Thread is unresolved | Thread `state != "RESOLVED"` in GitHub API |
| 6 | Comment body maps to a specific flagged pattern | Comment body contains a concrete reference (variable, path, function) that can be grepped |
| 7 | Current diff no longer contains the flagged pattern | `git fetch origin <base-branch> && git diff origin/<base-branch>...HEAD -- {files}` shows the pattern is absent. Alternatively, compare `https://github.com/{owner}/{repo}/compare/{base-branch}...<head-sha>` or use `git log --oneline -1 origin/<base-branch>` to find merge-base and diff from there. Never use `git diff HEAD -- {files}` in a clean checkout — that compares the working tree to HEAD and is always empty. |
| 8 | Current diff contains the replacement/fix if applicable | If the pattern implies a fix, the replacement is present in the diff |
| 9 | CI is green | All required branch-protection checks pass on the current head SHA |
| 10 | PMG is clean | `pmg snapshot` vs `pmg compare` shows no blocked changes |
| 11 | No other unresolved non-outdated blocking threads | All other unresolved threads must also be outdated |
| 12 | Changed files are within expected scope | Diff touches only files relevant to the PR purpose |
| 13 | No production safety policy is weakened | No `xpass`, `skip`, or test weakening in the diff |
| 14 | No tests skipped or marked xfail | pytest still runs all tests; no test is removed or decorated to skip |

---

## 7. Required Action

When all 14 conditions are met:

1. **Resolve only the stale thread.** Do not resolve any other threads in the same pass.
2. **Do not dismiss the review.** Only resolve the comment thread; do not call `dismissReview`.
3. **Do not resolve unrelated threads.** Even if multiple threads appear stale, resolve one, then re-verify all 14 conditions before resolving the next.
4. **Rerun the waiter** with `--require-review-comments-clean` still enabled to confirm the resolved thread no longer appears as a blocker.
5. **Merge only if `READY_TO_MERGE_CANDIDATE`.** If the waiter returns anything other than `READY_TO_MERGE_CANDIDATE`, do not merge. Investigate before proceeding.

---

## 8. Required Audit Record

Every auto-resolution must produce a written record containing:

```
PR number:          <PR>
Thread ID:          <thread_id>
Comment ID:         <comment_id or body summary>
Old flagged pattern: <exact pattern cited in comment>
Evidence (current diff no longer contains pattern):
  SHA:              <current head SHA>
  git diff output:  <grep/sed output showing pattern absent>
Current CI status:  <green/red>
PMG status:         <clean/dirty>
Waiter result:      <READY_TO_MERGE_CANDIDATE or BLOCKED>
Merge result:       <merged/Not merged>
```

This record must be attached to the PR as a comment or stored in the run's output directory before the merge command is issued.

---

## 9. Motivating Example: PR #355

PR #355 (`ci: provide hermes home for pmg tests`) demonstrated this policy in practice.

**What happened:**
- The PR had one outdated Codex review thread: a comment flagged a pattern in a file that was modified in the PR but where the specific flagged line was no longer present in the final head.
- The agent verified all 14 conditions.
- The thread was resolved, the waiter was re-run with `--require-review-comments-clean` still active, and the waiter returned `READY_TO_MERGE_CANDIDATE`.
- The PR was merged with `--match-head-commit` to prevent head-sha mismatch.

**What was verified:**
- PR state: open
- Head SHA: exact match verified twice
- Thread outdated: the flagged pattern was absent from the current diff
- CI green: `test (3.11)`, `validator`, `governance-validators`, `review-comment-gate`, `pr-gate-live-smoke` all passed
- PMG clean: no blocked changes
- No other unresolved non-outdated threads
- Changed files within scope

**What was NOT done:**
- No review was dismissed
- No other threads were resolved in the same pass
- No merge was issued without `READY_TO_MERGE_CANDIDATE`

---

## 10. Policy Compliance Checklist

Before every merge where a stale thread was resolved:

- [ ] PR open
- [ ] Head SHA verified (twice minimum)
- [ ] Target thread ID confirmed
- [ ] Thread confirmed outdated
- [ ] Thread confirmed unresolved
- [ ] Comment body maps to verifiable pattern
- [ ] Current diff does not contain flagged pattern
- [ ] Current diff contains fix/replacement if applicable
- [ ] CI green on current head
- [ ] PMG clean
- [ ] No other unresolved non-outdated blocking threads
- [ ] Changed files in scope
- [ ] No test skips/xfails added
- [ ] Audit record written
- [ ] Waiter returned `READY_TO_MERGE_CANDIDATE`
- [ ] Merge with `--match-head-commit`

---

*This policy is binding. Any resolution that does not follow this policy is an unauthorized mutation.*