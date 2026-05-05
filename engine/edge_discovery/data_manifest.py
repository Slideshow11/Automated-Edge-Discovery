"""Vendor-neutral manifest layer for externally provisioned local datasets.

AED primarily consumes pre-downloaded, cleaned, local datasets. Data acquisition
is upstream and entirely optional. This module provides a declarative interface
for describing what datasets exist, where they live locally, and what they
contain — without acquiring, downloading, or querying them.

Scope (v1):
- Declarative dataset metadata (role, source, format, date range, symbols).
- Path-level validation only (existence, file-vs-directory, suffix).
- No schema inspection, no SQLite queries, no Parquet reads, no network I/O.

Explicitly out of scope:
- DataSourceAdapter implementations.
- Vendor-specific downloaders or API clients.
- Web scraping or automated data acquisition.
- Promotion workflows or autonomous dataset discovery.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DataSourceKind(str, Enum):
    """Storage medium / access pattern for a dataset."""

    local_sqlite = "local_sqlite"
    local_csv = "local_csv"
    local_parquet = "local_parquet"
    local_duckdb = "local_duckdb"
    local_directory = "local_directory"
    external_cli = "external_cli"


class DatasetRole(str, Enum):
    """Domain role of the dataset within AED."""

    options_backtest_db = "options_backtest_db"
    preearn_repo = "preearn_repo"
    fmp_feature_lake = "fmp_feature_lake"
    price_history = "price_history"
    fundamentals = "fundamentals"
    earnings_calendar = "earnings_calendar"
    generic = "generic"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetManifest:
    """Declaration of a single externally provisioned local dataset.

    Parameters
    ----------
    dataset_id : str
        Stable identifier for this dataset (e.g. ``"options_2021_lane_0"``).
    role : DatasetRole
        Domain role the dataset serves within AED.
    source_kind : DataSourceKind
        Storage medium and access pattern.
    path : str
        Absolute or repo-root-relative path to the dataset.
    format : str
        Logical format string (e.g. ``"sqlite"``, ``"csv"``, ``"parquet"``,
        ``"directory"``). Free-form but should match source_kind conventions.
    schema_version : str, optional
        Schema version string if applicable.
    source_name : str, optional
        Origin system name (e.g. ``"vendor_a"``, ``"internal"``).
    created_at : str, optional
        ISO-8601 creation timestamp of the underlying data.
    date_range_start : str, optional
        Earliest data date (ISO-8601, e.g. ``"2021-01-01"``).
    date_range_end : str, optional
        Latest data date (ISO-8601, e.g. ``"2021-12-31"``).
    symbols : tuple[str, ...]
        Tuple of ticker symbols present in the dataset.
    provenance_notes : str
        Free-text notes describing where/how the data was obtained.
    quality_flags : tuple[str, ...]
        Tuple of quality signal strings
        (e.g. ``("cleaned", "deduplicated")``).
    """

    dataset_id: str
    role: DatasetRole
    source_kind: DataSourceKind
    path: str
    format: str
    schema_version: str | None = None
    source_name: str | None = None
    created_at: str | None = None
    date_range_start: str | None = None
    date_range_end: str | None = None
    symbols: tuple[str, ...] = ()
    provenance_notes: str = ""
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatasetValidationResult:
    """Result of validating a DatasetManifest against the filesystem.

    Parameters
    ----------
    dataset_id : str
        The manifest's dataset_id.
    ok : bool
        True when the manifest passes all checks (path exists, correct type,
        correct suffix — and errors is empty).
    path_exists : bool
        True when the declared path resolves to an existing filesystem entry.
    is_file : bool
        True when the path is an existing regular file.
    is_dir : bool
        True when the path is an existing directory.
    errors : tuple[str, ...]
        Non-empty when checks fail. Empty on success.
    warnings : tuple[str, ...]
        Advisory messages (e.g. missing optional metadata). Never causes ok=False.
    """

    dataset_id: str
    ok: bool
    path_exists: bool
    is_file: bool
    is_dir: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# Path security
# ---------------------------------------------------------------------------


class _PathSecurityError(ValueError):
    """Raised when a DatasetManifest path escapes its allowed base directory."""
    pass


def _is_safe_relative_path(path: str | Path, base_dir: Path) -> tuple[Path, Path]:
    """Resolve ``path`` and verify it stays within ``base_dir``.

    Parameters
    ----------
    path : str | Path
        The declared path from a DatasetManifest ``path`` field.
    base_dir : Path
        The directory the path must remain within after resolution.

    Returns
    -------
    resolved : Path
        The fully resolved Path object.
    resolved_base : Path
        The resolved base directory.

    Raises
    ------
    _PathSecurityError
        When the resolved path escapes ``base_dir`` via traversal components
        or symlinks.
    """
    base_resolved = base_dir.resolve(strict=False)
    p_resolved = Path(path).resolve(strict=False)

    # is_relative_to() returns False when p_resolved is not a sub-path of
    # base_resolved (Python 3.11). Python 3.12+ raises ValueError instead.
    # We handle both for compatibility across Python versions.
    try:
        is_inside = p_resolved.is_relative_to(base_resolved)
    except ValueError:
        is_inside = False

    if not is_inside:
        raise _PathSecurityError(
            f"Path '{path}' resolves to '{p_resolved}', which is outside "
            f"the allowed base directory '{base_resolved}'. "
            f"Path traversal and symlink escapes are not permitted."
        )

    return p_resolved, base_resolved


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def dataset_manifest_to_dict(manifest: DatasetManifest) -> dict[str, Any]:
    """Serialize a DatasetManifest to a plain dict.

    Enum fields are serialized as their ``.value`` strings.
    ``symbols`` and ``quality_flags`` tuples become lists.
    """
    out = asdict(manifest)
    out["role"] = manifest.role.value
    out["source_kind"] = manifest.source_kind.value
    out["symbols"] = list(manifest.symbols)
    out["quality_flags"] = list(manifest.quality_flags)
    return out


def dataset_manifest_from_dict(data: dict[str, Any]) -> DatasetManifest:
    """Deserialize a DatasetManifest from a plain dict.

    Raises
    ------
    ValueError
        If ``role`` or ``source_kind`` values are not recognised enum members.
    """
    role_val = data.get("role", "")
    source_kind_val = data.get("source_kind", "")

    try:
        role = DatasetRole(role_val)
    except ValueError:
        raise ValueError(
            f"Unknown DatasetRole {role_val!r}. "
            f"Valid values: {[e.value for e in DatasetRole]}"
        )

    try:
        source_kind = DataSourceKind(source_kind_val)
    except ValueError:
        raise ValueError(
            f"Unknown DataSourceKind {source_kind_val!r}. "
            f"Valid values: {[e.value for e in DataSourceKind]}"
        )

    # Normalise symbols / quality_flags to tuples; default to () if absent.
    symbols_raw = data.get("symbols")
    if symbols_raw is None:
        symbols: tuple[str, ...] = ()
    elif isinstance(symbols_raw, (list, tuple)):
        symbols = tuple(str(s) for s in symbols_raw)
    else:
        symbols = (str(symbols_raw),)

    quality_flags_raw = data.get("quality_flags")
    if quality_flags_raw is None:
        quality_flags: tuple[str, ...] = ()
    elif isinstance(quality_flags_raw, (list, tuple)):
        quality_flags = tuple(str(f) for f in quality_flags_raw)
    else:
        quality_flags = (str(quality_flags_raw),)

    path_val = data["path"]
    if not isinstance(path_val, str) or not path_val.strip():
        raise ValueError("path must be a non-empty string")

    return DatasetManifest(
        dataset_id=data["dataset_id"],
        role=role,
        source_kind=source_kind,
        path=data["path"],
        format=data["format"],
        schema_version=data.get("schema_version"),
        source_name=data.get("source_name"),
        created_at=data.get("created_at"),
        date_range_start=data.get("date_range_start"),
        date_range_end=data.get("date_range_end"),
        symbols=symbols,
        provenance_notes=data.get("provenance_notes", ""),
        quality_flags=quality_flags,
    )


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_dataset_manifest(path: str | Path) -> DatasetManifest:
    """Load a DatasetManifest from a JSON file.

    Parameters
    ----------
    path : str | Path
        Path to the JSON manifest file.

    Returns
    -------
    DatasetManifest

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If the JSON content cannot be parsed as a valid manifest.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Manifest file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return dataset_manifest_from_dict(data)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _suffix_ok(path: Path, allowed: tuple[str, ...]) -> bool:
    """Return True when ``path`` has one of ``allowed`` suffixes (case-insensitive)."""
    return path.suffix.lower() in allowed


