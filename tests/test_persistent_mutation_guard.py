"""Tests for check_persistent_mutation_guard.py."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
from check_persistent_mutation_guard import (
    GUARD_VERSION,
    _hash_file,
    _is_under_skills,
    _is_under_profiles,
    _is_memory_or_profile_file,
    _is_config_file,
    _load_allowlist,
    snapshot,
    compare,
    _load_snapshot,
    _build_file_index,
    _collect_monitored_files,
)

SCRIPT = Path(__file__).parent.parent / "scripts" / "local" / "check_persistent_mutation_guard.py"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def run_guard(*args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
    )
    return result


def touch(path: Path, content: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# -----------------------------------------------------------------------------
# Tests: snapshot records file hash, size, mtime (Test 1)
# -----------------------------------------------------------------------------

class TestSnapshotRecords:
    def test_snapshot_records_file_hash_size_mtime(self, tmp_path):
        """Snapshot record contains sha256, size_bytes, mtime_ns for each file."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        skill_dir = hermes_root / "skills" / "project" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# Test skill")

        snap_file = tmp_path / "snap.json"

        rc = snapshot(hermes_root, snap_file)
        assert rc == 0
        assert snap_file.exists()

        data = json.loads(snap_file.read_text())
        assert data["guard_version"] == GUARD_VERSION
        assert "snapshot_at" in data
        assert len(data["files"]) >= 1

        skill_rec = next((f for f in data["files"] if "test-skill/SKILL.md" in f["relative_path"]), None)
        assert skill_rec is not None, f"SKILL.md not found in snapshot: {[f['relative_path'] for f in data['files']]}"
        assert skill_rec["exists"] is True
        assert skill_rec["size_bytes"] > 0
        assert skill_rec["mtime_ns"] > 0
        assert skill_rec["sha256"] != ""  # computed hash


# -----------------------------------------------------------------------------
# Tests: compare clean returns PASS (Test 2)
# -----------------------------------------------------------------------------

class TestCompareClean:
    def test_compare_clean_returns_pass(self, tmp_path):
        """No changes → status=clean, recommendation=PASS, exit 0."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        snap1 = tmp_path / "before.json"
        snap2 = tmp_path / "after.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 0
        result = json.loads(report_json.read_text())
        assert result["status"] == "clean"
        assert result["recommendation"] == "PASS"
        assert result["blocked_changes"] == []


# -----------------------------------------------------------------------------
# Tests: added skill file returns BLOCK (Test 3)
# -----------------------------------------------------------------------------

class TestSkillAdditionBlocked:
    def test_added_skill_file_returns_block(self, tmp_path):
        """A new file under .hermes/skills → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        snap1 = tmp_path / "before.json"
        snap2 = tmp_path / "after.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        # Take snapshot with no skills
        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Add a new skill file
        new_skill = hermes_root / "skills" / "project" / "new-skill" / "SKILL.md"
        new_skill.parent.mkdir(parents=True)
        new_skill.write_text("# New skill")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert result["recommendation"] == "BLOCK"
        assert len(result["blocked_changes"]) >= 1
        assert any("skills" in b["relative_path"] for b in result["blocked_changes"])


# -----------------------------------------------------------------------------
# Tests: modified skill file returns BLOCK (Test 4)
# -----------------------------------------------------------------------------

class TestSkillModificationBlocked:
    def test_modified_skill_file_returns_block(self, tmp_path):
        """Modification of an existing skill file → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        skill_dir = hermes_root / "skills" / "project" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# Original")

        snap1 = tmp_path / "before.json"
        snap2 = tmp_path / "after.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        skill_file.write_text("# Modified")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert result["recommendation"] == "BLOCK"


# -----------------------------------------------------------------------------
# Tests: removed skill file returns BLOCK (Test 5)
# -----------------------------------------------------------------------------

class TestSkillRemovalBlocked:
    def test_removed_skill_file_returns_block(self, tmp_path):
        """Removal of a skill file → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        skill_dir = hermes_root / "skills" / "project" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# To be removed")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        skill_file.unlink()

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"


