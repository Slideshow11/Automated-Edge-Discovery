"""Tests for watch_pr_gate_state.py — read-only PR gate watchdog."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure scripts/local is on the path for local imports.
_SCRIPT_LOCAL = Path(__file__).parent.parent / "scripts" / "local"
sys.path.insert(0, str(_SCRIPT_LOCAL))

from watch_pr_gate_state import (
    build_compact,
    build_telegram_summary,
    _ci_label,
    _codex_label,
    STATE_LABELS,
    EXIT_NETWORK_ERROR,
    EXIT_ARGUMENT_ERROR,
    run,
)
from classify_pr_gate_state import CLASSIFICATIONS as _CLASSIFICATIONS

WATCHDOG = str(_SCRIPT_LOCAL / "watch_pr_gate_state.py")


class TestBuildCompact:
    """Unit tests for compact format builder."""

    def test_compact_all_classifications_no_blockers(self):
        for cls in [
            "ci_pending", "ci_failed", "codex_request_needed", "codex_pending",
            "codex_suggestions", "codex_clean", "ready_for_reviewer",
            "blocked_scope", "blocked_wrong_base", "blocked_pr_closed",
            "blocked_pr_merged", "unknown",
        ]:
            line = build_compact(189, cls, [])
            assert f"[PR #189] {cls}" in line
            assert "blockers: none" in line

    def test_compact_with_blockers(self):
        line = build_compact(189, "ci_failed", ["test-3.11", "governance-validators"])
        assert "ci_failed" in line
        assert "test-3.11" in line
        assert "governance-validators" in line
        assert "blockers:" in line

    def test_telegram_summary(self):
        line = build_telegram_summary(189, "ready_for_reviewer", [])
        assert "PR #189" in line
        assert "ready_for_reviewer" in line
        assert "CI: pass" in line
        # ready_for_reviewer has no Codex state, so Codex label is NA
        assert "Codex: NA" in line
        assert "blockers: none" in line

    def test_telegram_summary_with_blockers(self):
        line = build_telegram_summary(189, "ci_failed", ["test-3.11"])
        assert "ci_failed" in line
        assert "CI: fail" in line
        assert "test-3.11" in line


class TestCiLabel:
    def test_ci_pending_is_pending(self):
        assert _ci_label("ci_pending") == "pending"

    def test_ci_failed_is_fail(self):
        assert _ci_label("ci_failed") == "fail"

    def test_all_non_ci_states_are_pass(self):
        for cls in ["ready_for_reviewer", "blocked_scope", "codex_clean", "codex_suggestions"]:
            assert _ci_label(cls) == "pass", f"{cls} should map to 'pass'"


class TestCodexLabel:
    def test_codex_clean_is_clean(self):
        assert _codex_label("codex_clean") == "clean"

    def test_codex_pending_is_pending(self):
        assert _codex_label("codex_pending") == "pending"

    def test_codex_suggestions_is_suggestions(self):
        assert _codex_label("codex_suggestions") == "suggestions"

    def test_codex_request_needed_is_needed(self):
        assert _codex_label("codex_request_needed") == "needed"

    def test_non_codex_states_are_NA(self):
        for cls in ["ready_for_reviewer", "blocked_scope", "ci_pending", "ci_failed"]:
            assert _codex_label(cls) == "NA", f"{cls} should map to 'NA'"


class TestExitCodes:
    """Exit code deterministicism."""

    def test_missing_required_args_exits_3(self):
        result = subprocess.run(
            [sys.executable, WATCHDOG],
            capture_output=True,
        )
        assert result.returncode == 3, f"Expected 3, got {result.returncode}: {result.stderr.decode()}"

    def test_missing_pr_number_exits_3(self):
        result = subprocess.run(
            [sys.executable, WATCHDOG,
             "--repo-owner", "Slideshow11",
             "--repo-name", "Automated-Edge-Discovery"],
            capture_output=True,
        )
        assert result.returncode == 3


class TestClassificationCoverage:
    """All classifier classifications are known to the watchdog."""

    def test_all_classifier_classifications_mapped(self):
        for cls in _CLASSIFICATIONS:
            assert cls in STATE_LABELS, f"Missing STATE_LABELS entry for: {cls}"

    def test_compact_line_contains_no_newlines(self):
        for cls in _CLASSIFICATIONS:
            line = build_compact(1, cls, [])
            assert "\n" not in line, f"Single-line format violated for {cls}"


class TestUrllibErrorHandling:
    """URLError gives exit code 2, not generic exception handling."""

    def test_urllib_error_returns_exit_2(self):
        import urllib.error
        with patch(
            "watch_pr_gate_state.fetch_live_payloads",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            exit_code = run([
                "--repo-owner", "x",
                "--repo-name", "y",
                "--pr-number", "1",
            ])
            assert exit_code == EXIT_NETWORK_ERROR

    def test_value_error_returns_exit_3(self):
        with patch(
            "watch_pr_gate_state.fetch_live_payloads",
            side_effect=ValueError("bad argument"),
        ):
            exit_code = run([
                "--repo-owner", "x",
                "--repo-name", "y",
                "--pr-number", "1",
            ])
            assert exit_code == EXIT_ARGUMENT_ERROR


def _mock_packet(classification: str, blockers: list[str] | None = None) -> dict:
    return {
        "classification": classification,
        "blockers": blockers or [],
        "ci_state": "pass",
        "codex_state": "clean",
    }


class TestExitCodeOnly:
    """--exit-code-only must produce no stdout in every path."""

    def test_exit_code_only_empty_on_success(self, capsys):
        with patch("watch_pr_gate_state.fetch_live_payloads") as mock_fetch:
            mock_fetch.return_value = (MagicMock(), [], [], [], [], [])
            with patch("watch_pr_gate_state.classify_payloads") as mock_cp:
                mock_cp.return_value = _mock_packet("ready_for_reviewer")
                code = run([
                    "--repo-owner", "x", "--repo-name", "y", "--pr-number", "1",
                    "--exit-code-only",
                ])
                assert code == 0
                out = capsys.readouterr().out
                assert out == "", f"expected no stdout, got: {out!r}"

    def test_exit_code_only_empty_on_codex_pending(self, capsys):
        with patch("watch_pr_gate_state.fetch_live_payloads") as mock_fetch:
            mock_fetch.return_value = (MagicMock(), [], [], [], [], [])
            with patch("watch_pr_gate_state.classify_payloads") as mock_cp:
                mock_cp.return_value = _mock_packet("codex_pending")
                code = run([
                    "--repo-owner", "x", "--repo-name", "y", "--pr-number", "1",
                    "--exit-code-only",
                ])
                assert code == 0
                out = capsys.readouterr().out
                assert out == "", f"expected no stdout, got: {out!r}"

    def test_exit_code_only_returns_exit_0_with_blockers(self, capsys):
        with patch("watch_pr_gate_state.fetch_live_payloads") as mock_fetch:
            mock_fetch.return_value = (MagicMock(), [], [], [], [], [])
            with patch("watch_pr_gate_state.classify_payloads") as mock_cp:
                mock_cp.return_value = _mock_packet("codex_suggestions", ["test-3.11"])
                code = run([
                    "--repo-owner", "x", "--repo-name", "y", "--pr-number", "1",
                    "--exit-code-only",
                ])
                assert code == 0
                out = capsys.readouterr().out
                assert out == "", f"expected no stdout, got: {out!r}"

    def test_json_still_prints(self, capsys):
        with patch("watch_pr_gate_state.fetch_live_payloads") as mock_fetch:
            mock_fetch.return_value = (MagicMock(), [], [], [], [], [])
            with patch("watch_pr_gate_state.classify_payloads") as mock_cp:
                mock_cp.return_value = _mock_packet("ready_for_reviewer")
                code = run([
                    "--repo-owner", "x", "--repo-name", "y", "--pr-number", "1",
                    "--json",
                ])
                assert code == 0
                out = capsys.readouterr().out
                assert out.strip(), "expected JSON output"

    def test_compact_still_prints(self, capsys):
        with patch("watch_pr_gate_state.fetch_live_payloads") as mock_fetch:
            mock_fetch.return_value = (MagicMock(), [], [], [], [], [])
            with patch("watch_pr_gate_state.classify_payloads") as mock_cp:
                mock_cp.return_value = _mock_packet("ci_pending")
                code = run([
                    "--repo-owner", "x", "--repo-name", "y", "--pr-number", "1",
                    "--compact",
                ])
                assert code == 0
                out = capsys.readouterr().out
                assert "[PR #1]" in out

    def test_default_summary_still_prints(self, capsys):
        with patch("watch_pr_gate_state.fetch_live_payloads") as mock_fetch:
            mock_fetch.return_value = (MagicMock(), [], [], [], [], [])
            with patch("watch_pr_gate_state.classify_payloads") as mock_cp:
                mock_cp.return_value = _mock_packet("ci_failed", ["test-3.11"])
                code = run([
                    "--repo-owner", "x", "--repo-name", "y", "--pr-number", "1",
                ])
                assert code == 0
                out = capsys.readouterr().out
                assert "PR #1" in out and "ci_failed" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])