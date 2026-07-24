#!/usr/bin/env python3
"""Canonical shared pagination helpers.

This module provides complete, fail-closed, safety-cap-aware
paginated inventory helpers for every evidence surface used by
the autocoder:

  * GraphQL review threads
  * issue comments
  * formal reviews
  * inline review comments
  * workflow runs and jobs (caller-driven)

All helpers:

  * continue until the underlying pagination cursor is
    exhausted;
  * never treat the first page (default 100) as the complete
    inventory;
  * preserve the complete participant inventory and every
    field needed by downstream consumers;
  * expose inventory completeness explicitly via a
    ``complete`` boolean;
  * fail closed when pagination fails or a configured safety
    cap is reached;
  * never silently truncate results.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence


DEFAULT_PAGE_SIZE = 100
DEFAULT_SAFETY_CAP = 2000  # hard upper bound per surface


def _gh_token() -> str:
    """Return the GitHub auth token.

    Allows ``AED_SHARED_GITHUB_TOKEN`` to override the
    ``gh auth token`` subprocess call so tests and CI can
    run without a configured ``gh`` CLI.
    """
    import os
    env_token = os.environ.get("AED_SHARED_GITHUB_TOKEN")
    if env_token:
        return env_token
    return subprocess.check_output(["gh", "auth", "token"], text=True).strip()


def _gql_request(
    query: str,
    variables: Optional[Dict[str, Any]] = None,
    *,
    timeout: int = 60,
) -> Dict[str, Any]:
    """POST a GraphQL query and return the parsed JSON body."""
    token = _gh_token()
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def paginate_graphql_connection(
    *,
    owner: str,
    name: str,
    pr_number: int,
    query: str,
    path: Sequence[str],
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
    extra_variables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Paginate a GraphQL connection until hasNextPage is false.

    ``query`` MUST accept ``$owner``, ``$name``, ``$number``,
    ``$after``, and ``$first`` and return a connection under
    ``path`` with ``pageInfo{hasNextPage endCursor}`` and
    ``nodes``.

    Returns ``{"nodes": [...], "complete": bool, "pages": int,
    "capped": bool}``.

    Fails closed when ``safety_cap`` is reached or any page
    fails to fetch.
    """
    all_nodes: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    pages = 0
    capped = False
    while True:
        pages += 1
        if len(all_nodes) >= safety_cap:
            capped = True
            break
        variables = {
            "owner": owner,
            "name": name,
            "number": pr_number,
            "first": page_size,
            "after": cursor,
        }
        if extra_variables:
            variables.update(extra_variables)
        try:
            d = _gql_request(query, variables)
        except Exception as exc:
            # Fail closed.
            return {
                "nodes": all_nodes,
                "complete": False,
                "pages": pages,
                "capped": capped,
                "error": repr(exc),
            }
        # Round-69 Codex review 4769577891 (P2): fail
        # closed on GraphQL ``errors`` payloads. GitHub
        # may return an ``errors`` array together with
        # partial ``data`` (e.g. field / permission
        # errors). The previous behavior ignored
        # ``d.get("errors")`` and continued navigating
        # the partial connection, so a page with errors
        # could still be reported as ``complete=True``
        # with truncated evidence. The shared paginators
        # are used to decide review / comment inventory
        # completeness, so partial evidence must be
        # reported as incomplete.
        errors = d.get("errors") if isinstance(d, dict) else None
        if errors:
            return {
                "nodes": all_nodes,
                "complete": False,
                "pages": pages,
                "capped": capped,
                "error": f"graphql_errors: {errors}",
            }
        # Navigate the connection via ``path``.
        node = d
        for segment in path:
            node = node.get(segment) if isinstance(node, dict) else None
            if node is None:
                return {
                    "nodes": all_nodes,
                    "complete": False,
                    "pages": pages,
                    "capped": capped,
                    "error": "path_not_found",
                }
        nodes = node.get("nodes") or []
        all_nodes.extend(nodes)
        page_info = node.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if cursor is None:
            # hasNextPage true but no cursor is a defect.
            return {
                "nodes": all_nodes,
                "complete": False,
                "pages": pages,
                "capped": capped,
                "error": "hasNextPage_without_endCursor",
            }
    return {
        "nodes": all_nodes,
        "complete": not capped,
        "pages": pages,
        "capped": capped,
    }


