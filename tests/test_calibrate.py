import json
from pathlib import Path

from engine.edge_discovery import calibrate_costs


def test_calibrate_from_csv(tmp_path):
    p = tmp_path / "trades.csv"
    p.write_text("timestamp,size,price\n2021-01-01T09:30:00,10,100.0\n2021-01-01T09:31:00,-5,101.0\n")

    params = calibrate_costs.calibrate_from_csv(str(p))
    assert "avg_trade_value" in params
    assert "temp_impact_coeff" in params
    assert params["avg_trade_value"] > 0
    assert params["temp_impact_coeff"] > 0
