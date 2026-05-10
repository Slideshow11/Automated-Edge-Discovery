# AED Tasker, Executor, and PR gate packet architecture

## 1. Purpose

AED needs a separated multi-agent workflow because roadmap selection, PR planning, implementation, PR state control, and merge recommendation are different risk surfaces.

The architecture divides those concerns into read-only planning roles, a single implementation role, and read-only gate/review roles:

- **Tasker answers:** What should AED build next?
- **Executor answers:** What is the narrowest safe PR version of one approved Tasker idea?
- **PR Gate Controller answers:** How do we move a PR through CI, Codex, patch loops, Reviewer, and final merge readiness?

The design goal is not more autonomy by default. The goal is safer specialization:

- Tasker improves roadmap quality without being able to mutate the repository.
- Executor translates one approved roadmap idea into a PR-sized plan without being able to code.
- Specifier converts that plan into a precise Builder prompt.
- Builder is the only role that edits repository files.
- PR Gate Controller watches the pull request, requests and waits for Codex, and creates patch tasks when needed, but never edits files or merges.
- Reviewer performs guarded review and returns a merge, patch, or block recommendation.
- Human remains the authority for candidate selection and merge authorization.

This architecture preserves AED's current stop rules: no autonomous search, no automated promotion, no live trading, no production execution, no registry mutation, no ledger mutation, and no automatic merge.

## 2. Role definitions

### Tasker

Tasker is the **roadmap intelligence layer**.

Tasker is read-only. It reviews code, docs, tests, schemas, recent PRs, pain points, and external research themes, then recommends the next 3 to 5 PRs.

Tasker may inspect:

- code structure
- docs and design notes
- tests and fixtures
- schemas
- recent merged PRs
- open PRs
- user-supplied priorities
- external methodology themes

Tasker cannot:

- dispatch Builder
- modify repository files
- create commits
- create runtime behavior
- update memory
- use `skill_manage`
- create Hermes skills
- mutate registries or ledgers

Tasker output is advisory. Human selection is required before Executor begins.

### Executor

Executor is the **roadmap-to-PR planner**.

Executor is read-only. It takes one approved Tasker recommendation and writes a Specifier-ready PR plan. It defines the narrowest safe PR shape for the selected idea.

Executor defines:

- exact PR title
- goal
- allowed files
- forbidden files
- tests expected
- acceptance criteria
- reviewer focus
- gate config
- split triggers
- blockers or uncertainty

Executor cannot:

- edit repository files
- dispatch Builder by default
- create commits
- create runtime behavior
- update memory
- use `skill_manage`
- create Hermes skills

Executor output is a planning packet, not an implementation.

### Specifier

Specifier turns the Executor packet into an exact Builder prompt.

Specifier responsibilities:

- preserve all allowed/forbidden file boundaries
- convert acceptance criteria into explicit Builder instructions
- include hard stops and non-goals
- include validation commands
- include expected return format
- ensure Builder scope is small enough for one PR

Specifier does not broaden scope beyond the Executor packet. If the Executor packet is ambiguous, Specifier blocks or asks for clarification rather than filling gaps with implementation choices.

### Builder

Builder performs **code implementation only**.

Builder is the only role permitted to edit repository files. Builder may edit only the files explicitly allowed by the Specifier prompt or patch task. Builder must not modify forbidden paths. Builder must run the requested validation and commit only scoped changes.

Builder cannot:

- choose roadmap priority
- broaden the PR
- merge
- mutate registries or ledgers unless a future explicit PR allows it
- create Hermes skills
- update memory

### PR Gate Controller

PR Gate Controller watches PR state.

It is read-only with respect to repository files. It manages the gate sequence around an existing PR:

- verify current head
- verify changed files against allowed paths
- verify CI/check status
- request Codex review
- wait for Codex review
- classify Codex findings as stale or current-head applicable
- create patch tasks when Codex has current-head suggestions
- request another Codex review after patches
- create Reviewer only when Codex is clean
- prepare final merge readiness packet

PR Gate Controller never edits files and never merges.

### Reviewer

Reviewer performs guarded review only.

Reviewer reads the PR diff, task packets, validation results, Codex state, and project rules. Reviewer returns one of three recommendations:

- **merge** — PR is ready for human merge authorization
- **patch** — PR needs a scoped patch task before merge
- **block** — PR should not proceed without human decision or redesign

Reviewer never edits files.

### Human

Human chooses the candidate PR and authorizes merge.

