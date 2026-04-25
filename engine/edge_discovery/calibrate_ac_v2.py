"""v2 Almgren–Chriss parent-order calibrator CLI and core function.

This is a minimal implementation that wires schema -> features -> inference.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd

from engine.edge_discovery import schema as sc
from engine.edge_discovery import features as ft
from engine.edge_discovery import inference as inf
from engine.edge_discovery import stationarity as stn


def calibrate_ac_v2(
    input_path: str,
    output_path: Optional[str] = None,
    model: str = "normalized",
    cluster_by: Optional[str] = "parent_id",
    bootstrap: str = "cluster",
    n_bootstrap: int = 200,
    hc: str = "hc3",
    post_lag_minutes: int = 60,
    strict: bool = True,
    buy_sell_separate: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    df = pd.read_csv(input_path)

    # validate rows
    valid_rows = []
    dropped = 0
    for i, row in df.iterrows():
        try:
            sc.validate_parent_row(row.to_dict(), strict=strict)
            valid_rows.append(row)
        except Exception:
            dropped += 1

    if len(valid_rows) == 0:
        raise ValueError("no valid parent-order rows after validation")

    dfv = pd.DataFrame(valid_rows)
    df_feats = ft.build_features(dfv, strict=strict)

    used = df_feats[df_feats["_valid"]].copy()
    data_quality = {
        "rows_input": int(len(df)),
        "rows_used": int(len(used)),
        "rows_dropped": int(dropped + (len(df) - len(valid_rows))),
    }

    results: Dict[str, Any] = {
        "model": model,
        "n_orders": int(len(used)),
        "cluster_by": cluster_by,
        "post_lag_minutes": post_lag_minutes,
        "data_quality": data_quality,
    }

    def fit_target(dep: str, formula: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"formula": formula}
        try:
            if cluster_by and cluster_by in used.columns:
                cluster_series = used[cluster_by]
            else:
                cluster_series = None

            # Stationarity checks (ADF) and simple cointegration handling for 1-regressor cases
            stationarity = {}
            differenced = False
            used_for_fit = used
            try:
                dep_series = used[dep] if dep in used.columns else None
            except Exception:
                dep_series = None
            # parse regressors from formula RHS (naive split on '+' — acceptable for current formulas)
            try:
                rhs = formula.split('~', 1)[1]
                regs = [r.strip() for r in rhs.replace('+', ' ').split()]
                regs = [r for r in regs if r and r in used.columns]
            except Exception:
                regs = []

            adf_results = {}
            # run ADF on dependent and regressors
            if dep_series is not None:
                try:
                    adf_results[dep] = stn.adf_test(dep_series)
                except Exception:
                    adf_results[dep] = {"error": "adf_failed"}
            for r in regs:
                try:
                    adf_results[r] = stn.adf_test(used[r])
                except Exception:
                    adf_results[r] = {"error": "adf_failed"}

            # decide whether to difference: simple rule
            nonstat = [k for k, v in adf_results.items() if isinstance(v, dict) and not v.get('stationary', False)]
            if len(nonstat) > 0:
                # if exactly one regressor and both nonstationary, test cointegration
                if len(regs) == 1 and dep in nonstat and regs[0] in nonstat and dep_series is not None:
                    try:
                        ctest = stn.cointegration_test(dep_series, used[regs[0]])
                        stationarity['cointegration'] = ctest
                        if not ctest.get('cointegrated', False):
                            used_for_fit = used.diff().dropna()
                            differenced = True
                    except Exception:
                        used_for_fit = used.diff().dropna()
                        differenced = True
                else:
                    # difference only numeric columns to avoid errors on string columns (parent_id etc.)
                    num_cols = used.select_dtypes(include=["number"]).columns.tolist()
                    if not num_cols:
                        # nothing numeric to difference; fall back to original
                        used_for_fit = used
                        differenced = False
                    else:
                        df_num = used[num_cols].diff().dropna()
                        # preserve cluster_by if present as-is
                        if cluster_by and cluster_by in used.columns:
                            used_for_fit = pd.concat([used[[cluster_by]].iloc[df_num.index], df_num], axis=1)
                        else:
                            used_for_fit = df_num
                        differenced = True

            stationarity['adf'] = adf_results
            stationarity['differenced'] = differenced

            inf_res = inf.fit_ols_with_se(formula=formula, df=used_for_fit, cluster=cluster_series if not differenced else (used_for_fit.get(cluster_by) if cluster_by in used_for_fit.columns else None), hc=hc)

            # params -> dict
            params = inf_res.get("params")
            if params is None:
                out["coef"] = {}
            else:
                try:
                    # pandas Series or similar
                    out["coef"] = {k: float(v) for k, v in dict(params).items()}
                except Exception:
                    # fallback: try iteration or zipping with index
                    try:
                        out["coef"] = {k: float(v) for k, v in params.items()}
                    except Exception:
                        idx = list(getattr(params, "index", []))
                        out["coef"] = {k: float(v) for k, v in zip(idx, list(params))}

            # robust HC standard errors: handle Series, ndarray, or None
            se_hc = inf_res.get("se_hc")
            if se_hc is None:
                out["se_hc"] = None
            else:
                try:
                    # prefer dict-like conversion
                    out["se_hc"] = {k: float(v) for k, v in dict(se_hc).items()}
                except Exception:
                    # zip with param names as a fallback
                    param_names = list(getattr(params, "index", None) or list(out["coef"].keys()))
                    out["se_hc"] = {k: float(v) for k, v in zip(param_names, list(se_hc))}

            out["r2"] = float(inf_res.get("r2", 0.0))

            # attach stationarity summary
            out["stationarity"] = stationarity

            # cluster bootstrap CI: guard small-sample situations
            if bootstrap == "cluster" and cluster_series is not None:
                try:
                    model_obj = inf_res.get("model")
                    df_resid = getattr(model_obj, "df_resid", None)
                    if df_resid is not None and df_resid <= 0:
                        out["ci_cluster_boot"] = None
                        out.setdefault("warnings", []).append("too few residual degrees of freedom for bootstrap/robust SE")
                    else:
                        ci = inf.cluster_bootstrap_ci(formula=formula, df=used, cluster_col=cluster_by, n_bootstrap=n_bootstrap, rng_seed=0)
                        out["ci_cluster_boot"] = {k: list(v) for k, v in ci.items()} if ci else None
                except Exception:
                    out["ci_cluster_boot"] = None
            else:
                out["ci_cluster_boot"] = None
        except Exception as e:
            out["error"] = str(e)
        return out

    # choose formulas
    if model == "normalized":
        # require sigma_used
        # I_norm and J_norm
        realized_formula = "I_norm ~ sqrt_phi + phi + log_duration + participation"
        post_formula = "J_norm ~ sqrt_phi + phi + log_duration + participation"
    else:
        # ac_restricted mode: regress I on sigma*sqrt_phi and sigma*phi (we require sigma_used)
        used["sigma_sqrt_phi"] = used["sigma_used"] * used["sqrt_phi"]
        used["sigma_phi"] = used["sigma_used"] * used["phi"]
        realized_formula = "I ~ sigma_sqrt_phi + sigma_phi"
        post_formula = "J ~ sigma_phi"

    results["realized_model"] = fit_target("I_norm" if model == "normalized" else "I", realized_formula)
    results["post_model"] = fit_target("J_norm" if model == "normalized" else "J", post_formula)

    # write output
    if output_path:
        Path(output_path).write_text(json.dumps(results, indent=2))
    else:
        print(json.dumps(results, indent=2))

    return results


def _cli():
    parser = argparse.ArgumentParser(description="Calibrate AC v2 from parent-order CSV")
    parser.add_argument("input_csv")
    parser.add_argument("--output", "-o")
    parser.add_argument("--model", choices=["normalized", "ac_restricted"], default="normalized")
    parser.add_argument("--cluster-by", default="parent_id")
    parser.add_argument("--bootstrap", choices=["none", "cluster", "wild"], default="cluster")
    parser.add_argument("--n-bootstrap", type=int, default=200)
    parser.add_argument("--hc", choices=["hc2", "hc3"], default="hc3")
    parser.add_argument("--post-lag-minutes", type=int, default=60)
    parser.add_argument("--no-strict", dest="strict", action="store_false")
    parser.add_argument("--buy-sell-separate", action="store_true")
    args = parser.parse_args()

    calibrate_ac_v2(
        args.input_csv,
        output_path=args.output,
        model=args.model,
        cluster_by=args.cluster_by,
        bootstrap=args.bootstrap,
        n_bootstrap=args.n_bootstrap,
        hc=args.hc,
        post_lag_minutes=args.post_lag_minutes,
        strict=args.strict,
        buy_sell_separate=args.buy_sell_separate,
    )


if __name__ == "__main__":
    _cli()
