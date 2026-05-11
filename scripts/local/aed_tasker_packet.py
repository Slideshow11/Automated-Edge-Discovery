#!/usr/bin/env python3
"""Read-only AED Tasker packet scaffold.

Provides ROADMAP_PACKET.json v1 structure, validation helpers,
deterministic JSON output, and markdown memo rendering.

Must NOT call LLMs, mutate GitHub, create Kanban tasks, update memory,
or make network calls. Designed to be safe for any context.

This script is the OUTPUT FORMATTER AND VALIDATOR only. It does not
run a Tasker agent — future Tasker agents will emit ROADMAP_PACKET.json
files that this script validates and renders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Schema constants ──────────────────────────────────────────────────────────

PACKET_KIND = "aed.tasker.report.v1"
SCHEMA_VERSION = 1
MIN_CANDIDATES = 3
MIN_RECOMMENDED = 1
MAX_RECOMMENDED = 5

FORBIDDEN_PATHS = {
    "/home/max/.hermes",
    "hermes",
    ".hermes",
}
FORBIDDEN_DIRS = {
    "/home/max/.hermes",
}

LOCKED_MUTATION_KINDS = {"locked", "future"}
VALID_RECOMMENDATION_ACTIONS = {"defer", "blocked", "no-candidate"}


# ── Validation errors ───────────────────────────────────────────────────────────

class ValidationError(Exception):
    """Raised when a ROADMAP_PACKET fails validation."""
    pass


# ── Schema definitions ─────────────────────────────────────────────────────────

def make_empty_packet() -> dict:
    """Return an empty v1 packet skeleton with all required top-level keys.

    Schema aligns with the canonical AED Tasker/Executor design
    (docs/aed_tasker_executor_design.md section 5 ROADMAP_PACKET shape).
    """
    return {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "repo": "/home/max/Automated-Edge-Discovery",  # path string (design doc compatible)
        "base_ref": "origin/main",
        "observed_head": "",
        "generated_at": "",
        "current_state": {
            "summary": "",
            "completed_recent_prs": [],
        },
        "research_themes_reviewed": [],
        "drift_risks": [],
        "deep_module_assessment": [],
        "candidate_prs": [],
        "ranked_next_prs": [],   # design doc field name
        "do_not_build_yet": [],
        "questions_for_tom": [],
        "questions_for_chatgpt": [],
        # Extended fields for AED tooling layer (not in design doc but
        # necessary for the implementation scaffold):
        "tasker_scope": {
            "input_docs": [],
            "input_code_paths": [],
            "recent_prs_reviewed": [],
            "external_sources_reviewed": [],
            "limitations": "",
        },
        "implemented_in_code": [],
        "implemented_in_schema": [],
        "implemented_in_tests": [],
        "implemented_in_docs_only": [],
        "not_implemented": [],
        "recent_pr_lessons": [],
    }


def _flatten_fields(obj: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Recursively flatten a dict for deep comparison."""
    result = []
    for key, value in obj.items():
        field = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.extend(_flatten_fields(value, field))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    result.extend(_flatten_fields(item, f"{field}[{i}]"))
                else:
                    result.append((f"{field}[{i}]", item))
        else:
            result.append((field, value))
    return result


