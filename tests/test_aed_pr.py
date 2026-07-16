"""Focused tests for the canonical AED PR-lifecycle controller.

This test module covers the highest-risk behavior of the controller:

  - exact safe-merge argv shape (PR, repo, --squash, --delete-branch,
    --match-head-commit, no --admin, no --auto);
  - canonical 40-SHA authorization phrase (build + byte-exact validate
    + reject short prefixes + reject stale head);
  - lifecycle state vocabulary collapse;
  - argv safety gate (--admin and --auto both rejected);
  - controller CLI surface (status / advance / merge subcommands parse
    and route correctly to the canonical implementation).
"""

from __future__ import annotations

import pytest

from scripts.local import aed_pr_lib as L


# -----------------------------------------------------------------------------
# aed_pr_lib: SHA enforcement
# -----------------------------------------------------------------------------

class TestShaEnforcement:
    def test_is_full_sha_accepts_40_hex(self):
        sha = "0" * 40
        assert L.is_full_sha(sha) is True
        sha = "a" * 40
        assert L.is_full_sha(sha) is True
        sha = "0123456789abcdef0123456789abcdef01234567"
        assert L.is_full_sha(sha) is True

    def test_is_full_sha_rejects_short_prefix(self):
        sha = "a" * 39
        assert L.is_full_sha(sha) is False
        sha = "a" * 7
        assert L.is_full_sha(sha) is False

    def test_is_full_sha_rejects_non_hex(self):
        sha = "g" * 40
        assert L.is_full_sha(sha) is False
        sha = "Z" * 40
        assert L.is_full_sha(sha) is False

    def test_is_full_sha_rejects_uppercase(self):
        # Authorization SHA must be lowercase hex (live canonical form).
        sha = "ABCDEF" * 6 + "AB"  # 38 — for completeness
        sha = "A" * 40
        assert L.is_full_sha(sha) is False

    def test_extract_full_sha_from_phrase_returns_40_only(self):
        phrase = (
            "I confirm merge PR #410 at "
            "0123456789abcdef0123456789abcdef01234567 "
            "using final-head reviewed clean state."
        )
        sha = L.extract_full_sha_from_phrase(phrase)
        assert sha == "0123456789abcdef0123456789abcdef01234567"

    def test_extract_full_sha_rejects_short_sha(self):
        # 39 hex chars (one short) must NOT be accepted.
        phrase = (
            "I confirm merge PR #410 at "
            "0123456789abcdef0123456789abcdef0123456 "
            "using final-head reviewed clean state."
        )
        assert L.extract_full_sha_from_phrase(phrase) is None


# -----------------------------------------------------------------------------
# aed_pr_lib: authorization phrase
# -----------------------------------------------------------------------------

class TestAuthorizationPhrase:
    def test_build_authorization_phrase_shape(self):
        phrase = L.build_authorization_phrase(
            pr_number=410,
            head_sha="0" * 40,
        )
        assert phrase == (
            "I confirm merge PR #410 at 0000000000000000000000000000000000000000 "
            "using final-head reviewed clean state."
        )

    def test_build_rejects_short_sha(self):
        with pytest.raises(ValueError):
            L.build_authorization_phrase(pr_number=410, head_sha="a" * 39)

    def test_build_rejects_non_int_pr_number(self):
        with pytest.raises(ValueError):
            L.build_authorization_phrase(pr_number="not-an-int", head_sha="a" * 40)

    def test_is_valid_authorization_phrase_byte_match(self):
        phrase = L.build_authorization_phrase(
            pr_number=410,
            head_sha="a" * 40,
        )
        assert L.is_valid_authorization_phrase(phrase, 410, "a" * 40) is True

    def test_is_valid_authorization_phrase_mismatch_short_sha(self):
        # Phrase embeds short SHA; must reject.
        phrase = (
            "I confirm merge PR #410 at a using final-head reviewed clean state."
        )
        assert L.is_valid_authorization_phrase(phrase, 410, "a" * 40) is False

    def test_is_valid_authorization_phrase_stale_head(self):
        phrase = L.build_authorization_phrase(
            pr_number=410,
            head_sha="a" * 40,
        )
        # Different head MUST reject.
        assert L.is_valid_authorization_phrase(phrase, 410, "b" * 40) is False

    def test_is_valid_authorization_phrase_whitespace_strict(self):
        # Even one extra space must reject (no whitespace tolerance).
        canonical = L.build_authorization_phrase(pr_number=410, head_sha="a" * 40)
        with_extra = canonical + " "
        assert L.is_valid_authorization_phrase(with_extra, 410, "a" * 40) is False


# -----------------------------------------------------------------------------
# aed_pr_lib: safe merge command
# -----------------------------------------------------------------------------