Human responsibilities:

- choose which Tasker recommendation advances to Executor
- approve or reject Executor scope
- authorize final merge after PR Gate Controller and Reviewer are clean
- decide when to override, defer, or block a role recommendation

## 3. Role chain

The standard chain is:

1. Tasker
2. Human selection
3. Executor
4. Specifier
5. Builder
6. PR Gate Controller
7. Reviewer
8. Human merge authorization

The chain is intentionally asymmetric:

- roadmap intelligence does not code
- planning does not code
- implementation does not merge
- PR gate control does not edit files
- review does not edit files
- only Human authorizes merge

## 4. Tasker inputs

### Required internal inputs

Tasker must review or explicitly mark unavailable these internal inputs:

- `docs/current_project_status.md`
- `docs/runner_trial_accounting_linkage.md`
- `docs/evidence_tiers_and_claim_levels.md`
- `schemas/runner_output_spec_v1.schema.json`
- `engine/edge_discovery/runners/first_thin_real_data_runner.py`
- `tests/test_first_thin_real_data_runner.py`
- `tests/test_runner_artifacts.py`
- `tests/test_observation_table.py`
- `git log --oneline -20`
- recent merged PRs
- open PRs if any

Tasker should treat the repository as authoritative over memory. If a design doc and current schema disagree, Tasker must flag the disagreement rather than selecting an implementation PR that assumes the design doc is already true.

### Required external research themes

Tasker must include a read-only research scan, or a clearly marked deferred scan, across these themes:

- backtest overfitting
- Deflated Sharpe Ratio
- Probability of Backtest Overfitting
- CSCV
- purged and embargoed cross-validation
- trial accounting
- experiment tracking
- evidence-tiered decision workflows
- PR gate automation
- deep module architecture

Tasker should map each theme into AED implications. It should not treat research terms as implementation authorization. For example, identifying CPCV as relevant does not authorize building a full CPCV engine before policy contracts and acceptance boundaries exist.

## 5. Tasker output

Tasker produces two artifacts in its task workspace or Kanban handoff, not in the repository unless a future docs PR explicitly allows it:

- `AED_ROADMAP_TASKER_MEMO.md`
- `ROADMAP_PACKET.json`

The memo and packet must include:

- current AED state
- recent PR lessons
- external research reviewed
- drift risks
- deep module assessment
- 5 to 8 candidate PRs
- ranked next 3 to 5 PRs
- what not to build yet
- questions for Tom and ChatGPT

### `ROADMAP_PACKET.json` minimum shape

```json
{
  "packet_kind": "aed.tasker.report.v1",
  "schema_version": 1,
  "repo": "/home/max/Automated-Edge-Discovery",
  "base_ref": "origin/main",
  "observed_head": "<sha>",
  "generated_at": "<iso8601>",
  "current_state": {
    "summary": "<text>",
    "completed_recent_prs": []
  },
  "research_themes_reviewed": [],
  "drift_risks": [],
  "deep_module_assessment": [],
  "candidate_prs": [
    {
      "candidate_id": "AED-CAND-001",
      "title": "<candidate title>",
      "kind": "docs|tooling|schema|runner|tests",
      "why_now": "<text>",
      "expected_files": [],
      "risk_level": "low|medium|high",
      "blocked_by": []
    }
  ],
  "ranked_next_prs": ["AED-CAND-001"],
  "do_not_build_yet": [],
  "questions_for_tom": [],
  "questions_for_chatgpt": []
}
```

## 6. Executor inputs

Executor inputs are:

- `ROADMAP_PACKET.json`
- selected `candidate_id`
- current repo status
- relevant docs and tests
- open PR conflicts

Executor must verify that the selected candidate still applies to the current repository head. If the candidate conflicts with an open PR or merged change, Executor must either revise the packet or block.

Executor must not infer authorization to implement from Tasker ranking alone. The selected `candidate_id` must be human-approved.

## 7. Executor output

Executor produces three artifacts in its task workspace or Kanban handoff, not in the repository unless a future docs PR explicitly allows it:

- `AED_EXECUTION_PLAN.md`
- `PR_PLAN_PACKET.json`
- `SPECIFIER_TASK_DRAFT.md`

The output must include:

- exact PR title
- goal
- allowed files
- forbidden files
- tests expected
- acceptance criteria
- reviewer focus
- gate config
- split triggers
- blockers or uncertainty

### `PR_PLAN_PACKET.json` minimum shape

