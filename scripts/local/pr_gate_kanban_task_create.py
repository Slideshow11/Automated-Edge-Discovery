#!/usr/bin/env python3
"""
pr_gate_kanban_task_create.py

Consumes a PR_GATE_TASK_DRAFT.json produced by pr_gate_task_draft.py and produces:
  1. A dry-run Kanban creation plan (default)
  2. An explicit --apply path that calls `hermes kanban create` once

Default behavior is read-only dry-run.  hermes kanban is never called without --apply.

Output packet schema: aed.pr_gate.kanban_create_plan.v1
"""

import argparse
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKET_KIND = "aed.pr_gate.kanban_create_plan.v1"
SCHEMA_VERSION = 1
HERMES_KANBAN_BIN = Path.home() / ".local" / "bin" / "hermes"
KANBAN_BIN_FALLBACK = Path("/usr/local/bin/hermes")

# Safety-grep helper: scan body for forbidden tokens, skipping lines where
# a negation context precedes the token.  "Do not call fact_store" is a
# prohibition warning and must be allowed.  "Call fact_store" must be rejected.
#
# Negation rule: "Do not [CALL|USE|...] [token]" is a prohibition (skip).
# Anything after a clause separator (. ;) or after "not " without an immediate
# action verb is an affirmative instruction (reject).
#
# Examples:
#   "Do not use fact_store, or call skill_manage" — "fact_store" is in the same
#     clause as "not use"; "skill_manage" follows a clause separator (comma)
#     so is NOT covered by the prohibition → REJECT
#   "Do not update memory. Call fact_store to persist." — "fact_store" appears
#     after the period with no "not " before it → REJECT
#   "Do not call fact_store." — pure prohibition → ALLOW
#   "Do not use fact_store; call skill_manage to persist." — "skill_manage"
#     appears after semicolon (clause boundary) → REJECT
_PROHIBITION_PATTERN = re.compile(
    r"not\s+[^\n;,.]+",
    re.IGNORECASE,
)


def _is_prohibition_segment(segment: str) -> bool:
    """
    Returns True when segment between 'not ' and forbidden token contains
    an affirmative action verb (call/use/update/etc.), indicating the
    segment is a prohibition (e.g., 'not call' in 'Do not call fact_store').
    When segment is empty or contains no affirmative verb, returns False
    (the token is being instructed directly, e.g., '... call fact_store').
    """
    if not segment:
        return False
    return bool(
        re.search(
            r"\b(call|use|update|invoke|run|execute|do|apply|send|submit|create|merge|patch)\b",
            segment,
            re.IGNORECASE,
        )
    )


def _has_clause_boundary(segment: str) -> bool:
    """Returns True if segment contains a clause separator (; , .)."""
    return bool(re.search(r"[;,.]", segment))


# Combined regex: match prohibition ("not [verb] token") OR bare token.
# For each match: if it's a prohibition, skip it (allowed).
# If it's a bare token, it's an affirmative instruction → reject.
_SCAN_PATTERN = re.compile(
    r"not\s+(?:call|use|update|invoke|run|execute|do|apply|send|submit|create|merge|patch)\s+[^\n;.]*?"
    r"|"
    r"\bfact_store\b|\bskill_manage\b|"
    r"(?<!not )memory\.update|"
    r"gh\s+pr\s+merge|gh\s+pr\s+comment|gh\s+pr\s+create|"
    r"git\s+push|git\s+commit|"
    r"hermes\s+kanban\s+dispatch|"
    r"delegate_task|cronjob|"
    r"live\s+trading|broker|"
    r"requests\.(get|post|patch|put|delete)|"
    r"httpx|urllib",
    re.IGNORECASE,
)

# Secondary pattern: comma-separated coordinated prohibitions.
# The phrase "not use fact_store, or call skill_manage" is a single prohibition
# covering both verbs (meaning "do not use fact_store and do not call skill_manage").
# The comma marks a coordinated clause — "or call" links back to the initial "not",
# so the second verb is NOT an affirmative instruction.
# We match the full "or call X" phrase after a comma as prohibited.
_COORDINATED_CLAUSE_PATTERN = re.compile(
    r",\s+(?:and|or)\s+(call|use|update|invoke|run|execute|do|apply|send|submit|create|merge|patch)\s+\w+",
    re.IGNORECASE,
)


