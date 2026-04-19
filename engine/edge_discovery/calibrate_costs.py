"""Calibration helper for Almgren013 cost parameters.

This module provides a simple CLI and programmatic function to "calibrate"
placeholder Almgren013 parameters from a CSV of trades. The implementation
is intentionally minimal and deterministic for unit tests.

CSV format expected (header row):
- timestamp (optional)
- size (signed, positive for buy, negative for sell)
- price

Example:
    timestamp,size,price
    2021-01-01T09:32:00,10,100.0

The calibration here returns a small dictionary of parameters: temporary and
permanent impact coefficients and an estimate of volatility. This is a
placeholder for a more sophisticated calibration routine.

"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict


def calibrate_from_csv(path: str) -> Dict[str, float]:
    """Read a CSV of trades and return simple calibrated params.

    The routine computes:
    - avg_trade_value: mean(|size * price|)
    - temp_impact_coeff: 1e-4 * avg_trade_value (placeholder)
    - perm_impact_coeff: 1e-5 * avg_trade_value (placeholder)
    - volatility_est: stddev of price returns (simple proxy)

    Returns a dict of floats.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    sizes = []
    prices = []
    with p.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            # allow either 'size' or 'trade_value' columns
            size = None
            price = None
            if "size" in row and row["size"]:
                size = float(row["size"])
            if "price" in row and row["price"]:
                price = float(row["price"])
            if size is None and "trade_value" in row and row["trade_value"]:
                # derive size using price if available
                price = float(row.get("price", 1.0))
                size = float(row["trade_value"]) / price
            if size is None or price is None:
                continue
            sizes.append(size)
            prices.append(price)

    if not sizes:
        raise ValueError("No valid trades found in CSV")

    import math

    trade_values = [abs(s * p) for s, p in zip(sizes, prices)]
    avg_trade_value = sum(trade_values) / len(trade_values)

    # Placeholder heuristic parameters
    temp_impact_coeff = 1e-4 * avg_trade_value
    perm_impact_coeff = 1e-5 * avg_trade_value

    # Simple volatility proxy (sample stddev of prices)
    mean_price = sum(prices) / len(prices)
    var = sum((x - mean_price) ** 2 for x in prices) / max(1, len(prices) - 1)
    volatility_est = math.sqrt(var)

    return {
        "avg_trade_value": avg_trade_value,
        "temp_impact_coeff": temp_impact_coeff,
        "perm_impact_coeff": perm_impact_coeff,
        "volatility_est": volatility_est,
    }


def _cli():
    parser = argparse.ArgumentParser(description="Calibrate Almgren-Chriss params from trade CSV")
    parser.add_argument("input_csv", help="Path to CSV file with trades")
    parser.add_argument("--output", "-o", help="Output JSON file (default stdout)")
    args = parser.parse_args()

    params = calibrate_from_csv(args.input_csv)
    out = json.dumps(params, indent=2)
    if args.output:
        Path(args.output).write_text(out)
    else:
        print(out)


if __name__ == "__main__":
    _cli()