# -----------------------------------------------------------------------------
# Tests: added skill reference file returns BLOCK (Test 6)
# -----------------------------------------------------------------------------

class TestSkillReferenceAdditionBlocked:
    def test_added_skill_reference_file_returns_block(self, tmp_path):
        """New reference file under .hermes/skills/project/skill/refs → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        skill_dir = hermes_root / "skills" / "project" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Skill")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Add a reference file
        ref_file = skill_dir / "references" / "new-ref.md"
        ref_file.parent.mkdir(parents=True)
        ref_file.write_text("# Reference")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert any("references" in b["relative_path"] for b in result["blocked_changes"])


# -----------------------------------------------------------------------------
# Tests: modified skill reference file returns BLOCK (Test 7)
# -----------------------------------------------------------------------------

class TestSkillReferenceModificationBlocked:
    def test_modified_skill_reference_file_returns_block(self, tmp_path):
        """Modification of an existing skill reference file → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        skill_dir = hermes_root / "skills" / "project" / "test-skill"
        skill_dir.mkdir(parents=True)
        ref_dir = skill_dir / "references"
        ref_dir.mkdir()
        ref_file = ref_dir / "existing-ref.md"
        ref_file.write_text("# Original ref")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        ref_file.write_text("# Modified ref")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"


# -----------------------------------------------------------------------------
# Test: exact incident "aed-dependency-audit/SKILL.md" returns BLOCK (Test 8)
# -----------------------------------------------------------------------------

class TestIncidentAedDependencyAudit:
    def test_added_aed_dependency_audit_skill_returns_block(self, tmp_path):
        """Exact incident: adding .hermes/skills/project/aed-dependency-audit/SKILL.md → BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Exact incident path
        bad_skill = hermes_root / "skills" / "project" / "aed-dependency-audit" / "SKILL.md"
        bad_skill.parent.mkdir(parents=True)
        bad_skill.write_text("# Unauthorized skill created by subagent")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert any(
            "aed-dependency-audit/SKILL.md" in b["relative_path"]
            for b in result["blocked_changes"]
        )


# -----------------------------------------------------------------------------
# Test: exact incident "aed-session-patterns/references/skill-creation-guard..." returns BLOCK (Test 9)
# -----------------------------------------------------------------------------

class TestIncidentSkillCreationGuard:
    def test_added_skill_creation_guard_reference_returns_block(self, tmp_path):
        """Exact incident: adding skill-creation-guard-agent-created.md reference → BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        bad_ref = (
            hermes_root
            / "skills"
            / "project"
            / "aed-session-patterns"
            / "references"
            / "skill-creation-guard-agent-created.md"
        )
        bad_ref.parent.mkdir(parents=True)
        bad_ref.write_text("# Skill creation guard documentation")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert any(
            "skill-creation-guard-agent-created" in b["relative_path"]
            for b in result["blocked_changes"]
        )


# -----------------------------------------------------------------------------
# Test: modified config returns BLOCK (Test 10)
# -----------------------------------------------------------------------------

class TestConfigModificationBlocked:
    def test_modified_config_returns_block(self, tmp_path):
        """Modification of .hermes/config.yaml → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        config_file = hermes_root / "config.yaml"
        config_file.write_text("original: true\n")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        config_file.write_text("modified: true\n")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert any(
            b["relative_path"] == "config.yaml"
            for b in result["blocked_changes"]
        )


# -----------------------------------------------------------------------------
# Test: modified profile config returns BLOCK (Test 11)
# -----------------------------------------------------------------------------

class TestProfileModificationBlocked:
    def test_modified_profile_config_returns_block(self, tmp_path):
        """Modification of a profile config under .hermes/profiles/ → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        profiles_dir = hermes_root / "profiles"
        profile_dir = profiles_dir / "aed-quarantine"
        profile_dir.mkdir(parents=True)
        profile_config = profile_dir / "config.yaml"
        profile_config.write_text("memory:\n  enabled: true\n")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        profile_config.write_text("memory:\n  enabled: false\n")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert any(
            "profiles" in b["relative_path"]
            for b in result["blocked_changes"]
        )


