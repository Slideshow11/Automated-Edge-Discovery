"""Tests for scripts/local/check_merge_authorization.py and scripts/local/build_merge_ready_packet.py."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
from build_merge_ready_packet import build_packet, serialize_packet, render_markdown, PACKET_KIND as BUILD_PACKET_KIND
from build_merge_ready_packet import (
    build_review_evidence_packet,
    serialize_review_evidence_packet,
    render_review_evidence_markdown,
    REVIEW_EVIDENCE_KIND,
    ALLOWED_REVIEW_SOURCES,
    ALLOWED_REVIEW_STATUSES,
)
from check_merge_authorization import (
    check_packet_kind,
    check_not_expired,
    check_phrase_match,
    check_head_sha_match,
    check_no_blockers,
    check_recommendation_merge,
    check_required_fields,
    check_authorization_sha_match,
    load_packet,
    run_all_checks,
    PACKET_KIND,
    load_review_evidence,
    check_review_evidence,
    extract_sha_from_phrase,
    SHA_FULL_PATTERN,
)
import check_merge_authorization


# ── Helpers ─────────────────────────────────────────────────────────────────────

def make_valid_packet(overrides: dict | None = None) -> dict:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=1)
    base = {
        "packet_kind": PACKET_KIND,
        "pr_number": 193,
        "pr_url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
        "base_branch": "main",
        "head_sha": "af386e4c75341a2a6e7a6f68b680844de5cef1df",
        "mergeable": True,
        "ci_status": "green",
        "codex_status": "reviewed_clean",
        "reviewer_status": "approved",
        "changed_files": ["docs/README.md", "scripts/local/foo.py"],
        "allowed_files": ["docs/README.md", "scripts/local/foo.py"],
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "required_authorization_phrase": "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df",
        "blockers": [],
        "recommendation": "merge",
    }
    if overrides:
        base.update(overrides)
    return base


# ── Tests for check_merge_authorization.py ──────────────────────────────────

class TestPacketKind:
    def test_valid_kind_passes(self):
        packet = make_valid_packet()
        ok, _ = check_packet_kind(packet)
        assert ok is True

    def test_wrong_kind_fails(self):
        packet = make_valid_packet({"packet_kind": "aed.wrong.v1"})
        ok, msg = check_packet_kind(packet)
        assert ok is False
        assert "aed.wrong.v1" in msg

    def test_missing_kind_fails(self):
        packet = make_valid_packet()
        del packet["packet_kind"]
        ok, msg = check_packet_kind(packet)
        assert ok is False


class TestNotExpired:
    def test_fresh_packet_passes(self):
        packet = make_valid_packet()
        ok, _ = check_not_expired(packet)
        assert ok is True

    def test_expired_packet_fails(self):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        packet = make_valid_packet({"expires_at": past.strftime("%Y-%m-%dT%H:%M:%S+00:00")})
        ok, msg = check_not_expired(packet)
        assert ok is False
        assert "expired" in msg.lower()

    def test_missing_expires_at_fails(self):
        packet = make_valid_packet()
        del packet["expires_at"]
        ok, msg = check_not_expired(packet)
        assert ok is False


class TestPhraseMatch:
    def test_exact_phrase_passes(self):
        packet = make_valid_packet()
        phrase = "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"
        ok, _ = check_phrase_match(packet, phrase)
        assert ok is True

    def test_wrong_phrase_fails(self):
        packet = make_valid_packet()
        ok, msg = check_phrase_match(packet, "I confirm merge")
        assert ok is False
        assert "phrase mismatch" in msg

    def test_partial_phrase_fails(self):
        packet = make_valid_packet()
        ok, msg = check_phrase_match(packet, "I confirm")
        assert ok is False

    def test_phrase_with_wrong_sha_fails(self):
        packet = make_valid_packet()
        ok, msg = check_phrase_match(packet, "I confirm merge PR #193 at 0000000000000000000000000000000000000000")
        assert ok is False

    def test_phrase_with_wrong_pr_number_fails(self):
        packet = make_valid_packet({"pr_number": 42, "required_authorization_phrase": "I confirm merge PR #42 at abc123"})
        ok, msg = check_phrase_match(packet, "I confirm merge PR #193 at abc123")
        assert ok is False

    def test_case_sensitive(self):
        packet = make_valid_packet()
        ok, _ = check_phrase_match(packet, "i confirm merge pr #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df")
        assert ok is False


class TestHeadShaMatch:
    def test_no_current_head_passes(self):
        packet = make_valid_packet()
        ok, _ = check_head_sha_match(packet, None)
        assert ok is True

    def test_matching_head_passes(self):
        packet = make_valid_packet()
        ok, _ = check_head_sha_match(packet, "af386e4c75341a2a6e7a6f68b680844de5cef1df")
        assert ok is True

    def test_mismatched_head_fails(self):
        packet = make_valid_packet()
        ok, msg = check_head_sha_match(packet, "0000000000000000000000000000000000000000")
        assert ok is False
        assert "HEAD mismatch" in msg

    def test_mismatched_head_short_sha(self):
        packet = make_valid_packet({"head_sha": "af386e4"})
        ok, _ = check_head_sha_match(packet, "af386e4")
        assert ok is True


class TestNoBlockers:
    def test_empty_blockers_passes(self):
        packet = make_valid_packet({"blockers": []})
        ok, _ = check_no_blockers(packet)
        assert ok is True

    def test_blockers_fails(self):
        packet = make_valid_packet({"blockers": ["recommendation is 'patch', not 'merge'"]})
        ok, msg = check_no_blockers(packet)
        assert ok is False
        assert "blockers present" in msg


class TestRecommendation:
    def test_merge_passes(self):
        packet = make_valid_packet({"recommendation": "merge"})
        ok, _ = check_recommendation_merge(packet)
        assert ok is True

    def test_patch_fails(self):
        packet = make_valid_packet({"recommendation": "patch"})
        ok, msg = check_recommendation_merge(packet)
        assert ok is False
        assert "not 'merge'" in msg

    def test_block_fails(self):
        packet = make_valid_packet({"recommendation": "block"})
        ok, _ = check_recommendation_merge(packet)
        assert ok is False

    def test_wait_fails(self):
        packet = make_valid_packet({"recommendation": "wait"})
        ok, _ = check_recommendation_merge(packet)
        assert ok is False


class TestRequiredFields:
    def test_all_present_passes(self):
        packet = make_valid_packet()
        ok, _ = check_required_fields(packet)
        assert ok is True

    def test_missing_field_fails(self):
        packet = make_valid_packet()
        del packet["ci_status"]
        ok, msg = check_required_fields(packet)
        assert ok is False
        assert "missing" in msg.lower()


# ── Tests for build_merge_ready_packet.py ───────────────────────────────────

class TestBuildPacket:
    def test_packet_kind(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="af386e4c75341a2a6e7a6f68b680844de5cef1df",
            mergeable=True, ci_status="green", codex_status="reviewed_clean",
            reviewer_status="approved", changed_files=["a.md"], allowed_files=["a.md"],
            recommendation="merge",
        )
        assert packet["packet_kind"] == "aed.merge_ready.v1"

    def test_required_phrase_format(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="af386e4c75341a2a6e7a6f68b680844de5cef1df",
            mergeable=True, ci_status="green", codex_status="reviewed_clean",
            reviewer_status="approved", changed_files=[], allowed_files=[],
            recommendation="merge",
        )
        phrase = packet["required_authorization_phrase"]
        assert phrase.startswith("I confirm merge PR #193 at ")
        assert "af386e4c75341a2a6e7a6f68b680844de5cef1df" in phrase

    def test_merge_recommendation_no_blockers(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="abc123", mergeable=True,
            ci_status="green", codex_status="reviewed_clean", reviewer_status="approved",
            changed_files=[], allowed_files=[], recommendation="merge",
        )
        assert packet["recommendation"] == "merge"
        assert packet["blockers"] == []

    def test_patch_recommendation_adds_blocker(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="abc123", mergeable=True,
            ci_status="green", codex_status="reviewed_clean", reviewer_status="approved",
            changed_files=[], allowed_files=[], recommendation="patch",
        )
        assert packet["recommendation"] == "patch"
        assert len(packet["blockers"]) == 1
        assert "not 'merge'" in packet["blockers"][0]

    def test_block_recommendation_adds_blocker(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="abc123", mergeable=True,
            ci_status="green", codex_status="reviewed_clean", reviewer_status="approved",
            changed_files=[], allowed_files=[], recommendation="block",
        )
        assert packet["blockers"] == ["recommendation is 'block', not 'merge'"]

    def test_expires_at_set(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="abc123", mergeable=True,
            ci_status="green", codex_status="reviewed_clean", reviewer_status="approved",
            changed_files=[], allowed_files=[], recommendation="merge",
        )
        assert "expires_at" in packet
        assert "generated_at" in packet


class TestSerializePacket:
    def test_deterministic(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="abc123", mergeable=True,
            ci_status="green", codex_status="reviewed_clean", reviewer_status="approved",
            changed_files=["a.md"], allowed_files=["a.md"], recommendation="merge",
        )
        s1 = serialize_packet(packet)
        s2 = serialize_packet(packet)
        assert s1 == s2

    def test_contains_required_fields(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="abc123", mergeable=True,
            ci_status="green", codex_status="reviewed_clean", reviewer_status="approved",
            changed_files=[], allowed_files=[], recommendation="merge",
        )
        parsed = json.loads(serialize_packet(packet))
        for field in ["packet_kind", "pr_number", "head_sha", "recommendation", "required_authorization_phrase", "blockers"]:
            assert field in parsed


class TestRenderMarkdown:
    def test_contains_phrase(self):
        packet = build_packet(
            pr_number=193, pr_url="https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193",
            base_branch="main", head_sha="abc123", mergeable=True,
            ci_status="green", codex_status="reviewed_clean", reviewer_status="approved",
            changed_files=[], allowed_files=[], recommendation="merge",
        )
        md = render_markdown(packet)
        assert "I confirm merge PR #193 at abc123" in md

    def test_contains_packet_kind(self):
        packet = make_valid_packet()
        md = render_markdown(packet)
        assert "aed.merge_ready.v1" in md

    def test_no_blockers_section_present(self):
        packet = make_valid_packet()
        md = render_markdown(packet)
        assert "Blockers" in md


class TestLoadPacket:
    def test_loads_valid_json(self, tmp_path):
        packet = make_valid_packet()
        path = tmp_path / "packet.json"
        path.write_text(json.dumps(packet))
        loaded, _ = load_packet(str(path))
        assert loaded is not None
        assert loaded["pr_number"] == 193

    def test_missing_file_fails(self, tmp_path):
        loaded, err = load_packet(str(tmp_path / "nonexistent.json"))
        assert loaded is None
        assert "not found" in err

    def test_invalid_json_fails(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        loaded, err = load_packet(str(path))
        assert loaded is None
        assert "invalid JSON" in err


class TestRunAllChecks:
    def test_all_pass_returns_true(self, tmp_path):
        packet = make_valid_packet()
        path = tmp_path / "packet.json"
        path.write_text(json.dumps(packet))
        phrase = "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"
        results = run_all_checks(packet, phrase, None)
        assert all(passed for _, passed, _ in results)

    def test_wrong_phrase_fails(self, tmp_path):
        packet = make_valid_packet()
        results = run_all_checks(packet, "wrong phrase", None)
        phrase_check = [r for r in results if r[0] == "phrase_match"]
        assert len(phrase_check) == 1
        _, passed, _ = phrase_check[0]
        assert passed is False


class TestCLI:
    def test_valid_packet_and_phrase_exits_0(self, tmp_path):
        packet = make_valid_packet()
        p_json = tmp_path / "packet.json"
        p_json.write_text(json.dumps(packet))
        phrase = "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"
        result = check_merge_authorization.main(["--packet", str(p_json), "--phrase", phrase])
        assert result == 0

    def test_wrong_phrase_exits_1(self, tmp_path):
        packet = make_valid_packet()
        p_json = tmp_path / "packet.json"
        p_json.write_text(json.dumps(packet))
        result = check_merge_authorization.main(["--packet", str(p_json), "--phrase", "wrong phrase"])
        assert result == 1

    def test_expired_packet_exits_1(self, tmp_path):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        packet = make_valid_packet({"expires_at": past.strftime("%Y-%m-%dT%H:%M:%S+00:00")})
        p_json = tmp_path / "packet.json"
        p_json.write_text(json.dumps(packet))
        phrase = "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"
        result = check_merge_authorization.main(["--packet", str(p_json), "--phrase", phrase])
        assert result == 1

    def test_missing_packet_exits_1(self, tmp_path):
        result = check_merge_authorization.main(["--packet", str(tmp_path / "nonexistent.json"), "--phrase", "test"])
        assert result == 1

    def test_head_mismatch_exits_1(self, tmp_path):
        packet = make_valid_packet()
        p_json = tmp_path / "packet.json"
        p_json.write_text(json.dumps(packet))
        phrase = "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"
        result = check_merge_authorization.main([
            "--packet", str(p_json), "--phrase", phrase,
            "--current-head", "0000000000000000000000000000000000000000",
        ])
        assert result == 1

    def test_blockers_present_exits_1(self, tmp_path):
        packet = make_valid_packet({"blockers": ["some blocker"]})
        p_json = tmp_path / "packet.json"
        p_json.write_text(json.dumps(packet))
        phrase = "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"
        result = check_merge_authorization.main(["--packet", str(p_json), "--phrase", phrase])
        assert result == 1

    def test_patch_recommendation_exits_1(self, tmp_path):
        packet = make_valid_packet({"recommendation": "patch"})
        p_json = tmp_path / "packet.json"
        p_json.write_text(json.dumps(packet))
        phrase = "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"
        result = check_merge_authorization.main(["--packet", str(p_json), "--phrase", phrase])
        assert result == 1


# ── No-mutation audit ─────────────────────────────────────────────────────────

class TestNoMutation:
    def test_build_script_no_gh_pr_merge(self):
        path = Path(__file__).parent.parent / "scripts" / "local" / "build_merge_ready_packet.py"
        content = path.read_text()
        # Skip f-strings (command template strings, not actual calls)
        src_lines = content.split("\n")
        lines_with_phrase = [
            l for l in src_lines
            if "gh pr merge" in l
            and not l.strip().startswith("f\"")
            and not l.strip().startswith("f'")
            and "help=" not in l
        ]
        assert len(lines_with_phrase) == 0, f"Found 'gh pr merge' in lines: {[l.strip() for l in lines_with_phrase]}"

    def test_build_script_no_network(self):
        path = Path(__file__).parent.parent / "scripts" / "local" / "build_merge_ready_packet.py"
        content = path.read_text()
        assert "urllib.request" not in content
        assert "requests.get" not in content
        assert "requests.post" not in content
        assert "httpx" not in content

    def test_check_script_no_gh_pr_merge(self):
        path = Path(__file__).parent.parent / "scripts" / "local" / "check_merge_authorization.py"
        content = path.read_text()
        # Check for actual subprocess calls, not docstrings mentioning the phrase
        assert '"gh", "pr", "merge"' not in content
        assert "['gh', 'pr', 'merge']" not in content
        # Also check docstring doesn't contain the substring in a dangerous context
        lines_with_phrase = [l for l in content.split('\n') if 'gh pr merge' in l and 'subprocess' in l]
        assert len(lines_with_phrase) == 0

    def test_check_script_no_network(self):
        path = Path(__file__).parent.parent / "scripts" / "local" / "check_merge_authorization.py"
        content = path.read_text()
        assert "urllib.request" not in content
        assert "httpx" not in content
        assert "requests.post" not in content

    def test_no_hermes_kanban(self):
        for script in ["build_merge_ready_packet.py", "check_merge_authorization.py"]:
            path = Path(__file__).parent.parent / "scripts" / "local" / script
            content = path.read_text()
            assert "hermes kanban" not in content

    def test_no_git_push_or_commit(self):
        for script in ["build_merge_ready_packet.py", "check_merge_authorization.py"]:
            path = Path(__file__).parent.parent / "scripts" / "local" / script
            content = path.read_text()
            assert '"git", "push"' not in content
            assert "'git', 'push'" not in content
            assert '"git", "commit"' not in content
            assert "'git', 'commit'" not in content

    def test_no_memory_update(self):
        for script in ["build_merge_ready_packet.py", "check_merge_authorization.py"]:
            path = Path(__file__).parent.parent / "scripts" / "local" / script
            content = path.read_text()
            assert "memory.update" not in content
            assert "fact_store" not in content

    def test_no_skill_manage(self):
        for script in ["build_merge_ready_packet.py", "check_merge_authorization.py"]:
            path = Path(__file__).parent.parent / "scripts" / "local" / script
            content = path.read_text()
            assert "skill_manage" not in content


# ── Review Evidence Packet tests ──────────────────────────────────────────────

HEAD = "abc123" + "0" * 32  # 40-char
REPO_OWNER = "Slideshow11"
REPO_NAME = "Automated-Edge-Discovery"
PR_NUM = 207


class TestBuildReviewEvidencePacket:
    """Tests for build_review_evidence_packet()."""

    def test_exact_head_github_codex_clean_passes(self):
        """Test 1: exact-head GitHub Codex clean evidence passes."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["merge_allowed"] is True
        assert packet["review_is_stale"] is False
        assert packet["review_source"] == "github_codex"
        assert packet["review_status"] == "clean"

    def test_stale_github_codex_blocks_merge(self):
        """Test 2: stale GitHub Codex evidence blocks merge."""
        old_head = "deadbeef" + "1" * 32
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=old_head,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["review_is_stale"] is True
        assert packet["merge_allowed"] is False

    def test_exact_head_codex_cli_fallback_clean_passes(self):
        """Test 3: exact-head Codex CLI fallback clean evidence passes."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="codex_cli_fallback", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["merge_allowed"] is True
        assert packet["review_source"] == "codex_cli_fallback"

    def test_stale_codex_cli_fallback_blocks_merge(self):
        """Test 4: stale Codex CLI fallback evidence blocks merge."""
        old_head = "cafebabe" + "2" * 32
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=old_head,
            review_source="codex_cli_fallback", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["review_is_stale"] is True
        assert packet["merge_allowed"] is False

    def test_missing_review_blocks_merge(self):
        """Test 5: missing review evidence blocks merge."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="none", review_status="unknown",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["merge_allowed"] is False

    def test_pending_review_blocks_merge(self):
        """Test 6: pending review evidence blocks merge."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="pending",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["merge_allowed"] is False

    def test_suggestions_review_blocks_merge(self):
        """Test 7: suggestions review evidence blocks merge."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="reviewer", review_status="suggestions",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["merge_allowed"] is False

    def test_ci_red_blocks_merge_even_with_clean_review(self):
        """Test 8: CI red blocks merge even with clean review."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="red", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["ci_all_green"] is False
        assert packet["merge_allowed"] is False

    def test_scope_dirty_blocks_merge_even_with_clean_review(self):
        """Test 9: scope dirty blocks merge even with clean review."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["engine/foo.py"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["scope_status"] == "dirty"
        assert packet["merge_allowed"] is False

    def test_changed_file_outside_allowed_blocks_merge(self):
        """Test 10: changed file outside allowed_files blocks merge."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green",
            changed_files=["scripts/local/pr_gate_controller.py"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["scope_status"] == "dirty"
        assert packet["merge_allowed"] is False

    def test_recommended_merge_command_includes_match_head_commit(self):
        """Test 11: recommended_merge_command includes --match-head-commit and full SHA."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        cmd = packet["recommended_merge_command"]
        assert "--match-head-commit" in cmd
        assert HEAD in cmd
        assert f"gh pr merge {PR_NUM}" in cmd

    def test_review_source_none_blocks_merge(self):
        """Test 5b: review_source='none' blocks merge."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="none", review_status="unknown",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert "missing review evidence" in packet["blockers_or_uncertainty"][0]
        assert packet["merge_allowed"] is False

    def test_review_status_missing_blocks_merge(self):
        """Test 5c: review_status='missing' blocks merge."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="reviewer", review_status="missing",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["merge_allowed"] is False

    def test_all_required_fields_present(self):
        """Test: review evidence packet has all required fields."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        for field in [
            "packet_kind", "schema_version", "generated_at",
            "repo_owner", "repo_name", "pr_number",
            "current_head_sha", "reviewed_head_sha",
            "review_source", "review_status", "review_is_stale",
            "ci_status", "ci_required_jobs", "ci_all_green",
            "changed_files", "allowed_files", "scope_status",
            "mergeable", "merge_allowed", "blockers_or_uncertainty",
            "recommended_merge_command",
        ]:
            assert field in packet, f"Missing field: {field}"

    def test_ci_required_jobs_default(self):
        """Test: default ci_required_jobs includes all required jobs."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            changed_files=[], allowed_files=[], mergeable=True,
        )
        for job in ["test", "validator", "governance-validators", "pr-gate-live-smoke"]:
            assert job in packet["ci_required_jobs"]


