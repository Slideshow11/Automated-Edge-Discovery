# Nightly Quarantine Autocoder Protocol — v1

## Purpose

Let the AED autocoder work overnight in an isolated git mirror ("quarantine repo"), then export one candidate patch bundle for human review before anything touches the original repo.

The original repo is **read-only** to the overnight agent. All Codex work, all file mutations, all builds and tests happen inside the quarantine mirror. A patch bundle is produced and reviewed before any import into the original repo is authorized.

---

## Quarantine Boundary

### What "quarantine" means

| Property | Original Repo | Quarantine Mirror |
|---|---|---|
| Git remote | `origin` (read-only) | `origin` = quarantine bare repo |
| Hermes board | `aed` (production) | `aed-qa` or `aed-test` (dev) |
| Dispatch | Blocked | Blocked |
| Telegram | Blocked | Blocked |
| Memory / skill mutation | Blocked | Blocked |
| Direct push to original main | Blocked | Blocked |
| Auto-merge | Blocked | Blocked |
| Overnight scheduling | Via Hermes cron | Via Hermes cron |

### Quarantine repo definition

A bare git repo at a fixed path, refreshed each night from the original via `git fetch --all`.

```
/home/max/aed-quarantine/
  bare/                          # bare mirror of original
  workspaces/
    work/                        # working checkout for agent work
    outputs/                     # patch bundles per run
      YYYY-MM-DD/
        bundle/
          base_sha.txt
          final_sha.txt
          changed_files.txt
          diff.patch
          safety_grep.txt
          pytest_summary.txt
          codex_review_summary.md
          risk_notes.md
          proposed_pr_body.md
          import_command.sh
        BUNDLE_STATUS.json       # created | reviewed | imported | rejected
        RUN_REPORT.json
```

### Forbidden operations (hard rules — enforced by Hermès stop-rules, not by convention)

```
ORIGINAL_REPO_IS_READ_ONLY     # agent cannot write to original remotes
NO_HERMES_DISPATCH             # no hermes kanban dispatch, no worker spawning
NO_TELEGRAM_SEND               # no outbound messages during overnight run
NO_MEMORY_UPDATE               # no memory tool, fact_store writes
NO_SKILL_MUTATION              # no skill_manage creates or patches
NO_DIRECT_PUSH_TO_ORIGINAL_MAIN # quarantine work stays in quarantine
NO_AUTO_MERGE                  # no gh pr merge without explicit human authorization
```

---

## Overnight Cycle

### Trigger

Hermes cron job fires once per night (default: 02:00 local). The cron job prompt is self-contained — it does not carry context from previous sessions.

### Cycle steps

