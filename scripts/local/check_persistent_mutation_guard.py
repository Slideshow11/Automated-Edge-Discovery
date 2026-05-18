#!/usr/bin/env python3
from __future__ import annotations

"""Persistent Mutation Guard — Snapshot and compare Hermes state for AED work.

Purpose:
    Detect, report, and block unauthorized changes to Hermes persistent state
    during AED workflow runs. Unauthorized skill creations, memory/profile
    modifications, and Hermes config mutations are not silently accepted.

Design:
    Snapshot/diff/report/block guard — not full containerization, not complete
    staged filesystem. Practical v1 before overnight unattended runs.

Usage:
    python3 scripts/local/check_persistent_mutation_guard.py snapshot \\
        --root /home/max/.hermes \\
        --output /tmp/aed_runs/<run_id>/persistent_state_before.json

    python3 scripts/local/check_persistent_mutation_guard.py compare \\
        --root /home/max/.hermes \\
        --before /tmp/aed_runs/<run_id>/persistent_state_before.json \\
        --output-json /tmp/aed_runs/<run_id>/persistent_state_after.json \\
        --output-md /tmp/aed_runs/<run_id>/persistent_state_report.md \\
        [--allowlist /path/to/allowlist.json]

Exit codes:
    0   — snapshot written / compare passed (clean)
    1   — malformed input / missing root / bad snapshot
    2   — blocked changes detected
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

GUARD_VERSION = 1
# Paths are relative to the Hermes root (the .hermes directory itself).
# These are used to collect files; the .hermes/ prefix is added only in
# display/output for clarity.
MONITORED_ROOTS = [
    "skills",
    "config.yaml",
    "profiles",
    "memory",
    "memories",
]
SKILL_PATTERNS = ["skills"]
CONFIG_PATTERNS = ["config.yaml"]
PROFILE_PATTERNS = ["profiles"]
MEMORY_PATTERNS = ["**/USER.md", "**/MEMORY.md"]


# -----------------------------------------------------------------------------
# Snapshot
# -----------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    """SHA256 of file contents. Returns empty string for non-regular files."""
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError):
        return ""


def _snapshot_file(absolute_path: Path, root: Path) -> dict:
    """Return a snapshot record for one file."""
    rel = absolute_path.relative_to(root)
    stat = absolute_path.stat() if absolute_path.exists() else None
    return {
        "path": str(absolute_path),
        "relative_path": str(rel),
        "exists": absolute_path.exists(),
        "size_bytes": stat.st_size if stat else 0,
        "mtime_ns": stat.st_mtime_ns if stat else 0,
        "sha256": _hash_file(absolute_path) if absolute_path.is_file() else "",
    }


def _collect_monitored_files(root: Path) -> list[Path]:
    """Yield all existing regular files under the monitored paths within root.

    Symlinks are NOT followed to prevent traversing outside the root tree
    via malicious symlinks inside the monitored directories.
    """
    files = []
    for pattern in MONITORED_ROOTS:
        full_pattern = root / pattern
        if "**" in pattern:
            matching = root.glob(pattern)
            files.extend(matching)
        else:
            if full_pattern.is_dir():
                # Collect with symlink filter to avoid traversing outside root
                for item in _safe_rglob(full_pattern):
                    files.append(item)
            elif full_pattern.exists():
                files.append(full_pattern)
    # Deduplicate, keep only regular files (skip symlinks entirely)
    seen = set()
    result = []
    for f in files:
        if f.is_file() and not f.is_symlink() and f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _safe_rglob(directory: Path) -> list[Path]:
    """Recursively list all paths under directory without following symlinks."""
    # os.walk avoids following symlinks by default; followlinks=False is default
    # but we explicitly verify each entry is not a symlink to be safe
    result = []
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        # Prevent descending into symlink directories
        dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
        for fname in filenames:
            f = Path(dirpath, fname)
            if not f.is_symlink():
                result.append(f)
    return result


def snapshot(root: Path, output: Path) -> int:
    """
    Write a JSON snapshot of the current Hermes state to ``output``.

    Returns 0 on success, 1 on error.
    """
    root = root.resolve()
    if not root.exists():
        print(f"ERROR: root does not exist: {root}", file=sys.stderr)
        return 1

    if output.resolve().is_relative_to(root):
        print(f"ERROR: output path must be outside monitored root: {output}", file=sys.stderr)
        return 1

    monitored_files = _collect_monitored_files(root)

    snapshot_data = {
        "guard_version": GUARD_VERSION,
        "snapshot_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(root),
        "files": [_snapshot_file(f, root) for f in monitored_files],
    }

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(snapshot_data, fh, indent=2)
    except Exception as e:
        print(f"ERROR: failed to write snapshot: {e}", file=sys.stderr)
        return 1

    print(f"Snapshot written: {output} ({len(monitored_files)} files)", file=sys.stderr)
    return 0


# -----------------------------------------------------------------------------
# Compare
# -----------------------------------------------------------------------------

def _load_snapshot(path: Path) -> dict:
    """Load and validate a snapshot file. Raises ValueError on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, IOError) as e:
        raise ValueError(f"cannot read snapshot: {e}")
    except json.JSONDecodeError as e:
        raise ValueError(f"malformed JSON in snapshot: {e}")

    if "files" not in data or "guard_version" not in data:
        raise ValueError("snapshot missing required fields (guard_version, files)")

    return data


