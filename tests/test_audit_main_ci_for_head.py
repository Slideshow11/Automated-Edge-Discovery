"""
Tests for scripts/local/audit_main_ci_for_head.py

Covers:
1. Green status: two runs for exact head, both completed success.
2. Pending status: run remains in_progress through max polls, sleep called N-1 times.
3. Failed status: one run completed failure.
4. Missing required workflow: required workflow absent.
5. No runs for head: no runs for the exact head at all.
6. Exact SHA filtering: mixed SHAs, only exact head evaluated.
7. Invalid SHA: short SHA rejected.
8. poll-seconds > 30 rejected.
9. gh subprocess failure: ERROR_TOOL_FAILURE.
10. Invalid gh JSON: ERROR_TOOL_FAILURE.
11. JSON and Markdown artifacts written correctly.
12. Source safety: no forbidden strings in source.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Make the module under test importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
import audit_main_ci_for_head as mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

REPO = "owner/repo"
HEAD = "deadbeef" * 5  # 40 chars: deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
HEAD_OTHER = "abcd1234" * 5  # 40 chars different


def make_run(workflow: str, status: str, conclusion: str, head_sha: str = HEAD,
             created_at: str = "2026-06-01T22:00:00Z",
             updated_at: str = "2026-06-01T22:05:00Z",
             database_id: int | None = None) -> Dict[str, Any]:
    dbid = database_id if database_id is not None else (1000000 + hash(workflow) % 1000000)
    return {
        "databaseId": dbid,
        "name": workflow,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "headSha": head_sha,
        "headBranch": "main",
        "event": "push",
        "createdAt": created_at,
        "updatedAt": updated_at,
        "url": f"https://github.com/{REPO}/actions/runs/{dbid}",
        "displayTitle": f"push to main: {workflow}",
    }


def write_outputs_only(packet: Dict[str, Any], json_path: str, md_path: str) -> bool:
    from pathlib import Path as _P
    _P(json_path).parent.mkdir(parents=True, exist_ok=True)
    _P(md_path).parent.mkdir(parents=True, exist_ok=True)
    _P(json_path).write_text(json.dumps(packet, indent=2) + "\n")
    _P(md_path).write_text(mod.render_markdown(packet))
    return True


# A small fake `time.sleep` to track calls
class FakeSleep:
    def __init__(self) -> None:
        self.calls: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(float(seconds))


# ---------------------------------------------------------------------------
# 1. Green status
# ---------------------------------------------------------------------------


def test_green_status_two_runs_completed_success(tmp_path, monkeypatch):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    runs = [
        make_run("CI", "completed", "success"),
        make_run("Edge Discovery audit tests", "completed", "success"),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_GREEN
    assert packet["polls_used"] == 1
    assert len(packet["successful_runs"]) == 2
    assert len(packet["failed_runs"]) == 0
    assert len(packet["pending_runs"]) == 0
    assert sleep.calls == []  # no sleep when green on first poll


# ---------------------------------------------------------------------------
# 2. Pending status
# ---------------------------------------------------------------------------


def test_pending_status_through_max_polls(tmp_path, monkeypatch):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    runs = [
        make_run("CI", "in_progress", ""),
        make_run("Edge Discovery audit tests", "in_progress", ""),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    max_polls = 4
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", str(max_polls),
            "--poll-seconds", "7",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_PENDING
    assert packet["polls_used"] == max_polls
    assert len(sleep.calls) == max_polls - 1
    # Never sleeps more than poll_seconds
    assert all(s <= 7 for s in sleep.calls)
    # All sleeps equal poll_seconds in this design
    assert all(s == 7 for s in sleep.calls)


# ---------------------------------------------------------------------------
# 3. Failed status
# ---------------------------------------------------------------------------


def test_failed_status_one_run_completed_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        make_run("CI", "completed", "failure"),
        make_run("Edge Discovery audit tests", "completed", "success"),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_FAILED
    assert len(packet["failed_runs"]) == 1
    assert len(packet["successful_runs"]) == 1


# ---------------------------------------------------------------------------
# 4. Missing required workflow
# ---------------------------------------------------------------------------


def test_missing_required_workflow(tmp_path, monkeypatch):
    """Existing test: --required-workflow CI is requested but only Edge
    Discovery audit tests exists. With the new behavior the audit must
    exhaust the polling bound before declaring the workflow missing.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    runs = [
        make_run("Edge Discovery audit tests", "completed", "success"),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    max_polls = 3
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--required-workflow", "CI",
            "--max-polls", str(max_polls),
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_MISSING
    assert packet["missing_required_workflows"] == ["CI"]
    # New behavior: polls_used equals max_polls and sleep is called N-1 times
    assert packet["polls_used"] == max_polls
    assert len(sleep.calls) == max_polls - 1


def test_missing_required_workflow_appears_on_later_poll(tmp_path, monkeypatch):
    """New behavior: required workflow appears on a later poll and completes
    successfully. Final status must be MAIN_CI_AUDIT_GREEN, with the
    successful run counted and no missing list.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    # State: poll 1 has only the unrelated workflow; poll 2 has CI completed
    poll_1 = [make_run("Unrelated", "completed", "success")]
    poll_2 = [
        make_run("Unrelated", "completed", "success"),
        make_run("CI", "completed", "success"),
    ]
    responses = [poll_1, poll_2]
    counter = {"i": 0}

    def mock_gh(args):
        idx = counter["i"]
        counter["i"] += 1
        return responses[idx] if idx < len(responses) else responses[-1]

    monkeypatch.setattr(mod, "run_gh_run_list", mock_gh)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    max_polls = 4
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--required-workflow", "CI",
            "--max-polls", str(max_polls),
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_GREEN
    assert packet["polls_used"] == 2
    assert packet["missing_required_workflows"] == []
    # runs_for_head includes Unrelated + CI; successful_runs includes only the
    # required CI run.
    assert len(packet["runs_for_head"]) == 2
    assert len(packet["successful_runs"]) == 1
    assert packet["successful_runs"][0]["workflowName"] == "CI"
    assert len(sleep.calls) == 1  # one sleep between poll 1 and poll 2


def test_required_workflow_pending_at_final_poll(tmp_path, monkeypatch):
    """New behavior: required workflow appears but is still in_progress at
    the final poll. Final status must be HOLD_MAIN_CI_PENDING (not MISSING).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    # CI is present but in_progress across all polls
    in_progress_run = make_run("CI", "in_progress", "")
    responses = [[in_progress_run]] * 3
    counter = {"i": 0}

    def mock_gh(args):
        idx = counter["i"]
        counter["i"] += 1
        return responses[idx] if idx < len(responses) else responses[-1]

    monkeypatch.setattr(mod, "run_gh_run_list", mock_gh)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    max_polls = 3
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--required-workflow", "CI",
            "--max-polls", str(max_polls),
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_PENDING
    assert packet["missing_required_workflows"] == []
    assert packet["polls_used"] == max_polls
    assert len(sleep.calls) == max_polls - 1
    assert len(packet["pending_runs"]) == 1


def test_required_workflow_appears_with_failure(tmp_path, monkeypatch):
    """New behavior: required workflow appears with a failure on a later
    poll. Final status must be HOLD_MAIN_CI_FAILED and reported immediately.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    # Poll 1: only unrelated. Poll 2: CI present but failed.
    poll_1 = [make_run("Unrelated", "completed", "success")]
    poll_2 = [
        make_run("Unrelated", "completed", "success"),
        make_run("CI", "completed", "failure"),
    ]
    responses = [poll_1, poll_2]
    counter = {"i": 0}

    def mock_gh(args):
        idx = counter["i"]
        counter["i"] += 1
        return responses[idx] if idx < len(responses) else responses[-1]

    monkeypatch.setattr(mod, "run_gh_run_list", mock_gh)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    max_polls = 5
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--required-workflow", "CI",
            "--max-polls", str(max_polls),
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_FAILED
    assert packet["polls_used"] == 2
    assert len(packet["failed_runs"]) == 1
    # Polling stops immediately on failure — sleep is called only once
    # (between poll 1 and poll 2), not max_polls-1 times.
    assert len(sleep.calls) == 1


def test_missing_required_workflow_partial_appearances_through_polls(tmp_path, monkeypatch):
    """New behavior: when one of several required workflows is missing
    throughout, the audit must report MISSING with only the still-missing
    workflow listed, not all required workflows.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    # 'WFA' is present and completes success; 'CI' is never present.
    wfa = make_run("WFA", "completed", "success")
    responses = [[wfa]] * 3
    counter = {"i": 0}

    def mock_gh(args):
        idx = counter["i"]
        counter["i"] += 1
        return responses[idx] if idx < len(responses) else responses[-1]

    monkeypatch.setattr(mod, "run_gh_run_list", mock_gh)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    max_polls = 3
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--required-workflow", "CI",
            "--required-workflow", "WFA",
            "--max-polls", str(max_polls),
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_MISSING
    assert packet["missing_required_workflows"] == ["CI"]
    # WFA is reported as successful
    assert len(packet["successful_runs"]) == 1
    assert packet["polls_used"] == max_polls


# ---------------------------------------------------------------------------
# 5. No runs for head
# ---------------------------------------------------------------------------


def test_no_runs_for_head(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        make_run("CI", "completed", "success", head_sha=HEAD_OTHER),
        make_run("Edge Discovery audit tests", "completed", "success", head_sha=HEAD_OTHER),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_NO_RUNS
    assert packet["runs_for_head"] == []


# ---------------------------------------------------------------------------
# 6. Exact SHA filtering
# ---------------------------------------------------------------------------


def test_exact_sha_filtering(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        make_run("CI", "completed", "success", head_sha=HEAD),
        make_run("CI", "completed", "failure", head_sha=HEAD_OTHER),
        make_run("Edge Discovery audit tests", "completed", "success", head_sha=HEAD),
        make_run("Other Workflow", "completed", "failure", head_sha=HEAD_OTHER),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_GREEN
    # Only the two runs for the exact head are evaluated
    assert len(packet["runs_for_head"]) == 2
    assert {r["workflowName"] for r in packet["runs_for_head"]} == {
        "CI",
        "Edge Discovery audit tests",
    }
    # The HEAD_OTHER failure is filtered out
    assert len(packet["failed_runs"]) == 0


# ---------------------------------------------------------------------------
# 7. Invalid SHA
# ---------------------------------------------------------------------------


def test_invalid_short_sha_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", FakeSleep())
    fake_gh = MagicMock(return_value=[])
    monkeypatch.setattr(mod, "run_gh_run_list", fake_gh)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", "deadbeef",  # too short
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 2
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_ERROR_INVALID_ARGS
    fake_gh.assert_not_called()


# ---------------------------------------------------------------------------
# 8. poll-seconds > 30 rejected
# ---------------------------------------------------------------------------


def test_poll_seconds_above_30_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", FakeSleep())
    fake_gh = MagicMock(return_value=[])
    monkeypatch.setattr(mod, "run_gh_run_list", fake_gh)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "60",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 2
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_ERROR_INVALID_ARGS
    fake_gh.assert_not_called()


# ---------------------------------------------------------------------------
# 9. gh subprocess failure
# ---------------------------------------------------------------------------


def test_gh_subprocess_failure_returns_tool_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", FakeSleep())

    def boom(args):
        raise RuntimeError("gh run list failed (rc=1): HTTP 500")

    monkeypatch.setattr(mod, "run_gh_run_list", boom)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0  # writes report and exits 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_ERROR_TOOL_FAILURE
    assert any("HTTP 500" in e for e in packet["errors"])


# ---------------------------------------------------------------------------
# 10. Invalid gh JSON
# ---------------------------------------------------------------------------


def test_invalid_gh_json_returns_tool_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", FakeSleep())

    def bad_json(args):
        raise RuntimeError("gh run list returned invalid JSON: <err>")

    monkeypatch.setattr(mod, "run_gh_run_list", bad_json)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_ERROR_TOOL_FAILURE
    assert any("invalid JSON" in e for e in packet["errors"])


# ---------------------------------------------------------------------------
# 11. JSON and Markdown artifacts written
# ---------------------------------------------------------------------------


def test_artifacts_written_correctly(tmp_path, monkeypatch):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    runs = [make_run("CI", "completed", "success")]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    assert Path(json_out).exists()
    assert Path(md_out).exists()
    packet = json.loads(Path(json_out).read_text())
    assert packet["packet_kind"] == "aed.main_ci.audit.v0"
    assert packet["schema_version"] == 1
    assert packet["status"] == mod.STATUS_GREEN
    md_text = Path(md_out).read_text()
    assert "MAIN_CI_AUDIT_GREEN" in md_text
    assert "Final status" in md_text
    assert HEAD in md_text


# ---------------------------------------------------------------------------
# 12. Source safety
# ---------------------------------------------------------------------------


def test_source_safety_no_forbidden_strings():
    src = Path(mod.__file__).read_text()
    eq_token = "="
    forbidden = [
        "shell" + eq_token + "True",
        "gh run watch",
        "gh pr checks --watch",
        "gh pr merge",
        "gh api",
        "git push",
    ]
    for s in forbidden:
        assert s not in src, f"source contains forbidden substring: {s!r}"


# ---------------------------------------------------------------------------
# Bonus: pure-function unit tests (no monkeypatching needed)
# ---------------------------------------------------------------------------


def test_validate_args_accepts_valid():
    args = mod.parse_args(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--output-json", "/tmp/x.json",
            "--output-md", "/tmp/x.md",
        ]
    )
    ok, msg = mod.validate_args(args)
    assert ok is True, msg


def test_validate_args_rejects_bad_repo():
    args = mod.parse_args(
        [
            "--repo", "no-slash",
            "--head-sha", HEAD,
            "--output-json", "/tmp/x.json",
            "--output-md", "/tmp/x.md",
        ]
    )
    ok, msg = mod.validate_args(args)
    assert ok is False
    assert "OWNER/REPO" in msg or "repo" in msg


def test_filter_runs_for_head_case_insensitive():
    runs = [
        make_run("CI", "completed", "success", head_sha=HEAD.upper()),
        make_run("X", "completed", "success", head_sha=HEAD_OTHER),
    ]
    out = mod.filter_runs_for_head(runs, HEAD)
    assert len(out) == 1
    assert out[0]["workflowName"] == "CI"


def test_classify_runs_mixed_pending_and_success():
    runs = [
        make_run("CI", "in_progress", ""),
        make_run("Edge Discovery audit tests", "completed", "success"),
    ]
    status, pending, failed, successful = mod.classify_runs(runs)
    assert status == mod.STATUS_HOLD_PENDING
    assert len(pending) == 1
    assert len(successful) == 1
    assert len(failed) == 0


def test_classify_runs_all_success():
    runs = [
        make_run("CI", "completed", "success"),
        make_run("Edge Discovery audit tests", "completed", "success"),
    ]
    status, pending, failed, successful = mod.classify_runs(runs)
    assert status == mod.STATUS_GREEN
    assert len(pending) == 0
    assert len(successful) == 2
    assert len(failed) == 0


def test_classify_runs_one_failure():
    runs = [
        make_run("CI", "completed", "failure"),
        make_run("Edge Discovery audit tests", "completed", "success"),
    ]
    status, pending, failed, successful = mod.classify_runs(runs)
    assert status == mod.STATUS_HOLD_FAILED
    assert len(failed) == 1
    assert len(successful) == 1
    assert len(pending) == 0


def test_missing_required_workflows_helper():
    missing = mod.missing_required_workflows(
        ["CI", "WFA"], ["CI", "Edge Discovery audit tests"]
    )
    assert missing == ["WFA"]


def test_render_markdown_includes_status():
    packet = mod.build_packet(
        args=mod.parse_args(
            [
                "--repo", REPO,
                "--head-sha", HEAD,
                "--output-json", "/tmp/x.json",
                "--output-md", "/tmp/x.md",
            ]
        ),
        status=mod.STATUS_GREEN,
        runs_for_head=[],
        pending_runs=[],
        failed_runs=[],
        successful_runs=[],
        missing_required=[],
        polls_used=1,
        commands_run=[["gh", "run", "list"]],
        errors=[],
    )
    md = mod.render_markdown(packet)
    assert "MAIN_CI_AUDIT_GREEN" in md
    assert "Commands run" in md
    assert HEAD in md


# ---------------------------------------------------------------------------
# Superseded / cancelled run handling (newest-authoritative-per-workflow)
# ---------------------------------------------------------------------------


def test_classify_runs_for_workflows_older_cancelled_then_newer_success(tmp_path, monkeypatch):
    """Newest-authoritative semantics: a workflow with an older cancelled run
    and a newer success on the same exact head must be GREEN; the older
    cancelled run must appear in superseded_cancelled_runs (not failed_runs).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    # CI: older cancelled, newer success.
    runs = [
        make_run("CI", "completed", "cancelled", created_at="2026-06-01T22:00:00Z",
                 database_id=1001),
        make_run("CI", "completed", "success", created_at="2026-06-01T22:10:00Z",
                 database_id=1002),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2001),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_GREEN
    # The newer success per workflow shows up in successful_runs.
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert successful_dbs == [1002, 2001]
    # The older cancelled run for CI shows up in superseded_cancelled_runs.
    superseded_dbs = sorted(r["databaseId"] for r in packet["superseded_cancelled_runs"])
    assert superseded_dbs == [1001]
    # The cancelled run is NOT in failed_runs.
    failed_dbs = sorted(r["databaseId"] for r in packet["failed_runs"])
    assert failed_dbs == []
    # Summary count for superseded_cancelled is 1.
    assert packet["summary"]["superseded_cancelled"] == 1
    # The markdown report contains a "Superseded cancelled runs" section.
    md = Path(md_out).read_text()
    assert "## Superseded cancelled runs" in md
    assert "1001" in md


def test_classify_runs_for_workflows_older_failure_then_newer_success(tmp_path, monkeypatch):
    """An older failure followed by a newer success on the same workflow
    and head is GREEN at the workflow level (newest success is authoritative).
    The older failure is reported as superseded history (not in failed_runs).
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        make_run("CI", "completed", "failure", created_at="2026-06-01T22:00:00Z",
                 database_id=1101),
        make_run("CI", "completed", "success", created_at="2026-06-01T22:10:00Z",
                 database_id=1102),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2101),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    # Newest success wins → GREEN overall.
    assert packet["status"] == mod.STATUS_GREEN
    # Successful_runs: newest success per workflow.
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert successful_dbs == [1102, 2101]
    # failed_runs is empty: the older failure is superseded by the newer
    # success on the same workflow/head and is reported as history only.
    assert packet["failed_runs"] == []
    # The older failure appears in superseded_cancelled_runs.
    superseded_dbs = sorted(r["databaseId"] for r in packet["superseded_cancelled_runs"])
    assert superseded_dbs == [1101]


def test_classify_runs_for_workflows_newest_cancelled_no_later_success(tmp_path, monkeypatch):
    """If the newest terminal run is cancelled and no later success exists for
    the same workflow and head, the workflow is FAILED (and overall is FAILED).
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        # CI: only a cancelled run exists (the newest is cancelled).
        make_run("CI", "completed", "cancelled", created_at="2026-06-01T22:10:00Z",
                 database_id=1201),
        # Edge Discovery: success.
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2201),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_HOLD_FAILED
    # The newest terminal run for CI is cancelled → it is in failed_runs
    # (not in superseded_cancelled).
    failed_dbs = sorted(r["databaseId"] for r in packet["failed_runs"])
    assert 1201 in failed_dbs
    # No superseded runs (no success exists for CI to supersede the cancelled).
    assert packet["superseded_cancelled_runs"] == []


def test_classify_runs_for_workflows_newest_in_progress_no_terminal_success(tmp_path, monkeypatch):
    """If the newest run is in_progress and no later success exists for the
    same workflow and head, the workflow is PENDING.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        make_run("CI", "in_progress", "", created_at="2026-06-01T22:10:00Z",
                 database_id=1301),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2301),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    # CI is in-flight, Edge Discovery is success → overall PENDING.
    assert packet["status"] == mod.STATUS_HOLD_PENDING
    pending_dbs = sorted(r["databaseId"] for r in packet["pending_runs"])
    assert 1301 in pending_dbs


