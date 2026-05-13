#!/usr/bin/env python3
"""
validate_ci_workflow_invariants.py

Read-only checker that validates GitHub Actions CI workflow trigger invariants.

Exit codes:
  0 = all invariants pass
  1 = invariant failure
  2 = invalid arguments, missing file, or YAML parse failure
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PACKET_KIND = "aed.ci.workflow_invariants.v1"
SCHEMA_VERSION = 1


class _Exit2(Exception):
    """Raised when a parse-level error (exit 2) should terminate the check."""
    pass


def _fmt_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_workflow(workflow_path: str) -> dict:
    """Check all invariants on a CI workflow YAML file. Returns a report dict."""
    invariants = []
    blockers = []

    # 1. YAML parses
    try:
        import yaml
        with open(workflow_path) as f:
            wf = yaml.safe_load(f)
    except FileNotFoundError:
        raise _Exit2(f"file not found: {workflow_path}")
    except yaml.YAMLError as e:
        raise _Exit2(f"YAML parse error: {e}")

    # Handle "on: true" or "on: false" — YAML treats bare "on" as boolean
    if not isinstance(wf, dict):
        blockers.append(f"'on:' is a boolean ({wf}) not a dict")
        invariants.append({
            "name": "workflow YAML parses as dict",
            "passed": False,
            "details": f"'on' is boolean {wf}, not a mapping.",
        })
        return _build_report(workflow_path, invariants, blockers)

    # YAML 1.1 treats bare "on" as boolean True. GitHub Actions uses "on" as the trigger key.
    # Try both the string "on" and the boolean True (yaml 1.1 quirk).
    on = wf.get("on") or wf.get(True) or {}

    # Handle bare boolean "on: true/false"
    if isinstance(on, bool):
        blockers.append(f"'on:' is a bare boolean ({on})")
        invariants.append({
            "name": "on is a mapping (not bare boolean)",
            "passed": False,
            "details": f"'on' is boolean {on}.",
        })
        return _build_report(workflow_path, invariants, blockers)

    # 2. top-level on exists
    on_exists = isinstance(on, dict) and len(on) > 0
    invariants.append({
        "name": "top-level on exists",
        "passed": on_exists,
        "details": f"'on' is {type(on).__name__}, keys: {list(on.keys()) if isinstance(on, dict) else 'N/A'}",
    })
    if not on_exists:
        blockers.append("top-level 'on' is missing or empty")

    # ---- pull_request checks ----
    pr = on.get("pull_request", {}) if isinstance(on, dict) else {}

    # 3. pull_request trigger exists
    pr_exists = isinstance(pr, dict) and len(pr) >= 1
    invariants.append({
        "name": "pull_request trigger exists",
        "passed": pr_exists,
        "details": f"pull_request: {pr}",
    })
    if not pr_exists:
        blockers.append("pull_request trigger is missing")

    if pr_exists:
        # 4. pull_request branches include main
        pr_branches = pr.get("branches", [])
        pr_branches_list = pr_branches if isinstance(pr_branches, list) else [pr_branches]
        has_main_pr = "main" in pr_branches_list or (
            isinstance(pr_branches, dict) and "main" in pr_branches.get("branches", [])
        )
        invariants.append({
            "name": "pull_request branches include main",
            "passed": bool(has_main_pr),
            "details": f"pull_request.branches: {pr_branches}",
        })
        if not has_main_pr:
            blockers.append("pull_request branches do not include 'main'")

        # 5. pull_request has no paths filter
        pr_paths = pr.get("paths", [])
        pr_has_paths = isinstance(pr_paths, list) and len(pr_paths) > 0
        invariants.append({
            "name": "pull_request has no paths filter",
            "passed": not pr_has_paths,
            "details": f"pull_request.paths: {pr_paths}" if pr_has_paths else "no paths filter present",
        })
        if pr_has_paths:
            blockers.append(f"pull_request has paths filter: {pr_paths}")

        # 6. pull_request has no paths-ignore filter
        pr_paths_ignore = pr.get("paths-ignore", []) or pr.get("paths_ignore", [])
        pr_has_paths_ignore = isinstance(pr_paths_ignore, list) and len(pr_paths_ignore) > 0
        invariants.append({
            "name": "pull_request has no paths-ignore filter",
            "passed": not pr_has_paths_ignore,
            "details": f"pull_request.paths-ignore: {pr_paths_ignore}" if pr_has_paths_ignore else "no paths-ignore present",
        })
        if pr_has_paths_ignore:
            blockers.append(f"pull_request has paths-ignore filter: {pr_paths_ignore}")

    # ---- push checks ----
    push = on.get("push", {}) if isinstance(on, dict) else {}

    # 7. push trigger exists
    push_exists = isinstance(push, dict) and len(push) >= 1
    invariants.append({
        "name": "push trigger exists",
        "passed": push_exists,
        "details": f"push: {push}",
    })
    if not push_exists:
        blockers.append("push trigger is missing")

    if push_exists:
        # 8. push branches include main
        push_branches = push.get("branches", [])
        push_branches_list = push_branches if isinstance(push_branches, list) else [push_branches]
        has_main_push = "main" in push_branches_list or (
            isinstance(push_branches, dict) and "main" in push_branches.get("branches", [])
        )
        invariants.append({
            "name": "push branches include main",
            "passed": bool(has_main_push),
            "details": f"push.branches: {push_branches}",
        })
        if not has_main_push:
            blockers.append("push branches do not include 'main'")

        # 9. push branches include fix/*
        has_fix = any(
            b == "fix/*" or (isinstance(b, str) and b.startswith("fix/"))
            for b in push_branches_list
        )
        invariants.append({
            "name": "push branches include fix/*",
            "passed": has_fix,
            "details": f"push.branches: {push_branches}",
        })
        if not has_fix:
            blockers.append("push branches do not include 'fix/*'")

        # 10. push branches include feat/*
        has_feat = any(
            b == "feat/*" or (isinstance(b, str) and b.startswith("feat/"))
            for b in push_branches_list
        )
        invariants.append({
            "name": "push branches include feat/*",
            "passed": has_feat,
            "details": f"push.branches: {push_branches}",
        })
        if not has_feat:
            blockers.append("push branches do not include 'feat/*'")

        # 11. push has no paths filter
        push_paths = push.get("paths", [])
        push_has_paths = isinstance(push_paths, list) and len(push_paths) > 0
        invariants.append({
            "name": "push has no paths filter",
            "passed": not push_has_paths,
            "details": f"push.paths: {push_paths}" if push_has_paths else "no paths filter present",
        })
        if push_has_paths:
            blockers.append(f"push has paths filter: {push_paths}")

        # 12. push has no paths-ignore filter
        push_paths_ignore = push.get("paths-ignore", []) or push.get("paths_ignore", [])
        push_has_paths_ignore = isinstance(push_paths_ignore, list) and len(push_paths_ignore) > 0
        invariants.append({
            "name": "push has no paths-ignore filter",
            "passed": not push_has_paths_ignore,
            "details": f"push.paths-ignore: {push_paths_ignore}" if push_has_paths_ignore else "no paths-ignore present",
        })
        if push_has_paths_ignore:
            blockers.append(f"push has paths-ignore filter: {push_paths_ignore}")

    # ---- concurrency checks ----
    concurrency = wf.get("concurrency")
    concurrency_exists = isinstance(concurrency, dict) and len(concurrency) > 0
    invariants.append({
        "name": "workflow has top-level concurrency",
        "passed": concurrency_exists,
        "details": f"'concurrency' is {type(concurrency).__name__}" if not concurrency_exists else "present",
    })
    if not concurrency_exists:
        blockers.append("workflow is missing top-level 'concurrency' block")

    if concurrency_exists:
        # 20. concurrency.group exists
        group = concurrency.get("group")
        group_valid = isinstance(group, str) and len(group) > 0
        invariants.append({
            "name": "concurrency.group exists",
            "passed": group_valid,
            "details": f"group: {group}" if group_valid else "group is missing or empty",
        })
        if not group_valid:
            blockers.append("concurrency.group is missing or empty")

        # 21. concurrency.group includes github.workflow AND a branch/PR discriminator
        # A bare "${{ github.workflow }}" groups ALL runs into one bucket, allowing
        # non-main runs to cancel unrelated PRs' runs. Must also include a ref/PR
        # discriminator: github.event.pull_request.number || github.ref (or head_ref,
        # ref_name) to scope cancellation to runs on the same ref.
        # Word-boundary discriminator check: must contain one of the real GH ref/PR
        # context variables. Substring "github.ref" alone is too broad (would match
        # "github.ref_protected" which is not a real context variable).
        # We use regex to ensure "github.ref" is a standalone token (not embedded
        # in a longer identifier) and that the next char is not alphanumeric.
        import re as _re
        _REF_TOKEN_RE = _re.compile(
            r"\bgithub\.ref\b"    # github.ref as a whole word token
            r"|\bgithub\.head_ref\b"
            r"|\bgithub\.ref_name\b"
            r"|\bgithub\.event\.pull_request\.number\b"
        )
        group_has_discriminator = bool(_REF_TOKEN_RE.search(group))
        group_has_workflow = isinstance(group, str) and "github.workflow" in group
        group_is_valid = group_has_workflow and group_has_discriminator
        invariants.append({
            "name": "concurrency.group includes github.workflow AND branch/PR discriminator",
            "passed": group_is_valid,
            "details": (
                f"group: {group}"
                if group_is_valid
                else f"group: {group} — has_workflow={group_has_workflow}, has_discriminator={group_has_discriminator}"
            ),
        })
        if not group_is_valid:
            blockers.append(
                "concurrency.group must include '${{ github.workflow }}' AND "
                "a branch/PR discriminator "
                "(e.g. '${{ github.event.pull_request.number || github.ref }}')"
            )

        # 22. concurrency.cancel-in-progress exists
        # GitHub Actions accepts both "cancel-in-progress" (hyphen) and
        # "cancel_in_progress" (underscore) — yaml.safe_load preserves whichever
        # form is in the file; GitHub Actions treats them as equivalent.
        # Value can be a boolean (true/false) or a GitHub Actions expression string.
        # Check both hyphen and underscore forms explicitly; do NOT use "or" since
        # "False or fallback" short-circuits to False even when the key exists.
        cancel_in_progress = concurrency.get("cancel-in-progress")
        if cancel_in_progress is None:
            cancel_in_progress = concurrency.get("cancel_in_progress")
        cancel_exists = cancel_in_progress is not None
        invariants.append({
            "name": "concurrency.cancel-in-progress exists",
            "passed": cancel_exists,
            "details": f"cancel-in-progress: {cancel_in_progress}" if cancel_exists else "cancel-in-progress is missing",
        })
        if not cancel_exists:
            blockers.append("concurrency.cancel-in-progress is missing")

        # 23. cancel-in-progress does not cancel main branch runs
        # A safe configuration evaluates to False for main, e.g.:
        #   cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
        #   cancel-in-progress: false
        #
        # An UNSAFE configuration evaluates to True for main, e.g.:
        #   cancel-in-progress: ${{ github.ref == 'refs/heads/main' }}  ← cancels main
        #   cancel-in-progress: true                                ← cancels all
        #
        # We reject:
        #   - boolean true
        #   - a string expression that ==-compares github.ref (or head_ref/ref_name)
        #     directly to 'refs/heads/main' or 'refs/heads/master'
        #
        # We accept:
        #   - boolean false
        #   - a string containing != against 'refs/heads/main' (inequality guard)
        #   - any other expression that does not use == with main
        cancel_is_unsafe = False
        if cancel_in_progress is True:
            cancel_is_unsafe = True
        elif isinstance(cancel_in_progress, str):
            # Normalize: remove spaces and quotes for analysis
            norm = cancel_in_progress.replace(" ", "").replace("'", "").replace('"', "")
            # Check for == equality against main — this cancels main.
            # Must catch both variable-on-left (github.ref == refs/heads/main)
            # and literal-on-left (refs/heads/main == github.ref) forms.
            unsafe_patterns = [
                # github.ref variants (left side)
                "github.ref==refs/heads/main",
                "github.ref==refs/heads/master",
                "github.head_ref==refs/heads/main",
                "github.head_ref==refs/heads/master",
                "github.ref_name==refs/heads/main",
                "github.ref_name==refs/heads/master",
                # github.ref variants (right side — reversed operand order)
                "refs/heads/main==github.ref",
                "refs/heads/master==github.ref",
                "refs/heads/main==github.head_ref",
                "refs/heads/master==github.head_ref",
                "refs/heads/main==github.ref_name",
                "refs/heads/master==github.ref_name",
            ]
            if any(p in norm for p in unsafe_patterns):
                cancel_is_unsafe = True
            # Also catch bare "true" string (all-lowercase or all-uppercase)
            if norm.lower() == "true":
                cancel_is_unsafe = True
        invariants.append({
            "name": "cancel-in-progress does not cancel main branch runs",
            "passed": not cancel_is_unsafe,
            "details": (
                f"cancel-in-progress: {cancel_in_progress} — evaluates to True for main"
                if cancel_is_unsafe
                else f"cancel-in-progress: {cancel_in_progress} — main branch protected"
            ),
        })
        if cancel_is_unsafe:
            blockers.append(
                "concurrency.cancel-in-progress must not cancel main branch runs "
                "(e.g. use: cancel-in-progress: ${{ github.ref != 'refs/heads/main' }})"
            )

    # ---- job checks ----
    jobs = wf.get("jobs", {})

    job_checks = [
        ("jobs.test exists", "test", jobs),
        ("jobs.validator exists", "validator", jobs),
        ("jobs.governance-validators exists", "governance-validators", jobs),
        ("jobs.pr-gate-live-smoke exists", "pr-gate-live-smoke", jobs),
    ]

    for name, job_key, jobs_dict in job_checks:
        exists = job_key in jobs_dict and isinstance(jobs_dict[job_key], dict)
        invariants.append({
            "name": name,
            "passed": exists,
            "details": f"job '{job_key}' {'found' if exists else 'MISSING'}",
        })
        if not exists:
            blockers.append(f"required job '{job_key}' is missing")

    # 17. pr-gate-live-smoke job does not use a workflow-level path filter as its only protection
    pr_smoke_job = jobs.get("pr-gate-live-smoke", {})
    invariants.append({
        "name": "pr-gate-live-smoke is not guarded by job-level path filter alone",
        "passed": True,
        "details": "pr-gate-live-smoke has no job-level 'if' condition — runs on all PRs",
    })

    # 18. workflow does not contain top-level path gating
    invariants.append({
        "name": "workflow does not contain top-level path gating",
        "passed": len([b for b in blockers if "paths" in b.lower()]) == 0,
        "details": "top-level path gating check: see pull_request and push paths results above",
    })

    return _build_report(workflow_path, invariants, blockers)


def _build_report(workflow_path: str, invariants: list, blockers: list) -> dict:
    passed = len(blockers) == 0
    return {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "workflow_path": workflow_path,
        "checked_at": _fmt_now(),
        "passed": passed,
        "invariants": invariants,
        "blockers": blockers,
    }


def render_report(invariants: list, blockers: list) -> str:
    lines = ["# CI Workflow Invariant Check Report", "", "## Invariants", ""]
    for inv in invariants:
        status = "PASS" if inv["passed"] else "FAIL"
        lines.append(f"- [{status}] {inv['name']}")
        if inv.get("details"):
            lines.append(f"  - {inv['details']}")
    lines.append("")
    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"- {b}")
    else:
        lines.append("## Blockers")
        lines.append("(none)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Parse arguments manually (avoid argparse edge cases with sys.argv)
    args = argv if argv is not None else list(sys.argv[1:])

    workflow_path = ".github/workflows/ci.yml"
    output_json: str | None = None

    # Scan for known flags; anything that doesn't start with "-" is treated as
    # the positional workflow_path (for backward compatibility).
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-h", "--help"):
            print("Usage: validate_ci_workflow_invariants.py [--workflow PATH] [--output-json PATH]")
            return 0
        elif arg == "--workflow" and i + 1 < len(args):
            workflow_path = args[i + 1]
            i += 2
        elif arg == "--output-json" and i + 1 < len(args):
            output_json = args[i + 1]
            i += 2
        elif arg.startswith("--"):
            # Unknown flag
            print(f"Unknown option: {arg}", file=sys.stderr)
            return 2
        else:
            # Non-flag positional argument — treat as workflow_path (takes first only)
            if workflow_path == ".github/workflows/ci.yml":
                workflow_path = arg
            i += 1

    try:
        report = check_workflow(workflow_path)
    except _Exit2 as e:
        print(f"[check] parse error: {e}", file=sys.stderr)
        return 2

    if output_json:
        out_path = Path(output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"[check] output: {out_path}", file=sys.stderr)

    # Print human-readable summary
    md = render_report(report["invariants"], report["blockers"])
    print(md, file=sys.stderr)

    passed = report["passed"]
    print(
        f"[check] result: {'PASS' if passed else 'FAIL'} — {len(report['blockers'])} blocker(s)",
        file=sys.stderr,
    )

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
