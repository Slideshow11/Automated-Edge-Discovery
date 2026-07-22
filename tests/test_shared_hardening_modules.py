#!/usr/bin/env python3
"""Regression tests for the autocoder control-plane hardening
shared modules added in PR #412.
"""
import os
import sys
import unittest

REPO = "/home/max/aed_hardening_v1"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
SCRIPTS = os.path.join(REPO, "scripts", "local")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


# ---------------------------------------------------------------------------
# R-1: Pagination
# ---------------------------------------------------------------------------

class PaginationTests(unittest.TestCase):
    def setUp(self):
        """Skip live tests when no GitHub token is available."""
        import os
        self._has_token = bool(os.environ.get("AED_SHARED_GITHUB_TOKEN"))
        if not self._has_token:
            import subprocess
            try:
                subprocess.check_output(["gh", "auth", "token"], text=True, timeout=10)
                self._has_token = True
            except Exception:
                self._has_token = False

    def _require_live(self):
        if not self._has_token:
            self.skipTest("requires GitHub auth (gh or AED_SHARED_GITHUB_TOKEN)")

    def test_paginate_review_threads_complete(self):
        """Pagination must continue until hasNextPage=False.
        With >100 review threads the first page is NOT the
        complete inventory."""
        self._require_live()
        from scripts.local import _shared_pagination as pg
        # The repository has 102 review threads (verified
        # during PR #411 closeout). The helper must paginate
        # through both pages.
        res = pg.paginate_review_threads(
            "Slideshow11", "Automated-Edge-Discovery", 411
        )
        self.assertTrue(res["complete"], res)
        self.assertGreaterEqual(len(res["nodes"]), 100,
            "must span at least the full first page")
        self.assertGreaterEqual(res["pages"], 2,
            "must span at least two pages")

    def test_paginate_issue_comments_complete(self):
        self._require_live()
        from scripts.local import _shared_pagination as pg
        res = pg.paginate_issue_comments(
            "Slideshow11", "Automated-Edge-Discovery", 411
        )
        self.assertTrue(res["complete"], res)

    def test_paginate_formal_reviews_complete(self):
        self._require_live()
        from scripts.local import _shared_pagination as pg
        res = pg.paginate_formal_reviews(
            "Slideshow11", "Automated-Edge-Discovery", 411
        )
        self.assertTrue(res["complete"], res)

    def test_paginate_changed_files_complete(self):
        self._require_live()
        from scripts.local import _shared_pagination as pg
        res = pg.paginate_changed_files(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=411,
        )
        self.assertTrue(res["complete"], res)
        # PR #411 had 38 files changed.
        self.assertGreaterEqual(len(res["nodes"]), 1)

    def test_paginate_workflow_runs_complete(self):
        self._require_live()
        from scripts.local import _shared_pagination as pg
        res = pg.paginate_workflow_runs(
            repo="Slideshow11/Automated-Edge-Discovery",
            head_sha="4d41041d4d86826f68edd10fbe857f90263a4423",
        )
        self.assertTrue(res["complete"], res)
        self.assertGreater(len(res["nodes"]), 0,
            "must find the exact-head CI run")

    def test_paginate_jobs_for_run_complete(self):
        self._require_live()
        from scripts.local import _shared_pagination as pg
        res = pg.paginate_jobs_for_run(
            repo="Slideshow11/Automated-Edge-Discovery",
            run_id=29962043995,
        )
        self.assertTrue(res["complete"], res)
        self.assertGreaterEqual(len(res["nodes"]), 5,
            "CI run must have at least 5 jobs")

    def test_pagination_safety_cap_fail_closed(self):
        """Mock safety cap failure (no live API needed)."""
        import json as _json
        import urllib.request as ur
        import subprocess as sp
        from scripts.local import _shared_pagination as pg

        class FakeResp:
            def __init__(self):
                self.payload = _json.dumps({
                    "data": {
                        "x": {
                            "nodes": [{"id": "T1"}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        }
                    }
                }).encode()
            def read(self):
                return self.payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def fake_urlopen(req, timeout=None):
            return FakeResp()

        def fake_check_output(cmd, *a, **kw):
            return "fake-token\n"

        original_urlopen = ur.urlopen
        original_check = sp.check_output
        ur.urlopen = fake_urlopen
        sp.check_output = fake_check_output
        try:
            res = pg.paginate_graphql_connection(
                owner="x", name="y", pr_number=1,
                query="dummy",
                path=("data", "x"),
                safety_cap=1,
            )
            self.assertFalse(res["complete"])
            self.assertTrue(res["capped"])
        finally:
            ur.urlopen = original_urlopen
            sp.check_output = original_check

    def test_more_than_100_review_threads(self):
        """Regression test: more than 100 review threads.
        A finding exists on a later page (Round-50 finding,
        db_id 3627467490) — pagination must surface it.
        """
        self._require_live()
        from scripts.local import _shared_pagination as pg
        res = pg.paginate_review_threads(
            "Slideshow11", "Automated-Edge-Discovery", 411
        )
        self.assertGreater(len(res["nodes"]), 100,
            "test contract: PR #411 must have more than "
            "100 review threads to exercise pagination")
        self.assertTrue(res["complete"])
        # The Round-50 finding's thread id is recorded in the
        # PR. It MUST appear on a later page.
        thread_ids = {t.get("id") for t in res["nodes"]}
        self.assertIn("PRRT_kwDOSHFpYM6SrtNc", thread_ids,
            "Round-50 finding must be on a later page")


# ---------------------------------------------------------------------------
# R-2: Shared Codex classifier
# ---------------------------------------------------------------------------

class SharedCodexClassifierTests(unittest.TestCase):
    def test_clean_exact_phrase(self):
        from scripts.local._shared_codex_classifier import (
            is_codex_clean_pass_comment,
        )
        self.assertTrue(is_codex_clean_pass_comment(
            "Codex Review: Didn't find any major issues. :tada:"
        ))

    def test_clean_no_findings_reported(self):
        from scripts.local._shared_codex_classifier import (
            is_codex_clean_pass_comment,
        )
        self.assertTrue(is_codex_clean_pass_comment(
            "### 💡 Codex Review\n\n**Reviewed commit:** `abc123`\n\n"
            "No findings reported.\n"
        ))

    def test_clean_no_issues_found(self):
        from scripts.local._shared_codex_classifier import (
            is_codex_clean_pass_comment,
        )
        self.assertTrue(is_codex_clean_pass_comment(
            "### 💡 Codex Review\n\nNo issues found.\n"
        ))

    def test_summary_with_finding_badge_is_finding(self):
        from scripts.local._shared_codex_classifier import (
            is_codex_clean_pass_comment,
        )
        body = (
            "### 💡 Codex Review\n\n"
            "**<sub><sub>![P1 Badge]...</sub></sub>  Finding**\n"
        )
        self.assertFalse(is_codex_clean_pass_comment(body),
            "summary with finding badge MUST NOT be clean")

    def test_body_level_finding_badge_not_clean(self):
        from scripts.local._shared_codex_classifier import (
            is_codex_clean_pass_comment,
        )
        body = (
            "I looked and found no major issues overall.\n"
            "**<sub><sub>![P2 Badge]...</sub></sub>  Finding**\n"
        )
        self.assertFalse(is_codex_clean_pass_comment(body))

    def test_classify_codex_response_finding(self):
        from scripts.local._shared_codex_classifier import (
            classify_codex_response,
        )
        cand = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge]...</sub></sub>  Finding**",
            "submitted_at": "2026-07-22T10:00:00Z",
        }
        v = classify_codex_response(
            kind="review",
            candidate=cand,
            head="abc",
            expected_head_sha="abc",
        )
        self.assertEqual(v, "FINDING")

    def test_classify_codex_response_clean_phrase(self):
        from scripts.local._shared_codex_classifier import (
            classify_codex_response,
        )
        cand = {
            "user": {"login": "chatgpt-codex-connector"},
            "body": "Codex Review: Didn't find any major issues.",
            "created_at": "2026-07-22T10:00:00Z",
        }
        v = classify_codex_response(
            kind="issue_comment",
            candidate=cand,
            head="abc",
            expected_head_sha="abc",
        )
        self.assertEqual(v, "CLEAN_PASS")

    def test_classify_codex_response_non_codex(self):
        from scripts.local._shared_codex_classifier import (
            classify_codex_response,
        )
        cand = {
            "user": {"login": "some-human"},
            "body": "Looks good",
        }
        v = classify_codex_response(
            kind="review",
            candidate=cand,
            head="abc",
            expected_head_sha="abc",
        )
        self.assertIsNone(v, "non-Codex author must not classify")

    def test_extract_review_commit_oid_camelcase(self):
        from scripts.local._shared_codex_classifier import (
            extract_review_commit_oid,
        )
        self.assertEqual(
            extract_review_commit_oid({"commit": {"oid": "abc123"}}),
            "abc123",
        )

    def test_extract_review_commit_oid_snakecase(self):
        from scripts.local._shared_codex_classifier import (
            extract_review_commit_oid,
        )
        self.assertEqual(
            extract_review_commit_oid({"commit_id": "def456"}),
            "def456",
        )


