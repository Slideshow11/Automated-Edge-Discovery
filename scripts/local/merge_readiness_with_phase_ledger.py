#!/usr/bin/env python3
"""
merge_readiness_with_phase_ledger.py — leaf wrapper that optionally
enforces runner-produced phase-ledger evidence before invoking the
existing merge-readiness orchestrator (merge_pr_safely.py).

This wrapper composes two existing leaf scripts:

  1. ``scripts/local/finalize_with_phase_ledger.py`` (PR #392):
     reads aed.run_summary.v0 ``run_summary.json`` and forwards the
     ``phase_ledger_*`` fields into ``aed_final_gate.run_final_gate()``
     with the opt-in ``require_phase_ledger`` argument. Fail-closed:
     if any ``phase_ledger_*`` key is present but the ledger is
     missing/empty/stale/malformed, the adapter returns non-zero
     and the wrapper halts.

  2. ``scripts/local/merge_pr_safely.py`` (existing, v1):
     emits a verified safe merge command (never executes the merge).
     Refuses ``--admin`` always.

This wrapper is a leaf. It performs no ``gh``/``git``/merge/Hermes
operations of its own — the only subprocess it spawns is the
``merge_pr_safely.py`` orchestrator, and the only library import it
makes is ``finalize_with_phase_ledger.run_finalize()``.

Default-off behavior:
  When ``--run-summary`` is NOT provided, the wrapper delegates
  directly to ``merge_pr_safely.py`` unchanged. Existing operator
  workflows that do not produce a ``run_summary.json`` (e.g. PRs
  not driven by ``run_autocoder_single_task.py``) are unaffected.

Opt-in behavior:
  When ``--run-summary`` IS provided, the wrapper first invokes the
  phase-ledger/final-gate adapter. If the adapter returns:
    * 0 (MERGE_READY) → the wrapper then invokes
      ``merge_pr_safely.py`` and propagates its exit code.
    * 1 (HOLD/BLOCK from the gate) → the wrapper exits 1 and
      does NOT invoke ``merge_pr_safely.py``.
    * 2 (input error: missing/malformed run_summary) → the wrapper
      exits 2 and does NOT invoke ``merge_pr_safely.py``.

Real ``--expected-head-sha`` is REQUIRED when ``--run-summary`` is
provided. The wrapper does NOT fabricate or default a value — the
operator must supply the actual PR head SHA, which is then passed
through unchanged to the adapter.

Scope (leaf wrapper):
  * No ``gh pr merge``, no ``git push``, no ``hermes kanban``, no
    ``memory.update``, no ``skill_manage``, no ``fact_store``, no
    ``delegate_task``, no ``cronjob``.
  * No ``--admin`` is exposed or honored.
  * No ``--auto`` is exposed or honored.
  * Refuses ``--allow-admin`` if passed (defense-in-depth).
  * Module-level forbidden-pattern self-check enforces the safety
    surface at import time.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# Imported at module level so tests can monkeypatch
# ``finalize_with_phase_ledger.run_finalize`` and have the change
# picked up by ``_run_phase_gate`` here.
import finalize_with_phase_ledger  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Forbidden-executable-call self-check
# ---------------------------------------------------------------------------

# These patterns must NOT appear as live executable statements in
# this file. Mirrors the pattern used in finalize_with_phase_ledger.py
# (and aed_final_gate.py) so any regression in the wrapper's safety
# surface is caught the moment the module is loaded.
#
# NOTE: ``"merge_pr_safely.py"`` is intentionally NOT in this list
# because invoking that script is the explicit purpose of this
# wrapper. The check skips lines that are string-only or are part of
# a docstring, comment, or constant assignment.
FORBIDDEN_EXECUTABLE_CALLS = (
    "gh pr merge",
    "gh pr create",
    "git push",
    "hermes kanban dispatch",
    "hermes kanban create",
    "telegram send_message",
    "memory.update",
    "skill_manage",
    "fact_store",
    "delegate_task",
    "cronjob",
    "--admin",
    "--auto",
)


def _forbidden_self_check(source: str) -> list[str]:
    """Return a list of forbidden-call violations found in source.

    Walks the source line-by-line and skips:
      * comment lines (starting with '#')
      * triple-quoted blocks (docstrings and multi-line strings) of
        any kind (three-double-quote or three-single-quote)
      * string-only continuation lines (the body of a multi-line
        string/list constant such as FORBIDDEN_EXECUTABLE_CALLS)
      * the opening line of a multi-line constant assignment
        (e.g. X = ...)

    Only LIVE code statements are checked.
    """
    violations: list[str] = []
    in_triple = False  # True while inside a """ or ''' block
    triple_delim: str = ""  # the delimiter that opened the block
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()

        # Detect and skip a triple-quoted block. We use a small
        # state machine: when not inside a triple block, check for
        # the opening ``"""``/``'''``; when inside, check for the
        # matching closing delimiter. The docstring on a module or
        # function therefore gets skipped line-by-line.
        if not in_triple:
            # Comment line — skip.
            if stripped.startswith("#"):
                continue
            # Triple-quote opens on this line. Walk the line to see
            # whether it also closes on the same line. If not, set
            # in_triple=True and skip the rest of the line scan.
            if '"""' in line or "'''" in line:
                # If the line has both an opening and closing
                # delimiter, treat the entire line as a string and
                # skip it.
                triple_count_dq = line.count('"""')
                triple_count_sq = line.count("'''")
                if triple_count_dq == 2 or triple_count_sq == 2:
                    # Single-line docstring — skip whole line.
                    continue
                if triple_count_dq == 1:
                    in_triple = True
                    triple_delim = '"""'
                    continue
                if triple_count_sq == 1:
                    in_triple = True
                    triple_delim = "'''"
                    continue
            # String-only continuation line (part of a multi-line
            # string/list constant) — skip.
            if stripped.startswith(('"', "'")):
                continue
            # Opening line of a multi-line constant assignment
            # (e.g. ``X = (``. Mirrors aed_final_gate.py /
            # finalize_with_phase_ledger.py.
            if re.match(r"^[_A-Za-z][_A-Za-z0-9]*\s*=\s*[\(\[\"']", stripped):
                continue
            for pat in FORBIDDEN_EXECUTABLE_CALLS:
                if pat in line:
                    violations.append(f"line {lineno}: {stripped}")
                    break
        else:
            # Inside a triple-quoted block — wait for the closing
            # delimiter. If it appears, leave the block on this
            # line.
            if triple_delim in line:
                in_triple = False
                triple_delim = ""
    return violations


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


