"""AED guarded Git operations (the Git adapter).

The actual compare-and-swap operations that the executor
(Layer 4+) calls. The correctness here is delegated to Git:

  - For local ref mutations: git update-ref <ref> <new> <old>
    Refuses if the ref's current value differs from <old>.
  - For local create: git update-ref <ref> <new> <zero-oid>
    The zero-oid <old> requires the ref not to exist.
  - For local delete: git update-ref <ref> <zero-oid> <old>
    The zero-oid <new> requests deletion; <old> requires the
    ref's current value must match.
  - For remote push: git push --force-with-lease=<full-ref>:<sha>
    The receiving Git server refuses if the remote ref's current
    value differs from <sha>.
  - For empty exact expectation on remote push: use the
    empty-string form (the receiving server rejects the push
    if the ref exists).

Domain convention: None means "the ref does not exist". The
adapter converts None to the appropriate git argument:

  - local CREATE: --old=<zero-oid>
  - local DELETE: --new=<zero-oid>
  - remote push CREATE: --force-with-lease=<full-ref>:<empty>

Layer 3 of the Round-52-fix architectural repair.

No controller integration. No journal sentinel. No GraphQL
GitHub API.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scripts.local import guarded_ref_mutation as grm


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class RefMutationResult:
    """The result of a guarded ref mutation.

    ok: True iff the mutation succeeded.
    actual_ref_sha: the actual ref value AFTER the mutation
        (or BEFORE if the mutation failed). None for "ref does
        not exist" at reconciliation time.
    stdout: the captured stdout from the git subprocess.
    stderr: the captured stderr from the git subprocess.
    returncode: the subprocess return code.
    """

    ok: bool
    actual_ref_sha: Optional[str]
    stdout: str
    stderr: str
    returncode: int


class GuardedRefError(RuntimeError):
    """Raised by the guarded operations when an expected
    invariant is violated (e.g. update-ref reports a mismatch)."""


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_ref(repo: Path, ref: str) -> Optional[str]:
    """Return the SHA the ref currently points to, or None if the
    ref does not exist.

    Treats "" from git rev-parse as None. Any other nonzero
    return raises GuardedRefError.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        out = result.stdout.strip()
        return grm.oid_from_git(out)
    # git rev-parse returns nonzero for non-existent refs.
    err = result.stderr
    if (
        "unknown revision" in err
        or "Needed a single revision" in err
    ):
        return None
    raise GuardedRefError(
        f"git rev-parse {ref} failed: {err.strip()}"
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _update_ref(
    *,
    repo: Path,
    ref: str,
    new_sha: Optional[str],
    expected_old_sha: Optional[str],
    delete: bool,
) -> RefMutationResult:
    """Run git update-ref with the right argument shape.

    For CREATE: new_sha is the SHA, expected_old_sha is None
        (the ref must not exist). The zero-oid is used for
        expected_old_sha.
    For UPDATE: new_sha is the new SHA, expected_old_sha is the
        expected current SHA.
    For DELETE: new_sha is None (use zero-oid for the new
        position), expected_old_sha is the expected current SHA.
    """
    actual_new = grm.oid_to_zero(new_sha) if delete else new_sha
    actual_old = grm.oid_to_zero(expected_old_sha)
    if delete:
        cmd = ["git", "update-ref", "-d", ref, actual_old]
    else:
        if actual_new is None:
            raise ValueError("new_sha must be set for CREATE/UPDATE")
        cmd = ["git", "update-ref", ref, actual_new, actual_old]
    result = subprocess.run(
        cmd, cwd=str(repo), capture_output=True, text=True
    )
    return RefMutationResult(
        ok=result.returncode == 0,
        actual_ref_sha=read_ref(repo, ref),
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def guarded_create_ref(
    *,
    repo: Path,
    ref: str,
    new_sha: str,
) -> RefMutationResult:
    """CREATE: git update-ref <ref> <new_sha> <zero-oid>.

    Fails if the ref already exists.
    """
    return _update_ref(
        repo=repo,
        ref=ref,
        new_sha=new_sha,
        expected_old_sha=None,
        delete=False,
    )


def guarded_update_ref(
    *,
    repo: Path,
    ref: str,
    new_sha: str,
    expected_old_sha: str,
) -> RefMutationResult:
    """UPDATE: git update-ref <ref> <new_sha> <expected_old_sha>.

    Fails if the ref's current value is not expected_old_sha.
    """
    return _update_ref(
        repo=repo,
        ref=ref,
        new_sha=new_sha,
        expected_old_sha=expected_old_sha,
        delete=False,
    )


def guarded_delete_ref(
    *,
    repo: Path,
    ref: str,
    expected_old_sha: str,
) -> RefMutationResult:
    """DELETE: git update-ref -d <ref> <expected_old_sha>.

    Fails if the ref's current value is not expected_old_sha.
    """
    return _update_ref(
        repo=repo,
        ref=ref,
        new_sha=None,
        expected_old_sha=expected_old_sha,
        delete=True,
    )


# ---------------------------------------------------------------------------
# Remote push
# ---------------------------------------------------------------------------

def _build_push_force_with_lease(
    *,
    ref: str,
    expected_remote_sha: Optional[str],
) -> str:
    """Build the --force-with-lease=<ref>:<expected> option.

    For UPDATE/PUSH (expected_remote_sha is a full SHA), this
    returns the standard --force-with-lease=<ref>:<sha> option.
    For CREATE (expected_remote_sha is None), use the empty
    refspec form --force-with-lease=<ref>: which rejects the
    push if the ref exists.
    """
    if expected_remote_sha is None:
        return f"--force-with-lease={ref}:"
    return f"--force-with-lease={ref}:{expected_remote_sha}"


def guarded_push(
    *,
    repo: Path,
    remote: str,
    ref: str,
    expected_remote_sha: Optional[str],
    new_local_sha: Optional[str],
) -> RefMutationResult:
    """git push --force-with-lease=<ref>:<expected> <remote> <src>:<dst>.

    The push is rejected by the receiving Git server if the
    remote ref's current value is not expected_remote_sha.
    For CREATE, expected_remote_sha is None and the push is
    rejected if the ref exists.

    The source side of the refspec is the validated
    new_local_sha (full 40-char hex) when supplied, so the
    receiving server pushes exactly that object ID — never
    whatever the local ref happens to point at. The local
    ref itself is NOT updated here; staging the local ref is
    the caller's responsibility (and is verified by the
    orchestrator before invocation).

    For integration tests, the remote is a local bare repo path.
    """
    # Refuse if new_local_sha is missing for an UPDATE-style
    # push. CREATE-style pushes pass expected_remote_sha=None
    # AND new_local_sha=None.
    if new_local_sha is not None:
        # Verify the desired object ID exists in the local repo.
        # If it does not, the push will fail anyway; we abort
        # early with a clear error.
        cat_result = subprocess.run(
            ["git", "cat-file", "-e", new_local_sha],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        if cat_result.returncode != 0:
            return RefMutationResult(
                ok=False,
                actual_ref_sha=None,
                stdout="",
                stderr=(
                    f"refusing to push: new_local_sha={new_local_sha} "
                    f"does not exist in the local repo: "
                    f"{cat_result.stderr.strip()}"
                ),
                returncode=cat_result.returncode,
            )
        # Build a refspec that pushes the validated object ID
        # directly, NOT the local ref. git push accepts a SHA
        # as <src> and pushes exactly that commit to <dst>.
        src_spec = new_local_sha
    else:
        # CREATE: the caller did not supply a new_local_sha.
        # The local ref must be at the desired SHA; we read it.
        current_local = read_ref(repo, ref)
        if current_local is None:
            return RefMutationResult(
                ok=False,
                actual_ref_sha=None,
                stdout="",
                stderr=(
                    f"refusing to push: local ref {ref} does not exist "
                    f"and no new_local_sha was supplied"
                ),
                returncode=1,
            )
        src_spec = current_local
    lease_option = _build_push_force_with_lease(
        ref=ref,
        expected_remote_sha=expected_remote_sha,
    )
    spec = f"{src_spec}:{ref}"
    cmd = ["git", "push", lease_option, remote, spec]
    result = subprocess.run(
        cmd, cwd=str(repo), capture_output=True, text=True
    )
    return RefMutationResult(
        ok=result.returncode == 0,
        actual_ref_sha=None,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def read_remote_ref(
    *,
    remote_repo: Path,
    ref: str,
) -> Optional[str]:
    """Read the ref value from a remote (e.g. a local bare repo)."""
    return read_ref(remote_repo, ref)


__all__ = (
    "RefMutationResult",
    "GuardedRefError",
    "read_ref",
    "guarded_create_ref",
    "guarded_update_ref",
    "guarded_delete_ref",
    "guarded_push",
    "read_remote_ref",
)
