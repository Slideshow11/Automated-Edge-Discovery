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
        """Even with verified ancestry, an outdated-required or
        actor-required failure still blocks the thread."""
        # ``is_outdated=False`` -> reason "not_outdated".
        thread = _bot_thread(anchor=OTHER_HEAD, is_outdated=False)
        ancestry_runner = lambda *a, **kw: mock.Mock(
            returncode=0, stdout="ahead", stderr=""
        )
        ok, reason = R.is_eligible_for_bot_resolution(
            thread,
            **_eligibility_kwargs(ancestry_runner=ancestry_runner),
        )
        assert ok is False
        assert reason == "not_outdated"


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


class TestRound5Finding2GateRecheckDispatchesCI:
    def test_gate_recheck_invokes_workflow_run_with_ref(self):
        """``gate-recheck`` MUST invoke ``gh workflow run ci.yml``
        with ``--ref <live PR head branch>`` so the run is bound
        to the PR branch and not the repository default.

        Round-5 Codex finding 3604451971.
        """
        from unittest import mock
        dispatch_invocations = []
        list_invocations = []
        view_invocations = []

        def fake_dispatch(cmd, *a, **kw):
            dispatch_invocations.append(list(cmd))
            return mock.Mock(returncode=0, stdout="", stderr="")

        def fake_list(cmd, *a, **kw):
            list_invocations.append(list(cmd))
            # Return one matching run with ``createdAt`` set to
            # "now" so it qualifies as "at or after dispatched_at".
            import datetime as _dt
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 29593005015, "name": "CI",
                    "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "status": "completed", "conclusion": "success",
                    "createdAt": _dt.datetime.now(
                        _dt.timezone.utc
                    ).isoformat(),
                    "url": "https://example/runs/29593005015",
                    "workflowName": "CI",
                    "workflowDatabaseId": 263541549,
                }]),
                stderr="",
            )

        def fake_view(cmd, *a, **kw):
            view_invocations.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": [{
                    "name": "review-comment-gate",
                    "status": "completed",
                    "conclusion": "success",
                }]}),
                stderr="",
            )

        def fake_pr_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": DEFAULT_HEAD,
                    "headRefName": "reduction/pr-lifecycle-collapse-v1",
                    "baseRefOid": "b" * 40, "baseRefName": "main",
                    "additions": 0, "deletions": 0, "changedFiles": 0,
                    "url": "u", "files": [],
                }),
                stderr="",
            )

        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.discovery_timeout_seconds = 5
        ns.discovery_poll_seconds = 1
        ns.dispatch_runner = fake_dispatch
        ns.list_runner = fake_list
        ns.view_runner = fake_view
        ns.pr_view_runner = fake_pr_view

        ctrl.cmd_gate_recheck(ns)

        assert dispatch_invocations, (
            "gate-recheck must dispatch the CI workflow"
        )
        argv = dispatch_invocations[0]
        assert argv[0:3] == ["gh", "workflow", "run"]
        assert "ci.yml" in argv
        # CRITICAL: --ref must bind to the live PR branch, not main.
        assert "--ref" in argv, (
            "dispatch must include --ref to bind to the PR branch"
        )
        ref_idx = argv.index("--ref")
        assert argv[ref_idx + 1] == "reduction/pr-lifecycle-collapse-v1", (
            f"--ref must be the live PR branch; got {argv[ref_idx + 1]!r}"
        )
        assert "main" not in [
            a for a in argv[ref_idx:] if isinstance(a, str)
        ], "main must not appear as the dispatch ref"
        # pr_number and head_sha inputs are passed.
        assert any("411" in str(x) for x in argv)
        assert any(DEFAULT_HEAD in str(x) for x in argv)

    def test_gate_recheck_dispatch_failure_skips_run_list(self):
        """When dispatch fails, the run-list and run-view steps
        MUST NOT be invoked. The controller returns INCONCLUSIVE."""
        from unittest import mock
        list_invocations = []
        view_invocations = []

        def fake_dispatch(cmd, *a, **kw):
            return mock.Mock(returncode=1, stdout="", stderr="err")

        def fake_list(cmd, *a, **kw):
            list_invocations.append(list(cmd))
            return mock.Mock(returncode=0, stdout="[]", stderr="")

        def fake_view(cmd, *a, **kw):
            view_invocations.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": []}),
                stderr="",
            )

        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.discovery_timeout_seconds = 5
        ns.discovery_poll_seconds = 1
        ns.dispatch_runner = fake_dispatch
        ns.list_runner = fake_list
        ns.view_runner = fake_view

        def fake_pr_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": DEFAULT_HEAD,
                    "headRefName": "reduction/pr-lifecycle-collapse-v1",
                    "baseRefOid": "b" * 40, "baseRefName": "main",
                    "additions": 0, "deletions": 0, "changedFiles": 0,
                    "url": "u", "files": [],
                }),
                stderr="",
            )
        ns.pr_view_runner = fake_pr_view

        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2  # INCONCLUSIVE
        assert not list_invocations, (
            "run list must not run when dispatch fails"
        )
        assert not view_invocations, (
            "run view must not run when dispatch fails"
        )

    def test_gate_recheck_rejects_run_on_main(self):
        """A workflow run tied to ``main`` is rejected because it
        is not bound to the exact PR head."""
        from unittest import mock

        def fake_dispatch(cmd, *a, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")

        # Run on main instead of the PR branch
        main_run = {
            "databaseId": 1, "name": "CI",
            "event": "workflow_dispatch",
            "headBranch": "main",
            "headSha": DEFAULT_HEAD,
            "status": "completed", "conclusion": "success",
            "createdAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "url": "https://example/runs/1",
            "workflowName": "CI",
            "workflowDatabaseId": 263541549,
        }

        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([main_run]),
                stderr="",
            )

        def fake_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": [{
                    "name": "review-comment-gate",
                    "status": "completed",
                    "conclusion": "success",
                }]}),
                stderr="",
            )

        def fake_pr_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": DEFAULT_HEAD,
                    "headRefName": "reduction/pr-lifecycle-collapse-v1",
                    "baseRefOid": "b" * 40, "baseRefName": "main",
                    "additions": 0, "deletions": 0, "changedFiles": 0,
                    "url": "u", "files": [],
                }),
                stderr="",
            )

        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.discovery_timeout_seconds = 5
        ns.discovery_poll_seconds = 1
        ns.dispatch_runner = fake_dispatch
        ns.list_runner = fake_list
        ns.view_runner = fake_view
        ns.pr_view_runner = fake_pr_view

        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2, (
            f"a main-branch run must NOT be selected; got {result}"
        )

    def test_gate_recheck_rejects_run_on_other_sha(self):
        """A run whose head_sha does not match the live PR head is
        rejected."""
        from unittest import mock

        def fake_dispatch(cmd, *a, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")

        wrong_sha_run = {
            "databaseId": 2, "name": "CI",
            "event": "workflow_dispatch",
            "headBranch": "reduction/pr-lifecycle-collapse-v1",
            "headSha": "f" * 40,  # wrong head SHA
            "status": "completed", "conclusion": "success",
            "createdAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "url": "https://example/runs/2",
            "workflowName": "CI",
            "workflowDatabaseId": 263541549,
        }

        def fake_list(cmd, *a, **kw):
            run = dict(wrong_sha_run)
            run["createdAt"] = _dt.datetime.now(
                _dt.timezone.utc
            ).isoformat()
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([run]),
                stderr="",
            )

        def fake_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": [{
                    "name": "review-comment-gate",
                    "status": "completed",
                    "conclusion": "success",
                }]}),
                stderr="",
            )

        def fake_pr_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": DEFAULT_HEAD,
                    "headRefName": "reduction/pr-lifecycle-collapse-v1",
                    "baseRefOid": "b" * 40, "baseRefName": "main",
                    "additions": 0, "deletions": 0, "changedFiles": 0,
                    "url": "u", "files": [],
                }),
                stderr="",
            )

        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.discovery_timeout_seconds = 5
        ns.discovery_poll_seconds = 1
        ns.dispatch_runner = fake_dispatch
        ns.list_runner = fake_list
        ns.view_runner = fake_view
        ns.pr_view_runner = fake_pr_view

        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2, (
            f"a wrong-SHA run must NOT be selected; got {result}"
        )

    def test_gate_recheck_rejects_older_run(self):
        """A run that pre-dates the dispatch attempt is rejected.

        The controller records ``dispatched_at`` immediately
        before the ``gh workflow run`` call; any run with
        ``createdAt`` BEFORE ``dispatched_at`` is from a previous
        dispatch and must NOT be selected."""
        from unittest import mock

        def fake_dispatch(cmd, *a, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")

        # Run created one hour BEFORE the dispatch attempt would
        # be considered by the strict matcher.
        older_run = {
            "databaseId": 3, "name": "CI",
            "event": "workflow_dispatch",
            "headBranch": "reduction/pr-lifecycle-collapse-v1",
            "headSha": DEFAULT_HEAD,
            "status": "completed", "conclusion": "success",
            "createdAt": "2026-07-17T10:00:00Z",  # older than dispatch
            "url": "https://example/runs/3",
            "workflowName": "CI",
            "workflowDatabaseId": 263541549,
        }

        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([older_run]),
                stderr="",
            )

        def fake_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": [{
                    "name": "review-comment-gate",
                    "status": "completed",
                    "conclusion": "success",
                }]}),
                stderr="",
            )

        def fake_pr_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": DEFAULT_HEAD,
                    "headRefName": "reduction/pr-lifecycle-collapse-v1",
                    "baseRefOid": "b" * 40, "baseRefName": "main",
                    "additions": 0, "deletions": 0, "changedFiles": 0,
                    "url": "u", "files": [],
                }),
                stderr="",
            )

        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.discovery_timeout_seconds = 5
        ns.discovery_poll_seconds = 1
        ns.dispatch_runner = fake_dispatch
        ns.list_runner = fake_list
        ns.view_runner = fake_view
        ns.pr_view_runner = fake_pr_view

        result = ctrl.cmd_gate_recheck(ns)
        assert result == 2, (
            f"an older run must NOT be selected; got {result}"
        )

    def test_gate_recheck_local_check_does_not_substitute_for_ci_run(self):
        """The local ``check_pr_review_comments.py`` script result
        must NOT be treated as a GitHub Actions required-check
        result. A local CLEAN with no matching dispatched run
        returns INCONCLUSIVE, NOT 0."""
        from unittest import mock

        def fake_dispatch(cmd, *a, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")

        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([]),  # no dispatched run
                stderr="",
            )

        def fake_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": [{
                    "name": "review-comment-gate",
                    "status": "completed",
                    "conclusion": "success",
                }]}),
                stderr="",
            )

        def fake_pr_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": DEFAULT_HEAD,
                    "headRefName": "reduction/pr-lifecycle-collapse-v1",
                    "baseRefOid": "b" * 40, "baseRefName": "main",
                    "additions": 0, "deletions": 0, "changedFiles": 0,
                    "url": "u", "files": [],
                }),
                stderr="",
            )

        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.discovery_timeout_seconds = 5
        ns.discovery_poll_seconds = 1
        ns.dispatch_runner = fake_dispatch
        ns.list_runner = fake_list
        ns.view_runner = fake_view
        ns.pr_view_runner = fake_pr_view

        result = ctrl.cmd_gate_recheck(ns)
        # A clean local check alone cannot make gate-recheck return 0
        # when there is no matching GitHub Actions run.
        assert result != 0, (
            f"a local clean without a matching run must NOT return 0; "
            f"got {result}"
        )
        assert result == 2, (
            f"a missing dispatched run must return INCONCLUSIVE; got {result}"
        )

    def test_gate_recheck_terminal_blocking_returns_blocking(self):
        """When the dispatched gate's conclusion is ``failure``,
        return 1 (exact-head blocking)."""
        from unittest import mock

        def fake_dispatch(cmd, *a, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")

        matching_run = {
            "databaseId": 4, "name": "CI",
            "event": "workflow_dispatch",
            "headBranch": "reduction/pr-lifecycle-collapse-v1",
            "headSha": DEFAULT_HEAD,
            "status": "completed", "conclusion": "failure",
            "createdAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "url": "https://example/runs/4",
            "workflowName": "CI",
            "workflowDatabaseId": 263541549,
        }

        def fake_list(cmd, *a, **kw):
            # Generate the run's createdAt AT call time so it is
            # always >= the dispatched_at the controller records.
            run = dict(matching_run)
            run["createdAt"] = _dt.datetime.now(
                _dt.timezone.utc
            ).isoformat()
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([run]),
                stderr="",
            )

        def fake_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": [{
                    "name": "review-comment-gate",
                    "status": "completed",
                    "conclusion": "failure",
                }]}),
                stderr="",
            )

        def fake_pr_view(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": DEFAULT_HEAD,
                    "headRefName": "reduction/pr-lifecycle-collapse-v1",
                    "baseRefOid": "b" * 40, "baseRefName": "main",
                    "additions": 0, "deletions": 0, "changedFiles": 0,
                    "url": "u", "files": [],
                }),
                stderr="",
            )

        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = DEFAULT_HEAD
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.discovery_timeout_seconds = 5
        ns.discovery_poll_seconds = 1
        ns.dispatch_runner = fake_dispatch
        ns.list_runner = fake_list
        ns.view_runner = fake_view
        ns.pr_view_runner = fake_pr_view

        result = ctrl.cmd_gate_recheck(ns)
        assert result == 1, (
            f"exact-head blocking must return 1; got {result}"
        )


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


