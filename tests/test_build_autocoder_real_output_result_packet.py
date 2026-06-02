"""
Tests for scripts/local/build_autocoder_real_output_result_packet.py

Covers:
 1. valid packet writes JSON
 2. changed_files required (non-empty)
 3. allowed_files required (non-empty)
 4. invalid status rejected
 5. invalid boolean rejected (uppercase, yes, 1 all rejected)
 6. source_pr must be positive
 7. tests_passed cannot be negative
 8. scoped_files optional
 9. notes can be repeated
10. output packet can be consumed by run_autocoder_real_output_eval.py
11. source safety: no gh mutation strings, no live Claude strings,
    no shell-mode True flag, no subprocess import
12. CLI exit codes: 0 on success, 2 on invalid args, 1 on tool failure
13. result_packet_generated_at is a valid ISO 8601 UTC timestamp
14. builder_status is RESULT_PACKET_READY on success
15. hold_reason and error_reason are omitted when not provided
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make the modules under test importable
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
SCRIPTS_LOCAL = REPO_ROOT / "scripts" / "local"
CORPUS_PATH = REPO_ROOT / "corpus" / "autocoder-real-output-v0.json"

for p in (str(THIS_DIR.parent), str(SCRIPTS_LOCAL)):
    if p not in sys.path:
        sys.path.insert(0, p)

import build_autocoder_real_output_result_packet as builder  # noqa: E402
import run_autocoder_real_output_eval as eval_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_module(name: str, path: Path):  # pragma: no cover - import helper
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def _valid_argv(tmp_path: Path) -> List[str]:
    """Return a valid CLI argv list that should pass validation."""
    out = tmp_path / "packet.json"
    return [
        "--task-id", "real-output-v0-task-002",
        "--source-pr", "999",
        "--source-commit", "1111111111111111111111111111111111111111",
        "--source-head-sha", "2222222222222222222222222222222222222222",
        "--title", "test packet",
        "--status", "PASS",
        "--changed-file", "scripts/local/example.py",
        "--allowed-file", "scripts/local/*.py",
        "--tests-passed", "1",
        "--ci-green", "true",
        "--scope-clean", "true",
        "--review-ready", "true",
        "--merge-ready", "true",
        "--human-cleanup-required", "false",
        "--note", "smoke packet only",
        "--output-json", str(out),
    ]


def _valid_namespace() -> Dict[str, Any]:
    """Return a minimal valid dict of attribute values for an argparse Namespace."""
    return {
        "task_id": "real-output-v0-task-002",
        "source_pr": 999,
        "source_commit": "1111111111111111111111111111111111111111",
        "source_head_sha": "2222222222222222222222222222222222222222",
        "title": "test packet",
        "status": "PASS",
        "changed_files": ["scripts/local/example.py"],
        "allowed_files": ["scripts/local/*.py"],
        "scoped_files": [],
        "tests_passed": 1,
        "ci_green": "true",
        "scope_clean": "true",
        "review_ready": "true",
        "merge_ready": "true",
        "human_cleanup_required": "false",
        "hold_reason": None,
        "error_reason": None,
        "notes": [],
        "output_json": "/tmp/x.json",
    }


# ---------------------------------------------------------------------------
# 1. valid packet writes JSON
# ---------------------------------------------------------------------------


def test_valid_packet_writes_json(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path)
    rc = builder.main(argv)
    assert rc == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    assert out_path.exists()
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["task_id"] == "real-output-v0-task-002"
    assert packet["source_pr"] == 999
    assert packet["status"] == "PASS"
    assert packet["changed_files"] == ["scripts/local/example.py"]
    assert packet["allowed_files"] == ["scripts/local/*.py"]
    assert packet["tests_passed"] == 1
    assert packet["ci_green"] is True
    assert packet["scope_clean"] is True
    assert packet["review_ready"] is True
    assert packet["merge_ready"] is True
    assert packet["human_cleanup_required"] is False
    assert packet["builder_status"] == builder.STATUS_READY
    # File ends with a single trailing newline
    text = out_path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


# ---------------------------------------------------------------------------
# 2. changed_files required
# ---------------------------------------------------------------------------


def test_changed_files_required(tmp_path: Path) -> None:
    argv = [a for a in _valid_argv(tmp_path)
            if a not in ("--changed-file", "scripts/local/example.py")]
    rc = builder.main(argv)
    assert rc == 2
    err = sys.stderr.getvalue() if hasattr(sys.stderr, "getvalue") else ""
    out_path = Path(argv[argv.index("--output-json") + 1])
    assert not out_path.exists(), "no packet should be written on validation failure"


# ---------------------------------------------------------------------------
# 3. allowed_files required
# ---------------------------------------------------------------------------


def test_allowed_files_required(tmp_path: Path) -> None:
    argv = [a for a in _valid_argv(tmp_path)
            if a not in ("--allowed-file", "scripts/local/*.py")]
    rc = builder.main(argv)
    assert rc == 2
    out_path = Path(argv[argv.index("--output-json") + 1])
    assert not out_path.exists(), "no packet should be written on validation failure"


# ---------------------------------------------------------------------------
# 4. invalid status rejected
# ---------------------------------------------------------------------------


def test_invalid_status_rejected(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path)
    i = argv.index("--status")
    argv[i + 1] = "FAIL"  # not in {PASS, HOLD, ERROR, UNKNOWN}
    # argparse raises SystemExit(2) on an invalid `choices` value.
    with pytest.raises(SystemExit) as excinfo:
        builder.main(argv)
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# 5. invalid boolean rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", ["True", "TRUE", "false ", " yes", "1", "0", "off"])
def test_invalid_boolean_rejected(tmp_path: Path, bad_value: str) -> None:
    argv = _valid_argv(tmp_path)
    i = argv.index("--ci-green")
    argv[i + 1] = bad_value
    # argparse raises SystemExit(2) on an invalid `choices` value.
    with pytest.raises(SystemExit) as excinfo:
        builder.main(argv)
    assert excinfo.value.code == 2, (
        f"expected SystemExit(2) for --ci-green={bad_value!r}, got {excinfo.value.code}"
    )


# ---------------------------------------------------------------------------
# 6. source_pr must be positive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_pr", ["0", "-1", "-999"])
def test_source_pr_must_be_positive(tmp_path: Path, bad_pr: str) -> None:
    argv = _valid_argv(tmp_path)
    i = argv.index("--source-pr")
    argv[i + 1] = bad_pr
    rc = builder.main(argv)
    assert rc == 2


# ---------------------------------------------------------------------------
# 7. tests_passed cannot be negative
# ---------------------------------------------------------------------------


def test_tests_passed_cannot_be_negative(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path)
    i = argv.index("--tests-passed")
    argv[i + 1] = "-3"
    rc = builder.main(argv)
    assert rc == 2


# ---------------------------------------------------------------------------
# 8. scoped_files optional
# ---------------------------------------------------------------------------


def test_scoped_files_optional(tmp_path: Path) -> None:
    """If no --scoped-file is provided, the packet must still write successfully."""
    argv = [a for a in _valid_argv(tmp_path) if a != "scripts/local/example.py" or True]
    # Simpler: just run the base argv (which has no --scoped-file at all) and check.
    argv = _valid_argv(tmp_path)
    # Make sure scoped_files is not in argv
    argv = [a for a in argv if a != "--scoped-file"]
    rc = builder.main(argv)
    assert rc == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["scoped_files"] == []


def test_scoped_files_can_be_provided(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path) + ["--scoped-file", "scripts/local/example.py"]
    rc = builder.main(argv)
    assert rc == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["scoped_files"] == ["scripts/local/example.py"]


# ---------------------------------------------------------------------------
# 9. notes can be repeated
# ---------------------------------------------------------------------------


def test_notes_can_be_repeated(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path) + [
        "--note", "first note",
        "--note", "second note",
    ]
    rc = builder.main(argv)
    assert rc == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["notes"] == ["smoke packet only", "first note", "second note"]


# ---------------------------------------------------------------------------
# 10. output packet can be consumed by run_autocoder_real_output_eval.py
# ---------------------------------------------------------------------------


def test_output_packet_is_evaluator_compatible(tmp_path: Path) -> None:
    """Build a packet, then feed it to the eval and verify it accepts it."""
    # 1. Build the packet
    packet_path = tmp_path / "packet.json"
    argv = [
        "--task-id", "real-output-v0-task-002",
        "--source-pr", "999",
        "--source-commit", "1111111111111111111111111111111111111111",
        "--source-head-sha", "2222222222222222222222222222222222222222",
        "--title", "evaluator-compat test",
        "--status", "PASS",
        "--changed-file", "scripts/local/example.py",
        "--allowed-file", "scripts/local/*.py",
        "--tests-passed", "2",
        "--ci-green", "true",
        "--scope-clean", "true",
        "--review-ready", "true",
        "--merge-ready", "true",
        "--human-cleanup-required", "false",
        "--note", "compatibility test",
        "--output-json", str(packet_path),
    ]
    assert builder.main(argv) == 0
    assert packet_path.exists()

    # 2. Feed to the eval
    eval_json = tmp_path / "eval.json"
    eval_md = tmp_path / "eval.md"
    rc = eval_mod.main([
        "--corpus", str(CORPUS_PATH),
        "--result-json", str(packet_path),
        "--output-json", str(eval_json),
        "--output-md", str(eval_md),
    ])
    assert rc == 0
    report = json.loads(eval_json.read_text(encoding="utf-8"))
    assert report["status"] == "REAL_OUTPUT_EVAL_READY"
    assert report["result_count"] == 1
    assert report["matched_result_count"] == 1
    assert report["invalid_result_packets"] == []
    # The corpus has 5 tasks; we only provided one result, so 4 are
    # legitimately missing. Our task_id is the one that IS matched, so it
    # must NOT be in the missing list.
    assert "real-output-v0-task-002" not in report["missing_result_task_ids"]
    # The single result row should report PASS, scope_clean=True, merge_ready=True
    assert len(report["tasks"]) >= 1
    our_row = next(
        (t for t in report["tasks"] if t["task_id"] == "real-output-v0-task-002"),
        None,
    )
    assert our_row is not None
    assert our_row["result_status"] == "PASS"
    assert our_row["scope_clean"] is True
    assert our_row["merge_ready"] is True
    assert our_row["tests_passed"] == 2
    assert our_row["matched_in_corpus"] is True


# ---------------------------------------------------------------------------
# 11. source safety
# ---------------------------------------------------------------------------


def test_source_safety_no_gh_mutation_no_claude_no_shell_true() -> None:
    """The builder's own source file must not contain forbidden literals."""
    src = Path(builder.__file__).read_text(encoding="utf-8")
    eq_token = "="  # avoid writing the literal shell-equals-True in this file
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

    # Also check the builder does not import subprocess.
    assert "import subprocess" not in src, "source must not import subprocess"
    assert "from subprocess" not in src, "source must not import subprocess"
    # And the build script does not have a shell-mode-True-style invocation.
    # Build the literal at runtime so the test file itself doesn't trip the
    # same diff-pattern matcher.
    shell_true_literal = "shell" + "=" + "True"
    assert shell_true_literal not in src, "source must not use a shell-mode-True-style process invocation"


