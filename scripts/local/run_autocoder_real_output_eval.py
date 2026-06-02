#!/usr/bin/env python3
"""
run_autocoder_real_output_eval.py — Report-only real-output autocoder evaluator v0.

Reads a corpus of real-output task definitions (corpus/autocoder-real-output-v0.json)
and zero or more result packets (one per task), matches them, computes aggregate
metrics, and emits a JSON and Markdown report. The script is read-only:

  - No model calls.
  - No GitHub mutation.
  - No subprocess execution of any kind.
  - No shell.
  - No network.

The purpose is to MEASURE the usefulness of already-produced patch outputs
(across PRs, runs, or batches) without re-executing anything. v0 is intentionally
tolerant of result-packet shape variation.

Usage:
    python3 scripts/local/run_autocoder_real_output_eval.py \\
        --corpus corpus/autocoder-real-output-v0.json \\
        --result-json /tmp/task_001_result.json \\
        --result-json /tmp/task_002_result.json \\
        --output-json /tmp/eval.json \\
        --output-md   /tmp/eval.md

If no --result-json is provided, a baseline report is emitted with zero
completed tasks (status: REAL_OUTPUT_EVAL_READY, all counters at 0).

Exit codes:
    0 — report written (status may be any value, e.g. HOLD_*)
    2 — invalid CLI args (ERROR_INVALID_ARGS)
    1 — unexpected internal error (ERROR_TOOL_FAILURE)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Status taxonomy
# ---------------------------------------------------------------------------

STATUS_READY = "REAL_OUTPUT_EVAL_READY"
STATUS_HOLD_CORPUS_INVALID = "HOLD_REAL_OUTPUT_CORPUS_INVALID"
STATUS_HOLD_RESULT_INVALID = "HOLD_REAL_OUTPUT_RESULT_INVALID"
STATUS_ERROR_INVALID_ARGS = "ERROR_INVALID_ARGS"
STATUS_ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

PACKET_KIND_EVAL = "aed.autocoder.real_output_eval.v0"
PACKET_KIND_CORPUS = "aed.autocoder.real_output_corpus.v0"
SCHEMA_VERSION = 1

# Result-packet status tokens (tolerant: anything else falls into 'unknown')
RESULT_STATUS_PASS = "PASS"
RESULT_STATUS_HOLD = "HOLD"
RESULT_STATUS_FAIL = "FAIL"
RESULT_STATUS_ERROR = "ERROR"

RECOMMENDATION_BY_STATUS = {
    STATUS_READY: "Real-output eval report is valid. See metrics below.",
    STATUS_HOLD_CORPUS_INVALID: "Corpus is invalid. Fix the corpus and re-run.",
    STATUS_HOLD_RESULT_INVALID: "One or more result packets reference unknown or malformed task_ids. Re-run with corrected result packets.",
    STATUS_ERROR_INVALID_ARGS: "Invalid CLI args. See stderr for the offending argument.",
    STATUS_ERROR_TOOL_FAILURE: "Tool failure. See errors[] for the underlying cause.",
}


# ---------------------------------------------------------------------------
# Required-field validators
# ---------------------------------------------------------------------------

CORPUS_REQUIRED_FIELDS = (
    "packet_kind",
    "schema_version",
    "corpus_id",
    "created_at",
    "description",
    "tasks",
)

TASK_REQUIRED_FIELDS = (
    "task_id",
    "title",
    "task_type",
    "goal",
    "allowed_files",
    "forbidden_files",
    "expected_artifacts",
    "expected_tests",
    "scoring",
    "risk_level",
    "non_goals",
)

# Result-packet fields are all optional; we coerce with defaults.
# But if present, types must be sane.
RESULT_NUMERIC_FIELDS = (
    "tests_passed",
    "tests_failed",
    "tests_total",
)

RESULT_BOOL_FIELDS = (
    "ci_green",
    "scope_clean",
    "review_ready",
    "merge_ready",
    "human_cleanup_required",
)


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report-only real-output autocoder evaluator v0. "
            "Reads a corpus and zero or more result packets and emits JSON+Markdown."
        ),
    )
    parser.add_argument(
        "--corpus",
        required=True,
        help="Path to the real-output corpus JSON file.",
    )
    parser.add_argument(
        "--result-json",
        action="append",
        default=[],
        dest="result_json",
        help="Path to a result packet JSON file. Repeatable. Optional.",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path where the JSON eval report will be written.",
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path where the Markdown eval report will be written.",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> Tuple[bool, str]:
    if not isinstance(args.corpus, str) or not args.corpus.strip():
        return False, "--corpus must be a non-empty string"
    if not isinstance(args.output_json, str) or not args.output_json.strip():
        return False, "--output-json must be a non-empty string"
    if not isinstance(args.output_md, str) or not args.output_md.strip():
        return False, "--output-md must be a non-empty string"
    if args.result_json is None:
        return False, "--result-json must be a list (may be empty)"
    for rj in args.result_json:
        if not isinstance(rj, str) or not rj.strip():
            return False, "every --result-json value must be a non-empty string"
    return True, ""


# ---------------------------------------------------------------------------
# Corpus and result loading
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Dict[str, Any]:
    """Load a JSON file. Raises on missing, malformed, or non-object payload."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"file not found: {path}")
    text = p.read_text(encoding="utf-8")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError(f"expected JSON object at {path}, got {type(obj).__name__}")
    return obj


