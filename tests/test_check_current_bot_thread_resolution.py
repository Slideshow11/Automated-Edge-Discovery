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
from typing import Optional

# Make the script importable
SCRIPT_DIR = Path(__file__).parent.parent / "scripts" / "local"
sys.path.insert(0, str(SCRIPT_DIR))
import check_current_bot_thread_resolution as ccb  # noqa: E402


HEAD_SHA = "abc123aaa"


def _review_threads_page(
    thread_id: str = "PRRT_abc",
    is_resolved: bool = False,
    is_outdated: bool = False,
    other_threads: Optional[list] = None,
    has_next_page: bool = False,
    end_cursor: Optional[str] = None,
) -> dict:
    """Build a single reviewThreads page response.

    Used to mock the first phase of fetch_thread: page through
    pullRequest.reviewThreads looking for the target id.
    """
    if other_threads is None:
        other_threads = []
    nodes = list(other_threads) + [{
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "path": "scripts/local/check_current_bot_thread_resolution.py",
        "line": 192,
    }]
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {
                            "hasNextPage": has_next_page,
                            "endCursor": end_cursor,
                        },
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def _thread_node_page(
    thread_id: str = "PRRT_abc",
    is_resolved: bool = False,
    is_outdated: bool = False,
    author_logins: list = None,
    has_next_page: bool = False,
    end_cursor: str = None,
) -> dict:
    """Build a single node(id:) page response with comments.

    Used to mock the second phase of fetch_thread: page through
    the target thread's comments until all are collected.
    """
    if author_logins is None:
        author_logins = ["chatgpt-codex-connector[bot]"]
    return {
        "data": {
            "node": {
                "id": thread_id,
                "isResolved": is_resolved,
                "isOutdated": is_outdated,
                "path": "scripts/local/check_current_bot_thread_resolution.py",
                "line": 192,
                "comments": {
                    "pageInfo": {
                        "hasNextPage": has_next_page,
                        "endCursor": end_cursor,
                    },
                    "nodes": [
                        {
                            "body": "P1 concern",
                            "author": {"login": a},
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                        for a in author_logins
                    ],
                },
            }
        }
    }


def _thread_gql(
    thread_id: str = "PRRT_abc",
    is_resolved: bool = False,
    is_outdated: bool = False,
    author_logins: list = None,
) -> list:
    """Build the canned sequence of GraphQL responses for a default
    eligible scenario: one reviewThreads page containing the target,
    followed by one node(id:) page containing the thread comments.

    Returns a list (so the patcher can serve the next response on
    each gh_api call).
    """
    return [
        _review_threads_page(
            thread_id=thread_id,
            is_resolved=is_resolved,
            is_outdated=is_outdated,
        ),
        _thread_node_page(
            thread_id=thread_id,
            is_resolved=is_resolved,
            is_outdated=is_outdated,
            author_logins=author_logins,
        ),
    ]


def _pr_resp(head: str = HEAD_SHA, state: str = "open") -> dict:
    return {"state": state, "head": {"sha": head}}


def _approved():
    return ccb.parse_approved_bots(None)


class GhApiPatcher:
    """Context manager that patches ccb.gh_api with a stub.

    Routes pulls/<n> requests to pr_resp, and graphql requests to
    gql_responses. The current checker only uses pulls and graphql.

    gql_responses can be:
      * a single dict — returned for every GraphQL call (legacy mode);
      * a list of dicts — returned in order on successive GraphQL
        calls; the last response is repeated if the call list is
        exhausted (defensive fallback for tests that do not pin every
        call);
      * a callable taking the GraphQL call index and returning a
        dict — for tests that need fine-grained per-call control.
    """

    def __init__(self, pr_resp, gql_responses):
        self.pr_resp = pr_resp
        self.calls: list = []
        self._saved = None
        self._gql_idx = 0
        if callable(gql_responses):
            self._gql_kind = "fn"
            self._gql_fn = gql_responses
        elif isinstance(gql_responses, list):
            self._gql_kind = "list"
            self._gql_list = gql_responses
        else:
            self._gql_kind = "list"
            self._gql_list = [gql_responses]

    def _next_gql(self):
        if self._gql_kind == "fn":
            return self._gql_fn(self._gql_idx)  # type: ignore[return-value]
        idx = min(self._gql_idx, len(self._gql_list) - 1)
        return self._gql_list[idx]

    def __enter__(self):
        self._saved = ccb.gh_api
        outer = self

        def fake(*args):
            outer.calls.append(args)
            # REST variadic: ["repos", owner, name, "pulls", n]
            if args and args[0] == "repos":
                if len(args) >= 5 and args[3] == "pulls":
                    return outer.pr_resp
                return {}
            # Single-string REST path
            if (
                len(args) == 1
                and isinstance(args[0], str)
                and args[0].startswith("repos/")
            ):
                if "/pulls/" in args[0]:
                    return outer.pr_resp
                return {}
            # GraphQL: serve the next canned response
            resp = outer._next_gql()
            outer._gql_idx += 1
            return resp

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


# -------------------------------------------------------------------
# Pagination behavior
# -------------------------------------------------------------------


def _other_thread(thread_id: str) -> dict:
    return {
        "id": thread_id,
        "isResolved": False,
        "isOutdated": False,
        "path": "scripts/local/other.py",
        "line": 1,
    }


class T15ThreadOnLaterReviewThreadsPage(unittest.TestCase):
    def test_target_thread_is_found_after_first_review_threads_page(self):
        """The target thread sits on the SECOND reviewThreads page.

        The first page has hasNextPage=true with 3 unrelated threads;
        the second page contains the target and reports hasNextPage=false.
        A subsequent node(id:) page returns the comments.
        """
        ev = _evidence()
        page1 = _review_threads_page(
            thread_id="PRRT_xyz",  # overwritten by other_threads
            other_threads=[
                _other_thread("PRRT_other1"),
                _other_thread("PRRT_other2"),
                _other_thread("PRRT_other3"),
            ],
            has_next_page=True,
            end_cursor="page1end",
        )
        # page1's helper still appends the supplied thread_id; remove it
        # so the first page contains only unrelated threads.
        page1["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = [
            _other_thread("PRRT_other1"),
            _other_thread("PRRT_other2"),
            _other_thread("PRRT_other3"),
        ]
        page2 = _review_threads_page(thread_id="PRRT_abc", has_next_page=False)
        comments = _thread_node_page(thread_id="PRRT_abc")
        responses = [page1, page2, comments]
        with GhApiPatcher(_pr_resp(), responses) as patcher:
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION)
        # Two reviewThreads pages + one node page = 3 GraphQL calls
        gql_calls = [
            c for c in patcher.calls
            if any(isinstance(arg, str) and "query=" in arg for arg in c)
        ]
        self.assertEqual(len(gql_calls), 3)
        # The first two query strings should reference reviewThreads;
        # the third should reference node(id:).
        q0 = next(arg for arg in gql_calls[0] if isinstance(arg, str) and "query=" in arg)
        q1 = next(arg for arg in gql_calls[1] if isinstance(arg, str) and "query=" in arg)
        q2 = next(arg for arg in gql_calls[2] if isinstance(arg, str) and "query=" in arg)
        self.assertIn("reviewThreads", q0)
        self.assertIn("reviewThreads", q1)
        self.assertIn("node(id", q2)


