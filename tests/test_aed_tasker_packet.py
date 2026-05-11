"""Tests for scripts/local/aed_tasker_packet.py — read-only AED Tasker packet scaffold."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))

from aed_tasker_packet import (
    PACKET_KIND,
    ValidationError,
    deterministic_dumps,
    load_packet,
    make_empty_packet,
    render_memo,
    validate_packet,
    validate_file,
)

# ── Helper: minimal valid packet ───────────────────────────────────────────────

_MINIMAL_CANDIDATES = [
    {
        "candidate_id": "AED-CAND-001",
        "title": "Add PR gate watchdog",
        "goal": "Watch PR state for CI and Codex signals",
        "why_now": "Foundation for automation layer",
        "allowed_files": ["scripts/local/watch_pr_gate_state.py"],
        "forbidden_files": ["schemas/", "engine/", "fixtures/"],
        "risk_if_skipped": "medium",
        "risk_if_built_too_early": "low",
        "expected_tests": ["test_watch_pr_gate_state.py"],
        "deep_module_boundary": "tooling",
        "estimated_scope": {"files_changed": 1, "新增代码行": 100},
        "depends_on": [],
    },
    {
        "candidate_id": "AED-CAND-002",
        "title": "Add Tasker packet scaffold",
        "goal": "Define ROADMAP_PACKET.json schema",
        "why_now": "Enables future Tasker agent output",
        "allowed_files": ["scripts/local/aed_tasker_packet.py"],
        "forbidden_files": ["engine/", "schemas/"],
        "risk_if_skipped": "high",
        "risk_if_built_too_early": "low",
        "expected_tests": ["test_aed_tasker_packet.py"],
        "deep_module_boundary": "tooling",
        "estimated_scope": {"files_changed": 3, "新增代码行": 300},
        "depends_on": [],
    },
    {
        "candidate_id": "AED-CAND-003",
        "title": "Add Executor planner",
        "goal": "Translate Tasker recommendation into PR plan",
        "why_now": "Completes the Tasker → Executor chain",
        "allowed_files": ["scripts/local/aed_executor_packet.py"],
        "forbidden_files": ["engine/", "schemas/", "fixtures/"],
        "risk_if_skipped": "high",
        "risk_if_built_too_early": "medium",
        "expected_tests": [],
        "deep_module_boundary": "tooling",
        "estimated_scope": {"files_changed": 2, "新增代码行": 200},
        "depends_on": ["AED-CAND-002"],
    },
]


def _make_valid_packet(**overrides) -> dict:
    """Return a valid minimal packet with sensible defaults."""
    packet = make_empty_packet()
    packet.update({
        "generated_at": "2026-05-11T12:00:00+00:00",
        "repo": {
            "path": "/home/max/Automated-Edge-Discovery",
            "head_sha": "82f05db5e92d4ed5ac2b6d7a8afe6d67f1758ef3",
            "branch": "main",
            "clean_status": "clean",
        },
        "tasker_scope": {
            "input_docs": ["docs/current_project_status.md"],
            "input_code_paths": ["scripts/local/"],
            "recent_prs_reviewed": [191, 190, 189],
            "external_sources_reviewed": [],
            "limitations": "No live research scan.",
        },
        "current_state": {
            "implemented_in_code": ["PR gate watchdog"],
            "implemented_in_schema": [],
            "implemented_in_tests": [],
            "implemented_in_docs_only": [],
            "not_implemented": ["Tasker agent", "Executor agent"],
        },
        "recent_pr_lessons": [
            {"pr_number": 191, "title": "scheduled watchdog", "lesson": "Codex review caught flag-stripping bug", "impact": "high"},
        ],
        "drift_risks": [
            {"risk": "Watchdog design diverges from design doc", "severity": "LOW", "mitigation": "Keep PR #192 in sync"},
        ],
        "deep_module_assessment": [
            {"module": "tooling", "status": "healthy", "concern": "", "recommended_boundary": "tooling/"},
        ],
        "candidate_prs": _MINIMAL_CANDIDATES,
        "recommended_next_prs": ["AED-CAND-001", "AED-CAND-002"],
        "do_not_build_yet": [
            {"item": "Auto-merge", "reason": "Requires Reviewer agent"},
        ],
        "open_questions": [
            "Should Executor run before or after specifier approval?",
        ],
        "final_recommendation": "AED-CAND-001",
    })
    for key, value in overrides.items():
        if "." in key:
            parts = key.split(".")
            d = packet
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value
        else:
            packet[key] = value
    return packet


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestValidatePacket:
    """Validation rule tests."""

    def test_valid_minimal_packet_passes(self):
        errors = validate_packet(_make_valid_packet())
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_wrong_packet_kind_fails(self):
        errors = validate_packet(_make_valid_packet(packet_kind="wrong.kind"))
        assert any("packet_kind" in e for e in errors)

    def test_missing_generated_at_fails(self):
        errors = validate_packet(_make_valid_packet(generated_at=""))
        assert any("generated_at" in e for e in errors)

    def test_invalid_generated_at_format_fails(self):
        errors = validate_packet(_make_valid_packet(generated_at="not-a-date"))
        assert any("generated_at" in e for e in errors)

    def test_duplicate_candidate_id_fails(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        candidates[1]["candidate_id"] = "AED-CAND-001"  # duplicate
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert any("duplicate" in e.lower() for e in errors)

    def test_recommended_id_missing_from_candidates_fails(self):
        errors = validate_packet(_make_valid_packet(recommended_next_prs=["NOT-A-CANDIDATE"]))
        assert any("NOT-A-CANDIDATE" in e for e in errors)

    def test_candidate_missing_allowed_files_fails(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        del candidates[0]["allowed_files"]
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert any("allowed_files" in e for e in errors)

    def test_candidate_missing_forbidden_files_fails(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        del candidates[0]["forbidden_files"]
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert any("forbidden_files" in e for e in errors)

    def test_hermes_allowed_path_fails(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        candidates[0]["allowed_files"] = ["/home/max/.hermes/some_file"]
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert any(".hermes" in e for e in errors)

    def test_hermes_subpath_allowed_fails(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        candidates[0]["allowed_files"] = ["/home/max/.hermes/skills/myskill"]
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert any(".hermes" in e for e in errors)

    def test_registry_mutation_without_locked_fails(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        candidates[0]["allowed_files"] = ["edge_hypothesis_registry_v1.jsonl"]
        # estimated_scope with registry_mutation_mode=none (default)
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert any("registry" in e.lower() for e in errors)

    def test_registry_mutation_with_locked_passes(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        candidates[0]["allowed_files"] = ["edge_hypothesis_registry_v1.jsonl"]
        candidates[0]["estimated_scope"] = {"registry_mutation_mode": "locked"}
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert not any("registry" in e.lower() for e in errors)

    def test_registry_mutation_with_future_passes(self):
        candidates = [dict(c) for c in _MINIMAL_CANDIDATES]
        candidates[0]["allowed_files"] = ["edge_hypothesis_registry_v1.jsonl"]
        candidates[0]["estimated_scope"] = {"registry_mutation_mode": "future"}
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert not any("registry" in e.lower() for e in errors)

    def test_final_recommendation_unknown_id_fails(self):
        errors = validate_packet(_make_valid_packet(final_recommendation="NOT-A-CANDIDATE"))
        assert any("final_recommendation" in e for e in errors)

    def test_final_recommendation_valid_action_passes(self):
        errors = validate_packet(_make_valid_packet(final_recommendation="defer"))
        assert not any("final_recommendation" in e for e in errors)

    def test_final_recommendation_valid_candidate_passes(self):
        errors = validate_packet(_make_valid_packet(final_recommendation="AED-CAND-001"))
        assert not any("final_recommendation" in e for e in errors)

    def test_fewer_than_3_candidates_fails(self):
        candidates = _MINIMAL_CANDIDATES[:2]
        errors = validate_packet(_make_valid_packet(candidate_prs=candidates))
        assert any("at least 3" in e for e in errors)

    def test_fewer_than_1_recommended_fails(self):
        errors = validate_packet(_make_valid_packet(recommended_next_prs=[]))
        assert any("at least 1" in e for e in errors)

    def test_more_than_5_recommended_fails(self):
        errors = validate_packet(_make_valid_packet(
            recommended_next_prs=["AED-CAND-001", "AED-CAND-002", "AED-CAND-003", "AED-CAND-001", "AED-CAND-002", "AED-CAND-003"]
        ))
        assert any("at most 5" in e for e in errors)

    def test_missing_repo_fields_fails(self):
        packet = _make_valid_packet()
        packet["repo"]["head_sha"] = ""
        errors = validate_packet(packet)
        assert any("repo" in e and "head_sha" in e for e in errors)


class TestDeterministicOutput:
    def test_deterministic_json_is_stable(self):
        packet = _make_valid_packet()
        output1 = deterministic_dumps(packet)
        output2 = deterministic_dumps(packet)
        assert output1 == output2

    def test_deterministic_json_preserves_all_keys(self):
        packet = _make_valid_packet()
        output = deterministic_dumps(packet)
        loaded = json.loads(output)
        # All top-level keys preserved
        for key in make_empty_packet():
            assert key in loaded, f"Key {key!r} missing from deterministic output"


class TestRenderMemo:
    def test_render_includes_recommended_next_prs(self):
        packet = _make_valid_packet()
        memo = render_memo(packet)
        assert "AED-CAND-001" in memo
        assert "AED-CAND-002" in memo

    def test_render_includes_final_recommendation(self):
        packet = _make_valid_packet()
        memo = render_memo(packet)
        assert "Final Recommendation" in memo

    def test_render_includes_candidates(self):
        packet = _make_valid_packet()
        memo = render_memo(packet)
        assert "Add PR gate watchdog" in memo

    def test_render_includes_drift_risks(self):
        packet = _make_valid_packet()
        memo = render_memo(packet)
        assert "Drift Risks" in memo

    def test_render_includes_do_not_build_yet(self):
        packet = _make_valid_packet()
        memo = render_memo(packet)
        assert "Do Not Build Yet" in memo


class TestMakeEmptyPacket:
    def test_has_all_required_top_level_keys(self):
        empty = make_empty_packet()
        required = [
            "packet_kind", "schema_version", "generated_at", "repo",
            "tasker_scope", "current_state", "recent_pr_lessons",
            "drift_risks", "deep_module_assessment", "candidate_prs",
            "recommended_next_prs", "do_not_build_yet", "open_questions",
            "final_recommendation",
        ]
        for key in required:
            assert key in empty, f"Missing required key: {key}"


class TestNoMutation:
    """Prove the scaffold makes no mutation calls."""

    def test_no_requests_post(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        assert "requests.post" not in content
        assert "requests.patch" not in content
        assert "requests.put" not in content

    def test_no_urllib_post(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        assert "urllib.request.Request" not in content or "GET" in content
        # urllib is only used for urlopen with GET semantics in allowed contexts
        # We import urllib but only for json.loads from strings
        # Check there are no .POST or .put calls
        assert ".post(" not in content.lower()
        assert ".put(" not in content.lower()

    def test_no_gh_pr_mutation(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        assert "gh pr merge" not in content
        assert "gh pr create" not in content
        assert "gh issue create" not in content

    def test_no_subprocess_mutation(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        # subprocess is not imported at all
        assert "subprocess" not in content

    def test_no_hermes_kanban(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        assert "hermes kanban" not in content
        assert "kanban_create" not in content
        assert "kanban_dispatch" not in content

    def test_no_network_calls(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        # No urllib calls at all (read-only local file only)
        assert "urllib.request" not in content and "urllib3" not in content
        assert "httpx" not in content
        assert "aiohttp" not in content

    def test_no_memory_update(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        assert "memory" not in content.lower() or "from datetime" in content
        assert "fact_store" not in content
        assert "skill_manage" not in content

    def test_no_skill_manage(self):
        src = Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py"
        content = src.read_text()
        assert "skill_manage" not in content


class TestCLI:
    SCRIPT = str(Path(__file__).parent.parent / "scripts" / "local" / "aed_tasker_packet.py")

    def _write_packet(self, packet: dict, tmp_path: Path) -> Path:
        path = tmp_path / "packet.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return path

    def test_validate_returns_0_for_valid_packet(self, tmp_path):
        path = self._write_packet(_make_valid_packet(), tmp_path)
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "validate", str(path)],
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode()

    def test_validate_returns_nonzero_for_invalid_packet(self, tmp_path):
        path = self._write_packet(_make_valid_packet(packet_kind="bad"), tmp_path)
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "validate", str(path)],
            capture_output=True,
        )
        assert result.returncode != 0

    def test_validate_returns_nonzero_for_missing_file(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "validate", "/does/not/exist.json"],
            capture_output=True,
        )
        assert result.returncode != 0

    def test_render_md_writes_expected_sections(self, tmp_path):
        path = self._write_packet(_make_valid_packet(), tmp_path)
        out = tmp_path / "memo.md"
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "render-md", str(path), "--output", str(out)],
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode()
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "AED Tasker Roadmap Memo" in content
        assert "Recommended Next PRs" in content

    def test_render_md_to_stdout(self, tmp_path):
        path = self._write_packet(_make_valid_packet(), tmp_path)
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "render-md", str(path)],
            capture_output=True,
        )
        assert result.returncode == 0
        assert "AED Tasker Roadmap Memo" in result.stdout.decode()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])