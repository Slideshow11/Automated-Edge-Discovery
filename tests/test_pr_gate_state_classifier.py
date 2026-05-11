import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "local" / "classify_pr_gate_state.py"
spec = importlib.util.spec_from_file_location("classify_pr_gate_state", MODULE_PATH)
classifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classifier)


CURRENT_HEAD = "abc123current"
OLD_HEAD = "old123head"
ALLOWED = ["docs/current_project_status.md", "scripts/local/classify_pr_gate_state.py"]


def _pr(**overrides):
    data = {
        "number": 189,
        "html_url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/189",
        "state": "open",
        "merged": False,
        "draft": False,
        "mergeable": True,
        "base": {"ref": "main"},
        "head": {"ref": "tooling/pr-gate-state-classifier", "sha": CURRENT_HEAD},
        "head_pushed_at": "2026-05-10T20:00:00Z",
    }
    data.update(overrides)
    return data


def _green_checks():
    return [
        {"name": "test", "status": "completed", "conclusion": "success"},
        {"name": "validator", "status": "completed", "conclusion": "success"},
    ]


def _packet(*, pr=None, files=None, checks=None, comments=None, reviews=None):
    return classifier.classify_payloads(
        pr=pr or _pr(),
        changed_files=files or list(ALLOWED),
        check_runs=checks if checks is not None else _green_checks(),
        issue_comments=comments if comments is not None else [],
        reviews=reviews if reviews is not None else [],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
    )


def _request(created_at="2026-05-10T20:01:00Z", body=None):
    return {
        "id": 1,
        "user": {"login": "Slideshow11"},
        "created_at": created_at,
        "body": body or f"@codex review\n\nCurrent head: `{CURRENT_HEAD}`",
    }


def _codex_comment(body, created_at="2026-05-10T20:02:00Z"):
    return {
        "id": 2,
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "created_at": created_at,
        "body": body,
    }


def _codex_clean(created_at="2026-05-10T20:02:00Z"):
    return {
        "id": 100,
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "state": "COMMENTED",
        "submitted_at": created_at,
        "commit_id": CURRENT_HEAD,
        "body": "Codex Review: Didn't find any major issues.",
    }


def test_scope_clean_ci_green_codex_clean_issue_comment_on_current_head_is_ready():
    packet = _packet(
        comments=[
            _request(),
            _codex_comment("Codex Review: Didn't find any major issues. :+1:"),
        ]
    )

    assert packet["classification"] == "ready_for_reviewer"
    assert packet["codex_status"] == "clean"
    assert packet["codex_latest_clean_signal"]["source"] == "issue_comment"
    assert packet["head_matches_expected"] is True


def test_scope_clean_ci_green_codex_no_blocking_issue_comment_on_current_head_is_ready():
    packet = _packet(
        comments=[
            _request(),
            _codex_comment(
                "**Summary**\n* I reviewed the PR head "
                f"`{CURRENT_HEAD}` and did not find blocking issues."
            ),
        ]
    )

    assert packet["classification"] == "ready_for_reviewer"
    assert packet["codex_status"] == "clean"


def test_scope_clean_ci_green_codex_review_object_clean_on_current_head_is_ready():
    packet = _packet(
        reviews=[
            {
                "id": 9,
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "submitted_at": "2026-05-10T20:02:00Z",
                "commit_id": CURRENT_HEAD,
                "state": "COMMENTED",
                "body": "Codex Review: Didn't find any major issues. :+1:",
            }
        ]
    )

    assert packet["classification"] == "ready_for_reviewer"
    assert packet["codex_status"] == "clean"
    assert packet["codex_latest_clean_signal"]["source"] == "pr_review"


def test_codex_clean_on_old_head_but_current_head_newer_requires_request():
    packet = _packet(
        reviews=[
            {
                "id": 8,
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "submitted_at": "2026-05-10T19:00:00Z",
                "commit_id": OLD_HEAD,
                "state": "COMMENTED",
                "body": "Codex Review: Didn't find any major issues. :+1:",
            }
        ]
    )

    assert packet["classification"] == "codex_request_needed"
    assert packet["codex_status"] == "request_needed"


def test_codex_request_exists_on_current_head_without_bot_response_is_pending():
    packet = _packet(comments=[_request()])

    assert packet["classification"] == "codex_pending"
    assert packet["codex_status"] == "pending"


def test_codex_suggestions_on_current_head_are_reported():
    packet = _packet(
        comments=[
            _request(),
            _codex_comment("### 💡 Codex Review\n\nHere are some automated review suggestions for this pull request."),
        ]
    )

    assert packet["classification"] == "codex_suggestions"
    assert packet["codex_status"] == "suggestions"
    assert packet["codex_latest_suggestions"]["source"] == "issue_comment"


