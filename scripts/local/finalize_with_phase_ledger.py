#!/usr/bin/env python3
"""
finalize_with_phase_ledger.py — leaf adapter that consumes runner-produced
phase-ledger evidence from a run_summary.json (aed.run_summary.v0) and
forwards it into aed_final_gate.run_final_gate() via the opt-in
``require_phase_ledger`` argument.

Design contract (this file's only non-trivial safety rule):

* If the run_summary.json contains ZERO of the three
  ``phase_ledger_*`` keys (``phase_ledger_path``,
  ``phase_ledger_claimed_phases``, ``phase_ledger_expected_run_id``),
  ledger consumption is **default-off**: the adapter calls
  ``run_final_gate`` with ``require_phase_ledger=False`` and leaves the
  phase_ledger kwargs at ``None``. This preserves the pre-existing
  default behavior for runner invocations that did not opt into the
  ledger.

* If the run_summary.json contains ANY one of those keys, ledger
  consumption is **enabled**: the adapter calls ``run_final_gate`` with
  ``require_phase_ledger=True`` and forwards whatever values it found
  for the other two keys. Missing/empty claimed phases, missing ledger
  path, missing expected_run_id, stale run_id, or a malformed ledger
  file are then enforced by ``aed_final_gate.run_final_gate`` /
  ``validate_phase_ledger.validate`` (fail-closed → HOLD_UNEVIDENCED_PASS,
  HOLD_PHASE_EVIDENCE_CORRUPTED, or HOLD_PHASE_RESULT_INCONSISTENT).
  The adapter MUST NOT silently disable ledger validation in this
  branch — that would defeat the no-unproven-PASS guard.

Scope (leaf adapter):
* No subprocess, no shell, no orchestration, no gh/git/merges.
* No ``--allow-admin`` is exposed or honored.
* Calls ``aed_final_gate.run_final_gate()`` directly (library import).
* Emits the final-gate JSON to stdout and propagates its exit code
  (0 = MERGE_READY, 1 = any other recommendation).

This file is the implementation side of PR #392. It is invoked
manually or by a future merge-readiness orchestrator; it does NOT
auto-trigger.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Imported at module level (not lazy) so tests can monkeypatch
# ``aed_final_gate.run_final_gate`` and have the change picked up by
# ``run_finalize`` here. The adapter is a leaf script — it does not
# run any heavy code at import time beyond the source-level
# forbidden-pattern self-check below.
import aed_final_gate  # type: ignore[import-not-found]

# These three keys, when ANY one is present in run_summary.json, flip
# ledger consumption from default-off to required. The values are
# forwarded as-is (no resolve, no abspath, no normalization) so the
# runner-produced shape is preserved end-to-end.
LEDGER_KEYS = (
    "phase_ledger_path",
    "phase_ledger_claimed_phases",
    "phase_ledger_expected_run_id",
)

EXPECTED_RUN_SUMMARY_VERSION = "aed.run_summary.v0"

# Patterns that MUST NOT appear as live executable calls in this file.
# Mirrors the safety list in aed_final_gate.py. Enforced via
# _forbidden_self_check() at import time so a regression in the adapter
# is caught the moment the module is loaded.
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
)


# ---------------------------------------------------------------------------
# Forbidden-pattern self-check
# ---------------------------------------------------------------------------


def _forbidden_self_check(source: str) -> list[str]:
    """Return a list of forbidden-call violations found in ``source``.

    Walks the source line-by-line and skips lines that are clearly
    comments, docstrings, or string-only constant assignments (the
    patterns in ``FORBIDDEN_EXECUTABLE_CALLS`` must not appear as live
    executable statements).
    """
    violations: list[str] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        # Skip pure comment lines and docstring delimiters.
        if (
            stripped.startswith("#")
            or stripped.startswith('"""')
            or stripped.startswith("'''")
        ):
            continue
        # Skip lines that are only a string literal (the
        # continuation of a multi-line string/list constant such as
        # ``FORBIDDEN_EXECUTABLE_CALLS = ( "gh pr merge", ...)``).
        if stripped.startswith(('"', "'")):
            continue
        # Skip lines that assign a list/tuple/dict/string literal
        # (the opening line of a multi-line constant). Mirrors the
        # pattern used in aed_final_gate.py.
        if re.match(r"^[_A-Za-z][_A-Za-z0-9]*\s*=\s*[\(\[\"']", stripped):
            continue
        for pat in FORBIDDEN_EXECUTABLE_CALLS:
            if pat in line:
                violations.append(f"line {lineno}: {stripped}")
                break
    return violations


