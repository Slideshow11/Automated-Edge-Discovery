"""
Tests for scripts/local/run_autocoder_real_output_eval.py

Covers:
1. valid corpus with no results returns REAL_OUTPUT_EVAL_READY
2. invalid corpus returns HOLD_REAL_OUTPUT_CORPUS_INVALID
3. one matching successful result increments metrics
4. result for unknown task returns HOLD_REAL_OUTPUT_RESULT_INVALID
5. scope violation is counted
6. hold status is counted
7. human cleanup required is counted
8. JSON and Markdown artifacts are written
9. source safety: no gh mutation strings, no live Claude invocation, no shell-mode subprocess literal
10. CLI invalid args return ERROR_INVALID_ARGS or repo-standard nonzero behavior
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make the module under test importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
import run_autocoder_real_output_eval as mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

VALID_CORPUS: Dict[str, Any] = {
    "packet_kind": "aed.autocoder.real_output_corpus.v0",
    "schema_version": 1,
    "corpus_id": "test-corpus",
    "created_at": "2026-06-02T00:00:00Z",
    "description": "Test corpus for unit tests.",
    "tasks": [
        {
            "task_id": "task-A",
            "title": "Documentation update",
            "task_type": "docs",
            "goal": "Append a line to docs/x.md",
            "allowed_files": ["docs/x.md"],
            "forbidden_files": ["scripts/**", "tests/**", ".github/**", "*.py"],
            "expected_artifacts": ["docs/x.md"],
            "expected_tests": [],
            "scoring": {"merge_ready": 1},
            "risk_level": "low",
            "non_goals": ["Do not run live Claude"],
        },
        {
            "task_id": "task-B",
            "title": "Small report-only helper",
            "task_type": "report_only_tool",
            "goal": "Add scripts/local/h.py (read-only)",
            "allowed_files": ["scripts/local/h.py"],
            "forbidden_files": [".github/**", "*.json", "*.md"],
            "expected_artifacts": ["scripts/local/h.py"],
            "expected_tests": ["tests/test_h.py"],
            "scoring": {"merge_ready": 1},
            "risk_level": "low",
            "non_goals": ["Do not introduce gh mutation"],
        },
        {
            "task_id": "task-C",
            "title": "Test-only regression",
            "task_type": "test_only",
            "goal": "Add a test to tests/test_x.py",
            "allowed_files": ["tests/test_x.py"],
            "forbidden_files": ["scripts/**", ".github/**", "docs/**"],
            "expected_artifacts": ["tests/test_x.py"],
            "expected_tests": ["tests/test_x.py::test_new"],
            "scoring": {"merge_ready": 1},
            "risk_level": "low",
            "non_goals": ["Do not modify scripts/"],
        },
        {
            "task_id": "task-D",
            "title": "Narrow checker fix",
            "task_type": "narrow_code_fix",
            "goal": "Tighten scripts/local/c.py behavior",
            "allowed_files": ["scripts/local/c.py", "tests/test_c.py"],
            "forbidden_files": [".github/**", "docs/**", "*.json", "*.md"],
            "expected_artifacts": ["scripts/local/c.py", "tests/test_c.py"],
            "expected_tests": ["tests/test_c.py::test_narrow"],
            "scoring": {"merge_ready": 1},
            "risk_level": "medium",
            "non_goals": ["Do not add a CLI flag"],
        },
        {
            "task_id": "task-E",
            "title": "Packet schema validation",
            "task_type": "small_packet_validation",
            "goal": "Validate packet and emit report",
            "allowed_files": ["scripts/local/v.py", "tests/test_v.py"],
            "forbidden_files": [".github/**", "docs/**", "*.json"],
            "expected_artifacts": ["scripts/local/v.py", "tests/test_v.py"],
            "expected_tests": [
                "tests/test_v.py::test_valid_passes",
                "tests/test_v.py::test_missing_field_fails",
                "tests/test_v.py::test_wrong_kind_fails",
                "tests/test_v.py::test_unknown_fields_tolerated",
            ],
            "scoring": {"merge_ready": 1},
            "risk_level": "low",
            "non_goals": ["Do not depend on external schema library"],
        },
    ],
}


def _write_corpus(path: Path, corpus: Dict[str, Any]) -> None:
    path.write_text(json.dumps(corpus, indent=2), encoding="utf-8")


def _write_result(path: Path, result: Dict[str, Any]) -> None:
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def _run_eval(corpus_path: Path, result_paths: List[Path], json_out: Path, md_out: Path) -> int:
    argv: List[str] = [
        "--corpus", str(corpus_path),
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ]
    for rp in result_paths:
        argv.extend(["--result-json", str(rp)])
    return mod.main(argv)


# ---------------------------------------------------------------------------
# 1. valid corpus with no results
# ---------------------------------------------------------------------------


def test_valid_corpus_with_no_results_returns_ready(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_READY
    assert packet["task_count"] == 5
    assert packet["result_count"] == 0
    assert packet["matched_result_count"] == 0
    assert sorted(packet["missing_result_task_ids"]) == [
        "task-A", "task-B", "task-C", "task-D", "task-E"
    ]
    m = packet["metrics"]
    assert m["tasks_total"] == 5
    assert m["tasks_with_results"] == 0
    assert m["patches_produced"] == 0
    assert m["hold_count"] == 0
    assert m["error_count"] == 0


# ---------------------------------------------------------------------------
# 2. invalid corpus
# ---------------------------------------------------------------------------


def test_invalid_corpus_wrong_packet_kind(tmp_path: Path) -> None:
    bad = dict(VALID_CORPUS)
    bad["packet_kind"] = "aed.wrong.kind"
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, bad)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_HOLD_CORPUS_INVALID
    assert any("packet_kind" in e for e in packet["errors"])


def test_invalid_corpus_missing_tasks(tmp_path: Path) -> None:
    bad = dict(VALID_CORPUS)
    del bad["tasks"]
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, bad)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_HOLD_CORPUS_INVALID
    assert any("tasks" in e for e in packet["errors"])


def test_invalid_corpus_duplicate_task_id(tmp_path: Path) -> None:
    bad = dict(VALID_CORPUS)
    bad["tasks"] = list(VALID_CORPUS["tasks"]) + [dict(VALID_CORPUS["tasks"][0])]
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, bad)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_HOLD_CORPUS_INVALID
    assert any("duplicated" in e for e in packet["errors"])


def test_invalid_corpus_task_missing_required_field(tmp_path: Path) -> None:
    bad = dict(VALID_CORPUS)
    bad["tasks"] = [dict(t) for t in VALID_CORPUS["tasks"]]
    del bad["tasks"][1]["goal"]  # task-B missing goal
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, bad)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_HOLD_CORPUS_INVALID
    assert any("'goal'" in e or "goal" in e for e in packet["errors"])


# ---------------------------------------------------------------------------
# 3. one matching successful result
# ---------------------------------------------------------------------------


def test_one_matching_successful_result_increments_metrics(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    result = {
        "task_id": "task-A",
        "status": "PASS",
        "changed_files": ["docs/x.md"],
        "tests_passed": 0,
        "ci_green": True,
        "scope_clean": True,
        "review_ready": True,
        "merge_ready": True,
    }
    result_path = tmp_path / "r.json"
    _write_result(result_path, result)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [result_path], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_READY
    assert packet["matched_result_count"] == 1
    m = packet["metrics"]
    assert m["tasks_with_results"] == 1
    assert m["patches_produced"] == 1
    assert m["scope_clean_count"] == 1
    assert m["ci_green_count"] == 1
    assert m["review_ready_count"] == 1
    assert m["merge_ready_count"] == 1
    # The single matched task should be removed from missing_result_task_ids
    assert packet["missing_result_task_ids"] == [
        "task-B", "task-C", "task-D", "task-E"
    ]


# ---------------------------------------------------------------------------
# 4. result for unknown task
# ---------------------------------------------------------------------------


def test_result_for_unknown_task_returns_hold_result_invalid(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    # Only unknown results; no results match any corpus task.
    result = {
        "task_id": "task-DOES-NOT-EXIST",
        "status": "PASS",
        "changed_files": ["docs/x.md"],
    }
    result_path = tmp_path / "r.json"
    _write_result(result_path, result)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [result_path], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_HOLD_RESULT_INVALID
    # All corpus tasks remain missing
    assert sorted(packet["missing_result_task_ids"]) == [
        "task-A", "task-B", "task-C", "task-D", "task-E"
    ]
    # Error count includes the unknown result
    assert packet["metrics"]["error_count"] >= 1


def test_mixed_known_and_unknown_results_soft_warns(tmp_path: Path) -> None:
    """If at least one result matches a corpus task, unknown results become warnings (status READY)."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    good = {"task_id": "task-A", "status": "PASS", "changed_files": ["docs/x.md"]}
    bad = {"task_id": "task-UNKNOWN", "status": "PASS", "changed_files": ["x"]}
    p1 = tmp_path / "good.json"; _write_result(p1, good)
    p2 = tmp_path / "bad.json"; _write_result(p2, bad)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [p1, p2], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    # Mixed case: at least one matched → status READY with a warning in errors
    assert packet["status"] == mod.STATUS_READY
    assert any("not in the corpus" in e for e in packet["errors"])


