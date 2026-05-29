"""Unit tests for check_stale_review_thread_resolution.py"""
import json, os, subprocess, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts' / 'local'))
import check_stale_review_thread_resolution as csr

def make_cp(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)

class GhApiPatcher:
    def __init__(self, pr_resp, comp_resp, gql_resp):
        self.pr_resp = pr_resp; self.comp_resp = comp_resp; self.gql_resp = gql_resp; self._saved = None
    def __enter__(self):
        self._saved = csr.gh_api
        def fake(*args):
            if args[0] == "repos": return self.pr_resp if args[2] == "pulls" else self.comp_resp
            return self.gql_resp
        csr.gh_api = fake; return self
    def __exit__(self, *a): csr.gh_api = self._saved

class T1(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        comp = {"commits": [{"files": [{"filename": "d/p.md", "patch": "origin/base...HEAD"}]}]}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": False, "comments": {"nodes": [{"body": "the flagged pattern is git diff HEAD -- {files}", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, comp, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "git diff HEAD -- {files}")
        self.assertEqual(s, csr.ELIGIBLE_STALE_THREAD_RESOLUTION)
        self.assertTrue(a.get("thread_found")); self.assertTrue(a.get("is_outdated")); self.assertFalse(a.get("is_resolved")); self.assertIn("diff_length", a)

class T2(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "wrongsha999"}}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
        with GhApiPatcher(pr, {}, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flag")
        self.assertEqual(s, csr.HOLD_HEAD_MISMATCH)

class T3(unittest.TestCase):
    def test_it(self):
        pr = {"state": "closed", "head": {"sha": "abc123aaa"}}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
        with GhApiPatcher(pr, {}, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flag")
        self.assertEqual(s, csr.HOLD_PR_NOT_OPEN)

class T4(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
        with GhApiPatcher(pr, {}, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_xyz", "abc123aaa", "main", "flag")
        self.assertEqual(s, csr.HOLD_THREAD_NOT_FOUND)

class T5(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": False, "isResolved": False, "comments": {"nodes": [{"body": "flagged pattern", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, {}, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flagged pattern")
        self.assertEqual(s, csr.HOLD_THREAD_NOT_OUTDATED)

class T6(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": True, "comments": {"nodes": [{"body": "flagged pattern", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, {}, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flagged pattern")
        self.assertEqual(s, csr.HOLD_THREAD_ALREADY_RESOLVED)

class T7(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": False, "comments": {"nodes": [{"body": "unrelated text", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, {}, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "not present")
        self.assertEqual(s, csr.HOLD_FLAGGED_PATTERN_NOT_FOUND_IN_THREAD)

class T8(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        comp = {"commits": [{"files": [{"filename": "d/p.md", "patch": "git diff HEAD"}]}]}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": False, "comments": {"nodes": [{"body": "git diff HEAD", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, comp, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "git diff HEAD")
        self.assertEqual(s, csr.HOLD_FLAGGED_PATTERN_STILL_IN_DIFF)

class T9(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        comp = {"commits": [{"files": [{"filename": "d/p.md", "patch": "old content"}]}]}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": False, "comments": {"nodes": [{"body": "flagged pattern", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, comp, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flagged pattern", replacement_pattern="not found")
        self.assertEqual(s, csr.HOLD_REPLACEMENT_PATTERN_MISSING)

class T10(unittest.TestCase):
    def test_it(self):
        calls = []; saved = csr.subprocess.run
        csr.subprocess.run = lambda cmd, *a, **kw: calls.append(" ".join(str(x) for x in cmd)) or make_cp("{}")
        try: csr.fetch_compare_diff("OWNER/REPO", "main", "abc123aaa")
        except: pass
        finally: csr.subprocess.run = saved
        bad = [c for c in calls if "git diff HEAD" in c]
        self.assertEqual(bad, [], f"Found: {bad}")

class T11(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        comp = {"commits": [{"files": [{"filename": "d/p.md", "patch": "--- a/d/p.md\n+++ b/d/p.md\n@@ -1 +1 @@\nx"}, {"filename": "e/w.py", "patch": "--- a/e/w.py\n+++ b/e/w.py\n@@ -1 +1 @@\ny"}]}]}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": False, "comments": {"nodes": [{"body": "flagged pattern", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, comp, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flagged pattern", path_scope="d/p.md")
        self.assertEqual(s, csr.HOLD_UNEXPECTED_CHANGED_FILES)
        self.assertIn("e/w.py", a.get("unexpected_files", []))

class T12(unittest.TestCase):
    def test_it(self):
        import tempfile
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        comp = {"commits": [{"files": [{"filename": "d/p.md", "patch": "origin/base...HEAD"}]}]}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": False, "comments": {"nodes": [{"body": "flagged pattern", "author": {"login": "bot"}}]}}]}}}}}
        with GhApiPatcher(pr, comp, gql):
            s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flagged pattern", replacement_pattern="origin/base...HEAD")
        self.assertEqual(s, csr.ELIGIBLE_STALE_THREAD_RESOLUTION)
        with tempfile.TemporaryDirectory() as tmp:
            jp = os.path.join(tmp, "s.json"); mp = os.path.join(tmp, "s.md")
            a["status"] = s; csr.write_json(jp, a); csr.write_md(mp, s, a)
            self.assertTrue(os.path.exists(jp)); self.assertTrue(os.path.exists(mp))
            with open(jp) as f: d = json.load(f)
            self.assertEqual(d["status"], csr.ELIGIBLE_STALE_THREAD_RESOLUTION)
            with open(mp) as f: c = f.read()
            self.assertIn("ELIGIBLE", c); self.assertIn("Dry-run", c)

class T13(unittest.TestCase):
    def test_it(self):
        pr = {"state": "open", "head": {"sha": "abc123aaa"}}
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"id": "PRRT_abc", "isOutdated": True, "isResolved": False, "comments": {"nodes": [{"body": "flagged pattern", "author": {"login": "bot"}}]}}]}}}}}
        # Patch fetch_compare_diff directly to raise an exception
        saved = csr.fetch_compare_diff
        def bad(*a): raise RuntimeError("compare failed")
        csr.fetch_compare_diff = bad
        try:
            with GhApiPatcher(pr, {}, gql):
                s, a = csr.evaluate("OWNER/REPO", 1, "PRRT_abc", "abc123aaa", "main", "flagged pattern")
            self.assertEqual(s, csr.HOLD_DIFF_FETCH_FAILED)
        finally:
            csr.fetch_compare_diff = saved

if __name__ == "__main__": unittest.main()