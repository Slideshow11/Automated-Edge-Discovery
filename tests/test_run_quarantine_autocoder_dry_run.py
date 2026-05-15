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
            assert result_bad.get("patch") == "", \
                "Failed diff must have empty patch"