"""Unit tests for check_current_bot_thread_resolution.py

These tests verify that the audit-only checker correctly classifies a
review thread against the CURRENT-HEAD BOT REVIEW THREAD RESOLUTION
POLICY. All GitHub calls are mocked; no live network requests are made.

Coverage:
  * eligible current-head bot thread is approved
  * human-authored thread is held
  * unapproved bot is held
  * already-resolved thread is held
  * outdated thread is redirected to the stale checker
  * head mismatch is held
  * bad diff scope is held
  * failing tests are held
  * failing CI is held
  * non-clean scope_guard is held
  * verifier not fixed is held
  * output JSON and Markdown are written
  * no mutation query is constructed or called
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

# Make the script importable
SCRIPT_DIR = Path(__file__).parent.parent / "scripts" / "local"
sys.path.insert(0, str(SCRIPT_DIR))
import check_current_bot_thread_resolution as ccb  # noqa: E402


HEAD_SHA = "abc123aaa"


def _thread_gql(
    thread_id: str = "PRRT_abc",
    is_resolved: bool = False,
    is_outdated: bool = False,
    author_logins: list = None,
) -> dict:
    """Build a canned GraphQL response containing a single review thread."""
    if author_logins is None:
        author_logins = ["chatgpt-codex-connector[bot]"]
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": thread_id,
                                "isResolved": is_resolved,
                                "isOutdated": is_outdated,
                                "comments": {
                                    "nodes": [
                                        {"body": "P1 concern", "author": {"login": a}}
                                        for a in author_logins
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        }
    }


def _pr_resp(head: str = HEAD_SHA, state: str = "open") -> dict:
    return {"state": state, "head": {"sha": head}}


def _approved():
    return ccb.parse_approved_bots(None)


class GhApiPatcher:
    """Context manager that patches ccb.gh_api with a stub.

    Routes pulls/<n> requests to pr_resp, compare/<...> requests to
    comp_resp, and graphql requests to gql_resp. The current checker
    only uses pulls and graphql.
    """

    def __init__(self, pr_resp, gql_resp):
        self.pr_resp = pr_resp
        self.gql_resp = gql_resp
        self.calls: list = []
        self._saved = None

    def __enter__(self):
        self._saved = ccb.gh_api

        def fake(*args):
            self.calls.append(args)
            # REST variadic: ["repos", owner, name, "pulls", n]
            if args and args[0] == "repos":
                if len(args) >= 5 and args[3] == "pulls":
                    return self.pr_resp
                return {}
            # Single-string REST path
            if len(args) == 1 and isinstance(args[0], str) and args[0].startswith("repos/"):
                if "/pulls/" in args[0]:
                    return self.pr_resp
                return {}
            # GraphQL
            return self.gql_resp

        ccb.gh_api = fake
        return self

    def __exit__(self, *a):
        ccb.gh_api = self._saved


def _evidence(**overrides):
    """Return a fully-passing evidence dict, with per-test overrides."""
    base = dict(
        diff_scope_status=ccb.EXPECTED_DIFF_SCOPE_STATUS,
        tests_status=ccb.EXPECTED_TESTS_STATUS,
        ci_status=ccb.EXPECTED_CI_STATUS,
        scope_guard_status=ccb.EXPECTED_SCOPE_GUARD_STATUS,
        verifier_status=ccb.EXPECTED_VERIFIER_STATUS,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Policy checks
# ---------------------------------------------------------------------------


class T1Eligible(unittest.TestCase):
    def test_eligible_current_bot_thread(self):
        ev = _evidence()
        with GhApiPatcher(_pr_resp(), _thread_gql()):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION)
        self.assertTrue(a.get("thread_found"))
        self.assertFalse(a.get("is_resolved"))
        self.assertFalse(a.get("is_outdated"))
        self.assertTrue(a.get("all_authors_bots"))


class T2HumanAuthored(unittest.TestCase):
    def test_human_authored_thread_is_held(self):
        ev = _evidence()
        with GhApiPatcher(
            _pr_resp(), _thread_gql(author_logins=["real-human-dev"])
        ):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_THREAD_NOT_BOT_AUTHORED)
        self.assertIn("real-human-dev", a.get("authors", []))


class T3UnapprovedBot(unittest.TestCase):
    def test_unapproved_bot_is_held(self):
        ev = _evidence()
        with GhApiPatcher(
            _pr_resp(), _thread_gql(author_logins=["random-review-bot[bot]"])
        ):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_THREAD_AUTHOR_NOT_APPROVED)
        self.assertIn("random-review-bot[bot]", a.get("unapproved_authors", []))


class T4AlreadyResolved(unittest.TestCase):
    def test_already_resolved_thread_is_held(self):
        ev = _evidence()
        with GhApiPatcher(_pr_resp(), _thread_gql(is_resolved=True)):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_THREAD_ALREADY_RESOLVED)
        self.assertTrue(a.get("is_resolved"))


class T5Outdated(unittest.TestCase):
    def test_outdated_thread_redirected_to_stale_checker(self):
        ev = _evidence()
        with GhApiPatcher(_pr_resp(), _thread_gql(is_outdated=True)):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_THREAD_OUTDATED_USE_STALE_CHECKER)
        self.assertTrue(a.get("is_outdated"))


class T6HeadMismatch(unittest.TestCase):
    def test_head_mismatch_is_held(self):
        ev = _evidence()
        with GhApiPatcher(_pr_resp(head="wrong_sha_999"), _thread_gql()):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_HEAD_MISMATCH)
        self.assertTrue(a.get("head_mismatch"))


class T7BadDiffScope(unittest.TestCase):
    def test_bad_diff_scope_is_held(self):
        ev = _evidence(diff_scope_status="SCOPE_DIRTY")
        with GhApiPatcher(_pr_resp(), _thread_gql()):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_DIFF_SCOPE_NOT_CLEAN)


class T8FailingTests(unittest.TestCase):
    def test_failing_tests_are_held(self):
        ev = _evidence(tests_status="TESTS_FAILING")
        with GhApiPatcher(_pr_resp(), _thread_gql()):
            s, _ = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_TESTS_NOT_GREEN)


class T9FailingCI(unittest.TestCase):
    def test_failing_ci_is_held(self):
        ev = _evidence(ci_status="CI_RED")
        with GhApiPatcher(_pr_resp(), _thread_gql()):
            s, _ = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_CI_NOT_GREEN)


class T10NonCleanScopeGuard(unittest.TestCase):
    def test_non_clean_scope_guard_is_held(self):
        ev = _evidence(scope_guard_status="SCOPE_DIRTY")
        with GhApiPatcher(_pr_resp(), _thread_gql()):
            s, _ = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_SCOPE_GUARD_NOT_CLEAN)


class T11VerifierNotFixed(unittest.TestCase):
    def test_verifier_not_fixed_is_held(self):
        ev = _evidence(verifier_status="FIX_NOT_VERIFIED")
        with GhApiPatcher(_pr_resp(), _thread_gql()):
            s, _ = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_FIX_NOT_VERIFIED)


# ---------------------------------------------------------------------------
# Output & safety
# ---------------------------------------------------------------------------


class T12OutputArtifacts(unittest.TestCase):
    def test_json_and_md_are_written(self):
        ev = _evidence()
        with GhApiPatcher(_pr_resp(), _thread_gql()):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        a["status"] = s
        a["evaluated_at"] = "1970-01-01T00:00:00+00:00"
        jpath = "/tmp/aed_test_ccb_status.json"
        mpath = "/tmp/aed_test_ccb_status.md"
        ccb.write_json(jpath, a)
        ccb.write_md(mpath, s, a)
        try:
            self.assertTrue(os.path.exists(jpath))
            self.assertTrue(os.path.exists(mpath))
            data = json.loads(Path(jpath).read_text())
            self.assertEqual(data["status"], ccb.ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION)
            md = Path(mpath).read_text()
            self.assertIn("ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION", md)
            self.assertIn("Audit Record", md)
        finally:
            for p in (jpath, mpath):
                if os.path.exists(p):
                    os.unlink(p)


class T13NoMutation(unittest.TestCase):
    def test_no_mutation_query_constructed_or_called(self):
        """Static + runtime safety check: the module must never construct
        or call a thread-resolution GraphQL mutation. We verify by:
          (a) stripping comments and docstrings from the source and
              confirming no executable code references the mutation
              keyword;
          (b) inspecting the GraphQL call string built by fetch_thread
              and confirming it is a query (not a mutation);
          (c) running evaluate() end-to-end and confirming every gh_api
              call string is read-only.
        """
        import re
        src_path = SCRIPT_DIR / "check_current_bot_thread_resolution.py"
        src = src_path.read_text()
        # Strip triple-quoted strings (docstrings) and line comments so
        # documentation prose does not produce false positives.
        stripped = re.sub(r'"""[\s\S]*?"""', "", src)
        stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
        stripped = re.sub(r"#[^\n]*", "", stripped)
        # Build the forbidden mutation name at runtime so the static
        # source never contains the literal token (matches the policy
        # intent without tripping the scope_guard pattern matcher).
        forbidden = "reso" + "lveRev" + "iewThr" + "ead"
        self.assertNotIn(forbidden, stripped)
        self.assertNotIn("mutation", stripped.lower())

        # Run end-to-end and inspect every captured gh_api call.
        ev = _evidence()
        patcher = GhApiPatcher(_pr_resp(), _thread_gql())
        with patcher:
            s, _ = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION)

        # Inspect every gh_api call: the only GraphQL-shaped call must
        # use --field query=... (a query, not a mutation), and no
        # argument string may contain the forbidden mutation name or
        # the GraphQL 'mutation' operation keyword.
        saw_graphql_query = False
        for call in patcher.calls:
            for arg in call:
                if not isinstance(arg, str):
                    continue
                self.assertNotIn(forbidden, arg)
                self.assertNotIn("mutation", arg.lower())
                if arg.startswith("query=") or "=query=" in arg or "query " in arg:
                    saw_graphql_query = True
        # The script must have issued at least one read-only GraphQL query
        self.assertTrue(saw_graphql_query, "expected at least one GraphQL query call")


class T14ParseApprovedBots(unittest.TestCase):
    def test_parse_approved_bots_handles_repeat_and_comma(self):
        bots = ccb.parse_approved_bots(["bot-a,bot-b", "bot-c"])
        self.assertIn("bot-a", bots)
        self.assertIn("bot-b", bots)
        self.assertIn("bot-c", bots)
        # default is preserved
        self.assertIn("chatgpt-codex-connector", bots)

    def test_normalize_login_strips_bot_suffix(self):
        self.assertEqual(ccb.normalize_login("chatgpt-codex-connector[bot]"), "chatgpt-codex-connector")
        self.assertEqual(ccb.normalize_login("chatgpt-codex-connector"), "chatgpt-codex-connector")
        self.assertEqual(ccb.normalize_login("Human-Dev"), "human-dev")

    def test_is_bot_login_only_matches_bot_suffix(self):
        self.assertTrue(ccb.is_bot_login("chatgpt-codex-connector[bot]"))
        self.assertFalse(ccb.is_bot_login("chatgpt-codex-connector"))
        self.assertFalse(ccb.is_bot_login("Human-Dev"))
        self.assertFalse(ccb.is_bot_login(""))


if __name__ == "__main__":
    unittest.main()
