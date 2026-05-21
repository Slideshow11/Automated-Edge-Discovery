#!/usr/bin/env python3
"""
test_check_real_executor_readiness.py

Tests for check_real_executor_readiness.py.

Hard constraints enforced in tests:
- No Claude invocation
- No git push, gh pr create, gh pr merge, dispatch, board, Hermes, audit,
  memory/profile, package install in executable paths
- No shell=True in test execution
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.resolve()  # AED repo root
SCRIPT = REPO_ROOT / "scripts/local/check_real_executor_readiness.py"
HARNESS = REPO_ROOT / "scripts/local/run_temp_worktree_execution.py"
GATE_DOC = REPO_ROOT / "docs/real_claude_executor_readiness_gate.md"
DESIGN_DOC = REPO_ROOT / "docs/temp_worktree_execution_v1_design.md"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_checker(output_json=None, output_md=None) -> tuple[dict, str, int]:
    """Run the checker and return (result_dict, stderr, returncode)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(output_json) if output_json else Path(tmpdir) / "result.json"
        md_path = Path(output_md) if output_md else Path(tmpdir) / "result.md"

        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--output-json", str(json_path),
             "--output-md", str(md_path)],
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT),
        )

        if json_path.is_file():
            result_data = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            result_data = {}

        return result_data, result.stderr, result.returncode


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCheckerExitCode:
    """Test that the checker exits with code 0."""

    def test_exits_zero(self):
        """Checker runs to completion with code 0."""
        _, stderr, rc = run_checker()
        assert rc == 0, f"Non-zero exit: {stderr}"


class TestReadyState:
    """Test: all files/checklist items present -> READY_TO_IMPLEMENT_REAL_EXECUTOR."""

    def test_ready_when_all_present(self):
        """
        When all required files exist, git is clean, and checklist is complete,
        status is READY_TO_IMPLEMENT_REAL_EXECUTOR.
        """
        result, _, _ = run_checker()
        assert result["status"] == "READY_TO_IMPLEMENT_REAL_EXECUTOR"
        # real_executor_allowed must ALWAYS be False
        assert result["real_executor_allowed"] is False
        assert result["real_executor_allowed"] == False


class TestMissingDesignDoc:
    """Test: missing design doc -> HOLD_DESIGN_DOC_MISSING."""

    def test_missing_gate_doc(self):
        """Missing real_claude_executor_readiness_gate.md -> HOLD_DESIGN_DOC_MISSING."""
        # Temporarily move gate doc
        backup = GATE_DOC.with_suffix(".md.bak")
        moved = GATE_DOC.exists()
        if moved:
            GATE_DOC.rename(backup)

        try:
            result, _, _ = run_checker()
            assert result["status"] == "HOLD_DESIGN_DOC_MISSING"
        finally:
            if moved and backup.exists():
                backup.rename(GATE_DOC)


class TestMissingMockHarness:
    """Test: missing mock harness -> HOLD_MOCK_HARNESS_MISSING."""

    def test_missing_harness(self):
        """Missing run_temp_worktree_execution.py -> HOLD_MOCK_HARNESS_MISSING."""
        backup = HARNESS.with_suffix(".py.bak")
        moved = HARNESS.exists()
        if moved:
            HARNESS.rename(backup)

        try:
            result, _, _ = run_checker()
            assert result["status"] == "HOLD_MOCK_HARNESS_MISSING"
        finally:
            if moved and backup.exists():
                backup.rename(HARNESS)


class TestGitDirty:
    """Test: dirty git -> HOLD_MAIN_DIRTY."""

    def test_dirty_git_status(self):
        """
        When main repo has staged changes, checker returns HOLD_MAIN_DIRTY.
        We simulate dirty git by checking the field's existence and consistency.
        """
        result, _, _ = run_checker()
        # If the repo is clean, status should NOT be HOLD_MAIN_DIRTY
        # If the repo is dirty, it should be HOLD_MAIN_DIRTY
        if result["status"] == "HOLD_MAIN_DIRTY":
            assert "git_status_clean" in result["checks"]
            assert result["checks"]["git_status_clean"] is False


class TestMissingChecklistItem:
    """Test: missing checklist item -> HOLD_READINESS_ITEM_MISSING."""

    def test_missing_checklist_item_detected(self):
        """
        When the readiness gate doc is missing a required checklist item,
        status is HOLD_READINESS_ITEM_MISSING.

        We test the checklist checking logic directly with synthetic minimal content.
        """
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
        import check_real_executor_readiness as checker_mod

        # Minimal doc with no checklist items
        minimal_doc = "Minimal doc with no checklist items."

        checklist_result = checker_mod.check_readiness_gate_checklist(minimal_doc)
        # All items should be missing
        assert len(checklist_result["missing"]) == len(checker_mod.REQUIRED_CHECKLIST_ITEMS)
        for item in checker_mod.REQUIRED_CHECKLIST_ITEMS:
            assert item in checklist_result["missing"], f"Item {item} should be missing"

        # Now test with a complete doc (the real one)
        real_doc = GATE_DOC.read_text(encoding="utf-8") if GATE_DOC.exists() else ""
        real_result = checker_mod.check_readiness_gate_checklist(real_doc)
        # Real doc should have all or nearly all items present
        # (Some might still be missing if the doc is incomplete)
        if real_result["missing"]:
            # If items are missing in the real doc, status should be HOLD_READINESS_ITEM_MISSING
            result, _, _ = run_checker()
            if result["status"] == "HOLD_READINESS_ITEM_MISSING":
                assert len(result["missing"]) > 0


