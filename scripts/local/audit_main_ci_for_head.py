#!/usr/bin/env python3
"""
audit_main_ci_for_head.py — Read-only post-merge CI audit helper.

Polls GitHub Actions workflow runs for an exact main-branch head SHA and
classifies them as GREEN / HOLD_PENDING / HOLD_FAILED / HOLD_MISSING /
HOLD_NO_RUNS / ERROR_INVALID_ARGS / ERROR_TOOL_FAILURE.

This helper is REPORT-ONLY. It performs only read operations against
GitHub and never mutates repository or branch state. It parses JSON in
Python and invokes gh via list-form argv through subprocess.run.

Usage:
    python3 scripts/local/audit_main_ci_for_head.py \\
        --repo Slideshow11/Automated-Edge-Discovery \\
        --branch main \\
        --head-sha dd0b4e2b932b2e6b85a59c12d5aa24774b8994bf \\
        --required-workflow CI \\
        --required-workflow "Edge Discovery audit tests" \\
        --max-polls 6 \\
        --poll-seconds 30 \\
        --output-json /tmp/audit.json \\
        --output-md /tmp/audit.md

Exit codes:
    0 — report written (status may be any value)
    2 — invalid CLI args (ERROR_INVALID_ARGS)
    1 — unexpected internal error
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Status taxonomy
# ---------------------------------------------------------------------------

STATUS_GREEN = "MAIN_CI_AUDIT_GREEN"
STATUS_HOLD_PENDING = "HOLD_MAIN_CI_PENDING"
STATUS_HOLD_FAILED = "HOLD_MAIN_CI_FAILED"
STATUS_HOLD_MISSING = "HOLD_MAIN_CI_MISSING_REQUIRED_WORKFLOW"
STATUS_HOLD_NO_RUNS = "HOLD_MAIN_CI_NO_RUNS_FOR_HEAD"
STATUS_ERROR_INVALID_ARGS = "ERROR_INVALID_ARGS"
STATUS_ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

# Workflow-run conclusions that mean "terminal failure" (not superseded, not pending)
TERMINAL_FAILURE_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure", "stale"}
)

# Workflow-run conclusions that mean "terminal but non-failure" (cancelled is the
# only canonical one — superseded-by-success is handled separately).
TERMINAL_NEUTRAL_CONCLUSIONS = frozenset({"cancelled", "skipped"})

# Statuses that indicate "do not proceed"
HOLD_STATUSES = frozenset(
    {
        STATUS_HOLD_PENDING,
        STATUS_HOLD_FAILED,
        STATUS_HOLD_MISSING,
        STATUS_HOLD_NO_RUNS,
    }
)

# Recommendation text per status
RECOMMENDATIONS = {
    STATUS_GREEN: "Main CI is green for the exact head.",
    STATUS_HOLD_PENDING: "Re-run the audit later; bounded polling expired with pending runs.",
    STATUS_HOLD_FAILED: "Do not proceed; inspect failed workflow runs.",
    STATUS_HOLD_MISSING: "Do not proceed; required workflow run missing for this head.",
    STATUS_HOLD_NO_RUNS: "Do not proceed; no workflow runs found for this exact head.",
    STATUS_ERROR_INVALID_ARGS: "Stop and inspect tool error.",
    STATUS_ERROR_TOOL_FAILURE: "Stop and inspect tool error.",
}

# GitHub Actions status values that mean "still in flight"
PENDING_STATUSES = frozenset(
    {"queued", "pending", "in_progress", "requested", "waiting"}
)

# Exact 40-character lowercase hex
SHA_REGEX = re.compile(r"^[0-9a-f]{40}$")

# Fields extracted from each gh run list row
RUN_JSON_FIELDS = (
    "databaseId",
    "name",
    "workflowName",
    "status",
    "conclusion",
    "headSha",
    "headBranch",
    "event",
    "createdAt",
    "updatedAt",
    "url",
    "displayTitle",
)

PACKET_KIND = "aed.main_ci.audit.v0"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="audit_main_ci_for_head.py",
        description="Read-only post-merge CI audit helper for an exact head SHA.",
    )
    parser.add_argument(
        "--repo", required=True, help="OWNER/REPO (e.g. Slideshow11/Automated-Edge-Discovery)"
    )
    parser.add_argument("--head-sha", required=True, help="Exact 40-char hex commit SHA")
    parser.add_argument("--branch", default="main", help="Branch name (default: main)")
    parser.add_argument(
        "--required-workflow",
        action="append",
        default=[],
        dest="required_workflow",
        help="Required workflow name (repeatable). Optional.",
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="gh run list --limit (default 20)"
    )
    parser.add_argument(
        "--max-polls", type=int, default=6, help="Maximum polls (default 6, min 1)"
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="Seconds between polls (default 30, max 30)",
    )
    parser.add_argument(
        "--output-json", required=True, help="Path to write JSON artifact"
    )
    parser.add_argument("--output-md", required=True, help="Path to write Markdown artifact")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> Tuple[bool, str]:
    if not SHA_REGEX.match(args.head_sha or ""):
        return False, f"head_sha must be exactly 40 lowercase hex chars: got {args.head_sha!r}"
    if not args.repo or "/" not in args.repo:
        return False, f"repo must be OWNER/REPO: got {args.repo!r}"
    if not args.branch:
        return False, "branch must be non-empty"
    if args.poll_seconds > 30:
        return False, f"poll-seconds must be <=30: got {args.poll_seconds}"
    if args.max_polls < 1:
        return False, f"max-polls must be >=1: got {args.max_polls}"
    if args.limit < 1:
        return False, f"limit must be >=1: got {args.limit}"
    for wf in args.required_workflow or []:
        if not wf or not isinstance(wf, str):
            return False, f"required-workflow must be non-empty string: got {wf!r}"
    if not args.output_json or not args.output_md:
        return False, "output-json and output-md are required"
    return True, "ok"


# ---------------------------------------------------------------------------
# gh invocation
# ---------------------------------------------------------------------------


def run_gh_run_list(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Invoke `gh run list` with list-form argv. Return parsed JSON list.

    Raises RuntimeError on subprocess failure, JSON parse failure, or shape errors.
    """
    argv = [
        "gh",
        "run",
        "list",
        "--repo",
        args.repo,
        "--branch",
        args.branch,
        "--limit",
        str(args.limit),
        "--json",
        ",".join(RUN_JSON_FIELDS),
    ]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            f"gh run list failed (rc={exc.returncode}): {stderr or '<no stderr>'}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("gh executable not found on PATH") from exc
    except OSError as exc:
        raise RuntimeError(f"gh run list OS error: {exc}") from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh run list returned invalid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError(
            f"gh run list returned non-list JSON: {type(data).__name__}"
        )
    return data


