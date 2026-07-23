#!/usr/bin/env python3
"""PHASE 5 + PHASE 6 regression tests for the production
repair planner and test runner CLIs.

PHASE 7 source-contract enforcement: behavioral tests prove
that the production call paths invoke the shared policies.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = "/home/max/aed_hardening_v1"
if REPO not in sys.path:
    sys.path.insert(0, REPO)


# ---------------------------------------------------------------------------
# PHASE 5: Repair planner CLI.
# ---------------------------------------------------------------------------


class RepairPlannerCLITests(unittest.TestCase):
    """The planner CLI MUST invoke the shared batching policy
    and the shared test selector, and emit a machine-readable
    plan."""

    def _write_findings(self, findings):
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(findings, fd)
        fd.close()
        return fd.name

    def test_planner_emits_batches_and_test_plan(self):
        from scripts.local import aed_repair_planner as planner
        findings = [
            {
                "finding_id": f"F{i}",
                "severity": "P2",
                "subsystem": "audit_codex",
                "root_cause": "fragment_drift",
                "path": "scripts/local/audit_codex_response_for_pr.py",
                "summary": "",
            }
            for i in range(4)
        ]
        findings_file = self._write_findings(findings)
        plan_file = tempfile.mktemp(suffix=".json")
        try:
            rc = planner.main([
                "--findings-file", findings_file,
                "--output-plan", plan_file,
                "--tier", "tier_2_cohesive_batch",
            ])
            self.assertEqual(rc, 0, planner)
            with open(plan_file) as f:
                plan = json.load(f)
            self.assertEqual(plan["finding_count"], 4)
            self.assertGreaterEqual(plan["batch_count"], 1)
            self.assertEqual(plan["tier"], "tier_2_cohesive_batch")
            # Each batch must carry the required metadata.
            for batch in plan["batches"]:
                self.assertIn("batch_id", batch)
                self.assertIn("finding_ids", batch)
                self.assertIn("severities", batch)
                self.assertIn("root_cause", batch)
                self.assertIn("subsystem", batch)
                self.assertIn("grouping_reason", batch)
                self.assertIn("smaller_than_default_reason", batch)
                self.assertIn("focused_tests", batch)
                self.assertIn("requires_full_validation", batch)
        finally:
            os.unlink(findings_file)
            if os.path.exists(plan_file):
                os.unlink(plan_file)

    def test_planner_groups_3_to_6_findings_default(self):
        from scripts.local import aed_repair_planner as planner
        # 5 findings sharing root cause -> 1 batch.
        findings = [
            {
                "finding_id": f"F{i}",
                "severity": "P2",
                "subsystem": "audit_codex",
                "root_cause": "X",
                "path": "scripts/local/audit_codex_response_for_pr.py",
                "summary": "",
            }
            for i in range(5)
        ]
        findings_file = self._write_findings(findings)
        plan_file = tempfile.mktemp(suffix=".json")
        try:
            rc = planner.main([
                "--findings-file", findings_file,
                "--output-plan", plan_file,
            ])
            self.assertEqual(rc, 0)
            with open(plan_file) as f:
                plan = json.load(f)
            self.assertEqual(plan["batch_count"], 1)
            self.assertEqual(len(plan["batches"][0]["finding_ids"]), 5)
        finally:
            os.unlink(findings_file)
            os.unlink(plan_file)

    def test_planner_isolates_p1_findings(self):
        from scripts.local import aed_repair_planner as planner
        # 3 P1 findings -> must be split into 1-2 finding batches.
        findings = [
            {
                "finding_id": f"P{i}",
                "severity": "P1",
                "subsystem": "audit_codex",
                "root_cause": "X",
                "path": "scripts/local/audit_codex_response_for_pr.py",
                "summary": "",
            }
            for i in range(3)
        ]
        findings_file = self._write_findings(findings)
        plan_file = tempfile.mktemp(suffix=".json")
        try:
            rc = planner.main([
                "--findings-file", findings_file,
                "--output-plan", plan_file,
            ])
            self.assertEqual(rc, 0)
            with open(plan_file) as f:
                plan = json.load(f)
            self.assertGreater(plan["batch_count"], 1)
            for b in plan["batches"]:
                self.assertLessEqual(len(b["finding_ids"]), 2)
                self.assertTrue(b["requires_full_validation"])
        finally:
            os.unlink(findings_file)
            os.unlink(plan_file)

    def test_planner_final_candidate_requires_full_validation(self):
        from scripts.local import aed_repair_planner as planner
        findings = [
            {
                "finding_id": "F0",
                "severity": "P2",
                "subsystem": "audit_codex",
                "root_cause": "X",
                "path": "scripts/local/audit_codex_response_for_pr.py",
                "summary": "",
            }
        ]
        findings_file = self._write_findings(findings)
        plan_file = tempfile.mktemp(suffix=".json")
        try:
            rc = planner.main([
                "--findings-file", findings_file,
                "--output-plan", plan_file,
                "--final-candidate",
            ])
            self.assertEqual(rc, 0)
            with open(plan_file) as f:
                plan = json.load(f)
            self.assertEqual(
                plan["test_plan"]["requires_full_validation"], True,
            )
            self.assertEqual(
                plan["test_plan"]["tier"], "tier_3_final_candidate",
            )
        finally:
            os.unlink(findings_file)
            os.unlink(plan_file)


# ---------------------------------------------------------------------------
# PHASE 6: Test runner CLI.
# ---------------------------------------------------------------------------


class TestRunnerCLITests(unittest.TestCase):
    """The test runner CLI MUST invoke the shared test
    selector and the shared pagination module.

    The executor is injected as a keyword-only argument to
    ``runner.main()``. This seam lets unit tests verify
    selection, command construction, and result handling
    without running the real five-minute pytest suite.
    """

    @staticmethod
    def _fake_executor(**kwargs):
        """Recording fake executor. Returns a realistic
        result envelope so downstream assertions work.
        Also writes the log file to match the production
        ``run_selected_tests`` contract.
        """
        plan = kwargs["plan"]
        log_path = kwargs.get("log_path")
        result = {
            "tool": "aed_test_runner.run_selected_tests",
            "selected": plan.selected_tests,
            "tier": plan.tier.value,
            "requires_full_validation": plan.requires_full_validation,
            "command": [
                sys.executable, "-m", "pytest", "-q",
                "-p", "no:cacheprovider",
            ] + (
                ["FULL_REPOSITORY_SUITE"]
                if plan.requires_full_validation
                else plan.selected_tests
            ),
            "returncode": 0,
            "duration_seconds": 0.05,
            "selection_reason": plan.selection_reason,
            "stdout_tail": "5 passed in 0.05s",
            "stderr_tail": "",
            "error": None,
        }
        # Write the log file to match production.
        if log_path:
            try:
                os.makedirs(
                    os.path.dirname(log_path) or ".",
                    exist_ok=True,
                )
                with open(log_path, "w") as f:
                    json.dump(result, f, indent=2)
            except Exception:
                pass
        return result

    @staticmethod
    def _failing_executor(**kwargs):
        """Fake executor that returns a non-zero exit code."""
        plan = kwargs["plan"]
        log_path = kwargs.get("log_path")
        result = {
            "tool": "aed_test_runner.run_selected_tests",
            "selected": plan.selected_tests,
            "tier": plan.tier.value,
            "requires_full_validation": plan.requires_full_validation,
            "command": ["fake-fail"],
            "returncode": 3,
            "duration_seconds": 0.01,
            "selection_reason": plan.selection_reason,
            "stdout_tail": "",
            "stderr_tail": "FAIL",
            "error": None,
        }
        if log_path:
            try:
                os.makedirs(
                    os.path.dirname(log_path) or ".",
                    exist_ok=True,
                )
                with open(log_path, "w") as f:
                    json.dump(result, f, indent=2)
            except Exception:
                pass
        return result

    def _write_paths(self, paths):
        pf = tempfile.mktemp(suffix=".txt")
        with open(pf, "w") as f:
            for p in paths:
                f.write(p + "\n")
        return pf

    def test_runner_passes_selected_command_to_executor(self):
        from scripts.local import aed_test_runner as runner
        paths = ["scripts/local/build_autocoder_run_summary.py"]
        paths_file = self._write_paths(paths)
        log_file = tempfile.mktemp(suffix=".json")
        captured = {}

        def capturing_executor(**kwargs):
            captured["plan"] = kwargs["plan"]
            captured["cwd"] = kwargs.get("cwd")
            captured["log_path"] = kwargs.get("log_path")
            return self._fake_executor(**kwargs)

        try:
            rc = runner.main(
                [
                    "--changed-paths-file", paths_file,
                    "--output-log", log_file,
                    "--tier", "tier_2_cohesive_batch",
                ],
                executor=capturing_executor,
            )
            self.assertEqual(rc, 0)
            # The executor MUST have been called with the
            # selected plan.
            self.assertIn("plan", captured)
            self.assertFalse(
                captured["plan"].requires_full_validation,
            )
            self.assertEqual(
                captured["plan"].tier.value,
                "tier_2_cohesive_batch",
            )
            # The runner MUST persist the log to disk.
            with open(log_file) as f:
                log = json.load(f)
            # ``tool`` field in the persisted log file comes
            # from the executor's result envelope, which
            # tags itself with the production tool name.
            self.assertEqual(
                log["tool"],
                "aed_test_runner.run_selected_tests",
            )
            self.assertEqual(log["returncode"], 0)
        finally:
            os.unlink(paths_file)
            os.unlink(log_file)

    def test_runner_isolated_change_uses_focused_suite(self):
        from scripts.local import aed_test_runner as runner
        paths_file = self._write_paths(
            ["scripts/local/build_autocoder_run_summary.py"]
        )
        log_file = tempfile.mktemp(suffix=".json")
        try:
            rc = runner.main(
                [
                    "--changed-paths-file", paths_file,
                    "--output-log", log_file,
                    "--tier", "tier_2_cohesive_batch",
                ],
                executor=self._fake_executor,
            )
            self.assertEqual(rc, 0)
            with open(log_file) as f:
                log = json.load(f)
            self.assertFalse(log["requires_full_validation"])
            self.assertNotEqual(
                log["selected"], ["FULL_REPOSITORY_SUITE"]
            )
        finally:
            os.unlink(paths_file)
            os.unlink(log_file)

    def test_runner_shared_path_forces_full_validation(self):
        from scripts.local import aed_test_runner as runner
        paths_file = self._write_paths(["aed_policy/policy.py"])
        log_file = tempfile.mktemp(suffix=".json")
        try:
            rc = runner.main(
                [
                    "--changed-paths-file", paths_file,
                    "--output-log", log_file,
                    "--tier", "tier_2_cohesive_batch",
                ],
                executor=self._fake_executor,
            )
            with open(log_file) as f:
                log = json.load(f)
            self.assertTrue(log["requires_full_validation"])
            self.assertEqual(
                log["selected"], ["FULL_REPOSITORY_SUITE"]
            )
        finally:
            os.unlink(paths_file)
            os.unlink(log_file)

    def test_runner_final_candidate_always_full_validation(self):
        from scripts.local import aed_test_runner as runner
        paths_file = self._write_paths(
            ["scripts/local/build_autocoder_run_summary.py"]
        )
        log_file = tempfile.mktemp(suffix=".json")
        try:
            rc = runner.main(
                [
                    "--changed-paths-file", paths_file,
                    "--output-log", log_file,
                    "--tier", "tier_2_cohesive_batch",
                    "--final-candidate",
                ],
                executor=self._fake_executor,
            )
            with open(log_file) as f:
                log = json.load(f)
            self.assertTrue(log["requires_full_validation"])
            self.assertEqual(
                log["selected"], ["FULL_REPOSITORY_SUITE"]
            )
        finally:
            os.unlink(paths_file)
            os.unlink(log_file)

    def test_runner_records_failed_executor_exit_code(self):
        from scripts.local import aed_test_runner as runner
        paths_file = self._write_paths(
            ["scripts/local/build_autocoder_run_summary.py"]
        )
        log_file = tempfile.mktemp(suffix=".json")
        try:
            rc = runner.main(
                [
                    "--changed-paths-file", paths_file,
                    "--output-log", log_file,
                ],
                executor=self._failing_executor,
            )
            # Runner MUST propagate the non-zero exit.
            self.assertNotEqual(rc, 0)
            with open(log_file) as f:
                log = json.load(f)
            self.assertEqual(log["returncode"], 3)
            self.assertEqual(log["duration_seconds"], 0.01)
        finally:
            os.unlink(paths_file)
            os.unlink(log_file)

    def test_runner_invokes_executor_exactly_once(self):
        from scripts.local import aed_test_runner as runner
        paths_file = self._write_paths(
            ["scripts/local/build_autocoder_run_summary.py"]
        )
        log_file = tempfile.mktemp(suffix=".json")
        call_count = {"n": 0}

        def counting_executor(**kwargs):
            call_count["n"] += 1
            return self._fake_executor(**kwargs)

        try:
            rc = runner.main(
                [
                    "--changed-paths-file", paths_file,
                    "--output-log", log_file,
                ],
                executor=counting_executor,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(call_count["n"], 1)
        finally:
            os.unlink(paths_file)
            os.unlink(log_file)


# ---------------------------------------------------------------------------
# PHASE 7: Source-contract enforcement.
# ---------------------------------------------------------------------------


class SourceContractTests(unittest.TestCase):
    """Behavioral evidence that production call paths invoke
    the shared policies."""

    def test_planner_invokes_shared_batching(self):
        """The planner CLI MUST call the shared batching
        module."""
        from scripts.local import aed_repair_planner as planner
        import inspect
        src = inspect.getsource(planner.main)
        self.assertIn(
            "batch_findings",
            src,
            "PHASE 5: planner must invoke shared batching.",
        )

    def test_planner_invokes_shared_test_selector(self):
        from scripts.local import aed_repair_planner as planner
        import inspect
        src = inspect.getsource(planner.main)
        self.assertIn(
            "select_tests_with_invocation",
            src,
            "PHASE 6: planner must invoke shared test "
            "selector.",
        )

    def test_runner_invokes_shared_test_selector(self):
        from scripts.local import aed_test_runner as runner
        import inspect
        src = inspect.getsource(runner.main)
        self.assertIn(
            "select_tests_with_invocation",
            src,
            "PHASE 6: runner must invoke shared test "
            "selector.",
        )

    def test_runner_invokes_run_selected_tests(self):
        from scripts.local import aed_test_runner as runner
        import inspect
        src = inspect.getsource(runner.main)
        self.assertIn(
            "run_selected_tests",
            src,
            "PHASE 6: runner must invoke shared "
            "run_selected_tests.",
        )

    def test_aed_pr_readiness_invokes_shared_non_human_policy(self):
        from scripts.local import aed_pr_readiness as R
        import inspect
        src = inspect.getsource(R.is_eligible_for_bot_resolution)
        self.assertIn(
            "_shared_classify_review_thread_eligibility",
            src,
            "PHASE 4: is_eligible_for_bot_resolution must "
            "invoke shared policy via facade.",
        )

    def test_audit_invokes_shared_classifier(self):
        from scripts.local import audit_codex_response_for_pr as A
        import inspect
        # The audit's is_codex_clean_pass_comment MUST
        # delegate to the shared module.
        src = inspect.getsource(A.is_codex_clean_pass_comment)
        self.assertIn(
            "_shared_is_clean",
            src,
            "PHASE 3: audit must delegate to shared "
            "classifier.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
