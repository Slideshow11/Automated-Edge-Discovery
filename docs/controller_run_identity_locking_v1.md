# Controller Run Identity, Locking, and Mutation Authorization

**Status:** implemented in branch `feat/controller-run-identity-and-locking`.
**Round:** 120.
**Scope:** harden `scripts/local/autocoder_run_controller.py` and four
supporting modules.

## Why

The v0 controller had several gaps:

- No auditable per-run identity (no host, no PID, no start-time).
- No supervisor lock — two workers could simultaneously try to drive
  the same `(repository, PR)` and silently race.
- No mutation-authorization lifecycle — an external executor could
  perform a repository or GitHub mutation without any controller
  state recording that the mutation was authorized.
- `_save_state` and the launch receipt were world-readable (0o644),
  leaking the run identity and target SHA to any local user.
- `finalize-run` would happily mark the run complete even when an
  authorized mutation had never recorded a result — masking
  crash-after-authorization scenarios.

This PR adds all four hardening primitives and wires them into the
controller's existing CLI surface. It does NOT redesign unrelated
controller phases, monitoring, or provider-quota recovery.

## New modules

| Module | Purpose |
|---|---|
| `scripts/local/aed_run_identity.py` | Capture host, PID, `/proc/<pid>/stat` start_time, ctime; assert restrictive file modes; refuse to persist known secret patterns. |
| `scripts/local/aed_supervisor_lock.py` | Host-local exclusive lock with evidence-based liveness (PID existence AND start-time match AND/OR ctime match); fail-closed on indeterminate liveness; bounded stale-lock recovery with audit trail; lock files written 0o600. |
| `scripts/local/aed_mutation_authorization.py` | One-time authorization records with expected heads, terminal-result matching (success/failure/indeterminate), idempotent exact replay, fail-closed on non-identical duplicate. |
| `scripts/local/aed_launch_receipt.py` | Machine-readable and concise human-readable launch receipts, restrictive permissions, no-secret assertion. |

## Controller CLI additions

```
init --repository --target-pr-number --mutation-target \
     --current-main-sha --starting-target-sha --merge-policy
authorize-mutation --state --workspace --mutation-type \
                   --expected-main-sha --expected-target-sha \
                   --mutation-target --pending-action
record-mutation-result --workspace --mutation-id --status \
                       --evidence --actual-main-sha \
                       --actual-target-sha --error-detail
inspect-lock --state --workspace
recover-stale-lock --state --workspace --staleness-evidence
list-outstanding-mutations --workspace
```

## State-file shape

The `init` command now writes `CONTROLLER_STATE.json` atomically with
0o600 permissions, and embeds a `run_identity` object:

```json
{
  "controller_version": 1,
  "run_id": "...",
  "run_identity": {
    "run_id": "...",
    "controller_version": 1,
    "repository": "owner/name",
    "target_pr_number": 415,
    "current_main_sha": "...",
    "starting_target_sha": "...",
    "current_phase": "RUN_ACTIVE",
    "pending_action": "run_task",
    "merge_policy": "stop_before_merge",
    "host": {"hostname": "...", "platform": "linux", ...},
    "process": {"pid": ..., "stat_start_time": ..., "ctime_ns": ..., "source": "linux_proc"}
  },
  ...
}
```

## Lock file shape

Locks live under `<workspace.parent>/locks/<scope>.lock.json`:

```json
{
  "lock_version": 1,
  "scope_key": "repo:owner/name|pr:415",
  "scope": {"repository": "owner/name", "target_pr_number": 415, "mutation_target": null},
  "owner_run_id": "...",
  "owner_host": {...},
  "owner_pid": ...,
  "owner_start_evidence": {...},
  "created_at": "...",
  "max_age_seconds": 604800,
  "recovery_history": [
    {"recovered_at": "...", "recovered_by_run_id": "...",
     "previous_owner_run_id": "...", "staleness_evidence": "..."}
  ]
}
```

## Mutation journal shape

`<workspace>/MUTATIONS.jsonl`:

```jsonl
{"mutation_id":"<uuid>","run_id":"...","repository":"...","target_pr_number":415,"mutation_type":"squash_merge","expected_main_sha":"...","expected_target_sha":"...","pending_action":"merge","created_at":"...","authorization_status":"authorized","result":null}
{"kind":"result","mutation_id":"<uuid>","run_id":"...","result":{"status":"success","recorded_at":"...","actual_main_sha":"...","actual_target_sha":"..."}}
```

## What this PR explicitly does NOT do

- Branch comparison normalization (out of scope per operator).
- Codex response-surface classification (out of scope per operator).
- Review-thread resolution policy (out of scope per operator).
- CI/Codex monitoring (out of scope per operator).
- PR-body reconciliation (out of scope per operator).
- Provider-quota recovery (out of scope per operator).

These items were identified as follow-up work in the prior
PR-415 reconciliation scope and remain to be addressed separately.