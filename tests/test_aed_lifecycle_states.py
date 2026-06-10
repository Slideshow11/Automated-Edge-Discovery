"""Tests for the AED lifecycle state registry CLI.

Stdlib-only: uses unittest, subprocess, and json. No pytest-only fixtures.
The tests load the registry and CLI directly from the repo paths so they
work whether pytest is invoked from the repo root or from tests/.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "schemas" / "aed_lifecycle_states_v1.json"
CLI_PATH = REPO_ROOT / "scripts" / "local" / "aed_lifecycle_states.py"


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the registry CLI as a subprocess and capture output."""
    return subprocess.run(
        [sys.executable, str(CLI_PATH), "--registry", str(REGISTRY_PATH), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class RegistryLoadTests(unittest.TestCase):
    """The registry file must exist, parse, and have the expected shape."""

    def test_registry_file_exists(self) -> None:
        self.assertTrue(REGISTRY_PATH.exists(), f"missing: {REGISTRY_PATH}")

    def test_registry_parses_as_json(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)

    def test_registry_top_level_keys(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("schema_version"), 1)
        self.assertEqual(
            data.get("registry_kind"), "aed.lifecycle_state_registry.v1"
        )
        self.assertIsInstance(data.get("states"), dict)
        self.assertGreater(len(data["states"]), 0)


class RegistryCLIValidationTests(unittest.TestCase):
    """--validate must succeed on the committed registry."""

    def test_validate_exits_zero(self) -> None:
        result = _run_cli("--validate")
        self.assertEqual(
            result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        self.assertIn("PASSED", result.stdout)


class RegistryCLIListTests(unittest.TestCase):
    """--list must print every canonical state name."""

    EXPECTED_STATES = [
        "NOT_RUN",
        "HOLD_MAIN_HEAD_MISMATCH",
        "HOLD_HEAD_CHANGED",
        "HOLD_PR_CI_PENDING",
        "HOLD_PR_CI_FAILED",
        "HOLD_CODEX_RESPONSE_PENDING",
        "HOLD_NEW_CODEX_THREAD",
        "HOLD_NEW_ACTIVE_THREAD",
        "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED",
        "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
        "HOLD_MERGE_STATE_BLOCKED",
        "HOLD_PRE_MERGE_CONDITION_FAILED",
        "HOLD_POST_MERGE_CI_PENDING",
        "HOLD_POST_MERGE_CI_FAILED",
        "HOLD_POST_MERGE_CI_NOT_OBSERVED",
        "AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR",
        "PR_MERGED_PENDING_CLOSEOUT",
        "PR_MERGED_AND_CLOSED_OUT",
    ]

    def test_list_contains_all_required_canonical_states(self) -> None:
        result = _run_cli("--list")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        names = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        for state in self.EXPECTED_STATES:
            self.assertIn(state, names, f"missing required canonical state: {state}")

    def test_list_json_shape(self) -> None:
        result = _run_cli("--list", "--json")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("states", data)
        self.assertIsInstance(data["states"], list)
        for state in self.EXPECTED_STATES:
            self.assertIn(state, data["states"])


class RegistryCLIStateTests(unittest.TestCase):
    """--state <NAME> must return a JSON object describing that state."""

    def test_state_hold_pr_ci_pending(self) -> None:
        result = _run_cli("--state", "HOLD_PR_CI_PENDING")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("HOLD_PR_CI_PENDING", data)
        entry = data["HOLD_PR_CI_PENDING"]
        self.assertEqual(entry["category"], "hold")
        self.assertFalse(entry["merge_allowed"])
        self.assertFalse(entry["human_authorization_required"])

    def test_unknown_state_exits_nonzero(self) -> None:
        result = _run_cli("--state", "NOT_A_REAL_STATE")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown state", result.stderr)


class RegistryPolicyTests(unittest.TestCase):
    """Per-state policy expectations enforced by the validator."""

    def setUp(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.states = self.data["states"]

    def test_merge_ready_requires_human_authorization_and_permits_merge(self) -> None:
        entry = self.states["MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"]
        self.assertTrue(entry["merge_allowed"])
        self.assertTrue(entry["human_authorization_required"])
        self.assertIn("pr_merge", entry["allowed_mutations"])
        self.assertIn("admin_merge", entry["forbidden_mutations"])
        self.assertIn("auto_merge", entry["forbidden_mutations"])

    def test_resolve_only_state_does_not_permit_merge(self) -> None:
        entry = self.states["CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED"]
        self.assertFalse(entry["merge_allowed"])
        self.assertTrue(entry["human_authorization_required"])
        self.assertIn("thread_resolve", entry["allowed_mutations"])
        self.assertIn("pr_merge", entry["forbidden_mutations"])

    def test_terminal_state_has_no_mutations(self) -> None:
        entry = self.states["PR_MERGED_AND_CLOSED_OUT"]
        self.assertEqual(entry["category"], "terminal")
        # Terminal state must declare no further mutations. forbidden_mutations
        # is documentation of what the terminal state no longer permits, which
        # is allowed and useful for future readers; only the active mutation
        # surface must be empty.
        self.assertEqual(entry["allowed_mutations"], [])
        self.assertEqual(entry["allowed_next_states"], [])
        self.assertFalse(entry["merge_allowed"])
        self.assertFalse(entry["closeout_allowed"])
        # If forbidden_mutations is present, it must not include any mutation
        # that is also in allowed_mutations. Since allowed_mutations is empty,
        # this is trivially satisfied — but we still assert the field is a list
        # for shape discipline.
        self.assertIsInstance(entry["forbidden_mutations"], list)

    def test_allowed_next_states_reference_known_states(self) -> None:
        known = set(self.states.keys())
        for name, entry in self.states.items():
            for nxt in entry.get("allowed_next_states", []):
                self.assertIn(
                    nxt,
                    known,
                    f"state '{name}' references unknown next state '{nxt}'",
                )

    def test_no_state_has_conflicting_allowed_and_forbidden_mutations(self) -> None:
        for name, entry in self.states.items():
            allowed = set(entry.get("allowed_mutations", []) or [])
            forbidden = set(entry.get("forbidden_mutations", []) or [])
            overlap = allowed & forbidden
            self.assertFalse(
                overlap,
                f"state '{name}' has overlapping allowed/forbidden mutations: "
                f"{sorted(overlap)}",
            )

    def test_only_merge_ready_state_has_merge_allowed_true(self) -> None:
        offenders = [
            name
            for name, entry in self.states.items()
            if entry.get("merge_allowed", False)
            and name != "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
        ]
        self.assertEqual(
            offenders,
            [],
            f"only MERGE_READY_AWAITING_HUMAN_AUTHORIZATION may set merge_allowed; "
            f"offenders: {offenders}",
        )


class RegistryCategoryCoverageTests(unittest.TestCase):
    """The registry must cover each category at least once."""

    def test_every_category_has_at_least_one_state(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        categories = set(data.get("categories", []))
        seen = {entry["category"] for entry in data["states"].values()}
        missing = sorted(categories - seen)
        self.assertEqual(missing, [], f"categories with no state: {missing}")


if __name__ == "__main__":
    unittest.main()
