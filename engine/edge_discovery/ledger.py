"""Experiment ledger: append-only JSON Lines record of WFA runs.

Each invocation of a wired function (e.g. run_wfa_cpcv) writes one JSON line
to a configurable output file.  The file is append-only so that a full run
history is available for downstream reporting without a database.

This module has no external dependencies beyond the standard library.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._file_lock import exclusive_file_lock


@dataclass
class LedgerEntry:
    """One complete record for a single WFA run invocation.

    Fields
    ------
    run_id : str
        Unique identifier for this run (typically a millisecond timestamp).
    run_type : str
        Identifier for the wired function that produced this entry
        (e.g. "wfa_cpcv").
    started_at : str
        ISO8601 UTC timestamp recorded at the start of the run.
    completed_at : str
        ISO8601 UTC timestamp recorded when the run finished.
    status : str
        "success" if the run completed without error, "error" otherwise.
    config_hash : str
        SHA-256 hash (first 16 hex chars) of a canonical JSON representation
        of the run configuration.  Used to detect configuration changes across
        runs without storing sensitive or verbose config data.
    git_commit : str or None
        Git commit SHA (abbreviated) of the repository at the time of the run,
        or None if unavailable (e.g. not a git repo, or subprocess failure).
    error : str or None
        Error message string when status == "error", None otherwise.
    input_artifacts : dict
        Mapping of logical name to path/URI of input artifacts consumed by
        this run (e.g. {"metaorders_csv": "/data/orders.csv"}).
    output_artifacts : dict
        Mapping of logical name to path/URI of output artifacts produced by
        this run (e.g. {"raw_splits": "/path/raw_splits_123.json"}).
    metrics_summary : dict
        Arbitrary summary metrics produced by the run
        (e.g. {"pbo_estimate": 0.03, "mean_return": 0.12}).
    """

    run_id: str
    run_type: str
    started_at: str
    completed_at: str
    status: str
    config_hash: str
    git_commit: Optional[str] = None
    error: Optional[str] = None
    input_artifacts: Dict[str, str] = field(default_factory=dict)
    output_artifacts: Dict[str, str] = field(default_factory=dict)
    metrics_summary: Dict[str, Any] = field(default_factory=dict)


class Ledger:
    """Append-only JSON Lines ledger writer and reader.

    Writes are protected by an exclusive file lock so concurrent processes
    do not corrupt the JSONL file.
    """

    def __init__(self, path: str | Path = ".wfa/ledger.jsonl") -> None:
        self.path = Path(path)

    def write(self, entry: LedgerEntry) -> None:
        """Append a single LedgerEntry as one JSON line."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(entry), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            with exclusive_file_lock(fh):
                fh.write(line + "\n")

    def read(self) -> List[LedgerEntry]:
        """Read all ledger entries from the file.

        Returns an empty list if the file does not exist.
        Blank lines are skipped.
        """
        entries: List[LedgerEntry] = []
        if not self.path.exists():
            return entries
        with self.path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines rather than propagating
                    continue
                entries.append(_dict_to_entry(data))
        return entries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dict_to_entry(d: Dict[str, Any]) -> LedgerEntry:
    """Convert a decoded dict back into a LedgerEntry."""
    return LedgerEntry(
        run_id=str(d.get("run_id", "")),
        run_type=str(d.get("run_type", "")),
        started_at=str(d.get("started_at", "")),
        completed_at=str(d.get("completed_at", "")),
        status=str(d.get("status", "")),
        config_hash=str(d.get("config_hash", "")),
        git_commit=d.get("git_commit"),
        error=d.get("error"),
        input_artifacts=dict(d.get("input_artifacts", {})),
        output_artifacts=dict(d.get("output_artifacts", {})),
        metrics_summary=dict(d.get("metrics_summary", {})),
    )


def config_hash(config: Dict[str, Any]) -> str:
    """Return a truncated SHA-256 hex digest of a configuration dict.

    The dict is serialised with sorted keys and no extra whitespace so that
    the same logical config always produces the same hash.
    """
    canonical = json.dumps(config, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def git_commit() -> Optional[str]:
    """Return the current git commit SHA (abbreviated) if available.

    Returns None silently if this is not a git repo or if any subprocess
    call fails.  Failures are silent because the ledger must not crash the
    main code path.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def now_utc() -> str:
    """Return the current UTC time as an ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()
