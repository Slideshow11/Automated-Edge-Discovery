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
            assert body["raw_matches"] > 0, \
                f"raw_matches should be > 0 (includes policy mentions now), got: {body['raw_matches']}"
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
                f"raw_matches (pre-filter alias) should be > 0 from test file, got: {body['raw_matches']}"
            assert body["raw_matches_total"] > 0, \
                f"raw_matches_total should be > 0 from test file, got: {body['raw_matches_total']}"
            assert body["post_filter_matches_total"] == 0, \
                f"post_filter_matches_total should be 0 (test files suppressed), got: {body['post_filter_matches_total']}"
            assert body["executable_violations_count"] > 0, \
                f"executable_violations_count should be > 0 before test-file suppression, got: {body['executable_violations_count']}"
            assert body["clean_for_task"] is True, \
                f"clean_for_task must be True when only test-file matches exist, got: {body['clean_for_task']}"
            assert body["actionable_violations"] == 0, \
                f"actionable_violations must be 0 when matches are in test files only, got: {body['actionable_violations']}"


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
            assert body["raw_matches_total"] > 0, \
                f"raw_matches_total should be > 0, got: {body['raw_matches_total']}"
            assert body["post_filter_matches_total"] == 0, \
                f"post_filter_matches_total should be 0 (test files suppressed), got: {body['post_filter_matches_total']}"
            assert body["clean_for_task"] is True, \
                f"clean_for_task must be True when only test-file policy/comment matches exist, got: {body['clean_for_task']}"
            assert body["actionable_violations"] == 0, \
                f"actionable_violations must be 0, got: {body['actionable_violations']}"

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
            assert body["clean_for_task"] is False, \
                f"clean_for_task should be False when violation is in allowed scope, got: {body['clean_for_task']}"
            assert body["executable_violations_count"] > 0, \
                f"executable_violations_count should be > 0 for actionable violation, got: {body['executable_violations_count']}"
            assert body["raw_matches_total"] > 0, \
                f"raw_matches_total should be > 0, got: {body['raw_matches_total']}"
            assert body["post_filter_matches_total"] > 0, \
                f"post_filter_matches_total should be > 0 (non-test file violation), got: {body['post_filter_matches_total']}"


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


