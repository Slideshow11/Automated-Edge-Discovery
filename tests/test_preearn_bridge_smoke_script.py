import json
from pathlib import Path
from unittest import mock

import pytest

from engine.edge_discovery.evaluation import EvaluationLabel, EvaluationResult
from engine.edge_discovery.hypotheses.batch import BatchResult
from scripts.local.smoke_preearn_bridge import main as smoke_main


def _fake_batch_result(
    status: str = "dry_run",
    n_success: int = 0,
    n_error: int = 0,
    n_candidates_selected: int = 1,
) -> BatchResult:
    return BatchResult(
        batch_id="test-batch-001",
        hypothesis_id="smoke-preearn-0001",
        status=status,
        n_candidates_generated=1,
        n_candidates_selected=n_candidates_selected,
        n_success=n_success,
        n_error=n_error,
        results=[],
        output_artifacts={},
    )


# ---------------------------------------------------------------------------
# Original smoke tests — dry-run, ledger, output
# ---------------------------------------------------------------------------


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

    rc = smoke_main(args)
    assert rc == 0

    files = list(out.glob("batch_*.json"))
    assert len(files) == 1

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
# Evaluator integration tests
# ---------------------------------------------------------------------------


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
