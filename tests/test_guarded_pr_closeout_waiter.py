from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "local"
sys.path.insert(0, str(SCRIPT_DIR))

import guarded_pr_closeout_waiter as waiter


HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def make_thread(
    thread_id: str = "PRRT_1",
    *,
    resolved: bool = False,
    outdated: bool = False,
    body: str = "Review issue title\n\nreview body",
) -> dict:
    return {
        "id": thread_id,
        "is_resolved": resolved,
        "is_outdated": outdated,
        "path": "scripts/local/example.py",
        "line": 12,
        "comments": [{"body": body, "author": "chatgpt-codex-connector"}],
    }


def make_cfg(tmp_path: Path, **overrides) -> waiter.CloseoutConfig:
    values = {
        "repo": "Slideshow11/Automated-Edge-Discovery",
        "pr_number": 385,
        "expected_head": HEAD,
        "base_ref": "main",
        "max_wait_minutes": 1,
        "poll_seconds": 1,
        "output_json": tmp_path / "waiter.json",
        "output_md": tmp_path / "waiter.md",
        "repo_root": Path.cwd(),
    }
    values.update(overrides)
    return waiter.CloseoutConfig(**values)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[int] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds: int):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeClient:
    def __init__(
        self,
        *,
        pr: dict | None = None,
        ci_sequence: list[dict] | None = None,
        thread_sequence: list[list[dict]] | None = None,
    ):
        self.pr = pr or {
            "state": "open",
            "merged": False,
            "mergeable": True,
            "head_sha": HEAD,
            "title": "test",
            "url": "https://example.test/pr/385",
        }
        self.ci_sequence = ci_sequence or [{"state": "success", "summary": "green"}]
        self.thread_sequence = thread_sequence or [[]]
        self.codex_comments: list[str] = []
        self.resolved_threads: list[str] = []
        self.merge_commands: list[str] = []

    def fetch_pr(self, repo, pr_number):
        return dict(self.pr)

    def fetch_ci(self, repo, head_sha):
        if len(self.ci_sequence) > 1:
            return self.ci_sequence.pop(0)
        return self.ci_sequence[0]

    def fetch_review_threads(self, repo, pr_number):
        if len(self.thread_sequence) > 1:
            return self.thread_sequence.pop(0)
        return list(self.thread_sequence[0])

    def post_codex_review(self, repo, pr_number):
        self.codex_comments.append("@codex review")

    def resolve_thread(self, thread_id):
        self.resolved_threads.append(thread_id)

    def merge_with_command(self, merge_command):
        self.merge_commands.append(merge_command)
        self.pr = {**self.pr, "state": "closed", "merged": True}
        return {"merge_command": merge_command}


class FakeHelpers:
    def __init__(
        self,
        *,
        stale_statuses: list[str] | None = None,
        final_gate: dict | None = None,
        merge_verifier: dict | None = None,
    ):
        self.stale_statuses = stale_statuses or []
        self.final_gate = final_gate or {"status": "READY_TO_MERGE"}
        self.merge_verifier = merge_verifier or {
            "recommendation": "MERGE_READY_CANDIDATE",
            "canonical_head_sha": HEAD,
            "merge_command": (
                f"gh pr merge 385 --repo Slideshow11/Automated-Edge-Discovery "
                f"--squash --delete-branch --match-head-commit {HEAD}"
            ),
        }
        self.stale_checks: list[str] = []
        self.final_gate_calls = 0
        self.merge_verifier_calls = 0

    def run_stale_checker(self, cfg, thread, output_dir):
        self.stale_checks.append(thread["id"])
        status = self.stale_statuses.pop(0) if self.stale_statuses else waiter.ELIGIBLE_STALE_THREAD_RESOLUTION
        return {"status": status, "thread_id": thread["id"]}

    def run_final_gate(self, cfg, output_dir):
        self.final_gate_calls += 1
        return dict(self.final_gate)

    def run_merge_verifier(self, cfg, output_dir):
        self.merge_verifier_calls += 1
        return dict(self.merge_verifier)


def run_waiter(cfg, client, helpers):
    clock = FakeClock()
    w = waiter.CloseoutWaiter(
        cfg,
        client,
        helpers,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )
    report = w.run()
    return report, clock


def test_ci_pending_until_timeout_returns_hold_ci_pending(tmp_path):
    cfg = make_cfg(tmp_path, max_wait_minutes=0)
    client = FakeClient(ci_sequence=[{"state": "pending", "summary": "pending"}])
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.HOLD_CI_PENDING


def test_ci_failure_returns_hold_ci_failed_with_job_summary(tmp_path):
    cfg = make_cfg(tmp_path)
    ci = {
        "state": "failed",
        "summary": "failed",
        "failing_jobs": [{"name": "test", "failed_steps": [{"name": "pytest"}]}],
    }
    report, _ = run_waiter(cfg, FakeClient(ci_sequence=[ci]), FakeHelpers())
    assert report["status"] == waiter.HOLD_CI_FAILED
    assert report["ci"]["failing_jobs"][0]["name"] == "test"


def test_head_mismatch_returns_hold_head_changed(tmp_path):
    cfg = make_cfg(tmp_path)
    client = FakeClient(pr={"state": "open", "mergeable": True, "head_sha": OTHER_HEAD})
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.HOLD_HEAD_CHANGED
    assert report["final_head"] == OTHER_HEAD


