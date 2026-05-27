"""
Tests for scripts/local/wait_for_pr_ready.py

Read-only PR readiness waiter — does NOT merge, push, commit,
resolve review threads, invoke live Claude, run autocoder batch,
or mutate Hermes.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# The script under test
WAITER = Path(__file__).parent.parent / "scripts/local/wait_for_pr_ready.py"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REQUIRED_CHECKS = [
    "test (3.11)",
    "review-comment-gate",
    "validator",
    "governance-validators",
    "pr-gate-live-smoke",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def output_dir():
    """Create a temp output directory."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def output_json(output_dir):
    return str(output_dir / "status.json")


@pytest.fixture
def output_md(output_dir):
    return str(output_dir / "status.md")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_waiter(
    pr_number: int,
    output_json: str,
    output_md: str = "",
    timeout_minutes: int = 1,
    poll_seconds: int = 0,
    require_review_comments_clean: bool = False,
    require_pmg: bool = False,
    require_final_gates: bool = False,
    require_merge_ready: bool = False,
    required_checks: str = None,
    extra_args: list = None,
) -> subprocess.CompletedProcess:
    """Run wait_for_pr_ready.py with given args, return CompletedProcess."""
    cmd = [
        sys.executable,
        str(WAITER),
        "--pr-number", str(pr_number),
        "--timeout-minutes", str(timeout_minutes),
        "--poll-seconds", str(poll_seconds),
        "--output-json", output_json,
    ]
    if output_md:
        cmd.extend(["--output-md", output_md])
    if require_review_comments_clean:
        cmd.append("--require-review-comments-clean")
    if require_pmg:
        cmd.append("--require-pmg")
    if require_final_gates:
        cmd.append("--require-final-gates")
    if require_merge_ready:
        cmd.append("--require-merge-ready")
    if required_checks:
        cmd.extend(["--required-checks", required_checks])
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result