def _build_file_index(files: list) -> dict[str, dict]:
    """Index a snapshot's files by relative_path for O(1) lookup."""
    return {f["relative_path"]: f for f in files}


def _load_allowlist(path: Path | None, root: Path | None = None) -> set[str]:
    """Load an allowlist JSON file. Returns set of exact allowed paths.

    If root is provided, path existence checks are relative to root.
    """
    if path is None:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, IOError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read allowlist: {e}")

    allowed = set()
    for entry in data.get("allowed_paths", []):
        if not isinstance(entry, str):
            raise ValueError(f"allowlist entries must be strings, got {type(entry).__name__}")
        norm = os.path.normpath(entry)
        if root is not None:
            # Check existence relative to root
            check_path = root / norm
            if not check_path.is_file():
                raise ValueError(f"allowlist entry is not a file: {entry}")
        allowed.add(norm)

    return allowed


def _is_under_skills(rel_path: str) -> bool:
    """Check if a relative path is under skills."""
    if not isinstance(rel_path, str):
        rel_path = str(rel_path)
    parts = rel_path.split(os.sep)
    for p in parts:
        if p == "skills":
            return True
    return False


def _is_under_profiles(rel_path: str) -> bool:
    """Check if a relative path is under profiles."""
    if not isinstance(rel_path, str):
        rel_path = str(rel_path)
    parts = rel_path.split(os.sep)
    for p in parts:
        if p == "profiles":
            return True
    return False


def _is_memory_or_profile_file(rel_path: str) -> bool:
    """Check if a path is a USER.md or MEMORY.md file."""
    if not isinstance(rel_path, str):
        rel_path = str(rel_path)
    import fnmatch
    for pattern in MEMORY_PATTERNS:
        normalized = pattern.lstrip("./")
        if fnmatch.fnmatch(rel_path, normalized) or fnmatch.fnmatch(rel_path, pattern):
            return True
    name = os.path.basename(rel_path)
    return name in ("USER.md", "MEMORY.md")


def _is_config_file(rel_path: str) -> bool:
    """Check if path is a Hermes config file."""
    if not isinstance(rel_path, str):
        rel_path = str(rel_path)
    return rel_path == "config.yaml"