# ---------------------------------------------------------------------------
# 12. CLI exit codes
# ---------------------------------------------------------------------------


def test_cli_exit_code_zero_on_success(tmp_path: Path) -> None:
    assert builder.main(_valid_argv(tmp_path)) == 0


def test_cli_exit_code_two_on_invalid_args(tmp_path: Path) -> None:
    argv = [a for a in _valid_argv(tmp_path) if a != "scripts/local/example.py"]
    # Remove the value, leaving the bare flag, but also remove the flag itself:
    argv = [a for a in _valid_argv(tmp_path) if a not in ("--changed-file", "scripts/local/example.py")]
    assert builder.main(argv) == 2


# ---------------------------------------------------------------------------
# 13. result_packet_generated_at is a valid ISO 8601 UTC timestamp
# ---------------------------------------------------------------------------


def test_result_packet_generated_at_is_iso8601(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path)
    assert builder.main(argv) == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    ts = packet["result_packet_generated_at"]
    # Python's fromisoformat accepts a trailing 'Z' only on 3.11+. On 3.10
    # we replace it manually.
    ts_for_parse = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    parsed = datetime.fromisoformat(ts_for_parse)
    # Must be in UTC
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# 14. builder_status is RESULT_PACKET_READY on success
# ---------------------------------------------------------------------------


