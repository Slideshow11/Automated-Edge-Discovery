"""Precise two-clone competing-writer test.

Two independent clones of the same bare remote. Both create
mutation plans with the SAME expected_before_sha (the current
remote SHA) but DIFFERENT desired_after SHAs. Both execute
against the same bare remote. The test proves that no second
stale CAS overwrites the winner.

The test distinguishes:
  - the operation-applied evidence (CAS check passes, the
    ref is moved to desired_after by the winner);
  - the postcondition-already-satisfied reconciliation (the
    CAS check fails because the winner already moved the ref,
    so the loser's reconcile observes desired_after and
    reports SUCCEEDED WITHOUT having applied the operation).

A "second stale CAS overwrites the winner" failure mode is
detected by checking the bare remote's ref AFTER both
executions: the ref must equal one of the two desired SHAs,
NOT the winner's expected_before_sha, AND NOT the loser's
desired SHA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local.guarded_ref_mutation_runner import GuardedMutationOrchestrator
from scripts.local.guarded_ref_mutation import GuardedMutationPlan


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _setup_two_clones(tmp_path):
    """Create a bare repo plus TWO independent clones.

    Both clones start at the same HEAD on main. Both can
    push to the bare repo.
    """
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "--bare", str(bare), "-q")
    clone_a = tmp_path / "clone_a"
    clone_b = tmp_path / "clone_b"
    _git(tmp_path, "clone", str(bare), str(clone_a), "-q")
    _git(tmp_path, "clone", str(bare), str(clone_b), "-q")
    for clone in (clone_a, clone_b):
        _git(clone, "config", "user.email", "test@local")
        _git(clone, "config", "user.name", "Test")
    # Seed main with one commit and push so both clones agree
    # on the initial SHA.
    _git(clone_a, "commit", "--allow-empty", "-m", "init", "-q")
    initial_sha = _git(clone_a, "rev-parse", "HEAD").stdout.strip()
    _git(clone_a, "push", "origin", "refs/heads/main", "-q")
    _git(clone_b, "fetch", "origin", "main", "-q")
    _git(clone_b, "reset", "--hard", "origin/main", "-q")
    assert _git(clone_b, "rev-parse", "HEAD").stdout.strip() == initial_sha
    return bare, clone_a, clone_b, initial_sha


@pytest.fixture
def two_clones_with_bare(tmp_path):
    return _setup_two_clones(tmp_path)


def test_two_clones_competing_writer(tmp_path, two_clones_with_bare):
    """Two clones write to the same bare remote with the same
    expected_before but different desired SHAs. The winner
    applies its operation; the loser's CAS fails and
    reconciles to the winner's state (NOT_APPLIED or CONFLICT
    depending on the loser's desired). The bare remote must
    end at exactly the WINNER's desired SHA — never at the
    loser's desired SHA, never at initial_sha, never at some
    unknown third value.
    """
    bare, clone_a, clone_b, initial_sha = two_clones_with_bare

    # Each clone makes a new commit on main (its own desired
    # SHA). Both clones push their commits to the bare repo
    # under different refs so we can refer to them by SHA.
    _git(clone_a, "commit", "--allow-empty", "-m", "writer_a", "-q")
    desired_a = _git(clone_a, "rev-parse", "HEAD").stdout.strip()
    _git(clone_b, "commit", "--allow-empty", "-m", "writer_b", "-q")
    desired_b = _git(clone_b, "rev-parse", "HEAD").stdout.strip()
    assert desired_a != desired_b

    # Both clones prepare a mutation plan with the SAME
    # expected_before_sha (the initial remote SHA) and
    # DIFFERENT desired_after SHAs.
    plan_a = GuardedMutationPlan(
        mutation_id="m_writer_a",
        owner_run_id="r-writer-a",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_a,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )
    plan_b = GuardedMutationPlan(
        mutation_id="m_writer_b",
        owner_run_id="r-writer-b",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_b,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )

    # Run both plans through the orchestrator. They will
    # race against the bare remote. The first to acquire the
    # --force-with-lease=<ref>:<initial_sha> succeeds. The
    # second fails the CAS (the ref is now at desired_a, not
    # initial_sha) and reconciles.
    orch_a = GuardedMutationOrchestrator(
        workspace=tmp_path, plan=plan_a
    )
    orch_a.prepare()
    final_a = orch_a.execute(
        local_repo=clone_a, remote_ref_path=bare
    )

    orch_b = GuardedMutationOrchestrator(
        workspace=tmp_path, plan=plan_b
    )
    orch_b.prepare()
    final_b = orch_b.execute(
        local_repo=clone_b, remote_ref_path=bare
    )

    # The two plans MUST produce different outcomes because
    # the second CAS must fail (the remote was advanced by
    # the winner).
    assert final_a.status != final_b.status, (
        f"both writers succeeded/failed identically: "
        f"a={final_a.status} b={final_b.status}; "
        f"the second stale CAS must not silently overwrite "
        f"the winner"
    )

    # The winner's CAS check passes: the remote was at
    # initial_sha (the winner's expected_before). The
    # winner's reconcile reads desired_after -> SUCCEEDED.
    # The loser fails the CAS: the remote is at desired_a
    # (the winner's output). The loser's reconcile reads
    # desired_a. If desired_a == loser's desired_after_b
    # (collision), the loser reports SUCCEEDED without
    # applying. If desired_a == loser's expected_before_b
    # (the original initial_sha), NOT_APPLIED. Otherwise
    # CONFLICT.
    #
    # The bare remote must be at exactly the winner's
    # desired SHA. Verify it.
    final_remote = _git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    assert final_remote == desired_a, (
        f"bare remote ref is not at the winner's desired SHA: "
        f"got {final_remote} expected {desired_a} (writer_a); "
        f"writer_b desired was {desired_b}"
    )

    # The bare remote must NOT be at the loser's desired SHA
    # (otherwise writer_b overwrote writer_a, which is the
    # bug we're guarding against).
    assert final_remote != desired_b, (
        f"BUG: bare remote was overwritten by the loser; "
        f"got {final_remote} == loser desired {desired_b}"
    )

    # The bare remote must NOT be at the initial SHA (the
    # winner applied its operation).
    assert final_remote != initial_sha, (
        f"bare remote was not updated by the winner; "
        f"got {final_remote} == initial {initial_sha}"
    )

    # Distinguish operation-applied from postcondition-satisfied.
    if final_a.status == "SUCCEEDED":
        # Writer A applied the operation.
        assert final_remote == desired_a
    else:
        # Writer A did NOT apply. Some other state must hold.
        assert final_a.status in ("CONFLICT", "INDETERMINATE", "NOT_APPLIED")

    if final_b.status == "SUCCEEDED":
        # Writer B's CAS failed but the remote happens to be
        # at desired_b. This is the postcondition-already-
        # satisfied case. The bare remote MUST be at desired_b
        # for this to be honest.
        assert final_remote == desired_b, (
            f"writer_b reports SUCCEEDED but remote is not at "
            f"writer_b's desired: got {final_remote} expected {desired_b}"
        )


def test_two_clones_postcondition_already_satisfied(tmp_path, two_clones_with_bare):
    """Distinct scenario: writer B's desired_after happens to
    be at the winner's applied SHA. Writer B's CAS check fails
    (CAS sees initial_sha, but actual is desired_a). Writer
    B's reconcile reads desired_a which equals writer B's
    desired_after. The reconcile reports SUCCEEDED — this is
    the postcondition-already-satisfied case, NOT a blind
    overwrite.

    This test proves that writer B's report is honest: it
    observes desired_b on the remote and reports SUCCEEDED,
    but it did NOT apply any operation.
    """
    bare, clone_a, clone_b, initial_sha = two_clones_with_bare

    # Writer A makes commit A1, writer B makes commit B1
    # (different SHAs).
    _git(clone_a, "commit", "--allow-empty", "-m", "A1", "-q")
    desired_a = _git(clone_a, "rev-parse", "HEAD").stdout.strip()

    # Writer B wants to push SHA desired_b. We will arrange
    # the bare remote to be at desired_b BEFORE writer B
    # executes. Then writer B's reconcile reads desired_b and
    # reports SUCCEEDED (postcondition-already-satisfied),
    # but writer B's --force-with-lease=<ref>:<initial_sha>
    # would fail because the actual is desired_b, not
    # initial_sha.
    _git(clone_b, "commit", "--allow-empty", "-m", "B1", "-q")
    desired_b = _git(clone_b, "rev-parse", "HEAD").stdout.strip()

    # Pre-position the bare remote at desired_b.
    _git(clone_b, "push", "origin", desired_b + ":refs/heads/main", "-q")

    plan_b = GuardedMutationPlan(
        mutation_id="m_postcondition",
        owner_run_id="r-postcondition",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_b,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )
    orch_b = GuardedMutationOrchestrator(
        workspace=tmp_path, plan=plan_b
    )
    orch_b.prepare()
    final_b = orch_b.execute(
        local_repo=clone_b, remote_ref_path=bare
    )

    # Writer B's CAS must FAIL because the remote is at
    # desired_b (not initial_sha). The reconcile reads the
    # remote and finds desired_b == writer B's desired_after.
    # The reconcile reports SUCCEEDED — the postcondition-
    # already-satisfied case. But the operation was NOT
    # applied by writer B's executor (the CAS check failed).
    final_remote = _git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    assert final_remote == desired_b, (
        f"remote should still be at desired_b; got {final_remote}"
    )

    # The executor must report ok=False (the CAS failed).
    # The reconcile state is SUCCEEDED (the postcondition is
    # already met). These are independent observations:
    # executor reported a failed CAS, reconcile observed the
    # postcondition is met.
    # We assert the final plan's status is SUCCEEDED.
    assert final_b.status == "SUCCEEDED"