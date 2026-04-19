"""Example script demonstrating calibrate_costs end-to-end.

Usage:
    python examples/calibrate_demo.py

Produces:
 - examples/calibrate_params_demo.json  (fitted params + CIs)
 - examples/calibrate_bootstrap.png     (bootstrap histograms, if matplotlib installed)

This script is intentionally self-contained and uses the same preprocessing
logic as engine.edge_discovery.calibrate_costs so it remains a simple demo
that works from the repository root without an editable install.
"""
from pathlib import Path
import sys
import json

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

try:
    from engine.edge_discovery import calibrate_costs as cc
except Exception as e:
    print("Failed to import calibrator module:", e)
    raise

EX_CSV = REPO_ROOT / "examples" / "trades.csv"
OUT_JSON = REPO_ROOT / "examples" / "calibrate_params_demo.json"
OUT_PNG = REPO_ROOT / "examples" / "calibrate_bootstrap.png"

N_BOOT = 500
ALPHA = 0.05

print("Reading example CSV:", EX_CSV)
rows = cc._read_csv(str(EX_CSV))
print(f"Loaded {len(rows)} rows")

sizes, prices, advs, costs = cc._construct_obs(rows)
if len(sizes) == 0:
    raise SystemExit("No valid observations in example CSV")

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

print("Running calibrator (OLS + optional bootstrap)")
res = cc.calibrate_from_csv(str(EX_CSV), robust=False, bootstrap=True, n_bootstrap=N_BOOT, alpha=ALPHA)

# save params JSON
OUT_JSON.write_text(json.dumps(res, indent=2))
print("Wrote params to", OUT_JSON)

# compute bootstrap samples for visualization
n = X_with_int.shape[0]
rng = np.random.default_rng(seed=0)
coefs = np.zeros((N_BOOT, 3), dtype=float)
for i in range(N_BOOT):
    idx = rng.integers(0, n, size=n)
    Xb = X_with_int[idx, :]
    yb = y[idx]
    try:
        beta, *_ = np.linalg.lstsq(Xb, yb, rcond=None)
        coefs[i, :] = beta
    except Exception:
        coefs[i, :] = np.nan

coefs = coefs[~np.isnan(coefs).any(axis=1)]
if coefs.shape[0] == 0:
    print("Bootstrap failed to produce any samples")
else:
    gamma_samples = coefs[:, 1]
    eta_samples = coefs[:, 2]

    # summarize
    def pct(v, q):
        return float(np.percentile(v, q))

    print("Gamma: mean=%.6f, 2.5=%.6f, 97.5=%.6f" % (gamma_samples.mean(), pct(gamma_samples, 2.5), pct(gamma_samples, 97.5)))
    print("Eta:   mean=%.6f, 2.5=%.6f, 97.5=%.6f" % (eta_samples.mean(), pct(eta_samples, 2.5), pct(eta_samples, 97.5)))

    # try plotting if matplotlib available
    try:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        axes[0].hist(gamma_samples, bins=40, color="#1f77b4", alpha=0.8)
        axes[0].axvline(res.get("gamma"), color="k", linestyle="--", label="OLS")
        if res.get("gamma_boot_ci"):
            lo, hi = res["gamma_boot_ci"]
            axes[0].axvline(lo, color="r", linestyle=":")
            axes[0].axvline(hi, color="r", linestyle=":", label="bootstrap 95%")
        axes[0].set_title("Gamma (temporary impact)")
        axes[0].legend()

        axes[1].hist(eta_samples, bins=40, color="#ff7f0e", alpha=0.8)
        axes[1].axvline(res.get("eta"), color="k", linestyle="--", label="OLS")
        if res.get("eta_boot_ci"):
            lo, hi = res["eta_boot_ci"]
            axes[1].axvline(lo, color="r", linestyle=":")
            axes[1].axvline(hi, color="r", linestyle=":", label="bootstrap 95%")
        axes[1].set_title("Eta (permanent impact)")
        axes[1].legend()

        fig.suptitle("Bootstrap distributions (n_boot=%d)" % N_BOOT)
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        fig.savefig(OUT_PNG)
        print("Saved bootstrap histogram to", OUT_PNG)
    except Exception as e:
        print("matplotlib not available or plotting failed:", e)
        print("Bootstrap summary printed above")

print("Done.")
