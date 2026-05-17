# AED Merge Action Audit Log

## Overview

The AED merge-action audit log is an append-only JSONL file that records every significant action taken by Hermes during AED PR workflow execution. It lives at:

```
/home/max/.hermes/aed/audit/log.jsonl
```

## Event Types

### `pr_merge`

Recorded when a PR is merged into `main`.

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `audit_log_version` | string | Schema version, currently `"1.0"` |
| `event_type` | string | Must be `"pr_merge"` |
| `timestamp` | string | ISO8601 UTC timestamp of log write |
| `pr_number` | integer | PR number (e.g. `235`) |
| `head_sha` | string | 40-char hex SHA at merge time |
| `merge_sha` | string | 40-char hex SHA of the merge commit |
| `merged_at` | string | ISO8601 UTC timestamp from GitHub |
| `ci_status` | string | `"success"` or failure description |
| `codex_status` | string | `"clean"` or issue summary |
| `scope_status` | string | Scope validation result |
| `authorization_phrase` | string | Human authorization text |
| `hermes_touched` | boolean | Did Hermes write/create any resource? |
| `dispatch_occurred` | boolean | Did Hermes dispatch any agent? |
| `production_board_touched` | boolean | Did Hermes touch the `aed` Kanban board? |
| `gate_catches` | object | Map of gate name to catch description |

**Optional:** `merge_commit_sha`, `merged_by`, `title`

### `controlled_smoke_create`

Recorded when Hermes creates a controlled smoke-test Kanban task.

**Required fields:** `event_type`, `timestamp`, `candidate_id`, `board`, `task_id`

### `external_action`

Recorded for non-Hermes external actions.

**Required fields:** `event_type`, `timestamp`, `action`

### `blocked_action`

Recorded when an action is blocked (e.g. by validation gate).

**Required fields:** `event_type`, `timestamp`, `action`, `reason`

### `audit_correction`

Appended to correct a prior bad row. Does NOT delete or edit the original row.

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `event_type` | string | Must be `"audit_correction"` |
| `timestamp` | string | ISO8601 UTC |
| `corrects_line` | integer | Line number of the row being corrected (1-indexed) |
| `corrects_pr_number` | string | PR number being corrected |
| `correction_reason` | string | Human description of why |
| `replacement_fields` | object | Fields to add/override |
| `created_at` | string | ISO8601 UTC |

## Correction Strategy

**Do NOT edit or delete existing audit rows.** The append-only nature of the log is what makes it auditable.

If a row is bad (wrong SHA, missing fields, etc.), append an `audit_correction` event that:

1. References the bad row by line number (`corrects_line`)
2. References the PR (`corrects_pr_number`)
3. Provides replacement or additional fields in `replacement_fields`

Example:

```json
{
  "event_type": "audit_correction",
  "timestamp": "2026-05-17T00:00:00Z",
  "corrects_line": 14,
  "corrects_pr_number": "234",
  "correction_reason": "head_sha was wrong — updated to correct value",
  "replacement_fields": {
    "head_sha": "44fd2267c19eb1045b929ee33f5471cded09166f"
  },
  "created_at": "2026-05-17T00:00:00Z"
}
```

## Legacy Rows

Prior to the `audit_log_version: "1.0"` schema, some rows were written with:

- `pr_number` as string instead of integer
- `event_type` missing or null
- `gate_catches` missing
- Safety booleans missing

These rows are classified as **legacy** and are handled specially:

- **Non-strict mode (`--allow-legacy`)**: Legacy rows produce warnings, not errors, unless they cause duplicate ambiguity.
- **Strict mode (`--strict`)**: Legacy rows are treated as errors.

## Schema Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-05-16 | Current schema. `pr_number` must be integer, `event_type` required, `gate_catches` required, safety booleans required. |
| pre-1.0 | before 2026-05-16 | Legacy format — string pr_number, missing event_type, missing gate_catches, missing safety booleans. |

## Validator

See `scripts/local/validate_merge_action_audit_log.py` for the read-only consistency validator.

Usage:

```bash
python3 scripts/local/validate_merge_action_audit_log.py \
  --input /home/max/.hermes/aed/audit/log.jsonl \
  --output-json /tmp/report.json \
  --output-md /tmp/report.md \
  --allow-legacy \
  --expected-prs-json '[232,233,234,235]'
```

Flags:
- `--strict`: Treat warnings and legacy rows as errors
- `--allow-legacy`: Allow legacy rows without failing (non-strict default)
- `--expected-prs-json JSON`: PR numbers that must appear in the log

### Expected PR Validation

`--expected-prs-json` hard-requires specified PR numbers to appear in the log:

```bash
python3 scripts/local/validate_merge_action_audit_log.py \
  --input /home/max/.hermes/aed/audit/log.jsonl \
  --output-json /tmp/report.json \
  --output-md /tmp/report.md \
  --allow-legacy \
  --expected-prs-json '[232,233,234,235]'
```

**Non-strict mode behavior:**
- Legacy rows (missing `event_type`, `authorization_phrase`, `gate_catches`) generate warnings but not errors.
- After normalization, legacy PRs count toward the expected-PR set if their PR number matches.
- All expected PRs must be found (ignoring missing optional fields) to pass.

**Strict mode behavior:**
- Legacy rows cause validation to fail even if the PR number is correct.
- Missing required fields (`authorization_phrase`, `gate_catches`, `audit_log_version`, `timestamp`) are always errors.
- Safety booleans stored as strings (`"false"`) instead of booleans are errors.
- Strict mode does not count normalized legacy rows toward the expected-PR set.

**Output JSON shape:**

```json
{
  "overall_status": "valid_legacy",
  "expected_pr_results": [
    {"pr_number": "232", "found": true, "normalized": true},
    {"pr_number": "233", "found": true, "normalized": false},
    {"pr_number": "234", "found": true, "normalized": true}
  ]
}
```

**Normalization rules:**
- PR numbers as integers or strings (`237` vs `"237"`) are equivalent.
- `#237`, `PR #237`, `PR-237` are all normalized to `237`.
- Boolean fields must be actual booleans (`true`/`false`), not string `"true"`/`"false"`.