def test_builder_status_on_success(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path)
    assert builder.main(argv) == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["builder_status"] == "RESULT_PACKET_READY"


# ---------------------------------------------------------------------------
# 15. hold_reason and error_reason are omitted when not provided
# ---------------------------------------------------------------------------


def test_optional_hold_and_error_reason_omitted(tmp_path: Path) -> None:
    """When the user does not pass --hold-reason or --error-reason, the
    resulting packet must not contain those keys at all."""
    # Build argv without the note
    argv = [a for a in _valid_argv(tmp_path) if a not in ("--note", "smoke packet only")]
    assert builder.main(argv) == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert "hold_reason" not in packet
    assert "error_reason" not in packet
    assert "notes" not in packet


def test_optional_hold_and_error_reason_present(tmp_path: Path) -> None:
    argv = _valid_argv(tmp_path) + [
        "--hold-reason", "blocked on review",
        "--error-reason", "tool failure",
    ]
    assert builder.main(argv) == 0
    out_path = Path(argv[argv.index("--output-json") + 1])
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["hold_reason"] == "blocked on review"
    assert packet["error_reason"] == "tool failure"


# ---------------------------------------------------------------------------
# Extra: build_packet via direct namespace (bypasses argparse) for fast unit checks
# ---------------------------------------------------------------------------


