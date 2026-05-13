#!/usr/bin/env python3
"""Mechanical PR scope diff enforcement.

Compares actual changed files against allowed_files and forbidden_files
without relying on prompts or agent self-assessment.

Does NOT call git. Does NOT mutate repo state. Does NOT merge.

Exit codes:
  0 = clean scope
  1 = scope violation or unknown
  2 = invalid arguments, invalid JSON, missing input file, malformed list

Usage (inline comma-separated):
  python3 scripts/local/check_pr_scope.py \\
    --changed-files "scripts/local/foo.py,tests/test_foo.py" \\
    --allowed-files "scripts/local/foo.py,tests/test_foo.py" \\
    --forbidden-files ".github/workflows/,engine/" \\
    --output-json /tmp/PR_SCOPE_CHECK.json

Usage (JSON file paths):
  python3 scripts/local/check_pr_scope.py \\
    --changed-files-json /tmp/changed_files.json \\
    --allowed-files-json /tmp/allowed_files.json \\
    --forbidden-files-json /tmp/forbidden_files.json \\
    --output-json /tmp/PR_SCOPE_CHECK.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PACKET_KIND = "aed.pr_gate.scope_check.v1"
SCHEMA_VERSION = 1


# ── Normalization ────────────────────────────────────────────────────────────────

def normalize_path(path: str) -> str:
    """Normalize a file path: strip ./, replace \\ with /."""
    p = path.strip()
    if p.startswith("./"):
        p = p[2:]
    elif p.startswith(".\\"):
        p = p[2:]
    return p.replace("\\", "/")


def normalize_list(paths: list[str]) -> list[str]:
    """Normalize a list of paths, deduplicate, preserve order."""
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        n = normalize_path(p)
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


# ── Glob matching ─────────────────────────────────────────────────────────────

def matches_glob(path: str, pattern: str) -> bool:
    """Return True if path matches the glob pattern.

    Supports:
      docs/**          — matches docs/ and all subdirectories recursively
      docs/**/*.md     — matches docs/a/b/file.md, docs/file.md (not docs itself)
      scripts/local/*.py — matches scripts/local/foo.py ONLY (not nested)
      *.md             — matches README.md at root level
      docs/file.md     — exact match
      f*.py            — matches f.py, foo.py, foo.bar.py (backtracking *)

    Unlike fnmatch, * does not cross / boundaries.
    ** matches zero or more complete path segments.
    """
    # Handle trailing / in directory-style patterns (e.g. "engine/" matches "engine/core.py")
    dir_suffix = False
    if pattern.endswith("/"):
        dir_suffix = True
        pattern = pattern[:-1]
        # After stripping /, we need path to be exactly the prefix OR start with prefix/
        if path == pattern or path.startswith(pattern + "/"):
            return True
        return False

    # Split pattern by **
    parts = pattern.split("**")
    n_parts = len(parts)

    if n_parts == 1:
        # No ** in pattern — segment-by-segment match
        return _match_segments(path, pattern.split("/"))

    if n_parts >= 3:
        # Multiple ** — process sequentially: check each **/suffix constraint
        # Reduce to two-part case by processing left-to-right
        return _match_multi_glob(path, parts)

    # Exactly one ** in pattern (n_parts == 2)
    prefix = parts[0]
    suffix = parts[1]  # may be empty string

    if not path.startswith(prefix):
        return False

    remaining = path[len(prefix):]

    if not suffix:
        # Pattern ends with ** — remaining can be anything
        return True

    return _ends_with_glob(remaining, suffix)


