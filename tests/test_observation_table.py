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
    _summarize_observation_table_canonical,
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