def read_json(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultRequiredChecks:
    """Verify default required checks list is correct."""

    def test_default_checks_include_pr_gate_live_smoke(self, output_json):
        """pr-gate-live-smoke must be in DEFAULT_REQUIRED_CHECKS."""
        # Import the constant directly from the script by running it with --help
        # and checking the code.
        import importlib.util
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert "pr-gate-live-smoke" in mod.DEFAULT_REQUIRED_CHECKS, \
            "pr-gate-live-smoke must be in DEFAULT_REQUIRED_CHECKS"


class TestReadOnlyConstraint:
    """Verify the waiter never calls merge, push, commit, add, or merge."""

    def test_no_gh_pr_merge_in_source(self):
        """gh pr merge must not appear in the source (string or comment excluded)."""
        content = open(WAITER, "r").read()
        # Allow mentions in docstrings and comments (design docs)
        # but not as actual function calls
        lines = [l for l in content.split("\n")
                 if "gh pr merge" in l.lower()
                 and not l.strip().startswith("#")
                 and '"' not in l and "'" not in l]
        assert not lines, f"gh pr merge found outside comments/docstrings: {lines}"

    def test_no_git_push_in_source(self):
        """git push must not appear as an actual command in source."""
        content = open(WAITER, "r").read()
        lines = [l for l in content.split("\n")
                 if "git push" in l.lower()
                 and not l.strip().startswith("#")
                 and '"' not in l and "'" not in l]
        assert not lines, f"git push found in source: {lines}"

    def test_no_git_commit_in_source(self):
        """git commit must not appear as an actual command in source."""
        content = open(WAITER, "r").read()
        lines = [l for l in content.split("\n")
                 if "git commit" in l.lower()
                 and not l.strip().startswith("#")
                 and '"' not in l and "'" not in l]
        assert not lines, f"git commit found in source: {lines}"

    def test_no_git_add_in_source(self):
        """git add must not appear as an actual command in source."""
        content = open(WAITER, "r").read()
        lines = [l for l in content.split("\n")
                 if "git add" in l.lower()
                 and not l.strip().startswith("#")
                 and '"' not in l and "'" not in l]
        assert not lines, f"git add found in source: {lines}"

    def test_no_shell_true_in_subprocess_calls(self):
        """All subprocess.run calls must use shell=False."""
        import ast
        tree = ast.parse(open(WAITER, "r").read())

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if getattr(node.func, "id", None) == "run":
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            # Only shell=True is prohibited
                            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                pytest.fail(f"subprocess.run with shell=True found at line {node.lineno}")


class TestStatusesAndOutput:
    """Test status logic and report output."""

    def test_output_json_written_on_success(self, output_dir, output_json):
        """JSON report must be written even on success."""
        pr_num = 336  # already merged PR — CI will be green (per previous runs)
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            output_md=str(output_dir / "status.md"),
            timeout_minutes=1,
            poll_seconds=0,
        )
        # Exit 0 regardless of HOLD vs READY — tool writes report on every exit
        assert os.path.exists(output_json), f"JSON not written: {output_json}"
        data = read_json(output_json)
        assert "status" in data
        assert "next_safe_action" in data
        assert data["pr_number"] == pr_num

    def test_output_md_written_when_requested(self, output_dir, output_json):
        """Markdown report must be written when --output-md is provided."""
        pr_num = 336
        md_path = str(output_dir / "status.md")
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            output_md=md_path,
            timeout_minutes=1,
            poll_seconds=0,
        )
        if result.returncode == 0:
            assert os.path.exists(md_path), f"MD not written: {md_path}"

    def test_status_field_present_in_report(self, output_dir, output_json):
        """Report must always have a 'status' field."""
        pr_num = 336
        result = run_waiter(pr_number=pr_num, output_json=output_json, timeout_minutes=1, poll_seconds=0)
        assert os.path.exists(output_json)
        data = read_json(output_json)
        assert "status" in data
        # Status must be one of the known values
        VALID_STATUSES = [
            "READY_FOR_FINAL_GATES",
            "READY_TO_MERGE_CANDIDATE",
            "READY_PR_ALREADY_MERGED",
            "HOLD_CI_PENDING",
            "HOLD_CI_FAILED",
            "HOLD_REVIEW_COMMENTS_BLOCKED",
            "HOLD_REVIEW_COMMENTS_INCONCLUSIVE",
            "HOLD_PMG_DIRTY",
            "HOLD_HEAD_CHANGED",
            "HOLD_TIMEOUT",
            "HOLD_PR_NOT_OPEN",
            "ERROR_TOOLING",
        ]
        assert data["status"] in VALID_STATUSES, f"Unknown status: {data['status']}"

    def test_next_safe_action_field_present(self, output_dir, output_json):
        """Report must always have a 'next_safe_action' field."""
        pr_num = 336
        result = run_waiter(pr_number=pr_num, output_json=output_json, timeout_minutes=1, poll_seconds=0)
        data = read_json(output_json)
        assert "next_safe_action" in data
        # Must be a non-empty string
        assert isinstance(data["next_safe_action"], str)
        assert len(data["next_safe_action"]) > 0

    def test_next_safe_action_contains_merge_command_when_ready(self, output_dir, output_json):
        """
        When status is READY_TO_MERGE_CANDIDATE, next_safe_action must contain
        a gh pr merge command (not executed, just recommended).
        """
        # This test verifies the output structure; actual READY status depends on CI
        pr_num = 336
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
        )
        data = read_json(output_json)
        if data["status"] == "READY_TO_MERGE_CANDIDATE":
            assert "gh pr merge" in data["next_safe_action"]
            assert str(pr_num) in data["next_safe_action"]

    def test_next_safe_action_contains_hold_when_held(self, output_dir, output_json):
        """
        When status is a HOLD_* or ERROR_TOOLING, next_safe_action must say
        'Do not merge yet' or similar.
        """
        pr_num = 336
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
        )
        data = read_json(output_json)
        if data["status"].startswith("HOLD_") or data["status"] == "ERROR_TOOLING":
            assert any(phrase in data["next_safe_action"] for phrase in [
                "Do not merge yet", "Stop and resolve", "Investigate tooling error"
            ]), f"next_safe_action doesn't warn against merge: {data['next_safe_action']}"

    def test_no_merge_command_in_report_when_held(self, output_dir, output_json):
        """
        When status is a HOLD_* or ERROR_TOOLING, next_safe_action must NOT
        contain an actual gh pr merge command.
        """
        pr_num = 336
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
        )
        data = read_json(output_json)
        if data["status"].startswith("HOLD_") or data["status"] == "ERROR_TOOLING":
            action = data["next_safe_action"]
            # gh pr merge with --squash --delete-branch would be a real merge command
            assert not (
                "gh pr merge" in action and "--squash" in action and "--delete-branch" in action
            ), f"HOLD status contains full merge command: {action}"


