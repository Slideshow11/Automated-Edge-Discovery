# Worker Dependency Context Policy v1

**Version:** 1
**Status:** Experimental — opensrc read-only context, no authority grant
**Applies to:** Claude Code worker packets with `dependency_context` enabled

---

## Purpose

The Dependency Context Policy v1 defines safe rules for Claude Code to inspect third-party package source code using `opensrc` as a read-only context tool. It prevents the worker from:

- Gaining authority to modify, vendor, or install packages
- Treating dependency cache as part of the allowed repo scope
- Installing new dependencies without human approval

This policy is **packet-only** — no `opensrc` package-age lookup is implemented in this version.

---

## Core Principle

> `opensrc` is **read-only context only**. It does not grant any write authority, installation authority, or repo-modification authority to Claude Code.

---

## Schema

### `dependency_context`

```json
{
  "dependency_context": {
    "enabled": false,
    "tool": "opensrc",
    "opensrc_home": "/tmp/aed_runs/<run_id>/opensrc_cache",
    "packages_to_inspect": [],
    "read_only": true,
    "record_inspected_files": true,
    "rules": [
      "read only dependency inspection only",
      "do not vendor dependency source into repo",
      "do not patch cached dependency source",
      "do not treat dependency cache as allowed source scope",
      "record package name, version, source, and inspected files"
    ]
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Whether dependency context inspection is active |
| `tool` | string | `"opensrc"` | The context tool (only `opensrc` in v1) |
| `opensrc_home` | string | `<workspace>/opensrc_cache` | Run-scoped cache directory |
| `packages_to_inspect` | list[str] | `[]` | Packages to inspect (format: `npm:pkg`, `pypi:pkg`) |
| `read_only` | bool | `true` | Always True — opensrc is never a write tool |
| `record_inspected_files` | bool | `true` | Whether to record inspected file paths |
| `rules` | list[str] | (fixed) | Safety rules for dependency inspection |

### `dependency_install_policy`

```json
{
  "dependency_install_policy": {
    "new_dependencies_allowed": false,
    "requires_human_approval": true,
    "minimum_package_age_days": 14,
    "lockfile_review_required": true,
    "postinstall_scripts_require_approval": true
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `new_dependencies_allowed` | bool | `false` | Whether new dependencies may be installed |
| `requires_human_approval` | bool | `true` | Always required unless explicitly waived |
| `minimum_package_age_days` | int | `14` | Package must exist ≥N days before install |
| `lockfile_review_required` | bool | `true` | Lockfile changes must be reviewed |
| `postinstall_scripts_require_approval` | bool | `true` | Post-install scripts need explicit approval |

---

## OPENSRC_HOME Safety

`opensrc_home` must be a **run-scoped path only**:

| Allowed | Rejected |
|---------|----------|
| `<workspace>/opensrc_cache` | `.hermes/**` |
| `/tmp/aed_runs/<run_id>/opensrc_cache` | `<repo_source_tree>/**` |
| | `/usr/local/lib/**` |
| | Any symlink escape from workspace |

**Rejection rules:**

1. Paths containing `.hermes` are always rejected
2. Paths must be under the run workspace OR under `/tmp/aed_runs/<run_id>/`
3. Symlink resolution is applied — paths that resolve outside allowed zones are rejected

---

## Task Input Behavior

### Input (optional)

```json
{
  "task_id": "task-001",
  "dependency_context": {
    "enabled": true,
    "packages_to_inspect": ["npm:zod", "pypi:requests"]
  },
  "dependency_install_policy": {
    "new_dependencies_allowed": false
  }
}
```

### Output (packet)

When `dependency_context.enabled = false` (default):
```json
{
  "dependency_context": {
    "enabled": false,
    "tool": "opensrc",
    "opensrc_home": "<workspace>/opensrc_cache",
    "packages_to_inspect": [],
    "read_only": true,
    "record_inspected_files": true,
    "rules": [...]
  },
  "dependency_install_policy": {
    "new_dependencies_allowed": false,
    "requires_human_approval": true,
    "minimum_package_age_days": 14,
    "lockfile_review_required": true,
    "postinstall_scripts_require_approval": true
  }
}
```

---

## Markdown Output

When `dependency_context.enabled = true`:

```markdown
## Dependency Context

**Tool:** `opensrc`
**OPENSRC_HOME:** `<workspace>/opensrc_cache`
**Mode:** `read-only`

Allowed package inspection:
- `npm:zod`
- `pypi:requests`

Rules:
- Read only dependency inspection only.
- Do not vendor dependency source into repo.
- Do not patch cached dependency source.
- Do not treat dependency cache as allowed source scope.
- Record package name, version, source, and inspected files.

New dependency installation is not allowed for this task.
```

When `dependency_context.enabled = false`:

```markdown
## Dependency Context

**Tool:** `opensrc` (disabled)

_dependency inspection is not enabled for this task._

New dependency installation is not allowed for this task.
```

When `new_dependencies_allowed = true`:

```markdown
New dependency installation requires human approval, package age check,
lockfile review, and postinstall-script review.
```

---

## What Is NOT Allowed

| Action | Forbidden by default |
|--------|---------------------|
| `opensrc install` | `new_dependencies_allowed = false` |
| Vendoring dependency source into repo | `do_not` rules + cache not in `allowed_files` |
| Modifying cached dependency source | `read_only = true` + cache not in `allowed_files` |
| Treating dependency cache as repo scope | `allowed_files` does not include `opensrc_home` |
| Running `opensrc` without task enablement | `enabled = false` by default |

---

## What Is NOT Implemented (v1)

- **Package age lookup** — `minimum_package_age_days` is a policy field only; no actual PyPI/npm lookup is performed
- **opensrc invocation** — the packet builder records the policy; Claude Code is responsible for invoking `opensrc` if enabled
- **Lockfile diff generation** — `lockfile_review_required` is a policy flag; no automated diff is produced
- **Postinstall script scanning** — flagged for human review but not automated

---

## Relationship to Worker Packet v1

`dependency_context` and `dependency_install_policy` are **optional** top-level fields in the worker packet (`aed.worker.packet.v1`). They are:

- ✅ NOT added to `allowed_files`
- ✅ NOT added to `forbidden_files`
- ✅ NOT modifying the worker's write scope
- ✅ Recorded as separate policy context

The worker packet's hard constraints (`do_not`) remain unchanged and always apply regardless of `dependency_context` settings.

---

## Future Work

- Package age verification via PyPI/npm API
- Automated lockfile diff generation
- Postinstall script risk scoring
- Multiple dependency context sources (not just `opensrc`)