def validate_packet(packet: dict, *, strict: bool = True) -> list[str]:
    """Validate a parsed ROADMAP_PACKET dict.

    Args:
        packet: Parsed JSON packet.
        strict: If True, emit all errors. If False, stop at first error.

    Returns:
        List of validation error strings. Empty list means valid.

    Validation rules:
    - packet_kind must equal aed.tasker.report.v1
    - candidate_prs must have at least 3 items
    - recommended_next_prs must have at least 1 item
    - every recommended_next_prs id must exist in candidate_prs
    - candidate_id values must be unique
    - allowed_files and forbidden_files must be present for each candidate
    - no candidate may allow /home/max/.hermes
    - no candidate may allow registry/ledger mutation unless marked locked/future
    - final_recommendation must reference a valid candidate_id or recognized action
    """
    errors: list[str] = []

    # --- packet_kind ---
    if packet.get("packet_kind") != PACKET_KIND:
        errors.append(
            f"packet_kind must be '{PACKET_KIND}', got {packet.get('packet_kind')!r}"
        )

    # --- schema_version ---
    if packet.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION}, got {packet.get('schema_version')!r}"
        )

    # --- repo (string path — design doc compatible) ---
    repo = packet.get("repo", "")
    if not isinstance(repo, str) or not repo:
        errors.append("repo must be a non-empty string path")

    # --- base_ref ---
    base_ref = packet.get("base_ref", "")
    if not isinstance(base_ref, str) or not base_ref:
        errors.append("base_ref is required")

    # --- observed_head ---
    observed_head = packet.get("observed_head", "")
    if not isinstance(observed_head, str) or not observed_head:
        errors.append("observed_head is required")

    # --- generated_at ---
    gen_at = packet.get("generated_at", "")
    if not gen_at:
        errors.append("generated_at is required")
    else:
        try:
            datetime.fromisoformat(gen_at)
        except ValueError:
            errors.append(f"generated_at must be ISO-8601, got {gen_at!r}")

    # --- candidate_prs ---
    candidates = packet.get("candidate_prs", [])
    if not isinstance(candidates, list):
        errors.append("candidate_prs must be a list")
    elif len(candidates) < MIN_CANDIDATES:
        errors.append(f"candidate_prs must have at least {MIN_CANDIDATES} items, got {len(candidates)}")

    # --- ranked_next_prs (canonical name from design doc) ---
    # supported alias: recommended_next_prs (AED internal variant)
    ranked = packet.get("ranked_next_prs", [])
    alias = packet.get("recommended_next_prs", [])
    # Use ranked_next_prs if set, otherwise fall back to alias
    recommended = ranked if ranked else alias

    if not isinstance(recommended, list):
        errors.append("ranked_next_prs must be a list")
    elif len(recommended) < MIN_RECOMMENDED:
        errors.append(f"ranked_next_prs must have at least {MIN_RECOMMENDED} item(s), got {len(recommended)}")
    elif len(recommended) > MAX_RECOMMENDED:
        errors.append(f"ranked_next_prs must have at most {MAX_RECOMMENDED} items, got {len(recommended)}")

    # Candidate ID uniqueness
    seen_ids: set[str] = set()
    candidate_ids: set[str] = set()
    for i, cand in enumerate(candidates):
        if not isinstance(cand, dict):
            errors.append(f"candidate_prs[{i}] must be a dict")
            continue
        cid = cand.get("candidate_id", "")
        if not cid:
            errors.append(f"candidate_prs[{i}] missing candidate_id")
        elif cid in seen_ids:
            errors.append(f"duplicate candidate_id: {cid!r}")
        else:
            seen_ids.add(cid)
            candidate_ids.add(cid)

        # allowed_files and forbidden_files required
        if "allowed_files" not in cand:
            errors.append(f"candidate_prs[{i}] missing allowed_files")
        if "forbidden_files" not in cand:
            errors.append(f"candidate_prs[{i}] missing forbidden_files")

        # Check allowed_files for forbidden paths
        allowed = cand.get("allowed_files", [])
        if not isinstance(allowed, list):
            errors.append(f"candidate_prs[{i}].allowed_files must be a list")
        else:
            for path in allowed:
                for forbidden in FORBIDDEN_PATHS:
                    if path == forbidden or path.startswith(forbidden + "/"):
                        errors.append(
                            f"candidate_prs[{i}].allowed_files contains forbidden path: {path!r}"
                        )
                for fdir in FORBIDDEN_DIRS:
                    if path == fdir or path.startswith(fdir + "/"):
                        errors.append(
                            f"candidate_prs[{i}].allowed_files contains forbidden directory: {path!r}"
                        )

        # Registry/ledger DATA FILE mutation check.
        # Only flag writes to actual data files (ledger.jsonl, registry JSONL/CSV).
        # Paths to read-only tooling (evaluate_ledger_entry.py), design docs
        # (trial_ledger_v1_design.md), and schema files are not mutations.
        scope = cand.get("estimated_scope", {})
        mutation_mode = scope.get("registry_mutation_mode", "none") if isinstance(scope, dict) else "none"
        is_locked = mutation_mode in LOCKED_MUTATION_KINDS

        for path in allowed:
            basename = Path(path).name.lower()
            # Only actual data files count as registry/ledger mutations:
            # - ledger.jsonl (the append-only ledger data file)
            # - edge_hypothesis_registry.jsonl / .csv (the registry data file)
            is_data_file = (
                basename in ("ledger.jsonl", "ledger.jsonl.bak", "ledger.jsonl.tmp") or
                "edge_hypothesis_registry" in basename and basename.endswith((".jsonl", ".csv"))
            )
            if is_data_file and not is_locked:
                errors.append(
                    f"candidate_prs[{i}] allows registry/ledger DATA FILE mutation "
                    f"without locked/future flag: {path!r} (registry_mutation_mode={mutation_mode})"
                )

    # recommended_next_prs must reference existing candidate_ids
    for i, rec_id in enumerate(recommended):
        if rec_id not in candidate_ids:
            errors.append(
                f"recommended_next_prs[{i}]={rec_id!r} not found in candidate_prs"
            )

    # --- questions_for_tom and questions_for_chatgpt (design doc fields) ---
    for field in ("questions_for_tom", "questions_for_chatgpt"):
        val = packet.get(field, [])
        if val is not None and not isinstance(val, list):
            errors.append(f"{field} must be a list")

    # --- ranked_next_prs referencing candidate_ids ---
    # (used from alias above — already in `recommended` variable)

    # --- recent_pr_lessons (optional but if present must be well-formed) ---
    lessons = packet.get("recent_pr_lessons", [])
    if isinstance(lessons, list):
        for i, lesson in enumerate(lessons):
            if not isinstance(lesson, dict):
                errors.append(f"recent_pr_lessons[{i}] must be a dict")
                continue
            for field in ("pr_number", "title", "lesson"):
                if field not in lesson:
                    errors.append(f"recent_pr_lessons[{i}] missing {field}")

    # --- drift_risks ---
    risks = packet.get("drift_risks", [])
    if isinstance(risks, list):
        for i, risk in enumerate(risks):
            if not isinstance(risk, dict):
                errors.append(f"drift_risks[{i}] must be a dict")
                continue
            for field in ("risk", "severity"):
                if field not in risk:
                    errors.append(f"drift_risks[{i}] missing {field}")

    # --- tasker_scope ---
    scope = packet.get("tasker_scope", {})
    if not isinstance(scope, dict):
        errors.append("tasker_scope must be a dict")
    elif scope:
        # Enforce sub-field types when scope is non-empty
        for list_field in ("input_docs", "input_code_paths", "recent_prs_reviewed", "external_sources_reviewed"):
            val = scope.get(list_field)
            if val is not None and not isinstance(val, list):
                errors.append(f"tasker_scope.{list_field} must be a list if present")

    # --- current_state ---
    state = packet.get("current_state", {})
    if not isinstance(state, dict):
        errors.append("current_state must be a dict")

    # --- deep_module_assessment ---
    dmas = packet.get("deep_module_assessment", [])
    if isinstance(dmas, list):
        for i, dma in enumerate(dmas):
            if not isinstance(dma, dict):
                errors.append(f"deep_module_assessment[{i}] must be a dict")
                continue
            for field in ("module", "status"):
                if field not in dma:
                    errors.append(f"deep_module_assessment[{i}] missing {field}")

    return errors


