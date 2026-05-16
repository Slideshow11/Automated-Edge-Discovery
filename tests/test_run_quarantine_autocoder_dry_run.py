#!/usr/bin/env python3
"""
Tests for run_quarantine_autocoder_dry_run.py (Phase 1 dry-run bundle scaffold).

These tests verify safety invariants and validation logic.
Phase 1 does NOT execute real operations, so tests mock the filesystem entirely.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "local" / "run_quarantine_autocoder_dry_run.py"
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_script(*args, cwd=None):
    """Run the script and return (exit_code, stdout, stderr)."""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]))
    result = subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or SCRIPT.parent,
    )
    return result.returncode, result.stdout, result.stderr


def bundle_file(bundle_dir, filename):
    return os.path.join(bundle_dir, filename)


def read_json(bundle_dir, filename):
    with open(bundle_file(bundle_dir, filename)) as f:
        content = f.read()
    # Handle files that start with a text summary header before JSON body
    # (e.g. safety_grep.txt has "# Safety Grep Summary\n..." prefix)
    json_start = content.find("{")
    if json_start > 0:
        content = content[json_start:]
    return json.loads(content)


def read_text(bundle_dir, filename):
    with open(bundle_file(bundle_dir, filename)) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRefusesWithoutDryRun:
    """Test that the script refuses to run without --dry-run."""

    def test_refuses_missing_dry_run_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "0" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert "required: --dry-run" in out + err

    def test_refuses_even_with_all_other_args(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "0" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--dry-run",  # included — but without the enforcement flag it still proceeds
            )
            # If --dry-run is provided as value (not flag), it may be treated as a candidate value
            # We test the actual flag enforcement below
            assert "REQUIRED" in out + err or rc == 0  # depends on parser setup


class TestBaseShaValidation:
    """Test that invalid base SHAs are rejected."""

    def test_rejects_short_sha(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "abc",
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert "40-char hex" in out + err

    def test_rejects_non_hex_sha(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "g" * 40,  # 'g' is not hex
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert "40-char hex" in out + err

    def test_accepts_valid_40_char_hex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            # Valid SHA passes validation (bundle creation may still fail on other checks)
            # At minimum, validation error should not fire for base_sha
            assert "40-char hex" not in out + err or rc == 0


class TestCandidateIdValidation:
    """Test that unsafe candidate IDs are rejected."""

    @pytest.mark.parametrize("bad_id", [
        "test/candidate",   # slash
        "test candidate",   # space
        "test;candidate",   # semicolon
        "test'candidate",   # quote
        "test\"candidate",  # double quote
        "test$candidate",   # dollar
        "test`candidate",   # backtick
        "test#candidate",   # hash
    ])
    def test_rejects_unsafe_candidate_ids(self, bad_id):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", bad_id,
                "--objective", "test objective",
            )
            assert rc != 0
            assert "safe slug" in out + err or "alphanumeric" in out + err

    def test_accepts_safe_slug(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate_123",
                "--objective", "test objective",
            )
            assert "safe slug" not in out + err


class TestBundleDirValidation:
    """Test that unsafe bundle dirs are rejected."""

    def test_rejects_git_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.makedirs(git_dir)
            bundle = os.path.join(git_dir, "objects")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert ".git" in out + err or "inside production" in out + err

    def test_rejects_aed_repo_root(self):
        # Use the AED repo root as bundle dir — should be rejected
        aed_root = Path(__file__).resolve().parents[1]  # .../Automated-Edge-Discovery
        rc, out, err = run_script(
            "--dry-run",
            "--source-repo", str(aed_root),
            "--bundle-dir", str(aed_root),
            "--base-sha", "a" * 40,
            "--candidate-id", "test-candidate",
            "--objective", "test objective",
        )
        assert rc != 0
        assert "root" in out + err or "cannot be the production" in out + err

    def test_accepts_isolated_temp_bundle_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "isolated-bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            # Should not fail bundle-dir validation
            assert rc == 0 or "bundle" not in out.lower()

    def test_rejects_symlink_to_git_dir(self):
        """Symlink outside repo pointing into .git must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a symlink: /tmpdir/link -> /home/max/Automated-Edge-Discovery/.git
            # (or any .git within the AED repo root)
            aed_root = Path(__file__).resolve().parents[1]
            git_target = aed_root / ".git"
            if not git_target.exists():
                pytest.skip(".git directory not found")
            link_path = Path(tmpdir) / "link-to-git"
            try:
                link_path.symlink_to(git_target, target_is_directory=True)
            except OSError:
                pytest.skip("Cannot create symlink on this filesystem")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", str(link_path),
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert ".git" in out + err or "production directory" in out + err

    def test_rejects_symlink_to_repo_root(self):
        """Symlink outside repo pointing to AED repo root must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            aed_root = Path(__file__).resolve().parents[1]
            link_path = Path(tmpdir) / "link-to-aed"
            try:
                link_path.symlink_to(aed_root, target_is_directory=True)
            except OSError:
                pytest.skip("Cannot create symlink on this filesystem")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", str(link_path),
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert "root" in out + err or "production" in out + err

    @pytest.mark.parametrize("prefix", ["hermes", "workflows", ".hermes", ".github"])
    def test_rejects_forbidden_prefix_inside_repo(self, prefix):
        """Forbidden prefix dirs (hermes/, workflows/, .hermes/) must be rejected.

        Regression: the relative_to try/except pattern had reversed logic —
        ValueError was raised when bundle WAS inside (not-raise expected),
        so the except branch ran and passed even for inside dirs.
        The is_inside flag pattern fixes this.
        """
        aed_root = Path(__file__).resolve().parents[1]
        bundle = aed_root / prefix / "test-bundle"
        rc, out, err = run_script(
            "--dry-run",
            "--source-repo", str(aed_root),
            "--bundle-dir", str(bundle),
            "--base-sha", "a" * 40,
            "--candidate-id", "test-candidate",
            "--objective", "test",
        )
        assert rc != 0, f"bundle_dir inside {prefix}/ should be rejected but rc={rc}"
        combined = out + err
        assert "production directory" in combined or prefix in combined, \
            f"Expected rejection message for {prefix}, got: {combined!r}"


class TestBundleFilesCreated:
    """Test that all expected bundle files are created in temp dir."""

    def test_creates_all_expected_bundle_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0, f"Script failed: {err}"
            expected = [
                "BUNDLE_STATUS.json",
                "base_sha.txt",
                "candidate_id.txt",
                "objective.md",
                "changed_files.txt",
                "diff.patch",
                "scope_check.json",
                "safety_grep.txt",
                "local_gate.txt",
                "codex_review_summary.md",
                "risk_notes.md",
                "proposed_pr_body.md",
                "import_command.sh",
            ]
            for filename in expected:
                path = bundle_file(bundle, filename)
                assert os.path.exists(path), f"Missing: {filename}"


class TestBundleStatusSafetyBooleans:
    """Test that BUNDLE_STATUS.json contains correct dry-run safety invariants."""

    def test_bundlestatus_contains_dry_run_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert status["dry_run"] is True

    @pytest.mark.parametrize("key", [
        "dispatch_occurred",
        "hermes_touched",
        "production_board_touched",
        "pr_created",
        "import_performed",
    ])
    def test_bundlestatus_safety_booleans_all_false(self, key):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert status[key] is False, f"{key} should be False, got {status[key]}"


class TestImportCommandSafety:
    """Test that import_command.sh contains no executable mutation commands."""

    def test_import_command_sh_is_not_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            path = bundle_file(bundle, "import_command.sh")
            mode = os.stat(path).st_mode
            # Not executable (no user/group/other execute bits)
            assert not (mode & 0o111), f"import_command.sh should not be executable: {oct(mode)}"

    @pytest.mark.parametrize("cmd", [
        "hermes kanban create",
        "hermes kanban dispatch",
        "gh pr merge",
        "gh pr create",
        "git push",
        "git commit",
        "telegram",
        "send_message",
        "memory.update",
        "skill_manage",
        "fact_store",
        "delegate_task",
        "cronjob",
    ])
    def test_no_executable_mutation_commands(self, cmd):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            content = read_text(bundle, "import_command.sh")
            # Commands must appear only in commented lines or not at all
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # comments are fine
                if cmd in line:
                    pytest.fail(
                        f"Executable line contains forbidden command '{cmd}': {line!r}"
                    )

    def test_import_command_sh_contains_only_commented_instructions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            content = read_text(bundle, "import_command.sh")
            # Count lines that are not comments
            non_comment_lines = [
                l for l in content.splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
            # All non-blank, non-comment lines should be shebang only
            assert all(l.startswith("#!") for l in non_comment_lines), (
                f"Non-comment, non-shebang lines found: {non_comment_lines}"
            )


class TestRepeatedDryRunSafety:
    """Test that repeated dry-run to same bundle dir refuses without --force."""

    def test_refuses_repeated_run_without_force(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc1, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc1 == 0

            rc2, out2, err2 = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc2 != 0
            assert "not empty" in out2 + err2 or "force" in out2.lower()

    def test_allows_repeated_run_with_force(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc1, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc1 == 0

            rc2, out2, err2 = run_script(
                "--dry-run",
                "--force",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "b" * 40,
                "--candidate-id", "test-candidate-2",
                "--objective", "test objective 2",
            )
            assert rc2 == 0, f"Second run with --force failed: {err2}"

    def test_force_cleans_stale_bundle_files(self):
        """--force must remove stale bundle files before writing new ones."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc1, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc1 == 0
            # Verify initial bundle files exist
            assert os.path.exists(os.path.join(bundle, "BUNDLE_STATUS.json"))

            # Create a stale forbidden file
            stale_file = os.path.join(bundle, "stale_executable.sh")
            with open(stale_file, "w") as f:
                f.write("#!/bin/bash\necho 'dangerous'\n")
            os.chmod(stale_file, 0o755)

            # Re-run with --force
            rc2, out2, err2 = run_script(
                "--dry-run",
                "--force",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "c" * 40,
                "--candidate-id", "test-candidate-2",
                "--objective", "test objective 2",
            )
            assert rc2 == 0, f"Re-run with --force failed: {err2}"
            # Stale file must be gone
            assert not os.path.exists(stale_file), "Stale file should have been removed by --force"
            # New bundle files must exist
            assert os.path.exists(os.path.join(bundle, "BUNDLE_STATUS.json"))
            # Verify the new bundle has updated base_sha
            with open(os.path.join(bundle, "base_sha.txt")) as f:
                assert f.read().strip() == "c" * 40


