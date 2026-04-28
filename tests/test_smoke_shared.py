"""Tests for scripts/local/_smoke_shared.py."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from scripts.local._smoke_shared import (
    ensure_output_dir,
    print_batch_summary,
    warn_real_run,
)


class TestEnsureOutputDir:
    def test_creates_dir(self, tmp_path):
        out = tmp_path / "sub" / "nested"
        result = ensure_output_dir(str(out))
        assert result == out
        assert out.exists()
        assert out.is_dir()

    def test_exist_ok_is_idempotent(self, tmp_path):
        out = tmp_path / "existing"
        out.mkdir()
        (out / "file.txt").write_text("keep me")
        result = ensure_output_dir(str(out))
        assert result == out
        assert (out / "file.txt").read_text() == "keep me"


class TestWarnRealRun:
    def test_writes_to_stderr(self, capsys):
        warn_real_run("do something dangerous.")
        captured = capsys.readouterr()
        assert "WARNING: --real-run specified. This will do something dangerous." in captured.err
        assert captured.out == ""


class TestPrintBatchSummary:
    def test_all_fields(self, capsys):
        print_batch_summary(
            batch_id="batch-42",
            status="dry_run",
            n_candidates_generated=3,
            n_candidates_selected=2,
            n_success=1,
            n_error=0,
            output_artifacts={"log": "/tmp/log.txt"},
            ledger_path="/tmp/ledger.jsonl",
        )
        out = capsys.readouterr().out
        assert "batch_id: batch-42" in out
        assert "status: dry_run" in out
        assert "n_candidates_generated: 3" in out
        assert "n_candidates_selected: 2" in out
        assert "n_success: 1" in out
        assert "n_error: 0" in out
        assert "output_artifacts:" in out
        assert "log: /tmp/log.txt" in out
        assert "ledger_path: /tmp/ledger.jsonl" in out

    def test_empty_output_artifacts(self, capsys):
        print_batch_summary(
            batch_id="b1",
            status="ok",
            n_candidates_generated=1,
            n_candidates_selected=1,
            n_success=1,
            n_error=0,
            output_artifacts={},
            ledger_path=None,
        )
        out = capsys.readouterr().out
        assert "output_artifacts:" not in out
        assert "ledger_path:" not in out

    def test_ledger_path_not_shown_when_none(self, capsys):
        print_batch_summary(
            batch_id="b2",
            status="ok",
            n_candidates_generated=1,
            n_candidates_selected=1,
            n_success=1,
            n_error=0,
            output_artifacts={},
            ledger_path=None,
        )
        out = capsys.readouterr().out
        assert "ledger_path" not in out

    def test_indent(self, capsys):
        print_batch_summary(
            batch_id="b3",
            status="ok",
            n_candidates_generated=1,
            n_candidates_selected=1,
            n_success=1,
            n_error=0,
            output_artifacts={},
            ledger_path=None,
            indent="  ",
        )
        out = capsys.readouterr().out
        assert all(line.startswith("  ") for line in out.splitlines())
