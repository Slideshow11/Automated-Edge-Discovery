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


# ---------------------------------------------------------------------------
# Path sanitization — traversal and symlink escape tests
# ---------------------------------------------------------------------------


class TestValidateDatasetManifestPathSecurity:
    """Tests for DatasetManifest path security: traversal, symlinks, empty paths."""

    def test_absolute_path_within_base_passes(self, tmp_path: Path):
        """An absolute path that exists inside base_dir is accepted."""
        f = tmp_path / "data.parquet"
        f.touch()
        manifest = DatasetManifest(
            dataset_id="safe_parquet",
            role=DatasetRole.fmp_feature_lake,
            source_kind=DataSourceKind.local_parquet,
            path=str(f),  # absolute path within base
            format="parquet",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is True
        assert result.path_exists is True
        assert result.is_file is True

    def test_relative_path_resolved_outside_base_traversal_rejected(self, tmp_path: Path):
        """A relative path that resolves outside base_dir is rejected.

        When a relative path (e.g. data/../../etc) resolves to an absolute path
        outside base_dir, it is rejected. This is the core traversal-escape case.
        """
        # Create a subdirectory inside tmp_path
        sub = tmp_path / "sub"
        sub.mkdir()
        # Create a sibling directory OUTSIDE tmp_path via ../sibling
        sibling = tmp_path.parent / "DMtest_sibling_dir"
        sibling.mkdir(exist_ok=True)
        sibling_file = sibling / "data.csv"
        sibling_file.touch()
        try:
            manifest = DatasetManifest(
                dataset_id="escape",
                role=DatasetRole.price_history,
                source_kind=DataSourceKind.local_csv,
                path=f"../DMtest_sibling_dir/data.csv",
                format="csv",
            )
            result = validate_dataset_manifest(manifest, base_dir=tmp_path)
            assert result.ok is False
            assert any(
                "outside" in e or "traversal" in e.lower()
                for e in result.errors
            ), f"Expected traversal error, got: {result.errors}"
        finally:
            sibling_file.unlink(missing_ok=True)
            sibling.rmdir()

    def test_dotdot_traversal_outside_base_rejected(self, tmp_path: Path):
        """A path containing ../ that resolves outside base_dir is rejected.

        This is the classic ../../../etc/passwd attack vector.
        The resolved absolute path is checked against base_dir.
        """
        # Create the file at /tmp/<unique>
        import uuid
        unique_name = f"dm_traversal_{uuid.uuid4().hex[:8]}"
        escape_file = Path(f"/tmp/{unique_name}.txt")
        escape_file.touch()
        try:
            # Construct a relative path that escapes tmp_path to reach /tmp
            # tmp_path is at /tmp/pytest-of-max/pytest-N/test_dotdot
            # We need to go up past tmp_path parent and then to /tmp
            # Path from tmp_path: ../../../tmp/<unique>.txt
            depth = len(tmp_path.parts)  # e.g. 5 for /tmp/pytest-of-max/...
            traversal = "/".join([".."] * depth) + f"/tmp/{unique_name}.txt"
            manifest = DatasetManifest(
                dataset_id="bad_traversal",
                role=DatasetRole.options_backtest_db,
                source_kind=DataSourceKind.local_sqlite,
                path=traversal,
                format="sqlite",
            )
            result = validate_dataset_manifest(manifest, base_dir=tmp_path)
            assert result.ok is False
            assert any(
                "outside" in e or "traversal" in e.lower()
                for e in result.errors
            ), f"Expected traversal error, got: {result.errors}"
        finally:
            escape_file.unlink(missing_ok=True)

    def test_normalized_dotdot_path_within_base_accepted(
        self, tmp_path: Path, monkeypatch,
    ):
        """A normalized path like sub/../sub/data.csv that stays within base is accepted.

        Path.resolve() normalizes .. components against CWD, so we must chdir to
        base_dir so the resolved path lands inside base_dir.
        """
        monkeypatch.chdir(tmp_path)
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "data.csv"
        f.touch()
        manifest = DatasetManifest(
            dataset_id="normalized_ok",
            role=DatasetRole.price_history,
            source_kind=DataSourceKind.local_csv,
            path="sub/../sub/data.csv",
            format="csv",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is True
        assert result.path_exists is True
        assert result.is_file is True

    def test_absolute_path_outside_base_rejected(self, tmp_path: Path):
        """An absolute path that resolves outside base_dir is rejected."""
        manifest = DatasetManifest(
            dataset_id="outside_absolute",
            role=DatasetRole.options_backtest_db,
            source_kind=DataSourceKind.local_sqlite,
            path="/etc/passwd",
            format="sqlite",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is False
        assert any("outside" in e or "traversal" in e.lower() for e in result.errors)

    def test_symlink_inside_base_pointing_outside_rejected(self, tmp_path: Path):
        """A symlink inside base_dir that resolves outside base_dir is rejected.

        Path.resolve() follows symlinks, so the resolved path is the real
        target location — which is outside base_dir and correctly rejected.
        The symlink lives under base_dir; the target lives OUTSIDE base_dir.
        """
        import uuid

        inside = tmp_path / "inside"
        inside.mkdir()
        # Target lives OUTSIDE base_dir (tmp_path.parent), not inside it.
        # Using a unique name under tmp_path.parent to guarantee it's outside.
        unique_name = f"dm_sym_target_{uuid.uuid4().hex[:8]}"
        target = tmp_path.parent / unique_name
        target.touch()
        link = inside / "escape_link"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation not supported on this platform")
        manifest = DatasetManifest(
            dataset_id="symlink_escape",
            role=DatasetRole.fmp_feature_lake,
            source_kind=DataSourceKind.local_parquet,
            path="inside/escape_link",
            format="parquet",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is False
        assert any("symlink" in e.lower() or "outside" in e for e in result.errors)

    def test_empty_path_string_rejected(self, tmp_path: Path):
        """An empty string path is rejected."""
        manifest = DatasetManifest(
            dataset_id="empty_path",
            role=DatasetRole.options_backtest_db,
            source_kind=DataSourceKind.local_sqlite,
            path="",
            format="sqlite",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is False
        assert any("non-empty" in e for e in result.errors)

    def test_whitespace_only_path_rejected(self, tmp_path: Path):
        """A whitespace-only path string is rejected."""
        manifest = DatasetManifest(
            dataset_id="whitespace_path",
            role=DatasetRole.options_backtest_db,
            source_kind=DataSourceKind.local_sqlite,
            path="   ",
            format="sqlite",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is False
        assert any("non-empty" in e for e in result.errors)

    def test_relative_path_under_base_dir_accepted_with_different_cwd(
        self, tmp_path: Path, monkeypatch,
    ):
        """A relative path inside base_dir passes even when cwd is NOT base_dir.

        This is the core regression test for the fix: Path(path).resolve()
        must resolve relative paths against base_dir, not process cwd.
        Without the fix, a relative path like 'sub/data.csv' resolves to
        /cwd/sub/data.csv instead of /base_dir/sub/data.csv and fails the
        containment check when cwd != base_dir.
        """
        # Ensure cwd is somewhere completely different from base_dir.
        monkeypatch.chdir(tmp_path.parent.parent if len(tmp_path.parents) > 1 else Path("/tmp"))
        sub = tmp_path / "sub"
        sub.mkdir()
        data_file = sub / "data.csv"
        data_file.touch()
        manifest = DatasetManifest(
            dataset_id="relative_under_base",
            role=DatasetRole.price_history,
            source_kind=DataSourceKind.local_csv,
            path="sub/data.csv",
            format="csv",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is True, f"Expected ok=True, got errors: {result.errors}"

    def test_normalized_path_under_base_dir_accepted_with_different_cwd(
        self, tmp_path: Path, monkeypatch,
    ):
        """A normalized relative path like sub/../sub/data.csv under base_dir passes.

        Even when cwd != base_dir, Path.resolve() normalizes the ..
        components and the final resolved path must still land inside base_dir.
        """
        monkeypatch.chdir(tmp_path.parent.parent if len(tmp_path.parents) > 1 else Path("/tmp"))
        sub = tmp_path / "sub"
        sub.mkdir()
        data_file = sub / "data.csv"
        data_file.touch()
        manifest = DatasetManifest(
            dataset_id="normalized_under_base",
            role=DatasetRole.price_history,
            source_kind=DataSourceKind.local_csv,
            path="sub/../sub/data.csv",
            format="csv",
        )
        result = validate_dataset_manifest(manifest, base_dir=tmp_path)
        assert result.ok is True, f"Expected ok=True, got errors: {result.errors}"


class TestDatasetManifestFromDictPathValidation:
    """Tests for path field validation in dataset_manifest_from_dict."""

    def test_empty_path_raises(self):
        """An empty path string raises ValueError during deserialization."""
        data = {
            "dataset_id": "test",
            "role": "options_backtest_db",
            "source_kind": "local_sqlite",
            "path": "",
            "format": "sqlite",
        }
        with pytest.raises(ValueError, match="non-empty"):
            dataset_manifest_from_dict(data)

    def test_whitespace_only_path_raises(self):
        """A whitespace-only path string raises ValueError during deserialization."""
        data = {
            "dataset_id": "test",
            "role": "options_backtest_db",
            "source_kind": "local_sqlite",
            "path": "   ",
            "format": "sqlite",
        }
        with pytest.raises(ValueError, match="non-empty"):
            dataset_manifest_from_dict(data)

    def test_missing_path_field_raises(self):
        """A missing path field raises KeyError during deserialization."""
        data = {
            "dataset_id": "test",
            "role": "options_backtest_db",
            "source_kind": "local_sqlite",
            "format": "sqlite",
        }
        with pytest.raises(KeyError):
            dataset_manifest_from_dict(data)


class TestValidateDatasetManifestDefaultBaseDir:
    """Tests for the default base_dir (current working directory) behavior."""

    def test_relative_path_within_cwd_passes(self, tmp_path: Path, monkeypatch):
        """A relative path that resolves inside CWD is accepted when base_dir defaults."""
        monkeypatch.chdir(tmp_path)
        # Use correct .sqlite suffix for local_sqlite source_kind
        escape_file = tmp_path / "escape_target.sqlite"
        escape_file.touch()
        try:
            manifest = DatasetManifest(
                dataset_id="within_cwd",
                role=DatasetRole.options_backtest_db,
                source_kind=DataSourceKind.local_sqlite,
                path="escape_target.sqlite",  # resolves to tmp_path/escape_target.sqlite — inside CWD
                format="sqlite",
            )
            result = validate_dataset_manifest(manifest)
            assert result.ok is True
        finally:
            escape_file.unlink(missing_ok=True)

    def test_absolute_path_fails_without_base_dir(self, tmp_path: Path, monkeypatch):
        """An absolute path outside CWD fails validation (suffix check) without explicit base_dir.

        Without an explicit base_dir, absolute paths bypass the security check
        (they are treated as self-contained). The manifest then fails the
        source_kind suffix/type check. This test verifies that /etc/passwd
        is not silently accepted.
        """
        monkeypatch.chdir(tmp_path)
        manifest = DatasetManifest(
            dataset_id="abs_rejected",
            role=DatasetRole.options_backtest_db,
            source_kind=DataSourceKind.local_sqlite,
            path="/etc/passwd",
            format="sqlite",
        )
        result = validate_dataset_manifest(manifest)
        # Must be rejected (suffix/type check, not security check without base_dir)
        assert result.ok is False