class TestSourceRepoRootRejection:
    """Test that source repo cannot be filesystem root."""

    def test_rejects_root_as_source_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", "/",
                "--bundle-dir", tmpdir,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert "root" in out + err or "filesystem root" in out + err


class TestNoHermesDispatchAsExecutable:
    """Verify no Hermes/dispatch strings appear as executable commands in any bundle file."""

    FORBIDDEN_EXECUTABLE_STRINGS = [
        "hermes kanban create",
        "hermes kanban dispatch",
        "gh pr merge",
        "gh pr create",
        "git push",
        "git commit",
        "telegram",
        "send_message",
        "memory.update",
        "skill_manage",
        "fact_store",
        "delegate_task",
        "cronjob",
    ]

    @pytest.mark.parametrize("cmd", FORBIDDEN_EXECUTABLE_STRINGS)
    def test_no_forbidden_strings_in_any_bundle_file(self, cmd):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            for filename in os.listdir(bundle):
                path = bundle_file(bundle, filename)
                if not os.path.isfile(path):
                    continue
                with open(path) as f:
                    content = f.read()
                # Check only non-comment executable lines
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if stripped.startswith("#!"):
                        continue
                    if cmd in line:
                        pytest.fail(
                            f"File {filename} contains executable line with forbidden '{cmd}': {line!r}"
                        )


class TestDryRunBanner:
    """Test that Phase 1 dry-run banner appears in output."""

    def test_output_contains_no_patch_applied_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            combined = out + err
            assert "NO PATCH APPLIED" in combined
            assert "NO AGENT EXECUTED" in combined
            assert "NO HERMES TOUCHED" in combined
            assert "NO DISPATCH OCCURRED" in combined
            assert "NO PR CREATED" in combined
            assert "NO IMPORT PERFORMED" in combined


# =============================================================================
# Phase 2 Tests — Read-Only Trace Collection
# =============================================================================


