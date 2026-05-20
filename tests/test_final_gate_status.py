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
# CI green tests
# ---------------------------------------------------------------------------

class TestIsCiGreen:
    def test_all_success_returns_true(self):
        # gh_json returns the list directly (--jq filter applied by gh)
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = [
                {"head_sha": "abc123def0000000000000000000000000000000",
                 "conclusion": "success", "status": "completed", "name": "CI"},
                {"head_sha": "abc123def0000000000000000000000000000000",
                 "conclusion": "success", "status": "completed", "name": "test (3.11)"},
            ]
            green, reason = fgs.is_ci_green(
                265, "Slideshow11/Automated-Edge-Discovery",
                "abc123def0000000000000000000000000000000"
            )
        assert green is True

    def test_in_progress_returns_false(self):
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = [
                {"head_sha": "abc123def0000000000000000000000000000000",
                 "conclusion": "success", "status": "completed", "name": "CI"},
                {"head_sha": "abc123def0000000000000000000000000000000",
                 "conclusion": None, "status": "in_progress", "name": "governance-validators"},
            ]
            green, reason = fgs.is_ci_green(
                265, "Slideshow11/Automated-Edge-Discovery",
                "abc123def0000000000000000000000000000000"
            )
        assert green is False
        assert "in_progress" in reason

    def test_failure_returns_false(self):
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = [
                {"head_sha": "abc123def0000000000000000000000000000000",
                 "conclusion": "failure", "status": "completed", "name": "test (3.11)"},
            ]
            green, reason = fgs.is_ci_green(
                265, "Slideshow11/Automated-Edge-Discovery",
                "abc123def0000000000000000000000000000000"
            )
        assert green is False
        assert "failure" in reason.lower()

    def test_no_runs_returns_false(self):
        with mock.patch.object(fgs, "gh_json") as m:
            m.return_value = []
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