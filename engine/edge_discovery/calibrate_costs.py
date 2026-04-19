"""Calibration routines for Almgren–Chriss cost parameters.

This module implements a regression-based estimator for the Almgren–Chriss
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

The calibrator provides both OLS estimates and an optional bootstrap-based
confidence interval for small-sample robustness. If scikit-learn is installed
and --robust is passed to the CLI, HuberRegressor is used to compute robust
coefficients (returned alongside OLS estimates).

CLI:
- calibrate_costs.py input.csv --output params.json --bootstrap --n-bootstrap 500

Notes:
- If the CSV includes a "cost" column, that is used as y. Otherwise, a
  simple per-trade proxy: sign(size) * (price - prev_price) is used.
- Bootstrap uses resampling of rows with replacement and recomputes OLS.
  Use n_bootstrap small in unit tests (e.g., 200) and larger in real runs.

"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import math
import statistics

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy required
    raise


@dataclass
class CalibrateResult:
    gamma: float
    eta: float
    gamma_se: float
    eta_se: float
    gamma_ci: Tuple[float, float]
    eta_ci: Tuple[float, float]
    r2: float
    sigma_price: float
    adv_scale: float
    gamma_boot_ci: Optional[Tuple[float, float]] = None
    eta_boot_ci: Optional[Tuple[float, float]] = None
    gamma_robust: Optional[float] = None
    eta_robust: Optional[float] = None

    def to_dict(self) -> Dict:
        out = {
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
        if self.gamma_boot_ci is not None:
            out["gamma_boot_ci"] = [self.gamma_boot_ci[0], self.gamma_boot_ci[1]]
        if self.eta_boot_ci is not None:
            out["eta_boot_ci"] = [self.eta_boot_ci[0], self.eta_boot_ci[1]]
        if self.gamma_robust is not None:
            out["gamma_robust"] = self.gamma_robust
        if self.eta_robust is not None:
            out["eta_robust"] = self.eta_robust
        return out


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
                prev_price = price
                continue

        sizes.append(size)
        prices.append(price if price is not None else prev_price if prev_price is not None else 0.0)
        advs.append(adv)
        costs.append(cost)

        prev_price = price

    return sizes, prices, advs, costs


def _ols_estimate(X_with_int: np.ndarray, y: np.ndarray):
    # OLS via lstsq
    beta, *_ = np.linalg.lstsq(X_with_int, y, rcond=None)
    intercept = float(beta[0])
    gamma = float(beta[1])
    eta = float(beta[2])

    # residuals
    y_pred = X_with_int @ beta
    resid = y - y_pred
    dof = max(1, len(y) - X_with_int.shape[1])
    sigma2 = float((resid ** 2).sum() / dof)

    xtx = X_with_int.T @ X_with_int
    xtx_inv = np.linalg.pinv(xtx)
    var_beta = sigma2 * xtx_inv
    se = np.sqrt(np.maximum(np.diag(var_beta), 0.0))

    intercept_se = float(se[0])
    gamma_se = float(se[1])
    eta_se = float(se[2])

    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {
        "intercept": intercept,
        "gamma": gamma,
        "eta": eta,
        "intercept_se": intercept_se,
        "gamma_se": gamma_se,
        "eta_se": eta_se,
        "r2": r2,
        "resid": resid,
        "sigma2": sigma2,
    }


def _bootstrap_ci(X_with_int: np.ndarray, y: np.ndarray, n_bootstrap: int = 200, alpha: float = 0.05) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Compute bootstrap percentile CIs for gamma and eta by resampling rows.

    Fast, simple implementation using numpy. n_bootstrap should be kept small in tests.
    Returns (gamma_ci, eta_ci).
    """
    n = X_with_int.shape[0]
    coefs = np.zeros((n_bootstrap, 3), dtype=float)
    rng = np.random.default_rng(seed=0)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        Xb = X_with_int[idx, :]
        yb = y[idx]
        try:
            beta, *_ = np.linalg.lstsq(Xb, yb, rcond=None)
            coefs[i, :] = beta
        except Exception:
            coefs[i, :] = np.nan

    # remove any nan rows
    coefs = coefs[~np.isnan(coefs).any(axis=1)]
    if coefs.shape[0] == 0:
        return (None, None), (None, None)

    # percentiles for gamma (index 1) and eta (index 2)
    lower = 100.0 * (alpha / 2.0)
    upper = 100.0 * (1.0 - alpha / 2.0)
    gamma_ci = (float(np.percentile(coefs[:, 1], lower)), float(np.percentile(coefs[:, 1], upper)))
    eta_ci = (float(np.percentile(coefs[:, 2], lower)), float(np.percentile(coefs[:, 2], upper)))
    return gamma_ci, eta_ci


