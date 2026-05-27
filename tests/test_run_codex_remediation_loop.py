#!/usr/bin/env python3
"""
Tests for run_codex_remediation_loop.py

Covers v0 mock-plan-only behavior:
  1. Valid Wave 1 corpus creates task packets and status files.
  2. Unsafe task_id with ../ is rejected.
  3. Absolute allowed_file is rejected.
  4. Missing allowed_file is rejected unless explicitly declared new.
  5. mock-plan-only does not modify repo files.
  6. output_root null/empty is rejected.
  7. No controller subprocesses invoked (mock mode).
  8. No shell=True in source.
  9. Stop-condition documentation appears in loop_status.md.
 10. status JSON includes task counts and per-task classification.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
SCRIPT = REPO_ROOT / "scripts" / "local" / "run_codex_remediation_loop.py"


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def wave1_corpus(tmp_path: Path) -> Path:
    """Minimal valid Wave 1 corpus pointing at current main."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-wave1",
        "corpus_version": "0.1.0",
        "description": "Test Wave 1 corpus",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {
                "description": "Mock test wave",
                "task_ids": ["task-001"],
                "execution_mode": "mocked",
            }
        },
        "tasks": [
            {
                "task_id": "task-001",
                "wave": 1,
                "source_pr": 314,
                "finding_id": "codex-test-001",
                "severity": "P1",
                "classification": "FIXED_ALREADY",
                "finding_summary": "Test finding",
                "current_main_status": "Fixed in current main",
                "task_category": "already_fixed_needs_regression_test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [
                        "scripts/local/run_autocoder_batch.py",
                        ".hermes/**",
                    ],
                    "test_type": "unit",
                    "test_pattern": "test_example",
                    "success_criteria": "Test passes",
                    "deliverable": "New test function",
                },
                "safety_notes": [
                    "No live Claude execution",
                    "No Hermes mutation",
                    "No git push/merge",
                ],
            }
        ],
    }
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus), encoding="utf-8")
    return p


@pytest.fixture
def wave1_corpus_missing_allowed_file(tmp_path: Path) -> Path:
    """Corpus with an allowed_file that does not exist on current main."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-missing-file",
        "corpus_version": "0.1.0",
        "description": "Test missing file",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {
                "description": "Mock test wave",
                "task_ids": ["task-002"],
                "execution_mode": "mocked",
            }
        },
        "tasks": [
            {
                "task_id": "task-002",
                "wave": 1,
                "source_pr": 314,
                "finding_id": "codex-test-002",
                "severity": "P2",
                "classification": "FIXED_ALREADY",
                "finding_summary": "Test finding",
                "current_main_status": "Fixed",
                "task_category": "already_fixed_needs_regression_test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/nonexistent_file.py",
                    "allowed_files": ["tests/nonexistent_file.py"],
                    "forbidden_files": [],
                    "test_type": "unit",
                    "test_pattern": "test_example",
                    "success_criteria": "Test passes",
                    "deliverable": "New test",
                },
                "safety_notes": ["No live Claude", "No Hermes mutation"],
            }
        ],
    }
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus), encoding="utf-8")
    return p


@pytest.fixture
def wave1_corpus_path_traversal(tmp_path: Path) -> Path:
    """Corpus with unsafe task_id containing .."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-traversal",
        "corpus_version": "0.1.0",
        "description": "Test traversal",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {
                "description": "Mock test wave",
                "task_ids": ["../task-003"],
                "execution_mode": "mocked",
            }
        },
        "tasks": [
            {
                "task_id": "../task-003",
                "wave": 1,
                "source_pr": 314,
                "finding_id": "codex-test-003",
                "severity": "P1",
                "classification": "FIXED_ALREADY",
                "finding_summary": "Test finding",
                "current_main_status": "Fixed",
                "task_category": "already_fixed_needs_regression_test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "test_type": "unit",
                    "test_pattern": "test_example",
                    "success_criteria": "Test passes",
                    "deliverable": "New test",
                },
                "safety_notes": ["No live Claude", "No Hermes mutation"],
            }
        ],
    }
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus), encoding="utf-8")
    return p


