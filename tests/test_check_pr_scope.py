"""Tests for scripts/local/check_pr_scope.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
from check_pr_scope import (
    PACKET_KIND,
    SCHEMA_VERSION,
    normalize_path,
    normalize_list,
    matches_glob,
    path_matches_any,
    check_scope,
    parseCommaList,
    load_json_list,
    render_markdown,
    main as scope_main,
)


# ── Normalize ──────────────────────────────────────────────────────────────────

class TestNormalizePath:
    def test_leading_dot_slash_stripped(self):
        assert normalize_path("./scripts/local/foo.py") == "scripts/local/foo.py"

    def test_leading_dot_backslash_stripped(self):
        assert normalize_path(".\\scripts\\local\\foo.py") == "scripts/local/foo.py"

    def test_forward_slash_preserved(self):
        assert normalize_path("scripts/local/foo.py") == "scripts/local/foo.py"

    def test_backslash_converted(self):
        assert normalize_path("scripts\\local\\foo.py") == "scripts/local/foo.py"

    def test_strips_whitespace(self):
        assert normalize_path("  scripts/local/foo.py  ") == "scripts/local/foo.py"


class TestNormalizeList:
    def test_deduplicates(self):
        result = normalize_list(["a.py", "b.py", "a.py", "a.py"])
        assert result == ["a.py", "b.py"]

    def test_preserves_order(self):
        result = normalize_list(["z.py", "a.py", "m.py"])
        assert result == ["z.py", "a.py", "m.py"]

    def test_ignores_empty(self):
        result = normalize_list(["a.py", "", "  ", "b.py"])
        assert result == ["a.py", "b.py"]

    def test_normalizes_paths(self):
        result = normalize_list(["./a.py", ".\\b.py", "c.py"])
        assert result == ["a.py", "b.py", "c.py"]


# ── Glob matching ───────────────────────────────────────────────────────────────

class TestMatchesGlob:
    def test_exact_match(self):
        assert matches_glob("docs/README.md", "docs/README.md") is True
        assert matches_glob("docs/README.md", "docs/OTHER.md") is False

    def test_single_wildcard(self):
        assert matches_glob("scripts/local/foo.py", "scripts/local/*.py") is True
        assert matches_glob("scripts/local/foo.py", "scripts/local/*.md") is False
        assert matches_glob("scripts/local/foo.py", "scripts/*.py") is False

    def test_double_star_prefix(self):
        assert matches_glob("docs/api/endpoints.md", "docs/**") is True
        assert matches_glob("docs/README.md", "docs/**") is True
        assert matches_glob("scripts/local/foo.py", "docs/**") is False

    def test_double_star_middle(self):
        assert matches_glob("scripts/local/foo.py", "scripts/**/*.py") is True
        assert matches_glob("scripts/a/b/c.py", "scripts/**/*.py") is True

    def test_root_wildcard(self):
        assert matches_glob("README.md", "*.md") is True
        assert matches_glob("foo.py", "*.md") is False

    def test_nested_wildcard(self):
        assert matches_glob("a/b/c/file.py", "a/**/*.py") is True
        assert matches_glob("a/x/file.py", "a/**/*.py") is True


class TestPathMatchesAny:
    def test_no_patterns(self):
        assert path_matches_any("a.py", []) is False

    def test_matches_first(self):
        assert path_matches_any("a.py", ["a.py", "b.py"]) is True

    def test_matches_second(self):
        assert path_matches_any("b.py", ["a.py", "b.py"]) is True

    def test_glob_and_exact(self):
        assert path_matches_any("docs/README.md", ["docs/**", "README.md"]) is True


# ── Core check_scope ────────────────────────────────────────────────────────────

class TestExactAllowedFilesPass:
    def test_exact_allowed_files_pass(self):
        changed = ["scripts/local/foo.py", "tests/test_foo.py"]
        allowed = ["scripts/local/foo.py", "tests/test_foo.py"]
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is True
        assert pkt["scope_status"] == "clean"
        assert pkt["blockers"] == []

    def test_subset_allowed_is_clean(self):
        """Only files that are in changed are checked against allowed."""
        changed = ["scripts/local/foo.py"]
        allowed = ["scripts/local/foo.py", "tests/test_foo.py"]
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is True
        assert pkt["scope_status"] == "clean"


class TestChangedFileOutsideAllowedFails:
    def test_changed_file_outside_allowed_fails(self):
        changed = ["scripts/local/foo.py", "scripts/local/bar.py"]
        allowed = ["scripts/local/foo.py"]
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is False
        assert pkt["scope_status"] == "violation"
        assert "changed_file_outside_allowed_scope" in pkt["blockers"]
        assert "scripts/local/bar.py" in pkt["out_of_scope_files"]


class TestForbiddenFileTouchedFails:
    def test_forbidden_file_touched_fails(self):
        changed = ["scripts/local/foo.py", ".github/workflows/ci.yml"]
        allowed = ["scripts/local/foo.py", ".github/workflows/ci.yml"]
        forbidden = [".github/workflows/**"]
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is False
        assert pkt["scope_status"] == "violation"
        assert "forbidden_file_touched" in pkt["blockers"]
        assert ".github/workflows/ci.yml" in pkt["forbidden_files_touched"]


class TestBothOutOfScopeAndForbiddenReported:
    def test_both_out_of_scope_and_forbidden_are_reported(self):
        changed = ["engine/foo.py", ".github/workflows/ci.yml", "scripts/local/bar.py"]
        allowed = ["scripts/local/bar.py"]
        forbidden = [".github/workflows/**"]
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is False
        blockers = pkt["blockers"]
        assert "changed_file_outside_allowed_scope" in blockers
        assert "forbidden_file_touched" in blockers
        assert "engine/foo.py" in pkt["out_of_scope_files"]
        assert ".github/workflows/ci.yml" in pkt["forbidden_files_touched"]


class TestGlobAllowedFilesPass:
    def test_glob_allowed_files_pass(self):
        changed = ["docs/readme.md", "docs/api/v1/spec.md"]
        allowed = ["docs/**"]
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is True
        assert pkt["scope_status"] == "clean"


class TestGlobForbiddenFilesFail:
    def test_glob_forbidden_files_fail(self):
        changed = ["scripts/local/foo.py", "engine/core.py"]
        allowed = ["scripts/local/*.py", "engine/core.py"]
        forbidden = ["engine/**"]
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is False
        assert "forbidden_file_touched" in pkt["blockers"]
        assert "engine/core.py" in pkt["forbidden_files_touched"]


class TestEmptyAllowedFilesIsUnknown:
    def test_empty_allowed_files_is_unknown_and_fails(self):
        changed = ["scripts/local/foo.py"]
        allowed = []
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is False
        assert pkt["scope_status"] == "unknown"
        assert "allowed_files_missing" in pkt["blockers"]
        assert "scripts/local/foo.py" in pkt["out_of_scope_files"]


class TestMissingAllowedFilesJsonExits2:
    def test_missing_allowed_files_json_exits_2(self, tmp_path):
        changed_file = tmp_path / "changed.json"
        changed_file.write_text('["a.py"]\n')
        result = subprocess.run(
            [
                sys.executable, "scripts/local/check_pr_scope.py",
                "--changed-files-json", str(changed_file),
                "--allowed-files-json", str(tmp_path / "missing.json"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "not found" in result.stderr


class TestInvalidJsonExits2:
    def test_invalid_json_exits_2(self, tmp_path):
        changed_file = tmp_path / "changed.json"
        changed_file.write_text('["a.py"]\n')
        allowed_file = tmp_path / "allowed.json"
        allowed_file.write_text('not valid json\n')
        result = subprocess.run(
            [
                sys.executable, "scripts/local/check_pr_scope.py",
                "--changed-files-json", str(changed_file),
                "--allowed-files-json", str(allowed_file),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "invalid JSON" in result.stderr


class TestInlineCommaArgsWork:
    def test_inline_comma_args_work(self):
        result = subprocess.run(
            [
                sys.executable, "scripts/local/check_pr_scope.py",
                "--changed-files", "scripts/local/foo.py,tests/test_foo.py",
                "--allowed-files", "scripts/local/foo.py,tests/test_foo.py",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "scope_status=clean" in result.stdout


class TestDuplicatePathsAreDeduplicated:
    def test_duplicate_paths_are_deduplicated(self):
        changed = ["a.py", "b.py", "a.py"]
        allowed = ["a.py", "b.py"]
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["changed_files"] == ["a.py", "b.py"]
        assert pkt["passed"] is True


class TestLeadingDotSlashNormalized:
    def test_leading_dot_slash_is_normalized(self):
        changed = ["./scripts/local/foo.py"]
        allowed = ["scripts/local/foo.py"]
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["passed"] is True
        assert pkt["changed_files"] == ["scripts/local/foo.py"]


class TestOutputPacketHasRequiredFields:
    def test_output_packet_has_required_fields(self):
        changed = ["scripts/local/foo.py"]
        allowed = ["scripts/local/foo.py"]
        forbidden = []
        pkt = check_scope(changed, allowed, forbidden)
        assert pkt["packet_kind"] == PACKET_KIND
        assert pkt["schema_version"] == SCHEMA_VERSION
        assert "generated_at" in pkt
        assert "changed_files" in pkt
        assert "allowed_files" in pkt
        assert "forbidden_files" in pkt
        assert "scope_status" in pkt
        assert "out_of_scope_files" in pkt
        assert "forbidden_files_touched" in pkt
        assert "blockers" in pkt
        assert "passed" in pkt


class TestExitCodes:
    def test_exit_0_for_clean_scope(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, "scripts/local/check_pr_scope.py",
                "--changed-files", "scripts/local/foo.py",
                "--allowed-files", "scripts/local/foo.py",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_exit_1_for_scope_violation(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, "scripts/local/check_pr_scope.py",
                "--changed-files", "scripts/local/foo.py,engine/bar.py",
                "--allowed-files", "scripts/local/foo.py",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_exit_2_for_missing_changed_files(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, "scripts/local/check_pr_scope.py",
                "--allowed-files", "scripts/local/foo.py",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "required" in result.stderr


class TestSafetyGrepNoNetworkOrMutation:
    def test_safety_grep_no_network_or_mutation_calls(self):
        """No requests, urllib, httpx, gh, git push/commit, memory, skill_manage."""
        path = Path(__file__).parent.parent / "scripts" / "local" / "check_pr_scope.py"
        content = path.read_text()

        forbidden = [
            "requests.", "urllib", "httpx",
            "gh pr merge", "gh pr comment", "gh pr create",
            "git push", "git commit",
            "memory", "skill_manage", "fact_store",
            "delegate_task", "cronjob",
            "hermes kanban", "telegram", "send_message",
        ]
        violations = [kw for kw in forbidden if kw in content]
        assert violations == [], f"Found forbidden calls: {violations}"


class TestRenderMarkdown:
    def test_render_markdown_clean(self):
        pkt = check_scope(["a.py"], ["a.py"], [])
        md = render_markdown(pkt)
        assert "clean" in md
        assert pkt["passed"] is True

    def test_render_markdown_violation(self):
        pkt = check_scope(["a.py", "b.py"], ["a.py"], [])
        md = render_markdown(pkt)
        assert "violation" in md
        assert "changed_file_outside_allowed_scope" in md
        assert "b.py" in md

    def test_render_markdown_unknown(self):
        pkt = check_scope(["a.py"], [], [])
        md = render_markdown(pkt)
        assert "unknown" in md
        assert "allowed_files_missing" in md