def calibrate_from_csv(path: str, robust: bool = False, alpha: float = 0.05, bootstrap: bool = False, n_bootstrap: int = 200) -> Dict:
    """Calibrate Almgren–Chriss impact parameters from a CSV file.

    Parameters
    - path: path to CSV with columns at least 'size' and either 'cost' or 'price'.
    - robust: if True and scikit-learn is installed, compute HuberRegressor robust
      coefficient estimates (returned as gamma_robust, eta_robust).
    - alpha: significance level for confidence intervals (default 0.05 for 95%% CI)
    - bootstrap: if True compute bootstrap percentile confidence intervals (n_bootstrap)
    - n_bootstrap: number of bootstrap resamples (default 200; increase for real runs)

    Returns a dictionary with keys:
    - gamma, eta: OLS coefficient estimates
    - gamma_se, eta_se: standard errors (OLS)
    - gamma_ci, eta_ci: approximate normal-theory confidence intervals (OLS)
    - gamma_boot_ci, eta_boot_ci: optional bootstrap percentile CIs
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

    mean_adv = float(np.mean(advs[advs > 0]) if np.any(advs > 0) else 1.0)
    advs = np.where(advs <= 0, mean_adv, advs)

    x1 = np.sqrt(np.abs(sizes) / advs) * np.sign(sizes)
    x2 = sizes / advs
    X = np.column_stack([x1, x2])
    y = costs

    X_with_int = np.column_stack([np.ones(len(X)), X])

    ols = _ols_estimate(X_with_int, y)

    gamma = float(ols["gamma"])
    eta = float(ols["eta"])
    gamma_se = float(ols["gamma_se"])
    eta_se = float(ols["eta_se"])
    r2 = float(ols["r2"])

    # normal approx CIs
    z = 1.96
    gamma_ci = (gamma - z * gamma_se, gamma + z * gamma_se)
    eta_ci = (eta - z * eta_se, eta + z * eta_se)

    # bootstrap CI
    gamma_boot_ci = None
    eta_boot_ci = None
    if bootstrap:
        gamma_boot_ci, eta_boot_ci = _bootstrap_ci(X_with_int, y, n_bootstrap=n_bootstrap, alpha=alpha)

    # volatility / adv scale proxies
    price_diffs = np.diff(np.asarray([p for p in prices if p is not None], dtype=float))
    sigma_price = float(np.std(price_diffs)) if price_diffs.size > 0 else 0.0
    adv_scale = float(np.mean(advs) / (np.mean(np.abs(sizes)) + 1e-12))

    out = CalibrateResult(
        gamma=gamma,
        eta=eta,
        gamma_se=gamma_se,
        eta_se=eta_se,
        gamma_ci=gamma_ci,
        eta_ci=eta_ci,
        r2=r2,
        sigma_price=sigma_price,
        adv_scale=adv_scale,
        gamma_boot_ci=gamma_boot_ci,
        eta_boot_ci=eta_boot_ci,
    )

    res = out.to_dict()

    if robust:
        try:
            from sklearn.linear_model import HuberRegressor

            huber = HuberRegressor().fit(X, y)
            res["gamma_robust"] = float(huber.coef_[0])
            res["eta_robust"] = float(huber.coef_[1])
        except Exception:
            res["gamma_robust"] = None
            res["eta_robust"] = None

    return res


def _cli():
    parser = argparse.ArgumentParser(description="Calibrate Almgren-Chriss params from trade CSV")
    parser.add_argument("input_csv", help="Path to CSV file with trades")
    parser.add_argument("--output", "-o", help="Output JSON file (default stdout)")
    parser.add_argument("--robust", action="store_true", help="Compute robust estimates if sklearn is available")
    parser.add_argument("--bootstrap", action="store_true", help="Compute bootstrap percentile CIs")
    parser.add_argument("--n-bootstrap", type=int, default=200, help="Number of bootstrap resamples (default 200)")
    args = parser.parse_args()

    params = calibrate_from_csv(args.input_csv, robust=args.robust, bootstrap=args.bootstrap, n_bootstrap=args.n_bootstrap)
    out = json.dumps(params, indent=2)
    if args.output:
        Path(args.output).write_text(out)
    else:
        print(out)


if __name__ == "__main__":
    _cli()
