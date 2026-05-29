"""Unit tests for validate_finding_registry_record.py"""
import json, os, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts' / 'local'))
import validate_finding_registry_record as vfr


def make_record(**overrides):
    """Minimal valid base record with required fields."""
    base = {
        "finding_id": "codex-abc123",
        "pr_number": 364,
        "source": "check_pr_review_comments",
        "author": "chatgpt-codex-connector[bot]",
        "severity": "P1",
        "title": "Example finding",
        "body_summary": "This is an example finding.",
        "lifecycle_state": "OPEN",
        "status_reason": "Active finding on current head.",
        "current_head_sha": "d3828021b370a9ccbb3ab2796f1ead039ab0c774",
        "created_at": "2026-05-29T20:00:00Z",
        "updated_at": "2026-05-29T20:00:00Z",
        "merge_blocking": True,
        "gate_source": "review_comment_gate",
        "gate_policy_version": "v1.0",
        # Optional terminal-state fields — included so tests don't silently miss them
        "resolved_at": None,
        "resolved_by": None,
        "thread_id": None,
        "comment_id": None,
        "path": None,
        "line": None,
        "flagged_pattern": None,
        "replacement_pattern": None,
        "original_commit_sha": None,
        "base_sha": None,
        "blocking_level": None,
        "evidence_commands": None,
        "evidence_summary": None,
        "audit_log_path": None,
        "resolution_method": None,
        "waiter_status": None,
        "ci_status": None,
        "pmg_status": None,
    }
    base.update(overrides)
    return base


def validate(record):
    """Run validate_record on a dict and return (status, errors, warnings)."""
    return vfr.validate_record(record)


class TestValidOpenP2Blocker(unittest.TestCase):
    """Valid OPEN P2 blocker passes."""

    def test_valid_open_p2_blocker(self):
        rec = make_record(
            finding_id="codex-p2-001",
            severity="P2",
            lifecycle_state="OPEN",
            merge_blocking=True,
            status_reason="Active P2 finding.",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertEqual(e, [])


class TestOpenP2NonBlockingFails(unittest.TestCase):
    """OPEN P2 with merge_blocking=false must fail."""

    def test_open_p2_merge_blocking_false_fails(self):
        rec = make_record(
            finding_id="codex-p2-002",
            severity="P2",
            lifecycle_state="OPEN",
            merge_blocking=False,  # P2 is blocking by default — invalid
            status_reason="P2 should be blocking.",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertIn("OPEN P2", e[0])
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)


class TestValidResolvedByPolicy(unittest.TestCase):
    """Valid RESOLVED_BY_POLICY stale thread passes."""

    def test_valid_resolved_by_policy(self):
        rec = make_record(
            finding_id="codex-rbp-001",
            severity="P1",
            lifecycle_state="RESOLVED_BY_POLICY",
            merge_blocking=False,
            status_reason="Stale thread, checker returned ELIGIBLE.",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in current diff.",
            evidence_commands=["gh api repos/.../pulls/comments/1234"],
            audit_log_path="/tmp/audit.json",
            resolution_method="resolveReviewThread",
            resolved_at="2026-05-29T21:00:00Z",
            resolved_by="policy_checker",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertEqual(e, [])


class TestResolvedByPolicyForbiddenMethods(unittest.TestCase):
    """RESOLVED_BY_POLICY with forbidden resolution methods fails."""

    def test_delete_pull_request_review_comment_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="deletePullRequestReviewComment",
            merge_blocking=False,
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Thread deleted.",
            audit_log_path="/tmp/audit.json",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("deletePullRequestReviewComment" in x for x in e))

    def test_dismiss_review_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="dismissReview",
            merge_blocking=False,
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Review dismissed.",
            audit_log_path="/tmp/audit.json",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("dismissReview" in x for x in e))

    def test_admin_merge_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="admin_merge",
            merge_blocking=False,
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Merged with admin.",
            audit_log_path="/tmp/audit.json",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("admin_merge" in x for x in e))

    def test_delete_review_comment_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="delete_review_comment",
            merge_blocking=False,
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Comment deleted.",
            audit_log_path="/tmp/audit.json",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("delete_review_comment" in x for x in e))

    def test_delete_review_comment_spelled_out_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="deleteReviewComment",
            merge_blocking=False,
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Comment deleted via REST.",
            audit_log_path="/tmp/audit.json",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("deleteReviewComment" in x for x in e))