def load_corpus(path: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Return (corpus_dict_or_none, errors). None + errors means invalid."""
    errors: List[str] = []
    try:
        corpus = _load_json(path)
    except FileNotFoundError as e:
        return None, [str(e)]
    except json.JSONDecodeError as e:
        return None, [f"corpus JSON is malformed: {e}"]
    except ValueError as e:
        return None, [str(e)]

    if corpus.get("packet_kind") != PACKET_KIND_CORPUS:
        errors.append(
            f"corpus.packet_kind must be {PACKET_KIND_CORPUS!r}, "
            f"got {corpus.get('packet_kind')!r}"
        )
    if corpus.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"corpus.schema_version must be {SCHEMA_VERSION}, "
            f"got {corpus.get('schema_version')!r}"
        )
    for f in CORPUS_REQUIRED_FIELDS:
        if f not in corpus:
            errors.append(f"corpus missing required field: {f!r}")

    tasks = corpus.get("tasks")
    if not isinstance(tasks, list) or len(tasks) == 0:
        errors.append("corpus.tasks must be a non-empty list")

    # Per-task validation
    if isinstance(tasks, list):
        seen_ids: Set[str] = set()
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                errors.append(f"tasks[{i}] must be a JSON object")
                continue
            for f in TASK_REQUIRED_FIELDS:
                if f not in t:
                    errors.append(f"tasks[{i}] ({t.get('task_id', '?')}) missing {f!r}")
            tid = t.get("task_id")
            if not isinstance(tid, str) or not tid:
                errors.append(f"tasks[{i}].task_id must be a non-empty string")
            elif tid in seen_ids:
                errors.append(f"tasks[{i}].task_id {tid!r} is duplicated")
            else:
                seen_ids.add(tid)
            for list_field in ("allowed_files", "forbidden_files", "expected_artifacts", "expected_tests", "non_goals"):
                v = t.get(list_field)
                if v is not None and not isinstance(v, list):
                    errors.append(
                        f"tasks[{i}] ({tid}).{list_field} must be a list, got {type(v).__name__}"
                    )
            scoring = t.get("scoring")
            if scoring is not None and not isinstance(scoring, dict):
                errors.append(
                    f"tasks[{i}] ({tid}).scoring must be a dict, got {type(scoring).__name__}"
                )

    if errors:
        return None, errors
    return corpus, []


def load_result(path: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Return (result_dict_or_none, errors)."""
    errors: List[str] = []
    try:
        result = _load_json(path)
    except FileNotFoundError as e:
        return None, [str(e)]
    except json.JSONDecodeError as e:
        return None, [f"result JSON is malformed: {e}"]
    except ValueError as e:
        return None, [str(e)]

    # result packet is tolerant: missing fields default; type errors are flagged
    tid = result.get("task_id")
    if not isinstance(tid, str) or not tid:
        errors.append(f"result missing or invalid task_id (must be non-empty string)")

    status = result.get("status", "UNKNOWN")
    if not isinstance(status, str):
        errors.append(f"result.status must be a string, got {type(status).__name__}")

    for f in RESULT_NUMERIC_FIELDS:
        v = result.get(f)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            errors.append(f"result.{f} must be a non-negative int, got {v!r}")
    for f in RESULT_BOOL_FIELDS:
        v = result.get(f)
        if v is not None and not isinstance(v, bool):
            errors.append(f"result.{f} must be a boolean, got {v!r}")

    for list_field in ("changed_files", "allowed_files", "forbidden_files"):
        v = result.get(list_field)
        if v is not None and not isinstance(v, list):
            errors.append(
                f"result.{list_field} must be a list, got {type(v).__name__}"
            )

    if errors:
        return None, errors
    return result, []


# ---------------------------------------------------------------------------
# Scope violation check
# ---------------------------------------------------------------------------

def _glob_match(path: str, pattern: str) -> bool:
    """Match a path against a glob pattern. Supports ** for multi-segment wildcards."""
    # fnmatch doesn't support **, so do a simple manual expansion.
    if "**" in pattern:
        # translate ** to .* and * to [^/]* (greedy in practice via fnmatch fallback)
        regex = pattern.replace(".", r"\.").replace("**", ".*").replace("*", "[^/]*")
        regex = "^" + regex + "$"
        import re
        return re.match(regex, path) is not None
    return fnmatch.fnmatch(path, pattern)


def scope_violation(allowed: List[str], forbidden: List[str], changed: List[str]) -> List[str]:
    """Return the list of changed files that violate scope.

    A file is in violation if it matches any forbidden pattern OR if it does
    not match any allowed pattern. A file matching both forbidden and allowed
    is treated as forbidden (forbidden takes precedence).
    """
    violations: List[str] = []
    for f in changed:
        if not isinstance(f, str) or not f:
            continue
        if any(_glob_match(f, pat) for pat in forbidden):
            violations.append(f)
            continue
        if not any(_glob_match(f, pat) for pat in allowed):
            violations.append(f)
    return violations


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def empty_metrics() -> Dict[str, int]:
    return {
        "tasks_total": 0,
        "tasks_with_results": 0,
        "patches_produced": 0,
        "scope_clean_count": 0,
        "tests_passed_count": 0,
        "ci_green_count": 0,
        "review_ready_count": 0,
        "merge_ready_count": 0,
        "human_cleanup_required_count": 0,
        "hold_count": 0,
        "error_count": 0,
        "unknown_count": 0,
    }


def compute_task_record(
    task: Dict[str, Any],
    result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute the per-task record for the report."""
    record: Dict[str, Any] = {
        "task_id": task["task_id"],
        "title": task.get("title", ""),
        "task_type": task.get("task_type", ""),
        "risk_level": task.get("risk_level", "unknown"),
        "expected_scoring": task.get("scoring", {}),
        "has_result": result is not None,
    }
    if result is None:
        record["result_status"] = "MISSING"
        record["notes"] = ["no result packet provided for this task"]
        return record

    record["result_status"] = result.get("status", "UNKNOWN")
    record["result_patches_produced"] = int(bool(result.get("changed_files")))
    changed = result.get("changed_files") or []
    allowed = task.get("allowed_files") or []
    forbidden = task.get("forbidden_files") or []
    record["scope_violations"] = scope_violation(allowed, forbidden, changed)
    record["tests_passed"] = result.get("tests_passed", 0)
    record["ci_green"] = bool(result.get("ci_green", False))
    record["scope_clean"] = bool(result.get("scope_clean", False)) and not record["scope_violations"]
    record["review_ready"] = bool(result.get("review_ready", False))
    record["merge_ready"] = bool(result.get("merge_ready", False))
    record["human_cleanup_required"] = bool(result.get("human_cleanup_required", False))
    if result.get("hold_reason"):
        record["hold_reason"] = result["hold_reason"]
    if result.get("error_reason"):
        record["error_reason"] = result["error_reason"]
    return record


def compute_metrics(
    corpus: Dict[str, Any],
    results_by_task: Dict[str, Dict[str, Any]],
    invalid_result_packets: List[str],
) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    """Compute aggregate metrics and per-task records."""
    metrics = empty_metrics()
    tasks = corpus.get("tasks", [])
    metrics["tasks_total"] = len(tasks)

    records: List[Dict[str, Any]] = []
    for task in tasks:
        tid = task["task_id"]
        result = results_by_task.get(tid)
        rec = compute_task_record(task, result)
        records.append(rec)

        if result is None:
            continue
        metrics["tasks_with_results"] += 1
        status = (result.get("status") or "UNKNOWN").upper()
        if status == RESULT_STATUS_PASS:
            metrics["patches_produced"] += int(bool(result.get("changed_files")))
            if rec["scope_clean"]:
                metrics["scope_clean_count"] += 1
            metrics["tests_passed_count"] += int(result.get("tests_passed", 0) or 0)
            if result.get("ci_green"):
                metrics["ci_green_count"] += 1
            if result.get("review_ready"):
                metrics["review_ready_count"] += 1
            if result.get("merge_ready"):
                metrics["merge_ready_count"] += 1
            if result.get("human_cleanup_required"):
                metrics["human_cleanup_required_count"] += 1
        elif status == RESULT_STATUS_HOLD:
            metrics["hold_count"] += 1
        elif status == RESULT_STATUS_ERROR:
            metrics["error_count"] += 1
        else:
            metrics["unknown_count"] += 1

    if invalid_result_packets:
        # Any structurally invalid result packet is also a tool-level concern;
        # count it as an error.
        metrics["error_count"] += len(invalid_result_packets)
    return metrics, records


# ---------------------------------------------------------------------------
# Packet and report rendering
# ---------------------------------------------------------------------------

def build_packet(
    args: argparse.Namespace,
    status: str,
    corpus: Optional[Dict[str, Any]],
    results_by_task: Dict[str, Dict[str, Any]],
    invalid_result_packets: List[str],
    metrics: Dict[str, int],
    records: List[Dict[str, Any]],
    missing_result_task_ids: List[str],
    errors: List[str],
    matched_in_corpus_count: int = 0,
) -> Dict[str, Any]:
    """Build the eval packet. matched_in_corpus_count is the number of result
    packets whose task_id exists in the corpus (i.e. true matches). It is
    computed by evaluate() and passed in so that the count never includes
    results referencing unknown task_ids. invalid_result_packets is the list
    of paths of structurally invalid result packets (those that failed to
    load); it is the same collection that drives the hard HOLD gate."""
    packet: Dict[str, Any] = {
        "packet_kind": PACKET_KIND_EVAL,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_path": args.corpus,
        "corpus_id": (corpus or {}).get("corpus_id", ""),
        "status": status,
        "task_count": metrics["tasks_total"],
        "result_count": len(args.result_json),
        "matched_result_count": matched_in_corpus_count,
        "missing_result_task_ids": missing_result_task_ids,
        "invalid_result_packets": invalid_result_packets,
        "metrics": metrics,
        "tasks": records,
        "errors": errors,
        "recommendation": RECOMMENDATION_BY_STATUS.get(status, ""),
    }
    return packet


def render_markdown(packet: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Real-Output Autocoder Eval Report")
    lines.append("")
    lines.append(f"**Corpus**: `{packet.get('corpus_id', '')}`  ")
    lines.append(f"**Corpus path**: `{packet.get('corpus_path', '')}`  ")
    lines.append(f"**Generated at**: `{packet.get('generated_at', '')}`  ")
    lines.append(f"**Status**: `{packet.get('status', '')}`  ")
    lines.append("")
    lines.append("## Summary metrics")
    lines.append("")
    m = packet.get("metrics", {})
    rows = [
        ("tasks_total", m.get("tasks_total", 0)),
        ("tasks_with_results", m.get("tasks_with_results", 0)),
        ("patches_produced", m.get("patches_produced", 0)),
        ("scope_clean_count", m.get("scope_clean_count", 0)),
        ("tests_passed_count", m.get("tests_passed_count", 0)),
        ("ci_green_count", m.get("ci_green_count", 0)),
        ("review_ready_count", m.get("review_ready_count", 0)),
        ("merge_ready_count", m.get("merge_ready_count", 0)),
        ("human_cleanup_required_count", m.get("human_cleanup_required_count", 0)),
        ("hold_count", m.get("hold_count", 0)),
        ("error_count", m.get("error_count", 0)),
        ("unknown_count", m.get("unknown_count", 0)),
    ]
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    for k, v in rows:
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append("## Result match")
    lines.append("")
    lines.append(f"- result_count: {packet.get('result_count', 0)}")
    lines.append(f"- matched_result_count: {packet.get('matched_result_count', 0)}")
    missing = packet.get("missing_result_task_ids", [])
    if missing:
        lines.append(f"- missing_result_task_ids: {', '.join(missing)}")
    else:
        lines.append("- missing_result_task_ids: _(none)_")
    unknowns = packet.get("invalid_result_packets", [])
    if unknowns:
        lines.append("")
        lines.append("## Invalid result packets")
        lines.append("")
        for u in unknowns:
            lines.append(f"- `{u}`")
    lines.append("")
    lines.append("## Per-task records")
    lines.append("")
    lines.append("| task_id | task_type | risk | result_status | scope_clean | ci_green | merge_ready | tests_passed | scope_violations |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for rec in packet.get("tasks", []):
        scope_violations = rec.get("scope_violations", []) or []
        lines.append(
            f"| `{rec.get('task_id','')}` | "
            f"{rec.get('task_type','')} | "
            f"{rec.get('risk_level','')} | "
            f"{rec.get('result_status','')} | "
            f"{rec.get('scope_clean', '')} | "
            f"{rec.get('ci_green', '')} | "
            f"{rec.get('merge_ready', '')} | "
            f"{rec.get('tests_passed', 0)} | "
            f"{', '.join(scope_violations) if scope_violations else '_(none)_'} |"
        )
    lines.append("")
    errors = packet.get("errors", [])
    if errors:
        lines.append("## Errors")
        lines.append("")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append(packet.get("recommendation", ""))
    lines.append("")
    return "\n".join(lines)


def write_outputs(packet: Dict[str, Any], json_path: str, md_path: str) -> Tuple[bool, str]:
    """Write JSON and Markdown. Returns (ok, error_message)."""
    try:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(packet, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        Path(md_path).parent.mkdir(parents=True, exist_ok=True)
        Path(md_path).write_text(render_markdown(packet), encoding="utf-8")
        return True, ""
    except OSError as e:
        return False, f"failed to write outputs: {e}"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def evaluate(args: argparse.Namespace) -> Tuple[str, Dict[str, Any]]:
    """Run the full evaluation. Returns (status, packet)."""
    errors: List[str] = []

    # 1. Load corpus
    corpus, corpus_errors = load_corpus(args.corpus)
    if corpus is None:
        errors.extend(corpus_errors)
        packet = build_packet(
            args=args,
            status=STATUS_HOLD_CORPUS_INVALID,
            corpus=None,
            results_by_task={},
            invalid_result_packets=[],
            metrics=empty_metrics(),
            records=[],
            missing_result_task_ids=[],
            errors=errors,
        )
        return STATUS_HOLD_CORPUS_INVALID, packet

    # 2. Load result packets; bucket by task_id; collect invalid packets.
    #
    # A result packet is "structurally invalid" if it fails to load (malformed
    # JSON, missing required fields, wrong types, or an invalid task_id). The
    # presence of ANY structurally invalid packet forces a hard HOLD — see the
    # gate immediately after this loop. This loop is the review anchor: the
    # collection is named invalid_result_packets (not "unknown_results") and
    # the gate is right next to the loop so the relationship is obvious.
    results_by_task: Dict[str, Dict[str, Any]] = {}
    invalid_result_packets: List[str] = []
    for rj in args.result_json:
        result, result_errors = load_result(rj)
        if result is None:
            # STRUCTURALLY INVALID result packet. This is NOT an ignorable
            # "unknown" result — it is a hard error that MUST cause
            # HOLD_REAL_OUTPUT_RESULT_INVALID. The check below the loop is
            # the gate; do not "soften" the append into a plain log line.
            invalid_result_packets.append(rj)
            errors.extend([f"{rj}: {e}" for e in result_errors])
            continue
        tid = result.get("task_id", "")
        if tid in results_by_task:
            # duplicate result for the same task — keep first, log the dup
            errors.append(f"duplicate result for task_id {tid!r} ({rj}); keeping first")
            continue
        results_by_task[tid] = result

    # Compute everything the hard gate below needs in one block, so the gate
    # can return a fully-populated packet without falling through.
    metrics, records = compute_metrics(corpus, results_by_task, invalid_result_packets)
    corpus_task_ids = {t["task_id"] for t in corpus["tasks"]}
    matched_task_ids = set(results_by_task.keys())
    matched_in_corpus = matched_task_ids & corpus_task_ids
    extra_results = matched_task_ids - corpus_task_ids  # results for tasks not in corpus
    missing_result_task_ids = sorted(corpus_task_ids - matched_task_ids)
    matched_in_corpus_count = len(matched_in_corpus)
    for rec in records:
        rec["matched_in_corpus"] = rec["task_id"] in {tid for tid in matched_in_corpus}

    # HARD GATE: any structurally invalid result packet forces
    # HOLD_REAL_OUTPUT_RESULT_INVALID. This gate is placed immediately after
    # the loop that detects them so the relationship is direct and obvious.
    if invalid_result_packets:
        errors.append(
            f"{len(invalid_result_packets)} result packet(s) are structurally invalid: "
            + ", ".join(invalid_result_packets)
        )
        packet = build_packet(
            args=args,
            status=STATUS_HOLD_RESULT_INVALID,
            corpus=corpus,
            results_by_task=results_by_task,
            invalid_result_packets=invalid_result_packets,
            metrics=metrics,
            records=records,
            missing_result_task_ids=missing_result_task_ids,
            errors=errors,
            matched_in_corpus_count=matched_in_corpus_count,
        )
        return STATUS_HOLD_RESULT_INVALID, packet

    # 4. Determine overall status.
    # Now that the structural-invalidity gate has been handled above, the
    # only remaining reason to escalate to HOLD_RESULT_INVALID is
    # extra_results: result packets whose task_id is well-formed but not
    # in the corpus. If every well-formed result is extra, the report is
    # useless → HOLD. If at least one is in-corpus, downgrade to a warning.
    if extra_results:
        # Count each extra result (task_id not in corpus) as an error
        # regardless of whether we return HOLD_RESULT_INVALID or READY.
        metrics["error_count"] += len(extra_results)
        # Treat as HOLD_RESULT_INVALID only if every matched result is for an unknown task;
        # otherwise downgrade to a soft warning.
        if not matched_in_corpus:
            errors.append(
                f"all {len(extra_results)} result packet(s) reference unknown task_ids"
            )
            packet = build_packet(
                args=args,
                status=STATUS_HOLD_RESULT_INVALID,
                corpus=corpus,
                results_by_task=results_by_task,
                invalid_result_packets=invalid_result_packets,
                metrics=metrics,
                records=records,
                missing_result_task_ids=missing_result_task_ids,
                errors=errors,
                matched_in_corpus_count=matched_in_corpus_count,
            )
            return STATUS_HOLD_RESULT_INVALID, packet
        else:
            errors.append(
                f"{len(extra_results)} result packet(s) reference task_ids not in the corpus"
            )

    status = STATUS_READY
    packet = build_packet(
        args=args,
        status=status,
        corpus=corpus,
        results_by_task=results_by_task,
        invalid_result_packets=invalid_result_packets,
        metrics=metrics,
        records=records,
        missing_result_task_ids=missing_result_task_ids,
        errors=errors,
        matched_in_corpus_count=matched_in_corpus_count,
    )
    return status, packet


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    ok, msg = validate_args(args)
    if not ok:
        # ERROR_INVALID_ARGS — emit a minimal report that explains the failure.
        packet = {
            "packet_kind": PACKET_KIND_EVAL,
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "corpus_path": getattr(args, "corpus", "") or "",
            "corpus_id": "",
            "status": STATUS_ERROR_INVALID_ARGS,
            "task_count": 0,
            "result_count": 0,
            "matched_result_count": 0,
            "missing_result_task_ids": [],
            "invalid_result_packets": [],
            "metrics": empty_metrics(),
            "tasks": [],
            "errors": [msg],
            "recommendation": RECOMMENDATION_BY_STATUS[STATUS_ERROR_INVALID_ARGS],
        }
        try:
            write_outputs(packet, args.output_json, args.output_md)
        except OSError:
            pass
        print(msg, file=sys.stderr)
        return 2

    try:
        status, packet = evaluate(args)
    except Exception as e:  # noqa: BLE001
        packet = {
            "packet_kind": PACKET_KIND_EVAL,
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "corpus_path": args.corpus,
            "corpus_id": "",
            "status": STATUS_ERROR_TOOL_FAILURE,
            "task_count": 0,
            "result_count": 0,
            "matched_result_count": 0,
            "missing_result_task_ids": [],
            "invalid_result_packets": [],
            "metrics": empty_metrics(),
            "tasks": [],
            "errors": [f"unexpected error: {e}"],
            "recommendation": RECOMMENDATION_BY_STATUS[STATUS_ERROR_TOOL_FAILURE],
        }
        try:
            write_outputs(packet, args.output_json, args.output_md)
        except OSError:
            pass
        return 1

    write_ok, write_err = write_outputs(packet, args.output_json, args.output_md)
    if not write_ok:
        packet["errors"].append(write_err)
        packet["status"] = STATUS_ERROR_TOOL_FAILURE
        packet["recommendation"] = RECOMMENDATION_BY_STATUS[STATUS_ERROR_TOOL_FAILURE]
        try:
            Path(args.output_json).write_text(
                json.dumps(packet, indent=2, sort_keys=False) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