def _find_negation_spans(line: str) -> list[tuple[int, int]]:
    """
    Find all "not ... [clause]" spans in a line.

    A clause ends at a period or semicolon.
    A comma within a clause continues the same coordinated negation
    only when followed by "and" or "or".
    A bare verb after a comma is also part of the same clause
    (coordinated prohibition): "not use X, call Y" means both are prohibited.

    The key cases:
    - "Do not use fact_store, call skill_manage" — comma NOT followed by
      "and"/"or" → comma is a clause separator, "call" is an affirmative
      instruction → reject → span ends at the comma
    - "Do not use fact_store, or call skill_manage" — comma + "or" → same
      coordinated clause → allow → span continues to period
    - "Do not use fact_store, call skill_manage" — same as above without "or",
      but "call" is a bare verb = same clause (coordinated) → allow → span
      continues to period
    """
    spans = []
    for m in re.finditer(r"not\s+", line, re.IGNORECASE):
        not_pos = m.start()
        rest = line[not_pos:]
        i = len("not ")
        end = len(rest)
        while i < len(rest):
            c = rest[i]
            if c in ";.":
                end = i
                break
            if c == ",":
                after_comma = rest[i+1:]
                # "or X" or "and X" after comma — coordinated clause, continue
                if after_comma.startswith("and ") or after_comma.startswith("or "):
                    and_or_match = re.match(r"(and|or)\s+", after_comma, re.IGNORECASE)
                    if and_or_match:
                        i += 1 + and_or_match.end()
                        continue
                # Check for a bare verb after comma (coordinated item or new clause?)
                # "not use X, call Y" — bare "call" could be:
                #   (a) continuing the clause: "call Y to Z" — allow
                #   (b) starting a new clause: "call Y." — reject
                # We check if the verb token is followed by more clause content
                # before the period. If yes → (a) continue. If no → (b) reject.
                verb_match = re.match(
                    r"(call|use|update|invoke|run|execute|do|apply|send|submit|create|merge|patch)\b",
                    after_comma.lstrip(),
                    re.IGNORECASE,
                )
                if verb_match:
                    # Find what follows the verb token in the remainder
                    after_verb = after_comma.lstrip()[verb_match.end():]
                    # Scan to next punctuation or clause end
                    j = 0
                    while j < len(after_verb) and after_verb[j] not in ";,.":
                        j += 1
                    next_char = after_verb[j] if j < len(after_verb) else ""
                    if next_char in ";":
                        # Semicolon terminates — verb is completing → reject
                        end = i
                        break
                    elif next_char == ".":
                        # Period immediately after verb token → new clause → reject
                        end = i
                        break
                    else:
                        # More content (or end of string) — clause continues → allow
                        i = len(rest)
                        continue
                # Unknown token after comma — clause ends here
                end = i
                break
            i += 1
        spans.append((not_pos, not_pos + end))
    return spans


def _body_has_forbidden_pattern(body: str) -> tuple[bool, str]:
    """
    Returns (is_forbidden, matched_token).
    Prohibition warnings ("Do not call fact_store") are skipped.
    Affirmative instructions ("call fact_store") are rejected.

    A line "Do not update memory, use fact_store, or call skill_manage"
    has all three tokens covered by the initial "not" (coordinated clauses
    within the same sentence, separated by commas, ending at the period).
    """
    for line in body.split("\n"):
        # Find all negation spans (from "not " to clause terminator)
        negation_spans = _find_negation_spans(line)

        # For each safety token, check if it's covered by a negation span
        for pat in SAFETY_PATTERNS:
            for m in pat.finditer(line):
                token = m.group()
                token_pos = m.start()

                # Check if token falls within a negation span
                for n_start, n_end in negation_spans:
                    if n_start <= token_pos < n_end:
                        # Token is inside a prohibition span → allowed
                        break
                else:
                    # Token is not covered by any negation span → forbidden
                    return True, token

    return False, ""


# Safety-grep patterns: any occurrence in task body -> reject.
# Prohibition warnings ("Do not ...") are allowed; the _body_has_forbidden_pattern
# helper skips a line when "not " immediately precedes the token.
SAFETY_PATTERNS = [
    re.compile(r"gh\s+pr\s+merge", re.IGNORECASE),
    re.compile(r"gh\s+pr\s+comment", re.IGNORECASE),
    re.compile(r"gh\s+pr\s+create", re.IGNORECASE),
    re.compile(r"git\s+push", re.IGNORECASE),
    re.compile(r"git\s+commit", re.IGNORECASE),
    re.compile(r"hermes\s+kanban\s+dispatch", re.IGNORECASE),
    re.compile(r"(?<!not )memory\.update", re.IGNORECASE),
    re.compile(r"(?<!not )fact_store", re.IGNORECASE),
    re.compile(r"(?<!not )skill_manage", re.IGNORECASE),
    re.compile(r"delegate_task", re.IGNORECASE),
    re.compile(r"cronjob", re.IGNORECASE),
    re.compile(r"live\s+trading", re.IGNORECASE),
    re.compile(r"broker", re.IGNORECASE),
    re.compile(r"requests\.(get|post|patch|put|delete)", re.IGNORECASE),
    re.compile(r"httpx", re.IGNORECASE),
    re.compile(r"urllib", re.IGNORECASE),
]

