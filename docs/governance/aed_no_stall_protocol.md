# AED No-Stall Lifecycle Protocol (v1)

> **Purpose:** Make phase-header-only final outputs detectable, testable,
> and rejection-prone in downstream harnesses. This document is the
> authority for the no-stall lifecycle vocabulary that the future
> Humphry/Telegram runner will use to resume a long-running PR
> workflow without asking the user to say "Continue".

## 1. Background

PR #404 and prior runs exposed a recurring failure mode: the agent
emits a final response that is **just a phase header** —
`"Starting PHASE 1 — protected-state verification."` or
`"Now PHASE 8 — Codex re-review."` — and then stops. There is no
terminal lifecycle state, no checkpoint, no `next_action`, and no
explicit "I am finished" signal. A future Humphry/Telegram runner
that ingests this message cannot tell whether the work is in
progress, paused, or stuck, and the user has to say "Continue" by
hand to get the agent moving again.

This PR adds a **pure, testable, leaf-package** skeleton
(`aed_lifecycle/`) that future runners can call. It does not wire
into Telegram, Humphry, OpenHands, GitHub webhooks, or live merge
scripts. Wiring comes in a later PR.

## 2. The anti-stall rule

> **A Humphry/Telegram runner MUST classify every final agent
> output using `classify_humphry_message_for_stall`. The runner
> MUST NOT treat a `STALL_*` classification as "done." The runner
> MUST treat an `OK_TERMINAL` classification as the only valid
> "done" signal.**

The classifier emits one of six categories:

| Constant                    | Meaning                                                                                  |
|-----------------------------|------------------------------------------------------------------------------------------|
| `OK_TERMINAL`               | Output carries a recognized terminal lifecycle state — the run is finished (one way or another). |
| `OK_PROGRESS_WITH_NEXT_ACTION` | Mid-phase progress, but explicitly carries `next_action` (and optionally a checkpoint path) so the runner can resume. |
| `STALL_PHASE_HEADER_ONLY`   | Output is just a phase header (`"Starting PHASE 1 — ..."` or `"Now PHASE 8 — ..."`) with no terminal state, no `next_action`, and no checkpoint. |
| `STALL_WAITING_FOR_CONTINUE` | Output is a yes/no continue prompt. |
| `STALL_NO_TERMINAL_STATE`   | Output is a generic progress note with no terminal state and no `next_action`. The runner cannot tell whether the work is done, paused, or stuck. |
| `STALL_NO_CHECKPOINT`       | Output mentions a checkpoint but does not name one explicitly, has no `next_action`, and no terminal state. |

## 3. Examples of invalid phase-header-only output

These are the failure-mode cases the classifier MUST detect.

```text
"Starting PHASE 1 — protected-state verification."
  → STALL_PHASE_HEADER_ONLY

"Now PHASE 8 — Codex re-review."
  → STALL_PHASE_HEADER_ONLY

"Let me poll for Codex response."
  → STALL_NO_TERMINAL_STATE

"Continue? (yes/no)"
  → STALL_WAITING_FOR_CONTINUE

"Wrote checkpoint file but no next_action specified."
  → STALL_NO_CHECKPOINT
```

These are valid outputs:

```text
"MERGED"
  → OK_TERMINAL

"MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
  → OK_TERMINAL

"HOLD_PR_CI_PENDING — bounded polling reached limit"
  → OK_TERMINAL

"PHASE 3 complete. next_action: poll CI status, checkpoint: /tmp/aed/checkpoint.json"
  → OK_PROGRESS_WITH_NEXT_ACTION
```

## 4. Required terminal states

The canonical set is frozen in
`aed_lifecycle/no_stall.py::TERMINAL_LIFECYCLE_STATES`. A state
outside this set is not a terminal state, and the classifier
will not treat it as one.