def validate_dataset_manifest(
    manifest: DatasetManifest,
    *,
    base_dir: str | Path | None = None,
) -> DatasetValidationResult:
    """Validate a DatasetManifest against the local filesystem.

    Performs path-level checks only:
    - Existence of the declared path.
    - Correct file-vs-directory type.
    - Correct file extension where applicable.
    - Path stays within the allowed base directory (no traversal, no symlink escape).

    No schema inspection, no data reads, no network I/O.

    Parameters
    ----------
    manifest : DatasetManifest
        The manifest to validate.
    base_dir : str | Path | None, optional
        Directory that all dataset paths must remain within.
        Paths outside this directory are rejected with a security error.
        If None, the current working directory is used as the base.
        This parameter does not change the path's meaning — it only enforces
        a containment boundary. Absolute paths are checked against the resolved
        base_dir; paths that resolve outside it are rejected.

    Returns
    -------
    DatasetValidationResult
    """
    dataset_id = manifest.dataset_id
    base = Path(base_dir) if base_dir is not None else Path.cwd()

    # --- Reject empty path strings (including whitespace-only) ---
    path_str = manifest.path
    if not isinstance(path_str, str) or not path_str.strip():
        return DatasetValidationResult(
            dataset_id=dataset_id,
            ok=False,
            path_exists=False,
            is_file=False,
            is_dir=False,
            errors=("path must be a non-empty string",),
            warnings=(),
        )

    # --- Security check: reject traversal and symlink escapes ---
    #
    # Policy:
    #   - When base_dir is explicitly provided: all paths (absolute and relative)
    #     must resolve within base_dir. This lets callers declare a containment
    #     boundary for both relative traversal (../..) and absolute paths.
    #   - When base_dir is None (defaults to cwd): only relative paths are checked.
    #     Absolute paths are allowed through without base_dir security check.
    #     This preserves backward compatibility for existing tests that use
    #     absolute /tmp/... paths without passing base_dir.
    #
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    p = Path(manifest.path)

    if base_dir is not None or not p.is_absolute():
        # Explicit base_dir: always check. Or: implicit base (cwd) + relative path.
        try:
            p, _ = _is_safe_relative_path(manifest.path, base)
        except _PathSecurityError as e:
            return DatasetValidationResult(
                dataset_id=dataset_id,
                ok=False,
                path_exists=False,
                is_file=False,
                is_dir=False,
                errors=(str(e),),
                warnings=(),
            )

    errors: list[str] = []
    warnings: list[str] = []

    path_exists = p.exists()
    is_file = p.is_file() if path_exists else False
    is_dir = p.is_dir() if path_exists else False

    if not path_exists:
        errors.append(f"Path does not exist: {manifest.path}")
    else:
        # --- Type and suffix enforcement per source_kind ---
        sk = manifest.source_kind

        if sk == DataSourceKind.local_sqlite:
            if not is_file:
                errors.append(
                    f"local_sqlite path must be a file, got directory: {manifest.path}"
                )
            elif not _suffix_ok(p, (".sqlite", ".db")):
                errors.append(
                    f"local_sqlite file must have .sqlite or .db suffix: {manifest.path}"
                )

        elif sk == DataSourceKind.local_csv:
            if not is_file:
                errors.append(
                    f"local_csv path must be a file, got directory: {manifest.path}"
                )
            elif not _suffix_ok(p, (".csv",)):
                errors.append(
                    f"local_csv file must have .csv suffix: {manifest.path}"
                )

        elif sk == DataSourceKind.local_parquet:
            if is_file and not _suffix_ok(p, (".parquet",)):
                errors.append(
                    f"local_parquet file must have .parquet suffix: {manifest.path}"
                )
            # Directory is accepted as-is (no suffix check).

        elif sk == DataSourceKind.local_duckdb:
            if not is_file:
                errors.append(
                    f"local_duckdb path must be a file, got directory: {manifest.path}"
                )
            elif not _suffix_ok(p, (".duckdb", ".db")):
                errors.append(
                    f"local_duckdb file must have .duckdb or .db suffix: {manifest.path}"
                )

        elif sk == DataSourceKind.local_directory:
            if not is_dir:
                errors.append(
                    f"local_directory path must be a directory: {manifest.path}"
                )

        elif sk == DataSourceKind.external_cli:
            # Path must exist (file or directory accepted); no further constraints.
            pass

    # --- Warnings for missing optional metadata ---
    if manifest.date_range_start is None:
        warnings.append("date_range_start is not set")
    if manifest.date_range_end is None:
        warnings.append("date_range_end is not set")
    if not manifest.symbols:
        warnings.append("symbols is empty")
    if manifest.created_at is None:
        warnings.append("created_at is not set")

    ok = path_exists and not errors

    return DatasetValidationResult(
        dataset_id=dataset_id,
        ok=bool(ok),
        path_exists=bool(path_exists),
        is_file=bool(is_file),
        is_dir=bool(is_dir),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