def test_classify_runs_for_workflows_older_in_progress_then_newer_success(tmp_path, monkeypatch):
    """An older in_progress run that gets superseded by a newer success for
    the same workflow and head must not be in pending_runs; the success wins.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        make_run("CI", "in_progress", "", created_at="2026-06-01T22:00:00Z",
                 database_id=1401),
        make_run("CI", "completed", "success", created_at="2026-06-01T22:10:00Z",
                 database_id=1402),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2401),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_GREEN
    # pending_runs is empty (the in_progress was superseded by the success).
    assert packet["pending_runs"] == []
    # The older in_progress run is reported in superseded_cancelled_runs
    # (it is now history relative to the success).
    superseded_dbs = sorted(r["databaseId"] for r in packet["superseded_cancelled_runs"])
    assert 1401 in superseded_dbs
    # The newer success is in successful_runs.
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert 1402 in successful_dbs


def test_classify_runs_for_workflows_wrong_head_success_does_not_satisfy(tmp_path, monkeypatch):
    """Runs for a different head SHA must not satisfy the exact-head audit.
    The filter_runs_for_head step in audit() already enforces this; here we
    verify the classifier alone does the right thing if a wrong-head run
    is mistakenly passed in (it should be classified on its own head, not
    mixed with right-head runs in a way that flips verdicts).

    For this test we exercise the public `audit()` end-to-end with mixed
    head SHAs and assert the right-head runs are the only ones considered.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        # Wrong head: success should be ignored.
        make_run("CI", "completed", "success", head_sha=HEAD_OTHER,
                 created_at="2026-06-01T22:10:00Z", database_id=1501),
        # Right head: cancelled CI + success for the other workflow.
        make_run("CI", "completed", "cancelled",
                 created_at="2026-06-01T22:00:00Z", database_id=1502),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2501),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    # The wrong-head success (1501) is filtered out; only right-head runs
    # (1502 cancelled, 2501 success) are considered.
    all_dbs = sorted(r["databaseId"] for r in packet["runs_for_head"])
    assert 1501 not in all_dbs
    assert 1502 in all_dbs
    assert 2501 in all_dbs
    # The only success on the right head is Edge Discovery. CI on the right
    # head is cancelled with no later success → CI is failed, overall is
    # HOLD_FAILED.
    assert packet["status"] == mod.STATUS_HOLD_FAILED