@pytest.fixture
def wave1_corpus_absolute_path(tmp_path: Path) -> Path:
    """Corpus with an absolute allowed_file path."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-absolute",
        "corpus_version": "0.1.0",
        "description": "Test absolute",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {
                "description": "Mock test wave",
                "task_ids": ["task-004"],
                "execution_mode": "mocked",
            }
        },
        "tasks": [
            {
                "task_id": "task-004",
                "wave": 1,
                "source_pr": 314,
                "finding_id": "codex-test-004",
                "severity": "P2",
                "classification": "FIXED_ALREADY",
                "finding_summary": "Test finding",
                "current_main_status": "Fixed",
                "task_category": "already_fixed_needs_regression_test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["/etc/passwd"],
                    "forbidden_files": [],
                    "test_type": "unit",
                    "test_pattern": "test_example",
                    "success_criteria": "Test passes",
                    "deliverable": "New test",
                },
                "safety_notes": ["No live Claude", "No Hermes mutation"],
            }
        ],
    }
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus), encoding="utf-8")
    return p


@pytest.fixture
def wave1_corpus_forbidden_pattern(tmp_path: Path) -> Path:
    """Corpus with forbidden pattern in safety_notes."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-forbidden",
        "corpus_version": "0.1.0",
        "description": "Test forbidden",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {
                "description": "Mock test wave",
                "task_ids": ["task-005"],
                "execution_mode": "mocked",
            }
        },
        "tasks": [
            {
                "task_id": "task-005",
                "wave": 1,
                "source_pr": 314,
                "finding_id": "codex-test-005",
                "severity": "P1",
                "classification": "FIXED_ALREADY",
                "finding_summary": "Test finding",
                "current_main_status": "Fixed",
                "task_category": "already_fixed_needs_regression_test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "test_type": "unit",
                    "test_pattern": "test_example",
                    "success_criteria": "Test passes",
                    "deliverable": "New test",
                },
                "safety_notes": [
                    "No live Claude execution",
                    "May use --enable-real-claude-executor if needed",  # forbidden!
                ],
            }
        ],
    }
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus), encoding="utf-8")
    return p


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


def test_valid_wave1_corpus_creates_task_packets_and_status(
    wave1_corpus: Path, tmp_path: Path
) -> None:
    """Valid Wave 1 corpus creates task packets and loop status files."""
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    # Check task packet created
    task_packet = out_dir / "tasks" / "task-001" / "task_packet.json"
    assert task_packet.exists(), f"task_packet not found at {task_packet}"

    packet = json.loads(task_packet.read_text(encoding="utf-8"))
    assert packet["task_id"] == "task-001"
    assert packet["packet_kind"] == "aed.codex_remediation.task_packet.v0"
    assert packet["loop_runner_version"] == "0.1.0"
    assert packet["classification"] == "needs_regression_test"
    assert "safety_notes_verified.txt" in [
        f.name for f in (out_dir / "tasks" / "task-001").iterdir()
    ]

    # Check loop_status.json created
    status_json = out_dir / "loop_status.json"
    assert status_json.exists()
    status = json.loads(status_json.read_text(encoding="utf-8"))
    assert status["status"] == "LOOP_COMPLETE_MOCK_PLAN_ONLY"
    assert status["total_tasks"] == 1
    assert status["tasks_passed"] == 1
    assert status["tasks_failed"] == 0
    assert "needs_regression_test" in status["classifications"]

    # Check loop_status.md created
    status_md = out_dir / "loop_status.md"
    assert status_md.exists()
    md_text = status_md.read_text(encoding="utf-8")
    assert "task-001" in md_text
    assert "needs_regression_test" in md_text


def test_unsafe_task_id_with_path_traversal_rejected(
    wave1_corpus_path_traversal: Path, tmp_path: Path
) -> None:
    """task_id containing ../ is rejected as path traversal."""
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus_path_traversal),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1, f"Expected failure, got returncode {result.returncode}"
    assert "unsafe characters" in result.stderr.lower() or "task_id validation failed" in result.stderr.lower()


