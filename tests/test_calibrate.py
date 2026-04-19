import csv
import math
import random
from pathlib import Path

import numpy as np

from engine.edge_discovery import calibrate_costs


def test_calibrate_from_csv_basic(tmp_path):
    # basic smoke test that the function reads a small CSV and returns keys
    p = tmp_path / "trades.csv"
    p.write_text("timestamp,size,price\n2021-01-01T09:30:00,10,100.0\n2021-01-01T09:31:00,-5,101.0\n")

    params = calibrate_costs.calibrate_from_csv(str(p))
    assert "gamma" in params
    assert "eta" in params
    assert params["gamma"] is not None


def test_calibrate_recovers_parameters(tmp_path):
    # Create synthetic data with known gamma and eta and small noise
    gamma_true = 0.5
    eta_true = 1.2
    adv = 1e6

    rng = random.Random(0)
    rows = []
    n = 200
    price = 100.0
    for i in range(n):
        size = rng.choice([50, 100, 200, -50, -150])
        # compute per-share signed cost according to model
        x1 = math.sqrt(abs(size) / adv) * math.copysign(1.0, size)
        x2 = size / adv
        noise = rng.gauss(0, 1e-5)
        cost = gamma_true * x1 + eta_true * x2 + noise
        # we write CSV with a cost column so the calibrator uses it directly
        rows.append({"timestamp": f"t{i}", "size": str(size), "price": str(price), "adv": str(adv), "cost": str(cost)})
        price += 0.0001  # tiny price drift

    p = tmp_path / "synth.csv"
    with p.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "size", "price", "adv", "cost"])
        writer.writeheader()
        writer.writerows(rows)

    out = calibrate_costs.calibrate_from_csv(str(p))

    # recovered parameters should be close to true values
    assert abs(out["gamma"] - gamma_true) < 0.05
    assert abs(out["eta"] - eta_true) < 0.05
