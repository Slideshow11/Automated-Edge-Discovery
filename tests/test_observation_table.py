"""
Unit tests for engine.edge_discovery.runners.observation_table.

Scope: pure CSV helpers only. No runner orchestration, no registry writes,
no ledger writes, no live trading.
"""
import csv
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.edge_discovery.runners.observation_table import (
    _count_csv_rows,
    _normalize_optional_column_name,
    _parse_required_columns,
    _read_csv_header,
    _summarize_observation_close_returns,
    _summarize_observation_missing_values,
    _summarize_observation_table_canonical,
    _summarize_observation_duplicate_rows,
    _validate_observation_table_columns,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_csv(path: Path, rows: list[list[str]]) -> None:
    """Write rows (list of lists) to a CSV file. First row is the header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# _parse_required_columns
# ---------------------------------------------------------------------------

class TestParseRequiredColumns:
    def test_comma_separated_values(self):
        result = _parse_required_columns("date,symbol,close")
        assert result == ["date", "symbol", "close"]

    def test_trims_leading_trailing_whitespace(self):
        result = _parse_required_columns("  date  ,  symbol  ,  close  ")
        assert result == ["date", "symbol", "close"]

    def test_preserves_internal_whitespace(self):
        result = _parse_required_columns("obs date,obs symbol")
        assert result == ["obs date", "obs symbol"]

    def test_rejects_empty_token(self):
        with pytest.raises(ValueError, match="empty token"):
            _parse_required_columns("date,,close")

    def test_rejects_empty_token_with_whitespace(self):
        with pytest.raises(ValueError, match="empty token"):
            _parse_required_columns("date,  ,close")

    def test_deduplicates_preserving_first_occurrence(self):
        result = _parse_required_columns("date,symbol,date,symbol,close")
        assert result == ["date", "symbol", "close"]

    def test_none_returns_empty_list(self):
        result = _parse_required_columns(None)
        assert result == []


# ---------------------------------------------------------------------------
# _normalize_optional_column_name
# ---------------------------------------------------------------------------

class TestNormalizeOptionalColumnName:
    def test_none_returns_none(self):
        assert _normalize_optional_column_name(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_optional_column_name("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_optional_column_name("   ") is None

    def test_trims_leading_trailing_whitespace(self):
        assert _normalize_optional_column_name("  close  ") == "close"

    def test_preserves_internal_whitespace(self):
        assert _normalize_optional_column_name("obs close price") == "obs close price"

    def test_normal_column_name_unchanged(self):
        assert _normalize_optional_column_name("close") == "close"


# ---------------------------------------------------------------------------
# _read_csv_header
# ---------------------------------------------------------------------------

class TestReadCsvHeader:
    def test_reads_normal_header(self, tmp_path):
        path = tmp_path / "normal.csv"
        make_csv(path, [["date", "symbol", "close"], ["2024-01-01", "AAPL", "185.5"]])
        header = _read_csv_header(path)
        assert header == ["date", "symbol", "close"]

    def test_whitespace_in_header(self, tmp_path):
        # Whitespace in header cells is preserved as-is
        path = tmp_path / "whitespace.csv"
        make_csv(path, [["  date  ", "symbol  ", " close"], ["2024-01-01", "AAPL", "185.5"]])
        header = _read_csv_header(path)
        assert header == ["  date  ", "symbol  ", " close"]

    def test_empty_file_returns_empty_list(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        result = _read_csv_header(path)
        assert result == []

    def test_header_only_file(self, tmp_path):
        path = tmp_path / "header_only.csv"
        make_csv(path, [["date", "symbol", "close"]])
        header = _read_csv_header(path)
        assert header == ["date", "symbol", "close"]

    def test_nonexistent_file_returns_none(self, tmp_path):
        result = _read_csv_header(tmp_path / "does_not_exist.csv")
        assert result is None


# ---------------------------------------------------------------------------
# _validate_observation_table_columns
# ---------------------------------------------------------------------------

class TestValidateObservationTableColumns:
    def test_all_present(self, tmp_path):
        path = tmp_path / "valid.csv"
        make_csv(path, [["date", "symbol", "close"], ["2024-01-01", "AAPL", "185.5"]])
        missing, all_present = _validate_observation_table_columns(path, ["date", "symbol"])
        assert missing == []
        assert all_present is True

    def test_missing_columns_detected(self, tmp_path):
        path = tmp_path / "partial.csv"
        make_csv(path, [["date", "close"], ["2024-01-01", "185.5"]])
        missing, all_present = _validate_observation_table_columns(path, ["date", "symbol"])
        assert missing == ["symbol"]
        assert all_present is False

    def test_whitespace_normalization_in_header(self, tmp_path):
        # Header has whitespace; required column is undecorated
        path = tmp_path / "ws.csv"
        make_csv(path, [["  date  ", "symbol  "], ["2024-01-01", "AAPL"]])
        missing, all_present = _validate_observation_table_columns(path, ["date", "symbol"])
        assert missing == []
        assert all_present is True

    def test_required_column_with_internal_ws(self, tmp_path):
        # Required column has internal whitespace; header matches
        path = tmp_path / "internal_ws.csv"
        make_csv(path, [["obs date", "obs symbol"], ["2024-01-01", "AAPL"]])
        missing, all_present = _validate_observation_table_columns(path, ["obs date", "obs symbol"])
        assert missing == []
        assert all_present is True


# ---------------------------------------------------------------------------
# _count_csv_rows
# ---------------------------------------------------------------------------

class TestCountCsvRows:
    def test_header_plus_n_rows_returns_n(self, tmp_path):
        path = tmp_path / "rows.csv"
        make_csv(path, [["date", "symbol"], ["2024-01-01", "AAPL"], ["2024-01-02", "AAPL"]])
        assert _count_csv_rows(path) == 2

    def test_header_only_returns_zero(self, tmp_path):
        path = tmp_path / "header_only.csv"
        make_csv(path, [["date", "symbol"]])
        assert _count_csv_rows(path) == 0

    def test_empty_file_returns_zero(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        assert _count_csv_rows(path) == 0

    def test_nonexistent_file_returns_none(self, tmp_path):
        assert _count_csv_rows(tmp_path / "no.csv") is None


# ---------------------------------------------------------------------------
# _summarize_observation_table_canonical
# ---------------------------------------------------------------------------

class TestSummarizeObservationTableCanonical:
    def test_date_column_produces_min_max_date(self, tmp_path):
        path = tmp_path / "dates.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "185.0"],
                ["2024-03-15", "AAPL", "190.0"],
                ["2024-01-20", "AAPL", "187.0"],
            ],
        )
        result = _summarize_observation_table_canonical(path, "date", None)
        assert result["min_date"] == "2024-01-01"
        assert result["max_date"] == "2024-03-15"
        assert result["row_count"] == 3
        assert result["unique_symbol_count"] is None
        assert result["date_column"] == "date"

    def test_symbol_column_produces_unique_symbol_count(self, tmp_path):
        path = tmp_path / "symbols.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "185.0"],
                ["2024-01-02", "MSFT", "380.0"],
                ["2024-01-03", "AAPL", "187.0"],
            ],
        )
        result = _summarize_observation_table_canonical(path, None, "symbol")
        assert result["unique_symbol_count"] == 2
        assert result["min_date"] is None
        assert result["max_date"] is None

    def test_date_and_symbol_together(self, tmp_path):
        path = tmp_path / "both.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "185.0"],
            ],
        )
        result = _summarize_observation_table_canonical(path, "date", "symbol")
        assert result["min_date"] == "2024-01-01"
        assert result["max_date"] == "2024-01-01"
        assert result["unique_symbol_count"] == 1

    def test_empty_date_values_ignored(self, tmp_path):
        path = tmp_path / "empty_dates.csv"
        make_csv(
            path,
            [
                ["date", "symbol"],
                ["", "AAPL"],
                ["   ", "MSFT"],
                ["2024-01-01", "AAPL"],
            ],
        )
        result = _summarize_observation_table_canonical(path, "date", None)
        assert result["min_date"] == "2024-01-01"
        assert result["max_date"] == "2024-01-01"

    def test_empty_symbol_values_ignored(self, tmp_path):
        path = tmp_path / "empty_symbols.csv"
        make_csv(
            path,
            [
                ["date", "symbol"],
                ["2024-01-01", ""],
                ["2024-01-02", "   "],
                ["2024-01-03", "AAPL"],
            ],
        )
        result = _summarize_observation_table_canonical(path, None, "symbol")
        assert result["unique_symbol_count"] == 1

    def test_missing_date_column_raises(self, tmp_path):
        path = tmp_path / "no_date.csv"
        make_csv(path, [["symbol", "close"], ["AAPL", "185.0"]])
        with pytest.raises(ValueError, match="observation_date_column"):
            _summarize_observation_table_canonical(path, "date", None)

    def test_missing_symbol_column_raises(self, tmp_path):
        path = tmp_path / "no_symbol.csv"
        make_csv(path, [["date", "close"], ["2024-01-01", "185.0"]])
        with pytest.raises(ValueError, match="observation_symbol_column"):
            _summarize_observation_table_canonical(path, None, "symbol")

    def test_details_format_stable(self, tmp_path):
        path = tmp_path / "details.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "185.0"],
                ["2024-01-02", "MSFT", "380.0"],
            ],
        )
        result = _summarize_observation_table_canonical(path, "date", "symbol")
        assert "row_count=2" in result["details"]
        assert "date_column=date" in result["details"]
        assert "min_date=2024-01-01" in result["details"]
        assert "max_date=2024-01-02" in result["details"]
        assert "symbol_column=symbol" in result["details"]
        assert "unique_symbol_count=2" in result["details"]


# ---------------------------------------------------------------------------
# _summarize_observation_close_returns
# ---------------------------------------------------------------------------

class TestSummarizeObservationCloseReturns:
    def test_valid_two_symbol_csv_computes_returns(self, tmp_path):
        path = tmp_path / "returns.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "AAPL", "110.0"],
                ["2024-01-03", "AAPL", "105.0"],
                ["2024-01-01", "MSFT", "200.0"],
                ["2024-01-03", "MSFT", "220.0"],
            ],
        )
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        assert result["symbols_with_return"] == 2
        assert result["min_return"] is not None
        assert result["max_return"] is not None
        assert result["mean_return"] is not None
        assert result["close_column"] == "close"
        assert result["skipped_symbols"] == 0

    def test_lexicographic_date_ordering(self, tmp_path):
        # Dates are not in chronological order in file; lexicographic controls first/last
        path = tmp_path / "unordered.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-03-01", "AAPL", "110.0"],
                ["2024-01-01", "AAPL", "100.0"],  # earlier lexicographically
                ["2024-02-01", "AAPL", "105.0"],
            ],
        )
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        # AAPL: first=2024-01-01@100, last=2024-03-01@110
        assert result["symbols_with_return"] == 1
        assert result["min_return"] is not None
        assert result["max_return"] is not None

    def test_empty_close_skipped(self, tmp_path):
        path = tmp_path / "empty_close.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", ""],
                ["2024-01-02", "AAPL", "100.0"],
                ["2024-01-03", "AAPL", "110.0"],
            ],
        )
        # First row (empty close) is skipped; AAPL still has 2 valid dated rows
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        assert result["symbols_with_return"] == 1
        assert result["skipped_symbols"] == 0

    def test_non_numeric_close_skipped(self, tmp_path):
        path = tmp_path / "non_numeric.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "N/A"],
                ["2024-01-02", "AAPL", "100.0"],
                ["2024-01-03", "AAPL", "110.0"],
            ],
        )
        # N/A close row is skipped; AAPL still has 2 valid dated rows
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        assert result["symbols_with_return"] == 1
        assert result["skipped_symbols"] == 0

    def test_first_close_zero_skips_symbol(self, tmp_path):
        path = tmp_path / "zero_first.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "0.0"],
                ["2024-01-02", "AAPL", "110.0"],
            ],
        )
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        assert result["symbols_with_return"] == 0
        assert result["skipped_symbols"] == 1

    def test_single_date_symbol_skipped(self, tmp_path):
        path = tmp_path / "single_date.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
            ],
        )
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        assert result["symbols_with_return"] == 0
        assert result["skipped_symbols"] == 1

    def test_no_valid_returns(self, tmp_path):
        # All symbols have only one date or zero/NaN close
        # Since no row has BOTH date+symbol+valid_close with ≥2 distinct dates,
        # symbols_with_return=0 and skipped_symbols=0 (nothing enters symbol_data)
        path = tmp_path / "no_returns.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "N/A"],
                ["2024-01-01", "MSFT", ""],
                ["2024-01-01", "GOOG", "0.0"],
            ],
        )
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        assert result["symbols_with_return"] == 0
        assert result["min_return"] is None
        assert result["max_return"] is None
        assert result["mean_return"] is None
        # No symbol enters symbol_data (close is skipped for all), so skipped=0
        assert result["skipped_symbols"] == 0

    def test_missing_close_column_raises(self, tmp_path):
        path = tmp_path / "no_close.csv"
        make_csv(path, [["date", "symbol"], ["2024-01-01", "AAPL"]])
        with pytest.raises(ValueError, match="observation_close_column"):
            _summarize_observation_close_returns(path, "date", "symbol", "close")

    def test_missing_date_column_raises(self, tmp_path):
        path = tmp_path / "no_date.csv"
        make_csv(path, [["symbol", "close"], ["AAPL", "185.0"]])
        with pytest.raises(ValueError, match="observation_date_column"):
            _summarize_observation_close_returns(path, "date", "symbol", "close")

    def test_missing_symbol_column_raises(self, tmp_path):
        path = tmp_path / "no_symbol.csv"
        make_csv(path, [["date", "close"], ["2024-01-01", "185.0"]])
        with pytest.raises(ValueError, match="observation_symbol_column"):
            _summarize_observation_close_returns(path, "date", "symbol", "close")

    def test_no_symbol_names_in_details(self, tmp_path):
        """Symbol names must not appear in details for privacy."""
        path = tmp_path / "privacy.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "AAPL", "110.0"],
                ["2024-01-01", "MSFT", "200.0"],
                ["2024-01-03", "MSFT", "220.0"],
            ],
        )
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        details_lower = result["details"].lower()
        assert "aapl" not in details_lower
        assert "msft" not in details_lower
        assert "symbols_with_return=2" in result["details"]

    def test_details_format_stable(self, tmp_path):
        path = tmp_path / "details_fmt.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "AAPL", "110.0"],
            ],
        )
        result = _summarize_observation_close_returns(path, "date", "symbol", "close")
        assert "symbols_with_return=1" in result["details"]
        assert "close_column=close" in result["details"]
        assert "skipped_symbols=0" in result["details"]
        assert "min_return=" in result["details"]
        assert "max_return=" in result["details"]
        assert "mean_return=" in result["details"]


class TestSummarizeObservationMissingValues:
    """Unit tests for _summarize_observation_missing_values."""

    def test_missing_value_summary_counts_empty_strings(self, tmp_path):
        """Empty CSV field values count as missing."""
        path = tmp_path / "empty_val.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "volume"],
                ["2024-01-01", "AAPL", "1000"],
                ["2024-01-02", "AAPL", ""],  # empty string → missing
                ["2024-01-03", "AAPL", "3000"],
            ],
        )
        result = _summarize_observation_missing_values(path, ["volume"])
        assert result["row_count"] == 3
        assert result["missing"]["volume"] == 1
        assert "row_count=3" in result["details"]
        assert "missing[volume]=1" in result["details"]

    def test_missing_value_summary_counts_whitespace_only_strings(self, tmp_path):
        """Whitespace-only string values count as missing after strip."""
        path = tmp_path / "whitespace_val.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "volume"],
                ["2024-01-01", "AAPL", "1000"],
                ["2024-01-02", "AAPL", "   "],  # whitespace only → missing
                ["2024-01-03", "AAPL", "3000"],
            ],
        )
        result = _summarize_observation_missing_values(path, ["volume"])
        assert result["row_count"] == 3
        assert result["missing"]["volume"] == 1

    def test_missing_value_summary_counts_missing_csv_fields(self, tmp_path):
        """None from DictReader (field absent from row) counts as missing."""
        path = tmp_path / "missing_field.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "volume"],
                ["2024-01-01", "AAPL", "1000"],
                ["2024-01-02", "AAPL"],  # fewer fields → volume is None
                ["2024-01-03", "AAPL", "3000"],
            ],
        )
        result = _summarize_observation_missing_values(path, ["volume"])
        assert result["row_count"] == 3
        assert result["missing"]["volume"] == 1

    def test_missing_value_summary_multiple_columns(self, tmp_path):
        """Multiple columns are each summarized independently."""
        path = tmp_path / "multi_col.csv"
        make_csv(
            path,
            [
                ["date", "bid", "ask"],
                ["2024-01-01", "100.0", "101.0"],
                ["2024-01-02", "", "102.0"],  # bid missing
                ["2024-01-03", "103.0", ""],  # ask missing
                ["2024-01-04", "", ""],  # both missing
            ],
        )
        result = _summarize_observation_missing_values(path, ["bid", "ask"])
        assert result["row_count"] == 4
        assert result["missing"]["bid"] == 2
        assert result["missing"]["ask"] == 2

    def test_missing_requested_column_fails_deterministically(self, tmp_path):
        """Requesting a column not in header raises ValueError with column name."""
        path = tmp_path / "header_only.csv"
        make_csv(path, [["date", "symbol", "close"]])
        with pytest.raises(ValueError, match="not found in CSV header"):
            _summarize_observation_missing_values(path, ["nonexistent_column"])

    def test_header_whitespace_normalization(self, tmp_path):
        """Header tokens are stripped when matching column names."""
        path = tmp_path / "header_ws.csv"
        make_csv(
            path,
            [
                [" date ", "symbol", " close "],  # whitespace in header
                ["2024-01-01", "AAPL", "100.0"],
            ],
        )
        # Pass stripped column names (as caller would)
        result = _summarize_observation_missing_values(path, ["date", "close"])
        assert result["row_count"] == 1
        assert result["missing"]["date"] == 0
        assert result["missing"]["close"] == 0

    def test_internal_whitespace_in_column_names_preserved(self, tmp_path):
        """Internal whitespace in column names is preserved for matching."""
        path = tmp_path / "internal_ws.csv"
        make_csv(
            path,
            [
                ["date", "close price", "adj close"],
                ["2024-01-01", "100.0", "99.0"],
                ["2024-01-02", "", "98.0"],  # close price missing
            ],
        )
        result = _summarize_observation_missing_values(path, ["close price", "adj close"])
        assert result["row_count"] == 2
        assert result["missing"]["close price"] == 1
        assert result["missing"]["adj close"] == 0

    def test_details_format_stable(self, tmp_path):
        """details string is stable: row_count=N, missing[col]=N per column."""
        path = tmp_path / "stable_fmt.csv"
        make_csv(
            path,
            [
                ["date", "bid", "ask"],
                ["2024-01-01", "100.0", "101.0"],
            ],
        )
        result = _summarize_observation_missing_values(path, ["bid", "ask"])
        # Check format is stable
        assert result["details"].startswith("row_count=1; ")
        assert "missing[bid]=0" in result["details"]
        assert "missing[ask]=0" in result["details"]
        # No row-level values or symbols
        details_lower = result["details"].lower()
        assert "100.0" not in details_lower
        assert "101.0" not in details_lower
        assert "aapl" not in details_lower

    def test_no_row_level_values_in_details(self, tmp_path):
        """Details must not contain actual data values."""
        path = tmp_path / "privacy.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "185.50"],
                ["2024-01-02", "AAPL", "190.25"],
            ],
        )
        result = _summarize_observation_missing_values(path, ["close"])
        details_lower = result["details"].lower()
        assert "185" not in details_lower
        assert "190" not in details_lower
        assert "aapl" not in details_lower


class TestSummarizeObservationDuplicateRows:
    """Unit tests for _summarize_observation_duplicate_rows."""

    def test_no_duplicates(self, tmp_path):
        """No duplicate (symbol, date) keys → duplicate_row_count = 0."""
        path = tmp_path / "no_dup.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "AAPL", "110.0"],
                ["2024-01-01", "MSFT", "200.0"],
                ["2024-01-03", "MSFT", "210.0"],
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["total_rows"] == 4
        assert result["duplicate_row_count"] == 0
        assert result["affected_key_count"] == 0
        assert result["affected_symbols"] == []
        assert result["affected_dates"] == []
        assert result["has_duplicates"] is False
        assert result["duplicate_examples"] == []
        assert "no duplicate observation rows were detected" in result["details"]

    def test_one_duplicate_pair_two_occurrences(self, tmp_path):
        """A (symbol, date) key appearing exactly twice → duplicate_row_count = 1."""
        path = tmp_path / "one_dup_pair.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "AAPL", "110.0"],
                ["2024-01-01", "AAPL", "101.0"],  # duplicate of row 1
                ["2024-01-03", "MSFT", "200.0"],
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["total_rows"] == 4
        assert result["duplicate_row_count"] == 1
        assert result["affected_key_count"] == 1
        assert result["affected_symbols"] == ["AAPL"]
        assert result["affected_dates"] == ["2024-01-01"]
        assert result["has_duplicates"] is True
        assert len(result["duplicate_examples"]) == 1
        assert result["duplicate_examples"][0]["symbol"] == "AAPL"
        assert result["duplicate_examples"][0]["date"] == "2024-01-01"
        assert result["duplicate_examples"][0]["row_count"] == 2
        assert result["duplicate_examples"][0]["excess_row_count"] == 1

    def test_duplicate_pair_three_occurrences(self, tmp_path):
        """A key appearing three times → duplicate_row_count = 2 (3 - 1)."""
        path = tmp_path / "triple_dup.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-01", "AAPL", "101.0"],
                ["2024-01-01", "AAPL", "102.0"],
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["duplicate_row_count"] == 2
        assert result["affected_key_count"] == 1
        assert result["duplicate_examples"][0]["row_count"] == 3
        assert result["duplicate_examples"][0]["excess_row_count"] == 2

    def test_multiple_duplicate_pairs(self, tmp_path):
        """Two distinct duplicate keys → affected_key_count = 2."""
        path = tmp_path / "multi_dup.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "AAPL", "110.0"],
                ["2024-01-01", "AAPL", "101.0"],  # AAPL 2024-01-01 duplicate
                ["2024-01-01", "MSFT", "200.0"],
                ["2024-01-02", "MSFT", "210.0"],
                ["2024-01-01", "MSFT", "201.0"],  # MSFT 2024-01-01 duplicate
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["duplicate_row_count"] == 2
        assert result["affected_key_count"] == 2
        assert set(result["affected_symbols"]) == {"AAPL", "MSFT"}
        assert set(result["affected_dates"]) == {"2024-01-01"}
        assert len(result["duplicate_examples"]) == 2
        # Sorted deterministically by (symbol, date)
        assert result["duplicate_examples"][0]["symbol"] == "AAPL"
        assert result["duplicate_examples"][0]["date"] == "2024-01-01"
        assert result["duplicate_examples"][1]["symbol"] == "MSFT"
        assert result["duplicate_examples"][1]["date"] == "2024-01-01"

    def test_same_symbol_different_date_not_duplicate(self, tmp_path):
        """Same symbol on different dates is not a duplicate."""
        path = tmp_path / "same_sym_diff_date.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "AAPL", "110.0"],
                ["2024-01-03", "AAPL", "105.0"],
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["duplicate_row_count"] == 0
        assert result["has_duplicates"] is False
        assert result["affected_symbols"] == []

    def test_same_date_different_symbol_not_duplicate(self, tmp_path):
        """Same date across different symbols is not a duplicate."""
        path = tmp_path / "same_date_diff_sym.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-01", "MSFT", "200.0"],
                ["2024-01-01", "GOOG", "150.0"],
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["duplicate_row_count"] == 0
        assert result["has_duplicates"] is False
        assert result["affected_symbols"] == []

    def test_empty_csv(self, tmp_path):
        """CSV with header only and no data rows."""
        path = tmp_path / "empty.csv"
        make_csv(path, [["date", "symbol", "close"]])
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["total_rows"] == 0
        assert result["duplicate_row_count"] == 0
        assert result["has_duplicates"] is False
        assert result["duplicate_examples"] == []
        assert "no duplicate observation rows were detected" in result["details"]

    def test_duplicate_examples_sorted_deterministic(self, tmp_path):
        """duplicate_examples are sorted by (symbol, date) for determinism."""
        path = tmp_path / "sorted_dups.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-02", "MSFT", "210.0"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-02", "MSFT", "211.0"],
                ["2024-01-01", "AAPL", "101.0"],
                ["2024-01-01", "GOOG", "150.0"],
                ["2024-01-01", "GOOG", "151.0"],
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert len(result["duplicate_examples"]) == 3
        # Sorted alphabetically by symbol: AAPL, GOOG, MSFT
        assert result["duplicate_examples"][0]["symbol"] == "AAPL"
        assert result["duplicate_examples"][1]["symbol"] == "GOOG"
        assert result["duplicate_examples"][2]["symbol"] == "MSFT"

    def test_json_serializable(self, tmp_path):
        """Return dict must be JSON-serializable (no Python sets)."""
        import json
        path = tmp_path / "json_ok.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-01", "AAPL", "101.0"],
                ["2024-01-02", "AAPL", "110.0"],
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        # Should not raise
        json.dumps(result)
        assert isinstance(result["affected_symbols"], list)
        assert isinstance(result["affected_dates"], list)
        assert isinstance(result["duplicate_examples"], list)

    def test_missing_date_column_raises(self, tmp_path):
        """Missing date column raises ValueError."""
        path = tmp_path / "no_date.csv"
        make_csv(path, [["symbol", "close"], ["AAPL", "100.0"]])
        with pytest.raises(ValueError, match="observation_date_column"):
            _summarize_observation_duplicate_rows(path, "date", "symbol")

    def test_missing_symbol_column_raises(self, tmp_path):
        """Missing symbol column raises ValueError."""
        path = tmp_path / "no_sym.csv"
        make_csv(path, [["date", "close"], ["2024-01-01", "100.0"]])
        with pytest.raises(ValueError, match="observation_symbol_column"):
            _summarize_observation_duplicate_rows(path, "date", "symbol")

    def test_row_with_missing_date_skipped(self, tmp_path):
        """Row with missing date value is skipped and does not cause false duplicate."""
        path = tmp_path / "missing_date.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["", "AAPL", "110.0"],   # missing date → skipped
                ["2024-01-02", "AAPL", "101.0"],  # different date, not a duplicate of row 1
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        # Row with empty date is skipped; rows 1 and 3 have different dates
        assert result["total_rows"] == 3
        assert result["duplicate_row_count"] == 0

    def test_row_with_missing_symbol_skipped(self, tmp_path):
        """Row with missing symbol value is skipped and does not cause false duplicate."""
        path = tmp_path / "missing_sym.csv"
        make_csv(
            path,
            [
                ["date", "symbol", "close"],
                ["2024-01-01", "AAPL", "100.0"],
                ["2024-01-01", "", "110.0"],  # missing symbol → skipped
                ["2024-01-01", "MSFT", "101.0"],  # different symbol, not a duplicate of row 1
            ],
        )
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["total_rows"] == 3
        assert result["duplicate_row_count"] == 0

    def test_duplicate_examples_capped_at_10(self, tmp_path):
        """When > 10 duplicate keys exist, only first 10 appear in examples."""
        rows = [["date", "symbol", "close"]]
        for i in range(20):
            rows.append([f"2024-01-01", f"SYM{i:02d}", "100.0"])
            rows.append([f"2024-01-01", f"SYM{i:02d}", "101.0"])  # duplicate
        path = tmp_path / "many_dups.csv"
        make_csv(path, rows)
        result = _summarize_observation_duplicate_rows(path, "date", "symbol")
        assert result["affected_key_count"] == 20
        assert len(result["duplicate_examples"]) == 10
        assert result["duplicate_row_count"] == 20  # 1 excess per key
