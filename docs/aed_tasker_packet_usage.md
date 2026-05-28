# AED Tasker Packet Scaffold — Usage Guide

## What this is

`scripts/local/aed_tasker_packet.py` is the **output format validator and memo renderer** for the AED Tasker role. It defines the `ROADMAP_PACKET.json` v1 structure, validates incoming packets, and renders human-readable memos.

This script is the **scaffold only**. It does not run a Tasker agent, call LLMs, perform external research, or create Kanban tasks. Future Tasker agents will emit `ROADMAP_PACKET.json` files that this script validates and renders.

## Relationship to the design doc

See `docs/aed_tasker_executor_design.md` for the full Tasker/Executor/PR Gate architecture. That design doc defines:

- Tasker as the read-only roadmap intelligence layer
- The role chain: Tasker → Human selection → Executor → Specifier → Builder → PR Gate Controller → Reviewer → Human merge
- The packet kinds: `aed.tasker.report.v1`, `aed.executor.plan.v1`, `aed.pr_gate.state.v1`, `aed.review.packet.v1`

PR #192 implements the **output format infrastructure** for Tasker — specifically `aed.tasker.report.v1`. The actual autonomous Tasker agent (that reviews code, docs, schemas, tests, and external research themes) is **not yet built**. That is a future PR.

## Why packet infrastructure first

Building the output format before the agent gives us:

1. **Schema contract** — Executor and Specifier can build against the packet shape before the agent exists
2. **Validation before rendering** — bad packets fail fast with clear error messages
3. **Separation of concerns** — the scaffold is stable and independently testable; the agent can evolve
4. **Safe iteration** — we can test the rendering and validation pipeline with synthetic packets while the agent is being built

## How a future Tasker agent will use these tools

