#!/usr/bin/env python3
"""
tests/test_validate_ci_workflow_invariants.py

Behavioral tests for validate_ci_workflow_invariants.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "local" / "validate_ci_workflow_invariants.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_check(
    workflow_path: str,
    *,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    # Always use absolute path to avoid cwd ambiguity
    abs_workflow = str(Path(workflow_path).resolve())
    args = [sys.executable, str(SCRIPT), "--workflow", abs_workflow]
    if extra_args:
        # Rebuild so extra_args (e.g. --output-json X) come RIGHT BEFORE --workflow ci_path
        # Order: python script --output-json X --workflow ci_path
        base = [sys.executable, str(SCRIPT)]
        # extra_args is [opt, val, opt, val, ...]; insert --workflow ci_path after it
        args = base + extra_args + ["--workflow", abs_workflow]
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO_ROOT)


# ---------------------------------------------------------------------------
# Test 1: current ci.yml passes
# ---------------------------------------------------------------------------

def test_current_ci_yml_passes():
    """When pointed at the real .github/workflows/ci.yml, all invariants pass."""
    result = run_check(".github/workflows/ci.yml")
    assert result.returncode == 0, (
        f"Expected exit 0 for valid ci.yml, got {result.returncode}.\n"
        f"stderr: {result.stderr}"
    )
    assert "PASS" in result.stderr
    assert "FAIL" not in result.stderr


# ---------------------------------------------------------------------------
# Test 2: missing workflow file exits 2
# ---------------------------------------------------------------------------

def test_missing_workflow_file_exits_2():
    """Missing file must exit 2 with a file-not-found message."""
    result = run_check("/nonexistent/path/ci.yml")
    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
    assert "not found" in result.stderr.lower() or "no such file" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Test 3: invalid YAML exits 2
# ---------------------------------------------------------------------------

def test_invalid_yaml_exits_2():
    """A file with invalid YAML must exit 2."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("  this is not: [invalid yaml\n    - either")
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
        assert "YAML" in result.stderr or "parse" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 4: missing pull_request fails
# ---------------------------------------------------------------------------

def test_missing_pull_request_fails():
    """A workflow without a pull_request trigger must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "fix/*", "feat/*"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "pull_request" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 5: pull_request paths fails
# ---------------------------------------------------------------------------

def test_pull_request_paths_fails():
    """A pull_request with a paths filter must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "fix/*", "feat/*"]},
                "pull_request": {
                    "branches": ["main"],
                    "paths": ["scripts/local/pr_gate_*.py"],
                },
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "paths" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 6: pull_request paths-ignore fails
# ---------------------------------------------------------------------------

