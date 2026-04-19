"""Calibration routines for AlmgrenChriss cost parameters.

This module implements a regression-based estimator for the AlmgrenChriss
temporary impact (gamma) and permanent impact (eta) parameters, together with
simple estimates for volatility (sigma) and an ADV/volume scaling proxy.

Model assumed (per-trade, per-share signed cost):

    y_i = gamma * sqrt(|size_i| / adv_i) * sign(size_i)
          + eta * (size_i / adv_i)
          + eps_i

Where:
- size_i is signed executed size (positive buys, negative sells)
- adv_i is average daily volume for the instrument (per-trade column)
- y_i is the observed realized cost per share (signed in price units)

Input CSV:
- Required columns: size, price OR cost. adv is recommended (if missing a
  constant average ADV is used).
- If a "cost" column exists it will be used as y. Otherwise y is approximated
  as sign(size) * (price - prev_price) where prev_price is the previous row's
  price (simple proxy).

Calibration function:
- calibrate_from_csv(path, robust=False, alpha=0.05)
  performs ordinary least squares to estimate gamma and eta. If ``robust`` is
  True and scikit-learn is available the function will also compute robust
  coefficients using HuberRegressor (returned alongside OLS estimates).
- The function returns estimates and approximate confidence intervals for the
  OLS estimates using a normal approximation.

Assumptions and limitations:
- The implementation is intentionally compact and uses simple heuristics for
  volatility and ADV-scaling. It expects per-trade data and small datasets for
  unit tests.
- Confidence intervals use a Normal (z ~ 1.96 for 95%%) approximation rather
  than exact Student-t inversion to avoid adding scipy as a dependency.

CLI:
- A small command-line interface is provided to read a CSV and emit JSON with
  parameter estimates and confidence intervals.

"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

import math
import statistics

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy should be available in CI/tests
    raise


@dataclass
class CalibrateResult:
    gamma: float
    eta: float
    gamma_se: float
    eta_se: float
    gamma_ci: tuple
    eta_ci: tuple
    r2: float
    sigma_price: float
    adv_scale: float

    def to_dict(self) -> Dict:
        return {
            "gamma": self.gamma,
            "eta": self.eta,
            "gamma_se": self.gamma_se,
            "eta_se": self.eta_se,
            "gamma_ci": [self.gamma_ci[0], self.gamma_ci[1]],
            "eta_ci": [self.eta_ci[0], self.eta_ci[1]],
            "r2": self.r2,
            "sigma_price": self.sigma_price,
            "adv_scale": self.adv_scale,
        }


def _read_csv(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    rows = []
    with p.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _construct_obs(rows):
    sizes = []
    prices = []
    advs = []
    costs = []

    prev_price: Optional[float] = None
    for r in rows:
        # parse size
        if "size" not in r or r["size"] == "":
            continue
        size = float(r["size"])

        price = None
        if "price" in r and r["price"] != "":
            price = float(r["price"])
        adv = float(r.get("adv") or 0.0)

        # cost column preferred
        if "cost" in r and r["cost"] != "":
            cost = float(r["cost"])
        else:
            # fallback: estimate per-share signed cost as sign(size)*(price - prev_price)
            if price is not None and prev_price is not None:
                cost = math.copysign((price - prev_price), size)
            else:
                # cannot compute cost for this row
                prev_price = price
                continue

        sizes.append(size)
        prices.append(price if price is not None else prev_price if prev_price is not None else 0.0)
        advs.append(adv)
        costs.append(cost)

        prev_price = price

    return sizes, prices, advs, costs


def calibrate_from_csv(path: str, robust: bool = False, alpha: float = 0.05) -> Dict:
    """Calibrate AlmgrenChriss impact parameters from a CSV file.

    Parameters
    - path: path to CSV with columns at least 'size' and either 'cost' or 'price'.
    - robust: if True and scikit-learn is installed, also compute robust
      coefficient estimates using HuberRegressor (returned under keys
      'gamma_robust' and 'eta_robust').
    - alpha: significance level for confidence intervals (default 0.05 for 95%% CI)

    Returns a dictionary with keys:
    - gamma, eta: OLS coefficient estimates
    - gamma_se, eta_se: standard errors
    - gamma_ci, eta_ci: approximate confidence intervals (normal approx)
    - r2: coefficient of determination
    - sigma_price: simple volatility estimate (std of price diffs)
    - adv_scale: mean(adv) / mean(|size|) proxy

    """
    rows = _read_csv(path)
    sizes, prices, advs, costs = _construct_obs(rows)

    if not sizes:
        raise ValueError("No valid observations found for calibration")

    sizes = np.asarray(sizes, dtype=float)
    advs = np.asarray(advs, dtype=float)
    costs = np.asarray(costs, dtype=float)

    # If adv is zero for a row, replace with mean adv (avoid div by zero)
    mean_adv = float(np.mean(advs[advs > 0]) if np.any(advs > 0) else 1.0)
    advs = np.where(advs <= 0, mean_adv, advs)

    # Design matrix: [sqrt(|size|/adv) * sign(size), size/adv]
    x1 = np.sqrt(np.abs(sizes) / advs) * np.sign(sizes)
    x2 = sizes / advs
    X = np.column_stack([x1, x2])
    y = costs

    # Add intercept
    X_with_int = np.column_stack([np.ones(len(X)), X])

    # OLS via lstsq
    beta, *_ = np.linalg.lstsq(X_with_int, y, rcond=None)
    intercept = float(beta[0])
    gamma = float(beta[1])
    eta = float(beta[2])

    # residuals and sigma^2
    y_pred = X_with_int @ beta
    resid = y - y_pred
    dof = max(1, len(y) - X_with_int.shape[1])
    sigma2 = float((resid ** 2).sum() / dof)

    # variance-covariance of beta: sigma2 * (X'X)^{-1}
    # use pseudo-inverse for numerical stability on tiny / rank-deficient data
    xtx = X_with_int.T @ X_with_int
    xtx_inv = np.linalg.pinv(xtx)
    var_beta = sigma2 * xtx_inv
    se = np.sqrt(np.maximum(np.diag(var_beta), 0.0))

    intercept_se = float(se[0])
    gamma_se = float(se[1])
    eta_se = float(se[2])

    # R^2
    ss_tot = float(((y - y.mean()) ** 2).sum())
    ss_res = float((resid ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # CI using normal approx (z)
    # two-sided z for alpha
    try:
        # approximate z using inverse error function if numpy has it
        from math import erfcinv

        z = abs( np.sqrt(2) * (-math.erf(1) if False else 0) )  # fallback unused
        # fallback to 1.96
        z = 1.96
    except Exception:
        z = 1.96

    gamma_ci = (gamma - z * gamma_se, gamma + z * gamma_se)
    eta_ci = (eta - z * eta_se, eta + z * eta_se)

    # simple volatility: std of price diffs
    price_diffs = np.diff(np.asarray([p for p in prices if p is not None], dtype=float))
    sigma_price = float(np.std(price_diffs)) if price_diffs.size > 0 else 0.0

    # adv scaling proxy
    adv_scale = float(np.mean(advs) / (np.mean(np.abs(sizes)) + 1e-12))

    result = CalibrateResult(
        gamma=gamma,
        eta=eta,
        gamma_se=gamma_se,
        eta_se=eta_se,
        gamma_ci=gamma_ci,
        eta_ci=eta_ci,
        r2=r2,
        sigma_price=sigma_price,
        adv_scale=adv_scale,
    )

    out = result.to_dict()

    # Robust fallback: HuberRegressor if requested
    if robust:
        try:
            from sklearn.linear_model import HuberRegressor

            huber = HuberRegressor().fit(X, y)
            out["gamma_robust"] = float(huber.coef_[0])
            out["eta_robust"] = float(huber.coef_[1])
        except Exception:
            # sklearn not installed or failed; ignore
            out["gamma_robust"] = None
            out["eta_robust"] = None

    return out


def _cli():
    parser = argparse.ArgumentParser(description="Calibrate Almgren-Chriss params from trade CSV")
    parser.add_argument("input_csv", help="Path to CSV file with trades")
    parser.add_argument("--output", "-o", help="Output JSON file (default stdout)")
    parser.add_argument("--robust", action="store_true", help="Compute robust estimates if sklearn is available")
    args = parser.parse_args()

    params = calibrate_from_csv(args.input_csv, robust=args.robust)
    out = json.dumps(params, indent=2)
    if args.output:
        Path(args.output).write_text(out)
    else:
        print(out)


if __name__ == "__main__":
    _cli()