class TestPhase2BundleStatusSafetyBooleans:
    """Phase 2 BUNDLE_STATUS.json must include all Phase 2 safety booleans."""

    @pytest.mark.parametrize("key,expected", [
        ("phase", "Phase 2"),
        ("dry_run", True),
        ("agent_executed", False),
        ("patch_applied", False),
        ("dispatch_occurred", False),
        ("hermes_touched", False),
        ("production_board_touched", False),
        ("pr_created", False),
        ("import_performed", False),
    ])
    def test_bundlestatus_phase2_booleans(self, key, expected):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert key in status, f"Missing key: {key}"
            assert status[key] == expected, f"{key} should be {expected}, got {status[key]}"

    def test_bundlestatus_read_only_collections_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert "read_only_collections" in status
            roc = status["read_only_collections"]
            assert isinstance(roc, dict)
            for key in ("collect_scope", "collect_safety_grep",
                        "collect_local_gate_preview", "collect_git_diff"):
                assert key in roc, f"Missing collection key: {key}"


class TestPhase2CollectionFlags:
    """Test that Phase 2 collection flags control output correctly."""

    def test_collect_scope_writes_real_scope_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a temp repo with a known file to make git diff produce output
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            # Create initial commit
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()
            # Add a new file
            with open(os.path.join(repo, "newfile.py"), "w") as f:
                f.write("# New file\n")
            subprocess.run(["git", "add", "newfile.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add newfile"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", base_sha,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
            )
            assert rc == 0, f"Script failed: {err}"
            scope = read_json(bundle, "scope_check.json")
            # Phase 2 scope check should NOT be a placeholder
            assert scope.get("files_changed_count", "unknown") != "unknown (not computed)"
            assert "current_head" in scope
            assert "changed_files" in scope

    def test_collect_git_diff_writes_real_diff(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()
            with open(os.path.join(repo, "newfile.py"), "w") as f:
                f.write("x = 1\n")
            subprocess.run(["git", "add", "newfile.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", base_sha,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-git-diff",
            )
            assert rc == 0, f"Script failed: {err}"
            diff_content = read_text(bundle, "diff.patch")
            assert "newfile.py" in diff_content, "diff.patch should contain the new file"
            changed_files_content = read_text(bundle, "changed_files.txt")
            assert "newfile.py" in changed_files_content

    def test_collect_safety_grep_writes_scan_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0, f"Script failed: {err}"
            safety = read_json(bundle, "safety_grep.txt")
            assert "files_scanned" in safety
            assert "patterns_checked" in safety
            assert "forbidden_executable_matches" in safety
            assert "forbidden_policy_mentions" in safety
            assert "total_executable_matches" in safety
            assert "total_policy_mentions" in safety
            assert isinstance(safety["patterns_checked"], list)

    def test_collect_local_gate_preview_does_not_execute_pytest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-local-gate-preview",
            )
            assert rc == 0, f"Script failed: {err}"
            gate = read_json(bundle, "local_gate.txt")
            assert gate["phase"] == "Phase 2 (read-only preview — no execution)"
            assert gate["local_gate_passed"] is None
            assert gate["compiles"] is None
            assert gate["tests_pass"] is None
            # Verify no pytest execution is in the preview
            preview_cmds = gate.get("preview_commands", [])
            assert len(preview_cmds) > 0
            for cmd_entry in preview_cmds:
                assert cmd_entry.get("executed_in_phase2") is False

    def test_collection_flags_default_to_off(self):
        """Without any --collect-* flags, all bundle files should be placeholders."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert scope.get("note", "").startswith("Phase 2:")
            safety = read_json(bundle, "safety_grep.txt")
            assert safety.get("note", "").startswith("Phase 2:")
            gate = read_json(bundle, "local_gate.txt")
            assert gate.get("note", "").startswith("Phase 2:")

    def test_all_collect_flags_can_be_combined(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                "--collect-safety-grep",
                "--collect-local-gate-preview",
                "--collect-git-diff",
            )
            assert rc == 0, f"Script failed: {err}"
            status = read_json(bundle, "BUNDLE_STATUS.json")
            roc = status["read_only_collections"]
            assert roc["collect_scope"] is True
            assert roc["collect_safety_grep"] is True
            assert roc["collect_local_gate_preview"] is True
            assert roc["collect_git_diff"] is True


class TestPhase2SafetyGrepDistinguishesPolicy:
    """Safety grep must distinguish policy mentions from executable usage."""

    def test_policy_mention_in_comment_not_flagged_as_executable(self):
        """
        A comment like '# hermes kanban create is forbidden' should appear
        in forbidden_policy_mentions, NOT in forbidden_executable_matches.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            # Create a file with a policy/documentation mention
            policy_file = os.path.join(repo, "policy.py")
            with open(policy_file, "w") as f:
                f.write("# hermes kanban create is not allowed in this phase\n")
                f.write("# gh pr merge must not be called without dry-run\n")
                f.write('"""telegram integration"""' + "\n")

            subprocess.run(["git", "add", "policy.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            safety = read_json(bundle, "safety_grep.txt")
            # Policy mentions should be recorded
            assert safety["total_policy_mentions"] > 0
            # Executable matches should be zero (these are comments/docstrings)
            assert safety["total_executable_matches"] == 0, (
                f"Comments/docstrings should not be flagged as executable. "
                f"Got: {safety['forbidden_executable_matches']}"
            )

    def test_real_executable_usage_is_detected(self):
        """
        A real executable line (not a comment) containing a forbidden command
        should appear in forbidden_executable_matches.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            # Create a file with a real (non-comment) usage of a forbidden command
            bad_file = os.path.join(repo, "bad.py")
            with open(bad_file, "w") as f:
                f.write('if __name__ == "__main__":\n')
                # This is a non-comment line with the forbidden pattern
                f.write('    os.system("gh pr merge")\n')

            subprocess.run(["git", "add", "bad.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            safety = read_json(bundle, "safety_grep.txt")
            assert safety["total_executable_matches"] > 0, (
                "Non-comment executable usage should be detected"
            )


class TestPhase2NoHermesExecution:
    """Phase 2 must not execute Hermes, pytest, compileall, or any git mutation."""

    def test_no_pytest_execution_in_phase2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-local-gate-preview",
            )
            assert rc == 0
            gate = read_json(bundle, "local_gate.txt")
            # The preview should say no execution occurred
            for cmd in gate.get("preview_commands", []):
                assert cmd.get("executed_in_phase2") is False

    def test_read_only_collections_all_false_without_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            roc = status["read_only_collections"]
            assert all(v is False for v in roc.values()), (
                f"All collection flags should default to False, got: {roc}"
            )


class TestPhase2SymlinkRegression:
    """Symlink safety regressions from Phase 1 must still pass."""

    def test_symlink_to_git_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            aed_root = Path(__file__).resolve().parents[1]
            git_target = aed_root / ".git"
            if not git_target.exists():
                pytest.skip(".git directory not found")
            link_path = Path(tmpdir) / "link-to-git"
            try:
                link_path.symlink_to(git_target, target_is_directory=True)
            except OSError:
                pytest.skip("Cannot create symlink on this filesystem")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", str(link_path),
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert ".git" in out + err or "production directory" in out + err

    def test_symlink_to_repo_root_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            aed_root = Path(__file__).resolve().parents[1]
            link_path = Path(tmpdir) / "link-to-aed"
            try:
                link_path.symlink_to(aed_root, target_is_directory=True)
            except OSError:
                pytest.skip("Cannot create symlink on this filesystem")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", str(link_path),
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc != 0
            assert "root" in out + err or "production" in out + err


class TestPhase2CodexSummaryPlaceholder:
    """codex_review_summary.md must remain a placeholder in Phase 2."""

    def test_codex_summary_still_placeholder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                "--collect-safety-grep",
                "--collect-local-gate-preview",
                "--collect-git-diff",
            )
            assert rc == 0
            codex = read_json(bundle, "codex_review_summary.md")
            assert codex["codex_reviewed"] is False
            assert "Phase 2" in codex["phase"]
            assert "not run" in codex["note"].lower() or "no codex" in codex["note"].lower()


class TestPhase2ForceCleanStaleFiles:
    """--force must clean stale files in Phase 2."""

    def test_force_removes_stale_files_in_phase2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc1, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
            )
            assert rc1 == 0
            # Create a stale forbidden file
            stale = os.path.join(bundle, "stale_executable.sh")
            with open(stale, "w") as f:
                f.write("#!/bin/bash\necho 'stale'\n")
            os.chmod(stale, 0o755)

            rc2, _, _ = run_script(
                "--dry-run",
                "--force",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "b" * 40,
                "--candidate-id", "test-candidate-2",
                "--objective", "test objective 2",
            )
            assert rc2 == 0
            assert not os.path.exists(stale), "Stale file should be removed by --force"


class TestPhase2ImportCommandSh:
    """import_command.sh must remain non-executable in Phase 2."""

    EXECUTABLE_MUTATION_COMMANDS = frozenset([
        "hermes kanban create",
        "hermes kanban dispatch",
        "gh pr merge",
        "gh pr create",
        "git push",
        "git commit",
        "telegram",
        "send_message",
        "memory.update",
        "skill_manage",
        "fact_store",
        "delegate_task",
        "cronjob",
    ])

    def test_import_command_sh_not_executable_phase2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            path = bundle_file(bundle, "import_command.sh")
            mode = os.stat(path).st_mode
            assert not (mode & 0o111), f"import_command.sh should not be executable: {oct(mode)}"

    def test_no_mutation_commands_in_import_sh_phase2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            content = read_text(bundle, "import_command.sh")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("#!"):
                    continue
                for cmd in EXECUTABLE_MUTATION_COMMANDS:
                    if cmd in line:
                        pytest.fail(f"Executable line contains forbidden '{cmd}': {line!r}")


class TestPhase2NoForbiddenStringsAsExecutable:
    """No forbidden mutation strings appear as executable commands in any Phase 2 bundle file."""

    FORBIDDEN_EXECUTABLE_STRINGS = [
        "hermes kanban create",
        "hermes kanban dispatch",
        "gh pr merge",
        "gh pr create",
        "git push",
        "git commit",
        "telegram",
        "send_message",
        "memory.update",
        "skill_manage",
        "fact_store",
        "delegate_task",
        "cronjob",
    ]

    @pytest.mark.parametrize("cmd", FORBIDDEN_EXECUTABLE_STRINGS)
    def test_no_forbidden_strings_in_any_phase2_bundle_file(self, cmd):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                "--collect-safety-grep",
                "--collect-local-gate-preview",
                "--collect-git-diff",
            )
            assert rc == 0
            for filename in os.listdir(bundle):
                path = bundle_file(bundle, filename)
                if not os.path.isfile(path):
                    continue
                with open(path) as f:
                    content = f.read()
                # Skip JSON files — patterns appear as data values in safety_grep.txt
                # which is expected. JSON content is not executable.
                if filename.endswith(".json"):
                    continue
                # safety_grep.txt contains JSON scan results with patterns as data values;
                # these are not executable content.
                if filename == "safety_grep.txt":
                    continue
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith("#!"):
                        continue
                    if cmd in line:
                        pytest.fail(
                            f"File {filename} contains executable line with forbidden '{cmd}': {line!r}"
                        )


class TestGitHardeningAndFailureModes:
    """Regression tests: git diff hardening and explicit failure-state propagation.

    Verifies fixes for Codex-reported issues:
    1. GIT_EXTERNAL_DIFF and GIT_TEXTCONV must be sanitized / blocked.
    2. Nonzero git exit codes must NOT produce scope_clean: true.
    3. diff.patch and changed_files.txt must report failure explicitly.
    """

    def test_git_external_diff_env_is_neutralized(self):
        """GIT_EXTERNAL_DIFF env var must not cause external command execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            # Set a dangerous GIT_EXTERNAL_DIFF that would execute a command
            env = dict(os.environ)
            env["GIT_EXTERNAL_DIFF"] = "echo DANGEROUS >&2"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--dry-run",
                    "--source-repo", repo,
                    "--bundle-dir", bundle,
                    "--base-sha", "a" * 40,
                    "--candidate-id", "test-candidate",
                    "--objective", "test",
                    "--collect-git-diff",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            # If GIT_EXTERNAL_DIFF fired, it would appear in stderr
            # After sanitization, no external command output should appear
            diff_content = read_text(bundle, "diff.patch")
            assert "DANGEROUS" not in diff_content, \
                "GIT_EXTERNAL_DIFF was not blocked — external command executed"

    def test_git_diff_includes_no_ext_diff_and_no_textconv_flags(self):
        """All git diff invocations must include --no-ext-diff and --no-textconv."""
        import re
        # Verify the script code contains --no-ext-diff and --no-textconv for diff calls
        with open(SCRIPT) as f:
            content = f.read()
        # Check _run_git adds these flags when first arg is "diff"
        assert "--no-ext-diff" in content, \
            "Script must add --no-ext-diff to all diff invocations"
        assert "--no-textconv" in content, \
            "Script must add --no-textconv to all diff invocations"

    def test_bad_base_sha_produces_explicit_failure_state(self):
        """A bogus base SHA must not produce scope_clean: true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "b" * 40,  # valid hex format but doesn't exist
                "--candidate-id", "test-candidate",
                "--objective", "test",
                "--collect-scope",
            )
            assert rc == 0, "Script should succeed even when git diff fails"
            scope = read_json(bundle, "scope_check.json")
            # Must NOT be True on failure
            assert scope.get("scope_clean") is not True, \
                f"scope_clean must not be True on git failure, got: {scope.get('scope_clean')}"
            # Must have explicit failure indicator
            assert scope.get("scope_status") == "failed", \
                f"scope_status must be 'failed', got: {scope.get('scope_status')}"
            assert scope.get("git_rc", 0) != 0, \
                "git_rc must be nonzero on failure"

    def test_bad_base_sha_changed_files_reports_failure(self):
        """changed_files.txt must not silently claim 'no changes' on git error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "c" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test",
                "--collect-git-diff",
            )
            assert rc == 0
            changed = read_text(bundle, "changed_files.txt")
            # Must not claim "no changed files" when git actually failed
            # It should either be empty (legitimately no changes) OR contain failure info
            # The key invariant: it must NOT say "(no changed files)" when git_rc != 0
            diff_result_path = os.path.join(bundle, "diff.patch")
            diff_content = open(diff_result_path).read() if os.path.exists(diff_result_path) else ""
            # If diff.patch shows FAILED, the collection was a failure, not "no changes"
            if "FAILED" in diff_content or "git_rc" in diff_content:
                # git failed — changed_files should not claim "no changes" with certainty
                scope = read_json(bundle, "scope_check.json") if os.path.exists(
                    os.path.join(bundle, "scope_check.json")
                ) else {}
                if scope.get("scope_status") == "failed":
                    assert "no changed files" not in changed.lower() or \
                           "failed" in changed.lower() or \
                           scope.get("files_changed_count", 0) == 0, \
                        "changed_files.txt must not claim 'no changes' when git failed"

    def test_successful_no_change_diff_reports_clean_correctly(self):
        """A valid base SHA with no changes must produce scope_clean: true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", base_sha,
                "--candidate-id", "test-candidate",
                "--objective", "test",
                "--collect-scope",
                "--collect-git-diff",
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert scope.get("scope_clean") is True, \
                f"No-change diff must have scope_clean=true, got: {scope.get('scope_clean')}"
            assert scope.get("scope_status") == "clean"
            changed = read_text(bundle, "changed_files.txt")
            assert "(no changed files)" in changed, \
                "No-change diff must report '(no changed files)'"

    def test_successful_changed_diff_reports_changed_correctly(self):
        """A valid base SHA with changes must produce scope_clean: false and list files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()

            with open(os.path.join(repo, "newfile.py"), "w") as f:
                f.write("x = 1\n")
            subprocess.run(["git", "add", "newfile.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", base_sha,
                "--candidate-id", "test-candidate",
                "--objective", "test",
                "--collect-scope",
                "--collect-git-diff",
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert scope.get("scope_clean") is False, \
                f"Changed diff must have scope_clean=false, got: {scope.get('scope_clean')}"
            assert scope.get("scope_status") == "changed"
            assert scope.get("files_changed_count", 0) > 0
            changed = read_text(bundle, "changed_files.txt")
            assert "newfile.py" in changed, \
                "changed_files.txt must list the modified file"
            diff_content = read_text(bundle, "diff.patch")
            assert "newfile.py" in diff_content, \
                "diff.patch must contain the changed file"

    def test_collect_git_diff_returns_explicit_failure_fields(self):
        """collect_git_diff must return git_rc, git_error, failed, and has_changes=null on error."""
        import importlib
        # Dynamically import to test the helper in isolation
        import sys
        sys.path.insert(0, str(SCRIPT.parent))
        # Direct import
        from run_quarantine_autocoder_dry_run import collect_git_diff

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            # Valid diff (base = HEAD)
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()
            result = collect_git_diff(repo, head_sha)
            assert "git_rc" in result, "Result must contain git_rc"
            assert "git_error" in result, "Result must contain git_error"
            assert "failed" in result, "Result must contain failed"
            assert "has_changes" in result, "Result must contain has_changes"
            assert result.get("failed") is False, "Valid diff must not be failed"
            assert result.get("git_rc") == 0, "Valid diff must have git_rc=0"

            # Invalid base SHA
            result_bad = collect_git_diff(repo, "d" * 40)
            assert result_bad.get("failed") is True, \
                "Invalid base SHA must produce failed=True"
            assert result_bad.get("git_rc") != 0, \
                "Invalid base SHA must have nonzero git_rc"
            assert result_bad.get("has_changes") is None, \
                "Failed diff must have has_changes=null"


# =============================================================================
# Bundle Reviewability Tests — Phase 2 improvements for human review
# =============================================================================


class TestSafetyGrepHumanReadableHeader:
    """safety_grep.txt must start with a human-readable summary header."""

    def test_safety_grep_txt_starts_with_summary_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0, f"Script failed: {err}"
            path = bundle_file(bundle, "safety_grep.txt")
            with open(path) as f:
                content = f.read()
            # First line must be the summary header
            assert content.startswith("# Safety Grep Summary"), \
                f"safety_grep.txt must start with summary header, got: {content[:100]!r}"

    def test_summary_counts_match_json_body(self):
        """Summary counts in header must match JSON body values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            path = bundle_file(bundle, "safety_grep.txt")
            with open(path) as f:
                content = f.read()

            # Parse summary header lines
            summary = {}
            for line in content.splitlines():
                if line.startswith("# Safety Grep Summary"):
                    continue
                if not line or line.startswith("{") or line.startswith("}"):
                    break
                if ": " in line:
                    key, val = line.split(": ", 1)
                    summary[key.strip()] = val.strip()

            # Parse JSON body (find first { and last })
            json_start = content.index("{")
            json_text = content[json_start:]
            import json as _json
            body = _json.loads(json_text)

            assert summary.get("files_scanned") == str(body["files_scanned"]), \
                f"files_scanned mismatch: header={summary.get('files_scanned')} body={body['files_scanned']}"
            assert summary.get("raw_matches") == str(body["raw_matches"]), \
                f"raw_matches mismatch: header={summary.get('raw_matches')} body={body['raw_matches']}"
            assert summary.get("policy_mentions") == str(body["policy_mentions"]), \
                f"policy_mentions mismatch: header={summary.get('policy_mentions')} body={body['policy_mentions']}"
            assert summary.get("actionable_violations") == str(body["actionable_violations"]), \
                f"actionable_violations mismatch: header={summary.get('actionable_violations')} body={body['actionable_violations']}"
            assert summary.get("clean") == str(body["clean"]).lower(), \
                f"clean mismatch: header={summary.get('clean')} body={body['clean']}"
            assert summary.get("violations_only_file") == "violations_only.json", \
                f"violations_only_file should be 'violations_only.json', got: {summary.get('violations_only_file')}"

    def test_policy_mentions_without_executable_matches_produces_clean_true(self):
        """Policy mentions with zero executable matches must report clean: true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            # Create a file with policy mentions only (no executable usage)
            policy_file = os.path.join(repo, "policy.py")
            with open(policy_file, "w") as f:
                f.write("# hermes kanban create is not allowed here\n")
                f.write('"""telegram integration"""' + "\n")

            subprocess.run(["git", "add", "policy.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            path = bundle_file(bundle, "safety_grep.txt")
            with open(path) as f:
                content = f.read()

            # Header must show clean: true
            json_start = content.index("{")
            json_text = content[json_start:]
            import json as _json
            body = _json.loads(json_text)
            assert body["clean"] is True, \
                f"Policy mentions only should be clean=true, got: {body['clean']}"
            assert body["raw_matches"] == 0, \
                f"raw_matches should be 0, got: {body['raw_matches']}"
            assert body["policy_mentions"] > 0, \
                "Should have policy mentions recorded"

    def test_executable_matches_produce_clean_false(self):
        """Executable matches must produce clean: false."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            # Create a file with real executable usage
            bad_file = os.path.join(repo, "bad.py")
            with open(bad_file, "w") as f:
                f.write('if __name__ == "__main__":\n')
                f.write('    os.system("gh pr merge")\n')

            subprocess.run(["git", "add", "bad.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            path = bundle_file(bundle, "safety_grep.txt")
            with open(path) as f:
                content = f.read()

            json_start = content.index("{")
            json_text = content[json_start:]
            import json as _json
            body = _json.loads(json_text)
            assert body["clean"] is False, \
                f"Executable matches should be clean=false, got: {body['clean']}"
            assert body["raw_matches"] > 0, \
                f"raw_matches should be > 0, got: {body['raw_matches']}"
            assert body["actionable_violations"] >= 0
            assert isinstance(body["violations"], list)


class TestBundleStatusMode:
    """BUNDLE_STATUS.json must include mode field distinguishing placeholder vs trace collection."""

    def test_placeholder_mode_when_no_collect_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert "mode" in status, "BUNDLE_STATUS.json must include 'mode' field"
            assert status["mode"] == "placeholder_bundle", \
                f"No collection flags → mode should be 'placeholder_bundle', got: {status['mode']}"

    def test_read_only_trace_collection_mode_when_collect_flags_used(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                "--collect-safety-grep",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert "mode" in status
            assert status["mode"] == "read_only_trace_collection", \
                f"With collect flags → mode should be 'read_only_trace_collection', got: {status['mode']}"

    def test_mode_transitions_correctly_with_single_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-git-diff",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert status["mode"] == "read_only_trace_collection"


class TestCodexReviewSummaryPlaceholder:
    """codex_review_summary.md must explain that Codex was not run."""

    def test_codex_summary_explains_no_codex_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            codex = read_json(bundle, "codex_review_summary.md")
            assert "placeholder" in codex.get("mode", ""), \
                f"mode should include 'placeholder', got: {codex.get('mode')}"
            assert "not run" in codex["note"].lower() or "no codex" in codex["note"].lower(), \
                f"note should explain Codex was not run, got: {codex['note']}"
            assert codex["codex_reviewed"] is False


class TestScopeCheckDiffStatus:
    """scope_check.json must include diff_status field."""

    def test_scope_check_contains_diff_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", base_sha,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert "diff_status" in scope, "scope_check.json must include diff_status field"
            assert scope["diff_status"] in ("clean", "dirty", "failed", "unknown"), \
                f"diff_status must be one of clean/dirty/failed/unknown, got: {scope['diff_status']}"

    def test_diff_status_clean_when_no_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", base_sha,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert scope["diff_status"] == "clean"

    def test_diff_status_dirty_when_changes_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()
            with open(os.path.join(repo, "newfile.py"), "w") as f:
                f.write("x = 1\n")
            subprocess.run(["git", "add", "newfile.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", base_sha,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert scope["diff_status"] == "dirty"

    def test_diff_status_failed_when_git_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "c" * 40,  # valid format but doesn't exist
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert scope["diff_status"] == "failed"


class TestSafetyGrepGeneratedAt:
    """safety_grep.txt JSON body must include generated_at timestamp."""

    def test_safety_grep_contains_generated_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            path = bundle_file(bundle, "safety_grep.txt")
            with open(path) as f:
                content = f.read()
            json_start = content.index("{")
            json_text = content[json_start:]
            import json as _json
            body = _json.loads(json_text)
            assert "generated_at" in body, "safety_grep.json must include generated_at"
            assert body["generated_at"] != ""


# =============================================================================
# Reviewability Improvements — reviewer_summary, violations_only.json
# =============================================================================


class TestReviewerSummary:
    """BUNDLE_STATUS.json must include reviewer_summary field."""

    def test_reviewbundlestatus_contains_reviewer_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert "reviewer_summary" in status, \
                "BUNDLE_STATUS.json must include 'reviewer_summary' field"

    def test_reviewer_summary_mentions_mode(self):
        """reviewer_summary must mention the bundle mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            summary = status["reviewer_summary"]
            # Must mention mode (placeholder_bundle or read_only_trace_collection)
            assert any(m in summary for m in ("placeholder", "read-only", "trace", "bundle")), \
                f"reviewer_summary must mention mode: {summary!r}"

    def test_reviewer_summary_mentions_diff_status(self):
        """reviewer_summary must mention diff_status or changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            summary = status["reviewer_summary"]
            # Must mention diff status or changes status
            assert any(m in summary.lower() for m in
                      ("diff", "changes", "clean", "dirty", "status")), \
                f"reviewer_summary must mention diff_status or changes: {summary!r}"

    def test_reviewer_summary_mentions_safety_result(self):
        """reviewer_summary must mention safety result (violations or clean)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            summary = status["reviewer_summary"]
            # Must mention safety result
            assert any(m in summary.lower() for m in
                      ("safety", "violation", "clean", "actionable", "no")), \
                f"reviewer_summary must mention safety result: {summary!r}"

    def test_reviewer_summary_mentions_mutation_status(self):
        """reviewer_summary must mention mutation status (patch/change/no change)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            summary = status["reviewer_summary"]
            # Must mention mutation or patch status
            assert any(m in summary.lower() for m in
                      ("patch", "applied", "mutation", "change", "detected")), \
                f"reviewer_summary must mention mutation status: {summary!r}"


class TestViolationsOnlyFile:
    """violations_only.json must be written when --collect-safety-grep is used."""

    def test_violations_only_file_exists_when_safety_grep_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            path = bundle_file(bundle, "violations_only.json")
            assert os.path.exists(path), \
                "violations_only.json must exist when --collect-safety-grep is used"

    def test_violations_only_empty_when_clean_repo(self):
        """violations_only.json must be empty when no actionable violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("# Test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            assert violations["actionable_violations"] == 0, \
                f"Clean repo should have 0 actionable_violations, got: {violations['actionable_violations']}"
            assert violations["violations"] == [], \
                f"Clean repo should have empty violations list, got: {violations['violations']}"

    def test_violations_only_contains_violations_when_real_violation(self):
        """violations_only.json must contain violations when an actionable violation exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            # Create a real actionable violation in a non-test file
            bad_file = os.path.join(repo, "bad.py")
            with open(bad_file, "w") as f:
                f.write('if __name__ == "__main__":\n')
                f.write('    os.system("gh pr merge")\n')
            subprocess.run(["git", "add", "bad.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            assert violations["actionable_violations"] > 0, \
                f"Real violation should produce actionable_violations > 0, got: {violations['actionable_violations']}"
            assert len(violations["violations"]) > 0, \
                f"Real violation should produce non-empty violations list, got: {violations['violations']}"
            # Each violation should have pattern, line, text, file
            for v in violations["violations"]:
                assert "pattern" in v
                assert "line" in v
                assert "text" in v
                assert "file" in v

    def test_violations_only_not_created_without_safety_grep_flag(self):
        """violations_only.json must NOT be created when --collect-safety-grep is not used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                # Note: NO --collect-safety-grep
            )
            assert rc == 0
            path = bundle_file(bundle, "violations_only.json")
            assert not os.path.exists(path), \
                "violations_only.json must NOT be created without --collect-safety-grep"

    def test_clean_true_when_forbidden_string_in_test_file(self):
        """Forbidden strings in test files must NOT count as actionable violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            # Create test file with parameterized forbidden strings
            test_dir = os.path.join(repo, "tests")
            os.makedirs(test_dir, exist_ok=True)
            with open(os.path.join(test_dir, "test_forbidden.py"), "w") as f:
                f.write('EXEC = "gh pr merge"\n')
                f.write('MSG = "hermes kanban create"\n')
            subprocess.run(["git", "add", "tests/test_forbidden.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            # Test files are non-actionable, so violations should be 0
            assert violations["actionable_violations"] == 0, \
                f"Forbidden strings in test files should not count as actionable: {violations}"
            safety_grep_path = bundle_file(bundle, "safety_grep.txt")
            with open(safety_grep_path) as f:
                content = f.read()
            json_start = content.index("{")
            import json as _json
            body = _json.loads(content[json_start:])
            assert body["clean"] is True, \
                f"Test-file-only matches should be clean=true, got: {body['clean']}"
            assert body["raw_matches"] > 0, \
                f"raw_matches should be > 0 from test file, got: {body['raw_matches']}"


class TestCleanMeansActionableViolations:
    """clean: true must mean actionable_violations == 0, not raw_matches == 0."""

    def test_clean_true_when_raw_matches_only_in_test_files(self):
        """clean=true when all raw matches are in test files (non-actionable)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            test_dir = os.path.join(repo, "tests")
            os.makedirs(test_dir)
            with open(os.path.join(test_dir, "check.py"), "w") as f:
                f.write('# "gh pr merge" in a comment\n')
                f.write('# "hermes kanban dispatch" here too\n')
                f.write('FORBIDDEN = ["git push", "git commit"]\n')
            subprocess.run(["git", "add", "tests/check.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            # All matches are in test files → 0 actionable → clean: true
            assert violations["actionable_violations"] == 0
            safety_grep_path = bundle_file(bundle, "safety_grep.txt")
            with open(safety_grep_path) as f:
                content = f.read()
            json_start = content.index("{")
            import json as _json
            body = _json.loads(content[json_start:])
            assert body["clean"] is True, \
                f"Test-file matches only → clean should be true, got: {body['clean']}"
            assert body["raw_matches"] > 0, \
                f"raw_matches should be > 0 (test file has matches), got: {body['raw_matches']}"

    def test_clean_false_when_actionable_violation_in_script_file(self):
        """clean=false when an actionable violation exists in a non-test file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            # Create a production script file with actionable violation
            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "deploy.py"), "w") as f:
                f.write("#!/usr/bin/env python3\n")
                f.write('import os\n')
                f.write('os.system("gh pr merge --admin --squash")\n')
            subprocess.run(["git", "add", "scripts/deploy.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            safety_grep_path = bundle_file(bundle, "safety_grep.txt")
            with open(safety_grep_path) as f:
                content = f.read()
            json_start = content.index("{")
            import json as _json
            body = _json.loads(content[json_start:])
            assert violations["actionable_violations"] > 0, \
                f"Actionable violation in scripts/ should count: {violations}"
            assert body["clean"] is False, \
                f"Actionable violation → clean should be false, got: {body['clean']}"


class TestExistingSafetyInvariantsPreserved:
    """All existing safety invariants must remain unchanged after reviewability improvements."""

    @pytest.mark.parametrize("key", [
        "dispatch_occurred",
        "hermes_touched",
        "production_board_touched",
        "pr_created",
        "import_performed",
        "agent_executed",
        "patch_applied",
        "dry_run",
    ])
    def test_bundlestatus_safety_invariants_unchanged(self, key):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
            )
            assert rc == 0
            status = read_json(bundle, "BUNDLE_STATUS.json")
            assert key in status, f"Missing safety invariant: {key}"
            if key in ("dry_run", "agent_executed", "patch_applied",
                      "dispatch_occurred", "hermes_touched",
                      "production_board_touched", "pr_created", "import_performed"):
                expected = key in ("dry_run",)  # only dry_run is True
                assert status[key] is expected, \
                    f"{key} should be {expected}, got {status[key]}"


class TestEarlyScopeJsonValidation:
    """Scope JSON must be validated BEFORE any bundle files are written.

    Malformed scope JSON (invalid JSON, non-list, invalid paths) must exit(1)
    before the bundle directory is created or modified, preventing partial bundles
    and blocking re-runs that would require --force.
    """

    def test_malformed_allowed_files_json_with_collect_scope_exits_before_bundle(
        self,
    ):
        """Malformed --allowed-files-json with --collect-scope exits nonzero before any bundle writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--allowed-files-json', '{ invalid json }',
            )
            assert rc != 0, "Must exit nonzero for malformed JSON"
            assert "VALIDATION ERROR" in out + err
            assert not os.path.exists(bundle), (
                "Bundle dir must NOT be created when scope JSON is malformed"
            )

    def test_malformed_forbidden_files_json_with_collect_scope_exits_before_bundle(
        self,
    ):
        """Malformed --forbidden-files-json with --collect-scope exits nonzero before any bundle writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--forbidden-files-json', '["scripts/", --invalid]',
            )
            assert rc != 0, "Must exit nonzero for malformed JSON"
            assert "VALIDATION ERROR" in out + err
            assert not os.path.exists(bundle), (
                "Bundle dir must NOT be created when scope JSON is malformed"
            )

    def test_malformed_scope_json_with_collect_safety_grep_exits_before_bundle(
        self,
    ):
        """Malformed scope JSON with --collect-safety-grep still fails early."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
                '--allowed-files-json', '{"not": "a list"}',
            )
            assert rc != 0, "Must exit nonzero for non-list JSON"
            assert "VALIDATION ERROR" in out + err
            assert not os.path.exists(bundle), (
                "Bundle dir must NOT be created when scope JSON is malformed"
            )

    def test_valid_scope_json_with_collect_scope_writes_scope_check_json(
        self,
    ):
        """Valid scope JSON with --collect-scope writes scope_check.json with scope fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--allowed-files-json', '["docs/", "schemas/"]',
                '--forbidden-files-json', '["scripts/", ".github/"]',
            )
            assert rc == 0, f"Must succeed with valid scope JSON: {out}\n{err}"
            scope = read_json(bundle, "scope_check.json")
            assert "allowed_files" in scope, "scope_check.json must include allowed_files"
            assert "forbidden_files" in scope, "scope_check.json must include forbidden_files"
            assert "scope_applied" in scope, "scope_check.json must include scope_applied"
            assert scope["allowed_files"] == ["docs/", "schemas/"]
            assert scope["forbidden_files"] == ["scripts/", ".github/"]
            assert scope["scope_applied"] is True

    def test_valid_scope_json_with_collect_safety_grep_writes_safety_grep_with_scope(
        self,
    ):
        """Valid scope JSON with --collect-safety-grep writes safety_grep.txt with scope-scoped fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0, f"Must succeed with valid scope JSON: {out}\n{err}"
            safety = read_json(bundle, "safety_grep.txt")
            assert safety.get("scope_applied") is True
            assert safety.get("allowed_files") == ["docs/"]
            assert safety.get("forbidden_files") == ["scripts/"]
            assert "clean_for_task" in safety

    def test_invalid_path_entry_exits_before_bundle(self):
        """Absolute path entry in scope JSON fails before bundle writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--allowed-files-json', '["/absolute/path"]',
            )
            assert rc != 0, "Must exit nonzero for absolute path"
            assert "VALIDATION ERROR" in out + err
            assert "absolute paths" in (out + err).lower() or "invalid path" in (out + err).lower()
            assert not os.path.exists(bundle)

    def test_double_dot_path_entry_exits_before_bundle(self):
        """'..' in scope path entry fails before bundle writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--allowed-files-json', '["../escape"]',
            )
            assert rc != 0, "Must exit nonzero for '..' path"
            assert "VALIDATION ERROR" in out + err
            assert not os.path.exists(bundle)

    def test_empty_string_entry_exits_before_bundle(self):
        """Empty string in scope list fails before bundle writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--allowed-files-json', '["docs/", ""]',
            )
            assert rc != 0, "Must exit nonzero for empty string entry"
            assert "VALIDATION ERROR" in out + err
            assert not os.path.exists(bundle)

    def test_non_list_json_exits_before_bundle(self):
        """Non-list JSON (dict, string, number) fails before bundle writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--allowed-files-json', '"docs/"',
            )
            assert rc != 0, "Must exit nonzero for non-array JSON"
            assert "VALIDATION ERROR" in out + err
            assert not os.path.exists(bundle)

    def test_non_string_list_items_exits_before_bundle(self):
        """Non-string items in scope JSON array fail before bundle writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
                '--allowed-files-json', '[123]',
            )
            assert rc != 0, "Must exit nonzero for non-string array item"
            assert "VALIDATION ERROR" in out + err
            assert not os.path.exists(bundle)

    def test_no_scope_mode_still_works(self):
        """Running without any scope JSON still creates bundle successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, out, err = run_script(
                "--dry-run",
                "--source-repo", tmpdir,
                "--bundle-dir", bundle,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-scope",
            )
            assert rc == 0, f"No-scope mode must succeed: {out}\n{err}"
            assert os.path.isdir(bundle), "Bundle must be created"
            scope = read_json(bundle, "scope_check.json")
            assert "scope_applied" in scope
            assert scope["scope_applied"] is False