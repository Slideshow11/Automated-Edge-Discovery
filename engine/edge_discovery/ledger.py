"""Ledger for tracking edge-discovery candidates across train/test runs.

Every invocation of the backtest runner writes one entry.  Entries are
append-only and stored as JSON Lines in a configurable output file so that
downstream reporting tools can consume them without a database.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SplitSpec:
    """Describes the train/test split used for a single run."""
    n_splits: int = 2
    purge_fraction: float = 0.01
    purge_method: str = "timestamp"  # e.g. "timestamp", "count", "none"
    splitter_type: str = "kfold"      # e.g. "kfold", "purged", "cpcv"


@dataclass
class CostModelVersion:
    """Version descriptor for the cost model used."""
    name: str = "default"
    version: str = "1.0"
    config_hash: str = ""           # sha256 of the cost-model params dict


@dataclass
class AuditResult:
    """Outcome of the auditor's quality gate."""
    passed: bool = False
    reason: str = ""
    pbo_estimate: Optional[float] = None
    sharpe_filter_passed: bool = False
    drawdown_filter_passed: bool = False
    insufficient_data: bool = False


@dataclass
class CandidateMetrics:
    """All per-split and aggregated metrics emitted by the backtester."""
    total_return: Optional[float] = None
    sharpe: Optional[float] = None
    max_drawdown: Optional[float] = None
    trades: Optional[int] = None
    mean_return: Optional[float] = None
    median_return: Optional[float] = None
    mean_sharpe: Optional[float] = None
    mean_max_drawdown: Optional[float] = None
    pbo_estimate: Optional[float] = None           # DSR-style surrogate
    n_splits: int = 0
    n_trades: int = 0

    # Per-split raw metrics (optional, for audit replay)
    per_split: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class LedgerEntry:
    """One complete record for a single run of one candidate strategy."""
    # Identity
    run_id: str                           # uuid4, reset per invocation
    candidate_id: str                     # human-readable strategy label
    strategy_family: str                   # e.g. "earnings_straddle", "momentum"
    dataset_version: str                  # e.g. "v2_2024Q4", "prod_20250101"

    # Provenance
    feature_set_hash: str                 # sha256 of the feature-config dict
    code_commit: str                      # git SHA at time of run
    cost_model_version: CostModelVersion = field(default_factory=CostModelVersion)

    # Run configuration
    split_spec: SplitSpec = field(default_factory=SplitSpec)

    # Results
    metrics: CandidateMetrics = field(default_factory=CandidateMetrics)
    audit: AuditResult = field(default_factory=AuditResult)

    # Disposition
    promoted: bool = False
    promotion_reason: str = ""            # e.g. "pbo>0.6 and sharpe>1.0"
    rejected_reason: str = ""             # e.g. "insufficient data", "audit failed"

    # Artifact paths
    artifact_paths: Dict[str, str] = field(default_factory=dict)
    # Keys might include: "report_json", "diagnostics_dir", "backtest_stdout", etc.

    # Timestamps
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class Ledger:
    """Append-only JSON Lines ledger.  Thread-unsafe — use a lock externally."""

    def __init__(self, path: str | Path = ".wfa/ledger.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, entry: LedgerEntry) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def read(self) -> List[LedgerEntry]:
        entries = []
        if not self.path.exists():
            return entries
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entries.append(_dict_to_entry(data))
        return entries

    @staticmethod
    def feature_set_hash(features: Dict[str, Any]) -> str:
        """Deterministic hash of a feature-config dict."""
        canonical = json.dumps(features, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @staticmethod
    def cost_model_hash(config: Dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest()[:16]


def _dict_to_entry(d: Dict[str, Any]) -> LedgerEntry:
    # Unmarshal nested dataclasses
    d["cost_model_version"] = CostModelVersion(**d.get("cost_model_version", {}))
    d["split_spec"]          = SplitSpec(**d.get("split_spec", {}))
    d["metrics"]             = CandidateMetrics(**d.get("metrics", {}))
    d["audit"]               = AuditResult(**d.get("audit", {}))
    return LedgerEntry(**d)