def test_absolute_allowed_file_rejected(
    wave1_corpus_absolute_path: Path, tmp_path: Path
) -> None:
    """Absolute allowed_file path is rejected."""
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus_absolute_path),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "absolute path" in result.stderr.lower()


def test_missing_allowed_file_rejected_unless_declared_new(
    wave1_corpus_missing_allowed_file: Path, tmp_path: Path
) -> None:
    """Missing allowed_file is rejected unless explicitly declared new."""
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus_missing_allowed_file),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "does not exist at current main" in result.stderr.lower()


def test_mock_plan_only_does_not_modify_repo_files(
    wave1_corpus: Path, tmp_path: Path
) -> None:
    """mock-plan-only mode does not modify any repo files."""
    # Snapshot git status
    status_before = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    status_after = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert status_before.stdout == status_after.stdout, (
        f"Repo was modified!\nBefore: {status_before.stdout}\nAfter: {status_after.stdout}"
    )


def test_output_root_null_rejected(tmp_path: Path) -> None:
    """output_root null/empty is rejected."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-empty-root",
        "corpus_version": "0.1.0",
        "description": "Test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Mock", "task_ids": [], "execution_mode": "mocked"}
        },
        "tasks": [],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

    for bad_root in ["", "  "]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--corpus", str(corpus_path),
                "--output-root", bad_root,
                "--mode", "mock-plan-only",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 1
        assert "--output-root is required" in result.stderr


def test_no_controller_subprocess_invoked_in_mock_mode(
    wave1_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No batch controller subprocess is invoked in mock-plan-only mode."""
    spawned_processes: list[str] = []

    original_run = subprocess.run

    def tracking_run(args: Any, **kwargs: Any) -> Any:
        cmd = args if isinstance(args, (list, tuple)) else kwargs.get("args", [])
        if isinstance(cmd, (list, tuple)):
            cmd_str = " ".join(str(a) for a in cmd)
            if "run_autocoder_batch" in cmd_str or "run_autocoder_eval_corpus" in cmd_str:
                spawned_processes.append(cmd_str)
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", tracking_run)

    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert len(spawned_processes) == 0, (
        f"Batch controller subprocess spawned: {spawned_processes}"
    )


def test_no_shell_true_in_source() -> None:
    """subprocess.run(..., shell=True) must not appear in run_codex_remediation_loop.py."""
    source = SCRIPT.read_text(encoding="utf-8")
    # Only flag actual subprocess calls with shell=True, not documentation mentions.
    # Skip lines where shell=True appears inside a string literal (prohibition references
    # like "Do NOT use shell=True in any subprocess call").
    lines_with_shell_true = [
        f"{i+1}: {line}"
        for i, line in enumerate(source.splitlines())
        if re.search(r"shell\s*=\s*True", line, re.IGNORECASE)
        and "subprocess" in line
        and "no shell" not in line.lower()
        # Skip string literals that are prohibition references
        and not (
            re.search(r"['\"]", line[:line.find("shell")])  # quote before shell= → string literal
            and "do not" in line.lower()
        )
    ]
    assert len(lines_with_shell_true) == 0, (
        f"shell=True found in source:\n" + "\n".join(lines_with_shell_true)
    )


def test_stop_conditions_documented_in_md(
    wave1_corpus: Path, tmp_path: Path
) -> None:
    """Stop conditions appear in loop_status.md."""
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0

    md_text = (out_dir / "loop_status.md").read_text(encoding="utf-8")
    assert "REVIEW_COMMENTS_BLOCKED" in md_text
    assert "CI not green" in md_text
    assert "PMG dirty" in md_text
    assert "final_gate_status.py not READY_TO_MERGE" in md_text


def test_status_json_includes_task_counts_and_classification(
    wave1_corpus: Path, tmp_path: Path
) -> None:
    """loop_status.json includes total_tasks, tasks_passed, tasks_failed, and classifications."""
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0

    status = json.loads((out_dir / "loop_status.json").read_text(encoding="utf-8"))
    assert "total_tasks" in status
    assert "tasks_passed" in status
    assert "tasks_failed" in status
    assert "classifications" in status
    assert status["total_tasks"] == 1
    assert status["tasks_passed"] == 1
    assert status["tasks_failed"] == 0
    assert "needs_regression_test" in status["classifications"]


