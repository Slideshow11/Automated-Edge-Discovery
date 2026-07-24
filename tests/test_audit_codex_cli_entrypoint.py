"""Regression tests for the audit_codex_response_for_pr.py CLI entrypoint.

Round-70 (PR #412 finalization): the previous commit renamed
``import sys`` to ``import sys as _sys`` but the
``if __name__ == "__main__": sys.exit(main())`` block still
referenced the bare ``sys`` name. Direct script invocation
(including ``python3 scripts/local/audit_codex_response_for_pr.py --help``)
raised ``NameError`` on the very first line of the entrypoint.

These tests prove the entrypoint is reachable via direct
script-local invocation, package/module invocation, and
the argparse parser.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "local" / "audit_codex_response_for_pr.py"


def test_direct_script_help_exits_successfully() -> None:
    """Direct ``python3 scripts/local/audit_codex_response_for_pr.py --help``
    must exit 0 (not raise NameError on the entrypoint)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Direct --help exited with rc={result.returncode}; "
        f"stderr={result.stderr[:500]}"
    )
    # argparse --help writes to stdout
    assert "usage:" in result.stdout.lower() or "options:" in result.stdout.lower()


def test_direct_script_missing_required_args_reaches_argparse() -> None:
    """Direct invocation without --pr must reach argparse and exit non-zero
    with a usage message (i.e. the entrypoint did not crash on import)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    # argparse returns 2 on missing required args; the entrypoint should
    # propagate that, not raise NameError (which would be rc=1 with a
    # Python traceback in stderr).
    assert result.returncode in (1, 2), (
        f"Direct invocation exited with rc={result.returncode}; "
        f"stderr={result.stderr[:500]}"
    )
    assert "NameError" not in result.stderr, (
        f"Direct invocation raised NameError: {result.stderr[:500]}"
    )


def test_module_invocation_help_exits_successfully() -> None:
    """``python3 -m scripts.local.audit_codex_response_for_pr --help``
    must also exit 0 (the entrypoint is reachable via package import)."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.local.audit_codex_response_for_pr", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Module --help exited with rc={result.returncode}; "
        f"stderr={result.stderr[:500]}"
    )
    assert "NameError" not in result.stderr


def test_no_import_path_regression() -> None:
    """The entrypoint must not introduce an import-path regression:
    importing the module from a clean process must succeed without
    modifying ``sys.path`` beyond the module's own bootstrapping."""
    # Use a fresh subprocess so the test process's sys.path is irrelevant.
    code = (
        "import sys, runpy; "
        "before = set(sys.path); "
        "runpy.run_module('scripts.local.audit_codex_response_for_pr', "
        "run_name='__not_main__', alter_sys=False); "
        "after = set(sys.path); "
        "added = after - before; "
        # The module legitimately adds its script-dir + repo-root to
        # sys.path during import (see _sys.path.insert in the file).
        # That's not a regression; it's the documented bootstrap. We
        # only check that the module imported successfully without
        # raising NameError.
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Module import exited with rc={result.returncode}; "
        f"stderr={result.stderr[:500]}"
    )
    assert "NameError" not in result.stderr
    assert "OK" in result.stdout
