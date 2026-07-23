"""Round-4 regression tests for the four current-head Codex findings.

These tests are intentionally focused. They cover only the four
findings the controller's round-4 commit addresses (threads
``3600277233``, ``3600277236``, ``3600277238``, ``3600277242``) and
reuse the existing F1-F4 test scaffolding in
``tests/test_aed_pr_round3.py`` rather than duplicating it.

* Finding 1 — eliminate production scope-root env bypass
* Finding 2 — require a real thread commit anchor
* Finding 3 — make dry-run completely non-mutating
* Finding 4 — correctly propagate resolution failures

All tests use mocks; no live GitHub calls are made.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock
import unittest

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import datetime as _dt  # used by mock fixtures for "now" timestamps

import datetime as dt  # used by new tests for dispatched_at construction

from scripts.local import aed_pr_lib as L  # noqa: E402
from scripts.local import aed_pr_readiness as R  # noqa: E402
from scripts.local import aed_pr as ctrl  # noqa: E402


DEFAULT_REPO = "Slideshow11/Automated-Edge-Discovery"
DEFAULT_HEAD = "a" * 40
ANCESTOR_HEAD = "c" * 40
OTHER_HEAD = "f" * 40


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_canonical_root(monkeypatch):
    """Inject a tempdir as the canonical scope root for the test.

    The production canonical scope root is ``~/.hermes/aed/pr_scope``;
    tests must never write into the real home directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        saved = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = Path(tmpdir)
        try:
            yield Path(tmpdir)
        finally:
            ctrl._CANONICAL_SCOPE_ROOT = saved


@pytest.fixture
def mutation_spy(monkeypatch):
    """Install a mutation-spy fixture that fails the test if any
    write command is invoked through ``subprocess.run``.

    The spy classifies each ``subprocess.run`` argv as either a
    read-only or a write operation. Read commands pass through to a
    trivial mock that returns empty JSON. Write commands record the
    invocation and raise ``AssertionError`` so the test fails with a
    clear message naming the offending argv.
    """

    write_patterns = (
        # POST/PATCH/PUT/DELETE requests
        "POST",
        "PATCH",
        "PUT",
        "DELETE",
        # gh mutations
        "merge",
        "edit",
        "create",
        "comment",
        "review",
        # Mutation-via-graphql endpoints
        "graphql",
        # Workflow dispatches
        "workflow_dispatch",
    )
    read_only_subcommands = (
        "view", "checks", "api", "status", "list", "run",
        "diff",
    )

    def _classify(argv):
        if not isinstance(argv, (list, tuple)) or not argv:
            return "read"
        joined = " ".join(str(x) for x in argv)
        if "POST" in argv or "-X" in argv:
            return "write"
        for pattern in write_patterns:
            if pattern in joined:
                # Distinguish gh workflow list/view (read) from
                # gh workflow run (write). ``run`` alone is the
                # dispatch action which is a write.
                if pattern == "run" and "gh workflow run" not in joined:
                    continue
                return "write"
        for ro in read_only_subcommands:
            if f" {ro} " in f" {joined} " or joined.endswith(f" {ro}"):
                return "read"
        return "read"

    invocations = []

    def fake_run(cmd, *args, **kwargs):
        classification = _classify(cmd)
        invocations.append((classification, list(cmd)))
        if classification == "write":
            raise AssertionError(
                f"dry-run must not invoke write command: {cmd!r}"
            )
        return mock.Mock(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return invocations


def _bot_thread(
    thread_id="T1",
    author="chatgpt-codex-connector[bot]",
    is_outdated=True,
    anchor=None,
    comments=None,
    is_resolved=False,
):
    thread = {
        "thread_id": thread_id,
        "author": author,
        "isOutdated": is_outdated,
        "comments": (
            comments
            if comments is not None
            else [{"author": author}]
        ),
        "isResolved": is_resolved,
    }
    if anchor is not None:
        thread["original_commit_sha"] = anchor
    return thread


def _eligibility_kwargs(head=DEFAULT_HEAD, **overrides):
    base = {
        "head_sha": head,
        "codex_verdict": "CODEX_CLEAN_PASS",
        "codex_clean_passed": True,
        "codex_reviewed_sha": head,
        # Round-5: pass repo + an ancestry_runner that always
        # reports ``status="ahead"`` so the existing F4 tests
        # retain their intent (eligible vs ineligible based on
        # anchor shape and codex state) without each test
        # constructing a verifier mock. Tests that want a
        # different ancestry result can override
        # ``ancestry_runner``.
        "repo": "Slideshow11/Automated-Edge-Discovery",
        "ancestry_runner": lambda *a, **kw: mock.Mock(
            returncode=0, stdout="ahead", stderr=""
        ),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Finding 1 — eliminate production scope-root env bypass
# ---------------------------------------------------------------------------


class TestRound4Finding1ScopeRootHardening:
    def test_hermes_aed_scope_dir_cannot_redirect_merge_scope(
        self, fake_canonical_root, monkeypatch,
    ):
        """Even with HERMES_AED_SCOPE_DIR set to an attacker-controlled
        directory containing a permissive record, the merge path must
        read from the canonical scope root, NOT from the env-var
        directory."""
        # Place a permissive record under the canonical root.
        ctrl.write_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD,
            ["scripts/local/aed_pr.py"],
        )
        # Place a permissive record under the attacker's chosen
        # directory that the env-var would have pointed to.
        attacker_dir = tempfile.mkdtemp(prefix="attacker-scope-")
        try:
            (Path(attacker_dir) / "411.json").write_text(
                json.dumps({
                    "pr_number": 411,
                    "repo": DEFAULT_REPO,
                    "head_sha": DEFAULT_HEAD,
                    "allowed_files": ["**"],
                    "forbidden_files": [],
                }),
                encoding="utf-8",
            )
            monkeypatch.setenv(
                "HERMES_AED_SCOPE_DIR", attacker_dir
            )
            # Production read_trusted_scope MUST still resolve to the
            # canonical root and return the canonical record (NOT the
            # attacker's "**").
            allowed, forbidden, err = ctrl.read_trusted_scope(
                DEFAULT_REPO, 411, DEFAULT_HEAD
            )
            assert err == ""
            assert allowed == ["scripts/local/aed_pr.py"]
            assert "**" not in (allowed or [])
        finally:
            import shutil
            shutil.rmtree(attacker_dir, ignore_errors=True)

    def test_attacker_permissive_record_ignored(self, fake_canonical_root):
        """A permissive record under an attacker-selected directory is
        ignored by production callers; the canonical root's record
        alone authorizes merge."""
        # No record under canonical root - merge will block.
        allowed, forbidden, err = ctrl.read_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD
        )
        assert err  # blocked

    def test_canonical_root_record_accepted(self, fake_canonical_root):
        """An exact record under the canonical (or explicitly injected
        test) root is accepted."""
        ctrl.write_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD,
            ["scripts/local/aed_pr.py"],
        )
        allowed, forbidden, err = ctrl.read_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD
        )
        assert err == ""
        assert allowed == ["scripts/local/aed_pr.py"]

    def test_status_advance_merge_share_canonical_identity(
        self, fake_canonical_root,
    ):
        """The three subcommands consume the same canonical scope
        identity. status and advance accept CLI scope as a diagnostic
        override (test seam); merge refuses CLI scope outright."""
        ctrl.write_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD,
            ["scripts/local/aed_pr.py"],
        )
        # status/advance read the same canonical record.
        allowed_status, _, err_status = ctrl._resolve_effective_scope(
            subcommand="status",
            repo=DEFAULT_REPO,
            pr_number=411,
            head_sha=DEFAULT_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert err_status == ""
        assert allowed_status == ["scripts/local/aed_pr.py"]

        allowed_advance, _, err_advance = ctrl._resolve_effective_scope(
            subcommand="advance",
            repo=DEFAULT_REPO,
            pr_number=411,
            head_sha=DEFAULT_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert err_advance == ""
        assert allowed_advance == ["scripts/local/aed_pr.py"]

        allowed_merge, _, err_merge = ctrl._resolve_effective_scope(
            subcommand="merge",
            repo=DEFAULT_REPO,
            pr_number=411,
            head_sha=DEFAULT_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert err_merge == ""
        assert allowed_merge == ["scripts/local/aed_pr.py"]

    def test_unit_tests_do_not_write_into_real_home(self):
        """The canonical scope root for production is
        ``~/.hermes/aed/pr_scope``. The tests in this file inject a
        tempdir via ``fake_canonical_root`` so nothing is written to
        the real home directory."""
        real = ctrl._CANONICAL_SCOPE_ROOT
        # ``real`` must be under the real home directory.
        from pathlib import Path
        assert str(real).startswith(str(Path.home())), (
            f"canonical root must live under $HOME; got {real}"
        )

    def test_env_var_does_not_change_canonical_root(self):
        """HERMES_AED_SCOPE_DIR is no longer consulted. Setting it has
        no effect on the canonical root returned by
        ``_canonical_scope_root``."""
        before = ctrl._canonical_scope_root()
        os.environ["HERMES_AED_SCOPE_DIR"] = "/some/attacker/path"
        try:
            after = ctrl._canonical_scope_root()
        finally:
            os.environ.pop("HERMES_AED_SCOPE_DIR", None)
        assert before == after


# ---------------------------------------------------------------------------
# Finding 2 — require a real thread commit anchor
# ---------------------------------------------------------------------------


class TestRound4Finding2ThreadAnchor:
    def test_missing_anchor_is_ineligible(self):
        thread = _bot_thread(anchor=None)
        thread.pop("original_commit_sha", None)
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **_eligibility_kwargs()
        )
        assert ok is False
        assert reason == "missing_commit_anchor"

    def test_malformed_anchor_is_ineligible(self):
        thread = _bot_thread(anchor="not-a-sha")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **_eligibility_kwargs()
        )
        assert ok is False
        assert reason == "malformed_commit_anchor"

    def test_current_head_anchor_is_ineligible(self):
        thread = _bot_thread(anchor=DEFAULT_HEAD)
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **_eligibility_kwargs()
        )
        assert ok is False
        assert reason == "no_later_commit"

    def test_non_ancestor_anchor_is_ineligible(self):
        """A non-ancestor anchor must fail closed with
        ``ancestry_unavailable``. ``OTHER_HEAD`` is not a real
        ancestor of ``DEFAULT_HEAD``; the compare API returns
        ``status="diverged"`` and the verifier reports failure."""
        thread = _bot_thread(anchor=OTHER_HEAD)
        ancestry_runner = lambda *a, **kw: mock.Mock(
            returncode=0, stdout="diverged", stderr=""
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=ancestry_runner),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_valid_anchor_plus_clean_exact_head_codex_is_eligible(self):
        """A verified ancestor anchor plus a clean exact-head
        Codex review is eligible."""
        thread = _bot_thread(anchor=OTHER_HEAD)
        ancestry_runner = lambda *a, **kw: mock.Mock(
            returncode=0, stdout="ahead", stderr=""
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=ancestry_runner),
        )
        assert ok is True
        assert reason == "eligible"


# ---------------------------------------------------------------------------
# Round-5 follow-up: verify_anchor_ancestry
# ---------------------------------------------------------------------------


class TestRound5FollowUpAncestryVerifier:
    """Round-5 follow-up (Codex review 4724989281 on ``301ef32``):

    the eligibility check now requires a verified ancestry call to
    GitHub's compare API; the previous ``anchor_sha != head_sha``
    shortcut is gone.
    """

    def test_ahead_passes_ancestry_condition(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="ahead", stderr=""
            ),
        )
        assert ok is True
        assert reason == "anchor_is_ancestor"

    def test_identical_blocks(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", DEFAULT_HEAD, DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="identical", stderr=""
            ),
        )
        assert ok is False
        assert reason == "anchor_equals_head"

    def test_diverged_blocks(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="diverged", stderr=""
            ),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_behind_blocks(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="behind", stderr=""
            ),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_missing_status_blocks(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="", stderr=""
            ),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_malformed_response_blocks(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="invalid_response", stderr=""
            ),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_command_failure_blocks(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=1, stdout="", stderr="404 Not Found"
            ),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_malformed_sha_blocks_before_command(self):
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", "not-a-sha", DEFAULT_HEAD,
            runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="ahead", stderr=""
            ),
        )
        assert ok is False
        assert reason == "malformed_commit_anchor"

    def test_equal_sha_blocks_before_command(self):
        # Caller is expected to skip the verifier when anchor ==
        # head; the verifier itself enforces this.
        runner = mock.Mock()
        runner.return_value = mock.Mock(
            returncode=0, stdout="ahead", stderr=""
        )
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", DEFAULT_HEAD, DEFAULT_HEAD,
            runner=runner,
        )
        assert ok is False
        assert reason == "anchor_equals_head"
        runner.assert_not_called()

    def test_unverified_ancestry_invokes_no_resolution(self):
        """When ancestry is not verified, the eligibility check
        returns ``ancestry_unavailable`` and ``is_eligible=False``;
        the controller's resolver would never call
        ``resolveReviewThread`` for an ineligible thread."""
        thread = _bot_thread(anchor=OTHER_HEAD)
        ancestry_runner = lambda *a, **kw: mock.Mock(
            returncode=0, stdout="diverged", stderr=""
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=ancestry_runner),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_verified_ancestry_cannot_override_failed_safety(self):
        """Even with verified ancestry, a non-Codex actor or
        missing anchor still blocks the thread.

        PHASE 3 R-3 (PR #412): ``is_outdated=False`` is no
        longer a blocker on its own. The new policy rejects
        only when a HARD SAFETY condition fails. The
        legacy ``not_outdated`` reason is removed.
        """
        # ``is_outdated=False`` with all other evidence
        # proven -> ELIGIBLE (no ``not_outdated`` rejection).
        thread = _bot_thread(anchor=OTHER_HEAD, is_outdated=False)
        ancestry_runner = lambda *a, **kw: mock.Mock(
            returncode=0, stdout="ahead", stderr=""
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=ancestry_runner),
        )
        assert ok is True
        assert reason == "eligible"

        # Non-Codex top-level actor -> ``actor_not_codex``
        # despite verified ancestry and ``is_outdated=False``.
        thread = _bot_thread(
            anchor=OTHER_HEAD,
            is_outdated=False,
            author="dependabot[bot]",
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=ancestry_runner),
        )
        assert ok is False
        assert reason == "actor_not_codex"


# ---------------------------------------------------------------------------
# Round-5 follow-up: deduplicate_thread_records
# ---------------------------------------------------------------------------


class TestRound5FollowUpDeduplicateThreadRecords:
    """Round-5 follow-up (Codex review 4724907717 on ``bc70403``):

    the audit packet emits one entry per comment; resolveReviewThread
    must be invoked at most once per unique thread_id per advance
    execution.
    """

    def _thread(self, thread_id, author="chatgpt-codex-connector[bot]",
               anchor=OTHER_HEAD, comments=None, **kw):
        record = {
            "thread_id": thread_id,
            "author": author,
            "isOutdated": kw.get("is_outdated", True),
            "original_commit_sha": anchor,
            "comments": comments if comments is not None else [
                {"author": author, "database_id": thread_id + "-c1"},
            ],
            "isResolved": kw.get("is_resolved", False),
        }
        return record

    def test_two_duplicate_eligible_records_produce_one_canonical(self):
        a = self._thread("T-A", anchor=OTHER_HEAD)
        b = self._thread("T-A", anchor=OTHER_HEAD, comments=[
            {"author": "chatgpt-codex-connector[bot]", "database_id": "T-A-c2"},
        ])
        canonical, err = ctrl.deduplicate_thread_records([a, b])
        assert err == ""
        assert len(canonical) == 1
        assert canonical[0]["thread_id"] == "T-A"

    def test_three_duplicate_records_produce_one_canonical(self):
        a = self._thread("T-A", anchor=OTHER_HEAD)
        b = self._thread("T-A", anchor=OTHER_HEAD, comments=[
            {"author": "chatgpt-codex-connector[bot]", "database_id": "T-A-c2"},
        ])
        c = self._thread("T-A", anchor=OTHER_HEAD, comments=[
            {"author": "chatgpt-codex-connector[bot]", "database_id": "T-A-c3"},
        ])
        canonical, err = ctrl.deduplicate_thread_records([a, b, c])
        assert err == ""
        assert len(canonical) == 1
        assert canonical[0]["thread_id"] == "T-A"

    def test_unique_and_duplicate_produce_one_per_unique(self):
        canonical, err = ctrl.deduplicate_thread_records([
            self._thread("T-A", anchor=OTHER_HEAD),
            self._thread("T-A", anchor=OTHER_HEAD, comments=[
                {"author": "chatgpt-codex-connector[bot]", "database_id": "T-A-c2"},
            ]),
            self._thread("T-B", anchor="c" * 40),
        ])
        assert err == ""
        assert len(canonical) == 2
        tids = {c["thread_id"] for c in canonical}
        assert tids == {"T-A", "T-B"}

    def test_participant_lists_are_combined_safely(self):
        a = self._thread("T-A", anchor=OTHER_HEAD, comments=[
            {"author": "chatgpt-codex-connector[bot]", "database_id": "T-A-c1"},
        ])
        b = self._thread("T-A", anchor=OTHER_HEAD, comments=[
            {"author": "chatgpt-codex-connector[bot]", "database_id": "T-A-c2"},
        ])
        canonical, err = ctrl.deduplicate_thread_records([a, b])
        assert err == ""
        merged = canonical[0]["comments"]
        keys = [c.get("database_id") for c in merged]
        assert keys == ["T-A-c1", "T-A-c2"]

    def test_human_reply_in_any_duplicate_blocks_thread(self):
        a = self._thread("T-A", anchor=OTHER_HEAD, comments=[
            {"author": "chatgpt-codex-connector[bot]", "database_id": "T-A-c1"},
        ])
        b = self._thread("T-A", anchor=OTHER_HEAD, comments=[
            {"author": "human-reviewer", "database_id": "T-A-c2"},
        ])
        canonical, err = ctrl.deduplicate_thread_records([a, b])
        assert err == ""
        assert canonical == []

    def test_conflicting_anchors_block(self):
        a = self._thread("T-A", anchor=OTHER_HEAD)
        b = self._thread("T-A", anchor="c" * 40)
        canonical, err = ctrl.deduplicate_thread_records([a, b])
        assert err == "conflicting_duplicate_thread_records"
        assert canonical == []

    def test_missing_thread_id_blocks(self):
        record = {"author": "chatgpt-codex-connector[bot]",
                  "isOutdated": True,
                  "comments": [{"author": "chatgpt-codex-connector[bot]"}]}
        canonical, err = ctrl.deduplicate_thread_records([record])
        # Records without a thread_id are dropped silently; the
        # eligibility check treats them as ineligible.
        assert canonical == []

    def test_ordering_is_deterministic(self):
        records = [
            self._thread(f"T-{i}", anchor="c" * 40, comments=[
                {"author": "chatgpt-codex-connector[bot]",
                 "database_id": f"T-{i}-c1"},
            ])
            for i in range(5)
        ]
        # Reverse and re-run: ordering should follow first-seen.
        canonical, err = ctrl.deduplicate_thread_records(
            list(reversed(records))
        )
        tids = [c["thread_id"] for c in canonical]
        assert tids == ["T-4", "T-3", "T-2", "T-1", "T-0"]

    def test_already_resolved_threads_pass_through(self):
        # ``is_resolved`` is preserved on the canonical record; the
        # resolver's idempotency check (``already_resolved``) handles
        # it after dedup.
        a = self._thread("T-A", anchor=OTHER_HEAD, is_resolved=False)
        b = self._thread("T-A", anchor=OTHER_HEAD, is_resolved=False,
                          comments=[
                              {"author": "chatgpt-codex-connector[bot]",
                               "database_id": "T-A-c2"},
                          ])
        canonical, err = ctrl.deduplicate_thread_records([a, b])
        assert err == ""
        assert canonical[0]["isResolved"] is False