def _match_multi_glob(path: str, parts: list[str]) -> bool:
    """Match path against a pattern with multiple ** occurrences.

    parts: [prefix, middle1, middle2, ..., suffix]
    The pattern is: prefix + ** + middle1 + ** + middle2 + ... + suffix
    We require: path starts with prefix, then for each (middle, next_prefix):
      remaining must end with next_prefix
    """
    # Process sequentially
    remaining = path
    for idx in range(len(parts) - 1):
        prefix_here = parts[idx]
        suffix_here = parts[idx + 1]
        if idx == 0:
            # First part is a literal prefix
            if not remaining.startswith(prefix_here):
                return False
            remaining = remaining[len(prefix_here):]
        # remaining must match: ** + suffix_here + (rest)
        if not remaining.startswith("**"):
            # Need ** — not found
            return False
        remaining = remaining[2:]  # consume **
        # Now remaining must end with suffix_here
        if not remaining.endswith(suffix_here):
            return False
        remaining = remaining[:len(remaining) - len(suffix_here)]
    # After processing all but last part
    last_part = parts[-1]
    if last_part:
        if not remaining.endswith(last_part):
            return False
    return True


def _ends_with_glob(path: str, suffix_pattern: str) -> bool:
    """Check if path ends with a glob suffix (where ** matches zero or more segments).

    suffix_pattern examples:
      "/*.py"          → path must end with .py (no dir prefix)
      "/**/*.md"       → path must end with .md (any number of dir segments before)
      "/*.py"          → suffix starts with /
      ".py"            → suffix has no leading /
    """
    # Strip leading / from suffix for comparison
    if suffix_pattern.startswith("/"):
        suffix_inner = suffix_pattern[1:]
    else:
        suffix_inner = suffix_pattern

    # Determine the required final filename pattern (after last /)
    if "/" in suffix_inner:
        suffix_dir_idx = suffix_inner.index("/")
        suffix_dir = suffix_inner[:suffix_dir_idx]
        suffix_file = suffix_inner[suffix_dir_idx + 1:]
    else:
        suffix_dir = None
        suffix_file = suffix_inner

    # path must not start with / (we're comparing relative remainders)
    if path.startswith("/"):
        path = path[1:]

    if not path:
        return False

    # Check final filename matches (with backtracking * matching)
    if suffix_file:
        if "/" in path:
            fname = path.rsplit("/", 1)[-1]
        else:
            fname = path
        if not _segment_matches(fname, suffix_file):
            return False

    # Check directory suffix matches
    if suffix_dir is not None:
        if suffix_dir == "**":
            return True  # any directory structure OK
        # Find the directory part before the filename
        if "/" in path:
            dir_part = path.rsplit("/", 1)[0]
        else:
            return False  # filename only, no directory to match suffix_dir
        if not _glob_match_segment(dir_part, suffix_dir):
            return False

    return True


def _segment_matches(text: str, pattern: str) -> bool:
    """Match a single path segment against a glob pattern (no / in text).
    Supports *, ?, [abc], [a-z] with proper backtracking.
    """
    # Pre-scan for special chars
    has_glob = any(c in pattern for c in "*?[")
    if not has_glob:
        return text == pattern

    if not pattern:
        return not text

    if not text:
        return all(c in "*?" for c in pattern)

    p0 = pattern[0]
    if p0 not in "*?[":
        if not text or text[0] != p0:
            return False
        return _segment_matches(text[1:], pattern[1:])

    if p0 == "*":
        # Try * matching empty, then pattern[1:]
        if _segment_matches(text, pattern[1:]):
            return True
        # Try * matching 1..n chars, recursing
        for i in range(1, len(text) + 1):
            prefix = text[:i]
            suffix = text[i:]
            if _glob_suffix_match(prefix, "*") and _segment_matches(suffix, pattern[1:]):
                return True
        return False

    if p0 == "?":
        return bool(text) and _segment_matches(text[1:], pattern[1:])

    if p0 == "[":
        # Parse character class, then check and recurse
        end = pattern.index("]", 1)
        cls = pattern[1:end]
        if text and _char_in_class(text[0], cls):
            return _segment_matches(text[1:], pattern[end + 1:])
        return False

    return False


