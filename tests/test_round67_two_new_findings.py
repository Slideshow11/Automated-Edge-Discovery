#!/usr/bin/env python3
"""
Round-67 regression tests for the two new Codex findings on commit f1deee1.

Verifies two findings addressed by the round-67 branch:

  R. PRRT_kwDOSHFpYM6Vz0mn  Canonicalize repository forms in
     cross-scope checks. _check_cross_scope_conflict and
     _repo_sentinel_path must use the canonical (owner/name)
     identity, not the raw repository string, so two
     controllers expressing the same repository in different
     forms (e.g. `owner/repo` and
     `https://github.com/owner/repo.git`) produce the same
     scope key, the same per-repository sentinel filename,
     and the same cross-scope conflict comparison.

  S. PRRT_kwDOSHFpYM6Vz0mo  Defer RUN_COMPLETE until the
     lease can be released. The state is marked
     RUN_COMPLETE only AFTER the supervisor lease is
     successfully released; otherwise the state is left in
     its prior status so a successor run can recover the
     orphan lease via explicit recovery.

Findings deferred to follow-up commits (unchanged from
prior rounds):

  L. PRRT_kwDOSHFpYM6VzXFA  Hold the repository sentinel
     through lease publication. (Documented in Round-62.)

  M. PRRT_kwDOSHFpYM6VzmEz  Hold the scope sentinel through
     authorization. (Documented in Round-64.)

  N. PRRT_kwDOSHFpYM6VzmE0  Enforce target exclusion before
     upgrading a PR authorization. (Documented in Round-64.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import aed_supervisor_lock as sl


# ---------------------------------------------------------------------------
# Finding R — canonicalize repository forms in cross-scope checks
# ---------------------------------------------------------------------------

def test_r_repo_sentinel_path_canonicalizes_forms(tmp_path):
    """R.1: _repo_sentinel_path uses the canonical identity
    so two accepted forms of the same repository produce
    the same sentinel filename."""
    base_dir = tmp_path / "locks"
    base_dir.mkdir()
    path_a = sl._repo_sentinel_path("owner/repo", base_dir)
    path_b = sl._repo_sentinel_path(
        "https://github.com/owner/repo.git", base_dir
    )
    path_c = sl._repo_sentinel_path(
        "git@github.com:owner/repo.git", base_dir
    )
    assert path_a == path_b == path_c, (
        f"per-repository sentinel must be identical for the same "
        f"repository in different forms; got {path_a}, {path_b}, {path_c}"
    )


def test_r_repo_sentinel_path_normalizes_case(tmp_path):
    """R.2: case differences in the repository name
    normalize to the same sentinel path."""
    base_dir = tmp_path / "locks"
    base_dir.mkdir()
    path_a = sl._repo_sentinel_path("owner/repo", base_dir)
    path_b = sl._repo_sentinel_path("OWNER/Repo", base_dir)
    assert path_a == path_b, (
        f"case differences must not produce different sentinel "
        f"paths; got {path_a} vs {path_b}"
    )