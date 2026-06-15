# AED Policy Engine Skeleton (v1)

> Pure policy logic for the AED governance stack. No existing
> workflow is wired to it yet. The OpenHands plugin, the Humphry
> command bridge, and the safe tool wrappers come in later PRs.

## 1. Purpose

This document describes the AED policy engine skeleton that was
added immediately after PR #403 (the rules inventory PR). The
skeleton converts the canonical 30-rule inventory at
`docs/governance/aed_rules_inventory.md` into importable Python
decisions that the future OpenHands broker — and the existing
harness, if a future PR chooses — can call.

This is **preparation, not integration**. The skeleton ships
without any wiring into the existing PR workflow, the Codex
classifier, the audit appender, the merge guard, the scope guard,
or any other live tool. It is a pure logic library.

## 2. What this PR adds

- `aed_policy/` — a small new package with five modules:
  - `action_types.py` — typed action categories
  - `decisions.py` — decision data model and codes
  - `run_state.py` — minimal run-state container
  - `policy.py` — the pure decision function `evaluate_action`
  - `reporting.py` — formatting helpers
- `tests/test_aed_policy_engine.py` — stdlib-only unit tests
  covering allow/deny paths, decision codes, matched rule IDs,
  required evidence, and the decision serialization surface.
- `docs/governance/aed_policy_engine_skeleton.md` — this document.

## 3. Design principle

> **The policy engine is pure logic. It does not run shell
> commands, does not call the GitHub API, and does not mutate
> anything. Every input is passed in via `AEDRunState`; every
> output is a structured `AEDDecision`.**

This makes the engine:

- **Deterministic.** Same inputs always produce the same output.
- **Testable.** No fixtures, no network, no clock. Just data in,
  data out.
- **Decoupled.** The harness can collect state and call the
  engine without the engine ever depending on the harness.
- **Auditable.** Every decision lists the rule IDs that matched.

## 4. Public API (skeleton level)

```python
from aed_policy.action_types import AEDActionType
from aed_policy.decisions import AEDDecision, AEDDecisionCode
from aed_policy.run_state import AEDRunState
from aed_policy.policy import evaluate_action
from aed_policy.reporting import decision_to_paragraph, missing_evidence, summarize_denied

decision = evaluate_action(AEDActionType.GITHUB_MERGE, run_state)
if not decision.allowed:
    raise RuntimeError(decision_to_paragraph(decision))
```

`AEDDecision` carries:

- `allowed: bool`
- `code: AEDDecisionCode` (e.g., `DENY`, `REQUIRE_EXPLICIT_AUTHORIZATION`,
  `REQUIRE_CLEAN_CI`)
- `reason: str` — human-readable
- `required_evidence: list[str]` — what the caller must supply
  for the decision to flip
- `matched_rule_ids: list[str]` — which `AED-RULE-NNN` entries
  were consulted

## 5. Action types and decision codes

### Action types (`AEDActionType`)

| Constant | Meaning |
|----------|---------|
| `READ_ONLY_STATUS` | Pure status check (always allow) |
| `FILE_READ` | File read (always allow) |
| `FILE_WRITE` | File write |
| `TERMINAL_READ_ONLY` | Read-only subshell (always allow) |
| `TERMINAL_MUTATING` | Mutating subshell |
| `GIT_READ_ONLY` | Read-only git (always allow) |
| `GIT_MUTATING` | Mutating git |
| `GITHUB_READ_ONLY` | Read-only `gh` (always allow) |
| `GITHUB_COMMENT` | Generic GitHub comment |
| `GITHUB_THREAD_RESOLVE` | Resolve a review thread |
| `GITHUB_MERGE` | Merge a PR |
| `GITHUB_REOPEN` | Reopen a closed PR |
| `CODEX_PING` | Post a `@codex review` request |
| `AUDIT_APPEND` | Append to the merge-action audit log |
| `PRIMARY_WORKTREE_SYNC` | `git pull` / `fetch --merge` / `reset` / `checkout` on the primary worktree |
| `PRIMARY_WORKTREE_MUTATION` | Any other mutation on the primary worktree |
| `UNKNOWN` | Unclassified action (deny by default) |

