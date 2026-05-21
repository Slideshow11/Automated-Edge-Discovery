"""
Tests for audit_claude_invocation.py

No execution of real Claude, no shell=True, no file mutation beyond output.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "local" / "audit_claude_invocation.py"

# ---------------------------------------------------------------------------
# State constants (must match the script)
# ---------------------------------------------------------------------------

STATE_NO_CLAUDE         = "NO_CLAUDE_INVOCATION_DETECTED"
STATE_MOCK_ONLY         = "MOCK_ONLY_RUN_DETECTED"
STATE_CONTRACT_ONLY     = "CONTRACT_ONLY_RUN_DETECTED"
STATE_CLAUDE_INVOKED    = "CLAUDE_INVOCATION_DETECTED"
STATE_ARTIFACTS_MISSING = "HOLD_ARTIFACTS_MISSING"
STATE_REPO_DIRTY        = "HOLD_REPO_DIRTY"
STATE_EXTERNAL_MUTATION = "HOLD_EXTERNAL_MUTATION_EVIDENCE"
STATE_UNKNOWN           = "HOLD_UNKNOWN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_audit(
    run_root: Path,
    allow_dirty: bool = False,
    strict: bool = False,
    mock_git_clean: bool = True,
) -> dict:
    """Run audit_invocation() directly with mocked git status."""
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
    import audit_claude_invocation as audit

    def fake_git_clean(repo_root):
        return mock_git_clean

    with patch.object(audit, "_git_status_clean", fake_git_clean):
        result = audit.audit_invocation(
            run_root=run_root,
            repo_root=REPO_ROOT,
            allow_dirty=allow_dirty,
            strict=strict,
            output_json=None,
            output_md=None,
        )
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_repo():
    """Verify repo git status is clean."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        pytest.skip(f"Repo not clean: {result.stdout}")
    yield


@pytest.fixture
def empty_run_dir(tmp_path):
    """Run directory with no result.json."""
    run_root = tmp_path / "empty_run"
    run_root.mkdir()
    return run_root


