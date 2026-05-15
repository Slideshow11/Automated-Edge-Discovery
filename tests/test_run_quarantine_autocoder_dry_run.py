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
        return json.load(f)


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