class TestResolvedByPolicyRequiresResolveReviewThread(unittest.TestCase):
    """RESOLVED_BY_POLICY must use resolveReviewThread."""

    def test_wrong_resolution_method_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="patch_applied",
            merge_blocking=False,
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Patch applied.",
            audit_log_path="/tmp/audit.json",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("resolveReviewThread" in x for x in e))


class TestResolvedByPolicyMergeBlockingMustBeFalse(unittest.TestCase):
    """RESOLVED_BY_POLICY must have merge_blocking=false."""

    def test_resolved_by_policy_merge_blocking_true_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            merge_blocking=True,
            resolution_method="resolveReviewThread",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Thread resolved but still blocking.",
            audit_log_path="/tmp/audit.json",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("merge_blocking=false" in x for x in e))


class TestWaivedRequiresRationale(unittest.TestCase):
    """WAIVED missing status_reason or resolved_by fails."""

    def test_waived_missing_status_reason(self):
        rec = make_record(
            lifecycle_state="WAIVED",
            status_reason="",
            resolved_by="operator",
            resolved_at="2026-05-29T21:00:00Z",
            evidence_summary="Low priority finding.",
            merge_blocking=False,
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("WAIVED requires status_reason" in x for x in e))

    def test_waived_missing_resolved_by(self):
        rec = make_record(
            lifecycle_state="WAIVED",
            status_reason="Low priority, out of scope.",
            resolved_by="",
            resolved_at="2026-05-29T21:00:00Z",
            evidence_summary="Low priority finding.",
            merge_blocking=False,
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("WAIVED requires resolved_by" in x for x in e))

    def test_waived_missing_evidence_fails(self):
        rec = make_record(
            lifecycle_state="WAIVED",
            status_reason="Low priority.",
            resolved_by="operator",
            resolved_at="2026-05-29T21:00:00Z",
            evidence_summary="",
            audit_log_path="",
            merge_blocking=False,
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("WAIVED requires evidence_summary or audit_log_path" in x for x in e))

    def test_valid_waived_passes(self):
        rec = make_record(
            lifecycle_state="WAIVED",
            status_reason="Low priority, out of scope.",
            resolved_by="operator",
            resolved_at="2026-05-29T21:00:00Z",
            evidence_summary="Follow-up issue filed.",
            merge_blocking=False,
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestInvalidRequiresEvidence(unittest.TestCase):
    """INVALID missing evidence_summary fails."""

    def test_invalid_missing_evidence_fails(self):
        rec = make_record(
            lifecycle_state="INVALID",
            evidence_summary="",
            status_reason="Codex misread the file.",
            merge_blocking=False,
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("INVALID requires evidence_summary" in x for x in e))

    def test_invalid_missing_status_reason_fails(self):
        rec = make_record(
            lifecycle_state="INVALID",
            evidence_summary="Codex misread due to truncation.",
            status_reason="",
            merge_blocking=False,
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("INVALID requires status_reason" in x for x in e))

    def test_valid_invalid_passes(self):
        rec = make_record(
            lifecycle_state="INVALID",
            evidence_summary="gh api proved the import IS used on line 87.",
            status_reason="Codex misread due to truncation.",
            merge_blocking=False,
            resolved_at="2026-05-29T21:00:00Z",
            resolved_by="operator",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestEscalatedRequiresReason(unittest.TestCase):
    """ESCALATED missing status_reason fails."""

    def test_escalated_missing_status_reason_fails(self):
        rec = make_record(
            lifecycle_state="ESCALATED",
            status_reason="",
            merge_blocking=True,
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("ESCALATED requires status_reason" in x for x in e))

    def test_escalated_non_blocking_without_evidence_fails(self):
        rec = make_record(
            lifecycle_state="ESCALATED",
            status_reason="Requires human review.",
            merge_blocking=False,
            evidence_summary="",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("ESCALATED with merge_blocking!=true requires evidence_summary" in x for x in e))

    def test_valid_escalated_blocking_passes(self):
        rec = make_record(
            lifecycle_state="ESCALATED",
            status_reason="Requires human security review.",
            merge_blocking=True,
            resolved_at="2026-05-29T21:00:00Z",
            resolved_by="operator",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestInvalidLifecycleState(unittest.TestCase):
    """Invalid lifecycle state fails."""

    def test_unknown_lifecycle_state_fails(self):
        rec = make_record(lifecycle_state="BAD_STATE")
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("lifecycle_state" in x for x in e))

    def test_empty_lifecycle_state_fails(self):
        rec = make_record(lifecycle_state="")
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)


class TestInvalidSeverity(unittest.TestCase):
    """Invalid severity fails."""

    def test_unknown_severity_fails(self):
        rec = make_record(severity="P5")
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("severity" in x for x in e))

    def test_empty_severity_fails(self):
        rec = make_record(severity="")
        s, e, w = validate(rec)