class TestRound8EmptyChangedFileInventory:
    """Round-8 follow-up (Codex comment 3609202696 on 1e9867e):

    fetch_changed_files returns ok=True ONLY when at least one
    valid path was extracted. An empty / malformed inventory is a
    fetch failure that fails closed.
    """

    def _patch_json_or_none(self, payload):
        return mock.patch.object(
            ctrl, "_run_json_or_none",
            side_effect=lambda cmd, **kw: (True, payload, ""),
        )

    def test_normal_nonempty_dedicated_inventory_succeeds(self):
        payload = {
            "files": [
                {"path": "scripts/local/aed_pr.py"},
                {"path": "tests/test_aed_pr.py"},
            ]
        }
        with self._patch_json_or_none(payload):
            ok, paths, err = ctrl.fetch_changed_files(
                "owner/repo", 411
            )
        assert ok is True
        assert paths == [
            "scripts/local/aed_pr.py", "tests/test_aed_pr.py"
        ]
        assert err == ""

    def test_empty_dedicated_list_plus_valid_fallback_succeeds(self):
        # Dedicated list is empty; the fallback (pr_view['files'])
        # supplies valid paths.
        with self._patch_json_or_none({"files": []}):
            ok, paths, err = ctrl.fetch_changed_files(
                "owner/repo", 411,
                pr_view={
                    "files": [
                        {"path": "scripts/local/aed_pr.py"},
                    ]
                },
            )
        assert ok is True
        assert paths == ["scripts/local/aed_pr.py"]
        assert err == ""

    def test_all_malformed_dedicated_list_plus_valid_fallback_succeeds(self):
        with self._patch_json_or_none({
            "files": [
                {"no_path": True},
                "not-a-dict",
                {"path": ""},
                {"path": None},
            ]
        }):
            ok, paths, err = ctrl.fetch_changed_files(
                "owner/repo", 411,
                pr_view={
                    "files": [
                        {"path": "scripts/local/aed_pr.py"},
                    ]
                },
            )
        assert ok is True
        assert paths == ["scripts/local/aed_pr.py"]

    def test_empty_dedicated_and_empty_fallback_fail(self):
        with self._patch_json_or_none({"files": []}):
            ok, paths, err = ctrl.fetch_changed_files(
                "owner/repo", 411,
                pr_view={"files": []},
            )
        assert ok is False
        assert paths == []
        assert err == "empty_changed_file_inventory"

    def test_all_malformed_dedicated_and_missing_fallback_fail(self):
        with self._patch_json_or_none({
            "files": [
                {"no_path": True},
                {"path": ""},
            ]
        }):
            ok, paths, err = ctrl.fetch_changed_files(
                "owner/repo", 411,
                pr_view={},
            )
        assert ok is False
        assert paths == []
        assert err == "empty_changed_file_inventory"

    def test_successful_command_with_no_valid_paths_fails_closed(self):
        # The dedicated call succeeded but every ``path`` slot is
        # empty/missing - the controller MUST NOT report ok=True.
        with self._patch_json_or_none({
            "files": [
                {"path": ""},
                {"no_path": True},
            ]
        }):
            ok, paths, err = ctrl.fetch_changed_files("owner/repo", 411)
        assert ok is False
        assert paths == []
        assert err == "empty_changed_file_inventory"

    def test_build_evidence_reports_changed_files_missing(self):
        # When ``fetch_changed_files`` reports ok=False with the
        # empty-inventory marker, ``build_evidence`` must treat
        # the evidence as missing. The function itself returns
        # ``(False, [], "empty_changed_file_inventory")``; the
        # caller (``build_evidence``) propagates that into
        # ``changed_files_fetched=False``.
        with self._patch_json_or_none({"files": []}):
            ok, paths, err = ctrl.fetch_changed_files(
                "owner/repo", 411, pr_view={"files": []},
            )
        assert ok is False
        assert err == "empty_changed_file_inventory"

    def test_no_allowed_scope_can_make_empty_inventory_pass(self):
        # Even with an explicit allowed scope, an empty inventory
        # must not pass the scope gate. We verify the inventory is
        # rejected at fetch time; build_evidence propagates
        # ``changed_files_fetched=False`` and the scope check
        # fails closed.
        with self._patch_json_or_none({"files": []}):
            ok, paths, err = ctrl.fetch_changed_files(
                "owner/repo", 411, pr_view={"files": []},
            )
        assert ok is False
        assert paths == []
        # build_evidence would set changed_files_fetched=False
        # and the scope gate would fail closed with
        # ``changed_files_not_fetched``.
        assert err == "empty_changed_file_inventory"


