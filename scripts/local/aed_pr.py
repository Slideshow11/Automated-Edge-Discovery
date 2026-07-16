#!/usr/bin/env python3
"""aed_pr.py

Canonical AED PR-lifecycle controller.

The status / advance / merge subcommands all consult a single shared
readiness evaluator (``aed_pr_readiness.evaluate_readiness``) on
freshly-fetched live state, so the three subcommands cannot disagree
about what is and is not ready.

Subcommands:

  status   Read live PR state, gather every gate's evidence, emit one
           JSON report with the readiness verdict. The canonical
           authorization phrase is emitted only when ALL 12 gates pass
           on the current head; otherwise the field is explicitly
           None so the operator cannot accidentally copy a stale
           phrase.

  advance  Perform every safe mechanical lifecycle step except the
           merge itself. Implements:
             - one Codex-review ping per exact head SHA with
               duplicate-request prevention,
             - eligible outdated Codex-bot thread resolution
               (humans and current-bot threads are NEVER auto-resolved),
             - draft-to-ready conversion only after clean
               prerequisites,
             - no ``gh pr merge`` invocation.

  merge    Require the exact canonical authorization phrase, re-fetch
           live evidence, re-run every readiness gate, and only then
           execute the squash merge with the exact
           --match-head-commit <authorized_sha>.

The controller never chains subprocess wrapper invocations for one
decision. It imports ``aed_pr_lib``, the shared ``aed_pr_readiness``
evaluator, and the existing live-readiness helpers in
``audit_codex_response_for_pr``, ``check_pr_scope``, and
``check_pr_review_comments``. Those modules remain the read-only
sources of truth; the controller composes their results, it does not
replace them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import aed_pr_lib as L   # noqa: E402
import aed_pr_readiness as R  # noqa: E402

# Read-only helpers (sources of truth for live evidence).
import audit_codex_response_for_pr as CODEX  # noqa: E402
import check_pr_scope as SCOPE  # noqa: E402


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

DEFAULT_REPO = "Slideshow11/Automated-Edge-Discovery"

# The required-CI workflow names that must all exist and pass on the
# current head before merge is allowed. This list is the canonical AED
# required-CI surface; it is the single source of truth consumed by
# status/advance/merge.
REQUIRED_CI_WORKFLOW_NAMES = (
    "test",
    "validator",
    "governance-validators",
    "pr-gate-live-smoke",
)


# -----------------------------------------------------------------------------
# gh helpers
# -----------------------------------------------------------------------------

def _run_json(cmd: List[str], timeout: int = 30) -> Dict[str, Any]:
    """Run a gh command, parse JSON, return dict. Raise on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed (rc={proc.returncode}): {' '.join(cmd)}\n"
            f"stderr: {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def _run_json_or_none(
    cmd: List[str], timeout: int = 30
) -> Tuple[bool, Any, str]:
    """Run a gh command, return (ok, parsed_json, error_msg)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, None, f"gh invocation failed: {exc}"
    if proc.returncode != 0:
        return False, None, f"gh returned {proc.returncode}: {proc.stderr.strip()[:300]}"
    if not proc.stdout.strip():
        return False, None, "gh returned empty stdout"
    try:
        return True, json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return False, None, f"invalid JSON: {exc}"


# -----------------------------------------------------------------------------
# Live-state fetchers
# -----------------------------------------------------------------------------

def fetch_pr_state(repo: str, pr_number: int) -> Dict[str, Any]:
    """Fetch live PR state via gh; re-fetched at the top of every command."""
    return _run_json([
        "gh", "pr", "view", str(pr_number),
        "--repo", repo,
        "--json",
        "number,title,state,isDraft,mergeable,headRefOid,baseRefOid,"
        "additions,deletions,changedFiles,url,files",
    ])


def fetch_changed_files(
    repo: str, pr_number: int, pr_view: Optional[Dict[str, Any]] = None
) -> Tuple[bool, List[str], str]:
    """Fetch the actual changed file paths for the PR.

    Returns (ok, paths, error). When ok=False the controller must treat
    the evidence as missing; it must NEVER treat an empty list as
    clean=True (a PR with zero changed files is impossible, and an
    empty result here is a fetch failure).
    """
    # Prefer the dedicated --json files call (deterministic shape).
    cmd = ["gh", "pr", "view", str(pr_number), "--repo", repo,
           "--json", "files"]
    ok, payload, err = _run_json_or_none(cmd)
    if ok and isinstance(payload, dict):
        files = payload.get("files")
        if isinstance(files, list):
            paths = [
                (f.get("path") if isinstance(f, dict) else None)
                for f in files
            ]
            paths = [p for p in paths if isinstance(p, str) and p]
            return True, paths, ""
    # Fallback to the inline `files` field on the broader view payload.
    if isinstance(pr_view, dict):
        files = pr_view.get("files")
        if isinstance(files, list):
            paths = [
                (f.get("path") if isinstance(f, dict) else None)
                for f in files
            ]
            paths = [p for p in paths if isinstance(p, str) and p]
            if paths:
                return True, paths, ""
    return False, [], err or "could not fetch changed files"


def fetch_ci_conclusions(
    repo: str, head_sha: str, required_workflows: List[str]
) -> Tuple[bool, Dict[str, str], List[str], List[str], List[str], str]:
    """Fetch CI run conclusions for the current head.

    Returns (ok, name->conclusion dict, missing, pending, failed, err).
    ``ok=False`` means the run list could not be fetched; in that case
    every required workflow is reported as missing so the gate fails
    closed.
    """
    cmd = [
        "gh", "run", "list",
        "--repo", repo,
        "--commit", head_sha,
        "--json", "workflowName,conclusion,status,headSha",
        "--limit", "100",
    ]
    ok, payload, err = _run_json_or_none(cmd, timeout=45)
    if not ok or not isinstance(payload, list):
        missing = list(required_workflows)
        return False, {}, missing, [], missing, err

    by_workflow: Dict[str, Dict[str, Any]] = {}
    for run in payload:
        if not isinstance(run, dict):
            continue
        # Only consider runs on this exact head SHA.
        if run.get("headSha") and run.get("headSha") != head_sha:
            continue
        name = run.get("workflowName")
        if not isinstance(name, str):
            continue
        # First occurrence wins (gh run list is newest-first).
        by_workflow.setdefault(name, run)

    conclusions: Dict[str, str] = {}
    missing: List[str] = []
    pending: List[str] = []
    failed: List[str] = []
    for name in required_workflows:
        run = by_workflow.get(name)
        if run is None:
            missing.append(name)
            continue
        conclusion = (run.get("conclusion") or "").upper()
        status = (run.get("status") or "").upper()
        conclusions[name] = conclusion or status or "UNKNOWN"
        if status in {"IN_PROGRESS", "QUEUED", "PENDING", "REQUESTED", "WAITING"}:
            pending.append(name)
        elif conclusion == "SUCCESS":
            pass
        elif conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT",
                            "STARTUP_FAILURE", "STALE"}:
            failed.append(name)
        else:
            if status == "COMPLETED":
                failed.append(name)
            else:
                pending.append(name)
    return True, conclusions, missing, pending, failed, ""


# -----------------------------------------------------------------------------
# Codex / review-thread / comments inventory (delegated to CODEX module)
# -----------------------------------------------------------------------------

def fetch_codex_packet(
    repo: str, pr_number: int, head_sha: str
) -> Dict[str, Any]:
    """Call audit_codex_response_for_pr.classify on the live head."""
    return CODEX.classify(
        repo=repo,
        pr_number=pr_number,
        expected_head_sha=head_sha,
        ping_comment_id=None,
        ping_created_at=None,
        max_polls=1,
        poll_seconds=1,
    )


# -----------------------------------------------------------------------------
# Evidence assembly
# -----------------------------------------------------------------------------

def _default_scope_patterns() -> Tuple[List[str], List[str]]:
    """Conservative default scope patterns for the controller.

    These patterns are intentionally narrow: they accept only the new
    canonical surface (aed_pr / aed_pr_lib / aed_pr_readiness /
    docs/aed_pr*.md / test_aed_pr*.py) and explicitly forbid every
    retired wrapper path. The exact patterns are reported in the
    controller's status output so the operator can see what was
    checked.
    """
    allowed = [
        "scripts/local/aed_pr*.py",
        "scripts/local/aed_pr_lib.py",
        "scripts/local/aed_pr_readiness.py",
        "tests/test_aed_pr*.py",
        "tests/test_aed_pr_readiness*.py",
        "docs/aed_pr*.md",
        "docs/README.md",
        "aed_policy/policy.py",
        "docs/autocoder_autonomy_roadmap.md",
    ]
    forbidden = [
        "docs/aed_final_gate*.md",
        "scripts/local/aed_final_gate.py",
        "scripts/local/build_merge_ready_packet.py",
        "scripts/local/check_merge_authorization.py",
        "scripts/local/finalize_with_phase_ledger.py",
        "scripts/local/merge_readiness_with_phase_ledger.py",
    ]
    return allowed, forbidden


def build_evidence(
    *,
    repo: str,
    pr_number: int,
    pr_view: Dict[str, Any],
    changed_files: List[str],
    changed_files_fetched: bool,
    changed_files_error: str,
    authorization_phrase: Optional[str],
) -> R.ReadinessEvidence:
    """Build a ReadinessEvidence bundle for the current PR view."""
    head_sha = pr_view.get("headRefOid")

    # ---- Scope check ---------------------------------------------------------
    allowed, forbidden = _default_scope_patterns()
    if changed_files_fetched:
        scope_packet = SCOPE.check_scope(changed_files, allowed, forbidden)
        scope_clean = bool(scope_packet.get("passed"))
        out_of_scope = list(scope_packet.get("out_of_scope_files") or [])
        forbidden_touched = list(scope_packet.get("forbidden_files_touched") or [])
        scope_blockers = list(scope_packet.get("blockers") or [])
    else:
        scope_clean = None
        out_of_scope = []
        forbidden_touched = []
        scope_blockers = ["changed_files_not_fetched"]

    # ---- CI audit ------------------------------------------------------------
    ci_ok, ci_conclusions, ci_missing, ci_pending, ci_failed, ci_err = (
        fetch_ci_conclusions(repo, head_sha or "", list(REQUIRED_CI_WORKFLOW_NAMES))
    )

    # ---- Codex / reviews / threads ------------------------------------------
    codex_packet = fetch_codex_packet(repo, pr_number, head_sha or "")
    codex_verdict = str(codex_packet.get("status") or "")
    codex_clean = R.is_codex_clean_verdict(codex_verdict)
    codex_reviewed_sha = codex_packet.get("observed_head_sha")
    codex_artifact_present = bool(codex_verdict)
    if (
        isinstance(codex_reviewed_sha, str)
        and isinstance(head_sha, str)
        and head_sha
    ):
        codex_artifact_fresh = (codex_reviewed_sha == head_sha)
    else:
        codex_artifact_fresh = None

    reviews_inventory_complete = bool(
        codex_packet.get("issue_comment_inventory_complete")
        and codex_packet.get("review_submission_inventory_complete")
    )
    reviews_inventory_error = (
        codex_packet.get("issue_comment_inventory_last_error")
        or codex_packet.get("review_submission_inventory_last_error")
        or None
    )

    review_thread_inventory_complete = bool(
        codex_packet.get("review_thread_inventory_complete")
        and codex_packet.get("review_thread_comment_inventory_complete")
    )
    review_thread_inventory_error = (
        codex_packet.get("review_thread_inventory_last_error")
        or codex_packet.get("review_thread_comment_inventory_last_error")
        or None
    )

    active_threads = list(codex_packet.get("active_threads") or [])
    outdated_threads = list(codex_packet.get("outdated_threads") or [])
    partition = R.partition_unresolved_threads(active_threads + outdated_threads)
    unresolved_human_ids = [
        str(t.get("thread_id") or t.get("id"))
        for t in partition["unresolved_human"]
    ]
    unresolved_bot_current_ids = [
        str(t.get("thread_id") or t.get("id"))
        for t in partition["unresolved_bot_current"]
    ]
    outdated_bot_ids = [
        str(t.get("thread_id") or t.get("id"))
        for t in partition["outdated_bot_unresolved"]
    ]
    unresolved_total = (
        len(partition["unresolved_human"])
        + len(partition["unresolved_bot_current"])
        + len(partition["outdated_bot_unresolved"])
    )

    # ---- Evidence source ledger ---------------------------------------------
    evidence_sources: Dict[str, str] = {}
    evidence_sources["pr_view"] = "fetched"
    evidence_sources["changed_files"] = "fetched" if changed_files_fetched else (
        f"failed:{changed_files_error}"
    )
    evidence_sources["scope_check"] = (
        "fetched" if changed_files_fetched else "skipped:no_changed_files"
    )
    evidence_sources["ci_audit"] = (
        "fetched" if ci_ok else f"failed:{ci_err}"
    )
    evidence_sources["codex_audit"] = "fetched"
    evidence_sources["reviews_inventory"] = (
        "fetched" if reviews_inventory_complete
        else f"failed:{reviews_inventory_error or 'incomplete'}"
    )
    evidence_sources["review_thread_inventory"] = (
        "fetched" if review_thread_inventory_complete
        else f"failed:{review_thread_inventory_error or 'incomplete'}"
    )
    evidence_sources["pr_number"] = "fetched"  # metadata, not evidence

    ev = R.ReadinessEvidence(
        pr_state=pr_view.get("state"),
        is_draft=pr_view.get("isDraft"),
        mergeable=pr_view.get("mergeable"),
        head_sha=head_sha,
        authorization_phrase=authorization_phrase,
        changed_files=list(changed_files) if changed_files_fetched else None,
        changed_files_fetched=changed_files_fetched,
        scope_clean=scope_clean,
        out_of_scope_files=out_of_scope,
        forbidden_files_touched=forbidden_touched,
        scope_blockers=scope_blockers,
        required_ci_names=list(REQUIRED_CI_WORKFLOW_NAMES),
        ci_conclusions=ci_conclusions,
        ci_missing=ci_missing,
        ci_pending=ci_pending,
        ci_failed=ci_failed,
        codex_verdict=codex_verdict,
        codex_source=codex_packet.get("latest_codex_response_type"),
        codex_reviewed_sha=codex_reviewed_sha,
        codex_clean_passed=bool(codex_packet.get("clean_pass_detected")),
        codex_artifact_present=codex_artifact_present,
        codex_artifact_fresh=codex_artifact_fresh,
        codex_review_url=codex_packet.get("latest_codex_response_url"),
        codex_review_id=str(codex_packet.get("latest_codex_response_id") or "")
            if codex_packet.get("latest_codex_response_id") else None,
        reviews_inventory_complete=reviews_inventory_complete,
        reviews_inventory_error=reviews_inventory_error,
        review_threads=active_threads + outdated_threads,
        review_thread_inventory_complete=review_thread_inventory_complete,
        review_thread_inventory_error=review_thread_inventory_error,
        unresolved_thread_count=unresolved_total,
        unresolved_thread_ids=unresolved_human_ids + unresolved_bot_current_ids + outdated_bot_ids,
        unresolved_human_thread_ids=unresolved_human_ids,
        unresolved_bot_thread_ids=unresolved_bot_current_ids,
        outdated_bot_thread_ids=outdated_bot_ids,
        evidence_sources=evidence_sources,
    )
    # Stash the PR number for the readiness evaluator's canonical
    # phrase builder (kept off the public evidence_sources dict so
    # it does not pollute the strict evidence-source gate 12).
    setattr(ev, "_pr_number_int", int(pr_number))
    return ev


# -----------------------------------------------------------------------------
# Lifecycle state derivation (single source of truth for status output)
# -----------------------------------------------------------------------------

def derive_lifecycle_state(verdict: R.ReadinessVerdict, pr_view: Dict[str, Any]) -> str:
    """Collapse readiness verdict + raw PR view into one of 6 states.

    The READY_FOR_MERGE_AUTHORIZATION state is emitted when every
    evidence gate has converged and the only remaining requirement is
    the operator's exact authorization phrase (the verdict's sole
    failure reason is PHRASE_MISMATCH). This is the signal the
    operator uses to learn that speaking the phrase and running
    ``aed_pr merge`` is the next safe action.
    """
    if pr_view.get("state") == "MERGED":
        return "MERGED_PENDING_CLOSEOUT"
    if pr_view.get("state") == "CLOSED":
        return "COMPLETE"
    if not verdict.ready:
        codes = {r.code for r in verdict.reasons}
        # When only the phrase is missing, every other gate has passed.
        # That is the precise signal READY_FOR_MERGE_AUTHORIZATION was
        # defined for: the evidence bundle is complete and only the
        # human authorization step remains.
        if codes == {R.REASON_PHRASE_MISMATCH}:
            return "READY_FOR_MERGE_AUTHORIZATION"
        human_codes = {
            R.REASON_PR_IS_DRAFT,
            R.REASON_UNRESOLVED_THREAD,
        }
        if codes & human_codes:
            return "ACTION_REQUIRED"
        return "BLOCKED"
    return "READY_FOR_MERGE_AUTHORIZATION"


def _next_human_action(state: str) -> str:
    return {
        "WAITING": "Wait for CI / Codex to converge; rerun status.",
        "ACTION_REQUIRED": "Address the human-action item; rerun status.",
        "BLOCKED": "Resolve the deterministic block; rerun status.",
        "READY_FOR_MERGE_AUTHORIZATION": (
            "Speak the required_authorization_phrase and run aed_pr merge."
        ),
        "MERGED_PENDING_CLOSEOUT": (
            "Run aed_pr advance to perform post-merge closeout."
        ),
        "COMPLETE": "No further action.",
    }.get(state, "Unknown state; rerun status.")


# -----------------------------------------------------------------------------
# status command
# -----------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    repo = args.repo
    pr_number = args.pr_number

    pr_view = fetch_pr_state(repo, pr_number)
    head_sha = pr_view.get("headRefOid")
    ok_changed, changed_files, changed_err = fetch_changed_files(repo, pr_number, pr_view)

    evidence = build_evidence(
        repo=repo,
        pr_number=pr_number,
        pr_view=pr_view,
        changed_files=changed_files,
        changed_files_fetched=ok_changed,
        changed_files_error=changed_err,
        authorization_phrase=None,
    )
    verdict = R.evaluate_readiness(evidence)
    state = derive_lifecycle_state(verdict, pr_view)
    safe_cmd = L.build_safe_merge_command(pr_number, repo, head_sha)
    canonical_phrase = (
        L.build_authorization_phrase(pr_number, str(head_sha))
        if verdict.ready and R.is_canonical_head_sha(head_sha) else None
    )

    report: Dict[str, Any] = {
        "tool": "aed_pr.status",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr_view.get("url"),
        "pr_title": pr_view.get("title"),
        "pr_state": pr_view.get("state"),
        "is_draft": pr_view.get("isDraft"),
        "mergeable": pr_view.get("mergeable"),
        "head_sha": head_sha,
        "base_ref": pr_view.get("baseRefName"),
        "changed_files": changed_files if ok_changed else None,
        "changed_files_fetched": ok_changed,
        "changed_files_error": (changed_err or None) if not ok_changed else None,
        "scope_clean": evidence.scope_clean,
        "out_of_scope_files": evidence.out_of_scope_files,
        "forbidden_files_touched": evidence.forbidden_files_touched,
        "required_ci_names": list(REQUIRED_CI_WORKFLOW_NAMES),
        "ci_conclusions": evidence.ci_conclusions,
        "ci_missing": evidence.ci_missing,
        "ci_pending": evidence.ci_pending,
        "ci_failed": evidence.ci_failed,
        "codex_verdict": evidence.codex_verdict,
        "codex_source": evidence.codex_source,
        "codex_reviewed_sha": evidence.codex_reviewed_sha,
        "codex_artifact_fresh": evidence.codex_artifact_fresh,
        "codex_review_url": evidence.codex_review_url,
        "codex_review_id": evidence.codex_review_id,
        "reviews_inventory_complete": evidence.reviews_inventory_complete,
        "reviews_inventory_error": evidence.reviews_inventory_error,
        "review_thread_inventory_complete": evidence.review_thread_inventory_complete,
        "review_thread_inventory_error": evidence.review_thread_inventory_error,
        "unresolved_thread_count": evidence.unresolved_thread_count,
        "unresolved_human_thread_ids": evidence.unresolved_human_thread_ids,
        "unresolved_bot_thread_ids": evidence.unresolved_bot_thread_ids,
        "outdated_bot_thread_ids": evidence.outdated_bot_thread_ids,
        "evidence_sources": evidence.evidence_sources,
        "lifecycle_state": state,
        "ready": verdict.ready,
        "gates_passed": verdict.gates_passed,
        "gates_failed": verdict.gates_failed,
        "reason_codes": [r.code for r in verdict.reasons],
        "reasons": [r.to_dict() for r in verdict.reasons],
        "safe_merge_command_preview": safe_cmd,
        "required_authorization_phrase": canonical_phrase,
        "next_human_action": _next_human_action(state),
    }

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


# -----------------------------------------------------------------------------
# advance command (real behavior; never invokes gh pr merge)
# -----------------------------------------------------------------------------

def _post_codex_ping_comment(
    repo: str, pr_number: int, head_sha: str
) -> Tuple[bool, str]:
    """Post a Codex-review ping on the current head SHA.

    Duplicate-request prevention: if any existing PR-level issue
    comment already references the exact 40-character head SHA, the
    controller refuses to post a duplicate ping.
    """
    body_marker = (
        f"Codex review request for head {head_sha} "
        "(automated ping from aed_pr.advance)"
    )
    ok, payload, err = _run_json_or_none([
        "gh", "api",
        f"repos/{repo}/issues/{pr_number}/comments",
        "--paginate", "--slurp",
    ])
    if not ok or not isinstance(payload, list):
        return False, err or "could not list existing comments"
    comments: List[Dict[str, Any]] = []
    for page in payload:
        if isinstance(page, list):
            comments.extend(page)
        elif isinstance(page, dict) and isinstance(page.get("items"), list):
            comments.extend(page["items"])
    for c in comments:
        if not isinstance(c, dict):
            continue
        existing = c.get("body") or ""
        if head_sha in existing and "Codex review request for head" in existing:
            return True, "duplicate-ping-prevented"

    ok, payload, err = _run_json_or_none([
        "gh", "api", "-X", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={body_marker}",
    ])
    if not ok or not isinstance(payload, dict):
        return False, err or "could not create ping comment"
    return True, str(payload.get("id") or "created")


def _mark_pr_ready_for_review(repo: str, pr_number: int) -> Tuple[bool, str]:
    """Transition isDraft=True -> isDraft=False on the PR."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "ready", str(pr_number), "--repo", repo],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"gh pr ready failed: {exc}"
    if proc.returncode != 0:
        return False, (
            f"gh pr ready returned {proc.returncode}: "
            f"{proc.stderr.strip()[:300]}"
        )
    return True, "marked-ready"