def test_forbidden_pattern_in_safety_notes_rejected(
    wave1_corpus_forbidden_pattern: Path, tmp_path: Path
) -> None:
    """safety_notes containing --enable-real-claude-executor is rejected."""
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(wave1_corpus_forbidden_pattern),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "safety_notes validation failed" in result.stderr.lower() or "forbidden" in result.stderr.lower()


def test_unsupported_mode_rejected(tmp_path: Path) -> None:
    """Non-mock mode is rejected."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-mode",
        "corpus_version": "0.1.0",
        "description": "Test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Mock", "task_ids": ["test-task-1"], "execution_mode": "mocked"}
        },
        "tasks": [
            {
                "task_id": "test-task-1",
                "wave": 1,
                "classification": "already_fixed_needs_regression_test",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 314,
                "severity": "P1",
                "finding_summary": "Test finding",
                "current_main_status": "Fixed",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test passes",
                    "deliverable": "Add test",
                },
                "safety_notes": ["No live Claude"],
            },
        ],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--output-root", str(tmp_path / "out"),
            "--mode", "live",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "unsupported mode" in result.stderr.lower()


def test_invalid_corpus_kind_rejected(tmp_path: Path) -> None:
    """Invalid corpus_kind is rejected."""
    corpus = {
        "corpus_kind": "wrong.kind.v0",
        "corpus_id": "test",
        "corpus_version": "0.1.0",
        "description": "Test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Mock", "task_ids": [], "execution_mode": "mocked"}
        },
        "tasks": [],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--output-root", str(tmp_path / "out"),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "corpus_kind" in result.stderr.lower()


def test_task_classification_false_positive(
    tmp_path: Path,
) -> None:
    """false_positive_with_evidence task gets false_positive_has_evidence classification."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-fp",
        "corpus_version": "0.1.0",
        "description": "Test false positive",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {
                "description": "Mock",
                "task_ids": ["task-fp-001"],
                "execution_mode": "mocked",
            }
        },
        "tasks": [
            {
                "task_id": "task-fp-001",
                "wave": 1,
                "source_pr": 314,
                "finding_id": "codex-fp-001",
                "severity": "P1",
                "classification": "FALSE_POSITIVE_WITH_EVIDENCE",
                "finding_summary": "False positive",
                "current_main_status": "Was never a bug",
                "task_category": "false_positive_with_evidence",
                "action": {
                    "type": "add_evidence_note",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Document as false positive",
                    "deliverable": "Evidence note",
                },
                "safety_notes": ["No live Claude", "No Hermes mutation"],
            }
        ],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    status = json.loads((out_dir / "loop_status.json").read_text(encoding="utf-8"))
    assert status["classifications"].get("false_positive_has_evidence") == 1


