# AED Harness Charter V1

**Effective:** 2026-05-15
**Status:** Active
**Supersedes:** None (initial)

---

## 1. Purpose

This charter defines the AED harness: the architectural layer that turns a raw language model into a governed, auditable, collaborative agent system. It specifies what components exist, what their roles are, what they may read and write, and which actions require explicit human authorization.

This charter encodes the operating contract between the human operator and the agent system. It is not a technical specification for any single component — it is the binding layer that makes the system legible and controllable.

---

## 2. Harness Components

The AED harness consists of:

| Component | Role |
|---|---|
| **Model** | Language model used for reasoning, code generation, and decision support. Treated as a computational resource with non-deterministic output. Not the source of truth for policy. |
| **Human Operator** | Sole source of authorization for constrained actions. Defines scope, approves merges, and receives all significant notifications. |
| **Local Repo** | Working copy of the AED repository at `~/Automated-Edge-Discovery`. All source files, scripts, tests, and documentation live here. |
| **GitHub PRs** | The change management interface. All non-documentation changes to the repo flow through PRs. PRs carry metadata (branch, SHAs, review state) used in audit entries. |
| **CI** | Automated build and test pipeline on GitHub Actions. Runs on exact SHAs. CI status (pass/fail) is a hard gate for merge authorization. |
| **Codex Review** | AI-powered code review via OpenAI Codex CLI. Runs on the head commit of PR branches. Cleans the code surface but is not a security audit. |
| **Hermes Kanban** | Task and artifact tracker. Two boards: `aed-test` (staging/smoke) and `aed` (production). Used to create tasks, track smoke artifacts, and dispatch builders. |
| **Audit Log** | Append-only JSONL file at `~/.hermes/aed/audit/log.jsonl`. Every significant action produces a trace entry. The log is the operational memory of the system. |
| **Prompts** | Explicit instruction text passed to the model at context construction time. Prompt text is not itself a policy document — this charter is. |
| **Smoke Artifacts** | Task objects on the `aed-test` board created via `hermes kanban create` with `--smoke-apply`. Represent controlled, non-production test runs. |
| **Memory** | Persistent per-session context store. Read-only during task execution unless explicit authorization is granted. Writes must be recorded in the audit log. |
| **Skills** | Reusable procedural knowledge stored as markdown files under `~/.hermes/skills/`. Read-only during task execution unless explicit authorization is granted. Writes must be recorded in the audit log. |

---

## 3. Action Categories

All actions the agent can perform fall into one of the following categories:

| Category | Code Identifier | Description |
|---|---|---|
| Read-only inspection | `inspect` | Reading files, querying git, running compile checks, fetching metadata, viewing audit log. No side effects. |
| Local file mutation | `local_write` | Writing or patching files within the local working tree. Scoped to a PR's declared changed files. |
| Repo mutation | `repo_write` | Creating commits, pushing branches, opening PRs. Requires an open PR and a declared scope. |
| GitHub mutation | `github_write` | PR comments, merges, label edits. Merge requires exact SHA authorization from the human operator. |
| Hermes test board mutation | `kanban_test_write` | Creating, moving, or commenting on tasks on the `aed-test` board. Requires explicit smoke authorization for create. |
| Hermes production board mutation | `kanban_prod_write` | Creating, moving, or commenting on tasks on the `aed` board. Requires separate explicit authorization. |
| Dispatch | `dispatch` | Triggering a builder run via `hermes kanban dispatch`. Requires explicit authorization. |
| Memory write | `memory_write` | Writing to the persistent memory store. Requires explicit authorization. All memory writes must appear in audit log. |
| Skill creation or modification | `skill_write` | Creating or patching skill files. Requires explicit authorization. All skill mutations must appear in audit log. |
| External notification | `notify` | Sending messages to Telegram, email, or other external channels. Requires explicit authorization for any production-adjacent target. |

---

## 4. Authorization Rules

Authorization is the explicit grant of permission from the human operator for a constrained action. Authorization is always specific: it names the action, the target, and the condition.

### 4.1 Default Allow: Read-Only Inspection

Read-only inspection is allowed by default when the action is relevant to the declared task. This includes:
- Reading and searching source files
- Running compile checks, linters, or test collectors
- Querying git metadata (log, diff, status, branch)
- Fetching GitHub PR metadata via gh CLI
- Reading the audit log
- Viewing Hermes Kanban board state

### 4.2 Local Repo Edits: Scoped to PR Work

Local file edits are allowed only within the scope of an open PR or a branch that will become a PR. Edits to files outside the PR's declared scope require a new scope authorization. This prevents collateral mutation of unrelated files.

