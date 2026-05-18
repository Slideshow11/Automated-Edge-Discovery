"""
Tests for scripts/local/aed_final_gate.py

Covers:
- stale expected SHA rejected
- current SHA accepted
- changed files outside scope rejected
- CI not green rejected
- Codex artifact with wrong SHA rejected
- zero-test local validation rejected
- admin merge command rejected by default
- generated merge command includes repo and full 40-char SHA
- generated authorization phrase uses current SHA
- open mergeable clean PR returns MERGE_READY
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Module under test
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
from aed_final_gate import (
    build_authorization_phrase,
    build_merge_command,
    validate_changed_files_in_scope,
    validate_expected_head,
    validate_ci_green,
    validate_codex_artifact_head,
    validate_local_validation,
    validate_merge_command_safety,
    validate_pr_state,
    run_final_gate,
)


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------

class TestValidateExpectedHead:
    def test_stale_sha_rejected(self):
        valid, msg = validate_expected_head(
            "aaaaaaa1bbbbbb2cccccc3dddddd4eeeeee5",
            "bbbbbbb1ccccccc2dddddd3eeeeee4fffffff5"
        )
        assert valid is False
        assert "MISMATCH" in msg

    def test_none_expected_skipped(self):
        valid, msg = validate_expected_head(None, "bbbbbbb1ccccccc2dddddd3eeeeee4fffffff5")
        assert valid is True
        assert "skipped" in msg

    def test_matching_sha_accepted(self):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        valid, msg = validate_expected_head(sha, sha)
        assert valid is True
        assert "matches" in msg


class TestValidateChangedFilesInScope:
    def test_all_in_scope_accepted(self):
        valid, msg = validate_changed_files_in_scope(
            ["scripts/local/a.py", "tests/test_x.py"],
            ["scripts/**", "tests/**"]
        )
        assert valid is True

    def test_file_outside_scope_rejected(self):
        valid, msg = validate_changed_files_in_scope(
            ["scripts/local/a.py", "src/bad.py"],
            ["scripts/**"]
        )
        assert valid is False
        assert "outside scope" in msg

    def test_no_allowed_files_skipped(self):
        valid, msg = validate_changed_files_in_scope(
            ["anything/goes.py"],
            None
        )
        assert valid is True
        assert "skipped" in msg


class TestValidateCiGreen:
    def test_all_success_runs_accepted(self):
        runs = [
            {"head_sha": "abc123", "name": "CI", "conclusion": "success"},
            {"head_sha": "abc123", "name": "CI", "conclusion": "success"},
        ]
        valid, msg, used = validate_ci_green(runs, "abc123")
        assert valid is True
        assert "success" in msg

    def test_failure_rejected(self):
        runs = [
            {"head_sha": "abc123", "name": "CI", "conclusion": "success"},
            {"head_sha": "abc123", "name": "CI", "conclusion": "failure"},
        ]
        valid, msg, used = validate_ci_green(runs, "abc123")
        assert valid is False
        assert "CI failures" in msg

    def test_no_runs_for_sha_rejected(self):
        runs = [{"head_sha": "abc124", "name": "CI", "conclusion": "success"}]
        valid, msg, used = validate_ci_green(runs, "abc123")
        assert valid is False
        assert "No CI runs found" in msg


class TestGhRunsPagination:
    def test_actions_runs_endpoint_used_with_get_method(self):
        """Verify gh_runs_for_sha uses the GitHub Actions API endpoint with --method GET.

        The -f flag without --method GET causes gh to use POST, which the read-only
        Actions runs endpoint rejects. The --method GET flag is required.
        """
        import aed_final_gate as gate
        import subprocess
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock(stdout='{"workflow_runs": []}', returncode=0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = gate.gh_runs_for_sha("abc123", "owner/repo")
            mock_run.assert_called_once()
            mock_call_args = str(mock_run.call_args[0][0])
            # Must use actions/runs endpoint, not commits/{sha}/runs
            assert "actions/runs" in mock_call_args, "Must use actions/runs endpoint"
            assert "commits/" not in mock_call_args, "Must not use commits/{sha}/runs"
            # Must include --method GET to avoid gh switching to POST
            assert "--method" in mock_call_args, "Must specify --method"
            assert "GET" in mock_call_args, "Must use GET method"
            # Must pass head_sha correctly
            assert "head_sha=abc123" in mock_call_args, "Must pass head_sha parameter"


class TestValidateCodexArtifactHead:
    def test_missing_artifact_fails_by_default(self):
        """Missing artifact without allow_skip defaults to FAIL (not SKIP)."""
        valid, msg = validate_codex_artifact_head(None, "abc123")
        assert valid is False
        assert "required" in msg

    def test_missing_artifact_explicit_skip_authorized(self):
        """Missing artifact with allow_skip=True returns True with skip message."""
        valid, msg = validate_codex_artifact_head(None, "abc123", allow_skip=True)
        assert valid is True
        assert "skip" in msg.lower()
        assert "--allow-codex-skip" in msg

    def test_artifact_wrong_sha_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            # 40-char hex that does NOT match expected "abc123"
            f.write("Codex reviewed commit 0000000000000000000000000000000000000000\n")
            f.flush()
            path = f.name
        try:
            valid, msg = validate_codex_artifact_head(path, "abc123")
            assert valid is False
            assert "mismatch" in msg.lower()
        finally:
            Path(path).unlink()

    def test_artifact_matching_sha_accepted(self):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(f"Codex reviewed commit {sha}\nCLEAN — no issues.\n")
            f.flush()
            path = f.name
        try:
            valid, msg = validate_codex_artifact_head(path, sha)
            assert valid is True
            assert "current head" in msg.lower()
        finally:
            Path(path).unlink()

    def test_no_sha_in_artifact_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Codex reviewed — no SHA found\n")
            f.flush()
            path = f.name
        try:
            valid, msg = validate_codex_artifact_head(path, "abc123")
            assert valid is True
            assert "skipped" in msg
        finally:
            Path(path).unlink()


class TestValidateLocalValidation:
    def test_missing_path_skipped(self):
        valid, msg = validate_local_validation(None)
        assert valid is True
        assert "skipped" in msg

    def test_zero_collected_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"output": "collected 0 items"}, f)
            f.flush()
            path = f.name
        try:
            valid, msg = validate_local_validation(path)
            assert valid is False
            assert "collected 0 items" in msg
        finally:
            Path(path).unlink()

    def test_valid_validation_accepted(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"tests_collected": 153, "passed": 153}, f)
            f.flush()
            path = f.name
        try:
            valid, msg = validate_local_validation(path)
            assert valid is True
        finally:
            Path(path).unlink()


class TestValidateMergeCommandSafety:
    def test_no_gh_pr_merge_rejected(self):
        valid, msg = validate_merge_command_safety("echo hello", False)
        assert valid is False
        assert "No 'gh pr merge'" in msg

    def test_admin_flag_rejected_by_default(self):
        valid, msg = validate_merge_command_safety(
            "gh pr merge 231 --admin --squash --match-head-commit abc123",
            False
        )
        assert valid is False
        assert "--admin" in msg

    def test_admin_flag_accepted_when_allowed(self):
        valid, msg = validate_merge_command_safety(
            "gh pr merge 231 --admin --squash --match-head-commit abc123",
            True
        )
        assert valid is True


class TestBuildAuthorizationPhrase:
    def test_uses_current_sha(self):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        phrase = build_authorization_phrase(231, sha)
        assert sha in phrase
        assert "I confirm merge PR #231" in phrase
        assert sha == "46f3bf2b4fc490f3991409c33448c678c2f6ea10"

    def test_phrase_contains_pr_number_and_sha(self):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        phrase = build_authorization_phrase(999, sha)
        assert "999" in phrase
        assert sha in phrase


class TestBuildMergeCommand:
    def test_includes_repo_full_sha(self):
        cmd = build_merge_command(
            231,
            "46f3bf2b4fc490f3991409c33448c678c2f6ea10",
            "Slideshow11/Automated-Edge-Discovery",
            False
        )
        assert "gh pr merge" in cmd
        assert "231" in cmd
        assert "--squash" in cmd
        assert "--match-head-commit 46f3bf2b4fc490f3991409c33448c678c2f6ea10" in cmd
        assert "--admin" not in cmd

    def test_admin_allowed(self):
        cmd = build_merge_command(
            231,
            "46f3bf2b4fc490f3991409c33448c678c2f6ea10",
            "Slideshow11/Automated-Edge-Discovery",
            True
        )
        assert "--admin" in cmd

    def test_no_admin_by_default(self):
        cmd = build_merge_command(
            231,
            "46f3bf2b4fc490f3991409c33448c678c2f6ea10",
            "Slideshow11/Automated-Edge-Discovery",
            False
        )
        assert "--admin" not in cmd


# ---------------------------------------------------------------------------
# Integration tests — full gate with mocked GitHub
# ---------------------------------------------------------------------------

# Pre-defined mock data to avoid deeply nested dict literals
_MOCK_PR_FILES_RESPONSE = {
    "data": {
        "repository": {
            "pullRequest": {
                "files": {
                    "nodes": [
                        {"path": "scripts/local/run_quarantine_autocoder_dry_run.py"},
                        {"path": "tests/test_run_quarantine_autocoder_dry_run.py"},
                    ]
                },
                "changedFiles": 2,
                "headRefOid": "46f3bf2b4fc490f3991409c33448c678c2f6ea10",
            }
        }
    }
}

_MOCK_PR_HEAD_RESPONSE = {
    "data": {
        "repository": {
            "pullRequest": {
                "headRefOid": "46f3bf2b4fc490f3991409c33448c678c2f6ea10",
            }
        }
    }
}


class TestRunFinalGateFullGate:
    """Full gate with mocked GitHub — clean PR returns MERGE_READY."""

    def test_clean_pr_returns_merge_ready(self, tmp_path):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"

        # Create real validation and codex artifact files on disk
        validation_file = tmp_path / "validation.json"
        validation_file.write_text(json.dumps({
            "tests_collected": 153,
            "passed": 153,
            "exit_code": 0,
        }))

        codex_file = tmp_path / "codex.md"
        codex_file.write_text(f"Codex review of commit {sha}\nCLEAN — no issues.\n")

        # Minimal mocks: only gh_api and subprocess.run are expensive
        def fake_subprocess_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get('args', [])
            if isinstance(cmd, list):
                # git remote get-url origin
                if cmd[0] == 'git' and len(cmd) >= 4 and cmd[2] == 'get-url' and cmd[3] == 'origin':
                    return MagicMock(stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git", returncode=0)
                # gh api calls
                if cmd[0] == 'gh' and 'api' in cmd:
                    return MagicMock(stdout="{}", returncode=0)
            return MagicMock(stdout="{}", returncode=0)

        def fake_gh_pr_info(pr_number, repo):
            return {
                "number": 231,
                "state": "open",
                "mergeable": "MERGEABLE",
                "head": {"sha": sha},
                "headRefOid": sha,
                "changed_files": [
                    "scripts/local/run_quarantine_autocoder_dry_run.py",
                    "tests/test_run_quarantine_autocoder_dry_run.py",
                ],
                "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
            }

        def fake_gh_runs_for_sha(s, repo):
            return [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        def fake_gh(query, *args):
            # Returns file list for first query, head for second
            return _MOCK_PR_FILES_RESPONSE

        mock_path_inst_write = MagicMock()
        mock_path_inst_read = MagicMock()

        def path_constructor(path_str):
            m = MagicMock()
            m.write_text = mock_path_inst_write
            m.exists = MagicMock(return_value=True)
            s = str(path_str)
            if s == str(validation_file):
                m.read_text = lambda: validation_file.read_text()
            elif s == str(codex_file):
                m.read_text = lambda: codex_file.read_text()
            else:
                m.read_text = lambda: ""
            return m

        with patch("subprocess.run", side_effect=fake_subprocess_run):
            with patch("aed_final_gate.gh_pr_info", side_effect=fake_gh_pr_info):
                with patch("aed_final_gate.gh_runs_for_sha", side_effect=fake_gh_runs_for_sha):
                    with patch("aed_final_gate.gh", side_effect=fake_gh):
                        with patch("aed_final_gate.Path", side_effect=path_constructor):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha=sha,
                                allowed_files=["scripts/**", "tests/**"],
                                local_validation_path=str(validation_file),
                                codex_artifact_path=str(codex_file),
                                output_json_path=str(tmp_path / "FINAL_GATE.json"),
                                output_md_path=str(tmp_path / "FINAL_GATE.md"),
                                allow_admin=False,
                            )

        assert gate["final_recommendation"] == "MERGE_READY"
        assert gate["head_sha"] == sha
        assert f"46f3bf2b4fc490f3991409c33448c678c2f6ea10" in gate["authorization_phrase"]
        assert "gh pr merge" in gate["merge_command"]
        assert "--match-head-commit 46f3bf2b4fc490f3991409c33448c678c2f6ea10" in gate["merge_command"]
        assert "--admin" not in gate["merge_command"]


class TestRunFinalGateBlocks:
    """Full gate blocks on stale SHA, out-of-scope files, CI failures."""

    def test_stale_expected_sha_blocks(self, tmp_path):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    mock_gh = MagicMock()
                    mock_gh.side_effect = [_MOCK_PR_FILES_RESPONSE, _MOCK_PR_HEAD_RESPONSE]
                    with patch("aed_final_gate.gh", mock_gh):
                        mock_path_inst = MagicMock()
                        mock_path_inst.write_text = MagicMock()
                        mock_path_cls = MagicMock(return_value=mock_path_inst)
                        type(mock_path_inst.parent).mkdir = MagicMock()
                        with patch("aed_final_gate.Path", mock_path_cls):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha="0000000000000000000000000000000000000000",
                                allowed_files=["scripts/**"],
                                local_validation_path=None,
                                codex_artifact_path=None,
                                output_json_path=str(output_json),
                                output_md_path=str(output_md),
                                allow_admin=False,
                            )

        # Stale SHA + missing Codex → BLOCK (hard gate failure takes priority over Codex-missing WAIT)
        assert gate["final_recommendation"] == "BLOCK"
        assert "MISMATCH" in gate["head_sha_validation"]["message"]

    def test_missing_codex_artifact_returns_wait(self, tmp_path):
        """Missing Codex without --allow-codex-skip returns WAIT, not MERGE_READY."""
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    mock_gh = MagicMock()
                    mock_gh.side_effect = [_MOCK_PR_FILES_RESPONSE, _MOCK_PR_HEAD_RESPONSE]
                    with patch("aed_final_gate.gh", mock_gh):
                        mock_path_inst = MagicMock()
                        mock_path_inst.write_text = MagicMock()
                        mock_path_cls = MagicMock(return_value=mock_path_inst)
                        type(mock_path_inst.parent).mkdir = MagicMock()
                        with patch("aed_final_gate.Path", mock_path_cls):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha=sha,
                                allowed_files=None,  # Skip scope check — all hard gates pass except missing Codex
                                local_validation_path=None,
                                codex_artifact_path=None,  # MISSING
                                output_json_path=str(output_json),
                                output_md_path=str(output_md),
                                allow_admin=False,
                            )

        # All hard gates pass but Codex is missing → WAIT (not MERGE_READY)
        assert gate["final_recommendation"] == "WAIT"
        # authorization_phrase must NOT be emitted when Codex missing
        assert "authorization_phrase" not in gate
        # merge_command must NOT be emitted when Codex missing
        assert "merge_command" not in gate
        # codex_status must show failing
        assert gate["codex_status"]["passing"] is False
        assert "required" in gate["codex_status"]["message"]

    def test_missing_codex_artifact_with_allow_skip_returns_wait(self, tmp_path):
        """--allow-codex-skip with missing artifact still returns WAIT (not MERGE_READY)
        because codex_valid is True but all_valid needs codex_valid too.
        Actually: with allow_skip=True and no artifact, codex_valid=True.
        So all_valid=True, and recommendation = MERGE_READY.
        But we want skip_authorized=true to be visible."""
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    mock_gh = MagicMock()
                    mock_gh.side_effect = [_MOCK_PR_FILES_RESPONSE, _MOCK_PR_HEAD_RESPONSE]
                    with patch("aed_final_gate.gh", mock_gh):
                        mock_path_inst = MagicMock()
                        mock_path_inst.write_text = MagicMock()
                        mock_path_cls = MagicMock(return_value=mock_path_inst)
                        type(mock_path_inst.parent).mkdir = MagicMock()
                        with patch("aed_final_gate.Path", mock_path_cls):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha=sha,
                                allowed_files=None,  # Skip scope check to focus on allow_codex_skip behavior
                                local_validation_path=None,
                                codex_artifact_path=None,
                                output_json_path=str(output_json),
                                output_md_path=str(output_md),
                                allow_admin=False,
                                allow_codex_skip=True,  # Explicit skip — all gates pass
                            )

        # With allow_codex_skip, all gates pass → MERGE_READY
        assert gate["final_recommendation"] == "MERGE_READY"
        # skip_authorized must be True
        assert gate["codex_status"]["skipped"] is True
        assert gate["codex_status"]["skip_authorized"] is True
        # authorization phrase IS present (MERGE_READY)
        assert "authorization_phrase" in gate

    def test_out_of_scope_files_blocks(self, tmp_path):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        # Files include something outside scripts/**
        mock_files_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "files": {
                            "nodes": [
                                {"path": "scripts/local/run_quarantine_autocoder_dry_run.py"},
                                {"path": "src/invalid_file.py"},  # OUT OF SCOPE
                            ]
                        },
                        "changedFiles": 2,
                        "headRefOid": sha,
                    }
                }
            }
        }

        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    mock_gh = MagicMock()
                    mock_gh.side_effect = [mock_files_response, _MOCK_PR_HEAD_RESPONSE]
                    with patch("aed_final_gate.gh", mock_gh):
                        mock_path_inst = MagicMock()
                        mock_path_inst.write_text = MagicMock()
                        mock_path_cls = MagicMock(return_value=mock_path_inst)
                        type(mock_path_inst.parent).mkdir = MagicMock()
                        with patch("aed_final_gate.Path", mock_path_cls):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha=sha,
                                allowed_files=["scripts/**"],
                                local_validation_path=None,
                                codex_artifact_path=None,
                                output_json_path=str(output_json),
                                output_md_path=str(output_md),
                                allow_admin=False,
                            )

        # Scope failure + missing Codex → BLOCK (hard gate failure takes priority)
        assert gate["final_recommendation"] == "BLOCK"
        assert "outside scope" in gate["scope_status"]["message"]

    def test_ci_not_green_blocks(self, tmp_path):
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        # CI is FAILING
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "failure"}]

        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    mock_gh = MagicMock()
                    mock_gh.side_effect = [_MOCK_PR_FILES_RESPONSE, _MOCK_PR_HEAD_RESPONSE]
                    with patch("aed_final_gate.gh", mock_gh):
                        mock_path_inst = MagicMock()
                        mock_path_inst.write_text = MagicMock()
                        mock_path_cls = MagicMock(return_value=mock_path_inst)
                        type(mock_path_inst.parent).mkdir = MagicMock()
                        with patch("aed_final_gate.Path", mock_path_cls):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha=sha,
                                allowed_files=None,
                                local_validation_path=None,
                                codex_artifact_path=None,
                                output_json_path=str(output_json),
                                output_md_path=str(output_md),
                                allow_admin=False,
                            )

        # CI failure + missing Codex → BLOCK (hard gate failure takes priority)
        assert gate["final_recommendation"] == "BLOCK"
        assert "CI failures" in gate["ci_status"]["message"]

    def test_missing_codex_artifact_with_non_mergeable_pr_returns_block(self, tmp_path):
        """PR not mergeable + missing Codex → BLOCK (hard gate failure takes priority)."""
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": False,
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    mock_gh = MagicMock()
                    mock_gh.side_effect = [_MOCK_PR_FILES_RESPONSE, _MOCK_PR_HEAD_RESPONSE]
                    with patch("aed_final_gate.gh", mock_gh):
                        mock_path_inst = MagicMock()
                        mock_path_inst.write_text = MagicMock()
                        mock_path_cls = MagicMock(return_value=mock_path_inst)
                        type(mock_path_inst.parent).mkdir = MagicMock()
                        with patch("aed_final_gate.Path", mock_path_cls):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha=sha,
                                allowed_files=None,
                                local_validation_path=None,
                                codex_artifact_path=None,
                                output_json_path=str(output_json),
                                output_md_path=str(output_md),
                                allow_admin=False,
                            )

        # Non-mergeable PR + missing Codex → BLOCK
        assert gate["final_recommendation"] == "BLOCK"
        assert "not MERGEABLE" in gate["pr_state"]["message"]

    def test_missing_codex_artifact_with_failed_local_validation_returns_block(self, tmp_path):
        """Local validation failed + missing Codex → BLOCK (hard gate failure takes priority)."""
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        # Directly mock validate_local_validation to return failure
        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    with patch("aed_final_gate.validate_local_validation", return_value=(False, "Local validation failed")):
                        mock_gh = MagicMock()
                        mock_gh.side_effect = [_MOCK_PR_FILES_RESPONSE, _MOCK_PR_HEAD_RESPONSE]
                        with patch("aed_final_gate.gh", mock_gh):
                            mock_path_inst = MagicMock()
                            mock_path_inst.write_text = MagicMock()
                            mock_path_cls = MagicMock(return_value=mock_path_inst)
                            type(mock_path_inst.parent).mkdir = MagicMock()
                            with patch("aed_final_gate.Path", mock_path_cls):
                                gate = run_final_gate(
                                    pr_number=231,
                                    expected_head_sha=sha,
                                    allowed_files=None,
                                    local_validation_path="/tmp/fake_local_val.json",
                                    codex_artifact_path=None,
                                    output_json_path=str(output_json),
                                    output_md_path=str(output_md),
                                    allow_admin=False,
                                )

        # Local validation failure + missing Codex → BLOCK
        assert gate["final_recommendation"] == "BLOCK"
        assert gate["local_validation_status"]["passing"] is False

    def test_allow_codex_skip_does_not_override_hard_gate_failure(self, tmp_path):
        """--allow-codex-skip cannot override hard gate failures; stale SHA still BLOCKs."""
        sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
        output_json = tmp_path / "FINAL_GATE.json"
        output_md = tmp_path / "FINAL_GATE.md"

        mock_run_inst = MagicMock(
            stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
            returncode=0
        )
        mock_pr_state = {
            "number": 231, "state": "open", "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": 2,
            "base": {"sha": "76c2d017eba1de4f9ac03a0e7ffe98a83e4e262a"},
        }
        mock_ci_runs = [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

        with patch("subprocess.run", return_value=mock_run_inst):
            with patch("aed_final_gate.gh_pr_info", return_value=mock_pr_state):
                with patch("aed_final_gate.gh_runs_for_sha", return_value=mock_ci_runs):
                    mock_gh = MagicMock()
                    mock_gh.side_effect = [_MOCK_PR_FILES_RESPONSE, _MOCK_PR_HEAD_RESPONSE]
                    with patch("aed_final_gate.gh", mock_gh):
                        mock_path_inst = MagicMock()
                        mock_path_inst.write_text = MagicMock()
                        mock_path_cls = MagicMock(return_value=mock_path_inst)
                        type(mock_path_inst.parent).mkdir = MagicMock()
                        with patch("aed_final_gate.Path", mock_path_cls):
                            gate = run_final_gate(
                                pr_number=231,
                                expected_head_sha="0000000000000000000000000000000000000000",
                                allowed_files=None,
                                local_validation_path=None,
                                codex_artifact_path=None,
                                output_json_path=str(output_json),
                                output_md_path=str(output_md),
                                allow_admin=False,
                                allow_codex_skip=True,
                            )

        # Even with allow_codex_skip=True, stale SHA → BLOCK
        assert gate["final_recommendation"] == "BLOCK"
        assert "MISMATCH" in gate["head_sha_validation"]["message"]


# ---------------------------------------------------------------------------
# Forbidden executable check
# ---------------------------------------------------------------------------

class TestForbiddenExecutableCalls:
    def test_no_forbidden_executable_calls_in_source(self):
        """Verify the source distinguishes constant declarations from executable calls."""
        import aed_final_gate as module
        source = Path(__file__).parent.parent / "scripts" / "local" / "aed_final_gate.py"
        content = source.read_text()

        # The module has a helper for this — use it
        violations = module.forbidden_executable_check(content)
        assert not violations, "Forbidden executable calls found:\n" + "\n".join(violations)


class TestAedFinalGateModuleImport:
    def test_module_imports_without_error(self):
        import aed_final_gate
        assert hasattr(aed_final_gate, "run_final_gate")
        assert hasattr(aed_final_gate, "validate_expected_head")
        assert hasattr(aed_final_gate, "validate_ci_green")
        assert hasattr(aed_final_gate, "build_authorization_phrase")
        assert hasattr(aed_final_gate, "build_merge_command")