def _reject_admin(args: argparse.Namespace) -> None:
    """Refuse any ``allow_admin`` truthy attribute on the parsed args.

    The argparse below does not expose ``--allow-admin``. This guard
    catches the case where a caller (or a future test) constructs an
    ``argparse.Namespace`` with ``allow_admin`` set and tries to
    re-use the wrapper machinery.
    """
    if getattr(args, "allow_admin", False):
        print(
            "merge_readiness_with_phase_ledger: --allow-admin is forbidden in this wrapper; "
            "the wrapper hard-codes admin_refused=True semantics via merge_pr_safely.py and "
            "forbids any admin override on the phase-gate adapter.",
            file=sys.stderr,
        )
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# Phase-gate adapter invocation
# ---------------------------------------------------------------------------


def _run_phase_gate(args: argparse.Namespace) -> int:
    """Invoke ``finalize_with_phase_ledger.run_finalize()`` with a Namespace
    built from the wrapper's CLI args.

    Passes the operator-supplied ``--expected-head-sha`` through
    unchanged. The wrapper does NOT fabricate or default this value
    (it is required when ``--run-summary`` is provided).

    Returns the adapter's exit code: 0 (MERGE_READY), 1 (HOLD/BLOCK),
    or 2 (input error).
    """
    ns = argparse.Namespace(
        run_summary=args.run_summary,
        pr_number=args.pr_number,
        expected_head_sha=args.expected_head_sha,
        allowed_files=args.allowed_files,
        local_validation_path=args.local_validation_path,
        codex_artifact_path=args.codex_artifact_path,
        output_json=args.phase_gate_output_json,
        output_md=args.phase_gate_output_md,
        allow_codex_skip=bool(args.allow_codex_skip),
        require_persistent_guard=bool(args.require_persistent_guard),
        persistent_guard_root=args.persistent_guard_root,
        persistent_guard_snapshot=args.persistent_guard_snapshot,
        persistent_guard_compare_json=args.persistent_guard_compare_json,
        persistent_guard_compare_md=args.persistent_guard_compare_md,
    )
    return finalize_with_phase_ledger.run_finalize(ns)


# ---------------------------------------------------------------------------
# merge_pr_safely.py subprocess invocation
# ---------------------------------------------------------------------------


def _build_merge_pr_safely_cmd(args: argparse.Namespace) -> list:
    """Build the subprocess argv for ``merge_pr_safely.py``.

    Uses only the real ``merge_pr_safely`` CLI flags (discovered in
    PHASE 2 by reading the source): ``--repo``, ``--repo-root``,
    ``--pr-number``, ``--timeout-minutes``, ``--poll-seconds``,
    ``--ignore-users``, ``--output-json``, ``--output-md``.

    Does NOT forward wrapper-only args (``--run-summary``,
    ``--expected-head-sha``, ``--allowed-files``, etc.) because
    ``merge_pr_safely`` does not understand them. Persistent-guard
    args are not forwarded because ``merge_pr_safely`` does not
    support them either.
    """
    script = Path(__file__).parent / "merge_pr_safely.py"
    cmd: list = [sys.executable, str(script)]
    cmd.extend(["--repo", args.repo])
    cmd.extend(["--repo-root", args.repo_root])
    cmd.extend(["--pr-number", str(args.pr_number)])
    cmd.extend(["--timeout-minutes", str(args.timeout_minutes)])
    cmd.extend(["--poll-seconds", str(args.poll_seconds)])
    if args.ignore_users:
        cmd.extend(["--ignore-users", args.ignore_users])
    cmd.extend(["--output-json", args.output_json])
    if args.output_md:
        cmd.extend(["--output-md", args.output_md])
    return cmd


