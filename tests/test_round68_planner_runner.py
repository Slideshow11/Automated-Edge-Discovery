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

# Round-70 fix: derive the repository root from the test file
# location instead of a hardcoded /home/max/aed_hardening_v1 path.
# This makes the tests hermetic across worktrees and CI checkout
# locations. Path(__file__).resolve().parents[N] walks up from the
# test file at tests/test_X.py to the repository root.
REPO = str(Path(__file__).resolve().parents[1])
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

    def test_planner_has_script_local_path_setup(self):
        """PHASE 6 (Finding 1): planner CLI MUST add the repo
        root to ``sys.path`` before importing scripts.local
        packages."""
        from scripts.local import aed_repair_planner as planner
        import inspect
        src = inspect.getsource(planner)
        self.assertIn(
            "sys.path.insert",
            src,
            "PHASE 6: planner must add repo root to sys.path.",
        )
        self.assertIn(
            "_REPO_ROOT",
            src,
            "PHASE 6: planner must define _REPO_ROOT.",
        )

    def test_runner_has_script_local_path_setup(self):
        """PHASE 6 (Finding 1): runner CLI MUST add the repo
        root to ``sys.path``."""
        from scripts.local import aed_test_runner as runner
        import inspect
        src = inspect.getsource(runner)
        self.assertIn(
            "sys.path.insert",
            src,
            "PHASE 6: runner must add repo root to sys.path.",
        )
        self.assertIn(
            "_REPO_ROOT",
            src,
            "PHASE 6: runner must define _REPO_ROOT.",
        )

    def test_aed_pr_readiness_has_script_local_path_setup(self):
        """PHASE 4 (Finding 2): aed_pr_readiness MUST add the
        repo root to ``sys.path`` so the shared policy
        import works in script-local controller mode."""
        from scripts.local import aed_pr_readiness as R
        import inspect
        src = inspect.getsource(R)
        self.assertIn(
            "sys.path.insert",
            src,
            "PHASE 4: aed_pr_readiness must add repo root to sys.path.",
        )

    def test_paginate_review_threads_fails_closed_on_nested(self):
        """PHASE 2 (Finding 3): the shared paginator MUST
        fail closed when any thread's nested ``comments``
        connection has ``hasNextPage=true``."""
        from scripts.local import _shared_pagination as pg
        # Scoped token injection: set the env var for this
        # test only and restore it on teardown so other
        # tests in the same process are not affected.
        import os as _os
        _old_token = _os.environ.get("AED_SHARED_GITHUB_TOKEN")
        _os.environ["AED_SHARED_GITHUB_TOKEN"] = "test-token"

        # Simulate one page with hasNextPage=true on outer
        # reviewThreads but a nested comments pageInfo that
        # also has hasNextPage=true.
        fake_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": None,
                            },
                            "nodes": [
                                {
                                    "id": "T-1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "pageInfo": {
                                            "hasNextPage": True,
                                            "endCursor": "more",
                                        },
                                        "nodes": [
                                            {
                                                "databaseId": 1,
                                                "url": "x",
                                                "body": "b",
                                                "path": "p",
                                                "line": 1,
                                                "originalCommit": {"oid": "abc"},
                                                "author": {"login": "a"},
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        }
        import urllib.request as ur
        import json as _json
        class FakeResp:
            def read(self):
                return _json.dumps(fake_response).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
        original = ur.urlopen
        ur.urlopen = lambda *a, **kw: FakeResp()
        try:
            result = pg.paginate_review_threads(
                "owner", "repo", 1,
            )
            self.assertFalse(
                result["complete"],
                "must fail closed when nested comments are "
                "incomplete: " + repr(result),
            )
            self.assertEqual(
                result.get("error"),
                "nested_comments_not_paginated",
            )
            self.assertIn("T-1", result.get(
                "incomplete_nested_thread_ids", []
            ))
        finally:
            ur.urlopen = original
            # Restore the env var to its previous value to
            # avoid leaking into other tests in the same
            # process.
            if _old_token is None:
                _os.environ.pop("AED_SHARED_GITHUB_TOKEN", None)
            else:
                _os.environ["AED_SHARED_GITHUB_TOKEN"] = _old_token

    def test_test_selection_suites_point_to_existing_files(self):
        """PHASE 6 (Finding 4): the focused suites MUST
        reference only test files that exist in tests/."""
        import os
        from scripts.local import _shared_test_selection as ts
        # Resolve the repo root relative to this test file
        # so the test works regardless of the runner's
        # working directory.
        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        for component, tests in ts.TIER_2_SUITES.items():
            if tests == ["FULL_REPOSITORY_SUITE"]:
                continue
            for test_file in tests:
                path = os.path.join(repo_root, test_file)
                self.assertTrue(
                    os.path.exists(path),
                    f"{component} suite references nonexistent "
                    f"file: {test_file} (resolved: {path})",
                )

    def test_facade_log_path_handles_bare_filename(self):
        """PHASE 6 (Finding 5): ``run_selected_tests`` MUST
        handle bare-filename ``log_path`` values without
        raising ``os.makedirs("")``."""
        import os, tempfile, json
        from scripts.local import _production_facade as F
        from scripts.local._shared_test_selection import (
            ValidationTier, select_tests,
        )
        # Resolve the repo root relative to this test file
        # so the test works regardless of the runner's
        # working directory.
        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        # Create a plan that requires full validation.
        plan = select_tests(
            changed_paths=["scripts/local/x.py"],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
            final_candidate=False,
        )
        # Use a bare filename in the current working directory.
        log_file = "test_bare_log.json"
        try:
            # Use a tiny pytest_args that will fail fast but
            # we only care that the log file is written.
            result = F.run_selected_tests(
                plan=plan,
                pytest_args=["--co"],  # collect-only, fast
                cwd=repo_root,
                log_path=log_file,
            )
            # The log file MUST exist (proves bare-filename
            # handling works).
            self.assertTrue(
                os.path.exists(log_file),
                "bare-filename log path not written: "
                f"{result.get('error')}",
            )
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_aed_pr_lib_classifies_as_shared(self):
        """PHASE 6 (Round-68 Finding 1): ``scripts/local/aed_pr_lib.py``
        MUST classify as ``Component.SHARED``, NOT ``Component.AED``,
        because the broad ``aed_pr_*.py`` AED glob would otherwise
        match first and override the explicit shared entry.
        """
        from scripts.local._shared_test_selection import (
            classify_path, Component,
        )
        c = classify_path("scripts/local/aed_pr_lib.py")
        self.assertEqual(
            c, Component.SHARED,
            f"aed_pr_lib.py must be SHARED, got {c}",
        )

    def test_select_tests_empty_paths_fails_closed(self):
        """PHASE 6 (Round-68 Finding 2): an empty
        ``changed_paths`` list MUST fail closed to the full
        repository suite rather than emitting a plan with
        ``selected_tests=[]`` and ``requires_full_validation=False``.
        """
        from scripts.local._shared_test_selection import (
            select_tests, ValidationTier,
        )
        plan = select_tests(
            changed_paths=[],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
            final_candidate=False,
        )
        self.assertTrue(
            plan.requires_full_validation,
            "empty paths must fail closed to full validation",
        )
        self.assertEqual(
            plan.selected_tests, ["FULL_REPOSITORY_SUITE"]
        )
        self.assertIn(
            "empty_changed_paths",
            plan.classification_failures,
        )

    def test_classify_path_scripts_local_autocoder_routes_to_autocoder(self):
        """Round-85 follow-up: the autocoder paths under
        ``scripts/local/`` (e.g. ``scripts/local/autocoder_run_controller.py``
        and ``scripts/local/run_autocoder_*.py``) MUST be
        classified as ``Component.AUTOCODER`` so impact-selected
        validation runs the focused autocoder suite instead of
        falling through to ``UNKNOWN`` and forcing
        ``FULL_REPOSITORY_SUITE`` on every Tier-2 repair.
        """
        from scripts.local._shared_test_selection import (
            classify_path, Component,
        )
        for path in (
            "scripts/local/autocoder_run_controller.py",
            "scripts/local/run_autocoder_x.py",
            "scripts/local/build_autocoder_x.py",
            "tests/test_autocoder_x.py",
            "tests/test_run_autocoder_x.py",
            "tests/test_build_autocoder_x.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    classify_path(path), Component.AUTOCODER,
                    f"{path} must classify as AUTOCODER, got "
                    f"{classify_path(path)}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