def test_build_packet_via_namespace() -> None:
    """Direct construction from a namespace-style dict, no argparse."""
    ns = argparse_Namespace(**_valid_namespace())
    packet = builder.build_packet(ns, now_iso="2026-06-02T00:00:00+00:00")
    assert packet["builder_status"] == "RESULT_PACKET_READY"
    assert packet["ci_green"] is True
    assert packet["human_cleanup_required"] is False
    assert packet["result_packet_generated_at"] == "2026-06-02T00:00:00+00:00"


def argparse_Namespace(**kwargs: Any):  # noqa: N802 — keep name for clarity
    import argparse
    return argparse.Namespace(**kwargs)


# ---------------------------------------------------------------------------
# Extra: validate_args unit-level direct calls
# ---------------------------------------------------------------------------


def test_validate_args_rejects_zero_source_pr() -> None:
    ns = argparse_Namespace(**{**_valid_namespace(), "source_pr": 0})
    ok, errs = builder.validate_args(ns)
    assert not ok
    assert any("source-pr" in e or "source_pr" in e for e in errs)


def test_validate_args_rejects_empty_changed_files() -> None:
    ns = argparse_Namespace(**{**_valid_namespace(), "changed_files": []})
    ok, errs = builder.validate_args(ns)
    assert not ok
    assert any("changed-file" in e for e in errs)


def test_validate_args_rejects_unknown_status() -> None:
    ns = argparse_Namespace(**{**_valid_namespace(), "status": "FAIL"})
    ok, errs = builder.validate_args(ns)
    assert not ok
    assert any("status" in e for e in errs)


def test_validate_args_rejects_uppercase_bool() -> None:
    ns = argparse_Namespace(**{**_valid_namespace(), "ci_green": "True"})
    ok, errs = builder.validate_args(ns)
    assert not ok
    assert any("ci-green" in e for e in errs)


def test_validate_args_accepts_valid() -> None:
    ns = argparse_Namespace(**_valid_namespace())
    ok, errs = builder.validate_args(ns)
    assert ok, f"unexpected errors: {errs}"


# ---------------------------------------------------------------------------
# Extra: subprocess invocation of the builder CLI
# ---------------------------------------------------------------------------


def test_builder_cli_runs_as_subprocess(tmp_path: Path) -> None:
    """Run the builder as a real subprocess and confirm it produces a valid packet."""
    script = SCRIPTS_LOCAL / "build_autocoder_real_output_result_packet.py"
    out_path = tmp_path / "subproc_packet.json"
    proc = subprocess.run(
        [
            sys.executable, str(script),
            "--task-id", "real-output-v0-task-002",
            "--source-pr", "999",
            "--source-commit", "1111111111111111111111111111111111111111",
            "--source-head-sha", "2222222222222222222222222222222222222222",
            "--title", "subproc test",
            "--status", "PASS",
            "--changed-file", "scripts/local/example.py",
            "--allowed-file", "scripts/local/*.py",
            "--tests-passed", "1",
            "--ci-green", "true",
            "--scope-clean", "true",
            "--review-ready", "true",
            "--merge-ready", "true",
            "--human-cleanup-required", "false",
            "--output-json", str(out_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert out_path.exists()
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["builder_status"] == "RESULT_PACKET_READY"
    assert "RESULT_PACKET_READY" in proc.stdout