def compare(
    root: Path,
    before: Path,
    output_json: Path,
    output_md: Path,
    allowlist: Path | None,
) -> int:
    """
    Compare current Hermes state against a snapshot.

    Returns 0 if clean, 1 on error, 2 if blocked changes found.
    """
    root = root.resolve()

    # Validate root
    if not root.exists():
        print(f"ERROR: root does not exist: {root}", file=sys.stderr)
        return 1

    # Validate output paths are outside root
    for out_path in (output_json, output_md):
        try:
            if out_path.resolve().is_relative_to(root.resolve()):
                print(f"ERROR: output path must be outside monitored root: {out_path}", file=sys.stderr)
                return 1
        except ValueError:
            pass  # not relative, ok

    # Load snapshot
    try:
        before_data = _load_snapshot(before)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Load allowlist
    allowed_paths: set[str] = set()
    if allowlist is not None:
        try:
            allowed_paths = _load_allowlist(allowlist, root)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    # Build current state — store as snapshot records (like before_index) for uniform comparison
    current_files = _collect_monitored_files(root)
    current_index = {}
    for f in current_files:
        current_index[str(f.relative_to(root))] = _snapshot_file(f, root)
    before_index = _build_file_index(before_data["files"])

    files_added: list[str] = []
    files_removed: list[str] = []
    files_modified: list[str] = []
    allowed_changes: list[str] = []
    blocked_changes: list[dict] = []

    all_rel_paths = set(current_index.keys()) | set(before_index.keys())

    for rel_path in sorted(all_rel_paths):
        # Ensure rel_path is a string (keys from current_index may be Path objects)
        rel_path_str = str(rel_path)
        norm_path = os.path.normpath(rel_path_str)

        before_rec = before_index.get(rel_path_str)
        current_rec = current_index.get(rel_path_str)

        if before_rec is None and current_rec is not None:
            # File added
            if norm_path in allowed_paths:
                allowed_changes.append(f"added (allowed): {rel_path_str}")
            else:
                files_added.append(rel_path_str)
                blocked_changes.append({
                    "relative_path": rel_path_str,
                    "change": "added",
                })
        elif before_rec is not None and current_rec is None:
            # File removed
            if norm_path in allowed_paths:
                allowed_changes.append(f"removed (allowed): {rel_path_str}")
            else:
                files_removed.append(rel_path_str)
                blocked_changes.append({
                    "relative_path": rel_path_str,
                    "change": "removed",
                })
        elif before_rec is not None and current_rec is not None:
            # File modified (check hash)
            if before_rec.get("sha256") != current_rec.get("sha256"):
                if norm_path in allowed_paths:
                    allowed_changes.append(f"modified (allowed): {rel_path_str}")
                else:
                    files_modified.append(rel_path_str)
                    blocked_changes.append({
                        "relative_path": rel_path_str,
                        "change": "modified",
                    })

    # Categorize blocked changes
    skill_blocked = [b for b in blocked_changes if _is_under_skills(b["relative_path"])]
    config_blocked = [b for b in blocked_changes if _is_config_file(b["relative_path"])]
    profile_blocked = [b for b in blocked_changes if _is_under_profiles(b["relative_path"])]
    memory_blocked = [b for b in blocked_changes if _is_memory_or_profile_file(b["relative_path"])]

    status = "blocked" if blocked_changes else "clean"
    recommendation = "BLOCK" if blocked_changes else "PASS"

    # Write JSON report
    result_json = {
        "guard_version": GUARD_VERSION,
        "status": status,
        "files_added": files_added,
        "files_removed": files_removed,
        "files_modified": files_modified,
        "allowed_changes": allowed_changes,
        "blocked_changes": blocked_changes,
        "skill_blocked": skill_blocked,
        "config_blocked": config_blocked,
        "profile_blocked": profile_blocked,
        "memory_blocked": memory_blocked,
        "recommendation": recommendation,
    }

    try:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(result_json, f, indent=2)
    except Exception as e:
        print(f"ERROR: failed to write JSON output: {e}", file=sys.stderr)
        return 1

    # Write Markdown report
    lines = [
        "# Persistent Mutation Guard Report",
        "",
        f"**Guard version:** {GUARD_VERSION}",
        f"**Status:** `{status}`",
        f"**Recommendation:** `{recommendation}`",
        f"**Root:** `{root}`",
        f"**Snapshot:** `{before}`",
        "",
    ]

    if allowed_changes:
        lines += [
            "## Allowed Changes",
            "",
        ]
        for change in allowed_changes:
            lines.append(f"- {change}")
        lines.append("")

    if blocked_changes:
        lines += [
            "## Blocked Changes",
            "",
            "| Path | Change |",
            "|------|--------|",
        ]
        for b in blocked_changes:
            lines.append(f"| `{b['relative_path']}` | {b['change']} |")
        lines.append("")

    if skill_blocked:
        lines += [
            "### Skill Changes",
            "",
            f"**{len(skill_blocked)} skill file(s) changed.**",
            "Skill additions, modifications, and reference writes are blocked.",
            "```",
            *[f"  - {b['relative_path']} ({b['change']})" for b in skill_blocked],
            "```",
            "",
        ]
    if config_blocked:
        lines += [
            "### Config Changes",
            "",
            f"**{len(config_blocked)} config file(s) changed.**",
            "Hermes config mutations are blocked.",
            "```",
            *[f"  - {b['relative_path']} ({b['change']})" for b in config_blocked],
            "```",
            "",
        ]
    if profile_blocked:
        lines += [
            "### Profile Changes",
            "",
            f"**{len(profile_blocked)} profile file(s) changed.**",
            "Profile config mutations are blocked.",
            "```",
            *[f"  - {b['relative_path']} ({b['change']})" for b in profile_blocked],
            "```",
            "",
        ]
    if memory_blocked:
        lines += [
            "### Memory / Profile File Changes",
            "",
            f"**{len(memory_blocked)} memory/profile file(s) changed.**",
            "USER.md and MEMORY.md modifications are blocked.",
            "```",
            *[f"  - {b['relative_path']} ({b['change']})" for b in memory_blocked],
            "```",
            "",
        ]

    if not blocked_changes:
        lines += [
            "## Result",
            "",
            "✅ No unauthorized mutations detected.",
            "",
        ]

    try:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        with open(output_md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        print(f"ERROR: failed to write MD output: {e}", file=sys.stderr)
        return 1

    print(f"Compare complete: status={status}, blocked={len(blocked_changes)}", file=sys.stderr)
    return 2 if blocked_changes else 0


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persistent Mutation Guard — snapshot and compare Hermes state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Write a snapshot of current Hermes state.")
    snap.add_argument("--root", type=Path, required=True, help="Hermes root to snapshot.")
    snap.add_argument("--output", type=Path, required=True, help="Output JSON path.")

    comp = sub.add_parser("compare", help="Compare current state against a snapshot.")
    comp.add_argument("--root", type=Path, required=True, help="Hermes root to check.")
    comp.add_argument("--before", type=Path, required=True, help="Snapshot to compare against.")
    comp.add_argument("--output-json", type=Path, required=True, help="JSON output path.")
    comp.add_argument("--output-md", type=Path, required=True, help="Markdown output path.")
    comp.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help="Optional allowlist JSON file.",
    )

    args = parser.parse_args()

    if args.command == "snapshot":
        return snapshot(args.root, args.output)
    elif args.command == "compare":
        return compare(args.root, args.before, args.output_json, args.output_md, args.allowlist)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())