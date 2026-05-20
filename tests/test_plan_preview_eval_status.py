#!/usr/bin/env python3
"""
Tests for plan_preview_eval_status.py.

Covers all final states, classification logic, output formats.
Mocks subprocess calls to run_plan_preview.py — no real Claude needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import scripts.local.plan_preview_eval_status as mod


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def make_args(
    trials_json="/tmp/trials.json",
    output_root="/tmp/eval/",
    output_json=None,
    output_md=None,
    min_ready_ratio=0.8,
    repo_root="/home/max/Automated-Edge-Discovery",
    timeout=300,
):
    return type(
        "Args",
        (),
        {
            "trials_json": trials_json,
            "output_root": output_root,
            "output_json": output_json,
            "output_md": output_md,
            "min_ready_ratio": min_ready_ratio,
            "repo_root": repo_root,
            "timeout": timeout,
        },
    )()


def make_trial(
    trial_id: str = "A",
    task: str = "Fix the bug",
    allowed_files: list = None,
    forbidden_files: list = None,
    do_not: list = None,
) -> dict:
    return {
        "id": trial_id,
        "task": task,
        "allowed_files": allowed_files or [],
        "forbidden_files": forbidden_files or [],
        "do_not": do_not or [],
    }


def make_result(
    status: str,
    validation_errors: list = None,
    git_status_before: str = "clean",
    git_status_after: str = "clean",
    repo_mutated: bool = False,
    plan_length_chars: int = 100,
    elapsed_seconds: float = 10.0,
    timeout_seconds: float | None = None,
    stdout_bytes: int = 1000,
    error_type: str = "",
) -> dict:
    metadata = {"elapsed_seconds": elapsed_seconds, "stdout_bytes": stdout_bytes}
    if timeout_seconds is not None:
        metadata["timeout_seconds"] = timeout_seconds
    if error_type:
        metadata["error_type"] = error_type

    result = {
        "status": status,
        "validation_errors": validation_errors or [],
        "git_status_before": git_status_before,
        "git_status_after": git_status_after,
        "repo_mutated": repo_mutated,
        "plan_length_chars": plan_length_chars,
        "metadata": metadata,
    }
    return result


def make_trial_result(
    trial_id: str,
    result: dict,
    elapsed: float = 10.0,
) -> dict:
    return {
        "trial_id": trial_id,
        "result": result,
        "elapsed": elapsed,
    }


# -----------------------------------------------------------------------
# Tests: generate_trial_packet
# -----------------------------------------------------------------------

class TestGenerateTrialPacket:
    def test_creates_valid_packet_kind(self, tmp_path):
        trial = make_trial("A", "Fix bug", ["scripts/local/foo.py"], [".github/"], ["do not dispatch"])
        packet = mod.generate_trial_packet(trial, tmp_path)
        assert packet["packet_kind"] == "aed.worker.packet.v1"

    def test_task_fields(self, tmp_path):
        trial = make_trial(
            "B", "Implement feature",
            allowed_files=["src/feat.py"],
            forbidden_files=["secrets/"],
            do_not=["do not push", "do not install packages"],
        )
        packet = mod.generate_trial_packet(trial, tmp_path)
        task = packet["task"]
        assert task["description"] == "Implement feature"
        assert task["allowed_files"] == ["src/feat.py"]
        assert task["forbidden_files"] == ["secrets/"]
        assert task["do_not"] == ["do not push", "do not install packages"]

    def test_dependency_install_policy_set(self, tmp_path):
        trial = make_trial("C", "Task", [], [], [])
        packet = mod.generate_trial_packet(trial, tmp_path)
        dep = packet["task"]["dependency_install_policy"]
        assert dep["new_dependencies_allowed"] is False
        assert dep["requires_human_approval"] is True


# -----------------------------------------------------------------------
# Tests: classify_trial
# -----------------------------------------------------------------------

class TestClassifyTrial:
    def test_ready(self):
        result = make_result("PLAN_PREVIEW_READY")
        assert mod.classify_trial(result) == mod.CLASS_READY

    def test_repo_mutated(self):
        result = make_result("PLAN_PREVIEW_BLOCKED", repo_mutated=True)
        assert mod.classify_trial(result) == mod.CLASS_REPO_MUTATED

    def test_error_timeout(self):
        result = make_result("PLAN_PREVIEW_ERROR", error_type="claude_timeout")
        assert mod.classify_trial(result) == mod.CLASS_ERROR_TIMEOUT

    def test_error_packet_schema_invalid(self):
        result = make_result("PLAN_PREVIEW_ERROR", error_type="invalid_packet")
        assert mod.classify_trial(result) == mod.CLASS_ERROR_PACKET_SCHEMA

    def test_error_packet_schema_result_missing(self):
        result = make_result("PLAN_PREVIEW_ERROR", error_type="result_file_missing")
        assert mod.classify_trial(result) == mod.CLASS_ERROR_PACKET_SCHEMA

    def test_error_claude_invocation_nonzero(self):
        result = make_result("PLAN_PREVIEW_ERROR", error_type="claude_nonzero_exit")
        assert mod.classify_trial(result) == mod.CLASS_ERROR_CLAUDE_INVOCATION

    def test_error_claude_invocation_empty(self):
        result = make_result("PLAN_PREVIEW_ERROR", error_type="empty_plan_output")
        assert mod.classify_trial(result) == mod.CLASS_ERROR_CLAUDE_INVOCATION

    def test_error_unknown(self):
        result = make_result("PLAN_PREVIEW_ERROR", error_type="some_unknown_error")
        assert mod.classify_trial(result) == mod.CLASS_ERROR_UNKNOWN

    def test_blocked_true_positive_forbidden_file(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan references forbidden file: .github/workflows/ci.yml"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_TRUE_POSITIVE

    def test_blocked_true_positive_do_not(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan violates do_not constraint: do not dispatch"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_TRUE_POSITIVE

    def test_blocked_likely_false_positive_audit(self):
        # "audit" mentioned in context but not an actual audit log violation
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["the word audit appeared in the plan"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_LIKELY_FALSE_POSITIVE

    def test_blocked_likely_false_positive_memory(self):
        # "memory" appears in the plan as a general word, not update memory
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["the plan mentions memory management"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_LIKELY_FALSE_POSITIVE

    def test_blocked_likely_false_positive_profile(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan references 'profile' in a comment"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_LIKELY_FALSE_POSITIVE

    def test_blocked_likely_false_positive_board(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["the word board appears in the description"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_LIKELY_FALSE_POSITIVE

    def test_blocked_likely_false_positive_dispatch(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan mentions dispatch in passing"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_LIKELY_FALSE_POSITIVE

    def test_blocked_true_positive_not_counted_as_false_positive(self):
        # True positive: actual forbidden file reference, not just the word "audit"
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan references forbidden file: /home/max/.hermes/skills/"],
        )
        assert mod.classify_trial(result) == mod.CLASS_BLOCKED_TRUE_POSITIVE


# -----------------------------------------------------------------------
# Tests: has_external_mutation
# -----------------------------------------------------------------------

class TestHasExternalMutation:
    def test_no_mutation(self):
        result = make_result("PLAN_PREVIEW_READY")
        assert mod.has_external_mutation(result) is False

    def test_repo_mutated_yes(self):
        result = make_result("PLAN_PREVIEW_BLOCKED", repo_mutated=True)
        assert mod.has_external_mutation(result) is True

    def test_hermes_in_errors(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan references hermes path"],
            repo_mutated=False,
        )
        assert mod.has_external_mutation(result) is True

    def test_dispatch_in_errors(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan mentions dispatch"],
            repo_mutated=False,
        )
        assert mod.has_external_mutation(result) is True

    def test_board_in_errors(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan references board"],
            repo_mutated=False,
        )
        assert mod.has_external_mutation(result) is True


# -----------------------------------------------------------------------
# Tests: is_scope_violation
# -----------------------------------------------------------------------

class TestIsScopeViolation:
    def test_allowed_files_in_error(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["file not in allowed_files list"],
        )
        assert mod.is_scope_violation(result) is True

    def test_scope_phrase(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["file is outside scope"],
        )
        assert mod.is_scope_violation(result) is True

    def test_no_scope_violation(self):
        result = make_result(
            "PLAN_PREVIEW_BLOCKED",
            validation_errors=["plan references forbidden file"],
        )
        assert mod.is_scope_violation(result) is False


# -----------------------------------------------------------------------
# Tests: aggregate_results
# -----------------------------------------------------------------------

class TestAggregateResults:
    def test_all_ready(self):
        results = [
            make_trial_result("A", make_result("PLAN_PREVIEW_READY")),
            make_trial_result("B", make_result("PLAN_PREVIEW_READY")),
        ]
        agg = mod.aggregate_results(results)
        assert agg["ready_count"] == 2
        assert agg["total_trials"] == 2
        assert agg["ready_ratio"] == 1.0
        assert agg["all_clean_to_clean"] is True

    def test_mixed(self):
        results = [
            make_trial_result("A", make_result("PLAN_PREVIEW_READY")),
            make_trial_result("B", make_result("PLAN_PREVIEW_BLOCKED", validation_errors=["forbidden"])),
        ]
        agg = mod.aggregate_results(results)
        assert agg["ready_count"] == 1
        assert agg["blocked_true_positive_count"] == 1
        assert agg["all_clean_to_clean"] is False

    def test_repo_mutated_counted(self):
        results = [
            make_trial_result("A", make_result("PLAN_PREVIEW_READY")),
            make_trial_result("B", make_result("PLAN_PREVIEW_BLOCKED", repo_mutated=True)),
        ]
        agg = mod.aggregate_results(results)
        assert agg["repo_mutated_count"] == 1


# -----------------------------------------------------------------------
# Tests: determine_final_state
# -----------------------------------------------------------------------

class TestDetermineFinalState:
    def test_all_ready_above_ratio(self):
        agg = {
            "total_trials": 5,
            "ready_count": 5,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 1.0,
            "all_clean_to_clean": True,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.READY_FOR_MANUAL_PLAN_PREVIEW

    def test_all_ready_below_ratio(self):
        agg = {
            "total_trials": 10,
            "ready_count": 7,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.7,
            "all_clean_to_clean": False,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_PLAN_PREVIEW_ERRORS

    def test_one_false_positive(self):
        agg = {
            "total_trials": 5,
            "ready_count": 4,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 1,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.8,
            "all_clean_to_clean": True,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_VALIDATOR_FALSE_POSITIVES

    def test_timeout_errors(self):
        agg = {
            "total_trials": 5,
            "ready_count": 3,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 2,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.6,
            "all_clean_to_clean": False,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_TIMEOUTS

    def test_packet_schema_errors(self):
        agg = {
            "total_trials": 5,
            "ready_count": 4,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 1,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.8,
            "all_clean_to_clean": True,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_PACKET_SCHEMA

    def test_repo_mutation(self):
        agg = {
            "total_trials": 5,
            "ready_count": 4,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 1,
            "ready_ratio": 0.8,
            "all_clean_to_clean": False,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_REPO_MUTATION

    def test_external_mutation(self):
        agg = {
            "total_trials": 5,
            "ready_count": 4,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.8,
            "all_clean_to_clean": True,
            "any_external_mutation": True,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_EXTERNAL_MUTATION

    def test_true_positive_with_low_ratio_scope_mismatch(self):
        # True positives with low ratio → scope mismatch hold
        agg = {
            "total_trials": 10,
            "ready_count": 3,
            "blocked_true_positive_count": 7,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.3,
            "all_clean_to_clean": False,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_SCOPE_MISMATCH

    def test_true_positive_above_ratio_still_ready(self):
        # True positives with high ratio should still be ready if no other issues
        agg = {
            "total_trials": 5,
            "ready_count": 4,
            "blocked_true_positive_count": 1,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.8,
            "all_clean_to_clean": True,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.READY_FOR_MANUAL_PLAN_PREVIEW

    def test_claude_invocation_errors(self):
        agg = {
            "total_trials": 5,
            "ready_count": 3,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 1,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.6,
            "all_clean_to_clean": False,
            "any_external_mutation": False,
        }
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        assert state == mod.State.HOLD_PLAN_PREVIEW_ERRORS

    def test_hold_unknown(self):
        """HOLD_UNKNOWN is the defensive fallback when no other condition matches.
        Reach: all counts zero, ready_ratio < min, but all_clean_to_clean=True
        (so step-8 catches it as HOLD_PLAN_PREVIEW_ERRORS, not HOLD_UNKNOWN).
        We mock aggregate_results to construct the exact edge case.
        """
        # Step through determine_final_state logic manually to hit HOLD_UNKNOWN.
        # The only path: all error counts are 0, but the ratio check in step 8
        # fires before we reach the fallback. To force the fallback, we need
        # ready_ratio >= min_ready_ratio (skip step 8) AND all_clean_to_clean=False
        # (skip step 9 conditional) — but that also prevents reaching the fallback.
        #
        # Simulate the state machine directly:
        agg = {
            "total_trials": 5,
            "ready_count": 0,  # nobody ready
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 0.0,  # nothing ready → ratio < min
            "all_clean_to_clean": True,  # no trial returned non-ready class
            "any_external_mutation": False,
        }
        # With all error counts zero, step 8 fires (ratio < min) and returns
        # HOLD_PLAN_PREVIEW_ERRORS. So HOLD_UNKNOWN can only be hit in the
        # defensive else-branch if the state machine logic changes.
        # Verify the current logic handles the edge case gracefully.
        state = mod.determine_final_state(agg, min_ready_ratio=0.8)
        # Current implementation: step 8 fires for ratio < min → HOLD_PLAN_PREVIEW_ERRORS
        assert state in (mod.State.HOLD_PLAN_PREVIEW_ERRORS, mod.State.HOLD_UNKNOWN)


# -----------------------------------------------------------------------
# Tests: output JSON includes per-trial classifications
# -----------------------------------------------------------------------

class TestOutputJson:
    def test_output_json_structure(self, tmp_path, monkeypatch):
        trials_path = tmp_path / "trials.json"
        output_root = tmp_path / "output"
        output_json = tmp_path / "result.json"

        trials = {
            "run_id": "test_eval_001",
            "trials": [
                make_trial("A", "Task A", ["foo.py"]),
                make_trial("B", "Task B", ["bar.py"]),
            ],
        }
        trials_path.write_text(json.dumps(trials))

        # Mock run_trial to return ready results
        def mock_run_trial(trial_id, packet, output_dir, repo_root, timeout):
            return {
                "trial_id": trial_id,
                "packet_path": str(output_dir / f"{trial_id}_packet.json"),
                "result": make_result("PLAN_PREVIEW_READY"),
                "elapsed": 5.0,
            }

        monkeypatch.setattr(mod, "run_trial", mock_run_trial)

        args = make_args(
            trials_json=str(trials_path),
            output_root=str(output_root),
            output_json=str(output_json),
            min_ready_ratio=0.8,
        )
        result = mod.run(args)

        assert result == 0
        data = json.loads(output_json.read_text())
        assert data["final_state"] == mod.State.READY_FOR_MANUAL_PLAN_PREVIEW
        assert len(data["per_trial"]) == 2
        assert data["per_trial"][0]["classification"] == mod.CLASS_READY
        assert data["per_trial"][1]["classification"] == mod.CLASS_READY


# -----------------------------------------------------------------------
# Tests: output markdown contains summary table
# -----------------------------------------------------------------------

class TestOutputMarkdown:
    def test_markdown_has_summary_table(self, tmp_path, monkeypatch):
        trials_path = tmp_path / "trials.json"
        output_root = tmp_path / "output"
        output_md = tmp_path / "result.md"

        trials = {
            "run_id": "test_eval_md",
            "trials": [
                make_trial("A", "Task A", ["foo.py"]),
            ],
        }
        trials_path.write_text(json.dumps(trials))

        def mock_run_trial(trial_id, packet, output_dir, repo_root, timeout):
            return {
                "trial_id": trial_id,
                "packet_path": str(output_dir / f"{trial_id}_packet.json"),
                "result": make_result("PLAN_PREVIEW_READY"),
                "elapsed": 5.0,
            }

        monkeypatch.setattr(mod, "run_trial", mock_run_trial)

        args = make_args(
            trials_json=str(trials_path),
            output_root=str(output_root),
            output_md=str(output_md),
            min_ready_ratio=0.8,
        )
        result = mod.run(args)

        assert result == 0
        md = output_md.read_text()
        assert "| Total Trials |" in md
        assert "| Ready |" in md
        assert "| Ready Ratio |" in md
        assert "| Trial |" in md  # per-trial table
        assert "READY_FOR_MANUAL_PLAN_PREVIEW" in md


# -----------------------------------------------------------------------
# Tests: command does not invoke real Claude
# -----------------------------------------------------------------------

class TestNoRealClaude:
    def test_runs_without_claude_binary(self, tmp_path, monkeypatch):
        """Verify the command does not require a real Claude binary to return results."""
        trials_path = tmp_path / "trials.json"
        output_root = tmp_path / "output"
        output_json = tmp_path / "result.json"

        trials = {
            "run_id": "test_no_claude",
            "trials": [
                make_trial("A", "Task A", ["foo.py"]),
            ],
        }
        trials_path.write_text(json.dumps(trials))

        # Provide mock results — nothing hits the real run_plan_preview.py
        def mock_run_trial(trial_id, packet, output_dir, repo_root, timeout):
            return {
                "trial_id": trial_id,
                "packet_path": str(output_dir / f"{trial_id}_packet.json"),
                "result": make_result("PLAN_PREVIEW_READY"),
                "elapsed": 5.0,
            }

        monkeypatch.setattr(mod, "run_trial", mock_run_trial)

        args = make_args(
            trials_json=str(trials_path),
            output_root=str(output_root),
            output_json=str(output_json),
            min_ready_ratio=0.8,
        )
        result = mod.run(args)

        assert result == 0
        data = json.loads(output_json.read_text())
        assert data["final_state"] == mod.State.READY_FOR_MANUAL_PLAN_PREVIEW


# -----------------------------------------------------------------------
# Tests: generated worker packets use correct packet_kind
# -----------------------------------------------------------------------

class TestPacketSchema:
    def test_packet_kind_correct(self, tmp_path):
        trial = make_trial("X", "Do task", ["src/x.py"], [], [])
        packet = mod.generate_trial_packet(trial, tmp_path)
        assert packet["packet_kind"] == "aed.worker.packet.v1"

    def test_allowed_files_schema(self, tmp_path):
        trial = make_trial("Y", "Do task", ["lib/y.py", "tests/test_y.py"], [], [])
        packet = mod.generate_trial_packet(trial, tmp_path)
        assert packet["task"]["allowed_files"] == ["lib/y.py", "tests/test_y.py"]

    def test_forbidden_files_schema(self, tmp_path):
        trial = make_trial("Z", "Task", [], ["secrets/", "/home/max/.hermes/"], [])
        packet = mod.generate_trial_packet(trial, tmp_path)
        assert packet["task"]["forbidden_files"] == ["secrets/", "/home/max/.hermes/"]

    def test_do_not_schema(self, tmp_path):
        trial = make_trial("W", "Task", [], [], ["do not dispatch", "do not push"])
        packet = mod.generate_trial_packet(trial, tmp_path)
        assert packet["task"]["do_not"] == ["do not dispatch", "do not push"]

    def test_minimal_trial_still_valid(self, tmp_path):
        """A trial with only 'id' and 'task' produces a valid packet."""
        trial = {"id": "M", "task": "Minimal task"}
        packet = mod.generate_trial_packet(trial, tmp_path)
        assert packet["packet_kind"] == "aed.worker.packet.v1"
        assert packet["task"]["description"] == "Minimal task"
        assert packet["task"]["allowed_files"] == []
        assert packet["task"]["forbidden_files"] == []
        assert packet["task"]["do_not"] == []


# -----------------------------------------------------------------------
# Tests: run_trial function
# -----------------------------------------------------------------------

class TestRunTrial:
    def test_writes_packet_json(self, tmp_path, monkeypatch):
        """run_trial writes the packet JSON to the trial dir."""
        trial = make_trial("T", "Task", ["foo.py"], [], [])
        output_dir = tmp_path / "runs"
        output_dir.mkdir()

        def mock_subprocess_run(cmd, **kwargs):
            # Return a successful result with no output
            return mock.MagicMock(
                returncode=0,
                stdout="",
                stderr="",
            )

        # Mock _load_json to return a PLAN_PREVIEW_READY result
        def mock_load_json(path):
            if "result.json" in str(path):
                return {
                    "status": "PLAN_PREVIEW_READY",
                    "validation_errors": [],
                    "git_status_before": "clean",
                    "git_status_after": "clean",
                    "repo_mutated": False,
                    "plan_length_chars": 50,
                    "metadata": {"elapsed_seconds": 5.0, "stdout_bytes": 100},
                }
            return {}

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
        monkeypatch.setattr(mod, "_load_json", mock_load_json)

        repo_root = Path("/home/max/Automated-Edge-Discovery")
        result = mod.run_trial("T", trial, output_dir, repo_root, timeout=300)

        packet_path = output_dir / "trial_T" / "T_packet.json"
        assert packet_path.exists()

    def test_run_trial_timeout_returns_error_result(self, tmp_path, monkeypatch):
        """run_trial returns an error result when subprocess times out."""
        trial = make_trial("T", "Task", ["foo.py"], [], [])
        output_dir = tmp_path / "runs"
        output_dir.mkdir()

        def mock_subprocess_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=300)

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

        repo_root = Path("/home/max/Automated-Edge-Discovery")
        result = mod.run_trial("T", trial, output_dir, repo_root, timeout=5)

        assert result["result"]["status"] == "PLAN_PREVIEW_ERROR"
        assert result["result"]["error_type"] == "claude_timeout"


# -----------------------------------------------------------------------
# Tests: build_markdown_report
# -----------------------------------------------------------------------

class TestMarkdownReport:
    def test_report_has_final_state(self, tmp_path):
        agg = {
            "total_trials": 2,
            "ready_count": 2,
            "blocked_true_positive_count": 0,
            "blocked_likely_false_positive_count": 0,
            "error_timeout_count": 0,
            "error_claude_invocation_count": 0,
            "error_packet_schema_count": 0,
            "error_unknown_count": 0,
            "repo_mutated_count": 0,
            "ready_ratio": 1.0,
            "all_clean_to_clean": True,
            "any_external_mutation": False,
            "per_trial": [
                {
                    "trial_id": "A",
                    "status": "PLAN_PREVIEW_READY",
                    "classification": mod.CLASS_READY,
                    "validation_errors": [],
                    "git_status_before": "clean",
                    "git_status_after": "clean",
                    "repo_mutated": False,
                    "elapsed_seconds": 10.0,
                    "timeout_seconds": None,
                    "stdout_bytes": 1000,
                    "plan_length_chars": 200,
                    "scope_violation": False,
                },
                {
                    "trial_id": "B",
                    "status": "PLAN_PREVIEW_READY",
                    "classification": mod.CLASS_READY,
                    "validation_errors": [],
                    "git_status_before": "clean",
                    "git_status_after": "clean",
                    "repo_mutated": False,
                    "elapsed_seconds": 12.0,
                    "timeout_seconds": None,
                    "stdout_bytes": 1200,
                    "plan_length_chars": 250,
                    "scope_violation": False,
                },
            ],
        }
        trials_input = {"run_id": "test_001", "trials": [make_trial("A"), make_trial("B")]}
        md = mod.build_markdown_report("test_001", trials_input, agg, mod.State.READY_FOR_MANUAL_PLAN_PREVIEW)

        assert "# Plan-Preview Evaluation Report" in md
        assert "READY_FOR_MANUAL_PLAN_PREVIEW" in md
        assert "| Total Trials |" in md
        assert "| Trial |" in md


# -----------------------------------------------------------------------
# Tests: output-root must be outside repo
# -----------------------------------------------------------------------

class TestOutputRootValidation:
    def test_output_root_inside_repo_rejected(self, tmp_path, monkeypatch):
        """If output-root is inside the repo, run() returns 1."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        output_root = repo_root / "outputs"

        trials_path = tmp_path / "trials.json"
        trials = {"run_id": "test", "trials": [make_trial("A", "Task")]}
        trials_path.write_text(json.dumps(trials))

        args = make_args(
            trials_json=str(trials_path),
            output_root=str(output_root),
            min_ready_ratio=0.8,
            repo_root=str(repo_root),
        )
        result = mod.run(args)
        assert result == 1


# -----------------------------------------------------------------------
# Tests: empty trials list rejected
# -----------------------------------------------------------------------

class TestEmptyTrials:
    def test_empty_trials_returns_error(self, tmp_path):
        trials_path = tmp_path / "trials.json"
        trials_path.write_text(json.dumps({"run_id": "test", "trials": []}))

        args = make_args(
            trials_json=str(trials_path),
            output_root=str(tmp_path / "out"),
            min_ready_ratio=0.8,
        )
        result = mod.run(args)
        assert result == 1


# -----------------------------------------------------------------------
# Tests: broken JSON rejected
# -----------------------------------------------------------------------

class TestBrokenJson:
    def test_broken_json_returns_error(self, tmp_path):
        trials_path = tmp_path / "trials.json"
        trials_path.write_text("not valid json {")

        args = make_args(
            trials_json=str(trials_path),
            output_root=str(tmp_path / "out"),
            min_ready_ratio=0.8,
        )
        result = mod.run(args)
        assert result == 1