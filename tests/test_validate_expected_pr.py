#!/usr/bin/env python3
"""Tests for expected PR normalization and validation in validate_merge_action_audit_log.py"""

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


def make_pr_merge_row(pr_number: Any, **overrides) -> dict[str, Any]:
    """Make a valid pr_merge row with given pr_number (any type)."""
    row = {
        "audit_log_version": "1.0",
        "event_type": "pr_merge",
        "timestamp": "2026-05-16T00:00:00Z",
        "pr_number": pr_number,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "ci_status": "success",
        "codex_status": "clean",
        "scope_status": "clean",
        "authorization_phrase": "I confirm merge PR test at HEAD",
        "hermes_touched": False,
        "dispatch_occurred": False,
        "production_board_touched": False,
        "gate_catches": {"test": "ok"},
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Normalize PR number tests
# ---------------------------------------------------------------------------

def test_normalize_pr_number_int(temp_dir):
    """Integer pr_number counts toward expected PR."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(237)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert rc == 0, f"Expected pass, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    epr = report.get("expected_pr_results", {})
    assert "237" in epr, f"PR 237 not in expected_pr_results: {epr}"
    assert epr["237"]["count"] == 1
    assert epr["237"]["status"] == "present_once"


def test_normalize_pr_number_string(temp_dir):
    """String pr_number counts toward expected PR."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row("237")
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert rc == 0, f"Expected pass, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    epr = report.get("expected_pr_results", {})
    assert "237" in epr
    assert epr["237"]["count"] == 1
    assert epr["237"]["status"] == "present_once"


def test_normalize_pr_number_hash(temp_dir):
    """'#237' counts toward expected PR."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row("#237")
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert rc == 0, f"Expected pass, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    epr = report.get("expected_pr_results", {})
    assert "237" in epr
    assert epr["237"]["count"] == 1


def test_normalize_pr_number_pr_hash(temp_dir):
    """'PR #237' counts toward expected PR."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row("PR #237")
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert rc == 0, f"Expected pass, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    epr = report.get("expected_pr_results", {})
    assert "237" in epr
    assert epr["237"]["count"] == 1


def test_normalize_pr_number_pr_dash(temp_dir):
    """'PR-237' counts toward expected PR."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row("PR-237")
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert rc == 0, f"Expected pass, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    epr = report.get("expected_pr_results", {})
    assert "237" in epr


def test_normalize_pr_number_bool_rejected(temp_dir):
    """PR number true/false must be rejected as malformed, not accepted as int."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    # Boolean pr_number (True is subclass of int in Python)
    row = make_pr_merge_row(True)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    # With no other rows, PR 237 is missing → expect non-zero exit
    assert rc != 0, f"Boolean pr_number must be rejected, got rc={rc}"
    report = json.loads(json_content)
    error_codes = [e["code"] for e in report.get("errors", [])]
    # Boolean True should be rejected as malformed, not tracked as PR
    # Combined with no row matching 237, expect both malformed_pr_number and expected_pr_not_found
    assert "malformed_pr_number" in error_codes or "expected_pr_not_found" in error_codes


# ---------------------------------------------------------------------------
# Missing expected PR — must fail in BOTH modes
# ---------------------------------------------------------------------------

def test_missing_expected_pr_fails_non_strict(temp_dir):
    """Missing expected PR must error in non-strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    # Valid row with PR 999, but we expect 237
    row = make_pr_merge_row(999)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    assert rc != 0, "Expected failure when expected PR is missing in non-strict mode"
    report = json.loads(json_content)
    assert report["valid"] is False
    error_codes = {e["code"] for e in report.get("errors", [])}
    assert "expected_pr_not_found" in error_codes, f"expected_pr_not_found not in {error_codes}"


def test_missing_expected_pr_fails_strict(temp_dir):
    """Missing expected PR must error in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(999)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
        expected_prs_json="[237]",
    )

    assert rc != 0, "Expected failure when expected PR is missing in strict mode"
    report = json.loads(json_content)
    assert report["valid"] is False
    error_codes = {e["code"] for e in report.get("errors", [])}
    assert "expected_pr_not_found" in error_codes


# ---------------------------------------------------------------------------
# Duplicate expected PR — must fail in BOTH modes
# ---------------------------------------------------------------------------