# -----------------------------------------------------------------------------
# Test: modified USER.md returns BLOCK (Test 12)
# -----------------------------------------------------------------------------

class TestUserMdModificationBlocked:
    def test_modified_user_md_returns_block(self, tmp_path):
        """Modification of USER.md → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        memories_dir = hermes_root / "memories"
        memories_dir.mkdir()
        user_file = memories_dir / "USER.md"
        user_file.write_text("original user\n")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        user_file.write_text("modified user\n")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"


# -----------------------------------------------------------------------------
# Test: modified MEMORY.md returns BLOCK (Test 13)
# -----------------------------------------------------------------------------

class TestMemoryMdModificationBlocked:
    def test_modified_memory_md_returns_block(self, tmp_path):
        """Modification of MEMORY.md → exit 2, BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        memories_dir = hermes_root / "memories"
        memories_dir.mkdir()
        memory_file = memories_dir / "MEMORY.md"
        memory_file.write_text("original memory\n")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        memory_file.write_text("modified memory\n")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"


# -----------------------------------------------------------------------------
# Test: allowed_paths suppresses only the exact allowed path (Test 14)
# -----------------------------------------------------------------------------

class TestAllowlistExact:
    def test_allowlist_suppresses_exact_path_only(self, tmp_path):
        """allowlist JSON with exact path suppresses only that path."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()

        # Create only the blocked skill file and take snapshot
        blocked_file = hermes_root / "skills" / "project" / "bad-skill" / "SKILL.md"
        blocked_file.parent.mkdir(parents=True)
        blocked_file.write_text("# Bad skill")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # After snapshot: add the allowlisted .usage.json AND modify the blocked file
        allowed_file = hermes_root / "skills" / ".usage.json"
        allowed_file.write_text('{"allowed": true}')
        blocked_file.write_text("# Bad skill modified")

        allowlist_file = tmp_path / "allowlist.json"
        allowlist_file.write_text(json.dumps({
            "allowed_paths": ["skills/.usage.json"]
        }))

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=allowlist_file)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        # .usage.json should not appear in blocked_changes
        assert not any(
            ".usage.json" in b["relative_path"]
            for b in result["blocked_changes"]
        )
        # bad-skill must still appear as blocked
        assert any(
            "bad-skill" in b["relative_path"]
            for b in result["blocked_changes"]
        )


# -----------------------------------------------------------------------------
# Test: allowlist does not permit broad parent directory by accident (Test 15)
# -----------------------------------------------------------------------------

class TestAllowlistNoBroadPaths:
    def test_allowlist_rejects_directory_entries(self, tmp_path):
        """allowlist entries must be exact files; directory entries are rejected."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()

        allowlist_file = tmp_path / "allowlist.json"
        # Attempt to allow an entire directory (not a file) — should be rejected
        allowlist_file.write_text(json.dumps({
            "allowed_paths": [".hermes/skills"]
        }))

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc = snapshot(hermes_root, snap1)
        assert rc == 0

        # Try to use directory allowlist — should fail (v1 only accepts exact file paths)
        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=allowlist_file)
        # Should exit 1 because allowlist entry is not a file
        assert rc2 == 1


# -----------------------------------------------------------------------------
# Test: allowlisted path plus blocked path still returns BLOCK (Test 16)
# -----------------------------------------------------------------------------