| State                                              | Category       | Meaning                                          |
|----------------------------------------------------|----------------|--------------------------------------------------|
| `MERGED`                                           | success        | PR has been merged.                              |
| `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`         | success        | All gates green. Awaiting operator's merge authorization phrase. |
| `HOLD_NEW_CODEX_THREAD`                            | hold           | Codex raised a new finding on the current head. |
| `HOLD_CODEX_RESPONSE_PENDING`                      | hold           | Codex ping posted; awaiting response within bounded poll window. |
| `HOLD_PR_CI_PENDING`                               | hold           | CI is running or pending on the PR head. Bounded polling only. |
| `HOLD_PR_CI_FAILED`                                | hold           | A required CI check failed on the PR head. |
| `HOLD_SCOPE_GUARD_FAILED`                          | hold           | Scope guard reported forbidden or out-of-scope files. |
| `HOLD_UNAUTHORIZED_THREAD_INVENTORY`               | hold           | Thread inventory is not authorized to resolve. |
| `HOLD_BRANCH_POLICY_BLOCKED`                       | hold           | Branch policy blocked the action. |
| `HOLD_HEAD_CHANGED`                                | hold           | The PR's headRefOid changed unexpectedly. |
| `HOLD_ISOLATED_WORKSPACE_DIRTY`                    | hold           | The isolated workspace is dirty (uncommitted changes). |
| `HOLD_UNEXPECTED_LOCAL_CHANGES`                    | hold           | Unexpected local changes outside the allowed scope. |
| `HOLD_OPERATOR_REQUIRED`                           | hold           | Stale / incomplete checkpoint. Operator must intervene. |
| `FAILED`                                           | failure        | The run failed; no further action will resume it. |

`is_terminal_lifecycle_state(state)` is a strict predicate: only
the canonical identifiers above are recognized. Abbreviated or
case-mutated forms (`"merge_ready"`, `"MERGE_READY"`, `"merge ready"`)
are rejected.

## 5. Required checkpoint fields

A checkpoint is a pure data record that lets the runner resume
from where it left off. The shape is pinned in
`aed_lifecycle/checkpoint.py::CheckpointState`:

| Field                         | Type                       | Required | Notes |
|-------------------------------|----------------------------|----------|-------|
| `repo`                        | `str`                      | yes      | `owner/repo` |
| `pr_number`                   | `int`                      | yes      | positive int |
| `branch`                      | `str`                      | yes      | feature branch name |
| `current_head`                | `str`                      | yes      | the SHA the runner last observed for the PR head |
| `phase`                       | `Optional[str]`            | yes (None allowed) | the current phase name |
| `completed_phases`            | `List[str]`                | yes (empty allowed) | phases the runner has finished |
| `next_phase`                  | `Optional[str]`            | yes (None allowed) | the phase the runner will execute next |
| `next_action`                 | `Optional[str]`            | yes (None allowed) | the next string the runner is acting on |
| `pending_actions`             | `List[str]`                | yes (empty allowed) | outstanding actions |
| `last_verified_primary_head`  | `Optional[str]`            | yes (None allowed) | the SHA of the protected primary at the last verification |
| `last_verified_pr_head`       | `Optional[str]`            | yes (None allowed) | the SHA of the PR head at the last verification |
| `authorized_thread_ids`       | `List[str]`                | yes (empty allowed) | review-thread IDs the operator has authorized the runner to resolve |
| `unresolved_thread_ids`       | `List[str]`                | yes (empty allowed) | review-thread IDs that were open on the verified head |
| `terminal_state`              | `Optional[str]`            | yes (None allowed) | if set, the checkpoint is parked |
| `updated_at`                  | `Optional[str]`            | yes (None allowed) | ISO-8601 timestamp of the last checkpoint update |

`validate_checkpoint(state)` returns a list of human-readable
error strings. An empty list means the checkpoint is valid.

`next_action_from_checkpoint(state)` returns the string the
runner should act on, or `None` if the checkpoint says to stop.
Stale / incomplete checkpoints return `HOLD_OPERATOR_REQUIRED`,
not a silent continuation.

`checkpoint_requires_operator(state)` returns `True` iff the
checkpoint cannot be safely auto-resumed. Head changes,
unknown terminal states, and stale / empty checkpoints all
require operator intervention.

## 6. How a future runner resumes

The runner's resume loop is a pure function over the checkpoint:

```python
from aed_lifecycle.checkpoint import (
    CheckpointState,
    next_action_from_checkpoint,
    checkpoint_requires_operator,
    validate_checkpoint,
)

def resume(state: CheckpointState) -> str | None:
    errors = validate_checkpoint(state)
    if errors:
        return "HOLD_OPERATOR_REQUIRED"
    if checkpoint_requires_operator(state):
        return "HOLD_OPERATOR_REQUIRED"
    return next_action_from_checkpoint(state)
```

The runner MUST:

1. **Re-verify the head before resuming.** Fetch the current PR
   head SHA and the current protected primary SHA from the
   external source of truth. Update `state.current_head` (and,
   for the primary, pass it through a separate
   `verify_primary_head_unchanged` function — see the watchdog
   expansion in PR #406). If the head moved,
   `checkpoint_requires_operator` returns `True` and the runner
   surfaces `HOLD_HEAD_CHANGED` to the operator.

2. **Classify the final agent output.** The runner ingests the
   agent's final response text into
   `classify_humphry_message_for_stall`. A `STALL_*` result
   is treated as "do not proceed; surface to the operator."

3. **Emit bounded polling results as a `HOLD_*` state.** When
   the polling budget is exhausted, the runner emits
   `HOLD_PR_CI_PENDING` or `HOLD_CODEX_RESPONSE_PENDING`. The
   watchdog's `should_continue_polling` helper guarantees
   that polling can never become unbounded.

4. **Never silently continue.** A checkpoint with no
   `next_action`, no `phase`, and no `terminal_state` returns
   `HOLD_OPERATOR_REQUIRED`, not a default continuation. This
   is the regression guard for the PR #404 failure mode.

## 7. Watchdog model

`aed_lifecycle/watchdog.py::WatchdogState` is a pure data record
for a single phase. The :func:`evaluate_watchdog` helper returns
one of four verdicts:

| Verdict                          | Meaning |
|----------------------------------|---------|
| `OK_TERMINAL`                    | The phase has a recognized terminal state. |
| `WATCHDOG_PROGRESS_REQUIRED`     | Idle time exceeded `max_idle_seconds`. The runner should emit a progress update or a checkpoint. |
| `STALL_RISK`                     | No terminal_state and no checkpoint_path. The runner is at risk of stalling. |
| `OK_PROGRESS_WITH_NEXT_ACTION`   | Mid-phase progress with a `next_action` and a `checkpoint_path`. |

When the phase-time budget (`max_phase_seconds`) is exhausted, the
watchdog recommends a `HOLD_*` state (never a stall):

- `HOLD_PR_CI_PENDING` if the `next_action` mentions CI.
- `HOLD_CODEX_RESPONSE_PENDING` if the `next_action` mentions Codex.
- `HOLD_OPERATOR_REQUIRED` otherwise.

`should_continue_polling(...)` enforces bounded polling. The
helper can return `True` (continue) or a `HOLD_*` state name
(stop). It cannot return `False` and cannot return `True` past
the budget — the runner cannot accidentally enter an unbounded
loop by reading this helper's return value.

## 8. What this PR is not

This PR is a **skeleton**. It does not:

- Wire into Telegram, Humphry, OpenHands, GitHub webhooks, or
  live merge scripts.
- Modify the live merge gate, the Codex classifier, the audit
  appender, the merge guard, the scope guard, or any other
  live tool.
- Replace `scripts/local/aed_lifecycle_states.py`. The CLI
  there reads the JSON registry; the helpers here are
  independent pure logic.
- Implement OpenHands plugin behavior. The plugin comes in a
  later PR.
- Merge, auto-merge, force-push, or touch any production
  branches.

What it **does** do:

- Make phase-header-only final outputs detectable in tests.
- Pin the resume protocol to a documented set of pure helpers.
- Pin the terminal-state vocabulary to a frozen set.
- Pin the checkpoint shape to a frozen dataclass.
- Pin the polling helper to a bounded signature.
- Provide a regression suite that any future runner can use to
  confirm the protocol behaves as documented.

## 9. Future-work handoff (not in this PR)

A future PR will:

- Wire `classify_humphry_message_for_stall` into the Humphry
  command bridge so the runner surfaces `STALL_*` results to
  the operator instead of treating them as a finished run.
- Wire `next_action_from_checkpoint` into the Telegram runner
  so a long-running PR workflow can resume from a checkpoint
  without the user typing "Continue."
- Add a `verify_primary_head_unchanged` helper that takes the
  current primary SHA and returns `True` iff the protected
  primary has not moved since the checkpoint was taken.
- Add `HOLD_MAIN_HEAD_MISMATCH` and other registry states
  (where applicable) to `TERMINAL_LIFECYCLE_STATES` if and only
  if the AED lifecycle state registry PR is updated to add
  them. The current set is intentionally limited to the
  PR #405 spec.

Until that future PR lands, this skeleton is preparation, not
integration. The regression test suite is the deliverable.