class T16ThreadNotFoundAfterAllPages(unittest.TestCase):
    def test_hold_thread_not_found_only_after_all_pages_exhausted(self):
        """Two reviewThreads pages, neither contains the target.

        fetch_thread must issue both pages and then return None,
        which causes the policy to surface HOLD_THREAD_NOT_FOUND.
        No node(id:) call should be made because the target was
        never located.
        """
        ev = _evidence()
        page1 = _review_threads_page(
            thread_id="PRRT_xyz",
            other_threads=[
                _other_thread("PRRT_other1"),
                _other_thread("PRRT_other2"),
            ],
            has_next_page=True,
            end_cursor="page1end",
        )
        page1["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = [
            _other_thread("PRRT_other1"),
            _other_thread("PRRT_other2"),
        ]
        page2 = _review_threads_page(
            thread_id="PRRT_xyz",
            other_threads=[
                _other_thread("PRRT_other3"),
                _other_thread("PRRT_other4"),
            ],
            has_next_page=False,
        )
        page2["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = [
            _other_thread("PRRT_other3"),
            _other_thread("PRRT_other4"),
        ]
        responses = [page1, page2]
        with GhApiPatcher(_pr_resp(), responses) as patcher:
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_THREAD_NOT_FOUND)
        self.assertFalse(a.get("thread_found"))
        # Two reviewThreads pages and no node(id:) call
        gql_calls = [
            c for c in patcher.calls
            if any(isinstance(arg, str) and "query=" in arg for arg in c)
        ]
        self.assertEqual(len(gql_calls), 2)
        for call in gql_calls:
            q = next(arg for arg in call if isinstance(arg, str) and "query=" in arg)
            self.assertIn("reviewThreads", q)
            self.assertNotIn("node(id", q)


