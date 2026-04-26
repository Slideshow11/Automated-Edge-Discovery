"""Feature construction for parent-order calibrator.

Produces phi, sqrt_phi, duration, I/J targets and normalized versions.
"""
from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd


def build_features(df: pd.DataFrame, strict: bool = True) -> pd.DataFrame:
    """Given a DataFrame with parent-order columns, return features DataFrame.

    Expected columns: arrival_mid, vwap_exec, post_mid, signed_qty, adv, sigma (optional), participation (optional), arrival_ts, end_ts
    """
    df = df.copy()

    req = ["arrival_mid", "vwap_exec", "post_mid", "signed_qty", "adv", "arrival_ts", "end_ts"]
    missing = [c for c in req if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns for features: {missing}")

    # numeric conversions
    df["arrival_mid"] = df["arrival_mid"].astype(float)
    df["vwap_exec"] = df["vwap_exec"].astype(float)
    df["post_mid"] = df["post_mid"].astype(float)
    df["signed_qty"] = df["signed_qty"].astype(float)
    df["adv"] = df["adv"].astype(float)

    # q and side
    df["q"] = df["signed_qty"].abs()
    df["side"] = np.sign(df["signed_qty"]).replace(0, 1)

    # phi and sqrt_phi
    # avoid division by zero -> mark invalid
    df["phi"] = df["q"] / df["adv"].replace({0: np.nan})
    df["sqrt_phi"] = np.sqrt(np.abs(df["phi"])) * np.sign(df["signed_qty"]) * 1.0

    # duration minutes
    df["arrival_ts"] = pd.to_datetime(df["arrival_ts"])
    df["end_ts"] = pd.to_datetime(df["end_ts"])
    df["duration_minutes"] = (df["end_ts"] - df["arrival_ts"]).dt.total_seconds() / 60.0
    df["log_duration"] = np.log1p(df["duration_minutes"].clip(lower=0.0))

    # participation if present
    if "participation" in df.columns:
        df["participation"] = pd.to_numeric(df["participation"], errors="coerce")
    else:
        df["participation"] = np.nan

    # targets
    df["I"] = df["side"] * (df["vwap_exec"] - df["arrival_mid"])
    df["J"] = df["side"] * (df["post_mid"] - df["arrival_mid"])

    # sigma
    if "sigma" in df.columns:
        df["sigma_used"] = pd.to_numeric(df["sigma"], errors="coerce")
    else:
        df["sigma_used"] = np.nan

    # normalized targets
    df["I_norm"] = df["I"] / df["sigma_used"]
    df["J_norm"] = df["J"] / df["sigma_used"]

    # data quality
    df["_valid"] = True
    # invalid if adv <= 0 or sigma_used is nan (strict mode)
    df.loc[df["adv"] <= 0, "_valid"] = False
    if strict:
        df.loc[df["sigma_used"].isna(), "_valid"] = False

    # drop invalid rows but keep counts upstream
    return df