def test_classify_runs_for_workflows_multiple_workflows_all_need_success(tmp_path, monkeypatch):
    """Multiple workflows: every required workflow must have an authoritative
    success for the overall verdict to be GREEN.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        # CI: success.
        make_run("CI", "completed", "success", created_at="2026-06-01T22:10:00Z",
                 database_id=1601),
        # Edge Discovery: success.
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2601),
        # WFA: success.
        make_run("WFA", "completed", "success", created_at="2026-06-01T22:10:00Z",
                 database_id=3601),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--required-workflow", "CI",
            "--required-workflow", "Edge Discovery audit tests",
            "--required-workflow", "WFA",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_GREEN
    assert packet["missing_required_workflows"] == []
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert successful_dbs == [1601, 2601, 3601]


def test_classify_runs_for_workflows_mixed_cancelled_and_skipped_in_history(tmp_path, monkeypatch):
    """Older cancelled AND skipped runs for the same workflow are both
    captured in superseded_cancelled_runs when a newer success exists.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        make_run("CI", "completed", "cancelled", created_at="2026-06-01T22:00:00Z",
                 database_id=1701),
        make_run("CI", "completed", "skipped", created_at="2026-06-01T22:05:00Z",
                 database_id=1702),
        make_run("CI", "completed", "success", created_at="2026-06-01T22:10:00Z",
                 database_id=1703),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2701),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    assert packet["status"] == mod.STATUS_GREEN
    superseded_dbs = sorted(r["databaseId"] for r in packet["superseded_cancelled_runs"])
    # Both older cancelled (1701) and older skipped (1702) are superseded
    # by the newer success (1703).
    assert superseded_dbs == [1701, 1702]
    # Successful_runs: only the newer success per workflow.
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert successful_dbs == [1703, 2701]


