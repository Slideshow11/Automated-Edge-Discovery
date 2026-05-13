# Merge Authorization Guard

Read-only authorization system that verifies MERGE_READY_PACKET and optional REVIEW_EVIDENCE_PACKET before merge. Does NOT merge, does NOT post comments, does NOT update memory.

## Overview

```
                    ┌─────────────────────────────────────────┐
                    │  MERGE_READY_PACKET.json                │
                    │  (from build_merge_ready_packet.py)     │
                    └──────────────┬──────────────────────────┘
                                   │ check_merge_authorization.py
                    ┌──────────────▼──────────────────────────┐
                    │  Human phrase:                          │
                    │  "I confirm merge PR #N at <sha>"       │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │  ✅ AUTHORIZED  ──►  gh pr merge ...    │
                    │  ❌ DENIED      ──►  Fix failures first  │
                    └─────────────────────────────────────────┘
```

When a REVIEW_EVIDENCE_PACKET.json is also supplied, additional checks are run to ensure the review was performed on the exact current HEAD (not a stale commit) before presenting the PR for merge authorization.

---

## REVIEW_EVIDENCE_PACKET

**Packet kind:** `aed.pr_gate.review_evidence.v1`

### Purpose

Removes ambiguity between:
- GitHub Codex review on the final head
- Codex CLI fallback review on the final head
- Stale review on an older commit
- Current PR head
- CI status on the current head
- Changed-file scope

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `packet_kind` | string | Must be `aed.pr_gate.review_evidence.v1` |
| `schema_version` | int | Must be `1` |
| `generated_at` | string | ISO 8601 timestamp |
| `repo_owner` | string | e.g. `Slideshow11` |
| `repo_name` | string | e.g. `Automated-Edge-Discovery` |
| `pr_number` | int | PR number |
| `current_head_sha` | string | Full 40-char SHA of current HEAD |
| `reviewed_head_sha` | string | Full 40-char SHA that was reviewed |
| `review_source` | string | One of: `github_codex`, `codex_cli_fallback`, `reviewer`, `none` |
| `review_status` | string | One of: `clean`, `suggestions`, `pending`, `unavailable`, `stale`, `missing`, `unknown` |
| `review_is_stale` | bool | `true` if `reviewed_head_sha != current_head_sha` |
| `codex_github_review_id` | string? | Optional GitHub Codex review ID |
| `codex_cli_fallback_id` | string? | Optional Codex CLI fallback ID |
| `ci_status` | string | CI status (e.g. `green`, `red`) |
| `ci_required_jobs` | list[str] | Required CI job names |
| `ci_all_green` | bool | `true` only if `ci_status == "green"` |
| `changed_files` | list[str] | Files changed in this PR |
| `allowed_files` | list[str] | Files permitted by PR scope |
| `scope_status` | string | `clean` or `dirty` |
| `mergeable` | bool | Whether PR has no merge conflicts |
| `merge_allowed` | bool | Derived: all gates pass |
| `blockers_or_uncertainty` | list[str] | List of blockers if `merge_allowed` is false |
| `recommended_merge_command` | string | Full `gh pr merge` command with `--match-head-commit` |

### Allowed values

**`review_source`:** `github_codex`, `codex_cli_fallback`, `reviewer`, `none`

**`review_status`:** `clean`, `suggestions`, `pending`, `unavailable`, `stale`, `missing`, `unknown`

**`scope_status`:** `clean`, `dirty`

### Required CI jobs

By default, the following jobs are required:
- `test`
- `validator`
- `governance-validators`
- `pr-gate-live-smoke`

### Merge allowed logic

`merge_allowed = True` only when ALL of the following are true:

1. `review_is_stale == False` (i.e., `reviewed_head_sha == current_head_sha`)
2. `review_source` is one of: `github_codex`, `codex_cli_fallback`, `reviewer`
3. `review_status == "clean"`
4. `ci_all_green == True`
5. `scope_status == "clean"`
6. `mergeable == True`

### Stale review behavior

When `reviewed_head_sha != current_head_sha`, `review_is_stale` is set to `true` and `merge_allowed` is set to `false`.

This means:
- GitHub Codex review that was done on an older commit does not count for the current HEAD
- A new review must be performed on the exact final HEAD before merge authorization can be given

### Recommended merge command