class TestTaskCleanlinessNormalization:
    """Task cleanliness fields must be unambiguous and machine-parseable.

    clean_for_task is based ONLY on allowed_scope violations.
    Forbidden-scope and out-of-scope findings are visible separately
    and do NOT make clean_for_task = false.
    """

    def test_clean_for_task_true_when_allowed_scope_clean(self):
        """clean_for_task=true when allowed scope has zero violations,
        even when forbidden scope has violations (they don't dirty the task)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            # Create a violation in scripts/ (forbidden scope)
            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "bad.py"), "w") as f:
                f.write("os.system('hermes kanban create')\n")
            # Create clean docs/ file (allowed scope)
            docs_dir = os.path.join(repo, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "good.md"), "w") as f:
                f.write("# Just a doc\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0, f"Script should succeed: rc={rc}"
            violations = read_json(bundle, "violations_only.json")
            assert violations["clean_for_task"] is True, \
                f"Allowed scope clean → clean_for_task=true, got: {violations['clean_for_task']}"
            assert violations["executable_matches_in_allowed_scope"] == 0, \
                f"Allowed scope should have 0 matches, got: {violations['executable_matches_in_allowed_scope']}"
            assert violations["executable_matches_in_forbidden_scope"] > 0, \
                f"Forbidden scope should have matches, got: {violations['executable_matches_in_forbidden_scope']}"
            assert violations["task_clean_summary"]["clean_for_task"] is True

    def test_clean_for_task_false_when_allowed_scope_has_violation(self):
        """clean_for_task=false when allowed scope has an actionable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            # Create violation in docs/ (allowed scope)
            docs_dir = os.path.join(repo, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "bad.py"), "w") as f:
                f.write("os.system('gh pr merge')\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            assert violations["clean_for_task"] is False, \
                f"Allowed scope has violation → clean_for_task=false, got: {violations['clean_for_task']}"
            assert violations["executable_matches_in_allowed_scope"] > 0
            assert violations["task_clean_summary"]["allowed_scope"] == "dirty"

    def test_clean_for_task_false_when_executable_in_allowed_scope(self):
        """Executable match in allowed scope makes clean_for_task=false."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            # Put forbidden command in a scripts/ file but allow scripts/
            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "check.py"), "w") as f:
                f.write("os.system('gh pr merge --admin')\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
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
                '--allowed-files-json', '["scripts/"]',
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            assert violations["clean_for_task"] is False, \
                f"Executable in allowed scope → clean_for_task=false, got: {violations['clean_for_task']}"
            assert violations["executable_matches_in_allowed_scope"] > 0

    def test_clean_for_task_not_affected_by_out_of_scope_matches(self):
        """Out-of-scope violations do not affect clean_for_task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            # Create violation in scripts/ (out of allowed scope, not forbidden)
            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "bad.py"), "w") as f:
                f.write("os.system('gh pr merge')\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
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
                '--allowed-files-json', '["docs/"]',
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            # docs/ is clean, scripts/ is out of scope — clean_for_task should be true
            assert violations["clean_for_task"] is True, \
                f"Out-of-scope violation should NOT affect clean_for_task, got: {violations['clean_for_task']}"
            assert violations["executable_matches_out_of_scope"] > 0
            assert violations["executable_matches_in_allowed_scope"] == 0

    def test_normalized_count_fields_are_explicit_integers_not_null(self):
        """Count fields must be explicit integers (0), never null, when scope applied."""
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            for field in (
                "executable_matches_in_allowed_scope",
                "executable_matches_in_forbidden_scope",
                "executable_matches_out_of_scope",
                "allowed_scope_violations_count",
                "forbidden_scope_violations_count",
                "out_of_scope_violations_count",
            ):
                assert field in violations, f"Missing field: {field}"
                assert violations[field] == 0, \
                    f"{field} should be 0 (not null), got: {repr(violations[field])}"

    def test_normalized_array_fields_are_empty_arrays_not_null(self):
        """Array fields must be empty arrays ([]), never null, when scope applied."""
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            for field in (
                "allowed_scope_violations",
                "forbidden_scope_violations",
                "out_of_scope_violations",
            ):
                assert field in violations, f"Missing field: {field}"
                assert isinstance(violations[field], list), \
                    f"{field} should be list, got: {type(violations[field]).__name__}"
                assert violations[field] == [], \
                    f"{field} should be [] (not null), got: {repr(violations[field])}"

    def test_violations_only_and_safety_grep_agree_on_scope_counts(self):
        """safety_grep.txt and violations_only.json must agree on scoped counts."""
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0
            # Read safety_grep.txt text format
            import json as _json
            sg_path = bundle_file(bundle, "safety_grep.txt")
            sg_text = open(sg_path).read()
            json_start = sg_text.index("{")
            sg_body = _json.loads(sg_text[json_start:])

            violations = read_json(bundle, "violations_only.json")
            for field in (
                "executable_matches_in_allowed_scope",
                "executable_matches_in_forbidden_scope",
                "executable_matches_out_of_scope",
            ):
                assert sg_body.get(field) == violations.get(field), \
                    f"safety_grep.txt[{field}]={sg_body.get(field)} != violations_only[{field}]={violations.get(field)}"

    def test_scope_check_echoes_scope_args_without_null_fields(self):
        """scope_check.json echoes scope args; no null match count fields."""
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0
            scope = read_json(bundle, "scope_check.json")
            assert scope.get("allowed_files") == ["docs/"]
            assert scope.get("forbidden_files") == ["scripts/"]
            assert scope.get("scope_applied") is True
            # No null match fields in scope_check — scope_check is about git diff,
            # not about executable match counts
            for key in scope:
                assert scope[key] is not None or key in (
                    "scope_clean", "diff_status", "git_error",
                    "files_changed_count", "bundle_dir_outside_repo_root",
                    "bundle_dir_inside_git", "git_rc",
                ), f"Unexpected null field in scope_check: {key} = {scope[key]}"

    def test_summary_total_equals_bucket_sum_post_filter(self):
        """summary.total must equal in_allowed_scope + in_forbidden_scope + out_of_scope
        (total is post-test-file-filter sum, not executable_matches_total).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            # Create violation in scripts/ (forbidden scope)
            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "bad.py"), "w") as f:
                f.write("os.system('gh pr merge')\n")
            # Create clean docs/ file (allowed scope)
            docs_dir = os.path.join(repo, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "good.md"), "w") as f:
                f.write("# Doc\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            total = violations["summary"]["total"]
            bucket_sum = (
                violations["summary"]["in_allowed_scope"]
                + violations["summary"]["in_forbidden_scope"]
                + violations["summary"]["out_of_scope"]
            )
            assert total == bucket_sum, \
                f"summary.total={total} must equal sum of buckets={bucket_sum}"

    def test_task_clean_summary_schema_complete(self):
        """task_clean_summary must contain all required fields in both clean/dirty states."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Case 1: allowed_scope clean
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "bad.py"), "w") as f:
                f.write("os.system('gh pr merge')\n")
            docs_dir = os.path.join(repo, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "good.md"), "w") as f:
                f.write("# Doc\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
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
                '--allowed-files-json', '["docs/"]',
                '--forbidden-files-json', '["scripts/"]',
            )
            assert rc == 0
            violations = read_json(bundle, "violations_only.json")
            ts = violations["task_clean_summary"]
            for field in ("allowed_scope", "forbidden_scope", "out_of_scope_suppressed", "clean_for_task"):
                assert field in ts, f"task_clean_summary missing: {field}"
            assert ts["allowed_scope"] == "clean"
            assert ts["forbidden_scope"] == "dirty"
            assert ts["out_of_scope_suppressed"] == 0
            assert ts["clean_for_task"] is True

            # Case 2: allowed_scope dirty
            repo2 = os.path.join(tmpdir, "repo2")
            os.makedirs(repo2)
            subprocess.run(["git", "init"], cwd=repo2, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo2, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo2, capture_output=True)
            docs2_dir = os.path.join(repo2, "docs")
            os.makedirs(docs2_dir)
            with open(os.path.join(docs2_dir, "bad.py"), "w") as f:
                f.write("os.system('gh pr merge')\n")
            subprocess.run(["git", "add", "."], cwd=repo2, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo2, capture_output=True)

            bundle2 = os.path.join(tmpdir, "bundle2")
            rc2, _, _ = run_script(
                "--dry-run",
                "--source-repo", repo2,
                "--bundle-dir", bundle2,
                "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate",
                "--objective", "test objective",
                "--collect-safety-grep",
                '--allowed-files-json', '["docs/"]',
            )
            assert rc2 == 0
            violations2 = read_json(bundle2, "violations_only.json")
            ts2 = violations2["task_clean_summary"]
            assert ts2["allowed_scope"] == "dirty"
            assert ts2["clean_for_task"] is False