def load_packet(path: str | Path) -> dict:
    """Load and parse a ROADMAP_PACKET JSON file."""
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        return json.loads(fh.read())


def validate_file(path: str | Path) -> tuple[int, list[str]]:
    """Validate a ROADMAP_PACKET JSON file.

    Returns:
        (exit_code, error_list). exit_code=0 if valid, 1 if invalid.
    """
    try:
        packet = load_packet(path)
    except FileNotFoundError:
        return 1, [f"file not found: {path}"]
    except json.JSONDecodeError as e:
        return 1, [f"invalid JSON: {e}"]

    errors = validate_packet(packet)
    if errors:
        return 1, errors
    return 0, []


def deterministic_dumps(packet: dict) -> str:
    """Serialize packet to JSON with stable key ordering.

    Uses sort_keys=True and a compact separator to produce
    reproducible output suitable for checksumming.
    """
    return json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


# ── Markdown rendering ─────────────────────────────────────────────────────────

def render_memo(packet: dict) -> str:
    """Render a human-readable AED_ROADMAP_TASKER_MEMO.md from a valid packet."""
    lines: list[str] = []
    indent = "  "

    def section(title: str) -> None:
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")

    def bullet(text: str) -> None:
        lines.append(f"{indent}- {text}")

    def field(label: str, value: str) -> None:
        lines.append(f"{indent}**{label}:** {value}")

    # Header
    lines.append("# AED Tasker Roadmap Memo")
    lines.append("")
    lines.append(f"> Generated: {packet.get('generated_at', 'unknown')} | "
                 f"Repo: {packet.get('repo', 'unknown')} | "
                 f"Head: {packet.get('observed_head', 'unknown')[:8]}")

    # Repo status
    repo = packet.get("repo", "")
    base_ref = packet.get("base_ref", "")
    observed_head = packet.get("observed_head", "")
    section("Repository Status")
    field("Base ref", base_ref)
    field("Observed head", observed_head[:8] if observed_head else "unknown")

    # Tasker scope
    scope = packet.get("tasker_scope", {})
    section("Tasker Scope")
    if scope.get("limitations"):
        lines.append(f"{indent}*Limitations:* {scope['limitations']}")
    if scope.get("input_docs"):
        lines.append(f"{indent}**Input docs:** {', '.join(scope['input_docs'])}")
    if scope.get("input_code_paths"):
        lines.append(f"{indent}**Input code:** {', '.join(scope['input_code_paths'])}")
    if scope.get("recent_prs_reviewed"):
        lines.append(f"{indent}**Recent PRs reviewed:** {', '.join(str(p) for p in scope['recent_prs_reviewed'])}")

    # Current state (design doc format: summary + completed_recent_prs)
    state = packet.get("current_state", {})
    section("Current AED State")
    if isinstance(state, dict):
        summary = state.get("summary", "")
        if summary:
            lines.append(f"{indent}**Summary:** {summary}")
        completed = state.get("completed_recent_prs", [])
        if completed:
            lines.append(f"{indent}**Completed recent PRs:** {', '.join(str(p) for p in completed)}")
    # Also render the AED extended state categories if present
    for category in ("implemented_in_code", "implemented_in_schema", "implemented_in_tests",
                    "implemented_in_docs_only", "not_implemented"):
        items = packet.get(category, [])
        if items:
            label = category.replace("_", " ").title()
            for item in items:
                bullet(f"**{label}:** {item}")

    # Recent PR lessons
    lessons = packet.get("recent_pr_lessons", [])
    if lessons:
        section("Recent PR Lessons")
        for lesson in lessons:
            pr = lesson.get("pr_number", "?")
            title = lesson.get("title", "?")
            text = lesson.get("lesson", "")
            impact = lesson.get("impact", "")
            lines.append(f"{indent}- **PR #{pr}** ({title})")
            lines.append(f"{indent}  {text}")
            if impact:
                lines.append(f"{indent}  *Impact:* {impact}")

    # Drift risks
    risks = packet.get("drift_risks", [])
    if risks:
        section("Drift Risks")
        for risk in risks:
            sev = risk.get("severity", "?").upper()
            text = risk.get("risk", "?")
            mit = risk.get("mitigation", "")
            sev_marker = "🔴" if sev == "HIGH" else ("🟡" if sev == "MEDIUM" else "🟢")
            lines.append(f"{indent}{sev_marker} **{sev}**: {text}")
            if mit:
                lines.append(f"{indent}  → Mitigation: {mit}")

    # Deep module assessment
    dmas = packet.get("deep_module_assessment", [])
    if dmas:
        section("Deep Module Assessment")
        for dma in dmas:
            mod = dma.get("module", "?")
            status = dma.get("status", "?")
            concern = dma.get("concern", "")
            boundary = dma.get("recommended_boundary", "")
            lines.append(f"{indent}- **{mod}** — {status}")
            if concern:
                lines.append(f"{indent}  Concern: {concern}")
            if boundary:
                lines.append(f"{indent}  Boundary: {boundary}")

    # Candidate PRs
    candidates = packet.get("candidate_prs", [])
    if candidates:
        section(f"Candidate PRs ({len(candidates)} total)")
        for cand in candidates:
            cid = cand.get("candidate_id", "?")
            title = cand.get("title", "?")
            goal = cand.get("goal", "")
            why = cand.get("why_now", "")
            scope_est = _format_scope(cand.get("estimated_scope", {}))
            deps = cand.get("depends_on", [])
            lines.append(f"{indent}**{cid}**: {title}")
            if goal:
                lines.append(f"{indent}  Goal: {goal}")
            if why:
                lines.append(f"{indent}  Why now: {why}")
            if scope_est:
                lines.append(f"{indent}  Scope: {scope_est}")
            if deps:
                lines.append(f"{indent}  Depends on: {', '.join(deps)}")

    # Recommended next PRs (ranked_next_prs is canonical; supported alias recommended_next_prs)
    ranked = packet.get("ranked_next_prs", [])
    alias = packet.get("recommended_next_prs", [])
    recommended = ranked if ranked else alias
    if recommended:
        section("Recommended Next PRs (Ranked)")
        for rank, rec_id in enumerate(recommended, 1):
            # Find the candidate title
            title = next((c.get("title", "?") for c in candidates if c.get("candidate_id") == rec_id), "?")
            lines.append(f"{indent}{rank}. **{rec_id}** — {title}")

    # Do not build yet
    dnby = packet.get("do_not_build_yet", [])
    if dnby:
        section("Do Not Build Yet")
        for item in dnby:
            lines.append(f"{indent}- **{item.get('item', '?')}**")
            reason = item.get("reason", "")
            if reason:
                lines.append(f"{indent}  Reason: {reason}")

    # Open questions (canonical fields: questions_for_tom, questions_for_chatgpt)
    tom_qs = packet.get("questions_for_tom", [])
    chatgpt_qs = packet.get("questions_for_chatgpt", [])
    if tom_qs or chatgpt_qs:
        section("Open Questions")
        if tom_qs:
            lines.append(f"{indent}**For Tom:**")
            for q in tom_qs:
                bullet(str(q))
        if chatgpt_qs:
            lines.append(f"{indent}**For ChatGPT:**")
            for q in chatgpt_qs:
                bullet(str(q))

    # Final recommendation
    final = packet.get("final_recommendation", "")
    if final:
        section("Final Recommendation")
        # Look up candidate title if it's a candidate_id
        if final in {c.get("candidate_id", "") for c in candidates}:
            title = next((c.get("title", "?") for c in candidates if c.get("candidate_id") == final), "?")
            lines.append(f"→ **{final}**: {title}")
        else:
            lines.append(f"→ **{final}**")

    lines.append("")
    lines.append("---")
    lines.append(f"*Packet kind: {packet.get('packet_kind', '?')} | "
                 f"Schema version: {packet.get('schema_version', '?')}*")

    return "\n".join(lines)


