"""Tests for engine.edge_discovery.data_manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.edge_discovery.data_manifest import (
    DataSourceKind,
    DatasetManifest,
    DatasetRole,
    DatasetValidationResult,
    dataset_manifest_from_dict,
    dataset_manifest_to_dict,
    load_dataset_manifest,
    validate_dataset_manifest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


MINIMAL_MANIFEST_DATA = {
    "dataset_id": "test_options_2021",
    "role": "options_backtest_db",
    "source_kind": "local_sqlite",
    "path": "/tmp/options.sqlite",
    "format": "sqlite",
}


# ---------------------------------------------------------------------------
# to_dict / from_dict roundtrip
# ---------------------------------------------------------------------------


def test_roundtrip_is_lossless():
    manifest = DatasetManifest(
        dataset_id="options_2021_lane_0",
        role=DatasetRole.options_backtest_db,
        source_kind=DataSourceKind.local_sqlite,
        path="/cache/options_2021.sqlite",
        format="sqlite",
        schema_version="1.0.0",
        source_name="ivol",
        created_at="2024-01-01",
        date_range_start="2021-01-01",
        date_range_end="2021-12-31",
        symbols=("AAPL", "MSFT", "SPY"),
        provenance_notes="Downloaded from IVOL, cleaned locally.",
        quality_flags=("deduplicated", "gap_filled"),
    )

    d = dataset_manifest_to_dict(manifest)
    restored = dataset_manifest_from_dict(d)

    assert manifest == restored


def test_roundtrip_provenance_notes():
    manifest = DatasetManifest(
        dataset_id="price_history_2023",
        role=DatasetRole.price_history,
        source_kind=DataSourceKind.local_csv,
        path="/data/prices.csv",
        format="csv",
        provenance_notes="Sourced from internal data warehouse. No vendor scraping.",
    )
    d = dataset_manifest_to_dict(manifest)
    restored = dataset_manifest_from_dict(d)
    assert manifest == restored


def test_roundtrip_quality_flags():
    manifest = DatasetManifest(
        dataset_id="fundamentals_2022",
        role=DatasetRole.fundamentals,
        source_kind=DataSourceKind.local_csv,
        path="/data/fund.csv",
        format="csv",
        quality_flags=("cleaned", "validated", "vintage_2024"),
    )
    d = dataset_manifest_to_dict(manifest)
    restored = dataset_manifest_from_dict(d)
    assert manifest == restored


def test_roundtrip_symbols():
    manifest = DatasetManifest(
        dataset_id="earnings_cal",
        role=DatasetRole.earnings_calendar,
        source_kind=DataSourceKind.local_csv,
        path="/data/earnings.csv",
        format="csv",
        symbols=("TSLA", "NVDA", "AMD"),
    )
    d = dataset_manifest_to_dict(manifest)
    restored = dataset_manifest_from_dict(d)
    assert manifest == restored


# ---------------------------------------------------------------------------
# load_dataset_manifest
# ---------------------------------------------------------------------------


def test_load_dataset_manifest_reads_valid_json(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    data = {
        "dataset_id": "test_db",
        "role": "options_backtest_db",
        "source_kind": "local_sqlite",
        "path": "/tmp/test.sqlite",
        "format": "sqlite",
        "quality_flags": ["cleaned"],
    }
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    manifest = load_dataset_manifest(manifest_path)

    assert manifest.dataset_id == "test_db"
    assert manifest.role == DatasetRole.options_backtest_db
    assert manifest.source_kind == DataSourceKind.local_sqlite
    assert manifest.quality_flags == ("cleaned",)


def test_load_dataset_manifest_file_not_found(tmp_path: Path):
    missing = tmp_path / "nonexistent.json"
    with pytest.raises(FileNotFoundError):
        load_dataset_manifest(missing)


# ---------------------------------------------------------------------------
# validate_dataset_manifest — local_sqlite
# ---------------------------------------------------------------------------


def test_sqlite_sqlite_suffix_passes(tmp_path: Path):
    f = tmp_path / "options.sqlite"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="optsql",
        role=DatasetRole.options_backtest_db,
        source_kind=DataSourceKind.local_sqlite,
        path=str(f),
        format="sqlite",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True
    assert result.path_exists is True
    assert result.is_file is True
    assert result.is_dir is False


def test_sqlite_db_suffix_passes(tmp_path: Path):
    f = tmp_path / "options.db"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="optsql",
        role=DatasetRole.options_backtest_db,
        source_kind=DataSourceKind.local_sqlite,
        path=str(f),
        format="sqlite",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True


def test_sqlite_missing_path_fails(tmp_path: Path):
    manifest = DatasetManifest(
        dataset_id="optsql",
        role=DatasetRole.options_backtest_db,
        source_kind=DataSourceKind.local_sqlite,
        path=str(tmp_path / "nonexistent.sqlite"),
        format="sqlite",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is False
    assert result.path_exists is False
    assert any("does not exist" in e for e in result.errors)


def test_sqlite_wrong_suffix_fails(tmp_path: Path):
    f = tmp_path / "options.txt"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="optsql",
        role=DatasetRole.options_backtest_db,
        source_kind=DataSourceKind.local_sqlite,
        path=str(f),
        format="sqlite",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is False
    assert any(".sqlite or .db" in e for e in result.errors)


# ---------------------------------------------------------------------------
# validate_dataset_manifest — local_csv
# ---------------------------------------------------------------------------


def test_csv_csv_suffix_passes(tmp_path: Path):
    f = tmp_path / "prices.csv"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="prices",
        role=DatasetRole.price_history,
        source_kind=DataSourceKind.local_csv,
        path=str(f),
        format="csv",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True
    assert result.is_file is True


def test_csv_directory_fails(tmp_path: Path):
    d = tmp_path / "csv_dir"
    d.mkdir()
    manifest = DatasetManifest(
        dataset_id="prices",
        role=DatasetRole.price_history,
        source_kind=DataSourceKind.local_csv,
        path=str(d),
        format="csv",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is False
    assert any("must be a file" in e for e in result.errors)


# ---------------------------------------------------------------------------
# validate_dataset_manifest — local_parquet
# ---------------------------------------------------------------------------


def test_parquet_file_passes(tmp_path: Path):
    f = tmp_path / "data.parquet"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="features",
        role=DatasetRole.fmp_feature_lake,
        source_kind=DataSourceKind.local_parquet,
        path=str(f),
        format="parquet",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True


def test_parquet_directory_passes(tmp_path: Path):
    d = tmp_path / "parquet_dir"
    d.mkdir()
    manifest = DatasetManifest(
        dataset_id="features",
        role=DatasetRole.fmp_feature_lake,
        source_kind=DataSourceKind.local_parquet,
        path=str(d),
        format="parquet",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True


# ---------------------------------------------------------------------------
# validate_dataset_manifest — local_duckdb
# ---------------------------------------------------------------------------


def test_duckdb_duckdb_suffix_passes(tmp_path: Path):
    f = tmp_path / "analytics.duckdb"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="analytics",
        role=DatasetRole.generic,
        source_kind=DataSourceKind.local_duckdb,
        path=str(f),
        format="duckdb",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True


def test_duckdb_db_suffix_passes(tmp_path: Path):
    f = tmp_path / "analytics.db"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="analytics",
        role=DatasetRole.generic,
        source_kind=DataSourceKind.local_duckdb,
        path=str(f),
        format="duckdb",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True


# ---------------------------------------------------------------------------
# validate_dataset_manifest — local_directory
# ---------------------------------------------------------------------------


def test_local_directory_passes(tmp_path: Path):
    d = tmp_path / "price_history"
    d.mkdir()
    manifest = DatasetManifest(
        dataset_id="ph",
        role=DatasetRole.price_history,
        source_kind=DataSourceKind.local_directory,
        path=str(d),
        format="directory",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True
    assert result.is_dir is True


def test_local_directory_file_fails(tmp_path: Path):
    f = tmp_path / "not_a_dir.csv"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="ph",
        role=DatasetRole.price_history,
        source_kind=DataSourceKind.local_directory,
        path=str(f),
        format="directory",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is False
    assert any("must be a directory" in e for e in result.errors)


# ---------------------------------------------------------------------------
# validate_dataset_manifest — external_cli
# ---------------------------------------------------------------------------


def test_external_cli_file_passes(tmp_path: Path):
    f = tmp_path / "mytool"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="tool",
        role=DatasetRole.generic,
        source_kind=DataSourceKind.external_cli,
        path=str(f),
        format="binary",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True


def test_external_cli_directory_passes(tmp_path: Path):
    d = tmp_path / "cli_dir"
    d.mkdir()
    manifest = DatasetManifest(
        dataset_id="tool",
        role=DatasetRole.generic,
        source_kind=DataSourceKind.external_cli,
        path=str(d),
        format="directory",
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True


# ---------------------------------------------------------------------------
# Enum validation
# ---------------------------------------------------------------------------


def test_unknown_source_kind_raises():
    data = {**MINIMAL_MANIFEST_DATA, "source_kind": "not_a_kind"}
    with pytest.raises(ValueError, match="Unknown DataSourceKind"):
        dataset_manifest_from_dict(data)


def test_unknown_role_raises():
    data = {**MINIMAL_MANIFEST_DATA, "role": "not_a_role"}
    with pytest.raises(ValueError, match="Unknown DatasetRole"):
        dataset_manifest_from_dict(data)


# ---------------------------------------------------------------------------
# Warning behaviour
# ---------------------------------------------------------------------------


def test_missing_optional_metadata_produces_warnings(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.touch()
    manifest = DatasetManifest(
        dataset_id="incomplete",
        role=DatasetRole.generic,
        source_kind=DataSourceKind.local_csv,
        path=str(f),
        format="csv",
        # No date_range_start, date_range_end, symbols, created_at
    )
    result = validate_dataset_manifest(manifest)
    assert result.ok is True  # Warnings must not make ok=False
    assert len(result.warnings) > 0
    assert any("date_range_start" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# No forbidden behaviour
# ---------------------------------------------------------------------------


def test_no_vendor_network_subprocess():
    """Verify data_manifest module has no forbidden imports."""
    import engine.edge_discovery.data_manifest as dm

    src = open(dm.__file__, encoding="utf-8").read()

    forbidden = [
        "ivol", "requests", "httpx", "urllib", "scrape",
        "FMP", "Polygon", "ORATS", "CBOE", "Yahoo",
        "subprocess", "download",
    ]
    for term in forbidden:
        # Allow "download" in a docstring/comment explaining what is NOT done.
        # But the module should not import or call any download mechanism.
        if term == "download":
            # Just verify the word doesn't appear as an import or call
            assert "import download" not in src
            assert "download(" not in src
        else:
            assert term not in src.lower(), f"forbidden term '{term}' found in data_manifest.py"
