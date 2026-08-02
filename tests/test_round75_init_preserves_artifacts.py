#!/usr/bin/env python3
"""
Round-75 regression tests for Repair D.

Repair D: Preserve artifacts when rejecting a repeated init.

A rejected repeated init must NOT overwrite, truncate,
remove or replace the valid existing run's artifacts.
The previous Round-14/42 rollback used the predicted
artifact paths from `bootstrap_artifacts` (initialized
at the top of the function) and unlinked whatever
existed at those paths, including pre-existing
artifacts from a prior invocation. The Round-75 fix
introduces a `_rollback_published` flag that tracks
which artifacts THIS invocation actually published;
the rollback only unlinks those.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_repeated_init_preserves_existing_artifacts(
    monkeypatch, tmp_path
):
    """D.1: a rejected repeated init with the same
    run_id must NOT delete the existing state, JSON
    receipt, or MD receipt. After the rejection, all
    three artifacts must be byte-identical to the
    pre-existing files.
    """
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("AED_LOCK_DIR", str(lock_dir))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    state_path = workspace / "CONTROLLER_STATE.json"
    state_path.write_text(json.dumps({
        "controller_version": 1,
        "run_id": "run-D",
        "workspace": str(workspace),
        "overall_status": "RUN_ACTIVE",
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "noop"},
        "run_identity": {
            "run_id": "run-D",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
            "lock_dir": str(lock_dir),
        },
    }))
    # Pre-existing launch receipts (the prior init's
    # artifacts). The prior init may have written them
    # itself; the test simulates the case where the
    # active run already has these artifacts on disk.
    json_path = workspace / "LAUNCH_RECEIPT.json"
    md_path = workspace / "LAUNCH_RECEIPT.md"
    json_content = json.dumps({
        "run_identity": {
            "run_id": "run-D",
            "controller_version": "test",
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
        },
    })
    json_path.write_text(json_content)
    md_content = f"# Launch receipt for run-D\n\n**Run ID:** `run-D`\n"
    md_path.write_text(md_content)

    # Capture the byte-level state of all three artifacts.
    state_before = state_path.read_bytes()
    json_before = json_path.read_bytes()
    md_before = md_path.read_bytes()

    # Now invoke init again with the SAME run_id. The
    # controller must reject (rc=16) because the state
    # already has overall_status=RUN_ACTIVE.
    from scripts.local.autocoder_run_controller import main as controller_main

    tasks = workspace / "TASKS.jsonl"
    tasks.write_text(
        json.dumps({"task_id": "t1", "task_type": "noop",
                    "integration_order": 1, "depends_on": [],
                    "blocks": []}) + "\n"
    )

    rc = controller_main([
        "init",
        "--run-id", "run-D",
        "--tasks-jsonl", str(tasks),
        "--workspace", str(workspace),
        "--integration-branch", "feat/x",
        "--repository", "Slideshow11/Automated-Edge-Discovery",
        "--target-pr-number", "416",
        "--current-main-sha", "e4ef77400000000000000000000000000000abcd",
        "--starting-target-sha", "c973fa6c0718293a4b5c6d70e0f781d67a0c0a1b",
    ])

    # The init must reject with rc=16.
    assert rc == 16, (
        f"repeated init of an ACTIVE run must be rejected "
        f"with rc=16; got rc={rc}"
    )

    # All three artifacts must be byte-identical to the
    # pre-existing files. The Round-75 fix preserves them.
    assert state_path.read_bytes() == state_before, (
        "CONTROLLER_STATE.json must NOT be modified by "
        "a rejected repeated init"
    )
    assert json_path.read_bytes() == json_before, (
        "LAUNCH_RECEIPT.json must NOT be deleted by a "
        "rejected repeated init"
    )
    assert md_path.read_bytes() == md_before, (
        "LAUNCH_RECEIPT.md must NOT be deleted by a "
        "rejected repeated init"
    )


def test_repeated_init_preserves_artifacts_with_replace_stale(
    monkeypatch, tmp_path
):
    """D.2: a repeated init of a TERMINAL run
    (RUN_COMPLETE) without --replace-stale-state must
    reject and preserve the existing artifacts. With
    --replace-stale-state the rejection is bypassed
    (the existing artifacts are intentionally
    overwritten — this is the documented override).
    """
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("AED_LOCK_DIR", str(lock_dir))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    state_path = workspace / "CONTROLLER_STATE.json"
    state_path.write_text(json.dumps({
        "controller_version": 1,
        "run_id": "run-D2",
        "workspace": str(workspace),
        "overall_status": "RUN_COMPLETE",  # terminal
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "stop"},
        "run_identity": {
            "run_id": "run-D2",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr-number": 416,
            "lock_dir": str(lock_dir),
        },
    }))
    json_path = workspace / "LAUNCH_RECEIPT.json"
    md_path = workspace / "LAUNCH_RECEIPT.md"
    json_path.write_text(json.dumps({
        "run_identity": {"run_id": "run-D2", "controller_version": "v1"},
    }))
    md_path.write_text(f"# Launch receipt for run-D2\n\n**Run ID:** `run-D2`\n")

    state_before = state_path.read_bytes()
    json_before = json_path.read_bytes()
    md_before = md_path.read_bytes()

    tasks = workspace / "TASKS.jsonl"
    tasks.write_text(
        json.dumps({"task_id": "t1", "task_type": "noop",
                    "integration_order": 1, "depends_on": [],
                    "blocks": []}) + "\n"
    )

    from scripts.local.autocoder_run_controller import main as controller_main
    rc = controller_main([
        "init",
        "--run-id", "run-D2",
        "--tasks-jsonl", str(tasks),
        "--workspace", str(workspace),
        "--integration-branch", "feat/x",
        "--repository", "Slideshow11/Automated-Edge-Discovery",
        "--target-pr-number", "416",
        "--current-main-sha", "e4ef77400000000000000000000000000000abcd",
        "--starting-target-sha", "c973fa6c0718293a4b5c6d70e0f781d67a0c0a1b",
    ])
    assert rc == 16, (
        f"repeated init of a COMPLETE run must reject; "
        f"got rc={rc}"
    )
    assert state_path.read_bytes() == state_before
    assert json_path.read_bytes() == json_before
    assert md_path.read_bytes() == md_before