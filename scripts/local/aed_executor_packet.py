#!/usr/bin/env python3
"""
AED Executor Packet Scaffold

Reads a ROADMAP_PACKET.json from a Tasker run, selects one candidate,
and produces a draft EXECUTOR_PACKET.json + AED_EXECUTION_PLAN.md.

This script is read-only by design: it does not call LLMs, does not
mutate the repository, does not create commits, does not open PRs,
and does not interact with Kanban or GitHub APIs.

CLI:
  python3 scripts/local/aed_executor_packet.py validate <path>
  python3 scripts/local/aed_executor_packet.py render-md <path> [--output <path>]
  python3 scripts/local/aed_executor_packet.py from-roadmap \\
    --roadmap-packet <path> \\
    --candidate-id <id> \\
    --output-json <path> \\
    --output-md <path>
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

PACKET_KIND = "aed.executor.plan.v1"
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s): " + "; ".join(errors))

# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_json(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)

def save_json(path: str | Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def deterministic_serialize(data: dict) -> str:
    """Serialize to JSON with sorted keys for reproducible output."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_executor_packet(packet: dict, *, from_validation=False) -> list[str]:
    """
    Validate an executor packet.
    Returns a list of error strings. Empty list means valid.
    Does not mutate anything.
    """
    errors: list[str] = []

    # packet_kind check
    packet_kind = packet.get("packet_kind", "")
    if from_validation and packet_kind != PACKET_KIND:
        errors.append(
            f"packet_kind must be '{PACKET_KIND}' (got '{packet_kind}')"
        )

    # schema_version check
    schema_version = packet.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION} (got {schema_version})"
        )

    # generated_at check
    generated_at = packet.get("generated_at", "")
    if not generated_at:
        errors.append("generated_at is required")

    # source_roadmap_packet check
    src = packet.get("source_roadmap_packet")
    if not isinstance(src, dict):
        errors.append("source_roadmap_packet is required and must be an object")
    else:
        if not src.get("path"):
            errors.append("source_roadmap_packet.path is required")
        if not src.get("packet_kind"):
            errors.append("source_roadmap_packet.packet_kind is required")
        if not src.get("selected_candidate_id"):
            errors.append("source_roadmap_packet.selected_candidate_id is required")

    # selected_candidate check
    sc = packet.get("selected_candidate", {})
    if not isinstance(sc, dict):
        errors.append("selected_candidate is required and must be an object")
    else:
        if not sc.get("candidate_id"):
            errors.append("selected_candidate.candidate_id is required")
        if not sc.get("title"):
            errors.append("selected_candidate.title is required")
        if not sc.get("goal"):
            errors.append("selected_candidate.goal is required")

    # pr_plan check
    pr_plan = packet.get("pr_plan", {})
    if not isinstance(pr_plan, dict):
        errors.append("pr_plan is required and must be an object")
    else:
        if not pr_plan.get("pr_title"):
            errors.append("pr_plan.pr_title is required")
        if not pr_plan.get("branch_name"):
            errors.append("pr_plan.branch_name is required")
        if not pr_plan.get("goal"):
            errors.append("pr_plan.goal is required")

        allowed = pr_plan.get("allowed_files", [])
        if not isinstance(allowed, list) or len(allowed) == 0:
            errors.append("pr_plan.allowed_files must be a non-empty list")

        forbidden = pr_plan.get("forbidden_files", [])
        if not isinstance(forbidden, list):
            errors.append("pr_plan.forbidden_files must be a list")

        # /home/max/.hermes check
        for f in allowed:
            if "/home/max/.hermes" in f:
                errors.append(
                    f"pr_plan.allowed_files must not include /home/max/.hermes: found '{f}'"
                )

        # registry/ledger mutation check (unless marked future/locked)
        for f in allowed:
            lower = f.lower()
            if any(
                kw in lower
                for kw in [
                    "edge_hypothesis_registry",
                    "trial_ledger",
                    "registry.json",
                    "ledger.json",
                ]
            ):
                # Only block if it's an actual mutation path (not schemas, not fixtures)
                if "schema" not in lower and "fixture" not in lower and "valid" not in lower:
                    # Check if the packet marks this as future/locked
                    is_locked = packet.get("safety_annotations", {}).get("registry_mutation_locked", False)
                    if not is_locked:
                        errors.append(
                            f"pr_plan.allowed_files contains registry/ledger path without locked flag: '{f}'"
                        )
                    break

        impl_steps = pr_plan.get("implementation_steps", [])
        if not isinstance(impl_steps, list) or len(impl_steps) == 0:
            errors.append("pr_plan.implementation_steps must be a non-empty list")

        expected_tests = pr_plan.get("expected_tests", [])
        if not isinstance(expected_tests, list) or len(expected_tests) == 0:
            errors.append("pr_plan.expected_tests must be a non-empty list")

        validation_cmds = pr_plan.get("validation_commands", [])
        if not isinstance(validation_cmds, list) or len(validation_cmds) == 0:
            errors.append("pr_plan.validation_commands must be a non-empty list")

        merge_policy = pr_plan.get("merge_policy", {})
        if isinstance(merge_policy, dict):
            auth_phrase = merge_policy.get("required_authorization_phrase", "")
            if not auth_phrase:
                errors.append("pr_plan.merge_policy.required_authorization_phrase is required")
        else:
            errors.append("pr_plan.merge_policy must be an object")

    # gate_config check
    gate = packet.get("gate_config", {})
    if not isinstance(gate, dict):
        errors.append("gate_config is required and must be an object")
    else:
        if not gate.get("require_human_merge_authorization", False):
            errors.append("gate_config.require_human_merge_authorization must be true")

        codex_clean = gate.get("require_codex_clean")
        if codex_clean is False:
            policy = gate.get("codex_unavailable_policy", "")
            if not policy:
                errors.append(
                    "gate_config.require_codex_clean is false but "
                    "gate_config.codex_unavailable_policy is not documented"
                )

        max_cycles = gate.get("max_patch_cycles", 0)
        if not isinstance(max_cycles, int) or max_cycles < 1:
            errors.append("gate_config.max_patch_cycles must be a positive integer")

    # split_triggers check
    split = packet.get("split_triggers", [])
    if not isinstance(split, list) or len(split) == 0:
        errors.append("split_triggers must be a non-empty list")

    # blockers_or_uncertainty check
    bou = packet.get("blockers_or_uncertainty", [])
    if not isinstance(bou, list):
        errors.append("blockers_or_uncertainty must be a list")

    return errors

