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
from check_merge_authorization import (
    check_packet_kind,
    check_not_expired,
    check_phrase_match,
    check_head_sha_match,
    check_no_blockers,
    check_recommendation_merge,
    check_required_fields,
    load_packet,
    run_all_checks,
    PACKET_KIND,
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
        assert "gh pr merge" not in content
        assert "gh pr comment" not in content

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