"""Diagnostic utilities for calibrator v2.

Produces diagnostics including Cook's distance, leverage, residual plots, a lag-sensitivity grid,
and writes a CSV of top influential parent orders.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt
    HAS_MPL = True
except Exception:
    HAS_MPL = False

import statsmodels.formula.api as smf
import statsmodels.api as sm


def diagnostic_report(df: pd.DataFrame, results: Dict[str, Any], out_dir: str | Path, lag_fields: List[int] | None = None, top_n: int = 20) -> Dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {}

    # Keep a DataFrame of influence metrics for later CSV output (must contain parent_id if present)
    influence_rows = []

    for key in ("realized_model", "post_model"):
        info = results.get(key, {})
        formula = info.get("formula")
        if formula is None:
            summary[key] = {"error": "no formula provided"}
            continue

        try:
            model = smf.ols(formula=formula, data=df).fit()
            resid = model.resid
            fitted = model.fittedvalues
            coef = model.params.to_dict()
            summary[key] = {
                "coef": {k: float(v) for k, v in coef.items()},
                "r2": float(model.rsquared),
                "nobs": int(model.nobs),
            }

            # influence measures
            infl = model.get_influence()
            cooks = infl.cooks_distance[0]
            leverage = infl.hat_matrix_diag
            std_resid = infl.resid_studentized_internal
            dffits = infl.dffits[0]

            summary[key]["cooks_max"] = float(np.nanmax(cooks))
            summary[key]["leverage_max"] = float(np.nanmax(leverage))

            # collect per-row influence metrics
            for i in range(len(df)):
                row = {"index": int(i), "model": key, "cooks": float(cooks[i]), "leverage": float(leverage[i]), "std_resid": float(std_resid[i]), "dffits": float(dffits[i])}
                # copy parent_id if available
                if "parent_id" in df.columns:
                    row["parent_id"] = str(df.iloc[i].get("parent_id", ""))
                influence_rows.append(row)

            if HAS_MPL:
                fig, axes = plt.subplots(2, 2, figsize=(12, 9))
                axes = axes.ravel()
                axes[0].scatter(fitted, resid, s=10, alpha=0.6)
                axes[0].axhline(0, color="k", linestyle="--")
                axes[0].set_xlabel("fitted")
                axes[0].set_ylabel("residual")
                axes[0].set_title(f"{key}: residual vs fitted")

                if "phi" in df.columns:
                    axes[1].scatter(df["phi"].fillna(0), resid, s=10, alpha=0.6)
                    axes[1].set_xlabel("phi")
                    axes[1].set_ylabel("residual")
                    axes[1].set_title(f"{key}: residual vs phi")
                else:
                    axes[1].text(0.5, 0.5, "phi missing", ha="center")
                    axes[1].set_axis_off()

                axes[2].hist(resid, bins=40, color="#6baed6", alpha=0.8)
                axes[2].set_title("residual histogram")

                axes[3].scatter(leverage, cooks, s=20, alpha=0.7)
                axes[3].set_xlabel("leverage")
                axes[3].set_ylabel("Cooks distance")
                axes[3].set_title("leverage vs cooks")

                fig.tight_layout()
                png = out / f"{key}_diagnostics.png"
                fig.savefig(png)
                plt.close(fig)

        except Exception as e:
            summary[key] = {"error": str(e)}

    # produce top-influencers CSV (combined across models)
    if influence_rows:
        inf_df = pd.DataFrame(influence_rows)
        # rank by cooks distance descending
        top = inf_df.sort_values("cooks", ascending=False).head(top_n)
        csv_path = out / "top_influential_orders.csv"
        top.to_csv(csv_path, index=False)
        # include in summary
        summary["top_influential_csv"] = str(csv_path)
        # also include a small JSON-friendly list
        summary["top_influential_sample"] = top.to_dict(orient="records")
    else:
        summary["top_influential_csv"] = None
        summary["top_influential_sample"] = []

    # lag-sensitivity if requested: expects df to contain multiple post_mid variants or post_ts variants
    if lag_fields:
        lag_summary = {}
        for lag in lag_fields:
            # expected column naming convention: post_mid_<lag> (minutes)
            col = f"post_mid_{lag}"
            if col not in df.columns:
                continue
            df_l = df.copy()
            df_l["post_mid"] = df_l[col]
            # recompute features if needed: assume features.build_features will be used externally
            try:
                m = smf.ols(formula=results.get("post_model", {}).get("formula", "J_norm ~ sqrt_phi + phi"), data=df_l).fit()
                lag_summary[str(lag)] = {"coef": {k: float(v) for k, v in m.params.items()}, "r2": float(m.rsquared)}
            except Exception as e:
                lag_summary[str(lag)] = {"error": str(e)}
        summary["lag_sensitivity"] = lag_summary

    (out / "diagnostics_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