class TestCheckReviewEvidence:
    """Tests for check_review_evidence() function."""

    def test_accepts_exact_head_clean_evidence(self):
        """Test 15: check_review_evidence accepts exact-head clean evidence."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        results = check_review_evidence(packet, auth_head_sha=HEAD, current_head=HEAD)
        assert all(passed for _, passed, _ in results), f"Failed: {[r for r in results if not r[1]]}"

    def test_rejects_stale_evidence(self):
        """Test 14: check_review_evidence rejects stale evidence."""
        old_head = "deadbeef" + "1" * 32
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=old_head,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        results = check_review_evidence(packet, auth_head_sha=HEAD)
        stale_check = [r for r in results if r[0] == "review_not_stale"]
        assert len(stale_check) == 1
        assert stale_check[0][1] is False

    def test_rejects_merge_allowed_false_when_packet_disagrees(self):
        """Test 14b: check_review_evidence rejects when packet claims merge_allowed=True but facts say False.
        
        Note: when build_review_evidence_packet legitimately produces merge_allowed=False (e.g. review_source=none),
        check_review_evidence passes because packet and recomputed agree. The rejection is for forged packets
        that claim merge_allowed=True despite missing evidence.
        """
        # Forge: packet claims merge_allowed=True but review_source=none (missing)
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": HEAD,
            "reviewed_head_sha": HEAD,
            "review_source": "none",   # missing evidence
            "review_status": "clean",
            "review_is_stale": False,
            "merge_allowed": True,     # FORGED - should be False
            "ci_all_green": True,
            "scope_status": "clean",
            "blockers_or_uncertainty": [],
        }
        results = check_review_evidence(packet, auth_head_sha=HEAD)
        # Should detect that review_source=none is missing (fails review_source_not_none check)
        # AND that actual_merge_allowed=False (fails merge_allowed_accurate check)
        source_check = next((r for r in results if r[0] == "review_source_not_none"), None)
        assert source_check is not None and source_check[1] is False,             f"Expected review_source_not_none to fail: {results}"

    def test_rejects_ci_not_all_green(self):
        """Test 8b: check_review_evidence rejects when ci_all_green=False."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="red", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        results = check_review_evidence(packet)
        ci_checks = [r for r in results if r[0] == "ci_all_green"]
        assert len(ci_checks) == 1
        assert ci_checks[0][1] is False

    def test_rejects_scope_not_clean(self):
        """Test 9b: check_review_evidence rejects when scope_status != clean."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["engine/foo.py"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        results = check_review_evidence(packet)
        scope_checks = [r for r in results if r[0] == "scope_clean"]
        assert len(scope_checks) == 1
        assert scope_checks[0][1] is False

    def test_rejects_review_status_not_clean(self):
        """Test 7b: check_review_evidence rejects when review_status != clean."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="pending",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        results = check_review_evidence(packet)
        status_checks = [r for r in results if r[0] == "review_status_clean"]
        assert len(status_checks) == 1
        assert status_checks[0][1] is False


