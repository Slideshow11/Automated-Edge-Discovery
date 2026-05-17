#!/usr/bin/env python3
"""
check_hermes_profile_mutation.py

Read-only sentinel that detects whether Hermes profile files were mutated
during an AED quarantine autocoder run.

Two modes:
  1. Snapshot mode:  --snapshot-json  -> produce a snapshot of current file state
  2. Compare mode:  --before-json + --after-json  -> compare two snapshots

Snapshot format (JSON):
    {
        "generated_at": "ISO8601",
        "paths": ["/path/to/file1", "/path/to/file2"],
        "snapshots": {
            "/path/to/file1": {"md5": "hex", "mtime": float},
            "/path/to/file2": {"md5": "hex", "mtime": float}
        }
    }

Exit codes:
    0  — comparison produced (no mutation detected or snapshot written)
    1  — validation error
    2  — mutation detected (memory_or_profile_updated: true)

Usage (snapshot):
    python3 scripts/local/check_hermes_profile_mutation.py \
        --snapshot-json /tmp/profile_sentinel_before.json \
        --paths-json '["~/.hermes/memories/MEMORY.md", "~/.hermes/memories/USER.md"]'

Usage (compare):
    python3 scripts/local/check_hermes_profile_mutation.py \
        --before-json /tmp/profile_sentinel_before.json \
        --after-json /tmp/profile_sentinel_after.json \
        --output-json /tmp/profile_sentinel_result.json \
        --output-md /tmp/profile_sentinel_result.md
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Default paths to monitor
DEFAULT_HERMES_PATHS = [
    "~/.hermes/memories/MEMORY.md",
    "~/.hermes/memories/USER.md",
]


def _expand_path(path_str: str) -> Path:
    """Expand ~ and resolve to absolute Path."""
    return Path(os.path.expanduser(path_str)).resolve()


def compute_file_snapshot(paths: list[str]) -> dict:
    """Compute md5 + mtime for each path. Missing files are recorded with null."""
    snapshots = {}
    for p in paths:
        abs_path = _expand_path(p)
        if abs_path.exists():
            with open(abs_path, "rb") as f:
                md5 = hashlib.md5(f.read()).hexdigest()
            mtime = abs_path.stat().st_mtime
            snapshots[p] = {"md5": md5, "mtime": mtime}
        else:
            snapshots[p] = None
    return snapshots


def compare_snapshots(before: dict, after: dict) -> dict:
    """Compare two snapshots. Returns a comparison result dict."""
    result = {
        "before_snapshot": before,
        "after_snapshot": after,
        "checked": False,
        "paths_checked": [],
        "paths_mutated": [],
        "memory_or_profile_updated": False,
        "mutations": [],
        "errors": [],
    }

    before_snapshots = before.get("snapshots", {})
    after_snapshots = after.get("snapshots", {})

    all_paths = set(before_snapshots.keys()) | set(after_snapshots.keys())
    result["paths_checked"] = list(all_paths)
    result["checked"] = True

    for path_str in all_paths:
        before_state = before_snapshots.get(path_str)
        after_state = after_snapshots.get(path_str)

        if before_state is None and after_state is None:
            continue  # file never existed

        if before_state is None and after_state is not None:
            result["paths_mutated"].append(path_str)
            result["mutations"].append({
                "path": path_str,
                "type": "created",
                "before": None,
                "after": after_state,
            })
            result["memory_or_profile_updated"] = True
            continue

        if before_state is not None and after_state is None:
            result["paths_mutated"].append(path_str)
            result["mutations"].append({
                "path": path_str,
                "type": "deleted",
                "before": before_state,
                "after": None,
            })
            result["memory_or_profile_updated"] = True
            continue

        # Both non-null — compare hash
        if before_state.get("md5") != after_state.get("md5"):
            result["paths_mutated"].append(path_str)
            # pyright: we already checked both are non-None above
            bs = before_state  # type: ignore[assignment]
            as_ = after_state  # type: ignore[assignment]
            result["mutations"].append({
                "path": path_str,
                "type": "content_changed",
                "before": {"md5": bs.get("md5"), "mtime": bs.get("mtime")},
                "after": {"md5": as_.get("md5"), "mtime": as_.get("mtime")},
            })
            result["memory_or_profile_updated"] = True
            continue

        if before_state.get("mtime") != after_state.get("mtime"):
            result["mutations"].append({
                "path": path_str,
                "type": "mtime_changed",
                "before": before_state,
                "after": after_state,
            })
            # mtime-only change without content change — not a mutation concern
            # (could be from a read-only tool touching mtime)

    return result


def write_comparison_markdown(result: dict, output_md: Path) -> None:
    """Write a human-readable markdown comparison report."""
    lines = []
    lines.append("# Hermes Profile Mutation Sentinel Report\n")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    lines.append(f"**Checked:** {'yes' if result.get('checked') else 'no'}\n")
    lines.append(f"**Paths checked:** {', '.join(result.get('paths_checked', [])) or 'none'}\n")

    if result.get("memory_or_profile_updated"):
        lines.append(f"\n❌ **MUTATION DETECTED** — `memory_or_profile_updated: true`\n")
    else:
        lines.append(f"\n✅ **NO MUTATION** — `memory_or_profile_updated: false`\n")

    if result.get("mutations"):
        lines.append("\n## Mutations\n")
        for m in result["mutations"]:
            lines.append(f"- **{m['path']}** (`{m['type']}`)\n")
            if m["type"] == "created":
                lines.append(f"  - Before: N/A  |  After: md5={m['after'].get('md5')}\n")
            elif m["type"] == "deleted":
                lines.append(f"  - Before: md5={m['before'].get('md5')}  |  After: N/A\n")
            elif m["type"] == "content_changed":
                lines.append(
                    f"  - Before: md5={m['before'].get('md5')}  |  "
                    f"After: md5={m['after'].get('md5')}\n"
                )
        lines.append("\n")

    if result.get("errors"):
        lines.append("\n## Errors\n")
        for e in result["errors"]:
            lines.append(f"- {e}\n")
        lines.append("\n")

    with open(output_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def run_snapshot(args) -> int:
    """Snapshot mode: read current state of profile files, write JSON."""
    paths = json.loads(args.paths_json) if args.paths_json else DEFAULT_HERMES_PATHS

    # Expand paths
    expanded = [_expand_path(p) for p in paths]
    path_strs = [str(p) for p in expanded]

    snapshots = compute_file_snapshot(path_strs)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paths": path_strs,
        "snapshots": snapshots,
    }

    output_path = Path(args.snapshot_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Snapshot written: {output_path}")
    print(f"Paths: {', '.join(path_strs)}")
    print(f"Files found: {sum(1 for v in snapshots.values() if v is not None)}")
    return 0


def run_compare(args) -> int:
    """Compare mode: load before/after snapshots, compare, write report."""
    before_path = Path(args.before_json)
    after_path = Path(args.after_json)

    if not before_path.exists():
        print(f"ERROR: --before-json not found: {before_path}", file=sys.stderr)
        return 1
    if not after_path.exists():
        print(f"ERROR: --after-json not found: {after_path}", file=sys.stderr)
        return 1

    try:
        with open(before_path) as f:
            before = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: --before-json is malformed: {e}", file=sys.stderr)
        return 1

    try:
        with open(after_path) as f:
            after = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: --after-json is malformed: {e}", file=sys.stderr)
        return 1

    result = compare_snapshots(before, after)

    # Write JSON
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    # Write Markdown
    if args.output_md:
        write_comparison_markdown(result, Path(args.output_md))

    # Print summary
    if result["memory_or_profile_updated"]:
        print(f"❌ MUTATION DETECTED — memory_or_profile_updated: true", file=sys.stderr)
        print(f"   Mutated paths: {', '.join(result['paths_mutated'])}")
        print(f"   Report: {output_json}")
        return 2
    else:
        print(f"✅ NO MUTATION — memory_or_profile_updated: false")
        print(f"   Report: {output_json}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hermes profile mutation sentinel — read-only file hash checker."
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    snap = sub.add_parser("snapshot", help="Take a snapshot of current profile file state")
    snap.add_argument(
        "--paths-json", default=None,
        help='JSON array of paths to snapshot, e.g. '
             '["~/.hermes/memories/MEMORY.md", "~/.hermes/memories/USER.md"]'
    )
    snap.add_argument(
        "--snapshot-json", required=True,
        help="Output path for snapshot JSON"
    )

    comp = sub.add_parser("compare", help="Compare two snapshots")
    comp.add_argument("--before-json", required=True, help="Path to before snapshot JSON")
    comp.add_argument("--after-json", required=True, help="Path to after snapshot JSON")
    comp.add_argument("--output-json", required=True, help="Output path for comparison result JSON")
    comp.add_argument("--output-md", default=None, help="Output path for comparison result Markdown")

    args = parser.parse_args()

    if args.mode == "snapshot":
        return run_snapshot(args)
    elif args.mode == "compare":
        return run_compare(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())