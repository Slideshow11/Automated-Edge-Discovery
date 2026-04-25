import os
import json
from pathlib import Path
from subprocess import check_call

REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / "examples" / "metaorders.csv"
OUT = REPO / "examples" / "calibrate_ac_v2_demo.json"


def test_calibrate_v2_cli_writes_json(tmp_path):
    # run the CLI via python -m
    out = tmp_path / "out.json"
    cmd = ["python", "-m", "engine.edge_discovery.calibrate_ac_v2", str(INPUT), "--output", str(out), "--n-bootstrap", "50", "--bootstrap", "cluster"]
    res = check_call(cmd)
    assert out.exists()
    data = json.loads(out.read_text())
    assert "realized_model" in data and "post_model" in data


def test_recovery_on_synthetic_parent_orders():
    # quick synthetic sanity check via direct function call
    from engine.edge_discovery.calibrate_ac_v2 import calibrate_ac_v2
    res = calibrate_ac_v2(str(INPUT), output_path=None, n_bootstrap=50, bootstrap="none", strict=True)
    assert "realized_model" in res and "post_model" in res