# ---------------------------------------------------------------------------
# R-3: Non-human review policy
# ---------------------------------------------------------------------------

class NonHumanPolicyTests(unittest.TestCase):
    def test_codex_only_eligible_when_all_conditions_met(self):
        from scripts.local._shared_non_human_policy import (
            classify_review_thread,
            ParticipantInventory,
            RepairEvidence,
            CodexCleanEvidence,
            LiveHeadMatch,
            ReviewerClass,
        )
        v = classify_review_thread(
            participants=["chatgpt-codex-connector"],
            inventory_complete=True,
            repair=RepairEvidence(
                finding_thread_id="T-1",
                anchor_sha="abc",
                current_head_sha="def",
                repair_present=True,
                ancestry_ok=True,
            ),
            clean_evidence=CodexCleanEvidence(
                clean_response_id="R-1",
                clean_response_kind="issue_comment",
                clean_response_ts="2026-07-22T10:00:00Z",
                head_sha="def",
                live_head_matches_at_collection=True,
                no_newer_finding=True,
            ),
            live_head_match=LiveHeadMatch(
                expected_head="def",
                live_head="def",
            ),
        )
        self.assertTrue(v.eligible, v.reasons)
        self.assertEqual(v.reviewer_classes, [ReviewerClass.CODEX])

    def test_human_participant_hard_stop(self):
        from scripts.local._shared_non_human_policy import (
            classify_review_thread,
            RepairEvidence,
            CodexCleanEvidence,
            LiveHeadMatch,
        )
        v = classify_review_thread(
            participants=["chatgpt-codex-connector", "some-human"],
            inventory_complete=True,
            repair=RepairEvidence(
                finding_thread_id="T-1",
                anchor_sha="abc",
                current_head_sha="def",
                repair_present=True,
                ancestry_ok=True,
            ),
            clean_evidence=CodexCleanEvidence(
                clean_response_id="R-1",
                clean_response_kind="issue_comment",
                clean_response_ts="2026-07-22T10:00:00Z",
                head_sha="def",
                live_head_matches_at_collection=True,
                no_newer_finding=True,
            ),
            live_head_match=LiveHeadMatch(
                expected_head="def",
                live_head="def",
            ),
        )
        self.assertFalse(v.eligible)
        self.assertIn("human_participant", v.reasons)

    def test_incomplete_inventory_hard_stop(self):
        from scripts.local._shared_non_human_policy import (
            classify_review_thread,
            RepairEvidence,
            CodexCleanEvidence,
            LiveHeadMatch,
        )
        v = classify_review_thread(
            participants=["chatgpt-codex-connector"],
            inventory_complete=False,
            repair=RepairEvidence(
                finding_thread_id="T-1",
                anchor_sha="abc",
                current_head_sha="def",
                repair_present=True,
                ancestry_ok=True,
            ),
            clean_evidence=CodexCleanEvidence(
                clean_response_id="R-1",
                clean_response_kind="issue_comment",
                clean_response_ts="2026-07-22T10:00:00Z",
                head_sha="def",
                live_head_matches_at_collection=True,
                no_newer_finding=True,
            ),
            live_head_match=LiveHeadMatch(
                expected_head="def",
                live_head="def",
            ),
        )
        self.assertFalse(v.eligible)
        self.assertIn("inventory_incomplete", v.reasons)

    def test_ancestry_failed_hard_stop(self):
        from scripts.local._shared_non_human_policy import (
            classify_review_thread,
            RepairEvidence,
            CodexCleanEvidence,
            LiveHeadMatch,
        )
        v = classify_review_thread(
            participants=["chatgpt-codex-connector"],
            inventory_complete=True,
            repair=RepairEvidence(
                finding_thread_id="T-1",
                anchor_sha="abc",
                current_head_sha="def",
                repair_present=True,
                ancestry_ok=False,
            ),
            clean_evidence=CodexCleanEvidence(
                clean_response_id="R-1",
                clean_response_kind="issue_comment",
                clean_response_ts="2026-07-22T10:00:00Z",
                head_sha="def",
                live_head_matches_at_collection=True,
                no_newer_finding=True,
            ),
            live_head_match=LiveHeadMatch(
                expected_head="def",
                live_head="def",
            ),
        )
        self.assertFalse(v.eligible)
        self.assertIn("ancestry_failed", v.reasons)

    def test_newer_finding_hard_stop(self):
        from scripts.local._shared_non_human_policy import (
            classify_review_thread,
            RepairEvidence,
            CodexCleanEvidence,
            LiveHeadMatch,
        )
        v = classify_review_thread(
            participants=["chatgpt-codex-connector"],
            inventory_complete=True,
            repair=RepairEvidence(
                finding_thread_id="T-1",
                anchor_sha="abc",
                current_head_sha="def",
                repair_present=True,
                ancestry_ok=True,
            ),
            clean_evidence=CodexCleanEvidence(
                clean_response_id="R-1",
                clean_response_kind="issue_comment",
                clean_response_ts="2026-07-22T10:00:00Z",
                head_sha="def",
                live_head_matches_at_collection=True,
                no_newer_finding=False,
            ),
            live_head_match=LiveHeadMatch(
                expected_head="def",
                live_head="def",
            ),
        )
        self.assertFalse(v.eligible)
        self.assertIn("newer_finding_present", v.reasons)

    def test_live_head_moved_hard_stop(self):
        from scripts.local._shared_non_human_policy import (
            classify_review_thread,
            RepairEvidence,
            CodexCleanEvidence,
            LiveHeadMatch,
        )
        v = classify_review_thread(
            participants=["chatgpt-codex-connector"],
            inventory_complete=True,
            repair=RepairEvidence(
                finding_thread_id="T-1",
                anchor_sha="abc",
                current_head_sha="def",
                repair_present=True,
                ancestry_ok=True,
            ),
            clean_evidence=CodexCleanEvidence(
                clean_response_id="R-1",
                clean_response_kind="issue_comment",
                clean_response_ts="2026-07-22T10:00:00Z",
                head_sha="def",
                live_head_matches_at_collection=True,
                no_newer_finding=True,
            ),
            live_head_match=LiveHeadMatch(
                expected_head="def",
                live_head="xyz",
            ),
        )
        self.assertFalse(v.eligible)
        self.assertIn("live_head_moved", v.reasons)