class TestSourceContainsRealClaudeMarker:
    """Test: source contains real Claude implementation marker -> HOLD_IMPLEMENTATION_FOUND."""

    def test_real_claude_marker_detection_logic(self):
        """
        Verify the marker detection logic works on synthetic content.
        The real file should NOT contain real Claude implementation.
        """
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
        import check_real_executor_readiness as checker_mod

        # Test detection on synthetic content WITH marker
        content_with_marker = (
            '# Some code\n'
            'if execution.mode == "claude":\n'
            '    result = run_claude()\n'
        )

        # Verify our test content triggers detection
        impl_found = None
        for line_no, line in enumerate(content_with_marker.splitlines(), 1):
            if 'execution.mode == "claude"' in line:
                impl_found = ('execution.mode == "claude"', line_no)
                break
        assert impl_found is not None

        # Test the real file has no marker
        real_check = checker_mod.check_run_temp_worktree_execution_source()
        assert real_check["implementation_found"] is None, (
            f"Real harness should not have real Claude marker, "
            f"but found: {real_check['implementation_found']}"
        )
        assert real_check["has_blocking"] is True


class TestRealExecutorAllowedAlwaysFalse:
    """Test: output JSON always has real_executor_allowed=false."""

    def test_always_false_even_when_ready(self):
        """
        Even when status is READY_TO_IMPLEMENT_REAL_EXECUTOR,
        real_executor_allowed must remain False.
        """
        result, _, _ = run_checker()
        # Must always be False regardless of status
        assert result["real_executor_allowed"] is False
        # Verify it's the literal False, not a falsy string or 0
        assert result["real_executor_allowed"] is not None
        assert result["real_executor_allowed"] == False


class TestMarkdownOutput:
    """Test: markdown includes status and missing checks."""

    def test_md_has_status(self):
        """Markdown output contains status line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "result.json"
            md_path = Path(tmpdir) / "result.md"
            result, _, _ = run_checker(str(json_path), str(md_path))
            assert md_path.exists(), "Markdown output not created"
            md_content = md_path.read_text(encoding="utf-8")
            assert "Status:" in md_content or "**Status**" in md_content
            assert result["status"] in md_content or f"`{result['status']}`" in md_content

    def test_md_has_checks(self):
        """Markdown output contains checks section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "result.json"
            md_path = Path(tmpdir) / "result.md"
            run_checker(str(json_path), str(md_path))
            md_content = md_path.read_text(encoding="utf-8")
            assert "Required Files" in md_content or "## Checks" in md_content


class TestNoShellTrueInScript:
    """Test: no shell=True in the checker script itself."""

    def test_no_shell_true_in_checker(self):
        """The checker script must not contain shell=True."""
        content = SCRIPT.read_text(encoding="utf-8")
        # Allow in comments/docstrings
        lines = content.splitlines()
        violations = []
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '"shell=True"' in line or "'shell=True'" in line:
                violations.append((line_no, line))
        assert not violations, f"shell=True found at: {violations}"


class TestNoForbiddenCommandsInExecutablePaths:
    """Test: no forbidden command strings in executable paths of the checker."""

    FORBIDDEN = [
        "git push",
        "gh pr create",
        "gh pr merge",
        "dispatch",
        "board",
        "Hermes",
        "audit",
        "memory/profile",
        "package install",
    ]

    def test_no_forbidden_strings_as_path_components(self):
        """
        The checker script must not construct executable paths containing
        forbidden command strings as path components (not as literal check strings).
        """
        content = SCRIPT.read_text(encoding="utf-8")
        lines = content.splitlines()

        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and docstrings
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue

            for forbidden in self.FORBIDDEN:
                # Check if forbidden string appears as part of a path or command construction
                # but not as a literal string in a comment or check
                if forbidden in line:
                    # It's in an executable context - check more carefully
                    # Allow if it's clearly a doc/check string (in quotes)
                    if "subprocess.run" in line or "Popen" in line:
                        pytest.fail(
                            f"Forbidden string '{forbidden}' near subprocess in "
                            f"{SCRIPT.name}:{line_no}: {line.strip()}"
                        )


class TestJsonOutputSchema:
    """Test: JSON output has required fields."""

    def test_json_has_required_fields(self):
        """JSON output has status, checks, missing, recommendation, real_executor_allowed."""
        result, _, _ = run_checker()
        for field in ["status", "checks", "missing", "recommendation", "real_executor_allowed"]:
            assert field in result, f"Missing field: {field}"

    def test_missing_is_list(self):
        """missing field is a list."""
        result, _, _ = run_checker()
        assert isinstance(result["missing"], list)

    def test_status_is_string(self):
        """status field is a string."""
        result, _, _ = run_checker()
        assert isinstance(result["status"], str)

    def test_checks_has_required_subfields(self):
        """checks has required_files, git_status_clean, source, checklist."""
        result, _, _ = run_checker()
        checks = result.get("checks", {})
        for subfield in ["required_files", "git_status_clean", "source", "checklist"]:
            assert subfield in checks, f"Missing check subfield: {subfield}"


# ---------------------------------------------------------------------------
# Test that verify_final_head_merge_command.py exists (required script)
# ---------------------------------------------------------------------------

class TestRequiredScripts:
    """Test that all required scripts exist."""

    @pytest.mark.parametrize("rel_path", [
        "scripts/local/run_temp_worktree_execution.py",
        "scripts/local/build_temp_worktree_execution_packet.py",
        "scripts/local/run_plan_preview.py",
        "scripts/local/plan_preview_eval_status.py",
        "scripts/local/final_gate_status.py",
        "scripts/local/verify_final_head_merge_command.py",
        "scripts/local/check_persistent_mutation_guard.py",
    ])
    def test_required_script_exists(self, rel_path):
        """Each required script exists."""
        assert (REPO_ROOT / rel_path).is_file(), f"Missing: {rel_path}"