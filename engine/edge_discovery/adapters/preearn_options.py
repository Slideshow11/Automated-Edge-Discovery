"""Pre-earnings options backtest subprocess adapter.

This module provides a thin orchestration layer that runs the pre-earnings
options backtester as a subprocess, captures its results, and writes an AED
ledger entry.  It does NOT import or depend on any earnings_research Python
code.

Pre-earnings repo CLI:
  python3 scripts/run_options_backtest_v1.py \
    --options-db PATH \
    --run-id ID \
    --entry-dpe N \
    --delta-target F \
    --expiry-rank N \
    --fill-policy MID \
    --spread-penalty-k 0.5 \
    --contract-multiplier 100.0 \
    --out-csv /path/to/output.csv
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config as ed_config
from .. import ledger as ledger_module


_VALID_FILL_POLICIES = {"MID", "CROSS", "MID_WITH_SPREAD_PENALTY"}


@dataclass(frozen=True)
class CandidateSpec:
    """Specification for a single pre-earnings options backtest invocation.

    Parameters
    ----------
    entry_dpe : int
        Days-to-expiration at entry (DPE).  Must be >= 0.
    delta_target : float
        Option delta target.  Must be in (0.0, 1.0).
    expiry_rank : int
        Which expiry to trade relative to the event (0 = front,
        1 = next, ...).  Must be >= 0.
    options_db_path : str
        Absolute path to the pre-earnings options SQLite database.
    preearn_repo_path : str
        Absolute path to the engine_linux_main checkout containing
        ``scripts/run_options_backtest_v1.py``.
    fill_policy : str, default "MID"
        Fill policy passed to the pre-earnings backtester.
    spread_penalty_k : float, default 0.5
        Spread penalty coefficient passed to the pre-earnings backtester.
    contract_multiplier : float, default 100.0
        Contract multiplier passed to the pre-earnings backtester.
    run_id_prefix : str, default "preearn"
        Prefix for the run_id.  The full run_id is
        ``{run_id_prefix}_{timestamp}_{short_uuid}``.
    output_dir : str, default ".wfa/preearn"
        Directory for local artifact output.
    """

    entry_dpe: int
    delta_target: float
    expiry_rank: int
    options_db_path: str
    preearn_repo_path: str
    fill_policy: str = "MID"
    spread_penalty_k: float = 0.5
    contract_multiplier: float = 100.0
    run_id_prefix: str = "preearn"
    output_dir: str = ".wfa/preearn"

    # Internal-only fields (not part of the canonical spec for hashing)
    _run_id: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.entry_dpe < 0:
            raise ValueError(f"entry_dpe must be >= 0, got {self.entry_dpe}")
        if not (0.0 < self.delta_target < 1.0):
            raise ValueError(
                f"delta_target must be in (0.0, 1.0), got {self.delta_target}"
            )
        if self.expiry_rank < 0:
            raise ValueError(f"expiry_rank must be >= 0, got {self.expiry_rank}")
        if not self.options_db_path:
            raise ValueError("options_db_path must be a non-empty string")
        if not self.preearn_repo_path:
            raise ValueError("preearn_repo_path must be a non-empty string")
        if self.fill_policy not in _VALID_FILL_POLICIES:
            raise ValueError(
                f"fill_policy must be one of {_VALID_FILL_POLICIES}, "
                f"got {self.fill_policy!r}"
            )


@dataclass
class PreearnResult:
    """Result of a pre-earnings options backtest invocation.

    Fields
    ------
    run_id : str
        Unique run identifier used by the pre-earnings backtester.
    candidate_id : str
        Stable human-readable identifier derived from strategy parameters.
    status : str
        "success" or "error".
    config_hash : str
        16-char SHA-256 hash of the canonical CandidateSpec.
    git_commit : str or None
        Git commit SHA of the pre-earnings repo at invocation time.
    command : str
        Full command string that was executed.
    repo_path : str
        Absolute path to the pre-earnings repo.
    output_artifacts : dict
        Mapping of artifact name to absolute path.
    metrics_summary : dict
        Summary metrics extracted from the output CSV.
    error : str or None
        Error message string when status == "error".
    """

    run_id: str
    candidate_id: str
    status: str
    config_hash: str
    git_commit: Optional[str]
    command: str
    repo_path: str
    output_artifacts: Dict[str, str]
    metrics_summary: Dict[str, Any]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def candidate_id(spec: CandidateSpec) -> str:
    """Return a stable human-readable identifier for the candidate spec.

    Example: ``preearn_dpe2_delta30_rank0``
    """
    delta_int = int(round(spec.delta_target * 100))
    return (
        f"preearn"
        f"_dpe{spec.entry_dpe}"
        f"_delta{delta_int}"
        f"_rank{spec.expiry_rank}"
    )


def config_hash(spec: CandidateSpec) -> str:
    """Return a truncated SHA-256 hex digest of the candidate spec.

    The spec is serialised with sorted keys so the same logical spec
    always produces the same hash.
    """
    # Omit internal-only fields from hashing
    fields = {
        "entry_dpe": spec.entry_dpe,
        "delta_target": spec.delta_target,
        "expiry_rank": spec.expiry_rank,
        "options_db_path": spec.options_db_path,
        "preearn_repo_path": spec.preearn_repo_path,
        "fill_policy": spec.fill_policy,
        "spread_penalty_k": spec.spread_penalty_k,
        "contract_multiplier": spec.contract_multiplier,
        "output_dir": spec.output_dir,
    }
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def get_git_commit(repo_path: str) -> Optional[str]:
    """Return the current git commit SHA (abbreviated) for the given repo.

    Returns None silently if the path is not a git repo or if the subprocess
    call fails for any reason.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def build_command(spec: CandidateSpec, output_csv_path: str) -> List[str]:
    """Build the subprocess command list for running the pre-earnings backtest.

    Parameters
    ----------
    spec : CandidateSpec
        The candidate specification.
    output_csv_path : str
        Absolute path for the output CSV file.

    Returns
    -------
    list[str]
        Command argument list suitable for ``subprocess.run``.
    """
    return [
        sys.executable,
        str(Path(spec.preearn_repo_path) / "scripts" / "run_options_backtest_v1.py"),
        "--options-db",
        spec.options_db_path,
        "--run-id",
        spec._run_id or candidate_id(spec),
        "--entry-dpe",
        str(spec.entry_dpe),
        "--delta-target",
        str(spec.delta_target),
        "--expiry-rank",
        str(spec.expiry_rank),
        "--fill-policy",
        spec.fill_policy,
        "--spread-penalty-k",
        str(spec.spread_penalty_k),
        "--contract-multiplier",
        str(spec.contract_multiplier),
        "--out-csv",
        output_csv_path,
    ]


