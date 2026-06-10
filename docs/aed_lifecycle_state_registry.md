# AED Lifecycle State Registry

**Date:** 2026-06-09
**Classification:** GOVERNANCE_TOOLING
**Status:** ACTIVE
**Companion to:** `docs/aed_whole_workflow_operator_path.md`, `docs/aed_known_safe_command_cookbook.md`, `docs/merge_authorization_guard.md`

---

## 1. Purpose

This document introduces the canonical AED lifecycle state registry
and its companion CLI. The registry centralizes the lifecycle vocabulary
used across the operator path, the command cookbook, the merge
authorization guard, and the post-merge closeout pipeline. The CLI is
a small stdlib-only reader and validator that lets future helpers,
agents, and reviewers look up the meaning of any state without
parsing ad-hoc strings from prompt text.

The registry is **reporting vocabulary, not a runtime state machine**.
No script in the repo currently executes state transitions against it.
The registry exists so that when an agent, a codex review, a human
authorization prompt, or a future tool talks about
`MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`, all participants agree on
what that state means, what evidence is required, and which mutations
are allowed or forbidden.

---

## 2. Registry file path

```
schemas/aed_lifecycle_states_v1.json
```

This is the canonical machine-readable source. The CLI resolves the
path relative to its own location; tests resolve the same path
relative to the repo root.

The registry has the following top-level shape:

| Field             | Type          | Notes                                                     |
|-------------------|---------------|-----------------------------------------------------------|
| `schema_version`  | int           | Currently `1`.                                           |
| `registry_kind`   | string        | Must be `aed.lifecycle_state_registry.v1`.                |
| `categories`      | string list   | Valid category values. Must include all five.            |
| `description`     | string        | Free-form description of the registry's purpose.          |
| `states`          | object        | Map of state name to entry.                               |

Each state entry contains:

- `category`
- `description`
- `evidence_required`
- `allowed_next_states`
- `allowed_mutations`
- `forbidden_mutations`
- `human_authorization_required`
- `merge_allowed`
- `closeout_allowed`
- `notes`

---

## 3. CLI usage

The CLI lives at `scripts/local/aed_lifecycle_states.py` and is
**stdlib-only**. It does not call LLMs, does not mutate GitHub, does
not require any network access beyond the local filesystem.

### 3.1 List canonical state names

```
python3 scripts/local/aed_lifecycle_states.py --list
```

Prints one state name per line, in registry-insertion order.

Add `--json` to print the list as a JSON object:

```
python3 scripts/local/aed_lifecycle_states.py --list --json
```

### 3.2 Print a single state entry

```
python3 scripts/local/aed_lifecycle_states.py --state HOLD_PR_CI_PENDING
```

Prints the entry as a JSON object keyed by the state name. Exits
nonzero if the state is not registered.

### 3.3 Validate the registry

```
python3 scripts/local/aed_lifecycle_states.py --validate
```

Exits 0 on success and prints `registry validation PASSED`. Exits
nonzero with a list of validation errors otherwise. The validator
checks:

- top-level schema fields
- every required field is present on every state
- every state category is from the registry's `categories` list
- every allowed and forbidden mutation token is from the known set
- no state has the same mutation in both allowed and forbidden lists
- `merge_allowed: true` only on
  `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`
- terminal states have empty `allowed_mutations`, empty
  `allowed_next_states`, and `merge_allowed: false` /
  `closeout_allowed: false`
- any state that permits a guarded mutation (merge or
  `thread_resolve`) must set `human_authorization_required: true`
- every state referenced in `allowed_next_states` is a known
  canonical state

### 3.4 Print the full registry

```
python3 scripts/local/aed_lifecycle_states.py --all
```

Prints the full registry as JSON.

---

## 4. State categories

The registry defines five categories. Every state belongs to exactly
one.

| Category          | Meaning                                                                                  |
|-------------------|------------------------------------------------------------------------------------------|
| `hold`            | The PR run is stopped. Operator must investigate or wait.                                |
| `ready`           | All preconditions hold and a guarded mutation is now permitted with human authorization.|
| `mutation_pending`| A guarded mutation is in progress (e.g. threads being resolved, audit being appended).   |
| `terminal`        | The PR run is complete. No further mutations are required.                               |
| `informational`   | A label that names a stage but does not gate any mutation.                                |

The canonical states covered in v1 are:

```
NOT_RUN
HOLD_MAIN_HEAD_MISMATCH
HOLD_HEAD_CHANGED
HOLD_PR_CI_PENDING
HOLD_PR_CI_FAILED
HOLD_CODEX_RESPONSE_PENDING
HOLD_NEW_CODEX_THREAD
HOLD_NEW_ACTIVE_THREAD
CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED
MERGE_READY_AWAITING_HUMAN_AUTHORIZATION
HOLD_MERGE_STATE_BLOCKED
HOLD_PRE_MERGE_CONDITION_FAILED
HOLD_POST_MERGE_CI_PENDING
HOLD_POST_MERGE_CI_FAILED
HOLD_POST_MERGE_CI_NOT_OBSERVED
AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR
PR_MERGED_PENDING_CLOSEOUT
PR_MERGED_AND_CLOSED_OUT
```

---

## 5. Evidence model

Each state declares the evidence an operator or agent should have on
hand before claiming that state. The evidence list is **advisory**:
the CLI does not require callers to supply the evidence; it only
records what the canonical state considers sufficient.

Examples:

- `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` requires
  `pr_number`, `head_sha`, `merge_state_status=CLEAN`,
  `all_required_ci_passed`, `codex_clean_pass_for_current_head`,
  `all_threads_resolved_or_outdated`.
- `PR_MERGED_AND_CLOSED_OUT` requires
  `pr_number`, `merge_sha`, `head_sha`, `merged_at`,
  `audit_log_line_index`, `worktree_removed`.

---

## 6. Allowed transitions

Each state lists the states it may legally transition to. The list is
not a runtime-enforced DAG; it is documentation. A reader can use
`allowed_next_states` to decide whether a given transition is in the
intended flow.

Terminal states have an empty `allowed_next_states` list. The
validator enforces this.

---

## 7. Mutations and authorization

Each state lists:

- `allowed_mutations`: the only mutations that the state considers
  permissible.
- `forbidden_mutations`: mutations the state explicitly forbids. This
  is documentation; the registry is not the policy enforcement layer.
- `human_authorization_required`: true if and only if a guarded
  mutation (merge, resolve-only) is permitted by the state.
- `merge_allowed`: true only on
  `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`. The validator enforces
  this as a policy invariant.
- `closeout_allowed`: false on every state in v1, since closeout is
  never an autonomous action.

The registry is not an authority grant. It does not perform any
mutation. The merge command itself is still gated by
`docs/merge_authorization_guard.md`, which requires the exact live
40-character head SHA in the human authorization phrase.

---

## 8. Relationship to other governance docs

The registry is a vocabulary layer that sits below the operator path
and the command cookbook. Both of those documents use the state names
declared here; both should be updated if a state is added, renamed,
or removed.

| Document                                            | Role                                                   |
|-----------------------------------------------------|--------------------------------------------------------|
| `docs/aed_whole_workflow_operator_path.md`         | Describes the operator path. Uses the state names.     |
| `docs/aed_known_safe_command_cookbook.md`          | Documents the command shapes. Uses the state names.    |
| `docs/merge_authorization_guard.md`                | Defines the merge authorization phrase.                |
| `docs/phase_ledger_merge_readiness_wrapper.md`     | Documents the phase-ledger integration.                |
| `docs/pr_review_comment_gate.md`                   | Defines the review-comment gate.                       |
| `schemas/aed_lifecycle_states_v1.json`                   | The registry itself.                                   |
| `scripts/local/aed_lifecycle_states.py`            | The reader / validator CLI.                            |
| `tests/test_aed_lifecycle_states.py`               | Tests for the registry and CLI.                        |

---

## 9. PR-specific final-report labels

Each PR closeout produces a final-report label such as
`PR_395_MERGED` or `PR_396_MERGED`. These are **report labels**, not
canonical lifecycle states. They are derived from the terminal state
`PR_MERGED_AND_CLOSED_OUT` plus the merged PR number.

The registry deliberately does not create one entry per PR. The
report label is computed by the closeout pipeline from the canonical
terminal state and the PR number; the registry stays small and
canonical.

---

## 10. Append-only closeout rule (v1, codified 2026-06-10)

The AED merge-action audit log is append-only. The rule is stated
here once in the registry vocabulary so that every consumer of the
lifecycle states — the operator path, the command cookbook, the
audit appender, the audit validator, and the closeout pipeline —
points to the same canonical policy.