# ---------------------------------------------------------------------------
# Run summary loading
# ---------------------------------------------------------------------------


def _load_run_summary(path: Path) -> Optional[dict]:
    """Load and parse run_summary.json from ``path``.

    On any error (missing file, read failure, malformed JSON), prints
    a clear message to stderr and returns None. The caller (``run_finalize``)
    treats None as a hard error and exits 2.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(
            f"finalize_with_phase_ledger: run_summary file not found: {path}",
            file=sys.stderr,
        )
        return None
    except OSError as exc:
        print(
            f"finalize_with_phase_ledger: cannot read run_summary {path}: {exc}",
            file=sys.stderr,
        )
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"finalize_with_phase_ledger: malformed JSON in {path}: {exc}",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print(
            "finalize_with_phase_ledger: run_summary top-level is not an object",
            file=sys.stderr,
        )
        return None
    return data


# ---------------------------------------------------------------------------
# Ledger field extraction
# ---------------------------------------------------------------------------


def _extract_ledger_args(summary: dict) -> dict:
    """Map ``run_summary.json`` phase_ledger fields onto run_final_gate kwargs.

    Safety rule (PR #392 core invariant):

    * If ZERO of the three ``phase_ledger_*`` keys are present in the
      summary, the adapter does not opt into ledger validation
      (``enabled = False``). All phase_ledger kwargs are set to None.
      This matches the producer's behavior for runs that did not
      enable the ledger.

    * If ANY one of the three keys is present, the adapter opts in
      (``enabled = True``) and forwards whatever values it found for
      the other two keys (which may be None or []). Missing values
      are then enforced fail-closed by
      ``aed_final_gate.run_final_gate`` /
      ``validate_phase_ledger.validate``.

    Values are passed through unchanged. The path is NOT
    resolve()'d or abspath()'d — the runner has already produced an
    absolute path, and any local re-resolution could mask a
    runner-vs-final-gate path disagreement that we want to surface.
    """
    ledger_fields_present = any(k in summary for k in LEDGER_KEYS)
    if not ledger_fields_present:
        return {
            "enabled": False,
            "phase_ledger_path": None,
            "claimed_phases": None,
            "expected_run_id": None,
        }
    return {
        "enabled": True,
        "phase_ledger_path": summary.get("phase_ledger_path"),
        "claimed_phases": summary.get("phase_ledger_claimed_phases"),
        "expected_run_id": summary.get("phase_ledger_expected_run_id"),
    }


# ---------------------------------------------------------------------------
# Forbidden-call guard for caller-supplied args
# ---------------------------------------------------------------------------


def _reject_admin(args: argparse.Namespace) -> None:
    """Refuse any ``allow_admin`` truthy attribute on the parsed args.

    The argparse below does not expose ``--allow-admin``. This guard
    catches the case where a caller (or a future test) constructs an
    ``argparse.Namespace`` with ``allow_admin`` set and tries to
    re-use the adapter machinery.
    """
    if getattr(args, "allow_admin", False):
        print(
            "finalize_with_phase_ledger: --allow-admin is forbidden in this adapter; "
            "the adapter hard-codes allow_admin=False.",
            file=sys.stderr,
        )
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# Main adapter entry point
# ---------------------------------------------------------------------------


def run_finalize(args: argparse.Namespace) -> int:
    """Run the adapter. Returns the process exit code (0 or 1, or 2 on error)."""
    _reject_admin(args)

    summary_path = Path(args.run_summary)
    summary = _load_run_summary(summary_path)
    if summary is None:
        # Already printed a clear stderr message in _load_run_summary.
        return 2

    version = summary.get("run_summary_version")
    if version != EXPECTED_RUN_SUMMARY_VERSION:
        # Warn but continue — forward-compat with newer schema
        # versions. We still only know how to read the v0 keys; a
        # future version with additional ledger keys would simply
        # leave them unused.
        print(
            f"finalize_with_phase_ledger: warning: unexpected run_summary_version "
            f"{version!r}; expected {EXPECTED_RUN_SUMMARY_VERSION!r}. "
            f"Continuing with v0 field set.",
            file=sys.stderr,
        )

    ledger = _extract_ledger_args(summary)

    # Normalize --allowed_files here (single source of truth) so the
    # gate always receives a clean ``list[str]`` (or None), whether
    # the caller came in via the CLI (raw comma-separated string)
    # or via a hand-built ``Namespace`` (already a list, None, or
    # raw string). This is the Codex P2 fix on PR #392: previously
    # only the CLI path parsed, so a direct caller could hand a raw
    # string to the gate and out-of-scope files would be accepted.
    allowed_files = _parse_allowed_files(getattr(args, "allowed_files", None))

    gate: dict = aed_final_gate.run_final_gate(
        pr_number=args.pr_number,
        expected_head_sha=args.expected_head_sha,
        allowed_files=allowed_files,
        local_validation_path=args.local_validation_path,
        codex_artifact_path=args.codex_artifact_path,
        output_json_path=args.output_json,
        output_md_path=args.output_md,
        allow_admin=False,  # hard-coded; never overridden
        allow_codex_skip=bool(args.allow_codex_skip),
        require_persistent_guard=bool(args.require_persistent_guard),
        persistent_guard_root=args.persistent_guard_root,
        persistent_guard_snapshot=args.persistent_guard_snapshot,
        persistent_guard_compare_json=args.persistent_guard_compare_json,
        persistent_guard_compare_md=args.persistent_guard_compare_md,
        phase_ledger_path=ledger["phase_ledger_path"] if ledger["enabled"] else None,
        claimed_phases=ledger["claimed_phases"] if ledger["enabled"] else None,
        require_phase_ledger=bool(ledger["enabled"]),
        phase_ledger_expected_run_id=(
            ledger["expected_run_id"] if ledger["enabled"] else None
        ),
    )

    print(json.dumps(gate, indent=2, default=str))
    recommendation = gate.get("final_recommendation")
    return 0 if recommendation == "MERGE_READY" else 1


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finalize_with_phase_ledger",
        description=(
            "Leaf adapter: reads aed.run_summary.v0 run_summary.json and forwards "
            "its phase_ledger fields into aed_final_gate.run_final_gate(). "
            "Default-off when no phase_ledger_* keys are present; fail-closed "
            "(via aed_final_gate) when any ledger field is present but evidence "
            "is missing, empty, stale, or malformed."
        ),
    )
    parser.add_argument(
        "--run-summary",
        required=True,
        help="Path to aed.run_summary.v0 run_summary.json produced by the runner.",
    )
    parser.add_argument(
        "--pr-number",
        required=True,
        type=int,
        help="PR number for the final gate.",
    )
    parser.add_argument(
        "--expected-head-sha",
        required=True,
        help="Expected head SHA for the final gate (exact-head check).",
    )
    parser.add_argument(
        "--allowed-files",
        required=True,
        help=(
            "Comma-separated list of allowed file globs (passed to "
            "aed_final_gate for scope validation)."
        ),
    )
    parser.add_argument(
        "--local-validation-path",
        required=True,
        help="Path to the local validation JSON (tests_collected/passed/exit_code).",
    )
    parser.add_argument(
        "--codex-artifact-path",
        required=True,
        help="Path to the Codex review artifact (codex.md).",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to write FINAL_GATE.json (passed through unchanged).",
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path to write FINAL_GATE.md (passed through unchanged).",
    )
    parser.add_argument(
        "--allow-codex-skip",
        action="store_true",
        help="Forwarded to aed_final_gate.run_final_gate(allow_codex_skip=...).",
    )
    parser.add_argument(
        "--require-persistent-guard",
        action="store_true",
        help="Forwarded to aed_final_gate.run_final_gate(require_persistent_guard=...).",
    )
    parser.add_argument(
        "--persistent-guard-root",
        default="/home/max/.hermes",
        help="Forwarded to aed_final_gate.run_final_gate(persistent_guard_root=...).",
    )
    parser.add_argument(
        "--persistent-guard-snapshot",
        default=None,
        help="Forwarded to aed_final_gate.run_final_gate(persistent_guard_snapshot=...).",
    )
    parser.add_argument(
        "--persistent-guard-compare-json",
        default=None,
        help="Forwarded to aed_final_gate.run_final_gate(persistent_guard_compare_json=...).",
    )
    parser.add_argument(
        "--persistent-guard-compare-md",
        default=None,
        help="Forwarded to aed_final_gate.run_final_gate(persistent_guard_compare_md=...).",
    )
    # NOTE: --allow-admin is intentionally NOT exposed. The adapter
    # hard-codes allow_admin=False at the run_final_gate call site.
    return parser


def _parse_allowed_files(raw: Any) -> Optional[list[str]]:
    """Normalize the ``--allowed-files`` arg into a clean ``list[str]`` (or None).

    The argument is a comma-separated string on the CLI path
    (e.g. ``"scripts/**,tests/**"``). When ``run_finalize`` is called
    directly with a hand-built ``Namespace`` the value may already be
    a ``list[str]`` or ``None``. This helper is the single source of
    truth for normalizing all three shapes:

    * ``None``  → ``None`` (the gate treats ``None`` as "no scope
      constraint" via its ``Optional[list[str]]`` parameter type).
    * ``list[str]`` → returned unchanged (idempotent pass-through;
      double-parsing a list would break).
    * ``str``    → split on ``,``, strip whitespace from each
      segment, drop empty segments, return as ``list[str]``. This
      preserves the prior CLI-path behavior of dropping trailing
      commas and other empty entries.
    * anything else → ``TypeError`` (loud failure for caller bugs;
      a direct ``Namespace(allowed_files=42)`` should not silently
      degrade).

    Centralizing the normalization here means ``run_finalize`` is
    correct whether invoked via ``main()`` (CLI path) or directly
    (test/programmatic path). This is the Codex P2 fix on PR #392:
    previously the CLI path parsed and the direct-call path did not,
    so a direct caller passing a comma-separated string would have
    the gate iterate the string character-by-character, making
    single-character glob patterns like ``"*"`` match every changed
    file (out-of-scope files incorrectly accepted).
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        # Idempotent: a list is already the canonical shape. We do
        # not re-parse the elements (no comma-split, no strip) so the
        # caller's intended globs are forwarded byte-for-byte.
        return raw
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    raise TypeError(
        f"finalize_with_phase_ledger: --allowed-files must be a "
        f"comma-separated string, a list[str], or None; got "
        f"{type(raw).__name__}: {raw!r}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Note: ``args.allowed_files`` is intentionally NOT pre-parsed
    # here. Parsing happens inside ``run_finalize`` via
    # ``_parse_allowed_files``, which is the single source of truth
    # for all callers (CLI path AND direct-call path). This avoids
    # the previous P2 finding where a hand-built ``Namespace`` with
    # a raw comma-separated string could bypass normalization.
    return run_finalize(args)


# ---------------------------------------------------------------------------
# Module-level self-check on import
# ---------------------------------------------------------------------------


_source = Path(__file__).read_text(encoding="utf-8")
_violations = _forbidden_self_check(_source)
if _violations:
    # Fail loud at import time so any regression in the adapter's
    # safety surface is caught immediately. The ``__main__`` guard
    # below also re-runs this check; the import-time check is
    # belt-and-suspenders for ``from finalize_with_phase_ledger import ...``.
    raise RuntimeError(
        "finalize_with_phase_ledger: forbidden executable pattern(s) found in source: "
        + "; ".join(_violations)
    )


if __name__ == "__main__":
    sys.exit(main())
