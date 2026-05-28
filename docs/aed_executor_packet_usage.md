# AED Executor Packet — Usage Guide

> PR #196 adds Executor packet infrastructure only.
> This document describes the EXECUTOR_PACKET.json format and its role in the AED multi-agent chain.
> No LLM is called, no Kanban tasks are created, and no Builder is dispatched by this tooling.

## 1. What is the Executor?

The **Executor** is the second role in the AED chain, after **Tasker**:

```
Tasker → Human selects → Executor → Specifier → Builder → PR Gate → Reviewer → Human merge
```

**Tasker** answers: _"What should AED build next?"_ — producing `ROADMAP_PACKET.json`.

**Executor** answers: _"What is the narrowest safe PR version of this idea?"_ — producing `EXECUTOR_PACKET.json` (also called `PR_PLAN_PACKET.json`).

Executor is **read-only**. It does not edit files, dispatch workers, or call LLMs.

## 2. How Executor Consumes ROADMAP_PACKET.json

The `from-roadmap` subcommand reads a `ROADMAP_PACKET.json`, locates the selected `candidate_id`, and generates a draft `EXECUTOR_PACKET.json`:

```bash
python3 scripts/local/aed_executor_packet.py from-roadmap \
  --roadmap-packet /path/to/aed_tasker_runs/clean_tasker_run_after_pr195/ROADMAP_PACKET.json \
  --candidate-id AED-CAND-202 \
  --output-json /tmp/EXECUTOR_PACKET.json \
  --output-md /tmp/AED_EXECUTION_PLAN.md
```

The generated packet includes:
- `selected_candidate` — copied from Tasker's candidate_prs entry
- `pr_plan` — conservative defaults (branch name, allowed/forbidden files, implementation steps, validation commands, merge policy)
- `gate_config` — conservative CI/Codex/reviewer/human-merge gates
- `split_triggers` — conditions requiring PR to be split before implementation
- `blockers_or_uncertainty` — notes on what is not yet resolved

No LLM is called. All fields are mechanical transforms of the Tasker candidate.

## 3. Why PR #196 is Packet Infrastructure Only

PR #196 implements the `EXECUTOR_PACKET.json` format and validator. It does **not** implement autonomous Executor execution. The Executor role is still a human-assisted planning step:

- Human selects which Tasker candidate to promote
- `from-roadmap` generates the draft packet
- Human reviews the `EXECUTOR_PACKET.json` and adjusts if needed
- Only then does Specifier/Builder receive the bounded PR plan

Autonomous execution (the Executor making decisions without human review) is out of scope for PR #196.

## 4. How Specifier and Builder Consume EXECUTOR_PACKET.json

Specifier reads `EXECUTOR_PACKET.json` to produce an exact Builder prompt. The packet provides:

- `pr_plan.allowed_files` — explicit file boundary for Builder
- `pr_plan.forbidden_files` — explicit non-goals
- `pr_plan.implementation_steps` — step-by-step scope
- `pr_plan.validation_commands` — what to run before committing
- `pr_plan.safety_grep_patterns` — patterns that must not appear
- `gate_config` — CI/Codex/reviewer/human-gate requirements

Builder reads the Specifier prompt (derived from EXECUTOR_PACKET.json) and implements only within `allowed_files`.

## 5. Why Executor Cannot Directly Dispatch Builder Yet

Executor produces a bounded plan but does not itself dispatch work because:

1. **Human selection required** — Tasker output is advisory; a human must choose which candidate to advance.
2. **Scope verification needed** — before implementation, a human or Specifier should verify the plan's allowed/forbidden file boundaries are correct.
3. **Safety gates not automated** — the CI/Codex/reviewer/human-merge gate chain is defined in `gate_config` but the runtime orchestration is not yet wired.
4. **Specimen not approved** — the Builder role's exact scope and stop rules are defined in the design doc but not yet enforced by a Specifier prompt generator.

PR #196 closes the packet format gap. PRs for Specifier and Builder dispatch follow.

## 6. Safety Rules for Executor Packet Tooling