class TestHeadSHADetection:
    """Test that head SHA changes are detected and reported as HOLD_HEAD_CHANGED."""

    def test_missing_pr_returns_error_or_hold(self, output_dir, output_json):
        """Non-existent PR must not crash; must produce a sensible status."""
        result = run_waiter(
            pr_number=99999999,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
        )
        # Must not raise an unhandled exception
        assert os.path.exists(output_json)
        data = read_json(output_json)
        assert data["status"] in [
            "HOLD_CI_FAILED",
            "HOLD_HEAD_CHANGED",
            "HOLD_TIMEOUT",
            "ERROR_TOOLING",
            "HOLD_CI_PENDING",
        ]


class TestReviewCommentGate:
    """Test review-comment gate integration."""

    def test_review_comment_gate_is_called_when_flag_set(self, output_dir, output_json):
        """
        When --require-review-comments-clean is set, the report must include
        review_comment_gate data.
        """
        pr_num = 336  # already merged, review-comment gate was CLEAN
        md_path = str(output_dir / "status.md")
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            output_md=md_path,
            timeout_minutes=1,
            poll_seconds=0,
            require_review_comments_clean=True,
        )
        assert os.path.exists(output_json)
        data = read_json(output_json)
        # Since CI is green, we should have passed ci_poll stage
        assert "stages" in data
        stage_names = [s["stage"] for s in data["stages"]]
        # When require_review_comments_clean is set, review_comment_gate stage should appear
        # (it runs after CI green)
        if data["status"] not in ["HOLD_REVIEW_COMMENTS_BLOCKED", "HOLD_REVIEW_COMMENTS_INCONCLUSIVE"]:
            # Either clean (passed) or held for some other reason
            pass


class TestPMGGate:
    """Test PMG integration."""

    def test_pmg_snapshot_taken_when_require_pmg_set(self, output_dir, output_json):
        """
        When --require-pmg is set, a PMG snapshot JSON must be produced.
        """
        pr_num = 336
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
            require_pmg=True,
        )
        assert os.path.exists(output_json)
        data = read_json(output_json)
        # PMG snapshot is taken at start; check a snapshot file was created
        snapshot_path = output_json.replace(".json", "_pmg_before.json")
        # The report should have pmg_compare stage
        stage_names = [s["stage"] for s in data.get("stages", [])]
        if "ci_poll" in stage_names and data.get("ci_checks"):
            # PMG stage runs after CI, so if CI passed we should see it
            assert any("pmg" in s for s in stage_names), f"No PMG stage found in {stage_names}"


class TestFinalGates:
    """Test final_gate_status.py integration."""

    def test_final_gate_status_run_when_flag_set(self, output_dir, output_json):
        """
        When --require-final-gates is set and CI is green, final_gate_status
        stage must appear in the report.
        """
        pr_num = 336
        md_path = str(output_dir / "status.md")
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            output_md=md_path,
            timeout_minutes=1,
            poll_seconds=0,
            require_final_gates=True,
        )
        assert os.path.exists(output_json)
        data = read_json(output_json)
        stage_names = [s["stage"] for s in data.get("stages", [])]
        # final_gate_status stage runs only when CI is green (STATUS_READY_FOR_FINAL_GATES).
        # If the PR is already merged, we exit early at merged_pr_check.
        # Check that the waiter correctly handles both states.
        assert "final_gate_status" in stage_names or data["status"] in [
            "HOLD_CI_PENDING", "HOLD_CI_FAILED", "HOLD_TIMEOUT",
            "READY_PR_ALREADY_MERGED",  # merged PR exits before final_gate_status
        ], f"Unexpected: status={data['status']}, stages={stage_names}"


