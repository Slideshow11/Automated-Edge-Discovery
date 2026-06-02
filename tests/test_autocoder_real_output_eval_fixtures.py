"""Tests for the real-output autocoder eval seed result packets.

Validates that the seed packets in
``corpus/autocoder-real-output-results-v0/`` are well-formed, reference
valid corpus task_ids, and that the real-output evaluator can run them
end-to-end and produce a ``REAL_OUTPUT_EVAL_READY`` status with the
expected metric counts.

This test is read-only and does not mutate GitHub or the repo working
tree.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
CORPUS_PATH = REPO_ROOT / "corpus" / "autocoder-real-output-v0.json"
RESULTS_DIR = REPO_ROOT / "corpus" / "autocoder-real-output-results-v0"
EVAL_SCRIPT = REPO_ROOT / "scripts" / "local" / "run_autocoder_real_output_eval.py"


REQUIRED_RESULT_FIELDS = (
    "task_id",
    "source_pr",
    "source_commit",
    "title",
    "status",
    "changed_files",
    "allowed_files",
    "tests_passed",
    "ci_green",
    "scope_clean",
    "review_ready",
    "merge_ready",
    "human_cleanup_required",
    "hold_reason",
    "error_reason",
    "notes",
)


# The actual full source diff for each seed PR (verified against the
# GitHub PRs API in the patch session that produced this v0). These are
# the literal `changed_files` the source PRs produced. The evaluator
# passes them straight to `scope_violation()`, so the scope-clean
# metric is honest regardless of whether the diff fits the corpus task.
EXPECTED_FULL_DIFFS: dict[int, list[str]] = {
    379: [
        "scripts/local/audit_main_ci_for_head.py",
        "tests/test_audit_main_ci_for_head.py",
    ],
    380: [
        "corpus/autocoder-real-output-v0.json",
        "scripts/local/run_autocoder_real_output_eval.py",
        "tests/test_run_autocoder_real_output_eval.py",
    ],
    381: [
        ".github/workflows/post-merge-main-ci-audit.yml",
        "tests/test_post_merge_main_ci_audit_workflow.py",
    ],
}


# The expected scope_clean_count after running the evaluator on these
# patches. This is computed honestly: each PR's actual diff includes at
# least one file that does not match the corpus task's allowed_files
# pattern, so the script's scope_violation() function returns a
# non-empty list for every record, and scope_clean_count == 0.
EXPECTED_SCOPE_CLEAN_COUNT = 0


class TestAutocoderRealOutputEvalFixtures(unittest.TestCase):
    """Validates seed result packets + an end-to-end evaluator run."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS_PATH.read_text())
        cls.corpus_task_ids = {t["task_id"] for t in cls.corpus.get("tasks", [])}

        cls.result_packets = {}
        for p in sorted(RESULTS_DIR.glob("*.json")):
            cls.result_packets[p.name] = json.loads(p.read_text())

    # ---- Corpus / packet presence ------------------------------------

    def test_corpus_path_exists(self):
        self.assertTrue(
            CORPUS_PATH.exists(),
            f"corpus file not found: {CORPUS_PATH}",
        )

    def test_eval_script_exists(self):
        self.assertTrue(
            EVAL_SCRIPT.exists(),
            f"eval script not found: {EVAL_SCRIPT}",
        )

    def test_three_result_packets_present(self):
        # The task spec calls for exactly 3 seed packets.
        self.assertEqual(
            len(self.result_packets),
            3,
            f"expected exactly 3 seed result packets, found "
            f"{len(self.result_packets)}: {sorted(self.result_packets.keys())}",
        )

    # ---- Per-packet field validation ---------------------------------

    def test_each_packet_has_required_fields(self):
        for name, pkt in self.result_packets.items():
            for field in REQUIRED_RESULT_FIELDS:
                self.assertIn(
                    field,
                    pkt,
                    f"packet {name!r} is missing required field {field!r}",
                )

    def test_each_packet_task_id_is_in_corpus(self):
        for name, pkt in self.result_packets.items():
            self.assertIn(
                pkt["task_id"],
                self.corpus_task_ids,
                f"packet {name!r} task_id {pkt['task_id']!r} is not in the corpus",
            )

    def test_each_packet_task_ids_are_distinct(self):
        seen = set()
        for name, pkt in self.result_packets.items():
            tid = pkt["task_id"]
            self.assertNotIn(
                tid, seen, f"duplicate task_id {tid!r} in packet {name!r}"
            )
            seen.add(tid)

    def test_each_packet_changed_files_nonempty(self):
        for name, pkt in self.result_packets.items():
            self.assertIsInstance(
                pkt["changed_files"], list, f"{name}: changed_files must be a list"
            )
            self.assertGreater(
                len(pkt["changed_files"]),
                0,
                f"{name}: changed_files must be non-empty",
            )

    def test_changed_files_equals_full_actual_source_diff(self):
        """`changed_files` must equal the full actual source PR diff,
        not a curated subset chosen for corpus fit.
        """
        for name, pkt in self.result_packets.items():
            source_pr = pkt["source_pr"]
            self.assertIn(
                source_pr,
                EXPECTED_FULL_DIFFS,
                f"no expected full diff registered for source_pr={source_pr}",
            )
            expected = sorted(EXPECTED_FULL_DIFFS[source_pr])
            actual = sorted(pkt["changed_files"])
            self.assertEqual(
                actual,
                expected,
                f"{name}: changed_files must equal the full actual source PR "
                f"diff for PR #{source_pr}. "
                f"expected={expected}, actual={actual}",
            )

    def test_changed_files_includes_out_of_scope_files(self):
        """Each seed's `changed_files` must include the file that the
        source PR actually changed but is out-of-scope for the mapped
        corpus task. This is the regression guard against the earlier
        P2 finding (omitting out-of-scope files to make scope_clean
        look better).
        """
        for name, pkt in self.result_packets.items():
            changed = set(pkt["changed_files"])
            # The known out-of-scope file for each source PR (relative
            # to the mapped corpus task). If this assertion ever fails,
            # the packet is silently reverting to the pre-patch state.
            out_of_scope = {
                379: "tests/test_audit_main_ci_for_head.py",
                380: "corpus/autocoder-real-output-v0.json",
                381: ".github/workflows/post-merge-main-ci-audit.yml",
            }[pkt["source_pr"]]
            self.assertIn(
                out_of_scope,
                changed,
                f"{name}: changed_files must include {out_of_scope!r} "
                f"(the actual out-of-scope file from PR #{pkt['source_pr']}). "
                f"Pre-patch packets hid this file to make scope_clean look better.",
            )

    def test_scoped_files_separate_from_changed_files_when_present(self):
        """`scoped_files` (if present) is descriptive only and must be
        a subset of `changed_files`. It must NOT be the same as
        `changed_files` (that would defeat its descriptive purpose).
        """
        for name, pkt in self.result_packets.items():
            if "scoped_files" not in pkt:
                # Optional field; skip if the packet doesn't carry it.
                continue
            scoped = set(pkt["scoped_files"])
            changed = set(pkt["changed_files"])
            self.assertTrue(
                scoped.issubset(changed),
                f"{name}: scoped_files must be a subset of changed_files "
                f"(scoped={scoped}, changed={changed})",
            )
            self.assertLess(
                len(scoped),
                len(changed),
                f"{name}: scoped_files should be a narrower view than "
                f"changed_files; if they are equal the field is not adding "
                f"information (scoped={scoped}, changed={changed})",
            )

    def test_each_packet_allowed_files_nonempty(self):
        for name, pkt in self.result_packets.items():
            self.assertIsInstance(
                pkt["allowed_files"], list, f"{name}: allowed_files must be a list"
            )
            self.assertGreater(
                len(pkt["allowed_files"]),
                0,
                f"{name}: allowed_files must be non-empty",
            )

    def test_each_packet_source_pr_is_int(self):
        for name, pkt in self.result_packets.items():
            self.assertIsInstance(
                pkt["source_pr"], int, f"{name}: source_pr must be an int"
            )
            self.assertGreaterEqual(
                pkt["source_pr"], 1, f"{name}: source_pr must be a positive PR number"
            )

    def test_each_packet_status_uppercase(self):
        for name, pkt in self.result_packets.items():
            self.assertEqual(
                pkt["status"],
                pkt["status"].upper(),
                f"{name}: status should be uppercase (e.g. PASS, HOLD, FAIL)",
            )

    def test_each_packet_passes_tests(self):
        for name, pkt in self.result_packets.items():
            self.assertGreater(
                pkt["tests_passed"],
                0,
                f"{name}: tests_passed must be > 0 for a merged patch",
            )

    def test_each_packet_boolean_fields_are_bools(self):
        bool_fields = [
            "ci_green",
            "scope_clean",
            "review_ready",
            "merge_ready",
            "human_cleanup_required",
        ]
        for name, pkt in self.result_packets.items():
            for f in bool_fields:
                self.assertIsInstance(
                    pkt[f],
                    bool,
                    f"{name}: {f} must be a bool (got {type(pkt[f]).__name__}: {pkt[f]!r})",
                )

    # ---- End-to-end evaluator run -------------------------------------

    def test_eval_with_three_packets_is_ready(self):
        """Run the evaluator with all 3 seed packets and assert the
        overall status is ``REAL_OUTPUT_EVAL_READY`` with the expected
        metric counts.
        """
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "out.json")
            md_path = os.path.join(tmp, "out.md")
            cmd = [
                sys.executable,
                str(EVAL_SCRIPT),
                "--corpus",
                str(CORPUS_PATH),
                "--output-json",
                json_path,
                "--output-md",
                md_path,
            ]
            for pkt_path in sorted(RESULTS_DIR.glob("*.json")):
                cmd.extend(["--result-json", str(pkt_path)])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            self.assertEqual(
                result.returncode,
                0,
                f"evaluator exited non-zero: stdout={result.stdout!r} stderr={result.stderr!r}",
            )

            # Both artifacts must be written.
            self.assertTrue(
                os.path.exists(json_path),
                f"JSON artifact not written: {json_path}",
            )
            self.assertTrue(
                os.path.exists(md_path),
                f"Markdown artifact not written: {md_path}",
            )
            self.assertGreater(
                os.path.getsize(json_path),
                0,
                f"JSON artifact is empty: {json_path}",
            )
            self.assertGreater(
                os.path.getsize(md_path),
                0,
                f"Markdown artifact is empty: {md_path}",
            )

            packet = json.loads(Path(json_path).read_text())

            self.assertEqual(packet.get("status"), "REAL_OUTPUT_EVAL_READY")
            self.assertEqual(packet.get("result_count"), 3)
            self.assertEqual(packet.get("matched_result_count"), 3)

            metrics = packet.get("metrics", {})
            self.assertEqual(metrics.get("tasks_with_results"), 3)
            self.assertEqual(metrics.get("ci_green_count"), 3)
            self.assertEqual(metrics.get("merge_ready_count"), 3)
            self.assertGreaterEqual(
                metrics.get("human_cleanup_required_count", 0),
                1,
                "at least one seed packet should have human_cleanup_required=true",
            )

            # scope_clean_count must reflect the HONEST calculation
            # from full changed_files, not a hard-coded 3. Each PR's
            # actual diff includes a file outside the mapped corpus
            # task's allowed_files, so scope_clean_count must be 0.
            self.assertEqual(
                metrics.get("scope_clean_count"),
                EXPECTED_SCOPE_CLEAN_COUNT,
                f"scope_clean_count must reflect the honest full-diff "
                f"calculation, not a curated subset. "
                f"expected={EXPECTED_SCOPE_CLEAN_COUNT}, "
                f"actual={metrics.get('scope_clean_count')}",
            )

            # Per-record scope_clean must also be false for every seed.
            for rec in packet.get("records", []):
                if rec.get("has_result") and rec.get("result_status") == "PASS":
                    self.assertFalse(
                        rec.get("scope_clean", True),
                        f"record {rec.get('task_id')!r} should be scope_clean=false "
                        f"because its changed_files includes an out-of-scope file",
                    )

            # invalid_result_packets must be empty (all 3 are well-formed).
            self.assertEqual(packet.get("invalid_result_packets"), [])

            # Only 3 of the 5 corpus tasks are intentionally exercised by
            # these seeds (real-output-v0-task-001 docs and -003 test_only
            # have no matching merged PRs in the seed set). The remaining
            # 2 tasks should be reported as missing.
            missing = packet.get("missing_result_task_ids", [])
            self.assertEqual(
                len(missing),
                2,
                f"expected 2 missing tasks (5 corpus tasks - 3 seeds), got "
                f"{len(missing)}: {missing}",
            )
            for mid in missing:
                self.assertIn(
                    mid,
                    self.corpus_task_ids,
                    f"missing task {mid!r} is not in the corpus",
                )


if __name__ == "__main__":
    unittest.main()