def _glob_suffix_match(text: str, pattern: str) -> bool:
    """Check if text matches a suffix pattern that may contain *.

    Used when we need to verify a prefix matched by * is valid
    (i.e., the * portion itself contains no conflicts).
    """
    # A pure * matches anything (including empty)
    if pattern == "*":
        return True
    # A pattern ending with * matches anything
    if pattern.endswith("*"):
        return True
    # For our backtracking, we use _literal_suffix_match
    return _literal_suffix_match(text, pattern)


def _glob_suffix_check(text: str, pattern: str) -> bool:
    """Check if text (remaining portion) matches suffix pattern.

    This is suffix matching: does text end with pattern?
    pattern may contain * (meaning "match anything until next literal char
    or end of string").

    Returns True if text ends with pattern (with * interpreted as any chars).
    """
    if not pattern:
        return not text  # empty pattern matches only empty string
    if not text:
        return all(c in "*?" for c in pattern)  # only wildcards match empty

    p0 = pattern[0]
    if p0 == "*":
        # * at start — matches 0 or more chars
        # Try * matching empty (just check rest of pattern)
        if _glob_suffix_check(text, pattern[1:]):
            return True
        # Try * matching 1..n chars
        for i in range(1, len(text) + 1):
            if _glob_suffix_check(text[i:], pattern[1:]):
                return True
        return False

    if p0 == "?":
        return bool(text) and _glob_suffix_check(text[1:], pattern[1:])

    if p0 == "[":
        end = pattern.index("]", 1)
        cls = pattern[1:end]
        return bool(text) and _char_in_class(text[0], cls) and _glob_suffix_check(text[1:], pattern[end + 1:])

    # Literal character — must match text[0]
    if text and text[0] == p0:
        return _glob_suffix_check(text[1:], pattern[1:])
    return False


def _char_in_class(char: str, cls: str) -> bool:
    """Check if char is in a parsed character class string."""
    i = 0
    negate = False
    if cls.startswith("!") or cls.startswith("^"):
        negate = True
        i = 1
    chars = set()
    while i < len(cls):
        if i + 2 < len(cls) and cls[i + 1] == "-":
            for c in range(ord(cls[i]), ord(cls[i + 2]) + 1):
                chars.add(chr(c))
            i += 3
        else:
            chars.add(cls[i])
            i += 1
    in_cls = char in chars
    return not in_cls if negate else in_cls


def _literal_suffix_match(text: str, pattern: str) -> bool:
    """Match text exactly against pattern which may contain ?, [], but no *."""
    i = j = 0
    while i < len(pattern) and j < len(text):
        p = pattern[i]
        if p == "?":
            i += 1
            j += 1
        elif p == "[":
            i += 1
            negate = False
            if i < len(pattern) and pattern[i] == "!":
                negate = True
                i += 1
            chars = set()
            while i < len(pattern) and pattern[i] != "]":
                if i + 2 < len(pattern) and pattern[i + 1] == "-":
                    c_start, c_end = pattern[i], pattern[i + 2]
                    for c in range(ord(c_start), ord(c_end) + 1):
                        chars.add(chr(c))
                    i += 3
                else:
                    chars.add(pattern[i])
                    i += 1
            i += 1  # past ]
            matched = j < len(text) and text[j] in chars
            if negate:
                matched = not matched
            if not matched:
                return False
            j += 1
        else:
            if p != text[j]:
                return False
            i += 1
            j += 1

    # Handle trailing ? (each ? matches one char)
    while i < len(pattern) and pattern[i] == "?":
        i += 1
        j += 1
    return i == len(pattern) and j == len(text)


def _glob_match_segment(text: str, pattern: str) -> bool:
    """Match text (no /) against a glob pattern. Supports * ? [abc]."""
    return _segment_matches(text, pattern)


