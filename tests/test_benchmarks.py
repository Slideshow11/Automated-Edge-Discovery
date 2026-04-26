import pandas as pd
from engine.edge_discovery import benchmarks as bm


def test_compute_vwap_exec_basic():
    fills = [
        {"price": 100.0, "size": 10},
        {"price": 101.0, "size": 20},
    ]
    vwap = bm.compute_vwap_exec(fills)
    assert abs(vwap - (100*10 + 101*20) / 30) < 1e-9


def test_compute_post_mid():
    df = pd.DataFrame({
        "ts": ["2026-04-01T10:00:00", "2026-04-01T10:30:00"],
        "mid": [100.0, 100.5],
    })
    pm = bm.compute_post_mid(df, "2026-04-01T10:15:00")
    assert pm == 100.5
