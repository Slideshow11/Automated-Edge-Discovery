import json
from pathlib import Path
from unittest import mock

import pytest

from engine.edge_discovery.evaluation import EvaluationLabel, EvaluationResult
from engine.edge_discovery.hypotheses.batch import BatchResult
from scripts.local.smoke_preearn_bridge import main as smoke_main


def test_smoke_argparse_and_dry_run(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    ledger = tmp_path / "ledger.jsonl"
    args = [
        "--preearn-repo-path",
        "/tmp/fake_repo",
        "--options-db-path",
        "/tmp/fake_options.db",
        "--dry-run",
        "--output-dir",
        str(out),
        "--ledger-path",
        str(ledger),
    ]

    # Run the script's main entry with args (dry-run should be default safe path)
    rc = smoke_main(args)
    assert rc == 0

    # Summary JSON must exist
    files = list(out.glob("batch_*.json"))
    assert len(files) == 1

    # Ledger must exist and contain at least one line
    assert ledger.exists()
    content = [ln.strip() for ln in ledger.read_text().splitlines() if ln.strip()]
    assert len(content) >= 1


def test_smoke_default_is_dry(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out2"
    args = [
        "--preearn-repo-path",
        "/tmp/fake_repo",
        "--options-db-path",
        "/tmp/fake_options.db",
        "--output-dir",
        str(out),
    ]
    rc = smoke_main(args)
    assert rc == 0
    files = list(out.glob("batch_*.json"))
    assert len(files) == 1


# ---------------------------------------------------------------------------
# Tests for evaluator integration
# ---------------------------------------------------------------------------


def _fake_batch_result() -> BatchResult:
    return BatchResult(
        batch_id="test-batch-001",
        hypothesis_id="smoke-preearn-0001",
        status="dry_run",
        n_candidates_generated=1,
        n_candidates_selected=1,
        n_success=0,
        n_error=0,
        results=[],
        output_artifacts={},
    )


def test_evaluate_batch_result_called_after_run_candidate_batch(tmp_path, monkeypatch, capsys):
    """Verify evaluate_batch_result is called with the BatchResult from run_candidate_batch."""
    out = tmp_path / "out3"
    ledger = tmp_path / "ledger.jsonl"
    fake_result = _fake_batch_result()

    with mock.patch(
        "scripts.local.smoke_preearn_bridge.run_candidate_batch",
        return_value=fake_result,
    ) as mock_run, mock.patch(
        "scripts.local.smoke_preearn_bridge.evaluate_batch_result",
        return_value=EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason="dry_run_no_execution",
        ),
    ) as mock_eval:
        args = [
            "--preearn-repo-path", "/tmp/fake_repo",
            "--options-db-path", "/tmp/fake_options.db",
            "--output-dir", str(out),
            "--ledger-path", str(ledger),
        ]
        rc = smoke_main(args)

    assert rc == 0
    mock_run.assert_called_once()
    mock_eval.assert_called_once_with(fake_result)


def test_evaluation_label_and_reason_printed(tmp_path, monkeypatch, capsys):
    """Verify evaluation label and reason appear in stdout after batch summary."""
    out = tmp_path / "out4"
    ledger = tmp_path / "ledger.jsonl"
    fake_result = _fake_batch_result()

    with mock.patch(
        "scripts.local.smoke_preearn_bridge.run_candidate_batch",
        return_value=fake_result,
    ), mock.patch(
        "scripts.local.smoke_preearn_bridge.evaluate_batch_result",
        return_value=EvaluationResult(
            label=EvaluationLabel.PROMISING,
            reason="execution_thresholds_met",
            metrics={"n_success": 1, "n_error": 0},
        ),
    ):
        args = [
            "--preearn-repo-path", "/tmp/fake_repo",
            "--options-db-path", "/tmp/fake_options.db",
            "--output-dir", str(out),
            "--ledger-path", str(ledger),
        ]
        rc = smoke_main(args)

    assert rc == 0
    captured = capsys.readouterr().out
    assert "evaluation_label: promising_for_review" in captured
    assert "execution_thresholds_met" in captured


def test_evaluation_warnings_printed_when_present(tmp_path, monkeypatch, capsys):
    """Verify warnings appear in stdout when the evaluation result has warnings."""
    out = tmp_path / "out5"
    fake_result = _fake_batch_result()

    with mock.patch(
        "scripts.local.smoke_preearn_bridge.run_candidate_batch",
        return_value=fake_result,
    ), mock.patch(
        "scripts.local.smoke_preearn_bridge.evaluate_batch_result",
        return_value=EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason="error_rate_too_high",
            warnings=("error_rate_75.0pct_exceeds_limit",),
        ),
    ):
        args = [
            "--preearn-repo-path", "/tmp/fake_repo",
            "--options-db-path", "/tmp/fake_options.db",
            "--output-dir", str(out),
        ]
        rc = smoke_main(args)

    assert rc == 0
    captured = capsys.readouterr().out
    assert "evaluation_warnings:" in captured
    assert "error_rate_75.0pct_exceeds_limit" in captured