def test_later_codex_clean_signal_on_current_head_overrides_earlier_suggestions():
    packet = _packet(
        comments=[
            _request(),
            _codex_comment(
                "### 💡 Codex Review\n\nHere are some automated review suggestions for this pull request.",
                created_at="2026-05-10T20:02:00Z",
            ),
            _codex_comment(
                "**Summary**\nI reviewed the PR head and did not find blocking issues.",
                created_at="2026-05-10T20:03:00Z",
            ),
        ]
    )

    assert packet["classification"] == "ready_for_reviewer"
    assert packet["codex_status"] == "clean"
    assert packet["codex_latest_suggestions"] is None


def test_later_codex_request_invalidates_earlier_suggestions_until_response_arrives():
    packet = _packet(
        comments=[
            _request(),
            _codex_comment(
                "### 💡 Codex Review\n\nHere are some automated review suggestions for this pull request.",
                created_at="2026-05-10T20:02:00Z",
            ),
            _request(created_at="2026-05-10T20:03:00Z"),
        ]
    )

    assert packet["classification"] == "codex_pending"
    assert packet["codex_status"] == "pending"
    assert packet["codex_latest_suggestions"] is None


def test_unexpected_file_changed_blocks_scope():
    packet = _packet(files=[*ALLOWED, "engine/edge_discovery/runners/example.py"])

    assert packet["classification"] == "blocked_scope"
    assert packet["unexpected_files"] == ["engine/edge_discovery/runners/example.py"]


def test_ci_pending_blocks_before_codex():
    packet = _packet(checks=[{"name": "test", "status": "queued", "conclusion": None}])

    assert packet["classification"] == "ci_pending"
    assert packet["ci_status"] == "pending"


def test_pending_ci_with_failed_in_check_name_is_still_pending():
    packet = _packet(checks=[{"name": "failed-login-test", "status": "queued", "conclusion": None}])

    assert packet["classification"] == "ci_pending"
    assert packet["ci_status"] == "pending"


def test_skipped_and_neutral_ci_conclusions_are_green():
    packet = _packet(
        checks=[
            {"name": "conditional-docs", "status": "completed", "conclusion": "skipped"},
            {"name": "advisory", "status": "completed", "conclusion": "neutral"},
        ],
        comments=[_request()],
    )

    assert packet["ci_status"] == "green"
    assert packet["classification"] == "codex_pending"
    assert packet["blockers"] == []


def test_ci_failed_blocks_before_codex():
    packet = _packet(checks=[{"name": "test", "status": "completed", "conclusion": "failure"}])

    assert packet["classification"] == "ci_failed"
    assert packet["ci_status"] == "failed"


def test_wrong_base_branch_blocks():
    packet = _packet(pr=_pr(base={"ref": "develop"}))

    assert packet["classification"] == "blocked_wrong_base"
    assert packet["base_branch"] == "develop"


def test_expected_head_mismatch_blocks_ready_classification():
    packet = classifier.classify_payloads(
        pr=_pr(head={"ref": "tooling/pr-gate-state-classifier", "sha": "newer-head"}),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
    )

    assert packet["classification"] == "unknown"
    assert packet["head_matches_expected"] is False
    assert packet["blockers"] == ["PR head SHA is 'newer-head', expected 'abc123current'."]


def test_dismissed_codex_review_does_not_trigger_clean_signal():
    # A DISMISSED Codex review with a clean body must not be treated as clean.
    # The classifier must not accept DISMISSED state as a valid Codex signal.
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[{
            "id": 99,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "I reviewed and didn't find any major issues.",
            "commit_id": CURRENT_HEAD,
            "submitted_at": "2026-05-10T20:01:00Z",
            "state": "DISMISSED",
        }],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
    )

    assert packet["classification"] == "codex_pending"
    assert packet["codex_status"] == "pending"
    assert packet["blockers"] == []


def test_approved_codex_review_triggers_clean_signal():
    # APPROVED state on a clean-body review should still produce clean.
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[{
            "id": 99,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "I reviewed and didn't find any major issues.",
            "commit_id": CURRENT_HEAD,
            "submitted_at": "2026-05-10T20:01:00Z",
            "state": "APPROVED",
        }],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
    )

    assert packet["classification"] == "ready_for_reviewer"
    assert packet["codex_status"] == "clean"


def test_closed_unmerged_pr_blocks():
    packet = _packet(pr=_pr(state="closed", merged=False))

    assert packet["classification"] == "blocked_pr_closed"
    assert packet["state"] == "closed"
    assert packet["merged"] is False


def test_codex_request_without_head_sha_is_not_current_when_push_time_unknown():
    packet = _packet(
        pr=_pr(head_pushed_at=None),
        comments=[_request(body="@codex review")],
    )

    assert packet["classification"] == "codex_request_needed"


