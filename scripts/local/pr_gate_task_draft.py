#!/usr/bin/env python3
"""
AED PR Gate Task-Draft Generator

Reads a PR gate classifier packet (from classify_pr_gate_state.py) and an optional
EXECUTOR_PACKET.json (from aed_executor_packet.py), then produces a task-draft
JSON and markdown file describing the next action a human or Kanban controller
should take.

This tool is READ-ONLY. It does not:
- Call LLM APIs
- Search the internet
- Call GitHub APIs directly
- Create Kanban tasks
- Dispatch workers
- Patch PRs
- Merge
- Update memory
- Use skill_manage

It only generates a durable task-draft packet that a human or later controller
can inspect and submit to Hermes Kanban.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKET_KIND = "aed.pr_gate.task_draft.v1"
SCHEMA_VERSION = 1

ALLOWED_ACTIONS = frozenset([
    "no_action_wait",
    "create_codex_request_task_draft",
    "create_builder_patch_task_draft",
    "create_reviewer_task_draft",
    "create_human_escalation_task_draft",
])

ALLOWED_CLASSIFICATIONS = frozenset([
    "blocked_scope",
    "blocked_pr_closed",
    "blocked_pr_merged",
    "blocked_wrong_base",
    "ci_pending",
    "ci_failed",
    "codex_request_needed",
    "codex_pending",
    "codex_suggestions",
    "codex_clean",
    "ready_for_reviewer",
    "unknown",
])

CI_STATUS_VALUES = frozenset(["passed", "failed", "pending", "unknown"])

CODEX_STATUS_VALUES = frozenset([
    "clean",
    "suggestions",
    "pending",
    "unavailable",
    "not_requested",
    "unknown",
])

ASSIGNEE_MAP = {
    "create_builder_patch_task_draft": "aed-builder",
    "create_reviewer_task_draft": "aed-reviewer",
    "create_codex_request_task_draft": "aed-reviewer",
    "create_human_escalation_task_draft": "human",
    "no_action_wait": None,
}

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def load_json(path: str | Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _make_idempotency_key(pr_number: str, head_sha: str, action: str) -> str:
    # Format: pr{pr_number}-{head_sha[:8]}-{sha256(pr:head:action)[:8]}-{action}
    # The pr and head components are plain so the key is human-parseable;
    # the hash provides tamper-resistance; action is appended for disambiguation.
    head8 = head_sha[:8] if head_sha else "00000000"
    parts = f"{pr_number}:{head_sha}:{action}"
    hash_part = _sha256(parts)[:8]
    return f"pr{pr_number}-{head8}-{hash_part}-{action}"


def _fmt_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bullet_list(items: list[str], max_items: int = 20) -> str:
    """Format a list as bullet points, optionally truncated."""
    truncated = items[:max_items]
    result = "\n".join(f"- {item}" for item in truncated)
    if len(items) > max_items:
        result += "\n_(truncated)_"
    return result


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def classify_to_action(classification: str, ci_status: str) -> str:
    """
    Map classifier packet classification + ci_status to task-draft action.

    - ci_pending / codex_pending → no_action_wait
    - codex_request_needed → create_codex_request_task_draft
    - codex_suggestions → create_builder_patch_task_draft
    - ci_failed → create_builder_patch_task_draft (code-related failure)
    - ready_for_reviewer → create_reviewer_task_draft
    - blocked_scope / blocked_wrong_base / unknown / blocked_pr_closed /
      blocked_pr_merged → create_human_escalation_task_draft
    """
    if classification in ("ci_pending", "codex_pending"):
        return "no_action_wait"
    if classification == "codex_request_needed":
        return "create_codex_request_task_draft"
    if classification == "codex_suggestions":
        return "create_builder_patch_task_draft"
    if classification == "ci_failed":
        return "create_builder_patch_task_draft"
    if classification == "ready_for_reviewer":
        return "create_reviewer_task_draft"
    if classification in (
        "blocked_scope",
        "blocked_wrong_base",
        "blocked_pr_closed",
        "blocked_pr_merged",
        "unknown",
    ):
        return "create_human_escalation_task_draft"
    return "no_action_wait"


def _build_standard_warnings(action: str) -> list[str]:
    """Return mandatory safety warnings for non-wait task drafts."""
    if action == "no_action_wait":
        return []
    return [
        "Do not merge this PR from inside this task.",
        "Do not start PR #198 or any subsequent PR from inside this task.",
        "Do not update memory, use fact_store, or call skill_manage from inside this task.",
        "Do not call hermes kanban create-task or any Kanban mutation from inside this task.",
    ]


def _build_reviewer_body(
    pr_number: str,
    pr_url: str,
    head_sha: str,
    changed_files: list[str],
) -> str:
    body = (
        f"## Review Task for PR #{pr_number}\n\n"
        f"**PR**: {pr_url}\n"
        f"**Head**: {head_sha}\n\n"
        f"### Scope\n"
        f"Review the latest commit on this PR only. Do not re-review older commits.\n\n"
        f"### Changed files\n"
        + _bullet_list(changed_files)
        + "\n\n"
        f"### Instructions\n"
        f"1. Review the diff for the latest head SHA only.\n"
        f"2. Focus on: correct implementation, test coverage, safety.\n"
        f"3. Verify allowed_files / forbidden_files boundaries are respected.\n"
        f"4. Run validation commands before approving.\n\n"
        f"### Merge authorization\n"
        f"Human phrase: I confirm required before merge.\n"
        f"Auto-merge is disabled.\n"
    )
    return body


def _build_builder_body(
    pr_number: str,
    pr_url: str,
    head_sha: str,
    allowed_files: list[str],
    forbidden_files: list[str],
    executor_goal: str | None = None,
) -> str:
    goal_section = ""
    if executor_goal:
        goal_section = f"\n### Original Executor goal\n{executor_goal}\n\n"

    body = (
        f"## Builder Patch Task for PR #{pr_number}\n\n"
        f"**PR**: {pr_url}\n"
        f"**Head**: {head_sha}\n\n"
        f"{goal_section}"
        f"### Allowed files (patch only these)\n"
        f"{_bullet_list(allowed_files)}\n\n"
        f"### Forbidden files (do not touch)\n"
        f"{_bullet_list(forbidden_files) if forbidden_files else '_none_'}\n\n"
        f"### Instructions\n"
        f"1. Patch only the files listed in allowed_files above.\n"
        f"2. Do not broaden scope to files outside allowed_files.\n"
        f"3. Run compileall + pytest before committing.\n"
        f"4. Do not push directly to main.\n\n"
        f"### Idempotency\n"
        f"Task idempotency key includes PR#{pr_number} + head_sha + action.\n"
        f"Duplicate task submissions for the same head will be deduplicated.\n"
    )
    return body


def _build_codex_body(pr_number: str, pr_url: str, head_sha: str) -> str:
    return (
        f"## Codex Review Request for PR #{pr_number}\n\n"
        f"**PR**: {pr_url}\n"
        f"**Head**: {head_sha}\n\n"
        f"### What to do\n"
        f"1. Run Codex review on the latest head SHA.\n"
        f"2. Focus on: file boundaries, no forbidden paths, test coverage.\n"
        f"3. Post review comments to the PR.\n\n"
        f"### What NOT to do\n"
        f"- Do not merge.\n"
        f"- Do not request auto-merge.\n"
        f"- Do not update memory or use skill_manage.\n"
        f"- Do not dispatch workers or create Kanban tasks.\n\n"
        f"### Merge authorization\n"
        f"Human phrase I confirm required before merge.\n"
    )


def _build_escalation_body(
    pr_number: str,
    pr_url: str,
    head_sha: str,
    classification: str,
    blockers: list[str],
) -> str:
    reasons = "\n".join(f"- {b}" for b in blockers) or "_none_"
    return (
        f"## Human Escalation for PR #{pr_number}\n\n"
        f"**PR**: {pr_url}\n"
        f"**Head**: {head_sha}\n"
        f"**Classification**: {classification}\n\n"
        f"### Blockers\n{reasons}\n\n"
        f"### What a human must do\n"
        f"- Review the task draft body below.\n"
        f"- Take the appropriate action manually.\n"
        f"- Do not rely on automated dispatch.\n\n"
        f"### Idempotency\n"
        f"Task idempotency key includes PR#{pr_number} + head_sha + action.\n"
        f"Duplicate task submissions will be deduplicated.\n"
    )


def _build_wait_body(
    classification: str,
    ci_status: str,
    pr_url: str,
) -> str:
    return (
        f"## PR Gate -- Wait state for {pr_url}\n\n"
        f"**Classification**: {classification}\n"
        f"**CI status**: {ci_status}\n\n"
        f"No action required at this time. This task was generated because:\n"
        f"- classification = {classification} -> no_action_wait\n"
        f"- CI status = {ci_status} -> still pending\n\n"
        f"### Next steps\n"
        f"Wait for CI to complete or Codex to finish reviewing.\n"
        f"Re-run the classifier when status changes.\n"
    )


def build_task_draft(
    classifier_packet: dict[str, Any],
    executor_packet: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Convert a classifier packet (+ optional executor packet) into a task-draft.
    """
    classification = classifier_packet.get("classification", "unknown")
    ci_status = classifier_packet.get("ci_status", "unknown")
    codex_status = classifier_packet.get("codex_status", "unknown")
    pr_number = str(classifier_packet.get("pr_number", ""))
    pr_url = classifier_packet.get("pr_url", "")
    head_sha = classifier_packet.get("head_sha", classifier_packet.get("head_sha_after", ""))
    changed_files = classifier_packet.get("changed_files", [])

    if not head_sha:
        head_sha = "unknown"

    action = classify_to_action(classification, ci_status)

    # Build body, assignee, and field sets per action
    if action == "no_action_wait":
        title = f"PR #{pr_number}: {classification} -- wait"
        body = _build_wait_body(classification, ci_status, pr_url)
        assignee = None
        allowed_files: list[str] = []
        forbidden_files: list[str] = []
        stop_rules: list[str] = []
        validation_commands: list[str] = []
        expected_return_fields: list[str] = []

    elif action == "create_reviewer_task_draft":
        title = f"Review PR #{pr_number} (Codex clean, ready for human reviewer)"
        body = _build_reviewer_body(pr_number, pr_url, head_sha, changed_files)
        assignee = ASSIGNEE_MAP[action]
        if executor_packet:
            pr_plan = executor_packet.get("pr_plan", {})
            allowed_files = pr_plan.get("allowed_files", changed_files)
            forbidden_files = pr_plan.get("forbidden_files", [])
        else:
            allowed_files = changed_files
            forbidden_files = []
        stop_rules = [
            "Stop if PR is closed or merged.",
            "Stop if base branch changes.",
            "Stop if new commits arrive that differ from the reviewed head.",
        ]
        validation_commands = [
            "python3 -m compileall scripts/local tests",
            "PYTHONPATH=. python3 -m pytest -q",
            "bash scripts/ci/validate_governance_manifests.sh",
        ]
        expected_return_fields = ["review_state", "merge_authorized", "authorization_phrase"]

    elif action == "create_builder_patch_task_draft":
        title = f"Patch PR #{pr_number} (Codex suggestions)"
        executor_allowed = []
        if executor_packet:
            pr_plan = executor_packet.get("pr_plan", {})
            executor_goal = pr_plan.get("goal", "")
            executor_allowed = pr_plan.get("allowed_files", [])
            forbidden_files = pr_plan.get("forbidden_files", [])
            validation_commands = pr_plan.get("validation_commands", [])
        else:
            executor_goal = None
            executor_allowed = []
            forbidden_files = []
            validation_commands = [
                "python3 -m compileall scripts/local tests",
                "PYTHONPATH=. python3 -m pytest -q",
            ]
        # Fall back to classifier changed_files if executor allowed_files is empty
        # (empty scope from malformed executor packet is a validation error)
        allowed_files = executor_allowed if executor_allowed else changed_files
        body = _build_builder_body(
            pr_number, pr_url, head_sha,
            allowed_files, forbidden_files, executor_goal,
        )
        assignee = ASSIGNEE_MAP[action]
        stop_rules = [
            "Stop if PR is closed or merged.",
            "Stop if base branch changes.",
            "Do not broaden scope beyond allowed_files.",
        ]
        expected_return_fields = ["patch_applied", "files_changed", "validation_passed"]

    elif action == "create_codex_request_task_draft":
        title = f"Request Codex review for PR #{pr_number}"
        body = _build_codex_body(pr_number, pr_url, head_sha)
        assignee = ASSIGNEE_MAP[action]
        allowed_files = changed_files
        forbidden_files = []
        stop_rules = [
            "Stop if PR is closed or merged.",
            "Stop if Codex review is already posted.",
        ]
        validation_commands = []
        expected_return_fields = ["codex_review_posted", "codex_status_after"]

    else:  # create_human_escalation_task_draft
        title = f"HUMAN ESCALATION: PR #{pr_number} -- {classification}"
        blockers = classifier_packet.get("blockers", [])
        body = _build_escalation_body(pr_number, pr_url, head_sha, classification, blockers)
        assignee = ASSIGNEE_MAP[action]
        allowed_files = changed_files
        forbidden_files = []
        stop_rules = [
            "Escalation is terminal -- do not auto-resolve.",
            "Human must review and decide.",
        ]
        validation_commands = []
        expected_return_fields = ["human_decision", "action_taken", "notes"]

    # Idempotency key
    idempotency_key = _make_idempotency_key(pr_number, head_sha, action)

    # Add standard safety warnings
    extra_warnings = _build_standard_warnings(action)
    if extra_warnings:
        body = body.rstrip() + "\n\n" + "\n".join(extra_warnings)

    task_draft: dict[str, Any] = {
        "action": action,
        "title": title,
        "assignee": assignee,
        "status": "todo",
        "body": body,
        "idempotency_key": idempotency_key,
        "stop_rules": stop_rules,
        "validation_commands": validation_commands,
        "expected_return_fields": expected_return_fields,
    }
    if allowed_files:
        task_draft["allowed_files"] = allowed_files
    if forbidden_files:
        task_draft["forbidden_files"] = forbidden_files

    now = _fmt_now()

    packet: dict[str, Any] = {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "source": {
            "pr_number": pr_number,
            "pr_url": pr_url,
            "head_sha": head_sha,
            "classification": classification,
            "ci_status": ci_status,
            "codex_status": codex_status,
            "changed_files": changed_files,
        },
        "task_draft": task_draft,
        "controller_rules": {
            "no_auto_dispatch": True,
            "no_auto_merge": True,
            "human_merge_authorization_required": True,
            "max_patch_cycles": 3,
            "codex_cooldown_minutes": 5,
        },
        "blockers_or_uncertainty": [],
    }

    return packet


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_task_draft_packet(packet: dict[str, Any]) -> list[str]:
    """
    Validate a PR_GATE_TASK_DRAFT.v1 packet.

    Returns list of error messages (empty = valid).
    """
    errors: list[str] = []

    # packet_kind check
    if packet.get("packet_kind") != PACKET_KIND:
        errors.append(
            f"packet_kind must be '{PACKET_KIND}' "
            f"(got '{packet.get('packet_kind')}')"
        )

    # schema_version check
    if packet.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION} "
            f"(got {packet.get('schema_version')})"
        )

    # generated_at check -- must be ISO-8601
    generated_at = packet.get("generated_at", "")
    if not generated_at:
        errors.append("generated_at is required")
    else:
        try:
            datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            errors.append(
                f"generated_at must be ISO-8601 YYYY-MM-DDTHH:MM:SSZ "
                f"(got '{generated_at}')"
            )

    # source check
    src = packet.get("source", {})
    if not isinstance(src, dict):
        errors.append("source is required and must be an object")
    else:
        if not src.get("pr_number"):
            errors.append("source.pr_number is required")
        if not src.get("head_sha"):
            errors.append("source.head_sha is required")

    # task_draft check
    td = packet.get("task_draft", {})
    if not isinstance(td, dict):
        errors.append("task_draft is required and must be an object")
    else:
        action = td.get("action", "")
        if action not in ALLOWED_ACTIONS:
            errors.append(
                f"task_draft.action must be one of {sorted(ALLOWED_ACTIONS)} "
                f"(got '{action}')"
            )
        if action == "no_action_wait":
            pass  # title and body are optional for no_action_wait
        else:
            if not td.get("title"):
                errors.append("task_draft.title is required for non-wait actions")
            if not td.get("body"):
                errors.append("task_draft.body is required for non-wait actions")

        # Idempotency key must be present and well-formed
        # Key format: pr{pr_number}-{head_sha[:8]}-{hash}-{action}
        key = td.get("idempotency_key", "")
        if not key:
            errors.append("task_draft.idempotency_key is required")
        else:
            head8 = src.get("head_sha", "")[:8] if src.get("head_sha") else ""
            expected_prefix = f"pr{src.get('pr_number', '')}-{head8}-"
            if not key.startswith(expected_prefix):
                errors.append(
                    f"task_draft.idempotency_key format incorrect (got '{key}', "
                    f"expected prefix '{expected_prefix}<hash>-<action>')"
                )
            if action not in key:
                errors.append(
                    f"task_draft.idempotency_key must include action "
                    f"(got '{key}', action='{action}')"
                )

        # allowed_files check
        allowed_files = td.get("allowed_files", [])
        if allowed_files:
            if not isinstance(allowed_files, list):
                errors.append("task_draft.allowed_files must be a list")
            for f in allowed_files:
                if "/home/max/.hermes" in str(f):
                    errors.append(
                        f"task_draft.allowed_files may not include "
                        f"/home/max/.hermes (found '{f}')"
                    )
                # No Kanban/mutation commands in file paths
                prohibited = [
                    "memory.update", "skill_manage", "fact_store",
                    "delegate_task", "cronjob", "hermes kanban",
                    "gh pr merge", "gh api",
                ]
                for cmd in prohibited:
                    if cmd in str(f):
                        errors.append(
                            f"task_draft.allowed_files may not include "
                            f"forbidden command '{cmd}' (found '{f}')"
                        )

        # Reviewer drafts must mention "latest head" for scope
        if action == "create_reviewer_task_draft":
            body_lower = td.get("body", "").lower()
            if "latest head" not in body_lower and "review the latest" not in body_lower:
                errors.append(
                    "task_draft.body for reviewer must mention reviewing "
                    "the latest head only"
                )

        # Builder drafts must mention allowed files
        if action == "create_builder_patch_task_draft":
            body_lower = td.get("body", "").lower()
            if "allowed files" not in body_lower and "allowed_files" not in body_lower:
                errors.append(
                    "task_draft.body for builder patch must mention allowed files"
                )

        # All non-wait drafts must prohibit merge
        if action != "no_action_wait":
            body_lower = td.get("body", "").lower()
            if "merge" in body_lower:
                # Check that "do not merge" is present
                if "do not merge" not in body_lower and "do not auto-merge" not in body_lower:
                    errors.append(
                        "task_draft.body must include 'Do not merge' instruction"
                    )

    return errors


