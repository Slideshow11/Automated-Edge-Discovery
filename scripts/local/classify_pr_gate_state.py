#!/usr/bin/env python3
"""Read-only GitHub PR gate-state classifier for AED.

This tool reads GitHub/repository state and prints a structured JSON packet. It
must not post comments, request Codex, create Kanban tasks, patch PRs, or merge.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLASSIFICATIONS = {
    "blocked_scope",
    "blocked_pr_closed",
    "blocked_pr_merged",
    "blocked_wrong_base",
    "ci_pending",
    "ci_failed",
    "codex_request_needed",
    "codex_pending",
    "codex_suggestions",
    "codex_clean",
    "ready_for_reviewer",
    "unknown",
}

CLEAN_PATTERNS = (
    "didn't find any major issues",
    "did not find any major issues",
    "didn't find blocking issues",
    "did not find blocking issues",
    "didn't find any blocking issues",
    "did not find any blocking issues",
    "no major issues",
    "no findings",
    "no actionable issues",
)
SUGGESTION_PATTERNS = (
    "automated review suggestions",
    "review suggestions",
    "codex review\n\nhere are",
)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def flatten_allowed_files(values: list[str]) -> list[str]:
    files: list[str] = []
    for value in values:
        for part in value.split(","):
            item = part.strip()
            if item:
                files.append(item)
    return sorted(dict.fromkeys(files))


def _user_login(item: dict[str, Any]) -> str:
    user = item.get("user") or {}
    return str(user.get("login") or "")


def _body(item: dict[str, Any]) -> str:
    return str(item.get("body") or "")


def _contains_current_head(text: str, head_sha: str) -> bool:
    return bool(head_sha and (head_sha in text or head_sha[:10] in text or head_sha[:12] in text))


def _signal(source: str, item: dict[str, Any], created_key: str, body: str, reviewed_head: str | None = None) -> dict[str, Any]:
    return {
        "source": source,
        "id": item.get("id"),
        "user": _user_login(item),
        "created_at": item.get(created_key),
        "reviewed_head": reviewed_head,
        "body_excerpt": body[:300],
    }


def classify_ci(check_runs: list[dict[str, Any]]) -> tuple[str, list[str]]:
    blockers: list[str] = []
    has_pending = False
    has_failed = False
    passing_conclusions = {"success", "neutral", "skipped"}
    if not check_runs:
        return "pending", ["No CI check runs were found for the current head."]
    for run in check_runs:
        status = run.get("status")
        conclusion = run.get("conclusion")
        name = run.get("name") or "<unnamed>"
        if status != "completed":
            has_pending = True
            blockers.append(f"CI check pending: {name} status={status}")
        elif conclusion not in passing_conclusions:
            has_failed = True
            blockers.append(f"CI check failed: {name} conclusion={conclusion}")
    if has_failed:
        return "failed", blockers
    if has_pending:
        return "pending", blockers
    return "green", []


def classify_codex(
    *,
    head_sha: str,
    head_pushed_at: datetime | None,
    issue_comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    codex_bot_login: str,
    latest_request_reactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bot = codex_bot_login.lower()
    latest_request: dict[str, Any] | None = None
    latest_request_at: datetime | None = None
    latest_reviewed_head: str | None = None

    for comment in sorted(issue_comments, key=lambda c: str(c.get("created_at") or "")):
        created_at = parse_time(comment.get("created_at"))
        body = _body(comment)
        body_l = body.lower()
        if "@codex review" not in body_l:
            continue
        after_head = head_pushed_at is not None and created_at is not None and created_at >= head_pushed_at
        mentions_head = _contains_current_head(body, head_sha)
        if mentions_head or after_head:
            latest_request = comment
            latest_request_at = created_at

    signals: list[tuple[datetime, dict[str, Any], str]] = []

    for review in reviews:
        if _user_login(review).lower() != bot:
            continue
        body = _body(review)
        body_l = body.lower()
        state = str(review.get("state") or "")
        # DISMISSED reviews must not be accepted as any signal
        if state == "DISMISSED":
            continue
        commit_id = review.get("commit_id")
        if commit_id:
            latest_reviewed_head = str(commit_id)
        if commit_id != head_sha:
            continue
        submitted_at = parse_time(review.get("submitted_at"))
        if submitted_at is None:
            continue
        if latest_request_at is not None and submitted_at < latest_request_at:
            continue
        kind = None
        if any(pattern in body_l for pattern in CLEAN_PATTERNS):
            kind = "clean"
        elif any(pattern in body_l for pattern in SUGGESTION_PATTERNS):
            kind = "suggestions"
        if kind:
            signals.append((submitted_at, _signal("pr_review", review, "submitted_at", body, str(commit_id)), kind))

    for comment in issue_comments:
        created_at = parse_time(comment.get("created_at"))
        body = _body(comment)
        body_l = body.lower()
        login = _user_login(comment).lower()
        if login != bot or latest_request is None or created_at is None:
            continue
        if latest_request_at is not None and created_at < latest_request_at:
            continue
        kind = None
        if any(pattern in body_l for pattern in CLEAN_PATTERNS):
            kind = "clean"
        elif any(pattern in body_l for pattern in SUGGESTION_PATTERNS):
            kind = "suggestions"
        if kind:
            signals.append((created_at, _signal("issue_comment", comment, "created_at", body, head_sha), kind))

    if signals:
        _at, signal, kind = sorted(signals, key=lambda item: item[0])[-1]
        latest_reviewed_head = signal.get("reviewed_head") or latest_reviewed_head
        if kind == "suggestions":
            codex_reaction_status = None
            codex_latest_request_acknowledged = None
            codex_latest_request_acknowledged_at = None
            if latest_request_reactions:
                for reaction in latest_request_reactions:
                    reaction_user = str(reaction.get("user", {}).get("login") or "").lower()
                    if str(reaction.get("content")) in ("+1", "eyes") and reaction_user == bot:
                        codex_reaction_status = "acknowledged_pending"
                        codex_latest_request_acknowledged = True
                        codex_latest_request_acknowledged_at = reaction.get("created_at")
                        break

            return {
                "codex_status": "suggestions",
                "classification": "codex_suggestions",
                "latest_reviewed_head": latest_reviewed_head,
                "clean_signal": None,
                "suggestions": signal,
                "request": latest_request,
                "codex_latest_request_acknowledged": codex_latest_request_acknowledged,
                "codex_latest_request_acknowledged_at": codex_latest_request_acknowledged_at,
                "codex_reaction_status": codex_reaction_status,
                "uncertainty": [],
            }
        codex_reaction_status = None
        codex_latest_request_acknowledged = None
        codex_latest_request_acknowledged_at = None
        if latest_request_reactions:
            for reaction in latest_request_reactions:
                reaction_user = str(reaction.get("user", {}).get("login") or "").lower()
                if str(reaction.get("content")) in ("+1", "eyes") and reaction_user == bot:
                    codex_reaction_status = "acknowledged_pending"
                    codex_latest_request_acknowledged = True
                    codex_latest_request_acknowledged_at = reaction.get("created_at")
                    break

        return {
            "codex_status": "clean",
            "classification": "ready_for_reviewer",
            "latest_reviewed_head": latest_reviewed_head or head_sha,
            "clean_signal": signal,
            "suggestions": None,
            "request": latest_request,
            "codex_latest_request_acknowledged": codex_latest_request_acknowledged,
            "codex_latest_request_acknowledged_at": codex_latest_request_acknowledged_at,
            "codex_reaction_status": codex_reaction_status,
            "uncertainty": [],
        }

    codex_reaction_status = None
    codex_latest_request_acknowledged = None
    codex_latest_request_acknowledged_at = None
    if latest_request_reactions:
        for reaction in latest_request_reactions:
            reaction_user = str(reaction.get("user", {}).get("login") or "").lower()
            if str(reaction.get("content")) in ("+1", "eyes") and reaction_user == bot:
                codex_reaction_status = "acknowledged_pending"
                codex_latest_request_acknowledged = True
                codex_latest_request_acknowledged_at = reaction.get("created_at")
                break

    if latest_request is not None:
        return {
            "codex_status": "pending",
            "classification": "codex_pending",
            "latest_reviewed_head": latest_reviewed_head,
            "clean_signal": None,
            "suggestions": None,
            "request": latest_request,
            "codex_latest_request_acknowledged": codex_latest_request_acknowledged,
            "codex_latest_request_acknowledged_at": codex_latest_request_acknowledged_at,
            "codex_reaction_status": codex_reaction_status,
            "uncertainty": ["A current-head @codex review request exists, but no later Codex bot response was found."],
        }
    return {
        "codex_status": "request_needed",
        "classification": "codex_request_needed",
        "latest_reviewed_head": latest_reviewed_head,
        "clean_signal": None,
        "suggestions": None,
        "request": None,
        "codex_latest_request_acknowledged": None,
        "codex_latest_request_acknowledged_at": None,
        "codex_reaction_status": None,
        "uncertainty": [],
    }


def recommended_next_action(classification: str) -> str:
    return {
        "blocked_pr_closed": "Stop; PR is closed without merge.",
        "blocked_pr_merged": "Stop; PR is already merged. No further gate action is required.",
        "blocked_wrong_base": "Stop; retarget or recreate the PR against the expected base branch.",
        "blocked_scope": "Stop; patch the PR to include only approved files.",
        "ci_pending": "Wait for CI to complete, then rerun this classifier.",
        "ci_failed": "Patch the PR to fix failing CI, then rerun validation.",
        "codex_request_needed": "Request @codex review for the current head; do not dispatch Reviewer yet.",
        "codex_pending": "Wait for Codex to respond on the current head; do not dispatch Reviewer yet.",
        "codex_suggestions": "Address or report Codex suggestions; do not dispatch Reviewer yet.",
        "codex_clean": "Codex is clean; dispatch Reviewer if all other gates pass.",
        "ready_for_reviewer": "Dispatch fresh Reviewer task for the current head.",
        "unknown": "Inspect blockers and uncertainty manually before taking action.",
    }.get(classification, "Inspect manually.")


def classify_payloads(
    *,
    pr: dict[str, Any],
    changed_files: list[str],
    check_runs: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    allowed_files: list[str],
    expected_head: str | None,
    codex_bot_login: str,
    base_branch: str = "main",
    latest_request_reactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_sha = str(head.get("sha") or "")
    base_ref = str(base.get("ref") or "")
    state = str(pr.get("state") or "unknown")
    merged = bool(pr.get("merged"))
    unexpected_files = sorted(set(changed_files) - set(allowed_files))
    head_matches_expected = None if expected_head is None else head_sha == expected_head
    blockers: list[str] = []
    uncertainty: list[str] = []
    ci_status = "not_applicable"

    classification = "unknown"
    codex = {
        "codex_status": "unknown",
        "latest_reviewed_head": None,
        "clean_signal": None,
        "suggestions": None,
        "uncertainty": [],
    }

    if state == "closed" and not merged:
        classification = "blocked_pr_closed"
        blockers.append("PR is closed and not merged.")
    elif merged:
        classification = "blocked_pr_merged"
        blockers.append("PR is already merged.")
    elif base_ref != base_branch:
        ci_status, ci_blockers = classify_ci(check_runs)
        blockers.extend(ci_blockers)
        classification = "blocked_wrong_base"
        blockers.append(f"PR base branch is {base_ref!r}, expected {base_branch!r}.")
    elif head_matches_expected is False:
        ci_status, ci_blockers = classify_ci(check_runs)
        blockers.extend(ci_blockers)
        classification = "unknown"
        blockers.append(f"PR head SHA is {head_sha!r}, expected {expected_head!r}.")
    elif unexpected_files:
        ci_status, ci_blockers = classify_ci(check_runs)
        blockers.extend(ci_blockers)
        classification = "blocked_scope"
        blockers.append("Changed files include paths outside the allowed file list.")
    else:
        ci_status, ci_blockers = classify_ci(check_runs)
        blockers.extend(ci_blockers)
        if ci_status == "pending":
            classification = "ci_pending"
        elif ci_status == "failed":
            classification = "ci_failed"
        else:
            head_pushed_at = parse_time(pr.get("head_pushed_at"))
            codex = classify_codex(
                head_sha=head_sha,
                head_pushed_at=head_pushed_at,
                issue_comments=issue_comments,
                reviews=reviews,
                codex_bot_login=codex_bot_login,
                latest_request_reactions=latest_request_reactions,
            )
            classification = codex["classification"]
            uncertainty.extend(codex.get("uncertainty") or [])

    if classification not in CLASSIFICATIONS:
        uncertainty.append(f"Internal classifier returned unknown state: {classification}")
        classification = "unknown"

    return {
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url"),
        "state": state,
        "merged": merged,
        "draft": bool(pr.get("draft")),
        "mergeable": pr.get("mergeable"),
        "base_branch": base_ref,
        "head_branch": head.get("ref"),
        "head_sha": head_sha,
        "expected_head": expected_head,
        "head_matches_expected": head_matches_expected,
        "changed_files": sorted(changed_files),
        "unexpected_files": unexpected_files,
        "ci_status": ci_status,
        "ci_checks": [
            {"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion")}
            for r in check_runs
        ],
        "codex_status": codex.get("codex_status"),
        "codex_latest_reviewed_head": codex.get("latest_reviewed_head"),
        "codex_latest_clean_signal": codex.get("clean_signal"),
        "codex_latest_suggestions": codex.get("suggestions"),
        "codex_latest_request_acknowledged": codex.get("codex_latest_request_acknowledged"),
        "codex_latest_request_acknowledged_at": codex.get("codex_latest_request_acknowledged_at"),
        "codex_reaction_status": codex.get("codex_reaction_status"),
        "classification": classification,
        "recommended_next_action": recommended_next_action(classification),
        "blockers": blockers,
        "uncertainty": uncertainty,
    }


def parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        match = re.search(r"<([^>]+)>", section)
        return match.group(1) if match else None
    return None


class GitHubClient:
    def __init__(self, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo
        self.token = os.environ.get("GITHUB_TOKEN") or self._token_from_gh_hosts()
        self.use_gh = shutil.which("gh") is not None

    def _token_from_gh_hosts(self) -> str | None:
        hosts = Path.home() / ".config" / "gh" / "hosts.yml"
        if not hosts.exists():
            return None
        match = re.search(r"oauth_token:\s*(\S+)", hosts.read_text(encoding="utf-8"))
        return match.group(1) if match else None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "aed-pr-gate-state-classifier",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get(self, path: str) -> Any:
        if self.use_gh:
            try:
                result = subprocess.run(
                    ["gh", "api", f"repos/{self.owner}/{self.repo}{path}"],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return json.loads(result.stdout)
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                # Fall back to REST below; this tool remains read-only.
                pass
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.owner}/{self.repo}{path}",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_all(self, path: str) -> list[dict[str, Any]]:
        if self.use_gh:
            try:
                result = subprocess.run(
                    ["gh", "api", "--paginate", f"repos/{self.owner}/{self.repo}{path}"],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                text = result.stdout.strip()
                if not text:
                    return []
                decoder = json.JSONDecoder()
                idx = 0
                items: list[dict[str, Any]] = []
                while idx < len(text):
                    value, end = decoder.raw_decode(text, idx)
                    if isinstance(value, list):
                        items.extend(value)
                    else:
                        raise ValueError("paginated endpoint did not return a list")
                    idx = end
                    while idx < len(text) and text[idx].isspace():
                        idx += 1
                return items
            except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
                pass

        url: str | None = f"https://api.github.com/repos/{self.owner}/{self.repo}{path}"
        items: list[dict[str, Any]] = []
        while url:
            request = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(request, timeout=30) as response:
                page = json.loads(response.read().decode("utf-8"))
                if not isinstance(page, list):
                    raise TypeError(f"Expected list response for paginated endpoint {path!r}")
                items.extend(page)
                url = parse_next_link(response.headers.get("Link"))
        return items
    def get_check_runs_all(self, head_sha: str) -> list[dict[str, Any]]:
        path = f"/commits/{head_sha}/check-runs?per_page=100"
        if self.use_gh:
            try:
                result = subprocess.run(
                    ["gh", "api", "--paginate", f"repos/{self.owner}/{self.repo}{path}"],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                text = result.stdout.strip()
                if not text:
                    return []
                decoder = json.JSONDecoder()
                idx = 0
                items: list[dict[str, Any]] = []
                while idx < len(text):
                    value, end = decoder.raw_decode(text, idx)
                    if isinstance(value, dict):
                        items.extend(value.get("check_runs", []))
                    else:
                        raise ValueError("check-runs endpoint did not return an object")
                    idx = end
                    while idx < len(text) and text[idx].isspace():
                        idx += 1
                return items
            except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
                pass

        url: str | None = f"https://api.github.com/repos/{self.owner}/{self.repo}{path}"
        items: list[dict[str, Any]] = []
        while url:
            request = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(request, timeout=30) as response:
                page = json.loads(response.read().decode("utf-8"))
                if not isinstance(page, dict):
                    raise TypeError("Expected object response for check-runs endpoint")
                items.extend(page.get("check_runs", []))
                url = parse_next_link(response.headers.get("Link"))
        return items

    def get_reactions(self, comment_id: int) -> list[dict[str, Any]]:
        """Fetch reactions on an issue comment. Returns list of reaction objects."""
        path = f"/issues/comments/{comment_id}/reactions"
        if self.use_gh:
            try:
                result = subprocess.run(
                    ["gh", "api", "--paginate", f"repos/{self.owner}/{self.repo}{path}"],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                text = result.stdout.strip()
                if not text:
                    return []
                decoder = json.JSONDecoder()
                idx = 0
                items: list[dict[str, Any]] = []
                while idx < len(text):
                    value, end = decoder.raw_decode(text, idx)
                    if isinstance(value, list):
                        items.extend(value)
                    else:
                        raise ValueError("reactions endpoint did not return a list")
                    idx = end
                    while idx < len(text) and text[idx].isspace():
                        idx += 1
                return items
            except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
                pass

        url: str | None = f"https://api.github.com/repos/{self.owner}/{self.repo}{path}"
        items = []
        while url:
            request = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(request, timeout=30) as response:
                page = json.loads(response.read().decode("utf-8"))
                if not isinstance(page, list):
                    raise TypeError(f"Expected list response for reactions endpoint {path!r}")
                items.extend(page)
                url = parse_next_link(response.headers.get("Link"))
        return items

def fetch_live_payloads(owner: str, repo: str, pr_number: int) -> tuple[dict[str, Any], list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (pr, files, check_runs, issue_comments, reviews, reactions_for_latest_request)."""
    client = GitHubClient(owner, repo)
    pr = client.get(f"/pulls/{pr_number}")
    files = [item["filename"] for item in client.get_all(f"/pulls/{pr_number}/files?per_page=100")]
    head_sha = pr.get("head", {}).get("sha")
    check_runs = client.get_check_runs_all(str(head_sha))
    comments = client.get_all(f"/issues/{pr_number}/comments?per_page=100")
    reviews = client.get_all(f"/pulls/{pr_number}/reviews?per_page=100")
    if "head_pushed_at" not in pr or pr.get("head_pushed_at") is None:
        commit = client.get(f"/commits/{head_sha}")
        pr["head_pushed_at"] = (
            ((commit.get("commit") or {}).get("committer") or {}).get("date")
            or ((commit.get("commit") or {}).get("author") or {}).get("date")
        )

    # Identify the latest current-head @codex review request and fetch its reactions
    head_pushed_at = parse_time(pr.get("head_pushed_at"))
    latest_request_id: int | None = None
    latest_request_at: datetime | None = None
    for comment in sorted(comments, key=lambda c: str(c.get("created_at") or "")):
        body_l = str(comment.get("body") or "").lower()
        if "@codex review" not in body_l:
            continue
        created_at = parse_time(comment.get("created_at"))
        mentions_head = bool(head_sha and _contains_current_head(str(comment.get("body") or ""), str(head_sha)))
        after_head = head_pushed_at is not None and created_at is not None and created_at >= head_pushed_at
        if mentions_head or after_head:
            latest_request_id = int(comment["id"])
            latest_request_at = created_at

    reactions: list[dict[str, Any]] = []
    if latest_request_id is not None:
        reactions = client.get_reactions(latest_request_id)

    return pr, files, check_runs, comments, reviews, reactions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify read-only AED GitHub PR gate state.")
    parser.add_argument("--repo-owner", required=True)
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--allowed-file", action="append", default=[], help="Allowed changed file; may be repeated or comma-separated.")
    parser.add_argument("--expected-head")
    parser.add_argument("--codex-bot-login", default="chatgpt-codex-connector[bot]")
    parser.add_argument("--output-json", action="store_true", help="Print compact JSON instead of pretty JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    allowed_files = flatten_allowed_files(args.allowed_file)
    pr, files, check_runs, comments, reviews, reactions = fetch_live_payloads(args.repo_owner, args.repo_name, args.pr_number)
    packet = classify_payloads(
        pr=pr,
        changed_files=files,
        check_runs=check_runs,
        issue_comments=comments,
        reviews=reviews,
        allowed_files=allowed_files,
        expected_head=args.expected_head,
        codex_bot_login=args.codex_bot_login,
        base_branch=args.base_branch,
        latest_request_reactions=reactions,
    )
    print(json.dumps(packet, sort_keys=True, separators=(",", ":") if args.output_json else None, indent=None if args.output_json else 2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
