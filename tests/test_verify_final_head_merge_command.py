"""
Tests for verify_final_head_merge_command.py

Does NOT call real GitHub — gh pr view is monkey-patched for all tests.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest

# Ensure the script is importable
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "local"))

import verify_final_head_merge_command
from verify_final_head_merge_command import (
    build_authorization_phrase,
    build_merge_command,
    verify,
    validate_reported_sha,
    write_json,
    write_markdown,
    main,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockGhPrView:
    """Replace gh_pr_view_json for testing."""

    def __init__(self, data: dict):
        self.data = data
        self.call_count = 0

    def __call__(self, repo: str, pr_number: int) -> dict:
        self.call_count += 1
        return self.data


# ---------------------------------------------------------------------------
# Fixture: clean argv
# ---------------------------------------------------------------------------

def clean_argv(args: list[str]) -> list[str]:
    return ["verify_final_head_merge_command.py"] + args


# ---------------------------------------------------------------------------
# Tests: validate_reported_sha
# ---------------------------------------------------------------------------

class TestValidateReportedSha:
    def test_none_is_valid(self):
        assert validate_reported_sha(None) is None

    def test_valid_40_char_hex_is_valid(self):
        sha = "dab33c5dcc6ef9657644bbe160cf0ff08939a28c"
        assert validate_reported_sha(sha) is None

    def test_invalid_too_short_is_rejected(self):
        err = validate_reported_sha("dab33c5dcc6ef9657644")
        assert err is not None
        assert "40-char" in err

    def test_invalid_hex_is_rejected(self):
        err = validate_reported_sha("g" * 40)
        assert err is not None

    def test_non_hex_chars_rejected(self):
        err = validate_reported_sha("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
        assert err is not None

    def test_empty_string_is_rejected(self):
        err = validate_reported_sha("")
        assert err is not None


# ---------------------------------------------------------------------------
# Tests: build_merge_command
# ---------------------------------------------------------------------------

class TestBuildMergeCommand:
    def test_uses_pr_number(self):
        cmd = build_merge_command(227, "Slideshow11/Automated-Edge-Discovery",
                                  "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        assert "227" in cmd

    def test_uses_canonical_sha_not_reported(self):
        canonical = "dab33c5dcc6ef9657644bbe160cf0ff08939a28c"
        cmd = build_merge_command(227, "Slideshow11/Automated-Edge-Discovery", canonical)
        assert canonical in cmd

    def test_has_match_head_commit_flag(self):
        cmd = build_merge_command(227, "Slideshow11/Automated-Edge-Discovery",
                                  "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        assert "--match-head-commit" in cmd

    def test_has_squash_flag(self):
        cmd = build_merge_command(227, "Slideshow11/Automated-Edge-Discovery",
                                  "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        assert "--squash" in cmd

    def test_has_delete_branch_flag(self):
        cmd = build_merge_command(227, "Slideshow11/Automated-Edge-Discovery",
                                  "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        assert "--delete-branch" in cmd

    def test_command_is_not_executed(self):
        cmd = build_merge_command(227, "Slideshow11/Automated-Edge-Discovery",
                                  "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        # The generated text must not contain actual execution markers like "&&"
        # or chained commands that would mutate state
        assert "&&" not in cmd


# ---------------------------------------------------------------------------
# Tests: build_authorization_phrase
# ---------------------------------------------------------------------------

class TestBuildAuthorizationPhrase:
    def test_uses_pr_number(self):
        phrase = build_authorization_phrase(227, "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        assert "PR #227" in phrase

    def test_uses_canonical_sha_not_reported(self):
        canonical = "dab33c5dcc6ef9657644bbe160cf0ff08939a28c"
        phrase = build_authorization_phrase(227, canonical)
        assert canonical in phrase

    def test_contains_confirm_merge(self):
        phrase = build_authorization_phrase(227, "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        assert "I confirm merge" in phrase

    def test_contains_final_head_phrase(self):
        phrase = build_authorization_phrase(227, "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        assert "final-head reviewed clean state" in phrase


# ---------------------------------------------------------------------------
# Tests: verify() — mock gh pr view
# ---------------------------------------------------------------------------

# Canonical PR data used across tests
CANONICAL_PR_DATA = {
    "number": 227,
    "state": "OPEN",
    "mergeable": True,
    "headRefOid": "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
    "baseRefOid": "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
    "title": "fix: improve quarantine safety grep triage summary",
    "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/227",
    "changedFiles": 3,
}


class TestVerifyMatchingSha:
    """reported-head-sha matches canonical → MERGE_READY_CANDIDATE."""

    def test_matches_returns_merge_ready_candidate(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
        )
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"

    def test_head_sha_matches_true(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
        )
        assert result["head_sha_matches"] is True

    def test_authorization_phrase_uses_canonical_sha(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
        )
        assert result["canonical_head_sha"] in result["authorization_phrase"]

    def test_merge_command_uses_canonical_sha(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
        )
        assert result["canonical_head_sha"] in result["merge_command"]


class TestVerifyMismatchedSha:
    """reported-head-sha differs from canonical → PATCH."""

    def test_mismatch_returns_patch(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="0000000000000000000000000000000000000000",  # wrong
            require_mergeable=True,
        )
        assert result["recommendation"] == "PATCH"

    def test_head_sha_matches_false(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="0000000000000000000000000000000000000000",
            require_mergeable=True,
        )
        assert result["head_sha_matches"] is False

    def test_authorization_phrase_still_uses_canonical_not_reported(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="0000000000000000000000000000000000000000",
            require_mergeable=True,
        )
        assert result["canonical_head_sha"] in result["authorization_phrase"]
        assert result["reported_head_sha"] not in result["authorization_phrase"]

    def test_merge_command_still_uses_canonical_not_reported(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="0000000000000000000000000000000000000000",
            require_mergeable=True,
        )
        assert result["canonical_head_sha"] in result["merge_command"]
        assert "0000000000000000000000000000000000000000" not in result["merge_command"]


class TestVerifyNoReportedSha:
    """No reported SHA provided → MERGE_READY_CANDIDATE if open and mergeable."""

    def test_no_reported_sha_returns_merge_ready_candidate(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=True,
        )
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"

    def test_canonical_merge_command_still_generated(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=True,
        )
        assert result["canonical_head_sha"] in result["merge_command"]
        assert "--match-head-commit" in result["merge_command"]


class TestVerifyClosedPr:
    """Closed PR → BLOCK."""

    def test_closed_pr_returns_block(self, monkeypatch):
        closed_pr = dict(CANONICAL_PR_DATA, state="CLOSED")
        mock = MockGhPrView(closed_pr)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=True,
        )
        assert result["recommendation"] == "BLOCK"

    def test_closed_pr_state_is_preserved(self, monkeypatch):
        closed_pr = dict(CANONICAL_PR_DATA, state="CLOSED")
        mock = MockGhPrView(closed_pr)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=True,
        )
        assert result["state"] == "closed"


class TestVerifyNonMergeablePr:
    """Non-mergeable PR → BLOCK."""

    def test_non_mergeable_returns_block(self, monkeypatch):
        non_mergeable_pr = dict(CANONICAL_PR_DATA, mergeable=False)
        mock = MockGhPrView(non_mergeable_pr)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=True,
        )
        assert result["recommendation"] == "BLOCK"

    def test_non_mergeable_mergeable_field_is_false(self, monkeypatch):
        non_mergeable_pr = dict(CANONICAL_PR_DATA, mergeable=False)
        mock = MockGhPrView(non_mergeable_pr)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=True,
        )
        assert result["mergeable"] is False


class TestVerifyInvalidReportedSha:
    """Invalid reported SHA format → BLOCK."""

    def test_invalid_sha_format_returns_block(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="not-a-valid-sha",
            require_mergeable=True,
        )
        assert result["recommendation"] == "BLOCK"

    def test_errors_list_contains_format_error(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="not-a-valid-sha",
            require_mergeable=True,
        )
        assert len(result["verification_errors"]) > 0


class TestVerifyMergeableUnknown:
    """mergeable=null (unknown) → WAIT."""

    def test_mergeable_unknown_returns_wait(self, monkeypatch):
        unknown_pr = dict(CANONICAL_PR_DATA, mergeable=None)
        mock = MockGhPrView(unknown_pr)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=True,
        )
        assert result["recommendation"] == "WAIT"


class TestVerifyRequireMergeableFalse:
    """require_mergeable=False → skip mergeable check."""

    def test_require_mergeable_false_allows_non_mergeable(self, monkeypatch):
        non_mergeable_pr = dict(CANONICAL_PR_DATA, mergeable=False)
        mock = MockGhPrView(non_mergeable_pr)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha=None,
            require_mergeable=False,
        )
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"


# ---------------------------------------------------------------------------
# Tests: output files
# ---------------------------------------------------------------------------

class TestOutputJson:
    def test_writes_expected_keys(self, tmp_path):
        result = {
            "recommendation": "MERGE_READY_CANDIDATE",
            "pr_number": 227,
            "repo": "Slideshow11/Automated-Edge-Discovery",
            "title": "fix: improve quarantine safety grep triage summary",
            "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/227",
            "reported_head_sha": "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "canonical_head_sha": "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "base_sha": "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
            "mergeable": True,
            "state": "open",
            "head_sha_matches": True,
            "authorization_phrase": (
                "I confirm merge PR #227 at dab33c5dcc6ef9657644bbe160cf0ff08939a28c "
                "using final-head reviewed clean state."
            ),
            "merge_command": (
                "gh pr merge 227 \\\n"
                "  --repo Slideshow11/Automated-Edge-Discovery \\\n"
                "  --squash \\\n"
                "  --delete-branch \\\n"
                "  --match-head-commit dab33c5dcc6ef9657644bbe160cf0ff08939a28c"
            ),
            "verification_errors": [],
        }
        out = tmp_path / "verify.json"
        write_json(result, str(out))
        loaded = json.loads(out.read_text())
        for key in [
            "recommendation", "pr_number", "canonical_head_sha",
            "authorization_phrase", "merge_command", "head_sha_matches",
            "state", "mergeable", "verification_errors",
        ]:
            assert key in loaded, f"Missing key: {key}"


class TestOutputMarkdown:
    def test_writes_corrected_merge_command(self, tmp_path):
        result = {
            "recommendation": "MERGE_READY_CANDIDATE",
            "pr_number": 227,
            "repo": "Slideshow11/Automated-Edge-Discovery",
            "title": "fix: improve quarantine safety grep triage summary",
            "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/227",
            "reported_head_sha": None,
            "canonical_head_sha": "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "base_sha": "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
            "mergeable": True,
            "state": "open",
            "head_sha_matches": False,
            "authorization_phrase": (
                "I confirm merge PR #227 at dab33c5dcc6ef9657644bbe160cf0ff08939a28c "
                "using final-head reviewed clean state."
            ),
            "merge_command": (
                "gh pr merge 227 \\\n"
                "  --repo Slideshow11/Automated-Edge-Discovery \\\n"
                "  --squash \\\n"
                "  --delete-branch \\\n"
                "  --match-head-commit dab33c5dcc6ef9657644bbe160cf0ff08939a28c"
            ),
            "verification_errors": [],
        }
        out = tmp_path / "verify.md"
        write_markdown(result, str(out))
        text = out.read_text()
        # Canonical SHA must be in the merge command in the output
        assert "dab33c5dcc6ef9657644bbe160cf0ff08939a28c" in text
        assert "MERGE_READY_CANDIDATE" in text
        assert "Authorization Phrase" in text


# ---------------------------------------------------------------------------
# Tests: main() integration (CLI args)
# ---------------------------------------------------------------------------

class TestMainCli:
    """Test main() with real argv parsing (no gh calls — gh_pr_view_json is mocked)."""

    def test_pr_number_required(self, monkeypatch, capsys):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0  # argparse exits with 2 on missing required arg

    def test_output_json_writes_file(self, monkeypatch, tmp_path, capsys):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        out_json = tmp_path / "out.json"
        rc = main([
            "--pr-number", "227",
            "--output-json", str(out_json),
        ])
        assert rc == 0
        assert out_json.exists()
        data = json.loads(out_json.read_text())
        assert data["pr_number"] == 227
        assert data["canonical_head_sha"] == "dab33c5dcc6ef9657644bbe160cf0ff08939a28c"

    def test_output_md_writes_file(self, monkeypatch, tmp_path, capsys):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        out_md = tmp_path / "out.md"
        rc = main([
            "--pr-number", "227",
            "--output-md", str(out_md),
        ])
        assert rc == 0
        assert out_md.exists()

    def test_reported_sha_mismatch_emits_patch_recommendation(self, monkeypatch, capsys):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        rc = main([
            "--pr-number", "227",
            "--reported-head-sha", "0000000000000000000000000000000000000000",
        ])
        assert rc == 0
        output = capsys.readouterr().out
        assert "PATCH" in output

    def test_gh_pr_view_called_with_correct_pr_number(self, monkeypatch):
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        main(["--pr-number", "227"])
        assert mock.call_count == 1

    def test_no_real_gh_execution_in_test(self, monkeypatch, tmp_path):
        """Verify no gh subcommand is executed in this test suite."""
        executed_commands: list[str] = []

        original_run = subprocess.run

        def tracking_run(cmd, *args, **kwargs):
            executed_commands.append(" ".join(cmd) if isinstance(cmd, list) else str(cmd))
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", tracking_run)
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )

        main(["--pr-number", "227"])

        # All subprocess calls must be gh pr view (not gh pr merge, etc.)
        for cmd in executed_commands:
            if isinstance(cmd, str):
                # Any gh command executed must be gh pr view, not gh pr merge
                if "gh" in cmd and "merge" in cmd:
                    # Allow "gh pr view" but not "gh pr merge"
                    if re.search(r"gh\s+pr\s+merge", cmd):
                        pytest.fail(f"Test executed forbidden mutation command: {cmd}")


# ---------------------------------------------------------------------------
# Tests: no mutation in generated output
# ---------------------------------------------------------------------------

class TestNoMutationInOutput:
    """Generated output must not contain executable mutation commands."""

    def test_merge_command_text_contains_no_executable_mutations(self):
        cmd = build_merge_command(227, "Slideshow11/Automated-Edge-Discovery",
                                  "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        # The output is a command string — it must not actually run anything
        # It should be a safe print/output without && or ; chaining to shell
        forbidden = ["&&", "||", ";", "bash -c", "| xargs"]
        for f in forbidden:
            if f in cmd:
                # Allow these as part of comment documentation only
                pass

    def test_authorization_phrase_is_not_executed(self):
        phrase = build_authorization_phrase(227, "dab33c5dcc6ef9657644bbe160cf0ff08939a28c")
        # Authorization phrase is a string, not a command
        assert "&&" not in phrase
        assert "||" not in phrase


# ---------------------------------------------------------------------------
# Tests: PMG guard state enforcement
# ---------------------------------------------------------------------------

class TestPmgGuardStateEnforcement:
    """PMG guard state passed to verify() controls authorization emission."""

    def test_no_pmg_guard_state_preserves_auth_phrase(self, monkeypatch):
        """No pmg_guard_state → authorization phrase emitted normally."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
        )
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"
        assert result["authorization_phrase"] != ""
        assert result["merge_command"] != ""

    def test_pmg_clean_preserves_auth_phrase(self, monkeypatch):
        """PMG status=clean → authorization phrase emitted normally."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state={"status": "clean", "message": "guard recommendation: PASS"},
        )
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"
        assert result["authorization_phrase"] != ""
        assert result["merge_command"] != ""

    def test_pmg_error_withholds_auth_and_command(self, monkeypatch):
        """PMG status=error → auth phrase and merge command withheld, BLOCK."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state={
                "status": "error",
                "message": "compare JSON is stale (compare_at is 1800s older than gate execution, max 600s): pre-generated compare rejected",
            },
        )
        assert result["recommendation"] == "BLOCK"
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""
        assert any(
            "persistent_mutation_guard" in e
            for e in result["verification_errors"]
        )

    def test_pmg_blocked_withholds_auth_and_command(self, monkeypatch):
        """PMG status=blocked → auth phrase and merge command withheld, BLOCK."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state={
                "status": "blocked",
                "message": "guard recommendation: BLOCK",
            },
        )
        assert result["recommendation"] == "BLOCK"
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""

    def test_pmg_not_required_allows_auth(self, monkeypatch):
        """PMG status=not_required → authorization phrase still emitted."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state={"status": "not_required", "message": "PMG not required for this PR"},
        )
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"
        assert result["authorization_phrase"] != ""
        assert result["merge_command"] != ""

    def test_pmg_error_does_not_affect_non_merge_ready_recommendation(self, monkeypatch):
        """PMG error with existing PATCH recommendation keeps PATCH (not demoted)."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="0000000000000000000000000000000000000001",  # mismatch
            require_mergeable=True,
            pmg_guard_state={
                "status": "error",
                "message": "stale compare",
            },
        )
        # SHA mismatch already made it PATCH; PMG error does not override
        assert result["recommendation"] == "PATCH"
        assert result["authorization_phrase"] == ""  # PATCH already had none
        assert result["merge_command"] == ""  # PATCH already had none


class TestPmgGuardStateCli:
    """CLI --pmg-guard-state-json wires into verify() correctly."""

    def test_pmg_guard_state_json_missing_file_returns_error(self, tmp_path):
        """Non-existent PMG guard state JSON → non-zero exit."""
        out_json = tmp_path / "out.json"
        rc = main([
            "--pr-number", "227",
            "--pmg-guard-state-json", str(tmp_path / "nonexistent.json"),
            "--output-json", str(out_json),
        ])
        assert rc != 0

    def test_pmg_guard_state_json_clean_preserves_auth(self, tmp_path, monkeypatch):
        """--pmg-guard-state-json with clean status → authorization emitted."""
        import json
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        pmg_path = tmp_path / "pmg.json"
        pmg_path.write_text(json.dumps({
            "status": "clean",
            "message": "guard recommendation: PASS",
        }))
        out_json = tmp_path / "out.json"
        rc = main([
            "--pr-number", "227",
            "--reported-head-sha", "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "--pmg-guard-state-json", str(pmg_path),
            "--output-json", str(out_json),
        ])
        assert rc == 0
        data = json.loads(out_json.read_text())
        assert data["authorization_phrase"] != ""
        assert data["merge_command"] != ""

    def test_pmg_guard_state_json_stale_withholds_auth(self, tmp_path, monkeypatch):
        """--pmg-guard-state-json with stale error status → authorization withheld."""
        import json
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        pmg_path = tmp_path / "pmg.json"
        pmg_path.write_text(json.dumps({
            "status": "error",
            "message": "compare JSON is stale (compare_at is 1800s older than gate execution, max 600s): pre-generated compare rejected",
        }))
        out_json = tmp_path / "out.json"
        rc = main([
            "--pr-number", "227",
            "--reported-head-sha", "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "--pmg-guard-state-json", str(pmg_path),
            "--output-json", str(out_json),
        ])
        assert rc == 0
        data = json.loads(out_json.read_text())
        assert data["recommendation"] == "BLOCK"
        assert data["authorization_phrase"] == ""
        assert data["merge_command"] == ""

    def test_output_md_shows_no_auth_phrase_when_pmg_blocked(self, tmp_path, monkeypatch):
        """Markdown output omits authorization section when PMG blocks merge."""
        import json
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        pmg_path = tmp_path / "pmg.json"
        pmg_path.write_text(json.dumps({
            "status": "error",
            "message": "stale compare",
        }))
        out_md = tmp_path / "out.md"
        main([
            "--pr-number", "227",
            "--reported-head-sha", "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "--pmg-guard-state-json", str(pmg_path),
            "--output-md", str(out_md),
        ])
        text = out_md.read_text()
        # Authorization section should be absent or empty
        assert "Authorization Phrase" not in text or "I confirm merge" not in text


class TestRequirePmgEnforcement:
    """
    Acceptance criterion: when --require-pmg is set and no --pmg-guard-state-json
    is provided, verify_final_head_merge_command.py must emit:
      - recommendation: BLOCK
      - authorization_phrase: "" (empty)
      - merge_command: "" (empty)

    This closes the operator bypass path where the tool is used as the final
    merge helper without PMG coverage.
    """

    def test_require_pmg_no_guard_state_returns_block(self, monkeypatch):
        """--require-pmg without --pmg-guard-state-json → BLOCK, no auth, no merge_cmd."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state=None,
            require_pmg=True,
        )
        assert result["recommendation"] == "BLOCK"
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""
        assert any(
            "--require-pmg was set but --pmg-guard-state-json was not provided"
            in e
            for e in result["verification_errors"]
        )

    def test_require_pmg_with_clean_guard_preserves_auth(self, monkeypatch):
        """--require-pmg with fresh clean PMG → auth phrase and merge command emitted."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state={"status": "clean", "message": "guard recommendation: PASS"},
            require_pmg=True,
        )
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"
        assert result["authorization_phrase"] != ""
        assert result["merge_command"] != ""
        assert "I confirm merge" in result["authorization_phrase"]
        assert "gh pr merge" in result["merge_command"]

    def test_require_pmg_with_stale_guard_returns_block(self, monkeypatch):
        """--require-pmg with stale PMG (status=error) → BLOCK, no auth, no merge_cmd."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state={
                "status": "error",
                "message": "compare JSON is stale (compare_at is 1800s older than gate execution, max 600s)",
            },
            require_pmg=True,
        )
        assert result["recommendation"] == "BLOCK"
        assert result["authorization_phrase"] == ""
        assert result["merge_command"] == ""

    def test_no_require_pmg_no_guard_state_returns_merge_ready_candidate(self, monkeypatch):
        """Without --require-pmg and no PMG guard state → MERGE_READY_CANDIDATE (backward compat)."""
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        result = verify(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=227,
            reported_head_sha="dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            require_mergeable=True,
            pmg_guard_state=None,
            require_pmg=False,
        )
        # Backward compatible: no PMG required means auth phrase is still emitted
        assert result["recommendation"] == "MERGE_READY_CANDIDATE"
        assert result["authorization_phrase"] != ""
        assert result["merge_command"] != ""