def _match_segments(path: str, pattern_segments: list[str]) -> bool:
    """Match path segments against pattern segments (no ** in pattern).

    Key invariant: * in a pattern segment matches ONLY that segment (no /).
    Extra path segments beyond the pattern MUST be rejected unless the last
    pattern segment is * and no extra segments remain after matching.
    """
    path_segments = path.split("/")

    if len(pattern_segments) > len(path_segments):
        return False

    # Match each pattern segment against path segments
    for i, pat in enumerate(pattern_segments):
        is_last_pat = (i == len(pattern_segments) - 1)
        path_seg_idx = i
        if path_seg_idx >= len(path_segments):
            return False
        seg = path_segments[path_seg_idx]

        if not _glob_match_segment(seg, pat):
            return False

    # All pattern segments matched. Check for extra path segments.
    n_extra = len(path_segments) - len(pattern_segments)
    if n_extra > 0:
        # Extra path segments exist — this is a violation
        # docs/* should NOT cover docs/a/b.md (two extra segments after *)
        return False

    return True


def path_matches_any(path: str, patterns: list[str]) -> bool:
    """Return True if path matches any pattern in the list."""
    for p in patterns:
        if matches_glob(path, p):
            return True
    return False


# ── Scope checking ─────────────────────────────────────────────────────────────

def check_scope(
    changed_files: list[str],
    allowed_files: list[str],
    forbidden_files: list[str],
) -> dict:
    """Check changed files against allowed and forbidden scopes.

    Returns an aed.pr_gate.scope_check.v1 packet.
    """
    changed = normalize_list(changed_files)
    allowed = normalize_list(allowed_files) if allowed_files else []
    forbidden = normalize_list(forbidden_files) if forbidden_files else []

    blockers: list[str] = []
    out_of_scope_files: list[str] = []
    forbidden_files_touched: list[str] = []

    # Rule: allowed_files missing/empty → unknown
    if not allowed:
        blockers.append("allowed_files_missing")
        out_of_scope_files = list(changed)

    else:
        # Check each changed file
        for f in changed:
            if not path_matches_any(f, allowed):
                out_of_scope_files.append(f)

        if out_of_scope_files:
            blockers.append("changed_file_outside_allowed_scope")

    # Check forbidden files
    for f in changed:
        if path_matches_any(f, forbidden):
            forbidden_files_touched.append(f)
            if "forbidden_file_touched" not in blockers:
                blockers.append("forbidden_file_touched")

    # Determine scope_status
    if not allowed:
        scope_status = "unknown"
    elif out_of_scope_files or forbidden_files_touched:
        scope_status = "violation"
    else:
        scope_status = "clean"

    passed = scope_status == "clean"

    now = datetime.now(timezone.utc)

    packet = {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "changed_files": changed,
        "allowed_files": allowed,
        "forbidden_files": forbidden,
        "scope_status": scope_status,
        "out_of_scope_files": out_of_scope_files,
        "forbidden_files_touched": forbidden_files_touched,
        "blockers": blockers,
        "passed": passed,
    }
    return packet


# ── CLI helpers ─────────────────────────────────────────────────────────────────