def test_evaluate_only_mode_with_mocked_ledger_entry(tmp_path, monkeypatch, capsys):
    """Verify --evaluate-only skips run_candidate_batch and evaluates the ledger entry."""
    ledger = tmp_path / "ledger.jsonl"

    # Write a valid ledger entry for the evaluate-only path
    entry = {
        "run_id": "test-batch-eval-001",
        "run_type": "preearn_candidate_batch",
        "started_at": "2025-01-01T00:00:00Z",
        "completed_at": "2025-01-01T00:01:00Z",
        "status": "success",
        "config_hash": "abcd1234abcd1234",
        "git_commit": "deadbeef",
        "error": None,
        "input_artifacts": {"hypothesis_id": "smoke-preearn-0001"},
        "output_artifacts": {},
        "metrics_summary": {
            "batch_status": "success",
            "hypothesis_id": "smoke-preearn-0001",
            "n_candidates_generated": 1,
            "n_candidates_selected": 1,
            "n_success": 1,
            "n_error": 0,
        },
    }
    ledger.write_text(json.dumps(entry) + "\n")

    args = [
        "--preearn-repo-path", "/tmp/fake_repo",
        "--options-db-path", "/tmp/fake_options.db",
        "--evaluate-only",
        "--ledger-path", str(ledger),
    ]
    rc = smoke_main(args)

    assert rc == 0
    captured = capsys.readouterr().out
    assert "evaluation_label:" in captured
    assert "evaluation_reason:" in captured


def test_evaluate_only_requires_ledger_path(tmp_path, monkeypatch, capsys):
    """--evaluate-only without --ledger-path returns non-zero."""
    args = [
        "--preearn-repo-path", "/tmp/fake_repo",
        "--options-db-path", "/tmp/fake_options.db",
        "--evaluate-only",
    ]
    rc = smoke_main(args)
    assert rc == 1
    captured = capsys.readouterr().err
    assert "--evaluate-only requires --ledger-path" in captured


def test_evaluate_only_missing_ledger_returns_nonzero(tmp_path, monkeypatch, capsys):
    """--evaluate-only with a non-existent ledger file returns non-zero."""
    missing = tmp_path / "nonexistent.jsonl"
    args = [
        "--preearn-repo-path", "/tmp/fake_repo",
        "--options-db-path", "/tmp/fake_options.db",
        "--evaluate-only",
        "--ledger-path", str(missing),
    ]
    rc = smoke_main(args)
    assert rc == 1


def test_example_json_loads_and_smoke_flow_runs_with_mocks(tmp_path, monkeypatch, capsys):
    """Load the example JSON and verify smoke flow runs end-to-end with mocked batch."""
    import json as json_mod

    from engine.edge_discovery.hypotheses import HypothesisSpec

    example_path = Path("examples/preearn_hypotheses/basic_preearn_dpe2_delta50.json")
    if not example_path.exists():
        pytest.skip("example JSON not found")

    data = json_mod.load(open(example_path))
    spec = HypothesisSpec.from_dict(data)
    assert spec.hypothesis_id == "preearn-iv-ramp-basic-v1"

    # Build a fake BatchResult matching what run_candidate_batch would return for this spec
    fake_result = BatchResult(
        batch_id="test-batch-example-001",
        hypothesis_id=spec.hypothesis_id,
        status="dry_run",
        n_candidates_generated=1,
        n_candidates_selected=1,
        n_success=0,
        n_error=0,
        results=[],
        output_artifacts={},
    )

    out = tmp_path / "out_example"
    with mock.patch(
        "scripts.local.smoke_preearn_bridge.run_candidate_batch",
        return_value=fake_result,
    ), mock.patch(
        "scripts.local.smoke_preearn_bridge.evaluate_batch_result",
        return_value=EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason="dry_run_no_execution",
        ),
    ):
        args = [
            "--preearn-repo-path", "/tmp/fake_repo",
            "--options-db-path", "/tmp/fake_options.db",
            "--output-dir", str(out),
        ]
        rc = smoke_main(args)

    assert rc == 0
    captured = capsys.readouterr().out
    assert "batch_id: test-batch-example-001" in captured
    assert "evaluation_label: needs_more_data" in captured


def test_no_real_subprocess_in_smoke_tests(tmp_path, monkeypatch):
    """Verify no subprocess calls occur in the smoke tests."""
    import subprocess

    with mock.patch(
        "scripts.local.smoke_preearn_bridge.run_candidate_batch",
        return_value=_fake_batch_result(),
    ), mock.patch(
        "scripts.local.smoke_preearn_bridge.evaluate_batch_result",
        return_value=EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason="dry_run_no_execution",
        ),
    ):
        out = tmp_path / "out_subprocess"
        args = [
            "--preearn-repo-path", "/tmp/fake_repo",
            "--options-db-path", "/tmp/fake_options.db",
            "--output-dir", str(out),
        ]
        with mock.patch.object(subprocess, "run") as mock_subprocess_run:
            smoke_main(args)
            mock_subprocess_run.assert_not_called()

