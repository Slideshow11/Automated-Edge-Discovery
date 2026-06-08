# Phase-Ledger Merge-Readiness Wrapper

## Purpose

This document is the operator guide for the opt-in wrapper introduced by
[PR #393](https://github.com/Slideshow11/Automated-Edge-Discovery/pull/393):

```
scripts/local/merge_readiness_with_phase_ledger.py
```

The wrapper composes the phase-ledger final-gate adapter
(`scripts/local/finalize_with_phase_ledger.py`, PR #392) with the existing
merge-readiness orchestrator (`scripts/local/merge_pr_safely.py`) so that
merge-readiness output is only emitted when the runner-produced phase ledger
covers the live PR head. The wrapper never merges. A human must run
`gh pr merge` (or use the standard AED merge authorization flow) to actually
land a PR.

## Stack summary (PRs #390 – #393)

| PR | Commit | Component | Role |
|----|--------|-----------|------|
| #390 | `04cb934` | `scripts/local/phase_ledger.py`, `scripts/local/phase_exec.py` | Phase execution ledger guard infrastructure |
| #391 | `bf44200` | `scripts/local/run_autocoder_single_task.py` | Runner now emits `run_summary.json` with `phase_ledger_path`, `phase_ledger_expected_run_id`, and `phase_ledger_claimed_phases` fields when phase-ledger support is enabled |
| #392 | `7f7cb30` | `scripts/local/finalize_with_phase_ledger.py` | Leaf adapter (`run_finalize()`) that consumes the runner's phase ledger as part of the final-gate decision |
| #393 | `6beb337` | `scripts/local/merge_readiness_with_phase_ledger.py` | Opt-in wrapper that gates `merge_pr_safely.py` on the phase-ledger evidence and binds every read-only `gh pr view` recheck to the same `expected_head_sha` |

Each PR is small, additive, and covered by its own pytest suite. The
wrapper added in PR #393 has 46 dedicated tests in
`tests/test_merge_readiness_with_phase_ledger.py`.

## Operator model

The wrapper is **opt-in**. The default merge-readiness path remains
`scripts/local/merge_pr_safely.py`; the wrapper is only relevant when an
operator has a runner-produced `run_summary.json` for the PR in question.

Five invariants govern the operator model:

1. The wrapper is opt-in via `--run-summary`. Omitting the flag is a
   no-op pass-through to `merge_pr_safely.py`.
2. The wrapper never merges. It only produces or refuses merge-readiness
   output. Human merge authorization remains required.
3. The wrapper fail-closes on every check. It does not return success
   unless every binding step agrees that the live PR head is the same
   SHA the runner-produced phase ledger covered.
4. The wrapper never invokes `gh pr merge`, `gh pr create`, or
   `gh pr edit`. The only `gh` command it issues is a read-only
   `gh pr view --json headRefOid --jq .headRefOid`, bounded by a 30-second
   timeout.
5. The wrapper hard-rejects `--admin` and `--auto` at argparse time and
   via a defense-in-depth `_reject_admin(args)` check inside
   `run_wrapper`. The flag is intentionally never exposed.

## Default-off behavior

When `--run-summary` is omitted, the wrapper prints a single line to
stderr:

```
merge_readiness_with_phase_ledger: no --run-summary provided; phase-ledger gate skipped
```

…and then delegates directly to `scripts/local/merge_pr_safely.py`,
returning that subprocess's exit code unchanged. No additional
validations, no head rechecks, no phase-gate adapter invocation. This is
the safe default and is fully behavior-compatible with running
`merge_pr_safely.py` directly.

## Opt-in behavior (when `--run-summary` is provided)

When `--run-summary` is set, the wrapper runs the following sequence
inside `run_wrapper(args)`:

1. **Reject `--admin`** (defense in depth, even though argparse already
   refuses it).
2. **Validate that all six required phase-gate args are present**:
   `--expected-head-sha`, `--allowed-files`, `--local-validation-path`,
   `--codex-artifact-path`, `--phase-gate-output-json`,
   `--phase-gate-output-md`. If any are missing, print a clear
   "Refusing to proceed" message and exit `2`.
3. **Cross-script consistency check** — read `git -C <repo_root>
   remote get-url origin` (i.e. the origin of the directory the
   operator passed as `--repo-root`, **not** the origin of the
   AED script's own checkout) and compare its normalized
   owner/repo slug to the normalized `--repo` slug. On mismatch,
   print a `REPO_MISMATCH:` message and exit `2`.

   The phase-gate adapter (`aed_final_gate.run_final_gate`)
   separately derives its target repo from `git remote get-url
   origin` run with `cwd=Path(__file__).parent.parent` — i.e.
   from the AED checkout the wrapper itself is being executed
   out of, which may be a different physical directory than
   `--repo-root`. The wrapper does **not** verify that the
   script-checkout origin and the `--repo-root` origin agree;
   the operator is responsible for invoking the wrapper from
   the same AED checkout as `--repo-root` (or, equivalently,
   for ensuring the two are the same repo). See
   §Cross-checkout pitfall below.
4. **Run the phase-gate adapter** — invoke
   `finalize_with_phase_ledger.run_finalize(...)` with the runner's
   `run_summary.json` and the six phase-gate args. If the adapter
   returns non-zero, propagate that code and **do not** invoke
   `merge_pr_safely.py`. Exit `1` (HOLD) or `2` (ERROR) as the adapter
   decided.
5. **Pre-delegation live-head recheck** — issue a read-only
   `gh pr view --json headRefOid --jq .headRefOid` (bounded 30s
   timeout) for `args.repo`/`args.pr_number` and compare the result to
   `args.expected_head_sha`. On fetch failure, print
   "unable to recheck PR head after phase-ledger gate; merge_pr_safely
   not invoked" and exit `2`. On head mismatch, print
   `HOLD_HEAD_CHANGED: phase-ledger gate validated <expected> but PR
   head is now <live>` and exit `1`.
6. **Run `merge_pr_safely.py`** as a subprocess. If it returns non-zero,
   propagate the exit code unchanged and **do not** run any
   post-success checks.
7. **Verify the merge-readiness report head** — open the JSON report at
   `args.output_json` and compare its recorded `head_sha` to
   `args.expected_head_sha`. On missing report, malformed JSON, or head
   mismatch, exit `1` with `HEAD_MISMATCH_AFTER_MERGE_READINESS` (or
   `2` for missing/malformed).
8. **Final live-head recheck** — issue the same bounded `gh pr view`
   call as in step 5. On fetch failure, print
   "unable to recheck PR head after merge readiness; not returning
   success" and exit `2`. On head mismatch, print
   `HOLD_HEAD_CHANGED_AFTER_MERGE_READINESS: ledger-validated head was
   <expected> but PR head is now <live>` and exit `1`.
9. **Return `0`** only if every step above bound successfully and the
   final live head still equals `args.expected_head_sha`.

## Required opt-in flags

When `--run-summary` is set, the wrapper requires **all seven** of the
following args (the run-summary path plus six phase-gate args) in
addition to the merge-readiness pass-throughs:

| Flag | Purpose |
|------|---------|
| `--run-summary <path>` | Path to the runner-produced `run_summary.json` (version `aed.run_summary.v0`). Enables the opt-in flow. |
| `--expected-head-sha <sha>` | The 40-hex SHA the runner covered. Required; the wrapper does **not** fabricate or default this value. |
| `--allowed-files <globs>` | Comma-separated file globs the final-gate adapter is allowed to bless. |
| `--local-validation-path <path>` | Path to the local validation JSON consumed by the final-gate adapter. |
| `--codex-artifact-path <path>` | Path to the Codex review artifact consumed by the final-gate adapter. |
| `--phase-gate-output-json <path>` | Where the final-gate adapter writes its JSON output. |
| `--phase-gate-output-md <path>` | Where the final-gate adapter writes its Markdown output. |

Plus the merge-readiness output args (always required):

| Flag | Purpose |
|------|---------|
| `--output-json <path>` | Where `merge_pr_safely.py` writes its JSON readiness report. The wrapper never writes here itself. |
| `--output-md <path>` | Optional. Where `merge_pr_safely.py` writes its Markdown readiness report. |

And the always-required identity args:

| Flag | Purpose |
|------|---------|
| `--repo <owner/name>` | The GitHub repository the wrapper is gating on. Must match the origin of `--repo-root` (the directory the operator passes), not the origin of the AED script's own checkout — see §Cross-checkout pitfall. |
| `--repo-root <path>` | The AED repository root. Used for the repo-consistency check and forwarded to `merge_pr_safely.py`. |
| `--pr-number <int>` | The GitHub PR number being gated. |

## Copyable example command

The following is a **template** with placeholders. Replace `<...>`
tokens with real values from your runner output. Do not run a command
that hard-codes an active PR's SHAs without first verifying them in the
PR's own `run_summary.json` and `git log origin/main`.

```bash
python3 scripts/local/merge_readiness_with_phase_ledger.py \
  --repo <owner/name> \
  --repo-root <absolute/path/to/repo> \
  --pr-number <pr-number> \
  --run-summary <absolute/path/to/run_summary.json> \
  --expected-head-sha <40-hex-sha-from-runner> \
  --allowed-files "<comma,separated,globs>" \
  --local-validation-path <absolute/path/to/validation.json> \
  --codex-artifact-path <absolute/path/to/codex.md> \
  --phase-gate-output-json <absolute/path/to/FINAL_GATE.json> \
  --phase-gate-output-md <absolute/path/to/FINAL_GATE.md> \
  --output-json <absolute/path/to/merge_status.json> \
  --output-md <absolute/path/to/merge_status.md>
```

The wrapper returns `0` only if every binding check agrees. Anything
other than `0` is a refused merge readiness; the operator must
investigate before retrying.

## Failure modes

The wrapper is exhaustive about failure paths. The exit-code contract is:

| Symptom (stderr snippet) | Exit code | Meaning |
|--------------------------|-----------|---------|
| `Refusing to proceed` (missing required phase-gate arg) | `2` | One of the six required phase-gate args is missing or empty. |
| `REPO_MISMATCH:` or `unable to read git remote get-url origin` | `2` | `--repo` does not match `--repo-root`'s `origin` remote, or the remote could not be read. |
| `phase-ledger final gate blocked merge-readiness (gate exit code N)` | `1` or `2` | The phase-gate adapter returned non-zero (`1`=HOLD, `2`=ERROR). `merge_pr_safely.py` was **not** invoked. |
| `unable to recheck PR head after phase-ledger gate; merge_pr_safely not invoked` | `2` | The pre-delegation `gh pr view` recheck failed (timeout, missing `gh`, non-zero exit, empty/malformed stdout). |
| `HOLD_HEAD_CHANGED: phase-ledger gate validated <expected> but PR head is now <live>` | `1` | A new commit landed after the phase gate validated the head. `merge_pr_safely.py` was **not** invoked. |
| `merge_pr_safely` propagated exit code | unchanged | A non-zero exit from `merge_pr_safely.py` is propagated unchanged. No post-success checks are run on a failed merge readiness. |
| `HEAD_MISMATCH_AFTER_MERGE_READINESS` (or missing/malformed report) | `1` or `2` | The merge-readiness report's recorded `head_sha` does not equal `args.expected_head_sha`, or the report is missing or unparseable. |
| `unable to recheck PR head after merge readiness; not returning success` | `2` | The final `gh pr view` recheck failed. |
| `HOLD_HEAD_CHANGED_AFTER_MERGE_READINESS: ledger-validated head was <expected> but PR head is now <live>` | `1` | A new commit landed in the residual window after the report was written. |

Any non-zero exit is a refused merge readiness and **must not** be
treated as authorization to merge. The wrapper never returns success for
a head the runner-produced phase ledger did not cover.

## Guardrails

The wrapper is hardened against the obvious ways an opt-in gate could
be subverted:

- **No `--admin`.** Argparse does not expose it. `_reject_admin(args)`
  inside `run_wrapper` also refuses `args.allow_admin == True` shimmed
  via a fake Namespace, in case a future refactor accidentally surfaces
  it. (See PR #371 + PR #393 self-check invariants.)
- **No `--auto`.** Same treatment. The wrapper never auto-merges.
- **No merge operation.** The wrapper never calls `gh pr merge`, never
  calls `gh pr create`, never calls `gh pr edit`, and never invokes
  `git push`. The only `gh` command it issues is a read-only
  `gh pr view --json headRefOid --jq .headRefOid`.
- **Exact-head binding.** Every binding step (phase-gate, pre-delegation
  recheck, report-head verify, final recheck) uses
  `args.expected_head_sha` as the reference. A new commit landing at
  any of the four checkpoints will cause the wrapper to refuse success.
- **Bounded `gh` calls.** Both pre- and post-delegation rechecks are
  bounded by `GH_PR_VIEW_TIMEOUT_SECONDS = 30` and catch both
  `subprocess.TimeoutExpired` and `OSError` (so a missing `gh` binary
  fails closed with rc `2` rather than crashing with a traceback).
- **Fail-closed on missing evidence.** The wrapper does not invent,
  default, or reuse a SHA. A missing `--expected-head-sha` causes an
  immediate `Refusing to proceed` exit `2` before any subprocess is
  invoked.

## Cross-checkout pitfall

The wrapper's repo-consistency check (`REPO_MISMATCH:`) compares the
operator's `--repo` against the `origin` remote of the directory
passed as `--repo-root`. It does **not** also check that the
AED script's own checkout (the directory the wrapper is being
executed from) has an origin that agrees with `--repo-root`. The
phase-gate adapter, by contrast, derives its target repo from
`git remote get-url origin` with `cwd=Path(__file__).parent.parent`
— i.e. from the AED script's own checkout, **not** from
`--repo-root`. This means the following cross-checkout invocation
is a **forbidden mode** that defeats the consistency check:

```
WRONG — do NOT do this:
  # The wrapper is being invoked from AED checkout A...
  cd /path/to/aed-checkout-A
  python3 scripts/local/merge_readiness_with_phase_ledger.py \
    --repo <owner/repo> \
    --repo-root /path/to/aed-checkout-B \   # ← different AED checkout
    ...
```

In this pattern, the wrapper's `REPO_MISMATCH:` check is comparing
`--repo` against the origin of checkout B, while the phase-gate
adapter (`aed_final_gate.run_final_gate`) will validate against
the origin of checkout A. If the two checkouts are different repos
(or different forks, or different remotes pointing at the same repo
under different names), the wrapper's check can pass while the
phase gate validates a different PR than the operator intended.

The safe pattern is to invoke the wrapper from the same AED
checkout as `--repo-root`:

```
RIGHT:
  cd /path/to/aed-checkout
  python3 scripts/local/merge_readiness_with_phase_ledger.py \
    --repo <owner/repo> \
    --repo-root $(pwd) \
    ...
```

Or, equivalently, set `--repo-root` to the absolute path of the
AED checkout the operator is invoking the wrapper from. The
wrapper does not detect the cross-checkout pitfall; it is the
operator's responsibility to avoid it. (A future PR may add an
explicit script-checkout ↔ `--repo-root` comparison to
`_validate_repo_matches_repo_root`; until that lands, this
document is the only safety surface for the cross-checkout
mode.)

## Run-summary ledger field set semantics

The phase-gate adapter
(`scripts/local/finalize_with_phase_ledger.py`) inspects the
`run_summary.json` produced by the runner and decides whether
to call `aed_final_gate.run_final_gate(...)` with
`require_phase_ledger=True` or `require_phase_ledger=False`.
The decision is **not** symmetric across the three possible
field-presence states, and operators must understand which
state applies to their run before treating wrapper success
as phase-ledger evidence.

The three modes are distinguished by `LEDGER_KEYS` in
`finalize_with_phase_ledger.py`:

```
LEDGER_KEYS = (
    "phase_ledger_path",
    "phase_ledger_claimed_phases",
    "phase_ledger_expected_run_id",
)
```

`_extract_ledger_args(summary)` then applies the following
rule (paraphrased from the source):

- **Mode 1 — All three `phase_ledger_*` fields absent:**
  returns `{"enabled": False, "phase_ledger_path": None,
  "claimed_phases": None, "expected_run_id": None}`. The
  adapter calls
  `aed_final_gate.run_final_gate(..., require_phase_ledger=False,
  phase_ledger_path=None, claimed_phases=None,
  phase_ledger_expected_run_id=None)`. In this path the
  final gate does **not** require a phase ledger, and a
  return of `MERGE_READY` from the gate (and therefore a
  return of `0` from the wrapper) is **not** proof that
  any phase-ledger evidence was validated. This is the
  default-off compatibility path for older or unledgered
  run summaries — i.e. runs produced by a runner invocation
  that did not enable phase-ledger support, or by an
  earlier runner version that did not emit
  `phase_ledger_*` fields at all.

- **Mode 2 — At least one `phase_ledger_*` field present
  (any one of the three):** returns
  `{"enabled": True, "phase_ledger_path": summary.get(...),
  "claimed_phases": summary.get(...),
  "expected_run_id": summary.get(...)}`. The adapter calls
  `aed_final_gate.run_final_gate(...,
  require_phase_ledger=True, ...)` and forwards whatever
  values it found for the other two keys (which may be
  `None`, an empty list, a missing path, a stale run id,
  etc.). **This is the phase-ledger evidence boundary.**
  When the gate finds the evidence missing, empty, stale,
  or malformed, it enforces fail-closed
  (`HOLD_UNEVIDENCED_PASS`,
  `HOLD_PHASE_EVIDENCE_CORRUPTED`, or
  `HOLD_PHASE_RESULT_INCONSISTENT`) and returns non-zero
  from the wrapper. A return of `MERGE_READY` from the
  gate in this mode means the gate validated a real phase
  ledger; a HOLD/ERROR means it did not.

- **Mode 3 — All three fields present and valid:** a
  specialization of Mode 2. Same fail-closed semantics;
  no additional behavior.

**Operator rule of thumb:** for a PR intended to be
phase-gated, the `run_summary.json` **must** be in Mode 2
or Mode 3 — i.e. it must contain at least one
`phase_ledger_*` field, and the values for all three fields
must be valid (existing path, non-empty claimed phases,
non-stale expected run id). If the run summary is in Mode
1 (all-fields-absent), do not interpret wrapper success as
phase-ledger evidence. Regenerate the run summary from a
ledger-enabled runner, or treat the evidence as not
available, before relying on the wrapper as an evidence
boundary for that PR.

**Why the asymmetry exists:** Mode 1 is the
default-off compatibility path. A wrapper invocation
with `--run-summary <path>` against a run summary that
predates phase-ledger support, or that was produced by a
ledger-disabled runner, must not fail-closed; that would
break every pre-existing PR that did not opt into phase
ledgers. The trade-off is that operators who *did* intend
phase-gating must verify Mode 2/3 themselves (or trust
their runner to be in Mode 2/3 when it should be).

## When **not** to use the wrapper

The wrapper is the right tool only when **all** of the following hold:

1. The PR has a runner-produced `run_summary.json` that
   contains the **complete** `phase_ledger_*` field set —
   i.e. **all three** of `phase_ledger_path`,
   `phase_ledger_expected_run_id`, and
   `phase_ledger_claimed_phases` are present. See
   §Run-summary ledger field set semantics for the three
   modes the adapter distinguishes. The wrapper is the
   phase-ledger evidence boundary only when the complete
   field set is present and valid; otherwise the wrapper
   is in default-off / compatibility mode and is **not**
   a phase-ledger evidence boundary for that PR.
2. The runner actually ran to completion for the live PR head — i.e.
   the `expected_head_sha` in the run summary is the same as
   `git rev-parse origin/<pr-branch>` at the time the operator invokes
   the wrapper. If a new commit landed after the runner finished, the
   runner's evidence does not cover the live head; abort and re-run the
   runner before invoking the wrapper.
3. `--repo` matches the `git remote get-url origin` of the
   directory passed as `--repo-root` (the wrapper enforces
   this; a mismatch exits `2` with `REPO_MISMATCH:` before any
   subprocess is invoked). The wrapper does **not** check
   that the script's own AED-checkout origin matches
   `--repo`; see §Cross-checkout pitfall for the forbidden
   cross-checkout invocation mode.
4. The operator is not trying to merge directly. The wrapper is not a
   substitute for `gh pr merge` or the standard AED merge authorization
   flow. The wrapper only emits or refuses merge-readiness; the human
   must still authorize the actual merge.

**Invariant.** For a PR intended to be phase-gated,
`merge_readiness_with_phase_ledger.py` is the safety boundary.
Bypassing it after a failed prerequisite defeats exact-head
phase-ledger evidence: the wrapper is the only place where the
runner-produced ledger is bound to the live PR head, and skipping
it for the same PR re-merges on stale evidence.

The right response to a failed prerequisite depends on whether the
PR is being phase-gated at all:

- **PR is not phase-gated.** If the PR is not being phase-gated
  (no runner-produced `run_summary.json` for the live head, and
  the operator never intended to invoke the wrapper for it),
  running `merge_pr_safely.py` directly is fine. The wrapper is
  opt-in; invoking it without `--run-summary` is the correct way
  to opt out, and bypassing it is unnecessary.

- **PR is intended to be phase-gated.** If the operator started
  the phase-gating flow for this PR — i.e. a runner-produced
  `run_summary.json` exists for it — do **not** fall back to
  `merge_pr_safely.py` for that same PR after a prerequisite
  fails. Instead, address the specific failure:

  - **Stale head** (`HOLD_HEAD_CHANGED:` exit `1`, or a runner
    run whose `expected_head_sha` is older than
    `git rev-parse origin/<pr-branch>`): the live PR head has
    advanced past the evidence the runner covered. Re-run the
    runner against the **current** head to produce a fresh
    `run_summary.json`, then re-invoke the wrapper with the new
    `--expected-head-sha`. Do not re-invoke the wrapper with the
    stale summary, and do not fall back to `merge_pr_safely.py`
    to "get the merge in" against an un-evaluated head.
  - **Missing evidence** has two distinct shapes; the
    wrapper handles them very differently. Operators
    must know which one applies to their run:

    1. **`Refusing to proceed` exit `2` from the
       wrapper** for a missing/empty required phase-gate
       arg (e.g. `--run-summary`, `--expected-head-sha`,
       `--allowed-files`, `--local-validation-path`,
       `--codex-artifact-path`, `--phase-gate-output-json`,
       `--phase-gate-output-md`): the wrapper is refusing
       to invoke the phase-gate adapter at all because a
       required flag is empty. Fix the invocation.

    2. **`run_summary.json` is absent, malformed, or
       lacks one or more of the `phase_ledger_*` fields:**
       the adapter then distinguishes:

       - **All three `phase_ledger_*` fields absent:**
         `finalize_with_phase_ledger._extract_ledger_args`
         returns `{"enabled": False, ...}`. The adapter
         then calls `aed_final_gate.run_final_gate(...,
         require_phase_ledger=False)`. In this path the
         gate can return `MERGE_READY` without validating
         any phase ledger. This is **compatibility
         behavior for older or unledgered run summaries,
         not a phase-ledger evidence boundary.** Do not
         interpret wrapper success in this path as proof
         of phase-ledger validation.

       - **Any one of the three `phase_ledger_*` fields
         present (but possibly with missing/empty/stale
         values for the other two):**
         `_extract_ledger_args` returns `{"enabled":
         True, ...}`. The adapter then calls
         `aed_final_gate.run_final_gate(...,
         require_phase_ledger=True)`. This path
         **is** the phase-ledger evidence boundary; the
         gate enforces fail-closed
         (`HOLD_UNEVIDENCED_PASS`,
         `HOLD_PHASE_EVIDENCE_CORRUPTED`, or
         `HOLD_PHASE_RESULT_INCONSISTENT`) when the
         evidence is missing, empty, stale, or malformed.

       See §Run-summary ledger field set semantics for
       the full contract and the source files
       involved.

       **For a PR intended to be phase-gated**, an
       all-fields-absent `run_summary.json` is
       insufficient: regenerate the run summary from a
       ledger-enabled runner, or treat the evidence as
       not available. Do not interpret wrapper success
       in the all-fields-absent path as
       `phase_ledger`-validated merge-readiness.
  - **Repo/root mismatch** (`REPO_MISMATCH:` or `unable to read
    git remote get-url origin` exit `2`): the normalized
    `--repo` does not match the `origin` remote of the
    directory the operator passed as `--repo-root`. The
    wrapper's consistency check is between `--repo` and
    `--repo-root`'s origin — it does **not** also check the
    AED script's own checkout origin. Fix the invocation
    (correct `--repo`) or fix `--repo-root` (point it at the
    AED checkout the wrapper is being executed from, or run
    `git remote set-url origin ...` inside `--repo-root`)
    and re-invoke the wrapper. The mismatch is a binding
    error, not a suggestion to bypass. If `--repo` and
    `--repo-root`'s origin both pass the wrapper's check but
    the phase gate still appears to validate the wrong PR,
    see §Cross-checkout pitfall — the operator has likely
    invoked the wrapper from one AED checkout while passing
    `--repo-root` for a different AED checkout.
  - **Phase gate HOLD/ERROR** (exit `1` or `2` from the
    final-gate adapter, e.g.
    `phase-ledger final gate blocked merge-readiness (gate exit
    code N)`): treat it as a blocker. **This exit
    only fires when the adapter called the gate with
    `require_phase_ledger=True`** — i.e. when at
    least one `phase_ledger_*` field was present in
    `run_summary.json` and the gate found the
    evidence missing, empty, stale, or malformed. The
    phase-ledger evidence does not agree with the live
    PR state, and `merge_pr_safely.py` was **not**
    invoked. Inspect the gate output at
    `--phase-gate-output-json` /
    `--phase-gate-output-md` and resolve the
    underlying disagreement (stale head, missing
    claimed phase, unblessed files, etc.) before
    retrying. The gate is the safety boundary;
    bypassing it defeats exact-head phase-ledger
    evidence.

    **If the all-fields-absent path was taken**
    (see §Run-summary ledger field set semantics),
    the gate will *not* emit a HOLD/ERROR on the
    basis of missing phase-ledger evidence; it will
    only emit HOLD/ERROR on the other (non-ledger)
    failure modes. A successful run in that path is
    **not** phase-ledger evidence.

A failed prerequisite is information the operator acts on, not
authorization to skip the safety boundary.

## Related files

| File | Role |
|------|------|
| `scripts/local/run_autocoder_single_task.py` | Emits `run_summary.json` with phase-ledger fields when phase-ledger support is enabled. PR #391. |
| `scripts/local/phase_ledger.py` | Phase execution ledger guard. PR #390. |
| `scripts/local/phase_exec.py` | Phase execution guard plumbing. PR #390. |
| `scripts/local/finalize_with_phase_ledger.py` | Leaf adapter that consumes the runner's phase ledger (`run_finalize()`). PR #392. |
| `scripts/local/merge_readiness_with_phase_ledger.py` | The opt-in wrapper this document describes. PR #393. |
| `scripts/local/merge_pr_safely.py` | The underlying merge-readiness orchestrator the wrapper composes with. PR #371. Unchanged by the phase-ledger series. |
| `tests/test_merge_readiness_with_phase_ledger.py` | 46 pytest tests covering default-off, opt-in success, gate failure modes, head recheck, report-head binding, repo/origin validation, missing-gh/OSError handling, bounded timeout, and admin/auto rejection. PR #393. |
