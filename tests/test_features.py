import pandas as pd
from engine.edge_discovery import features as ft


def make_row():
    return {
        "arrival_mid": 100.0,
        "vwap_exec": 100.5,
        "post_mid": 100.2,
        "signed_qty": 1000,
        "adv": 100000,
        "arrival_ts": "2026-04-01T09:30:00",
        "end_ts": "2026-04-01T09:35:00",
        "sigma": 0.5,
        "participation": 0.01,
    }


def test_phi_and_sqrt_phi_computation():
    df = pd.DataFrame([make_row()])
    f = ft.build_features(df, strict=True)
    assert "phi" in f.columns
    assert abs(f.iloc[0]["phi"] - (1000.0 / 100000.0)) < 1e-12


def test_I_J_and_normalization():
    df = pd.DataFrame([make_row()])
    f = ft.build_features(df, strict=True)
    assert "I" in f.columns and "J" in f.columns
    assert "I_norm" in f.columns and "J_norm" in f.columns
    assert abs(f.iloc[0]["I"] - (1000.0/abs(1000.0) * (100.5 - 100.0))) < 1e-12