```bash
gh pr merge {pr_number} \
  --repo {repo_owner}/{repo_name} \
  --squash --delete-branch --match-head-commit {current_head_sha}
```

The `--match-head-commit` flag ensures GitHub rejects the merge if the PR head has changed since the authorization was given.

---

## check_merge_authorization.py

### Usage

```bash
python3 scripts/local/check_merge_authorization.py \
  --packet /tmp/MERGE_READY_PACKET.json \
  --phrase "I confirm merge PR #207 at abc1230000000000000000000000000000000000" \
  [--current-head abc1230000000000000000000000000000000000] \
  [--review-evidence /tmp/REVIEW_EVIDENCE.json]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--packet` | Yes | Path to `MERGE_READY_PACKET.json` |
| `--phrase` | Yes | Exact authorization phrase |
| `--current-head` | No | Verify current HEAD matches packet SHA |
| `--review-evidence` | No | Path to `REVIEW_EVIDENCE_PACKET.json` |

### Exit codes

- `0` — All checks passed, merge authorized
- `1` — One or more checks failed, merge denied

### Checks run

**Without `--review-evidence`:**
- `packet_kind` — correct packet kind
- `required_fields` — all required fields present
- `not_expired` — packet has not expired
- `phrase_match` — exact phrase match
- `head_sha_match` — HEAD SHA matches (if `--current-head` provided)
- `no_blockers` — no blockers in packet
- `recommendation_is_merge` — recommendation is `merge`

**With `--review-evidence` (additional checks):**
- `review_evidence_packet_kind` — correct packet kind
- `review_not_stale` — `review_is_stale != True`
- `merge_allowed` — `merge_allowed == True`
- `current_head_sha_match` — current HEAD matches packet (if `--current-head` provided)
- `ci_all_green` — `ci_all_green == True`
- `scope_clean` — `scope_status == "clean"`
- `review_status_clean` — `review_status == "clean"`

---

## build_merge_ready_packet.py

Can build both MERGE_READY_PACKET and REVIEW_EVIDENCE_PACKET.

### Build MERGE_READY_PACKET (existing behavior)

```bash
python3 scripts/local/build_merge_ready_packet.py \
  --pr-number 207 \
  --pr-url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/207 \
  --base-branch main \
  --head-sha abc1230000000000000000000000000000000000 \
  --mergeable true \
  --ci-status green \
  --codex-status reviewed_clean \
  --reviewer-status approved \
  --changed-files "docs/README.md" \
  --allowed-files "docs/README.md" \
  --recommendation merge \
  --output-json /tmp/MERGE_READY_PACKET.json \
  --output-md /tmp/MERGE_READY_PACKET.md
```

### Build REVIEW_EVIDENCE_PACKET

```bash
python3 scripts/local/build_merge_ready_packet.py \
  --build-review-evidence \
  --pr-number 207 \
  --pr-url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/207 \
  --base-branch main \
  --head-sha abc1230000000000000000000000000000000000 \
  --mergeable true \
  --ci-status green \
  --codex-status reviewed_clean \
  --reviewer-status approved \
  --changed-files "docs/README.md" \
  --allowed-files "docs/README.md" \
  --recommendation merge \
  --review-source github_codex \
  --review-status clean \
  --review-evidence-output-json /tmp/REVIEW_EVIDENCE.json \
  --review-evidence-output-md /tmp/REVIEW_EVIDENCE.md
```

### Build both in one run

```bash
python3 scripts/local/build_merge_ready_packet.py \
  --pr-number 207 \
  --pr-url ... \
  # ... MERGE_READY_PACKET args ... \
  --build-review-evidence \
  --review-source github_codex \
  --review-status clean \
  --review-evidence-output-json /tmp/REVIEW_EVIDENCE.json
```

---

## pr_gate_merge_ready_notify.py

Supports `--review-evidence` in both CLI parameter mode and packet mode.

### CLI parameter mode

```bash
python3 scripts/local/pr_gate_merge_ready_notify.py \
  --pr-number 207 \
  --pr-url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/207 \
  --head-sha abc1230000000000000000000000000000000000 \
  --ci-status green \
  --codex-status clean \
  --fallback-review-status clean \
  --reviewer-status approved \
  --scope-status clean \
  --mergeable \
  --changed-file docs/README.md \
  --output-json /tmp/notification.json \
  --output-md /tmp/notification.md \
  --review-evidence /tmp/REVIEW_EVIDENCE.json
```

