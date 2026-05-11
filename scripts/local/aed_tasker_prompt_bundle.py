#!/usr/bin/env python3
"""Read-only AED Tasker prompt bundle generator.

Reads AED_TASKER_CONTEXT.json (from aed_tasker_collect_context.py),
validates required fields, and generates:
  - AED_TASKER_PROMPT.md       — full Tasker agent prompt
  - AED_TASKER_RUN_CONFIG.json — run configuration and output contract

Must NOT call LLMs, mutate GitHub, create Kanban tasks, update memory,
or make network calls. Designed to be safe for any context.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Safety constants ──────────────────────────────────────────────────────────

HERMES_PREFIX = "/home/max/.hermes"
FORBIDDEN_OUTPUT_PREFIXES = (HERMES_PREFIX,)

# Context schema accepted by this tool.
# aed_tasker_collect_context.py emits a nested schema:
#   { repo: {path, branch, head_sha, clean}, docs, scripts, tests, schemas,
#     summary: {docs_present, scripts_present, tests_present, schemas_present},
#     recent_commits: [{sha, short_sha, subject, author, date}] }
#
# This tool normalizes to the flat shape expected by the prompt builder.
COLLECTOR_SCHEMA_FIELDS = {
    "repo",        # nested {path, branch, head_sha, clean}
    "docs",        # dict of doc info
    "scripts",     # dict of script info
    "tests",       # dict of test info
    "schemas",     # dict of schema info
    "summary",     # {docs_present, scripts_present, tests_present, schemas_present}
    "recent_commits",
}

# ── Validation helpers ───────────────────────────────────────────────────────


class ValidationError(Exception):
    """Raised when context or arguments fail validation."""
    pass


def validate_context_fields(context: dict) -> list[str]:
    """Check required fields from the collector schema.

    Accepts the nested schema emitted by aed_tasker_collect_context.py:
      { repo, docs, scripts, tests, schemas, summary, recent_commits }

    Returns list of missing field names (empty = valid).
    """
    missing = []
    for field in COLLECTOR_SCHEMA_FIELDS:
        if field not in context:
            missing.append(field)
    return missing


def normalize_context(context: dict) -> dict:
    """Normalize the nested collector schema to the flat shape expected by build_prompt_bundle.

    Collector schema:
      { repo: {path, branch, head_sha, clean},
        docs: {name: {exists, snippet, ...}},
        scripts: {name: {exists, snippet, ...}},
        tests: {name: {exists}},
        schemas: {name: {exists}},
        summary: {docs_present, scripts_present, tests_present, schemas_present},
        recent_commits: [{sha, short_sha, subject, author, date}] }

    Flat shape (for internal use by build_prompt_bundle):
      { repo_root, branch, head_sha, is_clean,
        recent_commits, docs_present, scripts_present, tests_present, schemas_present }
    """
    repo = context.get("repo", {})
    summary = context.get("summary", {})

    # recent_commits: collector uses {sha, short_sha, subject, author, date}
    # build_prompt_bundle expects {sha, author, date, message}
    raw_commits = context.get("recent_commits", [])
    commits = []
    for c in raw_commits:
        commits.append({
            "sha": c.get("sha", ""),
            "author": c.get("author", ""),
            "date": c.get("date", ""),
            "message": c.get("subject", ""),
        })

    return {
        "repo_root": repo.get("path", "unknown"),
        "branch": repo.get("branch", "unknown"),
        "head_sha": repo.get("head_sha", "unknown"),
        "is_clean": repo.get("clean", False),
        "recent_commits": commits,
        "docs_present": _presence_dict(context.get("docs", {})),
        "scripts_present": _presence_dict(context.get("scripts", {})),
        "tests_present": _presence_dict(context.get("tests", {})),
        "schemas_present": _presence_dict(context.get("schemas", {})),
    }


def _presence_dict(raw: dict) -> dict:
    """Convert a dict of file-info dicts to {name: exists_bool}."""
    return {k: bool(v.get("exists", False)) for k, v in raw.items()}


def validate_output_path(path: str) -> None:
    """Refuse output paths under forbidden prefixes."""
    abs_path = os.path.abspath(path)
    for prefix in FORBIDDEN_OUTPUT_PREFIXES:
        if abs_path.startswith(prefix):
            raise ValidationError(
                f"Output path '{path}' is under forbidden prefix '{prefix}'. "
                "Choose a path outside /home/max/.hermes."
            )


# ── Prompt bundle builder ─────────────────────────────────────────────────────


def build_prompt_bundle(context: dict) -> str:
    """Build the full AED_TASKER_PROMPT.md content."""

    repo_root = context.get("repo_root", "unknown")
    branch = context.get("branch", "unknown")
    head_sha = context.get("head_sha", "unknown")
    is_clean = context.get("is_clean", False)

    # Format recent commits for context display
    commits = context.get("recent_commits", [])
    commits_lines = []
    if commits:
        for c in commits[:20]:
            sha = c.get("sha", "?")[:7]
            author = c.get("author", "?")
            date = c.get("date", "")
            msg = c.get("message", "")
            commits_lines.append(f"- {sha}  {date}  {author}  {msg}")
    else:
        commits_lines = ["(no commits found)"]

    # Schema / design docs presence
    docs = context.get("docs_present", {})
    scripts = context.get("scripts_present", {})
    tests = context.get("tests_present", {})
    schemas = context.get("schemas_present", {})

    def presence_table(d: dict) -> str:
        if not d:
            return "  (none detected)"
        lines = []
        for k, v in sorted(d.items()):
            status = "✓" if v else "✗"
            lines.append(f"  - [{status}] {k}")
        return "\n".join(lines)

    # Stop rules
    stop_rules = [
        "  - Do NOT edit any file in the repository (no git commit, no git push)",
        "  - Do NOT create or update Kanban tasks (hermes kanban, linear, notion)",
        "  - Do NOT merge pull requests",
        "  - Do NOT update Hermes memory or facts (memory.update, fact_store)",
        "  - Do NOT use skill_manage to create or modify skills",
        "  - Do NOT mutate registry files (ledger.jsonl, edge_hypothesis_registry.*)",
        "  - Do NOT mutate ledger files (trial_ledger.jsonl, experiment ledger)",
        "  - Do NOT attempt live trading or interact with a broker",
        "  - Do NOT call external APIs beyond explicitly permitted research",
        "  - Do NOT dispatch subagents without Tom's explicit approval",
    ]

    # Model routing
    model_routing = """## Model Routing