```
STEP 1  Refresh quarantine mirror
        cd /home/max/aed-quarantine/bare
        git fetch --all --prune
        git reset --hard origin/main

STEP 2  Create fresh workspace from mirror
        Workspace is a new clone from the quarantine bare.
        NOT reused from previous runs. Previous workspace is archived.

STEP 3  Run AED classifier on each open PR
        python scripts/local/classify_pr_gate_state.py
          --repo-owner Slideshow11
          --repo-name Automated-Edge-Discovery
          --output json
        For each PR: record state, head SHA, CI status.

STEP 4  Identify candidate PRs for autocoder work
        Filter: state=open, codex_status=suggestions, ci=green, review_decision=commented.
        Skip: already has a pending builder-patch task (idempotency key check).

STEP 5  For each candidate PR (max 1 per night):
        5a. Fetch PR head into quarantine workspace
            git fetch origin pull/{N}/head:pr-{N}-review
            git checkout pr-{N}-review

        5b. Generate task draft via Codex
            codex exec -m gpt-5.5 "Analyze PR #{N} changes.
              Review the diff. Identify concrete, bounded code improvements.
              Do not refactor, do not add tests beyond what the PR already covers.
              Return a task_draft JSON for a builder_patch_task."

        5c. Validate task draft schema
            python scripts/local/pr_gate_task_draft.py --validate draft.json

        5d. Check scope (allowed_files / forbidden_files)
            python scripts/local/check_pr_scope.py --draft draft.json --ci-files {files}

        5e. Dry-run smoke of the builder patch plan
            python scripts/local/pr_gate_controller_live_smoke.py
              --real-kanban-create-smoke
              --board aed-test        # NOT production board aed
            This validates the Hermes command structure without executing it.

        5f. Apply the patch to the quarantine workspace
            python scripts/local/pr_gate_kanban_task_create.py
              --task-draft draft.json
              --board aed-qa          # dedicated QA board, not aed-test
              --apply
              --output-json plan.json

        5g. Run the local test suite
            python3 -m compileall scripts/local tests/
            PYTHONPATH=. python3 -m pytest tests/ -q --tb=short

        5h. Run governance validators
            bash scripts/ci/validate_governance_manifests.sh
            bash scripts/ci/validate_event_options_contract.sh

        5i. Run Codex final-head review on the applied patch
            codex exec -m gpt-5.5 "Review the applied diff.
              Focus on correctness, scope creep, stale-review logic,
              and backward compatibility. Return only blocking findings."

STEP 6  Assemble patch bundle
        Collect: base SHA, final SHA, changed files list, unified diff,
        pytest output, governance validator output, Codex review text,
        risk notes, proposed PR body, import script.

STEP 7  Write bundle to output directory
        Mark BUNDLE_STATUS.json as "created".

STEP 8  Deliver notification (human review required)
        Hermes cron delivers bundle summary to the configured home channel.
        Human must explicitly authorize the next step before any import.

### What the cron prompt contains (self-contained, no cross-session memory)

```
You are running the Nightly Quarantine Autocoder for AED.
Repo: /home/max/aed-quarantine/workspaces/work
Output: /home/max/aed-quarantine/workspaces/outputs/YYYY-MM-DD/

Rules:
  Original repo is read-only. Do not push to origin.
  Do not use hermes kanban dispatch.
  Do not send Telegram messages.
  Do not update memory or skills.
  Do not auto-merge.
  Maximum 1 candidate PR per night.

Steps:
  1. Refresh quarantine mirror (git fetch in bare repo).
  2. Clone fresh workspace from mirror.
  3. Run AED classifier on open PRs.
  4. Identify candidate PRs (codex_status=suggestions, ci=green).
  5. For the top candidate: generate draft, validate, dry-run smoke, apply, test.
  6. Assemble patch bundle to output directory.
  7. Return bundle summary with proposed PR body.

Do not proceed to Step 5 without passing Steps 1-4.
Stop and report if any test fails.
```

---

## Task Draft Schema (Quarantine Variant)

Same schema as `aed.pr_gate.task_draft.v1` with quarantine-specific fields added:

```json
{
  "packet_kind": "aed.pr_gate.task_draft.v1",
  "schema_version": 1,
  "idempotency_key": "aed-nightly-{YYYY-MM-DD}-{pr_number}-{digest}",
  "action": "create_builder_patch_task_draft",
  "pr_number": 207,
  "head_sha": "{sha}",
  "quarantine_mode": true,
  "quarantine_repo": "/home/max/aed-quarantine/workspaces/work",
  "controller_rules": {
    "no_auto_dispatch": true,
    "no_telegram": true,
    "no_memory_mutation": true,
    "no_direct_push": true,
    "max_files_changed": 10,
    "max_additions": 500,
    "allowed_file_globs": ["scripts/local/*.py", "tests/*.py"],
    "forbidden_file_globs": [".github/workflows/*", "engine/**", "schemas/**"]
  },
  "task_draft": {
    "title": "Nightly builder patch: {PR title}",
    "body": "See attached patch bundle.",
    "assignee": "aed-builder",
    "allowed_files": ["..."],
    "forbidden_files": [".github/workflows/*", "engine/**", "schemas/**"]
  }
}
```

---

## Patch Bundle Definition

Generated for every candidate PR, regardless of whether the patch is clean or not. Even a rejected bundle is preserved as evidence.

### Directory structure

```
outputs/YYYY-MM-DD/
  bundle/
    metadata.json               # Timestamps, PR number, repo, agent identity
    base_sha.txt              # SHA at start of overnight run
    final_sha.txt             # SHA after patch applied in quarantine
    changed_files.txt         # One path per line, sorted
    diff.patch                # Unified diff from base_sha to final_sha
    scope_check.json          # check_pr_scope.py output
    dry_run_smoke_report.json # pr_gate_controller_live_smoke.py output
    pytest_summary.txt        # stdout from pytest, condensed to failures+summary
    governance_validators.txt # Output from both governance scripts
    codex_review_summary.md   # Codex final-head review text
    risk_notes.md             # Agent-authored risk flags (see Risk Flags below)
    proposed_pr_body.md       # Ready-to-use PR description
    import_command.sh         # See Import Command below
  BUNDLE_STATUS.json          # created | reviewed | imported | rejected
  RUN_REPORT.json              # Full run log, step timings, step outcomes
