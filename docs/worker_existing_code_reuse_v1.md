# Existing Code Reuse Checklist v1

**Version:** 1
**Status:** Active
**Applies-to:** All worker packets for Claude Code (`claude_code`) workers
**Schema kind:** `aed.worker.packet.v1`

---

## Goal

Prevent implementation workers from creating parallel helpers, duplicate services, or reimplemented logic that already exists in the codebase. Every worker packet must include an explicit reuse checklist before implementation begins.

---

## Schema

```json
{
  "existing_code_reuse": {
    "enabled": true,
    "enforced": false,
    "search_required": true,
    "reuse_candidates_required": true,
    "service_layer_extraction_required_when_duplicate_runtime_logic_found": true,
    "instructions": [
      "search for existing helpers, services, validators, and utilities before adding new logic",
      "list candidate reusable modules or explain why none apply",
      "prefer reusing existing service-layer logic over creating parallel implementations",
      "if duplication is found, propose extraction or consolidation before adding new code",
      "record the reuse decision in the worker return"
    ],
    "required_return_fields": [
      "existing_code_searches",
      "reuse_candidates",
      "reuse_decision",
      "service_layer_extraction_notes"
    ]
  }
}
```

---

## Defaults (harness-controlled)

| Field | Default | Mutable by task JSON |
|-------|---------|---------------------|
| `enabled` | `true` | Yes |
| `enforced` | `false` | No — always `false` |
| `search_required` | `true` | No — always `true` |
| `reuse_candidates_required` | `true` | No — always `true` |
| `service_layer_extraction_required_when_duplicate_runtime_logic_found` | `true` | No — always `true` |
| `instructions` | 5 default instructions | Yes — task JSON can append |

---

## Task JSON Override Behavior

Task JSON **may** override:
- `enabled` — set to `false` to disable the checklist
- `instructions` — append additional task-specific guidance (never remove defaults)

Task JSON **may NOT** override:
- `enforced = true` — always `false` (advisory only, v1)
- `search_required = false` — always `true`
- `reuse_candidates_required = false` — always `true`

---

## Advisory-Only Enforcement (v1)

`enforced = false` means this checklist is advisory in v1. The worker is expected to follow it but will not be blocked for non-compliance. Future versions may change this.

---

## Markdown Output

When `enabled = true`:

```
## Existing Code Reuse Check

Before implementing:
1. search for existing helpers, services, validators, and utilities before adding new logic
2. list candidate reusable modules or explain why none apply
3. prefer reusing existing service-layer logic over creating parallel implementations
4. if duplication is found, propose extraction or consolidation before adding new code
5. record the reuse decision in the worker return

This does not grant extra authority. You may only edit `allowed_files`.
Service extraction must stay within `allowed_files` or be returned as a blocker.
```

When `enabled = false`:

```
## Existing Code Reuse Check

_existing code reuse check is not enabled for this task_

This does not grant extra authority.
```

---

## Required Return Fields

Workers must include these fields in their return payload:

| Field | Description |
|-------|-------------|
| `existing_code_searches` | List of locations searched for existing code |
| `reuse_candidates` | List of reusable modules found (or empty if none) |
| `reuse_decision` | Decision rationale: which candidate was chosen and why |
| `service_layer_extraction_notes` | Notes on any service-layer extraction opportunities found, or `none` |

---

## Scope and Authority

The existing code reuse checklist:

- **Does NOT grant additional file access** — only `allowed_files` are editable
- **Does NOT expand scope** — no new paths are added to `allowed_files`
- **Does NOT allow service extraction outside `allowed_files`** — extraction outside scope must be returned as a blocker
- **Is purely advisory in v1** — `enforced = false`

---

## Relationship to Other Packet Fields

`existing_code_reuse` coexists with:

- `dependency_context` (PR #249) — independent field, no cross-pollution
- `reuse_check` (v0) — replaced by `existing_code_reuse` in v1 schema
- `dependency_install_policy` (PR #249) — independent field

---

## Audit Log

No audit log entry is required for existing code reuse checklist behavior. The checklist is advisory and fully contained within the packet generation step.

---

## Examples

### Task JSON that enables with extra instructions

```json
{
  "task_id": "impl-001",
  "existing_code_reuse": {
    "enabled": true,
    "instructions": [
      "check the engine/ directory for existing validators first"
    ]
  }
}
```

### Task JSON that disables checklist

```json
{
  "task_id": "simple-001",
  "existing_code_reuse": {
    "enabled": false
  }
}
```

### Task JSON that tries to disable required fields (ignored)

```json
{
  "task_id": "test-001",
  "existing_code_reuse": {
    "enabled": true,
    "search_required": false,
    "enforced": true
  }
}
```

Result: `search_required` remains `true`, `enforced` remains `false`.

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 1 | 2026-05-18 | Initial existing_code_reuse checklist for worker packets |