```
PR #192 (packet scaffold)          PR #193 (context collector)       PR #195 (prompt bundle)
────────────────────────────────    ──────────────────────────────       ──────────────────────────
ROADMAP_PACKET.json v1 schema       AED_TASKER_CONTEXT.json              AED_TASKER_PROMPT.md
aed_tasker_packet.py               aed_tasker_collect_context.py       AED_TASKER_RUN_CONFIG.json
  ├── validate                        collects:                       takes context JSON + produces
  │     ROADMAP_PACKET.json              repo path, HEAD, branch       Tasker prompt + run config
  │                                         latest N git commits
  └── render-md                          docs present/absent
        ROADMAP_PACKET.json →             scripts present/absent
          AED_ROADMAP_TASKER_MEMO.md      tests present/absent
                                          schemas present/absent

Future Tasker agent connects the three:
  1. Run aed_tasker_collect_context.py → AED_TASKER_CONTEXT.json
  2. Run aed_tasker_prompt_bundle.py --context-json AED_TASKER_CONTEXT.json
       --output-prompt AED_TASKER_PROMPT.md
       --output-config AED_TASKER_RUN_CONFIG.json
  3. Tasker agent reads AED_TASKER_PROMPT.md + AED_TASKER_CONTEXT.json
  4. Tasker emits ROADMAP_PACKET.json
  5. aed_tasker_packet.py validates and renders memo
  6. Human selects recommended PR
  7. Executor creates the PR
```
Tasker agent (future)                    This scaffold (PR #192)
────────────────────────                 ──────────────────────
reads code, docs, schemas                defines packet schema
reads recent PRs                         validates packet
reads external research                  renders memo
emits ROADMAP_PACKET.json  ───────────►  validate → pass/fail
                                        render-md → AED_ROADMAP_TASKER_MEMO.md
```

The Tasker agent will:
1. Run read-only analysis (code, docs, tests, PRs, external research)
2. Produce a `ROADMAP_PACKET.json` file on disk or in a Kanban comment
3. Call `aed_tasker_packet.py validate <path>` to verify correctness
4. Call `aed_tasker_packet.py render-md <path> --output AED_ROADMAP_TASKER_MEMO.md` to produce the memo

The scaffold never calls the agent — the agent calls the scaffold.

## How Executor will consume ROADMAP_PACKET.json

Executor (future) will:
1. Read `ROADMAP_PACKET.json` from the Tasker handoff
2. Validate it with `aed_tasker_packet.py validate <path>`
3. Select the human-approved candidate from `recommended_next_prs`
4. Produce `PR_PLAN_PACKET.json` (defined in `docs/aed_tasker_executor_design.md`)
5. Pass to Specifier → Builder → PR Gate Controller

## Why Tasker cannot directly dispatch Builder

Tasker is **read-only analysis**. It does not own the implementation authority:

1. **Separation of concerns** — roadmap intelligence does not code
2. **Human authorization required** — Tasker output is advisory; humans select which candidate advances
3. **Safety** — if Tasker could dispatch Builder, it could escalate scope without review
4. **Stop rules** — AED prohibits autonomous search, auto-merge, and automatic promotion

The chain Tasker → Human selection → Executor → Specifier → Builder enforces that every PR has a human decision point before code is written.

## Safety rules for this scaffold

`aed_tasker_packet.py` is intentionally read-only:

- ❌ No LLM API calls
- ❌ No network calls (GitHub API, web requests, etc.)
- ❌ No file writes (only `render-md --output` writes a .md file you explicitly specify)
- ❌ No GitHub mutations (no `gh pr`, no `gh issue create`, no push)
- ❌ No Kanban operations
- ❌ No memory updates
- ❌ No `skill_manage`
- ❌ No Hermes skill creation

The script only reads JSON files, validates them, and writes an optional markdown memo.

## CLI usage

### Validate a packet

```bash
python scripts/local/aed_tasker_packet.py validate path/to/ROADMAP_PACKET.json
# Exit code 0: valid
# Exit code 1: invalid — errors printed to stderr
```

### Render a memo from a packet

```bash
# To stdout
python scripts/local/aed_tasker_packet.py render-md path/to/ROADMAP_PACKET.json

# To a file
python scripts/local/aed_tasker_packet.py render-md path/to/ROADMAP_PACKET.json \
  --output AED_ROADMAP_TASKER_MEMO.md
```

## ROADMAP_PACKET.json v1 structure

```json
{
  "packet_kind": "aed.tasker.report.v1",
  "schema_version": 1,
  "generated_at": "2026-05-11T12:00:00+00:00",
  "repo": {
    "path": "/path/to/Automated-Edge-Discovery",
    "head_sha": "82f05db5e92d4ed5ac2b6d7a8afe6d67f1758ef3",
    "branch": "main",
    "clean_status": "clean"
  },
  "tasker_scope": {
    "input_docs": [],
    "input_code_paths": [],
    "recent_prs_reviewed": [],
    "external_sources_reviewed": [],
    "limitations": ""
  },
  "current_state": {
    "implemented_in_code": [],
    "implemented_in_schema": [],
    "implemented_in_tests": [],
    "implemented_in_docs_only": [],
    "not_implemented": []
  },
  "recent_pr_lessons": [
    {
      "pr_number": 191,
      "title": "scheduled watchdog",
      "lesson": "Codex review caught flag-stripping bug",
      "impact": "high"
    }
  ],
  "drift_risks": [
    { "risk": "...", "severity": "HIGH|MEDIUM|LOW", "mitigation": "..." }
  ],
  "deep_module_assessment": [
    { "module": "...", "status": "healthy|concern|degraded", "concern": "...", "recommended_boundary": "..." }
  ],
  "candidate_prs": [
    {
      "candidate_id": "AED-CAND-001",
      "title": "Add PR gate watchdog",
      "goal": "Watch PR state for CI and Codex signals",
      "why_now": "Foundation for automation layer",
      "allowed_files": ["scripts/local/watch_pr_gate_state.py"],
      "forbidden_files": ["schemas/", "engine/", "fixtures/"],
      "risk_if_skipped": "medium|high|low",
      "risk_if_built_too_early": "medium|high|low",
      "expected_tests": ["test_watch_pr_gate_state.py"],
      "deep_module_boundary": "tooling",
      "estimated_scope": { "files_changed": 1, "新增代码行": 100 },
      "depends_on": []
    }
  ],
  "recommended_next_prs": ["AED-CAND-001"],
  "do_not_build_yet": [
    { "item": "...", "reason": "..." }
  ],
  "open_questions": ["..."],
  "final_recommendation": "AED-CAND-001"
}
```

## Validation rules

The validator enforces:

| Rule | Error |
|------|-------|
| `packet_kind` must be `aed.tasker.report.v1` | fails |
| `candidate_prs` must have ≥ 3 items | fails |
| `recommended_next_prs` must have ≥ 1 item | fails |
| `recommended_next_prs` IDs must exist in `candidate_prs` | fails |
| `candidate_id` values must be unique | fails |
| Every candidate must have `allowed_files` and `forbidden_files` | fails |
| No candidate may allow `~/.hermes` (or subpaths) | fails |
| Registry/ledger file mutations require `registry_mutation_mode: locked\|future` | fails |
| `final_recommendation` must be a valid candidate_id or recognized action (`defer`, `blocked`, `no-candidate`) | fails |
| `generated_at` must be valid ISO-8601 | fails |
| `repo` must have `path`, `head_sha`, `branch`, `clean_status` | fails |

## Rendered memo example

```
# AED Tasker Roadmap Memo

> Generated: 2026-05-11T12:00:00+00:00 | Repo: /path/to/Automated-Edge-Discovery | Head: 82f05db5

## Repository Status
  **Branch:** main
  **Head SHA:** 82f05db5
  **Clean status:** clean

## Recommended Next PRs (Ranked)
  1. **AED-CAND-001** — Add PR gate watchdog
  2. **AED-CAND-002** — Add Tasker packet scaffold

## Final Recommendation
→ **AED-CAND-001**: Add PR gate watchdog
```

## Files in this PR

| File | Purpose |
|------|---------|
| `scripts/local/aed_tasker_packet.py` | Packet validator and memo renderer |
| `tests/test_aed_tasker_packet.py` | 41 tests: validation rules, CLI, no-mutation audit |
| `docs/aed_tasker_packet_usage.md` | This file |
| `docs/current_project_status.md` | Updated |
| `docs/README.md` | Updated |

## What PR #192 does NOT do

- ❌ Does NOT implement autonomous Tasker agent
- ❌ Does NOT call LLMs (GPT-5.5, Codex, MiniMax, etc.)
- ❌ Does NOT perform external research (arxiv, blogWatcher, etc.)
- ❌ Does NOT create Kanban tasks
- ❌ Does NOT dispatch workers
- ❌ Does NOT update memory
- ❌ Does NOT create Hermes skills
- ❌ Does NOT auto-merge PRs
- ❌ Does NOT run the Tasker/Executor role chain

This is purely the **output format infrastructure** — the schema, validator, and renderer that future agents will use.