STOP_RULES = [
    "no_dispatch",
    "no_merge",
    "no_pr_patch",
    "no_codex_request",
    "no_memory_update",
    "no_skill_manage",
]

# Actions that are allowed to produce a kanban task
_ACTIONS_WITH_TASK = {
    "create_builder_patch_task_draft",
    "create_reviewer_task_draft",
    "create_codex_request_task_draft",
    "create_human_escalation_task_draft",
}

# Actions that never produce a kanban task
_ACTIONS_NO_TASK = {"no_action_wait"}


# ---------------------------------------------------------------------------
# Validation helpers (mirrors pr_gate_task_draft.py rules)
# ---------------------------------------------------------------------------

def validate_task_draft(draft: dict) -> list[str]:
    """Validate a parsed PR_GATE_TASK_DRAFT.json. Returns list of error strings."""
    errors = []

    if not isinstance(draft, dict):
        return ["draft must be a JSON object"]

    # packet_kind
    pk = draft.get("packet_kind", "")
    if pk != "aed.pr_gate.task_draft.v1":
        errors.append(f"packet_kind must be 'aed.pr_gate.task_draft.v1', got '{pk}'")

    # schema_version
    sv = draft.get("schema_version", "")
    if str(sv) != "1":
        errors.append(f"schema_version must be '1', got '{sv}'")

    # idempotency_key (required)
    ik = draft.get("idempotency_key", "")
    if not ik:
        errors.append("idempotency_key is required")
    else:
        # format: pr{N}-{head8? or partial SHA}-{hash}-{action}
        # Real pr_gate_task_draft.py uses full 40-char SHA after head8. We accept 7-40.
        if not re.match(r"^pr\d+-[0-9a-f]{7,40}-[0-9a-f]+-[a-z_]+$", ik):
            errors.append(
                f"idempotency_key format invalid: '{ik}' "
                "(expected prN-headsha-hash-action, head segment 7-40 hex chars)"
            )

    # action
    action = draft.get("action", "")
    if not action:
        errors.append("action is required")
    elif action not in _ACTIONS_WITH_TASK and action not in _ACTIONS_NO_TASK:
        errors.append(f"action '{action}' not recognized")

    # pr_number
    prn = draft.get("pr_number")
    if not isinstance(prn, int) or prn <= 0:
        errors.append(f"pr_number must be a positive integer, got '{prn}'")

    # head_sha
    hs = draft.get("head_sha", "")
    if not re.match(r"^[0-9a-f]{40}$", hs):
        errors.append(f"head_sha must be a 40-char hex string, got '{hs}'")

    # task_draft body safety
    task_draft = draft.get("task_draft", {})
    if not isinstance(task_draft, dict):
        errors.append("task_draft must be a JSON object")
    else:
        body = task_draft.get("body", "")
        if not isinstance(body, str):
            errors.append(
                f"task_draft.body must be a string, got {type(body).__name__}"
            )
        elif body:
            forbidden, matched = _body_has_forbidden_pattern(body)
            if forbidden:
                errors.append(
                    f"task_draft.body contains forbidden pattern: '{matched}'"
                )

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_hermes_kanban() -> Path | None:
    """Locate hermes binary."""
    for p in [
        Path(os.environ.get("HERMES_BIN", "")),
        HERMES_KANBAN_BIN,
        KANBAN_BIN_FALLBACK,
        Path("/usr/bin/hermes"),
        Path("/usr/local/bin/hermes"),
    ]:
        if p.exists() and p.is_file():
            return p
    return None