# Reusable GraphQL fragments.
_REVIEW_THREADS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$first:Int!,$after:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviewThreads(first:$first, after:$after){
        pageInfo{hasNextPage endCursor}
        nodes{
          id isResolved isOutdated
          comments(first:50){
            pageInfo{hasNextPage endCursor}
            nodes{
              databaseId url body path line
              originalCommit{oid}
              author{login}
            }
          }
        }
      }
    }
  }
}
"""


def paginate_review_threads(
    owner: str,
    name: str,
    pr_number: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
) -> Dict[str, Any]:
    """Complete paginated review-thread inventory.

    Round-412 (PHASE 2): the outer ``reviewThreads``
    connection is paginated until ``hasNextPage=false``.
    In addition, EVERY thread's nested ``comments``
    connection must be paginated too. If any thread's
    nested ``comments.pageInfo.hasNextPage=true`` is
    detected, the helper fails closed with
    ``error="nested_comments_not_paginated"`` and
    ``complete=False``. The first page of comments per
    thread is NEVER treated as the complete inventory.
    """
    result = paginate_graphql_connection(
        owner=owner,
        name=name,
        pr_number=pr_number,
        query=_REVIEW_THREADS_QUERY,
        path=("data", "repository", "pullRequest", "reviewThreads"),
        page_size=page_size,
        safety_cap=safety_cap,
    )
    # Fail closed on nested-comment pagination: if any
    # thread's ``comments.pageInfo.hasNextPage`` is True,
    # the inventory is incomplete regardless of the outer
    # reviewThreads completion state.
    nodes = result.get("nodes", []) or []
    incomplete_thread_ids: List[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        comments_field = node.get("comments")
        if not isinstance(comments_field, dict):
            continue
        page_info = comments_field.get("pageInfo") or {}
        if page_info.get("hasNextPage"):
            tid = node.get("id")
            if tid is not None:
                incomplete_thread_ids.append(tid)
    if incomplete_thread_ids:
        return {
            "nodes": nodes,
            "complete": False,
            "pages": result.get("pages"),
            "capped": result.get("capped"),
            "error": "nested_comments_not_paginated",
            "incomplete_nested_thread_ids": incomplete_thread_ids,
        }
    return result


_ISSUE_COMMENTS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$first:Int!,$after:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      comments(first:$first, after:$after){
        pageInfo{hasNextPage endCursor}
        nodes{
          databaseId author{login}
          body createdAt updatedAt
        }
      }
    }
  }
}
"""


def paginate_issue_comments(
    owner: str,
    name: str,
    pr_number: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
) -> Dict[str, Any]:
    """Complete paginated issue-comment inventory."""
    return paginate_graphql_connection(
        owner=owner,
        name=name,
        pr_number=pr_number,
        query=_ISSUE_COMMENTS_QUERY,
        path=("data", "repository", "pullRequest", "comments"),
        page_size=page_size,
        safety_cap=safety_cap,
    )