# ---------------------------------------------------------------------------
# Filtering and classification
# ---------------------------------------------------------------------------


def filter_runs_for_head(
    runs: List[Dict[str, Any]], head_sha: str
) -> List[Dict[str, Any]]:
    """Return only runs whose headSha exactly equals head_sha (case-insensitive)."""
    target = (head_sha or "").lower()
    out = []
    for r in runs:
        sha = (r.get("headSha") or "").lower()
        if sha == target:
            out.append(r)
    return out


def classify_runs(
    target_runs: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Classify target runs (legacy single-bucket classification).

    Returns (status, pending_runs, failed_runs, successful_runs).
    Priority: if any failed -> FAILED; else if all completed success -> GREEN;
    else -> PENDING (any still in flight).

    .. deprecated::
        This function does not handle workflow-keyed supersession of cancelled
        runs. New code should call :func:`classify_runs_for_workflows` instead.
        It is kept for backward compatibility and for the existing test
        ``test_green_status_two_runs_completed_success`` and
        ``test_pending_status_through_max_polls`` / ``test_failed_status_*``,
        which exercise the single-bucket behavior.
    """
    pending: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    successful: List[Dict[str, Any]] = []
    if not target_runs:
        return STATUS_HOLD_NO_RUNS, pending, failed, successful
    for r in target_runs:
        status = (r.get("status") or "").lower()
        conclusion = (r.get("conclusion") or "").lower()
        if status == "completed" and conclusion == "success":
            successful.append(r)
        elif status == "completed":
            failed.append(r)
        else:
            pending.append(r)
    if failed:
        return STATUS_HOLD_FAILED, pending, failed, successful
    if pending:
        return STATUS_HOLD_PENDING, pending, failed, successful
    return STATUS_GREEN, pending, failed, successful


def _run_sort_key(run: Dict[str, Any]) -> str:
    """Stable sort key: prefer createdAt, fall back to updatedAt, then databaseId."""
    created = (run.get("createdAt") or "").strip()
    if created:
        return created
    updated = (run.get("updatedAt") or "").strip()
    if updated:
        return updated
    dbid = run.get("databaseId")
    if dbid is not None:
        return f"id:{dbid}"
    return ""


def classify_runs_for_workflows(
    target_runs: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Classify target runs using newest-authoritative-per-workflow semantics
    with **in-flight precedence**.

    For each workflow name in the input, the runs are sorted newest-first
    (by createdAt, with updatedAt/databaseId as tiebreakers). The newest run
    overall for that workflow on the exact head is inspected FIRST:

    - If the newest run is in flight (queued / pending / in_progress /
      requested / waiting), the workflow is **PENDING**. An older completed
      success on the same workflow and head must NOT shadow the newer
      in-flight attempt — the in-flight rerun is the authoritative attempt
      and its outcome is not yet known.
    - Only if the newest run is terminal is its conclusion used as the
      authoritative verdict. Older runs (cancelled / skipped / failure /
      success / in-flight) are then classified as superseded history when
      the newest terminal run is success.

    Returns
    -------
    (status, pending_runs, failed_runs, successful_runs, superseded_cancelled_runs)

    - ``successful_runs``: the newest terminal success per workflow, when
      the newest run is terminal and concluded success. Older cancelled
      runs for the same workflow and head are **not** counted as failed.
    - ``failed_runs``: terminal-failure conclusions (failure / timed_out /
      action_required / startup_failure / stale) for the newest run of a
      workflow, OR a cancelled/skipped run that is the newest terminal run
      (no later success exists).
    - ``pending_runs``: the newest run for a workflow is still in flight
      (queued / in_progress / requested / waiting), regardless of whether
      an older terminal run exists. The audit must wait for the in-flight
      attempt to finish before declaring the workflow GREEN.
    - ``superseded_cancelled_runs``: older runs (cancelled / skipped /
      failure / success / in-flight) that are history relative to the
      authoritative verdict for the same workflow and exact head.

    Decision rules
    --------------
    1. Group runs by ``workflowName`` (case-sensitive exact match).
    2. Within each workflow group, sort newest-first by createdAt (with
       updatedAt/databaseId tiebreakers).
    3. Inspect ``runs_sorted[0]`` (the newest run overall) FIRST.
       - If its status is in-flight → workflow is PENDING. Older terminal
         runs (success / failure / cancelled / skipped) and older
         in-flight runs are reported as superseded history. The audit
         must NOT go green based on an older success.
       - If its status is terminal → use its conclusion as authoritative:
         - ``success`` → workflow is GREEN; older runs become superseded.
         - failure-class conclusions (failure / timed_out / action_required /
           startup_failure / stale) → workflow is FAILED.
         - ``cancelled`` / ``skipped`` → workflow is FAILED (no later
           terminal run exists; this is the newest terminal verdict).
         - unknown terminal conclusion → treat as failure conservatively.
    4. Across all required workflows: any FAILED → overall FAILED; any
       PENDING and none FAILED → overall PENDING; all GREEN → overall GREEN.
    """
    pending: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    successful: List[Dict[str, Any]] = []
    superseded_cancelled: List[Dict[str, Any]] = []

    if not target_runs:
        return STATUS_HOLD_NO_RUNS, pending, failed, successful, superseded_cancelled

    # Group by workflow name.
    by_workflow: Dict[str, List[Dict[str, Any]]] = {}
    for r in target_runs:
        wf = str(r.get("workflowName") or "")
        by_workflow.setdefault(wf, []).append(r)

    overall_has_failure = False
    overall_has_pending = False

    for wf, runs in by_workflow.items():
        # Sort newest-first.
        runs_sorted = sorted(runs, key=_run_sort_key, reverse=True)

        # Inspect the NEWEST run overall (runs_sorted[0]) BEFORE accepting
        # an older terminal verdict. This is the in-flight-precedence rule:
        # a newer in-flight rerun is the authoritative attempt for this
        # workflow/head, and an older completed success must NOT shadow it.
        # Without this check, a workflow with a newer in_progress run and
        # an older completed success would incorrectly return GREEN based
        # on the older success while the authoritative attempt is still
        # running. See audit_main_ci_for_head / Codex P2 finding on
        # classify_runs_for_workflows().
        newest = runs_sorted[0]
        newest_status = (newest.get("status") or "").lower()

        if newest_status in PENDING_STATUSES:
            # Newest run is in flight. Classify the workflow as PENDING
            # regardless of whether an older terminal run exists. The older
            # runs (terminal or in-flight) are reported as superseded
            # history so the audit reader can see what happened, but they
            # do NOT count as authoritative.
            pending.append(newest)
            overall_has_pending = True
            for older in runs_sorted[1:]:
                older_status = (older.get("status") or "").lower()
                if older_status == "completed":
                    older_conclusion = (older.get("conclusion") or "").lower()
                    if older_conclusion == "success":
                        # Older success is superseded by the newer in-flight
                        # attempt. Listed as history, NOT authoritative —
                        # the audit must wait for the in-flight run to
                        # finish before going GREEN.
                        superseded_cancelled.append(older)
                    elif older_conclusion in TERMINAL_NEUTRAL_CONCLUSIONS:
                        superseded_cancelled.append(older)
                    elif older_conclusion in TERMINAL_FAILURE_CONCLUSIONS:
                        # Older failure is also superseded by the newer
                        # in-flight attempt. Listed as history.
                        superseded_cancelled.append(older)
                    # Older terminal with unknown conclusion: drop (no
                    # signal — nothing to record as superseded).
                elif older_status in PENDING_STATUSES:
                    # Older in-flight runs are also reported as history
                    # when the newest run is the authoritative in-flight.
                    superseded_cancelled.append(older)
            # Do NOT use an older terminal run as authoritative — continue
            # to the next workflow with overall_has_pending already set.
            continue

        # Newest run is terminal. Use its conclusion as the authoritative
        # verdict for this workflow.
        conclusion = (newest.get("conclusion") or "").lower()
        if conclusion == "success":
            # Workflow is GREEN. All older terminal runs for this workflow
            # are reported as superseded history: cancelled/skipped and
            # failure-class conclusions are all moved to the superseded
            # bucket. They do not flip the verdict because the newest
            # terminal run for the same workflow is success.
            successful.append(newest)
            for older in runs_sorted[1:]:
                older_status = (older.get("status") or "").lower()
                if older_status == "completed":
                    older_conclusion = (older.get("conclusion") or "").lower()
                    if older_conclusion in TERMINAL_NEUTRAL_CONCLUSIONS:
                        superseded_cancelled.append(older)
                    elif older_conclusion in TERMINAL_FAILURE_CONCLUSIONS:
                        # Older failure-class runs are also superseded by
                        # the newer success on the same workflow/head.
                        # They are reported in superseded_cancelled_runs
                        # for full audit history, but they do not block
                        # the verdict.
                        superseded_cancelled.append(older)
                    # Older success: drop (newest success is authoritative)
                elif older_status in PENDING_STATUSES:
                    # In-flight older runs are also superseded by success.
                    superseded_cancelled.append(older)
        elif conclusion in TERMINAL_FAILURE_CONCLUSIONS:
            # Newest terminal is a real failure. Workflow is FAILED.
            failed.append(newest)
            overall_has_failure = True
            # Older runs are not classified as superseded; report as history.
            for older in runs_sorted[1:]:
                older_status = (older.get("status") or "").lower()
                if older_status == "completed":
                    older_conclusion = (older.get("conclusion") or "").lower()
                    if older_conclusion in TERMINAL_FAILURE_CONCLUSIONS:
                        failed.append(older)
        elif conclusion in TERMINAL_NEUTRAL_CONCLUSIONS:
            # Newest terminal is cancelled/skipped. No later success exists
            # (by construction: this is the newest terminal run). Workflow
            # is FAILED.
            failed.append(newest)
            overall_has_failure = True
        else:
            # Unknown terminal conclusion → treat as failure conservatively.
            failed.append(newest)
            overall_has_failure = True

    if overall_has_failure:
        return STATUS_HOLD_FAILED, pending, failed, successful, superseded_cancelled
    if overall_has_pending:
        return STATUS_HOLD_PENDING, pending, failed, successful, superseded_cancelled
    return STATUS_GREEN, pending, failed, successful, superseded_cancelled


def missing_required_workflows(
    required: List[str], found_workflow_names: List[str]
) -> List[str]:
    found = set(found_workflow_names)
    return [w for w in required if w not in found]


# ---------------------------------------------------------------------------
# Packet building
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_packet(
    args: argparse.Namespace,
    status: str,
    runs_for_head: List[Dict[str, Any]],
    pending_runs: List[Dict[str, Any]],
    failed_runs: List[Dict[str, Any]],
    successful_runs: List[Dict[str, Any]],
    missing_required: List[str],
    polls_used: int,
    commands_run: List[List[str]],
    errors: List[str],
    superseded_cancelled_runs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    target_workflow_names = list(args.required_workflow or [])
    if not target_workflow_names:
        target_workflow_names = sorted(
            {str(r.get("workflowName") or "") for r in runs_for_head if r.get("workflowName")}
        )
    superseded_cancelled_runs = superseded_cancelled_runs or []
    summary_counts = {
        "runs_for_head_total": len(runs_for_head),
        "pending": len(pending_runs),
        "failed": len(failed_runs),
        "successful": len(successful_runs),
        "superseded_cancelled": len(superseded_cancelled_runs),
        "missing_required_workflows": len(missing_required),
    }
    return {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "repo": args.repo,
        "branch": args.branch,
        "head_sha": args.head_sha,
        "status": status,
        "max_polls": args.max_polls,
        "poll_seconds": args.poll_seconds,
        "polls_used": polls_used,
        "required_workflows": list(args.required_workflow or []),
        "target_workflow_names": target_workflow_names,
        "runs_for_head": runs_for_head,
        "missing_required_workflows": missing_required,
        "pending_runs": pending_runs,
        "failed_runs": failed_runs,
        "successful_runs": successful_runs,
        "superseded_cancelled_runs": superseded_cancelled_runs,
        "summary": summary_counts,
        "errors": errors,
        "commands_run": commands_run,
        "recommendation": RECOMMENDATIONS.get(status, "Stop and inspect tool error."),
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _md_escape(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _md_run_table(runs: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    lines.append("| databaseId | workflowName | status | conclusion | updatedAt | url |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    if not runs:
        lines.append("|  | _(none)_ |  |  |  |  |")
        return lines
    for r in runs:
        lines.append(
            "| {dbid} | {wf} | {st} | {con} | {upd} | {url} |".format(
                dbid=_md_escape(r.get("databaseId", "")),
                wf=_md_escape(r.get("workflowName", "")),
                st=_md_escape(r.get("status", "")),
                con=_md_escape(r.get("conclusion", "")),
                upd=_md_escape(r.get("updatedAt", "")),
                url=_md_escape(r.get("url", "")),
            )
        )
    return lines


def render_markdown(packet: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Post-Merge CI Audit")
    lines.append("")
    lines.append(f"**Repo**: `{packet['repo']}`")
    lines.append(f"**Branch**: `{packet['branch']}`")
    lines.append(f"**Head SHA**: `{packet['head_sha']}`")
    lines.append(f"**Final status**: `{packet['status']}`")
    lines.append(f"**Polls used**: {packet['polls_used']} of {packet['max_polls']} (poll-seconds={packet['poll_seconds']})")
    lines.append(f"**Recommendation**: {packet.get('recommendation','')}")
    lines.append("")
    status = packet.get("status", "")
    if status == STATUS_HOLD_MISSING:
        lines.append("## Polling outcome")
        lines.append("")
        lines.append(
            "Bounded polling exhausted with one or more **required workflows still missing** "
            "for the exact head SHA. Re-run later; GitHub may not have surfaced the run yet, "
            "or the workflow was never triggered for this head."
        )
        lines.append("")
    elif status == STATUS_HOLD_PENDING:
        lines.append("## Polling outcome")
        lines.append("")
        lines.append(
            "Bounded polling exhausted with **required workflows present but still in flight** "
            "for the exact head SHA. Re-run later; the workflows are running and have not yet "
            "posted a terminal conclusion."
        )
        lines.append("")
    lines.append("## Required workflows")
    if packet.get("required_workflows"):
        for w in packet["required_workflows"]:
            lines.append(f"- `{w}`")
    else:
        lines.append("_(none specified — all runs found for the exact head are evaluated)_")
    lines.append("")
    lines.append("## Target runs for head")
    lines.extend(_md_run_table(packet.get("runs_for_head", [])))
    lines.append("")
    lines.append("## Missing required workflows")
    if packet.get("missing_required_workflows"):
        for w in packet["missing_required_workflows"]:
            lines.append(f"- `{w}`")
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Pending runs")
    lines.extend(_md_run_table(packet.get("pending_runs", [])))
    lines.append("")
    lines.append("## Failed runs")
    lines.extend(_md_run_table(packet.get("failed_runs", [])))
    lines.append("")
    lines.append("## Successful runs")
    lines.extend(_md_run_table(packet.get("successful_runs", [])))
    lines.append("")
    lines.append("## Superseded cancelled runs")
    lines.append("")
    lines.append(
        "Older cancelled or skipped runs for a required workflow whose "
        "authoritative verdict is a later successful run on the same exact "
        "head. These runs are reported as history and do **not** count as "
        "failures when the newest terminal run for the same workflow is "
        "`success`."
    )
    lines.extend(_md_run_table(packet.get("superseded_cancelled_runs", [])))
    lines.append("")
    lines.append("## Commands run")
    if packet.get("commands_run"):
        for cmd in packet["commands_run"]:
            lines.append("- `" + " ".join(_md_escape(part) for part in cmd) + "`")
    else:
        lines.append("_(none — exit before any gh call)_")
    lines.append("")
    lines.append("## Errors")
    if packet.get("errors"):
        for e in packet["errors"]:
            lines.append(f"- {e}")
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Summary")
    for k, v in packet.get("summary", {}).items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def write_outputs(
    packet: Dict[str, Any], output_json: str, output_md: str
) -> Tuple[bool, str]:
    try:
        json_path = Path(output_json)
        md_path = Path(output_md)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(packet, indent=2, sort_keys=False) + "\n")
        md_path.write_text(render_markdown(packet))
    except OSError as exc:
        return False, f"write_outputs failed: {exc}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Main audit loop
# ---------------------------------------------------------------------------


def _error_packet(
    args: argparse.Namespace,
    status: str,
    error_msg: str,
    commands_run: List[List[str]],
) -> Dict[str, Any]:
    return build_packet(
        args=args,
        status=status,
        runs_for_head=[],
        pending_runs=[],
        failed_runs=[],
        successful_runs=[],
        missing_required=[],
        polls_used=0,
        commands_run=commands_run,
        errors=[error_msg],
    )


def audit(args: argparse.Namespace) -> Dict[str, Any]:
    """Run the audit. Return the JSON-serializable packet dict.

    Side effect: sleeps via time.sleep between polls.
    """
    commands_run: List[List[str]] = []
    errors: List[str] = []
    required = list(args.required_workflow or [])
    require_workflow_list = bool(required)

    runs_for_head: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    successful: List[Dict[str, Any]] = []
    superseded_cancelled: List[Dict[str, Any]] = []
    missing: List[str] = []

    polls_used = 0
    target_workflow_names: List[str] = []

    for poll_index in range(args.max_polls):
        polls_used += 1
        argv = [
            "gh",
            "run",
            "list",
            "--repo",
            args.repo,
            "--branch",
            args.branch,
            "--limit",
            str(args.limit),
            "--json",
            ",".join(RUN_JSON_FIELDS),
        ]
        commands_run.append(list(argv))
        try:
            runs_all = run_gh_run_list(args)
        except RuntimeError as exc:
            errors.append(str(exc))
            return _error_packet(
                args, STATUS_ERROR_TOOL_FAILURE, str(exc), commands_run
            )

        runs_for_head = filter_runs_for_head(runs_all, args.head_sha)

        if require_workflow_list:
            found_names = sorted(
                {str(r.get("workflowName") or "") for r in runs_for_head}
            )
            # Record missing required workflows but DO NOT return early.
            # Missing required workflows are treated as pending until the
            # bounded polling window is exhausted. Only then do we report
            # HOLD_MAIN_CI_MISSING_REQUIRED_WORKFLOW. This matches the
            # wait_for_pr_ready.py convention: absent checks during the
            # polling window are not yet "missing" — GitHub may not have
            # surfaced the run yet.
            missing = missing_required_workflows(required, found_names)
            target_workflow_names = [w for w in required if w in set(found_names)]
        else:
            if not runs_for_head:
                return build_packet(
                    args=args,
                    status=STATUS_HOLD_NO_RUNS,
                    runs_for_head=[],
                    pending_runs=[],
                    failed_runs=[],
                    successful_runs=[],
                    missing_required=[],
                    polls_used=polls_used,
                    commands_run=commands_run,
                    errors=errors,
                )
            target_workflow_names = sorted(
                {str(r.get("workflowName") or "") for r in runs_for_head}
            )
            missing = []

        target_runs = [
            r
            for r in runs_for_head
            if str(r.get("workflowName") or "") in set(target_workflow_names)
        ]

        if target_runs:
            (
                status,
                pending,
                failed,
                successful,
                superseded_cancelled,
            ) = classify_runs_for_workflows(target_runs)
        else:
            # No target runs in this poll (e.g., all required workflows are
            # still missing). Treat as pending — keep polling.
            status, pending, failed, successful, superseded_cancelled = (
                STATUS_HOLD_PENDING,
                [],
                [],
                [],
                [],
            )

        if failed:
            return build_packet(
                args=args,
                status=STATUS_HOLD_FAILED,
                runs_for_head=runs_for_head,
                pending_runs=pending,
                failed_runs=failed,
                successful_runs=successful,
                missing_required=list(missing),
                polls_used=polls_used,
                commands_run=commands_run,
                errors=errors,
                superseded_cancelled_runs=list(superseded_cancelled),
            )

        # GREEN is only reachable when no required workflow is missing AND
        # every present required run is completed successfully. If any
        # required workflow is still missing we must keep polling — a partial
        # green during the polling window is not the final verdict.
        if not missing and status == STATUS_GREEN:
            return build_packet(
                args=args,
                status=STATUS_GREEN,
                runs_for_head=runs_for_head,
                pending_runs=pending,
                failed_runs=failed,
                successful_runs=successful,
                missing_required=[],
                polls_used=polls_used,
                commands_run=commands_run,
                errors=errors,
                superseded_cancelled_runs=list(superseded_cancelled),
            )

        # Either some required workflow is still missing, or some target
        # run is still in flight, or both. Continue polling.
        if poll_index < args.max_polls - 1:
            time.sleep(args.poll_seconds)

    # Polling bound exhausted. Distinguish MISSING from PENDING based on
    # whether any required workflow never appeared.
    if missing:
        return build_packet(
            args=args,
            status=STATUS_HOLD_MISSING,
            runs_for_head=runs_for_head,
            pending_runs=pending,
            failed_runs=failed,
            successful_runs=successful,
            missing_required=list(missing),
            polls_used=polls_used,
            commands_run=commands_run,
            errors=errors,
            superseded_cancelled_runs=list(superseded_cancelled),
        )
    return build_packet(
        args=args,
        status=STATUS_HOLD_PENDING,
        runs_for_head=runs_for_head,
        pending_runs=pending,
        failed_runs=failed,
        successful_runs=successful,
        missing_required=[],
        polls_used=polls_used,
        commands_run=commands_run,
        errors=errors,
        superseded_cancelled_runs=list(superseded_cancelled),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    ok, msg = validate_args(args)
    if not ok:
        packet = _error_packet(args, STATUS_ERROR_INVALID_ARGS, msg, commands_run=[])
        write_outputs(packet, args.output_json, args.output_md)
        return 2
    packet = audit(args)
    write_ok, write_err = write_outputs(packet, args.output_json, args.output_md)
    if not write_ok:
        # Already wrote once; best-effort re-emit with ERROR_TOOL_FAILURE
        packet["errors"].append(write_err)
        packet["status"] = STATUS_ERROR_TOOL_FAILURE
        packet["recommendation"] = RECOMMENDATIONS[STATUS_ERROR_TOOL_FAILURE]
        try:
            Path(args.output_json).write_text(
                json.dumps(packet, indent=2, sort_keys=False) + "\n"
            )
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
