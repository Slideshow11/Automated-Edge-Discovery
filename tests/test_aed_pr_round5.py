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
