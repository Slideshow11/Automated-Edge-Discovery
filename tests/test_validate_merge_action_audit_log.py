#!/usr/bin/env python3
"""Tests for validate_merge_action_audit_log.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "local" / "validate_merge_action_audit_log.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_validator(
    input_path: str,
    output_json: str,
    output_md: str,
    strict: bool = False,
    allow_legacy: bool = False,
    expected_prs_json: str = "[]",
) -> tuple[int, str, str]:
    """Run the validator and return (exit_code, json_content, md_content)."""
    cmd = [
        sys.executable, str(SCRIPT),
        "--input", input_path,
        "--output-json", output_json,
        "--output-md", output_md,
    ]
    if strict:
        cmd.append("--strict")
    if allow_legacy:
        cmd.append("--allow-legacy")
    if expected_prs_json:
        cmd.extend(["--expected-prs-json", expected_prs_json])

    result = subprocess.run(cmd, capture_output=True, text=True)
    json_content = Path(output_json).read_text() if Path(output_json).exists() else ""
    md_content = Path(output_md).read_text() if Path(output_md).exists() else ""
    return result.returncode, json_content, md_content


def make_log(*rows: dict[str, Any]) -> str:
    """Create a JSONL string from a list of row dicts."""
    return "\n".join(json.dumps(r) for r in rows) + "\n"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ---------------------------------------------------------------------------
# Valid row tests
# ---------------------------------------------------------------------------

def test_valid_pr_merge_row_passes(temp_dir):
    """A valid pr_merge row should pass validation."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    valid_row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "I confirm merge PR #232 at 1f2a739009813dcbdae590644b4c22771a602688 using final-head reviewed clean state.",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {"codex": "clean"},
    }
    log.write_text(make_log(valid_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[232]",
    )

    assert rc == 0, f"Expected pass, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    assert report["errors"] == []
    assert report["warnings"] == []
    assert report["pr_merge_counts"].get("232") == 1


def test_string_pr_number_emits_warning_non_strict(temp_dir):
    """String pr_number should emit warning in non-strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    # Legacy row with string pr_number and no event_type
    legacy_row = {
        "pr_number": "232",  # string, not int
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "authorization_phrase": "confirm",
        # missing event_type, gate_catches, safety booleans
    }
    log.write_text(make_log(legacy_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
    )

    # Non-strict with allow_legacy: should pass with warnings
    report = json.loads(json_content)
    # Should have warnings for legacy flags
    assert len(report["warnings"]) >= 1
    warning_codes = {w["code"] for w in report["warnings"]}
    assert "legacy_string_pr_number" in warning_codes
    assert "legacy_missing_event_type" in warning_codes


def test_string_pr_number_fails_strict(temp_dir):
    """String pr_number should fail in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    legacy_row = {
        "pr_number": "232",
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "authorization_phrase": "confirm",
    }
    log.write_text(make_log(legacy_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
    )

    assert rc != 0, "Expected failure in strict mode"
    report = json.loads(json_content)
    assert report["valid"] is False


def test_missing_event_type_emits_legacy_warning_non_strict(temp_dir):
    """Missing event_type should emit legacy warning in non-strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    no_event_row = {
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "authorization_phrase": "confirm",
    }
    log.write_text(make_log(no_event_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
    )

    report = json.loads(json_content)
    assert len(report["warnings"]) >= 1
    assert any("legacy_missing_event_type" in w["code"] for w in report["warnings"])


def test_missing_event_type_fails_strict(temp_dir):
    """Missing event_type should fail in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    no_event_row = {
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "authorization_phrase": "confirm",
    }
    log.write_text(make_log(no_event_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
    )

    assert rc != 0
    report = json.loads(json_content)
    assert report["valid"] is False


def test_duplicate_pr_merge_fails(temp_dir):
    """Duplicate pr_merge entries for the same PR should fail."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row1 = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    row2 = {**row1, "merge_sha": "bbbbbbb0000000000000000000000000000000000"}
    log.write_text(make_log(row1, row2))

    rc, json_content, _ = run_validator(str(log), str(json_out), str(md_out))

    assert rc != 0, "Expected failure for duplicate pr_merge"
    report = json.loads(json_content)
    assert report["valid"] is False
    dup_codes = {d["code"] for d in report["duplicates"]}
    assert "duplicate_pr_merge_entry" in dup_codes


def test_malformed_sha_fails(temp_dir):
    """Malformed SHA should fail validation."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    bad_row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "not-a-sha",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    log.write_text(make_log(bad_row))

    rc, json_content, _ = run_validator(str(log), str(json_out), str(md_out))

    assert rc != 0
    report = json.loads(json_content)
    assert any("malformed_head_sha" in e["code"] for e in report["errors"])


def test_missing_authorization_phrase_fails_strict(temp_dir):
    """Missing authorization_phrase should fail in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    no_auth = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        # missing authorization_phrase
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    log.write_text(make_log(no_auth))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
    )

    assert rc != 0
    report = json.loads(json_content)
    assert any("missing_required_field" in e["code"] and e.get("field") == "authorization_phrase"
               for e in report["errors"])


def test_missing_safety_booleans_fails(temp_dir):
    """Missing safety booleans should fail."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    no_safety = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        # missing hermes_touched, dispatch_occurred, production_board_touched
        "gate_catches": {},
    }
    log.write_text(make_log(no_safety))

    rc, json_content, _ = run_validator(str(log), str(json_out), str(md_out))

    assert rc != 0
    report = json.loads(json_content)
    assert any("missing_required_field" in e["code"] and e.get("field") in SAFETY_BOOLEAN_KEYS
               for e in report["errors"])


SAFETY_BOOLEAN_KEYS = frozenset([
    "hermes_touched", "dispatch_occurred",
    "production_board_touched", "import_performed", "pr_created",
])


def test_non_boolean_safety_booleans_fail_strict(temp_dir):
    """Non-boolean safety booleans should fail in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    bad_bool_row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": "false",  # string "false" — legacy encoding
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    log.write_text(make_log(bad_bool_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
    )

    assert rc != 0
    report = json.loads(json_content)
    assert any("safety_boolean_not_boolean" in e["code"] for e in report["errors"])


def test_missing_gate_catches_warns_non_strict(temp_dir):
    """Missing gate_catches should warn (not fail) in non-strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    no_gc = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        # missing gate_catches
    }
    log.write_text(make_log(no_gc))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
    )

    report = json.loads(json_content)
    assert report["valid"] is True  # passes non-strict
    assert any("legacy_missing_gate_catches" in w["code"] for w in report["warnings"])