Use the following routing policy in priority order:

### Preferred route
- **Model**: `openai-codex` or `gpt-5.5` via Codex OAuth
- **Trigger**: Standard Tasker runs
- **Why**: Best for roadmap analysis, multi-step reasoning, candidate PR generation

### Fallback (requires explicit Tom approval)
- **Model**: `openai/gpt-5.5` via direct API (`sk-pro...`)
- **Trigger**: Only if Codex OAuth quota is exhausted AND Tom explicitly approves spend
- **Constraint**: Do NOT auto-fallback; await Tom's `Ok use API` before using this route

### Lightweight-only fallback
- **Model**: `MiniMax 2.7` (this instance)
- **Trigger**: Only for brief status summaries, NOT for candidate PR generation
- **Constraint**: MiniMax is not approved for full Tasker runs — use only for quick triage"""

    # Research instructions
    research_instructions = """## Research Instructions

You may research the following topics IF explicitly enabled by Hermes config.
Do not proactively research without the `--research-enabled` flag from Hermes.

### Internal context first
1. Review AED_TASKER_CONTEXT.md for repo metadata (HEAD, branch, recent commits)
2. Review docs/ directory for design documents and project status
3. Review scripts/local/ for existing tooling
4. Review tests/ for test patterns and coverage
5. Review schemas/ for governance contract definitions

### Externally-enabled research topics
Only research these if Hermes passes `--research-enabled`:
- Backtest overfitting (Howard, 2018; Bailey, 2014)
- Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2012)
- Probability of Backtest Overfitting (PBO / CSCV method)
- Purged and embargoed cross-validation (Marcos Lopez de Prado, 2018)
- Experiment tracking tooling (MLflow, W&B, DVC)
- Evidence-tiered review workflows (tier-1/tier-2/tier-3 classification)
- Deep module architecture (hexagonal / onion / clean architecture)
- Options pre-earnings alpha decay patterns"""

    # Candidate PR requirements
    candidate_pr_requirements = """## Candidate PR Output Requirements

Your final output must include 5 to 8 candidate PRs, structured as follows:

