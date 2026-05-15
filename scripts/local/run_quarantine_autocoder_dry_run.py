#!/usr/bin/env python3
"""
Phase 1 Quarantine Autocoder — Dry-Run Bundle Scaffold

WARNING: This tool produces a bundle ONLY. It does NOT:
  - Apply any patch
  - Execute any agent
  - Touch Hermes
  - Dispatch any Kanban task
  - Create any PR
  - Perform any import

This is a dry-run-only Phase 1 implementation.
All bundle contents are scaffolds / placeholders.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_SLUG_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
FORBIDDEN_BUNDLE_PREFIXES = (".git", "hermes", ".hermes", "workflows", ".github")
FORBIDDEN_BUNDLE_INFIXES = (".git/", "/.git")
EXECUTABLE_MUTATION_COMMANDS = frozenset([
    "hermes kanban create",
    "hermes kanban dispatch",
    "gh pr merge",
    "gh pr create",
    "git push",
    "git commit",
    "telegram",
    "send_message",
    "memory.update",
    "skill_manage",
    "fact_store",
    "delegate_task",
    "cronjob",
])


def validate_base_sha(sha: str) -> None:
    if not HEX_SHA_RE.match(sha):
        raise ValueError(f"base_sha must be a 40-char hex string, got: {sha!r}")


def validate_candidate_id(candidate_id: str) -> None:
    if not SAFE_SLUG_RE.match(candidate_id):
        raise ValueError(
            f"candidate_id must be a safe slug (alphanumeric, underscore, hyphen), "
            f"got: {candidate_id!r}"
        )


def validate_source_repo(source_repo: str) -> None:
    source_repo = os.path.abspath(source_repo)
    if source_repo == "/":
        raise ValueError("source_repo cannot be the filesystem root '/'")


def validate_bundle_dir(bundle_dir: str, force: bool) -> None:
    # Use resolved (real) paths to handle symlinks — prevents bypass via
    # symlink that points into .git or repo root from outside the repo.
    bundle_dir_resolved = Path(bundle_dir).resolve()
    repo_root = Path(__file__).resolve().parents[2]  # .../Automated-Edge-Discovery
    repo_root_resolved = repo_root.resolve()

    # Check against forbidden production directories under repo root
    for prefix in FORBIDDEN_BUNDLE_PREFIXES:
        protected = (repo_root_resolved / prefix).resolve()
        try:
            bundle_dir_resolved.relative_to(protected)
            raise ValueError(
                f"bundle_dir cannot be inside production directory: {prefix}"
            )
        except ValueError:
            pass  # not inside this prefix, continue

    # Reject if bundle dir IS the repo root
    if bundle_dir_resolved == repo_root_resolved:
        raise ValueError("bundle_dir cannot be the production repository root")

    # Check .git infix even after resolve (covers both /path/.git and symlink resolved path)
    resolved_str = str(bundle_dir_resolved)
    for infix in FORBIDDEN_BUNDLE_INFIXES:
        if infix in resolved_str:
            raise ValueError(f"bundle_dir cannot contain: {infix}")

    if not force and any(Path(bundle_dir).iterdir() if Path(bundle_dir).is_dir() else []):
        raise ValueError(
            f"bundle_dir is not empty. Use --force to overwrite or re-run."
        )


def safety_grep_content(content: str) -> list[str]:
    """Check content for executable mutation commands. Returns list of matches."""
    found = []
    for cmd in EXECUTABLE_MUTATION_COMMANDS:
        if cmd in content:
            found.append(cmd)
    return found


# ---------------------------------------------------------------------------
# Bundle file generators
# ---------------------------------------------------------------------------

def write_bundle_status(bundle_dir: str) -> dict:
    status = {
        "phase": "Phase 1 (dry-run only)",
        "dry_run": True,
        "dispatch_occurred": False,
        "hermes_touched": False,
        "production_board_touched": False,
        "pr_created": False,
        "import_performed": False,
        "bundle_created_at": datetime.now(timezone.utc).isoformat(),
        "warning": (
            "NO PATCH APPLIED — NO AGENT EXECUTED — NO HERMES TOUCHED — "
            "NO DISPATCH OCCURRED — NO PR CREATED — NO IMPORT PERFORMED"
        ),
    }
    path = os.path.join(bundle_dir, "BUNDLE_STATUS.json")
    with open(path, "w") as f:
        json.dump(status, f, indent=2)
    return status


def write_text_file(bundle_dir: str, filename: str, content: str) -> str:
    path = os.path.join(bundle_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def write_markdown_file(bundle_dir: str, filename: str, content: str) -> str:
    path = os.path.join(bundle_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def generate_scope_check(source_repo: str, base_sha: str) -> dict:
    """Placeholder scope check — does NOT run git log."""
    return {
        "source_repo": source_repo,
        "base_sha": base_sha,
        "note": "Phase 1: scope check is a placeholder scaffold. No git log run.",
        "files_changed_count": "unknown (not computed)",
        "scope_clean": None,
    }


def generate_safety_grep(source_repo: str) -> dict:
    """Placeholder safety grep — does NOT scan repo."""
    return {
        "source_repo": source_repo,
        "note": "Phase 1: safety grep is a placeholder scaffold. No filesystem scan run.",
        "forbidden_patterns_found": [],
        "clean": None,
    }


def generate_local_gate() -> dict:
    return {
        "phase": "Phase 1",
        "local_gate_passed": None,
        "note": "Phase 1: local gate is a placeholder. No compileall, no pytest run.",
        "compiles": None,
        "tests_pass": None,
    }


def generate_codex_review_summary() -> dict:
    return {
        "phase": "Phase 1",
        "codex_reviewed": False,
        "note": "Phase 1: Codex review summary is a placeholder. No Codex run.",
        "clean": None,
    }


def generate_risk_notes(base_sha: str, candidate_id: str, objective: str) -> str:
    return (
        f"# Risk Notes — Phase 1 Dry-Run\n"
        f"\n"
        f"**base_sha**: {base_sha}\n"
        f"**candidate_id**: {candidate_id}\n"
        f"**objective**: {objective}\n"
        f"\n"
        f"## Phase 1 Disclaimer\n"
        f"\n"
        f"This bundle is a DRY-RUN SCAFFOLD ONLY. It contains:\n"
        f"- No real diff (diff.patch is a placeholder)\n"
        f"- No real scope check (scope_check.json is a placeholder)\n"
        f"- No real safety grep (safety_grep.txt is a placeholder)\n"
        f"- No real local gate (local_gate.txt is a placeholder)\n"
        f"- No real Codex review (codex_review_summary.md is a placeholder)\n"
        f"\n"
        f"No patch has been applied. No agent has been executed. Hermes has not been touched.\n"
        f"No Kanban dispatch has occurred. No PR has been created. No import has been performed.\n"
    )


def generate_proposed_pr_body(bundle_dir: str, candidate_id: str, objective: str) -> str:
    bundle_dir_name = os.path.basename(bundle_dir)
    return (
        f"# Proposed PR Body — Phase 1 Dry-Run\n"
        f"\n"
        f"**candidate_id**: {candidate_id}\n"
        f"**objective**: {objective}\n"
        f"**bundle**: {bundle_dir_name}\n"
        f"\n"
        f"## Phase 1 Disclaimer\n"
        f"\n"
        f"This PR body is a SCAFFOLD PLACEHOLDER.\n"
        f"Phase 1 does NOT create a real PR. It only produces a bundle.\n"
        f"\n"
        f"## Next Steps\n"
        f"\n"
        f"- Phase 2 (if approved) would execute the real autocoder against the scaffold.\n"
        f"- Phase 3 (if approved) would create and merge a real PR.\n"
    )


def generate_import_command_sh(bundle_dir: str, candidate_id: str) -> str:
    return (
        "#!/bin/bash\n"
        "# import_command.sh — Phase 1 Dry-Run Placeholder\n"
        "#\n"
        "# WARNING: This file is NON-EXECUTABLE by default.\n"
        "# It contains commented instructions only.\n"
        "# No git push, gh pr create, gh pr merge, Hermes, or dispatch commands\n"
        "# are executed in Phase 1.\n"
        "#\n"
        f"# bundle_dir : {bundle_dir}\n"
        f"# candidate_id: {candidate_id}\n"
        "#\n"
        "# Instructions:\n"
        "# 1. Review bundle contents in full.\n"
        "# 2. Run Phase 2 autocoder to populate real diff.patch / scope_check.json / safety_grep.txt.\n"
        "# 3. Run local gate (compileall + pytest) manually before any import.\n"
        "# 4. Obtain human approval before running any executable import commands.\n"
        "# 5. Codex review the bundle before any import.\n"
        "#\n"
        "# === DO NOT UNCOMMENT OR EXECUTE ANYTHING BELOW THIS LINE IN PHASE 1 ===\n"
        "#\n"
        "# git fetch origin <base-sha>\n"
        "# git diff <base-sha>..HEAD -- > diff.patch\n"
        "# gh pr create --title '...'\n"
        "# gh pr merge --admin --squash\n"
        "# hermes kanban dispatch --max 1\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase 1 Quarantine Autocoder — Dry-Run Bundle Scaffold Generator",
        epilog=(
            "Phase 1 produces a bundle scaffold ONLY. "
            "No patch applied, no agent executed, Hermes untouched, no dispatch, no PR, no import."
        ),
    )
    parser.add_argument("--source-repo", required=True, help="Path to source repository")
    parser.add_argument("--bundle-dir", required=True, help="Output directory for bundle")
    parser.add_argument("--base-sha", required=True, help="40-char hex commit SHA")
    parser.add_argument("--candidate-id", required=True, help="Safe slug identifier")
    parser.add_argument("--objective", required=True, help="Objective description")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="REQUIRED flag. Refuses to run without this.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite or re-run into an existing non-empty bundle-dir",
    )
    args = parser.parse_args(argv)

    # ---- Dry-run enforcement ----
    if not args.dry_run:
        print("ERROR: --dry-run is REQUIRED. Refusing to run.")
        print(
            "This tool is Phase 1 dry-run only. "
            "It will not execute without --dry-run."
        )
        sys.exit(1)

    # ---- Validations ----
    try:
        validate_base_sha(args.base_sha)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    try:
        validate_candidate_id(args.candidate_id)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    try:
        validate_source_repo(args.source_repo)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    try:
        validate_bundle_dir(args.bundle_dir, args.force)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    # ---- Create bundle ----
    # Clean existing bundle dir under --force to prevent stale/forbidden files
    # from persisting alongside new bundle files.
    if args.force and os.path.isdir(args.bundle_dir):
        for entry in os.listdir(args.bundle_dir):
            entry_path = os.path.join(args.bundle_dir, entry)
            if os.path.isfile(entry_path) or os.path.islink(entry_path):
                os.remove(entry_path)
            elif os.path.isdir(entry_path):
                import shutil
                shutil.rmtree(entry_path)
    os.makedirs(args.bundle_dir, exist_ok=True)

    # BUNDLE_STATUS.json
    status = write_bundle_status(args.bundle_dir)
    print(f"[Phase 1] Wrote BUNDLE_STATUS.json")

    # Text files
    write_text_file(args.bundle_dir, "base_sha.txt", args.base_sha)
    print(f"[Phase 1] Wrote base_sha.txt")

    write_text_file(args.bundle_dir, "candidate_id.txt", args.candidate_id)
    print(f"[Phase 1] Wrote candidate_id.txt")

    write_text_file(args.bundle_dir, "changed_files.txt", "(placeholder — no git diff run in Phase 1)\n")
    print(f"[Phase 1] Wrote changed_files.txt")

    # diff.patch placeholder
    diff_content = (
        "# diff.patch — Phase 1 placeholder\n"
        "# No diff computed in Phase 1 dry-run.\n"
        "# Run Phase 2 autocoder to produce real diff.\n"
        "# git diff <base-sha>..HEAD would populate this file.\n"
    )
    write_text_file(args.bundle_dir, "diff.patch", diff_content)
    print(f"[Phase 1] Wrote diff.patch (placeholder)")

    # Markdown files
    write_markdown_file(args.bundle_dir, "objective.md", f"# Objective\n{args.objective}\n")
    print(f"[Phase 1] Wrote objective.md")

    write_markdown_file(args.bundle_dir, "risk_notes.md",
                        generate_risk_notes(args.base_sha, args.candidate_id, args.objective))
    print(f"[Phase 1] Wrote risk_notes.md")

    write_markdown_file(args.bundle_dir, "proposed_pr_body.md",
                        generate_proposed_pr_body(args.bundle_dir, args.candidate_id, args.objective))
    print(f"[Phase 1] Wrote proposed_pr_body.md")

    # JSON files
    scope_check = generate_scope_check(args.source_repo, args.base_sha)
    scope_check_path = os.path.join(args.bundle_dir, "scope_check.json")
    with open(scope_check_path, "w") as f:
        json.dump(scope_check, f, indent=2)
    print(f"[Phase 1] Wrote scope_check.json (placeholder)")

    safety_grep = generate_safety_grep(args.source_repo)
    safety_grep_path = os.path.join(args.bundle_dir, "safety_grep.txt")
    with open(safety_grep_path, "w") as f:
        json.dump(safety_grep, f, indent=2)
    print(f"[Phase 1] Wrote safety_grep.txt (placeholder)")

    local_gate = generate_local_gate()
    local_gate_path = os.path.join(args.bundle_dir, "local_gate.txt")
    with open(local_gate_path, "w") as f:
        json.dump(local_gate, f, indent=2)
    print(f"[Phase 1] Wrote local_gate.txt (placeholder)")

    codex_summary = generate_codex_review_summary()
    codex_path = os.path.join(args.bundle_dir, "codex_review_summary.md")
    with open(codex_path, "w") as f:
        json.dump(codex_summary, f, indent=2)
    print(f"[Phase 1] Wrote codex_review_summary.md (placeholder)")

    # import_command.sh — non-executable by default
    import_sh = generate_import_command_sh(args.bundle_dir, args.candidate_id)
    import_sh_path = os.path.join(args.bundle_dir, "import_command.sh")
    with open(import_sh_path, "w") as f:
        f.write(import_sh)
    # Ensure NOT executable
    os.chmod(import_sh_path, 0o644)
    print(f"[Phase 1] Wrote import_command.sh (non-executable, commented only)")

    print()
    print("=== Phase 1 Bundle Complete ===")
    print(f"Bundle: {args.bundle_dir}")
    print(f"Dry-run: {status['dry_run']}")
    print(f"Dispatch occurred: {status['dispatch_occurred']}")
    print(f"Hermes touched: {status['hermes_touched']}")
    print(f"Production board touched: {status['production_board_touched']}")
    print(f"PR created: {status['pr_created']}")
    print(f"Import performed: {status['import_performed']}")
    print()
    print("NO PATCH APPLIED — NO AGENT EXECUTED — NO HERMES TOUCHED")
    print("NO DISPATCH OCCURRED — NO PR CREATED — NO IMPORT PERFORMED")
    return 0


if __name__ == "__main__":
    sys.exit(main())