#!/usr/bin/env python3
"""Production integration facade for the shared hardening modules.

This module is the THIN production adapter that wires the five
shared policies (PHASE 3 R-1..R-5) into the working autonomous
system:

  * R-1: Complete evidence pagination (``_shared_pagination``).
  * R-2: One shared Codex classifier
    (``_shared_codex_classifier``).
  * R-3: Hard-coded non-human review policy
    (``_shared_non_human_policy``).
  * R-4: Cohesive repair batching (``_shared_batching``).
  * R-5: Impact-based test selection (``_shared_test_selection``).

The facade MUST stay thin. It may:

  * normalize inputs;
  * preserve existing reason codes;
  * call the shared policies;
  * provide dependency injection;
  * record machine-readable invocation evidence.

It MUST NOT:

  * become a second source of policy truth;
  * duplicate the shared classifier, batching, pagination,
    test-selection, or non-human policy algorithms;
  * silently fall back to legacy behavior;
  * convert incomplete evidence into passing evidence;
  * grow a parallel readiness controller.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

# Re-exports for downstream consumers.
from scripts.local._shared_codex_classifier import (
    is_codex_login,
    is_codex_review_summary,
    is_codex_finding_body,
    body_has_finding_badge,
    is_codex_clean_pass_comment,
    is_codex_finding_comment,
    extract_review_commit_oid,
    classify_codex_response,
    CODEX_LOGINS,
    CODEX_CLEAN_PASS_PHRASES,
    CODEX_CLEAN_PASS_EXTRA_FRAGMENTS,
    CODEX_REVIEW_SUMMARY_PREFIX,
    CODEX_FINDING_BADGE_PREFIX,
)

from scripts.local._shared_pagination import (
    paginate_review_threads,
    paginate_issue_comments,
    paginate_formal_reviews,
    paginate_review_inline_comments,
    paginate_workflow_runs,
    paginate_jobs_for_run,
    paginate_changed_files,
    paginate_graphql_connection,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SAFETY_CAP,
)

from scripts.local._shared_non_human_policy import (
    classify_review_thread as _classify_review_thread,
    validate_thread_for_resolution as _validate_thread_for_resolution,
    parse_thread_inventory as _parse_thread_inventory,
    translate_class_to_legacy as _translate_class_to_legacy,
    ParticipantInventory,
    RepairEvidence,
    CodexCleanEvidence,
    LiveHeadMatch,
    EligibilityVerdict,
    ReviewerClass,
    LEGACY_REASONS,
)

from scripts.local._shared_batching import (
    FindingRecord,
    Severity,
    RepairBatch,
    batch_findings,
)

from scripts.local._shared_test_selection import (
    select_tests as _select_tests,
    TestPlan,
    ValidationTier,
    Component,
    classify_path,
    classify_paths,
)


# ---------------------------------------------------------------------------
# Invocation logging for PHASE 7 source-contract enforcement.
# ---------------------------------------------------------------------------

_INVOCATION_LOG: List[Dict[str, Any]] = []


def record_invocation(
    policy: str,
    *,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a shared-policy invocation for production tracing.

    Behavioral tests monkeypatch this function to verify
    that production call paths actually invoked the shared
    policies.
    """
    _INVOCATION_LOG.append({
        "policy": policy,
        "ts": time.time(),
        "inputs": inputs or {},
        "outputs": outputs or {},
    })


def get_invocations() -> List[Dict[str, Any]]:
    """Return the recorded invocations (used by tests)."""
    return list(_INVOCATION_LOG)


def clear_invocations() -> None:
    """Clear the invocation log (used by tests)."""
    _INVOCATION_LOG.clear()


# ---------------------------------------------------------------------------
# PHASE 4: Non-human resolution facade.
# Thin adapter: normalize inputs, call shared policy,
# translate reasons, record invocation. No business logic.
# ---------------------------------------------------------------------------


def classify_review_thread_eligibility(
    *,
    thread: Dict[str, Any],
    head_sha: Optional[str],
    codex_clean_passed: Optional[bool],
    codex_reviewed_sha: Optional[str],
    repo: Optional[str],
    inventory_complete: bool = True,
    ancestry_ok: bool = True,
    repair_present: bool = True,
    no_newer_finding: bool = True,
    live_head_match: bool = True,
    ancestry_runner: Optional[Any] = None,
    verify_ancestry: bool = True,
) -> EligibilityVerdict:
    """Production wrapper around the shared non-human policy.

    PHASE 3 R-3 contract. Delegates the eligibility decision
    to :func:`_validate_thread_for_resolution` in the shared
    module. Preserves every legacy reason code the controller
    previously produced. No fallback to legacy behavior; an
    import error from the shared module fails closed.
    """
    if not inventory_complete:
        verdict = EligibilityVerdict(
            eligible=False,
            reasons=["unknown_actor_in_thread"],
            reviewer_classes=[],
        )
    else:
        verdict = _validate_thread_for_resolution(
            thread=thread,
            head_sha=head_sha,
            codex_clean_passed=codex_clean_passed,
            codex_reviewed_sha=codex_reviewed_sha,
            repo=repo,
            ancestry_runner=ancestry_runner,
            verify_ancestry=verify_ancestry,
            no_newer_finding=no_newer_finding,
            live_head_match=live_head_match,
        )
    record_invocation(
        "non_human_policy.validate_thread_for_resolution",
        inputs={
            "thread_id": str(
                thread.get("id") or thread.get("thread_id") or ""
            ),
            "head_sha": head_sha,
            "inventory_complete": inventory_complete,
            "codex_clean_passed": codex_clean_passed,
            "codex_reviewed_sha": codex_reviewed_sha,
            "verify_ancestry": verify_ancestry,
            "no_newer_finding": no_newer_finding,
            "live_head_match": live_head_match,
        },
        outputs={
            "eligible": verdict.eligible,
            "reasons": verdict.reasons,
            "reviewer_classes": [c.value for c in verdict.reviewer_classes],
        },
    )
    return verdict