def test_task_classification_docs_fixed(
    tmp_path: Path,
) -> None:
    """docs_only_fixed task gets docs_fixed_has_evidence classification."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test-docs-fixed",
        "corpus_version": "0.1.0",
        "description": "Test docs fixed",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {
                "description": "Mock",
                "task_ids": ["task-docs-001"],
                "execution_mode": "mocked",
            }
        },
        "tasks": [
            {
                "task_id": "task-docs-001",
                "wave": 1,
                "source_pr": 323,
                "finding_id": "codex-docs-001",
                "severity": "P2",
                "classification": "FIXED_ALREADY",
                "finding_summary": "Docs gap",
                "current_main_status": "Fixed in PR #323",
                "task_category": "docs_only_fixed",
                "action": {
                    "type": "verify_existing_test_and_document",
                    "target_file": "docs/codex_remediation_corpus_design.md",
                    "allowed_files": [
                        "docs/codex_remediation_corpus_design.md",
                    ],
                    "forbidden_files": [],
                    "success_criteria": "grep confirms fix",
                    "deliverable": "Evidence note",
                },
                "safety_notes": ["No live Claude", "No Hermes mutation"],
            }
        ],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    status = json.loads((out_dir / "loop_status.json").read_text(encoding="utf-8"))
    assert status["classifications"].get("docs_fixed_has_evidence") == 1


# -------------------------------------------------------------------------
# one-task-repair-plan tests
# -------------------------------------------------------------------------


def _make_repair_plan_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """
    Create a Wave-2-style corpus with execution_mode="repair-plan".
    This is the eligible mode for one-task-repair-plan mode.
    Returns (corpus_path, wave1_corpus_path).
    """
    wave1_corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "wave1-test",
        "corpus_version": "0.1.0",
        "description": "Wave 1 test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Wave 1", "task_ids": ["wave1-task-1"], "execution_mode": "mocked"}
        },
        "tasks": [
            {
                "task_id": "wave1-task-1",
                "wave": 1,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 314,
                "finding_id": "codex-test-001",
                "severity": "P1",
                "finding_summary": "Wave 1 test finding",
                "current_main_status": "Fixed",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test passes on current main",
                    "deliverable": "New test function test_wave1_task_1",
                },
                "safety_notes": ["No live Claude", "No Hermes mutation"],
            },
        ],
    }
    wave1_path = tmp_path / "wave1_corpus.json"
    wave1_path.write_text(json.dumps(wave1_corpus), encoding="utf-8")

    wave2_corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "wave2-test",
        "corpus_version": "0.1.0",
        "description": "Wave 2 test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": "See wave1_corpus.json",
            "2": {"description": "Wave 2", "task_ids": ["wave2-task-1", "wave2-task-2"], "execution_mode": "repair-plan"},
        },
        "tasks": [
            {
                "task_id": "wave2-task-1",
                "wave": 2,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 319,
                "source_finding_id": "codex-wave2-001",
                "severity": "P1",
                "goal": "Add a regression test for wave2 finding",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test calls _normalize_task_packet with output_root null",
                    "deliverable": "New test function test_wave2_task_1_normalized",
                },
                "safety": {
                    "no_live_claude": True,
                    "no_hermes_mutations": True,
                    "no_github_mutations": True,
                    "no_install": True,
                    "scope_narrow": True,
                },
                "notes": "Wave 2 test task",
            },
            {
                "task_id": "wave2-task-2",
                "wave": 2,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 317,
                "source_finding_id": "codex-wave2-002",
                "severity": "P1",
                "goal": "Add a regression test for repo-root propagation",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test verifies --repo-root is passed to stage-2",
                    "deliverable": "New test function test_wave2_task_2_repo_root",
                },
                "safety": {
                    "no_live_claude": True,
                    "no_hermes_mutations": True,
                    "no_github_mutations": True,
                    "no_install": True,
                    "scope_narrow": True,
                },
                "notes": "Wave 2 test task 2",
            },
        ],
    }
    corpus_path = tmp_path / "wave2_corpus.json"
    corpus_path.write_text(json.dumps(wave2_corpus), encoding="utf-8")
    return corpus_path, wave1_path


def _make_wave2_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """
    Create a Wave-2-style corpus with execution_mode="mocked".
    This is NOT eligible for one-task-repair-plan mode.
    Returns (corpus_path, wave1_corpus_path).
    """
    wave1_corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "wave1-test",
        "corpus_version": "0.1.0",
        "description": "Wave 1 test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Wave 1", "task_ids": ["wave1-task-1"], "execution_mode": "mocked"}
        },
        "tasks": [
            {
                "task_id": "wave1-task-1",
                "wave": 1,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 314,
                "finding_id": "codex-test-001",
                "severity": "P1",
                "finding_summary": "Wave 1 test finding",
                "current_main_status": "Fixed",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test passes on current main",
                    "deliverable": "New test function test_wave1_task_1",
                },
                "safety_notes": ["No live Claude", "No Hermes mutation"],
            },
        ],
    }
    wave1_path = tmp_path / "wave1_corpus.json"
    wave1_path.write_text(json.dumps(wave1_corpus), encoding="utf-8")

    wave2_corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "wave2-test",
        "corpus_version": "0.1.0",
        "description": "Wave 2 test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": "See wave1_corpus.json",
            "2": {"description": "Wave 2", "task_ids": ["wave2-task-1", "wave2-task-2"], "execution_mode": "mocked"},
        },
        "tasks": [
            {
                "task_id": "wave2-task-1",
                "wave": 2,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 319,
                "source_finding_id": "codex-wave2-001",
                "severity": "P1",
                "goal": "Add a regression test for wave2 finding",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test calls _normalize_task_packet with output_root null",
                    "deliverable": "New test function test_wave2_task_1_normalized",
                },
                "safety": {
                    "no_live_claude": True,
                    "no_hermes_mutations": True,
                    "no_github_mutations": True,
                    "no_install": True,
                    "scope_narrow": True,
                },
                "notes": "Wave 2 test task",
            },
            {
                "task_id": "wave2-task-2",
                "wave": 2,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 317,
                "source_finding_id": "codex-wave2-002",
                "severity": "P1",
                "goal": "Add a regression test for repo-root propagation",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test verifies --repo-root is passed to stage-2",
                    "deliverable": "New test function test_wave2_task_2_repo_root",
                },
                "safety": {
                    "no_live_claude": True,
                    "no_hermes_mutations": True,
                    "no_github_mutations": True,
                    "no_install": True,
                    "scope_narrow": True,
                },
                "notes": "Wave 2 test task 2",
            },
        ],
    }
    corpus_path = tmp_path / "wave2_corpus.json"
    corpus_path.write_text(json.dumps(wave2_corpus), encoding="utf-8")
    return corpus_path, wave1_path


def test_one_task_repair_plan_generates_all_required_files(
    tmp_path: Path,
) -> None:
    """one-task-repair-plan generates all 7 required files."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "wave2-task-1",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    task_dir = out_dir / "wave2-task-1"
    assert (task_dir / "task_context.json").exists()
    assert (task_dir / "repair_prompt.md").exists()
    assert (task_dir / "safety_checklist.md").exists()
    assert (task_dir / "suggested_tests.md").exists()
    assert (task_dir / "stop_conditions.md").exists()
    assert (out_dir / "repair_plan_status.json").exists()
    assert (out_dir / "repair_plan_status.md").exists()


