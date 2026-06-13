# AED Enforcement Matrix

> Maps every AED governance rule to its current enforcement strength
> and the code / test / doc locations that implement it. This is the
> "where does this rule actually live today?" view; the
> `aed_rules_inventory.md` is the "what does the rule say?" view.

## 1. How to read this matrix

Each row covers one `AED-RULE-NNN` and includes:

- **Rule** — the short name.
- **Current location** — the file(s) where the rule currently lives
  (script, doc, schema, prompt).
- **Enforcement type** — one of:
  - `hard-coded` (the script refuses to proceed on violation)
  - `validator` (a separate validation script that reports but does
    not block)
  - `ci` (enforced by a GitHub Actions check)
  - `test` (enforced by a unit test fixture)
  - `doc` (described in a doc, no automated check)
  - `prompt` (operator prompt only)
  - `manual` (operator must do it by hand, no check)
  - `missing` (no current enforcement; the rule is aspirational)
  - `ambiguous` (enforcement exists in some places but not others)
- **Strength** — one of:
  - `Hard` (cannot proceed; violation is a hard fail)
  - `Medium` (warns + degrades behavior; can be bypassed with
    explicit opt-in)
  - `Soft` (operator must consciously read)
  - `Prompt-only` (no enforcement outside the operator prompt)
  - `Missing` (no enforcement today)
- **Test coverage** — relevant test files.
- **Gap** — what's missing today.
- **OpenHands target** — which OpenHands construct will own the rule
  post-migration (see `aed_openhands_migration_map.md`).

## 2. Rule matrix