class TestReviewEvidenceSerialize:
    """Tests for serialize_review_evidence_packet and render_review_evidence_markdown."""

    def test_serialize_deterministic(self):
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        s1 = serialize_review_evidence_packet(packet)
        s2 = serialize_review_evidence_packet(packet)
        assert s1 == s2

    def test_render_markdown_contains_key_fields(self):
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        md = render_review_evidence_markdown(packet)
        assert "REVIEW EVIDENCE PACKET" in md
        assert HEAD in md
        assert "github_codex" in md
        assert "clean" in md
        assert "--match-head-commit" in md


class TestLoadReviewEvidence:
    """Tests for load_review_evidence()."""

    def test_loads_valid_packet(self, tmp_path):
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        path = tmp_path / "review_evidence.json"
        path.write_text(json.dumps(packet))
        loaded, err = load_review_evidence(str(path))
        assert loaded is not None
        assert err == ""
        assert loaded["merge_allowed"] is True

    def test_missing_file_fails(self, tmp_path):
        loaded, err = load_review_evidence(str(tmp_path / "nonexistent.json"))
        assert loaded is None
        assert "not found" in err

    def test_wrong_kind_fails(self, tmp_path):
        path = tmp_path / "wrong_kind.json"
        path.write_text(json.dumps({"packet_kind": "aed.wrong.v1"}))
        loaded, err = load_review_evidence(str(path))
        assert loaded is None
        assert "aed.wrong.v1" in err


