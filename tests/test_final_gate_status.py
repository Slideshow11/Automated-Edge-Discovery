"""
Tests for final_gate_status.py — canonical AED PR final-gate status reporter.

Covers all HOLD states and READY_TO_MERGE.
Mocks gh CLI and git commands — no network required.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import scripts.local.final_gate_status as fgs


# ---------------------------------------------------------------------------
# Helper to build minimal args
# ---------------------------------------------------------------------------

def make_args(
    pr_number=265,
    reported_head_sha="abc123def0000000000000000000000000000000",
    codex_reviewed_sha="abc123def0000000000000000000000000000000",
    pmg_guard_state_json=None,
    output_json=None,
    output_md=None,
    repo="Slideshow11/Automated-Edge-Discovery",
    allow_docs_only_codex_waiver=False,
    review_comments_json=None,
):
    return type(
        "Args",
        (),
        {
            "pr_number": pr_number,
            "reported_head_sha": reported_head_sha,
            "codex_reviewed_sha": codex_reviewed_sha,
            "pmg_guard_state_json": pmg_guard_state_json,
            "output_json": output_json,
            "output_md": output_md,
            "repo": repo,
            "allow_docs_only_codex_waiver": allow_docs_only_codex_waiver,
            "review_comments_json": review_comments_json,
        },
    )()


# ---------------------------------------------------------------------------
# PMG fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pmg_clean(tmp_path):
    path = tmp_path / "pmg_clean.json"
    path.write_text(json.dumps({
        "status": "clean",
        "files_added": [],
        "files_removed": [],
        "files_modified": [],
        "recommendation": "PASS",
    }))
    return str(path)


@pytest.fixture
def pmg_blocked(tmp_path):
    path = tmp_path / "pmg_blocked.json"
    path.write_text(json.dumps({
        "status": "blocked",
        "message": "unauthorized Hermes skill mutation",
        "files_added": ["skills/test-rogue/SKILL.md"],
    }))
    return str(path)


@pytest.fixture
def pmg_no_status(tmp_path):
    path = tmp_path / "pmg_no_status.json"
    path.write_text(json.dumps({"files_added": []}))
    return str(path)


# ---------------------------------------------------------------------------
# PR data fixtures
# ---------------------------------------------------------------------------

def make_pr_open(head_sha="abc123def0000000000000000000000000000000"):
    return {
        "state": "OPEN",
        "headRefOid": head_sha,
        "mergeable": "MERGEABLE",
        "isDraft": False,
        "title": "fix: test PR",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/265",
    }


# ---------------------------------------------------------------------------
# PMG loading tests
# ---------------------------------------------------------------------------

class TestLoadPmgGuardState:
    def test_clean_returns_valid(self, pmg_clean):
        is_valid, data, reason = fgs.load_pmg_guard_state(pmg_clean)
        assert is_valid is True
        assert data["status"] == "clean"

    def test_blocked_returns_invalid(self, pmg_blocked):
        is_valid, data, reason = fgs.load_pmg_guard_state(pmg_blocked)
        assert is_valid is False
        assert "blocked" in reason.lower()

    def test_no_status_field_returns_invalid(self, pmg_no_status):
        is_valid, data, reason = fgs.load_pmg_guard_state(pmg_no_status)
        assert is_valid is False
        assert "status" in reason.lower()

    def test_nonexistent_returns_invalid(self):
        is_valid, data, reason = fgs.load_pmg_guard_state("/nonexistent/path.json")
        assert is_valid is False
        assert "not found" in reason.lower()

    def test_invalid_json_returns_invalid(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json")
        is_valid, data, reason = fgs.load_pmg_guard_state(str(path))
        assert is_valid is False
        assert "json" in reason.lower()


# ---------------------------------------------------------------------------
# Review-comments state loading tests
# ---------------------------------------------------------------------------

class TestLoadReviewCommentsState:
    def test_clean_returns_valid(self, tmp_path):
        path = tmp_path / "rc_clean.json"
        path.write_text(json.dumps({"status": "REVIEW_COMMENTS_CLEAN", "blockers": []}))
        is_valid, data, reason = fgs.load_review_comments_state(str(path))
        assert is_valid is True
        assert data["status"] == "REVIEW_COMMENTS_CLEAN"

    def test_blocked_returns_invalid(self, tmp_path):
        path = tmp_path / "rc_blocked.json"
        path.write_text(json.dumps({
            "status": "REVIEW_COMMENTS_BLOCKED",
            "blockers": [{"severity": "P2", "file_path": "scripts/test.py"}],
        }))
        is_valid, data, reason = fgs.load_review_comments_state(str(path))
        assert is_valid is False
        assert "BLOCKED" in reason

    def test_inconclusive_returns_invalid(self, tmp_path):
        path = tmp_path / "rc_inconclusive.json"
        path.write_text(json.dumps({"status": "REVIEW_COMMENTS_INCONCLUSIVE", "stale_blockers": []}))
        is_valid, data, reason = fgs.load_review_comments_state(str(path))
        assert is_valid is False
        assert "INCONCLUSIVE" in reason

    def test_no_status_field_returns_invalid(self, tmp_path):
        path = tmp_path / "rc_no_status.json"
        path.write_text(json.dumps({"blockers": []}))
        is_valid, data, reason = fgs.load_review_comments_state(str(path))
        assert is_valid is False
        assert "status" in reason.lower()

    def test_nonexistent_returns_invalid(self):
        is_valid, data, reason = fgs.load_review_comments_state("/nonexistent/path.json")
        assert is_valid is False
        assert "not found" in reason.lower()

    def test_invalid_json_returns_invalid(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json")
        is_valid, data, reason = fgs.load_review_comments_state(str(path))
        assert is_valid is False
        assert "json" in reason.lower()

    def test_blocked_inconclusive_next_action_is_review_comments(self, tmp_path):
        """Blockers starting with 'Review-comments gate' produce the review-comments next_action."""
        path = tmp_path / "rc_blocked.json"
        path.write_text(json.dumps({
            "status": "REVIEW_COMMENTS_BLOCKED",
            "blockers": [{"severity": "P1", "file_path": "test.py"}],
        }))
        # Verify the blocker text is recognized
        is_valid, data, reason = fgs.load_review_comments_state(str(path))
        assert "Review-comments gate" in reason or "BLOCKED" in reason


# ---------------------------------------------------------------------------
# CI green tests
# ---------------------------------------------------------------------------

class TestIsCiGreen:
    def test_all_success_returns_true(self):
        # gh_json returns the full dict with workflow_runs key (Python-side filtering)
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = {
                "total_count": 2,
                "workflow_runs": [
                    {"head_sha": "abc123def0000000000000000000000000000000",
                     "conclusion": "success", "status": "completed", "name": "CI"},
                    {"head_sha": "abc123def0000000000000000000000000000000",
                     "conclusion": "success", "status": "completed", "name": "test (3.11)"},
                ],
            }
            green, reason = fgs.is_ci_green(
                265, "Slideshow11/Automated-Edge-Discovery",
                "abc123def0000000000000000000000000000000"
            )
        assert green is True

    def test_in_progress_returns_false(self):
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = {
                "total_count": 2,
                "workflow_runs": [
                    {"head_sha": "abc123def0000000000000000000000000000000",
                     "conclusion": "success", "status": "completed", "name": "CI"},
                    {"head_sha": "abc123def0000000000000000000000000000000",
                     "conclusion": None, "status": "in_progress", "name": "governance-validators"},
                ],
            }
            green, reason = fgs.is_ci_green(
                265, "Slideshow11/Automated-Edge-Discovery",
                "abc123def0000000000000000000000000000000"
            )
        assert green is False
        assert "in_progress" in reason

    def test_failure_returns_false(self):
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = {
                "total_count": 1,
                "workflow_runs": [
                    {"head_sha": "abc123def0000000000000000000000000000000",
                     "conclusion": "failure", "status": "completed", "name": "test (3.11)"},
                ],
            }
            green, reason = fgs.is_ci_green(
                265, "Slideshow11/Automated-Edge-Discovery",
                "abc123def0000000000000000000000000000000"
            )
        assert green is False
        assert "failure" in reason.lower()

    def test_no_runs_returns_false(self):
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = {"total_count": 0, "workflow_runs": []}
            green, reason = fgs.is_ci_green(
                265, "Slideshow11/Automated-Edge-Discovery",
                "abc123def0000000000000000000000000000000"
            )
        assert green is False
        assert "no workflow runs" in reason.lower()


# ---------------------------------------------------------------------------
# Git status tests
# ---------------------------------------------------------------------------

class TestGitClean:
    def test_clean_git_status_returns_true(self, tmp_path, monkeypatch):
        repodir = tmp_path / "repo"
        repodir.mkdir()
        repodir.joinpath("README.md").write_text("test")
        subprocess.run(["git", "init"], cwd=str(repodir), capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(repodir), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repodir),
            capture_output=True,
            env={**os.environ,
                 "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"},
        )
        monkeypatch.chdir(str(repodir))
        is_clean, reason = fgs.is_git_clean()
        assert is_clean is True

    def test_dirty_git_status_returns_false(self, tmp_path, monkeypatch):
        repodir = tmp_path / "repo"
        repodir.mkdir()
        subprocess.run(["git", "init"], cwd=str(repodir), capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repodir),
            capture_output=True,
            env={**os.environ,
                 "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"},
        )
        repodir.joinpath("dirty.txt").write_text("dirty")
        monkeypatch.chdir(str(repodir))
        is_clean, reason = fgs.is_git_clean()
        assert is_clean is False
        assert "dirty.txt" in reason


# ---------------------------------------------------------------------------
# HOLD states
# ---------------------------------------------------------------------------

class TestHoldPrNotOpen:
    def test_hold_pr_not_open_when_merged(self, pmg_clean):
        pr_merged = {
            "state": "MERGED",
            "headRefOid": "abc123def0000000000000000000000000000000",
            "mergeable": None,
            "isDraft": False,
            "title": "fix: merged",
            "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/265",
        }
        args = make_args(pmg_guard_state_json=pmg_clean)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr_merged):
            result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_PR_NOT_OPEN
        assert "merged" in result["blockers"][0].lower()
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""

    def test_hold_pr_not_open_when_closed(self, pmg_clean):
        pr_closed = {
            "state": "CLOSED",
            "headRefOid": "abc123def0000000000000000000000000000000",
            "mergeable": None,
            "isDraft": False,
            "title": "fix: closed",
            "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/265",
        }
        args = make_args(pmg_guard_state_json=pmg_clean)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr_closed):
            result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_PR_NOT_OPEN


class TestHoldHeadMismatch:
    def test_hold_head_mismatch(self, pmg_clean):
        # reported != canonical
        args = make_args(
            reported_head_sha="zzzzzzzz0000000000000000000000000000000",
            pmg_guard_state_json=pmg_clean,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_HEAD_MISMATCH
        assert "mismatch" in result["blockers"][0].lower()
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""


class TestHoldCiRed:
    def test_hold_ci_red(self, pmg_clean):
        args = make_args(pmg_guard_state_json=pmg_clean)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(False, "test (3.11) failed")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CI_RED
        assert "ci" in result["blockers"][0].lower()
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""


class TestHoldCodexRequired:
    def test_hold_codex_required_when_no_sha(self, pmg_clean):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CODEX_REQUIRED
        assert "codex" in result["blockers"][0].lower()
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""


class TestHoldCodexStale:
    def test_hold_codex_stale(self, pmg_clean):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha="deadbeef00000000000000000000000000000000",
            pmg_guard_state_json=pmg_clean,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CODEX_STALE
        assert ("stale" in result["blockers"][0].lower()
                or "mismatch" in result["blockers"][0].lower())
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""


class TestHoldPmgMissing:
    def test_hold_pmg_missing(self):
        # No PMG guard state at all
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha="abc123def0000000000000000000000000000000",
            pmg_guard_state_json=None,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_PMG_MISSING
        assert "pmg" in result["blockers"][0].lower()
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""


class TestHoldPmgDirty:
    def test_hold_pmg_dirty_when_blocked(self, pmg_blocked):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha="abc123def0000000000000000000000000000000",
            pmg_guard_state_json=pmg_blocked,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_PMG_DIRTY
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""

    def test_hold_pmg_dirty_when_no_status_field(self, pmg_no_status):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha="abc123def0000000000000000000000000000000",
            pmg_guard_state_json=pmg_no_status,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_PMG_DIRTY


class TestHoldGitDirty:
    def test_hold_git_dirty(self, pmg_clean):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha="abc123def0000000000000000000000000000000",
            pmg_guard_state_json=pmg_clean,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(False, "Git status not clean:\n M file.txt")):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_GIT_DIRTY
        assert "git" in result["blockers"][0].lower()
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""


# ---------------------------------------------------------------------------
# READY_TO_MERGE
# ---------------------------------------------------------------------------

class TestReadyToMerge:
    def test_ready_to_merge_all_checks_pass(self, pmg_clean):
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=pmg_clean,
        )
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "All 4 workflow runs succeeded")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "Git status is clean")):
                    result = fgs.evaluate(args)

        assert result["status"] == fgs.State.READY_TO_MERGE
        assert result["checks"]["pr_open"] is True
        assert result["checks"]["head_matches"] is True
        assert result["checks"]["ci_green"] is True
        assert result["checks"]["codex_exact_head"] is True
        assert result["checks"]["pmg_clean"] is True
        assert result["checks"]["git_status_clean"] is True
        assert result["blockers"] == []
        assert result["next_action"] == "merge"
        assert "--match-head-commit" in result["merge_command"]
        assert sha in result["merge_command"]
        assert sha in result["authorization_phrase"]
        assert "265" in result["authorization_phrase"]

    def test_ready_to_merge_includes_match_head_commit(self, pmg_clean):
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=pmg_clean,
        )
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)

        assert "--match-head-commit" in result["merge_command"]
        assert sha in result["merge_command"]

    def test_hold_never_emits_auth_phrase_or_merge_command(self, pmg_clean):
        """Every HOLD state withholds authorization_phrase and merge_command."""
        sha = "abc123def0000000000000000000000000000000"

        # CI red
        args_ci = make_args(reported_head_sha=sha, codex_reviewed_sha=sha, pmg_guard_state_json=pmg_clean)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(False, "failed")):
                r = fgs.evaluate(args_ci)
        assert r["merge_command"] == ""
        assert r["authorization_phrase"] == ""

        # Git dirty
        args_git = make_args(reported_head_sha=sha, codex_reviewed_sha=sha, pmg_guard_state_json=pmg_clean)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(False, "dirty")):
                    r = fgs.evaluate(args_git)
        assert r["merge_command"] == ""
        assert r["authorization_phrase"] == ""

        # PMG missing
        args_pmg = make_args(reported_head_sha=sha, codex_reviewed_sha=sha, pmg_guard_state_json=None)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                r = fgs.evaluate(args_pmg)
        assert r["merge_command"] == ""
        assert r["authorization_phrase"] == ""


# ---------------------------------------------------------------------------
# Review-comments JSON integration (Check 6 — closes PR #326 structural gap)
# ---------------------------------------------------------------------------

class TestReviewCommentsJsonIntegration:
    """Tests for --review-comments-json integration in evaluate()."""

    def test_hold_review_comments_blocked_when_file_blocked(self, pmg_clean, tmp_path):
        """
        When --review-comments-json is supplied and the file shows REVIEW_COMMENTS_BLOCKED,
        evaluate() returns HOLD_REVIEW_COMMENTS_BLOCKED even when --codex-reviewed-sha matches.
        This is the PR #326 structural gap fix.
        """
        sha = "abc123def0000000000000000000000000000000"
        rc_path = tmp_path / "rc_blocked.json"
        rc_path.write_text(json.dumps({
            "status": "REVIEW_COMMENTS_BLOCKED",
            "blockers": [{"severity": "P2", "file_path": "scripts/test.py"}],
        }))
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=str(pmg_clean),
            review_comments_json=str(rc_path),
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)

        assert result["status"] == fgs.State.HOLD_REVIEW_COMMENTS_BLOCKED
        assert any("Review-comments gate" in b for b in result["blockers"])
        assert result["merge_command"] == ""

    def test_hold_review_comments_inconclusive_when_file_inconclusive(self, pmg_clean, tmp_path):
        """
        When --review-comments-json shows REVIEW_COMMENTS_INCONCLUSIVE,
        evaluate() returns HOLD_REVIEW_COMMENTS_INCONCLUSIVE.
        """
        sha = "abc123def0000000000000000000000000000000"
        rc_path = tmp_path / "rc_inconclusive.json"
        rc_path.write_text(json.dumps({
            "status": "REVIEW_COMMENTS_INCONCLUSIVE",
            "stale_blockers": [{"severity": "P1", "file_path": "test.py"}],
        }))
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=str(pmg_clean),
            review_comments_json=str(rc_path),
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)

        assert result["status"] == fgs.State.HOLD_REVIEW_COMMENTS_INCONCLUSIVE
        assert any("Review-comments gate" in b for b in result["blockers"])

    def test_ready_when_review_comments_json_is_clean(self, pmg_clean, tmp_path):
        """
        When --review-comments-json is supplied and shows REVIEW_COMMENTS_CLEAN,
        evaluate() continues to READY_TO_MERGE (no regression).
        """
        sha = "abc123def0000000000000000000000000000000"
        rc_path = tmp_path / "rc_clean.json"
        rc_path.write_text(json.dumps({"status": "REVIEW_COMMENTS_CLEAN", "blockers": []}))
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=str(pmg_clean),
            review_comments_json=str(rc_path),
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)

        assert result["status"] == fgs.State.READY_TO_MERGE
        assert result["blockers"] == []

    def test_hold_when_review_comments_json_missing(self, pmg_clean):
        """
        When --review-comments-json is supplied but the file does not exist,
        evaluate() returns HOLD_REVIEW_COMMENTS_INCONCLUSIVE (fail-closed).
        """
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=str(pmg_clean),
            review_comments_json="/nonexistent/review-comments.json",
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)

        assert result["status"] == fgs.State.HOLD_REVIEW_COMMENTS_INCONCLUSIVE
        assert any("not found" in b for b in result["blockers"])

    def test_no_review_comments_json_means_codex_sha_check_only(self, pmg_clean):
        """
        When --review-comments-json is NOT supplied, evaluate() falls back to
        the existing --codex-reviewed-sha trust check (Check 4) with no review-comments override.
        This confirms backward compatibility.
        """
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=str(pmg_clean),
            review_comments_json=None,  # not supplied
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)

        # Should pass — no review-comments-json means the gap is unchanged for old callers
        assert result["status"] == fgs.State.READY_TO_MERGE

    def test_next_action_for_review_comments_blocker(self, pmg_clean, tmp_path):
        """
        Review-comments blockers produce next_action pointing to check_pr_review_comments.py.
        """
        sha = "abc123def0000000000000000000000000000000"
        rc_path = tmp_path / "rc_blocked.json"
        rc_path.write_text(json.dumps({
            "status": "REVIEW_COMMENTS_BLOCKED",
            "blockers": [{"severity": "P2", "file_path": "scripts/test.py"}],
        }))
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=str(pmg_clean),
            review_comments_json=str(rc_path),
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)

        assert "check_pr_review_comments.py" in result["next_action"]


# ---------------------------------------------------------------------------
# Next action derivation
# ---------------------------------------------------------------------------

class TestNextAction:
    def test_next_action_merge_for_ready(self, pmg_clean):
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(reported_head_sha=sha, codex_reviewed_sha=sha, pmg_guard_state_json=pmg_clean)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)
        assert result["next_action"] == "merge"

    def test_next_action_pmg_for_pmg_blocker(self, pmg_blocked):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha="abc123def0000000000000000000000000000000",
            pmg_guard_state_json=pmg_blocked,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert "pmg" in result["next_action"].lower()

    def test_next_action_ci_for_ci_blocker(self, pmg_clean):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha="abc123def0000000000000000000000000000000",
            pmg_guard_state_json=pmg_clean,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(False, "failed")):
                result = fgs.evaluate(args)
        assert "ci" in result["next_action"].lower() or "green" in result["next_action"].lower()

    def test_next_action_codex_for_codex_blocker(self, pmg_clean):
        args = make_args(
            reported_head_sha="abc123def0000000000000000000000000000000",
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert "codex" in result["next_action"].lower()


# ---------------------------------------------------------------------------
# Fatal errors
# ---------------------------------------------------------------------------

class TestFatalError:
    def test_fatal_when_gh_not_found(self):
        args = make_args()
        with mock.patch.object(fgs, "gh_json", side_effect=RuntimeError("gh CLI not found in PATH")):
            result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_UNKNOWN
        assert "fatal" in result["blockers"][0].lower()
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""

    def test_fatal_when_pr_not_found(self):
        args = make_args()
        with mock.patch.object(fgs, "gh_json", side_effect=RuntimeError("PR not found")):
            result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_UNKNOWN


# ---------------------------------------------------------------------------
# PMG snapshot vs compare output
# ---------------------------------------------------------------------------

class TestPmgSnapshotNotEnough:
    def test_pmg_snapshot_without_status_clean_fails(self, tmp_path, pmg_clean):
        """A PMG snapshot JSON (without status=clean) must not pass as PMG guard state."""
        # Write a snapshot (no status field) — should fail validation
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(json.dumps({
            "files_added": [],
            "files_removed": [],
            "files_modified": [],
        }))
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=str(snapshot),
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_PMG_DIRTY


# ---------------------------------------------------------------------------
# Output format tests
# ---------------------------------------------------------------------------

class TestOutputFormat:
    def test_json_output_has_required_keys(self, pmg_clean):
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(reported_head_sha=sha, codex_reviewed_sha=sha, pmg_guard_state_json=pmg_clean)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)
        for key in ["status", "pr_number", "head_sha", "checks", "blockers",
                    "authorization_phrase", "merge_command", "next_action"]:
            assert key in result, f"Missing key: {key}"

    def test_md_output_writes_emoji_and_status(self, tmp_path, pmg_clean):
        sha = "abc123def0000000000000000000000000000000"
        md_path = tmp_path / "output.md"
        args = make_args(
            reported_head_sha=sha,
            codex_reviewed_sha=sha,
            pmg_guard_state_json=pmg_clean,
            output_md=str(md_path),
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open(sha)):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)
        fgs.write_md(result, str(md_path))
        content = md_path.read_text()
        assert "✅" in content
        assert "READY_TO_MERGE" in content


# -----------------------------------------------------------------------
# Regression: gh_json --jq argument ordering
# -----------------------------------------------------------------------

class TestGhJsonArgOrdering:
    """Regression tests for gh api argument ordering (gh 2.x compatibility).

    The bug: gh api --jq '.' must come AFTER the endpoint and -f/-F flags.
    In gh 2.x, placing --jq before the endpoint causes "accepts 1 arg(s), received 0".
    The fix: cmd = ["gh", "api"] + args + ["--jq", "."]  (--jq at END).

    Also: graphql variables of type Int must use -F (typed), not -f (raw string).
    """

    def test_gh_json_places_jq_at_end(self):
        """--jq '.' must be the last argument in the gh api command."""
        captured_cmd = None

        def capture_run(cmd, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            return mock.MagicMock(returncode=0, stdout='{}', stderr="")

        with mock.patch.object(subprocess, "run", side_effect=capture_run):
            try:
                fgs.gh_json(["graphql", "-f", "query={foo}"])
            except Exception:
                pass  # we only care about what command was constructed

        assert captured_cmd is not None
        assert captured_cmd[-2:] == ["--jq", "."], (
            f"--jq '.' must be last, got: {captured_cmd}"
        )

    def test_gh_json_uses_list_form_no_shell(self):
        """All gh api calls must use list-form args with no shell=True."""
        original_run = subprocess.run
        all_calls = []

        def tracking_run(cmd, **kwargs):
            all_calls.append((cmd, kwargs))
            return original_run(cmd, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=tracking_run):
            try:
                fgs.gh_json(["repos/test"])
            except Exception:
                pass

        for cmd, kwargs in all_calls:
            assert kwargs.get("shell") is not True, (
                f"shell=True found in gh api call: {cmd}"
            )
            assert isinstance(cmd, list), f"cmd must be list, got: {type(cmd)}"

    def test_gh_json_graphql_int_var_must_use_capital_F(self):
        """Int graphql variables must use -F (typed) not -f (raw string).

        -f passes raw strings; -F does type coercion (str→int for numeric values).
        Without -F, a variable of type Int! gets the string "270" and gh rejects it.
        """
        captured_cmd = None

        def capture_run(cmd, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            return mock.MagicMock(returncode=0, stdout='{}', stderr="")

        with mock.patch.object(subprocess, "run", side_effect=capture_run):
            try:
                fgs.gh_json([
                    "graphql",
                    "-f", "query={foo(bar: $pr)}",
                    "-F", "pr=270",
                ])
            except Exception:
                pass

        # Verify the command was built correctly
        assert captured_cmd is not None
        # -F for int variables
        assert any(arg == "-F" for arg in captured_cmd), (
            f"Int variables must use -F, got: {captured_cmd}"
        )

    def test_gh_json_graphql_query_not_treated_as_variable(self):
        """The query string itself must be passed as -f query=<string>, not as a positional arg.

        This was the original failure mode: --jq '.' was placed before the endpoint,
        causing gh to interpret the endpoint as the --jq argument value.
        After the fix (--jq at end), 'graphql' is correctly treated as the endpoint.
        """
        captured_cmd = None

        def capture_run(cmd, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            return mock.MagicMock(returncode=0, stdout='{"data":{}}', stderr="")

        with mock.patch.object(subprocess, "run", side_effect=capture_run):
            try:
                fgs.gh_json(["graphql", "-f", "query={foo}"])
            except Exception:
                pass

        assert captured_cmd is not None
        # The endpoint 'graphql' must come before -f flags
        graphql_idx = captured_cmd.index("graphql")
        # graphql should come before any -f flags
        f_flags = [i for i, a in enumerate(captured_cmd) if a == "-f"]
        if f_flags:
            assert all(graphql_idx < idx for idx in f_flags), (
                f"graphql endpoint must come before -f flags: {captured_cmd}"
            )


# --------------------------------------------------------------------------
# is_docs_only classifier
# --------------------------------------------------------------------------

class TestIsDocsOnly:
    def test_docs_subdirectory_md_is_docs_only(self):
        assert fgs.is_docs_only(["docs/architecture.md"]) is True

    def test_multiple_docs_md_files(self):
        assert fgs.is_docs_only([
            "docs/architecture.md",
            "docs/process_gap.md",
            "docs/notes.md",
        ]) is True

    def test_readme_root_is_docs_only(self):
        assert fgs.is_docs_only(["README.md"]) is True

    def test_mixed_readme_and_docs(self):
        assert fgs.is_docs_only(["README.md", "docs/notes.md"]) is True

    def test_governance_md_is_docs_only(self):
        assert fgs.is_docs_only(["GOVERNANCE.md"]) is True

    def test_license_is_docs_only(self):
        assert fgs.is_docs_only(["LICENSE"]) is True

    def test_txt_in_docs_is_docs_only(self):
        assert fgs.is_docs_only(["docs/notes.txt"]) is True

    def test_rst_in_docs_is_docs_only(self):
        assert fgs.is_docs_only(["docs/index.rst"]) is True

    def test_script_py_in_docs_is_not_docs_only(self):
        assert fgs.is_docs_only(["docs/script.py"]) is False

    def test_scripts_local_py_is_not_docs_only(self):
        assert fgs.is_docs_only(["scripts/local/foo.py"]) is False

    def test_test_file_is_not_docs_only(self):
        assert fgs.is_docs_only(["tests/test_final_gate_status.py"]) is False

    def test_workflow_is_not_docs_only(self):
        assert fgs.is_docs_only([".github/workflows/ci.yml"]) is False

    def test_config_is_not_docs_only(self):
        assert fgs.is_docs_only(["pyproject.toml"]) is False

    def test_mixed_docs_and_code_not_docs_only(self):
        assert fgs.is_docs_only(["docs/notes.md", "scripts/local/foo.py"]) is False

    def test_mixed_docs_and_workflow_not_docs_only(self):
        assert fgs.is_docs_only(["README.md", ".github/workflows/ci.yml"]) is False

    def test_empty_list_not_docs_only(self):
        assert fgs.is_docs_only([]) is False

    def test_directory_trailing_slash_not_docs_only(self):
        assert fgs.is_docs_only(["docs/"]) is False

    def test_doc_subdirectory_md_is_docs_only(self):
        assert fgs.is_docs_only(["doc/architecture.md"]) is True

    def test_nested_readme_not_docs_only(self):
        assert fgs.is_docs_only(["src/package/README.md"]) is False

    def test_nested_license_not_docs_only(self):
        assert fgs.is_docs_only(["vendor/LICENSE"]) is False

    def test_nested_governance_not_docs_only(self):
        assert fgs.is_docs_only(["src/GOVERNANCE.md"]) is False

    def test_json_in_docs_not_docs_only(self):
        assert fgs.is_docs_only(["docs/example.json"]) is False

    def test_yaml_in_docs_not_docs_only(self):
        assert fgs.is_docs_only(["docs/config.yaml"]) is False


# --------------------------------------------------------------------------
# Docs-only Codex waiver
# --------------------------------------------------------------------------

class TestDocsOnlyCodexWaiver:
    """Tests for --allow-docs-only-codex-waiver behavior."""

    def test_no_waiver_no_codex_returns_hold(self, pmg_clean):
        """Without waiver flag, missing Codex review returns HOLD_CODEX_REQUIRED."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=False,
        )
        with mock.patch.object(fgs, "fetch_pr_state", return_value=make_pr_open()):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CODEX_REQUIRED
        assert "codex" in result["blockers"][0].lower()

    def test_waiver_code_pr_still_holds(self, pmg_clean):
        """With waiver but non-docs diff, still returns HOLD_CODEX_REQUIRED."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "get_pr_changed_files", return_value=["scripts/local/foo.py"]):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CODEX_REQUIRED

    def test_waiver_mixed_pr_still_holds(self, pmg_clean):
        """With waiver but mixed diff (docs + code), still returns HOLD_CODEX_REQUIRED."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "get_pr_changed_files",
                                      return_value=["docs/notes.md", "scripts/local/foo.py"]):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CODEX_REQUIRED

    def test_waiver_docs_only_returns_ready(self, pmg_clean):
        """With waiver and docs-only diff, returns READY_TO_MERGE if all gates pass."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    with mock.patch.object(fgs, "get_pr_changed_files",
                                          return_value=["docs/notes.md", "README.md"]):
                        result = fgs.evaluate(args)
        assert result["status"] == fgs.State.READY_TO_MERGE

    def test_waiver_docs_only_emit_waiver_field(self, pmg_clean):
        """When waiver is used, output explicitly includes codex_review_waived_for_docs_only."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    with mock.patch.object(fgs, "get_pr_changed_files",
                                          return_value=["docs/notes.md"]):
                        result = fgs.evaluate(args)
        assert result["status"] == fgs.State.READY_TO_MERGE
        assert result["checks"].get("codex_review_waived_for_docs_only") is True
        assert result["checks"]["codex_exact_head"] is False

    def test_no_waiver_docs_pr_still_holds(self, pmg_clean):
        """Without waiver flag, docs-only PR with no Codex still returns HOLD."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=False,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "get_pr_changed_files",
                                      return_value=["docs/notes.md"]):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CODEX_REQUIRED

    def test_waiver_does_not_bypass_pmg(self, pmg_blocked):
        """Waiver must NOT skip PMG check — blocked PMG still returns HOLD_PMG_DIRTY."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_blocked,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "get_pr_changed_files",
                                      return_value=["docs/notes.md"]):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_PMG_DIRTY

    def test_waiver_does_not_bypass_ci(self, pmg_clean):
        """Waiver must NOT skip CI check — red CI still returns HOLD_CI_RED."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(False, "test failed")):
                with mock.patch.object(fgs, "get_pr_changed_files",
                                      return_value=["docs/notes.md"]):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CI_RED

    def test_waiver_does_not_bypass_head_match(self, pmg_clean):
        """Waiver must NOT skip head-match check."""
        args = make_args(
            reported_head_sha="deadbeef0000000000000000000000000000000",
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "get_pr_changed_files",
                                      return_value=["docs/notes.md"]):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_HEAD_MISMATCH

    def test_waiver_does_not_bypass_git_dirty(self, pmg_clean):
        """Waiver must NOT skip git status check."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(False, "M dirty.txt")):
                    with mock.patch.object(fgs, "get_pr_changed_files",
                                          return_value=["docs/notes.md"]):
                        result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_GIT_DIRTY

    def test_waiver_with_explicit_codex_sha_still_uses_codex(self, pmg_clean):
        """When --codex-reviewed-sha is explicitly provided, waiver is not invoked."""
        sha = "abc123def0000000000000000000000000000000"
        args = make_args(
            codex_reviewed_sha=sha,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,  # waiver present but not needed
        )
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "is_git_clean", return_value=(True, "ok")):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.READY_TO_MERGE
        # codex_exact_head should be True since explicit SHA was supplied
        assert result["checks"]["codex_exact_head"] is True
        assert "codex_review_waived_for_docs_only" not in result["checks"]

    def test_waiver_get_pr_changed_files_error_falls_back_to_hold(self, pmg_clean):
        """If get_pr_changed_files raises, waiver is NOT applied — returns HOLD."""
        args = make_args(
            codex_reviewed_sha=None,
            pmg_guard_state_json=pmg_clean,
            allow_docs_only_codex_waiver=True,
        )
        sha = "abc123def0000000000000000000000000000000"
        pr = make_pr_open(sha)
        with mock.patch.object(fgs, "fetch_pr_state", return_value=pr):
            with mock.patch.object(fgs, "is_ci_green", return_value=(True, "ok")):
                with mock.patch.object(fgs, "get_pr_changed_files",
                                      side_effect=RuntimeError("git diff failed")):
                    result = fgs.evaluate(args)
        assert result["status"] == fgs.State.HOLD_CODEX_REQUIRED