def _run_merge_pr_safely(args: argparse.Namespace) -> int:
    """Subprocess-invoke ``merge_pr_safely.py`` and return its exit code.

    The wrapper's stdout/stderr is connected through to the operator's
    terminal so that ``merge_pr_safely``'s normal progress reporting
    is visible. ``check=False`` — the wrapper surfaces the real
    return code unchanged.
    """
    cmd = _build_merge_pr_safely_cmd(args)
    completed = subprocess.run(
        cmd,
        check=False,
        # Connect stdout/stderr through so merge_pr_safely's normal
        # reporting is visible to the operator.
    )
    return completed.returncode


# ---------------------------------------------------------------------------
# Live PR head re-fetch (P1 regression guard on PR #393)
# ---------------------------------------------------------------------------


# Bounded timeout (seconds) for the read-only ``gh pr view`` recheck.
# Mirrors the timeout used by ``merge_pr_safely.fetch_pr_head_sha()`` so
# the wrapper's pre-delegation recheck and ``merge_pr_safely``'s
# internal fetch fail at the same rate on a stalled ``gh``. Closes
# the Codex P2 follow-up finding on PR #393 — inline comment
# PRRC_kwDOSHFpYM7I44yF, thread PRRT_kwDOSHFpYM6Hs2BD.
GH_PR_VIEW_TIMEOUT_SECONDS = 30


def _build_fetch_live_head_cmd(repo: str, pr_number: int) -> list:
    """Build the read-only ``gh pr view`` argv used to recheck the live
    PR head after the phase-ledger gate has passed.

    This closes the Codex P1 finding on PR #393 (inline comment id
    3370199372): the phase-gate validates ``args.expected_head_sha``,
    but ``merge_pr_safely.py`` re-fetches the live head itself. If
    the branch receives a new commit between the gate and the
    subprocess invocation, ``merge_pr_safely.py`` would otherwise
    build readiness/merge-command output for code that the
    runner-produced ledger never covered. This recheck catches
    that window.
    """
    return [
        "gh", "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", "headRefOid",
        "--jq", ".headRefOid",
    ]


def _fetch_live_pr_head(repo: str, pr_number: int) -> "tuple[bool, Optional[str]]":
    """Read-only ``gh pr view`` call to re-fetch the live PR head.

    Returns ``(True, head_sha)`` on success where ``head_sha`` is
    a 40-char hex string with surrounding whitespace stripped.
    Returns ``(False, None)`` on any failure: subprocess non-zero
    exit, ``subprocess.TimeoutExpired`` (gh stalled on auth or
    network I/O), empty stdout, malformed JSON, or a non-SHA
    stdout.

    The call is bounded by ``GH_PR_VIEW_TIMEOUT_SECONDS`` (see
    P2 fix on PR #393 — inline comment PRRC_kwDOSHFpYM7I44yF,
    thread PRRT_kwDOSHFpYM6Hs2BD). If ``gh`` hangs past the
    timeout, ``subprocess.TimeoutExpired`` is raised by
    ``subprocess.run``; we catch it and return ``(False, None)``
    so the wrapper takes the existing "unable to recheck PR head"
    path (exit 2, do not invoke ``merge_pr_safely``).

    This is intentionally read-only: it never invokes
    ``gh pr merge``, ``gh pr create``, ``gh pr edit``, or any
    state-mutating endpoint. The command is a single
    ``gh pr view --json headRefOid --jq .headRefOid`` call.
    """
    cmd = _build_fetch_live_head_cmd(repo, pr_number)
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=GH_PR_VIEW_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # ``gh`` did not return within the bounded window. Treat
        # as a failed recheck — the existing path in ``run_wrapper``
        # prints "unable to recheck PR head" and exits 2 without
        # invoking ``merge_pr_safely``.
        return False, None
    if completed.returncode != 0:
        return False, None
    raw = (completed.stdout or "").strip()
    if not raw:
        return False, None
    # Sanity: must look like a 40-char hex SHA. If it doesn't,
    # treat as a failure (defensive — protects against a future
    # ``gh`` version that returns a different shape).
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{40}", raw):
        return False, None
    return True, raw