# ---------------------------------------------------------------------------
# Render markdown
# ---------------------------------------------------------------------------

def render_md(packet: dict[str, Any]) -> str:
    """Render a PR_GATE_TASK_DRAFT.v1 packet as human-readable markdown."""
    src = packet.get("source", {})
    td = packet.get("task_draft", {})
    cr = packet.get("controller_rules", {})
    action = td.get("action", "")

    lines: list[str] = []
    lines.append("# AED PR Gate Task Draft")
    lines.append("")
    lines.append(f"**Generated**: {packet.get('generated_at', 'unknown')}")
    lines.append(f"**Packet kind**: {packet.get('packet_kind', '')}")
    lines.append(f"**Schema version**: {packet.get('schema_version', '')}")
    lines.append("")
    lines.append("## Source PR")
    lines.append("")
    lines.append(f"- **PR number**: {src.get('pr_number', '')}")
    lines.append(f"- **PR URL**: {src.get('pr_url', '')}")
    lines.append(f"- **Head SHA**: {src.get('head_sha', '')}")
    lines.append(f"- **Classification**: {src.get('classification', '')}")
    lines.append(f"- **CI status**: {src.get('ci_status', '')}")
    lines.append(f"- **Codex status**: {src.get('codex_status', '')}")
    lines.append("")
    lines.append("## Task Draft")
    lines.append("")
    lines.append(f"- **Action**: {action}")
    lines.append(f"- **Assignee**: {td.get('assignee', 'unassigned')}")
    lines.append(f"- **Status**: {td.get('status', 'todo')}")
    lines.append(f"- **Idempotency key**: {td.get('idempotency_key', '')}")
    lines.append("")

    if td.get("allowed_files"):
        lines.append("### Allowed files")
        lines.append("")
        for f in td["allowed_files"]:
            lines.append(f"- {f}")
        lines.append("")

    if td.get("forbidden_files"):
        lines.append("### Forbidden files")
        lines.append("")
        for f in td["forbidden_files"]:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("### Body")
    lines.append("")
    lines.append(td.get("body", "_empty_"))
    lines.append("")

    if td.get("stop_rules"):
        lines.append("### Stop rules")
        lines.append("")
        for r in td["stop_rules"]:
            lines.append(f"- {r}")
        lines.append("")

    if td.get("validation_commands"):
        lines.append("### Validation commands")
        lines.append("")
        for cmd in td["validation_commands"]:
            lines.append(f"```bash\n{cmd}\n```")
        lines.append("")

    if td.get("expected_return_fields"):
        lines.append("### Expected return fields")
        lines.append("")
        for f in td["expected_return_fields"]:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("## Controller Rules")
    lines.append("")
    lines.append(f"- **No auto-dispatch**: {cr.get('no_auto_dispatch', True)}")
    lines.append(f"- **No auto-merge**: {cr.get('no_auto_merge', True)}")
    lines.append(f"- **Human merge authorization required**: {cr.get('human_merge_authorization_required', True)}")
    lines.append(f"- **Max patch cycles**: {cr.get('max_patch_cycles', 3)}")
    lines.append(f"- **Codex cooldown (minutes)**: {cr.get('codex_cooldown_minutes', 5)}")
    lines.append("")

    blockers = packet.get("blockers_or_uncertainty", [])
    if blockers:
        lines.append("## Blockers and Uncertainty")
        lines.append("")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")
    else:
        lines.append("## Blockers and Uncertainty")
        lines.append("")
        lines.append("_none_")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AED PR Gate Task-Draft Generator"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate
    g = sub.add_parser("generate", help="Generate task draft from classifier packet")
    g.add_argument("--classifier-json", required=True, dest="classifier_json")
    g.add_argument("--executor-packet", dest="executor_packet")
    g.add_argument("--output-json", required=True, dest="output_json")
    g.add_argument("--output-md", dest="output_md")

    # validate
    v = sub.add_parser("validate", help="Validate a task-draft JSON file")
    v.add_argument("packet_path", help="Path to PR_GATE_TASK_DRAFT.json")

    # render-md
    r = sub.add_parser("render-md", help="Render task-draft as markdown")
    r.add_argument("packet_path", help="Path to PR_GATE_TASK_DRAFT.json")
    r.add_argument("--output", "-o", dest="output_path")

    args = parser.parse_args(argv)

    if args.command == "generate":
        classifier = load_json(args.classifier_json)
        executor = None
        if args.executor_packet:
            executor = load_json(args.executor_packet)
        packet = build_task_draft(classifier, executor)

        errs = validate_task_draft_packet(packet)
        if errs:
            for e in errs:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1

        with open(args.output_json, "w") as f:
            json.dump(packet, f, indent=2)
        print(f"Task draft written to {args.output_json}", file=sys.stdout)

        if args.output_md:
            with open(args.output_md, "w") as f:
                f.write(render_md(packet))
            print(f"Memo written to {args.output_md}", file=sys.stdout)

        return 0

    elif args.command == "validate":
        packet = load_json(args.packet_path)
        errs = validate_task_draft_packet(packet)
        if errs:
            for e in errs:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"OK: {args.packet_path} is valid", file=sys.stdout)
        return 0

    elif args.command == "render-md":
        packet = load_json(args.packet_path)
        errs = validate_task_draft_packet(packet)
        if errs:
            for e in errs:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        md = render_md(packet)
        if args.output_path:
            with open(args.output_path, "w") as f:
                f.write(md)
            print(f"Memo written to {args.output_path}", file=sys.stdout)
        else:
            print(md)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())