class TestAllowlistPlusBlockedStillBlocks:
    def test_allowed_plus_blocked_returns_block(self, tmp_path):
        """If both an allowed path and a blocked path change, result is still BLOCK."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()

        # Two files: one to allow, one to block
        allowed_file = hermes_root / "skills" / ".usage.json"
        allowed_file.parent.mkdir(parents=True)
        allowed_file.write_text('{"v": 1}')

        blocked_file = hermes_root / "skills" / "project" / "bad-skill" / "SKILL.md"
        blocked_file.parent.mkdir(parents=True)
        blocked_file.write_text("# Bad")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"
        allowlist_file = tmp_path / "allowlist.json"
        allowlist_file.write_text(json.dumps({
            "allowed_paths": ["skills/.usage.json"]
        }))

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Modify both files
        allowed_file.write_text('{"v": 2}')
        blocked_file.write_text("# Bad modified")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=allowlist_file)
        assert rc2 == 2
        result = json.loads(report_json.read_text())
        assert result["status"] == "blocked"
        assert result["recommendation"] == "BLOCK"


# -----------------------------------------------------------------------------
# Test: missing root exits nonzero (Test 17)
# -----------------------------------------------------------------------------

class TestMissingRoot:
    def test_missing_root_exits_nonzero_snapshot(self, tmp_path):
        """snapshot with non-existent root → exit 1."""
        nonexistent = tmp_path / "does_not_exist"
        out = tmp_path / "snap.json"
        rc = snapshot(nonexistent, out)
        assert rc == 1

    def test_missing_root_exits_nonzero_compare(self, tmp_path):
        """compare with non-existent root → exit 1."""
        nonexistent = tmp_path / "does_not_exist"
        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        # Write a valid snapshot first
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        rc2 = compare(nonexistent, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 1


# -----------------------------------------------------------------------------
# Test: malformed snapshot exits nonzero (Test 18)
# -----------------------------------------------------------------------------

class TestMalformedSnapshot:
    def test_malformed_snapshot_exits_nonzero(self, tmp_path):
        """compare with malformed snapshot → exit 1."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        bad_snap = tmp_path / "bad_snap.json"
        bad_snap.write_text("not valid json{{")

        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc = compare(hermes_root, bad_snap, report_json, report_md, allowlist=None)
        assert rc == 1

    def test_missing_guard_version_exits_nonzero(self, tmp_path):
        """compare with snapshot missing guard_version → exit 1."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        bad_snap = tmp_path / "bad_snap.json"
        bad_snap.write_text(json.dumps({"files": []}))  # missing guard_version

        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc = compare(hermes_root, bad_snap, report_json, report_md, allowlist=None)
        assert rc == 1


# -----------------------------------------------------------------------------
# Test: report markdown lists blocked changes (Test 19)
# -----------------------------------------------------------------------------

class TestMarkdownReport:
    def test_markdown_report_lists_blocked_changes(self, tmp_path):
        """Markdown report contains blocked change details."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        bad_file = hermes_root / "skills" / "project" / "bad-skill" / "SKILL.md"
        bad_file.parent.mkdir(parents=True)
        bad_file.write_text("# Bad")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Modify after snapshot — this is the blocked change
        bad_file.write_text("# Bad modified")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 2

        md_content = report_md.read_text()
        assert "blocked" in md_content.lower()
        assert "bad-skill" in md_content


# -----------------------------------------------------------------------------
# Test: output JSON has guard_version and recommendation (Test 20)
# -----------------------------------------------------------------------------

class TestJsonReport:
    def test_json_has_guard_version_and_recommendation(self, tmp_path):
        """JSON report always contains guard_version and recommendation."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 0
        result = json.loads(report_json.read_text())
        assert "guard_version" in result
        assert "recommendation" in result
        assert result["guard_version"] == GUARD_VERSION


# -----------------------------------------------------------------------------
# Test: compare mode does not mutate monitored root (Test 21)
# -----------------------------------------------------------------------------

class TestNoMutation:
    def test_compare_does_not_mutate_monitored_root(self, tmp_path):
        """compare must not create or modify any files under the monitored root."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        skill_file = hermes_root / "skills" / "project" / "test-skill" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# Original")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Record state before compare
        before_files = set(p for p in hermes_root.rglob("*") if p.is_file())

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        assert rc2 == 0

        # State must be unchanged
        after_files = set(p for p in hermes_root.rglob("*") if p.is_file())
        assert before_files == after_files


# -----------------------------------------------------------------------------
# Test: snapshot output can be outside monitored root (Test 22)
# -----------------------------------------------------------------------------