- ❌ Do not call LLMs from `aed_executor_packet.py`
- ❌ Do not create Kanban tasks
- ❌ Do not dispatch workers
- ❌ Do not open PRs automatically
- ❌ Do not mutate registries or ledgers
- ❌ Do not push commits from inside the script
- ❌ Do not use `memory.update`, `skill_manage`, `fact_store`, `delegate_task`, `cronjob`
- ✅ Do validate packets with `validate` subcommand before use
- ✅ Do run `render-md` to produce human-readable review copies
- ✅ Do use `--candidate-id` to select from validated ROADMAP_PACKET.json

## 7. Example from-roadmap Workflow

```bash
# 1. Tasker has already run and produced a roadmap packet
#    (from /path/to/aed_tasker_runs/clean_tasker_run_after_pr195/)

# 2. Human selects AED-CAND-202 as the next candidate

# 3. Executor generates draft packet
python3 scripts/local/aed_executor_packet.py from-roadmap \
  --roadmap-packet /path/to/aed_tasker_runs/clean_tasker_run_after_pr195/ROADMAP_PACKET.json \
  --candidate-id AED-CAND-202 \
  --output-json /tmp/EXECUTOR_PACKET.json \
  --output-md /tmp/AED_EXECUTION_PLAN.md

# 4. Human reviews /tmp/AED_EXECUTION_PLAN.md
#    Adjusts allowed_files, non_goals, implementation_steps if needed

# 5. Validate the adjusted packet before passing to Specifier
python3 scripts/local/aed_executor_packet.py validate /tmp/EXECUTOR_PACKET.json

# 6. Specifier reads EXECUTOR_PACKET.json and produces Builder prompt
#    (future PR — not implemented in PR #196)
```

## 8. CLI Reference

```bash
# Validate an executor packet
python3 scripts/local/aed_executor_packet.py validate <path/to/EXECUTOR_PACKET.json>

# Render as markdown execution plan
python3 scripts/local/aed_executor_packet.py render-md <path/to/EXECUTOR_PACKET.json> \
  [--output <path/to/AED_EXECUTION_PLAN.md>]

# Generate from a ROADMAP_PACKET.json
python3 scripts/local/aed_executor_packet.py from-roadmap \
  --roadmap-packet <path/to/ROADMAP_PACKET.json> \
  --candidate-id AED-CAND-<N> \
  [--output-json <path/to/EXECUTOR_PACKET.json>] \
  [--output-md <path/to/AED_EXECUTION_PLAN.md>]
```

## 9. Example EXECUTOR_PACKET.json

```json
{
  "packet_kind": "aed.executor.plan.v1",
  "schema_version": 1,
  "generated_at": "2026-05-11T00:00:00Z",
  "source_roadmap_packet": {
    "path": "/path/to/aed_tasker_runs/clean_tasker_run_after_pr195/ROADMAP_PACKET.json",
    "packet_kind": "aed.tasker.report.v1",
    "selected_candidate_id": "AED-CAND-202"
  },
  "selected_candidate": {
    "candidate_id": "AED-CAND-202",
    "title": "Add Executor planning packet scaffold and validator",
    "goal": "Implement aed_executor_packet.py scaffold and tests",
    "why_now": "Architecture requires this before Builder can be dispatched",
    "risk_if_skipped": "Executor cannot produce bounded PR plans",
    "risk_if_built_too_early": "Low — isolated to tooling"
  },
  "pr_plan": {
    "pr_title": "tooling: add read-only AED Executor packet scaffold",
    "branch_name": "tooling/aed-executor-packet-scaffold",
    "goal": "Add aed_executor_packet.py with validate/render-md/from-roadmap CLI",
    "non_goals": [
      "Do not call LLMs",
      "Do not dispatch Builder",
      "Do not mutate registries"
    ],
    "allowed_files": [
      "scripts/local/aed_executor_packet.py",
      "tests/test_aed_executor_packet.py",
      "docs/aed_executor_packet_usage.md",
      "docs/current_project_status.md"
    ],
    "forbidden_files": [
      "engine/",
      "schemas/",
      "fixtures/"
    ],
    "implementation_steps": [
      "Write aed_executor_packet.py with validate/render-md/from-roadmap CLI",
      "Write test_aed_executor_packet.py with 19+ tests",
      "Update docs and project status"
    ],
    "expected_tests": [
      "tests/test_aed_executor_packet.py"
    ],
    "validation_commands": [
      "python3 -m compileall scripts/local tests",
      "PYTHONPATH=. python3 -m pytest tests/test_aed_executor_packet.py -q",
      "bash scripts/ci/validate_governance_manifests.sh"
    ],
    "safety_grep_patterns": [
      "requests.post",
      "memory.update",
      "skill_manage",
      "gh pr merge"
    ],
    "merge_policy": {
      "required_authorization_phrase": "I confirm",
      "auto_merge_enabled": false,
      "require_exact_phrase_match": true
    }
  },
  "gate_config": {
    "require_ci_green": true,
    "require_codex_clean": true,
    "require_reviewer_merge_recommendation": true,
    "require_human_merge_authorization": true,
    "max_patch_cycles": 3,
    "codex_cooldown_minutes": 5,
    "codex_unavailable_policy": "block_merge"
  },
  "split_triggers": [
    "Changes touch engine/ or fixtures/ — must split into separate PR",
    "Changes add a new dependency to pyproject.toml or requirements.txt",
    "Allowed files exceed 10 paths — consider splitting by module boundary"
  ],
  "blockers_or_uncertainty": [
    "Executor packet scaffold is draft — real execution requires human candidate selection"
  ]
}
```

