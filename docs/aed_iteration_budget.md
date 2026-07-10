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

1. **CLI argument** (Hermes CLI exposes `max_turns` internally, but the installed
   command-line wrapper may not surface a `--max-turns` flag; check `hermes --help`
   before relying on a CLI override).
2. **`HERMES_MAX_ITERATIONS` environment variable** (supported in `cli.py:3856-3862`).
3. **`agent.max_turns` in `~/.hermes/config.yaml`** (line 14).

### CLI caveat for `max_turns`

The installed Hermes command-line wrapper may not expose a
`--max-turns` flag, even though the underlying agent class
accepts `max_turns` as a constructor argument. Before relying on
a CLI flag, check `hermes --help` to confirm the exact flag name
on your installation. If the flag is not present, use
`HERMES_MAX_ITERATIONS` or the config-file value instead — do not
assume a `--max-turns` override will be honored.

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

After raising the budget, verify the change took effect using
**safe, bounded checks only**. Never launch a real Hermes worker
with a huge iteration value (e.g. `999999`) for validation —
`head`/`tail` only limit displayed output, not the upstream
process, and the run can keep billing model calls.

### Preferred: inspect the resolved config value

The cheapest validation does not run Hermes at all. Read the
value that will actually be passed to the agent:

```bash
grep -E 'max_turns|max_iterations' ~/.hermes/config.yaml
printenv HERMES_MAX_ITERATIONS
```

This confirms the precedence layer you intend (env var, config
file, or hardcoded fallback of 90) is the one that will win.

### Bounded live check (only if Hermes supports a no-op task)

```bash
HERMES_MAX_ITERATIONS=200 hermes run --task "print the resolved max iteration budget and exit"
```

Use this **only if** your Hermes build supports a task that
exits immediately without making real model calls. Otherwise
treat it as an operator manual check, not an automated CI step.
Always pair live checks with a wall-clock timeout
(e.g. `timeout 30s hermes run ...`) so a stuck run cannot keep
billing.

### What NOT to do

- Do **not** validate by setting `HERMES_MAX_ITERATIONS=999999`
  and piping to `head -20`. The upstream Hermes process keeps
  running; only the displayed output is truncated.
- Do **not** disable the iteration cap. Hermes always enforces
  at least the hardcoded fallback (90).
- Do **not** run heavy-mode (`HERMES_MAX_ITERATIONS=200`)
  without a wall-clock timeout for unattended runs.

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
