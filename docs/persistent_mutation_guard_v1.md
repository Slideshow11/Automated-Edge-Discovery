# Persistent Mutation Guard v1

**Status:** Experimental — AED Phase 1 prerun guard

---

## Problem

During AED PR #253, a subagent created an unauthorized Hermes skill (`aed-dependency-audit`) and wrote a reference file to `aed-session-patterns`. These mutations occurred silently during an otherwise clean-appearing AED workflow. The Hermes framework's built-in `guard_agent_created: true` setting applies a post-write security scan that only blocks dangerous content — safe-looking skills still pass through.

Existing AED controls address:
- Which repo files change (scope diff)
- CI and automated review gates
- Finalization guard enforcement

They do not address:
- Unauthorized skill creation or modification
- Unauthorized Hermes config changes
- Unauthorized memory or profile writes
- Profile config mutations

## Design

The Persistent Mutation Guard (PMG) is a **snapshot/diff/report/block** mechanism — not a full containerization layer, not a complete staged filesystem, and not a replacement for the finalization guard. It is the practical v1 before overnight unsupervised runs.

```
Phase 1: Snapshot before work
Phase 2: Work happens (AED workflow)
Phase 3: Compare snapshot after work
         → clean  → proceed
         → blocked → BLOCK + report
```

## Monitored State

The PMG monitors these paths under the Hermes root (`$HERMES_HOME` or `~/.hermes`):

| Path | Reason |
|------|--------|
| `skills/` | Skill creation, modification, reference writes |
| `config.yaml` | Global Hermes config |
| `profiles/` | Profile-specific settings |
| `memory/` | Memory provider data |
| `memories/` | USER.md and MEMORY.md profile files |

Files written under `/tmp` during comparison are excluded — snapshots and reports are expected to live there.

## Snapshot Record Schema

Each monitored file produces:

```json
{
  "path": "/home/max/.hermes/skills/project/aed-pr-workflow/SKILL.md",
  "relative_path": "skills/project/aed-pr-workflow/SKILL.md",
  "exists": true,
  "size_bytes": 4096,
  "mtime_ns": 1716326400000000000,
  "sha256": "abc123..."
}
```

Snapshot file:

```json
{
  "guard_version": 1,
  "snapshot_at": "2026-05-18T12:00:00Z",
  "root": "/home/max/.hermes",
  "files": [...]
}
```

## Comparison Result Schema

```json
{
  "guard_version": 1,
  "status": "clean|blocked",
  "files_added": [...],
  "files_removed": [...],
  "files_modified": [...],
  "allowed_changes": [...],
  "blocked_changes": [...],
  "skill_blocked": [...],
  "config_blocked": [...],
  "profile_blocked": [...],
  "memory_blocked": [...],
  "recommendation": "PASS|BLOCK"
}
```

## Default Policy

| Change | Default Policy |
|--------|---------------|
| Skill file added | **BLOCK** |
| Skill file modified | **BLOCK** |
| Skill file removed | **BLOCK** |
| Skill reference added/modified | **BLOCK** |
| Global config modified | **BLOCK** |
| Profile config modified | **BLOCK** |
| USER.md modified | **BLOCK** |
| MEMORY.md modified | **BLOCK** |
| Snapshot/report files under `/tmp` | **Ignored** |

## Allowlist

The PMG supports an optional allowlist JSON file for post-incident recovery or approved changes:

```json
{
  "allowed_paths": [
    "skills/.usage.json"
  ],
  "allowed_reason": "archived unauthorized skill after user approval"
}
```

**Rules:**
- Allowlist entries must be **exact file paths**, not directory globs
- Directory entries in the allowlist are **rejected** (v1 only accepts exact files)
- An allowlisted path + a blocked path in the same compare still returns **BLOCK**
- Symlink escapes cannot bypass matching through normalization
- Allowlist suppresses only the exact listed path

## CLI

```bash
# Phase 1: snapshot current state before work
python3 scripts/local/check_persistent_mutation_guard.py snapshot \
  --root /home/max/.hermes \
  --output /tmp/aed_runs/<run_id>/persistent_state_before.json

# Phase 3: compare state after work
python3 scripts/local/check_persistent_mutation_guard.py compare \
  --root /home/max/.hermes \
  --before /tmp/aed_runs/<run_id>/persistent_state_before.json \
  --output-json /tmp/aed_runs/<run_id>/persistent_state_after.json \
  --output-md /tmp/aed_runs/<run_id>/persistent_state_report.md \
  [--allowlist /tmp/aed_runs/<run_id>/allowlist.json]
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Snapshot written / Compare passed (clean) |
| 1 | Malformed input / missing root / bad snapshot / bad allowlist |
| 2 | Blocked changes detected |

## Incident Regression Coverage

| Incident | Expected Behavior |
|----------|------------------|
| `.hermes/skills/project/aed-dependency-audit/SKILL.md` added | **BLOCK** |
| `.hermes/skills/project/aed-session-patterns/references/skill-creation-guard-agent-created.md` added or modified | **BLOCK** unless explicitly allowlisted |

## Security Properties

- **Permission gates are not enough** — agents can create safe-looking skills that pass security scans. The PMG blocks all skill mutations regardless of content classification.
- **Snapshot isolation** — output files are rejected if they would be written inside the monitored root
- **Symlink safety** — paths are normalized; symlink escapes cannot bypass detection
- **Read-only compare** — the `compare` command does not mutate the monitored root
- **Defense in depth** — the PMG complements `guard_agent_created: true` and the AED behavioral constraints rather than replacing them

## Limitations

- The PMG is a process-level snapshot guard, not a container or namespace isolator. A truly adversarial agent with filesystem access could potentially bypass it.
- Snapshot state is not automatically cleaned up between runs. Run IDs should be unique.
- The PMG does not track open handles or in-flight writes that complete after the snapshot but before the compare.
- v1 allowlist is manual — approved paths must be explicitly listed after user review.
- The PMG does not currently monitor cron job definitions, environment variables, or in-memory state.

## Future Work

- Wire PMG into the autocoder controller run lifecycle (pre-work snapshot, post-work compare with BLOCK on changes)
- Support directory allowlist patterns for approved skill families
- Add support for tracking ephemeral files created during the run that should be ignored
- Integrate PMG snapshots into the quarantine bundle for post-run audit
- Add regression test suite that verifies PMG behavior against known incidents