## 10. Example Rendered Execution Plan

```
# AED Executor Execution Plan

**Generated**: 2026-05-11T00:00:00Z
**Candidate**: AED-CAND-202 — Add Executor planning packet scaffold and validator
**Source roadmap**: /path/to/aed_tasker_runs/clean_tasker_run_after_pr195/ROADMAP_PACKET.json
**Candidate ID**: AED-CAND-202

## 1. Goal

Implement aed_executor_packet.py scaffold and tests

## 2. Why Now

Architecture requires this before Builder can be dispatched

## 3. Non-Goals

- Do not call LLMs
- Do not dispatch Builder
- Do not mutate registries

## 4. File Boundaries

### Allowed files

- `scripts/local/aed_executor_packet.py`
- `tests/test_aed_executor_packet.py`
- `docs/aed_executor_packet_usage.md`
- `docs/current_project_status.md`

### Forbidden files

- `engine/`
- `schemas/`
- `fixtures/`

## 5. Implementation Steps

1. Write aed_executor_packet.py with validate/render-md/from-roadmap CLI
2. Write test_aed_executor_packet.py with 19+ tests
3. Update docs and project status

## 6. Expected Tests

- `tests/test_aed_executor_packet.py`

## 7. Validation Commands

```bash
python3 -m compileall scripts/local tests
PYTHONPATH=. python3 -m pytest tests/test_aed_executor_packet.py -q
bash scripts/ci/validate_governance_manifests.sh
```

## 8. Safety Grep Patterns

- `requests.post`
- `memory.update`
- `skill_manage`
- `gh pr merge`

## 9. Gate Config

- **Require CI green**: True
- **Require Codex clean**: True
- **Require reviewer merge recommendation**: True
- **Require human merge authorization**: True
- **Max patch cycles**: 3
- **Codex cooldown (minutes)**: 5

## 10. Merge Policy

- **Required authorization phrase**: `I confirm`
- **Auto-merge enabled**: False
- **Require exact phrase match**: True

## 11. Split Triggers

- Changes touch engine/ or fixtures/ — must split into separate PR
- Changes add a new dependency to pyproject.toml or requirements.txt
- Allowed files exceed 10 paths — consider splitting by module boundary
```

## 11. Related Documents

- `docs/aed_tasker_executor_design.md` — architecture design for Tasker and Executor
- `docs/aed_tasker_packet_usage.md` — Tasker packet format and CLI
- `docs/current_project_status.md` — current project state and PR tracking
- `docs/merge_authorization_guard.md` — merge gate guard design