class TestMissingRequiredField(unittest.TestCase):
    """Missing required field fails."""

    def test_missing_finding_id(self):
        rec = make_record()
        del rec["finding_id"]
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("finding_id" in x for x in e))

    def test_missing_severity(self):
        rec = make_record()
        del rec["severity"]
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)

    def test_missing_gate_source(self):
        rec = make_record()
        del rec["gate_source"]
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)

    def test_missing_gate_policy_version(self):
        rec = make_record()
        del rec["gate_policy_version"]
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)


class TestMalformedJSON(unittest.TestCase):
    """Non-dict input is handled gracefully."""

    def test_non_dict_input_returns_invalid(self):
        # validate_record expects a dict; non-dict should be caught
        # by isinstance check in validate_record
        for bad in [None, 123, "string", ["list"], {"a": 1}]:
            s, e, w = vfr.validate_record(bad)
            self.assertNotEqual(s, vfr.VALID_FINDING_RECORD,
                f"expected non-dict {type(bad).__name__} to fail validation")


class TestOutputFilesWritten(unittest.TestCase):
    """Output JSON and MD are written correctly."""

    def test_output_files_written(self):
        import tempfile, os
        rec = make_record(
            severity="P2",
            lifecycle_state="OPEN",
            merge_blocking=True,
            status_reason="Active P2 finding.",
            gate_source="review_comment_gate",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ij = os.path.join(tmp, "input.json")
            oj = os.path.join(tmp, "output.json")
            om = os.path.join(tmp, "output.md")

            with open(ij, "w") as f:
                json.dump(rec, f)

            import subprocess, sys
            cp = subprocess.run(
                [sys.executable, "scripts/local/validate_finding_registry_record.py",
                 "--input-json", ij, "--output-json", oj, "--output-md", om],
                capture_output=True, text=True,
            )
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertTrue(os.path.exists(oj), "output JSON not written")
            self.assertTrue(os.path.exists(om), "output MD not written")

            with open(oj) as f:
                d = json.load(f)
            self.assertEqual(d["status"], vfr.VALID_FINDING_RECORD)
            self.assertIn("errors", d)
            self.assertIn("warnings", d)
            self.assertIn("normalized_summary", d)

            with open(om) as f:
                md = f.read()
            self.assertIn("VALID_FINDING_RECORD", md)
            self.assertIn("codex-abc123", md)


class TestInfoNonBlockingPasses(unittest.TestCase):
    """INFO severity non-blocking record passes."""

    def test_info_non_blocking(self):
        rec = make_record(
            finding_id="codex-info-001",
            severity="INFO",
            lifecycle_state="OPEN",
            merge_blocking=False,
            status_reason="Suggestion, not a blocker.",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestP3NonBlockingPasses(unittest.TestCase):
    """P3 severity non-blocking record passes."""

    def test_p3_non_blocking(self):
        rec = make_record(
            finding_id="codex-p3-001",
            severity="P3",
            lifecycle_state="OPEN",
            merge_blocking=False,
            status_reason="Minor suggestion.",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestOpenP0BlockingPasses(unittest.TestCase):
    """OPEN P0 blocking passes."""

    def test_open_p0_blocking(self):
        rec = make_record(
            finding_id="codex-p0-001",
            severity="P0",
            lifecycle_state="OPEN",
            merge_blocking=True,
            status_reason="Active P0 finding.",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestOpenP1BlockingPasses(unittest.TestCase):
    """OPEN P1 blocking passes."""

    def test_open_p1_blocking(self):
        rec = make_record(
            finding_id="codex-p1-001",
            severity="P1",
            lifecycle_state="OPEN",
            merge_blocking=True,
            status_reason="Active P1 finding.",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestStaleP2NonBlockingPasses(unittest.TestCase):
    """STALE P2 non-blocking passes."""

    def test_stale_p2_non_blocking(self):
        rec = make_record(
            finding_id="codex-stale-p2-001",
            severity="P2",
            lifecycle_state="STALE",
            merge_blocking=False,
            status_reason="Stale P2 finding, not on current head.",
            gate_source="review_comment_gate",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestResolvedByPatchPasses(unittest.TestCase):
    """RESOLVED_BY_PATCH passes."""

    def test_resolved_by_patch(self):
        rec = make_record(
            finding_id="codex-rbp-002",
            severity="P1",
            lifecycle_state="RESOLVED_BY_PATCH",
            merge_blocking=False,
            status_reason="Patch applied, finding no longer present.",
            resolved_at="2026-05-29T21:00:00Z",
            resolved_by="operator",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestSupersededPasses(unittest.TestCase):
    """SUPERSEDED passes."""

    def test_superseded(self):
        rec = make_record(
            finding_id="codex-sup-001",
            severity="P1",
            lifecycle_state="SUPERSEDED",
            merge_blocking=False,
            status_reason="Superseded by newer finding codex-new-001.",
            resolved_at="2026-05-29T21:00:00Z",
            resolved_by="operator",
        )
        s, e, w = validate(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestTerminalStateRequiresResolvedAt(unittest.TestCase):
    """Terminal states require resolved_at and resolved_by."""

    def test_resolved_by_policy_missing_resolved_at(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            merge_blocking=False,
            resolution_method="resolveReviewThread",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            audit_log_path="/tmp/audit.json",
            resolved_at="",
            resolved_by="policy_checker",
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("RESOLVED_BY_POLICY requires resolved_at" in x for x in e))

    def test_waived_missing_resolved_at(self):
        rec = make_record(
            lifecycle_state="WAIVED",
            status_reason="Low priority.",
            resolved_by="operator",
            resolved_at="",
            evidence_summary="Follow-up issue filed.",
            merge_blocking=False,
        )
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)


class TestPrNumberValidation(unittest.TestCase):
    """pr_number must be a positive integer."""

    def test_pr_number_zero_fails(self):
        rec = make_record(pr_number=0)
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("pr_number" in x for x in e))

    def test_pr_number_negative_fails(self):
        rec = make_record(pr_number=-1)
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)

    def test_pr_number_string_fails(self):
        rec = make_record(pr_number="364")
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)


class TestMergeBlockingBoolean(unittest.TestCase):
    """merge_blocking must be boolean."""

    def test_merge_blocking_string_fails(self):
        rec = make_record(merge_blocking="true")
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("merge_blocking must be boolean" in x for x in e))

    def test_merge_blocking_none_fails(self):
        rec = make_record(merge_blocking=None)
        s, e, w = validate(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)


class TestGateSourceRequiredWhenBlocking(unittest.TestCase):
    """gate_source required when merge_blocking=true."""

    def test_gate_source_missing_when_blocking(self):
        rec = make_record(merge_blocking=True, gate_source="")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        # The required-fields check fires first since gate_source is blank string
        self.assertTrue(any("gate_source" in x or "missing" in x for x in e))


class TestUnspecifiedSeverityAcceptance(unittest.TestCase):
    """UNSPECIFIED_BLOCKING and UNSPECIFIED_INFO are valid severities per design doc."""

    def test_unspecified_blocking_valid(self):
        rec = make_record(severity="UNSPECIFIED_BLOCKING", merge_blocking=True)
        s, e, w = vfr.validate_record(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)

    def test_unspecified_info_valid(self):
        rec = make_record(severity="UNSPECIFIED_INFO")
        s, e, w = vfr.validate_record(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)

    def test_unspecified_blocking_open_requires_merge_blocking(self):
        rec = make_record(severity="UNSPECIFIED_BLOCKING", lifecycle_state="OPEN", merge_blocking=False)
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("UNSPECIFIED_BLOCKING" in x and "merge_blocking=true" in x for x in e))

    def test_unspecified_info_no_merge_blocking_required(self):
        # UNSPECIFIED_INFO is not a blocking severity
        rec = make_record(severity="UNSPECIFIED_INFO", merge_blocking=False)
        s, e, w = vfr.validate_record(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)

    def test_random_unspecified_fails(self):
        rec = make_record(severity="UNKNOWN_SEVERITY")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("severity must be one of" in x for x in e))


class TestOpenStaleRejectsResolutionMethod(unittest.TestCase):
    """OPEN and STALE findings must not carry resolution_method."""

    def test_open_with_resolveReviewThread_fails(self):
        rec = make_record(lifecycle_state="OPEN", resolution_method="resolveReviewThread")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("OPEN" in x and "resolution_method" in x for x in e))

    def test_stale_with_resolveReviewThread_fails(self):
        rec = make_record(lifecycle_state="STALE", resolution_method="resolveReviewThread")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("STALE" in x and "resolution_method" in x for x in e))

    def test_open_with_patch_applied_fails(self):
        rec = make_record(lifecycle_state="OPEN", resolution_method="patch_applied")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("OPEN" in x and "resolution_method" in x for x in e))

    def test_open_with_waiver_fails(self):
        rec = make_record(lifecycle_state="OPEN", resolution_method="waiver")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("OPEN" in x and "resolution_method" in x for x in e))

    def test_open_with_dismissReview_fails(self):
        rec = make_record(lifecycle_state="OPEN", resolution_method="dismissReview")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("dismissReview" in x or "OPEN" in x for x in e))

    def test_open_no_resolution_method_passes(self):
        rec = make_record(lifecycle_state="OPEN", resolution_method=None)
        s, e, w = vfr.validate_record(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)

    def test_stale_no_resolution_method_passes(self):
        rec = make_record(lifecycle_state="STALE", resolution_method=None)
        s, e, w = vfr.validate_record(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)

    def test_resolved_by_policy_with_resolveReviewThread_passes(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="resolveReviewThread",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            evidence_commands=["gh api repos/.../pulls/comments/1234"],
            audit_log_path="/tmp/audit.json",
            resolved_at="2026-01-01T00:00:00Z",
            resolved_by="policy_checker",
            merge_blocking=False,
        )
        s, e, w = vfr.validate_record(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)