# ---------------------------------------------------------------------------
# In-flight precedence: newer in-flight run must NOT be shadowed by an
# older completed success on the same workflow/head.
# ---------------------------------------------------------------------------


def test_classify_runs_for_workflows_newer_in_progress_older_success_is_pending(tmp_path, monkeypatch):
    """The exact Codex regression: a workflow with a newer in_progress run
    and an older completed success on the same exact head must be PENDING,
    not GREEN. The older success must be reported in superseded_cancelled
    so the audit reader can see what happened, but it must NOT count as
    authoritative while the in-flight attempt is still running.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)

    runs = [
        # CI: older completed success, then a newer in_progress rerun
        # (e.g., a re-trigger created after the original successful run).
        make_run("CI", "completed", "success", created_at="2026-06-01T22:00:00Z",
                 database_id=1801),
        make_run("CI", "in_progress", "", created_at="2026-06-01T22:10:00Z",
                 database_id=1802),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=2801),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    # The newer in_progress run makes the audit PENDING — NOT GREEN.
    assert packet["status"] == mod.STATUS_HOLD_PENDING
    # The CI in-flight run is in pending_runs.
    pending_dbs = sorted(r["databaseId"] for r in packet["pending_runs"])
    assert 1802 in pending_dbs
    # The CI older success is in superseded_cancelled_runs, NOT successful_runs.
    superseded_dbs = sorted(r["databaseId"] for r in packet["superseded_cancelled_runs"])
    assert 1801 in superseded_dbs
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert 1801 not in successful_dbs
    # The Edge Discovery success is the only authoritative success.
    assert successful_dbs == [2801]
    # Polling should have been used (PENDING keeps the audit polling until
    # exhaustion); the in-flight CI run is still pending at the final poll.
    assert packet["polls_used"] == 3
    # The markdown report makes the supersession visible.
    md = Path(md_out).read_text()
    assert "## Superseded cancelled runs" in md
    assert "1801" in md


def test_classify_runs_for_workflows_newer_queued_older_success_is_pending(tmp_path, monkeypatch):
    """In-flight precedence also covers the other in-flight statuses:
    queued, requested, waiting. An older completed success must NOT shadow
    a newer queued rerun on the same workflow/head.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    for in_flight_status in ("queued", "requested", "waiting"):
        runs = [
            make_run("CI", "completed", "success",
                     created_at="2026-06-01T22:00:00Z", database_id=1901),
            make_run("CI", in_flight_status, "",
                     created_at="2026-06-01T22:10:00Z", database_id=1902),
            make_run("Edge Discovery audit tests", "completed", "success",
                     created_at="2026-06-01T22:10:00Z", database_id=2901),
        ]

        def make_mock(_runs):
            def _mock(_args):
                return _runs
            return _mock

        # Use a per-status fresh tmp dir; the function is called via main().
        from pathlib import Path as _P
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            json_out = str(_P(td) / "audit.json")
            md_out = str(_P(td) / "audit.md")
            monkeypatch.setattr(mod, "run_gh_run_list", make_mock(runs))
            rc = mod.main(
                [
                    "--repo", REPO,
                    "--head-sha", HEAD,
                    "--branch", "main",
                    "--max-polls", "1",
                    "--poll-seconds", "5",
                    "--output-json", json_out,
                    "--output-md", md_out,
                ]
            )
            assert rc == 0, f"main() returned {rc} for in_flight_status={in_flight_status}"
            packet = json.loads(Path(json_out).read_text())
            assert packet["status"] == mod.STATUS_HOLD_PENDING, (
                f"newer {in_flight_status!r} run + older success must be PENDING, "
                f"got {packet['status']!r}"
            )
            pending_dbs = sorted(r["databaseId"] for r in packet["pending_runs"])
            assert 1902 in pending_dbs, (
                f"newer {in_flight_status!r} run must be in pending_runs"
            )
            successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
            assert 1901 not in successful_dbs, (
                f"older success must NOT be in successful_runs when newer "
                f"{in_flight_status!r} run exists on the same workflow/head"
            )