For each candidate:
```
PR-<N>: <title>
  Why now:        <specific trigger or opportunity>
  Risk if skipped: <consequence of deferring>
  Risk if built too early: <cost of rushing>
  Allowed files:  <list of files this PR may touch>
  Forbidden files: <list of files this PR must not touch>
  Expected tests: <pytest files or test classes needed>
  Deep-module boundary: <which module(s) this PR touches>
```

Ranked next 3–5:
From the full list, mark the top 3–5 as `ranked_next` and explain the ordering
logic in the memo. Defer the rest to `do_not_build_yet` with a
`defer_reason` field.

Validation requirement:
All ROADMAP_PACKET.json output must validate against:
  python3 scripts/local/aed_tasker_packet.py validate ROADMAP_PACKET.json

If the packet fails validation, your run is considered incomplete."""

    lines = [
        "# AED Tasker Agent Prompt",
        "",
        f"**Generated at**: {datetime.now(timezone.utc).isoformat()}",
        f"**Context SHA**: {head_sha} (branch: `{branch}`)",
        f"**Repo clean**: {is_clean}",
        "",
        "---",
        "",
        "## Role",
        "",
        "You are the AED Tasker: a deterministic roadmap intelligence layer.",
        "Your job is to analyze the current project state and produce a ranked",
        "list of candidate PRs with rationale, risk assessments, and file",
        "boundaries — ready for Tom's review and downstream Executor consumption.",
        "",
        "You are READ-ONLY. You MUST NOT edit, commit, push, merge, or create",
        "any file in the repository except the two output files described below.",
        "",
        "## Output Contract",
        "",
        "You MUST produce exactly two files in the current working directory:",
        "",
        "1. **`AED_ROADMAP_TASKER_MEMO.md`**",
        "   Human-readable summary of current state, research themes reviewed,",
        "   drift risks, deep module assessment, and candidate PR list.",
        "",
        "2. **`ROADMAP_PACKET.json`**",
        "   Machine-readable structured output aligning with AED_TASKER_PACKET_SCHEMA.",
        "   Must validate with:",
        "   ```",
        "   python3 scripts/local/aed_tasker_packet.py validate ROADMAP_PACKET.json",
        "   ```",
        f"   Schema reference: `docs/aed_tasker_executor_design.md` section 5.",
        "",
        "## Hard Stop Rules",
        "",
        "You MUST NOT do any of the following under any circumstances:",
    ]
    lines.extend(stop_rules)

    lines.extend([
        "",
        "---",
        "",
        model_routing,
        "",
        "---",
        "",
        "## Context Provided",
        "",
        f"**Repo root**: `{repo_root}`",
        f"**Branch**: `{branch}`",
        f"**HEAD SHA**: `{head_sha}`",
        f"**Working tree clean**: `{is_clean}`",
        "",
        "### Recent commits (last 20)",
        "",
    ])
    lines.extend(commits_lines)

    lines.extend([
        "",
        "### Design documents present",
        presence_table(docs),
        "",
        "### Tooling scripts present",
        presence_table(scripts),
        "",
        "### Test files present",
        presence_table(tests),
        "",
        "### Governance schemas present",
        presence_table(schemas),
        "",
        "---",
        "",
        research_instructions,
        "",
        "---",
        "",
        candidate_pr_requirements,
        "",
        "---",
        "",
        "## Additional Guidance",
        "",
        "- Ground all candidate PRs in actual file evidence from the context above.",
        "- Do not propose PRs that touch engine/, fixtures/, or registry data.",
        "- Prefer small, sequential, logically-ordered PRs over large multi-feature PRs.",
        "- Every candidate must have explicit allowed_files and forbidden_files.",
        "- Include the specific test files expected for each candidate PR.",
        "- Mark candidates that require further design work as `do_not_build_yet`",
        "  with a `defer_reason` field instead of including them in the ranked list.",
    ])

    return "\n".join(lines)


def build_run_config(
    context: dict,
    context_json_path: str,
    output_prompt_path: str,
    output_config_path: str,
) -> dict:
    """Build AED_TASKER_RUN_CONFIG.json content."""

    now = datetime.now(timezone.utc).isoformat()

    return {
        "packet_kind": "aed.tasker.run_config.v1",
        "generated_at": now,
        "context_json": os.path.abspath(context_json_path),
        "output_prompt": os.path.abspath(output_prompt_path),
        "expected_outputs": [
            {
                "filename": "AED_ROADMAP_TASKER_MEMO.md",
                "purpose": "human-readable Tasker memo",
                "required": True,
            },
            {
                "filename": "ROADMAP_PACKET.json",
                "purpose": "machine-readable roadmap packet",
                "required": True,
                "validation_command": (
                    "python3 scripts/local/aed_tasker_packet.py validate ROADMAP_PACKET.json"
                ),
            },
        ],
        "preferred_model_route": {
            "tier": "preferred",
            "model": "openai-codex / gpt-5.5",
            "auth": "Codex OAuth",
            "trigger": "standard Tasker runs",
        },
        "api_fallback_policy": {
            "tier": "fallback",
            "model": "openai/gpt-5.5",
            "auth": "direct API key (sk-pro...)",
            "trigger": "Codex OAuth quota exhausted AND Tom explicitly approves spend",
            "constraint": "Do NOT auto-fallback; await explicit Tom approval",
        },
        "lightweight_fallback": {
            "tier": "lightweight-only",
            "model": "MiniMax 2.7",
            "auth": "current session",
            "trigger": "brief status summaries ONLY",
            "constraint": "NOT approved for full Tasker candidate PR generation",
        },
        "stop_rules": [
            "no repo edits (no git commit, no git push)",
            "no Kanban mutation (hermes kanban, linear, notion)",
            "no merge",
            "no memory update (memory.update, fact_store)",
            "no skill_manage",
            "no registry mutation (ledger.jsonl, edge_hypothesis_registry.*)",
            "no ledger mutation (trial_ledger.jsonl)",
            "no live trading / broker behavior",
            "no external API calls beyond explicit research",
            "no subagent dispatch without explicit Tom approval",
        ],
        "validation_command": (
            "python3 scripts/local/aed_tasker_packet.py validate ROADMAP_PACKET.json"
        ),
        "context_meta": {
            "repo_root": context.get("repo_root", ""),
            "branch": context.get("branch", ""),
            "head_sha": context.get("head_sha", ""),
            "is_clean": context.get("is_clean", False),
        },
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only AED Tasker prompt bundle generator. "
        "Reads AED_TASKER_CONTEXT.json and generates a Tasker prompt + run config.",
    )
    parser.add_argument(
        "--context-json",
        required=True,
        help="Path to AED_TASKER_CONTEXT.json (from aed_tasker_collect_context.py)",
    )
    parser.add_argument(
        "--output-prompt",
        required=True,
        help="Path to write AED_TASKER_PROMPT.md",
    )
    parser.add_argument(
        "--output-config",
        required=True,
        help="Path to write AED_TASKER_RUN_CONFIG.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Load context
    try:
        with open(args.context_json, "r", encoding="utf-8") as f:
            context = json.load(f)
    except FileNotFoundError:
        sys.stderr.write(f"Error: context file not found: {args.context_json}\n")
        return 2
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Error: malformed JSON in {args.context_json}: {e}\n")
        return 2

    # Validate required fields
    missing = validate_context_fields(context)
    if missing:
        sys.stderr.write(
            f"Error: context missing required fields: {', '.join(missing)}\n"
        )
        return 2

    # Validate output paths
    for path in (args.output_prompt, args.output_config):
        try:
            validate_output_path(path)
        except ValidationError as e:
            sys.stderr.write(f"Error: {e}\n")
            return 2

    # Normalize to flat shape for prompt builder
    flat_context = normalize_context(context)

    # Build content
    prompt_content = build_prompt_bundle(flat_context)
    run_config = build_run_config(
        flat_context,
        args.context_json,
        args.output_prompt,
        args.output_config,
    )

    # Write output
    try:
        with open(args.output_prompt, "w", encoding="utf-8") as f:
            f.write(prompt_content)
    except OSError as e:
        sys.stderr.write(f"Error: failed to write prompt to {args.output_prompt}: {e}\n")
        return 2

    try:
        with open(args.output_config, "w", encoding="utf-8") as f:
            json.dump(run_config, f, indent=2, ensure_ascii=False)
    except OSError as e:
        sys.stderr.write(f"Error: failed to write config to {args.output_config}: {e}\n")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())