### 4.3 GitHub Merge: Exact SHA Authorization Required

No GitHub merge may proceed without an explicit authorization phrase from the human operator that names the exact 40-character SHA to be merged. The merge command must use `--match-head-commit` or equivalent to confirm the SHA matches. Example authorization phrase:

> MERGE SHA 677410d223f52e5654a46f5d4ce1fc69e4f1acf4 FROM BRANCH feat/merge-action-audit-log INTO main

Partial or approximate SHA references are not sufficient.

### 4.4 Hermes `aed-test` Create: Explicit Smoke Authorization

Creating a task on `aed-test` via `hermes kanban create` requires explicit smoke authorization. This is distinct from production authorization and uses a separate authorization phrase naming the smoke artifact parameters. The `aed-test` board is the staging environment; tasks there do not affect production.

### 4.5 Production Board Mutation: Separate Explicit Authorization

Creating, moving, completing, or archiving tasks on the `aed` board requires explicit production authorization separate from any smoke or test authorization. This authorization must name the board (`aed`), the action, and the task parameters.

### 4.6 Dispatch: Separate Explicit Authorization

Dispatching a builder run via `hermes kanban dispatch` requires explicit dispatch authorization, separate from merge or board authorization. The dispatch authorization must name the task ID and the board.

### 4.7 Memory Writes: Explicit Authorization Required

Writing to the persistent memory store (via any memory write tool) requires explicit authorization from the human operator. The authorization phrase must name what will be stored and why. All authorized memory writes must produce a corresponding entry in the audit log.

### 4.8 Skill Creation or Modification: Explicit Authorization Required

Creating a new skill file or patching an existing skill requires explicit authorization from the human operator. The authorization phrase must name the skill, the change type (create or patch), and the rationale. All authorized skill mutations must appear in the audit log.

---

## 5. Stop Rules

A stop rule is a condition that, when triggered, halts the current operation before any irreversible side effect occurs. Stop rules are evaluated continuously during execution.

| Stop Rule | Condition | Response |
|---|---|---|
| Wrong board | The agent acts on a Hermes board other than the one authorized for the current operation | Halt. Reconfirm board identity. Do not proceed until board is verified. |
| Ambiguous scope | The set of files to be changed is unclear, overlaps with unauthorized files, or cannot be contained within a single coherent PR | Halt. Request explicit scope definition from human operator. |
| Unreviewed external mutation | A mutation to a system outside the repo (GitHub, Hermes, Telegram, external API) has been requested without explicit authorization | Halt. Request authorization. Do not execute the mutation. |
| Stale Codex review | A Codex review exists but is based on a commit that is not the current head of the PR branch | Halt. Request fresh Codex review on current head. Do not proceed to merge. |
| CI not green on exact SHA | CI status on the exact head SHA is not "success" | Halt. Do not authorize merge. Diagnose CI failure before proceeding. |
| Missing audit fields | A trace entry is required but one or more mandatory fields are absent or null | Halt. Complete the required fields. Do not emit an incomplete audit entry. |
| Production board touched unexpectedly | The agent modifies the `aed` board without explicit production authorization | Halt immediately. Report the unauthorized board contact. Do not continue until resolved. |
| Worker run spawned unexpectedly | A background or dispatched worker run is initiated without explicit dispatch authorization | Halt. Confirm authorization. If unauthorized, treat as a governance breach and report. |
| Memory or skill mutation without authorization | A memory write or skill creation/modification occurs without explicit authorization | Halt. Do not complete the write. Request explicit authorization. If already completed, log the unauthorized action as a governance incident. |

---

## 6. What This Charter Does Not Ban

This charter defines boundaries and authorization requirements. It does not:

- Ban the use of memory or skills. Both are permitted read-only resources during task execution. Writes require authorization and audit logging, which is the correct governance pattern.
- Ban any particular tool. The authorization rules specify when and how tools may be used; they do not preemptively disable them.
- Prevent the human operator from granting any authorization at any time. The human operator is the sovereign authority.

The distinction is intentional: a charter that bans resources wholesale creates workarounds and obscures intent. A charter that requires explicit authorization for sensitive operations creates accountability without sacrificing capability.

---

## 7. Amendment

This charter may be amended by any PR that:
1. Modifies this file (`docs/harness_charter_v1.md`) or the companion `trace_policy_v1.md`
2. Passes all gates (scope, Codex, CI, smoke)
3. Receives explicit merge authorization from the human operator naming the exact SHA

No emergency overrides exist. The stop rules are not advisory.