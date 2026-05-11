# PR Gate Watchdog Usage

## What it is

`watch_pr_gate_state.py` (PR #190) is the core read-only watchdog that classifies a PR's gate state by reading GitHub data. It produces Telegram-friendly output and uses deterministic exit codes.

`run_pr_gate_watchdog_once.py` (PR #191) is a thin wrapper that adds config-file support and a clean CLI interface. It delegates entirely to `watch_pr_gate_state.py`.

## Why it is read-only

The watchdog deliberately cannot:
- Post GitHub comments
- Request Codex review
- Create Kanban tasks
- Edit files
- Push commits
- Merge PRs

This is a safety property. Before any automation layer can act on watchdog output (e.g. posting a comment, opening a task), a human review step is required. The watchdog reports; a person decides.

## Manual usage

```bash
# Summary (default Telegram text)
python scripts/local/watch_pr_gate_state.py \
  --repo-owner Slideshow11 \
  --repo-name Automated-Edge-Discovery \
  --pr-number 191

# Compact single-line
python scripts/local/watch_pr_gate_state.py \
  --repo-owner Slideshow11 \
  --repo-name Automated-Edge-Discovery \
  --pr-number 191 \
  --compact

# JSON (full classifier packet)
python scripts/local/watch_pr_gate_state.py \
  --repo-owner Slideshow11 \
  --repo-name Automated-Edge-Discovery \
  --pr-number 191 \
  --json

# Exit-code-only (cron-friendly, no stdout)
python scripts/local/watch_pr_gate_state.py \
  --repo-owner Slideshow11 \
  --repo-name Automated-Edge-Discovery \
  --pr-number 191 \
  --exit-code-only
```

Or use the wrapper with config file:

```bash
# Config file (INI)
cat > /tmp/pr191.ini << 'EOF'
[watchdog]
repo_owner = Slideshow11
repo_name = Automated-Edge-Discovery
pr_number = 191
output = compact
EOF

python scripts/local/run_pr_gate_watchdog_once.py --config /tmp/pr191.ini
```

Or CLI overrides config:

```bash
python scripts/local/run_pr_gate_watchdog_once.py \
  --config /tmp/pr191.ini \
  --output json \
  --pr-number 192
```

## Escalation states

Each classification has a meaning and suggested next step (human action required):

| Classification | Meaning | Next step |
|---|---|---|
| `ci_pending` | CI checks have not completed | Wait; re-run watchdog after CI finishes |
| `ci_failed` | One or more CI checks failed | Do not merge; investigate failures |
| `codex_request_needed` | PR is open but Codex has not reviewed yet | Request Codex review manually |
| `codex_pending` | Codex review requested, no response yet | Wait for Codex response |
| `codex_suggestions` | Codex provided suggestions | Review suggestions before merge |
| `ready_for_reviewer` | All gates pass; human reviewer needed | Request review |
| `blocked_scope` | PR changes files outside allowed scope | Correct scope before merge |
| `blocked_wrong_base` | PR targets wrong base branch | Retarget or rebase |
| `blocked_pr_closed` | PR was closed without merging | No action needed |
| `blocked_pr_merged` | PR is already merged | No action needed |

## Future cron usage

When a cron job is desired, the watchdog can be run on a schedule. The cron-friendly invocation:

```bash
# Every 15 minutes, watch PR #191
*/15 * * * * cd /home/max/Automated-Edge-Discovery && python scripts/local/watch_pr_gate_state.py \
  --repo-owner Slideshow11 \
  --repo-name Automated-Edge-Discovery \
  --pr-number 191 \
  --exit-code-only \
  >> /home/max/.hermes/cron/output/pr191_watch.log 2>&1
```

Exit codes for scripting:
- `0` — success (any classification)
- `2` — network error (GitHub unreachable)
- `3` — argument error (missing/invalid args)

## No-agent Hermes cron idea

A future Hermes cron job can call the watchdog with no-agent mode and deliver the result to Telegram:

1. Cron fires every N minutes
2. `run_pr_gate_watchdog_once.py` runs with `--output compact`
3. Output is delivered to Telegram (no mutation)
4. If state is `ready_for_reviewer` or `codex_suggestions`, a human is notified
5. If state is `ci_pending` or `codex_pending`, silently retry next cycle
6. If state is `blocked_*`, report and stop polling

This pattern keeps humans in the loop for every action that would affect the repo or GitHub state. The watchdog is the eyes; people are the hands.

## How it fits before task-creation automation

Current automation layers:

```
watch_pr_gate_state.py        → reads, reports (READ ONLY)
run_pr_gate_watchdog_once.py  → wraps, config, CLI (READ ONLY)

Future (not yet implemented):
  Hermes cron → Telegram notification   (notify only)
  Hermes kanban → create review task     (requires human trigger)
  Hermes codex → auto-fix suggestions    (requires human trigger)
```

No automation layer can mutate GitHub, Kanban, or repo state without a human review step. The watchdog provides the signal; humans decide what to do with it.