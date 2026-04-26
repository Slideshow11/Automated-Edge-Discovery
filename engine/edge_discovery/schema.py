"""Schema and validation for parent-order (metaorder) records.

Simple dataclass-based validators used by calibrate_ac_v2.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any

import pandas as pd


@dataclass
class ParentOrderRecord:
    symbol: str
    parent_id: str
    trade_date: str
    arrival_ts: pd.Timestamp
    start_ts: pd.Timestamp
    end_ts: pd.Timestamp
    post_ts: pd.Timestamp
    arrival_mid: float
    vwap_exec: float
    post_mid: float
    signed_qty: float
    adv: float
    sigma: Optional[float] = None
    participation: Optional[float] = None


class ValidationError(ValueError):
    pass


def parse_timestamp(val) -> pd.Timestamp:
    if pd.isna(val):
        raise ValidationError("timestamp is missing")
    return pd.to_datetime(val)


def validate_parent_row(row: Dict[str, Any], strict: bool = True) -> ParentOrderRecord:
    """Validate a dict-like row and return ParentOrderRecord.

    If strict=True, raise ValidationError on missing mandatory fields.
    If strict=False, raise only for clearly malformed rows.
    """
    required = [
        "symbol",
        "parent_id",
        "trade_date",
        "arrival_ts",
        "start_ts",
        "end_ts",
        "post_ts",
        "arrival_mid",
        "vwap_exec",
        "post_mid",
        "signed_qty",
        "adv",
    ]

    missing = [c for c in required if c not in row or row.get(c) is None or str(row.get(c)).strip() == ""]
    if missing:
        msg = f"Missing required fields: {missing}"
        if strict:
            raise ValidationError(msg)
        else:
            raise ValidationError(msg)

    try:
        arrival_ts = parse_timestamp(row["arrival_ts"])
        start_ts = parse_timestamp(row["start_ts"])
        end_ts = parse_timestamp(row["end_ts"])
        post_ts = parse_timestamp(row["post_ts"])
    except Exception as e:
        raise ValidationError("Invalid timestamps: %s" % e)

    # numeric parsing
    try:
        arrival_mid = float(row["arrival_mid"])
        vwap_exec = float(row["vwap_exec"])
        post_mid = float(row["post_mid"])
        signed_qty = float(row["signed_qty"])
        adv = float(row["adv"])
    except Exception as e:
        raise ValidationError("Numeric parse error: %s" % e)

    sigma = None
    if row.get("sigma") not in (None, ""):
        try:
            sigma = float(row.get("sigma"))
        except Exception:
            if strict:
                raise ValidationError("sigma parse error")

    participation = None
    if row.get("participation") not in (None, ""):
        try:
            participation = float(row.get("participation"))
        except Exception:
            pass

    por = ParentOrderRecord(
        symbol=str(row["symbol"]),
        parent_id=str(row["parent_id"]),
        trade_date=str(row["trade_date"]),
        arrival_ts=arrival_ts,
        start_ts=start_ts,
        end_ts=end_ts,
        post_ts=post_ts,
        arrival_mid=arrival_mid,
        vwap_exec=vwap_exec,
        post_mid=post_mid,
        signed_qty=signed_qty,
        adv=adv,
        sigma=sigma,
        participation=participation,
    )

    # timestamp ordering
    if not (por.arrival_ts <= por.start_ts <= por.end_ts <= por.post_ts):
        raise ValidationError("Timestamps out of order: arrival <= start <= end <= post required")

    return por