def test_pull_request_paths_ignore_fails():
    """A pull_request with paths-ignore must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "pull_request": {
                    "branches": ["main"],
                    "paths-ignore": ["*.md"],
                },
                "push": {"branches": ["main", "fix/*", "feat/*"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "paths-ignore" in result.stderr.lower() or "paths_ignore" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 7: pull_request missing main fails
# ---------------------------------------------------------------------------

def test_pull_request_missing_main_fails():
    """pull_request without main in branches must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "pull_request": {"branches": ["develop"]},
                "push": {"branches": ["main", "fix/*", "feat/*"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "main" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 8: missing push fails
# ---------------------------------------------------------------------------

def test_missing_push_fails():
    """A workflow without a push trigger must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "push" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 9: push paths fails
# ---------------------------------------------------------------------------

def test_push_paths_fails():
    """A push with a paths filter must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {
                    "branches": ["main", "fix/*", "feat/*"],
                    "paths": ["scripts/local/pr_gate_*.py"],
                },
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "paths" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 10: push paths-ignore fails
# ---------------------------------------------------------------------------

def test_push_paths_ignore_fails():
    """A push with paths-ignore must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {
                    "branches": ["main", "fix/*", "feat/*"],
                    "paths-ignore": ["*.md"],
                },
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "paths-ignore" in result.stderr.lower() or "paths_ignore" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 11: push missing main fails
# ---------------------------------------------------------------------------

def test_push_missing_main_fails():
    """push without main in branches must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["develop", "fix/*", "feat/*"]},
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "main" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 12: push missing fix/* fails
# ---------------------------------------------------------------------------

def test_push_missing_fix_branch_fails():
    """push without fix/* in branches must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "feat/*"]},
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "fix" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 13: push missing feat/* fails
# ---------------------------------------------------------------------------

def test_push_missing_feat_branch_fails():
    """push without feat/* in branches must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "fix/*"]},
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "feat" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 14: missing test job fails
# ---------------------------------------------------------------------------

def test_missing_test_job_fails():
    """A workflow without jobs.test must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "fix/*", "feat/*"]},
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "validator": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "test" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 15: missing validator job fails
# ---------------------------------------------------------------------------

def test_missing_validator_job_fails():
    """A workflow without jobs.validator must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "fix/*", "feat/*"]},
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "validator" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 16: missing governance-validators job fails
# ---------------------------------------------------------------------------

def test_missing_governance_validators_job_fails():
    """A workflow without jobs.governance-validators must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "fix/*", "feat/*"]},
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
                "validator": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "governance" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 17: missing pr-gate-live-smoke job fails
# ---------------------------------------------------------------------------

def test_missing_pr_gate_live_smoke_job_fails():
    """A workflow without jobs.pr-gate-live-smoke must fail invariant."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "push": {"branches": ["main", "fix/*", "feat/*"]},
                "pull_request": {"branches": ["main"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
                "validator": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
                "governance-validators": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "pr-gate-live-smoke" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 18: output JSON contains packet_kind, passed, invariants, blockers
# ---------------------------------------------------------------------------

def test_json_output_contains_required_fields():
    """--output-json must produce a valid JSON file with required fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_json = Path(tmpdir) / "report.json"
        result = run_check(
            ".github/workflows/ci.yml",
            extra_args=["--output-json", str(out_json)],
        )
        assert result.returncode == 0
        assert out_json.exists(), "JSON output file not created"

        with open(out_json) as f:
            report = json.load(f)

        assert report.get("packet_kind") == "aed.ci.workflow_invariants.v1"
        assert "passed" in report
        assert "invariants" in report
        assert "blockers" in report
        assert isinstance(report["invariants"], list)
        assert isinstance(report["blockers"], list)


# ---------------------------------------------------------------------------
# Test 19: CLI exits 0 for valid fixture
# ---------------------------------------------------------------------------

def test_cli_exits_0_for_valid_fixture():
    """Exit code must be 0 when invariants pass."""
    result = run_check(".github/workflows/ci.yml")
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Test 20: CLI exits 1 for invariant failure
# ---------------------------------------------------------------------------

def test_cli_exits_1_for_invariant_failure():
    """Exit code must be 1 when any invariant fails."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump({
            "on": {
                "pull_request": {"branches": ["develop"]},  # missing main
                "push": {"branches": ["main", "fix/*", "feat/*"]},
            },
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            },
        }, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 21: CLI exits 2 for parse failure
# ---------------------------------------------------------------------------

def test_cli_exits_2_for_parse_failure():
    """Exit code must be 2 for YAML parse errors."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("key: [unclosed list\n")
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Test 22: no forbidden side-effect calls in the script
# ---------------------------------------------------------------------------

def test_no_forbidden_side_effect_calls():
    """Script must not contain any forbidden network/mutation patterns."""
    forbidden = [
        "requests.get", "requests.post", "requests.patch", "requests.put",
        "urllib.request", "urllib2", "httpx",
        "gh pr merge", "gh pr comment", "gh pr create",
        "gh api --graphql",
        "git push", "git commit",
        "hermes kanban",
        "memory.update", "memory.add", "fact_store",
        "skill_manage", "delegate_task", "cronjob",
        "telegram", "send_message",
    ]
    with open(SCRIPT) as f:
        content = f.read()

    found = []
    for pat in forbidden:
        if pat in content:
            found.append(pat)

    assert not found, f"Forbidden patterns found: {found}"


# ---------------------------------------------------------------------------
# Test: on: true (bare boolean) is caught
# ---------------------------------------------------------------------------

def test_bare_boolean_on_is_rejected():
    """A workflow that parses as a bare boolean (yaml.dump(True)) is not a valid workflow dict."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(True, f)  # writes "true\n...\n" — parses as Python True, not a dict
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        # Valid YAML but not a workflow dict — invariant failure (exit 1), not parse error (exit 2)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "boolean" in result.stderr.lower() or "not a dict" in result.stderr.lower()
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

def _base_workflow(**concurrency_overrides):
    """Minimal valid workflow base used for concurrency test variants."""
    return {
        "on": {
            "push": {"branches": ["main", "fix/*", "feat/*"]},
            "pull_request": {"branches": ["main"]},
        },
        "jobs": {
            "test": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            "validator": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            "governance-validators": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
            "pr-gate-live-smoke": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
        },
        **concurrency_overrides,
    }


def test_current_ci_yml_has_safe_concurrency():
    """The real ci.yml must have a safe concurrency block (PR #210 invariant)."""
    result = run_check(".github/workflows/ci.yml")
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}.\nstderr: {result.stderr}"
    assert "concurrency" in result.stderr
    assert "cancel-in-progress" in result.stderr
    assert "PASS" in result.stderr
    assert "FAIL" not in result.stderr


def test_missing_concurrency_fails():
    """A workflow without a concurrency block must fail invariant."""
    wf = _base_workflow()  # no concurrency key
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "concurrency" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_concurrency_missing_group_fails():
    """A concurrency block without a group key must fail invariant."""
    wf = _base_workflow(concurrency={"cancel-in-progress": False})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "group" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_concurrency_group_missing_discriminator_fails():
    """A concurrency group that has github.workflow but no ref/PR discriminator fails."""
    # github.workflow present, but no branch/PR discriminator — all runs would
    # be grouped together, causing cross-workflow cancellation.
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "discriminator" in result.stderr.lower() or "group" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_concurrency_group_missing_workflow_fails():
    """A concurrency group that omits github.workflow must fail invariant."""
    wf = _base_workflow(concurrency={
        "group": "${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "github.workflow" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_concurrency_group_ref_protected_fails():
    """A concurrency group with github.ref_protected (not a real var) must fail."""
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}-${{ github.ref_protected }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "discriminator" in result.stderr.lower() or "group" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_concurrency_missing_cancel_in_progress_fails():
    """A concurrency block without cancel-in-progress must fail invariant."""
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "cancel-in-progress" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_concurrency_cancel_main_runs_fails():
    """cancel-in-progress: true (hard-coded) must fail invariant — main would be cancelled."""
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": True,
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "main" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_concurrency_cancel_eq_reversed_operand_fails():
    """cancel-in-progress with reversed == operand (literal on left) must fail."""
    # refs/heads/main == github.ref — GitHub Actions evaluates this as True on main
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": "'refs/heads/main' == github.ref",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "main" in result.stderr.lower()
    finally:
        Path(path).unlink()


def test_pull_request_still_has_no_paths_filter():
    """pull_request trigger must not have paths/paths-ignore (regression check)."""
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"
        # Also assert paths-related FAILs are not present
        assert "pull_request" in result.stderr
        # The invariant check is in stderr; confirm no paths blocker surfaced
        blockers_lower = [b.lower() for b in result.stderr.split("\n") if "paths" in b.lower()]
        # Should have "no paths filter" pass lines, not "has paths filter" fail lines
        assert not any("paths filter" in b and "FAIL" in b for b in blockers_lower), \
            f"Unexpected paths filter blocker: {blockers_lower}"
    finally:
        Path(path).unlink()


def test_push_still_has_no_paths_filter():
    """push trigger must not have paths/paths-ignore (regression check)."""
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"
        blockers_lower = [b.lower() for b in result.stderr.split("\n") if "paths" in b.lower()]
        assert not any("paths filter" in b and "FAIL" in b for b in blockers_lower), \
            f"Unexpected paths filter blocker: {blockers_lower}"
    finally:
        Path(path).unlink()


def test_required_jobs_still_present():
    """All four required jobs must still be present after concurrency addition."""
    wf = _base_workflow(concurrency={
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(wf, f)
        f.flush()
        path = f.name

    try:
        result = run_check(path)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"
        for job in ("test", "validator", "governance-validators", "pr-gate-live-smoke"):
            assert job in result.stderr, f"Job '{job}' missing from output"
    finally:
        Path(path).unlink()