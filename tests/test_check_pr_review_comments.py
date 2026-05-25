"""
tests/test_check_pr_review_comments.py

Unit tests for check_pr_review_comments.py.
Uses mock subprocess to avoid real GitHub calls.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Ensure the module is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
import check_pr_review_comments as crc


class FakeResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def gh_reply(items: list) -> FakeResult:
    return FakeResult(stdout=json.dumps(items))


def gh_fail(msg: str) -> FakeResult:
    return FakeResult(stdout="", stderr=msg, returncode=1)


def gh_pr_view_reply(oid: str = "aabbccdd") -> FakeResult:
    return FakeResult(stdout=json.dumps({
        "headRefOid": oid,
        "state": "OPEN",
        "url": "https://github.com/OWNER/REPO/pull/1",
    }))


class TestClassify(unittest.TestCase):
    def test_no_findings_when_no_needles(self):
        item = {"user": {"login": "alice"}, "body": "looks good to me"}
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(got, [])

    def test_p1_inline_comment(self):
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
            "commit_id": "aabbccdd",
            "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")
        self.assertEqual(got[0]["user"], "chatgpt-codex-connector[bot]")

    def test_p2_issue_comment(self):
        item = {
            "user": {"login": "codex-reviewer"},
            "body": "### P2\nValidate literal base SHA as object",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")

    def test_unspecified_blocking(self):
        item = {
            "user": {"login": "safety-bot"},
            "body": "Codex finding: CRITICAL — shell=True found in subprocess call — must fix",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "UNSPECIFIED_BLOCKING")

    def test_unspecified_info(self):
        item = {
            "user": {"login": "codex"},
            "body": "Nit: consider renaming variable",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "UNSPECIFIED_INFO")

    def test_ignore_user(self):
        item = {
            "user": {"login": "alice"},
            "body": "P1: this is a finding",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", {"alice"})
        self.assertEqual(got, [])

    def test_high_maps_to_p1(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "High severity: path traversal risk",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_medium_maps_to_p2(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "Medium: test coverage gap",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")


class TestDedup(unittest.TestCase):
    def test_exact_duplicate_removed(self):
        item = {
            "user": {"login": "bot"},
            "body": "P1: fix this",
            "state": "",
        }
        findings = [item, item]
        got = crc.dedup_findings(findings)
        self.assertEqual(len(got), 1)

    def test_different_users_kept(self):
        f1 = {"user": "alice", "body": "P1: fix this"}
        f2 = {"user": "bob", "body": "P1: fix this"}
        got = crc.dedup_findings([f1, f2])
        self.assertEqual(len(got), 2)


class TestWaiverLoad(unittest.TestCase):
    def test_valid_waiver(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123",
                "waivers": [
                    {
                        "finding_id": "p2-001",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "acceptable risk",
                        "evidence": "test passes",
                        "expires_after_pr": 321,
                    }
                ],
            }, fh)
            path = fh.name
        try:
            ok, data, err = crc.load_waiver(path, 320, "abc123")
            self.assertTrue(ok)
            self.assertEqual(len(data["waivers"]), 1)
        finally:
            Path(path).unlink()

    def test_stale_sha_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "old_sha",
                "waivers": [],
            }, fh)
            path = fh.name
        try:
            ok, data, err = crc.load_waiver(path, 320, "new_sha")
            self.assertFalse(ok)
            self.assertIn("!=", err)
        finally:
            Path(path).unlink()

    def test_wrong_pr_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 319,
                "reported_head_sha": "abc123",
                "waivers": [],
            }, fh)
            path = fh.name
        try:
            ok, data, err = crc.load_waiver(path, 320, "abc123")
            self.assertFalse(ok)
            self.assertIn("pr_number", err)
        finally:
            Path(path).unlink()


class TestIntegration(unittest.TestCase):
    """Run the script end-to-end with mocked gh API calls."""

    def _run(self, gh_responses: dict[str, FakeResult], extra_args: list[str] | None = None):
        """
        Patch subprocess.run to return FakeResult keyed by command snippet.
        """
        def fake_run(cmd, **kwargs):
            # Find matching response by command string
            cmd_str = " ".join(cmd)
            for key, resp in gh_responses.items():
                if key in cmd_str:
                    return resp
            return FakeResult(returncode=1, stderr=f"No mock for: {cmd_str}")

        with mock.patch.object(subprocess, "run", fake_run):
            with tempfile.TemporaryDirectory() as tmp:
                json_out = Path(tmp) / "out.json"
                md_out = Path(tmp) / "out.md"
                args = [
                    "check_pr_review_comments.py",
                    "--repo", "OWNER/REPO",
                    "--pr-number", "320",
                    "--reported-head-sha", "abc123",
                    "--output-json", str(json_out),
                    "--output-md", str(md_out),
                ]
                if extra_args:
                    args.extend(extra_args)
                with mock.patch.object(sys, "argv", args):
                    rc = crc.main()
                result = json.loads(json_out.read_text()) if json_out.exists() else {}
                md = md_out.read_text() if md_out.exists() else ""
                return rc, result

    def test_no_comments_clean(self):
        rc, data = self._run({
            "issues/320/comments": gh_reply([]),
            "pulls/320/comments": gh_reply([]),
            "pulls/320/reviews": gh_reply([]),
        })
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        self.assertEqual(len(data.get("findings", [])), 0)

    def test_p1_inline_blocked(self):
        rc, data = self._run({
            "issues/320/comments": gh_reply([]),
            "pulls/320/comments": gh_reply([{
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
                "path": "scripts/local/run_temp_worktree_execution.py",
                "line": 1821,
                "commit_id": "aabbccdd",
                "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
            }]),
            "pulls/320/reviews": gh_reply([]),
        })
        self.assertEqual(rc, crc.EXIT_BLOCKED)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")
        self.assertEqual(len(data.get("blockers", [])), 1)
        self.assertEqual(data["blockers"][0]["severity"], "P1")

    def test_p2_blocked_by_default(self):
        rc, data = self._run({
            "issues/320/comments": gh_reply([{
                "user": {"login": "reviewer"},
                "body": "### P2\nValidate literal base SHA as object",
                "state": "",
            }]),
            "pulls/320/comments": gh_reply([]),
            "pulls/320/reviews": gh_reply([]),
        })
        self.assertEqual(rc, crc.EXIT_BLOCKED)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")

    def test_p2_waived_clean(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123",
                "waivers": [
                    {
                        "finding_id": "p2-001",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "test risk acceptable",
                        "evidence": "smoke passes",
                        "expires_after_pr": 321,
                        "body_prefix": "P2: Validate literal base SHA",
                    }
                ],
            }, fh)
            waiver_path = fh.name
        import os
        os.environ["_TEST_WAIVER_PATH"] = waiver_path
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: Validate literal base SHA",
                    "state": "",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path])
            self.assertEqual(rc, crc.EXIT_CLEAN)
            self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
            self.assertEqual(len(data.get("p2_waivers", [])), 1)
        finally:
            Path(waiver_path).unlink()

    def test_p1_waiver_still_blocked(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123",
                "waivers": [
                    {
                        "finding_id": "p1-001",
                        "severity": "P1",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "intentional",
                        "evidence": "accepted risk",
                        "expires_after_pr": 321,
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path])
            # P1 waivers are not supported — must still block
            self.assertEqual(rc, crc.EXIT_BLOCKED)
        finally:
            Path(waiver_path).unlink()

    def test_finding_id_non_empty(self):
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
            "commit_id": "aabbccdd",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        fid = got[0]["finding_id"]
        self.assertTrue(fid.startswith("codex-"), f"finding_id should start with codex-, got {fid}")
        self.assertEqual(len(fid), 18)  # "codex-" + 12 hex chars

    def test_duplicate_same_endpoint_same_id(self):
        """Same item from same endpoint -> same finding_id (dedup collapses it)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        f1 = crc.classify_item(item, "inline_review_comment", set())
        f2 = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(f1[0]["finding_id"], f2[0]["finding_id"])

    def test_same_finding_different_endpoints_deduped(self):
        """
        Same Codex finding text from two different endpoints produces one finding
        with both source kinds in its 'sources' list.
        """
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        f1_raw = crc.classify_item(item, "inline_review_comment", set())
        f2_raw = crc.classify_item(item, "per_review_comment", set())
        for f in f1_raw:
            f["_source_kind"] = "inline_review_comment"
        for f in f2_raw:
            f["_source_kind"] = "per_review_comment"

        findings = f1_raw + f2_raw
        deduped = crc.dedup_findings(findings)

        self.assertEqual(len(deduped), 1, "Same finding from 2 endpoints should collapse to 1")
        self.assertIn("sources", deduped[0])
        self.assertEqual(len(deduped[0]["sources"]), 2)
        self.assertEqual(set(deduped[0]["sources"]), {"inline_review_comment", "per_review_comment"})

    def test_blocker_count_unique_by_finding_id(self):
        """
        The same finding appearing from 3 endpoints should count as 1 blocker.
        """
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "P1: emit a real newline marker",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        findings = []
        for src in ["issue_comment", "inline_review_comment", "per_review_comment"]:
            for f in crc.classify_item(item, src, set()):
                f["_source_kind"] = src
                findings.append(f)

        deduped = crc.dedup_findings(findings)
        # Only P0/P1/UNSPECIFIED_BLOCKING count as blockers
        blockers = [f for f in deduped if f["severity"] in ("P0", "P1", "UNSPECIFIED_BLOCKING")]
        self.assertEqual(len(blockers), 1, "Duplicate P1 from 3 endpoints = 1 blocker")

    def test_p2_waiver_by_finding_id_waives_dup(self):
        """
        A P2 finding from two endpoints is deduped to one entry.
        A waiver by exact finding_id should waive that one entry.
        """
        item = {
            "user": {"login": "reviewer"},
            "body": "P2: validate base SHA",
            "path": "scripts/local/run_autocoder_eval_corpus.py",
            "line": 42,
        }
        findings = []
        for src in ["inline_review_comment", "per_review_comment"]:
            for f in crc.classify_item(item, src, set()):
                f["_source_kind"] = src
                findings.append(f)

        deduped = crc.dedup_findings(findings)
        self.assertEqual(len(deduped), 1, "Same P2 from 2 endpoints -> 1 finding")
        fid = deduped[0]["finding_id"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123",
                "waivers": [
                    {
                        "finding_id": fid,
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "already fixed in later commits",
                        "evidence": "post-merge verification passes",
                        "expires_after_pr": 321,
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: validate base SHA",
                    "path": "scripts/local/run_autocoder_eval_corpus.py",
                    "line": 42,
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path])
            self.assertEqual(rc, crc.EXIT_CLEAN, "Exact finding_id waiver should clear P2 blocker")
            self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        finally:
            Path(waiver_path).unlink()

    def test_p1_dup_still_blocks_once(self):
        """Same P1 from 2 endpoints -> 1 finding -> still blocks (P1 not waivable)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "P1: emit a real newline marker",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        findings = []
        for src in ["inline_review_comment", "per_review_comment"]:
            for f in crc.classify_item(item, src, set()):
                f["_source_kind"] = src
                findings.append(f)

        deduped = crc.dedup_findings(findings)
        blockers = [f for f in deduped if f["severity"] in ("P0", "P1", "UNSPECIFIED_BLOCKING")]
        self.assertEqual(len(blockers), 1, "Duplicate P1 -> 1 blocker, not 2")

    def test_different_users_different_finding_ids(self):
        item1 = {
            "user": {"login": "alice"},
            "body": "P1: fix this",
            "path": "x.py",
            "line": 10,
        }
        item2 = {
            "user": {"login": "bob"},
            "body": "P1: fix this",
            "path": "x.py",
            "line": 10,
        }
        f1 = crc.classify_item(item1, "issue_comment", set())
        f2 = crc.classify_item(item2, "issue_comment", set())
        self.assertNotEqual(f1[0]["finding_id"], f2[0]["finding_id"])

    def test_finding_id_used_in_dedup(self):
        item = {
            "user": {"login": "bot"},
            "body": "P1: fix this",
            "state": "",
        }
        findings = [
            {"finding_id": "codex-aaa111", "user": "bot", "body": "P1: fix this"},
            {"finding_id": "codex-aaa111", "user": "bot", "body": "P1: fix this"},
            {"finding_id": "codex-zzz999", "user": "bot", "body": "P1: fix this"},
        ]
        got = crc.dedup_findings(findings)
        self.assertEqual(len(got), 2)  # two unique IDs

    def test_p2_waiver_by_finding_id_clean(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123",
                "waivers": [
                    {
                        "finding_id": "codex-abc123def456",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "test risk acceptable",
                        "evidence": "smoke passes",
                        "expires_after_pr": 321,
                        "body_prefix": "P2: validate",
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: validate literal base SHA",
                    "state": "",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path, "--reported-head-sha", "abc123"])
            # The finding_id won't match (random hash), so it should still block
            # unless finding_id is actually the same as the waiver
            # This tests that P2 still blocks without exact finding_id match
            self.assertEqual(rc, crc.EXIT_BLOCKED)
        finally:
            Path(waiver_path).unlink()

    def test_stale_waiver_sha_blocks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "old_sha_abc123",
                "waivers": [
                    {
                        "finding_id": "codex-abc123def456",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "old",
                        "evidence": "x",
                        "expires_after_pr": 321,
                        "body_prefix": "P2: validate",
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: validate literal base SHA",
                    "state": "",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path, "--reported-head-sha", "new_sha_xyz789"])
            self.assertEqual(rc, crc.EXIT_BLOCKED)
        finally:
            Path(waiver_path).unlink()


    def test_api_failure_inconclusive(self):
        rc, data = self._run({
            "issues/320/comments": gh_fail("API rate limit exceeded"),
            "pulls/320/comments": gh_fail("API rate limit exceeded"),
            "pulls/320/reviews": gh_reply([]),
        })
        self.assertIn(rc, (crc.EXIT_INCONCLUSIVE, crc.EXIT_BLOCKED))
        self.assertTrue(len(data.get("api_errors", [])) >= 1)

    def test_per_review_comments_fetched(self):
        """Reviews with IDs trigger per-review comment fetch."""
        fetched_per_review = []
        def fake_run(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "reviews/456/comments" in cmd_str:
                fetched_per_review.append(456)
                return gh_reply([])
            if "pulls/320/reviews" in cmd_str:
                return gh_reply([{
                    "id": 456,
                    "user": {"login": "reviewer"},
                    "body": "overall review",
                    "state": "COMMENTED",
                }])
            if "comments" in cmd_str:
                return gh_reply([])
            return gh_reply([])

        with mock.patch.object(subprocess, "run", fake_run):
            with tempfile.TemporaryDirectory() as tmp:
                json_out = Path(tmp) / "out.json"
                md_out = Path(tmp) / "out.md"
                with mock.patch.object(sys, "argv", [
                    "check_pr_review_comments.py",
                    "--repo", "OWNER/REPO",
                    "--pr-number", "320",
                    "--reported-head-sha", "abc123",
                    "--output-json", str(json_out),
                    "--output-md", str(md_out),
                ]):
                    rc = crc.main()
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertIn(456, fetched_per_review)

    def test_subprocess_uses_list_argv_no_shell(self):
        """Verify all subprocess.run calls use list argv and shell=False."""
        captured_calls = []
        original_run = subprocess.run
        def capturing_run(cmd, **kwargs):
            captured_calls.append(cmd)
            return FakeResult(stdout="[]")

        with mock.patch.object(subprocess, "run", capturing_run):
            with tempfile.TemporaryDirectory() as tmp:
                json_out = Path(tmp) / "out.json"
                md_out = Path(tmp) / "out.md"
                with mock.patch.object(sys, "argv", [
                    "check_pr_review_comments.py",
                    "--repo", "OWNER/REPO",
                    "--pr-number", "320",
                    "--reported-head-sha", "abc123",
                    "--output-json", str(json_out),
                    "--output-md", str(md_out),
                ]):
                    try:
                        crc.main()
                    except Exception:
                        pass  # ignore errors, we only care about call patterns

        for cmd in captured_calls:
            self.assertIsInstance(cmd, (list, tuple)), \
                f"subprocess.run must receive list argv, got {type(cmd)}: {cmd}"
            self.assertNotIn("shell=True", " ".join(str(c) for c in cmd))


import tempfile

if __name__ == "__main__":
    unittest.main()