def _format_scope(scope: dict) -> str:
    """Format estimated_scope dict into a one-liner."""
    if not scope:
        return "unspecified"
    parts = []
    for key in ("files_changed", "新增代码行", "新增测试行", "risk_level"):
        if key in scope and scope[key]:
            parts.append(f"{key}={scope[key]}")
    return ", ".join(parts) if parts else "unspecified"


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only AED Tasker packet validator and memo renderer. "
                    "Must NOT call LLMs, mutate GitHub, or create Kanban tasks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    validate_parser = sub.add_parser("validate", help="Validate a ROADMAP_PACKET.json file")
    validate_parser.add_argument("file", type=str, help="Path to ROADMAP_PACKET.json")

    # render-md
    render_parser = sub.add_parser("render-md", help="Render a memo.md from a ROADMAP_PACKET.json file")
    render_parser.add_argument("file", type=str, help="Path to ROADMAP_PACKET.json")
    render_parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="Output .md file path. Defaults to stdout.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        exit_code, errors = validate_file(args.file)
        if exit_code != 0:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
        else:
            print(f"OK: {args.file} is valid", file=sys.stderr)
        return exit_code

    if args.command == "render-md":
        exit_code, errors = validate_file(args.file)
        if exit_code != 0:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return exit_code

        packet = load_packet(args.file)
        memo = render_memo(packet)

        if args.output:
            Path(args.output).write_text(memo + "\n", encoding="utf-8")
            print(f"Memo written to {args.output}", file=sys.stderr)
        else:
            print(memo)
        return 0

    return 1  # unreachable — required=True on sub


if __name__ == "__main__":
    raise SystemExit(main())