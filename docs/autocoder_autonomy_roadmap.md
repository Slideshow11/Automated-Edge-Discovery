# AED Autocoder Autonomy Roadmap

**Date:** 2026-06-01
**Base:** main at `dc12a4568efff8cfd784dc7d6911b31207cf5cf3` (post PR #376 squash)
**Authoring model:** M3 (MiniMax-M3 from MiniMax) — bounded implementation worker
**Classification:** Strategic planning, docs-only
**Companion docs:** `autocoder_roadmap.md` (2026-05-24, tactical next-PR plan), `autocoder_architecture_audit_2026_05.md` (drift audit), `autonomy_friction_log.md` (incident log)

This document sets the autonomy direction for the AED autocoder stack. It
is intentionally separated from the tactical `autocoder_roadmap.md` so
that long-term direction does not drift with the next 1-2 PRs. Where
they overlap (e.g. P3 task corpus), the tactical document is the source
of truth for scope; this document is the source of truth for **why** and
**what is deferred**.

---

## 1. Current diagnosis

Recent external and internal reviews converge on the same picture.

**What AED is already good at:**
- Containment. Primary worktree is read-only during autocoder runs.
  All execution happens in detached-HEAD worktrees under
  `/tmp/aed_runs/`. PMG and scope_guard wrap every run.
- Governance. 10 governance validators, 918 CI-enforced tests,
  16 total validators, fail-closed on unhandled cases.
- Review gates. The CI review-comment-gate job runs
  `check_pr_review_comments.py` on every PR. Bot-thread eligibility
  is now decidable via two complementary checkers
  (`check_stale_review_thread_resolution.py` and
  `check_current_bot_thread_resolution.py`).
- Exact-head merge. `check_merge_authorization.py` requires a human
  phrase including the exact full 40-character SHA.
  `gh pr merge --match-head-commit` is the only allowed merge form.
- Fail-closed. Every unhandled case returns a `HOLD_*` state, never a
  permissive default.

**What AED is NOT yet:**
- Proven as an independent autocoder. Real-output patch quality is not
  measured enough. The corpus is mock-only. A `promising_for_review`
  label is a review-readiness signal, not a quality verdict.
- Truly any-model. Execution is still provider/backend-specific.
  The `execution.mode` field is `mocked` by default. The live
  executor is gated behind `--enable-real-claude-executor` (False),
  and the actual `run_claude_executor()` raises
  `"claude mode not yet ready"` if the flag is True.
- Operationally autonomous. The multi-agent role design
  (Tasker/Executor/Specifier/Builder/Reviewer) is design-only. Only
  the read-only deterministic packet scaffolds have been implemented.

**The goal of the next phase is not unrestricted autonomy.** It is
**bounded autonomy under tight containment**, where:
- Implementation workers are **untrusted and replaceable**. Any single
  worker can be swapped without changing the harness.
- The **guarded flow remains merge authority**. A worker produces a
  patch artifact. The guarded flow decides whether that patch becomes
  a merged commit.
- **Containment precedes capability.** Every new capability is added
  inside the existing worktree + scope + PMG envelope, not as a
  side-channel.

This diagnosis is consistent with the existing
`autocoder_architecture_audit_2026_05.md` drift classification:
`STRONG_BUT_NEEDS_EVAL_LAYER`. The scaffold is sound; the
**measurability of real output** is the missing piece.

---

## 2. Product definition

**An independent AED autocoder is a system that can:**

1. **Produce a merge-ready patch artifact** inside a sandbox or temp
   worktree, starting from a structured task packet and ending with a
   diff + evidence packet under `/tmp/aed_runs/<run_id>/`.
2. **Run deterministic local checks** (scope_guard, PMG, observation
   table audits, validators on changed files) and exit with a
   structured pass/fail status.
3. **Produce reviewable evidence**: a `final_review_packet.json` with
   the full stages-completed list, the changed-files set, the
   config hash, and the ledger entries.

**An independent AED autocoder must NOT be able to:**

- Mutate the primary worktree, the GitHub repository, branch
  protection, workflows, or the Hermes tree outside an explicit
  allowlist.
- Bypass scope_guard, PMG, the review-comment gate, or the
  merge-authorization check.
- Treat any reviewer's comment (human or bot) as auto-resolvable.
  Bot review resolution requires checker evidence
  (`check_stale_review_thread_resolution.py` or
  `check_current_bot_thread_resolution.py`).

**Merge authority is the guarded flow, never the worker.** The merge
step requires three things to all be true:
1. `run_guarded_pr_flow.py` produced `READY_TO_MERGE_CANDIDATE`.
2. The authorization phrase includes the exact full 40-char SHA.
3. The merge command uses `--match-head-commit <sha>` so GitHub
   rejects the merge if the head has moved.

The worker never runs `gh pr merge`. The worker never runs
`gh pr create` in autonomous mode. The worker never pushes.

---

## 3. Non-negotiable invariants

These are the conditions any future PR must preserve. If a PR
violates any of these, the PR is rejected regardless of merit.

| # | Invariant | Why |
|---|---|---|
| 1 | No primary worktree mutation | The main repo is the trust anchor |
| 2 | Temp worktrees only (`/tmp/aed_runs/...`) | Contained blast radius |
| 3 | No unauthorized GitHub mutation | No `gh api` calls outside allowlist |
| 4 | No workflow mutation | CI YAML is human-authored and reviewed |
| 5 | No branch protection mutation | Branch protection is policy, not feature |
| 6 | No Hermes mutation outside PMG allowlist | Default-deny for skills/config/memory |
| 7 | No `--admin` | Force-pushes and bypasses are never the answer |
| 8 | No `--auto` | No autonomous merge / no autonomous PR |
| 9 | Exact `--match-head-commit` on every merge | Guard against head drift |
| 10 | Review-thread gate (CI job `review-comment-gate`) | No P1/P2 blocker ignored |
| 11 | Human-authored review remains protected | Human reviewers can never be auto-resolved |
| 12 | Bot review resolution only through explicit checker eligibility or approved policy | Two-checker gate, no ad-hoc resolve |
| 13 | `scope_guard` is mandatory on every PR | Allowed-files is a hard contract |
| 14 | `PMG` is mandatory on every worker run | Snapshot/diff/block Hermes state |
| 15 | Implementation worker is never merge authority | Worker produces artifact, guarded flow merges |
| 16 | No live Claude without 12+10+22 readiness gate implemented | Live executor is a separate phase, gated |
| 17 | No package install from worker subprocesses | 22-forbidden-pattern scan |
| 18 | No git push from worker subprocesses | 22-forbidden-pattern scan |
| 19 | Subprocess commands built as argv lists, no shell-equals-True form | Shell-injection prevention |
| 20 | All packet kinds listed in `aed_tasker_executor_design.md` §8 or have a documented design | Packet-kind sprawl is unconstrained JSONL |

These invariants are also enforced mechanically where possible:
- `validate_ci_workflow_invariants.py` checks 17 CI YAML invariants.
- `scope_guard.py` checks allowed/forbidden file lists.
- `check_persistent_mutation_guard.py` checks Hermes tree diff.
- `check_merge_authorization.py` checks exact-SHA + phrase.

The PR review checklist (Appendix D of
`/home/max/aed-reviews/2026-06-01/aed-autocoder-full-writeup.md`) maps
PRs to which invariants they touch.

---

## 4. Model roles

The current intended role assignment:

| Role | Model | Scope |
|---|---|---|
| **Bounded implementation worker** | MiniMax-M3 (current) | One PR, one worktree, mock execution by default |
| **Long-running implementation worker** | Claude Code | **Only inside harness, only when 12+10+22 readiness gate is fully implemented** |
| **Long-running implementation worker (alt)** | Open-weight model (e.g. Llama 3 70B) | **Only inside harness, only when ExecutionBackend contract supports it** |
| **Auditor** | M3 (MiniMax-M3) | Read-only analysis, evidence generation, gate evaluation |
| **Gate runner** | Humphry / M3 | Executes `run_guarded_pr_flow.py`, `wait_for_pr_ready.py`, scope_guard, PMG |
| **PR controller** | Humphry / M3 | Watches PR state, runs deterministic checks, emits gate state |
| **Merge authority** | Guarded flow | `run_guarded_pr_flow.py` + `check_merge_authorization.py` + human phrase + `--match-head-commit` |
| **Merge executor** | Human (operator) | Runs `gh pr merge` after gate authorizes |

**Model output is never trusted without deterministic checks.** Every
model-produced artifact (patch, evidence packet, gate state) is
re-validated by `scope_guard`, `PMG`, and the relevant
observation-table or validator.

**Model replaceability is a feature.** The bounded implementation
worker is replaceable. The harness contract is the only thing that
matters: the contract specifies the task packet in, the patch
artifact out, and the deterministic checks in between. Any model that
satisfies the contract is acceptable.

**Why M3 currently:** deterministic, auditable, fast, and the
reviewer-archive-writing task that produced this document is exactly
the kind of bounded implementation work M3 is suitable for. The
harness should be tested with M3 first; other models come later when
the ExecutionBackend contract (P5) makes the swap a one-line change.

---

## 5. Roadmap

Priority groups. P0 is in flight now. P1 is this document. P2-P15 are
sequenced by dependency, not by date. Each item lists the artifacts
to produce; the tactical `autocoder_roadmap.md` is the source of
truth for individual PR scope.

### P0: Finish current PR flow

In-flight. **Do not block on this roadmap landing.**

- Finish PR #377 (current-head bot checker + tests).
- Resolve only **eligible** outdated bot threads via the
  `check_current_bot_thread_resolution.py` policy.
- Run `wait_for_pr_ready.py` → `READY_TO_MERGE_CANDIDATE`.
- Merge with exact head via guarded flow
  (`gh pr merge <n> --match-head-commit <sha> --squash --delete-branch`).
- Post-merge CI audit on main.

### P1: Save this roadmap

**This document.** Single deliverable. Depends on: nothing. Blocks:
P2-P15 (sequence justification).

### P2: Post-merge CI audit helper

A small read-only script that, after a merge to main, audits whether
all required CI jobs ran for the merge commit and reports
green/pending/failed counts. Fails closed if any required job
unexpectedly missing.

- `scripts/local/audit_main_ci_for_head.py`
- `tests/test_audit_main_ci_for_head.py`

### P3: Real-output autocoder evaluation corpus

The single most important gap per the architecture audit. Move
from "corpus tests the pipeline" to "corpus tests the output
quality."

- `corpus/autocoder-real-output-v0.json` — task packets with
  real LLM execution when live is enabled
- `scripts/local/run_autocoder_real_output_eval.py` — runner
- `tests/test_run_autocoder_real_output_eval.py` — tests
- Documentation: what metrics are scored, what threshold
  constitutes "useful"

**Until this exists, no claim of "useful autocoder" can be made.**

### P4: HOLD state truth matrix

A documents-and-script pair that produces the **complete table**
mapping every `HOLD_*` state in the codebase to:
- the script that emits it
- the file path and line number
- the operator-actionable remediation
- the corresponding `PATCH_READY_*` / `READY_TO_MERGE_*` success
  state it blocks

- `scripts/local/audit_hold_state_coverage.py`
- `tests/test_audit_hold_state_coverage.py`
- `docs/hold_state_truth_matrix.md`

This is the "runtime truth matrix" the latest review asked for.
Currently we know there are 40+ HOLD states, but they are spread
across 15+ scripts with no single source of truth. This fixes that.

### P5: ExecutionBackend contract

The abstraction that makes execution provider-portable. A single
`ExecutionBackend` interface that any model worker (Claude Code,
open-weight, mock) implements. The single-task controller then
depends on the interface, not the implementation.

- `scripts/local/execution_backends.py` — interface + mock impl +
  Claude Code stub + open-weight stub
- `tests/test_execution_backends.py`

This is what the Perplexity review identified as the missing
"any-model" abstraction. Without it, every new model is a port.

### P6: Mock-first long-coder harness

A harness that simulates a long-running implementation worker in
mock mode. Tests the 7-stage pipeline under sustained use. Reveals
state-machine bugs that short-corpus tests miss.

- `scripts/local/run_long_coder_worker.py`
- `tests/test_run_long_coder_worker.py`

Distinct from P3 (real-output corpus) — P6 is about *long* runs,
P3 is about *real* outputs. Both are needed.

### P7: Transcript reviewer

A read-only review tool for the transcript log produced by the
future live executor. The live readiness gate requires
`HOLD_CLAUDE_TRANSCRIPT_MISSING` to be a recognized state. This is
the tool that surfaces and reviews that transcript.

- `scripts/local/review_coder_transcript.py`
- `tests/test_review_coder_transcript.py`

### P8: ModelProfile abstraction

Per-model configuration: rate limits, retry behavior, token budget,
response parsing quirks, model-specific failure modes.

- `scripts/local/model_profiles.py`
- `tests/test_model_profiles.py`
- `docs/model_profile_policy.md`

P5 is the *interface*. P8 is the *configuration*. P5 says "any
backend works"; P8 says "and here's how to talk to each one
safely."

### P9: Sandbox readiness checker

The design doc says: "The 12+10+22 readiness gate must be fully
implemented before live execution is enabled." This is the
checker that verifies all 12+10+22 are actually in place before
the `--enable-real-claude-executor` flag can be flipped.

- `docs/live_execution_sandbox_readiness.md` — design doc
- `scripts/local/check_live_sandbox_readiness.py` — the checker
- `tests/test_check_live_sandbox_readiness.py`

The checker must be runnable today and must return
`SANDBOX_NOT_READY` for every line item, with the path-to-ready
documented in the report.

### P10: Seven-stage state machine schema

Codify the 7-stage pipeline as a formal state machine with explicit
transitions, guard conditions, and side effects.

- `schemas/autocoder_state_machine_v0.schema.json`
- `configs/autocoder_state_machine_v0.json` — the actual
  declaration
- `scripts/local/validate_autocoder_state_machine.py` — validator
- `tests/test_validate_autocoder_state_machine.py`

This is what the Perplexity review identified as the missing
"state machine" abstraction. Without it, the 7 stages are spread
across `run_autocoder_single_task.py` lines 445-867 with no
single declaration.

### P11: Packet schema cleanup

Codify the 12 packet kinds documented in
`aed_tasker_executor_design.md` §8 as actual JSON schemas. Currently
most packets are documented but not validated. Add:

- `schemas/aed_tasker_report_v1.schema.json`
- `schemas/aed_executor_plan_v1.schema.json`
- `schemas/aed_pr_gate_state_v1.schema.json`
- `schemas/aed_review_packet_v1.schema.json`
- `schemas/aed_autocoder_single_task_v0.schema.json`
- `schemas/aed_pr_gate_review_evidence_v1.schema.json` (already
  exists in the design doc, codify it)
- `schemas/aed_pr_gate_merge_ready_notification_v1.schema.json`
- Plus packet schema tests

Plus a `validate_packet_kind.py` that validates any packet against
its declared kind.

### P12: Batch controller hardening

- Pin controller SHA explicitly (PR #314 P2 fix)
- Pin executor SHA explicitly
- Restartability: if the batch controller crashes mid-batch, the
  next invocation can resume from the last-completed task
- Cleanup: drop completed worktrees automatically after N days
- **No parallelism yet** — sequential batch is sufficient and
  easier to reason about

### P13: Stale worktree cleanup helper

70+ orphaned worktrees accumulate in `/tmp/aed_runs/` from test
runs. Operator ergonomics issue, not a safety issue. A small audit
script that lists them and a separate `--apply` flag that removes
them.

- `scripts/local/audit_stale_aed_worktrees.py`
- `tests/test_audit_stale_aed_worktrees.py`

### P14: Current-head bot checker integration

Integrate the `check_current_bot_thread_resolution.py` evidence into
the guarded flow. The checker is already in the repo (PR #377);
this is the wiring step. **Keep mutation separate and explicit:**
the checker produces evidence, the guarded flow consumes the
evidence, the merge authorization step is the only place that
actually calls the GraphQL thread-resolution mutation.

### P15: Multi-agent runtime later

The Tasker/Executor/Specifier role design is preserved
(`docs/aed_tasker_executor_design.md`) but **do not operationalize
the full multi-agent runtime** until:
- Real-output corpus (P3) shows that single-agent output is useful
- HOLD state truth matrix (P4) shows the operator is not
  overwhelmed
- ModelProfile (P8) shows model swap is safe
- Multi-agent metrics (roadmap adoption rate, recommendation hit
  rate, Reviewer reversal rate, etc.) are measurable

If the multi-agent runtime is operationalized prematurely, the team
will be debugging role handoff and merge authority before
understanding whether single-agent output is even useful.

---

## 6. Do not build yet

These are explicitly deferred. Any PR that adds these without a
separate design doc and a separate roadmap revision is rejected.

- **Full MCP rewrite.** Adding a model-context-protocol layer
  before the basic patch-quality loop is unproven.
- **Vector memory layer.** Persistent semantic memory across
  sessions is a separate problem. PMG already blocks
  `~/.hermes/memory/`, so this is also a safety question.
- **Autonomous roadmap agent.** The Tasker is not a "next-best-PR"
  recommender running on a schedule. It is a packet scaffold for
  human review. Do not loop it.
- **Parallel versions.** Sequential batch is sufficient. Adding
  parallelism now would require thinking about resource isolation
  before we have a clear picture of typical batch sizes.
- **Live Claude overnight worker.** The 12+10+22 readiness gate is
  not implemented. An overnight worker that runs before the gate
  is implemented is a different proposal that needs its own
  design.
- **Open-weight overnight worker.** Same as above. The
  ExecutionBackend contract (P5) is the prerequisite.
- **MicroVM execution.** A more aggressive isolation model than
  worktrees. May be useful for live execution, but only after
  P5/P9.
- **Autonomous PR chains.** A PR that opens another PR. The
  guarded flow requires a human phrase for every merge; a chain
  that bypasses that is a different design.
- **Broad autonomous merge authority.** Any change to the merge
  authority boundary. The guarded flow is the merge authority
  for the foreseeable future.
- **Automatic replacement of human review.** Human review remains
  protected per invariant #11. The two bot checkers exist to
  resolve bot comments, not human ones.

---

## 7. Acceptance standard for future autonomy

Every new autonomy feature must satisfy all of the following before
it is allowed to land:

1. **JSON/Markdown artifacts.** Every state the feature can be in
   has a JSON schema and a human-readable Markdown explanation.
   No "the script returns whatever it returns."
2. **Exact allowed mutation list.** Every mutation path the feature
   enables has a positive allowlist. Default deny.
3. **Worker wrapped by harness.** Every live worker is run inside
   `run_long_coder_worker.py` (P6) or its successor. Bare subprocess
   invocations from anywhere else are not acceptable.
4. **Resolved bot threads have checker evidence.** Every
   thread-resolution mutation must reference either a
   `check_stale_review_thread_resolution.py` or
   `check_current_bot_thread_resolution.py` evidence packet.
5. **Merge uses waiter and exact head.** Every `gh pr merge` call
   uses `wait_for_pr_ready.py` first and `--match-head-commit`
   second. No exceptions.
6. **Real-output tasks contribute to eval metrics.** Every
   real-output task packet is added to the corpus (P3) and the
   resulting patch is graded. No "fire and forget."

If any of these six is missing, the feature is not ready.

---

## 8. Review sources summarized

This roadmap synthesizes three reviews.

**Gemini review (external).** Useful long-term threat model,
especially around sandboxing, model routing, and MCP. **Too
aggressive for the next PRs** — many of the items it suggests
require designing systems that don't yet exist. P9 (sandbox
readiness) and P5 (ExecutionBackend contract) capture the
survivable parts.

**Perplexity review (external).** **Strongest near-term
implementation roadmap.** Specifically identified:
- The eval-layer gap (P3)
- The ExecutionBackend contract (P5)
- The state machine (P10)
- The ModelProfile abstraction (P8)

P3, P5, P8, P10 in this document are direct responses to that
review.

**Latest internal review (the comprehensive writeup at
`/home/max/aed-reviews/2026-06-01/aed-autocoder-full-writeup.md`).**
**Strongest diagnosis.** The headline is: "the next move is proof
of usefulness under containment, not a docs/runtime truth matrix
that doesn't yet exist." P2, P4, P13, P14 are direct responses to
the gaps the comprehensive writeup identified.

The three reviews do not conflict. They layer:
- Perplexity says **what to build** (eval layer, ExecutionBackend,
  state machine, ModelProfile)
- Gemini says **what threat model to keep in mind** (sandboxing,
  routing, MCP)
- Internal review says **what is the actual current state**
  (multi-agent design exists but is design-only, real-output
  quality is unmeasured, governance is sound)

This document is the synthesis.

---

## 9. Next 5 PRs

The next five PRs, in order, are:

1. **Finish PR #377 and post-merge audit.** P0. Resolves the
   `check_current_bot_thread_resolution.py` policy and lands it.
2. **Save this roadmap.** P1. This document. Single deliverable.
3. **Post-merge CI audit helper.** P2. Small read-only script.
   Establishes the pattern that post-merge audits are part of the
   standard flow.
4. **Real-output autocoder eval corpus v0.** P3. The most
   important gap. No claim of "useful autocoder" can be made
   without this.
5. **HOLD state truth matrix.** P4. Establishes a single source
   of truth for the 40+ HOLD states. Makes operator diagnosis
   possible.

After these five, the next five depend on what P3 (the eval
corpus) shows. If real-output is useful, the team can move to P5
(ExecutionBackend) and P8 (ModelProfile) and unlock multi-model
support. If real-output is not useful, the team has bigger
problems and the roadmap needs revision.

The roadmap is intentionally a 15-item sequence with explicit
"do not build yet" at item 6. Future revisions to this document
should preserve that structure: now, next, later, never.

---

**End of roadmap.**