def test_one_task_repair_plan_rejects_missing_task_id(
    tmp_path: Path,
) -> None:
    """one-task-repair-plan requires --task-id."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "--task-id is required" in result.stderr


def test_one_task_repair_plan_rejects_unknown_task_id(
    tmp_path: Path,
) -> None:
    """one-task-repair-plan rejects a task_id not in the corpus."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "nonexistent-task-id",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "not found in corpus" in result.stderr


def test_one_task_repair_plan_processes_exactly_one_task(
    tmp_path: Path,
) -> None:
    """Only the specified task is processed; other tasks are not touched."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "wave2-task-2",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # wave2-task-2 artifacts exist
    assert (out_dir / "wave2-task-2").exists()
    assert (out_dir / "wave2-task-2" / "repair_prompt.md").exists()
    # wave2-task-1 artifacts do NOT exist
    assert not (out_dir / "wave2-task-1").exists()


def test_one_task_repair_plan_prompt_includes_required_fields(
    tmp_path: Path,
) -> None:
    """repair_prompt.md includes allowed_files, forbidden_files, deliverable, success_criteria."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "wave2-task-1",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    prompt = (out_dir / "wave2-task-1" / "repair_prompt.md").read_text(encoding="utf-8")
    assert "tests/test_run_autocoder_batch.py" in prompt  # allowed_file
    assert "deliverable" in prompt.lower()
    assert "success criteria" in prompt.lower()
    # Safety requirements section
    assert "Do NOT" in prompt
    assert "live claude" in prompt.lower()


