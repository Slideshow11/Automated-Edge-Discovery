"""Almgren–Chriss transaction-cost model.

Implements a simplified Almgren–Chriss cost model with:
- Temporary linear temporary impact (gamma parameter)
- Permanent impact (eta parameter)
- Volatility scaling (sigma)
- Participation rate scaling (V)

Default parameters are approximate and should be calibrated to the specific
asset and market regime. This is a simplified/approximate implementation
suitable for prototype and backtesting; production use should consider
more sophisticated variants.

References:
- Almgren & Chriss (2001), "Optimal Execution of Portfolio Transactions"
"""
from __future__ import annotations

from typing import Optional, Dict, Any

# Lazy import to avoid hard dependency when not used
_pd = None

def _get_pd():
    global _pd
    if _pd is None:
        import pandas as pd
        _pd = pd
    return _pd

# Default parameters (approximate, calibrate for production)
DEFAULT_PARAMS = {
    "gamma": 0.5,   # temporary impact coefficient
    "eta": 0.1,     # permanent impact coefficient
    "sigma": 0.02,  # daily volatility (2%)
    "V": 0.1,       # participation rate (10% of ADV)
}


def apply_costs(trades_df, params: Optional[Dict[str, float]] = None):
    """Apply Almgren–Chriss transaction costs to a trades DataFrame.

    Parameters
    ----------
    trades_df : pd.DataFrame
        DataFrame with at least the following columns:
        - ``trade_value`` : float, dollar value of the trade (or position size)
        - ``price`` : float, execution price (optional, for permanent impact calc)
        If a ``size`` column is present instead of ``trade_value``, we attempt
        to infer trade_value as |size| * price.
    params : dict, optional
        Override for default Almgren–Chriss parameters:
        - ``gamma`` (default 0.5): temporary impact coefficient
        - ``eta`` (default 0.1): permanent impact coefficient
        - ``sigma`` (default 0.02): daily volatility
        - ``V`` (default 0.1): participation rate

    Returns
    -------
    pd.DataFrame
        Copy of ``trades_df`` with additional columns:
        - ``temp_impact``: temporary impact cost (negative = cost)
        - ``perm_impact``: permanent impact cost (negative = cost)
        - ``total_cost``: combined impact cost
        - ``net_proceeds``: trade_value - total_cost (sign-aware)
    """
    pd = _get_pd()
    p = {**DEFAULT_PARAMS, **(params or {})}
    gamma = p["gamma"]
    eta = p["eta"]
    sigma = p["sigma"]
    V = p["V"]

    df = trades_df.copy()

    # Derive trade_value if not present
    if "trade_value" not in df.columns:
        if "size" in df.columns and "price" in df.columns:
            df["trade_value"] = df["size"].abs() * df["price"]
        else:
            raise ValueError("trades_df must have 'trade_value' column, or both 'size' and 'price'")

    # Temporary impact: gamma * V * sigma * |trade_fraction| / sqrt(V)
    # Approximated as gamma * sigma * sqrt(|trade_value|) for a given V
    # This captures the idea that impact grows with sqrt of trade size.
    df["temp_impact"] = -gamma * sigma * (df["trade_value"].abs() ** 0.5)

    # Permanent impact: eta * sigma * |trade_fraction|
    # Linear in trade size relative to participation rate
    df["perm_impact"] = -eta * sigma * df["trade_value"].abs() / V

    # Total cost
    df["total_cost"] = df["temp_impact"] + df["perm_impact"]

    # Net proceeds (sign-aware: reduce proceeds by cost)
    sign = df["trade_value"].apply(lambda x: 1 if x >= 0 else -1)
    df["net_proceeds"] = df["trade_value"] + df["total_cost"] * sign

    return df