# ---------------------------------------------------------------------------
# Merge-readiness report head-binding (P1 regression guard on PR #393,
# follow-up to inline comment id 3370199372 → 3370258789)
# ---------------------------------------------------------------------------


_SHA40_RE = re.compile(r"[0-9a-f]{40}")


def _load_merge_readiness_report(path) -> "tuple[bool, Optional[dict]]":
    """Read and JSON-parse the merge-readiness report written by
    ``merge_pr_safely.py`` at ``path``.

    Returns ``(True, report_dict)`` on success where ``report_dict``
    is the parsed JSON object. Returns ``(False, None)`` on any
    failure: missing/empty path, file not found, permission
    error, or malformed JSON.

    This is purely a read; the wrapper never mutates the report.
    """
    if not path:
        return False, None
    try:
        p = Path(path)
    except TypeError:
        return False, None
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, None
    if not text.strip():
        return False, None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    return True, data


def _extract_report_head_sha(report: dict) -> Optional[str]:
    """Extract the head SHA recorded in a ``merge_pr_safely.py`` report.

    Discovery order (PR #393 Codex P1 fix — see
    ``scripts/local/merge_pr_safely.py`` lines 489, 520, 558, 583
    and ``tests/test_merge_pr_safely.py`` line 290):

      1. The ``head_sha`` field — the canonical, explicit
         field. ``merge_pr_safely.py`` always writes it when
         the ``gh pr view`` fetch inside that script succeeds.
      2. The ``safe_merge_command_text`` field — a textual
         ``gh pr merge ... --match-head-commit <sha> ...`` command.
         Used as a defensive fallback in case the explicit
         field is absent in a future revision.
      3. The ``safe_merge_command_list`` field — the same
         command as a list of tokens. Same defensive purpose.

    Each candidate value is validated against the 40-char
    lowercase-hex SHA pattern. The first validated value
    wins. Returns ``None`` if no usable SHA is found.
    """
    # 1) Explicit field.
    candidate = report.get("head_sha")
    if isinstance(candidate, str):
        c = candidate.strip()
        if _SHA40_RE.fullmatch(c):
            return c

    # 2) Defensive fallback: --match-head-commit <sha> inside the
    # textual command. ``re.search`` (not ``fullmatch``) because
    # the SHA is embedded in a larger command string.
    for key in ("safe_merge_command_text", "safe_merge_command_list"):
        value = report.get(key)
        if isinstance(value, str):
            m = re.search(r"--match-head-commit\s+([0-9a-f]{40})", value)
            if m:
                return m.group(1)
        elif isinstance(value, list):
            joined = " ".join(str(x) for x in value)
            m = re.search(r"--match-head-commit\s+([0-9a-f]{40})", joined)
            if m:
                return m.group(1)

    return None


def _verify_merge_readiness_head(args) -> int:
    """Verify the report written by ``merge_pr_safely.py`` records the
    same head SHA that the phase-ledger gate validated.

    This is the second half of the P1 fix on PR #393 (inline
    comment id 3370258789, thread PRRT_kwDOSHFpYM6HskHa). The
    pre-delegation live-head recheck (in ``run_wrapper``) catches
    commits that land BEFORE the subprocess starts; this
    post-delegation report check catches commits that land
    AFTER the subprocess's internal ``gh pr view`` fetch
    (line 442 of ``merge_pr_safely.py``) and BEFORE the wrapper
    returns. Together they ensure the wrapper can only report
    success for a head the runner-produced ledger actually
    covered.

    Return codes:
      0 — report's recorded head equals ``args.expected_head_sha``.
      1 — report's recorded head differs from
          ``args.expected_head_sha`` (``HEAD_MISMATCH_AFTER_MERGE_READINESS``).
      2 — report missing, unparseable, or no usable head SHA
          recorded. Treated as a hard error so the wrapper
          never returns success without a verifiable bind.
    """
    expected = getattr(args, "expected_head_sha", None) or ""
    report_path = getattr(args, "output_json", None)

    ok, report = _load_merge_readiness_report(report_path)
    if not ok:
        print(
            "merge_readiness_with_phase_ledger: unable to verify "
            "merge-readiness report head; not returning success",
            file=sys.stderr,
        )
        return 2

    # ``_load_merge_readiness_report`` guarantees ``isinstance(report, dict)``
    # when ``ok`` is True; narrow explicitly for the type checker.
    if not isinstance(report, dict):
        print(
            "merge_readiness_with_phase_ledger: unable to verify "
            "merge-readiness report head; not returning success",
            file=sys.stderr,
        )
        return 2

    report_head = _extract_report_head_sha(report)
    if report_head is None:
        print(
            "merge_readiness_with_phase_ledger: unable to verify "
            "merge-readiness report head; not returning success",
            file=sys.stderr,
        )
        return 2

    if report_head != expected:
        print(
            "HEAD_MISMATCH_AFTER_MERGE_READINESS: ledger-validated head "
            f"was {expected} but merge_pr_safely report shows "
            f"{report_head}; not returning success",
            file=sys.stderr,
        )
        return 1

    # Report's recorded head matches the ledger-validated head.
    # Safe to return success.
    return 0