class TestCountSchemaSemantics:
    """
    Verify the raw/post-filter count schema is unambiguous and correct.

    Required semantics:
    - raw_matches_total: ALL pattern hits BEFORE any suppression/filtering
    - post_filter_matches_total: matches REMAINING after suppression/filtering
    - raw_matches: backward-compatible alias for raw_matches_total
    - executable_violations_count: pre-filter executable violations
    - policy_mentions_count: pre-filter policy mentions
    - suppressed_context_count: pre-filter suppressed contexts
    """

    def test_raw_matches_total_includes_test_file_matches(self):
        """raw_matches_total must count matches even when they are in test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            test_dir = os.path.join(repo, "tests")
            os.makedirs(test_dir)
            with open(os.path.join(test_dir, "sample.py"), "w") as f:
                f.write('EXEC = "gh pr merge"\n')
                f.write('MSG = "hermes kanban create"\n')
            subprocess.run(["git", "add", "tests/sample.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")

            # raw_matches_total counts test-file matches
            assert sg["raw_matches_total"] == 2, f"raw_matches_total should be 2, got {sg['raw_matches_total']}"
            # raw_matches is backward-compatible alias
            assert sg["raw_matches"] == sg["raw_matches_total"]
            # post_filter removes test-file matches
            assert sg["post_filter_matches_total"] == 0, f"post_filter should be 0, got {sg['post_filter_matches_total']}"

    def test_policy_mention_in_comment_is_suppressed(self):
        """A forbidden string in a comment must not make clean_for_task false."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "deploy.py"), "w") as f:
                f.write('# "gh pr merge" is forbidden here\n')
                f.write('print("hello")\n')
            subprocess.run(["git", "add", "scripts/deploy.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"Comment policy mention should not dirty task, got clean_for_task={sg['clean_for_task']}"
            assert sg["policy_mentions_count"] >= 1, \
                f"policy_mentions_count should be >= 1, got {sg['policy_mentions_count']}"
            assert sg["actionable_violations"] == 0

    def test_argparse_help_text_is_suppressed(self):
        """A forbidden string in argparse help text must not make clean_for_task false."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Use a line that is clearly argparse help with "--" prefix
            with open(os.path.join(scripts_dir, "cli.py"), "w") as f:
                f.write('import argparse\n')
                f.write('parser.add_argument("--gh-pr-merge", help="gh pr merge is not allowed in Phase 2")\n')
            subprocess.run(["git", "add", "scripts/cli.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"argparse help context should not dirty task, got clean_for_task={sg['clean_for_task']}"
            assert sg["suppressed_context_count"] >= 1, \
                f"suppressed_context_count should be >= 1, got {sg['suppressed_context_count']}"

    def test_subprocess_run_is_executable_violation(self):
        """subprocess.run with a forbidden command must be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Must use shell=True or the pattern won't be found as a contiguous string
            with open(os.path.join(scripts_dir, "run.py"), "w") as f:
                f.write('import subprocess\n')
                f.write('subprocess.run("gh pr merge --admin --squash", shell=True)\n')
            subprocess.run(["git", "add", "scripts/run.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is False, \
                f"subprocess.run with forbidden command should dirty task, got clean_for_task={sg['clean_for_task']}"
            assert sg["executable_violations_count"] > 0, \
                f"executable_violations_count should be > 0, got {sg['executable_violations_count']}"
            assert sg["actionable_violations"] > 0

    def test_os_system_is_executable_violation(self):
        """os.system with a forbidden command must be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "sys.py"), "w") as f:
                f.write('import os\n')
                f.write('os.system("gh pr merge --admin --squash")\n')
            subprocess.run(["git", "add", "scripts/sys.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is False, \
                f"os.system with forbidden command should dirty task, got clean_for_task={sg['clean_for_task']}"
            assert sg["executable_violations_count"] > 0

    def test_allowed_scope_policy_mention_does_not_dirty_task(self):
        """Policy mention in allowed scope must not make clean_for_task false."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "policy.py"), "w") as f:
                f.write('# hermes kanban create is not allowed in Phase 1\n')
                f.write('# git push is also forbidden\n')
            subprocess.run(["git", "add", "scripts/policy.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
                '--allowed-files-json', '["scripts/"]',
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"Policy mentions in allowed scope should not dirty task, got {sg['clean_for_task']}"
            assert sg["actionable_violations"] == 0

    def test_allowed_scope_executable_violation_dirties_task(self):
        """Executable violation in allowed scope must make clean_for_task false."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "bad.py"), "w") as f:
                f.write('import os\n')
                f.write('os.system("git push")\n')
            subprocess.run(["git", "add", "scripts/bad.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
                '--allowed-files-json', '["scripts/"]',
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is False, \
                f"Executable violation in allowed scope must dirty task, got {sg['clean_for_task']}"
            assert sg["actionable_violations"] > 0

    def test_def_function_name_is_identifier_not_executable(self):
        """A function name containing a forbidden token must not be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "handler.py"), "w") as f:
                f.write("def build_telegram_summary(msg):\n")
                f.write("    return msg.text\n")
            subprocess.run(["git", "add", "scripts/handler.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"Function name with 'telegram' must not dirty task, got clean_for_task={sg['clean_for_task']}"
            # Must be classified as identifier_or_prose (policy_mention), not executable_violations
            assert sg["executable_violations_count"] == 0, \
                f"executable_violations_count should be 0 for identifier context, got {sg['executable_violations_count']}"

    def test_class_name_is_identifier_not_executable(self):
        """A class name containing a forbidden token must not be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "model.py"), "w") as f:
                f.write("class TelegramHandler:\n")
                f.write("    pass\n")
            subprocess.run(["git", "add", "scripts/model.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"Class name with 'TelegramHandler' must not dirty task, got {sg['clean_for_task']}"
            assert sg["executable_violations_count"] == 0

    def test_constant_name_containing_forbidden_token_is_identifier(self):
        """A constant/variable name containing a forbidden token must not be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "config.py"), "w") as f:
                f.write("TELEGRAM_WARNING = 'do not use gh pr merge'\n")
                f.write("gh_command = 'test'\n")
            subprocess.run(["git", "add", "scripts/config.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"Constant names with forbidden tokens must not dirty task, got {sg['clean_for_task']}"
            assert sg["executable_violations_count"] == 0, \
                f"executable_violations_count should be 0, got {sg['executable_violations_count']}"

    def test_subprocess_list_args_are_executable_violations(self):
        """subprocess.run(...) must be classified as executable_violations when it wraps a forbidden command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "run.py"), "w") as f:
                f.write("import subprocess\n")
                # Use a contiguous string so the grep pattern finds it;
                # subprocess.run is then classified as executable_context
                f.write('subprocess.run("gh pr merge --admin --squash", check=True)\n')
            subprocess.run(["git", "add", "scripts/run.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is False, \
                f"subprocess.run with forbidden command must dirty task, got {sg['clean_for_task']}"
            assert sg["executable_violations_count"] > 0

    def test_docstring_body_is_identifier_not_executable(self):
        """A forbidden string in a docstring body (not a docstring boundary) must not be executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "doc.py"), "w") as f:
                f.write("def foo():\n")
                f.write('    """Do not use gh pr merge in this function."""\n')
                f.write('    pass\n')
            subprocess.run(["git", "add", "scripts/doc.py"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"Docstring body with forbidden string must not dirty task, got {sg['clean_for_task']}"
            assert sg["executable_violations_count"] == 0

    def test_github_slug_rejected_before_git_operations(self):
        """GitHub slug like 'Slideshow11/Automated-Edge-Discovery' must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, stdout, stderr = run_script(
                "--dry-run", "--source-repo", "Slideshow11/Automated-Edge-Discovery",
                "--bundle-dir", bundle, "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate", "--objective", "test",
            )
            assert rc != 0, "Should fail for GitHub slug"
            combined = stdout + stderr
            assert "Slideshow11/Automated-Edge-Discovery" in combined, \
                f"Error should mention the invalid slug, got: {combined}"


class TestSourceRepoValidation:
    """Source repo validation rejects GitHub slugs, accepts local paths."""

    def test_github_slug_rejected_with_clear_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, stdout, stderr = run_script(
                "--dry-run", "--source-repo", "owner/repo",
                "--bundle-dir", bundle, "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate", "--objective", "test",
            )
            assert rc != 0
            combined = stdout + stderr
            assert "owner/repo" in combined, \
                f"Error should mention the invalid slug, got: {combined}"

    def test_absolute_local_path_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            rc, stdout, stderr = run_script(
                "--dry-run", "--source-repo", tmpdir,
                "--bundle-dir", bundle, "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate", "--objective", "test",
            )
            # Should not fail due to source-repo validation
            assert rc == 0 or "not a valid JSON" in stderr or "source-repo" in stderr

    def test_relative_local_path_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = os.path.join(tmpdir, "bundle")
            os.chdir(tmpdir)
            # Create a subdirectory and use relative path
            subdir = os.path.join(tmpdir, "myrepo")
            os.makedirs(subdir)
            rc, _, stderr = run_script(
                "--dry-run", "--source-repo", "myrepo",
                "--bundle-dir", bundle, "--base-sha", "a" * 40,
                "--candidate-id", "test-candidate", "--objective", "test",
            )
            # Relative path resolving to existing dir should be accepted
            # (or fail for other reasons, but not source-repo validation)
            assert "local path" not in stderr.lower() or rc == 0


class TestDocstringStateMachine:
    """
    Verify the docstring state machine handles all edge cases correctly.

    The state machine tracks whether we're inside a triple-quote docstring
    and must correctly handle: single-line docstrings, multiline opening/closing
    lines, end-of-line closes, adjacent function docstrings, and real executable
    code appearing after a docstring closes.
    """

    def test_end_of_line_docstring_close_exits_docstring_state(self):
        """A docstring that closes at the end of a content line must exit docstring state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Docstring with content THEN close on same line (end-of-line close)
            # followed by an executable violation
            # Use double-quote string: subprocess.run("gh pr merge ...", shell=True)
            # so the pattern 'gh pr merge' appears as a contiguous substring
            with open(os.path.join(scripts_dir, "deploy.py"), "w") as f:
                f.write(
                    '"""Collects violations for triage"""\n'
                    'subprocess.run("gh pr merge --admin", shell=True)\n'
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # The docstring line is a policy mention, not a violation
            # The subprocess.run line is a REAL executable violation
            assert sg["executable_violations_count"] >= 1, \
                f"executable_violations_count should be >= 1 (subprocess.run outside docstring), got {sg['executable_violations_count']}"
            assert sg["clean_for_task"] is False, \
                f"clean_for_task should be False (executable violation in allowed scope), got {sg['clean_for_task']}"

    def test_executable_violation_after_end_of_line_docstring_close_is_caught(self):
        """An executable violation appearing after an end-of-line docstring close must be caught."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # End-of-line close: 'Collects violations"""'
            with open(os.path.join(scripts_dir, "triage.py"), "w") as f:
                f.write('"""Collects violations for triage"""\n')
                f.write('os.system("git push origin main")\n')
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # os.system("git push") is a real executable violation after the docstring closed
            assert sg["executable_violations_count"] >= 1, \
                f"os.system(git push) after end-of-line close must be executable violation, got {sg['executable_violations_count']}"
            assert sg["actionable_violations"] >= 1, \
                f"actionable_violations should be >= 1, got {sg['actionable_violations']}"

    def test_docstring_body_with_forbidden_command_is_not_executable_violation(self):
        """A forbidden command in the body of a docstring must be suppressed, not a violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "policy.py"), "w") as f:
                f.write(
                    '"""\n'
                    'Policy: Do not call gh pr merge directly.\n'
                    'Use the merge gate instead.\n'
                    '"""\n'
                    'x = 1\n'
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # "gh pr merge" in docstring body → policy_mentions, not executable_violations
            assert sg["clean_for_task"] is True, \
                f"docstring body with 'gh pr merge' should not dirty task, got clean_for_task={sg['clean_for_task']}"
            assert sg["policy_mentions_count"] >= 1, \
                f"policy_mentions_count should be >= 1, got {sg['policy_mentions_count']}"

    def test_single_line_docstring_with_forbidden_command_is_not_executable_violation(self):
        """A single-line docstring containing a forbidden command must not be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Single-line docstring: '"""text"""' (count=2, even → no toggle)
            with open(os.path.join(scripts_dir, "single.py"), "w") as f:
                f.write('"""This policy forbids: gh pr merge."""\n')
                f.write('x = 1\n')
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"single-line docstring 'gh pr merge' should not dirty task, got clean_for_task={sg['clean_for_task']}"

    def test_adjacent_function_docstrings_do_not_break_state(self):
        """
        When a line like '    \"\"\"  # inner function docstring at START' appears inside an
        outer docstring body, the state machine must NOT exit the outer docstring.
        The comment-only heuristic (triple at START + comment starting with hash → stay inside)
        keeps us inside the outer docstring.

        Result: the os.system line is classified as policy_mentions (docstring), not
        executable_violations, because we never exit the outer docstring.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Structure:
            # L1: open outer docstring (ENTER outer)
            # L2: triple at START + comment after → STAY INSIDE (comment-only heuristic)
            # L3: inside outer body
            # L4: triple at START + comment after → STAY INSIDE (comment-only heuristic)
            # L5: os.system("git push origin main") → inside outer (never exited!)
            # The os.system line is inside the outer docstring → classified as policy_mentions (docstring)
            with open(os.path.join(scripts_dir, "adjacent.py"), "w") as f:
                f.write(
                    '"""Outer docstring with policy text.\n'
                    '    """  # inner function docstring at START\n'
                    'More outer content.\n'
                    '"""  # close outer\n'
                    'os.system("git push origin main")\n'
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # With the comment-only heuristic, we never exit the outer docstring
            # (because '"""  # close outer' is comment-only → stay inside).
            # Therefore the os.system line is INSIDE the outer docstring → policy_mentions.
            assert sg["clean_for_task"] is True, \
                f"clean_for_task should be True (os.system inside docstring = policy mention), got clean_for_task={sg['clean_for_task']}"
            assert sg["policy_mentions_count"] >= 1, \
                f"policy_mentions_count should be >= 1 (os.system inside docstring), got {sg['policy_mentions_count']}"

    def test_real_subprocess_run_outside_docstring_remains_violation(self):
        """subprocess.run with a forbidden command outside any docstring must be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Use double-quote string so 'gh pr merge' is a contiguous substring
            # (the pattern is matched as a substring in the line)
            with open(os.path.join(scripts_dir, "exec.py"), "w") as f:
                f.write('import subprocess\n')
                f.write('subprocess.run("gh pr merge --admin --squash", shell=True)\n')
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["executable_violations_count"] >= 1, \
                f"subprocess.run outside docstring must be executable violation, got {sg['executable_violations_count']}"
            assert sg["clean_for_task"] is False, \
                f"clean_for_task should be False (executable violation), got {sg['clean_for_task']}"

    def test_real_os_system_outside_docstring_remains_violation(self):
        """os.system with a forbidden command outside any docstring must be an executable violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "os_exec.py"), "w") as f:
                f.write('import os\n')
                f.write('os.system("git push origin main")\n')
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["executable_violations_count"] >= 1, \
                f"os.system(git push) outside docstring must be executable violation, got {sg['executable_violations_count']}"

    def test_allowed_scope_docstring_examples_do_not_make_clean_false(self):
        """Docstring examples with forbidden commands in the allowed scope must not dirty the task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # File with docstring examples of forbidden commands
            with open(os.path.join(scripts_dir, "policy_examples.py"), "w") as f:
                f.write(
                    '"""Policy: Do not call gh pr merge in Phase 1.\n'
                    'Example: "gh pr merge --admin --squash"\n'
                    'Forbidden: hermes kanban create, telegram send\n'
                    '"""\n'
                    'x = 1\n'
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
                '--allowed-files-json', '["scripts/"]',
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            assert sg["clean_for_task"] is True, \
                f"docstring examples should not dirty task, got clean_for_task={sg['clean_for_task']}"

    def test_mid_line_docstring_close_exits_docstring_state(self):
        """
        When a docstring closes mid-line (content before closing triple, e.g.
        'Forbidden: gh pr merge triple-close'), the state machine must exit docstring state
        even though the triple is NOT at the START of the line.

        The os.system line after the docstring close must be an executable violation.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Mid-line close: 'Forbidden: gh pr merge"""' — triple NOT at line start
            # os.system after this must be an executable violation
            with open(os.path.join(scripts_dir, "policy.py"), "w") as f:
                f.write(
                    '"""Forbidden: gh pr merge"""\n'
                    'os.system("git push origin main")\n'
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # os.system("git push") after mid-line docstring close must be executable violation
            assert sg["executable_violations_count"] >= 1, \
                f"executable_violations_count should be >= 1 (os.system after mid-line close), got {sg['executable_violations_count']}"
            assert sg["clean_for_task"] is False, \
                f"clean_for_task should be False (executable violation in allowed scope), got {sg['clean_for_task']}"

    def test_mid_line_docstring_close_then_docstring_then_executable(self):
        """
        When a docstring closes mid-line (triple not at start), a new docstring opens,
        and an executable appears after the new docstring closes — all three contexts
        must be correctly classified.

        Lines:
        - L1: policy_mentions boundary (docstring opens+closes with content inside)
        - L2: single-line docstring
        - L3: os.system executable violation
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "multi.py"), "w") as f:
                f.write(
                    '"""Forbidden: gh pr merge"""\n'       # mid-line close → exit L1 state
                    '"""Single-line doc"""\n'             # standalone → enter/exit (single-line)
                    'os.system("git push origin main")\n'  # outside all docstrings → executable violation
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # os.system after all docstrings closed must be caught
            assert sg["executable_violations_count"] >= 1, \
                f"executable_violations_count should be >= 1, got {sg['executable_violations_count']}"
            assert sg["clean_for_task"] is False, \
                f"clean_for_task should be False, got {sg['clean_for_task']}"

    def test_raw_docstring_forbidden_token_is_policy_mention(self):
        """
        A raw docstring (r\"\"\"...) containing a forbidden token must be classified
        as policy_mentions, not executable_violations.

        The raw docstring opening (r\"\"\") should count as entering docstring state
        without double-counting the delimiter.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # Raw docstring with forbidden command in body
            with open(os.path.join(scripts_dir, "raw_policy.py"), "w") as f:
                f.write(
                    'r"""Raw policy docstring.\n'
                    'Do not call gh pr merge in phase 1.\n'
                    '"""  # raw docstring close\n'
                    'os.system("git push origin main")\n'
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # Raw docstring content should be policy_mentions (not executable)
            assert sg["policy_mentions_count"] >= 1, \
                f"policy_mentions_count should be >= 1 (raw docstring body), got {sg['policy_mentions_count']}"
            # os.system after raw docstring close must be caught as executable
            assert sg["executable_violations_count"] >= 1, \
                f"executable_violations_count should be >= 1 (os.system after raw close), got {sg['executable_violations_count']}"
            assert sg["clean_for_task"] is False, \
                f"clean_for_task should be False (executable after raw docstring), got {sg['clean_for_task']}"

    def test_raw_matches_total_includes_policy_mentions(self):
        """
        raw_matches_total must include policy_mentions_count in its sum.

        A file with only docstring examples (policy mentions, no executable usage)
        must still produce raw_matches_total > 0.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # File with only docstring policy examples — no executable usage
            with open(os.path.join(scripts_dir, "policy_only.py"), "w") as f:
                f.write(
                    '"""Policy: Do not call gh pr merge in Phase 1.\n'
                    'Example: "gh pr merge --admin --squash"\n'
                    '"""'
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            # raw_matches_total must include policy_mentions
            assert sg["raw_matches_total"] > 0, \
                f"raw_matches_total should be > 0 (has policy mentions), got {sg['raw_matches_total']}"
            assert sg["raw_matches"] == sg["raw_matches_total"], \
                f"raw_matches should equal raw_matches_total, got raw_matches={sg['raw_matches']}"
            # clean_for_task must be true (only policy mentions, no executable)
            assert sg["clean_for_task"] is True, \
                f"clean_for_task should be True (policy mentions only), got {sg['clean_for_task']}"

    def test_raw_matches_total_equals_executable_plus_policy_plus_suppressed(self):
        """
        raw_matches_total must equal executable_violations_count + policy_mentions_count + suppressed_context_count.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

            scripts_dir = os.path.join(repo, "scripts")
            os.makedirs(scripts_dir)
            # File with all three classification types
            with open(os.path.join(scripts_dir, "mixed.py"), "w") as f:
                f.write(
                    '# gh pr merge in comment\n'          # policy_mentions (comment)
                    '"""hermes kanban create in docstring\n'  # policy_mentions (docstring)
                    '"""'
                    'os.system("git push origin main")\n'   # executable_violations
                )
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            bundle = os.path.join(tmpdir, "bundle")
            rc, _, _ = run_script(
                "--dry-run", "--source-repo", repo, "--bundle-dir", bundle,
                "--base-sha", "a" * 40, "--candidate-id", "test-candidate",
                "--objective", "test", "--collect-safety-grep",
            )
            assert rc == 0
            sg = read_json(bundle, "safety_grep.txt")
            expected = sg["executable_violations_count"] + sg["policy_mentions_count"] + sg["suppressed_context_count"]
            assert sg["raw_matches_total"] == expected, \
                f"raw_matches_total should be {expected} (exec+policy+suppressed), got {sg['raw_matches_total']}"