class TestReviewEvidenceCLI:
    """Test CLI with --review-evidence argument."""

    def test_cli_with_review_evidence_exits_0(self, tmp_path):
        # Build review evidence packet
        rev_packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev_packet))

        # Build MERGE_READY_PACKET
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=1)
        merge_packet = {
            "packet_kind": PACKET_KIND,
            "pr_number": PR_NUM,
            "pr_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/{PR_NUM}",
            "base_branch": "main",
            "head_sha": HEAD,
            "mergeable": True,
            "ci_status": "green",
            "codex_status": "reviewed_clean",
            "reviewer_status": "approved",
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "required_authorization_phrase": f"I confirm merge PR #{PR_NUM} at {HEAD}",
            "blockers": [],
            "recommendation": "merge",
        }
        packet_path = tmp_path / "MERGE_READY_PACKET.json"
        packet_path.write_text(json.dumps(merge_packet))

        phrase = f"I confirm merge PR #{PR_NUM} at {HEAD}"
        result = check_merge_authorization.main([
            "--packet", str(packet_path),
            "--phrase", phrase,
            "--current-head", HEAD,
            "--review-evidence", str(rev_path),
        ])
        assert result == 0

    def test_cli_with_stale_review_evidence_exits_1(self, tmp_path):
        old_head = "deadbeef" + "1" * 32
        rev_packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=PR_NUM,
            current_head_sha=HEAD, reviewed_head_sha=old_head,
            review_source="github_codex", review_status="clean",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev_packet))

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=1)
        merge_packet = {
            "packet_kind": PACKET_KIND,
            "pr_number": PR_NUM,
            "pr_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/{PR_NUM}",
            "base_branch": "main",
            "head_sha": HEAD,
            "mergeable": True,
            "ci_status": "green",
            "codex_status": "reviewed_clean",
            "reviewer_status": "approved",
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "required_authorization_phrase": f"I confirm merge PR #{PR_NUM} at {HEAD}",
            "blockers": [],
            "recommendation": "merge",
        }
        packet_path = tmp_path / "MERGE_READY_PACKET.json"
        packet_path.write_text(json.dumps(merge_packet))

        phrase = f"I confirm merge PR #{PR_NUM} at {HEAD}"
        result = check_merge_authorization.main([
            "--packet", str(packet_path),
            "--phrase", phrase,
            "--current-head", HEAD,
            "--review-evidence", str(rev_path),
        ])
        assert result == 1