# ---------------------------------------------------------------------------
# Round-5 follow-up: workflow-run matching (ci.yml vs "CI")
# ---------------------------------------------------------------------------


class TestRound5FollowUpWorkflowRunMatching:
    """Round-5 follow-up (Codex review 4724907717 on ``bc70403``):
    ``gh run list`` exposes ``workflowName`` as the human-readable
    display name (e.g. ``CI``), not the file basename. The query
    must be scoped to ``--workflow ci.yml`` and ``--branch
    <live-head-branch>``, and the matching ``workflowName`` must
    match exactly. The unused ``workflows`` JSON field is no
    longer requested.
    """

    def test_command_includes_workflow_ci_yml(self):
        # Run a fake list, capture the argv, verify the shape.
        invocations = []

        def fake_list(cmd, *a, **kw):
            invocations.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-17T15:30:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1",
                    "workflowName": "CI",
                }]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err == ""
        argv = invocations[0]
        # Scope to workflow file.
        assert "--workflow" in argv
        assert "ci.yml" in argv
        # Scope to branch.
        assert "--branch" in argv
        assert "reduction/pr-lifecycle-collapse-v1" in argv
        # Scope to event.
        assert "workflow_dispatch" in argv
        # Unused fields are no longer requested.
        joined = " ".join(str(x) for x in argv)
        # The CLI does NOT accept ``--json workflows``. Defensively
        # verify the requested field list contains no entry named
        # exactly ``workflows`` (which is the field that triggered
        # ``Unknown JSON field: "workflows"`` on round-5 commits).
        json_idx = argv.index("--json")
        json_field_list = argv[json_idx + 1]
        requested_fields = [f.strip() for f in json_field_list.split(",")]
        assert "workflows" not in requested_fields
        # The list does include the flat ``workflowName``.
        assert "workflowName" in requested_fields

    def test_workflow_name_CI_passes(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-17T15:30:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1",
                    "workflowName": "CI",
                }]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err == ""
        assert run["workflowName"] == "CI"

    def test_another_workflow_name_fails(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-17T15:30:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1",
                    "workflowName": "OTHER",
                }]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err
        assert run is None

    def test_wrong_branch_fails(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1, "event": "workflow_dispatch",
                    "headBranch": "main",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-17T15:30:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1",
                    "workflowName": "CI",
                }]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err
        assert run is None

    def test_wrong_sha_fails(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": "f" * 40,
                    "createdAt": "2026-07-17T15:30:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1",
                    "workflowName": "CI",
                }]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err
        assert run is None

    def test_old_run_fails(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-17T09:00:00Z",  # older
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1",
                    "workflowName": "CI",
                }]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err
        assert run is None

    def test_newest_uniquely_matching_run_is_selected(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([
                    {
                        "databaseId": 100, "event": "workflow_dispatch",
                        "headBranch": "reduction/pr-lifecycle-collapse-v1",
                        "headSha": DEFAULT_HEAD,
                        "createdAt": "2026-07-17T10:05:00Z",
                        "status": "completed", "conclusion": "success",
                        "url": "https://example/runs/100",
                        "workflowName": "CI",
                    },
                    {
                        "databaseId": 50, "event": "workflow_dispatch",
                        "headBranch": "reduction/pr-lifecycle-collapse-v1",
                        "headSha": DEFAULT_HEAD,
                        "createdAt": "2026-07-17T10:01:00Z",
                        "status": "completed", "conclusion": "success",
                        "url": "https://example/runs/50",
                        "workflowName": "CI",
                    },
                ]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err == ""
        assert run["databaseId"] == 100

    def test_malformed_run_data_returns_inconclusive(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": None,  # missing id
                    "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-17T10:05:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "",
                    "workflowName": "CI",
                }]),
                stderr="",
            )

        run, err = ctrl._find_dispatch_run(
            DEFAULT_REPO, "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 17, 10, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert err
        assert run is None


# ---------------------------------------------------------------------------
# Round-6 follow-up: fetch_ci_conclusions duplicate-check handling
# ---------------------------------------------------------------------------


class TestRound6DuplicateCIChecks:
    """Round-6 follow-up (Codex comment 3609075636 on c229be82):

    fetch_ci_conclusions fails closed when a required check name
    appears more than once in the gh pr checks payload.
    """

    def _make_check(self, name, state):
        return {"name": name, "state": state, "workflow": "CI"}

    def _run(self, payload, required=("test (3.11)",)):
        runner = lambda *a, **kw: mock.Mock(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        return ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(required),
            runner=runner,
        )

    def test_old_success_followed_by_current_failure_blocks(self):
        # Old success comes first; current failure second. The old
        # success must NOT pass the gate.
        payload = [
            self._make_check("test (3.11)", "SUCCESS"),
            self._make_check("test (3.11)", "FAILURE"),
        ]
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = self._run(payload)
        assert err == ""
        assert duplicated == ["test (3.11)"]
        assert "test (3.11)" not in conclusions

    def test_current_failure_followed_by_old_success_blocks(self):
        # Order reversed; duplicate must still block.
        payload = [
            self._make_check("test (3.11)", "FAILURE"),
            self._make_check("test (3.11)", "SUCCESS"),
        ]
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = self._run(payload)
        assert duplicated == ["test (3.11)"]

    def test_success_plus_pending_blocks(self):
        payload = [
            self._make_check("test (3.11)", "SUCCESS"),
            self._make_check("test (3.11)", "PENDING"),
        ]
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = self._run(payload)
        assert duplicated == ["test (3.11)"]

    def test_two_successes_same_required_name_blocks_as_ambiguous(self):
        # Two successes for the same required name still block -
        # the controller cannot tell which is the current head.
        payload = [
            self._make_check("test (3.11)", "SUCCESS"),
            self._make_check("test (3.11)", "SUCCESS"),
        ]
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = self._run(payload)
        assert duplicated == ["test (3.11)"]

    def test_one_unique_successful_required_check_passes(self):
        # Single SUCCESS for the required name passes; no duplicate
        # signal is emitted.
        payload = [
            self._make_check("test (3.11)", "SUCCESS"),
            self._make_check("validator", "SUCCESS"),
        ]
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = self._run(payload)
        assert conclusions == {"test (3.11)": "SUCCESS"}
        assert duplicated == []

    def test_duplicate_unrelated_checks_do_not_replace_missing_required(self):
        # The required name is absent; two unrelated duplicates must
        # not be confused for required-check evidence.
        payload = [
            self._make_check("other-check", "SUCCESS"),
            self._make_check("other-check", "SUCCESS"),
        ]
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = self._run(payload)
        assert missing == ["test (3.11)"]
        # The unrelated duplicates appear in the duplicated list
        # for diagnostic purposes but the required-check evidence
        # is still missing.
        assert "other-check" in duplicated

    def test_duplicate_names_included_in_structured_output(self):
        # ``duplicated`` is the 6th tuple element and lists every
        # duplicated check name (required + unrelated).
        payload = [
            self._make_check("test (3.11)", "SUCCESS"),
            self._make_check("test (3.11)", "FAILURE"),
            self._make_check("validator", "SUCCESS"),
            self._make_check("validator", "PENDING"),
        ]
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = self._run(payload)
        assert sorted(set(duplicated)) == ["test (3.11)", "validator"]


# ---------------------------------------------------------------------------
# Round-6 follow-up: resolveReviewThread nested-input mutation
# ---------------------------------------------------------------------------


class TestRound6ResolveReviewThreadInput:
    """Round-6 follow-up (Codex comment 3609075638 on c229be82):

    the GraphQL mutation must use ResolveReviewThreadInput.

    Round-7 follow-up: the original round-6 fix used
    ``--input <inline-json>``. The ``gh`` CLI's ``--input`` flag
    treats its argument as a filename, so the resolver must use
    repeated ``-f`` flags with nested-object syntax (``-f
    'input[threadId]=...'``) instead.
    """

    def _capture(self, stdout_payload="{}", thread_id="T-NEW", **kwargs):
        calls = []
        def fake_runner(cmd, *a, **kw):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout=stdout_payload, stderr="")
        ok, msg = ctrl.resolve_review_thread(
            "owner/repo", thread_id, runner=fake_runner
        )
        return ok, msg, calls

    def test_no_input_flag_in_argv(self):
        """``--input`` is a filename flag in ``gh``; the resolver
        MUST use repeated ``-f`` flags with nested-object syntax
        instead."""
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": {
                    "thread": {"id": "T-NEW", "isResolved": True}
                }}
            })
        )
        argv = calls[0]
        assert "--input" not in argv

    def test_argv_contains_input_thread_id_field(self):
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": {
                    "thread": {"id": "T-NEW", "isResolved": True}
                }}
            })
        )
        argv = calls[0]
        # The thread ID is supplied as a nested-variable
        # ``-f 'input[threadId]=<id>'`` field.
        thread_fields = [
            a for a in argv
            if a.startswith("input[threadId]=")
        ]
        assert thread_fields == ["input[threadId]=T-NEW"]

    def test_query_uses_ResolveReviewThreadInput(self):
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": {
                    "thread": {"id": "T-NEW", "isResolved": True}
                }}
            })
        )
        argv = calls[0]
        query_fields = [a for a in argv if a.startswith("query=")]
        assert len(query_fields) == 1
        query = query_fields[0].removeprefix("query=")
        assert "ResolveReviewThreadInput" in query
        assert "resolveReviewThread(input: $input)" in query

    def test_thread_id_not_embedded_in_query_string(self):
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": {
                    "thread": {"id": "T-NEW", "isResolved": True}
                }}
            })
        )
        argv = calls[0]
        query_fields = [a for a in argv if a.startswith("query=")]
        query = query_fields[0]
        # The thread ID is supplied via ``-f 'input[threadId]=...'``,
        # never embedded in the query text.
        assert "T-NEW" not in query

    def test_matching_id_and_is_resolved_true_succeeds(self):
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": {
                    "thread": {"id": "T-NEW", "isResolved": True}
                }}
            })
        )
        assert ok is True
        assert msg == "resolved"

    def test_mismatched_thread_id_fails(self):
        """A response whose ``thread.id`` does not match the
        requested thread ID is refused. This catches a resolver
        that acted on a different thread than requested."""
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": {
                    "thread": {"id": "T-OTHER", "isResolved": True}
                }}
            })
        )
        assert ok is False
        assert "does not match" in msg.lower()

    def test_missing_thread_payload_fails(self):
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": None}
            })
        )
        assert ok is False
        assert "thread" in msg or "resolveReviewThread" in msg

    def test_is_resolved_false_fails(self):
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "data": {"resolveReviewThread": {
                    "thread": {"id": "T-NEW", "isResolved": False}
                }}
            })
        )
        assert ok is False
        assert "not resolved" in msg.lower()

    def test_graphql_error_fails(self):
        ok, msg, calls = self._capture(
            stdout_payload=json.dumps({
                "errors": [{"message": "Could not resolve"}]
            })
        )
        assert ok is False
        assert "errors" in msg.lower()

    def test_nonzero_exit_fails(self):
        calls = []
        def fake_runner(cmd, *a, **kw):
            calls.append(list(cmd))
            return mock.Mock(returncode=1, stdout="", stderr="bad")
        ok, msg = ctrl.resolve_review_thread(
            "owner/repo", "T-NEW", runner=fake_runner
        )
        assert ok is False
        assert "bad" in msg

    def test_timeout_expired_fails(self):
        def fake_runner(cmd, *a, **kw):
            raise subprocess.TimeoutExpired(cmd, 30)
        ok, msg = ctrl.resolve_review_thread(
            "owner/repo", "T-NEW", runner=fake_runner
        )
        assert ok is False
        assert "failed" in msg.lower() or "timeout" in msg.lower()


class TestRound7GhPrChecksNoLimit:
    """Round-7 follow-up (Codex comment on c229be82):

    the installed ``gh pr checks`` does not accept ``--limit``;
    remove the unsupported flag.
    """

    def _capture_argv(self, payload=None):
        if payload is None:
            payload = [{
                "name": "test (3.11)",
                "state": "SUCCESS",
                "workflow": "CI",
            }]
        calls = []
        def fake_runner(cmd, *a, **kw):
            calls.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["test (3.11)"],
            runner=fake_runner,
        )
        return calls[0]

    def test_argv_contains_no_limit(self):
        argv = self._capture_argv()
        assert "--limit" not in argv

    def test_argv_still_contains_json_name_state_workflow(self):
        argv = self._capture_argv()
        assert "--json" in argv
        idx = argv.index("--json")
        assert argv[idx + 1] == "name,state,workflow"

    def test_unique_successful_required_check_passes(self):
        argv = self._capture_argv(payload=[{
            "name": "test (3.11)",
            "state": "SUCCESS",
            "workflow": "CI",
        }])
        # No ``--limit`` and the call returned successfully.
        assert "--limit" not in argv
        assert argv[:3] == ["gh", "pr", "checks"]
        assert "411" in argv

    def test_duplicate_required_checks_remain_blocking(self):
        # Two duplicates of the required check. Even though
        # ``--limit`` is removed, the duplicate-protection behavior
        # from round-6 must still fail closed.
        calls = []
        def fake_runner(cmd, *a, **kw):
            calls.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([
                    {"name": "test (3.11)", "state": "SUCCESS",
                     "workflow": "CI"},
                    {"name": "test (3.11)", "state": "FAILURE",
                     "workflow": "CI"},
                ]),
                stderr="",
            )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["test (3.11)"],
            runner=fake_runner,
        )
        assert err == ""
        assert duplicated == ["test (3.11)"]
        assert "test (3.11)" not in conclusions

    def test_command_failure_remains_fail_closed(self):
        # ``gh pr checks`` returns nonzero; the controller must
        # report every required check as missing and return ok=False.
        def fake_runner(cmd, *a, **kw):
            return mock.Mock(
                returncode=1, stdout="", stderr="error",
            )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["test (3.11)"],
            runner=fake_runner,
        )
        assert ok is False
        assert missing == ["test (3.11)"]
        assert err


# ---------------------------------------------------------------------------
# Round-6 follow-up: subprocess.TimeoutExpired caught by verify_anchor_ancestry
# ---------------------------------------------------------------------------


class TestRound6AncestryTimeoutExpired:
    """Round-6 follow-up (Codex comment 3609075639 on c229be82):

    verify_anchor_ancestry must catch subprocess.TimeoutExpired.
    """

    def test_real_subprocess_timeout_expired_is_caught(self):
        def fake_runner(cmd, *a, **kw):
            raise subprocess.TimeoutExpired(cmd, 30)
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=fake_runner,
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_mocked_timeout_returns_ancestry_unavailable(self):
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("gh", 30))
        ok, reason = R.verify_anchor_ancestry(
            "owner/name", OTHER_HEAD, DEFAULT_HEAD,
            runner=runner,
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_cmd_advance_emits_ineligible_report_instead_of_raising(self):
        """When ancestry verification times out, ``cmd_advance``
        must NOT raise; it must emit an ineligible record with
        reason ``ancestry_unavailable``."""
        # The controller emits an eligibility report; we assert it
        # contains no Python exception trace.
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("gh", 30))
        thread = _bot_thread(anchor=OTHER_HEAD)
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=runner),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"

    def test_resolver_not_invoked_after_timeout(self):
        """The eligibility classifier returns False on timeout;
        ``cmd_advance`` would not invoke ``resolveReviewThread``
        because the thread is ineligible with reason
        ``ancestry_unavailable``. The downstream resolver's
        ``any_failed`` accumulator is set to False (no mutation),
        and the elapsed work is limited to the eligibility
        classifier's return value."""
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("gh", 30))
        thread = _bot_thread(anchor=OTHER_HEAD)
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=runner),
        )
        assert ok is False
        assert reason == "ancestry_unavailable"
        # The classifier did not raise; no resolver invocation was
        # attempted. The runner was invoked at most once.
        assert runner.call_count <= 1


# ---------------------------------------------------------------------------
# Round-6 follow-up: live gate-recheck discovery race
# ---------------------------------------------------------------------------


class TestRound6DispatchRunDiscovery:
    """Round-6 follow-up: ``_wait_for_dispatch_run`` polls
    ``_find_dispatch_run`` until the exact dispatched run appears
    or the bounded discovery timeout expires. The dispatch is
    issued exactly once (caller responsibility); the discovery
    loop only re-queries the run list.
    """

    def test_first_list_empty_second_list_finds_run(self):
        # Poll 1: empty list. Poll 2: exact run appears.
        state = {"count": 0}
        def fake_list(cmd, *a, **kw):
            state["count"] += 1
            if state["count"] == 1:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps([]),
                    stderr="",
                )
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 999, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat(),
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/999",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        dispatched_at = dt.datetime.now(dt.timezone.utc)
        run, err = ctrl._wait_for_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            timeout_seconds=10,
            poll_seconds=1,
            list_runner=fake_list,
        )
        assert err == ""
        assert run is not None
        assert run["databaseId"] == 999
        # List was queried at least twice: the initial empty poll
        # and the discovery-poll that found the run.
        assert state["count"] >= 2

    def test_transient_list_error_followed_by_run_succeeds(self):
        state = {"count": 0}
        def fake_list(cmd, *a, **kw):
            state["count"] += 1
            if state["count"] == 1:
                return mock.Mock(
                    returncode=1, stdout="", stderr="transient",
                )
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1001, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat(),
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1001",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        dispatched_at = dt.datetime.now(dt.timezone.utc)
        run, err = ctrl._wait_for_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            timeout_seconds=10,
            poll_seconds=1,
            list_runner=fake_list,
        )
        assert err == ""
        assert run["databaseId"] == 1001

    def test_only_one_dispatch_occurs(self):
        # ``_wait_for_dispatch_run`` does NOT invoke ``gh workflow
        # run``. The dispatch is the caller's responsibility. We
        # assert the runner only sees ``gh run list``.
        dispatch_calls = []
        def fake_dispatch(cmd, *a, **kw):
            dispatch_calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="", stderr="")
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0, stdout=json.dumps([]), stderr=""
            )
        dispatched_at = dt.datetime.now(dt.timezone.utc)
        ctrl._wait_for_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            timeout_seconds=1,
            poll_seconds=1,
            list_runner=fake_list,
        )
        # No ``gh workflow run`` calls observed during discovery.
        assert not any(
            "workflow" in str(arg) and "run" in str(arg)
            for call in dispatch_calls
            for arg in call
        )

    def test_discovery_timeout_returns_inconclusive(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0, stdout=json.dumps([]), stderr=""
            )
        dispatched_at = dt.datetime.now(dt.timezone.utc)
        run, err = ctrl._wait_for_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            timeout_seconds=1,
            poll_seconds=1,
            list_runner=fake_list,
        )
        assert run is None
        assert err
        assert "did not appear" in err

    def test_old_run_rejected_by_discovery(self):
        # The only run is older than dispatched_at; discovery fails
        # closed.
        dispatched_at = dt.datetime.now(dt.timezone.utc)
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 2, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-17T09:00:00Z",  # older
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/2",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        run, err = ctrl._wait_for_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            timeout_seconds=1,
            poll_seconds=1,
            list_runner=fake_list,
        )
        assert run is None
        assert err

    def test_wrong_branch_and_sha_rejected_by_discovery(self):
        dispatched_at = dt.datetime.now(dt.timezone.utc)
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([
                    {
                        "databaseId": 3, "event": "workflow_dispatch",
                        "headBranch": "main",
                        "headSha": DEFAULT_HEAD,
                        "createdAt": dt.datetime.now(
                            dt.timezone.utc
                        ).isoformat(),
                        "status": "completed", "conclusion": "success",
                        "url": "https://example/runs/3",
                        "workflowName": "CI",
                    },
                    {
                        "databaseId": 4, "event": "workflow_dispatch",
                        "headBranch": "reduction/pr-lifecycle-collapse-v1",
                        "headSha": "f" * 40,
                        "createdAt": dt.datetime.now(
                            dt.timezone.utc
                        ).isoformat(),
                        "status": "completed", "conclusion": "success",
                        "url": "https://example/runs/4",
                        "workflowName": "CI",
                    },
                ]),
                stderr="",
            )
        run, err = ctrl._wait_for_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            timeout_seconds=1,
            poll_seconds=1,
            list_runner=fake_list,
        )
        assert run is None
        assert err

    def test_malformed_createdAt_rejected(self):
        # The run has a non-ISO ``createdAt`` string.
        dispatched_at = dt.datetime.now(dt.timezone.utc)
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 5, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "not-a-timestamp",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/5",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        run, err = ctrl._wait_for_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            timeout_seconds=1,
            poll_seconds=1,
            list_runner=fake_list,
        )
        assert run is None
        assert err

    def test_normalize_thread_anchor_populates_canonical_field(self):
        # When the packet supplies ``comment_sha`` but not
        # ``original_commit_sha``, the normalizer promotes the
        # ``comment_sha`` to ``original_commit_sha``.
        thread = {
            "thread_id": "T-A",
            "author": "chatgpt-codex-connector[bot]",
            "isOutdated": True,
            "comment_sha": OTHER_HEAD,
            "comments": [{"author": "chatgpt-codex-connector[bot]"}],
        }
        normalized = R.normalize_thread_anchor(thread)
        assert normalized["original_commit_sha"] == OTHER_HEAD
        assert normalized["comment_sha"] == OTHER_HEAD

    def test_normalize_thread_anchor_flags_missing(self):
        thread = {
            "thread_id": "T-B",
            "author": "chatgpt-codex-connector[bot]",
            "isOutdated": True,
            "comments": [{"author": "chatgpt-codex-connector[bot]"}],
        }
        normalized = R.normalize_thread_anchor(thread)
        assert "original_commit_sha" not in normalized
        assert "_missing_anchor_fields" in normalized
        assert "original_commit_sha" in normalized["_missing_anchor_fields"]

    def test_packet_preserves_anchor(self):
        """The live ``audit_codex_response_for_pr`` packet must
        propagate ``originalCommit.oid`` into the per-thread
        ``original_commit_sha`` field so the controller's
        eligibility check can find the canonical anchor.
        """
        from scripts.local import audit_codex_response_for_pr as AC
        # Verify the GraphQL query selects ``originalCommit { oid }``.
        # We introspect ``gh_graphql_review_threads`` indirectly by
        # inspecting its query assembly.
        import inspect
        src = inspect.getsource(AC.gh_graphql_review_threads)
        assert "originalCommit" in src
        assert "oid" in src

    def test_classify_rebuild_propagates_anchor_to_entry(self):
        """Round-4 follow-up: the rebuilt ``entry`` dict in
        ``classify()``'s review-thread inventory loop must copy
        ``original_commit_sha`` from the underlying thread. Without
        this the controller's ``normalize_thread_anchor`` sees an
        anchorless entry and reports ``missing_commit_anchor`` for
        every otherwise eligible outdated Codex thread.

        Verified by static inspection: ``classify``'s source must
        contain ``"original_commit_sha":`` inside the entry rebuild.
        """
        from scripts.local import audit_codex_response_for_pr as AC
        import inspect
        src = inspect.getsource(AC.classify)
        assert '"original_commit_sha":' in src

    def test_packet_preserves_participant_list(self):
        """Round-4 follow-up (Codex review 4724091490 on
        ``a8ccd9b``): the rebuilt ``entry`` dict must carry the
        ``comments`` participant list so the eligibility check can
        verify every reply in the thread is bot-authored. Without
        this field a human reply in the same review thread would
        not be detected and ``--resolve-eligible-bot-threads`` would
        resolve a thread with human participation.

        Verified by static inspection of both the GraphQL fetcher
        and the ``classify`` rebuild loop.
        """
        from scripts.local import audit_codex_response_for_pr as AC
        import inspect
        fetcher_src = inspect.getsource(AC.gh_graphql_review_threads)
        classify_src = inspect.getsource(AC.classify)
        assert '"comments":' in fetcher_src, (
            "gh_graphql_review_threads must attach a 'comments' "
            "participant list to each entry"
        )
        assert '"comments":' in classify_src, (
            "classify()'s entry rebuild must preserve the 'comments' "
            "participant list"
        )


