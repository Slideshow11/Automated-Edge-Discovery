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
import re
import subprocess
import time
import sys
from typing import Any, Dict, List, Optional, Tuple

# Maps ``.github/workflows/<file>`` → the workflow's human-readable
# display name returned by ``gh run list --json workflowName``.
# ``.github/workflows/ci.yml`` is named ``CI`` in this repository;
# other workflows are mapped here so the strict match succeeds
# without loose substring matching.
EXPECTED_WORKFLOW_NAME: Dict[str, str] = {
    "ci.yml": "CI",
    "review-comment-gate-recheck.yml": "review-comment-gate-recheck",
}

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

# The canonical AED required-check names consumed by ``status``,
# ``advance``, and ``merge``. These names are the actual GitHub Actions
# **check** names returned by ``gh pr checks --json name,state,workflow``,
# not workflow names. The single source of truth for required checks
# lives here; the controller never hardcodes job IDs or workflow names
# separately. (Round-2 Codex finding: do not treat CI job IDs as
# workflowName values.)
REQUIRED_CHECK_NAMES = (
    "test (3.11)",
    "validator",
    "governance-validators",
    "pr-gate-live-smoke",
    "review-comment-gate",
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

def fetch_pr_state(
    repo: str, pr_number: int, *, runner: Optional[Any] = None,
) -> Dict[str, Any]:
    """Fetch live PR state via gh; re-fetched at the top of every command.

    The returned dict includes the PR's ``headRefName`` (the branch
    name), ``headRefOid`` (the commit SHA), and ``headRepository``
    (the repository the head branch lives in). The
    ``headRefName`` is required by ``cmd_gate_recheck`` so the
    ``gh workflow run`` dispatch can be bound to the exact PR
    branch instead of the repository's default branch.

    ``runner`` (default ``subprocess.run``) is an injectable test
    seam so unit tests can avoid the live network. ``_run_json``
    is the only consumer; passing ``runner`` replaces
    ``subprocess.run`` for this single call.
    """
    return _run_json([
        "gh", "pr", "view", str(pr_number),
        "--repo", repo,
        "--json",
        "number,title,state,isDraft,mergeable,headRefOid,headRefName,"
        "baseRefOid,baseRefName,additions,deletions,changedFiles,url,"
        "files,headRepository",
    ], runner=runner)


def _run_json(
    cmd: List[str], timeout: int = 30, *, runner: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run a gh command, parse JSON, return dict. Raise on failure.

    ``runner`` (default ``subprocess.run``) is an injectable test
    seam so unit tests can avoid the live network.
    """
    if runner is None:
        runner = subprocess.run
    proc = runner(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed (rc={proc.returncode}): {' '.join(cmd)}\n"
            f"stderr: {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def _extract_valid_paths(files: Any) -> List[str]:
    """Return the list of nonempty path strings from a ``files`` payload.

    Round-8 follow-up (Codex comment 3609202696 on ``1e9867e``):
    helper used to detect empty / malformed inventories; an empty
    result signals fetch failure, not a clean inventory.

    Round-11 follow-up (Codex comment 3610828220 on ``83e3f24``):
    also reject duplicate filenames as ambiguous evidence; a
    PR's REST files endpoint can return duplicate paths if
    a file is touched more than once.
    """
    if not isinstance(files, list):
        return []
    out: List[str] = []
    seen = set()
    for f in files:
        if not isinstance(f, dict):
            continue
        path = f.get("path")
        if isinstance(path, str) and path:
            if path in seen:
                # Duplicate paths in the source are ambiguous
                # evidence; treat as malformed so the
                # controller fails closed rather than masking
                # out-of-scope changes.
                raise ValueError(
                    f"duplicate changed-file path {path!r} in "
                    f"inventory; refusing to treat the source as "
                    f"authoritative scope evidence"
                )
            seen.add(path)
            out.append(path)
    return out


def _extract_paginated_filenames(
    pages: Any,
) -> List[str]:
    """Extract ``filename`` (REST) paths from a slurped paginated
    payload.

    Round-11 helper: ``gh api /repos/<owner>/<repo>/pulls/<n>/files``
    returns the REST ``filename`` field (not ``path``). The
    ``--paginate --slurp`` flag wraps each page in an outer
    list, so the slurped payload is ``[page1, page2, ...]`` where
    each ``page`` is a list of file records.

    Returns the deduplicated list of nonempty filenames. Raises
    ``ValueError`` on:

    - malformed page (not a list of file records);
    - file record not a dict;
    - missing or empty ``filename``;
    - duplicate filenames in the paginated inventory.
    """
    if not isinstance(pages, list):
        raise ValueError(
            f"paginated payload is not a list: {type(pages).__name__}"
        )
    out: List[str] = []
    seen = set()
    for page_idx, page in enumerate(pages):
        if not isinstance(page, list):
            raise ValueError(
                f"page {page_idx} is not a list: {type(page).__name__}"
            )
        for rec_idx, rec in enumerate(page):
            if not isinstance(rec, dict):
                raise ValueError(
                    f"page {page_idx} record {rec_idx} is not a "
                    f"dict: {type(rec).__name__}"
                )
            fn = rec.get("filename")
            if not isinstance(fn, str) or not fn:
                raise ValueError(
                    f"page {page_idx} record {rec_idx} has no "
                    f"nonempty ``filename``"
                )
            if fn in seen:
                raise ValueError(
                    f"duplicate paginated filename {fn!r}; "
                    f"refusing to treat the inventory as authoritative"
                )
            seen.add(fn)
            out.append(fn)
    return out


def fetch_changed_files(
    repo: str, pr_number: int, pr_view: Optional[Dict[str, Any]] = None,
    *,
    runner: Optional[Any] = None,
) -> Tuple[bool, List[str], str]:
    """Fetch the complete changed file paths for the PR.

    Returns (ok, paths, error). When ok=False the controller
    must treat the evidence as missing; it must NEVER treat an
    empty list as clean=True (a PR with zero changed files is
    impossible, and an empty result here is a fetch failure).

    Round-8 follow-up (Codex comment 3609202696): the dedicated
    ``gh pr view --json files`` call may succeed but return an
    empty list, a list of entries with no valid ``path``
    strings, or a malformed payload from which no valid paths
    can be extracted. The current implementation rejects all
    of these and falls through to the paginated REST endpoint.

    Round-11 follow-up (Codex comment 3610828220): the
    ``gh pr view --json files`` inventory is capped at 100
    entries, so a PR with more than 100 changed files would
    produce a partial inventory. The controller now uses the
    paginated ``/repos/<owner>/<repo>/pulls/<n>/files``
    REST endpoint via ``gh api --paginate --slurp``. The
    function also requires:

    - ``changedFiles`` to be an integer greater than zero;
    - the unique paginated filenames to exactly equal
      ``changedFiles`` in count;
    - duplicate filenames to be rejected as ambiguous
      evidence;
    - malformed pages or records to fail closed;
    - the capped ``gh pr view --json files`` field NOT to be
      used as authoritative fallback.

    Distinct failure reasons:

    - ``changed_file_inventory_fetch_failed`` — the paginated
      call could not be completed;
    - ``malformed_changed_file_inventory`` — pages or records
      were malformed, or a filename was missing/empty;
    - ``empty_changed_file_inventory`` — zero unique
      filenames after a successful fetch;
    - ``duplicate_changed_file_paths`` — same filename
      appeared more than once across the paginated inventory;
    - ``missing_changed_file_count`` — the PR's
      ``changedFiles`` field is missing, zero, or not an
      integer;
    - ``changed_file_count_mismatch`` — the number of unique
      paginated filenames does not match ``changedFiles``.
    """
    # 1. Pull the PR's reported ``changedFiles`` count from
    # ``pr_view`` so we can verify the paginated count later.
    expected_count: Optional[int] = None
    if isinstance(pr_view, dict):
        raw_count = pr_view.get("changedFiles")
        if isinstance(raw_count, int) and raw_count > 0:
            expected_count = raw_count
        elif "changedFiles" in pr_view:
            # The field was present but malformed. Refuse
            # immediately so a partial inventory cannot be
            # accepted under a missing count.
            return (
                False, [],
                "missing_changed_file_count",
            )
    else:
        return (
            False, [],
            "missing_changed_file_count",
        )

    # 3. Paginated REST endpoint with explicit
    # ``per_page=100`` and ``--paginate --slurp``.
    cmd = [
        "gh", "api",
        f"repos/{repo}/pulls/{pr_number}/files?per_page=100",
        "--paginate", "--slurp",
    ]
    if runner is None:
        ok, payload, err = _run_json_or_none(cmd, timeout=120)
    else:
        try:
            proc = runner(
                cmd, capture_output=True, text=True, timeout=120
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return (
                False, [],
                f"changed_file_inventory_fetch_failed: {exc}",
            )
        if proc.returncode != 0:
            return (
                False, [],
                f"changed_file_inventory_fetch_failed: "
                f"{(proc.stderr or '').strip()[:300]}",
            )
        try:
            payload = json.loads(proc.stdout or "")
            ok = True
            err = ""
        except json.JSONDecodeError as exc:
            return (
                False, [],
                f"changed_file_inventory_fetch_failed: {exc}",
            )
    if not ok or not isinstance(payload, list):
        return (
            False, [],
            f"changed_file_inventory_fetch_failed: {err or ''}",
        )

    # 4. Extract filenames from the slurped pages.
    try:
        paths = _extract_paginated_filenames(payload)
    except ValueError as exc:
        return False, [], f"malformed_changed_file_inventory: {exc}"

    # 5. Empty inventory after a successful fetch is impossible.
    if not paths:
        return False, [], "empty_changed_file_inventory"

    # 6. The unique paginated count must match the PR's
    # reported ``changedFiles`` count. Any mismatch is a
    # partial inventory that the controller must not accept
    # as authoritative scope evidence.
    if expected_count is None:
        return False, [], "missing_changed_file_count"
    if len(paths) != expected_count:
        return (
            False, [],
            f"changed_file_count_mismatch: paginated={len(paths)} "
            f"changedFiles={expected_count}",
        )

    return True, paths, ""


def _find_exact_head_pull_request_run_id(
    repo: str,
    head_sha: str,
    *,
    runner: Optional[Any] = None,
    expected_head_branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Return structured evidence for the exact-head
    ``pull_request`` CI run.

    Round-13 helper. The function is called when ``gh pr
    checks`` reports duplicate required-check names that
    need authoritative disambiguation. It MUST return one
    of three success/failure structures; callers MUST NOT
    have to inspect undocumented list ordering to choose a
    run.

    Returns a dict with one of three shapes:

    - ``{"ok": True, "databaseId": <int>, "url": <str>,
       "headBranch": <str>, "workflowName": <str>,
       "headSha": <str>}`` on a unique exact-head match;
    - ``{"ok": False, "reason": "exact_head_pr_run_missing"}``
       when no matching run exists;
    - ``{"ok": False, "reason": "multiple_exact_head_pr_runs",
       "candidate_count": <int>}`` when more than one run
       matches and the controller cannot pick a winner;
    - ``{"ok": False, "reason": "malformed_exact_head_pr_run"}``
       when a matching run record is malformed (missing
       branch, missing URL, missing or non-integer
       ``databaseId``, etc.).

    Required binding fields:

    - ``workflowName`` exactly ``CI``;
    - ``event`` exactly ``pull_request``;
    - ``headSha`` byte-exactly equals the requested
      ``head_sha``;
    - ``headBranch`` byte-exactly equals
      ``expected_head_branch`` when supplied;
    - integer ``databaseId``;
    - nonempty ``url``;
    - record is a ``dict`` (other types fail closed).

    The function does NOT tolerate more than one candidate
    even when both have all binding fields. Two matching
    pull_request runs on the same head is treated as
    genuinely ambiguous evidence.
    """
    cmd = [
        "gh", "run", "list",
        "--repo", repo,
        "--workflow", "ci.yml",
        "--event", "pull_request",
        "--commit", head_sha,
        "--limit", "10",
        "--json", "databaseId,event,headBranch,headSha,"
        "workflowName,url",
    ]
    if runner is None:
        ok, payload, _err = _run_json_or_none(cmd, timeout=45)
        if not ok or not isinstance(payload, list):
            return {"ok": False, "reason": "exact_head_pr_run_missing"}
    else:
        try:
            proc = runner(cmd, capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "reason": "exact_head_pr_run_missing"}
        if proc.returncode != 0:
            return {"ok": False, "reason": "exact_head_pr_run_missing"}
        try:
            payload = json.loads(proc.stdout or "")
        except json.JSONDecodeError:
            return {"ok": False, "reason": "exact_head_pr_run_missing"}
        if not isinstance(payload, list):
            return {"ok": False, "reason": "exact_head_pr_run_missing"}
    candidates: List[Dict[str, Any]] = []
    malformed = False
    for run in payload:
        if not isinstance(run, dict):
            malformed = True
            break
        if (run.get("workflowName") or "") != "CI":
            continue
        if (run.get("event") or "") != "pull_request":
            continue
        if run.get("headSha") != head_sha:
            continue
        if expected_head_branch is not None:
            if run.get("headBranch") != expected_head_branch:
                continue
        if not isinstance(run.get("databaseId"), int):
            malformed = True
            break
        if not isinstance(run.get("url"), str) or not run.get("url"):
            malformed = True
            break
        if not isinstance(run.get("headBranch"), str) or not run.get("headBranch"):
            malformed = True
            break
        candidates.append(run)
    if malformed:
        return {"ok": False, "reason": "malformed_exact_head_pr_run"}
    if len(candidates) == 0:
        return {"ok": False, "reason": "exact_head_pr_run_missing"}
    if len(candidates) > 1:
        return {
            "ok": False,
            "reason": "multiple_exact_head_pr_runs",
            "candidate_count": len(candidates),
        }
    picked = candidates[0]
    return {
        "ok": True,
        "databaseId": picked["databaseId"],
        "url": picked.get("url"),
        "headBranch": picked.get("headBranch"),
        "workflowName": picked.get("workflowName"),
        "headSha": picked.get("headSha"),
    }


def _run_jobs_for_run(
    repo: str,
    run_id: int,
    *,
    runner: Optional[Any] = None,
    required_job_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return structured job evidence for a workflow run.

    Round-13 helper. Returns a dict with one of three
    shapes:

    - ``{"ok": True, "jobs": {<name>: <conclusion>},
       "job_ids": {<name>: <int>}}`` on a clean read;
    - ``{"ok": False, "reason": "duplicate_authoritative_job_names",
       "duplicates": [<name>...]}`` when more than one job
       in the run shares the same ``name``;
    - ``{"ok": False, "reason": "missing_authoritative_required_job",
       "missing": [<name>...]}`` when ``required_job_names``
       is supplied and any name is absent;
    - ``{"ok": False, "reason": "malformed_authoritative_job"}``
       when a job record is missing ``name``, missing a
       valid integer ``databaseId``, or otherwise malformed.

    Conclusions are mapped to the ``gh pr checks`` vocabulary:

    - ``status="completed"`` + ``conclusion="success"``
      → ``SUCCESS``;
    - ``status="completed"`` + any other ``conclusion``
      → mapped to the conclusion
      (``FAILURE``/``CANCELLED``/``SKIPPED``/``NEUTRAL``
      /``STALE``/``ERROR``);
    - any other ``status`` (queued, in_progress, pending,
      waiting, requested, expected) → ``PENDING``.

    Required jobs (when ``required_job_names`` is supplied)
    must appear exactly once. Duplicate required job
    names fail closed; missing required jobs fail closed;
    malformed required-job records fail closed.
    """
    if runner is None:
        jobs, _err = _list_run_jobs(repo, run_id)
    else:
        jobs, _err = _list_run_jobs(
            repo, run_id, list_runner=runner,
        )
    if not jobs:
        if required_job_names:
            return {
                "ok": False,
                "reason": "missing_authoritative_required_job",
                "missing": list(required_job_names),
            }
        return {"ok": False, "reason": "malformed_authoritative_job"}
    by_name: Dict[str, str] = {}
    job_ids: Dict[str, int] = {}
    seen_names: List[str] = []
    duplicates: List[str] = []
    for j in jobs:
        if not isinstance(j, dict):
            return {
                "ok": False,
                "reason": "malformed_authoritative_job",
            }
        name = j.get("name")
        if not isinstance(name, str) or not name:
            return {
                "ok": False,
                "reason": "malformed_authoritative_job",
            }
        dbid = j.get("databaseId")
        if not isinstance(dbid, int):
            return {
                "ok": False,
                "reason": "malformed_authoritative_job",
            }
        if name in by_name:
            duplicates.append(name)
            continue
        status = (j.get("status") or "").upper()
        conclusion = (j.get("conclusion") or "").upper()
        if status != "COMPLETED":
            mapped = "PENDING"
        elif conclusion == "SUCCESS":
            mapped = "SUCCESS"
        else:
            mapped = conclusion or "FAILURE"
        by_name[name] = mapped
        job_ids[name] = dbid
        seen_names.append(name)
    if duplicates:
        return {
            "ok": False,
            "reason": "duplicate_authoritative_job_names",
            "duplicates": sorted(set(duplicates)),
        }
    if required_job_names:
        missing = [n for n in required_job_names if n not in by_name]
        if missing:
            return {
                "ok": False,
                "reason": "missing_authoritative_required_job",
                "missing": missing,
            }
    return {"ok": True, "jobs": by_name, "job_ids": job_ids}


def _fetch_gh_pr_checks_payload(
    repo: str,
    pr_number: int,
    *,
    runner: Optional[Any] = None,
) -> Tuple[bool, Any, str]:
    """Wrap ``gh pr checks --json ...`` invocation for tests.

    ``gh pr checks`` uses **status-oriented** exit codes (per the
    GitHub CLI manual at https://cli.github.com/manual/gh_pr_checks):

    * ``0`` — all checks passed
    * ``1`` — one or more checks failed
    * ``8`` — one or more checks are pending

    All three codes may still produce valid JSON through
    ``--json``. The check states in that JSON are the
    authoritative per-check signal; the exit code is a
    *summary* of those states, not a transport signal.

    Acceptance contract:

    * ``returncode in (0, 1, 8)`` AND non-empty stdout AND valid
      JSON list  → ``ok=True``, parsed payload, empty error.
    * Empty stdout, malformed JSON, or any other return code is
      rejected with a bounded diagnostic so the operator can
      distinguish a transport / cancellation / auth failure from
      a status exit.

    The actual check states (``SUCCESS``, ``FAILURE``,
    ``PENDING``, ...) in the payload remain authoritative.
    Return codes ``1`` and ``8`` are **not** converted into a
    successful check conclusion: ``fetch_ci_conclusions`` still
    classifies each record on its own state.
    """
    cmd = [
        "gh", "pr", "checks", str(pr_number),
        "--repo", repo,
        "--json", "name,state,workflow",
    ]
    # Codes for which ``gh pr checks`` is documented to emit
    # valid JSON. ``fetch_ci_conclusions`` decides what each
    # individual check state means; this helper only decides
    # whether the JSON query itself succeeded.
    _GH_PR_CHECKS_ACCEPTED_RETURN_CODES = frozenset({0, 1, 8})

    def _invoke_and_parse(invoker):
        try:
            proc = invoker(cmd, capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, None, f"gh invocation failed: {exc}"
        # Process-launch or transport failure on the default
        # ``subprocess.run`` path can surface as ``returncode is
        # None`` with ``proc.stdout``/``proc.stderr`` empty or
        # unset. Treat that as a launch failure too.
        rc = getattr(proc, "returncode", None)
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        if rc is None:
            bounded = (stderr or "").strip()[:300] or "gh returned no exit code"
            return False, None, f"gh invocation incomplete: {bounded}"
        if not stdout.strip():
            return False, None, "gh returned empty stdout"
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return False, None, f"invalid JSON: {exc}"
        if rc not in _GH_PR_CHECKS_ACCEPTED_RETURN_CODES:
            bounded = (stderr or "").strip()[:300]
            return (
                False, None,
                f"unexpected gh exit code {rc}: {bounded}" if bounded
                else f"unexpected gh exit code {rc}",
            )
        return True, payload, ""

    if runner is None:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=45
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, None, f"gh invocation failed: {exc}"
        return _invoke_and_parse(lambda *a, **kw: proc)

    return _invoke_and_parse(runner)


def fetch_ci_conclusions(
    repo: str, pr_number: int, required_check_names: List[str],
    *,
    runner: Optional[Any] = None,
    head_sha: Optional[str] = None,
    head_branch: Optional[str] = None,
) -> Tuple[
    bool, Dict[str, str], List[str], List[str], List[str],
    List[str], str,
]:
    """Fetch CI check conclusions for the current PR head.

    Uses ``gh pr checks --json name,state,workflow`` for
    diagnostics only. The authoritative source of
    required-check evidence is the unique exact-head
    ``pull_request`` CI run's job inventory; ``gh pr checks``
    records are NEVER trusted directly for required checks
    when a canonical ``head_sha`` AND ``head_branch`` are
    supplied.

    Round-2 Codex fix: the prior implementation indexed
    ``gh run list --json workflowName`` by workflow name,
    which mapped a single workflow named ``CI`` to four
    required job names and therefore always reported every
    required check as missing. The current implementation
    uses the per-job name from ``gh run view --json jobs``.

    Round-6 follow-up (Codex comment ``3609075636`` on
    ``c229be82``): the round-6 logic refused to silently
    collapse duplicate ``gh pr checks`` records.

    Round-12 follow-up (Codex comment ``3610952756`` on
    ``48e1a33``): with the round-11
    ``if: github.event_name == 'pull_request'`` guard on
    ``review-comment-gate``, the gate is skipped on push
    events; ``gh pr checks`` reports that skipped job as
    ``SUCCESS``. The round-12 code began preferring the
    authoritative ``pull_request`` run's job evidence when
    a duplicate existed.

    Round-14 follow-up (Codex comment ``PRRT_kwDOSHFpYM6SGhXX``
    on ``2acdb1c``): the round-12 lookup was triggered ONLY
    when ``gh pr checks`` already contained a duplicate. The
    current implementation removes that trigger condition:

    1. When ``head_sha`` is canonical AND ``head_branch``
       is supplied, the controller always identifies the
       exact-head ``pull_request`` CI run via
       ``_find_exact_head_pull_request_run_id``.
    2. The authoritative run's job inventory supplies every
       required-check evidence. Push-triggered records in
       ``gh pr checks`` are NEVER used as authoritative.
    3. Failure modes:
       - missing exact-head ``pull_request`` run → all
         required checks reported as ``missing`` (the
         authoritative source is not available);
       - multiple matching exact-head ``pull_request``
         runs → every required check reported as
         ``missing`` and the run ID is recorded in the
         structured diagnostic;
       - malformed authoritative run / missing /
         duplicate / malformed authoritative jobs → the
         affected required check is reported as
         ``failed`` with a structured reason.
    4. When ``head_sha`` is not supplied, the previous
       generic duplicate-required-check fail-closed
       behavior is preserved.

    Returns ``(ok, conclusions, missing, pending, failed,
    duplicated_required, error)``:

    * ``ok=False`` means the ``gh pr checks`` query could
      not be completed; in that case every required check
      is reported as missing so the gate fails closed.
    * ``conclusions`` maps each required check name to its
      state string (``SUCCESS``, ``FAILURE``, ``PENDING``,
      ...).
    * ``missing`` lists required check names that did not
      appear in the authoritative source at all.
    * ``pending`` lists required check names with an
      in-flight state (``PENDING``, ``QUEUED``,
      ``IN_PROGRESS``, ``WAITING``, ``REQUESTED``,
      ``EXPECTED``).
    * ``failed`` lists required check names with a terminal
      non-success state (``FAILURE``, ``CANCELLED``,
      ``SKIPPED``, ``NEUTRAL``, ``STALE``, ``ERROR``, or any
      unrecognized state).
    * ``duplicated_required`` lists required check names
      that appeared more than once in ``gh pr checks`` when
      the controller falls back to the legacy path; on the
      authoritative path this is empty because the
      authoritative run exposes each job exactly once.
    * ``error`` is non-empty only when ``ok=False``.
    """
    ok, payload, err = _fetch_gh_pr_checks_payload(
        repo, pr_number, runner=runner,
    )
    if not ok or not isinstance(payload, list):
        missing = list(required_check_names)
        return False, {}, missing, [], missing, [], err

    # Collect every record by name; ``gh pr checks`` is
    # used for diagnostics only.
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for check in payload:
        if not isinstance(check, dict):
            continue
        name = check.get("name")
        if not isinstance(name, str) or not name:
            continue
        by_name.setdefault(name, []).append(check)

    # Unrelated (non-required) duplicates are reported as a
    # diagnostic only. They MUST NOT replace evidence for any
    # required check.
    unrelated_duplicated = sorted(
        n for n, records in by_name.items() if len(records) > 1
        and n not in required_check_names
    )

    authoritative_jobs: Dict[str, str] = {}
    authoritative_status: str = ""
    authoritative_run: Optional[Dict[str, Any]] = None

    use_authoritative = bool(
        head_sha
        and head_branch
        and R.is_canonical_head_sha(head_sha)
    )

    if use_authoritative:
        # ``head_sha`` and ``head_branch`` are guaranteed
        # truthy / canonical at this point because
        # ``use_authoritative`` is True.
        assert head_sha is not None and head_branch is not None
        authoritative_run = _find_exact_head_pull_request_run_id(
            repo, head_sha, runner=runner,
            expected_head_branch=head_branch,
        )
        if authoritative_run.get("ok") is True:
            authoritative_jobs_payload = _run_jobs_for_run(
                repo, authoritative_run["databaseId"],
                runner=runner,
                required_job_names=list(required_check_names),
            )
            if authoritative_jobs_payload.get("ok") is True:
                authoritative_jobs = authoritative_jobs_payload["jobs"]
                authoritative_status = "ok"
            else:
                # Authoritative run was identified but its
                # job inventory is malformed / missing /
                # duplicated. Fail closed by leaving
                # ``authoritative_jobs`` empty AND reporting
                # the structured reason.
                authoritative_jobs = {}
                authoritative_status = authoritative_jobs_payload.get(
                    "reason", "malformed_authoritative_job"
                )
        else:
            authoritative_status = authoritative_run.get(
                "reason", "exact_head_pr_run_missing"
            )

    if use_authoritative:
        # Build the entire required-check inventory from the
        # authoritative source. ``gh pr checks`` records are
        # diagnostic only.
        conclusions: Dict[str, str] = {}
        missing: List[str] = []
        pending: List[str] = []
        failed: List[str] = []
        if authoritative_status != "ok":
            # No authoritative inventory available; every
            # required check is reported as missing AND
            # failed so the gate fails closed without
            # trusting any push-run duplicate in
            # ``gh pr checks``.
            missing = list(required_check_names)
            failed = list(required_check_names)
            duplicated_required: List[str] = []
            return (
                True, conclusions, missing, pending, failed,
                duplicated_required, "",
            )
        for name in required_check_names:
            mapped = authoritative_jobs.get(name)
            if mapped is None:
                missing.append(name)
                failed.append(name)
                continue
            conclusions[name] = mapped
            if mapped == "SUCCESS":
                continue
            if mapped in {
                "PENDING", "QUEUED", "IN_PROGRESS", "WAITING",
                "REQUESTED", "EXPECTED",
            }:
                pending.append(name)
                continue
            # FAILURE, CANCELLED, SKIPPED, NEUTRAL, STALE,
            # ERROR, or any unrecognized terminal state all
            # block merge.
            failed.append(name)
        # Required-check duplicates in ``gh pr checks`` are
        # diagnostic only on the authoritative path because
        # the authoritative source is the single source of
        # truth. The unrelated-duplicates list still
        # surfaces so the operator can audit the push
        # duplicates.
        duplicated_required: List[str] = []
        return (
            True, conclusions, missing, pending, failed,
            duplicated_required + unrelated_duplicated,
            "",
        )

    # No authoritative binding: fall back to the round-6
    # generic ``gh pr checks`` duplicate-fails-closed path.
    conclusions = {}
    missing = []
    pending = []
    failed = []
    duplicated_required = []
    for name in required_check_names:
        records = by_name.get(name) or []
        if not records:
            missing.append(name)
            continue
        if len(records) > 1:
            duplicated_required.append(name)
            continue
        check = records[0]
        state = (check.get("state") or "").upper()
        conclusions[name] = state or "UNKNOWN"
        if state == "SUCCESS":
            continue
        if state in {
            "PENDING", "QUEUED", "IN_PROGRESS", "WAITING",
            "REQUESTED", "EXPECTED",
        }:
            pending.append(name)
            continue
        failed.append(name)
    duplicated_required = sorted(set(duplicated_required))
    return (
        True, conclusions, missing, pending, failed,
        duplicated_required + unrelated_duplicated,
        "",
    )


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

# Recognized codex bot authors for thread eligibility classification.
# Keep in sync with aed_pr_readiness.classify_thread_actor.
CODEX_BOT_LOGINS = frozenset({
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
})


def _coerce_scope_inputs(
    allowed_files: Optional[List[str]],
    forbidden_files: Optional[List[str]],
) -> Tuple[List[str], List[str]]:
    """Normalize scope inputs supplied by the operator.

    Either list may be ``None`` (meaning "no scope constraint from the
    operator"), in which case the controller falls back to scope=None
    in the evidence bundle (which fails the scope gate unless a
    canonical task-scope policy is supplied elsewhere).

    The controller NEVER invents a default scope list. (Round-2 Codex
    finding: do not hardcode a controller-only path allowlist.)
    """
    allowed = list(allowed_files) if allowed_files else []
    forbidden = list(forbidden_files) if forbidden_files else []
    # Strip non-string elements defensively so a malformed CLI arg
    # cannot poison the scope check.
    allowed = [a for a in allowed if isinstance(a, str) and a]
    forbidden = [a for a in forbidden if isinstance(a, str) and a]
    return allowed, forbidden


# -----------------------------------------------------------------------------
# Trusted scope source (round-3 fix #2; round-4 fix #1)
# -----------------------------------------------------------------------------
#
# Round-3 requirement: scope MUST come from a trusted, persistent
# source tied to the task/PR, not from the CLI at merge time. A caller
# must NOT be able to bypass scope by running merge with
# ``--allowed-files "**"``.
#
# The repository's existing scope checker is
# ``scripts/local/check_pr_scope.py``; we reuse it unchanged. The
# persistent trusted source it reads from is:
#
#     ``~/.hermes/aed/pr_scope/<repo_owner>/<repo_name>/<pr_number>/<head_sha>.json``
#
# The scope record is **immutably bound to a single exact head SHA**.
# Each push of the PR branch creates a new record; the previous
# record remains on disk but no longer authorizes a new merge. A
# moved head therefore requires a fresh ``aed_pr scope-write`` call
# before merge can be considered ready.
#
# Round-4 fix #1: the canonical scope root is hard-coded. There is
# no environment variable, CLI flag, or current-working-directory
# fall-back that may redirect production reads or writes. Tests
# inject an alternate root via the ``scope_root=`` keyword argument
# of the internal helpers; production callers never supply that
# argument.
#
# Production paths (status / advance / merge) read from the
# repository+PR+head-SHA-keyed location under ``~/.hermes/aed/...``.
# The merge command rejects CLI scope at the argparse level (no
# --allowed-files / --forbidden-files flag).
#
# Merge does NOT consult CLI scope and does NOT consult a
# caller-controlled scope directory: it reads from the canonical,
# head-tied, repository-keyed location. A merged commit requires
# the corresponding ``<head_sha>.json`` to exist; a stale record
# cannot authorize a moved head.
# scope file cannot authorize a moved head.

import os as _os
from pathlib import Path as _Path


# -----------------------------------------------------------------------------
# Trusted scope root selection
# -----------------------------------------------------------------------------
#
# Production paths MUST use the canonical scope root:
#     ~/.hermes/aed/pr_scope
#
# This is not configurable. There is no environment variable, no CLI
# flag, and no current-working-directory fall-back. The previous
# round-3 cycle left a `HERMES_AED_SCOPE_DIR` env-var seam in place
# which a hostile caller could use to point the merge path at a
# permissive directory of their own. That seam is now removed.
#
# Tests that need to bypass ``~/.hermes/aed/pr_scope`` pass an explicit
# ``scope_root`` argument to the internal functions. Production paths
# never accept that argument; they always read from the canonical
# root via ``_canonical_scope_root()``.
#
# ``_SCOPE_ROOT_OVERRIDE`` is a module-level variable that exists
# ONLY as a thread-local / process-local injection point for tests.
# It is read ONLY inside ``_trusted_scope_path`` when the caller has
# supplied an explicit ``scope_root=`` kwarg. Production code never
# supplies that kwarg, so the override is dead in production paths.

_CANONICAL_SCOPE_ROOT: _Path = _Path.home() / ".hermes" / "aed" / "pr_scope"


def _canonical_scope_root() -> _Path:
    """Return the canonical production scope root.

    Always ``~/.hermes/aed/pr_scope``. Not configurable. No
    environment variable is consulted. Tests inject via the
    ``scope_root=`` argument of the private helpers; production
    callers never do so.
    """
    return _CANONICAL_SCOPE_ROOT


_REPO_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_repo_components(repo: object) -> Tuple[str, str]:
    """Validate and split a GitHub ``owner/name`` repository value.

    Returns ``(owner, name)`` after enforcing the strict path-safe
    contract used by every trusted-scope path constructor. The
    contract is:

    * ``repo`` is a ``str``;
    * ``repo`` contains exactly one forward slash;
    * exactly two non-empty components;
    * each component matches ``[A-Za-z0-9._-]+`` (ASCII only);
    * neither component equals ``.`` or ``..``;
    * no backslashes, NUL characters, or whitespace anywhere;
    * no leading or trailing ``/``.

    Every other shape is rejected with a ``ValueError`` BEFORE any
    filesystem call. The helper never normalises malformed input
    into a valid repository name.
    """
    if not isinstance(repo, str):
        raise ValueError(
            "repo must be a str in 'owner/name' form"
        )
    if "\\" in repo:
        raise ValueError(
            "repo must not contain backslashes"
        )
    if "\x00" in repo:
        raise ValueError(
            "repo must not contain NUL characters"
        )
    if any(c.isspace() for c in repo):
        raise ValueError(
            "repo must not contain whitespace"
        )
    if repo.startswith("/"):
        raise ValueError(
            "repo must not start with '/'"
        )
    if repo.endswith("/"):
        raise ValueError(
            "repo must not end with '/'"
        )
    if "/" not in repo:
        raise ValueError(
            "repo must be in 'owner/name' form"
        )
    parts = repo.split("/")
    if len(parts) != 2:
        raise ValueError(
            "repo must contain exactly one '/' separator"
        )
    owner, name = parts
    if not owner or not name:
        raise ValueError(
            "repo owner and name must be non-empty"
        )
    if owner == "." or owner == "..":
        raise ValueError(
            "repo owner may not be '.' or '..'"
        )
    if name == "." or name == "..":
        raise ValueError(
            "repo name may not be '.' or '..'"
        )
    if not _REPO_COMPONENT_RE.match(owner):
        raise ValueError(
            "repo owner may only contain ASCII letters, digits, "
            "'.', '_', or '-'"
        )
    if not _REPO_COMPONENT_RE.match(name):
        raise ValueError(
            "repo name may only contain ASCII letters, digits, "
            "'.', '_', or '-'"
        )
    return owner, name


def _trusted_scope_path(
    repo: str,
    pr_number: int,
    head_sha: str,
    *,
    scope_root: Optional[_Path] = None,
) -> _Path:
    """Resolve the on-disk path of the trusted scope file.

    Production callers MUST NOT pass ``scope_root``. When ``scope_root``
    is supplied (tests only), it is used instead of the canonical
    root. The production root is hard-coded; no environment variable,
    CLI flag, or working-directory fall-back is consulted.

    The ``repo`` argument is validated through
    :func:`_validate_repo_components` before any filesystem call, so
    malformed repository values cannot escape ``scope_root`` via
    parent-directory segments or absolute paths.
    """
    owner, name = _validate_repo_components(repo)
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError("pr_number must be a positive int")
    if not R.is_canonical_head_sha(head_sha):
        raise ValueError("head_sha must be exactly 40 lowercase hex chars")
    base = scope_root if scope_root is not None else _canonical_scope_root()
    return base / owner / name / str(pr_number) / f"{head_sha}.json"


def read_trusted_scope(
    repo: str,
    pr_number: int,
    head_sha: str,
    *,
    scope_root: Optional[_Path] = None,
) -> Tuple[Optional[List[str]], Optional[List[str]], str]:
    """Read the trusted scope for ``(repo, pr_number, head_sha)``.

    Production callers MUST NOT pass ``scope_root``. When ``scope_root``
    is supplied (tests only), it is used instead of the canonical
    root. ``scope_root`` is the ONLY seam that lets tests redirect
    lookups; there is no environment-variable override.

    Returns ``(allowed, forbidden, error)``. When the file is
    absent, malformed, or the recorded ``head_sha`` field does not
    byte-exactly match the supplied ``head_sha``, ``allowed`` is
    ``None`` and ``error`` is non-empty so the readiness evaluator
    fails closed. A stale record (recorded ``head_sha`` different
    from the live head) MUST block merge.
    """
    try:
        path = _trusted_scope_path(
            repo, pr_number, head_sha, scope_root=scope_root
        )
    except ValueError as exc:
        return None, None, f"trusted scope path invalid: {exc}"
    if not path.exists():
        return None, None, f"trusted scope not found at {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, f"trusted scope unreadable: {exc}"
    if not isinstance(data, dict):
        return None, None, "trusted scope must be a JSON object"
    recorded_head = data.get("head_sha")
    # Round-19 hardening: ``head_sha`` is REQUIRED on every trusted
    # record. A missing, non-string, or malformed stored head
    # MUST NOT be silently accepted. A missing or wrong head would
    # let a copied/legacy file authorize a head it never attested
    # to; the fail-closed binding is enforced strictly.
    if not isinstance(recorded_head, str):
        return None, None, (
            "trusted scope head_sha missing or not a string"
        )
    if not R.is_canonical_head_sha(recorded_head):
        return None, None, (
            "trusted scope head_sha malformed: "
            "must be exactly 40 lowercase hex characters"
        )
    if recorded_head != head_sha:
        return None, None, (
            f"trusted scope head_sha mismatch: recorded "
            f"{recorded_head!r} != live {head_sha!r}"
        )
    # Round-20 hardening: ``repo`` and ``pr_number`` are REQUIRED
    # on every trusted record. A record copied from another PR or
    # another repository that happens to share the same head SHA
    # MUST NOT be silently accepted. The stored identity must
    # byte-exactly match the requested (repo, pr_number).
    recorded_repo = data.get("repo")
    if not isinstance(recorded_repo, str):
        return None, None, (
            "trusted scope repo missing or not a string"
        )
    # Validate the stored repo through the same path-safe
    # contract; a malformed stored value must also fail closed
    # so a hostile or corrupted record cannot reach the path
    # constructor.
    try:
        recorded_owner, recorded_name = _validate_repo_components(
            recorded_repo
        )
    except ValueError as exc:
        return None, None, (
            f"trusted scope repo malformed: {exc}"
        )
    requested_owner, requested_name = _validate_repo_components(repo)
    canonical_recorded_repo = f"{recorded_owner}/{recorded_name}"
    canonical_requested_repo = f"{requested_owner}/{requested_name}"
    if canonical_recorded_repo != canonical_requested_repo:
        return None, None, (
            f"trusted scope repo mismatch: recorded "
            f"{canonical_recorded_repo!r} != live "
            f"{canonical_requested_repo!r}"
        )
    recorded_pr = data.get("pr_number")
    if not isinstance(recorded_pr, int) or isinstance(recorded_pr, bool):
        return None, None, (
            "trusted scope pr_number missing or not an int"
        )
    if recorded_pr != pr_number:
        return None, None, (
            f"trusted scope pr_number mismatch: recorded "
            f"{recorded_pr!r} != live {pr_number!r}"
        )
    allowed_raw = data.get("allowed_files")
    forbidden_raw = data.get("forbidden_files")
    allowed = (
        [a for a in allowed_raw if isinstance(a, str) and a]
        if isinstance(allowed_raw, list)
        else None
    )
    forbidden = (
        [a for a in forbidden_raw if isinstance(a, str) and a]
        if isinstance(forbidden_raw, list)
        else None
    )
    return allowed, forbidden, ""


def write_trusted_scope(
    repo: str,
    pr_number: int,
    head_sha: str,
    allowed_files: List[str],
    forbidden_files: Optional[List[str]] = None,
    *,
    scope_root: Optional[_Path] = None,
) -> Tuple[bool, str]:
    """Persist the trusted scope for ``(repo, pr_number, head_sha)``.

    Used by the controller's ``scope-write`` subcommand and by tests.
    The parent directory is created on demand. Forbidden files default
    to ``[]`` when not provided. The written file includes the
    ``head_sha`` field so a later ``read_trusted_scope`` can detect
    a stale record.

    Production callers MUST NOT pass ``scope_root``. When
    ``scope_root`` is supplied (tests only), it is used instead of
    the canonical root. ``scope_root`` is the ONLY seam that lets
    tests redirect writes; there is no environment-variable override.
    """
    try:
        owner, name = _validate_repo_components(repo)
    except ValueError as exc:
        return False, f"repo invalid: {exc}"
    if not isinstance(pr_number, int) or pr_number <= 0:
        return False, "pr_number must be a positive int"
    if not R.is_canonical_head_sha(head_sha):
        return False, "head_sha must be exactly 40 lowercase hex chars"
    allowed_clean = [a for a in allowed_files if isinstance(a, str) and a]
    forbidden_clean = (
        [a for a in forbidden_files if isinstance(a, str) and a]
        if isinstance(forbidden_files, list)
        else []
    )
    payload = {
        "pr_number": pr_number,
        "repo": f"{owner}/{name}",
        "head_sha": head_sha,
        "allowed_files": allowed_clean,
        "forbidden_files": forbidden_clean,
        "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    try:
        path = _trusted_scope_path(
            repo, pr_number, head_sha, scope_root=scope_root
        )
    except ValueError as exc:
        return False, f"trusted scope path invalid: {exc}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        return False, f"could not write trusted scope: {exc}"
    return True, str(path)


def cmd_scope_write(args: argparse.Namespace) -> int:
    """Persist a trusted scope for ``--pr-number`` and ``--head-sha``.

    This subcommand is the operator-facing entry point that writes
    the trusted scope file. Subsequent ``status``, ``advance``, and
    ``merge`` calls read this file; the merge command refuses CLI
    scope overrides entirely.

    ``--head-sha`` is REQUIRED so the file is immutably bound to a
    single exact head SHA. A moved head requires a new ``scope-write``
    call.
    """
    head_sha = getattr(args, "head_sha", None)
    if not head_sha or not R.is_canonical_head_sha(head_sha):
        sys.stderr.write(
            "scope-write: --head-sha must be exactly 40 lowercase hex chars\n"
        )
        return 1
    allowed = _parse_scope_arg(args.allowed_files) or []
    forbidden = _parse_scope_arg(args.forbidden_files) or []
    ok, result = write_trusted_scope(
        args.repo, args.pr_number, head_sha, allowed, forbidden
    )
    if not ok:
        sys.stderr.write(f"scope-write failed: {result}\n")
        return 1
    sys.stdout.write(json.dumps({
        "tool": "aed_pr.scope_write",
        "pr_number": args.pr_number,
        "head_sha": head_sha,
        "path": result,
        "allowed_files": allowed,
        "forbidden_files": forbidden,
    }, indent=2))
    sys.stdout.write("\n")
    return 0


def cmd_scope_read(args: argparse.Namespace) -> int:
    """Read and emit the trusted scope for ``--pr-number`` and ``--head-sha``.

    Returns exit code 0 when the file exists and is well-formed,
    exit code 1 when the file is absent, malformed, or tied to a
    different head (fail closed).
    """
    head_sha = getattr(args, "head_sha", None)
    if not head_sha or not R.is_canonical_head_sha(head_sha):
        sys.stderr.write(
            "scope-read: --head-sha must be exactly 40 lowercase hex chars\n"
        )
        return 1
    allowed, forbidden, err = read_trusted_scope(
        args.repo, args.pr_number, head_sha
    )
    if err:
        sys.stderr.write(f"scope-read failed: {err}\n")
        return 1
    sys.stdout.write(json.dumps({
        "tool": "aed_pr.scope_read",
        "pr_number": args.pr_number,
        "head_sha": head_sha,
        "allowed_files": allowed or [],
        "forbidden_files": forbidden or [],
    }, indent=2))
    sys.stdout.write("\n")
    return 0


def _find_dispatch_run(
    repo: str,
    workflow_file: str,
    *,
    head_sha: str,
    head_branch: str,
    pr_number: int,
    dispatched_at: dt.datetime,
    list_runner: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Identify the workflow_dispatch run created by ``cmd_gate_recheck``.

    Lists workflow runs whose ``event`` is ``workflow_dispatch``,
    whose ``head_branch`` equals the live PR branch, whose
    ``head_sha`` equals the requested exact PR head, whose
    ``workflow.name`` matches ``workflow_file`` (or whose path
    matches ``.github/workflows/<filename>``), and whose
    ``created_at`` is at or after the supplied ``dispatched_at``.

    Returns ``(run, error)``. ``run`` is the matching run dict (or
    ``None`` when no run matches). ``error`` is non-empty when the
    listing itself failed.

    The function uses ``list_runner`` (default ``subprocess.run``)
    so tests can mock the GitHub API call deterministically.

    The check is deliberately strict: a run tied to ``main``, another
    branch, another SHA, another PR, or an earlier dispatch is
    rejected. The first matching run (chronologically) wins; ties
    are broken by ``created_at`` then ``id``.
    """
    if list_runner is None:
        list_runner = subprocess.run
    cmd = [
        "gh", "run", "list",
        "--repo", repo,
        # Scope the query to the exact workflow file. ``gh run
        # list --workflow <file>`` filters at the API level, so the
        # returned runs are guaranteed to belong to that workflow.
        "--workflow", workflow_file,
        # Scope the query to the live PR branch so the API never
        # returns main-branch runs in the first place.
        "--branch", head_branch,
        "--event", "workflow_dispatch",
        "--limit", "30",
        # ``workflowName`` (flat string) is the canonical name of
        # the workflow (e.g. ``CI``). The nested ``workflows`` array
        # is NOT exposed on this CLI version.
        "--json", "databaseId,event,headBranch,headSha,createdAt,"
        "status,conclusion,url,workflowName",
    ]
    try:
        proc = list_runner(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"run list failed: {exc}"
    if proc.returncode != 0:
        return None, (
            f"run list returned {proc.returncode}: "
            f"{proc.stderr.strip()[:300]}"
        )
    try:
        runs = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"invalid run list JSON: {exc}"
    if not isinstance(runs, list):
        return None, "run list payload is not a list"
    matching: List[Dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("event") != "workflow_dispatch":
            continue
        if run.get("headBranch") != head_branch:
            continue
        if run.get("headSha") != head_sha:
            continue
        # ``gh run list --workflow <file>`` should already filter to
        # the requested workflow file. Defensively verify
        # ``workflowName`` against the expected workflow display
        # name. The CLI maps ``.github/workflows/ci.yml`` to the
        # human-readable name ``CI``; we accept only the exact match
        # so an unrelated workflow with a similar basename cannot
        # be picked up.
        expected_workflow_name = EXPECTED_WORKFLOW_NAME.get(
            workflow_file, workflow_file
        )
        run_workflow_name = run.get("workflowName") or ""
        if run_workflow_name != expected_workflow_name:
            continue
        # Round-6 follow-up: parse createdAt as a real timezone-aware
        # datetime rather than comparing timestamp strings.
        created_at_str = run.get("createdAt") or ""
        if not created_at_str:
            continue
        try:
            created_at = dt.datetime.fromisoformat(
                created_at_str.replace("Z", "+00:00")
            )
            if created_at.tzinfo is None:
                # Naive timestamps from the API are treated as UTC
                # for comparison purposes.
                created_at = created_at.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            # Malformed createdAt fails closed.
            continue
        # Round-8 follow-up (Codex comment 3609202698): GitHub
        # Actions ``createdAt`` commonly has whole-second precision
        # while ``dispatched_at`` includes fractional seconds. Floor
        # ``dispatched_at`` to whole-second precision before
        # comparing so a dispatch at 12:00:00.900000Z against a run
        # at 12:00:00Z is correctly accepted. Runs from an earlier
        # second are still rejected.
        try:
            dispatch_boundary = dispatched_at.astimezone(
                dt.timezone.utc
            ).replace(microsecond=0)
        except (AttributeError, TypeError, ValueError):
            # Naive or non-pickleable ``dispatched_at``: treat as UTC.
            dispatch_boundary = dispatched_at.replace(
                tzinfo=dt.timezone.utc, microsecond=0
            )
        if created_at < dispatch_boundary:
            continue
        # databaseId must be present and an integer.
        if not isinstance(run.get("databaseId"), int):
            continue
        # URL must be present.
        if not (run.get("url") or ""):
            continue
        matching.append(run)
    if not matching:
        return None, (
            "no workflow_dispatch run found for "
            f"branch={head_branch!r} head={head_sha!r} "
            f"workflow={workflow_file!r} after {dispatched_at.isoformat()}"
        )
    # Sort by created_at descending then databaseId descending.
    # Newest matching run wins so a stale earlier dispatch does not
    # block a fresh one.
    matching.sort(
        key=lambda r: (r.get("createdAt") or "", r.get("databaseId") or 0),
        reverse=True,
    )
    return matching[0], ""


def _wait_for_dispatch_run(
    repo: str,
    workflow_file: str,
    *,
    head_sha: str,
    head_branch: str,
    pr_number: int,
    dispatched_at: "dt.datetime",
    timeout_seconds: int = 60,
    poll_seconds: int = 2,
    list_runner: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Wait for the dispatched ``workflow_dispatch`` run to appear.

    Round-6 follow-up (live gate-recheck defect on
    ``c229be82``): GitHub Actions may accept the dispatch before
    ``gh run list`` exposes the new run. The previous
    implementation called ``_find_dispatch_run`` once and
    immediately returned INCONCLUSIVE when no run was visible,
    even though the run had been queued and would appear a few
    seconds later.

    This function polls ``_find_dispatch_run`` until the exact
    run appears or the bounded discovery timeout expires. It
    does NOT issue a second workflow dispatch during polling
    (the dispatch has already been issued; the controller
    simply waits for the API to expose it). Transient list
    errors and empty lists are retried. ``createdAt`` is parsed
    as a real timezone-aware datetime so timestamp parsing
    matches ``dispatched_at`` precisely.

    Returns ``(run, error)``. ``run`` is the matching run dict
    (or ``None`` when the discovery timeout expires). ``error``
    is non-empty on timeout or persistent failure.

    The default ``timeout_seconds`` is 60; the default
    ``poll_seconds`` is 2. No daemon or scheduler is added.
    """
    if list_runner is None:
        list_runner = subprocess.run
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        run, err = _find_dispatch_run(
            repo,
            workflow_file,
            head_sha=head_sha,
            head_branch=head_branch,
            pr_number=pr_number,
            dispatched_at=dispatched_at,
            list_runner=list_runner,
        )
        if run is not None:
            return run, ""
        if err:
            last_error = err
        time.sleep(poll_seconds)
    return None, (
        f"dispatched run did not appear within {timeout_seconds}s "
        f"(last_error={last_error!r})"
    )


def _wait_for_gate_job(
    repo: str,
    run_id: int,
    *,
    timeout_seconds: int = 600,
    poll_seconds: int = 10,
    list_runner: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Wait for the ``review-comment-gate`` job of a workflow run
    to reach a terminal state.

    Returns ``(job, error)``. ``job`` is the matching job dict (or
    ``None`` when the gate does not reach a terminal state within
    ``timeout_seconds``). ``error`` is non-empty when the listing
    itself failed or the gate never reported a terminal conclusion.

    The function uses bounded polling. ``poll_seconds`` defaults to
    10; ``timeout_seconds`` defaults to 600. The cumulative wait is
    bounded by ``timeout_seconds``. No daemon or scheduler is added.
    """
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        jobs, err = _list_run_jobs(repo, run_id, list_runner=list_runner)
        if err:
            last_error = err
            time.sleep(poll_seconds)
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if job.get("name") != "review-comment-gate":
                continue
            status = job.get("status")
            if status == "completed":
                return job, ""
            break  # one match per run
        time.sleep(poll_seconds)
    return None, (
        f"review-comment-gate did not complete within "
        f"{timeout_seconds}s (last_error={last_error!r})"
    )


def _list_run_jobs(
    repo: str,
    run_id: int,
    *,
    list_runner: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """List the jobs of a workflow run via ``gh run view``."""
    if list_runner is None:
        list_runner = subprocess.run
    cmd = [
        "gh", "run", "view", str(run_id),
        "--repo", repo,
        "--json", "jobs",
    ]
    try:
        proc = list_runner(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"run view failed: {exc}"
    if proc.returncode != 0:
        return [], (
            f"run view returned {proc.returncode}: "
            f"{proc.stderr.strip()[:300]}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [], f"invalid run view JSON: {exc}"
    if not isinstance(payload, dict):
        return [], "run view payload is not a dict"
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return [], "run view jobs is not a list"
    return jobs, ""


def _find_exact_head_pull_request_run(
    repo: str,
    *,
    head_sha: str,
    head_branch: str,
    list_runner: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Find the unique ``pull_request`` CI run for the exact head.

    Round-10 helper: enumerates ``workflow_file=ci.yml`` runs with
    ``event=pull_request`` on the exact head branch, narrows the
    candidates to those whose ``headSha`` matches the requested
    exact SHA, and selects the newest run whose binding fields
    all match.

    Returns ``(run, error)``. ``run`` is the matching run dict
    (or ``None`` when no run qualifies). ``error`` is non-empty
    when the search fails or more than one run cannot be
    deterministically distinguished.

    The function is bound by:

    - ``workflowName`` exactly ``CI``;
    - ``event`` exactly ``pull_request``;
    - exact head branch;
    - exact head SHA;
    - valid integer ``databaseId``;
    - nonempty ``url``.

    Any workflow_dispatch or rerun run on the same head is
    rejected; only the pull_request trigger run is acceptable.
    """
    if list_runner is None:
        list_runner = subprocess.run
    cmd = [
        "gh", "run", "list",
        "--repo", repo,
        "--workflow", "ci.yml",
        "--event", "pull_request",
        "--branch", head_branch,
        "--commit", head_sha,
        "--limit", "30",
        "--json",
        "databaseId,event,headBranch,headSha,status,conclusion,"
        "url,workflowName,createdAt,name",
    ]
    try:
        proc = list_runner(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"run list failed: {exc}"
    if proc.returncode != 0:
        return None, (
            f"run list returned {proc.returncode}: "
            f"{(proc.stderr or '').strip()[:300]}"
        )
    try:
        runs = json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        return None, f"invalid run list JSON: {exc}"
    if not isinstance(runs, list):
        return None, "run list payload is not a list"
    matching: List[Dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if (run.get("workflowName") or "") != "CI":
            continue
        if (run.get("event") or "") != "pull_request":
            continue
        if run.get("headBranch") != head_branch:
            continue
        if run.get("headSha") != head_sha:
            continue
        if not isinstance(run.get("databaseId"), int):
            continue
        if not (run.get("url") or ""):
            continue
        matching.append(run)
    if len(matching) == 0:
        return None, (
            f"no exact-head pull_request CI run for "
            f"branch={head_branch!r} head={head_sha!r} workflow='ci.yml'"
        )
    if len(matching) > 1:
        # Round-21 fix: when two or more pull_request CI runs
        # match the exact head, the required-check evidence is
        # ambiguous. The readiness path treats this same case as
        # ``multiple_exact_head_pr_runs`` and refuses to authorise
        # a head with non-deterministic evidence. ``gate-recheck``
        # MUST apply the same rule: silently picking one run would
        # let the operator see a false successful recheck signal
        # even though the readiness evaluator would refuse the
        # same head. Return INCONCLUSIVE so the caller exits
        # non-zero and refuses to rerun any job.
        matching.sort(
            key=lambda r: (r.get("createdAt") or "", r.get("databaseId") or 0),
            reverse=True,
        )
        return None, (
            f"multiple_exact_head_pr_runs: "
            f"{len(matching)} matching pull_request CI runs for "
            f"branch={head_branch!r} head={head_sha!r}; refusing to "
            f"select one"
        )
    return matching[0], ""


def _read_run_attempt_count(
    repo: str,
    run_id: int,
    *,
    view_runner: Optional[Any] = None,
) -> Optional[int]:
    """Read the current ``attempt`` count of a workflow run.

    Round-10 helper. Returns ``None`` when the count cannot be
    fetched or parsed. ``view_runner`` is an injectable
    subprocess.run replacement.
    """
    if view_runner is None:
        view_runner = subprocess.run
    cmd = [
        "gh", "run", "view", str(run_id),
        "--repo", repo,
        "--json", "attempt",
    ]
    try:
        proc = view_runner(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    attempt = payload.get("attempt")
    if isinstance(attempt, int):
        return attempt
    return None


def _find_target_gate_job(
    repo: str,
    run_id: int,
    *,
    view_runner: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Find the unique ``review-comment-gate`` job in a run.

    Round-10 helper. Rejects:

    - jobs whose list cannot be fetched;
    - runs without a ``review-comment-gate`` job;
    - runs with more than one job named ``review-comment-gate``;
    - jobs whose ``databaseId`` is not an integer.

    Returns ``(job, error)``. ``job`` is the matching job dict.
    """
    jobs, err = _list_run_jobs(
        repo, run_id, list_runner=view_runner,
    )
    if err or not isinstance(jobs, list):
        return None, err or "job inventory is not a list"
    matching: List[Dict[str, Any]] = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        if j.get("name") == "review-comment-gate":
            matching.append(j)
    if not matching:
        return None, "no review-comment-gate job in run"
    if len(matching) > 1:
        return None, (
            f"duplicate review-comment-gate jobs ({len(matching)})"
        )
    job = matching[0]
    if not isinstance(job.get("databaseId"), int):
        return None, "target job has no integer databaseId"
    return job, ""


def _wait_for_rerun_attempt(
    repo: str,
    run_id: int,
    *,
    pre_rerun_attempt: int,
    target_job_name: str,
    timeout_seconds: int,
    poll_seconds: int,
    view_runner: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], str, Optional[int]]:
    """Wait for the rerun attempt and a terminal ``target_job_name``.

    Round-10 helper. Boundedly polls ``gh run view`` for the
    same run, requiring:

    1. The attempt count to strictly exceed ``pre_rerun_attempt``.
    2. A ``target_job_name`` job present in the new attempt.
    3. The target job in a terminal state with a valid
       ``conclusion``.

    Returns ``(job, error, attempt)``; ``job`` is the matching
    job dict in the new attempt, ``error`` is non-empty when
    the bound expired or evidence is malformed.
    """
    if view_runner is None:
        view_runner = subprocess.run
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        attempt = _read_run_attempt_count(
            repo, run_id, view_runner=view_runner,
        )
        if attempt is not None and attempt > pre_rerun_attempt:
            jobs, err = _list_run_jobs(
                repo, run_id, list_runner=view_runner,
            )
            if err:
                last_error = err
            else:
                for j in jobs:
                    if (
                        isinstance(j, dict)
                        and j.get("name") == target_job_name
                    ):
                        status = (j.get("status") or "").lower()
                        conclusion = (j.get("conclusion") or "")
                        if status == "completed" and conclusion:
                            return j, "", attempt
                        if status in {"queued", "in_progress", "waiting",
                                       "pending", "requested"}:
                            last_error = (
                                f"target job still {status!r}"
                            )
                        else:
                            # Anything else is a non-terminal,
                            # non-pending state - treat as a
                            # soft error and keep polling.
                            last_error = (
                                f"target job in unexpected state "
                                f"{status!r}"
                            )
        time.sleep(poll_seconds)
    return None, (
        f"rerun attempt did not appear within {timeout_seconds}s "
        f"(last_error={last_error!r})"
    ), None


def cmd_gate_recheck(args: argparse.Namespace) -> int:
    """Rerun the exact-head ``review-comment-gate`` job from the
    existing pull_request CI run for the live PR head.

    Round-10 follow-up (Codex review on ``f3c8c06``): the
    previous gate-only ``workflow_dispatch`` flow issued
    ``gh workflow run ci.yml`` with ``-f
    gate=review-comment-gate`` and used conditional ``if:``
    guards to skip the four ordinary jobs. GitHub may report
    conditionally-skipped jobs as successful checks, which
    could let the merge gate see green CI on a run that did
    not actually run the required jobs.

    The new flow reruns the existing
    ``review-comment-gate`` job from the existing exact-head
    ``pull_request`` CI run via ``gh run rerun --job <id>``.
    No ``workflow_dispatch`` is issued. No ordinary CI jobs
    are rerun. The bound is enforced at the controller layer
    rather than the workflow layer:

    1. Refetch the live PR state and reject the request when
       ``args.head_sha`` does not byte-exactly equal
       ``pr_view.headRefOid``.
    2. List the ``pull_request`` CI runs for the live head.
       Reject runs on the wrong branch, the wrong SHA, the
       wrong workflow name, or the wrong event. Select the
       newest unique run whose binding fields all match.
    3. Fetch the run's jobs. Reject a run whose job list
       cannot be fetched, whose ``review-comment-gate`` is
       missing, or whose ``review-comment-gate`` is
       duplicated. Reject a job without an integer
       ``databaseId``.
    4. Record the run's current ``attempt`` count, the run's
       ``databaseId``, and the target job's pre-rerun
       ``status`` and ``conclusion``.
    5. Invoke ``gh run rerun <run-id> --repo <repo> --job
       <job-database-id>`` exactly once.
    6. Boundedly poll the same run under a timeout until the
       attempt count is strictly greater than the pre-rerun
       attempt, the ``review-comment-gate`` is present in the
       new attempt, and the gate has reached a terminal state.
    7. Return ``0`` only when the rerun attempt's terminal
       conclusion is ``success``; ``1`` when it is ``failure``;
       ``2`` (INCONCLUSIVE) on missing, duplicate, malformed,
       stale, uncertain or timed-out evidence.

    Tests inject ``rerun_runner``, ``pr_view_runner``,
    ``list_runner``, ``view_runner`` so the GitHub surface is
    exercised deterministically. The default runner is
    ``subprocess.run``. ``dry-run`` is honored: when set,
    rerun_runner is NOT invoked and the report records
    ``would_rerun=True`` without mutation.
    """
    repo = args.repo
    pr_number = args.pr_number
    head_sha = args.head_sha
    if not R.is_canonical_head_sha(head_sha):
        sys.stderr.write(
            "gate-recheck: --head-sha must be exactly 40 lowercase hex chars\n"
        )
        return 2

    # Step 1: refetch the live PR state. The exact head SHA and
    # head branch must come from the live PR view; we do NOT trust
    # arguments alone.
    pr_view_runner = getattr(args, "pr_view_runner", None)
    try:
        pr_view = fetch_pr_state(repo, pr_number, runner=pr_view_runner)
    except Exception as exc:
        sys.stderr.write(f"gate-recheck: pr view failed: {exc}\n")
        return 2
    live_head_sha = pr_view.get("headRefOid") or ""
    live_head_branch = pr_view.get("headRefName") or ""
    if not R.is_canonical_head_sha(live_head_sha):
        sys.stderr.write(
            "gate-recheck: live pr view head SHA is not canonical: "
            f"{live_head_sha!r}\n"
        )
        return 2
    if head_sha != live_head_sha:
        sys.stderr.write(
            "gate-recheck: requested head_sha does not match live PR head: "
            f"requested={head_sha!r} live={live_head_sha!r}\n"
        )
        return 2
    if not live_head_branch:
        sys.stderr.write(
            "gate-recheck: live PR view has no headRefName; cannot bind rerun\n"
        )
        return 2

    list_runner = getattr(args, "list_runner", None) or subprocess.run
    view_runner = getattr(args, "view_runner", None) or list_runner
    rerun_runner = getattr(args, "rerun_runner", None) or subprocess.run
    dry_run = bool(getattr(args, "dry_run", False))

    # Step 2: find the existing exact-head pull_request run.
    run, err = _find_exact_head_pull_request_run(
        repo=repo,
        head_sha=head_sha,
        head_branch=live_head_branch,
        list_runner=list_runner,
    )
    if err or run is None:
        sys.stderr.write(f"gate-recheck: cannot identify pull_request run: {err}\n")
        return 2
    run_id = run.get("databaseId")
    if not isinstance(run_id, int):
        sys.stderr.write(
            "gate-recheck: pull_request run has no databaseId; refusing\n"
        )
        return 2
    pre_attempt = _read_run_attempt_count(
        repo, run_id, view_runner=view_runner,
    )
    if pre_attempt is None:
        sys.stderr.write(
            "gate-recheck: pull_request run has no attempt count; refusing\n"
        )
        return 2
    if not (run.get("url") or ""):
        sys.stderr.write(
            "gate-recheck: pull_request run has no url; refusing\n"
        )
        return 2

    # Step 3: fetch the run's jobs, find review-comment-gate.
    target_job, target_err = _find_target_gate_job(
        repo, run_id, view_runner=view_runner,
    )
    if target_err or target_job is None:
        sys.stderr.write(
            f"gate-recheck: cannot identify review-comment-gate job: "
            f"{target_err}\n"
        )
        return 2
    target_job_id = target_job.get("databaseId")
    if not isinstance(target_job_id, int):
        sys.stderr.write(
            "gate-recheck: target job has no integer databaseId; refusing\n"
        )
        return 2

    # Step 4: report pre-rerun state.
    sys.stdout.write(
        json.dumps({
            "tool": "aed_pr.gate_recheck",
            "exact_head_pull_request_run": {
                "databaseId": run_id,
                "name": run.get("name"),
                "headBranch": run.get("headBranch"),
                "headSha": run.get("headSha"),
                "event": run.get("event"),
                "workflowName": run.get("workflowName"),
                "url": run.get("url"),
                "pre_rerun_attempt": pre_attempt,
            },
            "target_job": {
                "databaseId": target_job_id,
                "name": target_job.get("name"),
                "pre_status": target_job.get("status"),
                "pre_conclusion": target_job.get("conclusion"),
            },
        }, indent=2)
    )
    sys.stdout.write("\n")

    if dry_run:
        sys.stdout.write(
            json.dumps({
                "tool": "aed_pr.gate_recheck",
                "would_rerun": True,
                "rerun_argv": [
                    "gh", "run", "rerun", str(run_id),
                    "--repo", repo, "--job", str(target_job_id),
                ],
            }, indent=2)
        )
        sys.stdout.write("\n")
        return 0

    # Step 5: invoke ``gh run rerun --job <id>`` exactly once.
    rerun_cmd = [
        "gh", "run", "rerun", str(run_id),
        "--repo", repo, "--job", str(target_job_id),
    ]
    try:
        rerun_proc = rerun_runner(
            rerun_cmd, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"gate-recheck rerun failed: {exc}\n")
        return 2
    if rerun_proc.returncode != 0:
        sys.stderr.write(
            f"gate-recheck rerun returned {rerun_proc.returncode}: "
            f"{(rerun_proc.stderr or '').strip()[:300]}\n"
        )
        return 2

    # Step 6: bounded wait for the new attempt.
    timeout_seconds = int(getattr(args, "wait_timeout_seconds", 600) or 600)
    poll_seconds = int(getattr(args, "wait_poll_seconds", 10) or 10)
    final_job, final_err, final_attempt = _wait_for_rerun_attempt(
        repo=repo,
        run_id=run_id,
        pre_rerun_attempt=pre_attempt,
        target_job_name="review-comment-gate",
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        view_runner=view_runner,
    )
    if final_job is None or final_attempt is None:
        sys.stderr.write(
            f"gate-recheck: rerun attempt did not appear: {final_err}\n"
        )
        return 2
    conclusion = (final_job.get("conclusion") or "").lower()
    sys.stdout.write(
        json.dumps({
            "tool": "aed_pr.gate_recheck",
            "rerun_attempt": {
                "databaseId": run_id,
                "attempt": final_attempt,
                "job_name": final_job.get("name"),
                "job_id": final_job.get("databaseId"),
                "status": final_job.get("status"),
                "conclusion": final_job.get("conclusion"),
                "url": final_job.get("url") or run.get("url"),
            },
        }, indent=2)
    )
    sys.stdout.write("\n")
    if conclusion == "success":
        return 0
    if conclusion == "failure":
        return 1
    return 2


# The merge command REJECTS CLI scope at the argparse level. The
# constant below documents the policy at the call site.
_MERGE_CLI_SCOPE_REJECTION = (
    "merge does not accept --allowed-files or --forbidden-files; scope "
    "must come from the canonical trusted scope file at "
    "~/.hermes/aed/pr_scope/<owner>/<name>/<pr>/<head_sha>.json. "
    "Use `aed_pr scope-write --head-sha <sha>` to persist the trusted "
    "scope before merge. The canonical scope root is hard-coded; "
    "no environment variable, CLI flag, or working-directory "
    "fall-back may redirect it."
)


# Round-16 — Codex finding PRRC_kwDOSHFpYM7XcqZM (review 4735335955,
# comment 3614615116, thread PRRT_kwDOSHFpYM6SQa5E, line 2085):
# ``status`` and ``advance`` previously accepted CLI-supplied
# ``--allowed-files`` / ``--forbidden-files`` patterns as authoritative
# scope, while ``merge`` rejected the same flags and read only the
# canonical trusted scope file. The status/advance path then fed the
# CLI patterns into ``build_evidence``, which set ``scope_clean=True``
# on a clean diff and let ``status`` emit the canonical merge
# authorization phrase — even though ``merge`` would then reject the
# same PR because it only trusts the canonical file. The controller's
# status/advance/merge contract diverged and operators could see a
# false ready signal from an untrusted CLI override.
#
# Round-16 policy: the only authoritative scope source for ANY
# lifecycle readiness command (status, advance, merge) is the canonical
# trusted exact-head record. CLI scope on status/advance is rejected
# with a structured, explicit diagnostic. ``scope-write`` is the only
# command that persists the trusted record. This constant documents
# the rejection at the call site.
_STATUS_ADVANCE_CLI_SCOPE_REJECTION = (
    "cli_scope_not_authoritative: --allowed-files and --forbidden-files "
    "are NOT authoritative for status or advance. Lifecycle readiness "
    "requires the canonical trusted scope record at "
    "~/.hermes/aed/pr_scope/<owner>/<name>/<pr>/<head_sha>.json. "
    "Persist the trusted exact-head scope with "
    "`aed_pr scope-write --head-sha <sha>` and rerun without the CLI "
    "scope flags. The canonical scope root is hard-coded; no "
    "environment variable, CLI flag, or working-directory fall-back may "
    "redirect it."
)


def _resolve_effective_scope(
    *,
    subcommand: str,
    repo: str,
    pr_number: int,
    head_sha: Optional[str],
    cli_allowed: Optional[List[str]],
    cli_forbidden: Optional[List[str]],
) -> Tuple[Optional[List[str]], Optional[List[str]], str]:
    """Resolve the scope that ``build_evidence`` will see.

    Three cases:

    * ``subcommand == "merge"`` - CLI scope is REJECTED outright. The
      trusted file is the only source, and it is read from the
      hard-coded canonical scope root. When the file is absent or
      tied to a different head SHA, the readiness evaluator fails
      closed.
    * ``subcommand in ("status", "advance")`` - CLI scope is
      REJECTED with a structured diagnostic. Lifecycle readiness for
      ``status`` and ``advance`` must come from the canonical trusted
      exact-head record, the same source ``merge`` reads. CLI scope
      on ``status``/``advance`` is silently ignored BEFORE the round-16
      fix; it is now an explicit ``cli_scope_not_authoritative``
      failure so ``status`` cannot emit an authorization phrase on a
      untrusted CLI override and ``advance`` cannot perform any
      lifecycle mutation.
    * Trusted file absent and no CLI scope - returns ``None`` lists
      so the scope gate fails closed (no fallback to a default
      allowlist).

    No environment variable is consulted. There is no working-directory
    fall-back. The canonical scope root is the only root consulted by
    any production caller.
    """
    if subcommand == "merge":
        if cli_allowed is not None or cli_forbidden is not None:
            return None, None, _MERGE_CLI_SCOPE_REJECTION
        if not head_sha or not R.is_canonical_head_sha(head_sha):
            return None, None, (
                "merge requires the live head SHA before the trusted "
                "scope can be resolved"
            )
        return read_trusted_scope(repo, pr_number, head_sha)
    # status / advance: CLI scope is rejected with an explicit
    # diagnostic. Lifecycle readiness for both commands comes from
    # the canonical trusted exact-head record, NOT from CLI patterns.
    if cli_allowed is not None or cli_forbidden is not None:
        return None, None, _STATUS_ADVANCE_CLI_SCOPE_REJECTION
    if not head_sha or not R.is_canonical_head_sha(head_sha):
        # No head_sha means status/advance is inspecting a PR whose
        # head we have not yet fetched (defensive). The trusted file
        # is keyed by head SHA, so we cannot read it. Fall through to
        # None so the scope gate fails closed; cmd_status will refetch
        # the head and the next status call will succeed if a scope
        # record exists for that head.
        return None, None, (
            "live head SHA required to read the trusted scope file"
        )
    return read_trusted_scope(repo, pr_number, head_sha)


def build_evidence(
    *,
    repo: str,
    pr_number: int,
    pr_view: Dict[str, Any],
    changed_files: List[str],
    changed_files_fetched: bool,
    changed_files_error: str,
    authorization_phrase: Optional[str],
    allowed_files: Optional[List[str]] = None,
    forbidden_files: Optional[List[str]] = None,
) -> R.ReadinessEvidence:
    """Build a ReadinessEvidence bundle for the current PR view.

    Scope policy (``allowed_files`` / ``forbidden_files``) is supplied
    by the caller (the operator via CLI flags, or the task-scope
    mechanism). The controller does not embed any default controller-
    only scope patterns. (Round-2 Codex finding: do not hardcode a
    controller-only path allowlist.)
    """
    head_sha = pr_view.get("headRefOid")
    head_branch = pr_view.get("headRefName")

    # ---- Scope check ---------------------------------------------------------
    # The scope check is allowed ONLY when the operator supplied an
    # explicit allowed_files list. When the operator supplied neither
    # allowed_files nor forbidden_files, the gate fails closed with
    # scope_clean=None (REASON_SCOPE_UNKNOWN).
    allowed, forbidden = _coerce_scope_inputs(allowed_files, forbidden_files)
    if changed_files_fetched and allowed:
        scope_packet = SCOPE.check_scope(changed_files, allowed, forbidden)
        scope_clean = bool(scope_packet.get("passed"))
        out_of_scope = list(scope_packet.get("out_of_scope_files") or [])
        forbidden_touched = list(scope_packet.get("forbidden_files_touched") or [])
        scope_blockers = list(scope_packet.get("blockers") or [])
    elif changed_files_fetched and not allowed:
        # Changed files were fetched, but the operator supplied no scope.
        # The gate fails closed - the controller MUST NOT silently fall
        # back to "no scope means clean".
        scope_clean = None
        out_of_scope = []
        forbidden_touched = []
        scope_blockers = ["scope_not_supplied"]
    else:
        scope_clean = None
        out_of_scope = []
        forbidden_touched = []
        scope_blockers = ["changed_files_not_fetched"]

    # ---- CI audit ------------------------------------------------------------
    (
        ci_ok, ci_conclusions, ci_missing, ci_pending, ci_failed,
        ci_duplicated, ci_err,
    ) = fetch_ci_conclusions(
        repo, int(pr_number), list(REQUIRED_CHECK_NAMES),
        head_sha=str(head_sha) if head_sha else None,
        head_branch=str(head_branch) if head_branch else None,
    )
    # Round-6 follow-up: required-check duplicates block the gate.
    ci_duplicated_required = (
        [n for n in (ci_duplicated or [])
         if n in set(REQUIRED_CHECK_NAMES)]
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
    # The scope-check source is "fetched" only when the operator supplied
    # an explicit allowed_files list AND changed files were fetched. If
    # the operator did not supply scope, the source is reported as
    # "skipped:scope_not_supplied" so the strict-evidence gate (12) sees
    # the failure as a real missing evidence rather than treating
    # absent scope as clean.
    if changed_files_fetched and allowed:
        evidence_sources["scope_check"] = "fetched"
    elif changed_files_fetched and not allowed:
        evidence_sources["scope_check"] = "skipped:scope_not_supplied"
    else:
        evidence_sources["scope_check"] = "skipped:no_changed_files"
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
        allowed_files_supplied=bool(allowed),
        required_ci_names=list(REQUIRED_CHECK_NAMES),
        ci_conclusions=ci_conclusions,
        ci_missing=ci_missing,
        ci_pending=ci_pending,
        ci_failed=ci_failed,
        # Round-6 follow-up: required-check duplicates block the gate.
        ci_duplicated=list(ci_duplicated_required or []),
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
    machine gate has converged (verdict.machine_ready is True) and the
    only remaining requirement is the operator's exact authorization
    phrase (the verdict's sole failure reason is PHRASE_MISMATCH). This
    is the signal the operator uses to learn that speaking the phrase
    and running ``aed_pr merge`` is the next safe action.

    The status command should always be able to reach
    READY_FOR_MERGE_AUTHORIZATION on a fully-green PR without an
    authorization phrase; this function consults ``machine_ready``
    rather than ``ready`` so the canonical authorization phrase can be
    emitted in the status output.
    """
    if pr_view.get("state") == "MERGED":
        return "MERGED_PENDING_CLOSEOUT"
    if pr_view.get("state") == "CLOSED":
        return "COMPLETE"
    # Round-2 fix: machine readiness is the canonical signal for the
    # operator-facing lifecycle state. ``verdict.ready`` mirrors
    # ``merge_ready`` (machine AND authorization), which can never be
    # True when status ran without a supplied phrase; using
    # ``verdict.machine_ready`` lets the status command report
    # READY_FOR_MERGE_AUTHORIZATION on a fully-green PR.
    if not verdict.machine_ready:
        codes = {r.code for r in verdict.reasons}
        human_codes = {
            R.REASON_PR_IS_DRAFT,
            R.REASON_UNRESOLVED_THREAD,
        }
        if codes & human_codes:
            return "ACTION_REQUIRED"
        return "BLOCKED"
    # machine_ready is True. Now consider whether the authorization
    # phrase has been supplied and is valid. When no phrase was
    # supplied (the status path), authorization_valid is None and
    # authorization_required is True - this is the
    # READY_FOR_MERGE_AUTHORIZATION signal.
    if verdict.authorization_valid is False:
        # Phrase was supplied but did not match the canonical phrase.
        # This is a transient state where the operator must re-speak
        # the phrase; surface it as BLOCKED so the operator notices
        # the mismatch.
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


def _parse_scope_arg(value: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated scope argument into a list of paths.

    ``None`` and the empty string both return ``None`` so the
    controller does not invent a scope constraint. A non-empty comma-
    separated string returns a list of trimmed paths (empty entries
    dropped). The controller never falls back to a default controller-
    only allowlist (round-2 fix).
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",")]
    parts = [p for p in parts if p]
    return parts if parts else None


# -----------------------------------------------------------------------------
# status command
# -----------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    repo = args.repo
    pr_number = args.pr_number
    cli_allowed = _parse_scope_arg(args.allowed_files)
    cli_forbidden = _parse_scope_arg(args.forbidden_files)
    cli_override = cli_allowed is not None or cli_forbidden is not None
    # Fetch the PR view first so the trusted scope can be keyed by
    # the live head SHA. The trusted scope file path requires all
    # three: repo, pr_number, head_sha.
    pr_view = fetch_pr_state(repo, pr_number)
    head_sha = pr_view.get("headRefOid")
    allowed_files, forbidden_files, scope_err = _resolve_effective_scope(
        subcommand="status",
        repo=repo,
        pr_number=pr_number,
        head_sha=str(head_sha) if head_sha else None,
        cli_allowed=cli_allowed,
        cli_forbidden=cli_forbidden,
    )

    ok_changed, changed_files, changed_err = fetch_changed_files(repo, pr_number, pr_view)

    evidence = build_evidence(
        repo=repo,
        pr_number=pr_number,
        pr_view=pr_view,
        changed_files=changed_files,
        changed_files_fetched=ok_changed,
        changed_files_error=changed_err,
        authorization_phrase=None,
        allowed_files=allowed_files,
        forbidden_files=forbidden_files,
    )
    verdict = R.evaluate_machine_readiness(evidence)
    state = derive_lifecycle_state(verdict, pr_view)
    safe_cmd = L.build_safe_merge_command(pr_number, repo, head_sha)
    # The canonical phrase is emitted ONLY when machine readiness
    # converged on the live head from the canonical trusted scope.
    # It is the operator's job to speak it back to ``aed_pr merge``;
    # the controller does not invent or pre-supply a phrase.
    canonical_phrase = (
        L.build_authorization_phrase(pr_number, str(head_sha))
        if verdict.machine_ready and R.is_canonical_head_sha(head_sha) else None
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
        "scope_allowed_files_supplied": bool(allowed_files),
        "scope_source": (
            "cli_override" if cli_override else "trusted_file"
        ),
        "out_of_scope_files": evidence.out_of_scope_files,
        "forbidden_files_touched": evidence.forbidden_files_touched,
        "scope_error": scope_err or None,
        "required_ci_names": list(REQUIRED_CHECK_NAMES),
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
        # Round-2 split: machine readiness vs authorization.
        "machine_ready": verdict.machine_ready,
        "authorization_required": verdict.authorization_required,
        "authorization_valid": verdict.authorization_valid,
        "merge_ready": verdict.merge_ready,
        # Backward-compatible alias.
        "ready": verdict.merge_ready,
        "gates_passed": verdict.gates_passed,
        "gates_failed": verdict.gates_failed,
        "reason_codes": [r.code for r in verdict.reasons],
        "reasons": [r.to_dict() for r in verdict.reasons],
        "safe_merge_command_preview": safe_cmd,
        "required_authorization_phrase": canonical_phrase,
        # Round-16 fix: when the scope resolver rejected CLI flags,
        # surface the explicit diagnostic as the next-action hint
        # so the operator sees the scope-write instruction.
        "next_human_action": (
            scope_err if scope_err else _next_human_action(state)
        ),
    }

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


# -----------------------------------------------------------------------------
# advance command (real behavior; never invokes gh pr merge)
# -----------------------------------------------------------------------------

def _post_codex_ping_comment(
    repo: str, pr_number: int, head_sha: str,
    *,
    runner: Optional[Any] = None,
) -> Tuple[bool, str]:
    """Post a Codex-review ping on the current head SHA.

    ``runner`` is an injectable subprocess.run replacement for
    tests. When ``None`` (default), ``_run_json_or_none`` is used,
    which in turn uses ``subprocess.run``.

    Round-9 follow-up (Codex comment 3609867541 on ``62f20b6``):
    the previous implementation posted a body that did NOT contain
    ``@codex review``. The PR-gate classifier and the governance
    rules only recognize ``@codex review`` comments as the
    actual Codex trigger, so the previous body never counted.

    The new body is::

        @codex review

        AED exact-head review request: <40-character-head-sha>

    The body is wrapped through ``shlex.quote`` so the SHA is
    safely passed as a single ``-f body=...`` argument to ``gh``.

    Duplicate-request prevention: an existing comment counts as
    a duplicate ONLY when it contains BOTH ``@codex review`` AND
    the exact 40-character head SHA. A legacy comment with the
    SHA but no ``@codex review`` does NOT suppress the new
    trigger, and a comment for a different head does NOT
    suppress the current one.

    The function returns ``(ok, info_string)``; ``info_string``
    is one of:

    - a comment database id (``"1234567890"``) when posted;
    - ``"duplicate_exact_head_request_prevented"`` when an exact
      duplicate exists;
    - ``"comment_inventory_failed"`` when the inventory could not
      be fetched;
    - ``"post_failed: <reason>"`` when the POST failed.

    The caller must report this ``info_string`` verbatim so the
    action report distinguishes posted, deduplicated and failed
    states.
    """
    # Validate the head SHA before any API call. Refuse on
    # malformed input so the action report cannot claim
    # ``requested`` for an invalid SHA.
    if not R.is_canonical_head_sha(head_sha):
        return False, "post_failed: malformed_head_sha"

    # Compose the comment body. The actual Codex trigger MUST
    # appear on its own line so the classifier matches it
    # verbatim; the SHA is included so the trigger can be tied
    # to one immutable head.
    body = (
        "@codex review\n\n"
        f"AED exact-head review request: {head_sha}"
    )

    def _run(cmd):
        if runner is not None:
            proc = runner(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                return False, None, (proc.stderr or "")
            try:
                return True, json.loads(proc.stdout or ""), ""
            except json.JSONDecodeError:
                return True, proc.stdout or "", ""
        return _run_json_or_none(cmd, timeout=60)

    # 1. Fetch every existing PR-level issue comment with
    # pagination. Fail closed on inventory failure.
    ok, payload, err = _run([
        "gh", "api",
        f"repos/{repo}/issues/{pr_number}/comments",
        "--paginate", "--slurp",
    ])
    if not ok or not isinstance(payload, list):
        return False, "comment_inventory_failed: " + (err or "")
    comments: List[Dict[str, Any]] = []
    for page in payload:
        if isinstance(page, list):
            comments.extend(page)
        elif isinstance(page, dict) and isinstance(page.get("items"), list):
            comments.extend(page["items"])
    # 2. Duplicate detection: the same exact head, AND the
    # ``@codex review`` trigger string. Anything else is ignored.
    for c in comments:
        if not isinstance(c, dict):
            continue
        existing_body = c.get("body") or ""
        if not isinstance(existing_body, str):
            continue
        if "@codex review" not in existing_body:
            continue
        if head_sha not in existing_body:
            continue
        return True, "duplicate_exact_head_request_prevented"

    # 3. Post the trigger comment.
    ok, payload, err = _run([
        "gh", "api", "-X", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={body}",
    ])
    if not ok or not isinstance(payload, dict):
        return False, "post_failed: " + (err or "")
    new_id = str(payload.get("id") or "")
    return True, new_id or "posted"


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


# -----------------------------------------------------------------------------
# Eligible-bot-thread resolution (round-2 fix #4)
# -----------------------------------------------------------------------------
#
# ``advance`` advertises the ability to resolve eligible outdated
# Codex-bot threads so the canonical operator flow does not stall on
# stale findings from a prior round. This module exposes the
# deterministic eligibility check and a mockable mutation path.
#
# Constraints:
#
# * Human-involved threads MUST NEVER reach the mutation call. The
#   mutation path requires an explicit eligible-thread IDs list.
# * Re-running advance MUST be idempotent: an already-resolved thread
#   is reported as ineligible on the next call and the mutation is
#   not invoked again.
# * The mutation itself is a separate ``gh api graphql`` call to
#   ``resolveReviewThread``. It is exposed as a helper so tests can
#   mock it; the controller NEVER invokes it on a thread that did
#   not pass the eligibility check.
#
# This repair cycle implements and tests the capability only; live
# invocation on PR #411 is explicitly deferred until later
# authorization.

def deduplicate_thread_records(
    threads: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], str]:
    """Group duplicate review-thread records by ``thread_id``.

    The audit packet built by ``audit_codex_response_for_pr`` emits
    one inventory entry per comment in a thread, while preserving
    the same ``thread_id``. ``select_eligible_bot_threads`` (and the
    downstream resolver) must therefore evaluate each unique
    thread at most once, regardless of how many comment records
    the packet carries.

    For each unique ``thread_id`` this function:

    - merges the ``comments`` list (deduplicated by ``database_id``
      or author, preserving first-seen order);
    - takes the **first** occurrence as the canonical record;
    - validates that all duplicates agree on
      ``is_outdated``/``is_resolved`` and on the canonical anchor
      (``original_commit_sha``/``comment_sha``/``head_sha``); when
      they disagree the function fails closed by returning
      ``([], "conflicting_duplicate_thread_records")``;
    - validates the participant list is non-empty;
    - fails closed when any duplicate record contains a non-bot
      author (a human reply somewhere in the thread blocks the
      whole thread);
    - skips records whose ``thread_id`` is missing (those are
      ineligible individually; the dedup function does not surface
      them as new canonical records).

    Returns ``(canonical_records, error)``. ``canonical_records`` is
    the deduplicated list, in deterministic first-seen order.
    ``error`` is non-empty when the function failed closed; the
    caller should treat the deduplication result as
    ineligible-with-reason ``conflicting_duplicate_thread_records``.
    """
    if not threads:
        return [], ""
    seen: Dict[str, Dict[str, Any]] = {}
    first_seen_order: List[str] = []
    for record in threads or []:
        if not isinstance(record, dict):
            continue
        tid = record.get("thread_id") or record.get("id") or ""
        if not isinstance(tid, str) or not tid:
            # Records without a thread_id are kept out of the
            # canonical set; the eligibility checker treats them
            # as ineligible individually. We still record that
            # the dedup saw a missing-id record so the caller can
            # detect "incomplete participant evidence" later.
            continue
        # Pull the per-record evidence we need to validate.
        rec_outdated = bool(
            record.get("isOutdated", record.get("is_outdated"))
        )
        rec_resolved = bool(
            record.get("isResolved", record.get("is_resolved"))
        )
        rec_anchor = R._extract_thread_anchor(record)
        rec_comments = record.get("comments") or []
        rec_top_author = (
            record.get("author") or record.get("author_login") or ""
        )
        if tid not in seen:
            seen[tid] = {
                "thread_id": tid,
                "first_record": dict(record),
                "anchors": [rec_anchor] if rec_anchor else [],
                "outdated": rec_outdated,
                "resolved": rec_resolved,
                "comments_merged": list(rec_comments),
                "comment_keys": set(),
                "top_authors": {rec_top_author} if rec_top_author else set(),
                "human_involvement": False,
            }
            for c in rec_comments:
                if isinstance(c, dict):
                    key = c.get("database_id") or (
                        c.get("author") or c.get("databaseId")
                    )
                    seen[tid]["comment_keys"].add(key)
            first_seen_order.append(tid)
            continue
        existing = seen[tid]
        # Validate agreement on state.
        if existing["outdated"] != rec_outdated:
            return [], "conflicting_duplicate_thread_records"
        if existing["resolved"] != rec_resolved:
            return [], "conflicting_duplicate_thread_records"
        # Validate agreement on anchor. Two non-empty anchors that
        # disagree are a hard conflict (failed closed).
        if rec_anchor:
            if existing["anchors"] and rec_anchor not in existing["anchors"]:
                return [], "conflicting_duplicate_thread_records"
            existing["anchors"].append(rec_anchor)
        # Validate participant list non-empty.
        if not rec_comments:
            return [], "conflicting_duplicate_thread_records"
        # Validate top-level author agreement.
        if rec_top_author:
            existing["top_authors"].add(rec_top_author)
        if len(existing["top_authors"]) > 1:
            return [], "conflicting_duplicate_thread_records"
        # Merge comments (dedupe by database_id or author+body).
        for c in rec_comments:
            if not isinstance(c, dict):
                continue
            key = c.get("database_id") or (
                c.get("author") or c.get("databaseId")
            )
            if key in existing["comment_keys"]:
                continue
            existing["comment_keys"].add(key)
            existing["comments_merged"].append(c)
            author = c.get("author") or c.get("author_login") or ""
            if author and not _is_known_bot_login(author):
                existing["human_involvement"] = True
    # Build canonical records.
    canonical: List[Dict[str, Any]] = []
    for tid in first_seen_order:
        entry = seen[tid]
        if entry["human_involvement"]:
            # Human involvement present in any duplicate record
            # blocks the whole thread; do not surface it as a
            # canonical record eligible for resolution.
            continue
        # Build the canonical record from the first occurrence,
        # with the merged comments list and the most specific
        # canonical anchor.
        canonical_record = dict(entry["first_record"])
        canonical_record["comments"] = entry["comments_merged"]
        # Pick a single canonical anchor (first non-empty).
        anchor = next(
            (a for a in entry["anchors"] if a), None
        )
        if anchor:
            canonical_record["original_commit_sha"] = anchor
            canonical_record["comment_sha"] = anchor
        canonical.append(canonical_record)
    return canonical, ""


def _is_known_bot_login(login: str) -> bool:
    if not isinstance(login, str):
        return False
    return login.lower() in R._RECOGNIZED_BOT_LOGINS


def select_eligible_bot_threads(
    threads: List[Dict[str, Any]],
    *,
    head_sha: Optional[str],
    codex_verdict: Optional[str],
    codex_clean_passed: Optional[bool],
    codex_reviewed_sha: Optional[str] = None,
    repo: Optional[str] = None,
    ancestry_runner: Optional[Any] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Run the eligibility checker over a thread inventory.

    Returns ``{"eligible": [...], "ineligible": [...]}``. Every entry
    in ``eligible`` carries a ``reason`` field set to ``"eligible"``
    and the deterministic ``thread_id`` recorded for the action
    report. Every entry in ``ineligible`` carries a ``reason`` field
    explaining why the thread was refused.

    Round-4 fix #2: each thread is normalized before the eligibility
    check so the canonical commit anchor is populated where the live
    GitHub packet supplied one. Threads without a canonical anchor
    after normalization are ineligible with reason
    ``missing_commit_anchor`` (or ``malformed_commit_anchor``).
    """
    eligible: List[Dict[str, Any]] = []
    ineligible: List[Dict[str, Any]] = []
    for raw_thread in threads or []:
        thread = R.normalize_thread_anchor(raw_thread)
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            head_sha=head_sha,
            codex_verdict=codex_verdict,
            codex_clean_passed=codex_clean_passed,
            codex_reviewed_sha=codex_reviewed_sha,
            repo=repo,
            ancestry_runner=ancestry_runner,
        )
        annotated = dict(thread)
        annotated["reason"] = reason
        thread_id = str(
            thread.get("thread_id")
            or thread.get("id")
            or thread.get("databaseId")
            or ""
        )
        annotated["thread_id"] = thread_id
        if ok:
            eligible.append(annotated)
        else:
            ineligible.append(annotated)
    return {"eligible": eligible, "ineligible": ineligible}


def resolve_review_thread(
    repo: str, thread_id: str, *, runner: Optional[Any] = None
) -> Tuple[bool, str]:
    """Resolve a single review thread via the GitHub GraphQL API.

    ``runner`` is an injectable subprocess.run replacement so tests can
    assert the argv shape without spawning a real gh. The default
    runner is ``subprocess.run``.

    This function is intentionally minimal: it accepts ONE eligible
    thread ID, builds the exact GraphQL mutation, and returns
    ``(ok, error_msg)``. It is the single mutation entry point and
    MUST be the only place ``gh api graphql`` is invoked for thread
    resolution. Calling it with a thread ID that did not pass
    ``is_eligible_for_bot_resolution`` would still work, but the
    controller's advance path always checks eligibility first.

    Round-6 follow-up (Codex comment ``3609075638`` on
    ``c229be82``): the previous implementation interpolated the
    thread ID into a top-level ``resolveReviewThread(threadId: ...)``
    GraphQL argument. GitHub's schema requires the thread ID to be
    supplied as the ``threadId`` field of a ``ResolveReviewThreadInput``
    object passed to the ``input`` argument. The original fix used
    ``--input`` with an inline JSON string; that was incorrect
    because the ``gh`` ``--input`` option treats its argument as a
    filename. The current implementation uses the supported
    nested-variable form via repeated ``-f`` fields:

        gh api graphql \
          -f 'query=mutation($input: ResolveReviewThreadInput!) { resolveReviewThread(input: $input) { thread { id isResolved } } }' \
          -f 'input[threadId]=<GRAPHQL_THREAD_ID>'

    The thread ID is supplied as data via ``-f 'input[threadId]=...'``
    so it is never embedded in the query text and the runner does
    not need to read from disk.
    """
    if not isinstance(thread_id, str) or not thread_id:
        return False, "thread_id required"
    if "/" not in repo:
        return False, "repo must be in 'owner/name' form"
    # The thread ID must be supplied as data, not interpolated into
    # the query. The ``gh`` ``-f`` flag accepts ``KEY=VALUE`` pairs
    # and supports nested-object syntax via ``KEY[subkey]=VALUE``,
    # which becomes ``{"KEY": {"subkey": "VALUE"}}`` in the request
    # body.
    query = (
        "mutation($input: ResolveReviewThreadInput!) {"
        " resolveReviewThread(input: $input) {"
        " thread { id isResolved }"
        " }"
        " }"
    )
    cmd = [
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-f", f"input[threadId]={thread_id}",
    ]
    run = runner if runner is not None else subprocess.run
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"gh graphql invocation failed: {exc}"
    if proc.returncode != 0:
        return False, (
            f"gh graphql returned {proc.returncode}: "
            f"{proc.stderr.strip()[:300]}"
        )
    # Validate the response payload: GitHub returns
    # ``{"data": {"resolveReviewThread": {"thread": {"id": ..., "isResolved": true/false}}}}``
    # for success, or ``{"errors": [...]}`` for failure.
    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return False, f"invalid GraphQL response: {exc}"
    if not isinstance(body, dict):
        return False, "GraphQL response is not a JSON object"
    if body.get("errors"):
        return False, (
            "GraphQL errors: "
            + json.dumps(body.get("errors"))[:300]
        )
    data_obj = body.get("data")
    if not isinstance(data_obj, dict):
        return False, "GraphQL response missing data object"
    payload_obj = data_obj.get("resolveReviewThread")
    if not isinstance(payload_obj, dict):
        return False, "GraphQL response missing resolveReviewThread"
    thread_obj = payload_obj.get("thread")
    if not isinstance(thread_obj, dict):
        return False, (
            "GraphQL response missing resolveReviewThread.thread"
        )
    if not thread_obj.get("isResolved"):
        return False, "GraphQL response thread is not resolved"
    # Validate that the returned thread ID matches the request.
    # GitHub always echoes the thread's node ID; a mismatch would
    # indicate the resolver acted on a different thread than
    # requested, so we refuse the result.
    returned_id = thread_obj.get("id")
    if returned_id is not None and returned_id != thread_id:
        return False, (
            "GraphQL response thread.id does not match requested "
            f"thread_id (requested={thread_id!r} got={returned_id!r})"
        )
    return True, "resolved"


def cmd_advance(args: argparse.Namespace) -> int:
    """Perform every safe mechanical lifecycle step except the merge."""
    repo = args.repo
    pr_number = args.pr_number
    cli_allowed = _parse_scope_arg(args.allowed_files)
    cli_forbidden = _parse_scope_arg(args.forbidden_files)
    cli_override = cli_allowed is not None or cli_forbidden is not None
    # Fetch the PR view first so the trusted scope can be keyed by
    # the live head SHA.
    pr_view = fetch_pr_state(repo, pr_number)
    head_sha = pr_view.get("headRefOid")
    allowed_files, forbidden_files, scope_err = _resolve_effective_scope(
        subcommand="advance",
        repo=repo,
        pr_number=pr_number,
        head_sha=str(head_sha) if head_sha else None,
        cli_allowed=cli_allowed,
        cli_forbidden=cli_forbidden,
    )
    ok_changed, changed_files, changed_err = fetch_changed_files(repo, pr_number, pr_view)

    evidence = build_evidence(
        repo=repo,
        pr_number=pr_number,
        pr_view=pr_view,
        changed_files=changed_files,
        changed_files_fetched=ok_changed,
        changed_files_error=changed_err,
        authorization_phrase=None,
        allowed_files=allowed_files,
        forbidden_files=forbidden_files,
    )
    machine_verdict = R.evaluate_machine_readiness(evidence)
    state = derive_lifecycle_state(machine_verdict, pr_view)

    actions_taken: List[Dict[str, Any]] = []
    # Round-16 fix: when the scope resolver rejected CLI flags, the
    # entire advance pipeline is fail-closed. We emit the diagnostic,
    # skip every mutation (codex-ping, thread resolution, ready
    # mark, workflow dispatch, scope-write, gh pr merge) and return.
    # The eligibility classifier is also skipped because machine
    # readiness is False by construction (``scope_clean=None``),
    # so no per-thread resolution can be authorized.
    #
    # Only the CLI-scope rejection is treated as a hard short-circuit
    # here. The "trusted scope not found" / "live head SHA required"
    # diagnostics flow through the existing path so existing tests
    # that observe the eligibility classifier running on those inputs
    # remain valid.
    if scope_err and "cli_scope_not_authoritative" in scope_err:
        actions_taken.append({
            "action": "cli_scope_rejected",
            "ok": False,
            "result": "cli_scope_not_authoritative",
            "error": scope_err,
        })
        out = {
            "tool": "aed_pr.advance",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "lifecycle_state": state,
            "scope_source": (
                "cli_override" if cli_override else "trusted_file"
            ),
            "scope_error": scope_err,
            "machine_ready": machine_verdict.machine_ready,
            "authorization_required": machine_verdict.authorization_required,
            "authorization_valid": machine_verdict.authorization_valid,
            "merge_ready": machine_verdict.merge_ready,
            "ready": machine_verdict.merge_ready,
            "reason_codes": [r.code for r in machine_verdict.reasons],
            "reasons": [r.to_dict() for r in machine_verdict.reasons],
            "actions_taken": actions_taken,
            "safe_merge_command_if_ready": None,
            "required_authorization_phrase_if_ready": None,
            "next_human_action": scope_err,
        }
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    # Step 1: classify every thread as eligible or ineligible. The
    # eligibility check is deterministic (R.is_eligible_for_bot_resolution)
    # and operates on the current inventory snapshot; it does NOT
    # require machine readiness. The classification is reported
    # unconditionally so dry-run, no-mutation, and mutation paths
    # all surface the same eligibility picture.
    #
    # Round-4 fix #2: each thread is normalized first so the
    # canonical commit anchor (``original_commit_sha``) is populated
    # where the live GitHub packet supplied it. Threads that lack a
    # canonical anchor after normalization are flagged
    # ``missing_commit_anchor`` and reported in the eligibility
    # record without triggering a mutation.
    raw_thread_inventory = (
        list(evidence.review_threads or [])
        if evidence.review_thread_inventory_complete
        else []
    )
    normalized_thread_inventory = [
        R.normalize_thread_anchor(t) for t in raw_thread_inventory
    ]
    # Round-5 fix: deduplicate thread records before eligibility
    # classification. The audit packet emits one entry per comment,
    # so a thread with N comments produces N records sharing one
    # thread_id. Without dedup the eligibility loop would evaluate
    # the same thread N times and the resolver would call
    # resolveReviewThread N times for one thread.
    deduplicated_inventory, dedup_err = deduplicate_thread_records(
        normalized_thread_inventory
    )
    if dedup_err:
        # Fail closed: surface the dedup failure in the action
        # report and proceed with an empty inventory (every thread
        # is blocked). The eligibility loop below cannot run with
        # a conflicting duplicate record set.
        actions_taken.append({
            "action": "thread_deduplication_report",
            "ok": False,
            "result": "deduplication_failed_closed",
            "error": dedup_err,
        })
        actions_taken.append({
            "action": "thread_eligibility_report",
            "eligible_count": 0,
            "ineligible_count": 0,
            "eligible_thread_ids": [],
            "ineligible_thread_ids": [],
            "ineligible_details": [],
        })
        if not args.dry_run:
            actions_taken.append({
                "action": "dry_run",
                "result": "skipped_all_mutations",
            })
        elif args.dry_run:
            actions_taken.append({
                "action": "dry_run",
                "result": "skipped_all_mutations",
            })
        # Emit the report and return.
        canonical_phrase = (
            L.build_authorization_phrase(pr_number, str(head_sha))
            if machine_verdict.machine_ready
            and R.is_canonical_head_sha(head_sha)
            else None
        )
        out = {
            "tool": "aed_pr.advance",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "lifecycle_state": state,
            "scope_source": (
                "cli_override" if cli_override else "trusted_file"
            ),
            "scope_error": scope_err or None,
            "machine_ready": machine_verdict.machine_ready,
            "authorization_required": machine_verdict.authorization_required,
            "authorization_valid": machine_verdict.authorization_valid,
            "merge_ready": machine_verdict.merge_ready,
            "ready": machine_verdict.merge_ready,
            "reason_codes": [r.code for r in machine_verdict.reasons],
            "reasons": [r.to_dict() for r in machine_verdict.reasons],
            "actions_taken": actions_taken,
            "safe_merge_command_if_ready": (
                L.build_safe_merge_command(pr_number, repo, head_sha)
                if machine_verdict.machine_ready else None
            ),
            "required_authorization_phrase_if_ready": canonical_phrase,
            "next_human_action": _next_human_action(state),
        }
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    actions_taken.append({
        "action": "thread_deduplication_report",
        "ok": True,
        "input_count": len(normalized_thread_inventory),
        "output_count": len(deduplicated_inventory),
    })
    eligibility = select_eligible_bot_threads(
        deduplicated_inventory,
        head_sha=str(head_sha) if head_sha else None,
        codex_verdict=evidence.codex_verdict,
        codex_clean_passed=(
            True if evidence.codex_clean_passed is True else None
        ),
        codex_reviewed_sha=evidence.codex_reviewed_sha,
        repo=repo,
        ancestry_runner=getattr(args, "ancestry_runner", None),
    )
    eligible_thread_records = [
        {
            "thread_id": str(t.get("thread_id") or ""),
            "reason": t.get("reason", ""),
        }
        for t in eligibility["eligible"]
    ]
    ineligible_thread_records = [
        {
            "thread_id": str(t.get("thread_id") or ""),
            "reason": t.get("reason", ""),
        }
        for t in eligibility["ineligible"]
    ]
    actions_taken.append({
        "action": "thread_eligibility_report",
        "eligible_count": len(eligible_thread_records),
        "ineligible_count": len(ineligible_thread_records),
        "eligible_thread_ids": [
            r["thread_id"] for r in eligible_thread_records
        ],
        "ineligible_thread_ids": [
            r["thread_id"] for r in ineligible_thread_records
        ],
        "ineligible_details": ineligible_thread_records,
    })

    # Step 2: Codex-review ping per exact head. Failure to fetch the
    # existing comment inventory prevents posting a potentially
    # duplicate ping - it must NOT fabricate a successful ping or
    # alter unrelated machine gates. Report explicitly under
    # codex_ping_inventory_unavailable.
    #
    # Round-4 fix #3: ``--dry-run`` must perform ZERO write operations.
    # The ping block is gated on ``not args.dry_run``; in dry-run
    # mode the controller records what *would* have happened without
    # invoking any mutation.
    would_post_codex_ping = (
        pr_view.get("state") == "OPEN"
        and R.is_canonical_head_sha(head_sha)
    )
    # Round-21 fix: a freshly posted ``@codex review`` ping must
    # suppress the canonical authorization phrase for this run.
    # The classifier was called BEFORE the ping, so a stale
    # ``codex_clean_passed=True`` evidence is no longer
    # authoritative once a new review is in flight. Without this
    # guard the report can advertise
    # ``required_authorization_phrase_if_ready`` while the new
    # review is still pending, allowing ``merge_ready`` to flip
    # True on the back of an in-flight review.
    fresh_codex_ping_posted = False
    if would_post_codex_ping and not args.dry_run:
        ok_ping, ping_result = _post_codex_ping_comment(
            repo, pr_number, str(head_sha) if head_sha else ""
        )
        # The new ping contract reports specific reason codes so
        # the action report can distinguish posted, deduplicated
        # and failed states without ambiguity.
        if ping_result.startswith("comment_inventory_failed"):
            actions_taken.append({
                "action": "codex_review_ping",
                "attempted": False,
                "ok": False,
                "result": ping_result,
                "codex_ping_inventory_unavailable": True,
            })
        elif ping_result == "duplicate_exact_head_request_prevented":
            actions_taken.append({
                "action": "codex_review_ping",
                "attempted": False,
                "ok": True,
                "result": ping_result,
                "duplicate_exact_head_request_prevented": True,
            })
        elif ping_result.startswith("post_failed"):
            actions_taken.append({
                "action": "codex_review_ping",
                "attempted": True,
                "ok": False,
                "result": ping_result,
            })
        else:
            actions_taken.append({
                "action": "codex_review_ping",
                "attempted": True,
                "ok": ok_ping,
                "result": ping_result,
            })
            # A non-empty non-duplicate result means a fresh
            # trigger was posted in THIS run. The existing
            # evidence is therefore stale for authorization.
            fresh_codex_ping_posted = True
    elif would_post_codex_ping and args.dry_run:
        actions_taken.append({
            "action": "codex_review_ping",
            "attempted": False,
            "reason": "dry_run",
            "would_post": True,
        })
    elif pr_view.get("state") == "OPEN" and R.is_canonical_head_sha(head_sha):
        # unreachable; defensive - would_post_codex_ping captures this
        pass

    if args.dry_run:
        actions_taken.append({
            "action": "dry_run",
            "result": "skipped_all_mutations",
        })
        actions_taken.append({
            "action": "mark_pr_ready",
            "attempted": False,
            "reason": "dry_run",
            "would_attempt": pr_view.get("isDraft") is True,
        })
        actions_taken.append({
            "action": "resolve_eligible_bot_threads",
            "attempted": False,
            "reason": "dry_run",
            "eligible_thread_ids": [
                r["thread_id"] for r in eligible_thread_records
            ],
            "ineligible_threads": ineligible_thread_records,
        })
        actions_taken.append({
            "action": "workflow_dispatch",
            "attempted": False,
            "reason": "dry_run",
            "would_attempt": False,
        })
        actions_taken.append({
            "action": "post_pr_comment",
            "attempted": False,
            "reason": "dry_run",
            "would_attempt": False,
        })
        actions_taken.append({
            "action": "write_trusted_scope",
            "attempted": False,
            "reason": "dry_run",
            "would_attempt": False,
        })
        actions_taken.append({
            "action": "gh_pr_merge",
            "attempted": False,
            "reason": "dry_run",
            "would_attempt": False,
        })
    else:
        # 1. Draft-to-ready only after the prerequisite gates are green.
        # This step is intentionally separate from the F4 mutation;
        # ``mark_pr_ready`` requires zero unresolved threads which
        # may not be achievable until eligible threads are resolved.
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
                "gates_blocking": machine_verdict.gates_failed,
            })

        # 3. Eligible-bot-thread resolution (round-3 fix #4).
        #
        # The mutation path is NOT gated on machine readiness.
        # Otherwise the control flow is circular: an eligible
        # outdated unresolved Codex thread contributes to
        # unresolved_thread_count, which makes machine readiness
        # false, which would forbid the resolution that is supposed
        # to clear that exact unresolved thread.
        #
        # The correct order is:
        #   a) classify eligibility from the current snapshot
        #   b) attempt resolutions for every eligible thread
        #   c) refetch the live review-thread inventory
        #   d) rebuild evidence from the refreshed inventory
        #   e) recompute machine readiness (now without the
        #      eligible threads, which are either resolved or
        #      recorded as failures)
        #
        # ``cmd_merge`` re-fetches everything immediately before the
        # squash merge so it sees the post-resolution inventory and
        # refuses merge if anything is still unresolved.
        if getattr(args, "resolve_eligible_bot_threads", False):
            if not R.is_canonical_head_sha(head_sha):
                actions_taken.append({
                    "action": "resolve_eligible_bot_threads",
                    "attempted": False,
                    "reason": "head_sha_not_canonical",
                    "ok": False,
                })
            else:
                resolution_results: List[Dict[str, Any]] = []
                any_failed = False
                resolved_thread_ids: List[str] = []
                failed_thread_ids: List[str] = []
                attempted = True
                for record in eligible_thread_records:
                    tid = record["thread_id"]
                    if not tid:
                        continue
                    ok_resolve, msg = resolve_review_thread(repo, tid)
                    if ok_resolve:
                        resolved_thread_ids.append(tid)
                    else:
                        # Round-4 fix #4: a failed resolution must
                        # mark ``any_failed`` as True so the action
                        # record reports ``ok=False`` and ``result``
                        # is "partial_or_failed" rather than falsely
                        # claiming "resolved".
                        any_failed = True
                        failed_thread_ids.append(tid)
                    resolution_results.append({
                        "thread_id": tid,
                        "ok": ok_resolve,
                        "result": msg,
                    })
                # Refetch the live review-thread inventory and rebuild
                # evidence so machine readiness reflects the post-
                # resolution state. The mutation outcome is recorded
                # honestly: any_failed=True means at least one mutation
                # failed; the controller does NOT falsely mark
                # ``ok=True``.
                #
                # Round-18 fix: ``build_evidence`` already calls
                # ``fetch_codex_packet`` exactly once and populates
                # every Codex/thread field from that single packet.
                # The previous implementation performed a redundant
                # ``fetch_codex_packet`` here AND a hidden second
                # fetch inside ``build_evidence``, then overwrote
                # ``review_threads`` on the resulting evidence with
                # a snapshot taken from the first packet while the
                # partition fields came from the second packet.
                # That mixing allowed a still-unresolved refreshed
                # thread to be reported as machine-ready if the
                # second packet's partition happened to be empty.
                # The repair: call ``build_evidence`` exactly once
                # and let it produce a coherent post-resolution
                # snapshot.
                refreshed_evidence: Optional[R.ReadinessEvidence] = None
                refreshed_machine_verdict: Optional[R.ReadinessVerdict] = None
                try:
                    refreshed_evidence = build_evidence(
                        repo=repo,
                        pr_number=pr_number,
                        pr_view=pr_view,
                        changed_files=(
                            list(evidence.changed_files)
                            if evidence.changed_files is not None
                            else []
                        ),
                        changed_files_fetched=evidence.changed_files_fetched,
                        changed_files_error="",
                        authorization_phrase=None,
                        allowed_files=allowed_files,
                        forbidden_files=forbidden_files,
                    )
                    refreshed_machine_verdict = (
                        R.evaluate_machine_readiness(refreshed_evidence)
                    )
                except Exception as exc:
                    actions_taken.append({
                        "action": "resolve_eligible_bot_threads",
                        "attempted": attempted,
                        "ok": False,
                        "result": "post_resolution_refresh_failed",
                        "error": repr(exc),
                        "thread_resolutions": resolution_results,
                    })
                actions_taken.append({
                    "action": "resolve_eligible_bot_threads",
                    "attempted": attempted,
                    "ok": (
                        len(resolution_results) > 0
                        and not any_failed
                        and refreshed_machine_verdict is not None
                    ),
                    "result": (
                        "resolved" if (
                            len(resolution_results) > 0
                            and not any_failed
                        ) else "partial_or_failed"
                    ),
                    "resolved_thread_ids": resolved_thread_ids,
                    "failed_thread_ids": failed_thread_ids,
                    "thread_resolutions": resolution_results,
                    "refreshed_machine_ready": (
                        refreshed_machine_verdict.machine_ready
                        if refreshed_machine_verdict is not None
                        else None
                    ),
                })
                if refreshed_machine_verdict is not None:
                    # Round-18 fix: replace the pre-resolution
                    # ``evidence`` and ``machine_verdict`` with
                    # the coherent refreshed snapshot so every
                    # later use (including the report fields and
                    # canonical authorization phrase) reflects the
                    # post-resolution state. The previous
                    # implementation only updated
                    # ``machine_verdict`` here, leaving the old
                    # ``evidence`` reachable for any code path
                    # that consulted it before the report was
                    # assembled.
                    evidence = refreshed_evidence
                    machine_verdict = refreshed_machine_verdict
                    state = derive_lifecycle_state(machine_verdict, pr_view)
        else:
            actions_taken.append({
                "action": "resolve_eligible_bot_threads",
                "attempted": False,
                "reason": "mutation_flag_not_supplied",
                "eligible_thread_ids": [
                    r["thread_id"] for r in eligible_thread_records
                ],
                "ineligible_threads": ineligible_thread_records,
            })

    canonical_phrase = (
        L.build_authorization_phrase(pr_number, str(head_sha))
        if (
            machine_verdict.machine_ready
            and R.is_canonical_head_sha(head_sha)
            and not fresh_codex_ping_posted
        ) else None
    )

    # Round-21 fix: a fresh ``@codex review`` ping posted in
    # THIS run invalidates the pre-ping ``codex_clean_passed``
    # evidence. ``merge_ready`` and ``safe_merge_command_if_ready``
    # must be suppressed until the new review lands so an
    # operator cannot advertise merge authorization on the back
    # of a stale clean pass.
    if fresh_codex_ping_posted:
        # Re-derive the readiness fields the operator-facing
        # report exposes so they cannot advertise authorization
        # while a new Codex review is in flight.
        effective_machine_ready = False
        effective_merge_ready = False
    else:
        effective_machine_ready = machine_verdict.machine_ready
        effective_merge_ready = machine_verdict.merge_ready

    out: Dict[str, Any] = {
        "tool": "aed_pr.advance",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "lifecycle_state": state,
        "scope_source": (
            "cli_override" if cli_override else "trusted_file"
        ),
        "scope_error": scope_err or None,
        # Round-2 split.
        "machine_ready": effective_machine_ready,
        "authorization_required": machine_verdict.authorization_required,
        "authorization_valid": machine_verdict.authorization_valid,
        "merge_ready": effective_merge_ready,
        "ready": effective_merge_ready,
        "reason_codes": [r.code for r in machine_verdict.reasons],
        "reasons": [r.to_dict() for r in machine_verdict.reasons],
        "actions_taken": actions_taken,
        "safe_merge_command_if_ready": (
            L.build_safe_merge_command(pr_number, repo, head_sha)
            if effective_machine_ready else None
        ),
        "required_authorization_phrase_if_ready": canonical_phrase,
        "next_human_action": _next_human_action(state),
        "fresh_codex_ping_posted": fresh_codex_ping_posted,
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
    exits non-zero and does NOT call ``gh pr merge``. The merge
    command is the single command that evaluates both machine
    readiness AND authorization, in that order:

      1. Validate the supplied authorization phrase byte-exactly
         against the canonical phrase for the live head.
      2. Re-fetch live evidence, build the evidence bundle, and call
         ``R.evaluate_readiness`` (which evaluates both machine gates
         and the supplied phrase).
      3. If ``verdict.merge_ready`` is True (machine_ready AND
         authorization_valid), call ``gh pr merge`` exactly once with
         the canonical safe argv. Otherwise exit non-zero.
    """
    repo = args.repo
    pr_number = args.pr_number
    phrase = args.authorization_phrase

    pr_view = fetch_pr_state(repo, pr_number)
    head_sha = pr_view.get("headRefOid")

    # Step 1: byte-exact phrase validation. Pure local check; no I/O
    # needed beyond the live head_sha we just fetched.
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

    # Step 2: trusted-scope resolution. CLI scope is rejected for
    # merge; only the trusted file may be consulted.
    cli_allowed = _parse_scope_arg(getattr(args, "allowed_files", None))
    cli_forbidden = _parse_scope_arg(getattr(args, "forbidden_files", None))
    allowed_files, forbidden_files, scope_err = _resolve_effective_scope(
        subcommand="merge",
        repo=repo,
        pr_number=pr_number,
        head_sha=str(head_sha) if head_sha else None,
        cli_allowed=cli_allowed,
        cli_forbidden=cli_forbidden,
    )
    if scope_err:
        sys.stderr.write(f"Deny: {scope_err}\n")
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
        allowed_files=allowed_files,
        forbidden_files=forbidden_files,
    )
    verdict = R.evaluate_readiness(evidence)

    # merge_ready requires both machine readiness AND a valid
    # authorization phrase. The merge command is the only command
    # allowed to consult ``verdict.merge_ready``; status and advance
    # use machine_ready so they can emit the canonical phrase before
    # the operator has spoken it.
    if not verdict.merge_ready:
        sys.stderr.write(
            "Deny: merge not ready on the live head.\n"
        )
        sys.stderr.write(
            f"  machine_ready={verdict.machine_ready} "
            f"authorization_valid={verdict.authorization_valid}\n"
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
    p_status.add_argument(
        "--allowed-files", default=None,
        help=(
            "Comma-separated allowed-files glob list for the scope check. "
            "Required: the controller never invents a default allowlist."
        ),
    )
    p_status.add_argument(
        "--forbidden-files", default=None,
        help=(
            "Comma-separated forbidden-files glob list for the scope check."
        ),
    )
    p_status.set_defaults(func=cmd_status)

    p_advance = sub.add_parser(
        "advance",
        help="Perform safe mechanical lifecycle steps; never merges.",
    )
    p_advance.add_argument("--pr-number", type=int, required=True)
    p_advance.add_argument("--repo", default=DEFAULT_REPO)
    p_advance.add_argument(
        "--allowed-files", default=None,
        help=(
            "Comma-separated allowed-files glob list for the scope check. "
            "Diagnostic/test-only injection on status/advance; merge "
            "ignores CLI scope entirely."
        ),
    )
    p_advance.add_argument(
        "--forbidden-files", default=None,
        help=(
            "Comma-separated forbidden-files glob list for the scope check. "
            "Diagnostic/test-only injection on status/advance; merge "
            "ignores CLI scope entirely."
        ),
    )
    p_advance.add_argument(
        "--dry-run", action="store_true",
        help="Compute the verdict but skip every mutation.",
    )
    p_advance.add_argument(
        "--resolve-eligible-bot-threads", action="store_true",
        help=(
            "Mutating flag: invoke the thread-resolution mutation for "
            "every eligible outdated Codex-bot thread. Without this "
            "flag, advance remains read-only regarding thread "
            "resolution and only reports eligibility. Use with care; "
            "this can never resolve human-authored or current-bot "
            "threads."
        ),
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
    # NOTE: merge does NOT accept --allowed-files or --forbidden-files.
    # Scope MUST come from the trusted scope file (see
    # _resolve_effective_scope and _MERGE_CLI_SCOPE_REJECTION).
    p_merge.set_defaults(func=cmd_merge)

    p_scope_write = sub.add_parser(
        "scope-write",
        help=(
            "Persist the trusted scope for --pr-number at --head-sha. "
            "Subsequent status/advance/merge calls read this file. "
            "Merge refuses CLI scope overrides."
        ),
    )
    p_scope_write.add_argument("--pr-number", type=int, required=True)
    p_scope_write.add_argument(
        "--repo", default=DEFAULT_REPO,
        help=(
            "Repository in 'owner/name' form. Defaults to "
            f"{DEFAULT_REPO}."
        ),
    )
    p_scope_write.add_argument(
        "--head-sha", required=True,
        help=(
            "Exact head SHA to bind the scope record to. The scope is "
            "immutably tied to this SHA; a moved head requires a new "
            "scope-write call."
        ),
    )
    p_scope_write.add_argument(
        "--allowed-files", required=True,
        help=(
            "Comma-separated allowed-files glob list to persist as the "
            "trusted scope for this PR."
        ),
    )
    p_scope_write.add_argument(
        "--forbidden-files", default=None,
        help=(
            "Comma-separated forbidden-files glob list to persist as "
            "the trusted scope for this PR."
        ),
    )
    p_scope_write.set_defaults(func=cmd_scope_write)

    p_scope_read = sub.add_parser(
        "scope-read",
        help=(
            "Read and emit the trusted scope for --pr-number at --head-sha."
        ),
    )
    p_scope_read.add_argument("--pr-number", type=int, required=True)
    p_scope_read.add_argument(
        "--repo", default=DEFAULT_REPO,
        help=(
            "Repository in 'owner/name' form. Defaults to "
            f"{DEFAULT_REPO}."
        ),
    )
    p_scope_read.add_argument(
        "--head-sha", required=True,
        help="Exact head SHA to read the trusted scope for.",
    )
    p_scope_read.set_defaults(func=cmd_scope_read)

    p_gate_recheck = sub.add_parser(
        "gate-recheck",
        help=(
            "Dispatch the CI workflow bound to the live PR head and "
            "wait for the review-comment-gate to reach a terminal "
            "state. Returns 0 on exact-head success, 1 on exact-head "
            "blocking failure, 2 on dispatch failure or "
            "INCONCLUSIVE. Used to obtain a final terminal result "
            "after a new-head Codex response has landed."
        ),
    )
    p_gate_recheck.add_argument("--pr-number", type=int, required=True)
    p_gate_recheck.add_argument("--repo", default=DEFAULT_REPO)
    p_gate_recheck.add_argument("--head-sha", required=True)
    p_gate_recheck.add_argument(
        "--wait-timeout-seconds", type=int, default=600,
        help="Maximum time to wait for the rerun attempt to terminal.",
    )
    p_gate_recheck.add_argument(
        "--wait-poll-seconds", type=int, default=10,
        help="Polling interval when waiting for the rerun attempt.",
    )
    p_gate_recheck.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Identify the exact-head pull_request CI run and the "
            "review-comment-gate job without invoking ``gh run "
            "rerun``."
        ),
    )
    p_gate_recheck.set_defaults(func=cmd_gate_recheck)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
