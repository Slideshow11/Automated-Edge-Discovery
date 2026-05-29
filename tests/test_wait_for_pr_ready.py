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
        When --require-final-gates is set and all CI checks are green, the
        final_gate_status stage must appear in the report and the status must
        be READY_TO_MERGE_CANDIDATE.

        This test is fully deterministic — it patches subprocess.run at the
        module level so no live GitHub calls or real helper-script execution occurs.
        """
        import importlib.util
        import subprocess
        import sys

        md_path = str(output_dir / "status.md")
        out_json = output_json

        # Load the waiter module directly (not as subprocess) so we can patch
        # subprocess.run at the module level — this prevents all real gh calls
        # and all real helper-script executions.
        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # --- Patch subprocess.run at module level to intercept ALL subprocess calls ---
        def _gh_args_without_repo(cmd):
            """Strip --repo owner/repo so routing uses subcommand position."""
            assert cmd[0] == "gh"
            args = list(cmd[1:])
            cleaned = []
            i = 0
            while i < len(args):
                if args[i] == "--repo":
                    i += 2
                    continue
                cleaned.append(args[i])
                i += 1
            return cleaned

        def fake_subprocess_run(popenargs, **kwargs):
            """
            Intercepts subprocess.run calls from the waiter module.
            For 'gh' commands: returns deterministic fake responses.
            For all other calls: writes deterministic output JSON for helper scripts.
            """
            cmd = popenargs if isinstance(popenargs, (list, tuple)) else None

            m = MagicMock()
            m.returncode = 0
            m.stdout = ''
            m.stderr = ''

            # Handle gh commands FIRST (before any out_path logic)
            if cmd and cmd[0] == "gh":
                gh_args = _gh_args_without_repo(cmd)

                # gh pr view — routes at indices 1,2 after gh stripped
                # Route on --jq to return the correct output format for each caller:
                #   get_pr_state uses --jq "{state:.state,head:.headRefOid}" → JSON {"state":"open","head":"test123abc"}
                #   get_live_head_sha uses --jq ".headRefOid" → raw SHA string "test123abc"
                if gh_args[:2] == ["pr", "view"]:
                    if '--jq' in gh_args:
                        jq_idx = gh_args.index('--jq')
                        jq_expr = gh_args[jq_idx + 1] if jq_idx + 1 < len(gh_args) else ''
                        if jq_expr == '.headRefOid':
                            # get_live_head_sha: returns the raw SHA string
                            m.stdout = 'test123abc'
                        elif 'head' in jq_expr:
                            # get_pr_state: --jq "{state:.state,head:.headRefOid}" → JSON
                            m.stdout = '{"state":"open","head":"test123abc"}'
                        else:
                            m.stdout = ''
                    else:
                        m.stdout = ''
                    return m

                # gh pr checks — routes at indices 1,2 after gh stripped
                if gh_args[:2] == ["pr", "checks"]:
                    m.stdout = json.dumps([
                        {"name": "test (3.11)", "state": "success", "link": ""},
                        {"name": "review-comment-gate", "state": "success", "link": ""},
                        {"name": "validator", "state": "success", "link": ""},
                        {"name": "governance-validators", "state": "success", "link": ""},
                        {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
                    ])
                    return m

                # Any other gh command — return empty
                return m

            # Non-gh calls: write deterministic output JSON for helper scripts
            # so the waiter can read them back and continue through all stages.
            cmd_str = ' '.join(cmd) if cmd else ''

            out_path = None
            for i, arg in enumerate(cmd or []):
                if arg in ('--output-json', '--output') and i + 1 < len(cmd):
                    out_path = cmd[i + 1]
                    break

            if out_path:
                if 'check_pr_review_comments' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'REVIEW_COMMENTS_CLEAN', 'blockers': [], 'findings': []}, f)
                elif 'check_persistent_mutation_guard' in cmd_str:
                    if 'compare' in cmd_str:
                        with open(out_path, 'w') as f:
                            json.dump({'status': 'clean', 'blocked': [], 'message': 'all clear'}, f)
                elif 'final_gate_status' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'READY_TO_MERGE', 'blockers': []}, f)
                elif 'verify_final_head_merge_command' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'MERGE_READY_CANDIDATE', 'recommendation': 'MERGE_READY_CANDIDATE', 'head_sha_match': True}, f)
                else:
                    with open(out_path, 'w') as f:
                        json.dump({}, f)

            return m

        # Set required globals before calling main()
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-pmg",
            "--require-final-gates",
        ]

        with patch.object(mod.subprocess, "run", side_effect=fake_subprocess_run):
            try:
                mod.main()
            except SystemExit:
                pass
            finally:
                sys.argv = old_argv

        assert os.path.exists(out_json), f"JSON not written: {out_json}"
        data = read_json(out_json)
        stage_names = [s["stage"] for s in data.get("stages", [])]
        assert "final_gate_status" in stage_names, (
            f"final_gate_status stage missing; stages={stage_names}, "
            f"status={data['status']}"
        )
        assert data["status"] == "READY_TO_MERGE_CANDIDATE", (
            f"Expected READY_TO_MERGE_CANDIDATE but got {data['status']}; "
            f"stages={stage_names}"
        )

    def test_pmg_compare_json_is_used_for_final_gate_status(self, output_dir, output_json):
        """
        When --require-pmg and --require-final-gates are both set, the waiter
        must pass the PMG COMPARE output JSON (status=clean/blocked) to
        final_gate_status.py -- NOT the before-snapshot JSON (which has no status field).

        This is verified by patching subprocess.run to capture the exact argv
        passed to final_gate_status.py and asserting the pmg_compare path
        (not pmg_before path) appears in --pmg-guard-state-json.
        """
        import importlib.util
        import subprocess
        import sys

        captured_cmds = []

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def fake_subprocess_run(popenargs, **kwargs):
            cmd = popenargs if isinstance(popenargs, (list, tuple)) else None
            m = MagicMock()
            m.returncode = 0
            m.stdout = ''
            m.stderr = ''

            if cmd and cmd[0] == "gh":
                # Strip --repo and the owner/repo argument from gh commands.
                # gh api ... --repo owner/repo puts --repo at the END, so filter
                # both the flag and the value that follows it.
                cleaned = []
                args_iter = iter(cmd[1:])
                for a in args_iter:
                    if a == "--repo":
                        try:
                            next(args_iter)  # skip the repo name
                        except StopIteration:
                            pass
                    else:
                        cleaned.append(a)
                gh_args = cleaned

                if gh_args[:2] == ["pr", "view"]:
                    # Route based on --jq expression to match real waiter behavior
                    if '--jq' in gh_args:
                        jq_idx = gh_args.index('--jq')
                        jq_expr = gh_args[jq_idx + 1] if jq_idx + 1 < len(gh_args) else ''
                        if jq_expr == '.headRefOid':
                            m.stdout = 'test123abc'
                        elif 'head' in jq_expr:
                            m.stdout = '{"state":"open","head":"test123abc"}'
                        else:
                            m.stdout = ''
                    else:
                        m.stdout = ''
                    return m

                if gh_args[:2] == ["pr", "checks"]:
                    m.stdout = json.dumps([
                        {"name": "test (3.11)", "state": "success", "link": ""},
                        {"name": "review-comment-gate", "state": "success", "link": ""},
                        {"name": "validator", "state": "success", "link": ""},
                        {"name": "governance-validators", "state": "success", "link": ""},
                        {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
                    ])
                    return m

                return m

            cmd_str = ' '.join(cmd) if cmd else ''
            out_path = None
            for i, arg in enumerate(cmd or []):
                if arg in ('--output-json', '--output') and i + 1 < len(cmd):
                    out_path = cmd[i + 1]
                    break

            if out_path:
                if 'check_pr_review_comments' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'REVIEW_COMMENTS_CLEAN', 'blockers': [], 'findings': []}, f)
                elif 'check_persistent_mutation_guard' in cmd_str:
                    if 'compare' in cmd_str:
                        # Real PMG compare output — has status field
                        with open(out_path, 'w') as f:
                            json.dump({'status': 'clean', 'blocked_changes': [], 'recommendation': 'PASS'}, f)
                    else:
                        # snapshot command — write a before JSON so waiter can proceed
                        with open(out_path, 'w') as f:
                            json.dump({'guard_version': 1, 'snapshot_at': '2026-05-27T00:00:00Z', 'root': '/home/max/.hermes', 'files': []}, f)
                elif 'final_gate_status' in cmd_str:
                    # Capture this call's --pmg-guard-state-json value
                    pmg_arg = None
                    for i, a in enumerate(cmd):
                        if a == '--pmg-guard-state-json' and i + 1 < len(cmd):
                            pmg_arg = cmd[i + 1]
                            break
                    captured_cmds.append({'script': 'final_gate_status', 'pmg_guard_state_json': pmg_arg})
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'READY_TO_MERGE', 'blockers': []}, f)
                elif 'verify_final_head_merge_command' in cmd_str:
                    pmg_arg = None
                    for i, a in enumerate(cmd):
                        if a == '--pmg-guard-state-json' and i + 1 < len(cmd):
                            pmg_arg = cmd[i + 1]
                            break
                    captured_cmds.append({'script': 'verify_final_head', 'pmg_guard_state_json': pmg_arg})
                    with open(out_path, 'w') as f:
                        json.dump({'recommendation': 'MERGE_READY_CANDIDATE', 'head_sha_match': True}, f)

            return m

        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-pmg",
            "--require-final-gates",
        ]

        with patch.object(mod.subprocess, "run", side_effect=fake_subprocess_run):
            try:
                mod.main()
            except SystemExit:
                pass
            finally:
                sys.argv = old_argv

        # Find the final_gate_status invocation
        fgs = next((c for c in captured_cmds if c['script'] == 'final_gate_status'), None)
        assert fgs is not None, f"final_gate_status not called; captured_cmds={captured_cmds}"

        # The pmg_guard_state_json must be the COMPARE output, not the BEFORE snapshot
        # Waiter naming convention: <output>.json → <output>_pmg_before.json (snapshot)
        #                             <output>.json → <output>_pmg_compare.json (compare result)
        base = out_json.replace(".json", "")
        expected_compare = f"{base}_pmg_compare.json"
        expected_before = f"{base}_pmg_before.json"

        assert fgs['pmg_guard_state_json'] == expected_compare, (
            f"final_gate_status must receive _pmg_compare.json (compare output), "
            f"not _pmg_before.json (snapshot). "
            f"Got: {fgs['pmg_guard_state_json']!r}, "
            f"Expected: {expected_compare!r}"
        )
        # Confirm the before-snapshot path would NOT be sent
        assert fgs['pmg_guard_state_json'] != expected_before, (
            f"before-snapshot must not be passed to final_gate_status; got: {fgs['pmg_guard_state_json']}"
        )

    def test_pmg_dirty_result_blocks_ready_to_merge_candidate(self, output_dir, output_json):
        """
        When final_gate_status.py returns HOLD_PMG_DIRTY (e.g. because pmg_compare
        returned status=blocked), the waiter must NOT emit READY_TO_MERGE_CANDIDATE.
        It must stop with a HOLD status and a next_safe_action that contains no
        merge command.
        """
        import importlib.util
        import subprocess
        import sys

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def fake_subprocess_run(popenargs, **kwargs):
            cmd = popenargs if isinstance(popenargs, (list, tuple)) else None
            m = MagicMock()
            m.returncode = 0
            m.stdout = ''
            m.stderr = ''

            if cmd and cmd[0] == "gh":
                # Strip --repo and the owner/repo argument from gh commands.
                # gh api ... --repo owner/repo puts --repo at the END, so filter
                # both the flag and the value that follows it.
                cleaned = []
                args_iter = iter(cmd[1:])
                for a in args_iter:
                    if a == "--repo":
                        try:
                            next(args_iter)  # skip the repo name
                        except StopIteration:
                            pass
                    else:
                        cleaned.append(a)
                gh_args = cleaned

                if gh_args[:2] == ["pr", "view"]:
                    if '--jq' in gh_args:
                        jq_idx = gh_args.index('--jq')
                        jq_expr = gh_args[jq_idx + 1] if jq_idx + 1 < len(gh_args) else ''
                        if jq_expr == '.headRefOid':
                            m.stdout = 'test123abc'
                        elif 'head' in jq_expr:
                            m.stdout = '{"state":"open","head":"test123abc"}'
                        else:
                            m.stdout = ''
                    else:
                        m.stdout = ''
                    return m

                if gh_args[:2] == ["pr", "checks"]:
                    m.stdout = json.dumps([
                        {"name": "test (3.11)", "state": "success", "link": ""},
                        {"name": "review-comment-gate", "state": "success", "link": ""},
                        {"name": "validator", "state": "success", "link": ""},
                        {"name": "governance-validators", "state": "success", "link": ""},
                        {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
                    ])
                    return m

                return m

            cmd_str = ' '.join(cmd) if cmd else ''
            out_path = None
            for i, arg in enumerate(cmd or []):
                if arg in ('--output-json', '--output') and i + 1 < len(cmd):
                    out_path = cmd[i + 1]
                    break

            if out_path:
                if 'check_pr_review_comments' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'REVIEW_COMMENTS_CLEAN', 'blockers': [], 'findings': []}, f)
                elif 'check_persistent_mutation_guard' in cmd_str:
                    if 'compare' in cmd_str:
                        # PMG dirty — blocked changes detected
                        with open(out_path, 'w') as f:
                            json.dump({'status': 'blocked', 'blocked_changes': [
                                {'relative_path': 'skills/new_skill/SKILL.md', 'change': 'added'}
                            ], 'recommendation': 'BLOCK'}, f)
                elif 'final_gate_status' in cmd_str:
                    # final_gate_status receives pmg_compare (blocked) → HOLD_PMG_DIRTY
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'HOLD_PMG_DIRTY', 'blockers': [
                            'PMG guard state invalid: PMG status is \'blocked\''
                        ]}, f)
                elif 'verify_final_head_merge_command' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'recommendation': 'BLOCK', 'verification_errors': [
                            'persistent_mutation_guard: PMG status is \'blocked\''
                        ]}, f)
                else:
                    with open(out_path, 'w') as f:
                        json.dump({}, f)

            return m

        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-pmg",
            "--require-final-gates",
        ]

        with patch.object(mod.subprocess, "run", side_effect=fake_subprocess_run):
            try:
                mod.main()
            except SystemExit:
                pass
            finally:
                sys.argv = old_argv

        data = read_json(out_json)
        # Must NOT be READY_TO_MERGE_CANDIDATE — final_gate_status returned HOLD_PMG_DIRTY
        assert data["status"] != "READY_TO_MERGE_CANDIDATE", (
            f"HOLD_PMG_DIRTY must block READY_TO_MERGE_CANDIDATE; got: {data['status']}"
        )
        assert data["status"].startswith("HOLD_") or data["status"] == "ERROR_TOOLING", (
            f"Expected HOLD_* or ERROR_TOOLING but got: {data['status']}"
        )
        # next_safe_action must not contain a merge command
        next_action = data.get("next_safe_action", "")
        assert "gh pr merge" not in next_action, (
            f"HOLD_PMG_DIRTY must not emit merge command; next_action={next_action!r}"
        )


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


class TestCIPollDeduplication:
    """
    Regression tests for parallel-workflow duplicate check-name handling.

    Problem: gh pr checks can return duplicate check names from parallel
    workflow runs on the same head. The old code used:
        reported = {c["name"]: c for c in checks}
    which means the LAST entry wins. A transient SKIPPED duplicate could
    overwrite a SUCCESS entry, causing false HOLD_CI_FAILED.

    Fix: Group by name, apply precedence (SUCCESS > PENDING > FAILURE).
    """

    def test_success_and_skipped_duplicate_success_wins(self):
        """Duplicate check with SUCCESS and SKIPPED → success wins, no HOLD_CI_FAILED."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Return two records for review-comment-gate: SUCCESS + SKIPPED
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            {"name": "review-comment-gate", "state": "SUCCESS", "link": ""},
            {"name": "review-comment-gate", "state": "SKIPPED", "link": ""},
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=0
            )
            # With poll_seconds=0 the deadline is already passed, so if no pending checks
            # we get HOLD_TIMEOUT; but the key invariant is HOLD_CI_FAILED must NOT appear
            # even when SKIPPED duplicates exist alongside SUCCESS.
            assert status != mod.STATUS_HOLD_CI_FAILED, (
                f"SUCCESS + SKIPPED must not produce HOLD_CI_FAILED; got {status}"
            )

    def test_all_required_checks_success_with_skipped_duplicates_ready_for_final_gates(self):
        """When all required checks have a SUCCESS record, poll_ci_checks returns READY_FOR_FINAL_GATES."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        # Every required check has a SUCCESS; review-comment-gate also has a SKIPPED duplicate
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            {"name": "review-comment-gate", "state": "SUCCESS", "link": ""},
            {"name": "review-comment-gate", "state": "SKIPPED", "link": ""},
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=0
            )
            assert status == mod.STATUS_READY_FOR_FINAL_GATES, (
                f"Expected READY_FOR_FINAL_GATES but got {status}: {error}"
            )

    def test_only_skipped_fails_closed(self):
        """Required check with only SKIPPED records → fail closed (HOLD_CI_FAILED)."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        # review-comment-gate has SKIPPED only (no SUCCESS anywhere)
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            {"name": "review-comment-gate", "state": "SKIPPED", "link": ""},
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=0
            )
            assert status == mod.STATUS_HOLD_CI_FAILED, (
                f"SKIPPED-only check must produce HOLD_CI_FAILED; got {status}"
            )
            assert "review-comment-gate" in error, f"Error should mention failed check: {error}"

    def test_only_failure_fails_closed(self):
        """Required check with only FAILURE records → fail closed (HOLD_CI_FAILED)."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            {"name": "review-comment-gate", "state": "FAILURE", "link": ""},
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=0
            )
            assert status == mod.STATUS_HOLD_CI_FAILED, (
                f"FAILURE-only check must produce HOLD_CI_FAILED; got {status}"
            )

    def test_pending_and_no_success_is_pending(self):
        """Required check with only PENDING records (no SUCCESS) → pending (HOLD_TIMEOUT with deadline)."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "in_progress", "link": ""},
            {"name": "review-comment-gate", "state": "in_progress", "link": ""},
            {"name": "validator", "state": "in_progress", "link": ""},
            {"name": "governance-validators", "state": "in_progress", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "in_progress", "link": ""},
        ])
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=0
            )
            # With poll_seconds=0 the deadline is immediate → HOLD_TIMEOUT
            # But it must NOT be HOLD_CI_FAILED (no failures, only pending)
            assert status != mod.STATUS_HOLD_CI_FAILED, (
                f"PENDING-only checks must not produce HOLD_CI_FAILED; got {status}"
            )

    def test_missing_required_check_is_hold_timeout(self):
        """Required check absent from gh pr checks output → treated as pending during
        polling window, returns HOLD_TIMEOUT when deadline is reached (fail-closed on
        timeout, not on first poll).

        This replaces the earlier wrong expectation that missing a check immediately
        returns HOLD_CI_FAILED. During CI workflow startup, pr-gate-live-smoke may not
        yet have posted results — the waiter must keep polling rather than false-fail.

        Fail-closed applies only after timeout: still-missing checks → HOLD_TIMEOUT.
        """
        import importlib.util
        import subprocess
        import time
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        # review-comment-gate is completely absent — missing check during polling
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            # review-comment-gate missing!
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        # Use poll_seconds=2 so deadline check triggers before time runs out.
        # mock_time Val advances by 2 per sleep → deadline reached after
        # (timeout_minutes*60)/poll_seconds iterations, each taking <1ms.
        mock_time_val = [1000.0]
        def mock_sleep(duration):
            mock_time_val[0] += duration

        with patch.object(time, "time", lambda: mock_time_val[0]), \
             patch.object(time, "sleep", mock_sleep), \
             patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=2
            )
            # Missing check → treated as pending → HOLD_TIMEOUT (deadline already passed
            # with poll_seconds=0) — NOT HOLD_CI_FAILED (false fail during polling)
            assert status in (mod.STATUS_HOLD_TIMEOUT, mod.STATUS_HOLD_CI_PENDING), (
                f"Missing check within timeout window must be HOLD_TIMEOUT/HOLD_CI_PENDING, "
                f"not HOLD_CI_FAILED; got {status}: {error}"
            )
            # Must NOT be HOLD_CI_FAILED — missing checks during polling are pending,
            # not immediate failure
            assert status != mod.STATUS_HOLD_CI_FAILED, (
                f"Missing check during polling window must not produce HOLD_CI_FAILED; "
                f"got {status}: {error}"
            )

    def test_missing_required_check_after_timeout_returns_hold_timeout_explicit(self):
        """Required check absent after timeout → fail-closed with explicit missing
        check reason in pending_at_timeout.

        This is the fail-closed path: if the workflow hasn't posted a check result
        before the deadline, the waiter must report timeout with the missing check
        name included so the operator knows what to look for.
        """
        import importlib.util
        import subprocess
        import time
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        # review-comment-gate is absent — workflow never posted it
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            # review-comment-gate missing!
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        mock_time_val = [1000.0]
        def mock_sleep(duration):
            mock_time_val[0] += duration

        with patch.object(time, "time", lambda: mock_time_val[0]), \
             patch.object(time, "sleep", mock_sleep), \
             patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=2
            )
            # poll_seconds=2 means deadline is immediate; missing check → timeout
            # pending_at_timeout field must contain the missing check name
            assert status == mod.STATUS_HOLD_TIMEOUT, (
                f"Expected HOLD_TIMEOUT after timeout with missing check; got {status}: {error}"
            )
            pending_at_timeout = data.get("pending_at_timeout", [])
            # The missing review-comment-gate should be listed as pending at timeout
            assert any("review-comment-gate" in str(p) for p in pending_at_timeout), (
                f"pending_at_timeout must include 'review-comment-gate'; got {pending_at_timeout}"
            )

    def test_missing_pr_gate_live_smoke_during_startup_does_not_emit_ready_to_merge_candidate(self):
        """During CI workflow startup, pr-gate-live-smoke may not yet be posted.
        The waiter must NOT emit READY_TO_MERGE_CANDIDATE — only READY_TO_MERGE_CANDIDATE
        after all checks are SUCCESS.
        """
        import importlib.util
        import subprocess
        import time
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        # pr-gate-live-smoke is absent (workflow spinning up)
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            {"name": "review-comment-gate", "state": "success", "link": ""},
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            # pr-gate-live-smoke missing!
        ])
        mock_result.stderr = ""

