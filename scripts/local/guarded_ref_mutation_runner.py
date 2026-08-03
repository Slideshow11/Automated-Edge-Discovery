"""AED guarded-ref mutation orchestrator.

The controller-level orchestrator that ties the durable plan
(guarded_ref_mutation) to the actual git operations
(guarded_ref_ops). The orchestrator:

  1. Initializes a GuardedMutationPlan (PREPARED).
  2. Performs the guarded git operation (EXECUTING).
  3. Reads the actual remote ref (RECONCILING).
  4. Classifies the outcome via reconcile() (terminal state).
  5. Updates the plan at each transition.

The orchestrator is a thin layer; it does NOT make policy
decisions. The mutations list (input to the orchestrator) is
already validated by the controller's authorize-mutation path.

The orchestrator is idempotent. Calling execute() twice on a
SUCCEEDED plan returns ok with the existing actual_ref_sha. The
reconcile() function handles the safety:
  - actual == desired_after -> SUCCEEDED
  - actual == expected_before -> NOT_APPLIED (safe retry)
  - actual differs from both -> CONFLICT
  - cannot read -> INDETERMINATE (reconcile retry only)

The primary controller lease remains active. This module does
not introduce a secondary target-lease lifecycle.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from scripts.local import guarded_ref_mutation as grm
from scripts.local import guarded_ref_ops as ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _persist_plan(plan: grm.GuardedMutationPlan, workspace: Path) -> None:
    """Rewrite the durable plan file atomically with
    restrictive permissions preserved across replacement.

    Uses a tmp-file + os.replace for atomicity. The
    controller and the executor both call this at every
    state transition.

    Round-60 P2 fix (PRRT_kwDOSHFpYM6VzOP8): the previous
    implementation used Path.write_text which creates the
    tmp file with the process umask (commonly 0o644 on
    POSIX), replacing the authorization-time 0o600 plan
    with a world-readable inode. Use safe_restrictive_open
    to create the tmp file with 0o600 mode so the
    replacement inode retains restrictive permissions.
    The os.replace atomically swaps the tmp file into the
    plan path (POSIX rename preserves the inode mode).
    """
    from scripts.local.aed_run_identity import safe_restrictive_open
    path = grm.guarded_ref_mutation_plan_path(workspace, plan.mutation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = plan.to_json()
    fd = safe_restrictive_open(tmp, "w")
    try:
        fd.write(payload)
        fd.flush()
        os.fsync(fd.fileno())
    finally:
        fd.close()
    os.replace(tmp, path)


def _read_remote_ref_via_query(
    remote_ref_path: Path, ref: str
) -> "_ReadResult":
    """Read the ref's current SHA from a local path that points
    to the remote bare repo.

    Returns a _ReadResult with three states:
      - actual_ref_sha=None, is_indeterminate=False:
        the ref does not exist at the remote (truly absent).
      - actual_ref_sha=<sha>, is_indeterminate=False:
        the ref's current value was successfully read.
      - actual_ref_sha=None, is_indeterminate=True:
        the read FAILED (path missing, permission denied,
        corrupt repo, network error, etc.). The caller MUST
        classify this as INDETERMINATE and never as
        SUCCEEDED — even for a delete, because we never
        observed the remote state.
    """
    if not remote_ref_path.exists():
        return _ReadResult(sha=None, indeterminate=True)
    try:
        sha = ops.read_ref(remote_ref_path, ref)
        return _ReadResult(sha=sha, indeterminate=False)
    except (ops.GuardedRefError, FileNotFoundError, PermissionError,
            OSError, subprocess.CalledProcessError):
        # FileNotFoundError: the git binary or a parent
        # directory was deleted between existence check and
        # git invocation.
        # PermissionError: the remote path is not readable.
        # OSError: broader filesystem error.
        # CalledProcessError: git returned non-zero for some
        # other reason (not a known "ref not found" case).
        return _ReadResult(sha=None, indeterminate=True)


def _read_remote_ref_via_ls_remote(
    remote_url: str, ref: str
) -> "_ReadResult":
    """Read the ref's current SHA from a remote URL via
    `git ls-remote <remote_url> <ref>`.

    Round-60 P1 fix (Reconcile configured remotes without a
    local bare path). Production mutations against real
    GitHub have no local bare repository mirror; the
    controller must still be able to verify the remote ref's
    current value. Use git ls-remote to query the ref
    directly from the remote.

    Returns a _ReadResult with the same tristate semantics as
    _read_remote_ref_via_query: present, absent, or
    indeterminate on read failure.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-remote", remote_url, ref],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return _ReadResult(sha=None, indeterminate=True)
    for line in proc.stdout.splitlines():
        m = re.match(r"^([0-9a-f]{40})\s+\S*$", line)
        if m:
            return _ReadResult(sha=m.group(1), indeterminate=False)
    return _ReadResult(sha=None, indeterminate=False)