### Packet mode

```bash
python3 scripts/local/pr_gate_merge_ready_notify.py \
  --merge-ready-packet /tmp/MERGE_READY_PACKET.json \
  --controller-run-packet /tmp/CONTROLLER_RUN_PACKET.json \
  --output-json /tmp/notification.json \
  --output-md /tmp/notification.md \
  --review-evidence /tmp/REVIEW_EVIDENCE.json
```

### Review evidence in notification output

When `--review-evidence` is supplied, the output JSON includes a `review_evidence_summary` field:

```json
{
  "packet_kind": "aed.pr_gate.merge_ready_notification.v1",
  "review_evidence_summary": {
    "review_source": "github_codex",
    "reviewed_head_sha": "abc1230000000000000000000000000000000000",
    "current_head_sha": "abc1230000000000000000000000000000000000",
    "review_is_stale": false,
    "ci_all_green": true,
    "scope_status": "clean",
    "merge_allowed": true,
    "review_status": "clean"
  },
  ...
}
```

If `merge_allowed` is `false` or `review_is_stale` is `true`, the notification sets `recommendation` to `not_merge_ready` and omits the authorization phrase.

---

## Workflow

1. Build `MERGE_READY_PACKET.json` with `build_merge_ready_packet.py`
2. Build `REVIEW_EVIDENCE_PACKET.json` with `build_merge_ready_packet.py --build-review-evidence`
3. Run `check_merge_authorization.py --packet MERGE_READY_PACKET.json --phrase "<phrase>" --review-evidence REVIEW_EVIDENCE_PACKET.json`
4. If all checks pass, run the `recommended_merge_command` manually

---

## Authorization phrase

The authorization phrase must include the **exact full 40-character SHA** of the confirmed PR head:

```
I confirm merge PR #207 at abc1230000000000000000000000000000000000
```

### Exact SHA requirement

The agent must **never** substitute, infer, correct, or use the confirmed PR head SHA in place of the user-provided SHA.

If the authorization phrase SHA does not exactly equal the current PR head SHA, the merge must be **blocked**.

**Required:** A fresh authorization phrase using the exact full 40-character current head SHA.

Specific rules:
- **Short SHA prefixes (7 characters) are not accepted.** A 7-character SHA is insufficient — only a full 40-character SHA is valid.
- **39-character SHAs are not accepted.** The SHA must be exactly 40 hex characters.
- **One wrong character is not acceptable.** A 40-character SHA that differs by a single character from the current head is rejected.
- **Substitution is forbidden.** If Tom authorizes SHA A but the PR head is now SHA B, the agent must not use SHA B. A new authorization phrase with the exact SHA B is required.

### SHA sources and priority

When checking authorization:

1. `--current-head` (CLI argument) is the **primary authority** — if provided, the phrase SHA must match this exactly
2. `packet.authorization_head_sha` is the **packet authority** — if `--current-head` is absent, the phrase SHA must match this
3. `packet.head_sha` is the **backward-compat fallback** — used if `authorization_head_sha` is absent

The merge packet also includes:
- `authorization_head_sha` — the SHA that must appear in the authorization phrase
- `head_sha_source` — `"packet"` when sourced from the packet (or `"current_head_cli"` if from `--current-head`)

### Why this prevents the PR #207 failure mode

In the PR #207 incident, Tom provided an authorization phrase for SHA A, but the PR head had moved to SHA B. The agent substituted SHA B and proceeded.

With exact-SHA enforcement:
- The authorization phrase contains SHA A
- The guard checks: does phrase SHA == current head SHA?
- They differ → blocker `authorization_sha_mismatch`
- Merge denied, fresh authorization with SHA B required

---

## Backward compatibility

Without `--review-evidence`, `check_merge_authorization.py` behaves exactly as before — all existing checks run, and no new behavior is introduced.

Without `--review-evidence`, `pr_gate_merge_ready_notify.py` produces the same notification as before, without the `review_evidence_summary` field.

---

## PR scope diff enforcement

The merge gate includes a mechanical scope check that compares actual changed files against `allowed_files` and `forbidden_files`. **Prompts are not enforcement.** The AED control plane preserves `allowed_files` and `forbidden_files` in Kanban task plans, but the merge gate must verify scope mechanically.

### check_pr_scope.py