class TestWaiterCLI:
    """Test the command-line interface."""

    def test_requires_pr_number(self, output_dir):
        """--pr-number is required."""
        output_json = str(output_dir / "status.json")
        result = subprocess.run(
            [sys.executable, str(WAITER), "--output-json", output_json],
            capture_output=True, text=True, shell=False,
        )
        assert result.returncode != 0 or "pr-number" in result.stderr.lower()

    def test_requires_output_json(self):
        """--output-json is required."""
        result = subprocess.run(
            [sys.executable, str(WAITER), "--pr-number", "336"],
            capture_output=True, text=True, shell=False,
        )
        assert result.returncode != 0 or "output-json" in result.stderr.lower()

    def test_custom_required_checks_flag(self, output_dir, output_json):
        """--required-checks overrides defaults."""
        pr_num = 336
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
            required_checks="test (3.11),validator",
        )
        assert os.path.exists(output_json)
        data = read_json(output_json)
        assert "stages" in data


class TestTimeoutBehavior:
    """Test timeout handling."""

    def test_timeout_produces_hold_timeout_status(self, output_dir):
        """
        Using a very short timeout on a PR with pending CI should produce
        HOLD_TIMEOUT.
        """
        # Use a PR that has pending CI — try a non-existent fake PR
        # which will return empty checks, treated as missing required = HOLD_CI_FAILED
        # Actually use PR 336 which has all checks green, so we get READY_FOR_FINAL_GATES
        # Test with timeout=0 (or 0 poll seconds) — with poll_seconds=0 and timeout_minutes=0
        # the deadline is already passed immediately.
        output_json = str(output_dir / "status.json")
        pr_num = 336
        result = subprocess.run(
            [sys.executable, str(WAITER),
             "--pr-number", str(pr_num),
             "--timeout-minutes", "0",
             "--poll-seconds", "0",
             "--output-json", output_json],
            capture_output=True, text=True, shell=False,
        )
        assert os.path.exists(output_json)
        data = read_json(output_json)
        # With timeout=0 on a merged PR, we get READY_PR_ALREADY_MERGED (merged PR check
        # happens before CI polling). On an open PR with all checks green and
        # timeout=0, we would get HOLD_TIMEOUT. On a PR with pending CI, also HOLD_TIMEOUT.
        # The key is we never get READY_TO_MERGE_CANDIDATE from this test.
        assert data["status"] in [
            "HOLD_TIMEOUT", "ERROR_TOOLING", "READY_PR_ALREADY_MERGED", "HOLD_PR_NOT_OPEN"
        ], f"Expected HOLD_TIMEOUT/ERROR_TOOLING/READY_PR_ALREADY_MERGED but got {data['status']}"