class TestBackwardCompatibility:
    """Test 16: backward compatibility — existing merge-ready packet still works."""

    def test_existing_packet_without_review_evidence_still_works(self):
        """Without --review-evidence arg, existing behavior is preserved."""
        # This is tested by all the existing tests remaining passing.
        # This class just documents the requirement.


# ── PATCH correctness tests ─────────────────────────────────────────────────

REPO_OWNER = "Slideshow11"
REPO_NAME = "Automated-Edge-Discovery"
HEAD = "abc123" + "0" * 34  # 40-char SHA constant


class TestPatchFixesBuildPacket:
    """Tests for PATCH-1 in build_merge_ready_packet.py."""

    def test_build_review_evidence_blocks_unavailable_reviewer(self):
        """reviewer + unavailable => merge_allowed=false."""
        packet = build_review_evidence_packet(
            repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=207,
            current_head_sha=HEAD, reviewed_head_sha=HEAD,
            review_source="reviewer", review_status="unavailable",
            ci_status="green", changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"], mergeable=True,
        )
        assert packet["merge_allowed"] is False
        assert any("unavailable" in b or "not 'clean'" in b for b in packet["blockers_or_uncertainty"]), \
            f"Expected blocker for unavailable status: {packet['blockers_or_uncertainty']}"

    def test_build_review_evidence_requires_clean_status_for_all_sources(self):
        """reviewer/github_codex/codex_cli_fallback all require review_status=clean."""
        for source in ("github_codex", "codex_cli_fallback", "reviewer"):
            for bad_status in ("unavailable", "pending", "suggestions", "stale", "unknown", "missing"):
                packet = build_review_evidence_packet(
                    repo_owner=REPO_OWNER, repo_name=REPO_NAME, pr_number=207,
                    current_head_sha=HEAD, reviewed_head_sha=HEAD,
                    review_source=source, review_status=bad_status,
                    ci_status="green", changed_files=["docs/README.md"],
                    allowed_files=["docs/README.md"], mergeable=True,
                )
                assert packet["merge_allowed"] is False, \
                    f"{source}/{bad_status}: expected merge_allowed=False, got {packet['merge_allowed']}"
                assert any("not 'clean'" in b for b in packet["blockers_or_uncertainty"]), \
                    f"{source}/{bad_status}: expected 'not clean' blocker: {packet['blockers_or_uncertainty']}"