def summarize_trades_csv(csv_path: str) -> Dict[str, Any]:
    """Extract summary metrics from a pre-earnings trades CSV.

    Parameters
    ----------
    csv_path : str
        Absolute path to the output trades CSV.

    Returns
    -------
    dict
        Dict with at least ``n_trades`` (int) and ``n_columns`` (int).
        If an ``earnings_event_id`` column is present, also includes
        ``n_events`` (distinct event count).  If a ``symbol`` column is
        present, also includes ``n_symbols`` (distinct symbol count).
    """
    import csv

    path = Path(csv_path)
    if not path.exists():
        return {"n_trades": 0, "n_columns": 0, "n_events": None, "n_symbols": None}

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        if not rows:
            return {"n_trades": 0, "n_columns": len(reader.fieldnames or []), "n_events": None, "n_symbols": None}

        n_trades = len(rows)
        n_columns = len(reader.fieldnames or [])

        n_events: Optional[int] = None
        n_symbols: Optional[int] = None

        if "earnings_event_id" in reader.fieldnames:
            n_events = len({r["earnings_event_id"] for r in rows if r.get("earnings_event_id")})

        if "symbol" in reader.fieldnames:
            n_symbols = len({r["symbol"] for r in rows if r.get("symbol")})

        return {
            "n_trades": n_trades,
            "n_columns": n_columns,
            "n_events": n_events,
            "n_symbols": n_symbols,
        }


