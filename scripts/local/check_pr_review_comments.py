#!/usr/bin/env python3
"""
check_pr_review_comments.py

Fetch and classify GitHub PR review feedback from all relevant endpoints.
Fails closed on P0/P1 unresolved blockers; P2 blocks unless explicitly waived.

Usage:
    python3 scripts/local/check_pr_review_comments.py \
        --repo OWNER/REPO \
        --pr-number 320 \
        --reported-head-sha <sha> \
        --output-json /tmp/status.json \
        --output-md /tmp/status.md

Exit codes:
    0 = REVIEW_COMMENTS_CLEAN
    1 = REVIEW_COMMENTS_BLOCKED
    2 = REVIEW_COMMENTS_INCONCLUSIVE
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Needles and blocking words
# ---------------------------------------------------------------------------

CODEX_NEEDLES = (
    "codex",
    "chatgpt-codex",
    "p0",
    "p1",
    "p2",
    "p3",
    "badge",
    "suggestion",
    "review suggestion",
    "high",
    "medium",
)

# Words that make an unspecified or low-severity Codex comment blocking.
BLOCKING_WORDS = (
    "must fix",
    "can fail",
    "security",
    "path traversal",
    "stale",
    "malformed",
    "nonzero",
    "unsafe",
    "shell=True",
    "live claude",
    "real executor",
    "hermes mutation",
    "memory",
    "profile",
    "outside repo",
    "bypass",
    "ready false positive",
)

SEVERITY_RECORDS = {"P0": "P0", "P1": "P1", "P2": "P2", "P3": "P3"}
SEVERITY_MAP = {
    "high": "P1",
    "medium": "P2",
    "low": "P3",
}


# ---------------------------------------------------------------------------
# GitHub API helpers (list-argv, no shell=True)
# ---------------------------------------------------------------------------

def gh_api(repo: str, endpoint: str) -> tuple[bool, list[dict[str, Any]], str]:
    """
    Call `gh api` for the given endpoint (no leading slash).

    Returns (success, data_list, error_msg).
    Fails closed: any non-zero return code, stderr, or bad JSON => error.
    """
    cmd = ["gh", "api", f"repos/{repo}/{endpoint}", "--paginate"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        return False, [], f"gh invocation failed: {exc}"

    if result.returncode != 0:
        return False, [], f"gh api returned {result.returncode}: {result.stderr[:500]}"

    if not result.stdout.strip():
        return True, [], ""

    try:
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return True, data, ""
        return True, [data], ""
    except json.JSONDecodeError as exc:
        return False, [], f"invalid JSON from gh api: {exc}"


def gh_pr_view(repo: str, pr_number: int) -> tuple[bool, dict[str, Any], str]:
    """Return --json fields needed for SHA alignment check."""
    cmd = [
        "gh", "pr", "view", str(pr_number),
        "--json", "headRefOid,state,url",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except OSError as exc:
        return False, {}, f"gh pr view invocation failed: {exc}"
    if result.returncode != 0:
        return False, {}, f"gh pr view returned {result.returncode}: {result.stderr[:300]}"
    try:
        return True, json.loads(result.stdout), ""
    except json.JSONDecodeError:
        return False, {}, "gh pr view returned non-JSON"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def extract_severity(text: str) -> str | None:
    """Return P0-P3 from text or None if not found."""
    upper = text.upper()
    for sev in ("P0", "P1", "P2", "P3"):
        if sev in upper:
            return sev
    for token, sev in SEVERITY_MAP.items():
        if token.upper() in upper:
            return sev
    return None


def is_blocking(text: str) -> bool:
    """Return True if an unspecified-severity comment contains blocking words."""
    lower = text.lower()
    return any(bw in lower for bw in BLOCKING_WORDS)


def make_finding_id(
    user: str,
    file_path: str,
    line: str,
    severity: str,
    body: str,
) -> str:
    """
    Deterministic, stable finding ID derived from content fields.
    Format: codex-<12-char-sha256>
    Same finding harvested from any endpoint -> same ID.
    source_kind is NOT included so duplicate endpoints merge correctly.
    """
    normalized = re.sub(r"\s+", " ", body).strip()
    payload = "|".join([
        user, file_path, str(line), severity,
        normalized[:200],
    ])
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"codex-{digest}"


def classify_item(item: dict[str, Any], source_kind: str, ignore_users: set[str]) -> list[dict[str, Any]]:
    """
    Given a single comment/review dict from any endpoint, scan for Codex
    findings and return a list of finding dicts (may be empty).
    """
    findings = []
    user = (item.get("user") or {}).get("login", "")
    if user in ignore_users:
        return findings

    body = item.get("body") or ""
    state = item.get("state") or ""
    file_path = item.get("path") or ""
    line = item.get("line") or item.get("original_line") or ""
    commit_id = item.get("commit_id") or ""
    html_url = item.get("html_url") or item.get("url") or ""

    combined = f"{body} {user} {state} {file_path}".lower()
    if not any(needle in combined for needle in CODEX_NEEDLES):
        return findings

    # Classify severity: explicit P0-P3 tokens take priority. High/Medium/Low are
    # mapped. Only if no severity keyword is found do we check blocking words.
    severity = extract_severity(combined)
    if severity is None and is_blocking(combined):
        severity = "UNSPECIFIED_BLOCKING"
    elif severity is None:
        severity = "UNSPECIFIED_INFO"

    finding_id = make_finding_id(user, file_path, str(line), severity, body)
    finding = {
        "finding_id": finding_id,
        "user": user,
        "body": body,
        "severity": severity,
        "state": state,
        "file_path": file_path,
        "line": line,
        "commit_id": commit_id[:12] if commit_id else "",
        "url": html_url,
    }
    findings.append(finding)
    return findings


def load_waiver(path: str, pr_number: int, reported_sha: str) -> tuple[bool, dict[str, Any], str]:
    """Load and validate a waiver JSON file. Fails if SHA mismatches."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return False, {}, f"waiver file unreadable: {exc}"

    if data.get("pr_number") != pr_number:
        return False, {}, f"waiver pr_number {data.get('pr_number')} != {pr_number}"
    if data.get("reported_head_sha") != reported_sha:
        return False, {}, (
            f"waiver head SHA {data.get('reported_head_sha')} "
            f"!= reported {reported_sha}"
        )

    return True, data, ""


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedup_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove duplicate findings by finding_id.
    Same finding from different endpoints (inline_review_comment, per_review_comment,
    etc.) collapses into one entry with a 'sources' list.
    """
    merged: dict[str, dict[str, Any]] = {}
    for f in findings:
        fid = f.get("finding_id", "")
        if not fid:
            # Pre-v1: create deterministic ID from user+body
            user_str = f["user"] if isinstance(f["user"], str) else f["user"].get("login", "")
            key_payload = f"pre-v1|{user_str}|{f['body'][:200]}"
            fid = f"pre-v1-{hashlib.sha256(key_payload.encode()).hexdigest()[:12]}"

        if fid in merged:
            # Collapse duplicate: merge source endpoints
            existing = merged[fid]
            src = f.get("_source_kind", "unknown")
            if "sources" not in existing:
                existing["sources"] = [src]
            elif src not in existing["sources"]:
                existing["sources"].append(src)
            # Preserve non-empty URL if we didn't have one
            if not existing.get("url") and f.get("url"):
                existing["url"] = f["url"]
        else:
            f["sources"] = [f.get("_source_kind", "unknown")]
            merged[fid] = f

    return list(merged.values())


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_md(
    status: str,
    pr_number: int,
    reported_sha: str,
    sources: list[str],
    findings: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    waivers: list[dict[str, Any]],
    counts: dict[str, int],
) -> str:
    lines = [
        f"# PR Review Comment Gate — PR #{pr_number}\n",
        f"**Reported head SHA:** `{reported_sha}`  ",
        f"**Status:** `{status}`  ",
        f"**Harvested at:** {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}\n",
        f"## Summary\n",
        f"| Severity | Count |",
        f"|---|---|",
    ]
    for sev in ("P0", "P1", "P2", "P3", "UNSPECIFIED_BLOCKING", "UNSPECIFIED_INFO"):
        count = counts.get(sev, 0)
        lines.append(f"| {sev} | {count} |")
    lines.extend([
        f"\n**Blocked:** {counts.get('blocked', 0)}  ",
        f"**Waived:** {counts.get('waived', 0)}\n",
        f"## Sources Fetched\n",
    ])
    for src in sources:
        lines.append(f"- {src}")
    lines.append(f"\n## Findings\n")
    if not findings:
        lines.append("_No Codex/automated-review findings detected._\n")
    for f in findings:
        waiver_str = " *(waived)*" if f.get("_waived") else ""
        sev = f["severity"]
        lines.extend([
            f"### {sev} — {f['user']} @ {f['file_path']}:{f['line']}{waiver_str}\n",
            f"- URL: {f['url'] or 'N/A'}\n",
            f"- Commit: `{f['commit_id']}`\n",
            f"\n{f['body'][:2000]}\n",
        ])
    lines.append(f"\n## Blockers\n")
    if not blockers:
        lines.append("_No blockers._\n")
    else:
        for b in blockers:
            lines.append(
                f"- **[{b['severity']}]** {b['user']} — {b['file_path']}:{b['line']}  "
                f"[link]({b['url']})\n"
            )
            lines.append(f"  {b['body'][:300]}\n")
    lines.append(f"\n## P2 Waivers\n")
    if not waivers:
        lines.append("_No waivers applied._\n")
    else:
        for w in waivers:
            lines.append(
                f"- **{w['finding_id']}** ({w['severity']}): "
                f"{w['reason']}  "
                f"[expires after PR #{w.get('expires_after_pr', '?')}]\n"
            )
    lines.append(f"\n## Recommended Action\n")
    if status == "REVIEW_COMMENTS_CLEAN":
        lines.append(
            "✅ All findings resolved or waived. Safe to proceed to `final_gate_status.py`.\n"
        )
    elif status == "REVIEW_COMMENTS_BLOCKED":
        lines.append(
            "❌ Unresolved blockers remain. Fix or explicitly waive before proceeding.\n"
        )
    else:
        lines.append(
            "⚠️  Could not determine status. Review API errors and retry.\n"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

EXIT_CLEAN = 0
EXIT_BLOCKED = 1
EXIT_INCONCLUSIVE = 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and classify GitHub PR review comments."
    )
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--reported-head-sha", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument(
        "--allow-p2-waivers", default=None,
        help="Path to JSON waiver file (optional)"
    )
    parser.add_argument(
        "--fail-on-p2", action="store_true",
        help="Treat P2 as blocking even without a waiver"
    )
    parser.add_argument(
        "--ignore-users", default="",
        help="Comma-separated logins to ignore"
    )
    args = parser.parse_args()

    ignore_users = set(u.strip() for u in args.ignore_users.split(",") if u.strip())

    all_findings: list[dict[str, Any]] = []
    sources_fetched: list[str] = []
    api_errors: list[str] = []

    # 1. Issue comments
    ok, data, err = gh_api(args.repo, f"issues/{args.pr_number}/comments")
    if not ok:
        api_errors.append(f"issue_comments: {err}")
    else:
        sources_fetched.append(f"issues/{args.pr_number}/comments ({len(data)} items)")
        for item in data:
            findings = classify_item(item, "issue_comment", ignore_users)
            for f in findings:
                f["_source_kind"] = "issue_comment"
            all_findings.extend(findings)

    # 2. Inline PR review comments
    ok, data, err = gh_api(args.repo, f"pulls/{args.pr_number}/comments")
    if not ok:
        api_errors.append(f"inline_review_comments: {err}")
    else:
        sources_fetched.append(f"pulls/{args.pr_number}/comments ({len(data)} items)")
        for item in data:
            findings = classify_item(item, "inline_review_comment", ignore_users)
            for f in findings:
                f["_source_kind"] = "inline_review_comment"
            all_findings.extend(findings)

    # 3. PR reviews
    ok, data, err = gh_api(args.repo, f"pulls/{args.pr_number}/reviews")
    if not ok:
        api_errors.append(f"reviews: {err}")
    else:
        sources_fetched.append(f"pulls/{args.pr_number}/reviews ({len(data)} items)")
        for item in data:
            findings = classify_item(item, "review", ignore_users)
            for f in findings:
                f["_source_kind"] = "review"
            all_findings.extend(findings)
            # 4. Per-review comments
            rev_id = item.get("id")
            if rev_id:
                ok2, comments2, err2 = gh_api(
                    args.repo, f"pulls/{args.pr_number}/reviews/{rev_id}/comments"
                )
                if not ok2:
                    api_errors.append(f"review_{rev_id}_comments: {err2}")
                else:
                    sources_fetched.append(
                        f"pulls/{args.pr_number}/reviews/{rev_id}/comments ({len(comments2)} items)"
                    )
                    for c in comments2:
                        findings2 = classify_item(c, "per_review_comment", ignore_users)
                        for f2 in findings2:
                            f2["_source_kind"] = "per_review_comment"
                        all_findings.extend(findings2)

    all_findings = dedup_findings(all_findings)

    # Load waivers if provided
    waivers_applied: list[dict[str, Any]] = []
    waiver_map: dict[str, dict[str, Any]] = {}
    if args.allow_p2_waivers:
        ok, waiver_data, err = load_waiver(
            args.allow_p2_waivers, args.pr_number, args.reported_head_sha
        )
        if not ok:
            print(f"WAIVER FILE INVALID: {err}", file=sys.stderr)
            # Fail closed: invalid waiver => do not apply waivers
            args.allow_p2_waivers = None
        else:
            for w in waiver_data.get("waivers", []):
                waiver_map[w.get("finding_id", "")] = w

    # Mark findings as waived.
    # v0 waiver matching: by finding_id OR by (severity + first-100-chars-of-body).
    for f in all_findings:
        matched_waiver = None
        fid = f.get("finding_id", "")
        if fid in waiver_map:
            matched_waiver = waiver_map[fid]
        else:
            # Fallback: match by severity + body prefix
            sev = f["severity"]
            body_prefix = f["body"][:100].lower()
            for w in waiver_map.values():
                if (w.get("severity") == sev or w.get("severity") == "P2") and \
                        w.get("body_prefix", "").lower() == body_prefix:
                    matched_waiver = w
                    break
        if matched_waiver:
            f["_waived"] = True
            f["_waiver_reason"] = matched_waiver.get("reason", "")
            waivers_applied.append(matched_waiver)

    # Classify blockers
    blockers = []
    for f in all_findings:
        sev = f["severity"]
        if sev in ("P0", "P1", "UNSPECIFIED_BLOCKING"):
            blockers.append(f)
        elif sev == "P2":
            if args.fail_on_p2:
                blockers.append(f)
            elif not f.get("_waived"):
                blockers.append(f)
        # P3 and UNSPECIFIED_INFO are informational only

    # Counts
    counts: dict[str, int] = {k: 0 for k in (
        "P0", "P1", "P2", "P3", "UNSPECIFIED_BLOCKING", "UNSPECIFIED_INFO",
        "blocked", "waived",
    )}
    for f in all_findings:
        sev = f["severity"]
        counts[sev] = counts.get(sev, 0) + 1
    counts["blocked"] = len(blockers)
    counts["waived"] = len(waivers_applied)

    # Determine status
    if api_errors and not all_findings:
        status = "REVIEW_COMMENTS_INCONCLUSIVE"
    elif blockers:
        status = "REVIEW_COMMENTS_BLOCKED"
    elif all_findings:
        status = "REVIEW_COMMENTS_CLEAN"
    else:
        status = "REVIEW_COMMENTS_CLEAN"

    # Write outputs
    output = {
        "status": status,
        "pr_number": args.pr_number,
        "reported_head_sha": args.reported_head_sha,
        "harvested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sources_fetched": sources_fetched,
        "api_errors": api_errors,
        "findings": all_findings,
        "blockers": blockers,
        "p2_waivers": waivers_applied,
        "summary_counts": counts,
    }

    Path(args.output_json).write_text(json.dumps(output, indent=2))
    md = render_md(
        status, args.pr_number, args.reported_head_sha,
        sources_fetched, all_findings, blockers, waivers_applied, counts,
    )
    Path(args.output_md).write_text(md)

    print(f"[check_pr_review_comments] status={status} blockers={len(blockers)} "
          f"findings={len(all_findings)} waivers={len(waivers_applied)}")

    if status == "REVIEW_COMMENTS_BLOCKED":
        return EXIT_BLOCKED
    if status == "REVIEW_COMMENTS_INCONCLUSIVE":
        return EXIT_INCONCLUSIVE
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())