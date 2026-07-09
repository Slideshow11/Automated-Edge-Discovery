# AED Iteration Budget

This document explains how to raise the Hermes agent iteration
budget for heavy AED runs (e.g. multi-day V-repair chains, large
PR-audit sweeps, batched-executor packet runs). It does NOT
modify the Hermes agent code; the raise mechanism already exists
and is fully configurable. The default budget (90) is unchanged
for normal runs.

## 1. Where the 90/90 limit lives

The `Iteration budget reached (90/90)` message is produced by
**Hermes** (not the AED repo). The 90/90 number is read at
runtime by the Hermes CLI from the active config; if no config
or env override is set, Hermes falls back to a hardcoded default
of 90.

| File | Field | Default | Purpose |
|---|---|---|---|
| `~/.hermes/config.yaml` (line 14) | `agent.max_turns: 90` | 90 | Configured max tool-calling iterations for the main Hermes agent loop. |
| `~/.hermes/hermes-agent/cli.py` (line 3862) | hardcoded fallback | 90 | Used only if neither config nor env var is set. |
| `~/.hermes/hermes-agent/run_agent.py` (line 427) | `AIAgent.__init__` default | 90 | Same fallback, used by `AIAgent` when constructed without an explicit value. |
| `~/.hermes/hermes-agent/cli.py` (line 12622) | message text | n/a | Produces the `Iteration budget reached (X/Y)` notification; the numbers come from the live config. |

The 90/90 message in `cli.py:12619` is purely cosmetic — it just
displays the API-call count vs the limit when the loop is
exhausted. The actual loop enforcement happens earlier in the
agent code.

## 2. How to raise the budget

There are three layers, in priority order (highest wins):

1. **CLI argument** (not currently exposed for `max_turns`, but the underlying class accepts it).
2. **`HERMES_MAX_ITERATIONS` environment variable** (supported in `cli.py:3856-3862`).
3. **`agent.max_turns` in `~/.hermes/config.yaml`** (line 14).

### Normal mode (default)

No action needed. The default is 90 iterations.

```bash
unset HERMES_MAX_ITERATIONS
# ~/.hermes/config.yaml has agent.max_turns: 90
hermes run --task "normal AED task"
```

### Heavy mode (200 iterations)

Set `HERMES_MAX_ITERATIONS=200` before running heavy AED tasks.
This overrides both the config-file value and the hardcoded
default. The value is consumed once at agent-construction time.

```bash
export HERMES_MAX_ITERATIONS=200
hermes run --task "PR #408 V-repair chain"
```

If the heavy run is the exception rather than the rule, set the
env var inline for that single invocation rather than exporting
it globally:

```bash
HERMES_MAX_ITERATIONS=200 hermes run --task "..."
```

### Persistent heavy mode

Edit `~/.hermes/config.yaml` line 14 and change `max_turns: 90`
to `max_turns: 200`. The change is persistent across all Hermes
sessions. Use this only if most of your Hermes usage is heavy
AED work; for mixed usage, prefer the per-invocation env var.

```yaml
# ~/.hermes/config.yaml (line 14)
agent:
  max_turns: 200
```

## 3. Validation

After raising the budget, verify the change took effect:

```bash
HERMES_MAX_ITERATIONS=200 hermes run --task "test the budget" --max-turns 1 --verbose
```

The agent's startup banner or the `/status` slash command should
report the new limit. The 90/90 message should not appear before
200 iterations.

To confirm the hardcoded fallback is NOT what you want:

```bash
HERMES_MAX_ITERATIONS=999999 hermes run --task "..." 2>&1 | head -20
# Should NOT print "Iteration budget reached (999999/999999)"
# Should print "Iteration budget reached (<actual_count>/999999)"
```

The actual count is bounded by your model provider's TPM/RPM, not
by the Hermes config. Setting a very high `HERMES_MAX_ITERATIONS`
is safe in the sense that the loop terminates, but it does not
override the provider rate limit.

## 4. Hard caps and safety

The 90 default is a hard cap that prevents runaway loops. Raising
it to 200 is a deliberate operator action. Do NOT:

- Set `HERMES_MAX_ITERATIONS` to a value that exceeds your model's
  effective context window (e.g. 10000 for a 200k-token model is
  wasteful).
- Disable the iteration cap entirely. Hermes does not support
  "unlimited" — the cap is always at least 90 (hardcoded fallback).
- Use heavy mode for unattended runs without a wall-clock timeout.
  Pair `HERMES_MAX_ITERATIONS=200` with an outer `timeout` shell
  command to bound the run.

## 5. Overnight mode

Overnight mode is not a separate Hermes config. The recommended
pattern for overnight AED runs is:

1. Set `HERMES_MAX_ITERATIONS=200` per worker.
2. Use a supervisor (cron, systemd timer, or external scheduler)
   that relaunches the worker from the last checkpoint.
3. Bound each worker run with a wall-clock timeout (e.g. `timeout
   4h hermes run ...`).
4. Bound total wall-clock via the supervisor.

Hermes does not provide chained-worker checkpointing; the
checkpoint is the AED `phase_ledger.py` (per-task) and the
`aed_continue_pr.py --dry-run` output JSON. The supervisor reads
the ledger, decides whether to relaunch, and passes the latest
checkpoint path to the next worker invocation.

## 6. Related audit findings

This doc was written in response to the audit question
"Find where the Iteration budget reached (90/90) limit is
configured and make it configurable or raiseable for heavy AED
runs." The audit confirmed the limit is in Hermes (not AED) and
that the mechanism for raising it already exists; this doc is
the documentation deliverable.

No AED code was modified by this change. No new env vars, CLI
flags, or config keys were introduced — the existing
`HERMES_MAX_ITERATIONS` and `agent.max_turns` are sufficient.