# ---------------------------------------------------------------------------
# PHASE 2: Pagination facade.
# ---------------------------------------------------------------------------


def paginate_review_threads_with_invocation(
    owner: str,
    name: str,
    pr_number: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
) -> Dict[str, Any]:
    """Production wrapper around the shared pagination helper."""
    res = paginate_review_threads(
        owner=owner,
        name=name,
        pr_number=pr_number,
        page_size=page_size,
        safety_cap=safety_cap,
    )
    record_invocation(
        "pagination.paginate_review_threads",
        inputs={
            "owner": owner,
            "name": name,
            "pr_number": pr_number,
        },
        outputs={
            "complete": res.get("complete"),
            "pages": res.get("pages"),
            "node_count": len(res.get("nodes", [])),
        },
    )
    return res


# ---------------------------------------------------------------------------
# PHASE 3: Shared Codex classifier facade.
# ---------------------------------------------------------------------------


def classify_codex_response_with_invocation(
    *,
    kind: str,
    candidate: Dict[str, Any],
    head: str,
    expected_head_sha: Optional[str],
    ping_dt: Optional[Any] = None,
) -> Optional[str]:
    """Production wrapper around the shared Codex classifier."""
    verdict = classify_codex_response(
        kind=kind,
        candidate=candidate,
        head=head,
        expected_head_sha=expected_head_sha,
        ping_dt=ping_dt,
    )
    record_invocation(
        "codex_classifier.classify_codex_response",
        inputs={
            "kind": kind,
            "head": head,
            "expected_head_sha": expected_head_sha,
        },
        outputs={
            "verdict": verdict,
        },
    )
    return verdict


# ---------------------------------------------------------------------------
# PHASE 5: Cohesive repair batching facade.
# ---------------------------------------------------------------------------


def batch_findings_with_invocation(
    findings: List[FindingRecord],
) -> List[RepairBatch]:
    """Production wrapper around the shared batching policy."""
    batches = batch_findings(findings)
    record_invocation(
        "batching.batch_findings",
        inputs={
            "finding_count": len(findings),
        },
        outputs={
            "batch_count": len(batches),
            "batch_ids": [b.batch_id for b in batches],
        },
    )
    return batches


# ---------------------------------------------------------------------------
# PHASE 6: Impact-based test execution facade.
# ---------------------------------------------------------------------------


def select_tests_with_invocation(
    *,
    changed_paths: Sequence[str],
    tier: ValidationTier,
    final_candidate: bool = False,
) -> TestPlan:
    """Production wrapper around the shared test selector."""
    plan = _select_tests(
        changed_paths=changed_paths,
        tier=tier,
        final_candidate=final_candidate,
    )
    record_invocation(
        "test_selection.select_tests",
        inputs={
            "changed_paths": list(changed_paths),
            "tier": tier.value,
            "final_candidate": final_candidate,
        },
        outputs=plan.to_machine_readable(),
    )
    return plan


def run_selected_tests(
    *,
    plan: TestPlan,
    pytest_args: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the tests selected by the impact-based selector.

    Falls back to the full repository suite for shared,
    unknown, or ambiguous paths and when in final-candidate
    mode.
    """
    pytest_args = pytest_args or []
    cwd = cwd or os.getcwd()
    if plan.requires_full_validation:
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        cmd.extend(pytest_args)
        cmd.append("--basetemp")
        cmd.append(tempfile.mkdtemp())
        selected = ["FULL_REPOSITORY_SUITE"]
    else:
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        cmd.extend(pytest_args)
        if plan.selected_tests:
            cmd.extend(plan.selected_tests)
        else:
            cmd.extend(pytest_args or ["."])
        selected = plan.selected_tests
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=600,
        )
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
        error = None
    except Exception as exc:
        returncode = -1
        stdout = ""
        stderr = ""
        error = repr(exc)
    duration = time.time() - start
    record_invocation(
        "test_selection.run_selected_tests",
        inputs={
            "selected": selected,
            "tier": plan.tier.value,
            "requires_full_validation": plan.requires_full_validation,
        },
        outputs={
            "returncode": returncode,
            "duration_seconds": round(duration, 3),
            "selected_count": len(selected),
        },
    )
    result = {
        "tool": "aed_test_runner.run_selected_tests",
        "selected": selected,
        "tier": plan.tier.value,
        "requires_full_validation": plan.requires_full_validation,
        "command": cmd,
        "returncode": returncode,
        "duration_seconds": round(duration, 3),
        "selection_reason": plan.selection_reason,
        "stdout_tail": stdout[-2000:] if stdout else "",
        "stderr_tail": stderr[-2000:] if stderr else "",
        "error": error,
    }
    if log_path:
        try:
            # Round-412 (PHASE 6): guard against bare
            # filenames (where ``os.path.dirname`` is the
            # empty string and ``os.makedirs("")`` raises).
            _dir = os.path.dirname(log_path)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            with open(log_path, "w") as f:
                json.dump(result, f, indent=2)
        except Exception:
            pass
    return result