class TestPatchFixesAuthorization:
    """Tests for PATCH-2 / PATCH-3 correctness fixes in check_merge_authorization.py."""

    def test_authorization_recomputes_staleness(self, tmp_path):
        """Packet claims review_is_stale=false but reviewed_head_sha != current_head_sha => reject."""
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": "a" * 40,
            "reviewed_head_sha": "b" * 40,  # different = stale
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,  # packet LIES
            "merge_allowed": True,     # packet LIES
            "ci_all_green": True,
            "scope_status": "clean",
            "blockers_or_uncertainty": [],
        }
        checks = check_review_evidence(packet, auth_head_sha="a" * 40)
        stale_check = next((c for c in checks if c[0] == "review_not_stale"), None)
        assert stale_check is not None and stale_check[1] is False, \
            f"Expected staleness detection to fail: {checks}"

    def test_authorization_rejects_forged_merge_allowed(self, tmp_path):
        """review_source=none with merge_allowed=True => reject even if review_status=clean."""
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": "a" * 40,
            "reviewed_head_sha": "a" * 40,
            "review_source": "none",   # missing source
            "review_status": "clean",
            "review_is_stale": False,
            "merge_allowed": True,     # forged
            "ci_all_green": True,
            "scope_status": "clean",
            "blockers_or_uncertainty": [],
        }
        checks = check_review_evidence(packet, auth_head_sha="a" * 40)
        source_check = next((c for c in checks if c[0] == "review_source_not_none"), None)
        assert source_check is not None and source_check[1] is False, \
            f"Expected source check to fail: {checks}"

    def test_authorization_rejects_packet_boolean_disagreement(self, tmp_path):
        """Packet merge_allowed=True but recomputed facts say false => reject."""
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": "a" * 40,
            "reviewed_head_sha": "a" * 40,
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,
            "merge_allowed": True,
            "ci_all_green": True,
            "scope_status": "clean",
            "mergeable": True,
            "blockers_or_uncertainty": [],
        }
        # Exact head + clean review + green CI + mergeable should still work
        checks = check_review_evidence(packet, auth_head_sha="a" * 40)
        merge_check = next((c for c in checks if c[0] == "merge_allowed_accurate"), None)
        assert merge_check is not None and merge_check[1] is True, f"Expected pass: {checks}"

        # Now corrupt: ci_all_green=False but merge_allowed=True in packet
        packet["ci_all_green"] = False
        packet["merge_allowed"] = True
        checks = check_review_evidence(packet, auth_head_sha="a" * 40)
        merge_check = next((c for c in checks if c[0] == "merge_allowed_accurate"), None)
        assert merge_check is not None and merge_check[1] is False, \
            f"Expected merge_allowed disagreement to fail: {checks}"

    def test_authorization_rejects_review_evidence_for_different_head(self, tmp_path):
        """Authorization packet head_sha differs from review_evidence.current_head_sha => reject."""
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": "a" * 40,  # evidence is for old head
            "reviewed_head_sha": "a" * 40,
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,
            "merge_allowed": True,
            "ci_all_green": True,
            "scope_status": "clean",
            "blockers_or_uncertainty": [],
        }
        checks = check_review_evidence(packet, auth_head_sha="b" * 40)  # auth is for new head
        auth_check = next((c for c in checks if c[0] == "auth_head_sha_matches_review_evidence"), None)
        assert auth_check is not None and auth_check[1] is False, \
            f"Expected auth head mismatch to fail: {checks}"

    def test_exact_head_clean_review_still_passes(self, tmp_path):
        """Valid exact-head clean review evidence still works."""
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": "a" * 40,
            "reviewed_head_sha": "a" * 40,
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,
            "merge_allowed": True,
            "ci_all_green": True,
            "scope_status": "clean",
            "mergeable": True,
            "blockers_or_uncertainty": [],
        }
        checks = check_review_evidence(packet, auth_head_sha="a" * 40, current_head="a" * 40)
        assert all(passed for _, passed, _ in checks), \
            f"All checks should pass for valid evidence: {checks}"

    def test_authorization_rejects_bogus_review_source(self, tmp_path):
        """review_source=bogus (not in allowed set) => reject even with clean status."""
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": "a" * 40,
            "reviewed_head_sha": "a" * 40,
            "review_source": "bogus",
            "review_status": "clean",
            "review_is_stale": False,
            "merge_allowed": True,
            "ci_all_green": True,
            "scope_status": "clean",
            "blockers_or_uncertainty": [],
        }
        checks = check_review_evidence(packet, auth_head_sha="a" * 40)
        source_check = next((r for r in checks if r[0] == "review_source_valid"), None)
        assert source_check is not None and source_check[1] is False, \
            f"Expected review_source_valid to fail for bogus: {checks}"

    def test_authorization_rejects_mergeable_false_forged(self, tmp_path):
        """mergeable=False with merge_allowed=True (forged) => reject."""
        packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": "a" * 40,
            "reviewed_head_sha": "a" * 40,
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,
            "merge_allowed": True,
            "mergeable": False,
            "ci_all_green": True,
            "scope_status": "clean",
            "blockers_or_uncertainty": [],
        }
        checks = check_review_evidence(packet, auth_head_sha="a" * 40)
        merge_check = next((r for r in checks if r[0] == "merge_allowed_accurate"), None)
        assert merge_check is not None and merge_check[1] is False, \
            f"Expected merge_allowed_accurate to fail for mergeable=False: {checks}"