def test_missing_gate_catches_fails_strict(temp_dir):
    """Missing gate_catches should fail in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    no_gc = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
    }
    log.write_text(make_log(no_gc))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
    )

    assert rc != 0
    report = json.loads(json_content)
    assert report["valid"] is False


def test_expected_pr_missing_reported(temp_dir):
    """Expected PR not found should be reported as warning."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    log.write_text(make_log())  # empty log

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[232,233]",
    )

    report = json.loads(json_content)
    assert any(
        w["code"] == "expected_pr_not_found" and w.get("expected_pr") == 232
        for w in report["warnings"]
    )


def test_expected_pr_exactly_once_passes(temp_dir):
    """Expected PR found exactly once should pass."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    valid_row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    log.write_text(make_log(valid_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[232]",
    )

    assert rc == 0
    report = json.loads(json_content)
    assert report["valid"] is True


def test_invalid_json_line_fails(temp_dir):
    """Invalid JSON line should fail."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    log.write_text("this is not json\n")

    rc, json_content, _ = run_validator(str(log), str(json_out), str(md_out))

    assert rc != 0
    report = json.loads(json_content)
    assert any(e["code"] == "invalid_json" for e in report["errors"])


def test_empty_line_deterministic(temp_dir):
    """Empty lines should be rejected deterministically."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    valid_row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    # 3 valid JSON rows each followed by an empty line
    # Pattern: row\n\nrow\n\nrow\n
    parts = [json.dumps(valid_row), "", json.dumps(valid_row), "", json.dumps(valid_row), ""]
    log.write_text("\n".join(parts) + "\n")

    rc, json_content, _ = run_validator(str(log), str(json_out), str(md_out))

    # All blank lines should be rejected as errors
    report = json.loads(json_content)
    assert len(report["errors"]) == 3


def test_controlled_smoke_create_with_board_task_passes(temp_dir):
    """controlled_smoke_create with board/task fields should pass."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    smoke_row = {
        "event_type": "controlled_smoke_create",
        "timestamp": "2026-05-16T00:00:00Z",
        "candidate_id": "test-candidate",
        "board": "aed",
        "task_id": "AED-123",
    }
    log.write_text(make_log(smoke_row))

    rc, json_content, _ = run_validator(str(log), str(json_out), str(md_out))

    assert rc == 0
    report = json.loads(json_content)
    assert report["valid"] is True
    assert report["events_by_type"].get("controlled_smoke_create") == 1