def validate_packet(path: str | Path) -> tuple[bool, list[str]]:
    """Load and validate an executor packet. Returns (is_valid, errors)."""
    try:
        packet = load_json(path)
    except Exception as e:
        return False, [f"Failed to load JSON: {e}"]

    errors = validate_executor_packet(packet, from_validation=True)
    return len(errors) == 0, errors

# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_md(packet: dict) -> str:
    """Render an executor packet as markdown execution plan."""
    lines: list[str] = []

    # Header
    sc = packet.get("selected_candidate", {})
    pr_plan = packet.get("pr_plan", {})
    gate = packet.get("gate_config", {})
    src = packet.get("source_roadmap_packet", {})
    generated_at = packet.get("generated_at", "unknown")

    lines.append(f"# AED Executor Execution Plan")
    lines.append("")
    lines.append(f"**Generated**: {generated_at}")
    lines.append(f"**Candidate**: {sc.get('candidate_id', 'unknown')} — {sc.get('title', 'unknown')}")
    lines.append(f"**Source roadmap**: {src.get('path', 'unknown')}")
    lines.append(f"**Candidate ID**: {src.get('selected_candidate_id', 'unknown')}")
    lines.append("")

    # Section 1: Goal
    lines.append("## 1. Goal")
    lines.append("")
    lines.append(sc.get("goal", pr_plan.get("goal", "See pr_plan.goal")))
    lines.append("")

    # Section 2: Why now
    lines.append("## 2. Why Now")
    lines.append("")
    lines.append(sc.get("why_now", "_not specified_"))
    lines.append("")

    # Section 3: Non-goals
    lines.append("## 3. Non-Goals")
    lines.append("")
    for ng in pr_plan.get("non_goals", []):
        lines.append(f"- {ng}")
    if not pr_plan.get("non_goals"):
        lines.append("_none declared_")
    lines.append("")

    # Section 4: Allowed and forbidden files
    lines.append("## 4. File Boundaries")
    lines.append("")
    lines.append("### Allowed files")
    lines.append("")
    for f in pr_plan.get("allowed_files", []):
        lines.append(f"- `{f}`")
    lines.append("")

    lines.append("### Forbidden files")
    lines.append("")
    for f in pr_plan.get("forbidden_files", []):
        lines.append(f"- `{f}`")
    if not pr_plan.get("forbidden_files"):
        lines.append("_none declared_")
    lines.append("")

    # Section 5: Implementation steps
    lines.append("## 5. Implementation Steps")
    lines.append("")
    for i, step in enumerate(pr_plan.get("implementation_steps", []), 1):
        lines.append(f"{i}. {step}")
    lines.append("")

    # Section 6: Expected tests
    lines.append("## 6. Expected Tests")
    lines.append("")
    for t in pr_plan.get("expected_tests", []):
        lines.append(f"- `{t}`")
    lines.append("")

    # Section 7: Validation commands
    lines.append("## 7. Validation Commands")
    lines.append("")
    lines.append("Run these before committing:")
    lines.append("")
    for cmd in pr_plan.get("validation_commands", []):
        lines.append(f"```bash\n{cmd}\n```")
    lines.append("")

    # Section 8: Safety grep
    lines.append("## 8. Safety Grep Patterns")
    lines.append("")
    lines.append("Ensure no occurrences of these patterns in changed files:")
    lines.append("")
    for pattern in pr_plan.get("safety_grep_patterns", []):
        lines.append(f"- `{pattern}`")
    if not pr_plan.get("safety_grep_patterns"):
        lines.append("_none configured_")
    lines.append("")

    # Section 9: Scope check
    scope = pr_plan.get("scope_check", {})
    if scope:
        lines.append("## 9. Scope Check")
        lines.append("")
        lines.append(f"- Max files changed: `{scope.get('max_files_changed', 'not set')}`")
        lines.append(f"- Allowed path prefixes: `{', '.join(scope.get('allowed_path_prefixes', [])) or 'all'}`")
        lines.append(f"- Forbidden path prefixes: `{', '.join(scope.get('forbidden_path_prefixes', [])) or 'none'}`")
        lines.append("")

    # Section 10: Gate config
    lines.append("## 10. Gate Config")
    lines.append("")
    lines.append(f"- **Require CI green**: `{gate.get('require_ci_green', False)}`")
    lines.append(f"- **Require Codex clean**: `{gate.get('require_codex_clean', False)}`")
    lines.append(f"- **Require reviewer merge recommendation**: `{gate.get('require_reviewer_merge_recommendation', False)}`")
    lines.append(f"- **Require human merge authorization**: `{gate.get('require_human_merge_authorization', False)}`")
    lines.append(f"- **Max patch cycles**: `{gate.get('max_patch_cycles', 0)}`")
    lines.append(f"- **Codex cooldown (minutes)**: `{gate.get('codex_cooldown_minutes', 0)}`")
    if gate.get("codex_unavailable_policy"):
        lines.append(f"- **Codex unavailable policy**: `{gate.get('codex_unavailable_policy')}`")
    lines.append("")

    # Section 11: Merge policy
    lines.append("## 11. Merge Policy")
    lines.append("")
    mp = pr_plan.get("merge_policy", {})
    lines.append(f"- **Required authorization phrase**: `{mp.get('required_authorization_phrase', '')}`")
    lines.append(f"- **Auto-merge enabled**: `{mp.get('auto_merge_enabled', False)}`")
    lines.append(f"- **Require exact phrase match**: `{mp.get('require_exact_phrase_match', False)}`")
    lines.append("")

    # Section 12: Codex review policy
    lines.append("## 12. Codex Review Policy")
    lines.append("")
    crp = pr_plan.get("codex_review_policy", {})
    lines.append(f"- **Required before merge**: `{crp.get('required_before_merge', False)}`")
    lines.append(f"- **Model**: `{crp.get('model', 'gpt-5.3-codex')}`")
    lines.append(f"- **Focus areas**: {', '.join(crp.get('focus_areas', [])) or '_none_'}")

    # Section 13: Split triggers
    lines.append("## 13. Split Triggers")
    lines.append("")
    lines.append("PR must be split if any of these conditions are met:")
    lines.append("")
    for trigger in packet.get("split_triggers", []):
        lines.append(f"- {trigger}")
    lines.append("")

    # Section 14: Blockers
    lines.append("## 14. Blockers and Uncertainty")
    lines.append("")
    for b in packet.get("blockers_or_uncertainty", []):
        lines.append(f"- {b}")
    if not packet.get("blockers_or_uncertainty"):
        lines.append("_none_")
    lines.append("")

    # Section 15: Risk if skipped / built too early
    lines.append("## 15. Risk Assessment")
    lines.append("")
    lines.append(f"**Risk if skipped**: {sc.get('risk_if_skipped', '_not specified_')}")
    lines.append("")
    lines.append(f"**Risk if built too early**: {sc.get('risk_if_built_too_early', '_not specified_')}")
    lines.append("")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# from-roadmap subcommand