def test_current_head_thread_blocks_without_rereview(tmp_path):
    cfg = make_cfg(tmp_path)
    current = make_thread()
    client = FakeClient(thread_sequence=[[current]])
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.HOLD_CURRENT_HEAD_THREADS
    assert report["thread_summary"]["current_head_unresolved_count"] == 1


def test_trigger_codex_review_posts_one_comment_only(tmp_path):
    cfg = make_cfg(tmp_path, trigger_codex_review=True, max_wait_minutes=0)
    current = make_thread()
    client = FakeClient(thread_sequence=[[current]])
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.HOLD_CODEX_REVIEW_PENDING
    assert client.codex_comments == ["@codex review"]


def test_trigger_codex_review_continues_when_thread_clears(tmp_path):
    cfg = make_cfg(tmp_path, trigger_codex_review=True)
    current = make_thread()
    client = FakeClient(thread_sequence=[[current], []])
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.CLOSEOUT_READY_TO_MERGE
    assert client.codex_comments == ["@codex review"]


def test_outdated_thread_eligible_resolves_and_continues(tmp_path):
    cfg = make_cfg(tmp_path, allow_stale_thread_resolution=True)
    stale = make_thread("PRRT_stale", outdated=True)
    client = FakeClient(thread_sequence=[[stale], []])
    helpers = FakeHelpers(stale_statuses=[waiter.ELIGIBLE_STALE_THREAD_RESOLUTION])
    report, _ = run_waiter(cfg, client, helpers)
    assert report["status"] == waiter.CLOSEOUT_READY_TO_MERGE
    assert helpers.stale_checks == ["PRRT_stale"]
    assert client.resolved_threads == ["PRRT_stale"]


def test_outdated_thread_not_eligible_blocks(tmp_path):
    cfg = make_cfg(tmp_path, allow_stale_thread_resolution=True)
    stale = make_thread("PRRT_stale", outdated=True)
    client = FakeClient(thread_sequence=[[stale]])
    helpers = FakeHelpers(stale_statuses=["HOLD_THREAD_NOT_OUTDATED"])
    report, _ = run_waiter(cfg, client, helpers)
    assert report["status"] == waiter.HOLD_STALE_THREAD_NOT_ELIGIBLE
    assert client.resolved_threads == []


def test_current_head_thread_never_resolved_manually(tmp_path):
    cfg = make_cfg(tmp_path, trigger_codex_review=True, max_wait_minutes=0)
    current = make_thread("PRRT_current")
    client = FakeClient(thread_sequence=[[current]])
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.HOLD_CODEX_REVIEW_PENDING
    assert client.resolved_threads == []


def test_all_gates_green_dry_run_ready_no_merge(tmp_path):
    cfg = make_cfg(tmp_path)
    client = FakeClient()
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.CLOSEOUT_READY_TO_MERGE
    assert client.merge_commands == []
    assert report["merge_command"].endswith(HEAD)


def test_all_gates_green_merge_if_ready_uses_exact_match_head(tmp_path):
    cfg = make_cfg(tmp_path, merge_if_ready=True)
    client = FakeClient()
    report, _ = run_waiter(cfg, client, FakeHelpers())
    assert report["status"] == waiter.CLOSEOUT_MERGED
    assert len(client.merge_commands) == 1
    assert "--match-head-commit" in client.merge_commands[0]
    assert HEAD in client.merge_commands[0]
    assert "--admin" not in client.merge_commands[0]
    assert "--auto" not in client.merge_commands[0]


def test_final_gate_failure_blocks(tmp_path):
    cfg = make_cfg(tmp_path)
    helpers = FakeHelpers(final_gate={"status": "HOLD_CI_RED"})
    report, _ = run_waiter(cfg, FakeClient(), helpers)
    assert report["status"] == waiter.HOLD_FINAL_GATE_FAILED


def test_merge_command_verifier_failure_blocks(tmp_path):
    cfg = make_cfg(tmp_path)
    helpers = FakeHelpers(merge_verifier={"recommendation": "BLOCK", "merge_command": ""})
    report, _ = run_waiter(cfg, FakeClient(), helpers)
    assert report["status"] == waiter.HOLD_MERGE_COMMAND_NOT_VERIFIED


def test_merge_command_with_auto_blocked_at_dry_run_gate(tmp_path):
    cfg = make_cfg(tmp_path)
    helpers = FakeHelpers(merge_verifier={
        "recommendation": "MERGE_READY_CANDIDATE",
        "canonical_head_sha": HEAD,
        "merge_command": (
            f"gh pr merge 385 --repo Slideshow11/Automated-Edge-Discovery "
            f"--squash --delete-branch --auto --match-head-commit {HEAD}"
        ),
    })
    report, _ = run_waiter(cfg, FakeClient(), helpers)
    assert report["status"] == waiter.HOLD_MERGE_COMMAND_NOT_VERIFIED
    assert "auto" in report["merge_verifier"]["merge_command"]


def test_json_and_markdown_outputs_include_required_summary(tmp_path):
    cfg = make_cfg(tmp_path)
    report, _ = run_waiter(cfg, FakeClient(), FakeHelpers())
    data = json.loads(cfg.output_json.read_text(encoding="utf-8"))
    md = cfg.output_md.read_text(encoding="utf-8")
    assert data["pr_number"] == 385
    assert data["expected_head"] == HEAD
    assert data["final_head"] == HEAD
    assert "ci" in data
    assert "thread_summary" in data
    assert "actions_taken" in data
    assert "next_action" in data
    assert "Guarded PR Closeout Waiter" in md
    assert "Expected head" in md
    assert report["status"] == waiter.CLOSEOUT_READY_TO_MERGE