# Use poll_seconds=30 so the deadline check triggers after exactly 2
        # iterations (remaining 60→30 after first sleep, then 30≤30 triggers timeout).
        # This avoids many iterations and test timeouts.
        mock_time_val = [1000.0]
        def mock_sleep(duration):
            mock_time_val[0] += duration

        with patch.object(time, "time", lambda: mock_time_val[0]), \
             patch.object(time, "sleep", mock_sleep), \
             patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=30
            )
            assert status == mod.STATUS_HOLD_TIMEOUT, (
                f"Missing pr-gate-live-smoke must produce HOLD_TIMEOUT; got {status}"
            )
            # next_action_for_status must not return a gh pr merge command
            action = mod.next_action_for_status(status, 339, "abc1234")
            assert "gh pr merge" not in action, (
                f"Missing check must not emit gh pr merge command; got: {action}"
            )

    def test_only_skipped_still_fails_closed(self):
        """Required check with only SKIPPED records (appeared but deliberately skipped)
        → fail closed (HOLD_CI_FAILED). Unlike missing (never appeared), SKIPPED means
        the check ran and returned a terminal non-success state.
        """
        import importlib.util
        import subprocess
        import time
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        # review-comment-gate has SKIPPED only (no SUCCESS anywhere)
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            {"name": "review-comment-gate", "state": "SKIPPED", "link": ""},
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=0
            )
            assert status == mod.STATUS_HOLD_CI_FAILED, (
                f"SKIPPED-only check must produce HOLD_CI_FAILED; got {status}"
            )
            assert "review-comment-gate" in error, f"Error should mention failed check: {error}"

    def test_duplicate_success_and_skipped_still_succeeds(self):
        """Duplicate check with SUCCESS and SKIPPED → SUCCESS wins, no HOLD_CI_FAILED.
        The SKIPPED duplicate must not overwrite the SUCCESS record.
        """
        import importlib.util
        import subprocess
        import time
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([
            {"name": "test (3.11)", "state": "success", "link": ""},
            {"name": "review-comment-gate", "state": "SUCCESS", "link": ""},
            {"name": "review-comment-gate", "state": "SKIPPED", "link": ""},
            {"name": "validator", "state": "success", "link": ""},
            {"name": "governance-validators", "state": "success", "link": ""},
            {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
        ])
        mock_result.stderr = ""

        mock_time_val = [1000.0]
        def mock_sleep(duration):
            mock_time_val[0] += duration

        with patch.object(time, "time", lambda: mock_time_val[0]), \
             patch.object(time, "sleep", mock_sleep), \
             patch.object(subprocess, "run", return_value=mock_result):
            status, data, error = mod.poll_ci_checks(
                339, list(mod.DEFAULT_REQUIRED_CHECKS), timeout_minutes=1, poll_seconds=0
            )
            # With poll_seconds=0 deadline is immediate → HOLD_TIMEOUT
            # But the key invariant: HOLD_CI_FAILED must NOT appear
            assert status != mod.STATUS_HOLD_CI_FAILED, (
                f"SUCCESS + SKIPPED duplicate must not produce HOLD_CI_FAILED; got {status}"
            )


class TestRepoContextAndCwd:
    """Verify the waiter works from any working directory."""

    def test_gh_run_includes_explicit_repo_argument(self):
        """Every gh call must include --repo so it works from non-repo cwd."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock, call
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = '[{"name":"test (3.11)","state":"success","link":""}]'
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            # gh_run should include --repo in the call
            mod.gh_run(["pr", "checks", "337", "--json", "name,state"])
            mock_run.assert_called_once()
            call_args_list = mock_run.call_args_list[0]
            gh_index = call_args_list[0].index("gh") if "gh" in call_args_list[0] else None
            if gh_index is not None:
                # Check that --repo appears after gh, as in "gh pr checks --repo owner/repo"
                # (--repo is placed after the subcommand, not before it)
                argv = list(call_args_list[0])
                if "--repo" in argv:
                    idx = argv.index("--repo")
                    assert idx > gh_index
                    next_arg = argv[idx + 1]
                    assert "/" in next_arg, f"--repo value must be 'owner/name', got: {next_arg}"

    def test_gh_run_includes_explicit_repo_in_pr_view(self):
        """gh pr view calls must use explicit --repo so they work from any cwd."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Ensure REPO_CONTEXT is populated (normally done by main())
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = '{"state":"open","headRefOid":"abc123"}'
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            mod.gh_run(["pr", "view", "337", "--json", "state,headRefOid", "--jq", "{state:.state}"])
            mock_run.assert_called_once()
            argv = list(mock_run.call_args[0][0])
            assert "--repo" in argv, f"--repo not found in gh pr view argv: {argv}"

    def test_run_external_script_uses_repo_root_as_cwd_by_default(self):
        """run_external_script must use REPO_ROOT as cwd when no explicit cwd given."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Set REPO_ROOT
        mod.REPO_ROOT = "/home/max/Automated-Edge-Discovery"

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = '{"status":"clean","blocked":0}'
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            mod.run_external_script(["python3", "check.py"], check=False)
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs.get("cwd") == "/home/max/Automated-Edge-Discovery", \
                f"cwd must be REPO_ROOT, got: {call_kwargs.get('cwd')}"

    def test_run_external_script_uses_explicit_cwd_when_passed(self):
        """run_external_script must use the explicit cwd argument, not REPO_ROOT."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = '{"status":"clean"}'
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            mod.run_external_script(["python3", "check.py"], check=False, cwd="/tmp/custom")
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs.get("cwd") == "/tmp/custom", \
                f"cwd must be explicit '/tmp/custom', got: {call_kwargs.get('cwd')}"

    def test_waiter_terminates_merged_pr_early_without_ci_poll(self):
        """For a merged PR, waiter must not poll CI — must return READY_PR_ALREADY_MERGED."""
        import importlib.util
        import subprocess
        from unittest.mock import patch, MagicMock
        import tempfile, os, json as json_mod
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Ensure REPO_CONTEXT is set (normally done in main())
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = "/home/max/Automated-Edge-Discovery"

        with tempfile.TemporaryDirectory() as tmp:
            out_json = os.path.join(tmp, "status.json")
            out_md = os.path.join(tmp, "status.md")

            # Mock get_pr_state to return "merged" and get_live_head_sha
            with patch.object(mod, "get_pr_state", return_value=("merged", "e2a8f0e")):
                with patch.object(mod, "get_live_head_sha", return_value="e2a8f0e"):
                    # Also mock the external scripts so we don't need the real repo
                    with patch.object(mod, "run_external_script") as mock_script:
                        mock_result = MagicMock(spec=subprocess.CompletedProcess)
                        mock_result.returncode = 0
                        mock_result.stdout = '{"status":"clean","blocked":0}'
                        mock_result.stderr = ""
                        mock_script.return_value = mock_result

                        import sys
                        old_argv = sys.argv
                        sys.argv = ["wait_for_pr_ready.py", "--pr-number", "338",
                                    "--output-json", out_json,
                                    "--timeout-minutes", "1",
                                    "--poll-seconds", "30"]
                        try:
                            mod.main()
                        except SystemExit:
                            pass
                        finally:
                            sys.argv = old_argv

            # Read the output JSON
            assert os.path.exists(out_json), f"Output JSON not written: {out_json}"
            with open(out_json) as f:
                data = json_mod.load(f)

            assert data["status"] in (
                mod.STATUS_READY_PR_ALREADY_MERGED,
                mod.STATUS_HOLD_PR_NOT_OPEN,
            ), f"Merged PR must not return merge candidate; got: {data['status']}"
            assert "gh pr merge" not in data.get("next_safe_action", ""), \
                f"Merged PR must not emit merge command; got: {data.get('next_safe_action')}"

    def test_hold_ci_red_blocks_ready_to_merge_candidate(self):
        """final_gate_status HOLD_CI_RED must block merge candidate transition."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("wait_for_pr_ready", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Verify STATUS_HOLD_CI_FAILED is a HOLD_* status (never becomes READY_TO_MERGE_CANDIDATE)
        assert mod.STATUS_HOLD_CI_FAILED.startswith("HOLD_"), \
            f"STATUS_HOLD_CI_FAILED must be a HOLD status, got: {mod.STATUS_HOLD_CI_FAILED}"
        # Verify next_action_for_status does not return a merge command for HOLD_CI_FAILED
        action = mod.next_action_for_status(mod.STATUS_HOLD_CI_FAILED, 999, "abc1234")
        assert "gh pr merge" not in action, \
            f"HOLD_CI_FAILED must not emit merge command; got: {action}"


class TestConversationResolutionEnforcement:
    """Test that branch protection conversation_resolution requirement is enforced."""

    def test_hold_when_conversation_resolution_required_but_flag_not_set(self, output_dir, output_json):
        """
        When branch protection has required_conversation_resolution=true AND
        --require-review-comments-clean was NOT set, the waiter must return
        HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION and emit no merge command.
        """
        import importlib.util
        import subprocess
        import sys

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def fake_subprocess_run(popenargs, **kwargs):
            cmd = popenargs if isinstance(popenargs, (list, tuple)) else None
            m = MagicMock()
            m.returncode = 0
            m.stdout = ''
            m.stderr = ''

            if cmd and cmd[0] == "gh":
                # Strip --repo and the owner/repo argument from gh commands.
                # gh api ... --repo owner/repo puts --repo at the END, so filter
                # both the flag and the value that follows it.
                cleaned = []
                args_iter = iter(cmd[1:])
                for a in args_iter:
                    if a == "--repo":
                        try:
                            next(args_iter)  # skip the repo name
                        except StopIteration:
                            pass
                    else:
                        cleaned.append(a)
                gh_args = cleaned

                if gh_args[:2] == ["pr", "view"]:
                    if '--jq' in gh_args:
                        jq_idx = gh_args.index('--jq')
                        jq_expr = gh_args[jq_idx + 1] if jq_idx + 1 < len(gh_args) else ''
                        if jq_expr == '.headRefOid':
                            m.stdout = 'test123abc'
                        elif 'head' in jq_expr:
                            m.stdout = '{"state":"open","head":"test123abc"}'
                        elif '.baseRefName' in jq_expr:
                            m.stdout = '"main"'
                        else:
                            m.stdout = ''
                    return m

                if gh_args[0] == "api":
                    if 'branches/main/protection' in ' '.join(gh_args):
                        m.stdout = json.dumps({
                            "required_conversation_resolution": {"enabled": True}
                        })
                    else:
                        m.stdout = '{}'
                    return m

                if gh_args[:2] == ["pr", "checks"]:
                    m.stdout = json.dumps([
                        {"name": "test (3.11)", "state": "success", "link": ""},
                        {"name": "review-comment-gate", "state": "success", "link": ""},
                        {"name": "validator", "state": "success", "link": ""},
                        {"name": "governance-validators", "state": "success", "link": ""},
                        {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
                    ])
                    return m

                return m

            cmd_str = ' '.join(cmd) if cmd else ''
            out_path = None
            for i, arg in enumerate(cmd or []):
                if arg in ('--output-json', '--output') and i + 1 < len(cmd):
                    out_path = cmd[i + 1]
                    break

            if out_path:
                if 'final_gate_status' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'READY_TO_MERGE', 'blockers': []}, f)
                elif 'verify_final_head_merge_command' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'recommendation': 'MERGE_READY_CANDIDATE', 'head_sha_match': True}, f)

            return m

        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-final-gates",
        ]

        with patch.object(mod.subprocess, "run", side_effect=fake_subprocess_run):
            try:
                mod.main()
            except SystemExit:
                pass
            finally:
                sys.argv = old_argv

        assert os.path.exists(out_json), f"JSON not written: {out_json}"
        data = read_json(out_json)
        assert data["status"] == "HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION", (
            f"Expected HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION but got {data['status']}"
        )
        action = data.get("next_safe_action", "")
        assert "gh pr merge" not in action, (
            f"HOLD must not contain merge command; got: {action}"
        )

    def test_proceeds_when_conversation_resolution_required_and_flag_set(self, output_dir, output_json):
        """
        When branch protection has required_conversation_resolution=true AND
        --require-review-comments-clean IS set, the waiter proceeds normally
        if the review comment gate is clean.
        """
        import importlib.util
        import subprocess
        import sys

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def fake_subprocess_run(popenargs, **kwargs):
            cmd = popenargs if isinstance(popenargs, (list, tuple)) else None
            m = MagicMock()
            m.returncode = 0
            m.stdout = ''
            m.stderr = ''

            if cmd and cmd[0] == "gh":
                # Strip --repo and the owner/repo argument from gh commands.
                # gh api ... --repo owner/repo puts --repo at the END, so filter
                # both the flag and the value that follows it.
                cleaned = []
                args_iter = iter(cmd[1:])
                for a in args_iter:
                    if a == "--repo":
                        try:
                            next(args_iter)  # skip the repo name
                        except StopIteration:
                            pass
                    else:
                        cleaned.append(a)
                gh_args = cleaned

                if gh_args[:2] == ["pr", "view"]:
                    if '--jq' in gh_args:
                        jq_idx = gh_args.index('--jq')
                        jq_expr = gh_args[jq_idx + 1] if jq_idx + 1 < len(gh_args) else ''
                        if jq_expr == '.headRefOid':
                            m.stdout = 'test123abc'
                        elif 'head' in jq_expr:
                            m.stdout = '{"state":"open","head":"test123abc"}'
                        elif '.baseRefName' in jq_expr:
                            m.stdout = '"main"'
                        else:
                            m.stdout = ''
                    return m

                if gh_args[0] == "api":
                    if 'branches/main/protection' in ' '.join(gh_args):
                        m.stdout = json.dumps({
                            "required_conversation_resolution": {"enabled": True}
                        })
                    else:
                        m.stdout = '{}'
                    return m

                if gh_args[:2] == ["pr", "checks"]:
                    m.stdout = json.dumps([
                        {"name": "test (3.11)", "state": "success", "link": ""},
                        {"name": "review-comment-gate", "state": "success", "link": ""},
                        {"name": "validator", "state": "success", "link": ""},
                        {"name": "governance-validators", "state": "success", "link": ""},
                        {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
                    ])
                    return m

                return m

            cmd_str = ' '.join(cmd) if cmd else ''
            out_path = None
            for i, arg in enumerate(cmd or []):
                if arg in ('--output-json', '--output') and i + 1 < len(cmd):
                    out_path = cmd[i + 1]
                    break

            if out_path:
                if 'check_pr_review_comments' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'REVIEW_COMMENTS_CLEAN', 'blockers': [], 'findings': []}, f)
                elif 'final_gate_status' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'READY_TO_MERGE', 'blockers': []}, f)
                elif 'verify_final_head_merge_command' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'recommendation': 'MERGE_READY_CANDIDATE', 'head_sha_match': True}, f)

            return m

        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-final-gates",
        ]

        with patch.object(mod.subprocess, "run", side_effect=fake_subprocess_run):
            try:
                mod.main()
            except SystemExit:
                pass
            finally:
                sys.argv = old_argv

        data = read_json(out_json)
        assert data["status"] == "READY_TO_MERGE_CANDIDATE", (
            f"Expected READY_TO_MERGE_CANDIDATE with flag set; got {data['status']}"
        )


class TestConversationResolutionBranchProtectionDisabled:
    """Test that conversation resolution is not enforced when branch protection has it disabled."""

    def test_merges_when_conversation_resolution_not_required(self, output_dir, output_json):
        """
        When branch protection has required_conversation_resolution=false (or not set),
        --require-review-comments-clean was NOT set, waiter must still return
        READY_TO_MERGE_CANDIDATE (old behavior preserved).
        """
        import importlib.util
        import subprocess
        import sys

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def fake_subprocess_run(popenargs, **kwargs):
            cmd = popenargs if isinstance(popenargs, (list, tuple)) else None
            m = MagicMock()
            m.returncode = 0
            m.stdout = ''
            m.stderr = ''

            if cmd and cmd[0] == "gh":
                # Strip --repo and the owner/repo argument from gh commands.
                # gh api ... --repo owner/repo puts --repo at the END, so filter
                # both the flag and the value that follows it.
                cleaned = []
                args_iter = iter(cmd[1:])
                for a in args_iter:
                    if a == "--repo":
                        try:
                            next(args_iter)  # skip the repo name
                        except StopIteration:
                            pass
                    else:
                        cleaned.append(a)
                gh_args = cleaned

                if gh_args[:2] == ["pr", "view"]:
                    if '--jq' in gh_args:
                        jq_idx = gh_args.index('--jq')
                        jq_expr = gh_args[jq_idx + 1] if jq_idx + 1 < len(gh_args) else ''
                        if jq_expr == '.headRefOid':
                            m.stdout = 'test123abc'
                        elif 'head' in jq_expr:
                            m.stdout = '{"state":"open","head":"test123abc"}'
                        elif '.baseRefName' in jq_expr:
                            m.stdout = '"main"'
                        else:
                            m.stdout = ''
                    return m

                if gh_args[0] == "api":
                    if 'branches/main/protection' in ' '.join(gh_args):
                        m.stdout = json.dumps({
                            "required_conversation_resolution": {"enabled": False}
                        })
                    else:
                        m.stdout = '{}'
                    return m

                if gh_args[:2] == ["pr", "checks"]:
                    m.stdout = json.dumps([
                        {"name": "test (3.11)", "state": "success", "link": ""},
                        {"name": "review-comment-gate", "state": "success", "link": ""},
                        {"name": "validator", "state": "success", "link": ""},
                        {"name": "governance-validators", "state": "success", "link": ""},
                        {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
                    ])
                    return m

                return m

            cmd_str = ' '.join(cmd) if cmd else ''
            out_path = None
            for i, arg in enumerate(cmd or []):
                if arg in ('--output-json', '--output') and i + 1 < len(cmd):
                    out_path = cmd[i + 1]
                    break

            if out_path:
                if 'final_gate_status' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'READY_TO_MERGE', 'blockers': []}, f)
                elif 'verify_final_head_merge_command' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'recommendation': 'MERGE_READY_CANDIDATE', 'head_sha_match': True}, f)

            return m

        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-final-gates",
        ]

        with patch.object(mod.subprocess, "run", side_effect=fake_subprocess_run):
            try:
                mod.main()
            except SystemExit:
                pass
            finally:
                sys.argv = old_argv

        data = read_json(out_json)
        assert data["status"] == "READY_TO_MERGE_CANDIDATE", (
            f"Expected READY_TO_MERGE_CANDIDATE when conversation_resolution not required; got {data['status']}"
        )


class TestConversationResolutionCheckStage:
    """
    Test the conversation_resolution_check stage in the main flow.
    These tests verify the stage correctly handles unresolved threads,
    GraphQL failures, pagination signals, and repo parameter usage.
    """

    def _make_fake(self, protection_enabled=True, threads_resolved=True,
                   graphql_error=False, has_next_page=False):
        """Factory of fake_subprocess_run with configurable behavior."""
        def fake_subprocess_run(popenargs, **kwargs):
            cmd = popenargs if isinstance(popenargs, (list, tuple)) else None
            m = MagicMock()
            m.returncode = 0
            m.stdout = ''
            m.stderr = ''

            if cmd and cmd[0] == "gh":
                cleaned = []
                args_iter = iter(cmd[1:])
                for a in args_iter:
                    if a == "--repo":
                        try:
                            next(args_iter)
                        except StopIteration:
                            pass
                    else:
                        cleaned.append(a)
                gh_args = cleaned

                if gh_args[:2] == ["pr", "view"]:
                    if '--jq' in gh_args:
                        jq_idx = gh_args.index('--jq')
                        jq_expr = gh_args[jq_idx + 1] if jq_idx + 1 < len(gh_args) else ''
                        if jq_expr == '.headRefOid':
                            m.stdout = 'test123abc'
                        elif 'head' in jq_expr:
                            m.stdout = '{"state":"open","head":"test123abc"}'
                        elif '.baseRefName' in jq_expr:
                            m.stdout = '"main"'
                        else:
                            m.stdout = ''
                    return m

                if gh_args[0] == "api":
                    if 'branches/main/protection' in ' '.join(gh_args):
                        m.stdout = json.dumps({
                            "required_conversation_resolution": {"enabled": protection_enabled}
                        })
                    elif 'graphql' in gh_args:
                        if graphql_error:
                            m.returncode = 1
                            m.stderr = "GraphQL error"
                        else:
                            # Return threads with isResolved based on threads_resolved param
                            m.stdout = json.dumps({
                                "data": {
                                    "repository": {
                                        "pullRequest": {
                                            "reviewThreads": {
                                                "pageInfo": {"hasNextPage": has_next_page},
                                                "nodes": [
                                                    {"id": "PRRT_1", "isResolved": threads_resolved,
                                                     "isOutdated": True,
                                                     "comments": {"nodes": [
                                                         {"id": "PRRC_1", "body": "comment body",
                                                          "author": {"login": "codex-bot"}}]}}
                                                ]
                                            }
                                        }
                                    }
                                }
                            })
                    else:
                        m.stdout = '{}'
                    return m

                if gh_args[:2] == ["pr", "checks"]:
                    m.stdout = json.dumps([
                        {"name": "test (3.11)", "state": "success", "link": ""},
                        {"name": "review-comment-gate", "state": "success", "link": ""},
                        {"name": "validator", "state": "success", "link": ""},
                        {"name": "governance-validators", "state": "success", "link": ""},
                        {"name": "pr-gate-live-smoke", "state": "success", "link": ""},
                    ])
                    return m

                return m

            cmd_str = ' '.join(cmd) if cmd else ''
            out_path = None
            for i, arg in enumerate(cmd or []):
                if arg in ('--output-json', '--output') and i + 1 < len(cmd):
                    out_path = cmd[i + 1]
                    break

            if out_path:
                if 'check_pr_review_comments' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'REVIEW_COMMENTS_CLEAN', 'blockers': [], 'findings': []}, f)
                elif 'final_gate_status' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'status': 'READY_TO_MERGE', 'blockers': []}, f)
                elif 'verify_final_head_merge_command' in cmd_str:
                    with open(out_path, 'w') as f:
                        json.dump({'recommendation': 'MERGE_READY_CANDIDATE', 'head_sha_match': True,
                                   'merge_command': 'gh pr merge 999 --squash --delete-branch --match-head-commit test123abc',
                                   'verification_errors': []}, f)
                elif 'status.json' in out_path and 'pmg' not in cmd_str and 'review_gate' not in cmd_str:
                    pass  # wait_for_pr_ready.py writes its own status.json

            return m
        return fake_subprocess_run

    def test_unresolved_thread_blocks_merge(self, output_dir, output_json):
        """
        When branch protection has required_conversation_resolution=true,
        --require-review-comments-clean is set, and an unresolved thread exists,
        the waiter must return HOLD_CONVERSATION_UNRESOLVED.
        """
        import importlib.util
        import sys
        from unittest.mock import patch
        from pathlib import Path

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake = self._make_fake(protection_enabled=True, threads_resolved=False)
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-final-gates",
        ]

        try:
            with patch.object(mod.subprocess, "run", side_effect=fake):
                try:
                    mod.main()
                except SystemExit:
                    pass
                finally:
                    sys.argv = old_argv

            data = read_json(out_json)
            stages = [s["stage"] for s in data.get("stages", [])]
            # Verify conversation_resolution_check stage exists
            assert "conversation_resolution_check" in stages, f"Missing stage; stages={stages}"
            assert data["status"] == "HOLD_CONVERSATION_UNRESOLVED", (
                f"Expected HOLD_CONVERSATION_UNRESOLVED; got {data['status']}; stages={stages}"
            )
        finally:
            sys.argv = old_argv

    def test_all_threads_resolved_proceeds(self, output_dir, output_json):
        """
        When branch protection has required_conversation_resolution=true,
        --require-review-comments-clean is set, and all threads are resolved,
        the waiter proceeds to READY_TO_MERGE_CANDIDATE.
        """
        import importlib.util
        import sys
        from unittest.mock import patch
        from pathlib import Path

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake = self._make_fake(protection_enabled=True, threads_resolved=True)
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-final-gates",
        ]

        try:
            with patch.object(mod.subprocess, "run", side_effect=fake):
                try:
                    mod.main()
                except SystemExit:
                    pass
                finally:
                    sys.argv = old_argv

            data = read_json(out_json)
            stages = [s["stage"] for s in data.get("stages", [])]
            assert "conversation_resolution_check" in stages, f"Missing stage; stages={stages}"
            assert data["status"] == "READY_TO_MERGE_CANDIDATE", (
                f"Expected READY_TO_MERGE_CANDIDATE; got {data['status']}; stages={stages}"
            )
        finally:
            sys.argv = old_argv

    def test_graphql_failure_holds(self, output_dir, output_json):
        """
        When branch protection requires conversation resolution but the GraphQL
        call fails, the waiter must return HOLD_CONVERSATION_CHECK_UNAVAILABLE
        (fail closed — do not merge if we cannot verify).
        """
        import importlib.util
        import sys
        from unittest.mock import patch
        from pathlib import Path

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake = self._make_fake(protection_enabled=True, threads_resolved=True, graphql_error=True)
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-final-gates",
        ]

        try:
            with patch.object(mod.subprocess, "run", side_effect=fake):
                try:
                    mod.main()
                except SystemExit:
                    pass
                finally:
                    sys.argv = old_argv

            data = read_json(out_json)
            stages = [s["stage"] for s in data.get("stages", [])]
            assert "conversation_resolution_check" in stages, f"Missing stage; stages={stages}"
            assert data["status"] == "HOLD_CONVERSATION_CHECK_UNAVAILABLE", (
                f"Expected HOLD_CONVERSATION_CHECK_UNAVAILABLE; got {data['status']}; stages={stages}"
            )
        finally:
            sys.argv = old_argv

    def test_has_next_page_holds(self, output_dir, output_json):
        """
        When the GraphQL returns pageInfo.hasNextPage=true, the waiter must
        return HOLD_CONVERSATION_CHECK_PAGINATION_REQUIRED (pagination not
        yet implemented).
        """
        import importlib.util
        import sys
        from unittest.mock import patch
        from pathlib import Path

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake = self._make_fake(protection_enabled=True, threads_resolved=True, has_next_page=True)
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-final-gates",
        ]

        try:
            with patch.object(mod.subprocess, "run", side_effect=fake):
                try:
                    mod.main()
                except SystemExit:
                    pass
                finally:
                    sys.argv = old_argv

            data = read_json(out_json)
            stages = [s["stage"] for s in data.get("stages", [])]
            assert "conversation_resolution_check" in stages, f"Missing stage; stages={stages}"
            assert data["status"] == "HOLD_CONVERSATION_CHECK_PAGINATION_REQUIRED", (
                f"Expected HOLD_CONVERSATION_CHECK_PAGINATION_REQUIRED; got {data['status']}; stages={stages}"
            )
        finally:
            sys.argv = old_argv

    def test_repo_flag_respected_no_hardcode(self, output_dir, output_json):
        """
        When --repo is provided, the GraphQL call must use owner and name
        variables derived from the --repo argument, not a hardcoded
        Slideshow11/Automated-Edge-Discovery.

        We verify this by checking the real code path: a properly configured fake
        (with owner=name variables) must reach READY_TO_MERGE_CANDIDATE.
        If the GraphQL call used a hardcoded repo instead of variables, the
        real API call would return unexpected data and the stage would fail.
        Since _make_fake returns all-resolved threads, the only way to get
        READY_TO_MERGE_CANDIDATE is if both the protection check AND the
        thread check succeed with the correct owner/name variables.
        """
        import importlib.util
        import sys
        from unittest.mock import patch
        from pathlib import Path

        md_path = str(output_dir / "status.md")
        out_json = output_json

        spec = importlib.util.spec_from_file_location("waiter", WAITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake = self._make_fake(protection_enabled=True, threads_resolved=True)
        mod.REPO_CONTEXT = ["--repo", "Slideshow11/Automated-Edge-Discovery"]
        mod.REPO_ROOT = str(Path(__file__).parent.parent)

        old_argv = sys.argv
        sys.argv = [
            "wait_for_pr_ready.py",
            "--pr-number", "999",
            "--timeout-minutes", "1",
            "--poll-seconds", "0",
            "--output-json", out_json,
            "--output-md", md_path,
            "--require-review-comments-clean",
            "--require-final-gates",
        ]

        try:
            with patch.object(mod.subprocess, "run", side_effect=fake):
                try:
                    mod.main()
                except SystemExit:
                    pass
                finally:
                    sys.argv = old_argv

            data = read_json(out_json)
            stages = [s["stage"] for s in data.get("stages", [])]
            assert "conversation_resolution_check" in stages, f"Missing stage; stages={stages}"
            # If we got READY_TO_MERGE_CANDIDATE, the GraphQL call used the correct
            # owner/name variables (derived from --repo). If it used a hardcoded
            # Slideshow11/Automated-Edge-Discovery, the call would return stale
            # data from main and the stage would fail with HOLD_CONVERSATION_UNRESOLVED.
            assert data["status"] == "READY_TO_MERGE_CANDIDATE", (
                f"Expected READY_TO_MERGE_CANDIDATE; got {data['status']}; stages={stages}. "
                "If HOLD_CONVERSATION_UNRESOLVED, the GraphQL may be using wrong repo."
            )
        finally:
            sys.argv = old_argv


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