### Decision codes (`AEDDecisionCode`)

`ALLOW`, `DENY`, `HOLD`, `REQUIRE_EXPLICIT_AUTHORIZATION`,
`REQUIRE_EXACT_HEAD_AUTHORIZATION`,
`REQUIRE_THREAD_LIST_AUTHORIZATION`, `REQUIRE_APPEND_ONLY_AUDIT`,
`REQUIRE_CLEAN_MERGE_STATE`, `REQUIRE_CLEAN_CI`,
`REQUIRE_CLEAN_SCOPE`, `REQUIRE_ISOLATED_WORKSPACE`,
`REQUIRE_NO_PRIMARY_MUTATION`, `REQUIRE_NO_DUPLICATE_CODEX_PING`,
`REQUIRE_NO_UNRESOLVED_THREADS`.

Codes beginning with `REQUIRE_` are conditional: the action is
denied until the caller supplies the named evidence. `DENY` and
`HOLD` are hard denials that the caller cannot override.

## 6. Rule coverage in this skeleton

The skeleton covers a strict subset of the 30-rule inventory
chosen to be sufficient for the upcoming plugin step. The mapping
below is normative; later PRs may expand coverage.

| AED action | Rule IDs consulted |
|------------|--------------------|
| `READ_ONLY_STATUS`, `FILE_READ`, `GIT_READ_ONLY`, `GITHUB_READ_ONLY`, `TERMINAL_READ_ONLY` | `AED-RULE-009`, `AED-RULE-019` (allow) |
| `FILE_WRITE` / `TERMINAL_MUTATING` / `GIT_MUTATING` | `AED-RULE-003` (require isolated workspace) |
| `PRIMARY_WORKTREE_MUTATION` | `AED-RULE-001` |
| `PRIMARY_WORKTREE_SYNC` | `AED-RULE-002` |
| `GITHUB_MERGE` | `AED-RULE-005`, `-007`, `-008`, `-011`, `-012`, `-018`, `-019`, `-021` |
| `GITHUB_THREAD_RESOLVE` | `AED-RULE-008` |
| `GITHUB_REOPEN` | `AED-RULE-021` |
| `GITHUB_COMMENT` | (no rule consulted, default allow) |
| `CODEX_PING` | `AED-RULE-010` |
| `AUDIT_APPEND` | `AED-RULE-020` |
| `UNKNOWN` | `AED-RULE-024` (deny by default) |

## 7. What this PR does NOT do (non-goals)

This is intentionally a narrow PR. The following are explicitly
out of scope and will land in subsequent PRs:

- **OpenHands plugin.** No `aed_pre_tool_use.py` hook, no
  `aed_stop_gate.py`, no `.plugin/plugin.json`. The plugin
  skeleton PR follows.
- **Humphry command bridge.** No `/aed-status`, `/aed-merge`,
  `/aed-resolve-threads`, etc. as OpenHands commands.
- **Safe tool wrappers.** No `aed_tools/file.py`,
  `aed_tools/git.py`, `aed_tools/github.py`,
  `aed_tools/codex_ping.py`, etc.
- **Integration with the existing PR workflow.** The existing
  scripts (`merge_pr_safely.py`, `audit_codex_response_for_pr.py`,
  `scope_guard.py`, `wait_for_pr_ready.py`,
  `append_merge_action_audit.py`, `aed_lifecycle_states.py`)
  continue to be the live enforcers. They are not modified in
  this PR.
- **OpenHands adapter, SDK runner, or `aed_run.py` standalone
  runner.** Those land in the runner PR.
- **Wiring into the Codex classifier, audit appender, or scope
  guard.** The policy engine is parallel to them, not above
  them, in this PR.