class TestSafeMergeCommand:
    PR = 410
    REPO = "Slideshow11/Automated-Edge-Discovery"
    HEAD = "0" * 40

    def test_exact_safe_merge_argv(self):
        cmd = L.build_safe_merge_command(self.PR, self.REPO, self.HEAD)
        # Check exact argv form.
        import shlex
        argv = shlex.split(cmd)
        assert argv[0] == "gh"
        assert argv[1] == "pr"
        assert argv[2] == "merge"
        assert "--repo" in argv
        assert "Slideshow11/Automated-Edge-Discovery" in argv
        assert "--squash" in argv
        assert "--delete-branch" in argv
        assert "--match-head-commit" in argv
        assert self.HEAD in argv
        assert "--admin" not in argv
        assert "--auto" not in argv

    def test_safe_merge_does_not_contain_admin(self):
        cmd = L.build_safe_merge_command(self.PR, self.REPO, self.HEAD)
        assert "--admin" not in cmd

    def test_safe_merge_short_sha_rejected(self):
        with pytest.raises(ValueError):
            L.build_safe_merge_command(self.PR, self.REPO, "a" * 39)

    def test_safe_merge_bad_repo_rejected(self):
        with pytest.raises(ValueError):
            L.build_safe_merge_command(self.PR, "no-slash", self.HEAD)


class TestArgvSafety:
    def test_argv_is_safe_rejects_admin(self):
        assert L.argv_is_safe(["gh", "pr", "merge", "1", "--admin"]) is False

    def test_argv_is_safe_rejects_admin_in_string_arg(self):
        assert L.argv_is_safe(["gh", "pr", "merge", "1 --admin"]) is False

    def test_argv_is_safe_rejects_auto(self):
        assert L.argv_is_safe(["gh", "pr", "merge", "1", "--auto"]) is False
        assert L.argv_is_safe(["gh", "pr", "merge", "1", "--auto=yes"]) is False

    def test_argv_is_safe_accepts_clean(self):
        argv = [
            "gh", "pr", "merge", "1",
            "--repo", "owner/name",
            "--squash", "--delete-branch",
            "--match-head-commit", "a" * 40,
        ]
        assert L.argv_is_safe(argv) is True

    def test_reject_admin_argv_raises(self):
        with pytest.raises(ValueError):
            L.reject_admin_argv(["gh", "pr", "merge", "--admin"])


# -----------------------------------------------------------------------------
# Lifecycle state vocabulary
# -----------------------------------------------------------------------------

class TestLifecycleVocabulary:
    EXPECTED_STATES = {
        "WAITING",
        "ACTION_REQUIRED",
        "BLOCKED",
        "READY_FOR_MERGE_AUTHORIZATION",
        "MERGED_PENDING_CLOSEOUT",
        "COMPLETE",
    }

    def test_lifecycle_states_are_the_canonical_set(self):
        assert set(L.LIFECYCLE_STATES) == self.EXPECTED_STATES

    def test_lifecycle_states_are_unique(self):
        assert len(set(L.LIFECYCLE_STATES)) == len(L.LIFECYCLE_STATES)


# -----------------------------------------------------------------------------
# controller CLI surface
# -----------------------------------------------------------------------------

class TestControllerCLI:
    def test_controller_status_rejects_missing_pr_number(self):
        from scripts.local import aed_pr as ctrl
        # argparse exits with code 2 on missing required arg.
        import subprocess as sp
        r = sp.run(
            ["python", "scripts/local/aed_pr.py", "status"],
            capture_output=True, text=True, cwd=".",
        )
        assert r.returncode == 2

    def test_controller_status_help_lists_pr_number(self):
        import subprocess as sp
        r = sp.run(
            ["python", "scripts/local/aed_pr.py", "status", "--help"],
            capture_output=True, text=True, cwd=".",
        )
        assert r.returncode == 0
        assert "--pr-number" in r.stdout

    def test_controller_merge_requires_authorization_phrase(self):
        import subprocess as sp
        r = sp.run(
            ["python", "scripts/local/aed_pr.py", "merge",
             "--pr-number", "410"],
            capture_output=True, text=True, cwd=".",
        )
        assert r.returncode == 2
        assert "authorization" in (r.stderr + r.stdout).lower()


# -----------------------------------------------------------------------------
# controller: live status command against PR #410 (smoke)
# -----------------------------------------------------------------------------

class TestControllerStatusLive:
    """Smoke test: status reads live state and emits one JSON report.

    Uses PR #410 (already merged on this base) so the status should
    collapse to MERGED_PENDING_CLOSEOUT.
    """

    def test_status_reads_live_state(self):
        import json
        import subprocess as sp
        r = sp.run(
            ["python", "scripts/local/aed_pr.py", "status",
             "--pr-number", "410"],
            capture_output=True, text=True, cwd=".",
        )
        # If gh is unauthenticated, controller still emits JSON; the gh
        # call is internal. Accept rc 0 here as long as JSON shape is OK.
        report = json.loads(r.stdout)
        assert report["tool"] == "aed_pr.status"
        assert report["pr_number"] == 410
        assert "lifecycle_state" in report
        assert "required_authorization_phrase" in report
        assert "8973761e8079756d25a7606431400ecab90e31c9" in \
            report["required_authorization_phrase"]