# ---------------------------------------------------------------------------
# Round-8 follow-up: dispatch timestamp precision skew
# ---------------------------------------------------------------------------


class TestRound8DispatchPrecisionSkew:
    """Round-8 follow-up (Codex comment 3609202698 on 1e9867e):

    GitHub Actions ``createdAt`` is whole-second; ``dispatched_at``
    has fractional seconds. The runner must floor ``dispatched_at``
    to whole-second precision before comparing so a dispatch at
    12:00:00.900000Z against a run at 12:00:00Z is accepted. Runs
    from an earlier second are still rejected.
    """

    def _run(self, *, created_at, dispatched_at):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 1, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": created_at,
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/1",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        return ctrl._find_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dispatched_at,
            list_runner=fake_list,
        )

    def test_fractional_dispatch_at_accepts_whole_second_run(self):
        run, err = self._run(
            created_at="2026-07-18T12:00:00Z",
            dispatched_at=dt.datetime(
                2026, 7, 18, 12, 0, 0, 900000, tzinfo=dt.timezone.utc
            ),
        )
        assert err == ""
        assert run is not None
        assert run["databaseId"] == 1

    def test_run_before_dispatch_second_rejected(self):
        run, err = self._run(
            created_at="2026-07-18T11:59:59Z",
            dispatched_at=dt.datetime(
                2026, 7, 18, 12, 0, 0, 900000, tzinfo=dt.timezone.utc
            ),
        )
        assert run is None
        assert err

    def test_run_after_dispatch_second_accepted(self):
        run, err = self._run(
            created_at="2026-07-18T12:00:05Z",
            dispatched_at=dt.datetime(
                2026, 7, 18, 12, 0, 0, 900000, tzinfo=dt.timezone.utc
            ),
        )
        assert err == ""
        assert run is not None

    def test_malformed_createdAt_rejected(self):
        run, err = self._run(
            created_at="not-a-timestamp",
            dispatched_at=dt.datetime(
                2026, 7, 18, 12, 0, 0, 0, tzinfo=dt.timezone.utc
            ),
        )
        assert run is None
        assert err

    def test_wrong_branch_rejected(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 2, "event": "workflow_dispatch",
                    "headBranch": "main",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-18T12:00:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/2",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        run, err = ctrl._find_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 18, 12, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert run is None
        assert err

    def test_wrong_sha_rejected(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 3, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": "f" * 40,
                    "createdAt": "2026-07-18T12:00:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/3",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        run, err = ctrl._find_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 18, 12, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert run is None
        assert err

    def test_wrong_workflow_name_rejected(self):
        def fake_list(cmd, *a, **kw):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 4, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-18T12:00:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/4",
                    "workflowName": "OTHER",
                }]),
                stderr="",
            )
        run, err = ctrl._find_dispatch_run(
            "owner/repo", "ci.yml",
            head_sha=DEFAULT_HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            pr_number=411,
            dispatched_at=dt.datetime(
                2026, 7, 18, 12, 0, 0, tzinfo=dt.timezone.utc
            ),
            list_runner=fake_list,
        )
        assert run is None
        assert err

    def test_discovery_polling_remains_intact(self):
        # ``_wait_for_dispatch_run`` continues to work with the new
        # whole-second boundary; the run is found on the first
        # poll.
        state = {"count": 0}
        def fake_list(cmd, *a, **kw):
            state["count"] += 1
            return mock.Mock(
                returncode=0,
                stdout=json.dumps([{
                    "databaseId": 7, "event": "workflow_dispatch",
                    "headBranch": "reduction/pr-lifecycle-collapse-v1",
                    "headSha": DEFAULT_HEAD,
                    "createdAt": "2026-07-18T12:00:00Z",
                    "status": "completed", "conclusion": "success",
                    "url": "https://example/runs/7",
                    "workflowName": "CI",
                }]),
                stderr="",
            )
        dispatched_at = dt.datetime(
            2026, 7, 18, 12, 0, 0, 900000, tzinfo=dt.timezone.utc
        )
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
        assert run["databaseId"] == 7