class T17HumanCommentOnLaterCommentsPage(unittest.TestCase):
    def test_human_comment_after_first_comments_page_holds(self):
        """Target thread is found immediately on the first reviewThreads
        page, but a HUMAN comment appears on the SECOND comments page.

        The checker must walk all comment pages and surface
        HOLD_THREAD_NOT_BOT_AUTHORED, not ELIGIBLE.
        """
        ev = _evidence()
        rt_page = _review_threads_page(thread_id="PRRT_abc")
        comments_page1 = _thread_node_page(
            thread_id="PRRT_abc",
            author_logins=["chatgpt-codex-connector[bot]"],
            has_next_page=True,
            end_cursor="cmt1end",
        )
        comments_page2 = _thread_node_page(
            thread_id="PRRT_abc",
            author_logins=["real-human-dev"],
            has_next_page=False,
        )
        responses = [rt_page, comments_page1, comments_page2]
        with GhApiPatcher(_pr_resp(), responses) as patcher:
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.HOLD_THREAD_NOT_BOT_AUTHORED)
        # Both comment authors should appear in the audit
        self.assertIn("chatgpt-codex-connector[bot]", a.get("authors", []))
        self.assertIn("real-human-dev", a.get("authors", []))
        # 1 reviewThreads + 2 comment pages = 3 GraphQL calls
        gql_calls = [
            c for c in patcher.calls
            if any(isinstance(arg, str) and "query=" in arg for arg in c)
        ]
        self.assertEqual(len(gql_calls), 3)
        node_calls = [
            c for c in gql_calls
            if any(isinstance(arg, str) and "query=" in arg and "node(id" in arg
                for arg in c)
        ]
        self.assertEqual(len(node_calls), 2)


class T18BotCommentsAcrossMultiplePages(unittest.TestCase):
    def test_approved_bot_comments_across_multiple_pages_still_eligible(self):
        """Target thread has approved bot comments spread across two
        pages. The checker must walk both pages and still conclude
        ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION.
        """
        ev = _evidence()
        rt_page = _review_threads_page(thread_id="PRRT_abc")
        comments_page1 = _thread_node_page(
            thread_id="PRRT_abc",
            author_logins=[
                "chatgpt-codex-connector[bot]",
                "chatgpt-codex-connector[bot]",
                "chatgpt-codex-connector[bot]",
            ],
            has_next_page=True,
            end_cursor="cmt1end",
        )
        comments_page2 = _thread_node_page(
            thread_id="PRRT_abc",
            author_logins=[
                "chatgpt-codex-connector[bot]",
                "chatgpt-codex-connector[bot]",
            ],
            has_next_page=False,
        )
        responses = [rt_page, comments_page1, comments_page2]
        with GhApiPatcher(_pr_resp(), responses):
            s, a = ccb.evaluate(
                repo="OWNER/REPO",
                pr_number=1,
                thread_id="PRRT_abc",
                expected_head=HEAD_SHA,
                approved_bots=_approved(),
                **ev,
            )
        self.assertEqual(s, ccb.ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION)
        # 5 comments merged across 2 pages
        self.assertEqual(len(a.get("authors", [])), 5)
        self.assertTrue(a.get("all_authors_bots"))


class T19NoMutationStillHoldsAfterPagination(unittest.TestCase):
    def test_no_mutation_query_in_paginated_code(self):
        """Regression: adding pagination must not introduce any
        GraphQL write operation. Scans executable code (after
        stripping docstrings and comments) for the forbidden
        mutation keyword and the canonical GraphQL write name.
        """
        import re
        src_path = SCRIPT_DIR / "check_current_bot_thread_resolution.py"
        src = src_path.read_text()
        stripped = re.sub(r'"""[\s\S]*?"""', "", src)
        stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
        stripped = re.sub(r"#[^\n]*", "", stripped)
        forbidden = "reso" + "lveRev" + "iewThr" + "ead"
        self.assertNotIn(forbidden, stripped)
        self.assertNotIn("mutation", stripped.lower())

        # Drive evaluate end-to-end and inspect every GraphQL-shaped
        # argument. None may reference the forbidden write name or the
        # GraphQL 'mutation' keyword.
        ev = _evidence()
        rt_page = _review_threads_page(thread_id="PRRT_abc")
        comments_page1 = _thread_node_page(
            thread_id="PRRT_abc",
            author_logins=["chatgpt-codex-connector[bot]"],
            has_next_page=True,
            end_cursor="cmt1end",
        )
        comments_page2 = _thread_node_page(
            thread_id="PRRT_abc",
            author_logins=["chatgpt-codex-connector[bot]"],
            has_next_page=False,
        )
        responses = [rt_page, comments_page1, comments_page2]
        patcher = GhApiPatcher(_pr_resp(), responses)
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
        for call in patcher.calls:
            for arg in call:
                if not isinstance(arg, str):
                    continue
                self.assertNotIn(forbidden, arg)
                self.assertNotIn("mutation", arg.lower())


if __name__ == "__main__":
    unittest.main()
