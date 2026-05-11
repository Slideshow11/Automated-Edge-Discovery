# Merge Authorization Guard

## Purpose

This tool prevents accidental or ambiguous merge authorization. It makes the human authorization phrase explicit, recorded, and verifiable — closing the gap between "report says ready to merge" and "human said go."

## What this tool does

`build_merge_ready_packet.py` reads PR gate data and produces:
- `MERGE_READY_PACKET.json` — machine-readable record
- `MERGE_READY_PACKET.md` — human-readable record

`check_merge_authorization.py` verifies:
- Packet is a valid `aed.merge_ready.v1`
- Packet has not expired (default 72h TTL)
- Provided phrase exactly matches `required_authorization_phrase`
- Current HEAD matches packet head_sha (if `--current-head` supplied)
- No blockers exist
- `recommendation` is exactly `merge`

It exits **0 (authorized)** or **1 (denied)**. It does not merge.

## Why this exists: PR #193 lesson

PR #193 went through a full review cycle. The final gate report ended with:

> "Waiting for Tom's explicit merge authorization before merging."

This phrase was clear in context, but:
- It was a natural-language statement, not a structured artifact
- It could not be programmatically verified
- It did not encode the exact PR number, HEAD SHA, or action
- A future system reading the transcript could not distinguish "I confirm" in a casual discussion from an explicit merge authorization

The authorization guard closes this gap by producing a packet with a specific required phrase that encodes PR number, HEAD, and action.

## The exact authorization phrase

```
I confirm merge PR #<number> at <head_sha>
```

Example:
```
I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df
```

### Why this specific format

The phrase encodes three facts:
1. **Which PR** — `PR #193`
2. **Which exact HEAD** — `at <sha>` (cannot be inferred from PR number alone; HEAD changes between pushes)
3. **The action** — `merge`

Saying only `"I confirm"` or `"merge"` is ambiguous. It could apply to any PR at any HEAD. The guard rejects partial phrases.

### Why "I confirm" alone is too ambiguous

- "I confirm" could mean "I confirm the tests passed" — not the merge itself
- "I confirm merge" could be read as a general statement of approval, not an authorization
- Without the PR number and HEAD SHA, the phrase cannot be linked to a specific state
- Any paraphrasing — "confirmed merge for PR 193" — is also rejected because it cannot be machine-verified

The guard requires the exact phrase. No aliases, no paraphrase, no natural language.

## No auto-merge in this PR

These scripts do not:
- Call `gh pr merge`
- Call `gh pr comment`
- Call GitHub mutation APIs
- Call `hermes kanban`
- Push or commit
- Update memory or create skills

The output of `check_merge_authorization.py` is a human-readable result and an exit code. The actual merge is performed by the human operator using `gh pr merge`.

## Flow: final merge-ready packet

```
1. build_merge_ready_packet.py (or manual construction)
   → MERGE_READY_PACKET.json + MERGE_READY_PACKET.md

2. Human reviews MERGE_READY_PACKET.md
   → copies exact phrase

3. check_merge_authorization.py --packet /tmp/MERGE_READY_PACKET.json --phrase "I confirm merge PR #N at <sha>"
   → exits 0 = authorized, exits 1 = denied

4. Human runs: gh pr merge --squash <pr-number>
   (the guard does not call gh pr merge)
```

## Packet fields

| Field | Description |
|-------|-------------|
| `packet_kind` | Must be `aed.merge_ready.v1` |
| `pr_number` | GitHub PR number |
| `pr_url` | Full GitHub PR URL |
| `base_branch` | Target branch (e.g. `main`) |
| `head_sha` | Exact git SHA at time of packet creation |
| `mergeable` | Boolean — is PR mergeable right now? |
| `ci_status` | e.g. `green`, `pending`, `red` |
| `codex_status` | e.g. `reviewed_clean`, `needs_review`, `unavailable` |
| `reviewer_status` | e.g. `approved`, `pending`, `changes_requested` |
| `changed_files` | List of files changed in this PR |
| `allowed_files` | List of files the PR is permitted to change |
| `generated_at` | UTC timestamp of packet creation |
| `expires_at` | UTC timestamp when packet becomes invalid (default +72h) |
| `required_authorization_phrase` | The exact phrase to pass to the guard |
| `blockers` | List of blocker strings; empty = no blockers |
| `recommendation` | `merge`, `patch`, `block`, or `wait` |

## Building a packet

```bash
python3 scripts/local/build_merge_ready_packet.py \
  --pr-number 194 \
  --pr-url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/194 \
  --base-branch main \
  --head-sha abc123def... \
  --mergeable true \
  --ci-status green \
  --codex-status reviewed_clean \
  --reviewer-status approved \
  --changed-files "docs/README.md,scripts/local/new_script.py" \
  --allowed-files "docs/README.md,scripts/local/new_script.py" \
  --recommendation merge \
  --output-json /tmp/MERGE_READY_PACKET.json \
  --output-md /tmp/MERGE_READY_PACKET.md
```

## Running the guard

```bash
python3 scripts/local/check_merge_authorization.py \
  --packet /tmp/MERGE_READY_PACKET.json \
  --phrase "I confirm merge PR #194 at abc123def..."

# With current HEAD verification:
python3 scripts/local/check_merge_authorization.py \
  --packet /tmp/MERGE_READY_PACKET.json \
  --phrase "I confirm merge PR #194 at abc123def..." \
  --current-head abc123def...
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed — authorized to merge |
| 1 | One or more checks failed — denied |

## Packet expiration

Default TTL is 72 hours. After `expires_at`, the guard rejects the packet even if the phrase is correct and all other checks pass. This prevents stale authorizations from being reused.

To re-authorize after expiration, rebuild the packet (step 1 above) with a fresh timestamp and new phrase.

## Relationship to PR #193

PR #193 built the Tasker input collector. This PR builds the merge authorization guard. A future PR will wire the two together: the collector produces context, a Tasker agent reviews it and produces a ROADMAP_PACKET, then the authorization guard verifies the human's explicit go-ahead before merge.

This PR (PR #194) is purely the authorization mechanism. The Tasker integration is a later PR.