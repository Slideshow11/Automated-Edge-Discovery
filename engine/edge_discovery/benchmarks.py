"""Benchmark utilities: VWAP, arrival/post mid helpers and timestamp checks.

These are intentionally small helpers used by the v2 calibrator. They do not
attempt to fetch market data; they operate on passed-in data structures.
"""
from __future__ import annotations
from typing import Iterable

import pandas as pd


def compute_vwap_exec(fill_rows: Iterable[dict]) -> float:
    """Compute VWAP from an iterable of fill dicts with keys 'price' and 'size'.

    Accepts list of dicts or a DataFrame-like input.
    """
    if hasattr(fill_rows, "to_dict") and hasattr(fill_rows, "values"):
        df = pd.DataFrame(fill_rows)
    else:
        df = pd.DataFrame(list(fill_rows))

    if df.empty:
        raise ValueError("no fills provided for vwap")

    if "price" not in df.columns or "size" not in df.columns:
        raise ValueError("fills must contain 'price' and 'size'")

    df = df.dropna(subset=["price", "size"])
    if df.empty:
        raise ValueError("no valid fills for vwap")

    # sizes may be signed; use absolute sizes for VWAP
    df["abs_size"] = df["size"].astype(float).abs()
    vwap = (df["price"].astype(float) * df["abs_size"]).sum() / df["abs_size"].sum()
    return float(vwap)


def compute_arrival_mid(snapshot_row: dict) -> float:
    """Compute arrival mid from a market snapshot dict. Accepts 'bid' and 'ask' or 'mid'."""
    if "mid" in snapshot_row and snapshot_row["mid"] not in (None, ""):
        return float(snapshot_row["mid"]) if snapshot_row["mid"] is not None else float("nan")
    if "bid" in snapshot_row and "ask" in snapshot_row:
        bid = snapshot_row["bid"]
        ask = snapshot_row["ask"]
        if bid in (None, "") or ask in (None, ""):
            raise ValueError("snapshot missing bid or ask")
        return float(bid) + (float(ask) - float(bid)) / 2.0
    raise ValueError("snapshot must contain mid OR bid and ask")


def compute_post_mid(market_snapshots: pd.DataFrame, post_ts) -> float:
    """Given a DataFrame of market snapshots with a timestamp column 'ts' and
    either mid or bid/ask columns, return the mid at the closest timestamp >= post_ts.
    """
    if "ts" not in market_snapshots.columns:
        raise ValueError("market_snapshots must include 'ts' column")

    ms = market_snapshots.copy()
    ms["ts"] = pd.to_datetime(ms["ts"])
    post_ts = pd.to_datetime(post_ts)
    ms = ms.loc[ms["ts"] >= post_ts]
    if ms.empty:
        raise ValueError("no snapshots at or after post_ts")
    row = ms.iloc[0]
    if "mid" in ms.columns and not pd.isna(row.get("mid")):
        return float(row["mid"])
    if "bid" in ms.columns and "ask" in ms.columns:
        bid = float(row["bid"])
        ask = float(row["ask"])
        return bid + (ask - bid) / 2.0
    raise ValueError("snapshot missing mid or bid/ask")