```json
{
  "packet_kind": "aed.executor.plan.v1",
  "schema_version": 1,
  "candidate_id": "AED-CAND-001",
  "repo": "/home/max/Automated-Edge-Discovery",
  "base_branch": "main",
  "observed_head": "<sha>",
  "generated_at": "<iso8601>",
  "pr_title": "<exact title>",
  "goal": "<one PR goal>",
  "allowed_files": [],
  "forbidden_files": [],
  "tests_expected": [],
  "acceptance_criteria": [],
  "reviewer_focus": [],
  "gate_config": {
    "base_branch": "main",
    "allowed_files": [],
    "required_checks": [],
    "max_patch_cycles": 3,
    "codex_cooldown_seconds": 300,
    "no_auto_merge": true,
    "human_approval_required": true
  },
  "split_triggers": [],
  "blockers_or_uncertainty": []
}
```

### `SPECIFIER_TASK_DRAFT.md` minimum content

The Specifier draft must include:

- approved candidate id
- source Executor packet id
- exact Builder scope
- allowed files
- forbidden files
- validation commands
- hard stops
- expected completion report format

## 8. Packet design

Kanban comments should carry machine-readable packet blocks using explicit sentinels:

```text
[AED_STATE_JSON_BEGIN]
{
  "packet_kind": "aed.pr_gate.state.v1",
  "schema_version": 1,
  "repo": "/home/max/Automated-Edge-Discovery",
  "observed_head": "<sha>",
  "generated_at": "<iso8601>"
}
[AED_STATE_JSON_END]
```

Rules for packet blocks:

- JSON must be valid UTF-8 JSON.
- Packet blocks must be standalone, not embedded in Markdown code fences.
- Each packet must include `packet_kind`, `schema_version`, `repo`, `observed_head`, and `generated_at` when applicable.
- Human-readable prose may surround the packet, but downstream tools must parse only text between the sentinels.
- Sensitive tokens or credentials must never be included.
- A packet is a report of state, not an authorization to merge.

### Packet kinds

#### `aed.tasker.report.v1`

Produced by Tasker. Describes roadmap state, candidate PRs, rankings, risks, and questions.

Required top-level fields:

- `packet_kind`
- `schema_version`
- `repo`
- `base_ref`
- `observed_head`
- `generated_at`
- `candidate_prs`
- `ranked_next_prs`
- `do_not_build_yet`

#### `aed.executor.plan.v1`

Produced by Executor. Describes one selected PR plan.

Required top-level fields:

- `packet_kind`
- `schema_version`
- `candidate_id`
- `repo`
- `base_branch`
- `observed_head`
- `generated_at`
- `pr_title`
- `goal`
- `allowed_files`
- `forbidden_files`
- `tests_expected`
- `acceptance_criteria`
- `reviewer_focus`
- `gate_config`

#### `aed.pr_gate.state.v1`

Produced by PR Gate Controller. Describes current PR state and next gate action.

Required top-level fields:

- `packet_kind`
- `schema_version`
- `repo`
- `generated_at`
- `pr_number`
- `base_branch`
- `head_branch`
- `observed_head`
- `allowed_files`
- `changed_files`
- `required_checks`
- `checks_state`
- `codex_state`
- `patch_cycle_count`
- `max_patch_cycles`
- `next_action`
- `human_approval_required`

#### `aed.review.packet.v1`

Produced by Reviewer. Describes guarded review outcome.

Required top-level fields:

- `packet_kind`
- `schema_version`
- `repo`
- `generated_at`
- `pr_number`
- `observed_head`
- `scope_result`
- `validation_result`
- `codex_result`
- `findings`
- `recommendation`
- `merge_readiness`

Recommendation enum:

- `merge`
- `patch`
- `block`

## 9. PR gate controller interface

Executor passes `gate_config` to PR Gate Controller through `PR_PLAN_PACKET.json` and the Specifier/Builder handoff.

The gate config must include:

- PR number, once the PR exists
- base branch
- allowed files
- required checks
- max patch cycles
- Codex cooldown
- no auto-merge policy
- human approval requirement

### Example `gate_config`

```json
{
  "pr_number": 188,
  "base_branch": "main",
  "allowed_files": [
    "docs/aed_tasker_executor_design.md",
    "docs/current_project_status.md",
    "docs/README.md"
  ],
  "required_checks": [
    "validator",
    "governance-validators",
    "test (3.11)",
    "edge-discovery-tests"
  ],
  "max_patch_cycles": 3,
  "codex_cooldown_seconds": 300,
  "no_auto_merge": true,
  "human_approval_required": true
}
```