@pytest.fixture
def mock_run_dir(tmp_path):
    """result.json with mock mode and PATCH_READY status."""
    run_root = tmp_path / "mock_run"
    run_root.mkdir()
    result = {
        "status": "PATCH_READY_FOR_HUMAN_REVIEW",
        "execution_mode": "mock",
        "run_id": "test-mock-run",
        "changed_files": ["README.md"],
        "validation_errors": [],
        "main_git_status_before": "clean",
        "main_git_status_after": "clean",
    }
    (run_root / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return run_root


@pytest.fixture
def contract_run_dir(tmp_path):
    """result.json with claude mode stub returning HOLD_CLAUDE_IMPLEMENTATION_PENDING."""
    run_root = tmp_path / "contract_run"
    run_root.mkdir()
    result = {
        "status": "HOLD_CLAUDE_IMPLEMENTATION_PENDING",
        "execution_mode": "claude",
        "run_id": "test-contract-run",
        "claude_command_contract_valid": True,
        "claude_command_contract_summary": "argv=['claude','--continue']; cwd=/tmp/wt; timeout=300s",
        "claude_command_contract_errors": [],
        "changed_files": [],
        "validation_errors": [
            "Real Claude executor not yet implemented. "
            "Skeleton present: execution.mode='claude' is recognized but blocked."
        ],
        "main_git_status_before": "clean",
        "main_git_status_after": "clean",
    }
    (run_root / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return run_root


@pytest.fixture
def claude_invoked_run_dir(tmp_path):
    """result.json with claude invocation fields."""
    run_root = tmp_path / "claude_invoked_run"
    run_root.mkdir()
    result = {
        "status": "PATCH_READY_FOR_HUMAN_REVIEW",
        "execution_mode": "claude",
        "run_id": "test-claude-run",
        "claude_exit_code": 0,
        "claude_started_at": "2026-05-21T12:00:00Z",
        "claude_elapsed_seconds": 47.3,
        "claude_stdout_path": str(run_root / "claude_stdout.txt"),
        "claude_stderr_path": str(run_root / "claude_stderr.txt"),
        "changed_files": ["src/main.py"],
        "validation_errors": [],
    }
    stdout = run_root / "claude_stdout.txt"
    stdout.write_text("role: assistant\n\n## Plan\n\n...", encoding="utf-8")
    (run_root / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return run_root


@pytest.fixture
def pmg_blocked_run_dir(tmp_path):
    """result.json with PMG compare showing blocked mutations."""
    run_root = tmp_path / "pmg_blocked_run"
    run_root.mkdir()
    result = {
        "status": "HOLD_EXTERNAL_MUTATION",
        "execution_mode": "mock",
        "run_id": "test-pmg-blocked",
        "pmg_compare_json_path": str(run_root / "pmg_compare.json"),
        "changed_files": ["x.txt"],
        "validation_errors": ["external mutation detected"],
    }
    pmg_data = {"status": "blocked", "blocked": 3, "message": "Hermes tree mutated"}
    (run_root / "pmg_compare.json").write_text(json.dumps(pmg_data), encoding="utf-8")
    (run_root / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return run_root


# ---------------------------------------------------------------------------
# Tests: state classification
# ---------------------------------------------------------------------------

class TestMockOnly:
    def test_mock_result_json_returns_mock_only(self, mock_run_dir):
        result = _run_audit(mock_run_dir)
        assert result["status"] == STATE_MOCK_ONLY, result["status"]
        assert result["real_claude_invoked"] is False
        assert result["run_kind"] == "mock"

    def test_real_claude_invoked_false_for_mock(self, mock_run_dir):
        result = _run_audit(mock_run_dir)
        assert result["real_claude_invoked"] is False

    def test_recommendation_mock(self, mock_run_dir):
        result = _run_audit(mock_run_dir)
        assert "Mock run confirmed" in result["recommendation"]


class TestContractOnly:
    def test_contract_result_returns_contract_only(self, contract_run_dir):
        result = _run_audit(contract_run_dir)
        assert result["status"] == STATE_CONTRACT_ONLY, result["status"]
        assert result["real_claude_invoked"] is False
        assert result["run_kind"] == "contract_only"

    def test_real_claude_invoked_false_for_contract(self, contract_run_dir):
        result = _run_audit(contract_run_dir)
        assert result["real_claude_invoked"] is False

    def test_recommendation_contract(self, contract_run_dir):
        result = _run_audit(contract_run_dir)
        assert "Contract-only" in result["recommendation"] or "not yet implemented" in result["recommendation"]


class TestClaudeInvocation:
    def test_claude_exit_code_returns_invoked(self, claude_invoked_run_dir):
        result = _run_audit(claude_invoked_run_dir)
        assert result["status"] == STATE_CLAUDE_INVOKED, result["status"]
        assert result["real_claude_invoked"] is True

    def test_claude_started_at_field_returns_invoked(self, tmp_path):
        run_root = tmp_path / "run"
        run_root.mkdir()
        result = {
            "status": "PATCH_READY_FOR_HUMAN_REVIEW",
            "execution_mode": "claude",
            "claude_started_at": "2026-05-21T12:00:00Z",
        }
        (run_root / "result.json").write_text(json.dumps(result), encoding="utf-8")
        data = _run_audit(run_root)
        assert data["status"] == STATE_CLAUDE_INVOKED
        assert data["real_claude_invoked"] is True

    def test_transcript_file_with_content_returns_invoked(self, tmp_path):
        run_root = tmp_path / "run"
        run_root.mkdir()
        transcript = run_root / "transcript.txt"
        transcript.write_text(
            "role: assistant\n\n## Plan\n\nThis is a Claude response.\n",
            encoding="utf-8",
        )
        result_json = {
            "status": "PATCH_READY_FOR_HUMAN_REVIEW",
            "execution_mode": "claude",
            "claude_transcript_path": str(transcript),
        }
        (run_root / "result.json").write_text(json.dumps(result_json), encoding="utf-8")
        data = _run_audit(run_root)
        assert data["status"] == STATE_CLAUDE_INVOKED
        assert data["real_claude_invoked"] is True

    def test_contract_summary_alone_does_not_count_as_invocation(self, contract_run_dir):
        """Having claude_command_contract_summary is NOT evidence of real invocation."""
        result = _run_audit(contract_run_dir)
        assert result["status"] == STATE_CONTRACT_ONLY
        assert result["real_claude_invoked"] is False

    def test_recommendation_for_invoked(self, claude_invoked_run_dir):
        result = _run_audit(claude_invoked_run_dir)
        assert "Real Claude invocation detected" in result["recommendation"]


class TestMissingArtifacts:
    def test_empty_run_dir_returns_hold(self, empty_run_dir):
        result = _run_audit(empty_run_dir)
        assert result["status"] == STATE_ARTIFACTS_MISSING
        assert result["real_claude_invoked"] is False

    def test_missing_includes_result_json(self, empty_run_dir):
        result = _run_audit(empty_run_dir)
        assert any("result.json" in m for m in result["missing"])


class TestExternalMutation:
    def test_pmg_blocked_returns_external_mutation(self, pmg_blocked_run_dir):
        result = _run_audit(pmg_blocked_run_dir)
        assert result["status"] == STATE_EXTERNAL_MUTATION
        assert result["real_claude_invoked"] is False

    def test_evidence_contains_pmg_status(self, pmg_blocked_run_dir):
        result = _run_audit(pmg_blocked_run_dir)
        assert any("pmg_status" in e for e in result["evidence"])


class TestRepoDirty:
    def test_dirty_repo_returns_hold(self, mock_run_dir):
        result = _run_audit(mock_run_dir, allow_dirty=False, mock_git_clean=False)
        assert result["status"] == STATE_REPO_DIRTY
        assert result["real_claude_invoked"] is False

    def test_allow_dirty_bypasses_repo_check(self, mock_run_dir):
        result = _run_audit(mock_run_dir, allow_dirty=True, mock_git_clean=False)
        assert result["status"] != STATE_REPO_DIRTY


class TestRealClaudeInvokedAlwaysFalse:
    """real_claude_invoked is derived from classification, not set by the script."""

    def test_mock_never_invoked(self, mock_run_dir):
        result = _run_audit(mock_run_dir)
        assert result["real_claude_invoked"] is False

    def test_contract_never_invoked(self, contract_run_dir):
        result = _run_audit(contract_run_dir)
        assert result["real_claude_invoked"] is False

    def test_artifacts_missing_never_invoked(self, empty_run_dir):
        result = _run_audit(empty_run_dir)
        assert result["real_claude_invoked"] is False

    def test_repo_dirty_never_invoked(self, mock_run_dir):
        result = _run_audit(mock_run_dir, allow_dirty=False, mock_git_clean=False)
        assert result["real_claude_invoked"] is False


# ---------------------------------------------------------------------------
# Tests: output files
# ---------------------------------------------------------------------------

class TestOutputFiles:
    def test_json_output_written(self, mock_run_dir, tmp_path):
        out_json = tmp_path / "audit.json"
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
        import audit_claude_invocation as audit
        with patch.object(audit, "_git_status_clean", lambda r: True):
            result = audit.audit_invocation(
                run_root=mock_run_dir,
                repo_root=REPO_ROOT,
                allow_dirty=False,
                strict=False,
                output_json=out_json,
                output_md=None,
            )
        assert out_json.exists()
        loaded = json.loads(out_json.read_text())
        assert loaded["status"] == STATE_MOCK_ONLY
        assert loaded["real_claude_invoked"] is False

    def test_md_output_written(self, mock_run_dir, tmp_path):
        out_md = tmp_path / "audit.md"
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
        import audit_claude_invocation as audit
        with patch.object(audit, "_git_status_clean", lambda r: True):
            result = audit.audit_invocation(
                run_root=mock_run_dir,
                repo_root=REPO_ROOT,
                allow_dirty=False,
                strict=False,
                output_json=None,
                output_md=out_md,
            )
        assert out_md.exists()
        content = out_md.read_text()
        assert STATE_MOCK_ONLY in content
        assert "real_claude_invoked" in content


# ---------------------------------------------------------------------------
# Tests: no forbidden patterns in script
# ---------------------------------------------------------------------------

class TestNoForbiddenPatterns:
    def test_no_shell_in_subprocess_calls(self):
        """Verify no shell=True in live subprocess calls."""
        content = SCRIPT.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if "shell=True" not in line:
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            before = line[:line.index("shell=True")]
            if '"' in before or "'" in before:
                continue
            assert False, f"shell=True in live code at line {i}: {stripped}"

    def test_no_claude_invocation_in_script(self):
        """Script itself must not invoke Claude or use forbidden side effects."""
        content = SCRIPT.read_text()
        # These patterns, if present in live code (not a comment), indicate a violation
        forbidden = [
            # Claude invocation
            ("import claude", "LLM import"),
            ("from claude", "LLM import"),
            ("import anthropic", "LLM import"),
            ("from anthropic", "LLM import"),
            ("import openai", "LLM import"),
            ("from openai", "LLM import"),
            # Side effects
            ("gh pr create", "GitHub PR creation"),
            ("gh pr merge", "GitHub PR merge"),
            ("git push", "git push"),
            ("repository_dispatch", "dispatch"),
            ("board", "board"),
            ("dispatch", "dispatch"),
            ("shell=True", "shell=True in subprocess"),
        ]
        for pat, description in forbidden:
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if pat in line:
                    # For shell=True, check it's not just in a docstring reference
                    if pat == "shell=True":
                        # Skip if it's a docstring mention (has quote before)
                        idx = line.index(pat)
                        before = line[:idx]
                        if '"' in before or "'" in before:
                            continue
                    assert False, f"Forbidden {description} '{pat}' at line {i}: {stripped}"

    def test_no_pr_creation(self):
        content = SCRIPT.read_text()
        assert "gh pr create" not in content
        assert "gh pr merge" not in content
        assert "git push" not in content


# ---------------------------------------------------------------------------
# Tests: no mutation
# ---------------------------------------------------------------------------

class TestNoMutation:
    def test_harness_not_mutated(self, clean_repo, mock_run_dir):
        import scripts.local.run_temp_worktree_execution as harness
        before = harness.__file__ and Path(harness.__file__).read_text()
        if before is None:
            # Module imported from zip or otherwise
            pytest.skip("Cannot read harness file")
        _run_audit(mock_run_dir)
        import scripts.local.run_temp_worktree_execution as harness2
        after = Path(harness2.__file__).read_text()
        assert before == after

    def test_no_worktree_created_by_audit(self, clean_repo, mock_run_dir):
        before = set(p for p in REPO_ROOT.rglob("*") if "worktrees" in str(p))
        _run_audit(mock_run_dir)
        after = set(p for p in REPO_ROOT.rglob("*") if "worktrees" in str(p))
        created = [p for p in (after - before) if p.is_dir()]
        assert not created


# ---------------------------------------------------------------------------
# Tests: JSON output fields
# ---------------------------------------------------------------------------

class TestJsonOutputFields:
    def test_required_fields_present(self, mock_run_dir):
        result = _run_audit(mock_run_dir)
        for field in ("status", "real_claude_invoked", "run_kind", "checks",
                      "evidence", "missing", "recommendation", "generated_at"):
            assert field in result, f"Missing field: {field}"

    def test_generated_at_is_iso_format(self, mock_run_dir):
        result = _run_audit(mock_run_dir)
        assert "T" in result["generated_at"]
        assert "Z" in result["generated_at"] or "+" in result["generated_at"]


# ---------------------------------------------------------------------------
# Tests: clean repo fixture skipped when dirty
# ---------------------------------------------------------------------------

class TestRepoDirtyBypass:
    def test_allow_dirty_allows_dirty_repo(self, mock_run_dir):
        """When --allow-dirty is passed, dirty repo does not block."""
        result = _run_audit(mock_run_dir, allow_dirty=True, mock_git_clean=False)
        # Should not be HOLD_REPO_DIRTY — should get to actual classification
        assert result["status"] != STATE_REPO_DIRTY
        assert result["real_claude_invoked"] is False


# ---------------------------------------------------------------------------
# Tests: pmg_status check in artifact scan
# ---------------------------------------------------------------------------

class TestPmgStatus:
    def test_pmg_not_clean_returns_external_mutation(self, pmg_blocked_run_dir):
        result = _run_audit(pmg_blocked_run_dir)
        assert result["status"] == STATE_EXTERNAL_MUTATION

    def test_pmg_clean_does_not_block(self, mock_run_dir):
        """PMG status clean → does not override mock classification."""
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
        import audit_claude_invocation as audit
        with patch.object(audit, "_git_status_clean", lambda r: True):
            result = audit.audit_invocation(
                run_root=mock_run_dir,
                repo_root=REPO_ROOT,
                allow_dirty=False,
                strict=False,
                output_json=None,
                output_md=None,
            )
            # Should be MOCK_ONLY, not blocked — pmg_status may not be present
            # when no pmg artifact exists; the key check is status is MOCK_ONLY
            assert result["status"] == STATE_MOCK_ONLY