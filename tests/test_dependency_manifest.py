"""
tests/test_dependency_manifest.py

Verifies the operational dependency manifest (requirements.txt) does not
contain active entries for packages confirmed unused by the opensrc
dependency audit 002.

Active requirement: a non-comment, non-blank line whose first token
(before whitespace or #) is a known package name.
"""

import re

import pytest
from pathlib import Path


REQUIREMENTS_PATH = Path("requirements.txt")
STALE_PACKAGES = {"pydantic", "requests", "httpx"}

# Version specifier characters to strip from package names
_VERSION_RE = re.compile(r"[>=<!\[\], ]+")


def parse_active_requirements(txt: str) -> set[str]:
    """Return the set of package names that are active (non-comment, non-blank) requirements.

    Handles:
    - Comment lines (starting with #) — skipped entirely
    - Active requirement lines — first token stripped of version specifiers
    - Inline comments — stripped before name extraction
    """
    active = set()
    for line in txt.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # Comment line — extract any commented-out package names
            # e.g. "# requests  # comment" -> package is "requests"
            inner = stripped.lstrip("#").strip()
            if inner:
                name = _VERSION_RE.split(inner)[0].lower()
                if name:
                    active.add(f"#{name}")  # mark as comment-only
            continue
        # Active line — drop inline comment
        name = stripped.split("#", 1)[0].strip()
        if not name:
            continue
        name = _VERSION_RE.split(name)[0].lower()
        if name:
            active.add(name)
    return active


def parse_commented_requirements(txt: str) -> set[str]:
    """Return set of package names that appear only in comment lines."""
    commented = set()
    for line in txt.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("#"):
            continue
        # e.g. "# requests   # HTTP library; ... " or "    # requests  # comment"
        inner = stripped.lstrip("#").strip()
        if not inner:
            continue
        # Drop inline comment
        name = inner.split("#", 1)[0].strip()
        if not name:
            continue
        name = _VERSION_RE.split(name)[0].lower()
        if name:
            commented.add(name)
    return commented


def test_stale_dependencies_removed_from_requirements():
    """pydantic, requests, httpx must not appear as active requirements."""
    content = REQUIREMENTS_PATH.read_text()
    active = parse_active_requirements(content)
    stale = active & STALE_PACKAGES
    assert not stale, (
        f"Stale packages found as active requirements: {sorted(stale)}. "
        f"These should be commented out or removed."
    )


def test_patsy_remains_deferred():
    """patsy must remain active pending numeric workflow verification."""
    content = REQUIREMENTS_PATH.read_text()
    active = parse_active_requirements(content)
    # patsy should still be active — removal is deferred to a separate verification PR
    assert "patsy" in active, (
        "patsy was incorrectly removed — it must remain active pending "
        "separate statsmodels numeric workflow verification"
    )


def test_known_runtime_dependencies_still_present():
    """Sanity check: confirmed runtime deps must still be active."""
    content = REQUIREMENTS_PATH.read_text()
    active = parse_active_requirements(content)
    must_have = {"openai", "pyyaml", "aiohttp", "rich", "numpy", "pandas", "statsmodels"}
    missing = must_have - active
    assert not missing, f"Runtime dependencies missing from manifest: {sorted(missing)}"


def test_requirements_file_not_empty():
    """The requirements file must have content after parsing."""
    content = REQUIREMENTS_PATH.read_text()
    active = parse_active_requirements(content)
    assert active, "requirements.txt has no active requirements — file may be empty or fully commented"


def test_commented_stale_packages_are_commented():
    """Stale package names should appear in comment lines in requirements.txt."""
    content = REQUIREMENTS_PATH.read_text()
    commented = parse_commented_requirements(content)
    stale_in_commented = commented & STALE_PACKAGES
    # At least one of the stale packages should be present as a comment
    assert stale_in_commented, (
        f"Stale packages {STALE_PACKAGES} not found in requirements.txt as comments. "
        f"File may have been incorrectly truncated. Commented packages found: {commented}"
    )