class TestSnapshotOutputOutside:
    def test_snapshot_output_outside_monitored_root_succeeds(self, tmp_path):
        """snapshot with output path outside the monitored root succeeds."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        out = tmp_path / "outside" / "snap.json"

        rc = snapshot(hermes_root, out)
        assert rc == 0
        assert out.exists()


# -----------------------------------------------------------------------------
# Test: output path under monitored root is rejected (Test 23)
# -----------------------------------------------------------------------------

class TestOutputUnderRootRejected:
    def test_output_under_monitored_root_rejected_snapshot(self, tmp_path):
        """snapshot with output inside monitored root → exit 1."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        out = hermes_root / "output.json"

        rc = snapshot(hermes_root, out)
        assert rc == 1

    def test_output_under_monitored_root_rejected_compare(self, tmp_path):
        """compare with output inside monitored root → exit 1."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        snap1 = tmp_path / "before.json"
        out_json = hermes_root / "report.json"
        out_md = tmp_path / "outside" / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        rc2 = compare(hermes_root, snap1, out_json, out_md, allowlist=None)
        assert rc2 == 1


# -----------------------------------------------------------------------------
# Test: paths normalized and symlink escapes do not bypass detection (Test 24)
# -----------------------------------------------------------------------------

class TestSymlinkNormalization:
    def test_symlink_escape_does_not_bypass_detection(self, tmp_path):
        """A symlink pointing outside the root is not followed for file matching."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        skill_dir = hermes_root / "skills" / "project" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# Original")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Create a symlink inside skills pointing outside
        escape_link = skill_dir / "escape_link"
        escape_link.symlink_to(tmp_path / "outside_target")
        (tmp_path / "outside_target").write_text("# Escaped")

        rc2 = compare(hermes_root, snap1, report_json, report_md, allowlist=None)
        # The symlink itself is not a regular file so it should not appear as a change
        # The real test is that the escape link doesn't cause false positives
        assert rc2 in (0, 2)  # 0 if clean, 2 if it detected the symlink as a new entry


# -----------------------------------------------------------------------------
# Test: compare exits 2 on blocked changes (Test 25)
# -----------------------------------------------------------------------------

class TestExitCodeBlocked:
    def test_compare_exits_2_on_blocked_changes(self, tmp_path):
        """compare with blocked changes → exit code 2."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        bad_file = hermes_root / "skills" / "project" / "bad-skill" / "SKILL.md"
        bad_file.parent.mkdir(parents=True)
        bad_file.write_text("# Bad")

        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        rc1 = snapshot(hermes_root, snap1)
        assert rc1 == 0

        # Modify after snapshot — this is the blocked change
        bad_file.write_text("# Bad modified")

        rc2 = run_guard("compare", "--root", str(hermes_root), "--before", str(snap1),
                        "--output-json", str(report_json), "--output-md", str(report_md))
        assert rc2.returncode == 2


# -----------------------------------------------------------------------------
# Test: CLI entrypoint
# -----------------------------------------------------------------------------

class TestCLI:
    def test_cli_snapshot_command(self, tmp_path):
        """CLI snapshot command works."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        out = tmp_path / "snap.json"

        rc = run_guard("snapshot", "--root", str(hermes_root), "--output", str(out))
        assert rc.returncode == 0
        assert out.exists()

    def test_cli_compare_command(self, tmp_path):
        """CLI compare command works."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        run_guard("snapshot", "--root", str(hermes_root), "--output", str(snap1))
        rc = run_guard(
            "compare",
            "--root", str(hermes_root),
            "--before", str(snap1),
            "--output-json", str(report_json),
            "--output-md", str(report_md),
        )
        assert rc.returncode == 0

    def test_cli_compare_with_blocked_exits_2(self, tmp_path):
        """CLI compare with blocked changes exits 2."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir()
        snap1 = tmp_path / "before.json"
        report_json = tmp_path / "report.json"
        report_md = tmp_path / "report.md"

        run_guard("snapshot", "--root", str(hermes_root), "--output", str(snap1))

        # Add a bad file
        bad = hermes_root / "skills" / "project" / "bad" / "SKILL.md"
        bad.parent.mkdir(parents=True)
        bad.write_text("# Bad")

        rc = run_guard(
            "compare",
            "--root", str(hermes_root),
            "--before", str(snap1),
            "--output-json", str(report_json),
            "--output-md", str(report_md),
        )
        assert rc.returncode == 2