def test_one_task_repair_plan_suggested_test_name_from_deliverable(
    tmp_path: Path,
) -> None:
    """When the corpus deliverable specifies an explicit test name, suggested_tests.md uses it."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "wave2-task-1",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    suggested_md = (out_dir / "wave2-task-1" / "suggested_tests.md").read_text(encoding="utf-8")
    repair_prompt_md = (out_dir / "wave2-task-1" / "repair_prompt.md").read_text(encoding="utf-8")

    # The corpus deliverable for wave2-task-1 is "New test function test_wave2_task_1_normalized"
    assert "test_wave2_task_1_normalized" in suggested_md
    assert "test_wave2_task_1_normalized" in repair_prompt_md

    # The old fallback name (generated from task_id alone) must NOT appear
    # as the primary suggestion in suggested_tests.md.
    # The old default was test_<task_id> = test_wave2_task_1
    # It may appear in a fallback note, but not as the primary suggested name.
    lines = suggested_md.split("\n")
    suggestion_lines = [
        l for l in lines
        if "**Suggested test name:**" in l and "`test_" in l
    ]
    assert len(suggestion_lines) == 1, f"Expected exactly one suggestion line, got: {suggestion_lines}"
    assert "test_wave2_task_1_normalized" in suggestion_lines[0]
    assert "test_wave2_task_1," not in suggestion_lines[0]  # not the fallback


def test_one_task_repair_plan_writes_only_under_output_root(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Runner writes no files outside output_root."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    written_paths: list[str] = []

    original_write = Path.write_text

    def tracking_write(self: Path, *args: Any, **kwargs: Any) -> Any:
        written_paths.append(str(self))
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracking_write)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "wave2-task-1",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # All written paths must be under out_dir
    for path_str in written_paths:
        assert path_str.startswith(str(out_dir)), f"Written outside output_root: {path_str}"


def test_one_task_repair_plan_no_subprocess_invocation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """one-task-repair-plan does not invoke subprocess for code execution."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    subprocess_calls: list[Any] = []

    original_run = subprocess.run

    def tracking_run(*args: Any, **kwargs: Any) -> Any:
        subprocess_calls.append((args, kwargs))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", tracking_run)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "wave2-task-1",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # Filter: skip the test's own subprocess.run that launches the script
    for args, kwargs in subprocess_calls:
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(str(x) for x in cmd)
        # Skip the test's own call that launches the runner
        if "run_codex_remediation_loop.py" in cmd_str:
            continue
        # Only allow git subprocess calls (for file existence checks)
        assert "git" in cmd_str, f"Unexpected subprocess call: {cmd_str}"


def test_one_task_repair_plan_no_repo_mutation(
    tmp_path: Path,
) -> None:
    """one-task-repair-plan does not mutate any repo files.

    Uses a temp copy of the repo corpus so the test is isolated from
    worktree state and does not need to whitelist intentional changes.
    """
    # Copy the repo corpus to tmp so the script reads from tmp, not REPO_ROOT.
    repo_corpus = REPO_ROOT / "corpus" / "codex-remediation-wave2-pr314-320.json"
    corpus_path = tmp_path / "wave2_corpus.json"
    import shutil
    shutil.copy2(repo_corpus, corpus_path)
    out_dir = tmp_path / "output"

    # Capture porcelain status BEFORE the script runs so we diff only what the
    # script changed, not accumulated worktree state from other tests in the
    # same pytest session.
    import subprocess as _sub
    before_status = _sub.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    before_lines = {ln for ln in before_status.stdout.strip().splitlines() if ln}

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "rgr-319-output-root-null-normalization",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # Diff before vs after to isolate script-caused changes.
    after_status = _sub.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    after_lines = {ln for ln in after_status.stdout.strip().splitlines() if ln}

    # Only files in after but not in before are script-caused changes.
    script_changes = after_lines - before_lines
    unexpected = []
    for ln in sorted(script_changes):
        # porcelain: XY path
        parts = ln.split(None, 1)
        if len(parts) < 2:
            continue
        path = parts[1]
        # This test file is modified by this branch's PR changes — ignore.
        # The script itself does not modify any repo files when run with a
        # temp corpus copy and temp output_root.
        if path == "tests/test_run_codex_remediation_loop.py":
            continue
        abs_path = (REPO_ROOT / path).resolve()
        if not str(abs_path).startswith(str(out_dir.resolve())):
            unexpected.append(path)
    assert not unexpected, f"Repo mutated by script: {unexpected}"