def test_repo_pushed_at_fallback_does_not_make_sha_less_request_current():
    packet = _packet(
        pr=_pr(
            head={
                "ref": "tooling/pr-gate-state-classifier",
                "sha": CURRENT_HEAD,
                "repo": {"pushed_at": "2026-05-10T20:00:00Z"},
            },
            head_pushed_at=None,
        ),
        comments=[_request(body="@codex review")],
    )

    assert packet["classification"] == "codex_request_needed"
    assert packet["codex_status"] == "request_needed"


def test_parse_next_link_extracts_only_next_page_url():
    header = '<https://api.github.com/resource?page=2>; rel="next", <https://api.github.com/resource?page=5>; rel="last"'

    assert classifier.parse_next_link(header) == "https://api.github.com/resource?page=2"


def test_codex_eyes_reaction_on_request_with_no_response_is_pending():
    """Codex bot reacting with +1 (eyes) on latest request with no later response.
    Reaction evidence is captured but classification stays codex_pending."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[
            {"id": 901, "content": "+1", "user": {"login": "chatgpt-codex-connector[bot]"}, "created_at": "2026-05-10T20:01:30Z"},
        ],
    )

    assert packet["classification"] == "codex_pending"
    assert packet["codex_status"] == "pending"
    assert packet["codex_latest_request_acknowledged"] is True
    assert packet["codex_reaction_status"] == "acknowledged_pending"
    assert packet["codex_latest_request_acknowledged_at"] == "2026-05-10T20:01:30Z"


def test_codex_eyes_reaction_followed_by_clean_comment_is_ready():
    """Codex eyes reaction on request, then clean issue comment arrives.
    Clean signal controls final state; reaction evidence is still captured."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[
            _request(),
            _codex_comment("Codex Review: Didn't find any major issues. :+1:", created_at="2026-05-10T20:02:00Z"),
        ],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[
            {"id": 901, "content": "+1", "user": {"login": "chatgpt-codex-connector[bot]"}, "created_at": "2026-05-10T20:01:30Z"},
        ],
    )

    assert packet["classification"] == "ready_for_reviewer"
    assert packet["codex_status"] == "clean"
    assert packet["codex_latest_request_acknowledged"] is True
    assert packet["codex_reaction_status"] == "acknowledged_pending"
    assert packet["codex_latest_clean_signal"]["source"] == "issue_comment"


def test_codex_eyes_reaction_followed_by_suggestions_comment():
    """Codex eyes reaction on request, then suggestions issue comment arrives.
    Final state is codex_suggestions; reaction evidence is captured."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[
            _request(),
            _codex_comment("### 💡 Codex Review\n\nHere are some automated review suggestions.", created_at="2026-05-10T20:02:00Z"),
        ],
        reviews=[],

        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[
            {"id": 901, "content": "+1", "user": {"login": "chatgpt-codex-connector[bot]"}, "created_at": "2026-05-10T20:01:30Z"},
        ],
    )

    assert packet["classification"] == "codex_suggestions"
    assert packet["codex_status"] == "suggestions"
    assert packet["codex_latest_request_acknowledged"] is True
    assert packet["codex_reaction_status"] == "acknowledged_pending"


def test_no_reaction_means_reaction_fields_none():
    """When there are no reactions on the latest request, all reaction fields are None."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[],
    )

    assert packet["classification"] == "codex_pending"
    assert packet["codex_latest_request_acknowledged"] is None
    assert packet["codex_reaction_status"] is None
    assert packet["codex_latest_request_acknowledged_at"] is None


def test_non_codex_user_reaction_does_not_set_acknowledged():
    """A reaction from a non-Codex user should not set acknowledged status."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[
            {"id": 902, "content": "+1", "user": {"login": "Slideshow11"}, "created_at": "2026-05-10T20:01:30Z"},
        ],
    )

    assert packet["classification"] == "codex_pending"
    assert packet["codex_latest_request_acknowledged"] is None
    assert packet["codex_reaction_status"] is None


def test_reaction_status_null_when_no_request_exists():
    """When there is no @codex review request, reaction fields are None even if reactions provided."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[
            {"id": 901, "content": "+1", "user": {"login": "chatgpt-codex-connector[bot]"}, "created_at": "2026-05-10T20:01:30Z"},
        ],
    )

    assert packet["classification"] == "codex_request_needed"
    assert packet["codex_latest_request_acknowledged"] is None
    assert packet["codex_reaction_status"] is None