**The rule.** Once an audit entry is appended to the audit log at
`~/.hermes/aed/audit/log.jsonl`, do not delete, trim, rewrite, or
replace it unless the human operator **explicitly** authorizes that
exact audit-log mutation. There is no blanket agent authority to
rewrite audit history. "Looks wrong" is not authorization.

**Alias.** The reporting vocabulary includes an alias,
`AUDIT_APPEND_NEEDS_OPERATOR`, for the situation where a
previously-appended audit entry is found to be malformed,
non-canonical, incomplete, or suboptimal. The alias is a synonym
for `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` — the registry stores a
single canonical state and treats the alias as a reporting label.
The validator does not currently resolve the alias because aliases
are a reporting convenience, not a structural feature of the
registry.

**Operator decision tree.** When an audit entry is suspected to be
malformed, non-canonical, incomplete, or suboptimal after append,
the operator must follow this decision tree in order:

1. **First** run the repo-standard audit validator
   (`scripts/local/validate_merge_action_audit_log.py`). The
   validator's non-strict mode (with `--allow-legacy`) records
   warnings; strict mode is the gating signal.
2. **If validation fails**, stop and report an audit hold. Do
   not amend the entry. Do not "fix and retry" the closeout.
3. **If validation passes but the entry is non-canonical**, do
   not rewrite it. A non-canonical but valid entry stays in
   the log.
4. **Append a corrective follow-up entry** *only* if the repo
   audit policy explicitly supports corrective entries. The
   current trace policy (see `docs/trace_policy_v1.md` §6 Trace
   Completeness Rule) requires every entry to be complete at
   emit time, so a corrective append is permitted only when the
   policy explicitly authorizes it for the specific defect.
5. **Otherwise** stop and report `AUDIT_APPEND_NEEDS_OPERATOR`
   (alias of `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR`). Human
   operator decision is required.

**Forbidden mutations while in this state.** The state
`AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` already forbids
`pr_merge`, `admin_merge`, and `auto_merge`. The append-only
closeout rule additionally forbids, while an audit-ambiguity
hold is in effect:

- `comment_delete` — comments may not be deleted to suppress
  evidence of the ambiguity.
- `review_dismiss` — reviews may not be dismissed for the same
  reason.
- `force_push` — history may not be rewritten on any branch
  involved in the closeout.

These are encoded in the registry entry's `forbidden_mutations`
list. The audit-log-mutation prohibition itself
(`audit_delete`, `audit_rewrite`, `audit_trim`, `audit_replace`)
is encoded in the entry's `description` and `notes` because the
validator's `VALID_MUTATIONS` set is a vocabulary of allowed
repository-side actions, not a vocabulary of audit-log row
operations; the policy text is the authoritative surface for the
audit-log-mutation rule.

**Reference.** `docs/merge_action_audit_log.md` §Append-only
("Do NOT edit or delete existing audit rows. The append-only
nature of the log is what makes it auditable."), and
`docs/trace_policy_v1.md` §6 Trace Completeness Rule.

**Why this state, not a new one.** A separate
`AUDIT_APPEND_NEEDS_OPERATOR` state would duplicate the
canonical entry and introduce a stale reporting alias with no
structural support. The single canonical state
`AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` covers both the "could not
append" case and the "appended entry needs operator review"
case, and the alias is documented in the entry's description and
notes for consumers that prefer the shorter name.

---

## 11. How future helpers should consume the registry

Future helpers and tools that need to look up a state should:

1. Load the registry from
   `schemas/aed_lifecycle_states_v1.json`. The CLI's `--state <NAME>`
   shape is a useful reference for what a lookup returns.
2. Treat the registry as a source of vocabulary and policy
   expectations, not a runtime state machine.
3. Never depend on the registry to authorize a mutation. The
   merge authorization guard, the resolve-only policy, and the
   audit append helper each have their own authorization rules.
4. When introducing a new state, add it to the registry in the same
   PR that introduces the script or doc that uses it. Update the
   operator path and command cookbook cross-references in the same
   change set.
5. Run `python3 scripts/local/aed_lifecycle_states.py --validate`
   as part of CI for any PR that touches the registry.

---

*This document is the v1 of the lifecycle state registry. Revisions
should be additive where possible, with deprecation notes appended
in the registry's `notes` field for any state that is no longer
recommended.*