# ---------------------------------------------------------------------------

def build_default_pr_plan(candidate: dict, candidate_id: str) -> dict:
    """
    Build a conservative default pr_plan from a Tasker candidate.
    This does NOT call any LLM — it makes mechanical choices only.
    """
    candidate_id_clean = candidate_id.replace("AED-CAND-", "cand")
    branch_name = f"tooling/{candidate_id_clean}-executor-plan"

    return {
        "pr_title": candidate.get("title", f"[Executor] {candidate_id}"),
        "branch_name": branch_name,
        "goal": candidate.get("goal", candidate.get("title", "")),
        "non_goals": [
            "Do not modify engine/ production code",
            "Do not mutate registries or ledgers",
            "Do not enable autonomous search",
            "Do not add live trading or broker integration",
        ],
        "allowed_files": candidate.get("allowed_files", []),
        "forbidden_files": candidate.get("forbidden_files", []),
        "implementation_steps": [
            f"Implement {candidate_id} scoped to allowed_files only",
            "Add corresponding test file",
            "Run validation commands (pytest + compileall + safety grep)",
            "Commit scoped changes — do not broaden scope",
        ],
        "expected_tests": candidate.get("expected_tests", []),
        "validation_commands": [
            "python3 -m compileall scripts/local tests",
            "PYTHONPATH=. python3 -m pytest -q",
            "bash scripts/ci/validate_governance_manifests.sh",
            "bash scripts/ci/validate_event_options_contract.sh",
        ],
        "safety_grep_patterns": [
            "urllib.request",
            "requests.post",
            "requests.get",
            "gh pr merge",
            "memory.update",
            "skill_manage",
            "delegate_task",
            "cronjob",
        ],
        "scope_check": {
            "max_files_changed": 8,
            "allowed_path_prefixes": ["scripts/local/", "tests/", "docs/"],
            "forbidden_path_prefixes": ["engine/", "schemas/", "fixtures/"],
        },
        "codex_review_policy": {
            "required_before_merge": True,
            "model": "gpt-5.3-codex",
            "focus_areas": [
                "file boundary compliance",
                "no forbidden path touched",
                "test coverage for new behavior",
            ],
        },
        "reviewer_focus": [
            "allowed_files and forbidden_files boundaries respected",
            "no registry or ledger mutation",
            "validation commands pass",
            "tests cover new behavior",
        ],
        "merge_policy": {
            "required_authorization_phrase": "I confirm",
            "auto_merge_enabled": False,
            "require_exact_phrase_match": True,
        },
    }