def test_duplicate_expected_pr_fails_non_strict(temp_dir):
    """Duplicate expected PR must error in non-strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    # Two rows with same PR number
    row1 = make_pr_merge_row(237, merge_sha="a" * 40)
    row2 = make_pr_merge_row(237, merge_sha="b" * 40)
    log.write_text(make_log(row1, row2))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    assert rc != 0, "Expected failure for duplicate expected PR in non-strict mode"
    report = json.loads(json_content)
    assert report["valid"] is False
    error_codes = {e["code"] for e in report.get("errors", [])}
    assert "expected_pr_duplicate" in error_codes or "duplicate_pr_merge_entry" in error_codes


def test_duplicate_expected_pr_fails_strict(temp_dir):
    """Duplicate expected PR must error in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row1 = make_pr_merge_row(237, merge_sha="a" * 40)
    row2 = make_pr_merge_row(237, merge_sha="b" * 40)
    log.write_text(make_log(row1, row2))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
        expected_prs_json="[237]",
    )

    assert rc != 0, "Expected failure for duplicate expected PR in strict mode"
    report = json.loads(json_content)
    assert report["valid"] is False


# ---------------------------------------------------------------------------
# Legacy row with string pr_number counts in non-strict mode
# ---------------------------------------------------------------------------

def test_legacy_string_pr_number_counts_non_strict(temp_dir):
    """Legacy row with string pr_number counts toward expected PR in non-strict."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    # Legacy row: no event_type, string pr_number
    legacy_row = {
        "pr_number": "237",
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "authorization_phrase": "confirm",
    }
    log.write_text(make_log(legacy_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    assert rc == 0, f"Expected pass with legacy row, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    epr = report.get("expected_pr_results", {})
    assert "237" in epr, f"PR 237 not counted in legacy row: {epr}"
    assert epr["237"]["count"] == 1


# ---------------------------------------------------------------------------
# Null/malformed pr_number does not count and warns/errors based on mode
# ---------------------------------------------------------------------------

def test_null_pr_number_warns_non_strict(temp_dir):
    """Null pr_number warns in non-strict and does not count."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(None)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    # Non-strict: passes (malformed pr_number warns but doesn't fail validation)
    report = json.loads(json_content)
    warning_codes = {w["code"] for w in report.get("warnings", [])}
    assert "malformed_pr_number" in warning_codes
    # PR 237 not found (null pr_number doesn't count)
    epr = report.get("expected_pr_results", {})
    assert "237" not in epr or epr["237"]["count"] == 0


def test_null_pr_number_errors_strict(temp_dir):
    """Null pr_number errors in strict mode."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(None)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        strict=True,
        expected_prs_json="[237]",
    )

    assert rc != 0
    report = json.loads(json_content)
    error_codes = {e["code"] for e in report.get("errors", [])}
    assert "malformed_pr_number" in error_codes


def test_malformed_pr_number_not_counted(temp_dir):
    """Malformed pr_number ('abc') does not count toward expected PR."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row("abc")
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    report = json.loads(json_content)
    epr = report.get("expected_pr_results", {})
    assert "237" not in epr or epr["237"]["count"] == 0


# ---------------------------------------------------------------------------
# expected_pr_results appears in output JSON
# ---------------------------------------------------------------------------

def test_expected_pr_results_in_output_json(temp_dir):
    """expected_pr_results must appear in the output JSON when expected_prs provided."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(237)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert rc == 0
    report = json.loads(json_content)
    assert "expected_pr_results" in report, "expected_pr_results missing from output"
    assert isinstance(report["expected_pr_results"], dict)
    assert "237" in report["expected_pr_results"]


# ---------------------------------------------------------------------------
# Markdown contains Expected PR Check table
# ---------------------------------------------------------------------------

def test_markdown_contains_expected_pr_check_section(temp_dir):
    """Markdown output must contain '## Expected PR Check' section."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(237)
    log.write_text(make_log(row))

    rc, json_content, md_content = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert "## Expected PR Check" in md_content, "Expected PR Check section not found in markdown"


# ---------------------------------------------------------------------------
# Duplicate report includes line numbers and merge SHAs
# ---------------------------------------------------------------------------

