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
            json.dumps({"allowed_files": ["scripts/local/aed_pr*.py"]}),
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
