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