# ── Exact SHA Authorization Enforcement Tests ────────────────────────────────────

HEAD = "a" * 40
OLD_HEAD = "b" * 40
VALID_PHRASE = f"I confirm merge PR #207 at {HEAD}"


class TestExtractShaFromPhrase:
    def test_exact_40_char_sha_extracted(self):
        sha = extract_sha_from_phrase(f"I confirm merge PR #207 at {HEAD}")
        assert sha == HEAD

    def test_7_char_prefix_not_extracted(self):
        sha = extract_sha_from_phrase(f"I confirm merge PR #207 at {HEAD[:7]}")
        assert sha is None

    def test_39_char_not_extracted(self):
        sha = extract_sha_from_phrase(f"I confirm merge PR #207 at {HEAD[:39]}")
        assert sha is None

    def test_no_sha_in_phrase(self):
        sha = extract_sha_from_phrase("I confirm merge PR #207")
        assert sha is None

    def test_40_char_middle_of_phrase(self):
        phrase = f"PR #207 — SHA is {HEAD} — confirmed"
        sha = extract_sha_from_phrase(phrase)
        assert sha == HEAD


class TestExactShaAuthorization:
    def test_exact_full_sha_authorization_passes(self):
        """Full 40-char SHA in phrase equals current head => pass."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        ok, msg = check_authorization_sha_match(VALID_PHRASE, packet, current_head=HEAD)
        assert ok is True, msg

    def test_short_sha_authorization_fails(self):
        """7-char prefix in phrase => reject with authorization_sha_mismatch."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase = f"I confirm merge PR #207 at {HEAD[:7]}"
        ok, msg = check_authorization_sha_match(phrase, packet, current_head=HEAD)
        assert ok is False
        assert "authorization_sha_mismatch" in msg
        assert "Short SHA prefixes are not accepted" in msg

    def test_39_char_sha_authorization_fails(self):
        """39-char SHA in phrase => reject with authorization_sha_mismatch."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase = f"I confirm merge PR #207 at {HEAD[:39]}"
        ok, msg = check_authorization_sha_match(phrase, packet, current_head=HEAD)
        assert ok is False
        assert "authorization_sha_mismatch" in msg

    def test_one_character_mismatch_fails(self):
        """40-char SHA differs by one char from current head => reject."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        wrong_sha = HEAD[:-1] + "1"  # one character different
        phrase = f"I confirm merge PR #207 at {wrong_sha}"
        ok, msg = check_authorization_sha_match(phrase, packet, current_head=HEAD)
        assert ok is False
        assert "authorization_sha_mismatch" in msg
        assert "does not equal" in msg

    def test_agent_substitution_pattern_fails(self):
        """Phrase SHA A, current head SHA B, guard blocks rather than using B."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase_a = f"I confirm merge PR #207 at {OLD_HEAD}"  # A != B
        ok, msg = check_authorization_sha_match(phrase_a, packet, current_head=HEAD)
        assert ok is False
        assert "authorization_sha_mismatch" in msg
        assert "must never substitute" in msg

    def test_current_head_must_match_packet_head(self):
        """--current-head differs from packet head_sha => reject."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase = f"I confirm merge PR #207 at {HEAD}"
        ok, msg = check_authorization_sha_match(phrase, packet, current_head=OLD_HEAD)
        assert ok is False
        assert "authorization_sha_mismatch" in msg

    def test_authorization_sha_must_match_review_evidence_head(self):
        """Authorization phrase SHA differs from review evidence current_head_sha => reject."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase_wrong = f"I confirm merge PR #207 at {OLD_HEAD}"
        ok, msg = check_authorization_sha_match(phrase_wrong, packet, current_head=None)
        assert ok is False
        assert "authorization_sha_mismatch" in msg

    def test_stale_review_plus_matching_authorization_still_fails(self):
        """Even if phrase SHA matches current head, stale review evidence blocks."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase = f"I confirm merge PR #207 at {HEAD}"
        # Stale review evidence: reviewed != current
        rev_packet = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "current_head_sha": HEAD,
            "reviewed_head_sha": OLD_HEAD,  # stale
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": True,
            "ci_all_green": True,
            "scope_status": "clean",
            "mergeable": True,
            "merge_allowed": False,
            "blockers_or_uncertainty": ["review is stale: reviewed_head_sha != current_head_sha"],
        }
        checks = check_review_evidence(rev_packet, auth_head_sha=HEAD, current_head=HEAD)
        stale_check = next((r for r in checks if r[0] == "review_not_stale"), None)
        assert stale_check is not None and stale_check[1] is False

    def test_missing_current_head_uses_packet_head(self):
        """Without --current-head, packet authorization_head_sha is used as required SHA."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase_wrong = f"I confirm merge PR #207 at {OLD_HEAD}"
        ok, msg = check_authorization_sha_match(phrase_wrong, packet, current_head=None)
        assert ok is False
        assert "authorization_sha_mismatch" in msg
        assert OLD_HEAD[:8] in msg

    def test_error_message_mentions_fresh_authorization_required(self):
        """Mismatch blocker text clearly requires fresh authorization."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase_wrong = f"I confirm merge PR #207 at {OLD_HEAD}"
        ok, msg = check_authorization_sha_match(phrase_wrong, packet, current_head=HEAD)
        assert ok is False
        assert "authorization_sha_mismatch" in msg
        assert "fresh authorization" in msg.lower() or "new" in msg.lower()

    def test_no_sha_at_all_fails(self):
        """Phrase with no SHA at all => reject."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase = "I confirm merge PR #207"
        ok, msg = check_authorization_sha_match(phrase, packet, current_head=HEAD)
        assert ok is False
        assert "authorization_sha_mismatch" in msg
        assert "no valid full 40-character SHA" in msg

    def test_backward_compat_full_sha_in_packet_head_fallback(self):
        """Without authorization_head_sha field, head_sha is used as fallback."""
        packet = make_valid_packet({"head_sha": HEAD})  # no authorization_head_sha
        phrase = f"I confirm merge PR #207 at {HEAD}"
        ok, msg = check_authorization_sha_match(phrase, packet, current_head=None)
        assert ok is True, msg

    def test_run_all_checks_includes_authorization_sha_match(self):
        """run_all_checks includes authorization_sha_match check."""
        packet = make_valid_packet({"head_sha": HEAD, "authorization_head_sha": HEAD})
        phrase = f"I confirm merge PR #207 at {HEAD}"
        checks = run_all_checks(packet, phrase, current_head=HEAD)
        names = [n for n, _, _ in checks]
        assert "authorization_sha_match" in names