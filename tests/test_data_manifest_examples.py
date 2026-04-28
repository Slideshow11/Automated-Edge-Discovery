"""Tests for example DataManifest JSON files in examples/data_manifests/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.edge_discovery.data_manifest import (
    DataSourceKind,
    DatasetRole,
    DatasetManifest,
    dataset_manifest_from_dict,
    dataset_manifest_to_dict,
    load_dataset_manifest,
    validate_dataset_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "data_manifests"
)


def load_example_manifest(filename: str) -> DatasetManifest:
    """Load an example manifest from examples/data_manifests/ by filename."""
    path = _EXAMPLES_DIR / filename
    return load_dataset_manifest(path)


# ---------------------------------------------------------------------------
# Directory contents
# ---------------------------------------------------------------------------


def test_examples_directory_contains_exactly_two_json_files():
    json_files = sorted(p.name for p in _EXAMPLES_DIR.glob("*.json"))
    assert json_files == [
        "preearn_options_2021_local.json",
        "preearn_repo_local.json",
    ]


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def test_options_manifest_parses():
    m = load_example_manifest("preearn_options_2021_local.json")
    assert isinstance(m, DatasetManifest)


def test_preearn_repo_manifest_parses():
    m = load_example_manifest("preearn_repo_local.json")
    assert isinstance(m, DatasetManifest)


# ---------------------------------------------------------------------------
# Field values — options manifest
# ---------------------------------------------------------------------------


def test_options_manifest_role_is_options_backtest_db():
    m = load_example_manifest("preearn_options_2021_local.json")
    assert m.role == DatasetRole.options_backtest_db


def test_options_manifest_source_kind_is_local_sqlite():
    m = load_example_manifest("preearn_options_2021_local.json")
    assert m.source_kind == DataSourceKind.local_sqlite


def test_options_manifest_format_is_sqlite():
    m = load_example_manifest("preearn_options_2021_local.json")
    assert m.format == "sqlite"


def test_options_manifest_provenance_says_no_download():
    m = load_example_manifest("preearn_options_2021_local.json")
    assert "AED does not download" in m.provenance_notes
    assert "AED does not" in m.provenance_notes  # AED is not the owner of the data
    assert "upstream" in m.provenance_notes.lower()  # acquisition is upstream


def test_options_manifest_quality_flags_includes_externally_provisioned():
    m = load_example_manifest("preearn_options_2021_local.json")
    assert "externally_provisioned" in m.quality_flags


# ---------------------------------------------------------------------------
# Field values — preearn repo manifest
# ---------------------------------------------------------------------------


def test_preearn_repo_manifest_role_is_preearn_repo():
    m = load_example_manifest("preearn_repo_local.json")
    assert m.role == DatasetRole.preearn_repo


def test_preearn_repo_manifest_source_kind_is_external_cli():
    m = load_example_manifest("preearn_repo_local.json")
    assert m.source_kind == DataSourceKind.external_cli


def test_preearn_repo_manifest_provenance_says_script_interface_only():
    m = load_example_manifest("preearn_repo_local.json")
    assert "script interface only" in m.provenance_notes.lower()


def test_preearn_repo_manifest_quality_flags_includes_external_system():
    m = load_example_manifest("preearn_repo_local.json")
    assert "external_system" in m.quality_flags


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_options_manifest_roundtrips_losslessly():
    m = load_example_manifest("preearn_options_2021_local.json")
    d = dataset_manifest_to_dict(m)
    m2 = dataset_manifest_from_dict(d)
    assert m == m2


def test_preearn_repo_manifest_roundtrips_losslessly():
    m = load_example_manifest("preearn_repo_local.json")
    d = dataset_manifest_to_dict(m)
    m2 = dataset_manifest_from_dict(d)
    assert m == m2


# ---------------------------------------------------------------------------
# Validation with tmp_path
# ---------------------------------------------------------------------------


def _rewrite_manifest_path(manifest: DatasetManifest, new_path: str) -> dict:
    """Return the manifest's to_dict with the path replaced."""
    d = dataset_manifest_to_dict(manifest)
    d["path"] = new_path
    return d


def test_options_manifest_validates_when_path_points_to_tmp_sqlite_file(
    tmp_path: Path,
):
    # Load the real manifest
    m = load_example_manifest("preearn_options_2021_local.json")

    # Create the sqlite file in tmp_path
    sqlite_file = tmp_path / "options_2021_lane_0.sqlite"
    sqlite_file.touch()

    # Build a manifest dict with the tmp path
    rewritten = _rewrite_manifest_path(m, str(sqlite_file))
    rewritten_manifest = dataset_manifest_from_dict(rewritten)

    result = validate_dataset_manifest(rewritten_manifest)
    assert result.ok is True
    assert result.path_exists is True
    assert result.is_file is True


def test_preearn_repo_manifest_validates_when_path_points_to_tmp_directory(
    tmp_path: Path,
):
    # Load the real manifest
    m = load_example_manifest("preearn_repo_local.json")

    # Create the directory in tmp_path
    repo_dir = tmp_path / "engine_linux_main"
    repo_dir.mkdir()

    # Build a manifest dict with the tmp path
    rewritten = _rewrite_manifest_path(m, str(repo_dir))
    rewritten_manifest = dataset_manifest_from_dict(rewritten)

    result = validate_dataset_manifest(rewritten_manifest)
    assert result.ok is True
    assert result.path_exists is True
    assert result.is_dir is True


def test_options_manifest_validation_fails_for_missing_path():
    # Construct a manifest dict directly with a path that does not exist anywhere.
    data = {
        "dataset_id": "nonexistent_options",
        "role": "options_backtest_db",
        "source_kind": "local_sqlite",
        "path": "/CI_DOES_NOT_EXIST/options_2021_lane_0.sqlite",
        "format": "sqlite",
    }
    m = dataset_manifest_from_dict(data)
    result = validate_dataset_manifest(m)
    assert result.ok is False
    assert result.path_exists is False
    assert any("does not exist" in e for e in result.errors)


def test_preearn_repo_manifest_validation_fails_for_missing_path():
    # Construct a manifest dict directly with a path that does not exist anywhere.
    data = {
        "dataset_id": "nonexistent_preearn_repo",
        "role": "preearn_repo",
        "source_kind": "external_cli",
        "path": "/CI_DOES_NOT_EXIST/engine_linux_main",
        "format": "directory",
    }
    m = dataset_manifest_from_dict(data)
    result = validate_dataset_manifest(m)
    assert result.ok is False
    assert result.path_exists is False


# ---------------------------------------------------------------------------
# No forbidden behaviour
# ---------------------------------------------------------------------------


def test_no_vendor_network_subprocess():
    """Verify no forbidden imports in data_manifest module."""
    import engine.edge_discovery.data_manifest as dm

    src = open(dm.__file__, encoding="utf-8").read()

    forbidden = [
        "requests", "httpx", "urllib", "subprocess",
        "import sqlite3", "read_parquet", "pyarrow", "pandas",
        "download(",
    ]
    for term in forbidden:
        assert term not in src, f"forbidden term '{term}' found"
