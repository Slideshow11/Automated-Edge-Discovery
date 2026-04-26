import pandas as pd
from engine.edge_discovery import diagnostics as diag


def test_diagnostic_report_writes_files(tmp_path):
    # construct a tiny DataFrame suitable for the diagnostics formulas
    n = 10
    df = pd.DataFrame({
        "I_norm": [1.0 + 0.1 * i for i in range(n)],
        "J_norm": [0.5 + 0.05 * i for i in range(n)],
        "sqrt_phi": [0.1 * i for i in range(n)],
        "phi": [0.01 * i for i in range(n)],
        "parent_id": [f"p{i}" for i in range(n)],
        # include an example lag column
        "post_mid_60": [0.2 + 0.01 * i for i in range(n)],
    })

    results = {
        "realized_model": {"formula": "I_norm ~ sqrt_phi + phi"},
        "post_model": {"formula": "J_norm ~ phi"},
    }

    out = tmp_path / "diag_out"
    summary = diag.diagnostic_report(df, results, out, lag_fields=[60], top_n=5)

    # basic assertions about the summary structure
    assert "realized_model" in summary and "post_model" in summary
    assert "top_influential_csv" in summary
    # CSV should have been written (or None if no influence rows)
    if summary["top_influential_csv"]:
        path = tmp_path / "diag_out" / "top_influential_orders.csv"
        assert path.exists(), "expected top_influential_orders.csv to be written"
    # diagnostics summary file should exist
    assert (tmp_path / "diag_out" / "diagnostics_summary.json").exists()