def parseCommaList(value: str) -> list[str]:
    """Parse a comma-separated string into a list, stripping whitespace."""
    if not value or not value.strip():
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def load_json_list(path: str) -> tuple[list[str] | None, str]:
    """Load a JSON file containing a list of strings."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, f"file not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in {path}: {e}"
    except Exception as e:
        return None, f"failed to read {path}: {e}"

    if not isinstance(data, list):
        return None, f"{path} does not contain a JSON list"
    for item in data:
        if not isinstance(item, str):
            return None, f"{path} contains non-string item: {item!r}"
    return data, ""


def render_markdown(packet: dict) -> str:
    """Render a human-readable scope check report."""
    status = packet["scope_status"]
    passed = packet["passed"]
    blockers = packet["blockers"]
    out_of_scope = packet["out_of_scope_files"]
    forbidden_touched = packet["forbidden_files_touched"]

    icon = "✅" if passed else "❌"

    lines = [
        "# PR Scope Check",
        "",
        f"**Status:** {icon} `{status}`",
        f"**Passed:** {passed}",
        "",
    ]

    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"  - ❌ `{b}`")
        lines.append("")

    if out_of_scope:
        lines.append("## Out-of-Scope Files")
        for f in out_of_scope:
            lines.append(f"  - `{f}`")
        lines.append("")

    if forbidden_touched:
        lines.append("## Forbidden Files Touched")
        for f in forbidden_touched:
            lines.append(f"  - ❌ `{f}`")
        lines.append("")

    lines += [
        "## Changed Files",
        f"_{len(packet['changed_files'])} files_",
        "",
        "## Allowed Files",
        f"_{len(packet['allowed_files'])} files_",
        "",
        "## Forbidden Patterns",
        f"_{len(packet['forbidden_files'])} patterns_",
    ]

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Mechanical PR scope diff enforcement. "
                    "Does NOT call git. Does NOT mutate repo state.",
    )

    # JSON file inputs
    p.add_argument(
        "--changed-files-json", type=str, default=None,
        help="Path to JSON file containing list of changed file paths"
    )
    p.add_argument(
        "--allowed-files-json", type=str, default=None,
        help="Path to JSON file containing list of allowed file patterns"
    )
    p.add_argument(
        "--forbidden-files-json", type=str, default=None,
        help="Path to JSON file containing list of forbidden file patterns"
    )

    # Inline comma-separated inputs
    p.add_argument(
        "--changed-files", type=str, default=None,
        help="Comma-separated list of changed file paths"
    )
    p.add_argument(
        "--allowed-files", type=str, default=None,
        help="Comma-separated list of allowed file patterns"
    )
    p.add_argument(
        "--forbidden-files", type=str, default=None,
        help="Comma-separated list of forbidden file patterns"
    )

    # Output
    p.add_argument(
        "--output-json", type=str, default=None,
        help="Path to write PR_SCOPE_CHECK.json output"
    )
    p.add_argument(
        "--output-md", type=str, default=None,
        help="Path to write PR_SCOPE_CHECK.md report"
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Load changed files
    if args.changed_files_json:
        changed, err = load_json_list(args.changed_files_json)
        if changed is None:
            print(f"ERROR: {err}", file=sys.stderr)
            return 2
        changed_files: list[str] = changed
    elif args.changed_files:
        changed_files = parseCommaList(args.changed_files)
    else:
        print("ERROR: --changed-files or --changed-files-json is required", file=sys.stderr)
        return 2

    # Load allowed files
    allowed_files: list[str] = []
    if args.allowed_files_json:
        allowed, err = load_json_list(args.allowed_files_json)
        if allowed is None:
            print(f"ERROR: {err}", file=sys.stderr)
            return 2
        allowed_files = allowed
    elif args.allowed_files:
        allowed_files = parseCommaList(args.allowed_files)
    # allowed_files=[] is valid (→ unknown scope_status)

    # Load forbidden files
    forbidden_files: list[str] = []
    if args.forbidden_files_json:
        forbidden, err = load_json_list(args.forbidden_files_json)
        if forbidden is None:
            print(f"ERROR: {err}", file=sys.stderr)
            return 2
        forbidden_files = forbidden
    elif args.forbidden_files:
        forbidden_files = parseCommaList(args.forbidden_files)

    # Run scope check
    packet = check_scope(changed_files, allowed_files, forbidden_files)

    # Write JSON output
    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        print(f"JSON written to {args.output_json}", file=sys.stderr)

    # Write markdown report
    if args.output_md:
        Path(args.output_md).write_text(
            render_markdown(packet) + "\n", encoding="utf-8"
        )
        print(f"Markdown written to {args.output_md}", file=sys.stderr)

    # Print summary to stdout
    status = packet["scope_status"]
    blockers = packet["blockers"]
    print(f"scope_status={status} passed={packet['passed']}")
    if blockers:
        for b in blockers:
            print(f"  blocker: {b}")

    # Exit code
    if not packet["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