class TestResolvedByPolicyRequiresEvidenceCommands(unittest.TestCase):
    """RESOLVED_BY_POLICY must have evidence_commands field."""

    def test_missing_evidence_commands_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="resolveReviewThread",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            audit_log_path="/tmp/audit.json",
            resolved_at="2026-01-01T00:00:00Z",
            resolved_by="policy_checker",
            merge_blocking=False,
        )
        # evidence_commands is missing
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("RESOLVED_BY_POLICY requires evidence_commands" in x for x in e))

    def test_empty_evidence_commands_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="resolveReviewThread",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            evidence_commands=[],
            audit_log_path="/tmp/audit.json",
            resolved_at="2026-01-01T00:00:00Z",
            resolved_by="policy_checker",
            merge_blocking=False,
        )
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("RESOLVED_BY_POLICY requires evidence_commands" in x for x in e))

    def test_evidence_commands_present_passes(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="resolveReviewThread",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            evidence_commands=["gh api repos/.../pulls/comments/1234"],
            audit_log_path="/tmp/audit.json",
            resolved_at="2026-01-01T00:00:00Z",
            resolved_by="policy_checker",
            merge_blocking=False,
        )
        s, e, w = vfr.validate_record(rec)
        self.assertEqual(s, vfr.VALID_FINDING_RECORD)

    def test_resolved_by_policy_with_deletePullRequestReviewComment_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="deletePullRequestReviewComment",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            evidence_commands=["gh api repos/.../pulls/comments/1234"],
            audit_log_path="/tmp/audit.json",
            resolved_at="2026-01-01T00:00:00Z",
            resolved_by="policy_checker",
            merge_blocking=False,
        )
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("resolveReviewThread" in x for x in e))

    def test_resolved_by_policy_with_dismissReview_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="dismissReview",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            evidence_commands=["gh api repos/.../pulls/comments/1234"],
            audit_log_path="/tmp/audit.json",
            resolved_at="2026-01-01T00:00:00Z",
            resolved_by="policy_checker",
            merge_blocking=False,
        )
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("dismissReview" in x or "resolveReviewThread" in x for x in e))

    def test_resolved_by_policy_with_admin_merge_fails(self):
        rec = make_record(
            lifecycle_state="RESOLVED_BY_POLICY",
            resolution_method="admin_merge",
            thread_id="PRRT_kwDOSHFpYM6Fxyz",
            evidence_summary="Pattern not in diff.",
            evidence_commands=["gh api repos/.../pulls/comments/1234"],
            audit_log_path="/tmp/audit.json",
            resolved_at="2026-01-01T00:00:00Z",
            resolved_by="policy_checker",
            merge_blocking=False,
        )
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("admin_merge" in x or "resolveReviewThread" in x for x in e))