```

### Scope check schema

```json
{
  "scope_status": "clean | modified | expanded",
  "allowed_files_used": ["..."],
  "forbidden_files_touched": [],
  "file_count": 7,
  "additions_estimate": 312,
  "blockers": []
}
```

### Risk flags (agent-authored, human must verify)

```
risk_high      = [">10 files changed", ">500 additions", "workflow files modified",
                  "schema files modified", "engine files modified",
                  "existing tests removed", "new runtime dependencies"]
risk_medium    = ["test file modified", "CI script modified",
                  "governance file modified", ">200 additions"]
risk_low       = ["docs only", "comment only", "whitespace only", "test added"]
risk_note      = free-text explanation of the most serious risk present
```

---

## Review Gate

Triggered after the cron run delivers its bundle summary. Human reviews the bundle before any import is authorized.

### Gate criteria

```
review_is_stale        = (SHA in bundle != current SHA of original PR)
merge_allowed          = (review_is_stale == false AND risk_high == [])
ci_all_green           = (governance validators passed AND pytest passed)
scope_status           = (scope_check.scope_status == "clean")
codex_clean            = (codex_review.blocking_findings == [])
patch_applies_cleanly  = (verified by re-playing diff against current main)
```

### Gate outcomes

| Condition | Outcome |
|---|---|
| All criteria pass | `MERGE_READY` — human can authorize import |
| Any `risk_high` present | `BLOCK` — must reduce scope before import |
| `review_is_stale == true` | `STALE` — must re-run or re-base |
| `scope_status != clean` | `PATCH` — scope expanded, must narrow |
| `codex_clean == false` | `PATCH` — Codex found correctness issues |
| Governance validators failed | `BLOCK` — governance violation |

### Manual review actions (human only — no automation)

```
import    → Apply the bundle to the original repo, open PR
reject    → Archive bundle as rejected, take no action
rebase    → Request re-run of overnight cycle on current PR head
narrow    → Request specific file exclusions, re-run from Step 5e
```

---

## Import into Original Repo

Authorized only after human explicitly selects `import` at the review gate.

### Import command (human-authorized, not agent-executed)

```bash
#!/bin/bash
# import_command.sh — generated by overnight autocoder, reviewed by human
set -euo pipefail

QUARANTINE="/home/max/aed-quarantine/workspaces/outputs/YYYY-MM-DD/bundle"
ORIGINAL="/home/max/Automated-Edge-Discovery"

BUNDLE_SHA=$(cat "$QUARANTINE/final_sha.txt")
BUNDLE_BASE=$(cat "$QUARANTINE/base_sha.txt")

# Verify patch applies cleanly against current main
cd "$ORIGINAL"
git fetch origin main
git checkout -B nightly-import --track origin/main
git am --empty=drop < "$QUARANTINE/diff.patch"

# Verify resulting SHA matches bundle
RESULT_SHA=$(git rev-parse HEAD)
if [ "$RESULT_SHA" != "$BUNDLE_SHA" ]; then
    echo "SHA mismatch: expected $BUNDLE_SHA, got $RESULT_SHA" >&2
    exit 1
fi

# Open PR (dry-run; remove --dry-run to execute)
gh pr create \
  --repo Slideshow11/Automated-Edge-Discovery \
  --title "$(cat "$QUARANTINE/proposed_pr_body.md" | head -1)" \
  --body "$(cat "$QUARANTINE/proposed_pr_body.md")" \
  --base main \
  --head nightly-import \
  --dry-run   # Remove --dry-run after human authorization

