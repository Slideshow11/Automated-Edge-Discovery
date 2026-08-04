"""Round-112 P3 regression tests for the gate's CodeRabbit
walkthrough/summary detector and authorization duplicate
scan. See CodeRabbit findings WKNBc (gate marker check), WKoC1
(unresolvable remote URL — persist as INDETERMINATE), WKoC_
(guarded_ref_ops read_ref exit-code semantics), and WJW_E
(desired_after_sha part of the duplicate-authorization key)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# --- WKNBc: _is_review_bot_summary_post must use startswith ---


def test_is_review_bot_summary_post_accepts_leading_marker():
    """A CodeRabbit summary post starts with the canonical marker."""
    from scripts.local.check_pr_review_comments import (
        _is_review_bot_summary_post,
    )
    body = (
        "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
        "<!-- review_stack_entry_start -->\n"
        "## Walkthrough\n..."
    )
    assert _is_review_bot_summary_post(
        user="coderabbitai[bot]",
        source_kind="issue_comment",
        body=body,
    )


def test_is_review_bot_summary_post_rejects_embedded_marker():
    """CodeRabbit finding WKNBc: a body that QUOTES or EMBEDS the
    marker later (but does NOT start with it) must NOT be
    classified as a summary. The previous `in` substring match
    incorrectly downgraded such bodies to UNSPECIFIED_INFO,
    hiding real findings."""
    from scripts.local.check_pr_review_comments import (
        _is_review_bot_summary_post,
    )
    body = (
        "A real finding body — NOT a summary.\n\n"
        "Note: the inline comment contains "
        "<!-- This is an auto-generated comment: summarize by coderabbit.ai --> "
        "as a quoted example of what the gate should NOT match."
    )
    assert not _is_review_bot_summary_post(
        user="coderabbitai[bot]",
        source_kind="issue_comment",
        body=body,
    )


def test_is_review_bot_summary_post_rejects_non_coderabbit_user():
    """Marker present but author is not coderabbitai[bot] —
    not a summary."""
    from scripts.local.check_pr_review_comments import (
        _is_review_bot_summary_post,
    )
    body = (
        "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
        "## Body"
    )
    assert not _is_review_bot_summary_post(
        user="chatgpt-codex-connector[bot]",
        source_kind="issue_comment",
        body=body,
    )


def test_is_review_bot_summary_post_accepts_review_stack_marker():
    """Body starts with <!-- review_stack_entry_start -->."""
    from scripts.local.check_pr_review_comments import (
        _is_review_bot_summary_post,
    )
    body = (
        "<!-- review_stack_entry_start -->\n"
        "Some walkthrough text\n"
        "<!-- review_stack_entry_end -->\n"
    )
    assert _is_review_bot_summary_post(
        user="coderabbitai[bot]",
        source_kind="review",
        body=body,
    )


# --- WKoC_: read_ref exit-code semantics ---


def test_read_ref_returns_none_on_exit_one_missing(tmp_path):
    """WKoC_: exit code 1 (missing ref) returns None — ref absent."""
    from scripts.local.guarded_ref_ops import read_ref

    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "i", "-q"], cwd=str(repo), check=True)
    # refs/heads/missing does not exist -> exit 1
    sha = read_ref(repo, "refs/heads/missing")
    assert sha is None


def test_read_ref_raises_on_exit_128_not_a_repository(tmp_path):
    """WKoC_: exit code 128 (not a git repository) raises
    GuardedRefError, NOT returns None. The previous implementation
    conflated exit 1 (missing) with exit 128 (repo failure),
    silently reporting the ref as missing for malformed repos."""
    from scripts.local.guarded_ref_ops import (
        GuardedRefError,
        read_ref,
    )
    # Create a directory that EXISTS but is not a git repo;
    # git rev-parse in such a directory exits with code 128
    # ("fatal: not a git repository").
    not_a_repo = tmp_path / "not-a-repository"
    not_a_repo.mkdir()
    with pytest.raises(GuardedRefError) as exc:
        read_ref(not_a_repo, "refs/heads/main")
    # The error must NOT claim "missing ref"; it must report
    # the non-1 exit code. Check that the exit code is
    # something other than 1 (i.e. exit=128 in this case, not
    # the exit=1 used for "missing ref").
    err = str(exc.value)
    assert "exit=" in err
    # Extract the exit code
    import re
    m = re.search(r"exit=(\d+)", err)
    assert m is not None
    exit_code = int(m.group(1))
    assert exit_code != 1, (
        f"exit code should be 128 (not a repo) not 1 (missing "
        f"ref); got exit={exit_code}"
    )


def test_read_ref_raises_on_arbitrary_nonexistent_directory(tmp_path):
    """WKoC_: a directory that does not exist at all — git
    itself cannot even chdir. The behavior depends on whether
    Python raises FileNotFoundError before git runs, OR git
    itself exits with a non-1 code. Both outcomes are
    acceptable: the test asserts that NO silent success
    (returning None) occurs."""
    from scripts.local.guarded_ref_ops import (
        GuardedRefError,
        read_ref,
    )
    fake = tmp_path / "does-not-exist"
    # Don't mkdir; the path doesn't exist
    try:
        result = read_ref(fake, "refs/heads/main")
    except (GuardedRefError, FileNotFoundError, OSError):
        # Acceptable: caller surfaces the failure, does not
        # silently treat the ref as missing.
        return
    # If somehow no exception, the ref must NOT have been
    # reported as "missing" via None — that would be the bug
    # we are fixing.
    assert result is not None, (
        "read_ref returned None for a path that does not exist; "
        "this is the WKoC_ regression"
    )


# --- WKoC1: clone_remote_url unresolvable persists INDETERMINATE ---


def test_reconcile_persists_indeterminate_when_remote_url_unresolvable(
    tmp_path, monkeypatch
):
    """WKoC1: when 'git config --get remote.<name>.url' fails
    AFTER `is_url_backed_remote` returned True, the runner
    must set status=INDETERMINATE, set last_reconciled_at, set
    terminal_evidence, and PERSIST the plan BEFORE returning.
    The previous fix set terminal_evidence but did not change
    status or persist, leaving the durable plan in RECONCILING
    forever. We use monkeypatch to simulate the race
    condition where the URL is set for `is_url_backed_remote`
    but cleared (returning empty) before the second
    `git config --get` call inside the execute branch.

    The fix lives in `GuardedMutationOrchestrator.execute()`
    (which transitions through RECONCILING), not in
    `reconcile()`. The test exercises `execute()` with
    local_repo to drive the URL-backed path.
    """
    from scripts.local import guarded_ref_mutation as grm
    from scripts.local.guarded_ref_mutation_runner import (
        GuardedMutationOrchestrator,
    )
    import subprocess as _sp

    workspace = tmp_path / "ws"
    workspace.mkdir()
    local_repo = tmp_path / "clone"
    local_repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=str(local_repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(local_repo), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(local_repo), check=True)
    # Create the initial commit so the local repo is non-empty
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=str(local_repo), check=True)
    initial_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(local_repo), capture_output=True, text=True, check=True).stdout.strip()
    # Create a second commit so desired_sha exists locally
    subprocess.run(["git", "commit", "--allow-empty", "-m", "next", "-q"], cwd=str(local_repo), check=True)
    desired_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(local_repo), capture_output=True, text=True, check=True).stdout.strip()
    # Reset back to initial_sha so the push happens during execute
    subprocess.run(["git", "reset", "--hard", initial_sha], cwd=str(local_repo), check=True)
    # Configure a URL-backed remote (so is_url_backed=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/repo.git"],
        cwd=str(local_repo),
        check=True,
    )

    # Patch subprocess.run so:
    #   1. The second `git config --get remote.<name>.url` call
    #      returns empty stdout (so clone_remote_url=None).
    #   2. ls-remote calls return empty stdout (so the read
    #      result is INDETERMINATE — not the bug we're testing).
    # Other subprocess invocations are passed through.
    import subprocess as _sp
    original_run = _sp.run
    call_count = {"n": 0}

    def selective_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git" and "config" in cmd and "--get" in cmd:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                return _sp.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr=""
                )
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git" and "ls-remote" in cmd:
            # Return empty output (no matching ref) so the
            # read result is a clean None.
            return _sp.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_sp, "run", selective_run)

    plan = grm.GuardedMutationPlan(
        mutation_id="m_wo_unresolvable",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path = grm.guarded_ref_mutation_plan_path(workspace, plan.mutation_id)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.to_json())

    orch = GuardedMutationOrchestrator(workspace=workspace, plan=plan)
    # execute() (not reconcile()) is where the URL-backed
    # reconciliation happens. Pass remote_ref_path=None so the
    # runner relies on the clone's remote URL.
    try:
        orch.execute(local_repo=local_repo, remote_ref_path=None)
    except Exception:
        # The executor may raise because the URL doesn't exist;
        # we only care about the post-reconcile plan state.
        pass
    # Reload from disk to verify persistence
    reloaded = grm.GuardedMutationPlan.from_json(plan_path.read_text())
    assert reloaded.status == grm.LifecycleState.INDETERMINATE.value
    assert reloaded.terminal_evidence is not None
    assert "clone_remote_url_unresolvable" in reloaded.terminal_evidence
    assert reloaded.last_reconciled_at is not None


# --- WJW_E: desired_after_sha is part of the duplicate-key ---


def _auth_req(**overrides):
    """Build an AuthorizationRequest with sane defaults."""
    from scripts.local.aed_mutation_authorization import (
        AuthorizationRequest,
    )
    defaults = dict(
        run_id="r1",
        repository="owner/name",
        target_pr_number=1,
        mutation_target="feat/x",
        mutation_type="force_push",
        expected_main_sha="a" * 40,
        expected_target_sha="a" * 40,
        desired_after_sha="b" * 40,
        pending_action="apply",
    )
    defaults.update(overrides)
    return AuthorizationRequest(**defaults)


def _write_existing_record(workspace, **fields):
    """Write a single AUTHORIZED record to the journal so the
    duplicate scan has something to match against."""
    from scripts.local.aed_mutation_authorization import (
        AUTHORIZED,
        mutations_path,
    )
    path = mutations_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    defaults = dict(
        mutation_id="m_prior",
        run_id="r1",
        repository="owner/name",
        target_pr_number=1,
        mutation_target="feat/x",
        mutation_type="force_push",
        expected_main_sha="a" * 40,
        expected_target_sha="a" * 40,
        desired_after_sha="b" * 40,
        authorization_status=AUTHORIZED,
        created_at="2026-08-01T00:00:00Z",
    )
    defaults.update(fields)
    with path.open("a") as f:
        f.write(json.dumps(defaults, sort_keys=True) + "\n")


def test_authorize_treats_same_heads_different_desired_as_distinct(
    tmp_path,
):
    """WJW_E: two authorizations with the SAME scope and SAME
    expected heads but DIFFERENT desired_after_sha are NOT
    duplicates — they are two distinct intended mutations.
    The previous scan ignored desired_after_sha and rejected
    the second authorization as a duplicate."""
    from scripts.local.aed_mutation_authorization import (
        authorize,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_existing_record(
        workspace,
        desired_after_sha="b" * 40,  # original wanted to push to b
        # No result -> still considered an outstanding auth.
    )

    # Second authorization with SAME scope + SAME expected heads
    # but DIFFERENT desired_after_sha (push to "c" instead of "b").
    req = _auth_req(desired_after_sha="c" * 40)
    out = authorize(workspace, req)
    assert out.ok, (
        f"second authorization with different desired_after_sha "
        f"must be accepted as a distinct intended mutation; got "
        f"ok={out.ok} reason={out.reason}"
    )
    assert out.mutation_id is not None
    assert out.mutation_id != "m_prior"


def test_authorize_rejects_duplicate_with_same_heads_and_desired(
    tmp_path,
):
    """WJW_E: two authorizations with the SAME scope, SAME
    expected heads, and SAME desired_after_sha are duplicates."""
    from scripts.local.aed_mutation_authorization import (
        authorize,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_existing_record(
        workspace,
        desired_after_sha="b" * 40,
    )

    req = _auth_req(desired_after_sha="b" * 40)
    out = authorize(workspace, req)
    assert not out.ok, (
        f"identical authorization must be rejected as a duplicate"
    )
    assert out.reason == "duplicate_authorization"


def test_authorize_rejects_duplicate_after_drifted_heads(tmp_path):
    """WJW_E complement: same scope + same desired_after_sha
    but drifted expected heads is still rejected (drift
    detection remains in force)."""
    from scripts.local.aed_mutation_authorization import (
        authorize,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_existing_record(
        workspace,
        desired_after_sha="b" * 40,
        expected_main_sha="a" * 40,
        expected_target_sha="a" * 40,
    )

    req = _auth_req(
        desired_after_sha="b" * 40,
        expected_main_sha="d" * 40,  # drifted
        expected_target_sha="a" * 40,
    )
    out = authorize(workspace, req)
    assert not out.ok
    assert "drifted_heads" in (out.reason or "")