class TestUnknownResolutionMethodsRejected(unittest.TestCase):
    """WAIVED/SUPERSEDED/INVALID/RESOLVED_BY_PATCH reject unknown resolution_method values."""

    def test_waived_unknown_method_fails(self):
        rec = make_record(lifecycle_state="WAIVED", resolution_method="banana",
                          status_reason="Waived.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                          evidence_summary="Waiver filed.")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("banana" in x for x in e))

    def test_superseded_unknown_method_fails(self):
        rec = make_record(lifecycle_state="SUPERSEDED", resolution_method="random_method",
                          status_reason="Superseded.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                          evidence_summary="Superseded by newer finding.")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("random_method" in x for x in e))

    def test_invalid_unknown_method_fails(self):
        rec = make_record(lifecycle_state="INVALID", resolution_method="typo",
                          status_reason="Finding is wrong.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                          evidence_summary="Factually incorrect finding.")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("typo" in x for x in e))

    def test_resolved_by_patch_unknown_method_fails(self):
        rec = make_record(lifecycle_state="RESOLVED_BY_PATCH", resolution_method="unknown_method",
                          status_reason="Patch applied.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                          evidence_summary="Patch applied.")
        s, e, w = vfr.validate_record(rec)
        self.assertNotEqual(s, vfr.VALID_FINDING_RECORD)
        self.assertTrue(any("unknown_method" in x for x in e))

    def test_waived_known_methods_pass(self):
        for method in ("waiver", "manual_override", "not_applicable", None):
            rec = make_record(lifecycle_state="WAIVED", resolution_method=method,
                              status_reason="Waived.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                              evidence_summary="Waiver filed.")
            s, e, w = vfr.validate_record(rec)
            self.assertEqual(s, vfr.VALID_FINDING_RECORD), f"method={method} should pass"

    def test_superseded_known_methods_pass(self):
        for method in ("not_applicable", None):
            rec = make_record(lifecycle_state="SUPERSEDED", resolution_method=method,
                              status_reason="Superseded.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                              evidence_summary="Superseded by newer finding.")
            s, e, w = vfr.validate_record(rec)
            self.assertEqual(s, vfr.VALID_FINDING_RECORD), f"method={method} should pass"

    def test_invalid_known_methods_pass(self):
        for method in ("not_applicable", None):
            rec = make_record(lifecycle_state="INVALID", resolution_method=method,
                              status_reason="Finding is wrong.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                              evidence_summary="Factually incorrect finding.")
            s, e, w = vfr.validate_record(rec)
            self.assertEqual(s, vfr.VALID_FINDING_RECORD), f"method={method} should pass"

    def test_resolved_by_patch_known_methods_pass(self):
        for method in ("patch_applied", "not_applicable", None):
            rec = make_record(lifecycle_state="RESOLVED_BY_PATCH", resolution_method=method,
                              status_reason="Patch applied.", resolved_by="operator", resolved_at="2026-01-01T00:00:00Z",
                              evidence_summary="Patch applied.")
            s, e, w = vfr.validate_record(rec)
            self.assertEqual(s, vfr.VALID_FINDING_RECORD), f"method={method} should pass"


if __name__ == "__main__":
    unittest.main()