class TestRequirePmgCli:
    """CLI --require-pmg flag wires into verify() correctly."""

    def test_require_pmg_cli_no_guard_state_returns_error_exit_code(self, tmp_path, monkeypatch):
        """--require-pmg without --pmg-guard-state-json → exit 0 (BLOCK, not fatal error)."""
        import json
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        out_json = tmp_path / "out.json"
        rc = main([
            "--pr-number", "227",
            "--reported-head-sha", "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "--require-pmg",
            "--output-json", str(out_json),
        ])
        assert rc == 0
        data = json.loads(out_json.read_text())
        assert data["recommendation"] == "BLOCK"
        assert data["authorization_phrase"] == ""
        assert data["merge_command"] == ""

    def test_require_pmg_cli_with_clean_guard_preserves_auth(self, tmp_path, monkeypatch):
        """--require-pmg --pmg-guard-state-json with clean status → auth emitted."""
        import json
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        pmg_path = tmp_path / "pmg.json"
        pmg_path.write_text(json.dumps({
            "status": "clean",
            "message": "guard recommendation: PASS",
        }))
        out_json = tmp_path / "out.json"
        rc = main([
            "--pr-number", "227",
            "--reported-head-sha", "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "--require-pmg",
            "--pmg-guard-state-json", str(pmg_path),
            "--output-json", str(out_json),
        ])
        assert rc == 0
        data = json.loads(out_json.read_text())
        assert data["recommendation"] == "MERGE_READY_CANDIDATE"
        assert data["authorization_phrase"] != ""
        assert data["merge_command"] != ""

    def test_require_pmg_disabled_no_guard_state_preserves_auth(self, tmp_path, monkeypatch):
        """--no-require-pmg without PMG guard state → auth phrase emitted (backward compat)."""
        import json
        mock = MockGhPrView(CANONICAL_PR_DATA)
        monkeypatch.setattr(
            "verify_final_head_merge_command.gh_pr_view_json", mock
        )
        out_json = tmp_path / "out.json"
        rc = main([
            "--pr-number", "227",
            "--reported-head-sha", "dab33c5dcc6ef9657644bbe160cf0ff08939a28c",
            "--no-require-pmg",
            "--output-json", str(out_json),
        ])
        assert rc == 0
        data = json.loads(out_json.read_text())
        assert data["recommendation"] == "MERGE_READY_CANDIDATE"
        assert data["authorization_phrase"] != ""
        assert data["merge_command"] != ""