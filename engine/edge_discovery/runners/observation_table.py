"""
Pure observation-table helpers for the first thin runner.

No registry writes, no ledger writes, no live trading, no production execution.

Functions here are pure CSV processing helpers with no runner-level dependencies.
"""

from __future__ import annotations

import csv
from pathlib import Path


def _count_csv_rows(file_path: Path) -> int | None:
    """
    Count data rows in a CSV file using streaming line iteration.

    Excludes the header row (first line) from the count.

    Returns None if the file cannot be read as CSV.
    """
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return 0
            count = sum(1 for _ in reader)
            return count
    except Exception:
        # If any error occurs (encoding, malformed CSV, etc.), return None
        # rather than propagating the error.
        return None


def _parse_required_columns(raw: str | None) -> list[str]:
    """
    Parse a comma-separated required-column string into a normalized list.

    - Trims whitespace from each token.
    - Rejects empty tokens (e.g. "a,,b" → ValueError).
    - De-duplicates preserving first occurrence.
    - Returns list in order of first occurrence (stable for hashing: caller
      normalizes to sorted for deterministic run_config_hash).

    Raises ValueError if any token is empty after trimming.
    """
    if raw is None:
        return []

    tokens = [t.strip() for t in raw.split(",")]
    # Check for empty tokens
    for t in tokens:
        if t == "":
            raise ValueError(
                "required_observation_columns contains an empty token; "
                "each column name must be non-empty"
            )

    # De-duplicate preserving first occurrence
    seen: set[str] = set()
    normalized: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            normalized.append(t)

    return normalized