_FORMAL_REVIEWS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$first:Int!,$after:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviews(first:$first, after:$after){
        pageInfo{hasNextPage endCursor}
        nodes{
          databaseId author{login}
          state
          submittedAt
          body
          commit{oid}
          url
        }
      }
    }
  }
}
"""


def paginate_formal_reviews(
    owner: str,
    name: str,
    pr_number: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
) -> Dict[str, Any]:
    """Complete paginated formal-review inventory."""
    return paginate_graphql_connection(
        owner=owner,
        name=name,
        pr_number=pr_number,
        query=_FORMAL_REVIEWS_QUERY,
        path=("data", "repository", "pullRequest", "reviews"),
        page_size=page_size,
        safety_cap=safety_cap,
    )


_REVIEW_INLINE_COMMENTS_QUERY = """
query($reviewId:ID!,$first:Int!,$after:String){
  node(id:$reviewId){
    ... on PullRequestReview{
      comments(first:$first, after:$after){
        pageInfo{hasNextPage endCursor}
        nodes{
          databaseId author{login}
          body path line
          originalCommit{oid}
        }
      }
    }
  }
}
"""


def paginate_review_inline_comments(
    owner: str,
    name: str,
    pr_number: int,
    review_id: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
) -> Dict[str, Any]:
    """Complete paginated inline-comment inventory for one review."""
    return paginate_graphql_connection(
        owner=owner,
        name=name,
        pr_number=pr_number,
        query=_REVIEW_INLINE_COMMENTS_QUERY,
        path=("data", "node", "comments"),
        page_size=page_size,
        safety_cap=safety_cap,
        extra_variables={"reviewId": review_id},
    )


def paginate_changed_files(
    *,
    repo: str,
    pr_number: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Paginate changed files via the REST ``/pulls/{n}/files`` endpoint."""
    all_files: List[Dict[str, Any]] = []
    page = 1
    pages = 0
    capped = False
    while True:
        pages += 1
        if len(all_files) >= safety_cap:
            capped = True
            break
        url = (
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files"
            f"?per_page={page_size}&page={page}"
        )
        try:
            token = _gh_token()
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                batch = json.loads(resp.read())
        except Exception as exc:
            return {
                "nodes": all_files,
                "complete": False,
                "pages": pages,
                "capped": capped,
                "error": repr(exc),
            }
        if not batch:
            break
        all_files.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    return {
        "nodes": all_files,
        "complete": not capped,
        "pages": pages,
        "capped": capped,
    }


def paginate_workflow_runs(
    *,
    repo: str,
    head_sha: str,
    event: str = "pull_request",
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Paginate workflow runs matching head_sha and event."""
    all_runs: List[Dict[str, Any]] = []
    page = 1
    pages = 0
    capped = False
    while True:
        pages += 1
        if len(all_runs) >= safety_cap:
            capped = True
            break
        url = (
            f"https://api.github.com/repos/{repo}/actions/runs"
            f"?event={event}&per_page={page_size}&page={page}"
        )
        try:
            token = _gh_token()
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except Exception as exc:
            return {
                "nodes": all_runs,
                "complete": False,
                "pages": pages,
                "capped": capped,
                "error": repr(exc),
            }
        # /actions/runs returns {"total_count": N,
        # "workflow_runs": [...]} — extract the runs list.
        if isinstance(payload, dict):
            runs = payload.get("workflow_runs") or []
        elif isinstance(payload, list):
            runs = payload
        else:
            runs = []
        matching = [r for r in runs if r.get("head_sha") == head_sha]
        all_runs.extend(matching)
        # The filter is by head_sha, so we must keep paginating
        # until the entire list is exhausted, not just until we
        # find a match.
        if not runs or len(runs) < page_size:
            break
        page += 1
    return {
        "nodes": all_runs,
        "complete": not capped,
        "pages": pages,
        "capped": capped,
    }


def paginate_jobs_for_run(
    *,
    repo: str,
    run_id: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    safety_cap: int = DEFAULT_SAFETY_CAP,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Paginate jobs for a specific workflow run."""
    all_jobs: List[Dict[str, Any]] = []
    page = 1
    pages = 0
    capped = False
    while True:
        pages += 1
        if len(all_jobs) >= safety_cap:
            capped = True
            break
        url = (
            f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
            f"?per_page={page_size}&page={page}"
        )
        try:
            token = _gh_token()
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except Exception as exc:
            return {
                "nodes": all_jobs,
                "complete": False,
                "pages": pages,
                "capped": capped,
                "error": repr(exc),
            }
        # /actions/runs/{id}/jobs returns {"total_count": N,
        # "jobs": [...]} — extract the jobs list, not the
        # payload as a list.
        if isinstance(payload, dict):
            jobs = payload.get("jobs") or []
        elif isinstance(payload, list):
            jobs = payload
        else:
            jobs = []
        all_jobs.extend(jobs)
        if len(jobs) < page_size:
            break
        page += 1
    return {
        "nodes": all_jobs,
        "complete": not capped,
        "pages": pages,
        "capped": capped,
    }