# ---------------------------------------------------------------------------
# Phase-gate required-arg validation
# ---------------------------------------------------------------------------


def _missing_required_phase_gate_args(args: argparse.Namespace) -> List[str]:
    """Return a list of phase-gate arg names that are missing/empty
    when ``--run-summary`` is set.

    The wrapper is strict: if the operator opts into the phase-gate
    adapter, ALL of its required-when-active args must be present
    and non-empty. No silent defaults, no fabrication.
    """
    required = [
        ("expected_head_sha", "--expected-head-sha"),
        ("allowed_files", "--allowed-files"),
        ("local_validation_path", "--local-validation-path"),
        ("codex_artifact_path", "--codex-artifact-path"),
        ("phase_gate_output_json", "--phase-gate-output-json"),
        ("phase_gate_output_md", "--phase-gate-output-md"),
    ]
    missing: List[str] = []
    for attr, flag in required:
        v = getattr(args, attr, None)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(flag)
    return missing


# ---------------------------------------------------------------------------
# Repository consistency check (P2 regression guard on PR #393,
# thread PRRT_kwDOSHFpYM6Hs9BB, comment PRRC_kwDOSHFpYM7I5CY5):
# aed_final_gate.run_final_gate derives its repo from the script
# repo's ``git remote get-url origin``. If the operator passes a
# ``--repo`` that doesn't match the script repo's remote origin,
# the ledger would validate a different PR than the one this
# wrapper re-fetches and delegates to merge_pr_safely. This
# section normalizes the two forms and rejects mismatches before
# the phase-gate adapter is called.
# ---------------------------------------------------------------------------


# Bounded timeout (seconds) for the read-only
# ``git remote get-url origin`` call. 10s is more than enough for
# a local filesystem ``git`` invocation; the bound exists so a
# wedged ``git`` on a slow filesystem cannot stall the wrapper
# indefinitely.
_GIT_REMOTE_GET_URL_TIMEOUT_SECONDS = 10