def _normalize_optional_column_name(raw: str | None) -> str | None:
    """
    Normalize an optional column name: strip leading/trailing whitespace.

    Returns None if raw is None or empty string.
    Internal whitespace is preserved.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped == "":
        return None
    return stripped


def _summarize_observation_table_canonical(
    dataset_path: Path,
    observation_date_column: str | None,
    observation_symbol_column: str | None,
) -> dict:
    """
    Compute a canonical summary of a CSV observation table in a single pass
    using csv.DictReader.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    observation_date_column : str | None
        Column name to use for min/max date computation. If None, skipped.
    observation_symbol_column : str | None
        Column name to use for unique symbol count. If None, skipped.

    Returns
    -------
    dict
        Canonical summary dict with keys:
        - row_count (int): total non-header rows read
        - min_date (str | None): lexicographic minimum of non-empty date values
        - max_date (str | None): lexicographic maximum of non-empty date values
        - unique_symbol_count (int | None): count of distinct non-empty symbol values
        - date_column (str | None): the date column name used
        - symbol_column (str | None): the symbol column name used
        - details (str): human-readable summary string for audit details_ref

    Raises
    ------
    FileNotFoundError
        If dataset_path does not exist.
    ValueError
        If observation_date_column or observation_symbol_column is not in the CSV header.
    """
    row_count = 0
    date_values: list[str] = []
    symbol_values: set[str] = set()

    date_col = observation_date_column.strip() if observation_date_column else None
    symbol_col = observation_symbol_column.strip() if observation_symbol_column else None

    with open(dataset_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        # Validate requested columns are present in header
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {dataset_path}")
        header_set = {f.strip() for f in reader.fieldnames}
        if date_col is not None and date_col not in header_set:
            raise ValueError(
                f"observation_date_column '{observation_date_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )
        if symbol_col is not None and symbol_col not in header_set:
            raise ValueError(
                f"observation_symbol_column '{observation_symbol_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )

        for row in reader:
            row_count += 1
            if date_col is not None:
                val = row.get(date_col, "").strip()
                if val:
                    date_values.append(val)
            if symbol_col is not None:
                val = row.get(symbol_col, "").strip()
                if val:
                    symbol_values.add(val)

    min_date = min(date_values) if date_values else None
    max_date = max(date_values) if date_values else None
    unique_symbol_count = len(symbol_values) if symbol_values else None

    # Build details string for audit details_ref
    parts = [f"row_count={row_count}"]
    if date_col is not None:
        if min_date is not None and max_date is not None:
            parts.append(f"date_column={date_col}")
            parts.append(f"min_date={min_date}")
            parts.append(f"max_date={max_date}")
        else:
            parts.append(f"date_column={date_col}")
            parts.append("min_date=None")
            parts.append("max_date=None")
    if symbol_col is not None:
        parts.append(f"symbol_column={symbol_col}")
        parts.append(f"unique_symbol_count={unique_symbol_count}")

    return {
        "row_count": row_count,
        "min_date": min_date,
        "max_date": max_date,
        "unique_symbol_count": unique_symbol_count,
        "date_column": date_col,
        "symbol_column": symbol_col,
        "details": "; ".join(parts),
    }


def _summarize_observation_close_returns(
    dataset_path: Path,
    observation_date_column: str,
    observation_symbol_column: str,
    observation_close_column: str,
) -> dict:
    """
    Compute per-symbol first/last close return summary from a CSV observation table
    in a single pass using csv.DictReader.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    observation_date_column : str
        Column name for date values (used for first/last ordering).
    observation_symbol_column : str
        Column name for symbol/ticker values.
    observation_close_column : str
        Column name for close price values.

    Returns
    -------
    dict
        Close-return summary dict with keys:
        - symbols_with_return (int): number of symbols with ≥2 distinct dates
          and valid non-zero first close
        - min_return (float | None): minimum simple return across symbols
        - max_return (float | None): maximum simple return across symbols
        - mean_return (float | None): mean simple return across symbols
        - close_column (str): the close column name used
        - skipped_symbols (int): number of symbols skipped (no valid return)
        - details (str): human-readable summary string for audit details_ref

    Raises
    ------
    ValueError
        If observation_close_column is not in the CSV header.
        (Missing date/symbol columns raise in _summarize_observation_table_canonical
        which is called before this function in build_runner_output.)
    """
    # Per-symbol state: track first and last (by lexicographic date) close.
    # We store first_valid_date and first_valid_close for the earliest date seen,
    # and last_valid_close for the latest date seen.
    # A symbol needs at least 2 distinct dates with valid close to have a return.
    symbol_data: dict[str, dict] = {}

    date_col = observation_date_column.strip()
    symbol_col = observation_symbol_column.strip()
    close_col = observation_close_column.strip()

    with open(dataset_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {dataset_path}")
        header_set = {f.strip() for f in reader.fieldnames}
        if close_col not in header_set:
            raise ValueError(
                f"observation_close_column '{observation_close_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )
        # Date/symbol columns are expected to have been validated by
        # _summarize_observation_table_canonical already; check them too.
        if date_col not in header_set:
            raise ValueError(
                f"observation_date_column '{observation_date_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )
        if symbol_col not in header_set:
            raise ValueError(
                f"observation_symbol_column '{observation_symbol_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )

        for row in reader:
            date_val = row.get(date_col, "").strip()
            symbol_val = row.get(symbol_col, "").strip()
            close_raw = row.get(close_col, "").strip()

            # Skip rows with missing essential fields
            if not date_val or not symbol_val:
                continue

            # Parse close: skip empty or non-numeric
            if not close_raw:
                continue
            try:
                close_val = float(close_raw)
            except ValueError:
                continue

            # Skip non-finite
            if not (0 < abs(close_val) < float("inf")):
                # float("inf") or nan — skip
                continue

            # Initialize symbol entry if first time seen
            if symbol_val not in symbol_data:
                symbol_data[symbol_val] = {
                    "first_date": date_val,
                    "first_close": close_val,
                    "last_date": date_val,
                    "last_close": close_val,
                }
            else:
                # Update first if earlier (lexicographic)
                if date_val < symbol_data[symbol_val]["first_date"]:
                    symbol_data[symbol_val]["first_date"] = date_val
                    symbol_data[symbol_val]["first_close"] = close_val
                # Update last if later (lexicographic)
                if date_val > symbol_data[symbol_val]["last_date"]:
                    symbol_data[symbol_val]["last_date"] = date_val
                    symbol_data[symbol_val]["last_close"] = close_val

    # Compute returns for symbols with ≥2 distinct dates and non-zero first close
    returns: list[float] = []
    for symbol, data in symbol_data.items():
        # Require at least 2 distinct dates
        if data["first_date"] == data["last_date"]:
            continue
        # Require non-zero first close
        if data["first_close"] == 0:
            continue
        simple_return = (data["last_close"] / data["first_close"]) - 1.0
        returns.append(simple_return)

    symbols_with_return = len(returns)
    skipped_symbols = len(symbol_data) - symbols_with_return

    if returns:
        min_return = min(returns)
        max_return = max(returns)
        mean_return = sum(returns) / len(returns)
    else:
        min_return = None
        max_return = None
        mean_return = None

    details_parts = [
        f"symbols_with_return={symbols_with_return}",
        f"close_column={close_col}",
        f"skipped_symbols={skipped_symbols}",
    ]
    if min_return is not None:
        details_parts.append(f"min_return={min_return!r}")
        details_parts.append(f"max_return={max_return!r}")
        details_parts.append(f"mean_return={mean_return!r}")

    return {
        "symbols_with_return": symbols_with_return,
        "min_return": min_return,
        "max_return": max_return,
        "mean_return": mean_return,
        "close_column": close_col,
        "skipped_symbols": skipped_symbols,
        "details": "; ".join(details_parts),
    }


def _summarize_observation_missing_values(
    dataset_path: Path,
    columns: list[str],
) -> dict:
    """
    Compute per-column missing-value counts from a CSV observation table in a
    single pass using csv.DictReader.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    columns : list[str]
        Column names to check for missing values. Each column must be present
        in the CSV header. Tokens are assumed to be pre-normalized (stripped,
        deduplicated) by the caller.

    Returns
    -------
    dict
        Missing-value summary dict with keys:
        - row_count (int): total non-header rows read
        - missing (dict[str, int]): per-column count of rows where the value
          is None (CSV field absent) or empty after strip
        - details (str): human-readable summary string for audit details_ref

    Raises
    ------
    ValueError
        If any requested column is not in the CSV header.
    """
    row_count = 0
    missing_counts: dict[str, int] = {col: 0 for col in columns}

    # Strip columns for header comparison (header tokens may have whitespace)
    stripped_columns = [col.strip() for col in columns]

    with open(dataset_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {dataset_path}")

        # Strip fieldnames so DictReader row keys are clean
        reader.fieldnames = [f.strip() for f in reader.fieldnames]
        header_fields = set(reader.fieldnames)

        # Validate all requested columns are present
        for col in stripped_columns:
            if col not in header_fields:
                raise ValueError(
                    f"Column '{col}' not found in CSV header: "
                    f"{list(reader.fieldnames)}"
                )

        for row in reader:
            row_count += 1
            for col in stripped_columns:
                val = row.get(col)
                # Missing: None (field absent from row) or empty/whitespace after strip
                if val is None or str(val).strip() == "":
                    missing_counts[col] += 1

    # Build details string for audit details_ref
    details_parts = [f"row_count={row_count}"]
    for col in stripped_columns:
        details_parts.append(f"missing[{col}]={missing_counts[col]}")

    return {
        "row_count": row_count,
        "missing": missing_counts,
        "details": "; ".join(details_parts),
    }


def _read_csv_header(file_path: Path) -> list[str] | None:
    """
    Read the header row (first line) of a CSV file using csv.reader.

    Returns None if the file cannot be opened or parsed as CSV.
    """
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return []
            return header
    except Exception:
        return None


def _validate_observation_table_columns(
    dataset_path: Path,
    required_columns: list[str],
) -> tuple[list[str], bool]:
    """
    Check that all required columns are present in a CSV file's header row.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    required_columns : list[str]
        Column names that must be present in the CSV header.

    Returns
    -------
    tuple[list[str], bool]
        (missing_columns, all_present).
        missing_columns is the list of required columns not found in the header.
        all_present is True when missing_columns is empty.
    """
    header = _read_csv_header(dataset_path)
    if header is None:
        # Could not read CSV; treat all required columns as missing
        return list(required_columns), False

    header_set = {col.strip() for col in header}
    missing = [col for col in required_columns if col.strip() not in header_set]
    return missing, len(missing) == 0


def _summarize_observation_duplicate_rows(
    dataset_path: Path,
    observation_date_column: str,
    observation_symbol_column: str,
) -> dict:
    """
    Detect duplicate (symbol, date) rows in a CSV observation table in a single
    pass using csv.DictReader.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    observation_date_column : str
        Column name for date values.
    observation_symbol_column : str
        Column name for symbol/ticker values.

    Returns
    -------
    dict
        Duplicate-row summary dict with keys:
        - total_rows (int): total non-header rows read
        - duplicate_row_count (int): total excess rows beyond first occurrence
          (e.g. a key appearing 3 times contributes 2 to this count)
        - affected_key_count (int): number of distinct (symbol, date) keys
          that appear more than once
        - affected_symbols (list[str]): sorted list of symbols that have at
          least one duplicate (date) key; JSON-serializable
        - affected_dates (list[str]): sorted list of dates that appear in at
          least one duplicate key; JSON-serializable
        - has_duplicates (bool): True when duplicate_row_count > 0
        - duplicate_examples (list[dict]): first 10 duplicate keys sorted by
          (symbol, date), each as {"symbol": str, "date": str,
          "row_count": int, "excess_row_count": int}; JSON-serializable
        - details (str): human-readable summary string for audit details_ref

    Raises
    ------
    ValueError
        If observation_date_column or observation_symbol_column is not in the
        CSV header.
    """
    date_col = observation_date_column.strip()
    symbol_col = observation_symbol_column.strip()

    # (symbol, date) → occurrence count; tracks distinct keys seen
    key_counts: dict[tuple[str, str], int] = {}
    # Per-key first occurrence for examples
    key_first_date: dict[tuple[str, str], str] = {}
    row_count = 0

    with open(dataset_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {dataset_path}")
        header_set = {f.strip() for f in reader.fieldnames}
        if date_col not in header_set:
            raise ValueError(
                f"observation_date_column '{observation_date_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )
        if symbol_col not in header_set:
            raise ValueError(
                f"observation_symbol_column '{observation_symbol_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )

        # Map stripped header names back to actual DictReader keys so row
        # lookups work regardless of surrounding whitespace in the CSV header.
        header_map = {f.strip(): f for f in reader.fieldnames}
        date_key = header_map[date_col]
        symbol_key = header_map[symbol_col]

        for row in reader:
            row_count += 1
            date_val = row.get(date_key, "").strip()
            symbol_val = row.get(symbol_key, "").strip()

            # Skip rows with missing essential fields
            if not date_val or not symbol_val:
                continue

            key = (symbol_val, date_val)
            if key not in key_counts:
                key_counts[key] = 1
                key_first_date[key] = date_val
            else:
                key_counts[key] += 1

    # Identify duplicate keys and aggregate
    duplicate_keys = sorted(
        (sym, dt) for (sym, dt), cnt in key_counts.items() if cnt > 1
    )

    duplicate_row_count = sum(
        cnt - 1 for cnt in key_counts.values() if cnt > 1
    )
    affected_key_count = len(duplicate_keys)

    # Collect affected symbols and dates from duplicate keys
    affected_symbols = sorted({sym for sym, _ in duplicate_keys})
    affected_dates = sorted({dt for _, dt in duplicate_keys})

    has_duplicates = duplicate_row_count > 0

    # Build deterministic examples: first 10 duplicate keys sorted (sym, dt)
    duplicate_examples = [
        {
            "symbol": sym,
            "date": dt,
            "row_count": key_counts[(sym, dt)],
            "excess_row_count": key_counts[(sym, dt)] - 1,
        }
        for (sym, dt) in duplicate_keys[:10]
    ]

    # details string — must not include actual data values or symbol names
    details_parts = [f"total_rows={row_count}"]
    details_parts.append(f"duplicate_row_count={duplicate_row_count}")
    details_parts.append(f"affected_key_count={affected_key_count}")
    if has_duplicates:
        details_parts.append(f"affected_symbol_count={len(affected_symbols)}")
        details_parts.append(f"affected_date_count={len(affected_dates)}")
    else:
        details_parts.append("no duplicate observation rows were detected")

    return {
        "total_rows": row_count,
        "duplicate_row_count": duplicate_row_count,
        "affected_key_count": affected_key_count,
        "affected_symbols": affected_symbols,
        "affected_dates": affected_dates,
        "has_duplicates": has_duplicates,
        "duplicate_examples": duplicate_examples,
        "details": "; ".join(details_parts),
    }