def test_classify_runs_for_workflows_newest_failure_older_success_is_failed(tmp_path, monkeypatch):
    """When the newest terminal run for a workflow is a failure and an
    older success exists on the same workflow/head, the verdict is
    FAILED. The newer failure is the authoritative verdict — the older
    success is dropped from the report (it is history, but the newest
    failure is what blocks the workflow).
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        # CI: older success, then a newer failure.
        make_run("CI", "completed", "success", created_at="2026-06-01T22:00:00Z",
                 database_id=2001),
        make_run("CI", "completed", "failure", created_at="2026-06-01T22:10:00Z",
                 database_id=2002),
        # Edge Discovery: success.
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=3001),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    # The newest CI terminal is failure → overall FAILED.
    assert packet["status"] == mod.STATUS_HOLD_FAILED
    failed_dbs = sorted(r["databaseId"] for r in packet["failed_runs"])
    assert 2002 in failed_dbs
    # The older success is NOT in successful_runs (newest failure is
    # authoritative, not the older success).
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert 2001 not in successful_dbs
    # Only Edge Discovery is in successful_runs.
    assert successful_dbs == [3001]


def test_classify_runs_for_workflows_one_workflow_pending_keeps_overall_pending(tmp_path, monkeypatch):
    """Multiple workflows: if one workflow has a newer in-flight run and
    an older completed success, the in-flight precedence rule applies to
    THAT workflow and the overall audit must remain PENDING — even if
    every other workflow has an authoritative success. The green workflows
    do not pull the audit out of PENDING.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        # CI: older completed success, then a newer in_progress rerun
        # (the in-flight precedence trigger).
        make_run("CI", "completed", "success", created_at="2026-06-01T22:00:00Z",
                 database_id=2101),
        make_run("CI", "in_progress", "", created_at="2026-06-01T22:10:00Z",
                 database_id=2102),
        # Edge Discovery: success (would be GREEN on its own).
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=3101),
        # WFA: success (would be GREEN on its own).
        make_run("WFA", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=4101),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--required-workflow", "CI",
            "--required-workflow", "Edge Discovery audit tests",
            "--required-workflow", "WFA",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    # CI's in-flight precedence keeps the whole audit PENDING.
    assert packet["status"] == mod.STATUS_HOLD_PENDING
    # CI in-flight is in pending_runs.
    pending_dbs = sorted(r["databaseId"] for r in packet["pending_runs"])
    assert 2102 in pending_dbs
    # The CI older success is in superseded_cancelled (history only).
    superseded_dbs = sorted(r["databaseId"] for r in packet["superseded_cancelled_runs"])
    assert 2101 in superseded_dbs
    # Edge Discovery and WFA are still in successful_runs — they are green
    # at the workflow level, but the overall audit is held on CI's in-flight.
    successful_dbs = sorted(r["databaseId"] for r in packet["successful_runs"])
    assert 3101 in successful_dbs
    assert 4101 in successful_dbs
    # No failure introduced.
    assert packet["failed_runs"] == []


