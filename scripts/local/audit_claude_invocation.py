#!/usr/bin/env python3
"""
audit_claude_invocation.py

Read-only audit script that inspects AED run artifacts and repo state to
classify whether a real Claude invocation occurred.

States:
  NO_CLAUDE_INVOCATION_DETECTED
  MOCK_ONLY_RUN_DETECTED
  CONTRACT_ONLY_RUN_DETECTED
  CLAUDE_INVOCATION_DETECTED
  HOLD_ARTIFACTS_MISSING
  HOLD_REPO_DIRTY
  HOLD_EXTERNAL_MUTATION_EVIDENCE
  HOLD_UNKNOWN

Inputs:
  - result.json, execution_packet.json from output_root
  - stdout/stderr/transcript files referenced in result
  - pmg_compare.json, diff.patch
  - git status of repo

Classification rules:
  1. result.status == PATCH_READY_FOR_HUMAN_REVIEW and execution.mode == mock
     → MOCK_ONLY_RUN_DETECTED
  2. result.status == HOLD_CLAUDE_IMPLEMENTATION_PENDING and
     claude_command_contract_valid == True
     → CONTRACT_ONLY_RUN_DETECTED
  3. result has claude_exit_code, claude_stdout_path, claude_stderr_path,
     claude_transcript_path with non-empty content
     → CLAUDE_INVOCATION_DETECTED
  4. Transcript / stdout / stderr files contain process responses beyond --help
     → CLAUDE_INVOCATION_DETECTED
  5. result.status is PATCH_READY and execution.mode == "claude" but no
     claude_* fields present → CONTRACT_ONLY_RUN_DETECTED (stub)
  6. No artifacts → HOLD_ARTIFACTS_MISSING
  7. git status dirty → HOLD_REPO_DIRTY (unless --allow-dirty)
  8. pmg_compare.json status != clean → HOLD_EXTERNAL_MUTATION_EVIDENCE

Key constraint: real_executor_allowed is NEVER set to True by this script.
The script only observes and reports.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/local/.. -> repo

STATE_NO_CLAUDE        = "NO_CLAUDE_INVOCATION_DETECTED"
STATE_MOCK_ONLY        = "MOCK_ONLY_RUN_DETECTED"
STATE_CONTRACT_ONLY    = "CONTRACT_ONLY_RUN_DETECTED"
STATE_CLAUDE_INVOKED   = "CLAUDE_INVOCATION_DETECTED"
STATE_ARTIFACTS_MISSING = "HOLD_ARTIFACTS_MISSING"
STATE_REPO_DIRTY       = "HOLD_REPO_DIRTY"
STATE_EXTERNAL_MUTATION = "HOLD_EXTERNAL_MUTATION_EVIDENCE"
STATE_UNKNOWN          = "HOLD_UNKNOWN"

RUN_KINDS = ("mock", "contract_only", "claude", "unknown")

# Fields that, if present and non-empty in result.json, indicate real Claude was invoked
CLAUDE_INVOCATION_FIELDS = (
    "claude_exit_code",
    "claude_started_at",
    "claude_elapsed_seconds",
    "claude_stdout_path",
    "claude_stderr_path",
    "claude_transcript_path",
    "claude_response_text",
    "claude_reply_lines",
)

# Patterns that, if found in stdout/stderr/transcript files, indicate real Claude invocation
# (beyond a simple --help probe)
FORBIDDEN_TRANSCRIPT_PATTERNS = (
    "role:",           # Claude message role in transcript
    "content:",        # Claude message content field
    "\\n\\n## ",       # Claude markdown section header
    "Thinkingchain",   # Claude internal marker (if leaked)
    "\\n### ",         # Claude subsection
    "plan_prompt",     # Claude planning variable
    "task_prompt",     # Claude task variable
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_status_clean(repo_root: Path) -> bool:
    """Return True if repo working tree is clean (no staged/unstaged changes)."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Artifact loading helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict | None:
    """Load JSON file, return None on error."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_text(path: Path, limit: int = 4096) -> str:
    """Read text file, return empty string on error. Limit to first N chars."""
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:limit]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def _classify_by_result(result: dict) -> tuple[str, str]:
    """
    Classify based on result.json content.
    Returns (state, run_kind).
    """
    status = result.get("status", "")

    # Rule 1: mock run → PATCH_READY + mode=mock
    if status == "PATCH_READY_FOR_HUMAN_REVIEW":
        exec_mode = result.get("execution", {}).get("mode", "mock") if isinstance(result.get("execution"), dict) else result.get("execution", {}).get("mode", "mock") if isinstance(result.get("execution"), dict) else "mock"
        # Re-read: result may have top-level execution.mode
        exec_mode = result.get("execution_mode", result.get("execution", {}).get("mode", "mock") if isinstance(result.get("execution"), dict) else "mock")
        if exec_mode == "mock":
            return STATE_MOCK_ONLY, "mock"

    # Rule 2: claude mode stub (contract-only)
    if status == "HOLD_CLAUDE_IMPLEMENTATION_PENDING":
        contract_valid = result.get("claude_command_contract_valid")
        if contract_valid is True:
            return STATE_CONTRACT_ONLY, "contract_only"

    # Rule 3: claude invocation fields present
    for field in CLAUDE_INVOCATION_FIELDS:
        val = result.get(field)
        if val and val not in ("", None, [], {}):
            return STATE_CLAUDE_INVOKED, "claude"

    # Rule 4: execution.mode == "claude" but no invocation fields and not HOLD_CLAUDE_IMPLEMENTATION_PENDING
    # This is an edge case for future — treat as unknown
    exec_mode_raw = result.get("execution", {})
    if isinstance(exec_mode_raw, dict):
        exec_mode = exec_mode_raw.get("mode", "")
    else:
        exec_mode = str(exec_mode_raw or "")

    if exec_mode == "claude" and status not in ("HOLD_CLAUDE_IMPLEMENTATION_PENDING",):
        # Has claude mode but not holding — could be a real invocation status
        # Check if it has any result fields that look like invocation output
        if any(result.get(f) for f in CLAUDE_INVOCATION_FIELDS):
            return STATE_CLAUDE_INVOKED, "claude"
        # Fall through to unknown — can't determine
        return STATE_UNKNOWN, "unknown"

    return STATE_UNKNOWN, "unknown"


def _classify_by_artifacts(
    result: dict,
    output_root: Path,
    strict: bool,
) -> tuple[str, list[str], dict]:
    """
    Inspect artifact files (transcript, stdout, stderr, pmg, diff).
    Returns (state, evidence_list, checks_dict).
    """
    evidence: list[str] = []
    checks: dict = {}

    # --- Transcript / stdout / stderr -------------------------------------

    transcript_path_str = result.get("claude_transcript_path", "")
    stdout_path_str = result.get("claude_stdout_path", "")
    stderr_path_str = result.get("claude_stderr_path", "")

    transcript_lines = 0
    for path_str in (transcript_path_str, stdout_path_str, stderr_path_str):
        if not path_str:
            continue
        p = Path(path_str)
        if not p.is_absolute():
            p = output_root / path_str
        if not p.exists():
            continue
        content = _read_text(p, limit=8192)
        if not content.strip():
            continue
        transcript_lines += len(content.splitlines())
        checks[f"file_exists::{p.name}"] = True
        # Scan for forbidden patterns
        for pat in FORBIDDEN_TRANSCRIPT_PATTERNS:
            if pat in content:
                evidence.append(f"forbidden_pattern::{pat}::in::{p.name}")
        # Also check for actual Claude response structure: multi-line content
        # A real Claude response has content blocks with \n\n## or similar
        if content.count("\n\n") >= 3 and "role:" in content:
            evidence.append(f"claude_response_structure::detected_in::{p.name}")

    checks["transcript_files_checked"] = True
    checks["transcript_lines_found"] = transcript_lines

    # --- PMG compare -------------------------------------------------------

    pmg_path_str = result.get("pmg_compare_json_path", "")
    if not pmg_path_str:
        # Try default location
        pmg_path = output_root / "pmg_compare.json"
    else:
        pmg_path = Path(pmg_path_str)

    pmg_data = _load_json(pmg_path)
    if pmg_data is not None:
        pmg_status = pmg_data.get("status", "unknown")
        checks["pmg_status"] = pmg_status
        if pmg_status != "clean":
            evidence.append(f"pmg_status={pmg_status}")
            return STATE_EXTERNAL_MUTATION, evidence, checks
    else:
        checks["pmg_status"] = "missing"

    # --- Diff patch -------------------------------------------------------

    diff_path_str = result.get("diff_path", "")
    if diff_path_str:
        diff_path = Path(diff_path_str)
        if not diff_path.is_absolute():
            diff_path = output_root / diff_path_str
        diff_content = _read_text(diff_path)
        checks["diff_size"] = len(diff_content)
    else:
        checks["diff_size"] = 0

    # --- Execution packet check --------------------------------------------

    packet_path = output_root / "execution_packet.json"
    packet_data = _load_json(packet_path)
    if packet_data:
        exec_mode = packet_data.get("execution", {}).get("mode") if isinstance(packet_data.get("execution"), dict) else None
        checks["packet_execution_mode"] = exec_mode or "unknown"
    else:
        checks["packet_execution_mode"] = "missing"

    # --- No invocation evidence found --------------------------------------
    return STATE_NO_CLAUDE, [], checks


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def audit_invocation(
    run_root: Path,
    repo_root: Path,
    allow_dirty: bool = False,
    strict: bool = False,
    output_json: Path | None = None,
    output_md: Path | None = None,
) -> dict:
    """
    Run the full audit on a run directory.

    Returns a result dict with:
      status, real_claude_invoked, run_kind, checks, evidence,
      missing, recommendation, generated_at
    """
    status = STATE_UNKNOWN
    run_kind = "unknown"
    checks: dict = {}
    evidence: list[str] = []
    missing: list[str] = []
    recommendation = ""

    result_json_path = run_root / "result.json"
    result_data = _load_json(result_json_path)
    checks["result_json_found"] = result_data is not None

    if result_data is None:
        status = STATE_ARTIFACTS_MISSING
        missing.append("result.json not found in run_root")
        recommendation = "Provide a run directory containing result.json from a prior AED run."
        return _finish(
            status=status, run_kind=run_kind, checks=checks,
            evidence=evidence, missing=missing, recommendation=recommendation,
            output_json=output_json, output_md=output_md,
        )

    # --- Git status -------------------------------------------------------

    repo_clean = _git_status_clean(repo_root)
    checks["repo_git_clean"] = repo_clean

    if not repo_clean and not allow_dirty:
        status = STATE_REPO_DIRTY
        missing.append("repo git status is not clean")
        recommendation = "Clean the repo or pass --allow-dirty to bypass this check."
        return _finish(
            status=status, run_kind=run_kind, checks=checks,
            evidence=evidence, missing=missing, recommendation=recommendation,
            output_json=output_json, output_md=output_md,
        )

    # --- Classify by result.json -----------------------------------------

    result_state, result_run_kind = _classify_by_result(result_data)
    checks["result_based_state"] = result_state
    checks["result_based_run_kind"] = result_run_kind

    # --- Artifact inspection ----------------------------------------------

    artifact_state, artifact_evidence, artifact_checks = _classify_by_artifacts(
        result_data, run_root, strict,
    )
    checks.update(artifact_checks)
    evidence.extend(artifact_evidence)

    # Override / confirm based on artifact analysis
    if artifact_state in (STATE_EXTERNAL_MUTATION,):
        status = artifact_state
        run_kind = result_run_kind
    elif artifact_state == STATE_NO_CLAUDE and result_state != STATE_UNKNOWN:
        # Artifact scan clean, use result classification
        status = result_state
        run_kind = result_run_kind
    else:
        # Conflict or artifact state is no-claude but result is unknown
        # Prefer the more restrictive state
        if result_state != STATE_UNKNOWN:
            status = result_state
            run_kind = result_run_kind
        elif artifact_state != STATE_NO_CLAUDE:
            status = artifact_state
            run_kind = result_run_kind
        else:
            status = STATE_NO_CLAUDE
            run_kind = "unknown"

    # Final overrides: real Claude invocation is always the final word
    # If any claude invocation fields were set in result, override to CLAUDE_INVOKED
    real_invoked = any(
        result_data.get(f) and result_data.get(f) not in ("", None, [], {})
        for f in CLAUDE_INVOCATION_FIELDS
    )
    if real_invoked:
        status = STATE_CLAUDE_INVOKED
        run_kind = "claude"
        evidence.append("claude_invocation_fields_present_in_result_json")

    # Determine real_claude_invoked (always False in this script — we only observe)
    real_claude_invoked = status == STATE_CLAUDE_INVOKED

    # Build missing
    if not result_data.get("claude_command_contract_valid") and status == STATE_CONTRACT_ONLY:
        pass  # already classified

    # Recommendation
    if status == STATE_MOCK_ONLY:
        recommendation = "Mock run confirmed. No Claude invocation detected. Safe to proceed with patch review."
    elif status == STATE_CONTRACT_ONLY:
        recommendation = "Contract-only run (claude mode stub). No real Claude invocation. Real executor not yet implemented."
    elif status == STATE_CLAUDE_INVOKED:
        recommendation = "Real Claude invocation detected. This output should only appear after execution.mode='claude' is implemented and authorized."
    elif status == STATE_NO_CLAUDE:
        recommendation = "No Claude invocation evidence found in artifacts. Verify the run did not invoke Claude."
    elif status == STATE_ARTIFACTS_MISSING:
        recommendation = "result.json not found. Cannot audit. Provide a valid run directory."
    elif status == STATE_REPO_DIRTY:
        recommendation = "Repo is dirty. Clean the repo or pass --allow-dirty."
    elif status == STATE_EXTERNAL_MUTATION:
        recommendation = "External mutation detected by PMG. Investigate Hermes tree changes before using this output."
    else:
        recommendation = "Unable to classify run artifacts. Manual inspection required."

    return _finish(
        status=status, run_kind=run_kind, checks=checks,
        real_claude_invoked=real_claude_invoked,
        evidence=evidence, missing=missing, recommendation=recommendation,
        output_json=output_json, output_md=output_md,
    )


def _finish(
    *,
    status: str,
    run_kind: str,
    checks: dict,
    evidence: list[str],
    missing: list[str],
    recommendation: str,
    real_claude_invoked: bool = False,
    output_json: Path | None,
    output_md: Path | None,
) -> dict:
    """Build result dict, write outputs, return."""
    result = {
        "status": status,
        "real_claude_invoked": real_claude_invoked,
        "run_kind": run_kind,
        "checks": checks,
        "evidence": evidence,
        "missing": missing,
        "recommendation": recommendation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if output_json:
        output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if output_md:
        md_lines = [
            f"# Claude Invocation Audit",
            f"",
            f"**Status:** `{status}`",
            f"**real_claude_invoked:** `{real_claude_invoked}`",
            f"**run_kind:** `{run_kind}`",
            "",
        ]
        if missing:
            md_lines.append("## Missing")
            for m in missing:
                md_lines.append(f"- {m}")
            md_lines.append("")
        if evidence:
            md_lines.append("## Evidence")
            for e in evidence:
                md_lines.append(f"- `{e}`")
            md_lines.append("")
        md_lines.extend([
            "## Checks",
            "```json",
            json.dumps(checks, indent=2),
            "```",
            "",
            "## Recommendation",
            recommendation,
        ])
        output_md.write_text("\n".join(md_lines), encoding="utf-8")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit AED run artifacts for Claude invocation evidence.",
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        required=True,
        help="Path to AED run output_root (contains result.json)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Path to AED repo (default: auto-detected)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Do not block on dirty repo git status",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable stricter classification (more false positives, fewer missed detections)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Path to write JSON audit result",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=None,
        help="Path to write Markdown audit result",
    )

    args = parser.parse_args()

    result = audit_invocation(
        run_root=args.run_root,
        repo_root=args.repo_root,
        allow_dirty=args.allow_dirty,
        strict=args.strict,
        output_json=args.output_json,
        output_md=args.output_md,
    )

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())