- **Network, shell, GitHub, or filesystem side effects in the
  policy engine itself.** This is intentional. The engine is a
  pure function.

## 8. Expected next PR

The follow-on PR introduces:

- `.plugin/plugin.json` and `hooks/hooks.json` with an
  `AEDPreToolUse` hook that always returns `allow` (no policy
  wired yet).
- A first set of safe tool wrappers (`AEDFileTool`,
  `AEDTerminalTool`, `AEDGitTool`) in read-only form.

A separate runner PR then introduces `aed_run.py` and the SDK
integration that actually calls `aed_policy.policy.evaluate_action`
on every tool call.

## 9. Why this skeleton ships before the plugin

- **Validates the rule set is implementable.** The skeleton
  forces every rule to be expressible as a pure function of
  `AEDRunState` and `AEDActionType`. Rules that cannot be
  expressed in that shape surface as gaps and feed the next
  PR's design.
- **Decouples rule evolution from OpenHands release cadence.**
  Rules can be added, tightened, or relaxed in pure Python
  without coordinating with the OpenHands upgrade path.
- **Lets tests fail on the right things.** The unit tests in
  this PR are the regression net for any future change to the
  rule set.

## 10. Source-of-truth chain

| Doc / module | Role |
|--------------|------|
| `docs/governance/aed_rules_inventory.md` | Authoritative human-readable rule list |
| `aed_policy/policy.py` | Importable Python form of the rules |
| `tests/test_aed_policy_engine.py` | Regression net for the rules |
| `docs/governance/aed_policy_engine_skeleton.md` | This document |

If a rule is changed in `aed_policy/policy.py` without updating
the inventory, the inventory is the source of truth and the
code must be reconciled. If a rule is added to the inventory
without a corresponding change in `policy.py`, that rule is
uncovered in the engine and is a debt entry on the next
prototype step.

## 11. Test inventory

The unit tests in `tests/test_aed_policy_engine.py` cover the
following scenarios (each is its own `TestCase` class):

- `TestReadOnlyAllowed` — read-only status / file read / git
  read-only / github read-only / terminal read-only all allowed
- `TestUnknownDenied` — `UNKNOWN` denied with `AED-RULE-024`
- `TestPrimaryWorktreeRules` — `PRIMARY_WORKTREE_MUTATION`
  denied by default, allowed with authorization + clean
  primary, denied if primary is dirty;
  `PRIMARY_WORKTREE_SYNC` denied by default, allowed with
  authorization
- `TestMergeRules` — merge denied without authorization, on
  head mismatch, with unresolved threads, with non-pass CI,
  with dirty scope, with non-clean merge state, on newer Codex
  finding; allowed only when all gates pass
- `TestThreadResolve` — `GITHUB_THREAD_RESOLVE` denied without
  an authorized thread list, allowed with one
- `TestCodexPing` — `CODEX_PING` denied on duplicate same-head,
  allowed with no existing ping, allowed when existing ping is
  for a different head
- `TestAuditAppend` — `AUDIT_APPEND` denied when append-only
  evidence is missing, allowed when it is present
- `TestFileWriteAndIsolatedWorkspace` — file write / terminal
  mutating / git mutating denied without an isolated workspace,
  allowed inside one; isolated-workspace path must be under
  `/tmp/aed_runs/worktrees/`
- `TestProtectedPRs` — `GITHUB_MERGE` and `GITHUB_REOPEN`
  denied for protected PRs; reopen denied without authorization
  even for non-protected PRs
- `TestDecisionSerialization` — decision includes matched rule
  IDs; `AEDDecision.to_dict` round-trips through JSON;
  `AEDRunState.to_dict` round-trips through JSON
- `TestReportingHelpers` — `decision_to_paragraph` produces the
  expected single-line format; `missing_evidence` returns the
  required-evidence list; `summarize_denied` produces the
  expected multi-line block