# ---------------------------------------------------------------------------
# R-4: Cohesive repair batching
# ---------------------------------------------------------------------------

class BatchingTests(unittest.TestCase):
    def test_default_batch_size_3_to_6(self):
        from scripts.local._shared_batching import (
            FindingRecord, Severity, batch_findings,
        )
        findings = [
            FindingRecord(
                finding_id=f"F{i}",
                severity=Severity.P2,
                subsystem="audit_codex",
                root_cause="clean_pass_predicate_drift",
                path="scripts/local/audit_codex_response_for_pr.py",
            )
            for i in range(5)
        ]
        batches = batch_findings(findings)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0].finding_ids), 5)

    def test_subsystem_change_closes_batch(self):
        from scripts.local._shared_batching import (
            FindingRecord, Severity, batch_findings,
        )
        findings = [
            FindingRecord(
                finding_id="F0",
                severity=Severity.P2,
                subsystem="audit_codex",
                root_cause="X",
                path="scripts/local/audit_codex_response_for_pr.py",
            ),
            FindingRecord(
                finding_id="F1",
                severity=Severity.P2,
                subsystem="poller",
                root_cause="X",
                path="scripts/local/codex_review_poller.py",
            ),
        ]
        batches = batch_findings(findings)
        self.assertEqual(len(batches), 2,
            "different subsystems MUST split into separate batches")

    def test_p1_isolation_1_to_2(self):
        from scripts.local._shared_batching import (
            FindingRecord, Severity, batch_findings,
        )
        findings = [
            FindingRecord(
                finding_id=f"P{i}",
                severity=Severity.P1,
                subsystem="audit_codex",
                root_cause="critical_safety",
                path="scripts/local/audit_codex_response_for_pr.py",
            )
            for i in range(3)
        ]
        batches = batch_findings(findings)
        # P1 findings must be split into 1-2 finding batches.
        self.assertGreater(len(batches), 1,
            "P1 findings must be split for isolation")
        for b in batches:
            self.assertLessEqual(len(b.finding_ids), 2)
            self.assertTrue(b.requires_full_validation)

    def test_root_cause_change_closes_batch(self):
        from scripts.local._shared_batching import (
            FindingRecord, Severity, batch_findings,
        )
        findings = [
            FindingRecord(
                finding_id="F0",
                severity=Severity.P2,
                subsystem="audit_codex",
                root_cause="A",
                path="scripts/local/audit_codex_response_for_pr.py",
            ),
            FindingRecord(
                finding_id="F1",
                severity=Severity.P2,
                subsystem="audit_codex",
                root_cause="B",
                path="scripts/local/audit_codex_response_for_pr.py",
            ),
        ]
        batches = batch_findings(findings)
        self.assertEqual(len(batches), 2,
            "different root causes MUST split into separate batches")

    def test_tightly_coupled_up_to_8(self):
        from scripts.local._shared_batching import (
            FindingRecord, Severity, batch_findings,
        )
        findings = [
            FindingRecord(
                finding_id=f"F{i}",
                severity=Severity.P2,
                subsystem="audit_codex",
                root_cause="X",
                path="scripts/local/audit_codex_response_for_pr.py",
            )
            for i in range(8)
        ]
        batches = batch_findings(findings)
        self.assertEqual(len(batches), 1,
            "8 tightly coupled findings MUST fit in one batch")
        self.assertEqual(len(batches[0].finding_ids), 8)