def test_fetch_live_payloads_derives_head_pushed_at_from_commit(monkeypatch):
    class FakeGitHubClient:
        def __init__(self, owner, repo):
            self.owner = owner
            self.repo = repo

        def get(self, path):
            if path == "/pulls/189":
                return {
                    "number": 189,
                    "head": {"sha": CURRENT_HEAD},
                    "base": {"ref": "main"},
                    "state": "open",
                    "merged": False,
                }
            if path == f"/commits/{CURRENT_HEAD}":
                return {"commit": {"committer": {"date": "2026-05-10T20:00:00Z"}}}
            raise AssertionError(path)

        def get_all(self, path):
            if path == "/pulls/189/files?per_page=100":
                return [{"filename": "scripts/local/classify_pr_gate_state.py"}]
            if path == "/issues/189/comments?per_page=100":
                return [{"id": 44, "user": {"login": "Slideshow11"}, "created_at": "2026-05-10T20:01:00Z", "body": "@codex review"}]
            if path == "/pulls/189/reviews?per_page=100":
                return []
            raise AssertionError(path)

        def get_check_runs_all(self, head_sha):
            return []

        def get_reactions(self, comment_id):
            assert comment_id == 44
            return [{"content": "eyes", "user": {"login": "chatgpt-codex-connector[bot]"}}]

    monkeypatch.setattr(classifier, "GitHubClient", FakeGitHubClient)

    pr, files, checks, comments, reviews, reactions = classifier.fetch_live_payloads("owner", "repo", 189)

    assert pr["head_pushed_at"] == "2026-05-10T20:00:00Z"
    assert files == ["scripts/local/classify_pr_gate_state.py"]
    assert checks == []
    assert comments[0]["id"] == 44
    assert reviews == []
    assert reactions == [{"content": "eyes", "user": {"login": "chatgpt-codex-connector[bot]"}}]


def test_non_codex_user_reaction_on_suggestions_request_does_not_acknowledge():
    """Human user's +1 on the request comment must not set acknowledged fields
    when Codex has already posted suggestions. The clean/suggestions branches
    already guard on reaction_user == bot, but this test covers the suggestions
    path specifically."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[
            _request(),
            _codex_comment("### 💡 Codex Review\n\nHere are some automated review suggestions.", created_at="2026-05-10T20:02:00Z"),
        ],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[
            {"id": 901, "content": "+1", "user": {"login": "sloppy-hacker99"}, "created_at": "2026-05-10T20:01:30Z"},
        ],
    )

    assert packet["classification"] == "codex_suggestions"
    assert packet["codex_status"] == "suggestions"
    # Human reaction must NOT set acknowledged — suggestions signal takes precedence
    assert packet["codex_latest_request_acknowledged"] is None
    assert packet["codex_reaction_status"] is None


def test_merged_pr_short_circuits_to_terminal_state():
    """A merged PR must be classified as blocked_pr_merged before CI or Codex checks run."""
    packet = classifier.classify_payloads(
        pr=_pr(state="closed", merged=True),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[_codex_clean()],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
    )
    assert packet["classification"] == "blocked_pr_merged"
    assert packet["recommended_next_action"] == "Stop; PR is already merged. No further gate action is required."
    assert any("already merged" in b for b in packet["blockers"])


def test_merged_pr_does_not_report_missing_ci_blocker():
    packet = classifier.classify_payloads(
        pr=_pr(state="closed", merged=True),
        changed_files=list(ALLOWED),
        check_runs=[],
        issue_comments=[_request()],
        reviews=[_codex_clean()],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
    )

    assert packet["classification"] == "blocked_pr_merged"
    assert packet["ci_status"] == "not_applicable"
    assert packet["blockers"] == ["PR is already merged."]


def test_codex_no_findings_comment_is_clean_not_suggestions():
    packet = _packet(
        comments=[
            _request(),
            _codex_comment("Codex Review: No findings for current head."),
        ]
    )

    assert packet["classification"] == "ready_for_reviewer"
    assert packet["codex_status"] == "clean"
    assert packet["codex_latest_suggestions"] is None


def test_codex_eyes_reaction_also_triggers_acknowledged():
    """Codex eyes reaction (content=='eyes') on request must set acknowledged fields,
    same as +1 reaction. Both are valid Codex acknowledgements."""
    packet = classifier.classify_payloads(
        pr=_pr(),
        changed_files=list(ALLOWED),
        check_runs=_green_checks(),
        issue_comments=[_request()],
        reviews=[],
        allowed_files=ALLOWED,
        expected_head=CURRENT_HEAD,
        codex_bot_login="chatgpt-codex-connector[bot]",
        latest_request_reactions=[
            {"id": 902, "content": "eyes", "user": {"login": "chatgpt-codex-connector[bot]"}, "created_at": "2026-05-10T20:01:30Z"},
        ],
    )
    # No clean/suggestions signal yet; pending with eyes acknowledgement
    assert packet["classification"] == "codex_pending"
    assert packet["codex_status"] == "pending"
    assert packet["codex_latest_request_acknowledged"] is True
    assert packet["codex_reaction_status"] == "acknowledged_pending"
