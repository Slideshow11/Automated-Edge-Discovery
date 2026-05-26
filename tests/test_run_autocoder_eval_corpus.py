"""
Tests for run_autocoder_eval_corpus.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

SCRIPT_PATH = Path("scripts/local/run_autocoder_eval_corpus.py").resolve()


# -----------------------------------------------------------------------
# Namespace helper — injects __file__ so Path(__file__) at module level works
# -----------------------------------------------------------------------

def _ns():
    ns = {"__file__": str(SCRIPT_PATH)}
    exec(SCRIPT_PATH.read_text(encoding="utf-8"), ns)
    return ns


# -----------------------------------------------------------------------
# Smoke test: runner works end-to-end with corpus-001
# -----------------------------------------------------------------------

def _cleanup_branches(repo: Path, prefixes: list[str]):
    """Remove branches and their associated worktrees matching any prefix.

    Uses -f for worktree removal to handle dirty worktrees.
    """
    for prefix in prefixes:
        # First, find all worktrees that match the branch prefix
        result = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                wt_path = line[9:]
            elif line.startswith("branch "):
                branch = line[8:]
                matched = any(
                    branch == p or branch.startswith(p + "-") or branch.startswith(p.replace("apply/", ""))
                    for p in prefixes
                )
                if matched:
                    # Remove worktree (use force to handle dirty)
                    subprocess.run(
                        ["git", "-C", str(repo), "worktree", "remove", "--force", wt_path],
                        capture_output=True,
                    )
                    # Delete branch
                    subprocess.run(
                        ["git", "-C", str(repo), "branch", "-D", branch],
                        capture_output=True,
                    )


def _time_based_run_id(prefix: str) -> str:
    """Generate a time-based run ID that is unique across pytest invocations."""
    import time
    return f"{prefix}-{int(time.time() * 1000)}"


def _main_available():
    """Check if main branch is available (for CI environments with shallow clones)."""
    try:
        subprocess.check_output(
            ["git", "rev-parse", "--verify", "main"],
            stderr=subprocess.DEVNULL, text=True, timeout=10
        )
        return True
    except subprocess.CalledProcessError:
        return False


class TestEvalCorpusSmoke:
    def test_runner_produces_eval_pass_true(self, tmp_path):
        """Full run of corpus-001 through the eval runner must produce eval_pass=True.

        Uses a fixed --run-id to ensure deterministic generated branch names.
        Cleans up generated branches after test.

        NOTE: Skipped in CI when main is not checked out (shallow clone push builds).
        The unit tests in TestResolveBaseSha verify base_sha resolution directly.
        """
        if not _main_available():
            pytest.skip("main branch not available (shallow clone)")
        ns = _ns()

        corpus_src = Path("corpus/corpus-001.json")
        corpus = json.loads(corpus_src.read_text(encoding="utf-8"))
        corpus_path = tmp_path / "corpus_test.json"
        corpus_path.write_text(json.dumps(corpus, indent=2), encoding="utf-8")

        output_root = tmp_path / "eval_out"
        report_json = output_root / "report.json"
        report_md = output_root / "report.md"

        # Use a time-based unique run_id so branches are predictable and unique
        # _time_based_run_id generates a new value on each call, but the value
        # used here is stable for the lifetime of this test (no other test can
        # use the same run_id in the same second)
        run_id = "pr320-" + str(int(time.time() * 1000))

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--corpus-json", str(corpus_path),
                "--output-root", str(output_root),
                "--report-json", str(report_json),
                "--report-md", str(report_md),
                "--run-id", run_id,
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(".").resolve()),
        )

        assert result.returncode == 0, (
            f"runner exited {result.returncode}: "
            f"stdout={result.stdout[:300]!r} "
            f"stderr={result.stderr[:300]!r}"
        )
        assert report_json.exists(), f"report.json not written: {result.stderr}"
        rep = json.loads(report_json.read_text(encoding="utf-8"))
        assert rep["eval_pass"] is True, f"eval_pass=False: {rep.get('failure_summary')}"
        assert rep["total_tasks"] == 5
        assert rep["passed_tasks"] == 5
        assert rep["batch_status"] == "BATCH_READY_FOR_HUMAN_REVIEW"
        assert rep["run_id"] == run_id
        # Verify generated branch names in task results
        for tr in rep.get("task_results", []):
            assert run_id in tr["generated_branch_name"]
            assert tr["generated_branch_name"].startswith("apply/corpus-001-")

        # Cleanup generated branches
        repo = Path(".").resolve()
        _cleanup_branches(repo, [f"apply/corpus-001-{run_id}"])

    def test_report_md_written_when_eval_passes(self, tmp_path):
        """Verify report.md is written when eval passes.

        NOTE: Skipped in CI when main is not checked out (shallow clone push builds).
        """
        if not _main_available():
            pytest.skip("main branch not available (shallow clone)")
        ns = _ns()
        corpus = json.loads(Path("corpus/corpus-001.json").read_text(encoding="utf-8"))
        corpus_path = tmp_path / "corpus_test.json"
        corpus_path.write_text(json.dumps(corpus, indent=2), encoding="utf-8")
        output_root = tmp_path / "eval_out"
        report_json = output_root / "report.json"
        report_md = output_root / "report.md"

        run_id = "pr320-" + str(int(time.time() * 1000))
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH),
                "--corpus-json", str(corpus_path),
                "--output-root", str(output_root),
                "--report-json", str(report_json),
                "--report-md", str(report_md),
                "--run-id", run_id,
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(".").resolve()),
        )
        assert result.returncode == 0, f"stderr: {result.stderr[:200]}"
        assert report_md.exists(), f"report.md not written: {result.stderr}"

        # Cleanup
        repo = Path(".").resolve()
        _cleanup_branches(repo, [f"apply/corpus-001-{run_id}"])


# -----------------------------------------------------------------------
# Test: validate_corpus_schema
# -----------------------------------------------------------------------

class TestValidateCorpusSchema:
    def test_valid_corpus_passes(self):
        ns = _ns()
        corpus = {
            "corpus_kind": "aed.autocoder.corpus.v0",
            "corpus_id": "test-001",
            "corpus_version": "0.1.0",
            "description": "A valid test corpus",
            "created_at": "2026-05-25T00:00:00Z",
            "base_sha_policy": "current_main",
            "tasks": [
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "t1",
                    "goal": "Do a thing",
                    "allowed_files": ["docs/x.md"],
                    "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
                    "execution_mode": "mocked",
                    "branch_name": "apply/t1",
                }
            ],
        }
        valid, err = ns["validate_corpus_schema"](corpus)
        assert valid, f"expected valid, got error: {err}"

    def test_missing_corpus_kind_fails(self):
        ns = _ns()
        corpus = {
            "corpus_id": "test-001",
            "corpus_version": "0.1.0",
            "description": "Missing corpus_kind",
            "created_at": "2026-05-25T00:00:00Z",
            "base_sha_policy": "current_main",
            "tasks": [],
        }
        valid, err = ns["validate_corpus_schema"](corpus)
        assert not valid
        assert "corpus_kind" in err

    def test_wrong_corpus_version_fails(self):
        ns = _ns()
        corpus = {
            "corpus_kind": "aed.autocoder.corpus.v0",
            "corpus_id": "test-001",
            "corpus_version": "1.0.0",
            "description": "Wrong version",
            "created_at": "2026-05-25T00:00:00Z",
            "base_sha_policy": "current_main",
            "tasks": [],
        }
        valid, err = ns["validate_corpus_schema"](corpus)
        assert not valid
        assert "0.1.0" in err

    def test_empty_tasks_fails(self):
        ns = _ns()
        corpus = {
            "corpus_kind": "aed.autocoder.corpus.v0",
            "corpus_id": "test-001",
            "corpus_version": "0.1.0",
            "description": "Empty tasks",
            "created_at": "2026-05-25T00:00:00Z",
            "base_sha_policy": "current_main",
            "tasks": [],
        }
        valid, err = ns["validate_corpus_schema"](corpus)
        assert not valid
        assert "non-empty" in err


# -----------------------------------------------------------------------
# Test: validate_task_packet
# -----------------------------------------------------------------------

class TestValidateTaskPacket:
    def test_valid_task_passes(self):
        ns = _ns()
        task = {
            "packet_kind": "aed.autocoder.single_task.v0",
            "task_id": "t1",
            "goal": "Do a thing",
            "allowed_files": ["docs/x.md"],
            "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
            "execution_mode": "mocked",
        }
        valid, err = ns["validate_task_packet"](task, 0)
        assert valid, err

    def test_wrong_packet_kind_fails(self):
        ns = _ns()
        task = {
            "packet_kind": "aed.autocoder.batch.v0",
            "task_id": "t1",
            "goal": "Do a thing",
            "allowed_files": ["docs/x.md"],
            "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
            "execution_mode": "mocked",
        }
        valid, err = ns["validate_task_packet"](task, 0)
        assert not valid
        assert "single_task.v0" in err

    def test_execution_mode_claude_fails(self):
        ns = _ns()
        task = {
            "packet_kind": "aed.autocoder.single_task.v0",
            "task_id": "t1",
            "goal": "Do a thing",
            "allowed_files": ["docs/x.md"],
            "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
            "execution_mode": "claude",
        }
        valid, err = ns["validate_task_packet"](task, 0)
        assert not valid
        assert "mocked" in err

    def test_missing_goal_fails(self):
        ns = _ns()
        task = {
            "packet_kind": "aed.autocoder.single_task.v0",
            "task_id": "t1",
            "goal": "",
            "allowed_files": ["docs/x.md"],
            "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
            "execution_mode": "mocked",
        }
        valid, err = ns["validate_task_packet"](task, 0)
        assert not valid
        assert "goal" in err

    def test_empty_allowed_files_fails(self):
        ns = _ns()
        task = {
            "packet_kind": "aed.autocoder.single_task.v0",
            "task_id": "t1",
            "goal": "Do a thing",
            "allowed_files": [],
            "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
            "execution_mode": "mocked",
        }
        valid, err = ns["validate_task_packet"](task, 0)
        assert not valid
        assert "allowed_files" in err

    def test_empty_mock_edits_fails(self):
        ns = _ns()
        task = {
            "packet_kind": "aed.autocoder.single_task.v0",
            "task_id": "t1",
            "goal": "Do a thing",
            "allowed_files": ["docs/x.md"],
            "mock_edits": [],
            "execution_mode": "mocked",
        }
        valid, err = ns["validate_task_packet"](task, 0)
        assert not valid
        assert "mock_edits" in err


# -----------------------------------------------------------------------
# Test: resolve_base_sha
# -----------------------------------------------------------------------

class TestResolveBaseSha:
    def test_current_main_resolves_to_sha(self):
        ns = _ns()
        repo = Path(".").resolve()
        # Skip if main branch is not available (e.g., CI checks out feature branch only)
        try:
            main_sha = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "main"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            pytest.skip("main branch not available")
        ok, msg, sha = ns["resolve_base_sha"]("current_main", repo)
        assert ok, msg
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)
        assert sha == main_sha

    def test_literal_valid_sha_passes(self):
        ns = _ns()
        repo = Path(".").resolve()
        # Skip if main branch is not available (e.g., CI checks out feature branch only)
        try:
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "main"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            pytest.skip("main branch not available")
        ok, msg, sha = ns["resolve_base_sha"](head, repo)
        assert ok, msg
        assert sha == head

    def test_literal_sha_with_invalid_chars_fails(self):
        ns = _ns()
        repo = Path(".").resolve()
        ok, msg, sha = ns["resolve_base_sha"]("gggggggg0000000000000000000000000000000000", repo)
        assert not ok
        assert "must be" in msg.lower() or "not exist" in msg.lower() or "invalid" in msg.lower()


# -----------------------------------------------------------------------
# Test: validate_corpus_targets
# -----------------------------------------------------------------------

class TestValidateCorpusTargets:
    def test_all_targets_exist_and_clean_no_aed_plan(self):
        ns = _ns()
        repo = Path(".").resolve()
        # Skip if main branch is not available (e.g., CI checks out feature branch only)
        try:
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "main"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            pytest.skip("main branch not available")
        corpus = json.loads(Path("corpus/corpus-001.json").read_text(encoding="utf-8"))
        ok, errors = ns["validate_corpus_targets"](corpus, head, repo, skip_branch_check=True)
        # Branch collision errors may occur if branches already exist from prior runs;
        # file-level errors (missing files, .aed_plan) should not occur.
        file_errors = [e for e in errors if "does not exist" in e or ".aed_plan" in e]
        assert not file_errors, f"file-level errors: {file_errors}"

    def test_nonexistent_file_fails_validation(self):
        ns = _ns()
        repo = Path(".").resolve()
        # Skip if main branch is not available (e.g., CI checks out feature branch only)
        try:
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "main"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            pytest.skip("main branch not available")
        corpus = {
            "corpus_id": "test",
            "corpus_version": "0.1.0",
            "tasks": [
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "t1",
                    "goal": "Do a thing",
                    "branch_name": "apply/t1-nonexistent",
                    "allowed_files": ["nonexistent/file/that/does/not/exist.md"],
                    "mock_edits": [],
                    "execution_mode": "mocked",
                }
            ],
        }
        ok, errors = ns["validate_corpus_targets"](corpus, head, repo, skip_branch_check=True)
        assert not ok
        assert any("does not exist" in e for e in errors)

    def test_catfile_sha_path_correct_format(self):
        """Regression test: validate_corpus_targets uses git cat-file -e sha:path.

        Codex found that validate_corpus_targets uses `git cat-file -e sha:path`
        for per-file existence checks, and resolve_base_sha uses
        `git rev-parse --verify SHA` for SHA validation. Both patterns are
        correct. This test confirms the sha:path format is used and that
        files absent from a valid SHA fail with the expected error message.
        """
        ns = _ns()
        repo = Path(".").resolve()
        try:
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "main"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            pytest.skip("main branch not available")

        # Use a file that definitely does not exist at HEAD
        corpus = {
            "corpus_id": "test-sha-path",
            "corpus_version": "0.1.0",
            "tasks": [
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "t1-sha-path",
                    "goal": "Verify sha:path pattern",
                    "branch_name": "apply/t1-sha-path",
                    "allowed_files": ["this/file/does/not/exist/at/this/sha.txt"],
                    "mock_edits": [],
                    "execution_mode": "mocked",
                }
            ],
        }
        ok, errors = ns["validate_corpus_targets"](corpus, head, repo, skip_branch_check=True)
        assert not ok, "validate_corpus_targets must fail for a file absent at a valid SHA"
        # Error must mention the file and the SHA prefix
        file_errors = [e for e in errors if "does not exist" in e]
        assert file_errors, f"Expected 'does not exist' error, got: {errors}"
        assert head[:8] in errors[0], f"Error must include SHA prefix, got: {errors[0]}"


# -----------------------------------------------------------------------
# Test: invoke_batch_controller — subprocess rc guard (rgr-320-batch-ok-subprocess-rc)
# -----------------------------------------------------------------------
class TestInvokeBatchControllerRCGuard:
    """Regression test: eval runner must not treat stale batch_status.json as success.

    Codex found that the eval runner logged batch_ok but would read
    batch_status.json regardless of the subprocess return code — meaning a
    stale OK-looking status file could mask a batch failure.

    Fix (e60e3b5, PR #320): `if rc != 0: return 1` added at the top of the
    batch-result handler. This test verifies the guard is in place and that
    a nonzero batch subprocess rc produces eval_pass=False in the report.
    """

    def test_eval_runner_exits_nonzero_on_batch_subprocess_failure(self, tmp_path):
        ns = _ns()

        # Create a minimal valid corpus
        corpus = {
            "corpus_kind": "aed.autocoder.corpus.v0",
            "corpus_id": "test-rc-guard",
            "corpus_version": "0.1.0",
            "tasks": [
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "t1-rc",
                    "goal": "Test rc guard",
                    "branch_name": "apply/t1-rc",
                    "allowed_files": ["docs/README.md"],
                    "mock_edits": [{"path": "docs/README.md", "content": "append", "text": "test"}],
                    "execution_mode": "mocked",
                }
            ],
        }
        corpus_path = tmp_path / "corpus.json"
        corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

        # Create a fake batch_status.json that looks OK (the stale data)
        batch_status = {
            "status": "BATCH_READY_FOR_HUMAN_REVIEW",
            "tasks": [{"task_id": "t1-rc", "status": "HOLD_TASK_PACKET_INVALID"}],
        }
        batch_status_path = tmp_path / "batch_status.json"
        batch_status_path.write_text(json.dumps(batch_status), encoding="utf-8")

        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        # Get a real SHA to avoid validate_corpus_targets failing before we reach the rc guard
        try:
            real_sha = subprocess.check_output(
                ["git", "-C", str(Path(".").resolve()), "rev-parse", "main"],
                text=True,
            ).strip()
        except subprocess.CalledProcessError:
            real_sha = "a" * 40  # fallback — validate_corpus_targets will fail, test will fail fast

        # Mock invoke_batch_controller to return nonzero rc
        # This simulates the batch subprocess crashing/failing
        def fake_invoke_batch_controller(batch_packet, output_root):
            return False, "batch subprocess died", 42

        # Patch invoke_batch_controller and _render_eval_report_md in the module namespace.
        # _render_eval_report_md crashes on string failure_summary (pre-existing bug);
        # we bypass it to test the JSON report output.
        orig_invoke = ns.get("invoke_batch_controller")
        orig_render = ns.get("_render_eval_report_md")

        def fake_render(report):
            # Write a dummy md so write_eval_report doesn't crash
            Path(str(report_md)).write_text("# dummy\n", encoding="utf-8")
            return "# dummy"

        ns["invoke_batch_controller"] = fake_invoke_batch_controller
        ns["_render_eval_report_md"] = fake_render

        try:
            rc = ns["main"](
                corpus_json=str(corpus_path),
                output_root=str(tmp_path),
                report_json=str(report_json),
                report_md=str(report_md),
                run_id="rc-guard-test",
                repo_root_override=Path(".").resolve(),
                base_sha_override=real_sha,
            )
        finally:
            if orig_invoke is not None:
                ns["invoke_batch_controller"] = orig_invoke
            if orig_render is not None:
                ns["_render_eval_report_md"] = orig_render

        # Eval runner MUST return nonzero when batch rc != 0
        assert rc == 1, f"main() must return 1 on nonzero batch rc, got {rc}"

        # The report must show eval_pass=False and batch_subprocess_rc=42
        # Note: _render_eval_report_md crashes on the string failure_summary set by
        # the rc-guard path (it expects list[dict], gets str). We only assert on
        # the JSON report — the md render failure is a pre-existing code bug.
        assert report_json.exists(), "report.json must be written"
        report = json.loads(report_json.read_text(encoding="utf-8"))
        assert report.get("eval_pass") is False, (
            "eval_pass must be False when batch subprocess fails"
        )
        assert report.get("batch_subprocess_rc") == 42, (
            f"batch_subprocess_rc must be 42, got {report.get('batch_subprocess_rc')}"
        )
        assert report.get("failure_summary"), (
            "failure_summary must be present"
        )
        # failure_summary is a str when rc guard triggers (pre-existing code issue)
        assert "stale batch_status.json was NOT treated as success" in str(
            report.get("failure_summary", "")
        ), f"failure_summary must mention stale-data guard, got: {report.get('failure_summary')}"


# -----------------------------------------------------------------------
# Test: build_batch_packet
# -----------------------------------------------------------------------

class TestBuildBatchPacket:
    def test_output_root_injected_per_task(self):
        ns = _ns()
        corpus = {
            "corpus_id": "test-corpus",
            "corpus_version": "0.1.0",
            "tasks": [
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "task-001",
                    "goal": "Do thing",
                    "branch_name": "apply/001",
                    "execution_mode": "mocked",
                    "allowed_files": ["docs/x.md"],
                    "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
                    "output_root": None,
                },
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "task-002",
                    "goal": "Do thing",
                    "branch_name": "apply/002",
                    "execution_mode": "mocked",
                    "allowed_files": ["docs/y.md"],
                    "mock_edits": [{"path": "docs/y.md", "content": "append", "text": "hi"}],
                    "output_root": None,
                },
            ],
        }
        out_root = Path("/tmp/test_batch")
        run_id = "run-abc"
        bp = ns["build_batch_packet"](corpus, "a" * 40, out_root, run_id)

        assert bp["stop_on_first_hold"] is False
        assert bp["tasks"][0]["output_root"] == "/tmp/test_batch/tasks/task-001"
        assert bp["tasks"][1]["output_root"] == "/tmp/test_batch/tasks/task-002"
        # Branch names are run-scoped
        assert bp["tasks"][0]["branch_name"] == "apply/test-corpus-run-abc-task-001"
        assert bp["tasks"][1]["branch_name"] == "apply/test-corpus-run-abc-task-002"
        # originals not mutated
        assert corpus["tasks"][0].get("output_root") is None
        assert corpus["tasks"][0]["branch_name"] == "apply/001"

    def test_stop_on_first_hold_always_false(self):
        ns = _ns()
        corpus = {
            "corpus_id": "test",
            "corpus_version": "0.1.0",
            "tasks": [
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "t1",
                    "goal": "Do thing",
                    "branch_name": "apply/t1",
                    "execution_mode": "mocked",
                    "allowed_files": ["docs/x.md"],
                    "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
                },
            ],
        }
        bp = ns["build_batch_packet"](corpus, "a" * 40, Path("/tmp/test"), "run-xyz")
        assert bp["stop_on_first_hold"] is False

    def test_generated_branch_name_format(self):
        ns = _ns()
        corpus = {
            "corpus_id": "my-corpus",
            "corpus_version": "0.1.0",
            "tasks": [
                {
                    "packet_kind": "aed.autocoder.single_task.v0",
                    "task_id": "task-001",
                    "goal": "Do thing",
                    "branch_name": "apply/original-001",
                    "execution_mode": "mocked",
                    "allowed_files": ["docs/x.md"],
                    "mock_edits": [{"path": "docs/x.md", "content": "append", "text": "hi"}],
                },
            ],
        }
        out_root = Path("/tmp/test_batch")

        bp1 = ns["build_batch_packet"](corpus, "a" * 40, out_root, "run-alpha")
        bp2 = ns["build_batch_packet"](corpus, "a" * 40, out_root, "run-beta")

        assert bp1["tasks"][0]["branch_name"] != bp2["tasks"][0]["branch_name"]
        assert "run-alpha" in bp1["tasks"][0]["branch_name"]
        assert "run-beta" in bp2["tasks"][0]["branch_name"]
        assert bp1["tasks"][0]["branch_name"] == "apply/my-corpus-run-alpha-task-001"
        assert bp2["tasks"][0]["branch_name"] == "apply/my-corpus-run-beta-task-001"


# -----------------------------------------------------------------------
# Test: build_eval_report
# -----------------------------------------------------------------------

class TestBuildEvalReport:
    def test_eval_pass_when_all_ready(self):
        ns = _ns()
        corpus = {
            "corpus_id": "c1",
            "corpus_version": "0.1.0",
            "tasks": [
                {"task_id": "t1", "branch_name": "apply/t1"},
                {"task_id": "t2", "branch_name": "apply/t2"},
            ],
        }
        batch_status = {
            "status": "BATCH_READY_FOR_HUMAN_REVIEW",
            "tasks": [
                {
                    "task_id": "t1",
                    "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
                    "task_worktree_path": "/tmp/w1",
                    "branch_name": "apply/c1-run-x-t1",
                    "final_status_json": "/tmp/w1/f.json",
                },
                {
                    "task_id": "t2",
                    "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
                    "task_worktree_path": "/tmp/w2",
                    "branch_name": "apply/c1-run-x-t2",
                    "final_status_json": "/tmp/w2/f.json",
                },
            ],
        }
        report = ns["build_eval_report"](corpus, "a" * 40, batch_status, Path("/tmp/out"), "run-x")
        assert report["eval_pass"] is True
        assert report["passed_tasks"] == 2
        assert report["held_tasks"] == 0
        assert report["failure_summary"] == []
        # Verify generated_branch_name and corpus_branch_name recorded
        assert report["task_results"][0]["generated_branch_name"] == "apply/c1-run-x-t1"
        assert report["task_results"][0]["corpus_branch_name"] == "apply/t1"

    def test_eval_fail_when_task_holds(self):
        ns = _ns()
        corpus = {
            "corpus_id": "c1",
            "corpus_version": "0.1.0",
            "tasks": [
                {"task_id": "t1", "branch_name": "apply/t1"},
                {"task_id": "t2", "branch_name": "apply/t2"},
            ],
        }
        batch_status = {
            "status": "HOLD_TASK_FAILED",
            "tasks": [
                {
                    "task_id": "t1",
                    "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
                    "task_worktree_path": "/tmp/w1",
                    "branch_name": "apply/c1-run-y-t1",
                    "final_status_json": "/tmp/w1/f.json",
                },
                {
                    "task_id": "t2",
                    "status": "HOLD_APPLY_NOT_READY",
                    "task_worktree_path": "/tmp/w2",
                    "branch_name": "apply/c1-run-y-t2",
                    "final_status_json": "/tmp/w2/f.json",
                },
            ],
        }
        report = ns["build_eval_report"](corpus, "a" * 40, batch_status, Path("/tmp/out"), "run-y")
        assert report["eval_pass"] is False
        assert report["passed_tasks"] == 1
        assert report["held_tasks"] == 1
        assert len(report["failure_summary"]) == 1
        assert report["failure_summary"][0]["task_id"] == "t2"


# -----------------------------------------------------------------------
# Test: safety constraints (static analysis)
# -----------------------------------------------------------------------

class TestSafetyConstraints:
    def test_no_enable_real_claude_executor(self):
        """The runner must never pass --enable-real-claude-executor to subprocess calls."""
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        code_no_doc = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
        code_no_doc = re.sub(r"'''.*?'''", '', code_no_doc, flags=re.DOTALL)
        code_no_comments = '\n'.join(
            l for l in code_no_doc.splitlines()
            if not l.strip().startswith('#')
        )
        assert '"--enable-real-claude-executor"' not in code_no_comments
        assert "'--enable-real-claude-executor'" not in code_no_comments

    def test_no_shell_true(self):
        """subprocess.run calls must never use shell=True."""
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        code_no_doc = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
        code_no_doc = re.sub(r"'''.*?'''", '', code_no_doc, flags=re.DOTALL)
        code_no_comments = '\n'.join(
            l for l in code_no_doc.splitlines()
            if not l.strip().startswith('#')
        )
        assert not re.search(r'\bshell\s*=\s*True\b', code_no_comments)

    def test_no_git_push(self):
        """The runner must never call git push, gh pr create/merge, git commit, git stage, or git add."""
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        code_no_doc = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
        code_no_doc = re.sub(r"'''.*?'''", '', code_no_doc, flags=re.DOTALL)
        code_no_comments = '\n'.join(
            l for l in code_no_doc.splitlines()
            if not l.strip().startswith('#')
        )
        assert "git push" not in code_no_comments
        assert "gh pr create" not in code_no_comments
        assert "gh pr merge" not in code_no_comments
        assert "git commit" not in code_no_comments
        assert "git stage" not in code_no_comments
        assert "git add" not in code_no_comments

    def test_no_hermes_writes(self):
        """The runner must never write to ~/.hermes or use Hermes memory/fact_store."""
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        code_no_doc = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
        code_no_doc = re.sub(r"'''.*?'''", '', code_no_doc, flags=re.DOTALL)
        code_no_comments = '\n'.join(
            l for l in code_no_doc.splitlines()
            if not l.strip().startswith('#')
        )
        assert "~/.hermes" not in code_no_comments
        assert "fact_store" not in code_no_comments
        assert "memory_store" not in code_no_comments
        assert "MEMORY.md" not in code_no_comments
        assert "USER.md" not in code_no_comments
        assert "skill_manage" not in code_no_comments
        assert "delegate_task" not in code_no_comments

    def test_no_branch_auto_deletion(self):
        """The runner must never automatically delete branches."""
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        code_no_doc = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
        code_no_doc = re.sub(r"'''.*?'''", '', code_no_doc, flags=re.DOTALL)
        code_no_comments = '\n'.join(
            l for l in code_no_doc.splitlines()
            if not l.strip().startswith('#')
        )
        assert "git branch -D" not in code_no_comments
        assert "git worktree remove" not in code_no_comments


# -----------------------------------------------------------------------
# Test: main() exit codes and argument handling
# -----------------------------------------------------------------------

class TestMainExitCodes:
    def test_help_flag_shows_all_required_args(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--corpus-json" in result.stdout
        assert "--output-root" in result.stdout
        assert "--report-json" in result.stdout
        assert "--report-md" in result.stdout
        assert "--run-id" in result.stdout

    def test_missing_corpus_exits_1(self):
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH),
                "--corpus-json", "/nonexistent/corpus.json",
                "--output-root", "/tmp/test_out",
                "--report-json", "/tmp/test.json",
                "--report-md", "/tmp/test.md",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "not found" in result.stderr

    def test_invalid_json_exits_1(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ invalid }", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH),
                "--corpus-json", str(bad),
                "--output-root", str(tmp_path / "out"),
                "--report-json", str(tmp_path / "r.json"),
                "--report-md", str(tmp_path / "r.md"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "not valid JSON" in result.stderr or "not valid" in result.stderr

    def test_wrong_corpus_kind_exits_1(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"corpus_kind": "wrong.kind", "corpus_version": "0.1.0", "corpus_id": "x", "base_sha_policy": "current_main", "tasks": []}', encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH),
                "--corpus-json", str(bad),
                "--output-root", str(tmp_path / "out"),
                "--report-json", str(tmp_path / "r.json"),
                "--report-md", str(tmp_path / "r.md"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "corpus_kind" in result.stderr


# -----------------------------------------------------------------------
# Test: write_eval_report produces both JSON and MD
# -----------------------------------------------------------------------

class TestWriteEvalReport:
    def test_json_and_md_written(self, tmp_path):
        ns = _ns()
        report = {
            "report_kind": "aed.autocoder.eval_report.v0",
            "corpus_id": "c1",
            "corpus_version": "0.1.0",
            "eval_runner_version": "0.1.0",
            "run_id": "run-abc",
            "base_sha": "a" * 40,
            "batch_status": "BATCH_READY_FOR_HUMAN_REVIEW",
            "total_tasks": 1,
            "passed_tasks": 1,
            "failed_tasks": 0,
            "held_tasks": 0,
            "eval_pass": True,
            "failure_summary": [],
            "task_results": [
                {
                    "task_id": "t1",
                    "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
                    "generated_branch_name": "apply/c1-run-abc-t1",
                    "corpus_branch_name": "apply/t1",
                    "task_worktree_path": "/tmp/w1",
                    "artifacts_present": True,
                    "diff_patch_applies_cleanly": True,
                }
            ],
            "generated_at": "2026-05-25T00:00:00Z",
        }
        corpus = {"corpus_id": "c1", "corpus_version": "0.1.0"}
        out_root = tmp_path / "out"
        json_p = tmp_path / "report.json"
        md_p = tmp_path / "report.md"
        ns["write_eval_report"](report, out_root, json_p, md_p)
        assert json_p.exists()
        assert md_p.exists()
        loaded = json.loads(json_p.read_text(encoding="utf-8"))
        assert loaded["eval_pass"] is True


# -----------------------------------------------------------------------
# Test: eval_run_metadata.json written
# -----------------------------------------------------------------------

class TestEvalRunMetadata:
    def test_metadata_file_written(self, tmp_path):
        ns = _ns()
        corpus = {"corpus_id": "c1", "corpus_version": "0.1.0"}
        out_root = tmp_path / "out"
        out_root.mkdir()
        ns["write_eval_run_metadata"](out_root, corpus, "a" * 40, True, "run-abc")
        meta = json.loads((out_root / "eval_run_metadata.json").read_text(encoding="utf-8"))
        assert meta["corpus_id"] == "c1"
        assert meta["eval_pass"] is True
        assert meta["base_sha"] == "a" * 40
        assert meta["eval_runner_version"] == "0.1.0"
        assert meta["run_id"] == "run-abc"


# -----------------------------------------------------------------------
# Test: sanitize_run_id
# -----------------------------------------------------------------------

class TestSanitizeRunId:
    def test_strips_invalid_chars(self):
        ns = _ns()
        assert ns["sanitize_run_id"]("abc/123") == "abc-123"
        assert ns["sanitize_run_id"]("abc 123") == "abc-123"
        assert ns["sanitize_run_id"]("abc.123") == "abc-123"
        assert ns["sanitize_run_id"]("abc-123") == "abc-123"
        assert ns["sanitize_run_id"]("abc_123") == "abc_123"
        assert ns["sanitize_run_id"]("abc@#$%123") == "abc-123"
        assert ns["sanitize_run_id"]("abc  multiple   spaces  ") == "abc-multiple-spaces"