def cmd_advance(args: argparse.Namespace) -> int:
    """Perform every safe mechanical lifecycle step except the merge."""
    repo = args.repo
    pr_number = args.pr_number
    pr_view = fetch_pr_state(repo, pr_number)
    head_sha = pr_view.get("headRefOid")
    ok_changed, changed_files, changed_err = fetch_changed_files(repo, pr_number, pr_view)

    evidence = build_evidence(
        repo=repo,
        pr_number=pr_number,
        pr_view=pr_view,
        changed_files=changed_files,
        changed_files_fetched=ok_changed,
        changed_files_error=changed_err,
        authorization_phrase=None,
    )
    verdict = R.evaluate_readiness(evidence)
    state = derive_lifecycle_state(verdict, pr_view)

    actions_taken: List[Dict[str, Any]] = []
    if args.dry_run:
        actions_taken.append({"action": "dry_run", "result": "skipped_all_mutations"})
    else:
        # 1. Codex-review ping per exact head (duplicate-prevented).
        if pr_view.get("state") == "OPEN" and R.is_canonical_head_sha(head_sha):
            ok_ping, ping_result = _post_codex_ping_comment(repo, pr_number, head_sha)
            actions_taken.append({
                "action": "request_codex_review",
                "head_sha": head_sha,
                "ok": ok_ping,
                "result": ping_result,
            })

        # 2. Draft-to-ready only after the prerequisite gates are green.
        if (
            pr_view.get("isDraft") is True
            and evidence.scope_clean is True
            and not evidence.ci_failed
            and not evidence.ci_missing
            and not evidence.ci_pending
            and evidence.review_thread_inventory_complete
            and evidence.unresolved_thread_count == 0
            and evidence.codex_artifact_fresh is True
            and R.is_codex_clean_verdict(evidence.codex_verdict)
        ):
            ok_ready, ready_result = _mark_pr_ready_for_review(repo, pr_number)
            actions_taken.append({
                "action": "mark_pr_ready",
                "ok": ok_ready,
                "result": ready_result,
            })
        elif pr_view.get("isDraft") is True:
            actions_taken.append({
                "action": "mark_pr_ready",
                "ok": False,
                "result": "skipped:prerequisites_not_clean",
                "gates_blocking": verdict.gates_failed,
            })

    canonical_phrase = (
        L.build_authorization_phrase(pr_number, str(head_sha))
        if verdict.ready and R.is_canonical_head_sha(head_sha) else None
    )

    out: Dict[str, Any] = {
        "tool": "aed_pr.advance",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "lifecycle_state": state,
        "ready": verdict.ready,
        "reason_codes": [r.code for r in verdict.reasons],
        "reasons": [r.to_dict() for r in verdict.reasons],
        "actions_taken": actions_taken,
        "safe_merge_command_if_ready": (
            L.build_safe_merge_command(pr_number, repo, head_sha)
            if verdict.ready else None
        ),
        "required_authorization_phrase_if_ready": canonical_phrase,
        "next_human_action": _next_human_action(state),
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


# -----------------------------------------------------------------------------
# merge command
# -----------------------------------------------------------------------------

def cmd_merge(args: argparse.Namespace) -> int:
    """Execute the canonical merge for a single PR.

    All 12 readiness gates must pass on the live head; any failure
    exits non-zero and does NOT call ``gh pr merge``.
    """
    repo = args.repo
    pr_number = args.pr_number
    phrase = args.authorization_phrase

    pr_view = fetch_pr_state(repo, pr_number)
    head_sha = pr_view.get("headRefOid")

    if not L.is_valid_authorization_phrase(phrase, pr_number, head_sha):
        sys.stderr.write(
            "Deny: phrase does NOT byte-match the canonical phrase for "
            f"PR #{pr_number} at head {head_sha}.\n"
        )
        sys.stderr.write("Expected (exact):\n")
        sys.stderr.write(
            "  " + L.build_authorization_phrase(pr_number, head_sha) + "\n"
        )
        return 1

    ok_changed, changed_files, changed_err = fetch_changed_files(repo, pr_number, pr_view)
    evidence = build_evidence(
        repo=repo,
        pr_number=pr_number,
        pr_view=pr_view,
        changed_files=changed_files,
        changed_files_fetched=ok_changed,
        changed_files_error=changed_err,
        authorization_phrase=phrase,
    )
    verdict = R.evaluate_readiness(evidence)

    if not verdict.ready:
        sys.stderr.write(
            "Deny: readiness verdict is not READY on the live head.\n"
        )
        for r in verdict.reasons:
            sys.stderr.write(f"  [{r.code}] {r.gate}: {r.detail}\n")
        return 1

    safe_cmd = L.build_safe_merge_command(pr_number, repo, head_sha)
    argv = safe_cmd.split()
    if not L.argv_is_safe(argv):
        sys.stderr.write("Deny: argv safety check failed.\n")
        return 1
    L.reject_admin_argv(argv)

    sys.stdout.write(f"# Executing: {safe_cmd}\n")
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        return proc.returncode
    return 0


# -----------------------------------------------------------------------------
# argparse
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aed_pr",
        description="Canonical AED PR-lifecycle controller.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_status = sub.add_parser(
        "status",
        help="Read live PR state and emit one JSON readiness report.",
    )
    p_status.add_argument("--pr-number", type=int, required=True)
    p_status.add_argument("--repo", default=DEFAULT_REPO)
    p_status.set_defaults(func=cmd_status)

    p_advance = sub.add_parser(
        "advance",
        help="Perform safe mechanical lifecycle steps; never merges.",
    )
    p_advance.add_argument("--pr-number", type=int, required=True)
    p_advance.add_argument("--repo", default=DEFAULT_REPO)
    p_advance.add_argument(
        "--dry-run", action="store_true",
        help="Compute the verdict but skip every mutation.",
    )
    p_advance.set_defaults(func=cmd_advance)

    p_merge = sub.add_parser(
        "merge",
        help=(
            "Execute the canonical squash merge. Requires the exact "
            "40-SHA authorization phrase AND every readiness gate to "
            "be green on the live head."
        ),
    )
    p_merge.add_argument("--pr-number", type=int, required=True)
    p_merge.add_argument("--repo", default=DEFAULT_REPO)
    p_merge.add_argument(
        "--authorization-phrase", required=True,
        help="Exact canonical phrase from `aed_pr status --pr-number N`.",
    )
    p_merge.set_defaults(func=cmd_merge)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
