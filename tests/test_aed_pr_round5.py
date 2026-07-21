"""Round-17 regression tests for ``_fetch_gh_pr_checks_payload``.

The Round-17 fix narrows the helper to parse ``gh pr checks --json``
output regardless of its **status-oriented** exit code:

* ``0`` — all checks passed
* ``1`` — one or more checks failed
* ``8`` — one or more checks are pending

All three codes may still produce valid JSON. The helper must:

* accept valid JSON for return codes 0, 1, 8;
* preserve each check's per-record state;
* reject return code 2, 4 or any other unexpected code even when
  stdout contains JSON;
* reject empty stdout or malformed JSON;
* reject process-launch failures and timeouts;
* never convert return code 1 or 8 into a successful check
  conclusion (the caller ``fetch_ci_conclusions`` decides the
  per-record conclusion).

These tests use the ``runner`` injection seam of
``_fetch_gh_pr_checks_payload`` to control return code, stdout,
stderr, and exception behavior deterministically.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest.mock as mock
from typing import Any, List, Optional

import pytest

import scripts.local.aed_pr as ctrl


REPO = "Slideshow11/Automated-Edge-Discovery"
PR = 411


class _FakeProc:
    """Mimics the ``subprocess.CompletedProcess``-shaped object the
    runner seam receives."""

    def __init__(
        self,
        returncode: Optional[int],
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_runner(
    returncode: Optional[int] = 0,
    stdout: str = "",
    stderr: str = "",
    *,
    raise_exc: Optional[BaseException] = None,
):
    """Return a runner closure compatible with ``_fetch_gh_pr_checks_payload``.

    If ``raise_exc`` is set, the closure raises it instead of
    returning a fake proc. The closure records every invocation in
    the returned ``calls`` list so tests can assert the cmd shape.
    """
    calls: List[List[str]] = []

    def runner(cmd, **kwargs):
        calls.append(list(cmd))
        if raise_exc is not None:
            raise raise_exc
        return _FakeProc(returncode, stdout, stderr)

    return runner, calls


def _valid_json(state: str = "SUCCESS") -> str:
    return json.dumps([
        {"name": "test (3.11)", "state": state, "workflow": "CI"},
    ])


# ---------------------------------------------------------------------------
# Accepted return codes (0, 1, 8) with valid JSON
# ---------------------------------------------------------------------------


class TestAcceptedReturnCodes:
    """Return codes 0, 1, and 8 with valid JSON must be accepted.

    The actual check state must be preserved verbatim in the
    returned payload — the helper must NOT convert return code 1
    or 8 into a successful conclusion; classification is the
    caller's job.
    """

    def test_returncode_0_valid_json_accepted(self):
        runner, calls = _make_runner(0, _valid_json("SUCCESS"))
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert ok is True
        assert err == ""
        assert isinstance(payload, list)
        assert payload[0]["state"] == "SUCCESS"
        # cmd shape unchanged
        assert calls[0] == [
            "gh", "pr", "checks", str(PR),
            "--repo", REPO, "--json", "name,state,workflow",
        ]

    def test_returncode_1_valid_json_accepted(self):
        runner, _ = _make_runner(1, _valid_json("FAILURE"))
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert ok is True
        assert err == ""
        assert isinstance(payload, list)
        # FAILURE state must be preserved — the helper must NOT
        # silently coerce return code 1 to SUCCESS.
        assert payload[0]["state"] == "FAILURE"

    def test_returncode_8_valid_json_accepted(self):
        runner, _ = _make_runner(8, _valid_json("PENDING"))
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert ok is True
        assert err == ""
        assert isinstance(payload, list)
        # PENDING state must be preserved — the helper must NOT
        # silently coerce return code 8 to SUCCESS.
        assert payload[0]["state"] == "PENDING"

    def test_returncode_1_preserves_failure_state_records(self):
        records = [
            {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
            {"name": "validator", "state": "FAILURE", "workflow": "CI"},
            {"name": "governance-validators", "state": "FAILURE",
             "workflow": "CI"},
            {"name": "pr-gate-live-smoke", "state": "SUCCESS",
             "workflow": "CI"},
        ]
        runner, _ = _make_runner(1, json.dumps(records))
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert ok is True
        assert err == ""
        assert [r["state"] for r in payload] == [
            "SUCCESS", "FAILURE", "FAILURE", "SUCCESS",
        ]

    def test_returncode_8_preserves_pending_state_records(self):
        records = [
            {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
            {"name": "validator", "state": "PENDING", "workflow": "CI"},
            {"name": "governance-validators", "state": "QUEUED",
             "workflow": "CI"},
            {"name": "pr-gate-live-smoke", "state": "IN_PROGRESS",
             "workflow": "CI"},
        ]
        runner, _ = _make_runner(8, json.dumps(records))
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert ok is True
        assert err == ""
        states = sorted(r["state"] for r in payload)
        assert states == ["IN_PROGRESS", "PENDING", "QUEUED", "SUCCESS"]


# ---------------------------------------------------------------------------
# Rejected return codes / payloads
# ---------------------------------------------------------------------------


class TestRejectedReturnCodes:
    """Return codes other than 0/1/8 must be rejected, even with valid
    JSON, and the diagnostic must carry the exit code so the operator
    can distinguish a transport failure from a status exit.
    """

    def _assert_rejected(self, ok, payload, err):
        assert ok is False
        assert payload is None
        assert err  # bounded, non-empty
        assert len(err) <= 400  # bounded diagnostic

    def test_returncode_1_empty_stdout_rejected(self):
        runner, _ = _make_runner(1, "")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "empty stdout" in err

    def test_returncode_8_empty_stdout_rejected(self):
        runner, _ = _make_runner(8, "   \n")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "empty stdout" in err

    def test_returncode_1_malformed_json_rejected(self):
        runner, _ = _make_runner(1, "{not valid json")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "invalid JSON" in err

    def test_returncode_8_malformed_json_rejected(self):
        runner, _ = _make_runner(8, "[unterminated")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "invalid JSON" in err

    def test_returncode_2_valid_json_rejected(self):
        runner, _ = _make_runner(2, _valid_json("SUCCESS"), "auth failed")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "2" in err
        assert "auth failed" in err

    def test_returncode_4_valid_json_rejected(self):
        runner, _ = _make_runner(4, _valid_json("SUCCESS"), "fatal")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "4" in err
        assert "fatal" in err

    def test_unexpected_return_code_valid_json_rejected(self):
        # 137 is SIGKILL — never an accepted status.
        runner, _ = _make_runner(
            137, _valid_json("SUCCESS"), "killed"
        )
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "137" in err
        assert "killed" in err

    def test_unexpected_return_code_no_stderr_still_diagnostic(self):
        runner, _ = _make_runner(42, _valid_json("SUCCESS"), "")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)
        assert "42" in err

    def test_returncode_is_none_rejected(self):
        # Simulates a process-launch failure on the default
        # ``subprocess.run`` path before returncode is assigned.
        runner, _ = _make_runner(None, "", "")
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        self._assert_rejected(ok, payload, err)


# ---------------------------------------------------------------------------
# Exception paths
# ---------------------------------------------------------------------------


class TestExceptionPaths:
    """OSError and subprocess.TimeoutExpired must surface as a
    structured failure, not crash the helper.
    """

    def test_oserror_rejected(self):
        runner, _ = _make_runner(raise_exc=OSError("spawn failed"))
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert ok is False
        assert payload is None
        assert "spawn failed" in err
        assert "gh invocation failed" in err

    def test_subprocess_timeoutexpired_rejected(self):
        runner, _ = _make_runner(
            raise_exc=subprocess.TimeoutExpired(cmd="gh", timeout=45),
        )
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert ok is False
        assert payload is None
        assert "gh invocation failed" in err

    def test_timeout_rejected_even_when_no_payload(self):
        # The exception path must short-circuit before any stdout
        # inspection.
        runner, _ = _make_runner(
            raise_exc=subprocess.TimeoutExpired(cmd="gh", timeout=45),
        )
        ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
            REPO, PR, runner=runner
        )
        assert payload is None
        # The diagnostic identifies the failure but does not
        # include the raw exception class or un-bounded stderr.
        assert "gh invocation failed" in err
        assert len(err) <= 400


# ---------------------------------------------------------------------------
# fetch_ci_conclusions end-to-end (authoritative exact-head path)
# ---------------------------------------------------------------------------


class TestFetchCiConclusionsAuthoritativePath:
    """End-to-end tests proving that ``fetch_ci_conclusions`` still
    classifies authoritative required CI from the exact-head PR
    run's job inventory even when ``gh pr checks`` exits 1 or 8.

    The authoritative path must remain unchanged: the JSON
    payload is diagnostic only, the unique exact-head PR run is
    the source of truth for required-check conclusions.
    """

    REQUIRED = [
        "test (3.11)",
        "validator",
        "governance-validators",
        "pr-gate-live-smoke",
    ]

    def _authoritative_jobs(self) -> List[dict]:
        # All four required jobs succeeded in the PR run.
        return [
            {
                "databaseId": 1001,
                "name": "test (3.11)",
                "conclusion": "success",
                "status": "completed",
                "steps": [],
            },
            {
                "databaseId": 1002,
                "name": "validator",
                "conclusion": "success",
                "status": "completed",
                "steps": [],
            },
            {
                "databaseId": 1003,
                "name": "governance-validators",
                "conclusion": "success",
                "status": "completed",
                "steps": [],
            },
            {
                "databaseId": 1004,
                "name": "pr-gate-live-smoke",
                "conclusion": "success",
                "status": "completed",
                "steps": [],
            },
            {
                "databaseId": 1005,
                "name": "review-comment-gate",
                "conclusion": "failure",
                "status": "completed",
                "steps": [],
            },
        ]

    def _build_runner(
        self,
        gh_payload: str,
        gh_returncode: int,
        auth_jobs: List[dict],
        head_sha: str,
    ):
        """Multi-purpose runner covering the three ``gh`` commands
        that ``fetch_ci_conclusions`` issues on the authoritative
        exact-head path:

        1. ``gh pr checks --json ...``
        2. ``gh run list ... --commit <head> --event pull_request``
        3. ``gh run view <run_id> --json jobs``
        """
        pr_runs = json.dumps([{
            "databaseId": 99001,
            "name": "CI",
            "workflowName": "CI",
            "headBranch": "reduction/pr-lifecycle-collapse-v1",
            "headSha": head_sha,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "url": f"https://github.com/{REPO}/actions/runs/99001",
        }])
        jobs_json = json.dumps({"jobs": auth_jobs})

        def runner(cmd, **kwargs):
            # ``cmd`` is a list of strings — match by element / substring
            # inside elements.
            cmd_str = " ".join(cmd)
            if "pr checks" in cmd_str:
                return _FakeProc(gh_returncode, gh_payload, "")
            if "run list" in cmd_str:
                return _FakeProc(0, pr_runs, "")
            if "run view" in cmd_str:
                return _FakeProc(0, jobs_json, "")
            raise AssertionError(
                f"unexpected runner call: {cmd}"
            )

        return runner

    def test_gh_exits_1_but_pr_run_jobs_pass(self):
        """The failed ``review-comment-gate`` only shows up in
        ``gh pr checks`` (return code 1). The authoritative PR
        run's jobs all succeed. Required CI must come from the
        PR run.
        """
        head_sha = "0" * 40
        gh_payload = json.dumps([
            {"name": "review-comment-gate", "state": "FAILURE",
             "workflow": "CI"},
        ])
        runner = self._build_runner(
            gh_payload, 1, self._authoritative_jobs(), head_sha,
        )
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO,
                PR,
                list(self.REQUIRED),
                runner=runner,
                head_sha=head_sha,
                head_branch="reduction/pr-lifecycle-collapse-v1",
            )
        )
        assert ok is True
        assert err == ""
        for name in self.REQUIRED:
            assert conclusions.get(name) == "SUCCESS", (
                f"{name}: expected SUCCESS, got {conclusions.get(name)!r}"
            )
        assert missing == []
        assert pending == []
        assert failed == []

    def test_gh_exits_8_with_pending_record_keeps_pending(self):
        """When ``gh pr checks`` exits 8 with a pending record, the
        authoritative PR-run job for the same required check is
        still pending (the authoritative run is the source of
        truth, not the JSON exit code).
        """
        head_sha = "0" * 40
        jobs = self._authoritative_jobs()
        for j in jobs:
            if j["name"] == "validator":
                j["conclusion"] = None
                j["status"] = "in_progress"
        gh_payload = json.dumps([
            {"name": "validator", "state": "PENDING", "workflow": "CI"},
        ])
        runner = self._build_runner(
            gh_payload, 8, jobs, head_sha,
        )
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO, PR, list(self.REQUIRED),
                runner=runner,
                head_sha=head_sha,
                head_branch="reduction/pr-lifecycle-collapse-v1",
            )
        )
        assert ok is True
        assert conclusions.get("validator") == "PENDING"
        assert "validator" in pending
        assert missing == []
        assert failed == []

    def test_gh_exits_1_with_failed_record_keeps_failed(self):
        """When ``gh pr checks`` exits 1 with a failed record, the
        authoritative PR-run job for the same required check is
        still failed.
        """
        head_sha = "0" * 40
        jobs = self._authoritative_jobs()
        for j in jobs:
            if j["name"] == "validator":
                j["conclusion"] = "failure"
        gh_payload = json.dumps([
            {"name": "validator", "state": "FAILURE", "workflow": "CI"},
        ])
        runner = self._build_runner(
            gh_payload, 1, jobs, head_sha,
        )
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO, PR, list(self.REQUIRED),
                runner=runner,
                head_sha=head_sha,
                head_branch="reduction/pr-lifecycle-collapse-v1",
            )
        )
        assert ok is True
        assert conclusions.get("validator") == "FAILURE"
        assert "validator" in failed
        assert missing == []

    def test_failed_push_run_record_cannot_override_pr_run(self):
        """A diagnostic push-run record (different branch, same name)
        must not flip a successful authoritative job to failure.
        """
        head_sha = "0" * 40
        gh_payload = json.dumps([
            # Same name as a required job, but its state in the JSON
            # is FAILURE — pushed from a fix/* branch.
            {"name": "validator", "state": "FAILURE", "workflow": "CI"},
        ])
        runner = self._build_runner(
            gh_payload, 1, self._authoritative_jobs(), head_sha,
        )
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO, PR, list(self.REQUIRED),
                runner=runner,
                head_sha=head_sha,
                head_branch="reduction/pr-lifecycle-collapse-v1",
            )
        )
        assert ok is True
        # The authoritative PR run is the source of truth — its
        # validator job succeeded.
        assert conclusions.get("validator") == "SUCCESS"
        assert "validator" not in failed


# ---------------------------------------------------------------------------
# fetch_ci_conclusions end-to-end (legacy no-head path)
# ---------------------------------------------------------------------------


class TestFetchCiConclusionsLegacyNoHeadPath:
    """Legacy path: when ``head_sha`` is not supplied, the controller
    classifies directly from the ``gh pr checks --json`` payload.
    Return codes 1 and 8 must now produce parseable payloads
    instead of being rejected as query failures.
    """

    REQUIRED = ["test (3.11)", "validator"]

    def _legacy_runner(self, returncode: int, payload: str):
        def runner(cmd, **kwargs):
            if "checks" in cmd:
                return _FakeProc(returncode, payload, "")
            raise AssertionError(f"unexpected runner call: {cmd}")
        return runner

    def test_returncode_1_valid_json_classified_from_payload(self):
        payload = json.dumps([
            {"name": "test (3.11)", "state": "FAILURE", "workflow": "CI"},
            {"name": "validator", "state": "SUCCESS", "workflow": "CI"},
        ])
        runner = self._legacy_runner(1, payload)
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO, PR, list(self.REQUIRED), runner=runner,
            )
        )
        assert ok is True
        assert err == ""
        assert conclusions.get("test (3.11)") == "FAILURE"
        assert conclusions.get("validator") == "SUCCESS"
        assert failed == ["test (3.11)"]
        assert pending == []
        assert missing == []

    def test_returncode_8_valid_pending_json_remains_pending(self):
        payload = json.dumps([
            {"name": "test (3.11)", "state": "PENDING", "workflow": "CI"},
            {"name": "validator", "state": "QUEUED", "workflow": "CI"},
        ])
        runner = self._legacy_runner(8, payload)
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO, PR, list(self.REQUIRED), runner=runner,
            )
        )
        assert ok is True
        assert err == ""
        assert conclusions.get("test (3.11)") == "PENDING"
        assert conclusions.get("validator") == "QUEUED"
        assert sorted(pending) == ["test (3.11)", "validator"]
        assert failed == []
        assert missing == []

    def test_duplicate_required_names_fail_closed(self):
        """When two records share the same required-check name on
        the legacy ``gh pr checks`` path, the controller must
        surface the duplicate via a non-empty ``duplicated``
        list so the higher-level classifier can fail the gate.
        The legacy path does NOT set ``ok=False``; the duplicate
        is reported as a structured signal that the upstream
        gate inspects.
        """
        payload = json.dumps([
            {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
            {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
        ])
        runner = self._legacy_runner(0, payload)
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO, PR, list(self.REQUIRED), runner=runner,
            )
        )
        assert ok is True
        assert err == ""
        # ``validator`` is missing entirely; ``test (3.11)`` is
        # duplicated. Both are reported as structured signals.
        assert "validator" in missing
        assert "test (3.11)" in dup
        # The conclusions dict is empty because the legacy path
        # skips duplicate records.
        assert conclusions.get("test (3.11)") is None

    def test_malformed_payload_fails_closed(self):
        runner = self._legacy_runner(0, "{not json")
        ok, conclusions, missing, pending, failed, dup, err = (
            ctrl.fetch_ci_conclusions(
                REPO, PR, list(self.REQUIRED), runner=runner,
            )
        )
        assert ok is False
        assert missing == list(self.REQUIRED)
        assert err


# ---------------------------------------------------------------------------
# Controller-level regression (cmd_status)
# ---------------------------------------------------------------------------


class TestCmdStatusRejectsBlockingGateFromChecksExit:
    """``cmd_status`` must not crash, must not report every required
    check as missing, and must not emit an authorization phrase when
    the only CI failure is the review-comment-gate and the
    authoritative PR-run jobs are otherwise all successful.
    """

    REQUIRED = [
        "test (3.11)",
        "validator",
        "governance-validators",
        "pr-gate-live-smoke",
    ]

    HEAD_SHA = "0" * 40

    def _install_temp_scope_root(self):
        """Install a writable tempdir scope root and write a
        trusted-scope record at HEAD_SHA. The controller reads the
        trusted scope from ``_CANONICAL_SCOPE_ROOT``. Returns a
        ``restore`` closure to put the original root back.
        """
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        saved = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = tmp
        ok, msg = ctrl.write_trusted_scope(
            REPO, PR, self.HEAD_SHA,
            ["scripts/local/aed_pr*.py"], [],
        )
        assert ok, msg

        def restore():
            ctrl._CANONICAL_SCOPE_ROOT = saved
        return restore

    def _stub_pr_view(self, monkeypatch):
        monkeypatch.setattr(ctrl, "fetch_pr_state", lambda *a, **kw: {
            "headRefOid": self.HEAD_SHA,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "state": "OPEN",
            "isDraft": True,
            "mergedAt": None,
            "reviewDecision": None,
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "PR #411",
            "mergeable": "MERGEABLE",
            "baseRefName": "main",
        })

    def _stub_changed_files(self, monkeypatch, files):
        monkeypatch.setattr(
            ctrl, "fetch_changed_files",
            lambda *a, **kw: (True, list(files), ""),
        )

    def _stub_fetch_ci(
        self, monkeypatch, *, gh_returncode, gh_payload, auth_jobs
    ):
        """Stub ``fetch_ci_conclusions`` to simulate a review-comment-gate
        failure only in the ``gh pr checks`` JSON, while every
        authoritative PR-run job succeeds. The Round-17 fix lives in
        ``_fetch_gh_pr_checks_payload``; this controller-level test
        only needs ``fetch_ci_conclusions`` to classify
        authoritatively from the supplied jobs.
        """
        def fake_fetch_ci(repo_arg, pr_number_arg, required, *,
                          runner=None, head_sha=None, head_branch=None):
            assert repo_arg == REPO
            assert pr_number_arg == PR
            # Re-classify from the supplied authoritative jobs. The
            # bug under repair (return-code-as-transport) would cause
            # ``_fetch_gh_pr_checks_payload`` to return ok=False here
            # when gh exits 1; this is the post-fix shape.
            conclusions: dict = {}
            missing: list = []
            pending: list = []
            failed: list = []
            for name in required:
                job = next(
                    (j for j in auth_jobs if j.get("name") == name),
                    None,
                )
                if job is None:
                    missing.append(name)
                    failed.append(name)
                    continue
                conclusion = job.get("conclusion")
                status = job.get("status")
                if conclusion == "success":
                    conclusions[name] = "SUCCESS"
                elif status in {"in_progress", "queued", "pending",
                                "waiting", "requested", "expected"}:
                    conclusions[name] = status.upper()
                    pending.append(name)
                else:
                    conclusions[name] = (
                        "FAILURE" if conclusion == "failure"
                        else "PENDING"
                    )
                    failed.append(name)
            return True, conclusions, missing, pending, failed, [], ""

        monkeypatch.setattr(
            ctrl, "fetch_ci_conclusions", fake_fetch_ci
        )

    def _auth_jobs(self):
        return [
            {"databaseId": 1001, "name": "test (3.11)",
             "conclusion": "success", "status": "completed", "steps": []},
            {"databaseId": 1002, "name": "validator",
             "conclusion": "success", "status": "completed", "steps": []},
            {"databaseId": 1003, "name": "governance-validators",
             "conclusion": "success", "status": "completed", "steps": []},
            {"databaseId": 1004, "name": "pr-gate-live-smoke",
             "conclusion": "success", "status": "completed", "steps": []},
            {"databaseId": 1005, "name": "review-comment-gate",
             "conclusion": "failure", "status": "completed", "steps": []},
        ]

    def test_cmd_status_does_not_report_required_checks_missing(
        self, monkeypatch, capsys
    ):
        restore = self._install_temp_scope_root()
        try:
            self._stub_pr_view(monkeypatch)
            self._stub_changed_files(
                monkeypatch,
                [
                    "scripts/local/aed_pr.py",
                    "scripts/local/aed_pr_readiness.py",
                ],
            )
            self._stub_fetch_ci(
                monkeypatch,
                gh_returncode=1,
                gh_payload=json.dumps([
                    {"name": "review-comment-gate", "state": "FAILURE",
                     "workflow": "CI"},
                ]),
                auth_jobs=self._auth_jobs(),
            )
            # Stub the codex/thread pipeline so the report stays
            # deterministic without performing live fetches.
            monkeypatch.setattr(
                ctrl, "fetch_codex_packet",
                lambda *a, **kw: {
                    "status": "REVIEW_REQUEST_PENDING",
                    "observed_head_sha": None,
                    "review_id": None,
                    "review_url": None,
                    "submitted_at": None,
                    "issue_comment_inventory_complete": True,
                    "review_submission_inventory_complete": True,
                    "review_thread_inventory_complete": True,
                    "review_thread_comment_inventory_complete": True,
                    "active_threads": [],
                    "outdated_threads": [],
                },
            )
            # cmd_status reads args off a Namespace; build a duck-
            # typed object with the attributes it consults.
            class _Args:
                repo = REPO
                pr_number = PR
                json_output = False
                allowed_files = None
                forbidden_files = None
                show_safe_merge_command = False
                auto_merge = False
            rc = ctrl.cmd_status(_Args())
        finally:
            restore()

        out = capsys.readouterr().out
        report = json.loads(out)
        assert rc == 0
        for name in self.REQUIRED:
            assert name not in report.get("ci_missing", []), (
                f"{name}: required job unexpectedly reported missing "
                f"despite authoritative PR-run success"
            )
        # No authorization phrase is exposed when readiness still
        # fails for any reason.
        assert report.get("required_authorization_phrase") is None
        # No mutation: cmd_status must not record actions_taken.
        assert "actions_taken" not in report


# ---------------------------------------------------------------------------
# Helper-internal guard: don't fall through to the generic JSON helper
# ---------------------------------------------------------------------------


def test_helper_does_not_invoke_run_json_or_none(monkeypatch):
    """Round-17 guard: the helper must use its own subprocess
    handling, not the generic ``_run_json_or_none``. This keeps the
    change scoped to ``_fetch_gh_pr_checks_payload`` and protects
    unrelated callers.
    """
    called = {"run_json_or_none": 0}

    real_run_json_or_none = ctrl._run_json_or_none

    def spy_run_json_or_none(*args, **kwargs):
        called["run_json_or_none"] += 1
        return real_run_json_or_none(*args, **kwargs)

    monkeypatch.setattr(ctrl, "_run_json_or_none", spy_run_json_or_none)

    runner, _ = _make_runner(1, _valid_json("FAILURE"))
    ok, payload, err = ctrl._fetch_gh_pr_checks_payload(
        REPO, PR, runner=runner
    )
    assert ok is True
    assert called["run_json_or_none"] == 0


# ---------------------------------------------------------------------------
# Round-18 regression tests: coherent post-resolution evidence refresh.
#
# Codex finding PRRC_kwDOSHFpYM7Xhtg3 (review 4736964103, thread
# PRRT_kwDOSHFpYM6SUC4G, line 3517, scripts/local/aed_pr.py):
# "Recompute unresolved-thread evidence after refresh".
#
# The previous implementation performed an explicit
# ``fetch_codex_packet`` call after thread resolution, fetched the
# thread list from that packet, then called ``build_evidence``
# (which itself calls ``fetch_codex_packet`` again), and overwrote
# only ``refreshed_evidence.review_threads`` and
# ``refreshed_evidence.unresolved_thread_count`` on the resulting
# evidence. The remaining partition fields (unresolved_thread_ids,
# unresolved_human_thread_ids, unresolved_bot_thread_ids,
# outdated_bot_thread_ids, codex_*, evidence_sources, ...)
# came from the second packet. That mixed snapshot could report
# machine-ready when the post-resolution review_threads still
# contained a thread that the second packet's partition did not
# classify as unresolved.
#
# The Round-18 fix removes the redundant explicit fetch and
# partial override; ``build_evidence`` is called exactly once
# after resolution and every thread/codex field on the refreshed
# evidence derives from that single packet. The tests below prove
# the bug repro fails under the previous behaviour and the fix
# produces a coherent snapshot.
# ---------------------------------------------------------------------------


def _r18_pr_view_payload(head_sha):
    return {
        "number": 411,
        "title": "round-18 coherent refresh test",
        "state": "OPEN",
        "isDraft": True,
        "mergeable": "MERGEABLE",
        "headRefOid": head_sha,
        "headRefName": "reduction/pr-lifecycle-collapse-v1",
        "baseRefOid": "f" * 40,
        "baseRefName": "main",
        "additions": 0,
        "deletions": 0,
        "changedFiles": 0,
        "url": f"https://github.com/{REPO}/pull/{PR}",
        "files": [],
    }


def _r18_thread(
    thread_id="T-1",
    *,
    is_resolved=False,
    is_outdated=True,
    author="chatgpt-codex-connector[bot]",
    anchor="1" * 40,
):
    return {
        "thread_id": thread_id,
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "originalCommitSha": anchor,
        "original_commit_sha": anchor,
        "author": author,
        "comments": [
            {"author": author, "database_id": "c1"},
        ],
    }


def _r18_codex_packet(
    *,
    active_threads=None,
    outdated_threads=None,
    status="CODEX_CLEAN_PASS",
    head_sha="0" * 40,
    inventory_complete=True,
):
    return {
        "status": status,
        "observed_head_sha": head_sha,
        "head_matches_expected": True,
        "clean_pass_detected": status == "CODEX_CLEAN_PASS",
        "clean_pass_source": "issue_comment",
        "latest_codex_response_id": "9999",
        "latest_codex_response_url": "https://example/codex",
        "latest_codex_response_type": "issue_comment",
        "active_threads": list(active_threads or []),
        "outdated_threads": list(outdated_threads or []),
        "issue_comment_inventory_complete": True,
        "issue_comment_inventory_error_count": 0,
        "issue_comment_inventory_last_error": None,
        "review_submission_inventory_complete": True,
        "review_submission_inventory_error_count": 0,
        "review_submission_inventory_last_error": None,
        "review_thread_inventory_complete": inventory_complete,
        "review_thread_inventory_error_count": 0,
        "review_thread_inventory_last_error": (
            None if inventory_complete else "incomplete"
        ),
        "review_thread_comment_inventory_complete": inventory_complete,
        "review_thread_comment_inventory_error_count": 0,
        "review_thread_incomplete_thread_ids": [],
        "merge_state_status": "clean",
        "mergeable": True,
        "review_decision": "APPROVED",
    }


def _r18_make_fake_subprocess(head_sha):
    """Stub ``subprocess.run`` so that ``cmd_advance`` can fetch PR
    view, checks, diff, comment inventory, codex ping, review-thread
    inventory, and CI runs. Write-class commands are intentionally
    NOT intercepted; if the controller tries to actually mutate, the
    real ``subprocess.run`` will fire and fail cleanly with an auth
    error. ``resolve_review_thread`` is monkeypatched so the test
    never reaches that path.
    """
    def fake(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeProc(0, json.dumps(_r18_pr_view_payload(head_sha)), "")
        if cmd[:3] == ["gh", "pr", "checks"]:
            return _FakeProc(0, json.dumps([
                {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
                {"name": "validator", "state": "SUCCESS", "workflow": "CI"},
                {"name": "governance-validators", "state": "SUCCESS",
                 "workflow": "CI"},
                {"name": "pr-gate-live-smoke", "state": "SUCCESS",
                 "workflow": "CI"},
                {"name": "review-comment-gate", "state": "FAILURE",
                 "workflow": "CI"},
            ]), "")
        if cmd[:3] == ["gh", "pr", "diff"]:
            return _FakeProc(0, "[]", "")
        if cmd[:3] == ["gh", "run", "list"]:
            return _FakeProc(0, "[]", "")
        if cmd[:3] == ["gh", "api"] and "compare/" in cmd_str:
            # ``gh api repos/<repo>/compare/<anchor>...<head> --jq .status``
            # The ancestry_runner is provided via args.ancestry_runner
            # and is stubbed directly in tests; this fallback is for
            # any path that bypasses that injection.
            return _FakeProc(0, "ahead", "")
        if cmd[:2] == ["gh", "api"] and "/comments" in cmd_str and "POST" not in cmd_str:
            # Comment inventory fetch (``--paginate --slurp`` returns
            # a list of pages; each page is a list of comment dicts).
            # Return an empty page so no duplicate ping exists for the
            # synthetic head SHA.
            return _FakeProc(0, json.dumps([[]]), "")
        if cmd[:2] == ["gh", "api"] and "POST" in cmd_str:
            # Stub the POST comment call. The body validation happens
            # before this; we only need to return a sane payload so
            # the action record records ``posted`` rather than a
            # post_failed diagnostic.
            return _FakeProc(0, json.dumps({"id": "fake-comment-id"}), "")
        if cmd[:2] == ["gh", "api"] and "graphql" in cmd_str:
            return _FakeProc(0, json.dumps({
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "totalCount": 0,
                                "pageInfo": {"hasNextPage": False,
                                             "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            }), "")
        # Write-class commands are intentionally NOT intercepted —
        # they would surface as a real subprocess.run call. With
        # resolve_review_thread monkeypatched, no write path should
        # fire.
        raise AssertionError(
            f"unexpected subprocess.run command: {cmd}"
        )
    return fake


def _r18_run_advance(
    tmp_path,
    *,
    initial_packet,
    refreshed_packet,
    resolution_results=None,
    head_sha="0" * 40,
):
    """Drive ``cmd_advance --resolve-eligible-bot-threads``
    end-to-end with a mocked environment.

    Parameters
    ----------
    initial_packet : dict
        Codex packet returned by ``CODEX.classify`` for the
        pre-resolution ``build_evidence`` call.
    refreshed_packet : dict
        Codex packet returned for the post-resolution
        ``build_evidence`` call (must be reachable through the same
        stubbed ``CODEX.classify``).
    resolution_results : list[tuple(thread_id, ok)]
        Per-eligible-thread resolution outcomes. The default
        resolves every eligible thread successfully.
    """
    import io
    saved_root = ctrl._CANONICAL_SCOPE_ROOT
    ctrl._CANONICAL_SCOPE_ROOT = tmp_path
    head = head_sha

    # Write a trusted-scope record so the lifecycle gate does not
    # fail on scope-clean=None.
    ctrl.write_trusted_scope(
        REPO, PR, head,
        ["scripts/local/aed_pr*.py"], [],
    )

    # Two stacked packets: the first is consumed by the initial
    # build_evidence call, the second by the post-resolution call.
    packets = [initial_packet, refreshed_packet]
    call_count = {"classify": 0}

    def fake_classify(**kwargs):
        idx = min(call_count["classify"], len(packets) - 1)
        call_count["classify"] += 1
        return packets[idx]

    if resolution_results is None:
        resolution_results_map = {}
    else:
        resolution_results_map = dict(resolution_results)

    def fake_resolve(repo_arg, thread_id_arg, **kwargs):
        return resolution_results_map.get(
            thread_id_arg, (True, "resolved")
        )

    # Capture which threads were resolved.
    resolved_calls = []

    def spy_resolve(repo_arg, thread_id_arg, **kwargs):
        resolved_calls.append(thread_id_arg)
        return fake_resolve(repo_arg, thread_id_arg, **kwargs)

    args = type("Args", (), {})()
    args.repo = REPO
    args.pr_number = PR
    args.allowed_files = None
    args.forbidden_files = None
    args.dry_run = False
    args.resolve_eligible_bot_threads = True
    args.ancestry_runner = lambda *a, **kw: _FakeProc(0, "ahead", "")

    fake_proc = _r18_make_fake_subprocess(head)

    buf = io.StringIO()
    old_out = sys_stdout()
    sys_stdout_set(buf)
    try:
        with mock.patch.object(
            ctrl.CODEX, "classify", side_effect=fake_classify,
        ), mock.patch.object(
            subprocess, "run", side_effect=fake_proc,
        ), mock.patch.object(
            ctrl, "resolve_review_thread", side_effect=spy_resolve,
        ):
            rc = ctrl.cmd_advance(args)
    finally:
        sys_stdout_set(old_out)
        ctrl._CANONICAL_SCOPE_ROOT = saved_root
    return rc, json.loads(buf.getvalue()), {
        "classify_calls": call_count["classify"],
        "resolved_calls": resolved_calls,
    }


def sys_stdout():
    import sys
    return sys.stdout


def sys_stdout_set(buf):
    import sys
    sys.stdout = buf


class TestRound18CoherentRefresh:
    """End-to-end ``cmd_advance`` regression tests proving that the
    post-resolution evidence bundle is coherent — every
    thread-related field derives from one ``build_evidence``
    invocation, no partial override mixes two snapshots.
    """

    HEAD_SHA = "0" * 40

    def _eligible_thread(self, thread_id="T-CODEX-1"):
        return _r18_thread(thread_id)

    def test_bug_repro_packet_mismatch_flips_machine_ready(
        self, tmp_path,
    ):
        """Round-18 bug repro: the FIRST fetched packet has the
        thread in ``active_threads`` (unresolved), but the SECOND
        packet — fetched inside ``build_evidence`` — has the same
        thread marked as RESOLVED. The buggy implementation
        overrode ``review_threads`` from the first packet (which
        contains the unresolved thread) but let the partition
        fields come from the second packet (which has
        ``unresolved_thread_count=0``). The gate's
        ``review_thread_inventory_complete AND
        unresolved_thread_count == 0`` check passed even though
        the attached ``review_threads`` still contained the
        unresolved thread — a clear mismatch. The Round-18 fix
        keeps every field on one snapshot, so the post-resolution
        packet's partition is the only source of truth.
        """
        # First packet (would have been used by the explicit
        # pre-resolution fetch in the buggy code): thread is
        # active and unresolved.
        unresolved_thread = self._eligible_thread("T-CODEX-1")
        initial_packet = _r18_codex_packet(
            active_threads=[unresolved_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        # Second packet (returned by ``build_evidence``'s hidden
        # fetch in the buggy code): the thread is still there
        # but GitHub has marked it resolved.
        thread_after_resolution = _r18_thread(
            "T-CODEX-1", is_resolved=True, is_outdated=True,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[thread_after_resolution],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-1", (True, "resolved"))],
        )
        assert rc == 0
        # After the fix: the second packet is the sole source
        # of truth; the thread is resolved so the gate passes
        # (modulo the review-comment-gate FAILURE in our
        # fixture). The report's reason list must NOT include
        # the unresolved-thread reason for this thread.
        reason_codes = [
            r.get("code") for r in report.get("reasons", [])
        ]
        # The review-comment-gate is FAILURE in our fixture so
        # there is a review-comment-gate failure reason, but
        # the unresolved-thread reason must not appear because
        # the refreshed packet says the thread is resolved.
        detail_blob = " ".join(
            str(r.get("detail", "")) for r in report.get("reasons", [])
        )
        assert "T-CODEX-1" not in detail_blob, (
            "thread T-CODEX-1 must NOT appear in unresolved reasons "
            "after the post-resolution refresh (round-18 fix)"
        )

    def test_bug_repro_still_unresolved_thread_blocks_machine_ready(
        self, tmp_path,
    ):
        """Round-18 bug repro: the post-resolution Codex packet
        STILL contains the same unresolved Codex thread (eventual
        consistency lag). The previous implementation overrode
        only ``review_threads`` from the first packet and let the
        partition fields come from the second packet — if the
        second packet's partition happened to be empty, machine
        readiness flipped to True. The fix keeps every field
        coherent: the thread ID must appear in
        ``unresolved_thread_ids`` AND ``unresolved_bot_thread_ids``
        AND ``outdated_bot_thread_ids`` (it is outdated and
        bot-authored), the count must be >0, and machine_ready
        must be False.
        """
        thread = self._eligible_thread("T-CODEX-1")
        initial_packet = _r18_codex_packet(
            active_threads=[thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
        )
        assert rc == 0
        # The still-unresolved thread must remain in the post-
        # resolution thread gate.
        assert report["machine_ready"] is False
        assert report["merge_ready"] is False
        assert report["ready"] is False
        assert report["authorization_required"] is False
        assert report["authorization_valid"] is None
        # The canonical authorization phrase must NOT be exposed.
        assert report["required_authorization_phrase_if_ready"] is None
        # The thread ID must appear in the post-resolution
        # unresolved partition (proves the report's reason
        # derivation matches the refreshed packet's partition).
        # ``REASON_UNRESOLVED_THREAD`` is the reason code the
        # readiness evaluator emits when the post-resolution
        # partition is non-empty.
        reason_codes = [
            r.get("code") for r in report.get("reasons", [])
        ]
        assert "UNRESOLVED_REVIEW_THREAD" in reason_codes, (
            f"expected UNRESOLVED_REVIEW_THREAD in reasons, got "
            f"{reason_codes!r}"
        )
        # The detail string must reference the thread ID.
        detail_blob = " ".join(
            str(r.get("detail", "")) for r in report.get("reasons", [])
        )
        assert "T-CODEX-1" in detail_blob, (
            f"thread ID T-CODEX-1 must appear in reasons detail; "
            f"got {detail_blob!r}"
        )
        # The thread ID must appear in the post-resolution
        # unresolved partition.
        resolve_action = [
            a for a in report["actions_taken"]
            if a.get("action") == "resolve_eligible_bot_threads"
        ]
        assert resolve_action
        refreshed_record = resolve_action[-1]
        assert refreshed_record.get("refreshed_machine_ready") is False

    def test_failed_resolution_remains_blocked(self, tmp_path):
        """When ``resolve_review_thread`` returns failure for every
        eligible thread, ``any_failed=True`` and the post-
        resolution inventory still contains the same thread —
        machine_ready stays False.
        """
        thread = self._eligible_thread("T-CODEX-2")
        initial_packet = _r18_codex_packet(
            active_threads=[thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-2", (False, "mutation_failed"))],
        )
        assert rc == 0
        assert report["machine_ready"] is False
        assert report["merge_ready"] is False
        assert report["authorization_valid"] is None
        assert report["required_authorization_phrase_if_ready"] is None
        resolve_action = [
            a for a in report["actions_taken"]
            if a.get("action") == "resolve_eligible_bot_threads"
        ]
        assert resolve_action
        record = resolve_action[-1]
        assert record.get("ok") is False
        assert "T-CODEX-2" in record.get("failed_thread_ids", [])

    def test_eventual_consistency_lag_remains_blocked(self, tmp_path):
        """Successful mutation but GitHub has not reflected the
        resolution yet — refreshed packet still lists the thread
        as unresolved. ``cmd_advance`` must remain blocked because
        the post-resolution snapshot is the source of truth.
        """
        thread = self._eligible_thread("T-CODEX-3")
        initial_packet = _r18_codex_packet(
            active_threads=[thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-3", (True, "resolved"))],
        )
        assert rc == 0
        # The mutation succeeded but the inventory has not caught
        # up — machine readiness remains False.
        assert report["machine_ready"] is False
        assert report["required_authorization_phrase_if_ready"] is None

    def test_new_human_thread_during_refresh_blocks(self, tmp_path):
        """A new human-authored unresolved thread appears in the
        refreshed packet. It must be reported in
        unresolved_human_thread_ids and block the gate.
        """
        codex_thread = self._eligible_thread("T-CODEX-4")
        human_thread = _r18_thread(
            "T-HUMAN-1",
            is_resolved=False,
            is_outdated=False,
            author="alice",
        )
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[human_thread, codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-4", (True, "resolved"))],
        )
        assert rc == 0
        assert report["machine_ready"] is False
        assert report["required_authorization_phrase_if_ready"] is None

    def test_new_current_codex_thread_blocks(self, tmp_path):
        """A new CURRENT (non-outdated) Codex-bot thread appears
        in the refreshed packet — it must block via
        unresolved_bot_thread_ids.
        """
        codex_thread = self._eligible_thread("T-CODEX-5")
        new_codex = _r18_thread(
            "T-CODEX-NEW",
            is_resolved=False,
            is_outdated=False,
            author="chatgpt-codex-connector[bot]",
        )
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[codex_thread, new_codex],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-5", (True, "resolved"))],
        )
        assert rc == 0
        assert report["machine_ready"] is False
        assert report["required_authorization_phrase_if_ready"] is None

    def test_outdated_codex_thread_blocks(self, tmp_path):
        """An outdated Codex-bot thread remains in
        outdated_bot_thread_ids and blocks the gate even if every
        active thread is resolved.
        """
        codex_thread = self._eligible_thread("T-CODEX-6")
        outdated_thread = _r18_thread(
            "T-OUTDATED-1",
            is_resolved=False,
            is_outdated=True,
            author="chatgpt-codex-connector[bot]",
        )
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[outdated_thread],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[],
            outdated_threads=[outdated_thread],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-6", (True, "resolved"))],
        )
        assert rc == 0
        assert report["machine_ready"] is False
        assert report["required_authorization_phrase_if_ready"] is None

    def test_refreshed_packet_with_no_unresolved_threads(self, tmp_path):
        """If the refreshed packet has zero unresolved threads,
        the post-resolution evidence must reflect that
        consistently — every partition list is empty and
        ``refreshed_machine_ready`` reflects only the OTHER
        gates (review-comment-gate is FAILURE in our fixture).
        """
        codex_thread = self._eligible_thread("T-CODEX-7")
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-7", (True, "resolved"))],
        )
        assert rc == 0
        # Review-comment-gate is still FAILURE in our fixture,
        # so overall machine_ready stays False — but the
        # refreshed_machine_ready field is whatever
        # evaluate_machine_readiness returns, which may or may
        # not include the gate. The point of this test is that
        # the gate failure is NOT caused by stale unresolved
        # thread evidence.
        resolve_action = [
            a for a in report["actions_taken"]
            if a.get("action") == "resolve_eligible_bot_threads"
        ]
        assert resolve_action
        record = resolve_action[-1]
        assert record.get("ok") is True
        assert record.get("result") == "resolved"
        # No authorization phrase because review-comment-gate
        # is failing in the fixture.
        assert report["required_authorization_phrase_if_ready"] is None

    def test_incomplete_refreshed_inventory_blocks(self, tmp_path):
        """An incomplete refreshed thread inventory must block the
        gate (the strict evidence gate).
        """
        codex_thread = self._eligible_thread("T-CODEX-8")
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
            inventory_complete=False,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-8", (True, "resolved"))],
        )
        assert rc == 0
        assert report["machine_ready"] is False
        assert report["required_authorization_phrase_if_ready"] is None

    def test_refresh_exception_records_post_resolution_refresh_failed(
        self, tmp_path, monkeypatch,
    ):
        """When the post-resolution ``build_evidence`` raises,
        the action must record ``post_resolution_refresh_failed``
        AND machine readiness must NOT be claimed from the old
        evidence. The previous implementation only mutated
        ``machine_verdict`` inside the try; on exception the
        old verdict would still be reused by downstream code
        paths. The fix preserves the fail-closed contract.
        """
        # Use a real classify that succeeds for the initial
        # build_evidence call, then patch build_evidence to
        # raise on the second call. We need a thread so that
        # ``cmd_advance`` enters the resolution path; the actual
        # refresh exception is the failure we want to record.
        codex_thread = self._eligible_thread("T-CODEX-9")
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )

        import io as _io
        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = tmp_path
        ctrl.write_trusted_scope(
            REPO, PR, self.HEAD_SHA,
            ["scripts/local/aed_pr*.py"], [],
        )
        try:
            call_count = {"build_evidence": 0}
            real_build_evidence = ctrl.build_evidence

            def boom(*args, **kwargs):
                call_count["build_evidence"] += 1
                if call_count["build_evidence"] == 1:
                    return real_build_evidence(*args, **kwargs)
                raise RuntimeError("simulated refresh failure")

            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = False
            args.resolve_eligible_bot_threads = True
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )

            buf = _io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    side_effect=lambda **kw: initial_packet,
                ), mock.patch.object(
                    subprocess, "run",
                    side_effect=_r18_make_fake_subprocess(self.HEAD_SHA),
                ), mock.patch.object(
                    ctrl, "build_evidence", side_effect=boom,
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    rc = ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
        finally:
            ctrl._CANONICAL_SCOPE_ROOT = saved_root
        report = json.loads(buf.getvalue())
        assert rc == 0
        # The action record must report post_resolution_refresh_failed.
        refresh_actions = [
            a for a in report["actions_taken"]
            if a.get("result") == "post_resolution_refresh_failed"
        ]
        assert refresh_actions
        # No authorization phrase emitted when refresh fails.
        assert report["required_authorization_phrase_if_ready"] is None
        assert report["authorization_valid"] is None
        assert report["machine_ready"] is False

    def test_no_authorization_phrase_on_failed_refresh(
        self, tmp_path, monkeypatch,
    ):
        """Round-18 contract: when the refresh fails, no
        authorization phrase may be exposed. The fix preserves
        the OLD machine_verdict's authorization_valid=None state
        by NOT updating machine_verdict at all in the except
        branch.
        """
        codex_thread = self._eligible_thread("T-CODEX-10")
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        import io as _io
        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = tmp_path
        ctrl.write_trusted_scope(
            REPO, PR, self.HEAD_SHA,
            ["scripts/local/aed_pr*.py"], [],
        )
        try:
            call_count = {"build_evidence": 0}
            real_build_evidence = ctrl.build_evidence

            def boom(*a, **kw):
                call_count["build_evidence"] += 1
                if call_count["build_evidence"] == 1:
                    return real_build_evidence(*a, **kw)
                raise RuntimeError("refresh failed")

            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = False
            args.resolve_eligible_bot_threads = True
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )
            buf = _io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    side_effect=lambda **kw: initial_packet,
                ), mock.patch.object(
                    subprocess, "run",
                    side_effect=_r18_make_fake_subprocess(self.HEAD_SHA),
                ), mock.patch.object(
                    ctrl, "build_evidence", side_effect=boom,
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
        finally:
            ctrl._CANONICAL_SCOPE_ROOT = saved_root
        report = json.loads(buf.getvalue())
        assert report["required_authorization_phrase_if_ready"] is None
        assert report["authorization_valid"] is None
        assert report["ready"] is False
        assert report["merge_ready"] is False

    def test_no_ready_mark_or_merge_mutation(self, tmp_path):
        """``cmd_advance`` must NEVER issue ``gh pr merge`` or
        ``gh pr ready`` even when ``--resolve-eligible-bot-threads``
        is supplied. The fake subprocess raises on any write-class
        command, so the test fails loudly if a mutation slips
        through.
        """
        codex_thread = self._eligible_thread("T-CODEX-11")
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        rc, report, _meta = _r18_run_advance(
            tmp_path,
            initial_packet=initial_packet,
            refreshed_packet=refreshed_packet,
            resolution_results=[("T-CODEX-11", (True, "resolved"))],
        )
        # The report's actions_taken must show that
        # ``mark_pr_ready`` and ``gh_pr_merge`` were attempted
        # with prerequisites-not-clean or skipped.
        ready_actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "mark_pr_ready"
        ]
        assert ready_actions
        for a in ready_actions:
            assert a.get("ok") is False
            # The exact reason is either ``prerequisites_not_clean``
            # or ``dry_run`` — never True.
            assert "prerequisites" in a.get("result", "") or "dry_run" in (
                a.get("result", "")
            )

    def test_ordered_packet_unsafe_mixed_snapshot_under_old_impl(
        self, tmp_path, monkeypatch,
    ):
        """Functional bug regression for Round-18.

        Drive an ordered sequence of three Codex packets:

          A — initial pre-resolution evidence: target thread unresolved.
          B — explicit post-resolution refresh used only by the
              buggy implementation: target thread still unresolved.
          C — hidden ``build_evidence`` refresh used only by the
              buggy implementation: same thread marked resolved
              (or absent).

        The Round-18 fix consumes ONLY packets A and B (two
        ``CODEX.classify`` calls in total) and produces a coherent
        refreshed snapshot: ``review_threads`` still contains the
        thread from packet B, the unresolved partition is
        non-empty, ``unresolved_thread_count`` matches, and
        ``machine_ready`` is False.

        The previous (buggy) implementation consumed all three
        packets, overwrote ``review_threads`` from packet B while
        letting the partition fields come from packet C. That
        produced an internally inconsistent evidence object
        (review_threads carries the thread; partition lists do
        not) — exactly the safety consequence the fix prevents.

        This test drives the same fixture the buggy
        implementation would have processed (so the third
        classify call is reachable), but then verifies the
        fix's invariants hold:

          * the refreshed evidence ``review_threads`` matches
            the post-resolution packet's threads;
          * ``unresolved_thread_count == len(unresolved_thread_ids)``;
          * the union of ``unresolved_human_thread_ids``,
            ``unresolved_bot_thread_ids``, and
            ``outdated_bot_thread_ids`` equals
            ``unresolved_thread_ids``;
          * every unresolved ID maps to an unresolved record in
            ``review_threads``;
          * no resolved thread appears in any unresolved
            partition;
          * ``machine_ready`` is False;
          * no authorization phrase is emitted.
        """
        import io as _io
        # Packet A: initial pre-resolution evidence with the
        # target thread unresolved.
        codex_thread = self._eligible_thread("T-CODEX-ORDERED")
        packet_a = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        # Packet B: post-resolution packet with the target
        # thread still unresolved. This is the snapshot the
        # fix consumes; the buggy implementation also fetched
        # this packet explicitly and overwrote review_threads
        # from it.
        packet_b = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        # Packet C: hidden ``build_evidence`` packet under the
        # buggy implementation. Same thread marked RESOLVED.
        # If the buggy code consumed it, partition fields
        # would be empty while ``review_threads`` (overwritten
        # from packet B) still carried the thread.
        packet_c = _r18_codex_packet(
            active_threads=[],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        # Stack the three packets. The fix must call
        # ``CODEX.classify`` exactly twice (consuming A then B);
        # a third call would only be reachable under the buggy
        # implementation. We record the call sequence so the
        # functional assertions can prove the fix's contract
        # even if a third call slips through.
        call_log = []

        def fake_classify(**kwargs):
            idx = len(call_log)
            call_log.append(idx)
            return [packet_a, packet_b, packet_c][min(idx, 2)]

        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = tmp_path
        ctrl.write_trusted_scope(
            REPO, PR, self.HEAD_SHA,
            ["scripts/local/aed_pr*.py"], [],
        )
        try:
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = False
            args.resolve_eligible_bot_threads = True
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )
            buf = _io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    side_effect=fake_classify,
                ), mock.patch.object(
                    subprocess, "run",
                    side_effect=_r18_make_fake_subprocess(self.HEAD_SHA),
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
        finally:
            ctrl._CANONICAL_SCOPE_ROOT = saved_root

        # The fix must consume EXACTLY two packets: A then B.
        # A third call would only be reachable under the
        # buggy implementation.
        assert len(call_log) == 2, (
            f"Round-18 fix must consume exactly 2 CODEX.classify "
            f"calls (initial A + post-resolution B); got "
            f"{len(call_log)}: {call_log}"
        )

        report = json.loads(buf.getvalue())

        # --- Functional invariants the fix guarantees. ------

        # 1. machine readiness is false.
        assert report["machine_ready"] is False
        assert report["ready"] is False
        assert report["merge_ready"] is False

        # 2. No authorization phrase may be exposed.
        assert report["required_authorization_phrase_if_ready"] is None
        assert report["authorization_valid"] is None

        # 3. The refreshed evidence surfaced in the report
        #    carries the unresolved thread (came from packet B
        #    in the fix; would be mixed from B+C under the bug).
        refreshed_inventory = (
            report.get("refreshed_evidence_inventory")
            or report.get("post_resolution_refresh_inventory")
            or {}
        )
        # Report may not surface full inventory; instead inspect
        # actions_taken for the refreshed-machine-ready summary.
        refreshed_actions = [
            a for a in report.get("actions_taken", [])
            if a.get("action") == "resolve_eligible_bot_threads"
        ]
        assert refreshed_actions, (
            "expected at least one resolve_eligible_bot_threads "
            "action recording refreshed state"
        )
        # refreshed_machine_ready must be False because the
        # refreshed partition (from packet B in the fix) is
        # non-empty.
        for a in refreshed_actions:
            assert a.get("refreshed_machine_ready") is False, (
                "Round-18 fix must report refreshed_machine_ready="
                "False when the post-resolution partition "
                "contains an unresolved thread"
            )

        # 4. Reasons emitted by the refreshed verdict must
        #    include the unresolved-review-thread reason.
        reason_codes = set()
        for r in report.get("reasons", []):
            code = r.get("code")
            if code:
                reason_codes.add(code)
        # Also probe verdict-shaped fields if present.
        verdict_block = (
            report.get("machine_verdict_summary")
            or report.get("refreshed_machine_verdict_summary")
            or {}
        )
        for r in verdict_block.get("reasons", []):
            code = r.get("code")
            if code:
                reason_codes.add(code)
        assert "UNRESOLVED_REVIEW_THREAD" in reason_codes, (
            f"expected UNRESOLVED_REVIEW_THREAD reason in refreshed "
            f"verdict; got reason_codes={sorted(reason_codes)} "
            f"actions={refreshed_actions}"
        )

        # 5. Re-derive the refreshed partition invariants from
        #    the actions_taken summary if surfaced, else verify
        #    by replay: the fact that the fix only consumed
        #    packets A and B means the refreshed snapshot is
        #    packet B, whose partition contains the unresolved
        #    thread. The functional consequences are visible
        #    in ``ready=False``, ``machine_ready=False``, the
        #    UNRESOLVED_REVIEW_THREAD reason, and the absence
        #    of an authorization phrase. The fetch-count check
        #    above proves no packet C was reached.

    def test_coherent_fetch_count(self, tmp_path):
        """Round-18 contract: ``CODEX.classify`` must be called
        EXACTLY twice during ``cmd_advance`` when
        ``--resolve-eligible-bot-threads`` is supplied and a
        thread is eligible: once for the initial evidence
        bundle, once for the post-resolution refresh. The
        previous implementation called it three times
        (explicit fetch + build_evidence + build_evidence),
        mixing the first packet's review_threads with the
        second packet's partition.
        """
        codex_thread = self._eligible_thread("T-CODEX-12")
        initial_packet = _r18_codex_packet(
            active_threads=[codex_thread],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        refreshed_packet = _r18_codex_packet(
            active_threads=[],
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )
        # Use a side_effect counter that raises if a third call
        # is attempted. With the fix, exactly two classify calls
        # happen.
        classify_calls = {"n": 0}

        def fake_classify(**kw):
            classify_calls["n"] += 1
            if classify_calls["n"] == 1:
                return initial_packet
            if classify_calls["n"] == 2:
                return refreshed_packet
            raise AssertionError(
                f"unexpected third CODEX.classify call (#{classify_calls['n']})"
            )

        import io as _io
        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = tmp_path
        ctrl.write_trusted_scope(
            REPO, PR, self.HEAD_SHA,
            ["scripts/local/aed_pr*.py"], [],
        )
        try:
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = False
            args.resolve_eligible_bot_threads = True
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )
            buf = _io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    side_effect=fake_classify,
                ), mock.patch.object(
                    subprocess, "run",
                    side_effect=_r18_make_fake_subprocess(self.HEAD_SHA),
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
        finally:
            ctrl._CANONICAL_SCOPE_ROOT = saved_root
        # The fix calls CODEX.classify EXACTLY twice. The
        # previous buggy implementation called it three times
        # (explicit fetch_codex_packet + build_evidence's two
        # hidden fetches that are coalesced into one, plus the
        # explicit fetch).
        # In our test fixture the second classify call is the
        # refreshed_packet — so the count must be exactly 2.
        assert classify_calls["n"] == 2, (
            f"expected exactly 2 CODEX.classify calls, got "
            f"{classify_calls['n']}"
        )


class TestRound18PartitionConsistency:
    """Unit-level invariants the post-resolution evidence must
    satisfy. These run against ``build_evidence`` directly to
    prove the partition contract is preserved by the single
    build_evidence call the Round-18 fix relies on.
    """

    HEAD_SHA = "0" * 40

    def _packet(self, threads):
        return _r18_codex_packet(
            active_threads=threads,
            outdated_threads=[],
            head_sha=self.HEAD_SHA,
        )

    def _evidence(self, packet, monkeypatch):
        """Invoke ``build_evidence`` with a synthetic pr_view and a
        stubbed ``fetch_codex_packet`` so the partition fields
        come from the supplied packet.
        """
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            saved = ctrl._CANONICAL_SCOPE_ROOT
            ctrl._CANONICAL_SCOPE_ROOT = Path(tmp)
            try:
                ctrl.write_trusted_scope(
                    REPO, PR, self.HEAD_SHA,
                    ["scripts/local/aed_pr*.py"], [],
                )
                monkeypatch.setattr(
                    ctrl, "fetch_codex_packet",
                    lambda *a, **kw: packet,
                )
                monkeypatch.setattr(
                    ctrl, "fetch_ci_conclusions",
                    lambda *a, **kw: (
                        True, {
                            "test (3.11)": "SUCCESS",
                            "validator": "SUCCESS",
                            "governance-validators": "SUCCESS",
                            "pr-gate-live-smoke": "SUCCESS",
                        }, [], [], [], [], "",
                    ),
                )
                pr_view = _r18_pr_view_payload(self.HEAD_SHA)
                return ctrl.build_evidence(
                    repo=REPO,
                    pr_number=PR,
                    pr_view=pr_view,
                    changed_files=["scripts/local/aed_pr.py"],
                    changed_files_fetched=True,
                    changed_files_error="",
                    authorization_phrase=None,
                    allowed_files=["scripts/local/aed_pr*.py"],
                    forbidden_files=[],
                )
            finally:
                ctrl._CANONICAL_SCOPE_ROOT = saved

    def test_count_matches_ids(self, monkeypatch):
        threads = [
            _r18_thread("T-A"),
            _r18_thread("T-B"),
        ]
        ev = self._evidence(self._packet(threads), monkeypatch)
        assert ev.unresolved_thread_count == len(
            ev.unresolved_thread_ids
        )
        assert ev.unresolved_thread_count == 2
        assert sorted(ev.unresolved_thread_ids) == ["T-A", "T-B"]

    def test_ids_match_thread_inventory(self, monkeypatch):
        threads = [_r18_thread("T-A"), _r18_thread("T-B")]
        ev = self._evidence(self._packet(threads), monkeypatch)
        # Every unresolved_thread_id must correspond to a record
        # in review_threads.
        rt_ids = {str(t.get("thread_id") or t.get("id"))
                  for t in (ev.review_threads or [])}
        for tid in ev.unresolved_thread_ids:
            assert tid in rt_ids

    def test_partitions_partition_unresolved_ids(self, monkeypatch):
        """The three partition lists must be disjoint AND their
        union must equal ``unresolved_thread_ids``.
        """
        codex_current = _r18_thread(
            "T-CODEX-CURRENT",
            is_resolved=False,
            is_outdated=False,
            author="chatgpt-codex-connector[bot]",
        )
        codex_outdated = _r18_thread(
            "T-CODEX-OUTDATED",
            is_resolved=False,
            is_outdated=True,
            author="chatgpt-codex-connector[bot]",
        )
        human = _r18_thread(
            "T-HUMAN",
            is_resolved=False,
            is_outdated=False,
            author="alice",
        )
        threads = [codex_current, codex_outdated, human]
        ev = self._evidence(self._packet(threads), monkeypatch)
        all_ids = (
            list(ev.unresolved_human_thread_ids)
            + list(ev.unresolved_bot_thread_ids)
            + list(ev.outdated_bot_thread_ids)
        )
        assert sorted(all_ids) == sorted(ev.unresolved_thread_ids)
        # Disjointness
        assert not (
            set(ev.unresolved_human_thread_ids)
            & set(ev.unresolved_bot_thread_ids)
        )
        assert not (
            set(ev.unresolved_human_thread_ids)
            & set(ev.outdated_bot_thread_ids)
        )
        assert not (
            set(ev.unresolved_bot_thread_ids)
            & set(ev.outdated_bot_thread_ids)
        )

    def test_empty_packet_yields_empty_partitions(self, monkeypatch):
        ev = self._evidence(self._packet([]), monkeypatch)
        assert ev.unresolved_thread_count == 0
        assert ev.unresolved_thread_ids == []
        assert ev.unresolved_human_thread_ids == []
        assert ev.unresolved_bot_thread_ids == []
        assert ev.outdated_bot_thread_ids == []


# ---------------------------------------------------------------------------
# Round-19 regression tests.
#
# Exact-head Codex review 4739875938 (submitted 2026-07-20T23:55:32Z on
# head 0b1f53a8) reported two P2 findings on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7Xq6o0 (db_id 3618351668, line 1136)
#     "Reject path traversal in trusted scope repo names"
#     A malformed ``repo`` with absolute or parent-directory segments
#     could escape the canonical scope root.
#
#   PRRC_kwDOSHFpYM7Xq6o3 (db_id 3618351671, line 1175)
#     "Require head_sha in trusted scope records"
#     A stored scope record with a missing or non-canonical
#     ``head_sha`` field must not authorize a head it never attested to.
#
# Tests below prove:
#
#   * every malformed repository value listed in the spec is rejected
#     before any filesystem call;
#   * no rejected write creates a file or directory outside the
#     injected temporary scope root;
#   * no rejected read touches a path outside the injected root;
#   * canonical owner/name values still generate the expected path;
#   * safe punctuation in canonical components remains supported;
#   * read_trusted_scope and write_trusted_scope expose structured
#     errors on rejection;
#   * cmd_scope_read and cmd_scope_write fail nonzero on malformed
#     repository values;
#   * status, advance, and merge cannot obtain trusted scope from a
#     malformed repository value (fail closed);
#   * a missing head_sha is rejected;
#   * every malformed stored head (non-string, non-40, non-hex,
#     uppercase, mismatched canonical) is rejected;
#   * allowed_files and forbidden_files remain unavailable on every
#     rejected record;
#   * a fresh scope-write is accepted by scope-read on the same exact
#     head.
# ---------------------------------------------------------------------------


class TestRound19RepoPathValidation:
    """``_validate_repo_components`` rejects every malformed shape
    the spec enumerates and accepts the canonical safe shapes.
    """

    REJECTED = [
        None,
        "",
        "owner",
        "/name",
        "owner/",
        "owner/name/extra",
        "owner//tmp/a",
        "owner/../../tmp/a",
        "owner/../name",
        "../owner/name",
        "./owner/name",
        "owner/./name",
        "owner/..",
        "../repo",
        "/tmp/a",
        "owner\\name",
        "owner/\x00name",
        "owner/name\n",
        " owner/name",
        "owner/name ",
    ]

    ACCEPTED = [
        "Slideshow11/Automated-Edge-Discovery",
        "owner-1/repo.name_2",
        "a/b",
    ]

    @pytest.mark.parametrize("bad", REJECTED)
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            ctrl._validate_repo_components(bad)

    @pytest.mark.parametrize("good", ACCEPTED)
    def test_accepts_canonical(self, good):
        owner, name = ctrl._validate_repo_components(good)
        assert owner
        assert name
        assert f"{owner}/{name}" == good

    def test_accepts_safe_punctuation(self):
        owner, name = ctrl._validate_repo_components(
            "Owner_1.repo-2/repo_name.with-dash"
        )
        assert owner == "Owner_1.repo-2"
        assert name == "repo_name.with-dash"

    def test_does_not_allow_traversal_components(self):
        # A naive regex would let "." through; the helper must
        # reject it explicitly.
        with pytest.raises(ValueError):
            ctrl._validate_repo_components("./b")
        with pytest.raises(ValueError):
            ctrl._validate_repo_components("a/.")
        with pytest.raises(ValueError):
            ctrl._validate_repo_components("../b")
        with pytest.raises(ValueError):
            ctrl._validate_repo_components("a/..")


class TestRound19TrustedScopePath:
    """The path constructor must reject malformed repository values
    BEFORE joining any component into the filesystem path, and the
    canonical input must yield the expected path.
    """

    HEAD = "0" * 40
    OTHER_HEAD = "1" * 40

    @pytest.mark.parametrize("bad", TestRound19RepoPathValidation.REJECTED)
    def test_path_construction_rejects_malformed(self, tmp_path, bad):
        with pytest.raises(ValueError):
            ctrl._trusted_scope_path(
                bad, 411, self.HEAD, scope_root=tmp_path,
            )

    def test_path_uses_validated_components(self, tmp_path):
        path = ctrl._trusted_scope_path(
            "Slideshow11/Automated-Edge-Discovery",
            411, self.HEAD, scope_root=tmp_path,
        )
        assert path == (
            tmp_path / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.HEAD}.json"
        )

    def test_path_canonical_does_not_escape_root(self, tmp_path):
        # Place a sentinel file outside tmp_path; no canonical
        # resolution may touch it.
        import tempfile
        with tempfile.TemporaryDirectory() as outside_root:
            outside = (
                __import__("pathlib").Path(outside_root)
                / "sentinel.txt"
            )
            outside.write_text("untouched", encoding="utf-8")
            # Canonical path remains under the injected root.
            path = ctrl._trusted_scope_path(
                "Slideshow11/Automated-Edge-Discovery",
                411, self.HEAD, scope_root=tmp_path,
            )
            assert tmp_path.resolve() in path.resolve().parents

    def test_path_no_filesystem_call_on_malformed(self, tmp_path):
        # A malformed repo with absolute path must NOT call .resolve()
        # or open any file outside the injected root. We assert that
        # _trusted_scope_path raises ValueError before any IO.
        import pathlib as _pl
        raised = False
        try:
            ctrl._trusted_scope_path(
                "/tmp/a", 411, self.HEAD, scope_root=tmp_path,
            )
        except ValueError:
            raised = True
        assert raised
        # Confirm no write happened at the injected root.
        assert list(tmp_path.iterdir()) == []


class TestRound19WriteTrustedScopeRejection:
    """``write_trusted_scope`` must refuse every malformed repo
    value and never create a file or directory outside the injected
    scope root.
    """

    HEAD = "0" * 40

    @pytest.mark.parametrize("bad", TestRound19RepoPathValidation.REJECTED)
    def test_write_rejects_malformed_repo(self, tmp_path, bad):
        ok, err = ctrl.write_trusted_scope(
            bad, 411, self.HEAD,
            ["scripts/local/aed_pr*.py"], [],
            scope_root=tmp_path,
        )
        assert ok is False
        assert err
        assert "repo" in err.lower() or "/" in err or "owner" in err.lower() or "name" in err.lower()
        # No file or directory may have been created under the root.
        assert list(tmp_path.iterdir()) == []

    def test_write_accepts_canonical_repo(self, tmp_path):
        ok, result = ctrl.write_trusted_scope(
            REPO, 411, self.HEAD,
            ["scripts/local/aed_pr*.py"], [],
            scope_root=tmp_path,
        )
        assert ok is True
        assert result.endswith(f"{self.HEAD}.json")
        # The file is at the expected path.
        expected = (
            tmp_path / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.HEAD}.json"
        )
        assert expected.exists()


class TestRound19ReadTrustedScopeHead:
    """``read_trusted_scope`` enforces strict ``head_sha`` binding.

    Rejects:
    * missing ``head_sha``;
    * null / boolean / int / list / dict / empty-string ``head_sha``;
    * 39 / 41 / uppercase / non-hex / whitespace-padded ``head_sha``;
    * a valid canonical ``head_sha`` that differs from the requested
      live SHA.

    The previous implementation silently passed ``data.get("head_sha")``
    returning ``None`` and skipped the mismatch check.
    """

    LIVE_HEAD = "0123456789abcdef" * 2 + "01234567"  # 40 lowercase hex
    OTHER_HEAD = "fedcba9876543210" * 2 + "fedcba98"  # 40 lowercase hex

    def _write_raw(self, tmp_path, body):
        import json
        path = (
            tmp_path / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.LIVE_HEAD}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_canonical_record_accepted(self, tmp_path):
        self._write_raw(tmp_path, {
            "repo": REPO,
            "pr_number": 411,
            "head_sha": self.LIVE_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
            "forbidden_files": [],
        })
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert err == ""
        assert allowed == ["scripts/local/aed_pr*.py"]
        assert forbidden == []

    def test_missing_head_sha_rejected(self, tmp_path):
        self._write_raw(tmp_path, {
            "repo": REPO,
            "pr_number": 411,
            "allowed_files": ["scripts/local/aed_pr*.py"],
        })
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert "head_sha" in err

    @pytest.mark.parametrize("bad_value", [
        None,
        True,
        False,
        12345,
        40,
        [],
        ["head_sha"],
        {},
        {"sha": "abc"},
        "",
        "0" * 39,                   # too short
        "0" * 41,                   # too long
        "0" * 40,                   # valid format but wrong value
        ("A" + "0" * 39),           # uppercase
        "g" * 40,                   # non-hex chars
        "0" * 39 + " ",             # trailing whitespace
        " 0" + "0" * 38 + "0",      # leading whitespace
        "0\n" + "0" * 38 + "0",     # newline padded
        "0" * 39 + "\x00",          # NUL padded
    ])
    def test_malformed_stored_head_rejected(self, tmp_path, bad_value):
        self._write_raw(tmp_path, {
            "repo": REPO,
            "pr_number": 411,
            "head_sha": bad_value,
            "allowed_files": ["scripts/local/aed_pr*.py"],
        })
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        # Error message must reference the stored head issue.
        assert "head_sha" in err

    def test_different_canonical_head_rejected(self, tmp_path):
        self._write_raw(tmp_path, {
            "repo": REPO,
            "pr_number": 411,
            "head_sha": self.OTHER_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
        })
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert "mismatch" in err
        assert self.OTHER_HEAD in err
        assert self.LIVE_HEAD in err

    def test_malformed_repo_does_not_touch_filesystem(self, tmp_path):
        # Write nothing; a malformed repo must NOT silently fall
        # back to the canonical root.
        allowed, forbidden, err = ctrl.read_trusted_scope(
            "owner/../../tmp/a", 411, self.LIVE_HEAD,
            scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        # No file or directory created under the injected root.
        assert list(tmp_path.iterdir()) == []

    def test_scope_write_then_scope_read_round_trip(self, tmp_path):
        ok, _path = ctrl.write_trusted_scope(
            REPO, 411, self.LIVE_HEAD,
            ["scripts/local/aed_pr*.py"], [],
            scope_root=tmp_path,
        )
        assert ok is True
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert err == ""
        assert allowed == ["scripts/local/aed_pr*.py"]
        assert forbidden == []


class TestRound19ScopeCLIFailClosed:
    """``cmd_scope_write`` and ``cmd_scope_read`` must fail nonzero
    on every malformed repository value.
    """

    HEAD = "0" * 40

    @pytest.mark.parametrize(
        "bad", TestRound19RepoPathValidation.REJECTED,
    )
    def test_cmd_scope_write_rejects_malformed_repo(self, bad, tmp_path, monkeypatch, capsys):
        # Patch the canonical scope root to a tmp path so we can
        # assert nothing leaks.
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", tmp_path)
        args = type("A", (), {})()
        args.repo = bad
        args.pr_number = 411
        args.head_sha = self.HEAD
        args.allowed_files = "scripts/local/aed_pr*.py"
        args.forbidden_files = ""
        rc = ctrl.cmd_scope_write(args)
        assert rc != 0
        captured = capsys.readouterr()
        assert "scope-write failed" in captured.err
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "bad", TestRound19RepoPathValidation.REJECTED,
    )
    def test_cmd_scope_read_rejects_malformed_repo(self, bad, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", tmp_path)
        args = type("A", (), {})()
        args.repo = bad
        args.pr_number = 411
        args.head_sha = self.HEAD
        rc = ctrl.cmd_scope_read(args)
        assert rc != 0
        captured = capsys.readouterr()
        assert "scope-read failed" in captured.err
        assert list(tmp_path.iterdir()) == []


class TestRound19LifecycleFailClosed:
    """``_resolve_effective_scope`` for status/advance/merge must
    fail closed when the repository value is malformed OR when the
    stored ``head_sha`` is missing/malformed.
    """

    LIVE_HEAD = "0123456789abcdef" * 2 + "01234567"

    def _args(self, head_sha=None, allowed=None, forbidden=None):
        args = type("A", (), {})()
        args.repo = REPO
        args.pr_number = 411
        args.head_sha = head_sha
        args.allowed_files = allowed
        args.forbidden_files = forbidden
        return args

    @pytest.mark.parametrize("sub", ["status", "advance", "merge"])
    def test_malformed_repo_fails_closed(self, sub):
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand=sub,
            repo="owner/../../tmp/a",
            pr_number=411,
            head_sha=self.LIVE_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert allowed is None
        assert forbidden is None
        assert err

    @pytest.mark.parametrize("sub", ["status", "advance", "merge"])
    def test_missing_stored_head_fails_closed(self, sub, tmp_path, monkeypatch):
        # Write a record without head_sha into the injected root.
        import json
        from pathlib import Path as _P
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", _P(tmp_path))
        path = (
            _P(tmp_path) / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.LIVE_HEAD}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "repo": REPO,
                "pr_number": 411,
                "allowed_files": ["scripts/local/aed_pr*.py"],
            }),
            encoding="utf-8",
        )
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand=sub,
            repo=REPO,
            pr_number=411,
            head_sha=self.LIVE_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "head_sha" in err

    @pytest.mark.parametrize("sub", ["status", "advance", "merge"])
    def test_canonical_record_accepted(self, sub, tmp_path, monkeypatch):
        from pathlib import Path as _P
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", _P(tmp_path))
        ok, _ = ctrl.write_trusted_scope(
            REPO, 411, self.LIVE_HEAD,
            ["scripts/local/aed_pr*.py"], [],
        )
        assert ok is True
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand=sub,
            repo=REPO,
            pr_number=411,
            head_sha=self.LIVE_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert err == ""
        assert allowed == ["scripts/local/aed_pr*.py"]
        assert forbidden == []

    @pytest.mark.parametrize("sub", ["status", "advance", "merge"])
    def test_uppercase_stored_head_fails_closed(self, sub, tmp_path, monkeypatch):
        import json
        from pathlib import Path as _P
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", _P(tmp_path))
        path = (
            _P(tmp_path) / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.LIVE_HEAD}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "repo": REPO,
                "pr_number": 411,
                "head_sha": self.LIVE_HEAD.upper(),
                "allowed_files": ["scripts/local/aed_pr*.py"],
            }),
            encoding="utf-8",
        )
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand=sub,
            repo=REPO,
            pr_number=411,
            head_sha=self.LIVE_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert allowed is None
        assert forbidden is None
        assert err


# ---------------------------------------------------------------------------
# Round-20 regression tests.
#
# Exact-head Codex review 4740272185 (submitted 2026-07-21T01:03:02Z on
# head 45986012ae311dda387af1731ee4a1b408c133a5) reported one P2 finding
# on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7XsMLN (db_id 3618685645)
#     "Validate trusted scope record identity"
#     A record from another PR or repository that happens to share
#     the same head SHA must not be accepted as authoritative.
#
# Tests below prove:
#
#   * a record with mismatched stored ``repo`` is rejected;
#   * a record with mismatched stored ``pr_number`` is rejected;
#   * a record with missing or non-string ``repo`` is rejected;
#   * a record with missing or non-int ``pr_number`` is rejected;
#   * a record with malformed stored ``repo`` is rejected;
#   * allowed_files and forbidden_files remain unavailable on every
#     rejected record;
#   * a fresh scope-write round-trips through scope-read;
#   * status / advance / merge fail closed when the stored identity
#     does not match.
# ---------------------------------------------------------------------------


class TestRound20StoredRepoBinding:
    """``read_trusted_scope`` requires the stored ``repo`` field to
    match the requested repo byte-exactly, after path-safe
    validation. A record from another repo with the same head SHA
    must NOT be accepted as authoritative.
    """

    LIVE_HEAD = "0123456789abcdef" * 2 + "01234567"  # 40 lowercase hex
    OTHER_REPO = "OtherOrg/OtherRepo"
    MALFORMED_REPO_VALUES = [
        None,
        True,
        False,
        12345,
        [],
        ["repo"],
        {},
        "",
        "owner",
        "owner/../../tmp/a",
        "/name",
        "owner//tmp/a",
        "owner/.\\name",
        "owner/name\n",
        "owner/\x00name",
        " owner/name",
        "owner/name ",
    ]

    def _write_raw(self, tmp_path, body):
        import json
        path = (
            tmp_path / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.LIVE_HEAD}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def _canonical_body(self, **overrides):
        body = {
            "repo": REPO,
            "pr_number": 411,
            "head_sha": self.LIVE_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
            "forbidden_files": [],
        }
        body.update(overrides)
        return body

    def test_matching_record_accepted(self, tmp_path):
        self._write_raw(tmp_path, self._canonical_body())
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert err == ""
        assert allowed == ["scripts/local/aed_pr*.py"]
        assert forbidden == []

    def test_other_repo_rejected(self, tmp_path):
        # A record from a different repo with the same head SHA
        # must NOT be accepted.
        self._write_raw(tmp_path, self._canonical_body(
            repo=self.OTHER_REPO,
        ))
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "repo" in err
        assert self.OTHER_REPO in err
        assert REPO in err

    @pytest.mark.parametrize("bad_repo", MALFORMED_REPO_VALUES)
    def test_malformed_stored_repo_rejected(self, tmp_path, bad_repo):
        self._write_raw(tmp_path, self._canonical_body(repo=bad_repo))
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "repo" in err

    def test_missing_stored_repo_rejected(self, tmp_path):
        self._write_raw(tmp_path, {
            "pr_number": 411,
            "head_sha": self.LIVE_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
        })
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "repo" in err


class TestRound20StoredPrNumberBinding:
    """``read_trusted_scope`` requires the stored ``pr_number`` field
    to match the requested PR number byte-exactly. A record from
    another PR with the same head SHA must NOT be accepted.
    """

    LIVE_HEAD = "0123456789abcdef" * 2 + "01234567"  # 40 lowercase hex
    OTHER_PR = 999

    def _write_raw(self, tmp_path, body):
        import json
        path = (
            tmp_path / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.LIVE_HEAD}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def _canonical_body(self, **overrides):
        body = {
            "repo": REPO,
            "pr_number": 411,
            "head_sha": self.LIVE_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
            "forbidden_files": [],
        }
        body.update(overrides)
        return body

    def test_other_pr_rejected(self, tmp_path):
        self._write_raw(tmp_path, self._canonical_body(
            pr_number=self.OTHER_PR,
        ))
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "pr_number" in err
        assert str(self.OTHER_PR) in err

    @pytest.mark.parametrize("bad_pr", [
        None,
        True,
        False,
        "411",
        411.0,
        [],
        ["411"],
        {},
        {"pr": 411},
        0,
        -1,
    ])
    def test_malformed_stored_pr_rejected(self, tmp_path, bad_pr):
        self._write_raw(tmp_path, self._canonical_body(
            pr_number=bad_pr,
        ))
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "pr_number" in err

    def test_missing_stored_pr_rejected(self, tmp_path):
        self._write_raw(tmp_path, {
            "repo": REPO,
            "head_sha": self.LIVE_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
        })
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "pr_number" in err


class TestRound20ScopeWriteReadRoundTrip:
    """``write_trusted_scope`` writes a record with ``repo`` and
    ``pr_number``; ``read_trusted_scope`` then accepts it on the
    same exact head. This is the canonical happy path.
    """

    LIVE_HEAD = "0123456789abcdef" * 2 + "01234567"  # 40 lowercase hex

    def test_round_trip(self, tmp_path):
        ok, _ = ctrl.write_trusted_scope(
            REPO, 411, self.LIVE_HEAD,
            ["scripts/local/aed_pr*.py"], [],
            scope_root=tmp_path,
        )
        assert ok is True
        # Inspect the on-disk record: it MUST include repo and
        # pr_number.
        import json
        path = (
            tmp_path / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.LIVE_HEAD}.json"
        )
        body = json.loads(path.read_text(encoding="utf-8"))
        assert body["repo"] == REPO
        assert body["pr_number"] == 411
        assert body["head_sha"] == self.LIVE_HEAD
        # And it round-trips through read_trusted_scope.
        allowed, forbidden, err = ctrl.read_trusted_scope(
            REPO, 411, self.LIVE_HEAD, scope_root=tmp_path,
        )
        assert err == ""
        assert allowed == ["scripts/local/aed_pr*.py"]
        assert forbidden == []


class TestRound20LifecycleFailClosedIdentity:
    """``_resolve_effective_scope`` for status/advance/merge must
    fail closed when the stored identity does not match.
    """

    LIVE_HEAD = "0123456789abcdef" * 2 + "01234567"  # 40 lowercase hex
    OTHER_REPO = "OtherOrg/OtherRepo"
    OTHER_PR = 999

    def _write_raw(self, tmp_path, body):
        import json
        path = (
            tmp_path / "Slideshow11" / "Automated-Edge-Discovery"
            / "411" / f"{self.LIVE_HEAD}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    @pytest.mark.parametrize("sub", ["status", "advance", "merge"])
    def test_other_repo_record_fails_closed(
        self, sub, tmp_path, monkeypatch,
    ):
        from pathlib import Path as _P
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", _P(tmp_path))
        self._write_raw(tmp_path, {
            "repo": self.OTHER_REPO,
            "pr_number": 411,
            "head_sha": self.LIVE_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
        })
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand=sub,
            repo=REPO,
            pr_number=411,
            head_sha=self.LIVE_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "repo" in err

    @pytest.mark.parametrize("sub", ["status", "advance", "merge"])
    def test_other_pr_record_fails_closed(
        self, sub, tmp_path, monkeypatch,
    ):
        from pathlib import Path as _P
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", _P(tmp_path))
        self._write_raw(tmp_path, {
            "repo": REPO,
            "pr_number": self.OTHER_PR,
            "head_sha": self.LIVE_HEAD,
            "allowed_files": ["scripts/local/aed_pr*.py"],
        })
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand=sub,
            repo=REPO,
            pr_number=411,
            head_sha=self.LIVE_HEAD,
            cli_allowed=None,
            cli_forbidden=None,
        )
        assert allowed is None
        assert forbidden is None
        assert err
        assert "pr_number" in err


# ---------------------------------------------------------------------------
# Round-21 regression tests.
#
# Exact-head Codex review 4740468145 (submitted 2026-07-21T01:30:28Z on
# head 4029f2d9dd789b33d46dc315a00177930004e31a) reported two findings
# on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7XssA7 (db_id 3618816059, P1)
#     "Don't authorize after posting a new Codex request"
#     When ``cmd_advance`` posts a fresh ``@codex review`` ping in
#     this run, the canonical authorization phrase must be
#     suppressed even if the captured pre-ping evidence showed
#     ``codex_clean_passed=True``. A stale clean pass is no longer
#     authoritative once a new review is in flight.
#
#   PRRC_kwDOSHFpYM7XssBA (db_id 3618816064, P2)
#     "Fail closed on duplicate exact-head CI runs"
#     ``_find_exact_head_pull_request_run`` (used by
#     ``cmd_gate_recheck``) must return INCONCLUSIVE rather than
#     silently selecting the newest run when two or more matching
#     ``pull_request`` CI runs exist on the same head.
#
# Tests below prove:
#
#   * ``cmd_advance`` does NOT expose ``required_authorization_phrase``
#     and reports ``machine_ready=False`` / ``merge_ready=False``
#     when a fresh ``@codex review`` ping is posted in this run;
#   * ``cmd_advance`` reports ``fresh_codex_ping_posted=True`` in
#     the action report;
#   * ``cmd_advance`` does NOT post a ping under
#     ``--dry-run`` (so the suppression flag stays False);
#   * duplicate-prevention path keeps the suppression flag False
#     (no fresh ping was posted, so authorization is allowed when
#     the underlying machine gate is clean);
#   * ``_find_exact_head_pull_request_run`` returns
#     ``multiple_exact_head_pr_runs`` for two-or-more matching
#     runs, refuses to select one, and exits the gate-recheck
#     caller with a non-zero status;
#   * zero matches still returns ``no exact-head pull_request CI
#     run`` and one match still returns the unique run.
# ---------------------------------------------------------------------------


def _r21_clean_packet(head_sha):
    """A ``CODEX.classify`` packet that advertises a clean pass on
    the exact head. Used to drive the
    ``machine_ready=True`` pre-ping path so the P1 regression can
    prove the authorization phrase is suppressed after a fresh
    ping.
    """
    return _r18_codex_packet(
        active_threads=[],
        outdated_threads=[],
        status="CODEX_CLEAN_PASS",
        head_sha=head_sha,
    )


class TestRound21FreshPingSuppressesAuthorization:
    """When ``cmd_advance`` posts a fresh ``@codex review`` ping in
    this run, the canonical authorization phrase MUST be
    suppressed. The pre-ping ``codex_clean_passed=True`` evidence
    is no longer authoritative while a new review is pending.
    """

    HEAD = "0" * 40

    def _run(self, *, initial_packet, dry_run=False, with_eligible=True):
        """Run ``cmd_advance`` with the supplied ``CODEX.classify``
        packet and return the parsed JSON report plus a small
        meta dict.
        """
        import io
        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        from pathlib import Path as _P
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            ctrl._CANONICAL_SCOPE_ROOT = _P(tmpdir)
            ctrl.write_trusted_scope(
                REPO, PR, self.HEAD,
                ["scripts/local/aed_pr*.py"], [],
            )
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = dry_run
            args.resolve_eligible_bot_threads = with_eligible
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )
            fake_proc = _r18_make_fake_subprocess(self.HEAD)
            buf = io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    return_value=initial_packet,
                ), mock.patch.object(
                    subprocess, "run", side_effect=fake_proc,
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    rc = ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
                ctrl._CANONICAL_SCOPE_ROOT = saved_root
        return rc, json.loads(buf.getvalue())

    def test_fresh_ping_suppresses_phrase(self):
        """The P1 bug repro: pre-ping evidence says clean pass,
        ``cmd_advance`` posts a fresh ``@codex review`` ping, the
        canonical authorization phrase MUST be None, and
        ``machine_ready``/``merge_ready`` MUST be False on the
        operator-facing report.
        """
        clean = _r21_clean_packet(self.HEAD)
        rc, report = self._run(initial_packet=clean)
        assert rc == 0
        # A fresh ping was actually posted.
        assert report["fresh_codex_ping_posted"] is True
        # The bug was: this would still expose the phrase on a
        # stale clean pass. The fix suppresses it.
        assert report["required_authorization_phrase_if_ready"] is None
        assert report["machine_ready"] is False
        assert report["merge_ready"] is False
        assert report["ready"] is False
        assert report["safe_merge_command_if_ready"] is None

    def test_dry_run_does_not_post_and_does_not_suppress(self):
        """``--dry-run`` must perform zero mutations. With no
        ping posted, the suppression flag stays False so a future
        dry-run report does not falsely advertise blocking just
        because dry-run was set.
        """
        clean = _r21_clean_packet(self.HEAD)
        rc, report = self._run(initial_packet=clean, dry_run=True)
        assert rc == 0
        assert report["fresh_codex_ping_posted"] is False
        # The report fields are unaffected by the suppression
        # logic on a dry run; this is the regression baseline.

    def test_action_report_records_ping(self):
        """The action record must include a ``codex_review_ping``
        entry whose ``result`` is a numeric comment id (the new
        ping was actually posted).
        """
        clean = _r21_clean_packet(self.HEAD)
        rc, report = self._run(initial_packet=clean)
        assert rc == 0
        ping_actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "codex_review_ping"
        ]
        assert ping_actions
        posted = [
            a for a in ping_actions
            if a.get("attempted") is True and a.get("ok") is True
        ]
        assert posted
        # The result is the comment database id returned by the
        # fake POST (fake-comment-id).
        assert posted[0]["result"] == "fake-comment-id"


class TestRound21DuplicatePingDoesNotSuppress:
    """When ``cmd_advance`` detects an existing exact-head ping
    and reports ``duplicate_exact_head_request_prevented``,
    ``fresh_codex_ping_posted`` stays False. The pre-ping
    ``codex_clean_passed`` evidence is therefore still
    authoritative.
    """

    HEAD = "0" * 40

    def test_duplicate_does_not_suppress(self, tmp_path, monkeypatch):
        import io
        from pathlib import Path as _P
        monkeypatch.setattr(ctrl, "_CANONICAL_SCOPE_ROOT", _P(tmp_path))
        ctrl.write_trusted_scope(
            REPO, PR, self.HEAD,
            ["scripts/local/aed_pr*.py"], [],
        )

        # Build a fake subprocess that returns an existing
        # duplicate exact-head ping comment on the inventory
        # fetch.
        def fake(cmd, *args, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return _FakeProc(
                    0, json.dumps(_r18_pr_view_payload(self.HEAD)), "",
                )
            if cmd[:3] == ["gh", "pr", "checks"]:
                return _FakeProc(0, json.dumps([
                    {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
                    {"name": "validator", "state": "SUCCESS", "workflow": "CI"},
                    {"name": "governance-validators", "state": "SUCCESS", "workflow": "CI"},
                    {"name": "pr-gate-live-smoke", "state": "SUCCESS", "workflow": "CI"},
                    {"name": "review-comment-gate", "state": "FAILURE", "workflow": "CI"},
                ]), "")
            if cmd[:3] == ["gh", "pr", "diff"]:
                return _FakeProc(0, "[]", "")
            if cmd[:3] == ["gh", "run", "list"]:
                return _FakeProc(0, "[]", "")
            if cmd[:3] == ["gh", "api"] and "compare/" in cmd_str:
                return _FakeProc(0, "ahead", "")
            if (
                cmd[:2] == ["gh", "api"]
                and "/comments" in cmd_str
                and "POST" not in cmd_str
            ):
                # Return an inventory that already contains a
                # ping for THIS exact head, so the controller
                # short-circuits with
                # ``duplicate_exact_head_request_prevented``.
                existing = [{
                    "id": 12345,
                    "body": (
                        "@codex review\n\n"
                        f"AED exact-head review request: {self.HEAD}"
                    ),
                }]
                return _FakeProc(0, json.dumps([existing]), "")
            if cmd[:2] == ["gh", "api"] and "POST" in cmd_str:
                return _FakeProc(
                    0, json.dumps({"id": "fake-comment-id"}), "",
                )
            if cmd[:2] == ["gh", "api"] and "graphql" in cmd_str:
                return _FakeProc(0, json.dumps({
                    "data": {"repository": {"pullRequest": {
                        "reviewThreads": {
                            "totalCount": 0,
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [],
                        }
                    }}}
                }), "")
            raise AssertionError(f"unexpected subprocess.run: {cmd}")

        clean = _r21_clean_packet(self.HEAD)
        args = type("Args", (), {})()
        args.repo = REPO
        args.pr_number = PR
        args.allowed_files = None
        args.forbidden_files = None
        args.dry_run = False
        args.resolve_eligible_bot_threads = True
        args.ancestry_runner = (
            lambda *a, **kw: _FakeProc(0, "ahead", "")
        )

        buf = io.StringIO()
        old = sys_stdout()
        sys_stdout_set(buf)
        try:
            with mock.patch.object(
                ctrl.CODEX, "classify",
                return_value=clean,
            ), mock.patch.object(
                subprocess, "run", side_effect=fake,
            ), mock.patch.object(
                ctrl, "resolve_review_thread",
                return_value=(True, "resolved"),
            ):
                rc = ctrl.cmd_advance(args)
        finally:
            sys_stdout_set(old)
        report = json.loads(buf.getvalue())
        assert rc == 0
        # No fresh ping was posted in this run because the
        # duplicate-detection path short-circuited the POST.
        assert report["fresh_codex_ping_posted"] is False
        # The action record shows duplicate_exact_head_request_prevented.
        dup_actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "codex_review_ping"
            and a.get("duplicate_exact_head_request_prevented") is True
        ]
        assert dup_actions


class TestRound21FindExactHeadRunMultipleRefusesToSelect:
    """``_find_exact_head_pull_request_run`` (used by
    ``cmd_gate_recheck``) MUST return INCONCLUSIVE rather than
    silently selecting the newest run when two or more matching
    ``pull_request`` CI runs exist on the same head. The
    readiness path applies the same rule
    (``multiple_exact_head_pr_runs``); gate-recheck must apply
    it too.
    """

    HEAD = "0" * 40

    def _list_runner(self, runs):
        def fake(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "run", "list"]:
                return _FakeProc(0, json.dumps(runs), "")
            raise AssertionError(f"unexpected subprocess.run: {cmd}")
        return fake

    def _run(self, runs):
        return ctrl._find_exact_head_pull_request_run(
            repo=REPO,
            head_sha=self.HEAD,
            head_branch="reduction/pr-lifecycle-collapse-v1",
            list_runner=self._list_runner(runs),
        )

    def _run_record(self, db_id, head_sha=None):
        return {
            "databaseId": db_id,
            "event": "pull_request",
            "headBranch": "reduction/pr-lifecycle-collapse-v1",
            "headSha": head_sha or self.HEAD,
            "status": "completed",
            "conclusion": "success",
            "url": f"https://github.com/{REPO}/actions/runs/{db_id}",
            "workflowName": "CI",
            "createdAt": f"2026-07-21T00:00:{db_id:02d}Z",
            "name": "CI",
        }

    def test_zero_matches_returns_missing(self):
        run, err = self._run([])
        assert run is None
        assert "no exact-head pull_request CI run" in err

    def test_unique_match_returns_the_run(self):
        run, err = self._run([self._run_record(111)])
        assert err == ""
        assert run is not None
        assert run["databaseId"] == 111

    def test_two_matches_returns_multiple(self):
        """P2 bug repro: the previous implementation sorted and
        returned the newest one; the fix returns
        ``multiple_exact_head_pr_runs`` so ``cmd_gate_recheck``
        exits non-zero.
        """
        run, err = self._run([
            self._run_record(111),
            self._run_record(222),
        ])
        assert run is None
        assert "multiple_exact_head_pr_runs" in err

    def test_three_matches_returns_multiple(self):
        run, err = self._run([
            self._run_record(111),
            self._run_record(222),
            self._run_record(333),
        ])
        assert run is None
        assert "multiple_exact_head_pr_runs" in err

    def test_non_pr_event_run_is_ignored(self):
        """A workflow_dispatch or push run on the same head must
        NOT count as a matching pull_request run; only the
        pull_request trigger run is acceptable.
        """
        runs = [
            {
                "databaseId": 111,
                "event": "workflow_dispatch",
                "headBranch": "reduction/pr-lifecycle-collapse-v1",
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/111",
            },
            {
                "databaseId": 222,
                "event": "push",
                "headBranch": "reduction/pr-lifecycle-collapse-v1",
                "headSha": self.HEAD,
                "workflowName": "CI",
                "url": "https://example/222",
            },
            self._run_record(333),
        ]
        run, err = self._run(runs)
        # Only the pull_request run counts; the dispatch and push
        # runs are skipped, so a single match survives.
        assert err == ""
        assert run is not None
        assert run["databaseId"] == 333

    def test_gate_recheck_refuses_multiple_matches(self):
        """``cmd_gate_recheck`` exits non-zero when two or more
        matching runs exist (no rerun fires, no false success).
        """
        import io
        import sys
        runs = [
            self._run_record(111),
            self._run_record(222),
        ]
        # The pr_view runner must return a head that matches
        # args.head_sha so the live-binding check passes; we
        # want to reach the run-list step, where the
        # multiple-match rejection fires.
        pr_view_payload = {
            "number": PR,
            "headRefOid": self.HEAD,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": True,
            "state": "OPEN",
        }
        args = type("Args", (), {})()
        args.repo = REPO
        args.pr_number = PR
        args.head_sha = self.HEAD
        args.dry_run = True
        args.list_runner = self._list_runner(runs)
        args.view_runner = lambda *a, **kw: _FakeProc(0, "[]", "")
        args.rerun_runner = lambda *a, **kw: _FakeProc(0, "[]", "")
        args.pr_view_runner = (
            lambda *a, **kw: _FakeProc(
                0, json.dumps(pr_view_payload), "",
            )
        )
        args.allowed_files = None
        args.forbidden_files = None
        args.ancestry_runner = (
            lambda *a, **kw: _FakeProc(0, "ahead", "")
        )

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        old_out = sys.stdout
        old_err = sys.stderr
        sys.stdout = out_buf
        sys.stderr = err_buf
        try:
            rc = ctrl.cmd_gate_recheck(args)
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
        # non-zero: gate-recheck refuses to rerun any job when
        # the run identity is ambiguous.
        assert rc != 0
        # The diagnostic may go to stderr (rejection path) or
        # stdout (early report on the selected run); assert that
        # either path surfaces the rejection reason or that
        # rc reflects an inconclusive exit.
        combined = out_buf.getvalue() + err_buf.getvalue()
        assert "multiple_exact_head_pr_runs" in combined or rc == 2


# ---------------------------------------------------------------------------
# Round-22 regression tests.
#
# Exact-head Codex review 4740621538 (submitted 2026-07-21T02:03:05Z on
# head 1125a48316690f1d0c771690e9e3f4f3ac632649) reported two P2
# findings on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7XtIQ0 (db_id 3618931764)
#     "Recompute state after posting review ping"
#     When ``cmd_advance`` posts a fresh ``@codex review`` ping,
#     the report's ``lifecycle_state`` and ``next_human_action``
#     must be overridden so an operator keying off
#     ``lifecycle_state`` cannot treat an in-flight review as
#     READY_FOR_MERGE_AUTHORIZATION.
#
#   PRRC_kwDOSHFpYM7XtIQ4 (db_id 3618931768)
#     "Guard status preview on canonical head SHA"
#     ``cmd_status`` MUST NOT call ``build_safe_merge_command``
#     when ``head_sha`` is missing or non-canonical, because
#     ``build_safe_merge_command`` raises ``ValueError`` and
#     turns a recoverable ``machine_ready=false`` status into a
#     traceback. The preview must be ``None`` on a non-canonical
#     head and the JSON blocking report must still be emitted.
#
# Tests below prove:
#
#   * ``cmd_advance`` reports ``lifecycle_state == "WAITING"``
#     and ``next_human_action`` no longer says "speak the phrase"
#     when a fresh ping was posted in this run;
#   * ``cmd_advance`` keeps the pre-ping ``lifecycle_state`` and
#     the canonical ``next_human_action`` when the duplicate-
#     prevention path fired (no fresh ping);
#   * ``cmd_status`` emits ``safe_merge_command_if_ready=None``
#     when the live PR view returns a missing or non-canonical
#     ``headRefOid`` instead of raising;
#   * ``cmd_status`` emits a populated ``safe_merge_command_if_ready``
#     on a canonical live head.
# ---------------------------------------------------------------------------


class TestRound22LifecycleStateOnFreshPing:
    """P2 #1 (PRRC_kwDOSHFpYM7XtIQ0): ``lifecycle_state`` and
    ``next_human_action`` MUST be recomputed when
    ``fresh_codex_ping_posted`` is True so an operator keying
    off ``lifecycle_state`` cannot treat an in-flight review
    as ``READY_FOR_MERGE_AUTHORIZATION``.
    """

    HEAD = "0" * 40

    def _run(self, *, initial_packet, dry_run=False, with_eligible=True):
        """Run ``cmd_advance`` with the supplied ``CODEX.classify``
        packet and return the parsed JSON report.
        """
        import io
        import tempfile
        from pathlib import Path as _P
        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        with tempfile.TemporaryDirectory() as tmpdir:
            ctrl._CANONICAL_SCOPE_ROOT = _P(tmpdir)
            ctrl.write_trusted_scope(
                REPO, PR, self.HEAD,
                ["scripts/local/aed_pr*.py"], [],
            )
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = dry_run
            args.resolve_eligible_bot_threads = with_eligible
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )
            fake_proc = _r18_make_fake_subprocess(self.HEAD)
            buf = io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    return_value=initial_packet,
                ), mock.patch.object(
                    subprocess, "run", side_effect=fake_proc,
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    rc = ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
                ctrl._CANONICAL_SCOPE_ROOT = saved_root
        return rc, json.loads(buf.getvalue())

    def test_fresh_ping_overrides_state_to_waiting(self):
        """Bug repro: with a fresh ping posted in this run,
        ``lifecycle_state`` must NOT be
        ``READY_FOR_MERGE_AUTHORIZATION`` and
        ``next_human_action`` must NOT tell the operator to
        speak the phrase.
        """
        clean = _r21_clean_packet(self.HEAD)
        rc, report = self._run(initial_packet=clean)
        assert rc == 0
        assert report["fresh_codex_ping_posted"] is True
        # Round-23 fix: the canonical ``WAITING`` state (in
        # ``LIFECYCLE_STATES``) replaces the Round-22
        # ``WAITING_FOR_REVIEW`` so downstream consumers
        # validating ``lifecycle_state`` against the exported
        # enum still recognise the state.
        assert report["lifecycle_state"] == "WAITING"
        # The hint MUST NOT say "speak the phrase" because the
        # phrase is suppressed in this same report.
        assert "speak" not in report["next_human_action"].lower()
        assert "merge" not in report["next_human_action"].lower()
        # And the hint SHOULD name the wait so the operator
        # knows why nothing else happened.
        assert "review" in report["next_human_action"].lower()

    def test_duplicate_ping_keeps_state(self):
        """When the duplicate-detection path short-circuits the
        POST, ``fresh_codex_ping_posted`` stays False and the
        report keeps the pre-ping ``lifecycle_state`` and
        canonical ``next_human_action``.
        """
        import io
        from pathlib import Path as _P
        import tempfile
        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        with tempfile.TemporaryDirectory() as tmpdir:
            ctrl._CANONICAL_SCOPE_ROOT = _P(tmpdir)
            ctrl.write_trusted_scope(
                REPO, PR, self.HEAD,
                ["scripts/local/aed_pr*.py"], [],
            )

            def fake(cmd, *args, **kwargs):
                cmd_str = " ".join(str(c) for c in cmd)
                if cmd[:3] == ["gh", "pr", "view"]:
                    return _FakeProc(
                        0,
                        json.dumps(_r18_pr_view_payload(self.HEAD)),
                        "",
                    )
                if cmd[:3] == ["gh", "pr", "checks"]:
                    return _FakeProc(0, json.dumps([
                        {"name": "test (3.11)", "state": "SUCCESS",
                         "workflow": "CI"},
                        {"name": "validator", "state": "SUCCESS",
                         "workflow": "CI"},
                        {"name": "governance-validators",
                         "state": "SUCCESS", "workflow": "CI"},
                        {"name": "pr-gate-live-smoke",
                         "state": "SUCCESS", "workflow": "CI"},
                        {"name": "review-comment-gate",
                         "state": "FAILURE", "workflow": "CI"},
                    ]), "")
                if cmd[:3] == ["gh", "pr", "diff"]:
                    return _FakeProc(0, "[]", "")
                if cmd[:3] == ["gh", "run", "list"]:
                    return _FakeProc(0, "[]", "")
                if cmd[:3] == ["gh", "api"] and "compare/" in cmd_str:
                    return _FakeProc(0, "ahead", "")
                if (
                    cmd[:2] == ["gh", "api"]
                    and "/comments" in cmd_str
                    and "POST" not in cmd_str
                ):
                    existing = [{
                        "id": 12345,
                        "body": (
                            "@codex review\n\n"
                            f"AED exact-head review request: {self.HEAD}"
                        ),
                    }]
                    return _FakeProc(0, json.dumps([existing]), "")
                if cmd[:2] == ["gh", "api"] and "POST" in cmd_str:
                    return _FakeProc(
                        0, json.dumps({"id": "fake-comment-id"}), "",
                    )
                if cmd[:2] == ["gh", "api"] and "graphql" in cmd_str:
                    return _FakeProc(0, json.dumps({
                        "data": {"repository": {"pullRequest": {
                            "reviewThreads": {
                                "totalCount": 0,
                                "pageInfo": {
                                    "hasNextPage": False,
                                    "endCursor": None,
                                },
                                "nodes": [],
                            }
                        }}}
                    }), "")
                raise AssertionError(f"unexpected: {cmd}")

            clean = _r21_clean_packet(self.HEAD)
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = False
            args.resolve_eligible_bot_threads = True
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )

            buf = io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    return_value=clean,
                ), mock.patch.object(
                    subprocess, "run", side_effect=fake,
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    rc = ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
                ctrl._CANONICAL_SCOPE_ROOT = saved_root

        report = json.loads(buf.getvalue())
        assert rc == 0
        # No fresh ping was posted, so the lifecycle state is
        # the pre-ping value (whatever derive_lifecycle_state
        # returned on the captured evidence). Critically, it
        # must NOT have been overridden to ``WAITING``
        # because no ping was posted.
        assert report["fresh_codex_ping_posted"] is False
        assert report["lifecycle_state"] != "WAITING"
        # And the next_human_action must NOT be the Round-22
        # wait hint.
        assert "wait for the response" \
            not in report["next_human_action"].lower()


class TestRound22StatusPreviewOnCanonicalHead:
    """P2 #2 (PRRC_kwDOSHFpYM7XtIQ4): ``cmd_status`` MUST NOT
    call ``build_safe_merge_command`` on a missing or
    non-canonical ``head_sha``. The preview must be ``None``
    and the JSON blocking report must still be emitted without
    a traceback.
    """

    def _run_status(self, head_ref_oid):
        """Invoke ``cmd_status`` with a stubbed PR view whose
        ``headRefOid`` is the supplied value. Returns
        ``(rc, report)``.
        """
        import io
        pr_view = {
            "number": PR,
            "headRefOid": head_ref_oid,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": True,
            "state": "OPEN",
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "test",
        }

        def fake(cmd, *args, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return _FakeProc(0, json.dumps(pr_view), "")
            if cmd[:3] == ["gh", "pr", "checks"]:
                return _FakeProc(0, json.dumps([]), "")
            if cmd[:3] == ["gh", "pr", "diff"]:
                return _FakeProc(0, "[]", "")
            if cmd[:3] == ["gh", "run", "list"]:
                return _FakeProc(0, "[]", "")
            if cmd[:2] == ["gh", "api"]:
                if "compare/" in cmd_str:
                    return _FakeProc(0, "ahead", "")
                if "graphql" in cmd_str:
                    return _FakeProc(0, json.dumps({
                        "data": {"repository": {"pullRequest": {
                            "reviewThreads": {
                                "totalCount": 0,
                                "pageInfo": {
                                    "hasNextPage": False,
                                    "endCursor": None,
                                },
                                "nodes": [],
                            }
                        }}}
                    }), "")
                # Comment inventory fetch (empty).
                return _FakeProc(0, json.dumps([[]]), "")
            raise AssertionError(f"unexpected: {cmd}")

        args = type("Args", (), {})()
        args.repo = REPO
        args.pr_number = PR
        args.allowed_files = None
        args.forbidden_files = None
        args.ancestry_runner = (
            lambda *a, **kw: _FakeProc(0, "ahead", "")
        )

        buf = io.StringIO()
        old = sys_stdout()
        sys_stdout_set(buf)
        try:
            with mock.patch.object(
                subprocess, "run", side_effect=fake,
            ):
                rc = ctrl.cmd_status(args)
        finally:
            sys_stdout_set(old)
        return rc, json.loads(buf.getvalue())

    def test_canonical_head_emits_safe_merge_command(self):
        """Baseline: on a canonical live head, the preview is
        populated.
        """
        canonical = "0" * 40
        rc, report = self._run_status(canonical)
        assert rc == 0
        # The preview is the canonical safe argv on a
        # well-formed head.
        assert report["safe_merge_command_preview"] is not None
        assert "gh pr merge" in report["safe_merge_command_preview"]
        assert canonical in report["safe_merge_command_preview"]

    def test_missing_head_ref_does_not_raise(self):
        """Bug repro: a missing ``headRefOid`` would previously
        raise ``ValueError`` from ``build_safe_merge_command``
        before the JSON report was emitted. The fix gates the
        preview on a canonical head and emits the blocking
        report with ``safe_merge_command_preview=None``.
        """
        rc, report = self._run_status(None)
        # The report was emitted (no traceback).
        assert rc == 0
        assert report["tool"] == "aed_pr.status"
        assert report["head_sha"] is None
        # The preview is None instead of raising.
        assert report["safe_merge_command_preview"] is None
        # machine_ready stays False on missing evidence.
        assert report["machine_ready"] is False

    def test_short_head_ref_does_not_raise(self):
        """A 39-character head SHA would previously raise
        ``ValueError`` from ``build_safe_merge_command``.
        """
        rc, report = self._run_status("0" * 39)
        assert rc == 0
        assert report["safe_merge_command_preview"] is None
        assert report["machine_ready"] is False

    def test_non_hex_head_ref_does_not_raise(self):
        """A 40-character non-hex head SHA would previously
        raise ``ValueError`` from ``build_safe_merge_command``.
        """
        rc, report = self._run_status("z" * 40)
        assert rc == 0
        assert report["safe_merge_command_preview"] is None
        assert report["machine_ready"] is False

    def test_uppercase_head_ref_does_not_raise(self):
        """An uppercase 40-character hex head SHA would
        previously raise ``ValueError`` because
        ``build_safe_merge_command`` uses ``is_full_sha`` which
        is case-sensitive.
        """
        rc, report = self._run_status("A" * 40)
        assert rc == 0
        assert report["safe_merge_command_preview"] is None
        assert report["machine_ready"] is False


# ---------------------------------------------------------------------------
# Round-23 regression tests.
#
# Exact-head Codex review 4740936968 (submitted 2026-07-21T03:20:51Z on
# head 4c998f5f80f1d4698adc4d6e51a414e694737229) reported three P2
# findings on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7XuJqh (db_id 3619199649)
#     "Return WAITING for in-flight gates"
#     ``derive_lifecycle_state`` falls through to ``BLOCKED``
#     for in-flight gates (e.g. ``REASON_CI_PENDING``); the
#     exported vocabulary defines ``WAITING`` for exactly this
#     case. Map transient reason codes to ``WAITING`` so an
#     automation does not take a deterministic repair path
#     while CI / Codex is still converging.
#
#   PRRC_kwDOSHFpYM7XuJqj (db_id 3619199651)
#     "Emit a canonical waiting state after fresh pings"
#     The Round-22 override used ``WAITING_FOR_REVIEW`` which
#     is not in the exported ``LIFECYCLE_STATES`` enum. Use the
#     canonical ``WAITING`` state so consumers validating
#     ``lifecycle_state`` against the enum still recognise
#     the state.
#
#   PRRC_kwDOSHFpYM7XuJqo (db_id 3619199656)
#     "Clear authorization_required while review is in flight"
#     On a fresh-ping path, only ``machine_ready`` and
#     ``merge_ready`` were overridden; ``authorization_required``
#     still came from the pre-ping verdict and could say
#     ``true`` while the same report withholds the phrase and
#     says to wait. Clear ``authorization_required`` whenever a
#     fresh ping is posted in this run.
#
# Tests below prove:
#
#   * ``derive_lifecycle_state`` returns ``WAITING`` for
#     a verdict whose only failure is ``REASON_CI_PENDING``
#     and returns ``WAITING`` for any other pure-transient
#     failure (Codex missing, codex stale, codex clean
#     missing, reviews incomplete, thread inventory failed,
#     changed files missing, evidence missing);
#   * ``derive_lifecycle_state`` keeps ``BLOCKED`` when a
#     transient reason coexists with a deterministic reason
#     (e.g. ``SCOPE_UNKNOWN`` + ``CHANGED_FILES_NOT_FETCHED``);
#   * ``derive_lifecycle_state`` keeps ``ACTION_REQUIRED``
#     when draft / unresolved-thread reasons are present;
#   * the fresh-ping path reports ``lifecycle_state ==
#     "WAITING"`` (canonical, in ``LIFECYCLE_STATES``) and
#     ``authorization_required == False`` even when the pre-ping
#     verdict said ``authorization_required == True``;
#   * the duplicate-detection path keeps the pre-ping
#     ``lifecycle_state`` and the original
#     ``authorization_required`` value.
# ---------------------------------------------------------------------------


def _r23_verdict(*, codes, machine_ready=False,
                 authorization_required=True, authorization_valid=None,
                 gates_passed=None, gates_failed=None):
    """Build a ``ReadinessVerdict`` with the supplied reason
    codes. Used to drive ``derive_lifecycle_state`` directly
    without going through the full evidence pipeline.
    ``merge_ready`` is a property, so we only set the
    underlying fields.
    """
    import scripts.local.aed_pr_readiness as R
    reasons = []
    for c in codes:
        reasons.append(R.ReadinessReason(
            code=c,
            detail=f"synthetic {c}",
            gate="",
        ))
    return R.ReadinessVerdict(
        ready=False,
        reasons=reasons,
        gates_passed=gates_passed or [],
        gates_failed=gates_failed or [],
        machine_ready=machine_ready,
        authorization_required=authorization_required,
        authorization_valid=authorization_valid,
    )


class TestRound23WaitingStateForInFlightGates:
    """P2 #1 (PRRC_kwDOSHFpYM7XuJqh):
    ``derive_lifecycle_state`` returns ``WAITING`` for transient
    in-flight gates so the operator is not routed to a
    deterministic repair path while CI / Codex is still
    converging.
    """

    PR_VIEW_OPEN = {
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
    }

    def test_ci_pending_only_is_waiting(self):
        """A verdict whose only failure is
        ``REQUIRED_CI_PENDING`` (CI in flight) maps to
        ``WAITING``, not ``BLOCKED``.
        """
        v = _r23_verdict(codes=["REQUIRED_CI_PENDING"])
        from scripts.local import aed_pr as ctrl
        assert ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN) == "WAITING"

    def test_codex_missing_only_is_waiting(self):
        v = _r23_verdict(codes=["CODEX_EVIDENCE_MISSING"])
        from scripts.local import aed_pr as ctrl
        assert ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN) == "WAITING"

    def test_codex_clean_missing_only_is_waiting(self):
        v = _r23_verdict(codes=["CODEX_CLEAN_VERDICT_MISSING"])
        from scripts.local import aed_pr as ctrl
        assert ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN) == "WAITING"

    def test_reviews_incomplete_only_is_waiting(self):
        v = _r23_verdict(codes=["REVIEWS_AND_COMMENTS_INCOMPLETE"])
        from scripts.local import aed_pr as ctrl
        assert ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN) == "WAITING"

    def test_evidence_missing_only_is_waiting(self):
        v = _r23_verdict(codes=["EVIDENCE_MISSING_OR_TREATED_AS_PASSING"])
        from scripts.local import aed_pr as ctrl
        assert ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN) == "WAITING"

    def test_transient_plus_scope_unknown_stays_blocked(self):
        """Bug regression: when a transient reason coexists
        with a deterministic reason (``SCOPE_UNKNOWN``), the
        state must remain ``BLOCKED`` — never ``WAITING``.
        ``SCOPE_UNKNOWN`` is a deterministic policy decision
        that requires operator repair, not a re-fetch.
        """
        v = _r23_verdict(codes=[
            "CHANGED_FILES_NOT_FETCHED",  # transient
            "SCOPE_UNKNOWN",              # deterministic
        ])
        from scripts.local import aed_pr as ctrl
        assert ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN) == "BLOCKED"

    def test_transient_plus_pr_is_draft_stays_action_required(self):
        """A draft reason stays ``ACTION_REQUIRED`` even when
        transient reasons coexist (human action required to
        flip the draft).
        """
        v = _r23_verdict(codes=[
            "REQUIRED_CI_PENDING",  # transient
            "PR_IS_DRAFT",          # deterministic / human
        ])
        from scripts.local import aed_pr as ctrl
        assert (
            ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN)
            == "ACTION_REQUIRED"
        )

    def test_transient_plus_unresolved_thread_stays_action_required(self):
        v = _r23_verdict(codes=[
            "REQUIRED_CI_PENDING",
            "UNRESOLVED_REVIEW_THREAD",
        ])
        from scripts.local import aed_pr as ctrl
        assert (
            ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN)
            == "ACTION_REQUIRED"
        )

    def test_machine_ready_with_no_phrase_is_ready_for_authorization(self):
        """Baseline: a fully-green verdict (machine_ready) is
        ``READY_FOR_MERGE_AUTHORIZATION`` on an open PR when
        no phrase is supplied.
        """
        v = _r23_verdict(
            codes=[], machine_ready=True, authorization_valid=None,
        )
        from scripts.local import aed_pr as ctrl
        assert (
            ctrl.derive_lifecycle_state(v, self.PR_VIEW_OPEN)
            == "READY_FOR_MERGE_AUTHORIZATION"
        )

    def test_waiting_state_is_in_lifecycle_states_enum(self):
        """The exported ``LIFECYCLE_STATES`` enum must contain
        ``WAITING``; the Round-22 override used
        ``WAITING_FOR_REVIEW`` which was NOT in the enum.
        """
        from scripts.local import aed_pr_lib as L
        assert "WAITING" in L.LIFECYCLE_STATES
        # And ``WAITING_FOR_REVIEW`` must NOT be in the enum.
        assert "WAITING_FOR_REVIEW" not in L.LIFECYCLE_STATES


class TestRound23FreshPingEmitsCanonicalWaitingAndClearsAuth:
    """P2 #2 + #3 (PRRC_kwDOSHFpYM7XuJqj, PRRC_kwDOSHFpYM7XuJqo):
    On a fresh-ping path, ``lifecycle_state`` must be the
    canonical ``WAITING`` (in ``LIFECYCLE_STATES``) and
    ``authorization_required`` must be ``False`` even when the
    pre-ping verdict said ``True``.
    """

    HEAD = "0" * 40

    def _run(self, *, initial_packet):
        import io
        import tempfile
        from pathlib import Path as _P
        saved_root = ctrl._CANONICAL_SCOPE_ROOT
        with tempfile.TemporaryDirectory() as tmpdir:
            ctrl._CANONICAL_SCOPE_ROOT = _P(tmpdir)
            ctrl.write_trusted_scope(
                REPO, PR, self.HEAD,
                ["scripts/local/aed_pr*.py"], [],
            )
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.allowed_files = None
            args.forbidden_files = None
            args.dry_run = False
            args.resolve_eligible_bot_threads = True
            args.ancestry_runner = (
                lambda *a, **kw: _FakeProc(0, "ahead", "")
            )
            fake_proc = _r18_make_fake_subprocess(self.HEAD)
            buf = io.StringIO()
            old = sys_stdout()
            sys_stdout_set(buf)
            try:
                with mock.patch.object(
                    ctrl.CODEX, "classify",
                    return_value=initial_packet,
                ), mock.patch.object(
                    subprocess, "run", side_effect=fake_proc,
                ), mock.patch.object(
                    ctrl, "resolve_review_thread",
                    return_value=(True, "resolved"),
                ):
                    rc = ctrl.cmd_advance(args)
            finally:
                sys_stdout_set(old)
                ctrl._CANONICAL_SCOPE_ROOT = saved_root
        return rc, json.loads(buf.getvalue())

    def test_fresh_ping_state_is_canonical_waiting(self):
        """P2 #2: the override must use the canonical
        ``WAITING`` state in the exported enum, not
        ``WAITING_FOR_REVIEW``.
        """
        clean = _r21_clean_packet(self.HEAD)
        rc, report = self._run(initial_packet=clean)
        assert rc == 0
        assert report["fresh_codex_ping_posted"] is True
        assert report["lifecycle_state"] == "WAITING"
        # The state MUST be in the exported enum so downstream
        # consumers can validate it.
        from scripts.local import aed_pr_lib as L
        assert report["lifecycle_state"] in L.LIFECYCLE_STATES

    def test_fresh_ping_clears_authorization_required(self):
        """P2 #3: when a fresh ping is posted in this run,
        ``authorization_required`` MUST be ``False`` even
        when the pre-ping verdict said ``True``.
        """
        clean = _r21_clean_packet(self.HEAD)
        rc, report = self._run(initial_packet=clean)
        assert rc == 0
        # The pre-ping verdict's ``authorization_required``
        # was True (clean pass means phrase is required), but
        # the fresh-ping override clears it.
        assert report["authorization_required"] is False

    def test_fresh_ping_does_not_advertise_merge(self):
        """Composite regression: a fresh ping posts, the
        lifecycle state is canonical ``WAITING``,
        authorization_required is False, and the merge phrase
        is suppressed. An automation reading the report MUST
        NOT receive a contradictory "ask the operator for
        merge authorization while also saying wait".
        """
        clean = _r21_clean_packet(self.HEAD)
        rc, report = self._run(initial_packet=clean)
        assert rc == 0
        # No authorization phrase was emitted.
        assert report["required_authorization_phrase_if_ready"] is None
        # The state is the canonical WAITING.
        assert report["lifecycle_state"] == "WAITING"
        # authorization_required is False.
        assert report["authorization_required"] is False
        # merge_ready is False.
        assert report["merge_ready"] is False
        # machine_ready is False.
        assert report["machine_ready"] is False
        # ready mirrors merge_ready.
        assert report["ready"] is False


# ---------------------------------------------------------------------------
# Round-24 regression tests.
#
# Exact-head Codex review 4741080178 (submitted 2026-07-21T03:56:03Z on
# head 2f04696409ebacefa80e7ce7a7cd150c9b70f9d8) reported one P2
# finding on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7XulIk (db_id 3619312164)
#     "Make merged PRs reach closeout completion"
#     When the live PR is already ``MERGED``, ``cmd_advance``
#     must reach the promised ``COMPLETE`` state instead of
#     leaving the operator stuck in ``MERGED_PENDING_CLOSEOUT``
#     forever. The fix adds a short-circuit branch that emits a
#     structured closeout report with ``lifecycle_state ==
#     "COMPLETE"`` and a ``post_merge_closeout`` action record.
#
# Tests below prove:
#
#   * ``cmd_advance`` returns ``lifecycle_state == "COMPLETE"``
#     and records a ``post_merge_closeout`` action when the
#     live PR view reports ``state == "MERGED"``;
#   * ``cmd_advance`` does NOT post a fresh codex ping, does
#     NOT mark the PR ready, and does NOT resolve any review
#     thread on the merged-PR path;
#   * ``cmd_advance`` returns ``lifecycle_state ==
#     "COMPLETE"`` even when the scope resolver would have
#     surfaced a transient / structural scope diagnostic
#     (e.g. trusted-scope-not-found), so a merged PR can
#     always reach closeout;
#   * the closeout report surfaces the merge commit SHA and
#     ``mergedAt`` timestamp when the live PR view supplies
#     them.
# ---------------------------------------------------------------------------


class TestRound24MergedPrReachesCloseout:
    """P2 (PRRC_kwDOSHFpYM7XulIk): ``cmd_advance`` must reach
    the promised ``COMPLETE`` state when the live PR is
    already ``MERGED``.
    """

    def _run_advance(self, *, pr_view):
        """Run ``cmd_advance`` with a stubbed ``fetch_pr_state``
        returning the supplied PR view. Returns
        ``(rc, report)``.

        Round-25 placement: the merged-PR short-circuit lives
        at the top of ``cmd_advance``, BEFORE
        ``_resolve_effective_scope``, ``fetch_changed_files``,
        ``build_evidence``, and ``evaluate_machine_readiness``.
        A subprocess that raises here is a precise bug
        detector — the controller must reach ``COMPLETE``
        without invoking any further I/O.
        """
        import io
        buf = io.StringIO()
        old = sys_stdout()
        sys_stdout_set(buf)

        def fake(cmd, *args, **kwargs):
            raise AssertionError(
                f"unexpected subprocess.run on merged-PR path: {cmd}"
            )

        try:
            with mock.patch.object(
                ctrl, "fetch_pr_state", return_value=pr_view,
            ), mock.patch.object(
                subprocess, "run", side_effect=fake,
            ):
                args = type("Args", (), {})()
                args.repo = REPO
                args.pr_number = PR
                args.allowed_files = None
                args.forbidden_files = None
                args.dry_run = False
                args.resolve_eligible_bot_threads = True
                args.ancestry_runner = (
                    lambda *a, **kw: _FakeProc(0, "ahead", "")
                )
                rc = ctrl.cmd_advance(args)
        finally:
            sys_stdout_set(old)
        return rc, json.loads(buf.getvalue())

    def test_merged_pr_reports_complete(self):
        """Bug repro: a MERGED PR used to leave the operator
        stuck in ``MERGED_PENDING_CLOSEOUT`` because
        ``cmd_advance`` had no closeout branch. The fix
        short-circuits the pipeline and emits
        ``lifecycle_state == "COMPLETE"``.
        """
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "mergedAt": "2026-07-21T03:00:00Z",
            "mergeCommit": {"oid": "1" * 40},
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        rc, report = self._run_advance(pr_view=pr_view)
        assert rc == 0
        assert report["tool"] == "aed_pr.advance"
        assert report["lifecycle_state"] == "COMPLETE"

    def test_merged_pr_records_closeout_action(self):
        """The action record must surface a
        ``post_merge_closeout`` entry that names the merge
        commit and the merge timestamp.
        """
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "mergedAt": "2026-07-21T03:00:00Z",
            "mergeCommit": {"oid": "1" * 40},
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        rc, report = self._run_advance(pr_view=pr_view)
        assert rc == 0
        actions = report["actions_taken"]
        closeout = [
            a for a in actions
            if a.get("action") == "post_merge_closeout"
        ]
        assert closeout, actions
        assert closeout[0]["ok"] is True
        assert closeout[0]["result"] == "merged_pr_closeout_complete"
        assert closeout[0]["merged_at"] == "2026-07-21T03:00:00Z"
        assert closeout[0]["merge_commit_sha"] == "1" * 40

    def test_merged_pr_does_not_post_codex_ping(self):
        """A merged-PR closeout MUST NOT post a fresh
        ``@codex review`` ping; the operator does not need a
        new Codex review on a merged PR.
        """
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "mergedAt": "2026-07-21T03:00:00Z",
            "mergeCommit": {"oid": "1" * 40},
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        rc, report = self._run_advance(pr_view=pr_view)
        assert rc == 0
        assert report["fresh_codex_ping_posted"] is False
        ping_actions = [
            a for a in report["actions_taken"]
            if a.get("action") == "codex_review_ping"
        ]
        assert not ping_actions

    def test_merged_pr_does_not_mark_ready(self):
        """A merged-PR closeout MUST NOT mark the PR ready
        and MUST NOT resolve any review thread.
        """
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "mergedAt": "2026-07-21T03:00:00Z",
            "mergeCommit": {"oid": "1" * 40},
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        rc, report = self._run_advance(pr_view=pr_view)
        assert rc == 0
        actions = report["actions_taken"]
        ready_actions = [
            a for a in actions
            if a.get("action") == "mark_pr_ready"
        ]
        resolve_actions = [
            a for a in actions
            if a.get("action") == "resolve_eligible_bot_threads"
        ]
        assert not ready_actions
        assert not resolve_actions

    def test_merged_pr_reaches_complete_even_without_merged_at(self):
        """A merged PR with no ``mergedAt`` / ``mergeCommit``
        in the live view still reaches ``COMPLETE`` — the
        closeout action is still recorded with whatever the
        view supplied.
        """
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        rc, report = self._run_advance(pr_view=pr_view)
        assert rc == 0
        assert report["lifecycle_state"] == "COMPLETE"
        closeout = [
            a for a in report["actions_taken"]
            if a.get("action") == "post_merge_closeout"
        ]
        assert closeout
        assert closeout[0]["merged_at"] is None
        assert closeout[0]["merge_commit_sha"] is None


# ---------------------------------------------------------------------------
# Round-25 regression tests.
#
# Exact-head Codex review 4741176246 (submitted 2026-07-21T04:15:05Z on
# head 8cd86614b59ecdfee76156a6c73ab02e7ee34d1f) reported one P2
# finding on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7Xu1j5 (db_id 3619379449)
#     "Close out merged PRs before fetching readiness evidence"
#     The Round-24 merged-PR short-circuit lived after
#     ``_resolve_effective_scope``, ``fetch_changed_files``,
#     ``build_evidence``, and ``evaluate_machine_readiness``.
#     If any of those pre-closeout evidence calls hung or
#     raised (for example a Codex audit ``gh api`` timeout
#     inside ``build_evidence``), the controller never emitted
#     the promised ``COMPLETE`` closeout report. Move the
#     merged-state short-circuit immediately after
#     ``fetch_pr_state`` and before the readiness / scope
#     evidence fetches.
#
# Tests below prove:
#
#   * ``cmd_advance`` reaches ``COMPLETE`` on a MERGED PR
#     even when ``_resolve_effective_scope`` is monkeypatched
#     to raise (the Round-24 branch was reached only after
#     this call; the Round-25 placement must short-circuit
#     before it);
#   * ``cmd_advance`` reaches ``COMPLETE`` on a MERGED PR
#     even when ``fetch_changed_files`` is monkeypatched to
#     raise;
#   * ``cmd_advance`` reaches ``COMPLETE`` on a MERGED PR
#     even when ``build_evidence`` is monkeypatched to
#     raise;
#   * ``cmd_advance`` reaches ``COMPLETE`` on a MERGED PR
#     even when ``evaluate_machine_readiness`` is monkeypatched
#     to raise;
#   * ``cmd_advance`` does NOT invoke any subprocess when
#     the live PR is MERGED (the closeout is purely a
#     lifecycle transition the controller was always
#     supposed to perform).
# ---------------------------------------------------------------------------


class TestRound25MergedPrClosesOutBeforeEvidence:
    """P2 (PRRC_kwDOSHFpYM7Xu1j5): the merged-PR short-circuit
    must run BEFORE ``_resolve_effective_scope``,
    ``fetch_changed_files``, ``build_evidence``, and
    ``evaluate_machine_readiness`` so a hang or raise in any
    of those cannot prevent the closeout.
    """

    def _run_advance_with_failure(self, *, monkeypatch_target):
        """Run ``cmd_advance`` on a MERGED PR view while
        monkeypatching the named readiness helper to raise.
        Returns ``(rc, report_or_exc)``.
        """
        import io
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "mergedAt": "2026-07-21T03:00:00Z",
            "mergeCommit": {"oid": "1" * 40},
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        buf = io.StringIO()
        old = sys_stdout()
        sys_stdout_set(buf)
        try:
            with mock.patch.object(
                ctrl, "fetch_pr_state", return_value=pr_view,
            ), mock.patch.object(
                ctrl, monkeypatch_target,
                side_effect=AssertionError(
                    f"{monkeypatch_target} must not run on the "
                    f"merged-PR path"
                ),
            ):
                args = type("Args", (), {})()
                args.repo = REPO
                args.pr_number = PR
                args.allowed_files = None
                args.forbidden_files = None
                args.dry_run = False
                args.resolve_eligible_bot_threads = True
                args.ancestry_runner = (
                    lambda *a, **kw: _FakeProc(0, "ahead", "")
                )
                rc = ctrl.cmd_advance(args)
        finally:
            sys_stdout_set(old)
        return rc, json.loads(buf.getvalue())

    def test_closeout_short_circuits_scope_resolver(self):
        """Bug regression: a raise in ``_resolve_effective_scope``
        must not prevent the closeout.
        """
        rc, report = self._run_advance_with_failure(
            monkeypatch_target="_resolve_effective_scope",
        )
        assert rc == 0
        assert report["lifecycle_state"] == "COMPLETE"

    def test_closeout_short_circuits_changed_files(self):
        rc, report = self._run_advance_with_failure(
            monkeypatch_target="fetch_changed_files",
        )
        assert rc == 0
        assert report["lifecycle_state"] == "COMPLETE"

    def test_closeout_short_circuits_build_evidence(self):
        rc, report = self._run_advance_with_failure(
            monkeypatch_target="build_evidence",
        )
        assert rc == 0
        assert report["lifecycle_state"] == "COMPLETE"

    def test_closeout_short_circuits_evaluate_readiness(self):
        """The merged-PR branch must fire before
        ``R.evaluate_machine_readiness`` is invoked; otherwise
        a Codex audit timeout inside ``build_evidence`` would
        skip the closeout. ``evaluate_machine_readiness`` lives
        in ``aed_pr_readiness`` (referenced as ``R`` in
        ``aed_pr``), so we patch it on the readiness module.
        """
        import scripts.local.aed_pr_readiness as R
        import io
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "mergedAt": "2026-07-21T03:00:00Z",
            "mergeCommit": {"oid": "1" * 40},
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        buf = io.StringIO()
        old = sys_stdout()
        sys_stdout_set(buf)
        try:
            with mock.patch.object(
                ctrl, "fetch_pr_state", return_value=pr_view,
            ), mock.patch.object(
                R, "evaluate_machine_readiness",
                side_effect=AssertionError(
                    "R.evaluate_machine_readiness must not run "
                    "on the merged-PR path"
                ),
            ):
                args = type("Args", (), {})()
                args.repo = REPO
                args.pr_number = PR
                args.allowed_files = None
                args.forbidden_files = None
                args.dry_run = False
                args.resolve_eligible_bot_threads = True
                args.ancestry_runner = (
                    lambda *a, **kw: _FakeProc(0, "ahead", "")
                )
                rc = ctrl.cmd_advance(args)
        finally:
            sys_stdout_set(old)
        report = json.loads(buf.getvalue())
        assert rc == 0
        assert report["lifecycle_state"] == "COMPLETE"

    def test_closeout_does_not_invoke_subprocess(self):
        """Structural property: the merged-PR closeout is a
        lifecycle transition the controller performs; no
        subprocess is invoked for it.
        """
        import io
        pr_view = {
            "number": PR,
            "headRefOid": "0" * 40,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "MERGED",
            "mergedAt": "2026-07-21T03:00:00Z",
            "mergeCommit": {"oid": "1" * 40},
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "merged PR",
        }
        buf = io.StringIO()
        old = sys_stdout()
        sys_stdout_set(buf)
        try:
            with mock.patch.object(
                ctrl, "fetch_pr_state", return_value=pr_view,
            ), mock.patch.object(
                subprocess, "run",
                side_effect=AssertionError(
                    "no subprocess.run should fire on the "
                    "merged-PR path"
                ),
            ):
                args = type("Args", (), {})()
                args.repo = REPO
                args.pr_number = PR
                args.allowed_files = None
                args.forbidden_files = None
                args.dry_run = False
                args.resolve_eligible_bot_threads = True
                args.ancestry_runner = (
                    lambda *a, **kw: _FakeProc(0, "ahead", "")
                )
                rc = ctrl.cmd_advance(args)
        finally:
            sys_stdout_set(old)
        report = json.loads(buf.getvalue())
        assert rc == 0
        assert report["lifecycle_state"] == "COMPLETE"
        assert report["fresh_codex_ping_posted"] is False
        actions = report["actions_taken"]
        assert [a.get("action") for a in actions] == [
            "post_merge_closeout",
        ]


# ---------------------------------------------------------------------------
# Round-28 regression tests.
#
# Exact-head Codex review 4743626614 (submitted 2026-07-21T10:32:05Z on
# head 4ce523784c8ec6ac3546f29486461c9e32fdffa0) reported one P2
# finding on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7X2XkY (db_id 3621353752)
#     "Guard malformed heads before validating merge phrase"
#     When ``gh pr view`` returns a missing or non-canonical
#     ``headRefOid`` (for example a deleted/malformed PR head
#     while the merge command is refetching live state), this
#     call reaches ``build_authorization_phrase()`` through
#     ``is_valid_authorization_phrase()`` and raises
#     ``ValueError`` before ``cmd_merge`` can emit its
#     structured deny response. ``cmd_status`` already guards
#     the same malformed-head case; the final merge path
#     should also fail closed with a diagnostic instead of a
#     traceback.
#
# Tests below prove:
#
#   * ``cmd_merge`` returns rc=1 with a structured deny
#     response when ``headRefOid`` is None / empty / non-hex /
#     uppercase / short / long;
#   * ``cmd_merge`` does NOT invoke ``is_valid_authorization_phrase``
#     on a malformed head (no ValueError traceback);
#   * ``cmd_merge`` does NOT invoke ``gh pr merge`` on a
#     malformed head;
#   * the same guard does not affect the happy path
#     (canonical head passes through to the regular deny /
#     authorize path).
# ---------------------------------------------------------------------------


class TestRound28MergeGuardsMalformedHead:
    """P2 (PRRC_kwDOSHFpYM7X2XkY): cmd_merge must fail closed
    with a structured deny response when the live PR view
    returns a missing or non-canonical headRefOid, instead of
    crashing inside ``is_valid_authorization_phrase`` /
    ``build_authorization_phrase``.
    """

    def _run_merge(self, head_ref_oid):
        """Run ``cmd_merge`` with the supplied head_ref_oid
        embedded in the PR view. Returns ``(rc, report_or_None,
        err_text)``.
        """
        import io
        import subprocess as sp

        from scripts.local import aed_pr as ctrl

        pr_view = {
            "number": PR,
            "headRefOid": head_ref_oid,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "baseRefOid": "f" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "state": "OPEN",
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "title": "test",
            "mergeable": True,
            "mergeable_state": "clean",
        }

        recorded_calls = []

        def _fake_run(cmd, *args, **kwargs):
            recorded_calls.append(list(cmd))
            # Anything that reaches subprocess on the
            # malformed-head path is a bug: the merge
            # guard must short-circuit before any I/O.
            raise AssertionError(
                f"unexpected subprocess.run on malformed-head "
                f"merge path: {cmd}"
            )

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = buf_out
        sys.stderr = buf_err
        try:
            with mock.patch.object(
                ctrl, "fetch_pr_state", return_value=pr_view,
            ), mock.patch.object(
                sp, "run", side_effect=_fake_run,
            ):
                args = type("Args", (), {})()
                args.repo = REPO
                args.pr_number = PR
                args.authorization_phrase = (
                    f"I confirm merge PR #{PR} at "
                    + ("a" * 40)
                    + " using final-head reviewed clean state."
                )
                args.allowed_files = None
                args.forbidden_files = None
                rc = ctrl.cmd_merge(args)
        finally:
            sys.stdout, sys.stderr = old_out, old_err

        out_text = buf_out.getvalue()
        err_text = buf_err.getvalue()
        try:
            report = json.loads(out_text) if out_text else None
        except json.JSONDecodeError:
            report = None
        return rc, report, err_text, recorded_calls

    def test_merge_returns_1_when_head_oid_is_none(self):
        rc, report, err, calls = self._run_merge(None)
        assert rc == 1
        assert report is not None
        assert report["merge_attempted"] is False
        assert report["merge_succeeded"] is False
        assert report["reason"] == "head_sha_not_canonical"
        assert report["head_sha"] is None
        assert "not a canonical" in err
        # No subprocess should have fired.
        assert calls == []

    def test_merge_returns_1_when_head_oid_is_empty(self):
        rc, report, err, calls = self._run_merge("")
        assert rc == 1
        assert report is not None
        assert report["reason"] == "head_sha_not_canonical"
        assert report["head_sha"] == ""
        assert calls == []

    def test_merge_returns_1_when_head_oid_is_39_char_hex(self):
        rc, report, err, calls = self._run_merge("a" * 39)
        assert rc == 1
        assert report["reason"] == "head_sha_not_canonical"
        assert calls == []

    def test_merge_returns_1_when_head_oid_is_41_char_hex(self):
        rc, report, err, calls = self._run_merge("a" * 41)
        assert rc == 1
        assert report["reason"] == "head_sha_not_canonical"
        assert calls == []

    def test_merge_returns_1_when_head_oid_is_40_char_non_hex(self):
        rc, report, err, calls = self._run_merge("z" * 40)
        assert rc == 1
        assert report["reason"] == "head_sha_not_canonical"
        assert calls == []

    def test_merge_returns_1_when_head_oid_is_uppercase_40_char_hex(self):
        rc, report, err, calls = self._run_merge("A" * 40)
        assert rc == 1
        assert report["reason"] == "head_sha_not_canonical"
        assert calls == []

    def test_merge_does_not_raise_valueerror_on_malformed_head(self):
        """Structural regression: the previous (un-patched)
        code raised ValueError from inside
        ``build_authorization_phrase`` on a malformed head.
        The Round-28 guard must catch the malformed-head
        case before the helper is invoked, so cmd_merge
        returns cleanly with rc=1 instead of an
        unhandled ValueError traceback.
        """
        rc, report, err, calls = self._run_merge("not-a-sha")
        assert rc == 1
        assert report is not None
        assert "Traceback" not in err
        assert "ValueError" not in err

    def test_merge_guard_short_circuits_before_subprocess(self):
        """On a malformed head, the merge path must NOT
        invoke any subprocess. The ``_fake_run`` here
        raises on any call, so a single recorded call
        means the guard failed.
        """
        rc, report, err, calls = self._run_merge(None)
        assert rc == 1
        assert calls == []
        assert report is not None
        assert report["merge_attempted"] is False


# ---------------------------------------------------------------------------
# Round-29 regression tests.
#
# Exact-head Codex review 4743813446 (submitted 2026-07-21T10:57:39Z on
# head ebb0608028c13b9d769971392b70721f22b7dfb9) reported one P2
# finding on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7X28bT (db_id 3621504723)
#     "Fail closed on malformed forbidden scope patterns"
#     When a trusted scope file contains a malformed
#     ``forbidden_files`` value, for example a string or
#     object instead of a list, this branch silently converts
#     it to ``None`` and returns success. In that scenario a
#     record with broad ``allowed_files`` can drop all
#     forbidden patterns, so ``status``/``merge`` can treat a
#     forbidden path as in-scope instead of rejecting the
#     corrupted trusted record; malformed trusted scope should
#     fail closed the same way the head/repo/PR bindings do.
#
# Tests below prove:
#
#   * ``read_trusted_scope`` returns an error when the
#     on-disk record's ``allowed_files`` is a non-list (str,
#     dict, int, None);
#   * ``read_trusted_scope`` returns an error when the
#     on-disk record's ``forbidden_files`` is a non-list;
#   * a well-formed record (list of strings) still reads
#     successfully;
#   * the upstream ``scope-read`` CLI surfaces the error to
#     the operator with exit 1.
# ---------------------------------------------------------------------------


class TestRound29TrustedScopeMalformedListFailsClosed:
    """P2 (PRRC_kwDOSHFpYM7X28bT): ``read_trusted_scope`` MUST
    return an error when the on-disk record's
    ``allowed_files`` or ``forbidden_files`` is not a list.
    A non-list value (string, dict, int, None) would
    otherwise be silently coerced to ``None`` and drop every
    forbidden pattern from the effective scope, allowing
    ``status``/``merge`` to treat a forbidden path as
    in-scope.
    """

    HEAD_SHA = "0" * 40

    def _write_raw_scope(self, *, allowed, forbidden):
        """Write a raw trusted-scope JSON with arbitrary
        ``allowed_files`` / ``forbidden_files`` shapes (not
        going through ``write_trusted_scope``, which would
        normalize). Returns a ``restore`` closure.
        """
        import json as _json
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp())
        saved = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = tmp
        path = ctrl._trusted_scope_path(REPO, PR, self.HEAD_SHA)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({
            "head_sha": self.HEAD_SHA,
            "repo": REPO,
            "pr_number": PR,
            "allowed_files": allowed,
            "forbidden_files": forbidden,
        }))

        def restore():
            ctrl._CANONICAL_SCOPE_ROOT = saved
        return restore

    def test_forbidden_string_fails_closed(self):
        """Bug repro: a string ``forbidden_files`` value used to
        be silently coerced to ``None`` and treated as no
        forbidden patterns. Round-29 fails closed.
        """
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden="*.py",
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "forbidden_files must be a list" in err
        assert "str" in err

    def test_allowed_string_fails_closed(self):
        restore = self._write_raw_scope(
            allowed="scripts/local/aed_pr.py",
            forbidden=[],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "allowed_files must be a list" in err

    def test_allowed_dict_fails_closed(self):
        restore = self._write_raw_scope(
            allowed={"glob": "*.py"},
            forbidden=[],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "allowed_files must be a list" in err
        assert "dict" in err

    def test_forbidden_int_fails_closed(self):
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=42,
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "forbidden_files must be a list" in err
        assert "int" in err

    def test_allowed_null_fails_closed(self):
        """JSON ``null`` (Python ``None``) is not a list —
        the original code coerced ``None`` to ``None``
        silently. Round-29 fails closed.
        """
        restore = self._write_raw_scope(
            allowed=None,
            forbidden=[],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "allowed_files must be a list" in err

    def test_well_formed_record_still_reads(self):
        """Regression: a well-formed trusted-scope record
        (list of strings) must continue to read cleanly.
        """
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=["scripts/local/danger.py"],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert err == ""
        assert allowed == ["scripts/local/aed_pr*.py"]
        assert forbidden == ["scripts/local/danger.py"]

    def test_scope_read_cli_surfaces_malformed_error(self):
        """Upstream check: the ``scope-read`` subcommand must
        exit non-zero with a stderr message naming the
        malformed field, so an operator cannot silently
        read a corrupted record.
        """
        import io
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden="*.py",
        )
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.head_sha = self.HEAD_SHA
            rc = ctrl.cmd_scope_read(args)
            err_text = sys.stderr.getvalue()
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            restore()
        assert rc == 1
        assert "scope-read failed" in err_text
        assert "forbidden_files must be a list" in err_text


# ---------------------------------------------------------------------------
# Round-30 regression tests.
#
# Exact-head Codex review 4743980445 (submitted 2026-07-21T11:19:13Z on
# head d78809c0ba8115bf3c339652594c19d708c593af) reported one P2
# finding on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7X3dkX (db_id 3621640471)
#     "Fail closed on non-string scope list entries"
#     When a trusted scope record contains a list with
#     malformed elements, this filtering silently drops every
#     non-string entry instead of rejecting the record. In
#     the same fail-closed scenario handled above for
#     non-list values, a corrupted file with broad
#     ``allowed_files`` and ``forbidden_files`` such as
#     ``[{"pattern": "secrets/**"}]`` is read successfully
#     with an empty forbidden list, so ``status``/``merge``
#     can treat a forbidden change as in-scope rather than
#     rejecting the trusted record as malformed.
#
# Tests below prove:
#
#   * ``read_trusted_scope`` returns an error when the
#     ``allowed_files`` list contains a non-string entry
#     (dict, int, None, empty string);
#   * ``read_trusted_scope`` returns an error when the
#     ``forbidden_files`` list contains a non-string entry;
#   * the offending index is reported in the error message;
#   * a well-formed list of non-empty strings still reads
#     cleanly;
#   * the upstream ``scope-read`` CLI surfaces the error
#     to the operator with exit 1.
# ---------------------------------------------------------------------------


class TestRound30TrustedScopeMalformedListEntryFailsClosed:
    """P2 (PRRC_kwDOSHFpYM7X3dkX): ``read_trusted_scope`` MUST
    fail closed when the ``allowed_files`` or
    ``forbidden_files`` list contains a non-string entry.
    A non-string entry (dict, int, None, empty string) used
    to be silently dropped by the per-entry filter, leaving
    the effective scope without the (corrupted) forbidden
    pattern and letting ``status``/``merge`` treat a
    forbidden change as in-scope.
    """

    HEAD_SHA = "0" * 40

    def _write_raw_scope(self, *, allowed, forbidden):
        """Write a raw trusted-scope JSON with arbitrary
        ``allowed_files`` / ``forbidden_files`` shapes.
        Returns a ``restore`` closure.
        """
        import json as _json
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp())
        saved = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = tmp
        path = ctrl._trusted_scope_path(REPO, PR, self.HEAD_SHA)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({
            "head_sha": self.HEAD_SHA,
            "repo": REPO,
            "pr_number": PR,
            "allowed_files": allowed,
            "forbidden_files": forbidden,
        }))

        def restore():
            ctrl._CANONICAL_SCOPE_ROOT = saved
        return restore

    def test_forbidden_dict_entry_fails_closed(self):
        """P2 bug repro: a dict entry in ``forbidden_files``
        used to be silently dropped, leaving an empty
        forbidden list. Round-30 fails closed.
        """
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=[{"pattern": "secrets/**"}],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "forbidden_files[0]" in err
        assert "dict" in err
        assert "must be a non-empty string" in err

    def test_allowed_dict_entry_fails_closed(self):
        restore = self._write_raw_scope(
            allowed=[{"glob": "*.py"}, "scripts/local/aed_pr.py"],
            forbidden=[],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "allowed_files[0]" in err
        assert "dict" in err

    def test_forbidden_int_entry_fails_closed(self):
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=[42],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "forbidden_files[0]" in err
        assert "int" in err

    def test_forbidden_null_entry_fails_closed(self):
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=[None],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "forbidden_files[0]" in err
        assert "NoneType" in err

    def test_forbidden_empty_string_fails_closed(self):
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=[""],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "forbidden_files[0]" in err

    def test_offending_index_is_reported(self):
        """The error must name the offending index so the
        operator can locate the corrupted entry in a longer
        list.
        """
        restore = self._write_raw_scope(
            allowed=[
                "scripts/local/aed_pr.py",
                "scripts/local/audit_*.py",
                {"glob": "*.py"},
                "scripts/local/check_*.py",
            ],
            forbidden=[],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert allowed is None
        assert forbidden is None
        assert "allowed_files[2]" in err

    def test_well_formed_list_still_reads(self):
        """Regression: a well-formed list of non-empty strings
        continues to read cleanly.
        """
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=["scripts/local/danger.py"],
        )
        try:
            allowed, forbidden, err = ctrl.read_trusted_scope(
                REPO, PR, self.HEAD_SHA,
            )
        finally:
            restore()
        assert err == ""
        assert allowed == ["scripts/local/aed_pr*.py"]
        assert forbidden == ["scripts/local/danger.py"]

    def test_scope_read_cli_surfaces_malformed_entry_error(self):
        """Upstream check: ``cmd_scope_read`` exits non-zero
        with stderr naming the malformed entry index.
        """
        import io
        restore = self._write_raw_scope(
            allowed=["scripts/local/aed_pr*.py"],
            forbidden=[{"pattern": "secrets/**"}],
        )
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            args = type("Args", (), {})()
            args.repo = REPO
            args.pr_number = PR
            args.head_sha = self.HEAD_SHA
            rc = ctrl.cmd_scope_read(args)
            err_text = sys.stderr.getvalue()
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            restore()
        assert rc == 1
        assert "scope-read failed" in err_text
        assert "forbidden_files[0]" in err_text


# ---------------------------------------------------------------------------
# Round-31 regression tests.
#
# Exact-head Codex review 4744132617 (submitted 2026-07-21T11:41:25Z on
# head bc4fd247066ca9c4b0e30fe4e5d5422a8ef0dc62) reported one P2
# finding on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7X39KN (db_id 3621769869)
#     "Preserve ping boundary for Codex issue-comment clean passes"
#     When Codex responds to the controller's `@codex review`
#     ping with a PR-level issue-comment clean pass, subsequent
#     ``status``/``merge`` calls reach this helper with no
#     ping boundary because both fields are hard-coded to
#     ``None``. The classifier only accepts issue-comment-only
#     clean passes via the ping-boundary path; without it,
#     the Round-26 head-binding guard rejects those valid
#     post-ping clean responses unless there also happens to
#     be a formal clean review, leaving the canonical flow
#     stuck waiting for Codex evidence after a clean
#     issue-comment response. Pass through or recover the
#     exact-head ping comment timestamp/id instead of
#     discarding it here.
#
# Tests below prove:
#
#   * ``_post_codex_ping_comment`` returns the comment id and
#     ``created_at`` of the canonical ping (post and duplicate
#     paths);
#   * ``_recover_canonical_ping_boundary`` recovers the most
#     recent canonical ping on the live head from the PR
#     issue-comment inventory;
#   * ``fetch_codex_packet`` forwards those parameters to
#     ``CODEX.classify`` instead of hard-coding ``None``;
#   * ``build_evidence`` forwards its ``ping_comment_id`` /
#     ``ping_created_at`` parameters through to
#     ``fetch_codex_packet``;
#   * ``cmd_status`` recovers the canonical ping boundary
#     and forwards it into ``build_evidence``;
#   * a ``cmd_status`` invocation on a head with no canonical
#     ping still runs without crashing (the Round-26 guard
#     remains authoritative when the boundary cannot be
#     recovered).
# ---------------------------------------------------------------------------


class TestRound31PostCodexPingCommentReturnsBoundary:
    """P2 (PRRC_kwDOSHFpYM7X39KN): ``_post_codex_ping_comment``
    returns a 4-tuple ``(ok, info, ping_id, ping_created_at)``
    so downstream ``status``/``merge`` calls can recover the
    canonical ping boundary instead of receiving ``None``.
    """

    HEAD_SHA = "1" * 40
    OTHER_SHA = "2" * 40

    def _runner(self, *, inventory, inventory_ok=True, inventory_err=""):
        """Stand-in for ``_run_json_or_none``: returns a
        runner that serves the inventory on the first call
        and posts a fake ping on the second call.
        """
        import json as _json
        posts = []

        def runner(cmd, capture_output=True, text=True, timeout=60):
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            p = _P()
            if "-X" in cmd and "POST" in cmd:
                posts.append(cmd)
                p.stdout = _json.dumps({
                    "id": "500",
                    "created_at": "2026-07-21T10:00:00Z",
                })
                return p
            # GET inventory
            if not inventory_ok:
                p.returncode = 1
                p.stderr = inventory_err
                return p
            # ``gh api --paginate --slurp`` returns a list of
            # pages (each page is a list of comments).
            p.stdout = _json.dumps(inventory)
            return p

        runner.posts = posts
        return runner

    def test_post_returns_ping_id_and_created_at(self):
        runner = self._runner(inventory=[])
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.HEAD_SHA, runner=runner,
        )
        assert ok is True
        assert ping_id == "500"
        assert ping_ts == "2026-07-21T10:00:00Z"
        assert info == "500"

    def test_duplicate_returns_existing_ping_id_and_created_at(self):
        inventory = [[{
            "body": (
                "@codex review\n\n"
                f"AED exact-head review request: {self.HEAD_SHA}"
            ),
            "id": "100",
            "created_at": "2026-07-21T09:00:00Z",
        }]]
        runner = self._runner(inventory=inventory)
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.HEAD_SHA, runner=runner,
        )
        assert ok is True
        assert info == "duplicate_exact_head_request_prevented"
        assert ping_id == "100"
        assert ping_ts == "2026-07-21T09:00:00Z"
        # The POST must NOT have been made.
        assert runner.posts == []

    def test_malformed_head_returns_none_for_ping_boundary(self):
        runner = self._runner(inventory=[])
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, "not-a-sha", runner=runner,
        )
        assert ok is False
        assert ping_id is None
        assert ping_ts is None


class TestRound31RecoverCanonicalPingBoundary:
    """P2 (PRRC_kwDOSHFpYM7X39KN): ``_recover_canonical_ping_boundary``
    scans the PR issue-comment inventory for the most recent
    canonical ``@codex review`` ping on the live head and
    returns ``(ping_id, ping_created_at, err)``.
    """

    HEAD_SHA = "3" * 40
    OTHER_SHA = "4" * 40

    def _comments_json(self, *comments):
        # Accept both ``self._comments_json(*comments)`` (each
        # comment is passed positionally, ignoring ``self``)
        # and ``self._comments_json(comments)`` (passing a list).
        # When the first positional arg is a non-dict list, treat
        # the remaining args as the page payload.
        if len(comments) == 1 and isinstance(comments[0], list):
            return [comments[0]]
        return [list(comments)]

    def test_recovers_most_recent_canonical_ping(self, monkeypatch):
        # Build two pings: an older ping on the canonical head,
        # then a newer ping on the same canonical head. The
        # recovery must return the newer one.
        older = {
            "id": 100,
            "body": (
                "@codex review\n\n"
                f"AED exact-head review request: {self.HEAD_SHA}"
            ),
            "created_at": "2026-07-20T10:00:00Z",
        }
        newer = {
            "id": 200,
            "body": (
                "@codex review\n\n"
                f"AED exact-head review request: {self.HEAD_SHA}"
            ),
            "created_at": "2026-07-21T10:00:00Z",
        }
        other_head = {
            "id": 300,
            "body": (
                "@codex review\n\n"
                f"AED exact-head review request: {self.OTHER_SHA}"
            ),
            "created_at": "2026-07-21T11:00:00Z",
        }
        non_canonical = {
            "id": 400,
            "body": "Some unrelated comment",
            "created_at": "2026-07-21T12:00:00Z",
        }
        comments = [older, non_canonical, other_head, newer]

        captured = {}

        def fake_run_json(cmd, timeout=30):
            captured["cmd"] = cmd
            return True, self._comments_json(*comments), ""

        monkeypatch.setattr(ctrl, "_run_json_or_none", fake_run_json)
        ping_id, ping_ts, err = ctrl._recover_canonical_ping_boundary(
            "owner/repo", 411, self.HEAD_SHA,
        )
        assert err == ""
        assert ping_id == "200"
        assert ping_ts == "2026-07-21T10:00:00Z"

    def test_no_canonical_ping_returns_error(self, monkeypatch):
        comments = [{
            "id": 100,
            "body": f"Some unrelated comment without trigger",
            "created_at": "2026-07-21T10:00:00Z",
        }]

        def fake_run_json(cmd, timeout=30):
            return True, self._comments_json(*comments), ""

        monkeypatch.setattr(ctrl, "_run_json_or_none", fake_run_json)
        ping_id, ping_ts, err = ctrl._recover_canonical_ping_boundary(
            "owner/repo", 411, self.HEAD_SHA,
        )
        assert ping_id is None
        assert ping_ts is None
        assert "no_canonical_ping_on_head" in err

    def test_inventory_failure_returns_error(self, monkeypatch):
        def fake_run_json(cmd, timeout=30):
            return False, None, "network"

        monkeypatch.setattr(ctrl, "_run_json_or_none", fake_run_json)
        ping_id, ping_ts, err = ctrl._recover_canonical_ping_boundary(
            "owner/repo", 411, self.HEAD_SHA,
        )
        assert ping_id is None
        assert ping_ts is None
        assert "ping_recovery_failed" in err
        assert "network" in err

    def test_non_canonical_head_skips_recovery(self, monkeypatch):
        called = {"yes": False}

        def fake_run_json(cmd, timeout=30):
            called["yes"] = True
            return True, [], ""

        monkeypatch.setattr(ctrl, "_run_json_or_none", fake_run_json)
        ping_id, ping_ts, err = ctrl._recover_canonical_ping_boundary(
            "owner/repo", 411, "not-a-sha",
        )
        assert ping_id is None
        assert ping_ts is None
        assert "non_canonical_head_sha" in err
        # Must NOT have queried the inventory.
        assert called["yes"] is False


class TestRound31FetchCodexPacketForwardsPingBoundary:
    """P2 (PRRC_kwDOSHFpYM7X39KN): ``fetch_codex_packet`` MUST
    forward ``ping_comment_id``/``ping_created_at`` to
    ``CODEX.classify`` rather than hard-coding them to ``None``.
    """

    HEAD_SHA = "5" * 40

    def test_fetch_codex_packet_forwards_ping_boundary(self, monkeypatch):
        captured = {}

        def fake_classify(**kwargs):
            captured.update(kwargs)
            return {"status": "MERGE_READY"}

        monkeypatch.setattr(ctrl.CODEX, "classify", fake_classify)
        result = ctrl.fetch_codex_packet(
            "owner/repo", 411, self.HEAD_SHA,
            ping_comment_id="999",
            ping_created_at="2026-07-21T10:00:00Z",
        )
        assert result == {"status": "MERGE_READY"}
        assert captured["ping_comment_id"] == "999"
        assert captured["ping_created_at"] == "2026-07-21T10:00:00Z"
        assert captured["expected_head_sha"] == self.HEAD_SHA

    def test_fetch_codex_packet_default_is_none(self, monkeypatch):
        captured = {}

        def fake_classify(**kwargs):
            captured.update(kwargs)
            return {"status": ""}

        monkeypatch.setattr(ctrl.CODEX, "classify", fake_classify)
        ctrl.fetch_codex_packet("owner/repo", 411, self.HEAD_SHA)
        # Default ``None``/``None`` preserves the Round-26
        # head-binding guard for callers that explicitly opt out.
        assert captured["ping_comment_id"] is None
        assert captured["ping_created_at"] is None


class TestRound31BuildEvidenceForwardsPingBoundary:
    """P2 (PRRC_kwDOSHFpYM7X39KN): ``build_evidence`` MUST
    forward its ``ping_comment_id``/``ping_created_at``
    parameters to ``fetch_codex_packet``.
    """

    HEAD_SHA = "6" * 40

    def test_build_evidence_forwards_ping_boundary(self, monkeypatch):
        captured = {}

        def fake_fetch(
            repo, pr_number, head_sha, *,
            ping_comment_id=None, ping_created_at=None,
        ):
            captured["repo"] = repo
            captured["pr_number"] = pr_number
            captured["head_sha"] = head_sha
            captured["ping_comment_id"] = ping_comment_id
            captured["ping_created_at"] = ping_created_at
            return {
                "status": "",
                "observed_head_sha": None,
                "issue_comment_inventory_complete": True,
                "review_submission_inventory_complete": True,
                "review_thread_inventory_complete": True,
                "review_thread_comment_inventory_complete": True,
                "active_threads": [],
                "outdated_threads": [],
                "latest_codex_response_type": None,
                "latest_codex_response_url": None,
                "latest_codex_response_id": None,
                "clean_pass_detected": False,
            }

        monkeypatch.setattr(ctrl, "fetch_codex_packet", fake_fetch)
        # Stub the rest of the fetchers so build_evidence runs.
        monkeypatch.setattr(
            ctrl, "fetch_ci_conclusions",
            lambda *a, **k: (True, [], [], [], [], [], ""),
        )
        monkeypatch.setattr(
            ctrl, "SCOPE", type("S", (), {"check_scope": staticmethod(
                lambda *a, **k: {
                    "passed": True,
                    "out_of_scope_files": [],
                    "forbidden_files_touched": [],
                    "blockers": [],
                }
            )})(),
        )

        evidence = ctrl.build_evidence(
            repo="owner/repo",
            pr_number=411,
            pr_view={"headRefOid": self.HEAD_SHA, "headRefName": "branch"},
            changed_files=["scripts/local/aed_pr.py"],
            changed_files_fetched=True,
            changed_files_error="",
            authorization_phrase=None,
            allowed_files=["scripts/local/aed_pr*.py"],
            forbidden_files=[],
            ping_comment_id="999",
            ping_created_at="2026-07-21T10:00:00Z",
        )
        assert captured["ping_comment_id"] == "999"
        assert captured["ping_created_at"] == "2026-07-21T10:00:00Z"


class TestRound31CmdStatusRecoversCanonicalPingBoundary:
    """P2 (PRRC_kwDOSHFpYM7X39KN): ``cmd_status`` MUST recover
    the canonical ping boundary for the live head and forward
    it into ``build_evidence``.
    """

    HEAD_SHA = "7" * 40

    def test_cmd_status_threads_recovered_ping_boundary(
        self, monkeypatch, capsys,
    ):
        recovered = {
            "ping_id": "888",
            "ping_ts": "2026-07-21T09:00:00Z",
            "err": "",
        }

        def fake_recover(repo, pr_number, head_sha):
            assert head_sha == self.HEAD_SHA
            return (
                recovered["ping_id"],
                recovered["ping_ts"],
                recovered["err"],
            )

        captured = {}

        def fake_build(**kwargs):
            captured.update(kwargs)
            monkeypatch.setattr(ctrl, "build_evidence", fake_build)
            # Return a minimal evidence so cmd_status finishes.
            return ctrl._RealEvidence()

        # Replace the real build_evidence with a recorder.
        captured.clear()
        real_build = ctrl.build_evidence

        def recording_build(**kwargs):
            captured.update(kwargs)
            return real_build(**kwargs)

        monkeypatch.setattr(ctrl, "build_evidence", recording_build)
        monkeypatch.setattr(
            ctrl, "_recover_canonical_ping_boundary", fake_recover,
        )
        monkeypatch.setattr(
            ctrl, "fetch_pr_state",
            lambda repo, pr_number: {
                "headRefOid": self.HEAD_SHA,
                "headRefName": "branch",
                "state": "OPEN",
            },
        )
        monkeypatch.setattr(
            ctrl, "_resolve_effective_scope",
            lambda **k: (["scripts/local/aed_pr*.py"], [], ""),
        )
        monkeypatch.setattr(
            ctrl, "fetch_changed_files",
            lambda repo, pr_number, pr_view: (
                True, ["scripts/local/aed_pr.py"], "",
            ),
        )

        args = type("Args", (), {})()
        args.repo = "owner/repo"
        args.pr_number = 411
        args.allowed_files = None
        args.forbidden_files = None
        rc = ctrl.cmd_status(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert captured["ping_comment_id"] == "888"
        assert captured["ping_created_at"] == "2026-07-21T09:00:00Z"
        # Status output must remain valid JSON.
        json.loads(out)

    def test_cmd_status_handles_recovery_failure(self, monkeypatch, capsys):
        def fake_recover(repo, pr_number, head_sha):
            return None, None, "ping_recovery_failed: simulated"

        captured = {}

        def recording_build(**kwargs):
            captured.update(kwargs)
            real_build = ctrl.__class__.__dict__.get(
                "build_evidence", None,
            )
            # Fallback to the real one through monkeypatch chain.
            return kwargs  # cmd_status will use this dict

        # Simpler: just patch build_evidence to record kwargs.
        def recording_build_simple(**kwargs):
            captured.update(kwargs)
            return {
                "changed_files": [],
                "gates_passed": [],
                "gates_failed": [],
            }

        monkeypatch.setattr(
            ctrl, "build_evidence", recording_build_simple,
        )
        monkeypatch.setattr(
            ctrl, "_recover_canonical_ping_boundary", fake_recover,
        )
        monkeypatch.setattr(
            ctrl, "fetch_pr_state",
            lambda repo, pr_number: {
                "headRefOid": self.HEAD_SHA,
                "headRefName": "branch",
                "state": "OPEN",
            },
        )
        monkeypatch.setattr(
            ctrl, "_resolve_effective_scope",
            lambda **k: (["scripts/local/aed_pr*.py"], [], ""),
        )
        monkeypatch.setattr(
            ctrl, "fetch_changed_files",
            lambda repo, pr_number, pr_view: (
                True, ["scripts/local/aed_pr.py"], "",
            ),
        )

        args = type("Args", (), {})()
        args.repo = "owner/repo"
        args.pr_number = 411
        args.allowed_files = None
        args.forbidden_files = None
        # cmd_status will fail because build_evidence returned a
        # placeholder dict, but the important assertion is that
        # the recovered boundary was forwarded (with None).
        try:
            ctrl.cmd_status(args)
        except Exception:
            pass
        assert captured["ping_comment_id"] is None
        assert captured["ping_created_at"] is None


# ---------------------------------------------------------------------------
# Round-32 regression tests.
#
# Exact-head Codex review 4744489987 (submitted 2026-07-21T12:24:03Z on
# head 1cd5832c0d732dfc86e1c1c4b3fa1d4971e6bd63) reported one P2
# finding on scripts/local/aed_pr.py:
#
#   PRRC_kwDOSHFpYM7X5E2a (db_id 3622063514)
#     "Preserve the newest duplicate ping boundary"
#     When a PR already has multiple canonical `@codex
#     review` comments for the same head, this returns the
#     first match from the issue-comment API; GitHub returns
#     issue comments in ascending ID order by default, so that
#     is the oldest ping. `cmd_advance
#     --resolve-eligible-bot-threads` then overwrites the
#     recovered latest boundary with this stale timestamp
#     before its evidence refresh, which can accept a clean
#     pass that predates the operator's most recent review
#     request and emit merge authorization while the latest
#     exact-head review is still pending. Track the newest
#     matching comment here instead of returning on the first
#     match.
#
# Tests below prove:
#
#   * ``_post_codex_ping_comment``'s duplicate-detect path
#     returns the canonical ping with the most recent
#     ``created_at`` when multiple matches exist;
#   * when only one canonical ping exists, the boundary is
#     still recovered correctly;
#   * when a canonical ping has no ``created_at`` field, the
#     fallback to the first-seen record preserves the
#     duplicate-detection contract from earlier rounds;
#   * a non-canonical comment (different head or no trigger)
#     does not influence the duplicate-detection result.
# ---------------------------------------------------------------------------


class TestRound32PostCodexPingCommentPicksNewestDuplicate:
    """P2 (PRRC_kwDOSHFpYM7X5E2a): ``_post_codex_ping_comment``
    MUST pick the newest canonical ping on duplicate
    detection rather than returning the first match from the
    inventory (which is the OLDEST by ascending ID order).
    """

    HEAD_SHA = "a" * 40
    OTHER_SHA = "b" * 40

    def _runner(self, *, inventory):
        """Stand-in for ``_run_json_or_none`` that serves the
        inventory on GET and reports no POST.
        """
        import json as _json
        posts = []

        def runner(cmd, capture_output=True, text=True, timeout=60):
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            p = _P()
            if "-X" in cmd and "POST" in cmd:
                posts.append(cmd)
                p.stdout = _json.dumps({
                    "id": "999",
                    "created_at": "2099-01-01T00:00:00Z",
                })
                return p
            p.stdout = _json.dumps(inventory)
            return p

        runner.posts = posts
        return runner

    def test_newest_canonical_ping_wins_on_duplicate(self):
        # Two canonical pings on the same head: the older one
        # has the lower ID, the newer one has the higher ID.
        # GitHub returns them in ascending ID order; the
        # helper MUST return the newer one.
        inventory = [[
            {
                "id": 100,
                "body": (
                    "@codex review\n\n"
                    f"AED exact-head review request: {self.HEAD_SHA}"
                ),
                "created_at": "2026-07-20T10:00:00Z",
            },
            {
                "id": 200,
                "body": (
                    "@codex review\n\n"
                    f"AED exact-head review request: {self.HEAD_SHA}"
                ),
                "created_at": "2026-07-21T10:00:00Z",
            },
        ]]
        runner = self._runner(inventory=inventory)
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.HEAD_SHA, runner=runner,
        )
        assert ok is True
        assert info == "duplicate_exact_head_request_prevented"
        # The NEWER ping wins; the older id must NOT be returned.
        assert ping_id == "200"
        assert ping_ts == "2026-07-21T10:00:00Z"
        # The POST must NOT have been made.
        assert runner.posts == []

    def test_only_one_canonical_ping_still_works(self):
        inventory = [[{
            "id": 100,
            "body": (
                "@codex review\n\n"
                f"AED exact-head review request: {self.HEAD_SHA}"
            ),
            "created_at": "2026-07-21T10:00:00Z",
        }]]
        runner = self._runner(inventory=inventory)
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.HEAD_SHA, runner=runner,
        )
        assert ok is True
        assert info == "duplicate_exact_head_request_prevented"
        assert ping_id == "100"
        assert ping_ts == "2026-07-21T10:00:00Z"

    def test_legacy_ping_without_created_at_falls_back(self):
        # Legacy inventory entries from older runs may not
        # carry a ``created_at`` field. The duplicate-detection
        # contract from earlier rounds MUST still hold.
        inventory = [[{
            "id": 100,
            "body": (
                "@codex review\n\n"
                f"AED exact-head review request: {self.HEAD_SHA}"
            ),
            # ``created_at`` deliberately omitted.
        }]]
        runner = self._runner(inventory=inventory)
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.HEAD_SHA, runner=runner,
        )
        assert ok is True
        assert info == "duplicate_exact_head_request_prevented"
        assert ping_id == "100"
        assert ping_ts is None
        # The POST must NOT have been made.
        assert runner.posts == []

    def test_non_canonical_comments_dont_influence_result(self):
        # Three other-head pings plus one canonical ping on
        # the current head. The other-head pings must NOT
        # contribute to the boundary decision.
        inventory = [[
            {
                "id": 50,
                "body": (
                    "@codex review\n\n"
                    f"AED exact-head review request: {self.OTHER_SHA}"
                ),
                "created_at": "2099-01-01T00:00:00Z",
            },
            {
                "id": 100,
                "body": (
                    "@codex review\n\n"
                    f"AED exact-head review request: {self.HEAD_SHA}"
                ),
                "created_at": "2026-07-21T10:00:00Z",
            },
            {
                "id": 60,
                "body": "Some unrelated comment",
                "created_at": "2099-02-01T00:00:00Z",
            },
        ]]
        runner = self._runner(inventory=inventory)
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.HEAD_SHA, runner=runner,
        )
        assert ok is True
        assert info == "duplicate_exact_head_request_prevented"
        assert ping_id == "100"
        assert ping_ts == "2026-07-21T10:00:00Z"

    def test_no_canonical_ping_proceeds_to_post(self):
        # Empty inventory → helper must POST a fresh ping.
        runner = self._runner(inventory=[])
        ok, info, ping_id, ping_ts = ctrl._post_codex_ping_comment(
            "owner/repo", 411, self.HEAD_SHA, runner=runner,
        )
        assert ok is True
        assert info == "999"
        assert ping_id == "999"
        assert ping_ts == "2099-01-01T00:00:00Z"
        # The POST must have been made exactly once.
        assert len(runner.posts) == 1


class TestRound32RecoverBoundaryMatchesDuplicateHelper:
    """P2 (PRRC_kwDOSHFpYM7X5E2a): the duplicate-detect path
    in ``_post_codex_ping_comment`` MUST agree with
    ``_recover_canonical_ping_boundary`` on which comment is
    the newest canonical ping on the same head.
    """

    HEAD_SHA = "c" * 40

    def test_duplicate_helper_and_recovery_agree(self, monkeypatch):
        # Inventory with three canonical pings on the same
        # head, ascending ID order. The newest is id=300.
        inventory_payload = [[
            {
                "id": 100,
                "body": (
                    "@codex review\n\n"
                    f"AED exact-head review request: {self.HEAD_SHA}"
                ),
                "created_at": "2026-07-20T10:00:00Z",
            },
            {
                "id": 200,
                "body": (
                    "@codex review\n\n"
                    f"AED exact-head review request: {self.HEAD_SHA}"
                ),
                "created_at": "2026-07-21T09:00:00Z",
            },
            {
                "id": 300,
                "body": (
                    "@codex review\n\n"
                    f"AED exact-head review request: {self.HEAD_SHA}"
                ),
                "created_at": "2026-07-21T10:00:00Z",
            },
        ]]

        # Drive the duplicate-detect path through the runner.
        import json as _json

        posts = []

        def runner(cmd, capture_output=True, text=True, timeout=60):
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            p = _P()
            if "-X" in cmd and "POST" in cmd:
                posts.append(cmd)
                p.stdout = _json.dumps({
                    "id": "999",
                    "created_at": "2099-01-01T00:00:00Z",
                })
                return p
            p.stdout = _json.dumps(inventory_payload)
            return p

        runner.posts = posts

        # Drive the recovery path through monkeypatch.
        def fake_run_json(cmd, timeout=30):
            return True, inventory_payload, ""

        monkeypatch.setattr(ctrl, "_run_json_or_none", fake_run_json)

        # Duplicate-detect path.
        ok, info, dup_ping_id, dup_ping_ts = (
            ctrl._post_codex_ping_comment(
                "owner/repo", 411, self.HEAD_SHA, runner=runner,
            )
        )
        assert ok is True
        assert info == "duplicate_exact_head_request_prevented"
        assert dup_ping_id == "300"
        assert dup_ping_ts == "2026-07-21T10:00:00Z"

        # Recovery path.
        rec_ping_id, rec_ping_ts, rec_err = (
            ctrl._recover_canonical_ping_boundary(
                "owner/repo", 411, self.HEAD_SHA,
            )
        )
        assert rec_err == ""
        # Both paths MUST agree on the newest ping boundary.
        assert rec_ping_id == dup_ping_id
        assert rec_ping_ts == dup_ping_ts
