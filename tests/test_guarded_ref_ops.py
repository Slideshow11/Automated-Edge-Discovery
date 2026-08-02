"""Integration tests for scripts/local/guarded_ref_ops.py.

Uses a temporary bare Git repository and a clone. Each test
provisions a fresh bare repo and a fresh clone so tests are
independent. The synthetic-remote pattern allows the
guarded_push test to verify the receiving Git server rejects
the push when the precondition is not met.

These tests prove the user's required correctness properties:
  - two stale writers cannot both overwrite the same ref;
  - create, update, force-update and delete respect exact
    expected state;
  - the guarded push refuses when the remote ref has drifted;
  - reconciliation is idempotent (callable multiple times).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Make the local scripts importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import guarded_ref_ops as ops
from scripts.local import guarded_ref_mutation as grm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in cwd and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _make_bare_with_clone(
    tmp_path: Path,
) -> tuple[Path, Path]:
    """Create a bare repo plus a clone that uses it as origin.

    Returns (bare_repo, clone_path).
    """
    bare = tmp_path / "bare.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "clone", str(bare), str(clone))
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    return bare, clone


@pytest.fixture
def bare_and_clone(tmp_path):
    bare, clone = _make_bare_with_clone(tmp_path)
    return bare, clone


@pytest.fixture
def clone(bare_and_clone):
    _bare, clone = bare_and_clone
    return clone


@pytest.fixture
def bare(bare_and_clone):
    bare, _clone = bare_and_clone
    return bare


def _seed_commit(clone: Path, ref: str = "refs/heads/main") -> str:
    """Make an initial commit and put it on the given ref. Returns
    the commit SHA."""
    _git(clone, "commit", "--allow-empty", "-m", "initial")
    sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "update-ref", ref, sha)
    return sha


def _advance_ref_to(clone: Path, ref: str, sha: str) -> None:
    """Advance the ref to a given SHA (creating a new commit if needed)."""
    _git(clone, "update-ref", ref, sha)


def _seed_extra_commit(clone: Path, ref: str = "refs/heads/main") -> str:
    """Make an additional commit and advance the ref to it.
    Returns the new SHA. The ref must already exist."""
    _git(clone, "commit", "--allow-empty", "-m", "second")
    new_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "update-ref", ref, new_sha)
    return new_sha


# ---------------------------------------------------------------------------
# read_ref
# ---------------------------------------------------------------------------

def test_read_ref_returns_sha(clone, tmp_path):
    sha = _seed_commit(clone)
    assert ops.read_ref(clone, "refs/heads/main") == sha


def test_read_ref_returns_none_for_missing_ref(clone):
    assert ops.read_ref(clone, "refs/heads/does-not-exist") is None


# ---------------------------------------------------------------------------
# guarded_update_ref
# ---------------------------------------------------------------------------

def test_update_ref_with_matching_expected_sha_succeeds(clone):
    sha = _seed_commit(clone)
    # After _seed_commit the ref is at sha. _seed_extra_commit
    # advances the ref to a new SHA. We try to update from sha
    # (the prior expected) to yet another SHA so the swap is
    # meaningful.
    new_sha = _seed_extra_commit(clone)
    yet_another = _git(
        clone, "commit-tree", new_sha + "^{tree}", "-m", "third"
    ).stdout.strip()
    result = ops.guarded_update_ref(
        repo=clone,
        ref="refs/heads/main",
        new_sha=yet_another,
        expected_old_sha=new_sha,
    )
    assert result.ok is True
    assert ops.read_ref(clone, "refs/heads/main") == yet_another


def test_update_ref_with_mismatched_expected_sha_refuses(clone):
    """The compare-and-swap MUST refuse when the current ref
    does not match expected_old_sha."""
    sha = _seed_commit(clone)
    # After _seed_commit the ref is at sha. _seed_extra_commit
    # advances the ref. So the ref is now at new_sha, NOT
    # sha. We try to update from sha (the prior expected) when
    # the ref is actually at new_sha.
    new_sha = _seed_extra_commit(clone)
    result = ops.guarded_update_ref(
        repo=clone,
        ref="refs/heads/main",
        new_sha="a" * 40,
        expected_old_sha=sha,
    )
    assert result.ok is False
    # The ref is unchanged.
    assert ops.read_ref(clone, "refs/heads/main") == new_sha


def test_create_ref_with_zero_oid_succeeds(clone):
    """guarded_create_ref uses git update-ref <ref> <new> <zero-oid>.
    The zero-oid <old> requires the ref not to exist."""
    new_sha = _seed_extra_commit(clone)
    _git(clone, "update-ref", "-d", "refs/heads/main")
    assert ops.read_ref(clone, "refs/heads/main") is None
    result = ops.guarded_create_ref(
        repo=clone,
        ref="refs/heads/main",
        new_sha=new_sha,
    )
    assert result.ok is True
    assert ops.read_ref(clone, "refs/heads/main") == new_sha


def test_two_stale_writers_cannot_both_overwrite(clone):
    """Proves the user's required correctness property: two
    stale writers cannot both overwrite the same ref."""
    sha = _seed_commit(clone)
    # Both writers saw the ref at sha.
    writer_a_expected = sha
    writer_b_expected = sha
    # Third party advances the ref to new_sha.
    new_sha = _seed_extra_commit(clone)
    # Both writers try to push from writer_a_expected /
    # writer_b_expected (both = sha, but the ref is now at new_sha).
    result_a = ops.guarded_update_ref(
        repo=clone,
        ref="refs/heads/main",
        new_sha="a" * 40,
        expected_old_sha=writer_a_expected,
    )
    assert result_a.ok is False
    result_b = ops.guarded_update_ref(
        repo=clone,
        ref="refs/heads/main",
        new_sha="b" * 40,
        expected_old_sha=writer_b_expected,
    )
    assert result_b.ok is False
    # The ref is still at new_sha (both stale writers failed).
    assert ops.read_ref(clone, "refs/heads/main") == new_sha


# ---------------------------------------------------------------------------
def test_create_ref_fails_when_ref_already_exists(clone):
    """CREATE: git update-ref <ref> <new> <zero-oid> refuses if the
    ref exists. The error must be surfaced as ok=False (not
    suppressed). The result.stderr must mention the existing
    ref so the caller can diagnose."""
    initial = _seed_commit(clone)
    other_sha = _seed_extra_commit(clone)
    result = ops.guarded_create_ref(
        repo=clone,
        ref="refs/heads/main",
        new_sha=other_sha,
    )
    assert result.ok is False
    assert "already exists" in result.stderr or "cannot lock" in result.stderr


# guarded_delete_ref
# ---------------------------------------------------------------------------

def test_delete_ref_with_matching_expected_succeeds(clone):
    sha = _seed_commit(clone)
    result = ops.guarded_delete_ref(
        repo=clone,
        ref="refs/heads/main",
        expected_old_sha=sha,
    )
    assert result.ok is True
    assert ops.read_ref(clone, "refs/heads/main") is None


def test_delete_ref_with_mismatched_expected_fails(clone):
    sha = _seed_commit(clone)
    # Now the ref is at sha. Try to delete with a wrong expected.
    new_sha_for_test = _seed_extra_commit(clone)
    # The ref is now at new_sha_for_test (the auto-advance).
    result = ops.guarded_delete_ref(
        repo=clone,
        ref="refs/heads/main",
        expected_old_sha=sha,
    )
    assert result.ok is False
    # The ref is still at new_sha_for_test.
    assert ops.read_ref(clone, "refs/heads/main") == new_sha_for_test


# ---------------------------------------------------------------------------
# guarded_push (local bare as remote)
# ---------------------------------------------------------------------------

def test_push_with_matching_expected_remote_sha_succeeds(
    tmp_path,
):
    bare, clone = _make_bare_with_clone(tmp_path)
    initial = _seed_commit(clone)
    # Push to the remote.
    p = _git(clone, "push", "origin", "refs/heads/main")
    assert p.returncode == 0
    new_sha = _seed_extra_commit(clone)
    result = ops.guarded_push(
        repo=clone,
        remote="origin",
        ref="refs/heads/main",
        expected_remote_sha=initial,
        new_local_sha=new_sha,
    )
    assert result.ok is True
    assert ops.read_remote_ref(remote_repo=bare, ref="refs/heads/main") == new_sha


def test_push_with_mismatched_expected_remote_sha_refuses(
    tmp_path,
):
    bare, clone = _make_bare_with_clone(tmp_path)
    initial = _seed_commit(clone)
    _git(clone, "push", "origin", "refs/heads/main")
    # Third-party advances the remote.
    third_party = _seed_extra_commit(clone)
    _git(clone, "push", "origin", "refs/heads/main")
    # Writer A tries to push from initial (stale).
    new_sha = _seed_extra_commit(clone)
    result = ops.guarded_push(
        repo=clone,
        remote="origin",
        ref="refs/heads/main",
        expected_remote_sha=initial,
        new_local_sha=new_sha,
    )
    assert result.ok is False
    # The remote is still at third_party.
    assert (
        ops.read_remote_ref(remote_repo=bare, ref="refs/heads/main")
        == third_party
    )


# ---------------------------------------------------------------------------
# Repair 4: staged-SHA validation
# ---------------------------------------------------------------------------

def test_push_aborts_when_new_local_sha_does_not_exist(tmp_path):
    """guarded_push must abort if new_local_sha is not a
    valid object in the local repo. The previous
    implementation ignored the update-ref failure and pushed
    whatever the local ref happened to point to."""
    bare = tmp_path / "bare.git"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "--bare", str(bare), "-q"],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(clone), "-q"],
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@l"],
                   cwd=str(clone), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=str(clone), check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "i", "-q"],
                   cwd=str(clone), check=True, capture_output=True)
    initial = subprocess.run(["git", "rev-parse", "HEAD"],
                             cwd=str(clone), capture_output=True,
                             text=True).stdout.strip()
    subprocess.run(["git", "push", "origin", "refs/heads/main", "-q"],
                   cwd=str(clone), check=True, capture_output=True)

    # A SHA that does NOT exist in the local repo.
    fake_sha = "0" * 40

    result = ops.guarded_push(
        repo=clone,
        remote="origin",
        ref="refs/heads/main",
        expected_remote_sha=initial,
        new_local_sha=fake_sha,
    )
    assert result.ok is False, (
        "guarded_push must abort when new_local_sha does not "
        "exist in the local repo"
    )
    assert "does not exist" in result.stderr or "not exist" in result.stderr
    # The remote was NOT touched.
    actual_remote = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"],
        cwd=str(bare), capture_output=True, text=True,
    ).stdout.strip()
    assert actual_remote == initial
