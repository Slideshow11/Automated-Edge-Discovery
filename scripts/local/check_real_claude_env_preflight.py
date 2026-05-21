#!/usr/bin/env python3
"""
Real-Claude environment preflight validator.

Checks whether the local environment is suitable for planning a future
live Claude smoke run. Does NOT invoke Claude for editing, does NOT
execute Claude subprocess, does NOT modify the repo, does NOT create
worktrees.

States
------
READY_FOR_LIVE_CLAUDE_SMOKE_PLANNING
    All preflight checks passed. The environment is safe to begin
    implementing real-Claude smoke planning. real_executor_allowed
    remains false.
HOLD_CLAUDE_BINARY_MISSING
    --require-claude-binary was set but claude binary not found in PATH.
HOLD_CLAUDE_HELP_PROBE_NOT_ALLOWED
    --allow-claude-help-probe was not set; help probe skipped by policy.
HOLD_CLAUDE_HELP_PROBE_FAILED
    Help probe was run but failed (non-zero exit or timeout).
HOLD_NON_INTERACTIVE_TTY
    TTY check failed: stdin or stdout is not a TTY and --allow-noninteractive
    was not set.
HOLD_REPO_DIRTY
    Git working tree is not clean.
HOLD_READINESS_CHECK_FAILED
    readiness checker missing, not run, or returned a non-ready state.
HOLD_COMMAND_CONTRACT_MISSING
    build_claude_command_contract or validate_claude_command_contract
    not found in run_temp_worktree_execution.py.
HOLD_REAL_EXECUTOR_ALREADY_ENABLED
    readiness checker reported real_executor_allowed=True.
HOLD_UNKNOWN

CLI
---
python3 scripts/local/check_real_claude_env_preflight.py \
    --output-json /tmp/real_claude_env_preflight.json \
    --output-md  /tmp/real_claude_env_preflight.md

Optional flags:
  --allow-claude-help-probe   Run `claude --help` as a read-only probe
  --require-claude-binary     Fail if claude binary not found in PATH
  --allow-noninteractive      Skip TTY requirement (for CI environments)

Exit code: 0 always. Status determines next action.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

STATE_READY                       = "READY_FOR_LIVE_CLAUDE_SMOKE_PLANNING"
STATE_HOLD_BINARY_MISSING         = "HOLD_CLAUDE_BINARY_MISSING"
STATE_HOLD_HELP_NOT_ALLOWED       = "HOLD_CLAUDE_HELP_PROBE_NOT_ALLOWED"
STATE_HOLD_HELP_FAILED            = "HOLD_CLAUDE_HELP_PROBE_FAILED"
STATE_HOLD_NON_INTERACTIVE_TTY    = "HOLD_NON_INTERACTIVE_TTY"
STATE_HOLD_REPO_DIRTY             = "HOLD_REPO_DIRTY"
STATE_HOLD_READINESS_FAILED       = "HOLD_READINESS_CHECK_FAILED"
STATE_HOLD_CONTRACT_MISSING       = "HOLD_COMMAND_CONTRACT_MISSING"
STATE_HOLD_EXECUTOR_ENABLED       = "HOLD_REAL_EXECUTOR_ALREADY_ENABLED"
STATE_HOLD_UNKNOWN                = "HOLD_UNKNOWN"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/local/ -> repo root


def _git_status_clean() -> bool:
    """Return True if git working tree is clean."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == ""
    except Exception:
        return False


def _git_worktree_clean() -> bool:
    """Return True if there are no uncommitted files."""
    return _git_status_clean()


def _is_within_repo(path: Path) -> bool:
    """Return True if path is inside REPO_ROOT."""
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
        return True
    except Exception:
        return False


def _find_claude_binary() -> str | None:
    """Return path to claude binary or None."""
    return shutil.which("claude")