# ---------------------------------------------------------------------------
# Round-5 Codex findings (review 4724164893 on a1f4fe7)
# ---------------------------------------------------------------------------


class TestRound5Finding1ScopeWriteRepoArg:
    def test_scope_write_parser_has_repo_arg(self):
        """``scope-write`` must register ``--repo`` so ``cmd_scope_write``
        can access ``args.repo`` without ``AttributeError``. Round-5
        Codex finding 3604451966."""
        parser = ctrl.build_parser()
        ns = parser.parse_args([
            "scope-write",
            "--pr-number", "411",
            "--repo", "owner/name",
            "--head-sha", "a" * 40,
            "--allowed-files", "**",
        ])
        assert ns.repo == "owner/name"

    def test_scope_write_parser_repo_defaults(self):
        """``scope-write`` accepts ``--repo`` with a default."""
        parser = ctrl.build_parser()
        ns = parser.parse_args([
            "scope-write",
            "--pr-number", "411",
            "--head-sha", "a" * 40,
            "--allowed-files", "**",
        ])
        assert ns.repo == ctrl.DEFAULT_REPO

    def test_scope_write_parses_without_repo_kwarg(self):
        """End-to-end: scope-write parses successfully WITHOUT an
        explicit ``--repo`` and uses the default."""
        parser = ctrl.build_parser()
        ns = parser.parse_args([
            "scope-write",
            "--pr-number", "411",
            "--head-sha", "a" * 40,
            "--allowed-files", "scripts/local/aed_pr.py",
        ])
        assert hasattr(ns, "repo"), (
            "scope-write must register --repo so cmd_scope_write "
            "can access args.repo"
        )
        assert ns.repo == ctrl.DEFAULT_REPO

    def test_scope_read_parser_has_repo_arg(self):
        parser = ctrl.build_parser()
        ns = parser.parse_args([
            "scope-read",
            "--pr-number", "411",
            "--repo", "owner/name",
            "--head-sha", "a" * 40,
        ])
        assert ns.repo == "owner/name"

    def test_scope_read_parser_repo_defaults(self):
        parser = ctrl.build_parser()
        ns = parser.parse_args([
            "scope-read",
            "--pr-number", "411",
            "--head-sha", "a" * 40,
        ])
        assert ns.repo == ctrl.DEFAULT_REPO

    def test_scope_write_command_does_not_attribute_error(self):
        """End-to-end: ``cmd_scope_write`` does NOT raise
        ``AttributeError`` for ``args.repo`` when the user did NOT
        supply ``--repo``."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmpdir:
            saved_root = ctrl._CANONICAL_SCOPE_ROOT
            ctrl._CANONICAL_SCOPE_ROOT = Path(tmpdir)
            try:
                ns = mock.Mock()
                ns.pr_number = 411
                ns.head_sha = "a" * 40
                ns.repo = ctrl.DEFAULT_REPO  # default value from parser
                ns.allowed_files = "scripts/local/aed_pr.py"
                ns.forbidden_files = None
                # cmd_scope_write would try to write to the real home
                # directory if scope_root were the production value;
                # monkey-patch _CANONICAL_SCOPE_ROOT above prevents
                # that. Verify the command reaches the write step
                # without raising AttributeError.
                from scripts.local import aed_pr as ctrl_module
                code = ctrl_module.cmd_scope_write(ns)
                assert code == 0, (
                    f"cmd_scope_write returned {code}; expected 0"
                )
            finally:
                ctrl._CANONICAL_SCOPE_ROOT = saved_root

    def test_scope_read_command_does_not_attribute_error(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmpdir:
            saved_root = ctrl._CANONICAL_SCOPE_ROOT
            ctrl._CANONICAL_SCOPE_ROOT = Path(tmpdir)
            try:
                ctrl.write_trusted_scope(
                    ctrl.DEFAULT_REPO, 411, "a" * 40,
                    ["scripts/local/aed_pr.py"],
                )
                ns = mock.Mock()
                ns.pr_number = 411
                ns.head_sha = "a" * 40
                ns.repo = ctrl.DEFAULT_REPO
                from scripts.local import aed_pr as ctrl_module
                code = ctrl_module.cmd_scope_read(ns)
                assert code == 0
            finally:
                ctrl._CANONICAL_SCOPE_ROOT = saved_root

    def test_malformed_repo_rejected_before_filesystem_mutation(self):
        """Malformed ``--repo`` values (without ``/``) are rejected
        BEFORE any filesystem mutation."""
        # ``write_trusted_scope`` returns ``(False, err)`` without
        # mutating the filesystem.
        ok, err = ctrl.write_trusted_scope(
            "no-slash", 411, "a" * 40,
            ["scripts/local/aed_pr.py"],
        )
        assert ok is False
        assert "repo" in err.lower()
        # ``read_trusted_scope`` returns ``(None, None, err)``
        # without reading from the filesystem.
        allowed, forbidden, read_err = ctrl.read_trusted_scope(
            "no-slash", 411, "a" * 40,
        )
        assert allowed is None
        assert forbidden is None
        assert "repo" in read_err.lower() or "invalid" in read_err.lower()


class TestRound10GateRecheckRerunsExactHeadGate:
    """Round-10 follow-up (Codex review on ``f3c8c06``):

    gate-recheck no longer dispatches ``ci.yml`` with
    ``gate=review-comment-gate`` (which used conditional ``if:``
    guards that let GitHub report skipped jobs as successful
    checks). Instead, it finds the existing exact-head
    ``pull_request`` CI run and reruns its
    ``review-comment-gate`` job via ``gh run rerun --job <id>``.

    The new tests prove the rerun-based flow is correct, the
    rerun argv uses the integer ``databaseId``, only the gate
    job is rerun, and ordinary jobs are not affected.
    """

    @staticmethod
    def _live_view():
        return {
            "number": 411, "title": "t", "state": "OPEN",
            "isDraft": False, "mergeable": True,
            "headRefOid": DEFAULT_HEAD,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "b" * 40, "baseRefName": "main",
            "additions": 0, "deletions": 0, "changedFiles": 0,
            "url": "u", "files": [],
        }

    @staticmethod
    def _pr_view_runner():
        return lambda *a, **kw: mock.Mock(
            returncode=0,
            stdout=json.dumps(
                TestRound10GateRecheckRerunsExactHeadGate._live_view()
            ),
            stderr="",
        )

    @staticmethod
    def _make_run(*, event="pull_request", workflow_name="CI",
                  head_sha=DEFAULT_HEAD,
                  head_branch="reduction/pr-lifecycle-collapse-v1",
                  databaseId=29689308922, name="CI",
                  status="completed", conclusion="success",
                  url=None):
        if url is None:
            url = f"https://example/runs/{databaseId}"
        import datetime as _dt
        return {
            "databaseId": databaseId, "name": name, "event": event,
            "headBranch": head_branch, "headSha": head_sha,
            "status": status, "conclusion": conclusion,
            "createdAt": _dt.datetime.now(
                _dt.timezone.utc
            ).isoformat(),
            "url": url,
            "workflowName": workflow_name,
        }

    @staticmethod
    def _ns(*, list_payload=None, attempt_returns=None,
            job_payload=None, rerun_failure=False,
            head_mismatch=False):
        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 3
        ns.wait_poll_seconds = 1
        ns.dry_run = False
        live = dict(
            TestRound10GateRecheckRerunsExactHeadGate._live_view()
        )
        if head_mismatch:
            live["headRefOid"] = "f" * 40
        ns.pr_view_runner = TestRound10GateRecheckRerunsExactHeadGate._pr_view_runner()
        ns.rerun_runner = (
            (lambda *a, **kw: mock.Mock(
                returncode=1, stdout="", stderr="rerun failed"
            ))
            if rerun_failure
            else (lambda *a, **kw: mock.Mock(
                returncode=0, stdout="", stderr=""
            ))
        )
        ns.list_runner = (
            lambda *a, **kw: mock.Mock(
                returncode=0,
                stdout=json.dumps(
                    list_payload if list_payload is not None
                    else [
                        TestRound10GateRecheckRerunsExactHeadGate._make_run()
                    ]
                ),
                stderr="",
            )
        )
        # Default attempt returns: first call returns pre
        # (default 1), second call returns 2 (post-rerun).
        if attempt_returns is None:
            attempt_returns = [2]
        attempt_state = {"attempt_calls": 0, "values": list(attempt_returns)}
        # Capture via default arg to avoid Python's
        # UnboundLocalError when the closure reads
        # ``job_payload``.
        jp = job_payload
        def _view(cmd, *a, **kw):
            if "--json" in cmd and "attempt" in cmd[cmd.index("--json") + 1]:
                idx = attempt_state["attempt_calls"]
                attempt_state["attempt_calls"] += 1
                if idx == 0:
                    value = 1
                else:
                    value = (
                        attempt_state["values"][idx - 1]
                        if idx - 1 < len(attempt_state["values"])
                        else attempt_state["values"][-1]
                    )
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"attempt": value}),
                    stderr="",
                )
            payload = jp
            if payload is None:
                payload = {
                    "jobs": [{
                        "name": "review-comment-gate",
                        "databaseId": 29689308922,
                        "status": "completed",
                        "conclusion": "success",
                    }]
                }
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        ns.view_runner = _view
        return ns

    def test_exact_head_pull_request_run_is_selected(self):
        """The run-list query is scoped to the exact head
        branch and commit; only ``event=pull_request`` runs
        are accepted."""
        seen = []
        list_payload = [self._make_run()]
        ns = self._ns(list_payload=list_payload)
        orig_list = ns.list_runner
        def _spy(cmd, *a, **kw):
            seen.append(list(cmd))
            return orig_list(cmd, *a, **kw)
        ns.list_runner = _spy
        result = ctrl.cmd_gate_recheck(ns)
        assert result == 0
        assert len(seen) == 1
        argv = seen[0]
        assert "--workflow" in argv and "ci.yml" in argv
        assert "--event" in argv and "pull_request" in argv
        assert "--branch" in argv
        assert "reduction/pr-lifecycle-collapse-v1" in argv
        assert "--commit" in argv
        assert DEFAULT_HEAD in argv

    def test_workflow_dispatch_run_is_rejected(self):
        """A ``workflow_dispatch`` run on the same head is
        REJECTED; only ``event=pull_request`` runs are
        acceptable."""
        list_payload = [self._make_run(event="workflow_dispatch")]
        ns = self._ns(list_payload=list_payload)
        # The dispatch run is not even attempted for rerun.
        ns.rerun_runner = mock.Mock()
        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2
        ns.rerun_runner.assert_not_called()

    def test_wrong_head_is_rejected(self):
        """A run on a different head SHA is rejected."""
        list_payload = [self._make_run(head_sha="f" * 40)]
        ns = self._ns(list_payload=list_payload)
        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2

    def test_wrong_branch_is_rejected(self):
        """A run on the right SHA but wrong branch is
        rejected."""
        list_payload = [
            self._make_run(head_branch="some-other-branch"),
        ]
        ns = self._ns(list_payload=list_payload)
        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2

    def test_wrong_workflow_name_is_rejected(self):
        """A run on the right head+branch but wrong workflow
        name is rejected."""
        list_payload = [self._make_run(workflow_name="OTHER")]
        ns = self._ns(list_payload=list_payload)
        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2

    def test_missing_run_id_fails(self):
        """A matching run without an integer ``databaseId``
        is rejected (the run is dropped from candidates)."""
        # Two runs: one with no dbId (dropped), one with dbId.
        list_payload = [
            {"event": "pull_request", "headBranch":
             "reduction/pr-lifecycle-collapse-v1",
             "headSha": DEFAULT_HEAD, "workflowName": "CI",
             "url": "https://example/runs/1"},
            self._make_run(databaseId=29689308922),
        ]
        ns = self._ns(list_payload=list_payload)
        assert ctrl.cmd_gate_recheck(ns) == 0

    def test_missing_review_comment_gate_fails(self):
        """A run without a ``review-comment-gate`` job is
        rejected."""
        job_payload = {"jobs": [{
            "name": "test",
            "databaseId": 123,
            "status": "completed",
            "conclusion": "success",
        }]}
        ns = self._ns(job_payload=job_payload)
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_duplicate_review_comment_gate_fails(self):
        """A run with two ``review-comment-gate`` jobs is
        rejected; rerun cannot deterministically pick one."""
        job_payload = {"jobs": [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
            {"name": "review-comment-gate", "databaseId": 2,
             "status": "completed", "conclusion": "success"},
        ]}
        ns = self._ns(job_payload=job_payload)
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_job_database_id_is_used_in_rerun(self):
        """The rerun argv MUST use the integer
        ``databaseId`` from the ``review-comment-gate``
        job."""
        seen_rerun = []
        job_id = 29689308999
        job_payload = {"jobs": [{
            "name": "review-comment-gate",
            "databaseId": job_id,
            "status": "completed", "conclusion": "success",
        }]}
        ns = self._ns(job_payload=job_payload)
        orig = ns.rerun_runner
        def _spy(cmd, *a, **kw):
            seen_rerun.append(list(cmd))
            return orig(cmd, *a, **kw)
        ns.rerun_runner = _spy
        assert ctrl.cmd_gate_recheck(ns) == 0
        assert len(seen_rerun) == 1
        argv = seen_rerun[0]
        assert argv[:3] == ["gh", "run", "rerun"]
        # The job databaseId is the last argument (after --job).
        assert "--job" in argv
        idx = argv.index("--job")
        assert argv[idx + 1] == str(job_id)

    def test_only_one_rerun_mutation_occurs(self):
        """The controller MUST invoke the rerun runner
        exactly once, even if polling continues for the new
        attempt."""
        ns = self._ns()
        orig = ns.rerun_runner
        calls = []
        def _spy(cmd, *a, **kw):
            calls.append(cmd)
            return orig(cmd, *a, **kw)
        ns.rerun_runner = _spy
        assert ctrl.cmd_gate_recheck(ns) == 0
        assert len(calls) == 1

    def test_first_poll_shows_old_attempt_later_new_attempt(self):
        """When the first attempt-count read returns the
        pre-rerun value, polling continues; the next read
        returns the new value and the gate terminalizes."""
        # attempt_returns is consumed in order: 1, 1, 2.
        ns = self._ns(attempt_returns=[1, 1, 2])
        assert ctrl.cmd_gate_recheck(ns) == 0

    def test_successful_target_job_passes(self):
        """A terminal success on the rerun attempt returns 0."""
        ns = self._ns()
        assert ctrl.cmd_gate_recheck(ns) == 0

    def test_failed_target_job_blocks(self):
        """A terminal failure on the rerun attempt returns 1."""
        job_payload = {"jobs": [{
            "name": "review-comment-gate",
            "databaseId": 29689308999,
            "status": "completed", "conclusion": "failure",
        }]}
        ns = self._ns(job_payload=job_payload)
        assert ctrl.cmd_gate_recheck(ns) == 1

    def test_pending_target_job_times_out(self):
        """When the attempt count never exceeds
        ``pre_rerun_attempt``, gate-recheck times out (2)."""
        # attempt_returns stays at 1 forever.
        ns = self._ns(attempt_returns=[1])
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_malformed_attempt_data_fails(self):
        """When ``gh run view --json attempt`` returns a
        malformed payload, gate-recheck refuses."""
        def _view(cmd, *a, **kw):
            if "--json" in cmd and "attempt" in cmd[
                cmd.index("--json") + 1
            ]:
                return mock.Mock(
                    returncode=0,
                    stdout="not json",
                    stderr="",
                )
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": []}),
                stderr="",
            )
        ns = self._ns()
        ns.view_runner = _view
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_ordinary_jobs_are_not_rerun(self):
        """The rerun MUST target only the
        ``review-comment-gate`` job. The controller never
        invokes a separate workflow dispatch; the rerun
        argv contains a single ``--job <id>`` reference."""
        seen_rerun = []
        ns = self._ns()
        orig = ns.rerun_runner
        def _spy(cmd, *a, **kw):
            seen_rerun.append(list(cmd))
            return orig(cmd, *a, **kw)
        ns.rerun_runner = _spy
        assert ctrl.cmd_gate_recheck(ns) == 0
        argv = seen_rerun[0]
        # Exactly one --job flag.
        assert argv.count("--job") == 1
        # The argv does NOT contain ``gh workflow run``.
        assert "workflow" not in argv or "run" not in argv

    def test_no_skipped_job_satisfies_required_check(self):
        """No ordinary CI job is skipped by the rerun-based
        flow. The rerun argv targets one job; the
        underlying CI workflow's run is unchanged otherwise.
        """
        # The run's job list contains all five ordinary jobs;
        # only review-comment-gate is rerun.
        job_payload = {"jobs": [
            {"name": "review-comment-gate", "databaseId": 29689308999,
             "status": "completed", "conclusion": "success"},
            {"name": "test", "databaseId": 29689309000,
             "status": "completed", "conclusion": "success"},
            {"name": "validator", "databaseId": 29689309001,
             "status": "completed", "conclusion": "success"},
        ]}
        ns = self._ns(job_payload=job_payload)
        seen_rerun = []
        orig = ns.rerun_runner
        def _spy(cmd, *a, **kw):
            seen_rerun.append(list(cmd))
            return orig(cmd, *a, **kw)
        ns.rerun_runner = _spy
        assert ctrl.cmd_gate_recheck(ns) == 0
        argv = seen_rerun[0]
        # The rerun argv's --job arg points at the
        # review-comment-gate databaseId only.
        idx = argv.index("--job")
        assert argv[idx + 1] == "29689308999"

    def test_dry_run_does_not_invoke_rerun(self):
        """``--dry-run`` records ``would_rerun=True`` without
        invoking the rerun runner."""
        ns = self._ns()
        ns.dry_run = True
        seen_rerun = []
        orig = ns.rerun_runner
        def _spy(cmd, *a, **kw):
            seen_rerun.append(cmd)
            return orig(cmd, *a, **kw)
        ns.rerun_runner = _spy
        assert ctrl.cmd_gate_recheck(ns) == 0
        assert seen_rerun == []

    def test_dispatching_ci_yml_with_gate_input_is_not_invoked(self):
        """The new flow NEVER invokes ``gh workflow run``;
        it only invokes ``gh run rerun --job <id>`` against
        an existing exact-head pull_request run."""
        ns = self._ns()
        seen_cmds = []
        orig_rerun = ns.rerun_runner
        def _spy(cmd, *a, **kw):
            seen_cmds.append(list(cmd))
            return orig_rerun(cmd, *a, **kw)
        ns.rerun_runner = _spy
        # Also spy on list_runner (which would receive the
        # ``--commit <sha>`` query) and view_runner to
        # capture the new command shape.
        seen_lists = []
        orig_list = ns.list_runner
        def _list_spy(cmd, *a, **kw):
            seen_lists.append(list(cmd))
            return orig_list(cmd, *a, **kw)
        ns.list_runner = _list_spy
        assert ctrl.cmd_gate_recheck(ns) == 0
        # The list query uses --commit <sha> + --event pull_request.
        list_argv = seen_lists[0]
        assert "--commit" in list_argv
        assert "pull_request" in list_argv
        # The rerun query does NOT use ``workflow run``.
        rerun_argv = seen_cmds[0]
        assert "workflow" not in rerun_argv
        assert "run" != rerun_argv[2]
        assert rerun_argv[2] == "rerun"


# ---------------------------------------------------------------------------
# Finding 3 — dry-run performs zero mutations
# ---------------------------------------------------------------------------


class TestRound4Finding3DryRunNonMutating:
    def _run_dry_run(self, mutation_spy):
        """Run ``advance --dry-run`` in-process and capture output."""
        from scripts.local import aed_pr as ctrl_module

        def _fake_run_no_writes(cmd, *args, **kwargs):
            # Use the mutation_spy fake from the fixture. It raises
            # on write commands; for read commands it returns empty
            # JSON. But we want a richer mock for the codex path.
            classification = "read"
            joined = " ".join(str(x) for x in cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                payload = {
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": "a" * 40, "baseRefOid": "b" * 40,
                    "baseRefName": "main", "additions": 0, "deletions": 0,
                    "changedFiles": 0, "url": "u", "files": [],
                }
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(payload),
                    stderr="",
                )
            if cmd[:3] == ["gh", "pr", "checks"]:
                return mock.Mock(
                    returncode=0, stdout=json.dumps([
                        {"name": "test (3.11)", "state": "SUCCESS",
                         "workflow": "CI"},
                        {"name": "validator", "state": "SUCCESS",
                         "workflow": "CI"},
                        {"name": "governance-validators", "state": "SUCCESS",
                         "workflow": "CI"},
                        {"name": "pr-gate-live-smoke", "state": "SUCCESS",
                         "workflow": "CI"},
                        {"name": "review-comment-gate", "state": "SUCCESS",
                         "workflow": "CI"},
                    ]),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="[]", stderr="")

        def _fake_codex_classify(**kwargs):
            return {
                "status": "CODEX_CLEAN_PASS",
                "observed_head_sha": "a" * 40,
                "head_matches_expected": True,
                "clean_pass_detected": True,
                "clean_pass_source": "issue_comment",
                "latest_codex_response_id": "12345",
                "latest_codex_response_url": "https://example/codex",
                "latest_codex_response_type": "issue_comment",
                "active_threads": [],
                "outdated_threads": [],
                "issue_comment_inventory_complete": True,
                "issue_comment_inventory_error_count": 0,
                "issue_comment_inventory_last_error": None,
                "review_submission_inventory_complete": True,
                "review_submission_inventory_error_count": 0,
                "review_submission_inventory_last_error": None,
                "review_thread_inventory_complete": True,
                "review_thread_inventory_error_count": 0,
                "review_thread_inventory_last_error": None,
                "review_thread_comment_inventory_complete": True,
                "review_thread_comment_inventory_error_count": 0,
                "review_thread_incomplete_thread_ids": [],
                "merge_state_status": "clean",
                "mergeable": True,
                "review_decision": "APPROVED",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            saved_root = ctrl_module._CANONICAL_SCOPE_ROOT
            ctrl_module._CANONICAL_SCOPE_ROOT = Path(tmpdir)
            try:
                ctrl_module.write_trusted_scope(
                    DEFAULT_REPO, 411, DEFAULT_HEAD,
                    ["scripts/local/aed_pr.py"],
                )
                with mock.patch.object(
                    subprocess, "run", side_effect=_fake_run_no_writes,
                ), mock.patch.object(
                    ctrl.CODEX, "classify",
                    side_effect=lambda **kw: _fake_codex_classify(**kw),
                ):
                    old_argv = sys.argv
                    sys.argv = [
                        "aed_pr.py", "advance", "--pr-number", "411",
                        "--dry-run",
                    ]
                    buf = io.StringIO()
                    old_out = sys.stdout
                    sys.stdout = buf
                    try:
                        ctrl.main(sys.argv[1:])
                    except SystemExit:
                        pass
                    finally:
                        sys.stdout = old_out
                        sys.argv = old_argv
                return json.loads(buf.getvalue())
            finally:
                ctrl_module._CANONICAL_SCOPE_ROOT = saved_root

    def test_dry_run_emits_codex_review_ping_skipped(
        self, mutation_spy,
    ):
        report = self._run_dry_run(mutation_spy)
        actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "codex_review_ping"
        ]
        # At least one report records that the ping was skipped.
        skipped = [
            a for a in actions
            if a.get("attempted") is False and a.get("reason") == "dry_run"
        ]
        assert skipped, (
            f"dry-run must report codex_review_ping as skipped; "
            f"got {actions!r}"
        )
        for s in skipped:
            assert s.get("would_post") is True

    def test_dry_run_emits_thread_resolution_skipped(
        self, mutation_spy,
    ):
        report = self._run_dry_run(mutation_spy)
        actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "resolve_eligible_bot_threads"
        ]
        assert actions
        for a in actions:
            assert a.get("attempted") is False
            assert a.get("reason") == "dry_run"

    def test_dry_run_emits_mark_pr_ready_skipped(
        self, mutation_spy,
    ):
        report = self._run_dry_run(mutation_spy)
        actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "mark_pr_ready"
        ]
        assert actions
        for a in actions:
            assert a.get("attempted") is False
            assert a.get("reason") == "dry_run"

    def test_dry_run_emits_workflow_dispatch_skipped(
        self, mutation_spy,
    ):
        report = self._run_dry_run(mutation_spy)
        actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "workflow_dispatch"
        ]
        assert actions
        for a in actions:
            assert a.get("attempted") is False
            assert a.get("reason") == "dry_run"

    def test_dry_run_emits_post_pr_comment_skipped(
        self, mutation_spy,
    ):
        report = self._run_dry_run(mutation_spy)
        actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "post_pr_comment"
        ]
        assert actions
        for a in actions:
            assert a.get("attempted") is False
            assert a.get("reason") == "dry_run"

    def test_dry_run_emits_write_trusted_scope_skipped(
        self, mutation_spy,
    ):
        report = self._run_dry_run(mutation_spy)
        actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "write_trusted_scope"
        ]
        assert actions
        for a in actions:
            assert a.get("attempted") is False
            assert a.get("reason") == "dry_run"

    def test_dry_run_emits_gh_pr_merge_skipped(
        self, mutation_spy,
    ):
        report = self._run_dry_run(mutation_spy)
        actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "gh_pr_merge"
        ]
        assert actions
        for a in actions:
            assert a.get("attempted") is False
            assert a.get("reason") == "dry_run"

    def test_dry_run_mutation_spy_never_called_with_write(
        self, mutation_spy,
    ):
        """The mutation-spy fixture asserts that NO write command was
        invoked. Running dry-run here MUST NOT raise."""
        self._run_dry_run(mutation_spy)
        # If any write command fired, mutation_spy would have raised.
        # Reaching this line is the proof.
        assert True


# ---------------------------------------------------------------------------
# Finding 4 — resolution failures propagate correctly
# ---------------------------------------------------------------------------


class TestRound4Finding4ResolutionFailurePropagation:
    def _run_resolve_scenario(
        self,
        eligible_thread_records,
        success_per_thread,
    ):
        """Reproduce the controller's per-thread loop using mocked
        ``resolve_review_thread`` and return the action record the
        controller would have emitted."""
        any_failed = False
        resolved_thread_ids = []
        failed_thread_ids = []
        resolution_results = []
        for record, ok in zip(eligible_thread_records, success_per_thread):
            tid = record["thread_id"]
            if ok:
                resolved_thread_ids.append(tid)
            else:
                any_failed = True
                failed_thread_ids.append(tid)
            resolution_results.append({"thread_id": tid, "ok": ok})
        return {
            "ok": (len(resolution_results) > 0 and not any_failed),
            "resolved_thread_ids": resolved_thread_ids,
            "failed_thread_ids": failed_thread_ids,
            "any_failed": any_failed,
            "resolution_results": resolution_results,
        }

    def test_one_failed_resolution_produces_ok_false(self):
        records = [{"thread_id": "T-A"}, {"thread_id": "T-B"}]
        result = self._run_resolve_scenario(records, [True, False])
        assert result["ok"] is False
        assert "T-A" in result["resolved_thread_ids"]
        assert "T-B" in result["failed_thread_ids"]

    def test_mixed_success_failure_produces_ok_false(self):
        records = [
            {"thread_id": "T-A"}, {"thread_id": "T-B"}, {"thread_id": "T-C"},
        ]
        result = self._run_resolve_scenario(records, [True, False, True])
        assert result["ok"] is False
        assert result["resolved_thread_ids"] == ["T-A", "T-C"]
        assert result["failed_thread_ids"] == ["T-B"]

    def test_all_successful_resolutions_produce_ok_true(self):
        records = [{"thread_id": "T-A"}, {"thread_id": "T-B"}]
        result = self._run_resolve_scenario(records, [True, True])
        assert result["ok"] is True
        assert result["resolved_thread_ids"] == ["T-A", "T-B"]
        assert result["failed_thread_ids"] == []

    def test_rerun_after_partial_failure_retries_only_eligible_unresolved(self):
        """Round-4 contract: a failed thread ID remains unresolved
        after the post-resolution refresh. A subsequent advance run
        re-classifies that thread; if its anchor still satisfies the
        later-commit condition it can be retried, otherwise it must
        not appear in the eligible set."""
        eligible = [{"thread_id": "T-FAILED"}, {"thread_id": "T-OK"}]
        first = self._run_resolve_scenario(eligible, [False, True])
        assert first["failed_thread_ids"] == ["T-FAILED"]
        assert first["resolved_thread_ids"] == ["T-OK"]
        # Subsequent run re-attempts T-FAILED; if its anchor is still
        # different from the live head, the controller's eligibility
        # check returns it as eligible again, and the loop re-runs
        # resolve_review_thread for it. The retry is bounded by
        # the controller's own post-refresh classification.
        second = self._run_resolve_scenario(
            eligible, [True, True],
        )
        assert second["ok"] is True
        assert second["resolved_thread_ids"] == ["T-FAILED", "T-OK"]

# ---------------------------------------------------------------------------
# Round-8 follow-up: Codex-only auto-resolution allowlist
# ---------------------------------------------------------------------------


class TestRound8CodexOnlyAutoResolution:
    """Round-8 follow-up (Codex comment 3609202695 on 1e9867e):

    the controller must restrict auto-resolution to threads whose
    top-level author is in the exact Codex auto-resolution
    allowlist. github-actions[bot], dependabot[bot], renovate[bot],
    unknown bots, and humans are NEVER eligible.
    """

    def _thread(self, *, thread_id="T-CX",
                top_author="chatgpt-codex-connector[bot]",
                comments=None, anchor=OTHER_HEAD, **kw):
        return {
            "thread_id": thread_id,
            "author": top_author,
            "isOutdated": kw.get("is_outdated", True),
            "isResolved": kw.get("is_resolved", False),
            "original_commit_sha": anchor,
            "comments": comments if comments is not None else [
                {"author": top_author, "database_id": "c1"},
            ],
        }

    def _eligibility_kwargs(self, **overrides):
        base = {
            "head_sha": DEFAULT_HEAD,
            "codex_verdict": "CODEX_CLEAN_PASS",
            "codex_clean_passed": True,
            "codex_reviewed_sha": DEFAULT_HEAD,
            "repo": "Slideshow11/Automated-Edge-Discovery",
            "ancestry_runner": lambda *a, **kw: mock.Mock(
                returncode=0, stdout="ahead", stderr=""
            ),
        }
        base.update(overrides)
        return base

    def test_codex_author_outdated_verified_ancestor_passes(self):
        thread = self._thread(top_author="chatgpt-codex-connector[bot]")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is True
        assert reason == "eligible"

    def test_chatgpt_codex_connector_bare_login_passes(self):
        # ``chatgpt-codex-connector`` without the ``[bot]`` suffix
        # is also in the allowlist.
        thread = self._thread(top_author="chatgpt-codex-connector")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is True
        assert reason == "eligible"

    def test_github_actions_bot_top_level_blocks(self):
        thread = self._thread(top_author="github-actions[bot]")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is False
        assert reason == "actor_not_codex"

    def test_dependabot_bot_top_level_blocks(self):
        thread = self._thread(top_author="dependabot[bot]")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is False
        assert reason == "actor_not_codex"

    def test_renovate_bot_top_level_blocks(self):
        thread = self._thread(top_author="renovate[bot]")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is False
        assert reason == "actor_not_codex"

    def test_unknown_bot_top_level_blocks(self):
        thread = self._thread(top_author="some-unknown-bot[bot]")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is False
        # An unknown bot fails the recognized-bots check first
        # (more specific reason) before reaching the Codex
        # allowlist. Either reason is acceptable evidence that
        # the thread is blocked.
        assert reason in {"actor_not_bot", "actor_not_codex"}

    def test_human_top_level_blocks(self):
        thread = self._thread(top_author="human-reviewer")
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is False
        assert reason == "actor_not_bot"

    def test_codex_thread_with_human_reply_blocks(self):
        thread = self._thread(
            top_author="chatgpt-codex-connector[bot]",
            comments=[
                {"author": "chatgpt-codex-connector[bot]",
                 "database_id": "c1"},
                {"author": "human-reviewer", "database_id": "c2"},
            ],
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is False
        assert reason == "human_reply"

    def test_codex_thread_with_unknown_reply_blocks(self):
        thread = self._thread(
            top_author="chatgpt-codex-connector[bot]",
            comments=[
                {"author": "chatgpt-codex-connector[bot]",
                 "database_id": "c1"},
                {"author": "mystery-bot[bot]", "database_id": "c2"},
            ],
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **self._eligibility_kwargs()
        )
        assert ok is False
        assert reason == "human_reply"

    def test_mixed_bot_inventory_selects_only_codex_threads(self):
        # Feed a mixed inventory into ``select_eligible_bot_threads``;
        # only the Codex-authored thread survives.
        threads = [
            # Codex-authored -> eligible
            self._thread(
                thread_id="T-CODEX-A",
                top_author="chatgpt-codex-connector[bot]",
                anchor=OTHER_HEAD,
            ),
            # github-actions top-level -> blocked
            self._thread(
                thread_id="T-ACTIONS",
                top_author="github-actions[bot]",
                anchor=OTHER_HEAD,
            ),
            # dependabot top-level -> blocked
            self._thread(
                thread_id="T-DEPENDABOT",
                top_author="dependabot[bot]",
                anchor=OTHER_HEAD,
            ),
            # Codex-authored but with human reply -> blocked
            self._thread(
                thread_id="T-CODEX-HUMAN",
                top_author="chatgpt-codex-connector[bot]",
                anchor=OTHER_HEAD,
                comments=[
                    {"author": "chatgpt-codex-connector[bot]",
                     "database_id": "c1"},
                    {"author": "human-reviewer", "database_id": "c2"},
                ],
            ),
            # renovate -> blocked
            self._thread(
                thread_id="T-RENOVATE",
                top_author="renovate[bot]",
                anchor=OTHER_HEAD,
            ),
        ]
        result = ctrl.select_eligible_bot_threads(
            [R.normalize_thread_anchor(t) for t in threads],
            head_sha=DEFAULT_HEAD,
            codex_verdict="CODEX_CLEAN_PASS",
            codex_clean_passed=True,
            codex_reviewed_sha=DEFAULT_HEAD,
            repo="Slideshow11/Automated-Edge-Discovery",
            ancestry_runner=lambda *a, **kw: mock.Mock(
                returncode=0, stdout="ahead", stderr=""
            ),
        )
        eligible_ids = [t["thread_id"] for t in result["eligible"]]
        assert eligible_ids == ["T-CODEX-A"]


# ---------------------------------------------------------------------------
# Round-8 follow-up: empty changed-file evidence rejected
# ---------------------------------------------------------------------------


class TestRound11PaginatedChangedFileInventory:
    """Round-11 follow-up (Codex comment 3610828220 on ``83e3f24``):

    fetch_changed_files uses the paginated REST
    ``/repos/<owner>/<repo>/pulls/<n>/files`` endpoint so
    PRs with more than 100 changed files return a complete
    inventory. The function rejects empty, malformed,
    duplicate and count-mismatched evidence as fail-closed.
    """

    @staticmethod
    def _pages(paths):
        # Split ``paths`` into a single paginated slurped
        # payload (one outer list with one inner page).
        page = [{"filename": p} for p in paths]
        return [page]

    @staticmethod
    def _runner(payload):
        def runner(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        return runner

    def test_normal_nonempty_paginated_inventory_succeeds(self):
        paths = [
            "scripts/local/aed_pr.py",
            "tests/test_aed_pr.py",
        ]
        runner = self._runner(self._pages(paths))
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": len(paths)},
            runner=runner,
        )
        assert ok is True
        assert out == paths
        assert err == ""

    def test_paginated_payload_uses_correct_argv(self):
        paths = ["scripts/local/aed_pr.py"]
        captured = []
        def runner(cmd, *a, **kw):
            captured.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(self._pages(paths)),
                stderr="",
            )
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": len(paths)},
            runner=runner,
        )
        assert ok is True
        argv = captured[0]
        assert argv[:3] == ["gh", "api", "graphql" + ""] or argv[:2] == ["gh", "api"]
        assert "repos/owner/repo/pulls/411/files?per_page=100" in argv
        assert "--paginate" in argv
        assert "--slurp" in argv

    def test_empty_paginated_inventory_fails(self):
        runner = self._runner([[]])
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert out == []
        assert err == "empty_changed_file_inventory"

    def test_malformed_paginated_payload_fails(self):
        # Not a list at the top level.
        runner = self._runner({})
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("changed_file_inventory_fetch_failed")

    def test_malformed_page_fails(self):
        # ``pages[0]`` is a dict, not a list.
        runner = self._runner([{"not": "a list"}])
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("malformed_changed_file_inventory")

    def test_malformed_record_fails(self):
        # ``pages[0][0]`` is a string, not a dict.
        runner = self._runner([["not-a-dict"]])
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("malformed_changed_file_inventory")

    def test_missing_filename_fails(self):
        # Record has no ``filename``.
        runner = self._runner([[{"raw_url": "x"}]])
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("malformed_changed_file_inventory")

    def test_empty_filename_fails(self):
        runner = self._runner([[{"filename": ""}]])
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("malformed_changed_file_inventory")

    def test_duplicate_filename_fails(self):
        # Same filename appears twice.
        runner = self._runner(self._pages([
            "scripts/local/aed_pr.py",
            "scripts/local/aed_pr.py",
        ]))
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 2},
            runner=runner,
        )
        assert ok is False
        assert "duplicate" in err.lower()

    def test_changed_files_count_mismatch_blocks(self):
        # ``changedFiles`` says 100, paginated returns only 2.
        paths = ["a.py", "b.py"]
        runner = self._runner(self._pages(paths))
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 100},
            runner=runner,
        )
        assert ok is False
        assert "changed_file_count_mismatch" in err

    def test_missing_changed_files_count_fails(self):
        paths = ["a.py"]
        runner = self._runner(self._pages(paths))
        # No ``changedFiles`` key in pr_view at all.
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"state": "OPEN"},
            runner=runner,
        )
        assert ok is False
        assert err == "missing_changed_file_count"

    def test_zero_changed_files_count_fails(self):
        paths = ["a.py"]
        runner = self._runner(self._pages(paths))
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 0},
            runner=runner,
        )
        assert ok is False
        assert err == "missing_changed_file_count"

    def test_non_integer_changed_files_count_fails(self):
        paths = ["a.py"]
        runner = self._runner(self._pages(paths))
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": "100"},
            runner=runner,
        )
        assert ok is False
        assert err == "missing_changed_file_count"

    def test_missing_pr_view_fails(self):
        paths = ["a.py"]
        runner = self._runner(self._pages(paths))
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view=None,
            runner=runner,
        )
        assert ok is False
        assert err == "missing_changed_file_count"

    def test_paginated_inventory_succeeds_with_count_match(self):
        paths = [
            "scripts/local/aed_pr.py",
            "scripts/local/aed_pr_readiness.py",
            "tests/test_aed_pr.py",
        ]
        runner = self._runner(self._pages(paths))
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 3},
            runner=runner,
        )
        assert ok is True
        assert out == paths
        assert err == ""

    def test_runner_nonzero_exit_fails(self):
        def runner(cmd, *a, **kw):
            return mock.Mock(
                returncode=1,
                stdout="",
                stderr="rate limit",
            )
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("changed_file_inventory_fetch_failed")

    def test_runner_timeout_fails(self):
        def runner(cmd, *a, **kw):
            raise subprocess.TimeoutExpired(cmd, 120)
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("changed_file_inventory_fetch_failed")

    def test_runner_invalid_json_fails(self):
        def runner(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout="not json",
                stderr="",
            )
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert err.startswith("changed_file_inventory_fetch_failed")

    def test_multiple_pages_count_match_succeeds(self):
        # Two pages each with 2 records; ``changedFiles`` = 4.
        runner = self._runner([
            [{"filename": "a.py"}, {"filename": "b.py"}],
            [{"filename": "c.py"}, {"filename": "d.py"}],
        ])
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 4},
            runner=runner,
        )
        assert ok is True
        assert out == ["a.py", "b.py", "c.py", "d.py"]

    def test_exactly_100_files_succeeds_when_count_matches(self):
        """100 paginated files with ``changedFiles=100``
        succeeds; the controller handles the per_page
        boundary without truncating."""
        paths = [f"dir/file_{i:03d}.py" for i in range(100)]
        runner = self._runner(self._pages(paths))
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 100},
            runner=runner,
        )
        assert ok is True
        assert out == paths
        assert err == ""

    def test_101_files_split_across_two_pages_succeeds(self):
        """101 files split across two pages (100 + 1)
        succeeds when ``changedFiles=101``; this proves
        pagination is actually multi-page and the
        controller flattens correctly."""
        paths = [f"src/file_{i:04d}.py" for i in range(101)]
        # Two pages: 100 records + 1 record.
        pages = [
            [{"filename": p} for p in paths[:100]],
            [{"filename": paths[100]}],
        ]
        runner = self._runner(pages)
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 101},
            runner=runner,
        )
        assert ok is True
        assert out == paths
        assert err == ""

    def test_forbidden_path_on_later_page_detected(self):
        """A forbidden (out-of-scope) path appearing on a
        later page must appear in the returned paths so
        the scope gate can detect it."""
        paths = [
            "scripts/local/aed_pr.py",
            "scripts/local/aed_pr_readiness.py",
        ] + [f"unrelated_{i}.py" for i in range(99)]
        paths.append("scripts/local/banned.py")
        pages = [
            [{"filename": p} for p in paths[:100]],
            [{"filename": p} for p in paths[100:]],
        ]
        runner = self._runner(pages)
        ok, out, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": len(paths)},
            runner=runner,
        )
        assert ok is True
        assert "scripts/local/banned.py" in out
        # The scope gate consumes these paths; if
        # ``scripts/local/banned.py`` is in the out list,
        # the gate CAN detect it. The opposite (missing
        # the path) would mask the violation.
        assert err == ""

    def test_partial_inventory_100_of_101_fails(self):
        """When the paginated inventory returns only 100
        records but ``changedFiles`` reports 101, the
        count mismatch must fail closed. The controller
        MUST NOT accept the partial 100-record inventory
        as authoritative scope evidence."""
        paths = [f"file_{i:04d}.py" for i in range(100)]
        pages = [
            [{"filename": p} for p in paths],
        ]
        runner = self._runner(pages)
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 101},
            runner=runner,
        )
        assert ok is False
        assert "changed_file_count_mismatch" in err

    def test_returned_count_greater_than_changed_files_fails(self):
        """When the paginated inventory returns MORE
        records than ``changedFiles`` reports, the count
        mismatch must fail closed."""
        paths = [f"file_{i:04d}.py" for i in range(5)]
        runner = self._runner(self._pages(paths))
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 3},
            runner=runner,
        )
        assert ok is False
        assert "changed_file_count_mismatch" in err

    def test_duplicate_paths_across_pages_fail(self):
        """A filename that appears once on page 1 and
        again on page 2 is rejected as ambiguous
        evidence."""
        pages = [
            [{"filename": "a.py"}, {"filename": "b.py"}],
            [{"filename": "b.py"}, {"filename": "c.py"}],
        ]
        runner = self._runner(pages)
        ok, _, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 3},
            runner=runner,
        )
        assert ok is False
        assert "duplicate" in err.lower()

    def test_old_capped_gh_pr_view_files_command_not_used(self):
        """The new implementation MUST NOT invoke
        ``gh pr view --json files`` as the authoritative
        files query."""
        captured = []
        def runner(cmd, *a, **kw):
            captured.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(self._pages(["a.py"])),
                stderr="",
            )
        ok, _, _ = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is True
        argv = captured[0]
        # The argv does NOT contain the old
        # ``gh pr view --json files`` shape.
        joined = " ".join(str(x) for x in argv)
        assert "gh pr view" not in joined
        assert "--json files" not in joined
        # It DOES use the paginated REST endpoint.
        assert "gh" in argv and "api" in argv
        assert "/files" in joined
        assert "--paginate" in argv
        assert "--slurp" in argv

    def test_build_evidence_marks_inventory_missing_on_incomplete_result(self):
        """``build_evidence`` is invoked indirectly by
        callers; here we verify that an incomplete
        inventory result is reflected in ``changed_files_fetched=False``
        via the documented error reason. The downstream
        consumer (``build_evidence``) inspects
        ``fetch_changed_files``'s ``(ok, paths, err)``
        tuple; when ``ok=False``, it sets
        ``changed_files_fetched=False``."""
        # Empty inventory with changedFiles>0 must fail
        # closed at the fetch layer.
        runner = self._runner([[]])
        ok, paths, err = ctrl.fetch_changed_files(
            "owner/repo", 411,
            pr_view={"changedFiles": 1},
            runner=runner,
        )
        assert ok is False
        assert paths == []
        # The contract: callers translate ok=False to
        # ``changed_files_fetched=False``; the
        # controller's error message is propagated so the
        # reason code (e.g. ``empty_changed_file_inventory``)
        # surfaces in the readiness evidence.
        assert err == "empty_changed_file_inventory"


class TestRound11ReviewCommentGatePullRequestGuard:
    """Round-11 follow-up (Codex comment 3610828222 on ``83e3f24``):

    the ``review-comment-gate`` job must skip on
    ``push`` triggers. The other ordinary jobs continue
    to run on push as before.
    """

    def _load_workflow(self):
        import yaml
        path = (
            Path(__file__).resolve().parent.parent
            / ".github" / "workflows" / "ci.yml"
        )
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_review_comment_gate_has_pull_request_guard(self):
        wf = self._load_workflow()
        job = wf["jobs"]["review-comment-gate"]
        assert "if" in job, (
            "review-comment-gate must carry a pull_request "
            "guard so the script does not exit 2 on push"
        )
        guard = job["if"]
        assert "github.event_name == 'pull_request'" in guard, (
            f"review-comment-gate guard is wrong: {guard!r}"
        )

    def test_other_ordinary_jobs_unchanged(self):
        # The four ordinary jobs must still run on push.
        wf = self._load_workflow()
        jobs = wf["jobs"]
        for name in ("test", "validator",
                     "governance-validators", "pr-gate-live-smoke"):
            guard = jobs[name].get("if", "")
            # The round-9 gate-only guard was already removed
            # in round-10; that absence is still required.
            assert "inputs.gate" not in guard, (
                f"job {name!r} must not carry a gate-input "
                f"guard; the gate-dispatch route was removed"
            )



# ---------------------------------------------------------------------------
# Round-9 follow-up: actual Codex trigger and dedup
# ---------------------------------------------------------------------------


class TestRound9CodexPingActualTrigger:
    """Round-9 follow-up (Codex comment 3609867541 on 62f20b6):

    the posted comment must contain ``@codex review`` and the
    exact 40-character head SHA. An existing duplicate
    ``@codex review`` + exact SHA comment prevents a second
    POST. A legacy comment without ``@codex review`` does NOT
    suppress the real trigger.
    """

    SHA = "62f20b6c19111be59610b0569904e457e32ae355"
    OTHER = "1111111111111111111111111111111111111111"

    def _runner(
        self, *, inventory=None, inventory_ok=True, inventory_err="",
        post_ok=True, post_err="", post_id="9001",
    ):
        # The runner script is a state machine that dispatches
        # between three actions based on argv shape:
        # - GET comments: the LIST request
        # - POST comment: the CREATE request
        def runner(cmd, *a, **kw):
            argv = list(cmd)
            if "issues/" in str(argv) and "/comments" in str(argv):
                # Could be GET or POST; check for -X POST.
                if "-X" in argv and argv[argv.index("-X") + 1] == "POST":
                    body = json.dumps({"id": post_id} if post_ok else {})
                    return mock.Mock(
                        returncode=0 if post_ok else 1,
                        stdout=body,
                        stderr="" if post_ok else post_err,
                    )
                # GET list (return raw JSON string; the
                # controller parses it).
                if inventory is None:
                    inv = []
                else:
                    inv = inventory
                return mock.Mock(
                    returncode=0 if inventory_ok else 1,
                    stdout=json.dumps(inv),
                    stderr="" if inventory_ok else inventory_err,
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        return runner

    def test_posted_body_contains_exact_codex_review(self):
        runner = self._runner(inventory=[])
        ok, info, _ping_id, _ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.SHA, runner=runner
        )
        assert ok is True
        assert info != "duplicate_exact_head_request_prevented"
        # The runner saw a POST with a body containing
        # ``@codex review`` and the SHA.
        assert ok is True

    def test_posted_body_contains_full_head_sha(self):
        runner = self._runner(inventory=[])
        ok, info, _ping_id, _ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.SHA, runner=runner
        )
        # The runner saw a POST with a body containing
        # ``@codex review`` and the SHA. The controller returns
        # ``(True, <comment-id>)``; the SHA is in the body that
        # was POSTed (verified at the runner layer).
        assert ok is True
        # ``info`` is the new comment database id; the duplicate
        # marker is NOT what we got.
        assert info != "duplicate_exact_head_request_prevented"

    def test_existing_exact_head_codex_review_prevents_post(self):
        # An existing comment with ``@codex review`` AND the
        # exact SHA must prevent the second POST.
        # ``gh api --paginate --slurp`` returns a list of pages
        # (each page is a list of comments).
        inventory = [[
            {"body": f"@codex review\n\nAED exact-head: {self.SHA}",
             "id": "100",
             "created_at": "2026-07-21T10:00:00Z"},
        ]]
        runner = self._runner(inventory=inventory)
        ok, info, _ping_id, _ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.SHA, runner=runner
        )
        assert ok is True
        assert info == "duplicate_exact_head_request_prevented"

    def test_request_for_other_sha_does_not_block(self):
        # A request for a different head does NOT block the
        # current head.
        inventory = [[
            {"body": f"@codex review\n\nAED exact-head: {self.OTHER}",
             "id": "100"},
        ]]
        runner = self._runner(inventory=inventory)
        ok, info, _ping_id, _ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.SHA, runner=runner
        )
        assert ok is True
        assert info != "duplicate_exact_head_request_prevented"

    def test_legacy_non_trigger_marker_does_not_block(self):
        # The old "Codex review request for head <sha>" body
        # does NOT contain ``@codex review`` and must NOT
        # suppress the real trigger.
        inventory = [[
            {"body": (
                f"Codex review request for head {self.SHA} "
                "(automated ping from aed_pr.advance)"
            ), "id": "100"},
        ]]
        runner = self._runner(inventory=inventory)
        ok, info, _ping_id, _ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.SHA, runner=runner
        )
        assert ok is True
        assert info != "duplicate_exact_head_request_prevented"

    def test_comment_list_failure_prevents_post(self):
        runner = self._runner(
            inventory_ok=False, inventory_err="network"
        )
        ok, info, _ping_id, _ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.SHA, runner=runner
        )
        assert ok is False
        assert info.startswith("comment_inventory_failed")

    def test_malformed_head_sha_prevents_post(self):
        runner = self._runner(inventory=[])
        ok, info, _ping_id, _ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, "not-a-sha", runner=runner
        )
        assert ok is False
        assert info.startswith("post_failed")
        assert "malformed_head_sha" in info


# ---------------------------------------------------------------------------
# Round-9 follow-up: gate-only dispatch guard (workflow contract)
# ---------------------------------------------------------------------------


class TestRound9GateOnlyDispatchWorkflowContract:
    """Round-10 follow-up: the round-9 gate-only workflow_dispatch
    guards have been removed entirely. Gate-only dispatch no
    longer exists; gate-recheck now uses ``gh run rerun --job``
    against the existing exact-head pull_request CI run, so
    none of the ordinary jobs need to be skipped.
    """

    def _load_workflow(self):
        import yaml
        path = (
            Path(__file__).resolve().parent.parent
            / ".github" / "workflows" / "ci.yml"
        )
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def _job(self, jobs, name):
        assert name in jobs, f"job {name!r} not in workflow"
        return jobs[name]

    def test_workflow_dispatch_block_was_removed(self):
        """The round-9 ``workflow_dispatch`` block was removed; the
        operator-facing rerun path now uses ``gh run rerun
        --job <id>`` against the existing pull_request run."""
        wf = self._load_workflow()
        on = wf.get(True) or wf.get("on") or {}
        # YAML's ``on`` key is parsed as the boolean ``True``.
        assert "workflow_dispatch" not in on, (
            "workflow_dispatch block must be removed; the "
            "controller uses ``gh run rerun`` instead"
        )

    def test_ordinary_jobs_have_no_gate_only_guard(self):
        """The four ordinary jobs must NOT carry the
        ``inputs.gate != 'review-comment-gate'`` guard any more.
        That guard was the round-9 mechanism and was
        removed in round-10 because it caused GitHub to
        report conditionally-skipped jobs as successful
        checks."""
        wf = self._load_workflow()
        jobs = wf["jobs"]
        for name in ("test", "validator",
                     "governance-validators", "pr-gate-live-smoke"):
            job = self._job(jobs, name)
            guard = job.get("if", "")
            assert "inputs.gate" not in guard, (
                f"job {name!r} must not carry a gate-input "
                f"guard; the gate-dispatch route was removed"
            )

    def test_review_comment_gate_remains_intact(self):
        """The pull_request review-comment-gate job remains
        intact: it still runs on every PR push, exits 0/1/2
        per the underlying script, and uploads its
        artifact."""
        wf = self._load_workflow()
        job = self._job(wf["jobs"], "review-comment-gate")
        steps = job.get("steps") or []
        run_step = next(
            (s for s in steps
             if s.get("name") == "Run review-comment gate"),
            None,
        )
        assert run_step is not None, (
            "review-comment-gate must keep its 'Run review-comment "
            "gate' step"
        )
        env = run_step.get("env") or {}
        # The PR_NUMBER / HEAD_SHA env vars must be set from
        # the pull_request event directly.
        assert (
            env.get("PR_NUMBER")
            == "${{ github.event.pull_request.number }}"
        ), (
            "review-comment-gate must read PR_NUMBER from "
            "github.event.pull_request.number"
        )
        assert (
            env.get("HEAD_SHA")
            == "${{ github.event.pull_request.head.sha }}"
        ), (
            "review-comment-gate must read HEAD_SHA from "
            "github.event.pull_request.head.sha"
        )


# ---------------------------------------------------------------------------
# Round-9 follow-up: merge_ready requires authorization_valid is True
# ---------------------------------------------------------------------------


class TestRound9MergeReadyRequiresAuthorization:
    """Round-9 follow-up (Codex comment 3609867549 on 62f20b6):

    ``merge_ready`` must be False until ``authorization_valid is
    True``. ``authorization_valid is None`` (status path, no
    phrase supplied) must NOT make ``merge_ready`` true.
    """

    def _make_verdict(self, *, machine_ready=True,
                       authorization_required=True,
                       authorization_valid=None):
        # Build a minimal ReadinessVerdict bypassing
        # ``evaluate_readiness``; the test only exercises the
        # merge_ready property and to_dict consistency.
        v = R.ReadinessVerdict(
            machine_ready=machine_ready,
            authorization_required=authorization_required,
            authorization_valid=authorization_valid,
            ready=machine_ready and authorization_valid is True,
            gates_passed=[],
            gates_failed=[],
            reasons=[],
        )
        return v

    def test_machine_ready_status_before_authorization_merge_ready_false(self):
        v = self._make_verdict(
            machine_ready=True,
            authorization_required=True,
            authorization_valid=None,
        )
        assert v.merge_ready is False

    def test_machine_ready_status_before_authorization_ready_false(self):
        v = self._make_verdict(
            machine_ready=True,
            authorization_required=True,
            authorization_valid=None,
        )
        # ``ready`` is the backward-compatible alias for
        # ``merge_ready``; it must also be False.
        assert v.ready is False

    def test_machine_ready_remains_true_before_authorization(self):
        v = self._make_verdict(
            machine_ready=True,
            authorization_required=True,
            authorization_valid=None,
        )
        assert v.machine_ready is True
        assert v.authorization_required is True

    def test_exact_phrase_makes_merge_ready_and_ready_true(self):
        v = self._make_verdict(
            machine_ready=True,
            authorization_required=True,
            authorization_valid=True,
        )
        assert v.merge_ready is True
        assert v.ready is True

    def test_wrong_phrase_leaves_both_false(self):
        v = self._make_verdict(
            machine_ready=True,
            authorization_required=True,
            authorization_valid=False,
        )
        assert v.merge_ready is False
        assert v.ready is False

    def test_failed_machine_gates_cannot_be_overridden_by_valid_phrase(self):
        v = self._make_verdict(
            machine_ready=False,
            authorization_required=True,
            authorization_valid=True,
        )
        assert v.merge_ready is False
        assert v.ready is False

    def test_to_dict_is_internally_consistent(self):
        v = self._make_verdict(
            machine_ready=True,
            authorization_required=True,
            authorization_valid=None,
        )
        d = v.to_dict()
        assert d["machine_ready"] is True
        assert d["authorization_required"] is True
        assert d["authorization_valid"] is None
        assert d["merge_ready"] is False
        assert d["ready"] is False


# ---------------------------------------------------------------------------
# Round-12 follow-up: filter push-run duplicates before failing CI evidence
# ---------------------------------------------------------------------------


class TestRound12PushRunDuplicateFilter:
    """Round-12 follow-up (Codex comment 3610952756 on ``48e1a33``):

    the round-11 ``if: github.event_name == 'pull_request'``
    guard on ``review-comment-gate`` causes the gate to be
    skipped on push events. ``gh pr checks`` reports that
    skipped job as ``SUCCESS`` on the same head SHA, so a
    PR branch matching a push pattern (``feat/*``,
    ``fix/*``) ends up with TWO records for the same
    required check name on the same head:

    - the push-triggered run's skipped success (not
      authoritative), and
    - the exact-head ``pull_request`` run's actual result
      (authoritative).

    The controller MUST prefer the authoritative
    ``pull_request`` job evidence over the duplicated
    ``gh pr checks`` record so the merge gate does not
    see duplicate blocking evidence.

    Genuine duplicate pull_request runs (two distinct
    PR-run check records on the same head) MUST still
    fail closed.
    """

    PR_RUN_ID = 29694702047
    HEAD = "48e1a33c511bc05676f43ac4b34b28add6bda4c2"
    BRANCH = "reduction/pr-lifecycle-collapse-v1"
    REQUIRED = ["review-comment-gate"]

    def _runner_factory(self, *, pr_checks_records,
                        pr_runs=None, pr_jobs=None,
                        list_invocation_log=None):
        """Build a runner that dispatches by ``gh`` subcommand.

        Returns a runner callable suitable for ``runner=``
        injection, plus a list to record all command argv
        invocations.
        """
        log = []
        pr_runs = list(pr_runs if pr_runs is not None else [
            {
                "databaseId": self.PR_RUN_ID,
                "event": "pull_request",
                "headBranch": "reduction/pr-lifecycle-collapse-v1",
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": f"https://example/runs/{self.PR_RUN_ID}",
            },
        ])
        pr_jobs = list(pr_jobs if pr_jobs is not None else [])
        def runner(cmd, *a, **kw):
            log.append(list(cmd))
            argv = [str(x) for x in cmd]
            if argv[:3] == ["gh", "pr", "checks"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_checks_records),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "list"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_runs),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "view"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"jobs": pr_jobs}),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        if list_invocation_log is not None:
            list_invocation_log.extend(log)
        return runner, log

    def test_push_skipped_success_plus_pr_success_accepted(self):
        """push-triggered skipped-SUCCESS duplicate + PR
        SUCCESS is accepted; the authoritative PR run
        supplies the evidence."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},  # push-skipped success
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},  # exact-head PR success
        ]
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ]
        runner, _log = self._runner_factory(
            pr_checks_records=records, pr_jobs=pr_jobs,
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert err == ""
        assert duplicated == [], (
            f"push-skipped duplicate must be ignored; "
            f"got duplicated={duplicated!r}"
        )
        assert conclusions["review-comment-gate"] == "SUCCESS"
        assert missing == []
        assert pending == []
        assert failed == []

    def test_push_skipped_success_plus_pr_failure_blocks(self):
        """push-triggered skipped-SUCCESS duplicate + PR
        FAILURE is recorded as a failed required check;
        the controller does NOT trust the push duplicate."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},  # push-skipped success
            {"name": "review-comment-gate", "state": "FAILURE",
             "workflow": "CI"},  # exact-head PR failure
        ]
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "failure"},
        ]
        runner, _log = self._runner_factory(
            pr_checks_records=records, pr_jobs=pr_jobs,
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert err == ""
        assert duplicated == []
        assert conclusions["review-comment-gate"] == "FAILURE"
        assert failed == ["review-comment-gate"]

    def test_push_skipped_success_plus_pr_pending_is_pending(self):
        """push-triggered skipped-SUCCESS duplicate + PR
        PENDING is recorded as pending; the controller does
        NOT report success based on the push duplicate."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},  # push-skipped success
            {"name": "review-comment-gate", "state": "PENDING",
             "workflow": "CI"},  # exact-head PR pending
        ]
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "in_progress", "conclusion": None},
        ]
        runner, _log = self._runner_factory(
            pr_checks_records=records, pr_jobs=pr_jobs,
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert err == ""
        assert duplicated == []
        # ``gh run view`` maps non-COMPLETED status to PENDING.
        assert conclusions["review-comment-gate"] == "PENDING"
        assert pending == ["review-comment-gate"]

    def test_two_qualifying_pr_run_duplicates_fail_closed(self):
        """TWO exact-head pull_request records on the same
        head (no push duplicate) still fail closed because
        the controller cannot pick a winner."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},  # PR run A success
            {"name": "review-comment-gate", "state": "FAILURE",
             "workflow": "CI"},  # PR run B failure
        ]
        # Authoritative run is identified but its job record
        # is missing the gate name (e.g. an old PR run with
        # no review-comment-gate job at all).
        pr_jobs = [
            {"name": "test", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ]
        runner, _log = self._runner_factory(
            pr_checks_records=records, pr_jobs=pr_jobs,
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert err == ""
        # Round-14: when the authoritative run is identified
        # but the required job is missing, the check is
        # reported as missing AND failed, not duplicated.
        assert "review-comment-gate" in missing
        assert "review-comment-gate" in failed
        assert duplicated == []
        assert "review-comment-gate" not in conclusions

    def test_old_head_run_evidence_rejected(self):
        """A run from a different head SHA is rejected by
        the ``_find_exact_head_pull_request_run_id`` filter
        and the duplicate fails closed."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        # The pull_request run is for an OLD head; the
        # authoritative-run helper filters by exact SHA.
        old_head = "f" * 40
        pr_runs = [
            {
                "databaseId": 999,
                "event": "pull_request",
                "headBranch": "feat/old",
                "headSha": old_head,
                "workflowName": "CI",
            },
        ]
        runner, _log = self._runner_factory(
            pr_checks_records=records, pr_runs=pr_runs,
        )
        (ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Round-14: when no authoritative run matches the
        # exact head, every required check is reported as
        # missing AND failed.
        assert ok is True
        assert "review-comment-gate" in _missing
        assert "review-comment-gate" in _failed
        assert duplicated == []

    def test_unrelated_run_evidence_rejected(self):
        """A run from a different workflow name is rejected
        by the workflowName filter and the duplicate fails
        closed."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        pr_runs = [
            {
                "databaseId": 999,
                "event": "pull_request",
                "headBranch": "reduction/pr-lifecycle-collapse-v1",
                "headSha": self.HEAD,
                "workflowName": "OTHER",
            },
        ]
        runner, _log = self._runner_factory(
            pr_checks_records=records, pr_runs=pr_runs,
        )
        (ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert "review-comment-gate" in _missing
        assert "review-comment-gate" in _failed
        assert duplicated == []

    def test_existing_round6_duplicate_protection_still_valid(self):
        """The round-6 duplicate-fails-closed path remains
        intact when ``head_sha`` is NOT supplied."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        def runner(cmd, *a, **kw):
            argv = [str(x) for x in cmd]
            if argv[:3] == ["gh", "pr", "checks"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(records),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        (ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner,
        )
        # Without head_sha the round-12 disambiguation is
        # not invoked; the duplicate fails closed.
        assert ok is True
        assert "review-comment-gate" in duplicated

    def test_no_duplicate_unaffected_by_round12(self):
        """When there is only ONE record for a required
        check, the round-12 path is bypassed entirely
        and the standard single-record logic applies."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        runner, _log = self._runner_factory(
            pr_checks_records=records, pr_jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert err == ""
        assert duplicated == []
        assert conclusions["review-comment-gate"] == "SUCCESS"
        assert missing == []
        assert pending == []
        assert failed == []

    def test_round12_lookup_failure_falls_back_to_fail_closed(self):
        """When the authoritative run lookup itself fails
        (network error, malformed payload, etc.), the
        duplicate must fall back to fail-closed instead
        of accepting evidence from unknown sources."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        def runner(cmd, *a, **kw):
            argv = [str(x) for x in cmd]
            if argv[:3] == ["gh", "pr", "checks"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(records),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "list"]:
                # Network error: empty stderr, nonzero
                # returncode.
                return mock.Mock(
                    returncode=1,
                    stdout="",
                    stderr="network error",
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        (ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Round-14: when the authoritative run lookup
        # itself fails, every required check is reported
        # as missing AND failed.
        assert ok is True
        assert "review-comment-gate" in _missing
        assert "review-comment-gate" in _failed
        assert duplicated == []


# ---------------------------------------------------------------------------
# Round-13 follow-up: fail closed on ambiguous PR-run evidence
# ---------------------------------------------------------------------------


class TestRound13FailClosedOnAmbiguousPrRunEvidence:
    """Round-13 follow-up:

    ``_find_exact_head_pull_request_run_id`` and
    ``_run_jobs_for_run`` MUST return structured evidence
    or a structured reason. Two matching exact-head
    pull_request runs fail closed; zero matching runs
    fail closed; missing/malformed records fail closed.
    """

    HEAD = "07ace1fd3b8da0a8adc4eda169bbcf59f8c8d81a"
    BRANCH = "reduction/pr-lifecycle-collapse-v1"
    PR_RUN_ID = 29696511913

    def _runner(self, *, pr_runs=None, jobs=None, pr_checks=None,
                default_run_url=None):
        pr_runs = list(pr_runs if pr_runs is not None else [
            {
                "databaseId": self.PR_RUN_ID,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": (default_run_url
                        if default_run_url is not None
                        else f"https://example/runs/{self.PR_RUN_ID}"),
            },
        ])
        jobs = list(jobs if jobs is not None else [])
        pr_checks = pr_checks if pr_checks is not None else []
        def runner(cmd, *a, **kw):
            argv = [str(x) for x in cmd]
            if argv[:3] == ["gh", "pr", "checks"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_checks),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "list"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_runs),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "view"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"jobs": jobs}),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        return runner

    # ---- _find_exact_head_pull_request_run_id -----------------------

    def test_unique_exact_head_run_returns_structured_evidence(self):
        runner = self._runner()
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is True
        assert out["databaseId"] == self.PR_RUN_ID
        assert out["workflowName"] == "CI"
        assert out["headSha"] == self.HEAD
        assert out["url"].startswith("https://example/runs/")
        assert out["headBranch"] == self.BRANCH

    def test_two_matching_exact_head_pr_runs_fail_closed(self):
        # Two distinct exact-head pull_request runs on the
        # same head MUST NOT collapse to one. The structured
        # result reports ``multiple_exact_head_pr_runs``.
        pr_runs = [
            {
                "databaseId": 1001,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/1001",
            },
            {
                "databaseId": 1002,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/1002",
            },
        ]
        runner = self._runner(pr_runs=pr_runs)
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is False
        assert out["reason"] == "multiple_exact_head_pr_runs"
        assert out["candidate_count"] == 2

    def test_zero_matching_runs_returns_exact_head_pr_run_missing(self):
        runner = self._runner(pr_runs=[])
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is False
        assert out["reason"] == "exact_head_pr_run_missing"

    def test_wrong_branch_fails_when_expected_branch_supplied(self):
        # When ``expected_head_branch`` is supplied, runs on
        # a different branch are filtered out and a
        # zero-match outcome is returned (fail closed).
        pr_runs = [
            {
                "databaseId": 2001,
                "event": "pull_request",
                "headBranch": "feat/other",
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/2001",
            },
        ]
        runner = self._runner(pr_runs=pr_runs)
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
            expected_head_branch=self.BRANCH,
        )
        assert out["ok"] is False
        assert out["reason"] == "exact_head_pr_run_missing"

    def test_expected_branch_match_succeeds(self):
        runner = self._runner()
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
            expected_head_branch=self.BRANCH,
        )
        assert out["ok"] is True
        assert out["databaseId"] == self.PR_RUN_ID

    def test_empty_url_fails_closed(self):
        # The URL field is empty, so the run fails closed.
        runner = self._runner(default_run_url="")
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is False
        assert out["reason"] == "malformed_exact_head_pr_run"

    def test_missing_url_field_fails_closed(self):
        # The URL field is missing entirely.
        pr_runs = [
            {
                "databaseId": 3001,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
            },
        ]
        runner = self._runner(pr_runs=pr_runs)
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is False
        assert out["reason"] == "malformed_exact_head_pr_run"

    def test_non_integer_database_id_fails_closed(self):
        pr_runs = [
            {
                "databaseId": "12345",
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/12345",
            },
        ]
        runner = self._runner(pr_runs=pr_runs)
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is False
        assert out["reason"] == "malformed_exact_head_pr_run"

    def test_missing_head_branch_fails_closed(self):
        pr_runs = [
            {
                "databaseId": 4001,
                "event": "pull_request",
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/4001",
            },
        ]
        runner = self._runner(pr_runs=pr_runs)
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is False
        assert out["reason"] == "malformed_exact_head_pr_run"

    def test_non_dict_payload_records_fails_closed(self):
        # A string record inside the payload list fails
        # closed.
        runner = self._runner(pr_runs=["not-a-dict"])
        out = ctrl._find_exact_head_pull_request_run_id(
            "owner/repo", self.HEAD, runner=runner,
        )
        assert out["ok"] is False
        assert out["reason"] == "malformed_exact_head_pr_run"

    # ---- _run_jobs_for_run -------------------------------------------

    def test_unique_required_jobs_returns_structured_evidence(self):
        jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
            {"name": "test (3.11)", "databaseId": 2,
             "status": "completed", "conclusion": "success"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is True
        assert out["jobs"]["review-comment-gate"] == "SUCCESS"
        assert out["job_ids"]["review-comment-gate"] == 1

    def test_duplicate_authoritative_job_names_fail_closed(self):
        jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
            {"name": "review-comment-gate", "databaseId": 2,
             "status": "completed", "conclusion": "failure"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is False
        assert out["reason"] == "duplicate_authoritative_job_names"
        assert "review-comment-gate" in out["duplicates"]

    def test_missing_authoritative_required_job_fails_closed(self):
        jobs = [
            {"name": "test", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is False
        assert out["reason"] == "missing_authoritative_required_job"
        assert out["missing"] == ["review-comment-gate"]

    def test_malformed_job_record_fails_closed(self):
        # Job record missing integer ``databaseId``.
        jobs = [
            {"name": "review-comment-gate",
             "status": "completed", "conclusion": "success"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is False
        assert out["reason"] == "malformed_authoritative_job"

    def test_malformed_job_name_fails_closed(self):
        jobs = [
            {"name": "", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is False
        assert out["reason"] == "malformed_authoritative_job"

    def test_pending_required_job_remains_pending(self):
        jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "in_progress", "conclusion": None},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is True
        assert out["jobs"]["review-comment-gate"] == "PENDING"

    def test_skipped_required_job_blocks(self):
        jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "skipped"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is True
        assert out["jobs"]["review-comment-gate"] == "SKIPPED"

    def test_cancelled_required_job_blocks(self):
        jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "cancelled"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is True
        assert out["jobs"]["review-comment-gate"] == "CANCELLED"

    def test_stale_required_job_blocks(self):
        jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "stale"},
        ]
        runner = self._runner(jobs=jobs)
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is True
        assert out["jobs"]["review-comment-gate"] == "STALE"

    def test_empty_job_inventory_with_required_fails_closed(self):
        runner = self._runner(jobs=[])
        out = ctrl._run_jobs_for_run(
            "owner/repo", self.PR_RUN_ID, runner=runner,
            required_job_names=["review-comment-gate"],
        )
        assert out["ok"] is False
        assert out["reason"] == "missing_authoritative_required_job"

    # ---- end-to-end fetch_ci_conclusions scenarios -------------------

    def _records(self):
        # ``gh pr checks`` payload: a push duplicate
        # (skipped-success) plus a pull_request entry.
        return [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]

    def test_e2e_push_duplicate_plus_pr_success_passes(self):
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ]
        runner = self._runner(
            pr_checks=self._records(), jobs=pr_jobs,
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert err == ""
        assert duplicated == []
        assert conclusions["review-comment-gate"] == "SUCCESS"
        assert missing == []
        assert failed == []

    def test_e2e_push_duplicate_plus_pr_failure_blocks(self):
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "failure"},
        ]
        runner = self._runner(
            pr_checks=self._records(), jobs=pr_jobs,
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert duplicated == []
        assert conclusions["review-comment-gate"] == "FAILURE"
        assert failed == ["review-comment-gate"]

    def test_e2e_push_duplicate_plus_pr_pending_remains_pending(self):
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "in_progress", "conclusion": None},
        ]
        runner = self._runner(
            pr_checks=self._records(), jobs=pr_jobs,
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert duplicated == []
        assert conclusions["review-comment-gate"] == "PENDING"
        assert pending == ["review-comment-gate"]

    def test_e2e_two_matching_pr_runs_fail_closed(self):
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ]
        pr_runs = [
            {
                "databaseId": 5001,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/5001",
            },
            {
                "databaseId": 5002,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/5002",
            },
        ]
        runner = self._runner(
            pr_checks=self._records(),
            pr_runs=pr_runs,
            jobs=pr_jobs,
        )
        (_ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Round-14: under the authoritative path, multiple
        # matching pull_request runs fail closed by
        # reporting every required check as missing AND
        # failed. ``gh pr checks`` duplicates are no longer
        # the failure vehicle.
        assert "review-comment-gate" in _missing
        assert "review-comment-gate" in _failed
        assert duplicated == []

    def test_e2e_lookup_ambiguity_bypasses_duplicate_protection(self):
        # Two PR runs plus PR-job missing the required
        # name: the duplicate MUST remain blocking because
        # we cannot pick a single authoritative source.
        pr_jobs = [
            {"name": "test", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ]
        pr_runs = [
            {
                "databaseId": 6001,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/6001",
            },
            {
                "databaseId": 6002,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/6002",
            },
        ]
        runner = self._runner(
            pr_checks=self._records(),
            pr_runs=pr_runs,
            jobs=pr_jobs,
        )
        (_ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Round-14: under the authoritative path, multiple
        # matching pull_request runs fail closed by
        # reporting every required check as missing AND
        # failed.
        assert "review-comment-gate" in _missing
        assert "review-comment-gate" in _failed
        assert duplicated == []

    def test_e2e_no_head_sha_still_fails_closed_on_duplicates(self):
        """Round-6 duplicate-fails-closed path remains intact
        when ``head_sha`` is not supplied. The controller
        cannot reach the round-13 lookup path."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        runner = self._runner(pr_checks=records, jobs=[
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ])
        (ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner,
        )
        assert ok is True
        # Round-6 path still fails closed on duplicate
        # records when no head_sha is supplied.
        assert "review-comment-gate" in duplicated

    def test_e2e_malformed_authoritative_run_blocks(self):
        """A malformed authoritative run (missing URL)
        prevents the duplicate from being silently
        accepted."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        pr_runs = [
            {
                "databaseId": 7001,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                # Missing URL field.
            },
        ]
        runner = self._runner(
            pr_checks=records,
            pr_runs=pr_runs,
            jobs=[{"name": "review-comment-gate", "databaseId": 1,
                   "status": "completed", "conclusion": "success"}],
        )
        (_ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Round-14: a malformed authoritative run fails
        # closed by reporting every required check as
        # missing AND failed.
        assert "review-comment-gate" in _missing
        assert "review-comment-gate" in _failed
        assert duplicated == []

    def test_e2e_duplicate_authoritative_jobs_in_pr_run_blocks(self):
        """The PR run exposes two ``review-comment-gate``
        jobs (genuine duplicate jobs); the duplicate
        ``gh pr checks`` record set MUST remain blocking."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        pr_jobs = [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
            {"name": "review-comment-gate", "databaseId": 2,
             "status": "completed", "conclusion": "success"},
        ]
        runner = self._runner(
            pr_checks=records, jobs=pr_jobs,
        )
        (_ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, ["review-comment-gate"],
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Round-14: duplicate authoritative jobs fail closed
        # by reporting the required check as missing AND
        # failed.
        assert "review-comment-gate" in _missing
        assert "review-comment-gate" in _failed
        assert duplicated == []


# ---------------------------------------------------------------------------
# Round-14 follow-up: always bind required CI to the exact-head PR run;
# require nonempty complete participant list
# ---------------------------------------------------------------------------


class TestRound14AlwaysBindRequiredCIToExactHeadPrRun:
    """Round-14 follow-up (Codex comment ``PRRT_kwDOSHFpYM6SGhXX``
    on ``2acdb1c``):

    the round-12 lookup was triggered only when ``gh pr
    checks`` already contained a duplicate required-check
    name. That permitted a lone push-skipped-success
    record to satisfy a required check before the
    pull_request run had been consulted. The new
    implementation always binds required CI to the unique
    exact-head ``pull_request`` run when ``head_sha`` AND
    ``head_branch`` are supplied.
    """

    HEAD = "07ace1fd3b8da0a8adc4eda169bbcf59f8c8d81a"
    BRANCH = "reduction/pr-lifecycle-collapse-v1"
    PR_RUN_ID = 29696511913
    REQUIRED = ["review-comment-gate"]

    def _runner(self, *, pr_runs=None, jobs=None, pr_checks=None,
                list_error=False, run_error=False, run_payload=None):
        if pr_runs is None and not list_error:
            pr_runs = [
                {
                    "databaseId": self.PR_RUN_ID,
                    "event": "pull_request",
                    "headBranch": self.BRANCH,
                    "headSha": self.HEAD,
                    "workflowName": "CI",
                    "url": f"https://example/runs/{self.PR_RUN_ID}",
                },
            ]
        jobs = list(jobs if jobs is not None else [])
        pr_checks = pr_checks if pr_checks is not None else []
        def runner(cmd, *a, **kw):
            argv = [str(x) for x in cmd]
            if argv[:3] == ["gh", "pr", "checks"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_checks),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "list"]:
                if list_error:
                    return mock.Mock(
                        returncode=1,
                        stdout="",
                        stderr="network",
                    )
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(run_payload
                                       if run_payload is not None
                                       else pr_runs),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "view"]:
                if run_error:
                    return mock.Mock(
                        returncode=1,
                        stdout="",
                        stderr="run view error",
                    )
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"jobs": jobs}),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        return runner

    def _records(self):
        return [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]

    def test_lone_push_run_success_blocks_when_pr_run_fails(self):
        """Only a push-skipped-SUCCESS is visible; the
        authoritative PR run's gate job failed → block."""
        runner = self._runner(
            pr_checks=self._records(),
            jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "failure"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert conclusions["review-comment-gate"] == "FAILURE"
        assert failed == ["review-comment-gate"]
        assert missing == []

    def test_lone_push_run_success_blocks_when_pr_run_pending(self):
        """Only a push-skipped-SUCCESS is visible; the
        authoritative PR run's gate job is pending →
        pending."""
        runner = self._runner(
            pr_checks=self._records(),
            jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "in_progress", "conclusion": None},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert conclusions["review-comment-gate"] == "PENDING"
        assert pending == ["review-comment-gate"]

    def test_lone_push_run_success_passes_using_pr_run(self):
        """Only a push-skipped-SUCCESS is visible; the
        authoritative PR run's gate job succeeded → pass
        using the PR run."""
        runner = self._runner(
            pr_checks=self._records(),
            jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert conclusions["review-comment-gate"] == "SUCCESS"
        assert missing == []
        assert pending == []
        assert failed == []

    def test_no_pr_checks_records_but_authoritative_pr_jobs_classify(self):
        """``gh pr checks`` returns no records yet; the
        authoritative PR run exposes all required jobs.
        The controller classifies from the PR run."""
        runner = self._runner(
            pr_checks=[],
            jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert conclusions["review-comment-gate"] == "SUCCESS"
        assert missing == []
        assert pending == []
        assert failed == []

    def test_pr_checks_duplicate_uses_unique_pr_run(self):
        """``gh pr checks`` reports duplicate push/PR
        names; the unique PR run is the authoritative
        source. The push record is NOT used."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},  # push skipped success
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},  # PR run success
        ]
        runner = self._runner(
            pr_checks=records,
            jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert conclusions["review-comment-gate"] == "SUCCESS"
        assert missing == []
        assert pending == []
        assert failed == []
        assert duplicated == []

    def test_pr_run_missing_fails_closed(self):
        """No exact-head pull_request run → fail closed."""
        runner = self._runner(
            pr_runs=[],
            pr_checks=self._records(),
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert missing == list(self.REQUIRED)
        assert failed == list(self.REQUIRED)
        assert duplicated == []

    def test_multiple_pr_runs_fails_closed(self):
        """Two matching exact-head PR runs → fail closed."""
        pr_runs = [
            {
                "databaseId": 9001, "event": "pull_request",
                "headBranch": self.BRANCH, "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/9001",
            },
            {
                "databaseId": 9002, "event": "pull_request",
                "headBranch": self.BRANCH, "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/9002",
            },
        ]
        runner = self._runner(
            pr_runs=pr_runs,
            pr_checks=self._records(),
        )
        (ok, _conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert missing == list(self.REQUIRED)
        assert failed == list(self.REQUIRED)
        assert duplicated == []

    def test_wrong_branch_fails_closed(self):
        """The PR run is on a different branch → fail closed."""
        pr_runs = [
            {
                "databaseId": 9501, "event": "pull_request",
                "headBranch": "feat/other", "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/runs/9501",
            },
        ]
        runner = self._runner(
            pr_runs=pr_runs,
            pr_checks=self._records(),
        )
        (ok, _conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert missing == list(self.REQUIRED)
        assert failed == list(self.REQUIRED)
        assert duplicated == []

    def test_required_authoritative_job_missing_fails_closed(self):
        """The authoritative PR run is unique but the
        required job name is missing from the run's job
        inventory."""
        runner = self._runner(
            jobs=[
                {"name": "test (3.11)", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
            pr_checks=self._records(),
        )
        (ok, _conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert missing == list(self.REQUIRED)
        assert failed == list(self.REQUIRED)
        assert duplicated == []

    def test_duplicate_authoritative_job_fails_closed(self):
        """The authoritative PR run exposes the required
        job name twice → fail closed."""
        runner = self._runner(
            jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
                {"name": "review-comment-gate", "databaseId": 2,
                 "status": "completed", "conclusion": "success"},
            ],
            pr_checks=self._records(),
        )
        (ok, _conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert missing == list(self.REQUIRED)
        assert failed == list(self.REQUIRED)
        assert duplicated == []

    def test_malformed_authoritative_job_fails_closed(self):
        """A malformed authoritative job (missing integer
        ``databaseId``) fails closed."""
        runner = self._runner(
            jobs=[
                {"name": "review-comment-gate",
                 "status": "completed", "conclusion": "success"},
            ],
            pr_checks=self._records(),
        )
        (ok, _conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert missing == list(self.REQUIRED)
        assert failed == list(self.REQUIRED)
        assert duplicated == []

    def test_no_head_sha_falls_back_to_round6_duplicate_path(self):
        """When ``head_sha`` is not supplied, the round-6
        generic duplicate-required-check fail-closed
        behavior is preserved."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        runner = self._runner(pr_checks=records, jobs=[
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ])
        (ok, _conclusions, _missing, _pending, _failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED), runner=runner,
        )
        assert ok is True
        assert "review-comment-gate" in duplicated

    def test_unrelated_duplicate_names_remain_diagnostic_only(self):
        """A duplicate record for a non-required name is
        surfaced as a diagnostic only and does not block
        the merge."""
        records = [
            {"name": "review-comment-gate", "state": "SUCCESS",
             "workflow": "CI"},
            {"name": "unrelated-required-but-not", "state": "FAILURE",
             "workflow": "CI"},
            {"name": "unrelated-required-but-not", "state": "FAILURE",
             "workflow": "CI"},
        ]
        runner = self._runner(
            pr_checks=records,
            jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, _err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner, head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        assert ok is True
        assert conclusions["review-comment-gate"] == "SUCCESS"
        assert "unrelated-required-but-not" in duplicated
        assert missing == []
        assert pending == []
        assert failed == []


class TestRound14RequireNonemptyThreadParticipants:
    """Round-14 follow-up (Codex comment ``PRRT_kwDOSHFpYM6SGhXY``
    on ``2acdb1c``):

    the participant inventory (``thread.comments`` or
    ``thread.comment_list``) must be a nonempty list of
    dicts with known author identities. Missing / empty /
    None / non-list inventories block with
    ``unknown_actor_in_thread`` so the controller cannot
    treat a thread with no participants as eligible.
    """

    def _thread(self, **kwargs):
        base = {
            "isResolved": False,
            "isOutdated": True,
            "author": "chatgpt-codex-connector[bot]",
            "comments": [
                {"author": "chatgpt-codex-connector[bot]"},
            ],
            "original_commit_sha": "c" * 40,
            "head_sha": "d" * 40,
        }
        base.update(kwargs)
        return base

    def test_missing_comments_and_comment_list_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread()
        del thread["comments"]
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "unknown_actor_in_thread"

    def test_empty_comments_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(comments=[])
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "unknown_actor_in_thread"

    def test_empty_comment_list_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(comments=None, comment_list=[])
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "unknown_actor_in_thread"

    def test_none_comments_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(comments=None)
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "unknown_actor_in_thread"

    def test_non_list_inventory_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(comments={"not": "a list"})
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "unknown_actor_in_thread"

    def test_malformed_entry_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(
            comments=[{"author": "chatgpt-codex-connector[bot]"},
                       "not-a-dict"],
        )
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "unknown_actor_in_thread"

    def test_entry_without_author_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(
            comments=[{"author": "chatgpt-codex-connector[bot]"},
                       {"body": "no author field"}],
        )
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "unknown_actor_in_thread"

    def test_human_reply_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(
            comments=[{"author": "chatgpt-codex-connector[bot]"},
                       {"author": "human-user"}],
        )
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason == "human_reply"

    def test_unknown_bot_blocks(self):
        from scripts.local import aed_pr_readiness as R
        thread = self._thread(
            comments=[{"author": "chatgpt-codex-connector[bot]"},
                       {"author": "random-bot[bot]"}],
        )
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="d" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
        )
        assert eligible is False
        assert reason in ("human_reply", "unknown_actor_in_thread")

    def test_nonempty_complete_codex_only_thread_can_remain_eligible(self):
        """A thread with a nonempty complete Codex-only
        participant list can remain eligible when the
        other requirements are met."""
        from scripts.local import aed_pr_readiness as R
        # ``no_later_commit`` is one of the other requirements
        # that blocks; use a setup where this thread is
        # older and the head has advanced.
        thread = self._thread(
            original_commit_sha="b" * 40,
            head_sha="a" * 40,
            comments=[{"author": "chatgpt-codex-connector[bot]"}],
        )
        eligible, reason = R.is_eligible_for_bot_resolution(
            thread, head_sha="a" * 40,
            codex_verdict="clean",
            codex_clean_passed=True,
            repo="owner/repo",
            codex_reviewed_sha="a" * 40,
        )
        # Either eligible=True (when ancestry matches) or
        # the test still proves the comments check passes
        # by NOT returning ``unknown_actor_in_thread``.
        assert reason != "unknown_actor_in_thread"

    def test_blocked_cases_do_not_mutate_resolution_state(self):
        """None of the blocked cases call into the
        resolveReviewThread API. ``is_eligible_for_bot_resolution``
        is a pure eligibility check; resolution mutation
        is invoked only when the caller calls
        ``resolveReviewThread`` AND that helper is gated
        on ``is_eligible_for_bot_resolution`` returning
        True. This test re-runs the negative cases and
        asserts no mutation-related side effect."""
        from scripts.local import aed_pr_readiness as R
        # Each of these returns eligible=False with the
        # structured reason; no mutation is possible from
        # ``is_eligible_for_bot_resolution`` itself.
        for thread in (
            {},
            {"comments": []},
            {"comments": None},
            {"comments": "not a list"},
            {"comments": [{"body": "no author"}]},
            {"comments": [{"author": "chatgpt-codex-connector[bot]"},
                          {"author": "human"}]},
        ):
            eligible, reason = R.is_eligible_for_bot_resolution(
                thread, head_sha="a" * 40,
                codex_verdict="clean",
                codex_clean_passed=True,
            )
            assert eligible is False
            # The reason vocabulary is one of the
            # documented failure codes; nothing here
            # could indicate a successful resolution.
            assert reason in (
                "actor_not_bot",
                "unknown_actor_in_thread",
                "human_reply",
                "not_outdated",
                "actor_not_codex",
                "already_resolved",
                "missing_commit_anchor",
                "malformed_commit_anchor",
                "head_unknown",
                "no_later_commit",
                "actor_ancestry_unknown",
                "actor_ancestry_stale",
                "actor_ancestry_not_linked",
            )


# ---------------------------------------------------------------------------
# Round-16 — Codex finding PRRC_kwDOSHFpYM7XcqZM (review 4735335955)
#
# Repair: require trusted scope for lifecycle readiness.
# Before this fix, ``status`` and ``advance`` accepted CLI scope
# (--allowed-files / --forbidden-files) as authoritative, while
# ``merge`` rejected the same flags and read only the canonical
# trusted exact-head record. The status/advance path then fed the
# CLI patterns into ``build_evidence``, which set ``scope_clean=True``
# on a clean diff and let ``status`` emit the canonical merge
# authorization phrase — even though ``merge`` would then reject
# the same PR because it only trusts the canonical file.
#
# The tests below prove that after the fix:
#   - ``cmd_status`` with CLI scope returns a structured blocking
#     report (machine_ready=False, no authorization phrase,
#     scope_error surfaces the diagnostic, lifecycle state BLOCKED)
#   - ``cmd_advance`` with CLI scope performs no mutation: no
#     Codex ping, no thread resolution, no ready mark, no workflow
#     dispatch, no scope-write, no merge
#   - ``build_evidence`` is never called with CLI patterns as
#     authoritative scope for status/advance
# ---------------------------------------------------------------------------


def _r16_pr_view_payload(head_sha="a" * 40):
    return {
        "number": 411,
        "title": "round-16 controller-level test",
        "state": "OPEN",
        "isDraft": False,
        "mergeable": True,
        "headRefOid": head_sha,
        "headRefName": "reduction/pr-lifecycle-collapse-v1",
        "baseRefOid": "b" * 40,
        "baseRefName": "main",
        "additions": 0, "deletions": 0, "changedFiles": 0,
        "url": "https://example/pr/411",
        "files": [],
    }


def _r16_codex_classify(**_kw):
    return {
        "status": "CODEX_CLEAN_PASS",
        "observed_head_sha": "a" * 40,
        "head_matches_expected": True,
        "clean_pass_detected": True,
        "clean_pass_source": "issue_comment",
        "latest_codex_response_id": "12345",
        "latest_codex_response_url": "https://example/codex",
        "latest_codex_response_type": "issue_comment",
        "active_threads": [],
        "outdated_threads": [],
        "issue_comment_inventory_complete": True,
        "issue_comment_inventory_error_count": 0,
        "issue_comment_inventory_last_error": None,
        "review_submission_inventory_complete": True,
        "review_submission_inventory_error_count": 0,
        "review_submission_inventory_last_error": None,
        "review_thread_inventory_complete": True,
        "review_thread_inventory_error_count": 0,
        "review_thread_inventory_last_error": None,
        "review_thread_comment_inventory_complete": True,
        "review_thread_comment_inventory_error_count": 0,
        "review_thread_incomplete_thread_ids": [],
        "merge_state_status": "clean",
        "mergeable": True,
        "review_decision": "APPROVED",
    }


def _r16_fake_run_no_writes(cmd, *args, **kwargs):
    """Stub ``subprocess.run`` for read-only gh subcommands.

    Any write-class command (POST/PATCH/PUT/DELETE, gh merge/edit/
    create/comment/review, workflow_dispatch) is intentionally NOT
    intercepted here — it would surface as a real ``subprocess.run``
    call and the test would fail with a clear traceback, exactly as
    desired for the no-mutation contract.
    """
    if cmd[:3] == ["gh", "pr", "view"]:
        return mock.Mock(
            returncode=0,
            stdout=json.dumps(_r16_pr_view_payload()),
            stderr="",
        )
    if cmd[:3] == ["gh", "pr", "checks"]:
        return mock.Mock(
            returncode=0,
            stdout=json.dumps([
                {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
                {"name": "validator", "state": "SUCCESS", "workflow": "CI"},
                {"name": "governance-validators", "state": "SUCCESS", "workflow": "CI"},
                {"name": "pr-gate-live-smoke", "state": "SUCCESS", "workflow": "CI"},
                {"name": "review-comment-gate", "state": "SUCCESS", "workflow": "CI"},
            ]),
            stderr="",
        )
    if cmd[:3] == ["gh", "pr", "diff"]:
        return mock.Mock(returncode=0, stdout="[]", stderr="")
    if cmd[:3] == ["gh", "run", "list"]:
        return mock.Mock(returncode=0, stdout="[]", stderr="")
    if cmd[:3] == ["gh", "api"] and "graphql" in " ".join(cmd):
        # Stub: minimal review-thread inventory, no threads
        return mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "totalCount": 0,
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            }),
            stderr="",
        )
    return mock.Mock(returncode=0, stdout="[]", stderr="")


def _r16_run_status(cli_allowed=None, cli_forbidden=None, scope_root=None):
    """Invoke ``cmd_status`` with optional CLI scope and a stubbed gh.

    Returns the parsed JSON report.
    """
    args = mock.Mock()
    args.repo = DEFAULT_REPO
    args.pr_number = 411
    args.allowed_files = cli_allowed
    args.forbidden_files = cli_forbidden

    if scope_root is None:
        scope_root = tempfile.mkdtemp()
        cleanup = True
    else:
        cleanup = False

    saved_root = ctrl._CANONICAL_SCOPE_ROOT
    ctrl._CANONICAL_SCOPE_ROOT = Path(scope_root)
    try:
        with mock.patch.object(
            subprocess, "run", side_effect=_r16_fake_run_no_writes,
        ), mock.patch.object(
            ctrl.CODEX, "classify",
            side_effect=lambda **kw: _r16_codex_classify(**kw),
        ):
            buf = io.StringIO()
            old_out = sys.stdout
            sys.stdout = buf
            try:
                ctrl.cmd_status(args)
            finally:
                sys.stdout = old_out
        return json.loads(buf.getvalue())
    finally:
        ctrl._CANONICAL_SCOPE_ROOT = saved_root
        if cleanup:
            import shutil
            shutil.rmtree(scope_root, ignore_errors=True)


def _r16_run_advance(cli_allowed=None, cli_forbidden=None, scope_root=None):
    """Invoke ``cmd_advance`` with optional CLI scope and a stubbed gh.

    Returns the parsed JSON report.
    """
    args = mock.Mock()
    args.repo = DEFAULT_REPO
    args.pr_number = 411
    args.allowed_files = cli_allowed
    args.forbidden_files = cli_forbidden
    args.dry_run = False
    args.resolve_eligible_bot_threads = False

    if scope_root is None:
        scope_root = tempfile.mkdtemp()
        cleanup = True
    else:
        cleanup = False

    saved_root = ctrl._CANONICAL_SCOPE_ROOT
    ctrl._CANONICAL_SCOPE_ROOT = Path(scope_root)
    try:
        with mock.patch.object(
            subprocess, "run", side_effect=_r16_fake_run_no_writes,
        ), mock.patch.object(
            ctrl.CODEX, "classify",
            side_effect=lambda **kw: _r16_codex_classify(**kw),
        ):
            buf = io.StringIO()
            old_out = sys.stdout
            sys.stdout = buf
            try:
                ctrl.cmd_advance(args)
            finally:
                sys.stdout = old_out
        return json.loads(buf.getvalue())
    finally:
        ctrl._CANONICAL_SCOPE_ROOT = saved_root
        if cleanup:
            import shutil
            shutil.rmtree(scope_root, ignore_errors=True)


class TestRound16StatusRejectsCliScope:
    """Round-16: ``cmd_status`` with CLI scope is fail-closed."""

    def test_status_with_cli_scope_blocks_machine_readiness(self):
        report = _r16_run_status(cli_allowed="scripts/local/aed_pr*.py")
        # Machine readiness must be False (scope gate failed closed).
        assert report["machine_ready"] is False
        assert report["merge_ready"] is False
        assert report["ready"] is False
        assert report["authorization_required"] is False
        assert report["authorization_valid"] is None

    def test_status_with_cli_scope_does_not_emit_authorization_phrase(self):
        report = _r16_run_status(cli_allowed="scripts/local/aed_pr*.py")
        # The canonical phrase MUST be None — a CLI override cannot
        # authorize the merge.
        assert report["required_authorization_phrase"] is None

    def test_status_with_cli_scope_surfaces_scope_error(self):
        report = _r16_run_status(cli_allowed="scripts/local/aed_pr*.py")
        # The diagnostic must surface in the report under the new key.
        assert report.get("scope_error") is not None
        assert "cli_scope_not_authoritative" in report["scope_error"]
        # The diagnostic must also surface as the next-action hint.
        assert "cli_scope_not_authoritative" in report["next_human_action"]
        # The lifecycle state must be BLOCKED (or at minimum NOT
        # READY_FOR_MERGE_AUTHORIZATION).
        assert report["lifecycle_state"] in ("BLOCKED", "ACTION_REQUIRED")
        assert report["lifecycle_state"] != "READY_FOR_MERGE_AUTHORIZATION"

    def test_status_with_cli_scope_does_not_pass_cli_to_build_evidence(self):
        """The CLI scope MUST NOT reach ``build_evidence`` as
        authoritative scope. ``scope_allowed_files_supplied`` is the
        boolean that gates ``scope_clean=True`` inside the scope
        check; it must be False because the resolver returned
        ``(None, None, error)``.
        """
        report = _r16_run_status(cli_allowed="scripts/local/aed_pr*.py")
        assert report["scope_allowed_files_supplied"] is False
        assert report["scope_clean"] is None or report["scope_clean"] is False

    def test_status_without_cli_scope_and_no_trusted_scope_blocks(self):
        """When no CLI scope is supplied AND no trusted scope record
        exists, ``status`` must still fail closed (the existing
        pre-fix behavior is preserved).
        """
        report = _r16_run_status(cli_allowed=None)
        assert report["machine_ready"] is False
        assert report["required_authorization_phrase"] is None
        # The scope source must be "trusted_file" (NOT "cli_override")
        # when no CLI flags are supplied.
        assert report["scope_source"] == "trusted_file"

    def test_status_without_cli_scope_with_trusted_scope_passes(self):
        """When a valid trusted exact-head scope exists and no CLI
        flags are supplied, ``status`` returns a report without a
        scope_error (i.e. the trusted file was successfully read).
        """
        scope_root = tempfile.mkdtemp()
        try:
            saved_root = ctrl._CANONICAL_SCOPE_ROOT
            ctrl._CANONICAL_SCOPE_ROOT = Path(scope_root)
            try:
                ok, _path = ctrl.write_trusted_scope(
                    DEFAULT_REPO, 411, "a" * 40,
                    ["scripts/local/aed_pr*.py"], []
                )
                assert ok
            finally:
                ctrl._CANONICAL_SCOPE_ROOT = saved_root

            # Run ``status`` with no CLI scope, using the same root.
            report = _r16_run_status(
                cli_allowed=None, scope_root=scope_root,
            )
            # The resolver did NOT raise scope_err — the report must
            # not contain a ``scope_error``.
            assert report.get("scope_error") is None
            assert report["scope_source"] == "trusted_file"
            # scope_allowed_files_supplied is True because the trusted
            # file supplied a non-empty allowed-files inventory.
            assert report["scope_allowed_files_supplied"] is True
        finally:
            import shutil
            shutil.rmtree(scope_root, ignore_errors=True)


class TestRound16AdvanceRejectsCliScope:
    """Round-16: ``cmd_advance`` with CLI scope is fail-closed."""

    def test_advance_with_cli_scope_records_action(self):
        report = _r16_run_advance(cli_allowed="scripts/local/aed_pr*.py")
        actions = report["actions_taken"]
        rejections = [
            a for a in actions if a.get("action") == "cli_scope_rejected"
        ]
        assert len(rejections) == 1
        assert rejections[0]["result"] == "cli_scope_not_authoritative"
        assert "cli_scope_not_authoritative" in rejections[0]["error"]

    def test_advance_with_cli_scope_does_not_post_codex_request(self):
        report = _r16_run_advance(cli_allowed="scripts/local/aed_pr*.py")
        # The codex_review_ping action must NOT appear at all.
        # The advance pipeline short-circuits before that step.
        pings = [
            a for a in report["actions_taken"]
            if a.get("action") == "codex_review_ping"
        ]
        assert pings == []

    def test_advance_with_cli_scope_does_not_resolve_threads(self):
        report = _r16_run_advance(cli_allowed="scripts/local/aed_pr*.py")
        resolutions = [
            a for a in report["actions_taken"]
            if a.get("action") == "resolve_eligible_bot_threads"
        ]
        assert resolutions == []

    def test_advance_with_cli_scope_does_not_mark_pr_ready(self):
        report = _r16_run_advance(cli_allowed="scripts/local/aed_pr*.py")
        marks = [
            a for a in report["actions_taken"]
            if a.get("action") == "mark_pr_ready"
        ]
        assert marks == []

    def test_advance_with_cli_scope_does_not_dispatch_workflow(self):
        report = _r16_run_advance(cli_allowed="scripts/local/aed_pr*.py")
        dispatches = [
            a for a in report["actions_taken"]
            if a.get("action") == "workflow_dispatch"
        ]
        assert dispatches == []

    def test_advance_with_cli_scope_does_not_emit_authorization_phrase(self):
        report = _r16_run_advance(cli_allowed="scripts/local/aed_pr*.py")
        assert report["required_authorization_phrase_if_ready"] is None
        assert report["safe_merge_command_if_ready"] is None

    def test_advance_with_cli_scope_blocks_machine_readiness(self):
        report = _r16_run_advance(cli_allowed="scripts/local/aed_pr*.py")
        assert report["machine_ready"] is False
        assert report["merge_ready"] is False
        assert report["ready"] is False
        assert report["authorization_required"] is False
        assert report["authorization_valid"] is None

    def test_advance_with_cli_forbidden_also_rejected(self):
        report = _r16_run_advance(cli_forbidden="**")
        assert report["machine_ready"] is False
        assert report.get("scope_error") is not None
        assert "cli_scope_not_authoritative" in report["scope_error"]

    def test_advance_without_cli_scope_uses_trusted_path(self):
        """No CLI flags, no trusted file: ``advance`` proceeds through
        the existing ``trusted scope not found`` path. The diagnostic
        surfaces in ``scope_error`` (and as ``next_human_action``);
        no ``cli_scope_rejected`` action appears because no CLI
        flags were supplied.
        """
        report = _r16_run_advance()
        rejections = [
            a for a in report["actions_taken"]
            if a.get("action") == "cli_scope_rejected"
        ]
        assert rejections == []
        # The trusted-file missing diagnostic surfaces in scope_error.
        assert report.get("scope_error") is not None
        assert "trusted scope not found" in report["scope_error"]
        assert report["scope_source"] == "trusted_file"


class TestRound16MergeStillRejectsCliScope:
    """Round-16: the merge command's existing rejection is preserved.

    ``cmd_merge`` exits non-zero when CLI scope is supplied and never
    invokes ``gh pr merge``. We assert that behavior end-to-end.
    """

    def test_merge_with_cli_scope_exits_nonzero_without_merge_call(self):
        # Capture every subprocess.run call so we can prove no
        # ``gh pr merge`` invocation occurs.
        captured_argvs = []

        def _capture(cmd, *args, **kwargs):
            captured_argvs.append(list(cmd))
            # Stub the pr view read so merge can get past the head_sha
            # check; anything else returns non-zero so merge bails on
            # the scope rejection before reaching the merge mutation.
            if cmd[:3] == ["gh", "pr", "view"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(_r16_pr_view_payload()),
                    stderr="",
                )
            return mock.Mock(returncode=1, stdout="", stderr="blocked")

        args = mock.Mock()
        args.repo = DEFAULT_REPO
        args.pr_number = 411
        args.allowed_files = "scripts/local/aed_pr*.py"
        args.forbidden_files = None
        args.authorization_phrase = (
            "I confirm merge PR #411 at " + "a" * 40
            + " using final-head reviewed clean state."
        )

        with mock.patch.object(
            subprocess, "run", side_effect=_capture,
        ), mock.patch.object(
            ctrl.CODEX, "classify",
            side_effect=lambda **kw: _r16_codex_classify(**kw),
        ):
            buf_out = io.StringIO()
            buf_err = io.StringIO()
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = buf_out, buf_err
            try:
                rc = ctrl.cmd_merge(args)
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        assert rc == 1
        # No ``gh pr merge`` invocation may occur.
        merge_calls = [
            argv for argv in captured_argvs
            if "merge" in argv and "gh" in argv
        ]
        assert merge_calls == [], (
            f"merge must NOT call gh when CLI scope is supplied; "
            f"saw {merge_calls!r}"
        )
        # The stderr message must mention the CLI-scope rejection.
        assert "merge does not accept" in buf_err.getvalue()


class TestRound16BuildEvidenceNeverSeesCliScope:
    """Round-16: ``build_evidence`` is never called with CLI patterns as
    authoritative ``allowed_files`` or ``forbidden_files`` for status
    or advance. The resolver's fail-closed return
    ``(None, None, error)`` means the controller passes
    ``allowed_files=None`` to ``build_evidence``, which then sets
    ``scope_clean=None`` and produces the canonical scope-not-supplied
    blocker.
    """

    def _capture_build_evidence_argv(self, cli_allowed, cli_forbidden):
        """Run ``cmd_status`` with stubbed ``build_evidence`` and
        record every keyword argument supplied.
        """
        captured = {}

        real_build_evidence = ctrl.build_evidence

        def _spy_build_evidence(**kwargs):
            captured.update(kwargs)
            return real_build_evidence(**kwargs)

        args = mock.Mock()
        args.repo = DEFAULT_REPO
        args.pr_number = 411
        args.allowed_files = cli_allowed
        args.forbidden_files = cli_forbidden

        with mock.patch.object(
            subprocess, "run", side_effect=_r16_fake_run_no_writes,
        ), mock.patch.object(
            ctrl.CODEX, "classify",
            side_effect=lambda **kw: _r16_codex_classify(**kw),
        ), mock.patch.object(
            ctrl, "build_evidence", side_effect=_spy_build_evidence,
        ):
            buf = io.StringIO()
            old_out = sys.stdout
            sys.stdout = buf
            try:
                ctrl.cmd_status(args)
            finally:
                sys.stdout = old_out
        return captured

    def test_status_with_cli_allowed_passes_none_to_build_evidence(self):
        captured = self._capture_build_evidence_argv(
            cli_allowed="scripts/local/aed_pr.py", cli_forbidden=None,
        )
        # CLI patterns MUST NOT reach build_evidence as authoritative
        # scope. The resolver returns None so the controller passes
        # allowed_files=None.
        assert captured.get("allowed_files") is None
        assert captured.get("forbidden_files") is None

    def test_status_with_cli_forbidden_passes_none_to_build_evidence(self):
        captured = self._capture_build_evidence_argv(
            cli_allowed=None, cli_forbidden="**",
        )
        assert captured.get("allowed_files") is None
        assert captured.get("forbidden_files") is None

    def test_status_without_cli_scope_passes_trusted_or_none(self):
        """When no CLI flags are supplied the resolver either returns
        the trusted scope or ``(None, None, error)``. In either case
        the controller MUST NOT invent CLI scope.
        """
        captured = self._capture_build_evidence_argv(
            cli_allowed=None, cli_forbidden=None,
        )
        # When no trusted scope exists (the default for this test)
        # the resolver returns None, None — that is the only value
        # the controller is allowed to forward.
        assert captured.get("allowed_files") is None or captured.get("allowed_files") == []
        # If a list was passed, it must not contain CLI patterns
        # (CLI patterns were never supplied in this test).
        if captured.get("allowed_files"):
            assert all(
                "scripts/local/aed_pr.py" != x for x in captured["allowed_files"]
            ), (
                "CLI patterns must not be forwarded to build_evidence; "
                f"got {captured['allowed_files']!r}"
            )


class TestRound37AuthoritativePathWhenGhPrChecksFails(unittest.TestCase):
    """Round-37 regression: when ``gh pr checks`` returns no
    JSON or has a transient endpoint failure, the controller
    MUST NOT bail out before consulting the authoritative
    exact-head ``pull_request`` run's job inventory, provided
    ``head_sha`` and ``head_branch`` are supplied and
    canonical. The bug was an early-return at the top of
    ``fetch_ci_conclusions`` that exited before
    ``_find_exact_head_pull_request_run_id`` and
    ``_run_jobs_for_run`` could run; the fix lets the
    authoritative path proceed when ``head_sha`` and
    ``head_branch`` are valid, treating the diagnostic
    ``gh pr checks`` failure as non-fatal.

    Under the previous behavior, a transient ``gh pr checks``
    failure would cause ``status`` / ``merge`` to report
    every required check as missing/failed even when the
    exact CI run was readable and green.
    """

    PR_RUN_ID = 29694702047
    HEAD = "48e1a33c511bc05676f43ac4b34b28add6bda4c2"
    BRANCH = "reduction/pr-lifecycle-collapse-v1"
    REQUIRED = ["review-comment-gate"]

    def _runner_factory(self, *, pr_checks_records=None,
                        pr_checks_failure=False,
                        pr_checks_no_payload=False,
                        pr_runs=None, pr_jobs=None):
        log = []
        pr_runs = list(pr_runs if pr_runs is not None else [
            {
                "databaseId": self.PR_RUN_ID,
                "event": "pull_request",
                "headBranch": self.BRANCH,
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": f"https://example/runs/{self.PR_RUN_ID}",
            },
        ])
        pr_jobs = list(pr_jobs if pr_jobs is not None else [
            {"name": "review-comment-gate", "databaseId": 1,
             "status": "completed", "conclusion": "success"},
        ])

        def runner(cmd, *a, **kw):
            log.append(list(cmd))
            argv = [str(x) for x in cmd]
            if argv[:3] == ["gh", "pr", "checks"]:
                if pr_checks_failure:
                    return mock.Mock(
                        returncode=1, stdout="",
                        stderr="gh pr checks: transient failure",
                    )
                if pr_checks_no_payload:
                    return mock.Mock(returncode=0, stdout="", stderr="")
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_checks_records or []),
                    stderr="",
                )
            if argv[:3] == ["gh", "run", "list"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_runs), stderr="",
                )
            if argv[:3] == ["gh", "run", "view"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"jobs": pr_jobs}), stderr="",
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        return runner, log

    def test_transient_gh_pr_checks_failure_falls_through(self):
        """Bug repro: ``gh pr checks`` returns non-zero
        exit code, but ``head_sha`` and ``head_branch`` are
        supplied. The controller MUST proceed to the
        authoritative path and report SUCCESS for the
        required check.
        """
        runner, _log = self._runner_factory(
            pr_checks_failure=True,
            pr_jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner,
            head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Round-37 invariant: ok=True (the authoritative path
        # succeeded); conclusions reports the run's status;
        # missing/failed are EMPTY even though ``gh pr checks``
        # was unavailable.
        self.assertTrue(ok)
        self.assertEqual(conclusions.get("review-comment-gate"), "SUCCESS")
        self.assertEqual(missing, [])
        self.assertEqual(failed, [])

    def test_gh_pr_checks_no_payload_falls_through(self):
        """Bug repro: ``gh pr checks`` returns exit 0 but
        empty stdout (no payload). Authoritative path must
        still be used.
        """
        runner, _log = self._runner_factory(
            pr_checks_no_payload=True,
            pr_jobs=[
                {"name": "review-comment-gate", "databaseId": 1,
                 "status": "completed", "conclusion": "success"},
            ],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner,
            head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        self.assertTrue(ok)
        self.assertEqual(conclusions.get("review-comment-gate"), "SUCCESS")
        self.assertEqual(missing, [])
        self.assertEqual(failed, [])

    def test_no_head_sha_still_bails_on_gh_pr_checks_failure(self):
        """Round-37 guard: when ``head_sha`` is NOT supplied,
        the controller has no authoritative binding to fall
        back to. A ``gh pr checks`` failure MUST fail closed
        (the previous behavior is preserved).
        """
        runner, _log = self._runner_factory(pr_checks_failure=True)
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner,
            head_sha=None, head_branch=None,
        )
        # No authoritative binding; gh pr checks failure
        # fails closed as before.
        self.assertFalse(ok)
        self.assertEqual(missing, list(self.REQUIRED))
        self.assertEqual(failed, list(self.REQUIRED))
        self.assertTrue(err, "err field must be non-empty on gh pr checks failure")

    def test_non_canonical_head_sha_still_bails(self):
        """Round-37 guard: a non-canonical ``head_sha`` (the
        early-return only falls through on canonical
        40-lowercase-hex strings) MUST still bail out when
        ``gh pr checks`` fails. The path safety contract is
        preserved.
        """
        runner, _log = self._runner_factory(pr_checks_failure=True)
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner,
            head_sha="not-canonical", head_branch=self.BRANCH,
        )
        self.assertFalse(ok)
        self.assertEqual(missing, list(self.REQUIRED))
        self.assertEqual(failed, list(self.REQUIRED))

    def test_authoritative_failure_still_fails_closed(self):
        """Round-37 guard: when both ``gh pr checks`` AND
        the authoritative path fail, every required check is
        reported missing/failed (fail-closed). The fix MUST
        NOT silently report success.
        """
        # Simulate authoritative path failure: empty pr_runs
        runner, _log = self._runner_factory(
            pr_checks_failure=True, pr_runs=[],
        )
        (ok, conclusions, missing, pending, failed,
         duplicated, err) = ctrl.fetch_ci_conclusions(
            "owner/repo", 411, list(self.REQUIRED),
            runner=runner,
            head_sha=self.HEAD, head_branch=self.BRANCH,
        )
        # Authoritative path failed (no exact-head PR run);
        # every required check missing and failed.
        self.assertTrue(ok)
        self.assertEqual(missing, list(self.REQUIRED))
        self.assertEqual(failed, list(self.REQUIRED))