def build_default_gate_config(candidate: dict) -> dict:
    """Build a conservative gate_config from a Tasker candidate."""
    return {
        "require_ci_green": True,
        "require_codex_clean": True,
        "require_reviewer_merge_recommendation": True,
        "require_human_merge_authorization": True,
        "max_patch_cycles": 3,
        "codex_cooldown_minutes": 5,
        "codex_unavailable_policy": "block_merge",
    }

def build_default_split_triggers() -> list[str]:
    """Build conservative default split triggers."""
    return [
        "Changes touch engine/ or fixtures/ — must split into separate PR",
        "Changes add a new dependency to pyproject.toml or requirements.txt",
        "Changes modify a schema JSON file — must split schema-only PR first",
        "Implementation steps exceed 5 items — consider splitting",
        "Allowed files exceed 10 paths — consider splitting by module boundary",
        "forbidden_files includes engine/ or schemas/ — do not cross this boundary",
    ]

def from_roadmap(
    roadmap_path: str | Path,
    candidate_id: str,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    """
    Read ROADMAP_PACKET.json, locate candidate_id, emit draft EXECUTOR_PACKET.json.
    Returns the packet dict. Optionally writes JSON and MD files.
    """
    roadmap = load_json(roadmap_path)

    # Find candidate
    candidates = roadmap.get("candidate_prs", [])
    candidate = None
    for c in candidates:
        if c.get("candidate_id") == candidate_id:
            candidate = c
            break

    if not candidate:
        raise ValueError(
            f"Candidate '{candidate_id}' not found in ROADMAP_PACKET.json. "
            f"Available: {[c.get('candidate_id') for c in candidates]}"
        )

    pr_plan = build_default_pr_plan(candidate, candidate_id)
    gate_config = build_default_gate_config(candidate)

    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    packet: dict[str, Any] = {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "source_roadmap_packet": {
            "path": str(roadmap_path),
            "packet_kind": roadmap.get("packet_kind", "aed.tasker.report.v1"),
            "selected_candidate_id": candidate_id,
        },
        "selected_candidate": {
            "candidate_id": candidate_id,
            "title": candidate.get("title", ""),
            "goal": candidate.get("goal", candidate.get("title", "")),
            "why_now": candidate.get("why_now", ""),
            "risk_if_skipped": candidate.get("risk_if_skipped", ""),
            "risk_if_built_too_early": candidate.get("risk_if_built_too_early", ""),
            "estimated_scope": candidate.get("estimated_scope", "medium"),
            "depends_on": candidate.get("depends_on", []),
        },
        "pr_plan": pr_plan,
        "gate_config": gate_config,
        "split_triggers": build_default_split_triggers(),
        "blockers_or_uncertainty": [
            "Executor packet scaffold is draft — real execution requires human candidate selection",
            "No LLM is called during this generation — all fields are conservative defaults",
        ],
        "safety_annotations": {
            "registry_mutation_locked": True,
            "no_llm_call": True,
            "no_kanban_dispatch": True,
            "no_github_mutation": True,
        },
    }

    # Write outputs if requested
    if output_json:
        save_json(output_json, packet)

    if output_md:
        md_text = render_md(packet)
        with open(output_md, "w") as f:
            f.write(md_text)

    return packet

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AED Executor Packet: validate, render, or generate from roadmap"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    v = sub.add_parser("validate", help="Validate an executor packet JSON file")
    v.add_argument("packet_path", help="Path to EXECUTOR_PACKET.json")

    # render-md
    r = sub.add_parser("render-md", help="Render executor packet as markdown")
    r.add_argument("packet_path", help="Path to EXECUTOR_PACKET.json")
    r.add_argument("--output", "-o", dest="output_path", help="Output .md path")

    # from-roadmap
    f = sub.add_parser("from-roadmap", help="Generate executor packet from a ROADMAP_PACKET.json")
    f.add_argument("--roadmap-packet", required=True, dest="roadmap_path")
    f.add_argument("--candidate-id", required=True, dest="candidate_id")
    f.add_argument("--output-json", dest="output_json", help="Output .json path")
    f.add_argument("--output-md", dest="output_md", help="Output .md path")

    args = parser.parse_args(argv)

    if args.command == "validate":
        valid, errs = validate_packet(args.packet_path)
        if valid:
            print(f"OK: {args.packet_path} is valid", file=sys.stdout)
            return 0
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    elif args.command == "render-md":
        packet = load_json(args.packet_path)
        errs = validate_executor_packet(packet, from_validation=True)
        if errs:
            for e in errs:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        md_text = render_md(packet)
        if args.output_path:
            with open(args.output_path, "w") as f:
                f.write(md_text)
            print(f"Memo written to {args.output_path}")
        else:
            print(md_text)
        return 0

    elif args.command == "from-roadmap":
        try:
            packet = from_roadmap(
                args.roadmap_path,
                args.candidate_id,
                output_json=args.output_json,
                output_md=args.output_md,
            )
            print(f"Generated executor packet for {args.candidate_id}")
            if args.output_json:
                print(f"  JSON: {args.output_json}")
            if args.output_md:
                print(f"  MD: {args.output_md}")
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    else:
        parser.print_help()
        return 0

if __name__ == "__main__":
    sys.exit(main())