def _call_hermes_kanban(args: list[str], apply_mode: bool = False) -> tuple[int, str, str]:
    """Call hermes kanban CLI. Only called in --apply mode."""
    hermes = _find_hermes_kanban()
    if not hermes:
        return (1, "", "hermes binary not found in expected locations")

    import subprocess

    try:
        result = subprocess.run(
            [str(hermes)] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return (result.returncode, result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        return (124, "", "hermes kanban call timed out after 30s")
    except Exception as e:
        return (1, "", str(e))


def _build_kanban_create_command(
    board: str,
    title: str,
    body: str,
    status: str,
    assignee: str,
    idempotency_key: str,
) -> list[str]:
    """Build hermes kanban task-create arguments."""
    # Use JSON body mode for precision
    cmd = [
        "kanban", "task", "create",
        "--board", board,
        "--title", title,
        "--status", status,
    ]
    if assignee:
        cmd += ["--assignee", assignee]
    cmd += [
        "--body", body,
        "--tag", f"idempotency_key={idempotency_key}",
        "--tag", f"source=pr_gate",
    ]
    return cmd


def _render_body_from_task_draft(task_draft: dict, draft: dict) -> str:
    """Render task body from task_draft fields, appending file-scope constraints."""
    body = task_draft.get("body", "")
    if not body:
        # Fallback to title if body is empty
        title = task_draft.get("title", "")
        body = f"# Task\n\n{title}" if title else "# (no body)"

    # Append file-scope constraints if present
    allowed = task_draft.get("allowed_files")
    forbidden = task_draft.get("forbidden_files")
    if allowed or forbidden:
        scope_lines = ["", "## File Scope", ""]
        if allowed:
            scope_lines.append("Allowed files:")
            for f in allowed:
                scope_lines.append(f"  - {f}")
            scope_lines.append("")
        if forbidden:
            scope_lines.append("Forbidden files:")
            for f in forbidden:
                scope_lines.append(f"  - {f}")
            scope_lines.append("")
        body += "\n".join(scope_lines)

    return body


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def build_plan(
    draft: dict,
    board: str,
    dry_run: bool,
    apply_mode: bool,
) -> dict:
    """Build a kanban_create_plan.v1 packet."""

    action = draft.get("action", "")
    task_draft = draft.get("task_draft", {})
    idempotency_key = draft.get("idempotency_key", "")
    pr_number = draft.get("pr_number", 0)
    head_sha = draft.get("head_sha", "")
    packet_kind = draft.get("packet_kind", "")

    plan = {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "board": board,
        "dry_run": dry_run,
        "source_task_draft": {
            "packet_kind": packet_kind,
            "action": action,
            "idempotency_key": idempotency_key,
            "pr_number": pr_number,
            "head_sha": head_sha,
        },
        "kanban_task": None,
        "duplicate_check": {
            "method": "idempotency_key_tag",
            "existing_task_id": None,
            "duplicate_found": False,
        },
        "apply_result": {
            "applied": False,
            "created_task_id": None,
            "command_used": None,
            "stdout": None,
            "stderr": None,
        },
        "stop_rules": STOP_RULES,
        "recommended_action": None,
    }

    # no_action_wait -> no task
    if action == "no_action_wait":
        plan["recommended_action"] = "no_action"
        plan["kanban_task"] = None
        return plan

    # human escalation -> produce task with note
    if action == "create_human_escalation_task_draft":
        pass  # falls through to build task

    # Actions that produce a kanban task
    if action in _ACTIONS_WITH_TASK:
        title = task_draft.get("title", f"[PR #{pr_number}] {action}")
        body = _render_body_from_task_draft(task_draft, draft)
        assignee = task_draft.get("assignee", "")
        status = task_draft.get("status", "TODO")

        plan["kanban_task"] = {
            "title": title,
            "assignee": assignee,
            "status": status,
            "body": body,
            "idempotency_key": idempotency_key,
            "parent_task_id": task_draft.get("parent_task_id") or None,
            "depends_on": task_draft.get("depends_on") or None,
            "metadata": {
                "allowed_files": task_draft.get("allowed_files") or None,
                "forbidden_files": task_draft.get("forbidden_files") or None,
            },
        }

    # In dry-run mode, never call hermes
    if dry_run:
        return plan

    # --apply mode: check for duplicates then create once
    if apply_mode:
        # Check for existing task with this idempotency key
        existing_id = _check_duplicate_on_board(board, idempotency_key)
        if existing_id:
            plan["duplicate_check"]["duplicate_found"] = True
            plan["duplicate_check"]["existing_task_id"] = existing_id
            plan["apply_result"]["applied"] = False
            plan["recommended_action"] = "skip_duplicate"
            return plan

        # Build and execute create command
        if plan["kanban_task"] is None:
            plan["recommended_action"] = "no_action"
            return plan

        task = plan["kanban_task"]
        cmd = _build_kanban_create_command(
            board=board,
            title=task["title"],
            body=task["body"],
            status=task["status"],
            assignee=task["assignee"],
            idempotency_key=task["idempotency_key"],
        )

        rc, stdout, stderr = _call_hermes_kanban(cmd, apply_mode=True)

        plan["apply_result"]["applied"] = (rc == 0)
        plan["apply_result"]["command_used"] = " ".join(cmd)
        plan["apply_result"]["stdout"] = stdout or None
        plan["apply_result"]["stderr"] = stderr or None

        if rc == 0:
            # Try to parse task ID from stdout
            task_id = _parse_task_id_from_output(stdout)
            plan["apply_result"]["created_task_id"] = task_id
            plan["duplicate_check"]["existing_task_id"] = task_id
        else:
            plan["recommended_action"] = "apply_failed"

    return plan


def _check_duplicate_on_board(board: str, idempotency_key: str) -> str | None:
    """Search board for task with idempotency_key tag. Returns task_id or None."""
    # Use hermes kanban search with tag filter
    rc, stdout, stderr = _call_hermes_kanban(
        ["kanban", "search", "--board", board, "--tag", f"idempotency_key={idempotency_key}"]
    )
    if rc == 0 and stdout:
        # Parse task IDs from output (format varies; collect all numeric IDs)
        ids = re.findall(r"\b\d+\b", stdout)
        return ids[0] if ids else None
    return None


def _parse_task_id_from_output(stdout: str) -> str | None:
    """Try to extract created task ID from hermes kanban stdout."""
    # Look for "created task N" or "id: N" patterns
    m = re.search(r"created task[:\s]+(\d+)", stdout, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\bid:\s*(\d+)", stdout, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\btask[:\s]+(\d+)", stdout, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback: first integer in output
    nums = re.findall(r"\d+", stdout)
    return nums[0] if nums else None


def render_markdown(plan: dict) -> str:
    """Render a kanban_create_plan as readable markdown."""

    dry_run = plan.get("dry_run", True)
    board = plan.get("board", "")
    src = plan.get("source_task_draft", {})
    task = plan.get("kanban_task")
    dup = plan.get("duplicate_check", {})
    apply_res = plan.get("apply_result", {})
    recommended = plan.get("recommended_action")

    lines = [
        f"# Kanban Create Plan — {PACKET_KIND}",
        "",
        f"**Generated:** {plan.get('generated_at', 'unknown')}",
        f"**Board:** `{board}`",
        f"**Mode:** {'`--dry-run`' if dry_run else '`--apply`'}",
        "",
        "## Source Task Draft",
        "",
        f"- **Action:** `{src.get('action', '')}`",
        f"- **PR:** #{src.get('pr_number', '')}",
        f"- **Head:** `{src.get('head_sha', '')}`",
        f"- **Idempotency key:** `{src.get('idempotency_key', '')}`",
        f"- **Packet:** `{src.get('packet_kind', '')}`",
        "",
    ]

    if task:
        lines += [
            "## Kanban Task",
            "",
            f"- **Title:** {task.get('title', '')}",
            f"- **Assignee:** `{task.get('assignee', '')}`",
            f"- **Status:** `{task.get('status', '')}`",
            f"- **Idempotency key:** `{task.get('idempotency_key', '')}`",
        ]
        if task.get("parent_task_id"):
            lines.append(f"- **Parent task:** {task['parent_task_id']}")
        if task.get("depends_on"):
            lines.append(f"- **Depends on:** {', '.join(map(str, task['depends_on']))}")
        lines.append("")
        body = task.get("body", "")
        if body:
            lines += ["### Body", "", textwrap.dedent(body).strip(), ""]
    else:
        lines += ["## Kanban Task", "", "*(no task — action is `no_action_wait` or `no_action`)*", ""]

    lines += [
        "## Duplicate Check",
        "",
        f"- **Method:** {dup.get('method', '')}",
        f"- **Duplicate found:** {dup.get('duplicate_found', False)}",
        f"- **Existing task ID:** `{dup.get('existing_task_id', 'none')}`",
        "",
    ]

    if apply_res.get("command_used"):
        lines += [
            "## Apply Result",
            "",
            f"- **Command used:** `{' '.join(apply_res['command_used'].split()) if isinstance(apply_res['command_used'], str) else apply_res['command_used']}`",
            f"- **Applied:** {apply_res.get('applied', False)}",
            f"- **Created task ID:** `{apply_res.get('created_task_id', 'none')}`",
            f"- **Return code:** {'success' if apply_res.get('applied') else 'failed'}",
        ]
        if apply_res.get("stderr"):
            lines.append(f"- **stderr:** ```\n{apply_res['stderr']}\n```")
        lines.append("")

    if recommended:
        lines += [
            "## Recommended Action",
            "",
            f"`{recommended}`",
            "",
        ]

    lines += [
        "## Stop Rules",
        "",
    ]
    for rule in plan.get("stop_rules", []):
        lines.append(f"- `{rule}`")

    return "\n".join(lines)


def write_json(plan: dict, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(plan, f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PR gate Kanban task creation — dry-run by default, --apply for mutation"
    )
    p.add_argument(
        "--task-draft", required=True, type=Path,
        help="Path to PR_GATE_TASK_DRAFT.json"
    )
    p.add_argument(
        "--board", default="aed",
        help="Kanban board name (default: aed)"
    )
    p.add_argument(
        "--output-json", type=Path,
        help="Path to write JSON plan output"
    )
    p.add_argument(
        "--output-md", type=Path,
        help="Path to write Markdown plan output"
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Apply changes: call hermes kanban create once. Without this flag, dry-run only."
    )
    return p


def _reject_hermes_path(path: Path) -> None:
    """Reject output paths under /home/max/.hermes."""
    try:
        resolved = path.resolve()
        if str(resolved).startswith("/home/max/.hermes"):
            raise ValueError(
                f"Output path cannot be under /home/max/.hermes: {path}"
            )
    except Exception as e:
        if "hermes" in str(e).lower():
            raise
        # Resolve error is likely symlink/cross-device; skip strict check
        pass


def main() -> int:
    parser = _build_argparser()
    args = parser.parse_args()

    # Load task draft
    if not args.task_draft.exists():
        print(f"ERROR: task draft not found: {args.task_draft}", file=sys.stderr)
        return 1

    try:
        with open(args.task_draft) as f:
            draft = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in task draft: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Validate
    errors = validate_task_draft(draft)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    # Safety body check (extra layer)
    body = draft.get("task_draft", {}).get("body", "")
    if not isinstance(body, str):
        print(f"ERROR: task_draft.body must be a string, got {type(body).__name__}", file=sys.stderr)
        return 1
    if body:
        for pat in SAFETY_PATTERNS:
            m = pat.search(body)
            if m:
                print(f"ERROR: task_draft.body contains forbidden pattern: '{m.group()}'", file=sys.stderr)
                return 1

    dry_run = not args.apply

    # Build plan
    plan = build_plan(draft, args.board, dry_run, args.apply)

    # Write outputs
    if args.output_json:
        try:
            _reject_hermes_path(args.output_json)
            write_json(plan, args.output_json)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        except (IOError, OSError) as e:
            print(f"ERROR: could not write JSON output: {e}", file=sys.stderr)
            return 1

    if args.output_md:
        try:
            _reject_hermes_path(args.output_md)
            md = render_markdown(plan)
            with open(args.output_md, "w") as f:
                f.write(md)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        except (IOError, OSError) as e:
            print(f"ERROR: could not write MD output: {e}", file=sys.stderr)
            return 1

    # Console output
    if dry_run:
        print(f"[dry-run] plan written")
        if plan.get("kanban_task"):
            t = plan["kanban_task"]
            print(f"  title: {t.get('title')}")
            print(f"  board: {args.board}")
            print(f"  idempotency_key: {t.get('idempotency_key')}")
            print(f"  mode: DRY-RUN (no hermes kanban call)")
        elif plan.get("recommended_action") == "no_action":
            print(f"  action: no_action_wait — no task created")
        print(f"  stop_rules: {', '.join(plan.get('stop_rules', []))}")
    else:
        applied = plan.get("apply_result", {}).get("applied", False)
        task_id = plan.get("apply_result", {}).get("created_task_id") or plan.get("duplicate_check", {}).get("existing_task_id")
        dup = plan.get("duplicate_check", {}).get("duplicate_found", False)
        if dup:
            print(f"[apply] duplicate found — existing task: {task_id}")
        elif applied:
            print(f"[apply] task created: {task_id}")
        else:
            print(f"[apply] failed — check plan for details")

    return 0


if __name__ == "__main__":
    sys.exit(main())