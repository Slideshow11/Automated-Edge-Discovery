## Summary

Adds a scheduled-run wrapper (`run_pr_gate_watchdog_once.py`) + test suite + usage docs for the PR gate watchdog stack (PRs #189, #190).

### Files

| File | Purpose |
|------|---------|
| `scripts/local/run_pr_gate_watchdog_once.py` | INI-config-aware CLI wrapper; supports summary/compact/json output modes |
| `tests/test_scheduled_pr_gate_watchdog.py` | 19 tests: parse_args, load_config, merge_args, output modes, exit codes, read-only audit, smoke |
| `docs/pr_gate_watchdog_usage.md` | Usage guide: manual invocation, cron setup, escalation states |

### Testing

```
python -m pytest tests/test_scheduled_pr_gate_watchdog.py -v  # 19 passed
python -m pytest -q                                           # 2067 passed, 4 skipped
```

### Read-only gate

This script is **read-only only** — no GitHub mutations, no Kanban ops, no merge, no push.

### Notes

- Depends on `scripts/local/watch_pr_gate_state.py` (PR #190) and `scripts/local/classify_pr_gate_state.py` (PR #189)
- INI config file path passed via `--config` CLI arg or `WATCHDOG_CONFIG` env var
- Exit codes: 0=pass, 2=network error, 3=argument error

---

**Reviewers:** @Slideshow11 (self-review)

**Labels:** tooling, automation, testing, documentation