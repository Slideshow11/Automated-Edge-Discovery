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


def make_run(workflow: str, status: str, conclusion: str, head_sha: str = HEAD) -> Dict[str, Any]:
    return {
        "databaseId": 1000000 + hash(workflow) % 1000000,
        "name": workflow,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "headSha": head_sha,
        "headBranch": "main",
        "event": "push",
        "createdAt": "2026-06-01T22:00:00Z",
        "updatedAt": "2026-06-01T22:05:00Z",
        "url": f"https://github.com/{REPO}/actions/runs/{hash(workflow) % 1000000}",
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