def is_url_backed_remote(local_repo: Path, remote_name: str) -> bool:
    """Return True iff the configured remote.<remote_name>.url
    in `local_repo` is URL-backed
    (http/https/git@/ssh/file). Local-bare filesystem paths
    return False.

    Round-69 P1 fix (Create authorized branches on the
    remote): the runner's CREATE_LOCAL dispatcher used to
    dispatch guarded_create_ref against only the local clone
    for ANY configured remote, including URL-backed ones.
    Reconciliation then read that clone and reported
    SUCCEEDED, even though the remote branch was never
    created. The fix: detect URL-backed remotes and route the
    create through guarded_push.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(local_repo),
             "config", "--get",
             f"remote.{remote_name}.url"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    url = result.stdout.strip()
    return (
        url.startswith("http")
        or url.startswith("git@")
        or url.startswith("ssh://")
        or url.startswith("file://")
    )


def read_remote_ref_unified(
    remote_ref_path: Optional[Path],
    *,
    remote_url: Optional[str] = None,
    ref: str,
) -> "_ReadResult":
    """Unified reader: use local path if supplied, else
    fall back to ls-remote over remote_url.

    Round-60 P1 fix: callers should use this entry point so
    that production mutations without a local bare mirror
    still get authoritative reconciliation via ls-remote.
    """
    if remote_ref_path is not None:
        return _read_remote_ref_via_query(remote_ref_path, ref)
    if remote_url is not None:
        return _read_remote_ref_via_ls_remote(remote_url, ref)
    return _ReadResult(sha=None, indeterminate=True)


class _ReadResult:
    """Tristate result of reading a ref from a remote path.

    is_indeterminate=True means the read FAILED. The caller
    MUST classify this as INDETERMINATE (especially for a
    delete, where the absence-of-ref domain value is
    indistinguishable from a read failure under the previous
    implementation).
    """

    __slots__ = ("sha", "indeterminate")

    def __init__(self, *, sha: Optional[str], indeterminate: bool):
        self.sha = sha
        self.indeterminate = indeterminate


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class GuardedMutationOrchestrator:
    """Coordinate the lifecycle of a GuardedMutationPlan.

    The orchestrator owns the durable plan file. Each public
    method updates the plan and persists it. The primary run
    lease is held by the controller's existing supervisor-lock
    primitive; this module does not touch it.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        plan: grm.GuardedMutationPlan,
    ):
        self.workspace = Path(workspace)
        self.plan = plan
        grm.validate_plan(self.plan)
        self.plan_path = grm.guarded_ref_mutation_plan_path(
            self.workspace, plan.mutation_id
        )

    # ------------------------------------------------------------------
    # Prepare (write the PREPARED plan)
    # ------------------------------------------------------------------

    def prepare(self) -> grm.GuardedMutationPlan:
        """Persist the plan in PREPARED state. Must be called
        before any other operation. Idempotent: if the plan is
        already PREPARED, returns it unchanged."""
        if self.plan.status != grm.LifecycleState.PREPARED.value:
            grm.assert_allowed_transition(
                grm.LifecycleState(self.plan.status),
                grm.LifecycleState.PREPARED,
            )
        self.plan.status = grm.LifecycleState.PREPARED.value
        # Round-66 P2 fix (Preserve the plan creation timestamp
        # during prepare): only set created_at if not already
        # set. The previous code overwrote it on every
        # prepare() call, which contradicts the method's
        # idempotency contract (a retried prepare must not
        # change the plan's identity attributes). The
        # controller's authorize-mutation emits the durable
        # plan with the initial created_at; subsequent
        # prepare() calls (e.g. after NOT_APPLIED retry)
        # preserve that timestamp.
        if not self.plan.created_at:
            self.plan.created_at = _utcnow_iso()
        _persist_plan(self.plan, self.workspace)
        return self.plan

    # ------------------------------------------------------------------
    # Execute (run the guarded mutation)
    # ------------------------------------------------------------------

    def execute(
        self,
        *,
        local_repo: Path,
        remote_ref_path: Optional[Path] = None,
        remote: str = "origin",
    ) -> grm.GuardedMutationPlan:
        """Execute the guarded mutation against `local_repo`.

        local_repo is the path of the LOCAL repo from which
        the mutation is performed. remote_ref_path is the path
        of the REMOTE repo (e.g. a local bare repo) used for
        reconciliation. If None, the local repo is used as
        the remote (useful for update_ref and delete_ref).

        remote is the NAME of the remote (e.g. "origin",
        "upstream") that the CAS-protected push targets. It is
        threaded through to guarded_push so a clone configured
        with multiple remotes pushes to the correct one.
        Default: "origin".

        The plan transitions PREPARED -> EXECUTING -> RECONCILING
        -> terminal state. The plan is persisted at each
        transition.
        """
        # PREPARED -> EXECUTING
        if self.plan.status != grm.LifecycleState.PREPARED.value:
            raise grm.LifecycleError(
                f"execute() requires PREPARED; current status is "
                f"{self.plan.status!r}"
            )
        self.plan.status = grm.LifecycleState.EXECUTING.value
        _persist_plan(self.plan, self.workspace)

        # Perform the guarded mutation. Wrap in try/except so
        # a missing remote path, a missing local repo, or
        # any other unexpected git failure produces an
        # INDETERMINATE result instead of an unhandled
        # exception. The durable plan is preserved at the
        # EXECUTING state so a recovery run can resume.
        op = grm.Operation(self.plan.operation)
        result: Optional[ops.RefMutationResult] = None
        try:
            result = self._do_execute(
                local_repo, op, remote=remote,
                remote_ref_path=remote_ref_path,
            )
        except (ops.GuardedRefError, FileNotFoundError,
                PermissionError, OSError,
                subprocess.CalledProcessError) as e:
            # Treat any executor exception as INDETERMINATE.
            # We never saw a successful CAS; the durable
            # evidence is the EXECUTING state plus the
            # exception. The reconcile phase below reads the
            # remote ref (which may now also be unreadable)
            # and reports INDETERMINATE.
            self.plan.terminal_evidence = (
                f"executor_exception={type(e).__name__}:{e}"
            )

        # EXECUTING -> RECONCILING
        self.plan.status = grm.LifecycleState.RECONCILING.value
        _persist_plan(self.plan, self.workspace)

        # Read the actual ref for reconciliation.
        # Round-60 P1 fix: prefer the caller-supplied local
        # bare path; fall back to ls-remote over the clone's
        # configured remote URL when the operation is
        # PUSH_REMOTE and no local bare is available; fall
        # back to the local repo for non-PUSH operations.
        #
        # Round-68 P1 fix (Reconcile URL-backed deletions
        # against the remote): for a DELETE_LOCAL plan that
        # was just pushed to a URL-backed remote (Round-63
        # path), the local clone's branch still exists
        # until the next `git remote prune`, so reading the
        # local branch returns the pre-deletion SHA. The
        # runner must reconcile against the actual remote
        # state (via ls-remote on the configured remote URL)
        # so a successful remote deletion is reported as
        # SUCCEEDED, not NOT_APPLIED. The condition here
        # extends the Round-60 PUSH_REMOTE fallback to also
        # cover DELETE_LOCAL when the configured remote is
        # URL-backed (http/https/git@/ssh/file). Local-bare
        # URLs continue to use the local_repo fallback
        # because the runner's _do_execute used local delete
        # for them, not the remote CAS.
        is_url_backed = False
        if remote_ref_path is None and self.plan.operation in (
            grm.Operation.PUSH_REMOTE.value,
            grm.Operation.DELETE_LOCAL.value,
            # Round-104 P1 fix: also detect URL-backed remotes
            # for CREATE_LOCAL plans. The CREATE_LOCAL path in
            # _do_execute() already routes URL-backed creates
            # through guarded_push (Round-69), but
            # reconciliation was missing the same detection,
            # so it fell through to reading the local clone.
            # A successful remote creation was then either
            # persisted as NOT_APPLIED (target ref absent
            # locally) or reported as SUCCEEDED from a stale
            # local ref that happened to match the desired
            # SHA. Include CREATE_LOCAL here so the
            # reconciliation path below also uses ls-remote
            # on the configured remote URL, the authoritative
            # source of truth.
            grm.Operation.CREATE_LOCAL.value,
        ):
            is_url_backed = is_url_backed_remote(local_repo, remote)
        if remote_ref_path is not None and not is_url_backed:
            # Round-90 P1 fix (V1BqG continuation): only use
            # remote_ref_path for reconciliation when the
            # remote is NOT URL-backed. For URL-backed
            # remotes, the configured URL is the actual
            # push endpoint; reconciling against a
            # different remote_ref_path can falsely report
            # SUCCEEDED (e.g. if the supplied path
            # coincidentally has the desired_after_sha).
            # For URL-backed remotes, always use ls-remote
            # over the configured URL — that is the
            # authoritative source of truth.
            read_result = _read_remote_ref_via_query(
                remote_ref_path, self.plan.target_ref
            )
        elif (
            self.plan.operation == grm.Operation.PUSH_REMOTE.value
            or (
                self.plan.operation == grm.Operation.DELETE_LOCAL.value
                and is_url_backed
            )
            or (
                self.plan.operation == grm.Operation.CREATE_LOCAL.value
                and is_url_backed
            )
        ):
            clone_remote_url = None
            try:
                cfg = subprocess.run(
                    ["git", "-C", str(local_repo),
                     "config", "--get",
                     f"remote.{remote}.url"],
                    capture_output=True, text=True, check=True,
                )
                clone_remote_url = cfg.stdout.strip() or None
            except (subprocess.CalledProcessError, FileNotFoundError):
                clone_remote_url = None
            read_result = _read_remote_ref_via_ls_remote(
                clone_remote_url or remote,
                self.plan.target_ref,
            )
        else:
            read_result = _read_remote_ref_via_query(
                local_repo, self.plan.target_ref
            )
        # If the read failed, force INDETERMINATE. Never
        # confuse a read failure with a missing ref — for
        # delete this is the difference between SUCCEEDED and
        # INDETERMINATE.
        if read_result.indeterminate:
            outcome = grm.LifecycleState.INDETERMINATE
            evidence = (
                f"actual_ref_sha=<read failed>, "
                f"executor_result="
                f"{result.returncode if result is not None else 'exception'}, "
                f"stdout="
                f"{(result.stdout if result is not None else '')[:200]!r}, "
                f"stderr="
                f"{(result.stderr if result is not None else '')[:200]!r}"
            )
            self.plan.terminal_evidence = evidence
        else:
            outcome = grm.reconcile(
                expected_before_sha=self.plan.expected_before_sha,
                desired_after_sha=self.plan.desired_after_sha,
                actual_ref_sha=read_result.sha,
            )
            evidence = (
                f"actual_ref_sha={read_result.sha!r}, "
                f"stdout="
                f"{(result.stdout if result is not None else '')[:200]!r}, "
                f"stderr="
                f"{(result.stderr if result is not None else '')[:200]!r}, "
                f"returncode="
                f"{result.returncode if result is not None else 'exception'}"
            )
            self.plan.terminal_evidence = evidence
        self.plan.status = outcome.value
        self.plan.last_reconciled_at = _utcnow_iso()
        _persist_plan(self.plan, self.workspace)
        return self.plan

    def _do_execute(
        self, local_repo: Path, op: grm.Operation,
        remote: str = "origin",
        remote_ref_path: Optional[Path] = None,
    ) -> ops.RefMutationResult:
        """Perform the actual git operation."""
        # Round-69 P1 fix: helper for the URL-backed
        # detection used by both CREATE_LOCAL and
        # DELETE_LOCAL dispatch below. The configured
        # remote URL is URL-backed if it starts with
        # http/https/git@/ssh/file. Local-bare paths
        # (filesystem paths) are NOT URL-backed; they
        # use the local-bare mirror path.
        if op == grm.Operation.PUSH_REMOTE:
            # Push to the selected remote. The remote is
            # threaded from the caller so a clone with multiple
            # remotes (origin vs upstream) mutates the correct
            # one.
            return ops.guarded_push(
                repo=local_repo,
                remote=remote,
                ref=self.plan.target_ref,
                expected_remote_sha=self.plan.expected_before_sha,
                new_local_sha=self.plan.desired_after_sha,
            )
        elif op == grm.Operation.UPDATE_LOCAL:
            return ops.guarded_update_ref(
                repo=local_repo,
                ref=self.plan.target_ref,
                new_sha=self.plan.desired_after_sha or "",
                expected_old_sha=self.plan.expected_before_sha,
            )
        elif op == grm.Operation.CREATE_LOCAL:
            # Round-69 P1 fix (Create authorized branches on
            # the remote): for a CREATE_LOCAL plan with a
            # URL-backed configured remote and no
            # --remote-path, the previous code dispatched
            # guarded_create_ref against only the local clone.
            # Reconciliation then read that clone and
            # reported SUCCEEDED, even though the remote
            # branch was never created. Route URL-backed
            # creates through guarded_push with
            # expected_remote_sha=None (the create refspec is
            # `refs/heads/<branch>` with no expected prior
            # state). Local-bare URLs and UPDATE_LOCAL are
            # unchanged.
            if remote_ref_path is not None or is_url_backed_remote(
                local_repo, remote
            ):
                # Route through push. The create refspec
                # is `<new_sha>:refs/heads/<branch>`. For
                # CREATE the expected_remote_sha is None
                # (no prior ref).
                return ops.guarded_push(
                    repo=local_repo,
                    remote=remote,
                    ref=self.plan.target_ref,
                    expected_remote_sha=None,
                    new_local_sha=self.plan.desired_after_sha or "",
                )
            return ops.guarded_create_ref(
                repo=local_repo,
                ref=self.plan.target_ref,
                new_sha=self.plan.desired_after_sha or "",
            )
        elif op == grm.Operation.DELETE_LOCAL:
            # Round-59 P1 fix (Execute branch deletion
            # against the remote): when a remote_ref_path
            # is supplied, the branch must be removed from
            # the remote repository, not just the local
            # clone. Use push-delete with the supplied
            # expected_remote_sha as the force-with-lease
            # CAS so the remote ref's current value is
            # verified before deletion. When no
            # remote_ref_path is supplied, fall back to
            # local delete via git update-ref (the previous
            # behavior).
            #
            # Round-63 P1 fix (Route URL-backed deletions
            # through the remote CAS): the Round-59 fix
            # only handled the locally-mounted-bare
            # mirror case. For URL-backed remotes
            # (HTTPS / SSH / file://) without a local
            # bare mirror, resolve the configured remote
            # URL and use push-delete against the URL. The
            # push will fail if the local repo's remote
            # URL is not actually a remote (network,
            # auth); the runner treats that as
            # INDETERMINATE. Local delete (the previous
            # fallback) only happens when the configured
            # remote URL is a local path (local-bare
            # mirror without --remote-path) or when no
            # remote URL can be resolved at all.
            if remote_ref_path is not None and remote_ref_path != local_repo:
                # Caller-supplied local bare path (CI
                # integration test with --remote-path).
                return ops.guarded_push(
                    repo=local_repo,
                    remote=remote,
                    ref=self.plan.target_ref,
                    expected_remote_sha=self.plan.expected_before_sha,
                    new_local_sha=None,
                    delete_remote=True,
                )
            # Try to resolve the configured remote URL.
            configured_url = None
            try:
                cfg = subprocess.run(
                    ["git", "-C", str(local_repo),
                     "config", "--get",
                     f"remote.{remote}.url"],
                    capture_output=True, text=True, check=True,
                )
                configured_url = cfg.stdout.strip() or None
            except (subprocess.CalledProcessError, FileNotFoundError):
                configured_url = None
            if configured_url and (
                configured_url.startswith("http")
                or configured_url.startswith("https")
                or configured_url.startswith("git@")
                or configured_url.startswith("ssh://")
                or configured_url.startswith("file://")
            ):
                # URL-backed remote: use push-delete via
                # the configured URL. The local clone's
                # remote.<name>.url is the actual push
                # target.
                return ops.guarded_push(
                    repo=local_repo,
                    remote=remote,
                    ref=self.plan.target_ref,
                    expected_remote_sha=self.plan.expected_before_sha,
                    new_local_sha=None,
                    delete_remote=True,
                )
            # Fall back to local delete (the previous
            # behavior for local-bare without --remote-path
            # or no remote URL at all).
            return ops.guarded_delete_ref(
                repo=local_repo,
                ref=self.plan.target_ref,
                expected_old_sha=self.plan.expected_before_sha,
            )
        elif op == grm.Operation.GRAPHQL_UPDATE_REFS:
            raise NotImplementedError(
                "GRAPHQL_UPDATE_REFS is not implemented in Layer 4; "
                "Layer 5+ replaces the executor packet call."
            )
        else:
            raise ValueError(f"unknown operation: {op}")

    # ------------------------------------------------------------------
    # Reconcile (re-read the actual ref and update the plan)
    # ------------------------------------------------------------------

    def reconcile(
        self,
        *,
        remote_ref_path: Optional[Path] = None,
        remote_url: Optional[str] = None,
    ) -> grm.GuardedMutationPlan:
        """Re-read the actual ref and update the plan with the
        new outcome.

        Allowed from PREPARED, EXECUTING, RECONCILING,
        NOT_APPLIED, or INDETERMINATE. NOT_APPLIED can
        transition to RECONCILING (the actual is still at
        expected_before so the retry is safe). INDETERMINATE
        can transition to RECONCILING (reconcile retry only).

        Round-60 P1 fix (Reconcile configured remotes without a
        local bare path): the caller may supply either
        `remote_ref_path` (local bare) OR `remote_url` (a Git
        URL resolved via `git ls-remote`). At least one must
        be supplied; both is also acceptable.
        """
        current = grm.LifecycleState(self.plan.status)
        if current not in TERMINAL_STATES_FOR_RECONCILE:
            grm.assert_allowed_transition(
                current, grm.LifecycleState.RECONCILING
            )
        # Transition to RECONCILING.
        self.plan.status = grm.LifecycleState.RECONCILING.value
        _persist_plan(self.plan, self.workspace)

        # Re-read the actual ref via the unified reader
        # (Round-60 P1 fix): prefer the local path, fall
        # back to ls-remote over remote_url.
        read_result = read_remote_ref_unified(
            remote_ref_path,
            remote_url=remote_url,
            ref=self.plan.target_ref,
        )
        if read_result.indeterminate:
            outcome = grm.LifecycleState.INDETERMINATE
            self.plan.terminal_evidence = (
                f"actual_ref_sha=<read failed>"
            )
        else:
            outcome = grm.reconcile(
                expected_before_sha=self.plan.expected_before_sha,
                desired_after_sha=self.plan.desired_after_sha,
                actual_ref_sha=read_result.sha,
            )
            self.plan.terminal_evidence = (
                f"actual_ref_sha={read_result.sha!r}"
            )
        self.plan.status = outcome.value
        self.plan.last_reconciled_at = _utcnow_iso()
        _persist_plan(self.plan, self.workspace)
        return self.plan


# States from which reconcile() can be called.
TERMINAL_STATES_FOR_RECONCILE = frozenset({
    grm.LifecycleState.PREPARED,
    grm.LifecycleState.EXECUTING,
    grm.LifecycleState.RECONCILING,
    grm.LifecycleState.NOT_APPLIED,
    grm.LifecycleState.INDETERMINATE,
})


__all__ = (
    "GuardedMutationOrchestrator",
)