# ---------------------------------------------------------------------------
# R-5: Impact-based test selection
# ---------------------------------------------------------------------------

class TestSelectionTests(unittest.TestCase):
    def test_isolated_autocoder_change_selects_autocoder_tests(self):
        from scripts.local._shared_test_selection import (
            select_tests, ValidationTier,
        )
        plan = select_tests(
            changed_paths=[
                "scripts/local/build_autocoder_run_summary.py",
                "tests/test_autocoder_run_controller.py",
            ],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
        )
        self.assertIn("autocoder", [c.value for c in plan.components])
        self.assertFalse(plan.requires_full_validation)

    def test_isolated_aed_change_selects_aed_tests(self):
        from scripts.local._shared_test_selection import (
            select_tests, ValidationTier,
        )
        plan = select_tests(
            changed_paths=[
                "scripts/local/audit_codex_response_for_pr.py",
                "tests/test_audit_codex_response_for_pr.py",
            ],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
        )
        self.assertIn("aed", [c.value for c in plan.components])
        self.assertFalse(plan.requires_full_validation)

    def test_shared_dependency_change_selects_full_suite(self):
        from scripts.local._shared_test_selection import (
            select_tests, ValidationTier,
        )
        plan = select_tests(
            changed_paths=["aed_policy/policy.py"],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
        )
        self.assertTrue(plan.requires_full_validation)
        self.assertEqual(plan.selected_tests, ["FULL_REPOSITORY_SUITE"])

    def test_unknown_path_selects_full_suite(self):
        from scripts.local._shared_test_selection import (
            select_tests, ValidationTier,
        )
        plan = select_tests(
            changed_paths=["some/unknown/path.py"],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
        )
        self.assertTrue(plan.requires_full_validation)

    def test_final_candidate_always_selects_full_suite(self):
        from scripts.local._shared_test_selection import (
            select_tests, ValidationTier,
        )
        plan = select_tests(
            changed_paths=[
                "scripts/local/build_autocoder_run_summary.py",
            ],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
            final_candidate=True,
        )
        self.assertTrue(plan.requires_full_validation)
        self.assertEqual(plan.selected_tests, ["FULL_REPOSITORY_SUITE"])

    def test_machine_readable_output(self):
        import json
        from scripts.local._shared_test_selection import (
            select_tests, ValidationTier,
        )
        plan = select_tests(
            changed_paths=["scripts/local/aed_pr.py"],
            tier=ValidationTier.TIER_2_COHESIVE_BATCH,
        )
        mr = plan.to_machine_readable()
        # Round-trip through JSON to prove machine-readability.
        j = json.dumps(mr)
        self.assertIn("tier", j)
        self.assertIn("selected_tests", j)
        self.assertIn("selection_reason", j)


if __name__ == "__main__":
    unittest.main(verbosity=2)