| Rule ID | Rule | Current location | Enforcement type | Strength | Test coverage | Gap | OpenHands target |
|---------|------|------------------|------------------|----------|--------------|-----|------------------|
| AED-RULE-001 | No primary worktree mutation | `docs/aed_known_safe_command_cookbook.md` §3; `docs/aed_whole_workflow_operator_path.md` §11 | prompt | Prompt-only | none | no automated check | `AEDGitTool` PreToolUse hook |
| AED-RULE-002 | No primary sync without explicit authorization | PR #400; `docs/aed_known_safe_command_cookbook.md` §3; `schemas/aed_lifecycle_states_v1.json` | doc + schema | Soft | schema-level | no runtime check | `AEDGitTool` auth.phrase gate |
| AED-RULE-003 | Use temp worktrees | `docs/aed_known_safe_command_cookbook.md` §3; `docs/temp_worktree_human_apply_workflow.md` | prompt | Prompt-only | none | no automated check | `AEDWorkspaceProvisioner` |
| AED-RULE-004 | Use exact 40-char SHAs | `docs/aed_known_safe_command_cookbook.md` §3; `merge_pr_safely.py` | hard-coded + prompt | Medium | `tests/test_merge_pr_safely.py` | not enforced in every wrapper | `AEDPolicy` SHA validator |
| AED-RULE-005 | Re-verify live head before merge-authorizing action | `docs/aed_known_safe_command_cookbook.md` §13; `verify_final_head_merge_command.py`; `merge_readiness_with_phase_ledger.py` | hard-coded + ci | Hard | `tests/test_verify_final_head_merge_command.py`; `tests/test_merge_readiness_with_phase_ledger.py` | not enforced on thread resolution | `AEDGitHubTool` head recheck |
| AED-RULE-006 | Reject `--admin` and `--auto` | `merge_pr_safely.py::reject_admin`; `merge_readiness_with_phase_ledger.py`; `docs/phase_ledger_merge_readiness_wrapper.md` | hard-coded | Hard | `tests/test_merge_pr_safely.py`; `tests/test_merge_readiness_with_phase_ledger.py` | none | `AEDMergeTool` argparse + `AEDPolicy` |
| AED-RULE-007 | Human merge authorization phrase required | `docs/merge_authorization_guard.md`; `schemas/aed_lifecycle_states_v1.json` `human_authorization_required` | doc + schema | Soft | schema-level | no runtime phrase check | `AEDPolicy` auth.phrase gate |
| AED-RULE-008 | Thread resolution requires explicit human authorization | `docs/stale_review_thread_auto_resolution_policy.md`; `check_stale_review_thread_resolution.py` | hard-coded + prompt | Medium | `tests/test_check_stale_review_thread_resolution.py` | not a hard fail by default; only stale-policy check | `AEDGitHubTool` per-thread auth.phrase |
| AED-RULE-009 | Codex response classifier drives lifecycle state | `audit_codex_response_for_pr.py`; `docs/aed_lifecycle_state_registry.md` | ci + hard-coded | Hard | `tests/test_audit_codex_response_for_pr.py` (122 tests) | none | `AEDCodexClassifierTool` |
| AED-RULE-010 | Exactly one Codex ping per head | `audit_codex_response_for_pr.py` ping tracking | hard-coded | Medium | ping-window tests in `tests/test_audit_codex_response_for_pr.py` | not enforced before posting (post-hoc only) | `AEDCodexPingTool` de-dup |
| AED-RULE-011 | Clean pass must be tied to current head | `audit_codex_response_for_pr.py` `clean_pass_*` | hard-coded | Hard | clean-pass tests in `tests/test_audit_codex_response_for_pr.py` | none | `AEDCodexClassifierTool` |
| AED-RULE-012 | Formal reviews on non-head commits must not downgrade current-head clean pass | `audit_codex_response_for_pr.py` `extract_review_commit_oid` filter in `newer_finding_after_clean_pass` | hard-coded | Hard | `test_p2_stale_review_*` (3); `test_p2_formal_clean_pass_then_later_review_on_other_head_still_hold_new` | none | `AEDCodexClassifierTool` |
| AED-RULE-013 | Review-thread inventory must be complete | `audit_codex_response_for_pr.py` `review_thread_inventory_complete` | hard-coded | Hard | `test_p2_partial_inventory_*`; `test_paginated_*` | none | `AEDCodexClassifierTool` |
| AED-RULE-014 | Issue-comment inventory must be complete | `audit_codex_response_for_pr.py` `issue_comment_inventory_complete` | hard-coded | Hard | `test_p1_*_fetch_failure_*` | none | `AEDCodexClassifierTool` |
| AED-RULE-015 | Review-submission inventory must be complete | `audit_codex_response_for_pr.py` `review_submission_inventory_complete` | hard-coded | Hard | `test_p1_*_fetch_failure_*` | none | `AEDCodexClassifierTool` |
| AED-RULE-016 | Visible findings from incomplete nested pagination must be preserved | `audit_codex_response_for_pr.py` nested pagination block | hard-coded | Hard | `test_p2_partial_inventory_*` | none | `AEDCodexClassifierTool` |
| AED-RULE-017 | Timestamps must be timezone-aware ISO 8601 | `audit_codex_response_for_pr.py` `parse_iso_utc` | hard-coded | Hard | `test_p2_partial_inventory_hold_pending_renders_pending_status`; `test_valid_*_ping_timestamp_*` | none | `AEDPolicy` ISO validator |
| AED-RULE-018 | All 5 required CI checks must pass | `.github/workflows/ci.yml`; `audit_main_ci_for_head.py` | ci + hard-coded | Hard | `tests/test_audit_main_ci_for_head.py` (45 tests); `tests/test_check_pr_review_comments.py` | none | `AEDCI` + `AEDMergeTool` |
| AED-RULE-019 | Scope guard must report SCOPE_CLEAN | `scripts/local/scope_guard.py` | hard-coded | Hard | `tests/test_scope_guard.py` | not run automatically on every commit; run per-turn | `AEDPolicy` post-diff hook |
| AED-RULE-020 | Audit log is append-only; external audit is never mutated | `append_merge_action_audit.py`; `validate_merge_action_audit_log.py`; `docs/trace_policy_v1.md`; PR #398 | hard-coded + prompt | Hard + Soft | `tests/test_append_merge_action_audit.py`; `tests/test_validate_merge_action_audit_log.py` | operator-prompt rule for "never normalize" is soft | `AEDAuditTool` append-only |
| AED-RULE-021 | Protected historical PRs are read-only | `docs/aed_known_safe_command_cookbook.md` §3; closeout entries | prompt | Prompt-only | none | no automated check on PR number list | `AEDGitHubTool` protected-PR list |
| AED-RULE-022 | Resume checkpoint continuation rule | `docs/aed_lifecycle_state_registry.md` §11; `aed_lifecycle_states.py`; PR #399 | doc + schema | Medium | `tests/test_aed_lifecycle_states.py` resume rules | not enforced at runtime | `AEDLifecycleStateStore` |
| AED-RULE-023 | Gate-safe comment language for Codex pings | `docs/aed_known_safe_command_cookbook.md` §13; `check_pr_review_comments.py` `BLOCKING_WORDS` | prompt + ci (backstop) | Soft (pre) + Hard (post) | gate tests | pre-post scan is operator-side; no automated pre-post check | `AEDCodexPingTool` pre-post scan |
| AED-RULE-024 | Forbidden operations are hard-rejected | `scope_guard.py` `BUILTIN_FORBIDDEN_DIFF_REGEXES`; `merge_pr_safely.py` argparse; cookbook | hard-coded + prompt | Hard | `tests/test_check_pr_scope.py`; `tests/test_scope_guard.py` | runtime operations (not just diffs) not all guarded | `AEDPolicy` + tool-level rejection |
| AED-RULE-025 | Bounded polling only | `audit_codex_response_for_pr.py`; `audit_main_ci_for_head.py` argparse; cookbook | hard-coded | Hard | `tests/test_audit_codex_response_for_pr.py` bounded-polling tests | none | `AEDPolicy` bounded-poll flag |
| AED-RULE-026 | Final report distinguishes post-ping from older anchored | per-turn workflow prompt | prompt | Prompt-only | none | no template enforcement | `AEDReportTool` template |
| AED-RULE-027 | Classifier returns a precise lifecycle state | `schemas/aed_lifecycle_states_v1.json`; classifier packet contract | hard-coded + schema | Hard | `tests/test_aed_lifecycle_states.py` (73 tests) | none | `AEDLifecycleStateStore` |
| AED-RULE-028 | Formal clean-pass detection scans all post-ping formal reviews | `audit_codex_response_for_pr.py` formal clean-pass branch; PR #402 | hard-coded | Hard | 8 tests including `test_p2_formal_clean_pass_then_later_commented_non_clean_routes_hold_new` | none | `AEDCodexClassifierTool` |
| AED-RULE-029 | Per-poll raw/derived reset | `audit_codex_response_for_pr.py` per-poll reset block; PR #402 | hard-coded | Hard | `test_raw_poll_snapshot_reset_*`; `test_stale_stop_state_cleared_*` | none | `AEDCodexClassifierTool` |
| AED-RULE-030 | Operator-prompt rules not yet hard-enforced | meta-rule for the migration | doc | Prompt-only | none | the gap this rule exists to track | migration target itself |