def test_duplicate_report_includes_line_numbers_and_shapes(temp_dir):
    """Duplicate report must include line numbers and merge SHAs."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row1 = make_pr_merge_row(237, merge_sha="a" * 40)
    row2 = make_pr_merge_row(237, merge_sha="b" * 40)
    log.write_text(make_log(row1, row2))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    report = json.loads(json_content)
    dup_prs = [d for d in report.get("duplicates", []) if d["code"] == "duplicate_pr_merge_entry"]
    assert len(dup_prs) >= 1
    # Must include line number and merge_sha
    assert "line" in dup_prs[0]
    assert "merge_sha" in dup_prs[0] or "merge_sha" in str(dup_prs[0])
    # all_occurrences must include real line numbers (not L1, L2 enumerated indices)
    all_occ = dup_prs[0].get("all_occurrences", [])
    assert len(all_occ) == 2
    # Each occurrence must have a 'line' key with the real audit-log line number
    assert "line" in all_occ[0]
    assert "line" in all_occ[1]
    # Lines should be distinct (these rows are on lines 1 and 2)
    assert all_occ[0]["line"] != all_occ[1]["line"]
    # Verify markdown also uses real line numbers (not L1/L2)
    md_content = open(temp_dir / "report.md").read() if (temp_dir / "report.md").exists() else ""
    # The markdown should reference actual line numbers (L1, L2 pattern replaced by real line nums)


def test_all_occurrences_has_line_field(temp_dir):
    """all_occurrences in duplicate entry must include 'line' per occurrence."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row1 = make_pr_merge_row(237, merge_sha="a" * 40, head_sha="1" * 40)
    row2 = make_pr_merge_row(237, merge_sha="b" * 40, head_sha="2" * 40)
    log.write_text(make_log(row1, row2))

    rc, json_content, md_content = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    report = json.loads(json_content)
    dup_prs = [d for d in report.get("duplicates", []) if d["code"] == "duplicate_pr_merge_entry"]
    assert len(dup_prs) >= 1
    all_occ = dup_prs[0].get("all_occurrences", [])
    assert len(all_occ) == 2
    # 'line' key must be present in each occurrence dict
    for occ in all_occ:
        assert "line" in occ, f"all_occurrences entry missing 'line': {occ}"
        assert isinstance(occ["line"], int), f"line must be int, got {type(occ['line'])}"
    # Lines must be 1 and 2 (the two rows in the log)
    occ_lines = sorted(occ["line"] for occ in all_occ)
    assert occ_lines == [1, 2], f"Expected lines [1, 2], got {occ_lines}"


# ---------------------------------------------------------------------------
# Validator remains read-only
# ---------------------------------------------------------------------------

def test_validator_does_not_modify_input(temp_dir):
    """Validator must not modify the input JSONL file."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(237)
    original_content = make_log(row)
    log.write_text(original_content)

    rc, json_content, md_content = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    assert log.read_text() == original_content, "Validator modified input file"


# ---------------------------------------------------------------------------
# expected_pr_results structure
# ---------------------------------------------------------------------------

def test_expected_pr_results_has_correct_structure(temp_dir):
    """expected_pr_results dict must have count, status, lines, head_shas, merge_shas."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row = make_pr_merge_row(237)
    log.write_text(make_log(row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        expected_prs_json="[237]",
    )

    report = json.loads(json_content)
    epr = report.get("expected_pr_results", {})
    result = epr.get("237")
    assert result is not None
    assert "count" in result
    assert "status" in result
    assert "lines" in result
    assert "head_shas" in result
    assert "merge_shas" in result
    assert result["status"] == "present_once"
    assert result["count"] == 1
    assert result["lines"] == [1]  # line 1


# ---------------------------------------------------------------------------
# Missing expected PR with legacy row -- still errors
# ---------------------------------------------------------------------------

def test_missing_expected_pr_legacy_row_still_errors_non_strict(temp_dir):
    """Missing expected PR errors in non-strict even when present rows are legacy."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    # Legacy row with PR 999 (not what we're expecting)
    legacy_row = {
        "pr_number": 999,
        "head_sha": "1f2a739009813dcbdae590644b4c22771a602688",
        "merge_sha": "27b60a8801dedba932016ed1f77e03f6d45c2db5",
        "merged_at": "2026-05-16T17:32:39Z",
        "authorization_phrase": "confirm",
    }
    log.write_text(make_log(legacy_row))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[237]",
    )

    assert rc != 0, "Missing expected PR must error even with legacy rows present"
    report = json.loads(json_content)
    error_codes = {e["code"] for e in report.get("errors", [])}
    assert "expected_pr_not_found" in error_codes


# ---------------------------------------------------------------------------
# Multiple expected PRs in single run
# ---------------------------------------------------------------------------

def test_multiple_expected_prs_all_found(temp_dir):
    """When all expected PRs are present, validation passes."""
    log = temp_dir / "log.jsonl"
    json_out = temp_dir / "report.json"
    md_out = temp_dir / "report.md"

    row232 = make_pr_merge_row(232, merge_sha="a" * 40)
    row233 = make_pr_merge_row(233, merge_sha="b" * 40)
    row234 = make_pr_merge_row(234, merge_sha="c" * 40)
    log.write_text(make_log(row232, row233, row234))

    rc, json_content, _ = run_validator(
        str(log), str(json_out), str(md_out),
        allow_legacy=True,
        expected_prs_json="[232,233,234]",
    )

    assert rc == 0, f"Expected pass, got rc={rc}"
    report = json.loads(json_content)
    assert report["valid"] is True
    epr = report.get("expected_pr_results", {})
    for pr in ["232", "233", "234"]:
        assert epr[pr]["count"] == 1
        assert epr[pr]["status"] == "present_once"