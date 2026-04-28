"""Tests for scripts/local/_smoke_shared.py."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from engine.edge_discovery.data_manifest import DatasetRole
from scripts.local._smoke_shared import (
    ensure_output_dir,
    print_batch_summary,
    resolve_path_from_manifest,
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


_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "data_manifests"
)


def _load_example_manifest(filename: str) -> "DatasetManifest":
    from engine.edge_discovery.data_manifest import load_dataset_manifest

    return load_dataset_manifest(str(_EXAMPLES_DIR / filename))


def _rewrite_manifest_path(manifest: "DatasetManifest", new_path: str) -> dict:
    from engine.edge_discovery.data_manifest import dataset_manifest_to_dict

    d = dataset_manifest_to_dict(manifest)
    d["path"] = new_path
    return d


class TestResolvePathFromManifest:
    def test_options_db_manifest_returns_path(self, tmp_path: Path):
        import json

        from engine.edge_discovery.data_manifest import dataset_manifest_from_dict

        m = _load_example_manifest("preearn_options_2021_local.json")
        sqlite_file = tmp_path / "options_2021_lane_0.sqlite"
        sqlite_file.touch()
        rewritten = _rewrite_manifest_path(m, str(sqlite_file))
        # Write to tmp_path so resolve_path_from_manifest can load it
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(rewritten), encoding="utf-8")

        from scripts.local._smoke_shared import resolve_path_from_manifest

        result = resolve_path_from_manifest(str(manifest_file), "options_backtest_db")
        assert result == str(sqlite_file)

    def test_preearn_repo_manifest_returns_path(self, tmp_path: Path):
        import json

        from engine.edge_discovery.data_manifest import dataset_manifest_from_dict

        m = _load_example_manifest("preearn_repo_local.json")
        repo_dir = tmp_path / "engine_linux_main"
        repo_dir.mkdir()
        rewritten = _rewrite_manifest_path(m, str(repo_dir))
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(rewritten), encoding="utf-8")

        from scripts.local._smoke_shared import resolve_path_from_manifest

        result = resolve_path_from_manifest(str(manifest_file), "preearn_repo")
        assert result == str(repo_dir)

    def test_wrong_role_exits(self, capsys):
        path = _EXAMPLES_DIR / "preearn_options_2021_local.json"
        with pytest.raises(SystemExit) as exc:
            resolve_path_from_manifest(str(path), "preearn_repo")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "is required" in err

    def test_missing_file_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            resolve_path_from_manifest("/CI_DOES_NOT_EXIST/manifest.json", "options_backtest_db")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_invalid_json_exits(self, capsys, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not json }", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            resolve_path_from_manifest(str(bad), "options_backtest_db")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "failed to parse" in err