def test_output_json_and_md_files_written(temp_dir):
    """Output JSON and MD files should be written."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    valid_row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    log.write_text(make_log(valid_row))

    rc, _, md_content = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[232]",
    )

    assert json_out.exists()
    assert md_out.exists()
    assert md_content.startswith("# AED Audit Log Validation Report")


def test_validator_readonly_does_not_modify_input(temp_dir):
    """Validator should not modify the input file."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    valid_row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": 232,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "confirm",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    original = make_log(valid_row)
    log.write_text(original)

    rc, _, _ = run_validator(str(log), str(json_out), str(md_out))

    assert log.read_text() == original, "Input file should not be modified"

# -----------------------------------------------------------------------------
# Concatenated JSON object detection tests
# -----------------------------------------------------------------------------

def test_validator_detects_concatenated_json_objects_strict(temp_dir):
    """Validator strict mode errors on concatenated JSON objects."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    # Two JSON objects concatenated on one line (the bug scenario)
    row1 = {
        "event_type": "pr_merge",
        "pr_number": 901,
        "head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "merged_at": "2026-01-01T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    row2 = {
        "event_type": "pr_merge",
        "pr_number": 902,
        "head_sha": "c" * 40,
        "merge_sha": "d" * 40,
        "merged_at": "2026-01-02T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    # Write concatenated (no newline between them)
    log.write_text(json.dumps(row1, separators=(",", ":")) + json.dumps(row2, separators=(",", ":")))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
    )

    assert rc != 0
    report = json.loads(json_content)
    assert any(
        "concatenated_json_objects" in e["code"]
        for e in report["errors"]
    )


def test_validator_reports_concatenation_warning_non_strict(temp_dir):
    """Validator non-strict mode reports concatenation as warning."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row1 = {
        "event_type": "pr_merge",
        "pr_number": 903,
        "head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "merged_at": "2026-01-01T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    row2 = {
        "event_type": "pr_merge",
        "pr_number": 904,
        "head_sha": "c" * 40,
        "merge_sha": "d" * 40,
        "merged_at": "2026-01-02T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    # Write concatenated on one physical line
    log.write_text(json.dumps(row1, separators=(",", ":")) + json.dumps(row2, separators=(",", ":")))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
    )

    # Non-strict should still pass (warnings only) but must emit concatenation warning
    report = json.loads(json_content)
    concat_codes = [w["code"] for w in report["warnings"]] + [e["code"] for e in report["errors"]]
    assert "concatenated_json_objects" in concat_codes, f"Expected concatenated_json_objects warning, got {report['warnings'] + report['errors']}"


def test_validator_passes_valid_jsonl_despite_concatenation_in_non_strict(temp_dir):
    """
    Even with concatenated JSON on a line, non-strict mode should recover
    and validate the rest of the file successfully.
    """
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row1 = {
        "event_type": "pr_merge",
        "pr_number": 905,
        "head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "merged_at": "2026-01-01T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    row2 = {
        "event_type": "pr_merge",
        "pr_number": 906,
        "head_sha": "c" * 40,
        "merge_sha": "d" * 40,
        "merged_at": "2026-01-02T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    valid_row = {
        "event_type": "pr_merge",
        "pr_number": 907,
        "head_sha": "e" * 40,
        "merge_sha": "f" * 40,
        "merged_at": "2026-01-03T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    # Write: valid line, concatenated line, valid line
    content = (
        json.dumps(row1, separators=(",", ":")) + "\n"
        + json.dumps(row2, separators=(",", ":")) + json.dumps(valid_row, separators=(",", ":"))
        + "\n"
        + json.dumps(valid_row, separators=(",", ":"))
    )
    # Fix: write with trailing newline for the first valid row
    content = json.dumps(row1, separators=(",", ":")) + "\n"
    content += json.dumps(row2, separators=(",", ":")) + json.dumps(valid_row, separators=(",", ":"))
    content += "\n"
    content += json.dumps(valid_row, separators=(",", ":"))
    log.write_text(content)

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
    )

    report = json.loads(json_content)
    concat_warnings = [w for w in report["warnings"] if w["code"] == "concatenated_json_objects"]
    # Should have exactly 1 concatenation warning for the bad line
    assert len(concat_warnings) >= 1
    # The valid rows should still be counted
    assert report["pr_merge_counts"].get("907") == 1


def test_validator_invalid_json_after_concatenation_fails_strict(temp_dir):
    """If content after first JSON is not valid JSON, strict mode errors."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row1 = {
        "event_type": "pr_merge",
        "pr_number": 910,
        "head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "merged_at": "2026-01-01T00:00:00Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {},
    }
    # Write valid JSON + garbage
    log.write_text(json.dumps(row1, separators=(",", ":")) + "THIS IS NOT JSON")

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
    )

    assert rc != 0
    report = json.loads(json_content)
    codes = [e["code"] for e in report["errors"]]
    assert "concatenated_json_objects" in codes