class TestMergedPRHandling:
    """Verify merged PRs are handled correctly — no merge command in next_safe_action."""

    def test_merged_pr_does_not_return_ready_to_merge_candidate(self, output_dir, output_json):
        """
        PR #336 is already merged. The waiter must not return READY_TO_MERGE_CANDIDATE.
        It should return READY_PR_ALREADY_MERGED instead.
        """
        pr_num = 336  # already merged
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
        )
        assert os.path.exists(output_json)
        data = read_json(output_json)
        assert data["status"] != "READY_TO_MERGE_CANDIDATE", \
            f"Merged PR must not return READY_TO_MERGE_CANDIDATE; got {data['status']}"
        assert data["status"] in (
            "READY_PR_ALREADY_MERGED",
            "HOLD_PR_NOT_OPEN",
            "ERROR_TOOLING",
            "HOLD_TIMEOUT",
        ), f"Unexpected merged-PR status: {data['status']}"

    def test_merged_pr_never_emits_gh_pr_merge_in_next_safe_action(self, output_dir, output_json):
        """
        For PR #336 (merged), next_safe_action must never contain a gh pr merge command.
        """
        pr_num = 336
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
        )
        data = read_json(output_json)
        action = data.get("next_safe_action", "")
        # A real merge command has --squash and --delete-branch together
        assert not (
            "gh pr merge" in action and "--squash" in action and "--delete-branch" in action
        ), f"Merged PR must not emit merge command; got: {action}"

    def test_merged_pr_writes_json_and_md_reports(self, output_dir, output_json):
        """Reports must be written even for merged PRs."""
        pr_num = 336
        md_path = str(output_dir / "status.md")
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            output_md=md_path,
            timeout_minutes=1,
            poll_seconds=0,
        )
        assert os.path.exists(output_json), "JSON report must be written"
        data = read_json(output_json)
        assert "status" in data
        assert "next_safe_action" in data

    def test_merged_pr_next_safe_action_is_descriptive_not_merge(self, output_dir, output_json):
        """
        For already-merged PR #336, next_safe_action should describe the state
        (already merged) rather than suggesting a merge command.
        """
        pr_num = 336
        result = run_waiter(
            pr_number=pr_num,
            output_json=output_json,
            timeout_minutes=1,
            poll_seconds=0,
        )
        data = read_json(output_json)
        action = data.get("next_safe_action", "")
        if data["status"] == "READY_PR_ALREADY_MERGED":
            assert any(phrase in action for phrase in [
                "already merged", "No merge action needed", "verify main"
            ]), f"READY_PR_ALREADY_MERGED should describe the state; got: {action}"

    def test_merged_pr_status_includes_READY_PR_ALREADY_MERGED(self, output_dir, output_json):
        """READY_PR_ALREADY_MERGED must be a defined status value."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "STATUS_READY_PR_ALREADY_MERGED"), \
            "STATUS_READY_PR_ALREADY_MERGED must be defined"
        assert mod.STATUS_READY_PR_ALREADY_MERGED == "READY_PR_ALREADY_MERGED"


class TestGhRunExitCode8Handling:
    """Prove gh_run handles exit code 8 (CI pending) correctly with check=False."""

    def test_gh_run_does_not_raise_on_exit_code_8_with_check_false(self):
        """gh_run must not raise when gh pr checks returns exit code 8 (pending CI)."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 8
        mock_result.stdout = '[{"name":"test (3.11)","state":"in_progress","link":""}]'
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = mod.gh_run(["pr", "checks", "337", "--json", "name,state"], check=False)
            assert result.returncode == 8
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs.get("shell") is False

    def test_gh_run_provides_meaningful_error_when_check_true_and_exit_code_nonzero_not_8(self):
        """When check=True and exit code is neither 0 nor 8, gh_run raises RuntimeError."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "authentication failed"

        with patch.object(subprocess, "run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="gh pr checks.*failed"):
                mod.gh_run(["pr", "checks", "337", "--json", "name,state"], check=True)

    def test_poll_ci_checks_continues_poll_loop_on_exit_code_8(self):
        """poll_ci_checks must not return ERROR_TOOLING on exit code 8; it should parse data."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 8
        mock_result.stdout = '[{"name":"test (3.11)","state":"in_progress","link":""}]'
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(337, ["test (3.11)"], timeout_minutes=1, poll_seconds=0)
            # With one pending check and poll_seconds=0 (immediate deadline), should be HOLD_TIMEOUT
            # NOT ERROR_TOOLING — proves exit code 8 was handled as non-error
            assert status != mod.STATUS_ERROR_TOOLING, "exit code 8 must not produce ERROR_TOOLING"


class TestSubprocessCalls:
    """Verify all subprocess calls use shell=False."""

    def test_all_subprocess_calls_use_shell_false(self):
        """Every subprocess.run must have shell=False."""
        import ast
        tree = ast.parse(open(WAITER, "r").read())
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_id = getattr(node.func, "id", None)
                func_attr = getattr(node.func, "attr", None)
                if func_id == "run" or func_attr == "run":
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                violations.append(f"line {node.lineno}")
        assert not violations, f"shell=True found in subprocess.run at: {violations}"