def test_classify_runs_for_workflows_newer_in_progress_older_failure_is_pending(tmp_path, monkeypatch):
    """When a workflow has a newer in-flight run and an older completed
    failure (not success), the verdict is still PENDING — the in-flight
    attempt is the authoritative rerun and may turn the workflow green.
    The older failure is reported as superseded history, not as a
    blocking failure.
    """
    monkeypatch.setattr("time.sleep", FakeSleep())

    runs = [
        # CI: older failure, then a newer in_progress rerun.
        make_run("CI", "completed", "failure", created_at="2026-06-01T22:00:00Z",
                 database_id=2201),
        make_run("CI", "in_progress", "", created_at="2026-06-01T22:10:00Z",
                 database_id=2202),
        make_run("Edge Discovery audit tests", "completed", "success",
                 created_at="2026-06-01T22:10:00Z", database_id=3201),
    ]
    monkeypatch.setattr(mod, "run_gh_run_list", lambda args: runs)

    json_out = str(tmp_path / "audit.json")
    md_out = str(tmp_path / "audit.md")
    rc = mod.main(
        [
            "--repo", REPO,
            "--head-sha", HEAD,
            "--branch", "main",
            "--max-polls", "3",
            "--poll-seconds", "5",
            "--output-json", json_out,
            "--output-md", md_out,
        ]
    )
    assert rc == 0
    packet = json.loads(Path(json_out).read_text())
    # The newer in-flight run is the authoritative attempt → PENDING.
    # The older failure is NOT a blocking failure while the rerun is
    # still running.
    assert packet["status"] == mod.STATUS_HOLD_PENDING
    pending_dbs = sorted(r["databaseId"] for r in packet["pending_runs"])
    assert 2202 in pending_dbs
    # The older failure is reported as superseded history, NOT failed_runs.
    assert packet["failed_runs"] == []
    superseded_dbs = sorted(r["databaseId"] for r in packet["superseded_cancelled_runs"])
    assert 2201 in superseded_dbs