def _normalize_repo_slug(value) -> Optional[str]:
    """Normalize a GitHub repo reference to ``"owner/repo"`` lowercase.

    Accepts all of the common forms the operator or ``git remote
    get-url`` may produce:

      * ``Slideshow11/Automated-Edge-Discovery``
      * ``https://github.com/Slideshow11/Automated-Edge-Discovery``
      * ``https://github.com/Slideshow11/Automated-Edge-Discovery.git``
      * ``git@github.com:Slideshow11/Automated-Edge-Discovery.git``
      * ``ssh://git@github.com/Slideshow11/Automated-Edge-Discovery.git``

    Returns ``"owner/repo"`` in lowercase with any trailing
    ``.git`` stripped. Returns ``None`` for any value that cannot
    be parsed as an ``owner/repo`` pair (missing slash, empty
    owner, empty repo, etc.). The case-fold makes the comparison
    case-insensitive.
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Strip trailing ``.git`` if present (after whitespace).
    if s.endswith(".git"):
        s = s[: -len(".git")].rstrip()
    # SSH form: ``git@github.com:owner/repo`` → ``github.com/owner/repo``
    if s.startswith("git@") and ":" in s:
        s = s.split(":", 1)[1]
    # Strip scheme/host for URL forms.
    if "://" in s:
        s = s.split("://", 1)[1]
    # Now we should have either ``host/owner/repo`` or
    # ``owner/repo``. Drop the host segment if present.
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    if not owner or not repo:
        return None
    # Reject anything that doesn't look like a plausible GitHub
    # owner/repo. (We deliberately do not enforce strict
    # character classes here; the goal is just to filter the
    # empty / pathological cases that would otherwise produce
    # false-positive matches.)
    if "/" in owner or "/" in repo:
        return None
    return f"{owner.lower()}/{repo.lower()}"


def _fetch_repo_root_origin(repo_root: str) -> "tuple[bool, Optional[str]]":
    """Read ``git -C <repo_root> remote get-url origin``.

    Returns ``(True, origin_url)`` only on a non-empty rc=0
    stdout (whitespace stripped). Returns ``(False, None)`` on
    any failure: non-zero exit, empty stdout, missing
    ``origin`` remote, ``subprocess.TimeoutExpired``, or
    ``OSError`` (missing ``git`` binary, missing repo path).

    The call is bounded by
    ``_GIT_REMOTE_GET_URL_TIMEOUT_SECONDS``. This is
    intentionally read-only: it never invokes ``git push``,
    ``git remote set-url``, or any state-mutating command.
    """
    if not repo_root or not isinstance(repo_root, str):
        return False, None
    cmd = ["git", "-C", repo_root, "remote", "get-url", "origin"]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_REMOTE_GET_URL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, None
    if completed.returncode != 0:
        return False, None
    raw = (completed.stdout or "").strip()
    if not raw:
        return False, None
    return True, raw


def _validate_repo_matches_repo_root(args) -> Optional[str]:
    """Return a clear error string if ``args.repo`` does not
    match the script repo's ``git remote get-url origin``.

    The phase-ledger gate (``aed_final_gate.run_final_gate``)
    derives its target repo from the script's own ``git
    remote get-url origin``. If the operator passes a
    ``--repo`` that resolves to a different GitHub repo than
    what the gate validates, the ledger can cover code in repo
    A while the wrapper's downstream ``gh pr view`` and
    ``merge_pr_safely`` operate on repo B. This is a
    cross-script consistency violation; we fail closed.

    The check is OPT-IN ONLY: this function is only called
    when ``args.run_summary`` is provided (see
    ``run_wrapper``). The default-off path delegates directly
    to ``merge_pr_safely.py`` and does not run the gate, so
    there is no consistency surface to enforce.

    Failure modes (all return an error string; the caller
    prints it to stderr and exits 2):

      * ``args.repo`` is empty/missing/non-string → unable to
        determine; refuse to proceed.
      * ``args.repo_root`` is empty/missing/non-string →
        unable to determine; refuse to proceed.
      * ``git remote get-url origin`` fails (non-zero exit,
        empty stdout, TimeoutExpired, OSError) → unable to
        determine; refuse to proceed (fail closed).
      * Either side normalizes to ``None`` (unparseable) →
        unable to determine; refuse to proceed.
      * Normalized values differ → explicit REPO_MISMATCH.

    On a successful match, returns ``None`` and the caller
    proceeds to the phase-gate adapter.
    """
    expected_raw = getattr(args, "repo", None)
    repo_root = getattr(args, "repo_root", None)

    if not expected_raw or not isinstance(expected_raw, str):
        return (
            "merge_readiness_with_phase_ledger: --repo is missing or empty; "
            "unable to verify repo/root consistency; refusing to run "
            "phase-ledger gate"
        )
    if not repo_root or not isinstance(repo_root, str):
        return (
            "merge_readiness_with_phase_ledger: --repo-root is missing or "
            "empty; unable to verify repo/root consistency; refusing to "
            "run phase-ledger gate"
        )

    expected_norm = _normalize_repo_slug(expected_raw)
    if expected_norm is None:
        return (
            f"merge_readiness_with_phase_ledger: --repo {expected_raw!r} "
            "is not a parseable owner/repo; refusing to run phase-ledger "
            "gate"
        )

    ok, origin_raw = _fetch_repo_root_origin(repo_root)
    if not ok:
        return (
            f"merge_readiness_with_phase_ledger: unable to read "
            f"git remote get-url origin for --repo-root {repo_root!r}; "
            "refusing to run phase-ledger gate against an unverified repo"
        )

    origin_norm = _normalize_repo_slug(origin_raw)
    if origin_norm is None:
        return (
            f"merge_readiness_with_phase_ledger: git remote origin "
            f"{origin_raw!r} for --repo-root {repo_root!r} is not a "
            "parseable owner/repo; refusing to run phase-ledger gate"
        )

    if expected_norm != origin_norm:
        return (
            f"REPO_MISMATCH: --repo {expected_raw} does not match git "
            f"remote origin for --repo-root {repo_root} ({origin_raw}); "
            "refusing to run phase-ledger gate against a different "
            "repository than merge readiness"
        )

    # Match. Proceed.
    return None


# ---------------------------------------------------------------------------
# Main wrapper entry point
# ---------------------------------------------------------------------------


def run_wrapper(args: argparse.Namespace) -> int:
    """Run the wrapper. Returns the process exit code.

    Decision tree:
      1. _reject_admin (defense in depth).
      2. If args.run_summary is None:
           - default-off: skip the phase-gate adapter.
           - skip the live-head recheck (only the opt-in path
             needs it; the default-off path delegates to
             merge_pr_safely which fetches its own head).
           - invoke merge_pr_safely.py directly.
           - return merge_pr_safely's exit code.
      3. If args.run_summary is provided:
           - validate all required phase-gate args are present.
             If any are missing, exit 2.
           - invoke the phase-gate adapter.
             If the adapter returns non-zero, exit with the
             adapter's code and do NOT invoke merge_pr_safely.
           - If the adapter returns 0, RE-CHECK the live PR head
             against args.expected_head_sha (closes the Codex P1
             finding on PR #393 — see inline comment id
             3370199372). On any discrepancy (head differs OR
             fetch fails), exit non-zero and do NOT invoke
             merge_pr_safely. Only when the live head matches do
             we proceed to merge_pr_safely.
           - If merge_pr_safely returns 0, VERIFY that the
             report it wrote to args.output_json records the
             same head SHA the gate validated (closes the
             Codex follow-up P1 — inline comment id 3370258789,
             thread PRRT_kwDOSHFpYM6HskHa). On a head mismatch,
             missing report, or unparseable report, exit
             non-zero. Only when the report's recorded head
             matches do we return 0.
    """
    _reject_admin(args)

    if args.run_summary is None:
        # Default-off: operator did not opt into the phase-gate
        # adapter. Pass through to merge_pr_safely unchanged.
        print(
            "merge_readiness_with_phase_ledger: no --run-summary provided; "
            "phase-ledger gate skipped",
            file=sys.stderr,
        )
        return _run_merge_pr_safely(args)

    # Opt-in: phase-gate adapter is required to pass before
    # merge_pr_safely is invoked.
    missing = _missing_required_phase_gate_args(args)
    if missing:
        print(
            "merge_readiness_with_phase_ledger: --run-summary is set but the "
            "following required phase-gate args are missing or empty: "
            + ", ".join(missing)
            + ". Refusing to proceed.",
            file=sys.stderr,
        )
        return 2

    # Cross-script consistency check: ensure the operator-supplied
    # ``--repo`` matches the script repo's ``git remote get-url
    # origin`` (which is what ``aed_final_gate.run_final_gate`` uses
    # to determine the target repo for the phase-ledger gate).
    # Closes the Codex P2 follow-up on PR #393 — inline comment
    # PRRC_kwDOSHFpYM7I5CY5, thread PRRT_kwDOSHFpYM6Hs9BB.
    repo_err = _validate_repo_matches_repo_root(args)
    if repo_err is not None:
        print(repo_err, file=sys.stderr)
        return 2

    gate_rc = _run_phase_gate(args)
    if gate_rc != 0:
        # Fail-closed: do NOT invoke merge_pr_safely if the gate
        # returned HOLD (1) or input error (2).
        print(
            "merge_readiness_with_phase_ledger: phase-ledger final gate "
            f"blocked merge-readiness (gate exit code {gate_rc}); "
            "merge_pr_safely not invoked",
            file=sys.stderr,
        )
        return gate_rc

    # Gate returned 0 (MERGE_READY). BEFORE invoking
    # merge_pr_safely, re-fetch the live PR head and compare it
    # to args.expected_head_sha. If the branch received a new
    # commit between the gate and now, merge_pr_safely would
    # otherwise build readiness output for code the ledger never
    # covered. This is the P1 fix for inline comment 3370199372.
    ok, live_head = _fetch_live_pr_head(args.repo, args.pr_number)
    if not ok:
        # Read-only gh pr view failed (subprocess non-zero, empty
        # stdout, or non-SHA result). Treat as a hard error: do
        # NOT delegate to merge_pr_safely.
        print(
            "merge_readiness_with_phase_ledger: unable to recheck PR head "
            "after phase-ledger gate; merge_pr_safely not invoked",
            file=sys.stderr,
        )
        return 2
    if live_head != args.expected_head_sha:
        # Head changed between the gate and the subprocess. The
        # ledger evidence no longer covers the live head. Block
        # the delegation.
        print(
            f"HOLD_HEAD_CHANGED: phase-ledger gate validated "
            f"{args.expected_head_sha} but PR head is now {live_head}; "
            "merge_pr_safely not invoked",
            file=sys.stderr,
        )
        return 1

    # Live head matches the validated head. Proceed to merge_pr_safely.
    merge_rc = _run_merge_pr_safely(args)
    if merge_rc != 0:
        # Preserve existing behavior: propagate merge_pr_safely's exit
        # code unchanged. Do not run the post-success head-binding
        # verification on a failed run — the report may be missing
        # or partial in that case, and the failure mode is already
        # surfaced by merge_pr_safely's own non-zero exit.
        return merge_rc
    # merge_pr_safely returned 0. The report should be at
    # args.output_json. Verify its recorded head still equals
    # args.expected_head_sha (closes the residual TOCTOU window
    # between merge_pr_safely's internal ``gh pr view`` fetch
    # and the wrapper returning — the P1 follow-up to inline
    # comment 3370258789, thread PRRT_kwDOSHFpYM6HskHa).
    return _verify_merge_readiness_head(args)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="merge_readiness_with_phase_ledger",
        description=(
            "Leaf wrapper: optionally enforces runner-produced phase-ledger "
            "evidence (via the PR #392 finalize_with_phase_ledger adapter) "
            "before invoking the existing merge_pr_safely.py orchestrator. "
            "Default-off when --run-summary is omitted (delegates directly "
            "to merge_pr_safely). Fail-closed when --run-summary is set "
            "and the adapter returns HOLD or ERROR."
        ),
    )

    # ---- merge_pr_safely pass-through args (real CLI surface) ----
    parser.add_argument(
        "--repo",
        required=True,
        help="GitHub repository in 'owner/name' form (passed to merge_pr_safely).",
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        help="Absolute path to the AED repository root (passed to merge_pr_safely).",
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="GitHub PR number (passed to both merge_pr_safely and the adapter).",
    )
    parser.add_argument(
        "--timeout-minutes",
        type=int,
        default=15,
        help="Max wait time in minutes (passed to merge_pr_safely; default: 15).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="Seconds between CI polls (passed to merge_pr_safely; default: 30).",
    )
    parser.add_argument(
        "--ignore-users",
        default=None,
        help="Comma-separated users to ignore in review-comment gate "
             "(passed to merge_pr_safely).",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to merge_pr_safely's JSON report (the wrapper never "
             "writes here itself).",
    )
    parser.add_argument(
        "--output-md",
        default=None,
        help="Path to merge_pr_safely's Markdown report (optional).",
    )

    # ---- Phase-gate adapter args (opt-in via --run-summary) ----
    parser.add_argument(
        "--run-summary",
        default=None,
        help=(
            "Path to aed.run_summary.v0 run_summary.json. When set, the "
            "wrapper invokes the phase-ledger/final-gate adapter before "
            "merge_pr_safely. When omitted, the wrapper delegates to "
            "merge_pr_safely unchanged (default-off)."
        ),
    )
    parser.add_argument(
        "--expected-head-sha",
        default=None,
        help=(
            "Expected head SHA for the final-gate adapter. REQUIRED when "
            "--run-summary is set; passed through to the adapter unchanged. "
            "The wrapper does NOT fabricate or default this value."
        ),
    )
    parser.add_argument(
        "--allowed-files",
        default=None,
        help=(
            "Comma-separated list of allowed file globs for the final-gate "
            "adapter. REQUIRED when --run-summary is set."
        ),
    )
    parser.add_argument(
        "--local-validation-path",
        default=None,
        help=(
            "Path to the local validation JSON (passed to the final-gate "
            "adapter). REQUIRED when --run-summary is set."
        ),
    )
    parser.add_argument(
        "--codex-artifact-path",
        default=None,
        help=(
            "Path to the Codex review artifact (passed to the final-gate "
            "adapter). REQUIRED when --run-summary is set."
        ),
    )
    parser.add_argument(
        "--phase-gate-output-json",
        default=None,
        help=(
            "Path to write the final-gate adapter's JSON output. REQUIRED "
            "when --run-summary is set."
        ),
    )
    parser.add_argument(
        "--phase-gate-output-md",
        default=None,
        help=(
            "Path to write the final-gate adapter's Markdown output. REQUIRED "
            "when --run-summary is set."
        ),
    )

    # ---- Pass-through flags forwarded to finalize_with_phase_ledger ----
    parser.add_argument(
        "--allow-codex-skip",
        action="store_true",
        help="Forwarded to finalize_with_phase_ledger.run_finalize(...).",
    )
    parser.add_argument(
        "--require-persistent-guard",
        action="store_true",
        help="Forwarded to finalize_with_phase_ledger.run_finalize(...).",
    )
    parser.add_argument(
        "--persistent-guard-root",
        default="/home/max/.hermes",
        help="Forwarded to finalize_with_phase_ledger.run_finalize(...).",
    )
    parser.add_argument(
        "--persistent-guard-snapshot",
        default=None,
        help="Forwarded to finalize_with_phase_ledger.run_finalize(...).",
    )
    parser.add_argument(
        "--persistent-guard-compare-json",
        default=None,
        help="Forwarded to finalize_with_phase_ledger.run_finalize(...).",
    )
    parser.add_argument(
        "--persistent-guard-compare-md",
        default=None,
        help="Forwarded to finalize_with_phase_ledger.run_finalize(...).",
    )

    # NOTE: --allow-admin is intentionally NOT exposed. The wrapper
    # hard-rejects it at runtime via _reject_admin(args).
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_wrapper(args)


# ---------------------------------------------------------------------------
# Module-level self-check on import
# ---------------------------------------------------------------------------


_source = Path(__file__).read_text(encoding="utf-8")
_violations = _forbidden_self_check(_source)
if _violations:
    # Fail loud at import time so any regression in the wrapper's
    # safety surface is caught immediately. The ``__main__`` guard
    # below re-runs nothing; the import-time check is the canonical
    # safety gate.
    raise RuntimeError(
        "merge_readiness_with_phase_ledger: forbidden executable pattern(s) "
        "found in source: " + "; ".join(_violations)
    )


if __name__ == "__main__":
    sys.exit(main())