## 3. Strength summary

| Strength | Count | Rules |
|----------|-------|-------|
| Hard | 16 | AED-RULE-005, -006, -009, -011, -012, -013, -014, -015, -016, -017, -018, -019, -020 (append-only half), -024, -025, -027, -028, -029 |
| Medium | 5 | AED-RULE-004, -008, -010, -020 (no-normalize half), -022 |
| Soft | 2 | AED-RULE-002, -007 |
| Prompt-only | 7 | AED-RULE-001, -003, -021, -023 (pre-post half), -026, -030 |
| Missing | 0 | — |
| Ambiguous | 0 | — |

(Rule counts in the summary may exceed 30 because some rules have
mixed enforcement — e.g., AED-RULE-020 has both a hard `append-only`
half and a soft `no-normalize` half.)

## 4. Prompt-only / manual-only rules (the migration debt)

The following rules are enforced solely through the operator prompt
today. Each is a candidate for promotion to a hard rule in the
OpenHands migration:

1. **AED-RULE-001** — No primary worktree mutation
2. **AED-RULE-002** — No primary sync without explicit authorization
3. **AED-RULE-003** — Use temp worktrees
4. **AED-RULE-007** — Human merge authorization phrase required
5. **AED-RULE-021** — Protected historical PRs are read-only
6. **AED-RULE-023 (pre-post half)** — Gate-safe comment language for
   Codex pings (pre-post scan is operator-side)
7. **AED-RULE-026** — Final report distinguishes post-ping from older
   anchored
8. **AED-RULE-030** — Meta-rule tracking the gap above

These 8 rules define the OpenHands migration's enforcement-hardening
scope. A `PreToolUse` hook and a `Stop` hook can promote the first 5
to hard rules; a report template can promote AED-RULE-026.

## 5. Worked examples (from the matrix)

### Example A — No primary worktree mutation (AED-RULE-001)

- **Where today:** `docs/aed_known_safe_command_cookbook.md` §3; operator
  prompt.
- **Enforcement strength today:** Prompt-only.
- **Test coverage:** None.
- **Gap:** No automated check that the primary worktree HEAD is
  unchanged.
- **OpenHands target:** `AEDGitTool` PreToolUse hook refuses any
  `git` mutation when `cwd` matches the primary worktree path AND
  `auth.phrase` is absent.

### Example B — Formal reviews on non-head commits must not downgrade current-head clean pass (AED-RULE-012)

- **Where today:** `audit_codex_response_for_pr.py` formal-review
  `newer_finding_after_clean_pass` loop with
  `extract_review_commit_oid` filter.
- **Enforcement strength today:** Hard (script-enforced).
- **Test coverage:** 4 tests
  (`test_p2_stale_review_changes_requested_does_not_downgrade_clean_pass`,
  `_commented_`, `_approved_`,
  `test_p2_formal_clean_pass_then_later_review_on_other_head_still_hold_new`).
- **Gap:** None today; the rule is well-enforced and well-tested.
- **OpenHands target:** `AEDCodexClassifierTool` (existing classifier
  behavior preserved).

### Example C — No merge without exact human authorization (AED-RULE-007)

- **Where today:** `docs/merge_authorization_guard.md` documents the
  phrase; `schemas/aed_lifecycle_states_v1.json` marks merge-allowed
  states as `human_authorization_required: true`.
- **Enforcement strength today:** Soft — the schema declares the
  requirement, but there is no runtime check that the human phrase
  was actually issued before `gh pr merge` was invoked.
- **Test coverage:** Schema-level only.
- **Gap:** No automated phrase check.
- **OpenHands target:** `AEDPolicy` requires an `auth.phrase` field in
  the `AEDMergeTool` tool call payload before issuing any `gh pr merge`
  command. The phrase string is then logged in the post-tool-use
  audit entry.

## 6. Open questions

1. Should the `AED-RULE-023` pre-post scan be moved into a shared
   helper so that both the cookbook and the OpenHands `AEDCodexPingTool`
   use the same scan logic?
2. Should the `AED-RULE-022` resume-checkpoint rule be promoted to
   runtime enforcement (today it is schema-only)?
3. Should the protection list for `AED-RULE-021` be enumerated
   somewhere machine-readable (e.g., `schemas/aed_protected_prs.json`)
   so the OpenHands `AEDGitHubTool` can read it instead of relying on
   a doc-only list?