```
python3 scripts/local/check_pr_scope.py \
  --changed-files "scripts/local/foo.py,tests/test_foo.py" \
  --allowed-files "scripts/local/foo.py,tests/test_foo.py" \
  --forbidden-files ".github/workflows/**,engine/**,schemas/**" \
  --output-json /tmp/PR_SCOPE_CHECK.json
```

Or with JSON file inputs:

```
python3 scripts/local/check_pr_scope.py \
  --changed-files-json /tmp/changed_files.json \
  --allowed-files-json /tmp/allowed_files.json \
  --forbidden-files-json /tmp/forbidden_files.json \
  --output-json /tmp/PR_SCOPE_CHECK.json
```

### Packet: aed.pr_gate.scope_check.v1

| Field | Type | Description |
|---|---|---|
| `packet_kind` | string | `aed.pr_gate.scope_check.v1` |
| `schema_version` | int | `1` |
| `generated_at` | string | ISO 8601 timestamp |
| `changed_files` | list[str] | Files that differ from base |
| `allowed_files` | list[str] | Allowed file patterns (glob) |
| `forbidden_files` | list[str] | Forbidden file patterns (glob) |
| `scope_status` | string | `clean`, `violation`, or `unknown` |
| `out_of_scope_files` | list[str] | Changed files not matched by allowed |
| `forbidden_files_touched` | list[str] | Changed files matching forbidden |
| `blockers` | list[str] | List of blockers if scope_status != clean |
| `passed` | bool | `true` only when `scope_status == clean` |

### Matching rules

**Glob patterns supported:**
- `docs/**` — matches `docs/` and all subdirectories
- `scripts/local/*.py` — matches `scripts/local/foo.py`
- `*.md` — matches any `.md` file at the root

**Normalization:** `./` prefix stripped, `\` converted to `/`.

**Blockers:**
- `allowed_files_missing` — `allowed_files` is empty
- `changed_file_outside_allowed_scope` — one or more changed files not matched by allowed
- `forbidden_file_touched` — one or more changed files match forbidden patterns

### Exit codes

- `0` — `scope_status == clean`, merge-ready
- `1` — `scope_status == violation` or `unknown`, merge blocked
- `2` — invalid arguments, missing file, malformed JSON

### Example scope violation output

```
scope_status=violation passed=False
  blocker: changed_file_outside_allowed_scope
  blocker: forbidden_file_touched
```

---

## Stop rules (enforced in pr_gate_merge_ready_notify.py)

- `no_auto_merge` — never auto-merge
- `no_dispatch` — never dispatch workers
- `no_patch` — never auto-patch
- `no_memory_update` — never update memory
- `no_skill_manage` — never modify skills

---

## Example: Full workflow

```bash
# 1. Build merge ready packet
python3 scripts/local/build_merge_ready_packet.py \
  --pr-number 207 --pr-url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/207 \
  --base-branch main --head-sha abc1230000000000000000000000000000000000 \
  --mergeable true --ci-status green --codex-status reviewed_clean \
  --reviewer-status approved --changed-files "docs/README.md" \
  --allowed-files "docs/README.md" --recommendation merge \
  --output-json /tmp/MERGE_READY_PACKET.json

# 2. Build review evidence packet
python3 scripts/local/build_merge_ready_packet.py \
  --build-review-evidence \
  --pr-number 207 --pr-url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/207 \
  --base-branch main --head-sha abc1230000000000000000000000000000000000 \
  --mergeable true --ci-status green --codex-status reviewed_clean \
  --reviewer-status approved --changed-files "docs/README.md" \
  --allowed-files "docs/README.md" --recommendation merge \
  --review-source github_codex --review-status clean \
  --review-evidence-output-json /tmp/REVIEW_EVIDENCE.json

# 3. Verify authorization
python3 scripts/local/check_merge_authorization.py \
  --packet /tmp/MERGE_READY_PACKET.json \
  --phrase "I confirm merge PR #207 at abc1230000000000000000000000000000000000" \
  --current-head abc1230000000000000000000000000000000000 \
  --review-evidence /tmp/REVIEW_EVIDENCE.json

# 4. If authorized, merge manually
gh pr merge 207 \
  --repo Slideshow11/Automated-Edge-Discovery \
  --squash --delete-branch \
  --match-head-commit abc1230000000000000000000000000000000000
```