### PR Gate Controller loop

1. Verify PR exists and head branch matches the expected branch.
2. Resolve current head SHA.
3. Compare changed files against `allowed_files` and forbidden patterns.
4. Verify required checks are complete and successful.
5. Request Codex review for the current head.
6. Wait for Codex response or clean reaction.
7. If Codex suggestions apply to current head, create a scoped Builder patch task.
8. After Builder patch, re-run steps 2 through 7.
9. If patch cycle count exceeds `max_patch_cycles`, block for Human decision.
10. When Codex and checks are clean, create Reviewer task.
11. If Reviewer recommends merge, produce final packet for Human merge authorization.

PR Gate Controller never edits files and never merges.

## 10. Safety rules

The architecture has the following hard safety rules:

- Tasker cannot edit files.
- Executor cannot edit files.
- PR Gate Controller cannot edit files.
- Reviewer cannot edit files.
- Only Builder edits files.
- Only Human authorizes merge.
- No registry mutation.
- No ledger mutation.
- No live trading.
- No production execution.
- No broker behavior.
- No external downloads unless explicitly approved.
- No memory updates.
- No `skill_manage`.
- No Hermes skill creation.

Additional AED stop rules remain active:

- no autonomous search
- no Bayesian optimization
- no genetic programming
- no automatic registry mutation
- no automated promotion
- no GCRU integration until explicitly designed and approved

## 11. Model routing

Default role routing:

- **Tasker:** GPT-5.5 via `openai-codex`
- **Executor:** GPT-5.5 via `openai-codex`
- **Specifier:** GPT-5.5 via `openai-codex`
- **Builder:** GPT-5.5 for hard code; GPT-5.3-Codex for routine patches; MiniMax 2.7 backup
- **Reviewer:** GPT-5.5 via `openai-codex`
- **PR Gate Controller:** deterministic checks first; GPT-5.5 only if reasoning is needed

Routing principles:

- deterministic checks precede model reasoning
- read-only roles should prefer high-reasoning models over fast patch models
- Builder model choice depends on code difficulty and rate-limit state
- fallback models do not relax safety rules
- model routing must be recorded in task metadata or packet comments for auditability

## 12. Recommended implementation roadmap

Recommended sequence:

- **PR #188:** docs: design AED Tasker, Executor, and gate packets
- **PR #189:** tooling: add read-only PR gate classifier
- **PR #190:** tooling: implement read-only AED Tasker
- **PR #191:** tooling: implement AED Executor / PR planner

The sequence intentionally starts with design and read-only classification before any roadmap automation. PR #189 should not begin until PR #188 is reviewed and merged.

## 13. What not to build yet

Do not build yet:

- auto-merge
- fully automatic patch swarm
- live trading
- broker integration
- full CPCV engine before policy contracts exist
- cross-project memory or skill mutation
- one agent that both prioritizes and codes

These remain deferred because they combine multiple risk surfaces: search pressure, code mutation, evidence claims, and merge authority.

## 14. Evaluation metrics

Evaluate Tasker, Executor, and PR Gate Controller with these metrics:

- **roadmap adoption rate** — fraction of Tasker recommendations selected by Human
- **recommendation hit rate** — fraction of selected recommendations that produce useful merged PRs
- **scope fidelity** — whether final changed files match Executor allowed files and non-goals
- **patch-cycle count** — number of Builder patch loops required after initial PR creation
- **Reviewer reversal rate** — how often Reviewer reverses Tasker/Executor assumptions
- **human override rate** — how often Human overrides recommended next action
- **source quality ratio** — proportion of recommendations grounded in repo evidence and research references
- **compression burden** — amount of context needed to hand off cleanly between roles
- **end-to-end latency** — elapsed time from Tasker report to merge-ready packet

These metrics should be reported descriptively first. Numeric thresholds should be introduced only after several PRs establish baseline behavior.

## 15. Implementation status

This PR is design only.

It does not add runtime behavior.

It does not add schemas.

It does not add tests.

It does not enable Tasker or Executor.

It does not enable PR Gate Controller runtime behavior.

It does not enable auto-merge.

It does not enable autonomous search.

It does not change Builder, Reviewer, or Kanban behavior.

It does not mutate registries or ledgers.

It does not change live trading, production execution, or broker behavior.
