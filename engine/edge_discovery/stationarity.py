from __future__ import annotations
from typing import Dict, Any, Tuple, Optional

import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.stattools import adfuller, coint
except Exception:  # pragma: no cover - statsmodels required for these tests
    adfuller = None
    coint = None


def adf_test(series: pd.Series, maxlag: Optional[int] = None) -> Dict[str, Any]:
    """Run Augmented Dickey-Fuller test and return structured result.

    Returns dict with keys: stat, pvalue, usedlag, nobs, crit (dict), stationary (bool)
    """
    if adfuller is None:
        raise RuntimeError("statsmodels is required for ADF test")
    res = adfuller(series.dropna(), maxlag=maxlag, autolag='AIC')
    stat, pvalue, usedlag, nobs = res[0], res[1], res[2], res[3]
    crit = res[4]
    return {
        "stat": float(stat),
        "pvalue": float(pvalue),
        "usedlag": int(usedlag),
        "nobs": int(nobs),
        "crit": {k: float(v) for k, v in crit.items()},
        "stationary": bool(pvalue < 0.05),
    }


def cointegration_test(x: pd.Series, y: pd.Series) -> Dict[str, Any]:
    """Run Engle-Granger two-step cointegration test (statsmodels.tsa.stattools.coint).

    Returns dict with keys: t_stat, pvalue, crit, cointegrated (bool)
    """
    if coint is None:
        raise RuntimeError("statsmodels is required for cointegration test")
    t_stat, pvalue, crit = coint(x.dropna(), y.dropna())
    return {
        "t_stat": float(t_stat),
        "pvalue": float(pvalue),
        "crit": {"90": float(crit[0]), "95": float(crit[1]), "99": float(crit[2])},
        "cointegrated": bool(pvalue < 0.05),
    }
