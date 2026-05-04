"""Inference helpers: OLS with HC and cluster robust SEs, cluster bootstrap and wild-cluster bootstrap.

This module wraps statsmodels to provide consistent outputs for the calibrator.

Changes in this file:
- Normalize outputs so params, se_hc, se_cluster are pandas.Series when available.
- Suppress internal RuntimeWarnings from statsmodels during fitting (small-sample degenerate cases).
- Provide helper to coerce array-like objects into Series aligned with parameter names.
"""
from __future__ import annotations
from typing import Optional, Dict, Tuple

import warnings
import numpy as np
import pandas as pd

try:
    import statsmodels.formula.api as smf
    import statsmodels.api as sm
except Exception:  # pragma: no cover - import will fail if not installed
    smf = None
    sm = None


def _to_series_like(x, index):
    """Convert array-like or scalar to pandas.Series aligned to index if possible."""
    if x is None:
        return None
    # If already a pandas Series, ensure index alignment
    if isinstance(x, pd.Series):
        try:
            return x.reindex(index)
        except Exception:
            return pd.Series(list(x), index=index)
    # If numpy array or list-like
    try:
        arr = np.asarray(x)
        return pd.Series(arr, index=index)
    except Exception:
        # fallback: attempt to iterate
        try:
            return pd.Series(list(x), index=index)
        except Exception:
            return None


def fit_ols_with_se(formula: str, df: pd.DataFrame, cluster: Optional[pd.Series] = None, hc: str = "hc3") -> Dict:
    if smf is None:
        raise RuntimeError("statsmodels is required for inference")

    # statsmodels can emit RuntimeWarnings for degenerate/small samples; suppress here
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        model = smf.ols(formula=formula, data=df).fit()

    results = {}

    # HC standard errors
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            robust = model.get_robustcov_results(cov_type=hc)
            se_hc = robust.bse
    except Exception:
        se_hc = model.bse

    params = model.params
    # normalize to pandas.Series keyed by parameter names
    se_hc_s = _to_series_like(se_hc, index=params.index)

    results["params"] = params
    results["se_hc"] = se_hc_s
    results["r2"] = float(model.rsquared)
    results["model"] = model

    if cluster is not None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                cov_cluster = model.get_robustcov_results(cov_type="cluster", groups=cluster)
                se_cluster = cov_cluster.bse
                cov_mat = cov_cluster.cov
            results["se_cluster"] = _to_series_like(se_cluster, index=params.index)
            # cov_cluster may be ndarray or DataFrame; normalize to DataFrame
            if isinstance(cov_mat, pd.DataFrame):
                results["cov_cluster"] = cov_mat
            else:
                try:
                    results["cov_cluster"] = pd.DataFrame(cov_mat, index=params.index, columns=params.index)
                except Exception:
                    results["cov_cluster"] = None
        except Exception:
            results["se_cluster"] = None
            results["cov_cluster"] = None
    else:
        results["se_cluster"] = None
        results["cov_cluster"] = None

    return results


def cluster_bootstrap_ci(formula: str, df: pd.DataFrame, cluster_col: str, n_bootstrap: int = 200, alpha: float = 0.05, rng_seed: Optional[int] = 0) -> Dict[str, Tuple[float, float]]:
    """Pairs cluster bootstrap: resample clusters with replacement and recompute OLS parameters.

    Returns dict param -> (lo, hi)
    """
    if smf is None:
        raise RuntimeError("statsmodels is required for inference")
    rng = np.random.default_rng(seed=rng_seed)
    clusters = df[cluster_col].unique()
    n_clusters = len(clusters)
    params = []
    n_successful = 0
    for i in range(n_bootstrap):
        sampled = rng.choice(clusters, size=n_clusters, replace=True)
        df_b = pd.concat([df[df[cluster_col] == c] for c in sampled], ignore_index=True)
        try:
            m_b = smf.ols(formula=formula, data=df_b).fit()
            params.append(m_b.params.values)
            n_successful += 1
        except Exception:
            pass  # drop this draw; do not append NaN row

    if n_successful == 0:
        raise ValueError(
            f"All {n_bootstrap} cluster bootstrap OLS fits failed. "
            f"Cannot compute confidence intervals with zero successful draws."
        )
    mat = np.array(params, dtype=float)

    lower = 100.0 * (alpha / 2.0)
    upper = 100.0 * (1.0 - alpha / 2.0)
    ci = {}
    # param names from a single fit on original data
    m0 = smf.ols(formula=formula, data=df).fit()
    names = list(m0.params.index)
    for idx, name in enumerate(names):
        lo = float(np.percentile(mat[:, idx], lower))
        hi = float(np.percentile(mat[:, idx], upper))
        ci[name] = (lo, hi)
    return ci


def wild_cluster_bootstrap_ci(formula: str, df: pd.DataFrame, cluster_col: str, n_bootstrap: int = 200, alpha: float = 0.05, rng_seed: Optional[int] = 0) -> Dict[str, Tuple[float, float]]:
    """Wild cluster bootstrap (Rademacher weights) approximation.

    Implementation notes:
    - Fit the original model, obtain residuals and the design matrix rows grouped by cluster.
    - For each bootstrap, flip the signs of cluster residual contributions with Rademacher variables (+1/-1), reconstruct dependent var: y_b = y_hat + W_c * (y - y_hat) for each cluster c.
    - Refit model on y_b and collect params.

    This is a pragmatic implementation suitable as a sensitivity check; for
    formal inference see econometrics references for wild cluster bootstrap variants
    (Cameron, Gelbach, and Miller 2008; Webb 2014).
    """
    if smf is None:
        raise RuntimeError("statsmodels is required for inference")
    rng = np.random.default_rng(seed=rng_seed)
    # fit original model
    model0 = smf.ols(formula=formula, data=df).fit()
    y = model0.model.endog
    y_hat = model0.fittedvalues.values
    resid = (y - y_hat)

    clusters = df[cluster_col].values
    unique_clusters = np.unique(clusters)
    n_clusters = unique_clusters.shape[0]

    # precompute indices for clusters
    cluster_idx = {c: np.where(clusters == c)[0] for c in unique_clusters}

    params = []
    names = list(model0.params.index)
    n_successful = 0

    for b in range(n_bootstrap):
        # Rademacher +/-1 per cluster
        signs = {c: (1 if rng.random() < 0.5 else -1) for c in unique_clusters}
        yb = y_hat.copy()
        # add signed cluster residuals
        for c in unique_clusters:
            idx = cluster_idx[c]
            yb[idx] += signs[c] * resid[idx]
        # create a copy of df with new dependent variable
        df_b = df.copy()
        # statsmodels formula expects the column name of dependent var; extract it
        dep = model0.model.endog_names
        df_b[dep] = yb
        try:
            m_b = smf.ols(formula=formula, data=df_b).fit()
            params.append(m_b.params.values)
            n_successful += 1
        except Exception:
            pass  # drop this draw; do not append NaN row

    if n_successful == 0:
        raise ValueError(
            f"All {n_bootstrap} wild cluster bootstrap OLS fits failed. "
            f"Cannot compute confidence intervals with zero successful draws."
        )
    mat = np.array(params, dtype=float)

    lower = 100.0 * (alpha / 2.0)
    upper = 100.0 * (1.0 - alpha / 2.0)
    ci = {}
    for idx, name in enumerate(names):
        lo = float(np.percentile(mat[:, idx], lower))
        hi = float(np.percentile(mat[:, idx], upper))
        ci[name] = (lo, hi)
    return ci