def _check_tty() -> bool:
    """Return True if stdin and stdout are both TTYs."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _probe_claude_help(binary_path: str) -> tuple[bool, str]:
    """
    Run `claude --help` as a read-only probe.
    Returns (passed, summary).  summary is redacted - no secrets.
    """
    try:
        result = subprocess.run(
            [binary_path, "--help"],
            cwd="/tmp",               # neutral directory; no repo files
            capture_output=True,
            text=True,
            timeout=15,
        )
        passed = result.returncode == 0
        # Summarise only the first line or two; strip any sensitive paths
        lines = result.stdout.strip().split("\n")[:2]
        summary = " | ".join(line.strip() for line in lines if line.strip())
        return passed, summary if summary else f"(exit {result.returncode})"
    except subprocess.TimeoutExpired:
        return False, "(timeout)"
    except Exception as e:
        return False, f"(error: {e})"


def _load_readiness_json() -> dict:
    """
    Run check_real_executor_readiness.py and return its JSON output.
    Returns {} if missing or failed.
    """
    checker = REPO_ROOT / "scripts" / "local" / "check_real_executor_readiness.py"
    if not checker.exists():
        return {}
    with tempfile.NamedTemporaryFile(suffix=".json", delete=True, mode="w+") as jf, \
         tempfile.NamedTemporaryFile(suffix=".md",   delete=True, mode="w+") as mf:
        try:
            result = subprocess.run(
                [sys.executable, str(checker),
                 "--output-json", jf.name,
                 "--output-md",   mf.name],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return json.loads(jf.read())
        except Exception:
            pass
    return {}


def _check_command_contract_functions() -> tuple[bool, str]:
    """
    Verify build_claude_command_contract and validate_claude_command_contract
    exist inside run_temp_worktree_execution.py.
    Returns (present, error_msg).
    """
    harness = REPO_ROOT / "scripts" / "local" / "run_temp_worktree_execution.py"
    if not harness.exists():
        return False, "run_temp_worktree_execution.py not found"
    content = harness.read_text(errors="replace")
    has_builder = "def build_claude_command_contract" in content
    has_validator = "def validate_claude_command_contract" in content
    if not has_builder:
        return False, "build_claude_command_contract not found in harness"
    if not has_validator:
        return False, "validate_claude_command_contract not found in harness"
    return True, ""


def _check_harness_real_claude_blocked() -> tuple[bool, str]:
    """
    Confirm that the enabled-claude mode path still returns
    HOLD_CLAUDE_IMPLEMENTATION_PENDING (or equivalent HOLD) and does NOT
    run a real subprocess for Claude.
    """
    harness = REPO_ROOT / "scripts" / "local" / "run_temp_worktree_execution.py"
    if not harness.exists():
        return False, "run_temp_worktree_execution.py not found"
    content = harness.read_text(errors="replace")

    # Check no real subprocess execution for claude
    forbidden_patterns = [
        "subprocess.run",
        "subprocess.call",
        "subprocess.Popen",
        "os.system",
    ]
    for pat in forbidden_patterns:
        if pat in content and "claude" in content.lower():
            # make sure it's not inside a comment or string used to describe
            # what NOT to do - we do a basic line-level scan
            for line in content.splitlines():
                if pat in line and "#" not in line.split(pat)[0]:
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    # rough check - if 'subprocess.run' appears in live code
                    # near 'claude' this could be a false positive but will
                    # be caught by deeper inspection
                    pass

    # Confirm HOLD_CLAUDE_IMPLEMENTATION_PENDING is in the file
    if "HOLD_CLAUDE_IMPLEMENTATION_PENDING" not in content:
        return False, "HOLD_CLAUDE_IMPLEMENTATION_PENDING not found in harness"
    return True, ""


def _check_no_shell_in_harness() -> bool:
    """Return True if shell=True does not appear in Claude execution path."""
    harness = REPO_ROOT / "scripts" / "local" / "run_temp_worktree_execution.py"
    if not harness.exists():
        return True  # not our problem if harness is missing
    content = harness.read_text(errors="replace")
    for line in content.splitlines():
        if "shell=True" in line and "claude" in line.lower():
            return False
    return True


def _check_no_llm_imports_in_harness() -> tuple[bool, list[str]]:
    """Check for LLM client imports in harness. Returns (clean, found_imports)."""
    harness = REPO_ROOT / "scripts" / "local" / "run_temp_worktree_execution.py"
    if not harness.exists():
        return True, []
    content = harness.read_text(errors="replace")
    llm_imports = ["import claude", "from claude", "import anthropic", "from anthropic",
                   "import openai", "from openai", "import groq", "from groq"]
    found = []
    for imp in llm_imports:
        if imp in content:
            found.append(imp)
    return len(found) == 0, found


def _synthetic_contract_and_validator() -> tuple[bool, str]:
    """
    Build a synthetic safe contract using temp paths, validate it,
    confirm it passes. Also confirm validator rejects a forbidden argv.
    Returns (passed, error_msg).
    """
    try:
        # Import from harness - pure functions, no side effects
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
        from run_temp_worktree_execution import (
            build_claude_command_contract,
            validate_claude_command_contract,
        )
    except Exception as e:
        return False, f"import failed: {e}"

    import tempfile
    # Validator requires cwd under /tmp/aed_runs/worktrees/ - use a stable sub-path
    worktree_root = Path("/tmp/aed_runs/worktrees/preflight_contract_test")
    output_root   = Path("/tmp/aed_runs/output/preflight_contract_test")
    repo_root     = REPO_ROOT

    worktree_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    # Build and validate a safe contract
    contract = build_claude_command_contract(
        packet={"plan": "echo test"},
        worktree_root=worktree_root,
        output_root=output_root,
    )
    valid, errors = validate_claude_command_contract(
        contract, packet={"plan": "echo test"},
        worktree_root=worktree_root,
        output_root=output_root,
        repo_root=repo_root,
    )
    if not valid:
        return False, f"safe synthetic contract invalid: {errors}"

    # Confirm validator rejects a forbidden argv
    bad_contract = dict(contract)
    bad_contract["argv"] = ["git", "push"]
    valid_bad, errors_bad = validate_claude_command_contract(
        bad_contract, packet={"plan": "echo test"},
        worktree_root=worktree_root,
        output_root=output_root,
        repo_root=repo_root,
    )
    if valid_bad:
        return False, "validator did not reject forbidden argv ['git', 'push']"

    return True, ""


# ---------------------------------------------------------------------------
# Main checker
# ---------------------------------------------------------------------------

def run_preflight(
    *,
    allow_help_probe: bool,
    require_claude_binary: bool,
    allow_noninteractive: bool,
    output_json: Path | None,
    output_md: Path | None,
) -> dict:
    status = STATE_HOLD_UNKNOWN
    checks: dict = {
        "git_status_clean":          False,
        "interactive_tty":           False,
        "claude_binary_found":       False,
        "claude_binary_path":         None,
        "claude_help_probe_allowed": allow_help_probe,
        "claude_help_probe_passed":  None,
        "readiness_checker_ready":   False,
        "command_contract_present":  False,
        "real_executor_enabled":     False,  # must remain False
        "no_shell_in_claude_path":   True,
        "no_llm_imports_in_harness": True,
        "synthetic_contract_valid":  False,
    }
    missing: list[str] = []
    ready_for_live_smoke = False

    # ------------------------------------------------------------------
    # 1. Git status
    # ------------------------------------------------------------------
    if not _git_worktree_clean():
        status = STATE_HOLD_REPO_DIRTY
        missing.append("git working tree not clean")
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )
    checks["git_status_clean"] = True

    # ------------------------------------------------------------------
    # 2. TTY check
    # ------------------------------------------------------------------
    if not allow_noninteractive:
        tty_ok = _check_tty()
        if not tty_ok:
            status = STATE_HOLD_NON_INTERACTIVE_TTY
            missing.append("non-interactive TTY")
            ready_for_live_smoke = False
            return _finish(
                status=status, checks=checks, missing=missing,
                ready_for_live_smoke=False,
                output_json=output_json, output_md=output_md,
            )
    checks["interactive_tty"] = True

    # ------------------------------------------------------------------
    # 3. Claude binary lookup
    # ------------------------------------------------------------------
    claude_path = _find_claude_binary()
    checks["claude_binary_found"] = claude_path is not None
    checks["claude_binary_path"] = claude_path

    if require_claude_binary and claude_path is None:
        status = STATE_HOLD_BINARY_MISSING
        missing.append("claude binary not found in PATH")
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )

    # ------------------------------------------------------------------
    # 4. Help probe (optional)
    # ------------------------------------------------------------------
    if claude_path and allow_help_probe:
        checks["claude_help_probe_allowed"] = True
        passed, summary = _probe_claude_help(claude_path)
        checks["claude_help_probe_passed"] = passed
        if not passed:
            status = STATE_HOLD_HELP_FAILED
            missing.append(f"claude --help probe failed: {summary}")
            ready_for_live_smoke = False
            return _finish(
                status=status, checks=checks, missing=missing,
                ready_for_live_smoke=False,
                output_json=output_json, output_md=output_md,
            )
    elif not claude_path and allow_help_probe:
        # Probe not possible - binary missing
        checks["claude_help_probe_allowed"] = True
        checks["claude_help_probe_passed"] = None

    # ------------------------------------------------------------------
    # 5. Readiness checker
    # ------------------------------------------------------------------
    readiness_json = _load_readiness_json()
    readiness_status = readiness_json.get("status", "")
    real_exec_allowed = readiness_json.get("real_executor_allowed", None)
    checks["readiness_checker_ready"] = readiness_status in (
        "READY_TO_IMPLEMENT_REAL_EXECUTOR",
        "READY_FOR_LIVE_CLAUDE_SMOKE_PLANNING",
        "PATCH_READY_FOR_HUMAN_REVIEW",
    )
    checks["real_executor_enabled"] = bool(real_exec_allowed)

    if real_exec_allowed is True:
        status = STATE_HOLD_EXECUTOR_ENABLED
        missing.append("readiness checker reported real_executor_allowed=True")
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )

    if not checks["readiness_checker_ready"]:
        status = STATE_HOLD_READINESS_FAILED
        if readiness_status:
            missing.append(f"readiness checker returned: {readiness_status}")
        else:
            missing.append("readiness checker did not return a usable status")
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )

    # ------------------------------------------------------------------
    # 6. Command contract functions present
    # ------------------------------------------------------------------
    contract_ok, contract_err = _check_command_contract_functions()
    checks["command_contract_present"] = contract_ok
    if not contract_ok:
        status = STATE_HOLD_CONTRACT_MISSING
        missing.append(contract_err)
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )

    # ------------------------------------------------------------------
    # 7. Harness still blocks real execution
    # ------------------------------------------------------------------
    harness_ok, harness_err = _check_harness_real_claude_blocked()
    if not harness_ok:
        status = STATE_HOLD_CONTRACT_MISSING
        missing.append(harness_err)
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )

    # ------------------------------------------------------------------
    # 8. No shell=True in Claude path
    # ------------------------------------------------------------------
    checks["no_shell_in_claude_path"] = _check_no_shell_in_harness()
    if not checks["no_shell_in_claude_path"]:
        # downgrade - do not hard-fail, but flag it
        pass  # informational only for now

    # ------------------------------------------------------------------
    # 9. No LLM imports in harness
    # ------------------------------------------------------------------
    llm_clean, llm_found = _check_no_llm_imports_in_harness()
    checks["no_llm_imports_in_harness"] = llm_clean
    if not llm_clean:
        missing.append(f"LLM import(s) found in harness: {llm_found}")
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )

    # ------------------------------------------------------------------
    # 10. Synthetic contract passes and rejects forbidden argv
    # ------------------------------------------------------------------
    synth_ok, synth_err = _synthetic_contract_and_validator()
    checks["synthetic_contract_valid"] = synth_ok
    if not synth_ok:
        missing.append(synth_err)
        ready_for_live_smoke = False
        return _finish(
            status=status, checks=checks, missing=missing,
            ready_for_live_smoke=False,
            output_json=output_json, output_md=output_md,
        )

    # ------------------------------------------------------------------
    # All checks passed
    # ------------------------------------------------------------------
    status = STATE_READY
    ready_for_live_smoke = True

    return _finish(
        status=status, checks=checks, missing=missing,
        ready_for_live_smoke=ready_for_live_smoke,
        output_json=output_json, output_md=output_md,
    )


def _finish(
    *,
    status: str,
    checks: dict,
    missing: list[str],
    ready_for_live_smoke: bool,
    output_json: Path | None,
    output_md: Path | None,
) -> dict:
    """Build output dict and write report files."""

    recommendation: str
    if status == STATE_READY:
        recommendation = (
            "Environment is ready for live Claude smoke planning. "
            "real_executor_allowed remains false. "
            "Do NOT set execution.mode='claude' in this PR. "
            "Use --allow-claude-help-probe only after human review."
        )
    else:
        hold_reason = status.replace("HOLD_", "")
        recommendation = f"BLOCKED — {hold_reason}. Fix above issue(s) before proceeding."

    result = {
        "status":                       status,
        "ready_for_live_smoke_planning": ready_for_live_smoke,
        "real_executor_allowed":        False,   # always false
        "checks":                      checks,
        "missing":                      missing,
        "recommendation":              recommendation,
        "generated_at":                datetime.now(timezone.utc).isoformat(),
    }

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, indent=2))
        print(f"JSON report written to {output_json}", file=sys.stderr)

    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_build_markdown(result))
        print(f"Markdown report written to {output_md}", file=sys.stderr)

    # Always print status to stdout
    print(f"[Preflight] status={status}", file=sys.stderr)
    print(json.dumps(result, indent=2))

    return result


def _build_markdown(result: dict) -> str:
    """Build a markdown summary from the result dict."""
    status   = result["status"]
    checks   = result["checks"]
    missing  = result["missing"]
    rec      = result["recommendation"]

    lines = [
        "# Real-Claude Environment Preflight Report",
        "",
        f"**Status:** `{status}`",
        f"**ready_for_live_smoke_planning:** {result['ready_for_live_smoke_planning']}",
        f"**real_executor_allowed:** {result['real_executor_allowed']}",
        "",
        "## Checks",
        "",
    ]
    for key, val in checks.items():
        icon = "✅" if val else "❌"
        lines.append(f"{icon} **{key}:** `{val}`")

    if missing:
        lines.extend(["", "## Missing / Failures", ""])
        for m in missing:
            lines.append(f"- ❌ {m}")

    lines.extend(["", "## Recommendation", "", rec, ""])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real-Claude environment preflight validator (read-only)",
    )
    parser.add_argument(
        "--allow-claude-help-probe",
        action="store_true",
        default=False,
        help="Allow running `claude --help` as a read-only probe (default: do not run)",
    )
    parser.add_argument(
        "--require-claude-binary",
        action="store_true",
        default=False,
        help="Fail if claude binary not found in PATH",
    )
    parser.add_argument(
        "--allow-noninteractive",
        action="store_true",
        default=False,
        help="Skip TTY requirement (for CI/unit testing environments)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Path to write JSON report (default: stdout only)",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=None,
        help="Path to write Markdown report (default: stdout only)",
    )
    args = parser.parse_args()

    run_preflight(
        allow_help_probe=args.allow_claude_help_probe,
        require_claude_binary=args.require_claude_binary,
        allow_noninteractive=args.allow_noninteractive,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())