def run_preearn_backtest(
    spec: CandidateSpec,
    ledger_path: Optional[str] = None,
    timeout: Optional[float] = 600.0,
) -> PreearnResult:
    """Run a pre-earnings options backtest as a subprocess.

    This function:
    1. Validates that required paths exist.
    2. Generates a run_id.
    3. Builds the subprocess command.
    4. Executes the pre-earnings backtester.
    5. Writes one AED ledger entry (success or error).
    6. Returns a ``PreearnResult``.

    On error, the ledger entry is written before the exception is re-raised,
    so every invocation leaves a trace in the ledger.

    Parameters
    ----------
    spec : CandidateSpec
        The candidate specification.
    ledger_path : str, optional
        Path for the AED JSONL ledger.  If None, uses
        ``get_config()["ledger_path"]``.
    timeout : float, optional
        Subprocess timeout in seconds.  Defaults to 600 (10 minutes).

    Returns
    -------
    PreearnResult

    Raises
    ------
   subprocess.CalledProcessError
        When the pre-earnings script exits with a non-zero code.
    subprocess.TimeoutExpired
        When the subprocess exceeds ``timeout``.
    FileNotFoundError
        When required paths do not exist.
    """
    # Preflight validation
    preearn_root = Path(spec.preearn_repo_path)
    if not preearn_root.exists():
        raise FileNotFoundError(f"preearn_repo_path does not exist: {spec.preearn_repo_path}")

    backtest_script = preearn_root / "scripts" / "run_options_backtest_v1.py"
    if not backtest_script.exists():
        raise FileNotFoundError(
            f"Pre-earnings backtest script not found: {backtest_script}"
        )

    options_db = Path(spec.options_db_path)
    if not options_db.exists():
        raise FileNotFoundError(f"options_db_path does not exist: {spec.options_db_path}")

    # Generate run_id and output paths
    import time
    import uuid

    ts = int(time.time() * 1000)
    short_uid = uuid.uuid4().hex[:6]
    run_id = f"{spec.run_id_prefix}_{ts}_{short_uid}"

    # Create a frozen spec copy with the generated run_id
    frozen_spec = CandidateSpec(
        entry_dpe=spec.entry_dpe,
        delta_target=spec.delta_target,
        expiry_rank=spec.expiry_rank,
        options_db_path=spec.options_db_path,
        preearn_repo_path=spec.preearn_repo_path,
        fill_policy=spec.fill_policy,
        spread_penalty_k=spec.spread_penalty_k,
        contract_multiplier=spec.contract_multiplier,
        run_id_prefix=spec.run_id_prefix,
        output_dir=spec.output_dir,
        _run_id=run_id,
    )

    output_dir = Path(spec.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv_path = str(output_dir / f"trades_{run_id}.csv")

    started_at = ledger_module.now_utc()
    candidate_id_str = candidate_id(frozen_spec)
    config_hash_str = config_hash(frozen_spec)
    git_commit = get_git_commit(spec.preearn_repo_path)

    cmd = build_command(frozen_spec, output_csv_path)
    command_str = " ".join(cmd)

    _ledger_error: Optional[str] = None
    _ledger_status = "error"
    _result: PreearnResult

    try:
        result = subprocess.run(
            cmd,
            cwd=str(preearn_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        metrics = summarize_trades_csv(output_csv_path)
        _result = PreearnResult(
            run_id=run_id,
            candidate_id=candidate_id_str,
            status="success",
            config_hash=config_hash_str,
            git_commit=git_commit,
            command=command_str,
            repo_path=str(preearn_root),
            output_artifacts={"trades_csv": output_csv_path},
            metrics_summary=metrics,
            error=None,
        )
        _ledger_status = "success"

    except subprocess.CalledProcessError as e:
        _ledger_error = str(e.stderr or e)[:500]
        _result = PreearnResult(
            run_id=run_id,
            candidate_id=candidate_id_str,
            status="error",
            config_hash=config_hash_str,
            git_commit=git_commit,
            command=command_str,
            repo_path=str(preearn_root),
            output_artifacts={},
            metrics_summary={},
            error=_ledger_error,
        )
        raise

    except subprocess.TimeoutExpired as e:
        _ledger_error = f"timeout after {timeout} seconds"
        _result = PreearnResult(
            run_id=run_id,
            candidate_id=candidate_id_str,
            status="error",
            config_hash=config_hash_str,
            git_commit=git_commit,
            command=command_str,
            repo_path=str(preearn_root),
            output_artifacts={},
            metrics_summary={},
            error=_ledger_error,
        )
        raise

    finally:
        completed_at = ledger_module.now_utc()

        if ledger_path is None:
            ledger_path = ed_config.get_config()["ledger_path"]

        entry = ledger_module.LedgerEntry(
            run_id=run_id,
            run_type="preearn_options",
            started_at=started_at,
            completed_at=completed_at,
            status=_ledger_status,
            config_hash=config_hash_str,
            git_commit=git_commit,
            error=_result.error,
            input_artifacts={"options_db": spec.options_db_path},
            output_artifacts=_result.output_artifacts,
            metrics_summary=_result.metrics_summary,
        )

        try:
            ledger_module.Ledger(path=ledger_path).write(entry)
        except Exception:
            # Ledger failure must never mask or alter the function result
            import logging
            logger2 = logging.getLogger(__name__)
            logger2.exception("Failed to write preearn_options ledger entry")

    return _result