echo "Patch imported. Review PR before merging."
```

---

## Connection to Existing AED Infrastructure

### Reuses

| Component | Used how |
|---|---|
| `classify_pr_gate_state.py` | Step 3 — PR state classification |
| `pr_gate_task_draft.py` | Step 5b/5c — draft generation and validation |
| `check_pr_scope.py` | Step 5d — scope enforcement |
| `pr_gate_controller_live_smoke.py` | Step 5e — dry-run smoke (board=aed-test) |
| `pr_gate_kanban_task_create.py` | Step 5f — apply (board=aed-qa) |
| `validate_governance_manifests.sh` | Step 5h |
| `validate_event_options_contract.sh` | Step 5h |
| Codex OAuth route (`codex exec -m gpt-5.5`) | Steps 5b, 5i |

### Differences from existing live smoke

| Aspect | Existing live smoke | Quarantine autocoder |
|---|---|---|
| Target | Real PR on original repo | Candidate PR in quarantine mirror |
| Hermes board | `aed-test` | `aed-qa` |
| Dispatch | Blocked | Blocked |
| Patch output | None | Full patch bundle |
| Human review gate | N/A | Required before import |
| Scheduling | Manual / CI-triggered | Overnight cron |

---

## Implementation Sequence

### Phase 1 — Protocol script (no scheduling)

**Goal:** One-shot dry-run of the full protocol against a real open PR.

1. Create `scripts/local/run_nightly_quarantine_autocoder.py` (dry-run mode only, `--dry-run` flag)
2. Add `--dry-run` to every step that would mutate — skip if dry-run
3. Validate that the script produces a valid bundle structure
4. Run against a real open PR (e.g. PR #214) in quarantine workspace
5. Verify bundle contains all required files
6. Confirm no mutation of original repo, no dispatch, no Telegram

### Phase 2 — Smoke test

**Goal:** Prove the protocol runs clean in CI before scheduling.

1. Add `quarantine-autocoder-smoke` CI job to existing workflow
2. Target: current `main` HEAD, synthetic task draft
3. Verify bundle assembled, status = `created`
4. Verify no production Hermes calls, no Telegram, no memory mutation

### Phase 3 — Safety grep integration

**Goal:** Automated pre-commit safety checks in the protocol script itself.

Add to `run_nightly_quarantine_autocoder.py`:

```bash
safety_grep() {
  grep -rn "hermes kanban dispatch" scripts/local/*.py && return 1
  grep -rn "gh pr merge" scripts/local/*.py && return 1
  grep -rn "memory.update\|fact_store" scripts/local/*.py && return 1
  grep -rn "skill_manage\|create.*skill" scripts/local/*.py && return 1
  grep -rn "telegram\|send_message" scripts/local/*.py && return 1
  return 0
}
```

Failing the safety grep aborts the run before any mutation.

### Phase 4 — Scheduling (when authorized)

**Goal:** Wire to Hermes cron. Human explicitly authorizes Phase 4 separately.

```bash
hermes kanban cron create \
  --name "aed-nightly-quarantine-autocoder" \
  --schedule "0 2 * * *" \
  --prompt "$(cat scripts/local/nightly_quarantine_prompt.md)" \
  --deliver origin \
  --skills hermes-agent
```

`nightly_quarantine_prompt.md` is the self-contained prompt from Step 8 of the Overnight Cycle.

---

## Kill Switch

If any step fails or any safety grep fires, the run aborts immediately.

```
ABORT conditions:
  - pytest fails
  - governance validators fail
  - scope check returns blockers
  - safety grep finds forbidden pattern
  - Codex review returns blocking findings
  - Hermes dispatch attempted
  - Original repo remote mutated
  - Telegram send attempted
  - Memory/skill mutation attempted
```

Bundle status is set to `aborted-{step}` with a `RUN_REPORT.json` capturing the failure point.

---

## Docs

- `docs/nightly_quarantine_autocoder_protocol_v1.md` — This document
- `docs/nightly_quarantine_quick_ref.md` — One-page operator reference (TBD)
- `scripts/local/run_nightly_quarantine_autocoder.py` — Protocol runner (Phase 1)
- `scripts/local/nightly_quarantine_prompt.md` — Self-contained cron prompt (Phase 4)

---

## Status

**Design: complete. Implementation: not started.**

Phase 1 (protocol script, dry-run mode) is the next recommended step.