# ---------------------------------------------------------------------------
# 5. scope violation
# ---------------------------------------------------------------------------


def test_scope_violation_is_counted(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    # task-A allows only docs/x.md; changed_files include a forbidden .py file
    result = {
        "task_id": "task-A",
        "status": "PASS",
        "changed_files": ["docs/x.md", "scripts/local/inject.py"],  # inject.py forbidden
        "ci_green": True,
        "scope_clean": True,
    }
    result_path = tmp_path / "r.json"
    _write_result(result_path, result)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [result_path], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_READY
    # scope_clean_count should NOT include this task (per compute_task_record)
    m = packet["metrics"]
    assert m["scope_clean_count"] == 0
    # Find the task record and confirm violation is recorded
    rec = next(r for r in packet["tasks"] if r["task_id"] == "task-A")
    assert "scripts/local/inject.py" in rec["scope_violations"]
    # The task's own scope_clean flag is False
    assert rec["scope_clean"] is False


def test_scope_violation_via_disallowed_path(tmp_path: Path) -> None:
    """A file that matches NO allowed pattern is also a violation."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    result = {
        "task_id": "task-A",
        "status": "PASS",
        "changed_files": ["docs/x.md", "unrelated/random.txt"],
    }
    result_path = tmp_path / "r.json"
    _write_result(result_path, result)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [result_path], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    rec = next(r for r in packet["tasks"] if r["task_id"] == "task-A")
    assert "unrelated/random.txt" in rec["scope_violations"]


# ---------------------------------------------------------------------------
# 6. hold status
# ---------------------------------------------------------------------------


def test_hold_status_is_counted(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    result = {
        "task_id": "task-B",
        "status": "HOLD",
        "hold_reason": "waiting on review",
    }
    result_path = tmp_path / "r.json"
    _write_result(result_path, result)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [result_path], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    m = packet["metrics"]
    assert m["hold_count"] == 1
    assert m["tasks_with_results"] == 1
    # A HOLD result should not increment pass-side metrics
    assert m["patches_produced"] == 0
    assert m["merge_ready_count"] == 0
    rec = next(r for r in packet["tasks"] if r["task_id"] == "task-B")
    assert rec["result_status"] == "HOLD"
    assert rec["hold_reason"] == "waiting on review"


# ---------------------------------------------------------------------------
# 7. human cleanup required
# ---------------------------------------------------------------------------


def test_human_cleanup_required_is_counted(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    result = {
        "task_id": "task-C",
        "status": "PASS",
        "changed_files": ["tests/test_x.py"],
        "tests_passed": 1,
        "ci_green": True,
        "scope_clean": True,
        "review_ready": True,
        "merge_ready": True,
        "human_cleanup_required": True,
    }
    result_path = tmp_path / "r.json"
    _write_result(result_path, result)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [result_path], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    m = packet["metrics"]
    assert m["human_cleanup_required_count"] == 1
    assert m["patches_produced"] == 1
    assert m["tests_passed_count"] == 1
    rec = next(r for r in packet["tasks"] if r["task_id"] == "task-C")
    assert rec["human_cleanup_required"] is True


# ---------------------------------------------------------------------------
# 8. JSON and Markdown artifacts are written
# ---------------------------------------------------------------------------


def test_json_and_markdown_artifacts_are_written(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [], json_out, md_out)
    assert rc == 0
    assert json_out.exists()
    assert md_out.exists()

    j = json.loads(json_out.read_text())
    assert j["packet_kind"] == mod.PACKET_KIND_EVAL
    assert j["schema_version"] == mod.SCHEMA_VERSION
    assert "metrics" in j
    assert "tasks" in j
    assert "errors" in j
    assert "recommendation" in j

    md = md_out.read_text()
    assert "# Real-Output Autocoder Eval Report" in md
    assert "REAL_OUTPUT_EVAL_READY" in md or "STATUS" in md.upper()
    assert "task-A" in md  # per-task table


# ---------------------------------------------------------------------------
# 9. source safety
# ---------------------------------------------------------------------------


def test_source_safety_no_gh_mutation_no_claude_no_shell_true() -> None:
    src = Path(mod.__file__).read_text(encoding="utf-8")
    eq_token = "="
    # Build forbidden tokens as concatenated strings so this very file does
    # not accidentally trip the grep.
    forbidden_literals = [
        "gh " + "pr merge",
        "gh " + "api",
        "gh " + "run watch",
        "gh " + "pr checks --watch",
        "git " + "push",
        "shell" + eq_token + "True",
        "claude" + "-code",
        "live" + " claude",
        "Live Claude",
        "enable" + "-real-claude-executor",
    ]
    for s in forbidden_literals:
        assert s not in src, f"source contains forbidden literal: {s!r}"

    # Also check the evaluator does not import subprocess at all (per spec).
    assert "import subprocess" not in src, "source must not import subprocess"
    assert "from subprocess" not in src, "source must not import subprocess"


# ---------------------------------------------------------------------------
# 10. CLI invalid args
# ---------------------------------------------------------------------------


def test_cli_invalid_missing_required_flag(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Missing --corpus should be a CLI error."""
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"
    # No --corpus, no --output-json, no --output-md
    with pytest.raises(SystemExit) as excinfo:
        mod.main([])
    assert excinfo.value.code == 2  # argparse error


def test_cli_invalid_args_writes_error_packet(tmp_path: Path) -> None:
    """When validation fails (e.g. whitespace-only --corpus), write an ERROR_INVALID_ARGS packet."""
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"
    rc = mod.main([
        "--corpus", "   ",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    assert rc == 2
    assert json_out.exists()
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_ERROR_INVALID_ARGS
    assert packet["recommendation"]


# ---------------------------------------------------------------------------
# 11. CLI runnable as a subprocess
# ---------------------------------------------------------------------------


def test_cli_subprocess_invocation(tmp_path: Path) -> None:
    """A real subprocess invocation should produce a valid report."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    repo_root = Path(__file__).parent.parent
    script = repo_root / "scripts" / "local" / "run_autocoder_real_output_eval.py"
    proc = subprocess.run(
        [
            sys.executable, str(script),
            "--corpus", str(corpus_path),
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_READY
    assert packet["task_count"] == 5


# ---------------------------------------------------------------------------
# 12. P2 review-thread fixes
# ---------------------------------------------------------------------------


def test_malformed_only_result_packets_return_hold_result_invalid(tmp_path: Path) -> None:
    """All result packets with non-string task_id → HOLD_RESULT_INVALID.

    Specifically, this must NOT fall through to REAL_OUTPUT_EVAL_READY.
    """
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    # Both results have a non-string task_id (an int) — load_result will
    # reject them as structurally invalid, putting both into invalid_result_packets.
    r1 = {"task_id": 12345, "status": "PASS"}
    r2 = {"task_id": 67890, "status": "PASS"}
    p1 = tmp_path / "r1.json"; _write_result(p1, r1)
    p2 = tmp_path / "r2.json"; _write_result(p2, r2)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [p1, p2], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_HOLD_RESULT_INVALID
    # Both packet paths are reported in invalid_result_packets
    assert len(packet["invalid_result_packets"]) == 2
    # matched_result_count is 0 because no result matched a corpus task
    assert packet["matched_result_count"] == 0
    # The errors should explicitly mention structural invalidity
    assert any("structurally invalid" in e for e in packet["errors"])


def test_malformed_task_id_is_structurally_invalid(tmp_path: Path) -> None:
    """A result packet whose task_id is structurally invalid (not a non-empty
    string) is treated as a structurally invalid packet and forces HOLD, even
    when every other field is well-formed. This pins the contract that
    task_id validation is part of structural validity."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    # Several variations of invalid task_id; all must be treated as invalid.
    cases = [
        {"task_id": 12345, "status": "PASS"},         # int instead of string
        {"task_id": ["a", "b"], "status": "PASS"},   # list instead of string
        {"task_id": {"k": "v"}, "status": "PASS"},    # dict instead of string
        {"task_id": None, "status": "PASS"},          # explicit null
        {"task_id": "", "status": "PASS"},            # empty string
        {"status": "PASS"},                            # missing task_id
    ]
    paths = []
    for i, r in enumerate(cases):
        p = tmp_path / f"r{i}.json"
        _write_result(p, r)
        paths.append(p)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, paths, json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_HOLD_RESULT_INVALID
    # All 6 packets are reported as invalid
    assert len(packet["invalid_result_packets"]) == 6
    assert packet["matched_result_count"] == 0
    # The MD report should also list invalid result packets under the renamed section
    md = md_out.read_text(encoding="utf-8")
    assert "## Invalid result packets" in md


def test_invalid_result_packets_field_replaces_unknown_result_paths(tmp_path: Path) -> None:
    """The output packet field is named ``invalid_result_packets`` (not the
    older ``unknown_result_paths``). The OLD field name MUST NOT appear
    anywhere in the report."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    # Force an invalid packet so the field is populated.
    r = {"task_id": 42, "status": "PASS"}
    p = tmp_path / "r.json"; _write_result(p, r)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [p], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    # The new field exists
    assert "invalid_result_packets" in packet
    assert len(packet["invalid_result_packets"]) == 1
    # The old field MUST NOT exist
    assert "unknown_result_paths" not in packet


def test_mixed_valid_and_malformed_result_packets_return_hold_result_invalid(tmp_path: Path) -> None:
    """Even one structurally invalid result packet must trigger HOLD_RESULT_INVALID,
    even when another result packet is well-formed and references a real corpus task."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    good = {"task_id": "task-A", "status": "PASS", "changed_files": ["docs/x.md"]}
    malformed = {"task_id": 999, "status": "PASS"}  # non-string task_id
    p1 = tmp_path / "good.json"; _write_result(p1, good)
    p2 = tmp_path / "bad.json"; _write_result(p2, malformed)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [p1, p2], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    # HOLD, not READY — the malformed packet is a hard signal
    assert packet["status"] == mod.STATUS_HOLD_RESULT_INVALID
    assert len(packet["invalid_result_packets"]) == 1
    # Even with one valid match, the malformed packet forces HOLD
    assert packet["matched_result_count"] == 1


def test_unknown_task_id_does_not_increase_matched_result_count(tmp_path: Path) -> None:
    """matched_result_count is the in-corpus intersection, not a count of all
    result packets. Results referencing unknown task_ids must NOT inflate it."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    # Two valid-shape results, both referencing task_ids that are NOT in the corpus
    r1 = {"task_id": "totally-bogus-1", "status": "PASS"}
    r2 = {"task_id": "totally-bogus-2", "status": "PASS"}
    p1 = tmp_path / "r1.json"; _write_result(p1, r1)
    p2 = tmp_path / "r2.json"; _write_result(p2, r2)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [p1, p2], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    # All-unknown case still returns HOLD per the existing extra_results path
    assert packet["status"] == mod.STATUS_HOLD_RESULT_INVALID
    # But matched_result_count is 0 — not 2
    assert packet["matched_result_count"] == 0
    # Both task_ids surface in errors as "unknown"
    assert any("unknown task_ids" in e for e in packet["errors"])


def test_known_task_id_does_increase_matched_result_count(tmp_path: Path) -> None:
    """A result whose task_id IS in the corpus must increment matched_result_count
    (and not be conflated with unknown results)."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    known = {"task_id": "task-B", "status": "PASS", "changed_files": ["scripts/local/h.py"]}
    p1 = tmp_path / "known.json"; _write_result(p1, known)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [p1], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    assert packet["status"] == mod.STATUS_READY
    assert packet["matched_result_count"] == 1
    # The matched task should be removed from missing_result_task_ids
    assert "task-B" not in packet["missing_result_task_ids"]


def test_mixed_known_and_unknown_does_not_inflate_matched_count(tmp_path: Path) -> None:
    """In a mixed valid+unknown-result set, matched_result_count counts only
    the in-corpus matches; the unknown task_id result is tracked separately
    in errors and does NOT inflate matched_result_count."""
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path, VALID_CORPUS)

    good = {"task_id": "task-A", "status": "PASS", "changed_files": ["docs/x.md"]}
    bad = {"task_id": "task-NOT-IN-CORPUS", "status": "PASS", "changed_files": ["x"]}
    p1 = tmp_path / "good.json"; _write_result(p1, good)
    p2 = tmp_path / "bad.json"; _write_result(p2, bad)
    json_out = tmp_path / "eval.json"
    md_out = tmp_path / "eval.md"

    rc = _run_eval(corpus_path, [p1, p2], json_out, md_out)
    assert rc == 0
    packet = json.loads(json_out.read_text())
    # Mixed case with at least one match → READY (with a warning in errors)
    assert packet["status"] == mod.STATUS_READY
    # matched_result_count is 1 — only the known task, NOT 2
    assert packet["matched_result_count"] == 1
    assert any("not in the corpus" in e for e in packet["errors"])


def test_corpus_allowed_files_have_no_angle_bracket_placeholders() -> None:
    """The shipped real-output corpus must not contain any angle-bracket
    placeholder paths (e.g. ``<new_helper>.py``) — those are literal strings
    that don't match real files. All allowed_files entries should be
    matchable globs or concrete paths."""
    repo_root = Path(__file__).parent.parent
    corpus_path = repo_root / "corpus" / "autocoder-real-output-v0.json"
    assert corpus_path.exists(), f"corpus file missing: {corpus_path}"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    placeholders: List[str] = []
    for task in corpus.get("tasks", []):
        tid = task.get("task_id", "?")
        for fld in ("allowed_files", "forbidden_files", "expected_artifacts", "expected_tests"):
            for entry in task.get(fld, []) or []:
                if not isinstance(entry, str):
                    continue
                if "<" in entry and ">" in entry:
                    placeholders.append(f"{tid}.{fld}: {entry!r}")
    assert not placeholders, (
        "corpus contains angle-bracket placeholders that won't match real files: "
        + "; ".join(placeholders)
    )