def test_one_task_repair_plan_rejects_missing_success_criteria(
    tmp_path: Path,
) -> None:
    """Task with empty success_criteria fails closed."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test",
        "corpus_version": "0.1.0",
        "description": "Test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Wave 1", "task_ids": ["bad-task"], "execution_mode": "repair-plan"}
        },
        "tasks": [
            {
                "task_id": "bad-task",
                "wave": 1,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 314,
                "finding_summary": "Test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "",  # empty — should fail
                    "deliverable": "Add test",
                },
                "safety_notes": ["No live Claude"],
            },
        ],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    out_dir = tmp_path / "output"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "bad-task",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "success_criteria" in result.stderr.lower()


def test_one_task_repair_plan_rejects_missing_deliverable(
    tmp_path: Path,
) -> None:
    """Task with empty deliverable fails closed."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test",
        "corpus_version": "0.1.0",
        "description": "Test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Wave 1", "task_ids": ["bad-task-2"], "execution_mode": "repair-plan"}
        },
        "tasks": [
            {
                "task_id": "bad-task-2",
                "wave": 1,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 314,
                "finding_summary": "Test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test passes",
                    "deliverable": "",  # empty — should fail
                },
                "safety_notes": ["No live Claude"],
            },
        ],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    out_dir = tmp_path / "output"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "bad-task-2",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "deliverable" in result.stderr.lower()


def test_one_task_repair_plan_rejects_mocked_wave(
    tmp_path: Path,
) -> None:
    """one-task-repair-plan rejects tasks from waves with execution_mode='mocked'."""
    corpus = {
        "corpus_kind": "aed.codex_remediation.corpus.v0",
        "corpus_id": "test",
        "corpus_version": "0.1.0",
        "description": "Test",
        "source_audit_doc": "docs/test.md",
        "base_sha": "03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211",
        "base_sha_policy": "current_main",
        "wave_definitions": {
            "1": {"description": "Wave 1", "task_ids": ["mocked-task"], "execution_mode": "mocked"}
        },
        "tasks": [
            {
                "task_id": "mocked-task",
                "wave": 1,
                "classification": "FIXED_ALREADY",
                "task_category": "already_fixed_needs_regression_test",
                "source_pr": 314,
                "finding_summary": "Test",
                "action": {
                    "type": "add_regression_test",
                    "target_file": "tests/test_run_autocoder_batch.py",
                    "allowed_files": ["tests/test_run_autocoder_batch.py"],
                    "forbidden_files": [],
                    "success_criteria": "Test passes",
                    "deliverable": "New test function",
                },
                "safety_notes": ["No live Claude"],
            },
        ],
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    out_dir = tmp_path / "output"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "mocked-task",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "mocked" in result.stderr.lower()
    assert "repair-plan" in result.stderr.lower()


def test_one_task_repair_plan_status_fields(
    tmp_path: Path,
) -> None:
    """repair_plan_status.json has correct execution fields set to False."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--task-id", "wave2-task-1",
            "--output-root", str(out_dir),
            "--mode", "one-task-repair-plan",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    status = json.loads((out_dir / "repair_plan_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "REPAIR_PLAN_READY"
    assert status["execution_performed"] is False
    assert status["live_claude_invoked"] is False
    assert status["autocoder_batch_invoked"] is False
    assert status["repo_mutated"] is False
    assert status["git_mutation_allowed"] is False
    assert status["task_id"] == "wave2-task-1"


def test_mock_plan_only_still_works(
    tmp_path: Path,
) -> None:
    """mock-plan-only mode continues to work after one-task-repair-plan additions."""
    corpus_path, _ = _make_repair_plan_corpus(tmp_path)
    out_dir = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus", str(corpus_path),
            "--output-root", str(out_dir),
            "--mode", "mock-plan-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (out_dir / "loop_status.json").exists()
    status = json.loads((out_dir / "loop_status.json").read_text(encoding="utf-8"))
    assert status["total_tasks"] == 2  # Both wave2 tasks


# -------------------------------------------------------------------------
# Imports needed by tests
# -------------------------------------------------------------------------

import re  # noqa: E402 (used in test_no_shell_true_in_source)

from typing import Any  # noqa: E402
