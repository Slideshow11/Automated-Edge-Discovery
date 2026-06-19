"""Tests for the AED no-stall lifecycle watchdog skeleton.

Stdlib-only: uses unittest, no pytest-only fixtures. The tests
import the package directly from the repo root and exercise
the pure helpers in ``aed_lifecycle`` (no_stall, checkpoint,
watchdog).

These tests are the regression guard for PR #405
(tooling/aed-no-stall-watchdog-v1). The PR exposes a class of
bug seen in PR #404 and prior runs: the agent emits a final
response that is just a phase header (``Starting PHASE 1 — ...``)
or a generic progress note (``Now PHASE 8 — Codex re-review.``)
and then stops. There is no terminal lifecycle state, no
checkpoint, and no ``next_action``. A future Humphry/Telegram
runner that ingests these messages must be able to detect this
as a stall and either resume from a checkpoint or refuse to
treat the run as finished.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the aed_lifecycle package importable when pytest is invoked
# from any working directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aed_lifecycle.no_stall import (  # noqa: E402
    STALL_NO_CHECKPOINT,
    STALL_NO_TERMINAL_STATE,
    STALL_PHASE_HEADER_ONLY,
    STALL_WAITING_FOR_CONTINUE,
    OK_PROGRESS_WITH_NEXT_ACTION,
    OK_TERMINAL,
    classify_humphry_message_for_stall,
    is_terminal_lifecycle_state,
    is_valid_next_action,
    TERMINAL_LIFECYCLE_STATES,
)
from aed_lifecycle.checkpoint import (  # noqa: E402
    CheckpointState,
    checkpoint_requires_operator,
    next_action_from_checkpoint,
    validate_checkpoint,
    validate_resume_observations,
)
from aed_lifecycle.watchdog import (  # noqa: E402
    STALL_RISK,
    WATCHDOG_PROGRESS_REQUIRED,
    WatchdogState,
    evaluate_watchdog,
    should_continue_polling,
)


# ---------------------------------------------------------------------------
# Section 1: is_terminal_lifecycle_state and TERMINAL_LIFECYCLE_STATES
# ---------------------------------------------------------------------------


class TerminalLifecycleStateTests(unittest.TestCase):
    """The terminal-state registry must match the PR #405 spec."""

    def test_merged_is_terminal(self) -> None:
        self.assertTrue(is_terminal_lifecycle_state("MERGED"))

    def test_merge_ready_awaiting_human_authorization_is_terminal(self) -> None:
        self.assertTrue(
            is_terminal_lifecycle_state(
                "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
            )
        )

    def test_all_hold_states_are_terminal(self) -> None:
        holds = [
            "HOLD_NEW_CODEX_THREAD",
            "HOLD_CODEX_RESPONSE_PENDING",
            "HOLD_PR_CI_PENDING",
            "HOLD_PR_CI_FAILED",
            "HOLD_SCOPE_GUARD_FAILED",
            "HOLD_UNAUTHORIZED_THREAD_INVENTORY",
            "HOLD_BRANCH_POLICY_BLOCKED",
            "HOLD_HEAD_CHANGED",
            "HOLD_ISOLATED_WORKSPACE_DIRTY",
            "HOLD_UNEXPECTED_LOCAL_CHANGES",
            "HOLD_OPERATOR_REQUIRED",
        ]
        for state in holds:
            with self.subTest(state=state):
                self.assertTrue(
                    is_terminal_lifecycle_state(state),
                    f"{state} must be terminal",
                )

    def test_failed_is_terminal(self) -> None:
        self.assertTrue(is_terminal_lifecycle_state("FAILED"))

    def test_unknown_state_is_not_terminal(self) -> None:
        self.assertFalse(is_terminal_lifecycle_state("PHASE_1_STARTING"))
        self.assertFalse(is_terminal_lifecycle_state(""))

    def test_terminal_lifecycle_states_set_is_frozen(self) -> None:
        # Set-like object — must be iterable; we treat it as frozen at runtime.
        self.assertIn("MERGED", TERMINAL_LIFECYCLE_STATES)
        self.assertIn(
            "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION", TERMINAL_LIFECYCLE_STATES
        )
        self.assertIn("FAILED", TERMINAL_LIFECYCLE_STATES)


# ---------------------------------------------------------------------------
# Section 2: classify_humphry_message_for_stall
# ---------------------------------------------------------------------------


class ClassifyHumphryMessageTests(unittest.TestCase):
    """Final-output anti-stall guard.

    The classifier is the primary regression guard for the
    phase-header-only failure mode. Every PR #405 spec example
    must produce the documented classification.
    """

    def test_starting_phase_1_is_phase_header_only(self) -> None:
        text = "Starting PHASE 1 — protected-state verification."
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_PHASE_HEADER_ONLY,
        )

    def test_now_phase_8_is_phase_header_only(self) -> None:
        text = "Now PHASE 8 — Codex re-review."
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_PHASE_HEADER_ONLY,
        )

    def test_polling_message_without_terminal_or_next_action_is_no_terminal(
        self,
    ) -> None:
        text = "Let me poll for Codex response."
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_NO_TERMINAL_STATE,
        )

    def test_hold_pr_ci_pending_with_bounded_reason_is_terminal(self) -> None:
        text = "HOLD_PR_CI_PENDING — bounded polling reached limit"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_merge_ready_awaiting_human_authorization_is_terminal(self) -> None:
        text = "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_merged_is_terminal(self) -> None:
        text = "MERGED"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_message_with_next_action_is_progress(self) -> None:
        text = (
            "PHASE 3 complete. next_action: poll CI status, "
            "checkpoint: /tmp/aed/checkpoint.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_continue_keyword_only_is_stall(self) -> None:
        text = "Continue? (yes/no)"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_WAITING_FOR_CONTINUE,
        )

    def test_empty_string_is_no_terminal(self) -> None:
        self.assertEqual(
            classify_humphry_message_for_stall(""),
            STALL_NO_TERMINAL_STATE,
        )

    def test_message_with_checkpoint_marker_but_no_terminal_is_no_checkpoint(
        self,
    ) -> None:
        # Checkpoint is mentioned but no terminal state and no next_action
        # and the message is otherwise a progress note. Treated as missing
        # checkpoint continuation: STALL_NO_CHECKPOINT.
        text = "Wrote checkpoint file but no next_action specified."
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_NO_CHECKPOINT,
        )

    def test_terminal_with_checkpoint_buried_is_not_terminal(self) -> None:
        # A descriptive mention of a terminal state is NOT an
        # explicit assertion. The runner must keep going
        # (or surface a stall) rather than stop on a buried
        # token.
        text = "I see HOLD_PR_CI_PENDING showing in the harness output."
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_TERMINAL)

    def test_explicit_terminal_assertion_via_prefix_is_terminal(self) -> None:
        # The new explicit-assertion rule accepts a
        # "Final lifecycle state: <STATE>" line as terminal.
        text = "Final lifecycle state: HOLD_PR_CI_PENDING"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )


# ---------------------------------------------------------------------------
# Section 3: Watchdog
# ---------------------------------------------------------------------------


class WatchdogStateTests(unittest.TestCase):
    """The watchdog dataclass and evaluate_watchdog helper."""

    def _make_state(self, **overrides: Any) -> WatchdogState:
        base: Dict[str, Any] = dict(
            phase_name="PHASE_1",
            started_at=100.0,
            last_progress_at=100.0,
            max_idle_seconds=300.0,
            max_phase_seconds=1800.0,
            next_action=None,
            checkpoint_path=None,
            terminal_state=None,
        )
        base.update(overrides)
        return WatchdogState(**base)

    def test_terminal_state_is_ok(self) -> None:
        st = self._make_state(terminal_state="HOLD_PR_CI_PENDING")
        verdict = evaluate_watchdog(st, now=200.0)
        self.assertEqual(verdict, "OK_TERMINAL")

    def test_no_terminal_no_next_action_is_stall_risk(self) -> None:
        st = self._make_state()
        verdict = evaluate_watchdog(st, now=200.0)
        self.assertEqual(verdict, STALL_RISK)

    def test_no_terminal_no_checkpoint_is_stall_risk(self) -> None:
        st = self._make_state(
            next_action="poll Codex",
            checkpoint_path=None,
        )
        verdict = evaluate_watchdog(st, now=200.0)
        self.assertEqual(verdict, STALL_RISK)

    def test_idle_exceeded_requires_progress(self) -> None:
        # last progress 400s ago, max_idle 300s
        st = self._make_state(
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=300.0,
            max_phase_seconds=10000.0,
            next_action="poll Codex",
            checkpoint_path="/tmp/ckpt.json",
        )
        verdict = evaluate_watchdog(st, now=400.0)
        self.assertEqual(verdict, WATCHDOG_PROGRESS_REQUIRED)

    def test_phase_time_exceeded_recommends_hold(self) -> None:
        # total phase 2000s, max_phase 1800s
        st = self._make_state(
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10000.0,
            max_phase_seconds=1800.0,
            next_action="poll Codex",
            checkpoint_path="/tmp/ckpt.json",
        )
        verdict = evaluate_watchdog(st, now=2000.0)
        self.assertTrue(
            verdict.startswith("HOLD_"),
            f"expected HOLD_* recommendation, got {verdict!r}",
        )

    def test_within_bounds_with_progress_is_ok(self) -> None:
        st = self._make_state(
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=300.0,
            max_phase_seconds=1800.0,
            next_action="poll Codex",
            checkpoint_path="/tmp/ckpt.json",
        )
        verdict = evaluate_watchdog(st, now=50.0)
        # Within bounds + no terminal => not a stall risk, recommend keep going
        self.assertIn(verdict, ("OK_PROGRESS_WITH_NEXT_ACTION", "OK_TERMINAL"))


# ---------------------------------------------------------------------------
# Section 4: Checkpoint protocol
# ---------------------------------------------------------------------------


def _make_checkpoint(**overrides: Any) -> CheckpointState:
    base: Dict[str, Any] = dict(
        repo="Slideshow11/Automated-Edge-Discovery",
        pr_number=405,
        branch="tooling/aed-no-stall-watchdog-v1",
        current_head="a" * 40,
        phase="PHASE_3",
        completed_phases=["PHASE_1", "PHASE_2"],
        next_phase="PHASE_4",
        next_action="poll CI status",
        pending_actions=["poll CI", "await Codex"],
        # In the default state, all three SHAs match — the
        # PR head, the last-verified PR head, and the
        # last-verified primary head are all on the same SHA.
        # Tests that exercise a head-change case override one
        # of the two ``last_verified_*`` fields.
        last_verified_primary_head="a" * 40,
        last_verified_pr_head="a" * 40,
        authorized_thread_ids=[],
        unresolved_thread_ids=[],
        terminal_state=None,
        updated_at="2026-06-15T00:00:00Z",
    )
    base.update(overrides)
    return CheckpointState(**base)


class CheckpointValidationTests(unittest.TestCase):
    """validate_checkpoint is structural only (no head-comparison logic)."""

    def test_valid_checkpoint_has_no_errors(self) -> None:
        errors = validate_checkpoint(_make_checkpoint())
        self.assertEqual(errors, [])

    def test_missing_required_field(self) -> None:
        ck = _make_checkpoint(branch=None)
        errors = validate_checkpoint(ck)
        self.assertTrue(
            any("branch" in e for e in errors),
            f"expected branch error, got {errors}",
        )

    # ------------------------------------------------------------------
    # Fix A — structural validation must NOT compare PR head to
    # primary head. A normal feature-branch PR has different SHAs
    # in those two fields by design.
    # ------------------------------------------------------------------

    def test_structural_validation_passes_when_pr_head_differs_from_primary(
        self,
    ) -> None:
        # PR head "a"*40, primary head "0"*40. A normal feature
        # branch sits on a different SHA than origin/main.
        # Structural validation must accept this without error.
        ck = _make_checkpoint(
            last_verified_pr_head="a" * 40,
            last_verified_primary_head="0" * 40,
        )
        errors = validate_checkpoint(ck)
        self.assertEqual(
            errors,
            [],
            f"structural validation must not flag PR/primary head inequality, got {errors}",
        )

    def test_pr_head_inequality_with_primary_is_not_a_validation_error(
        self,
    ) -> None:
        # Even when the SHA strings are wildly different and the
        # checkpoint is otherwise fine, structural validation
        # must succeed because the inequality is intentional
        # (PR head != primary head by design).
        ck = _make_checkpoint(
            current_head="a" * 40,
            last_verified_pr_head="a" * 40,
            last_verified_primary_head="0" * 40,
        )
        errors = validate_checkpoint(ck)
        self.assertNotIn(
            True,
            [("primary" in e.lower() or "pr head" in e.lower()) for e in errors],
            f"structural validation must not compare PR head to primary head, got {errors}",
        )


class CheckpointResumeObservationsTests(unittest.TestCase):
    """validate_resume_observations is the head-drift detector.

    The function compares each recorded head against its own
    freshly observed SHA. The runner calls it with the SHAs
    it just fetched from the GitHub API (observed_pr_head) and
    from the protected primary worktree (observed_primary_head).
    """

    def test_resume_allowed_when_observations_match(self) -> None:
        # Default checkpoint: all three SHAs are "a"*40. The
        # observed PR head and observed primary head both match
        # the recorded values — no drift.
        ck = _make_checkpoint()
        errors = validate_resume_observations(
            ck,
            observed_pr_head="a" * 40,
            observed_primary_head="a" * 40,
        )
        self.assertEqual(errors, [])

    def test_resume_hold_on_pr_head_drift(self) -> None:
        ck = _make_checkpoint()
        errors = validate_resume_observations(
            ck,
            observed_pr_head="b" * 40,  # PR head moved
            observed_primary_head="a" * 40,
        )
        self.assertTrue(
            any("PR head" in e for e in errors),
            f"expected PR head drift error, got {errors}",
        )

    def test_resume_hold_on_primary_head_drift(self) -> None:
        ck = _make_checkpoint(last_verified_primary_head="0" * 40)
        errors = validate_resume_observations(
            ck,
            observed_pr_head="a" * 40,
            observed_primary_head="1" * 40,  # primary moved
        )
        self.assertTrue(
            any("primary" in e.lower() for e in errors),
            f"expected primary head drift error, got {errors}",
        )

    def test_resume_holds_on_both_drifts(self) -> None:
        ck = _make_checkpoint(last_verified_primary_head="0" * 40)
        errors = validate_resume_observations(
            ck,
            observed_pr_head="b" * 40,  # PR head moved
            observed_primary_head="1" * 40,  # primary moved
        )
        self.assertEqual(len(errors), 2, f"expected 2 drift errors, got {errors}")

    def test_resume_allows_when_pr_and_primary_heads_differ(self) -> None:
        # The critical regression guard: PR head "a"*40, primary
        # head "0"*40. Both observations match their own
        # recorded values. validate_resume_observations must
        # succeed — the two are different SHAs by design.
        ck = _make_checkpoint(
            last_verified_pr_head="a" * 40,
            last_verified_primary_head="0" * 40,
        )
        errors = validate_resume_observations(
            ck,
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertEqual(errors, [])

    def test_resume_observations_skip_when_observation_is_empty(self) -> None:
        ck = _make_checkpoint()
        # None or empty observation is "skip this check" so the
        # runner can populate only one observation.
        errors_none = validate_resume_observations(
            ck,
            observed_pr_head="",
            observed_primary_head="a" * 40,
        )
        self.assertEqual(errors_none, [])
        errors_empty = validate_resume_observations(
            ck,
            observed_pr_head="a" * 40,
            observed_primary_head="",
        )
        self.assertEqual(errors_empty, [])


class CheckpointResumeTests(unittest.TestCase):
    """next_action_from_checkpoint and checkpoint_requires_operator behavior.

    The PR spec demands these resume behaviors:
      - resume after PHASE 1
      - resume during CI polling
      - resume during Codex polling
      - cannot resume if PR head changed
      - cannot resume if protected primary head changed
      - stale / incomplete checkpoint returns HOLD_OPERATOR_REQUIRED
        or HOLD_HEAD_CHANGED, NOT silent continuation
    """

    def test_resume_after_phase_1(self) -> None:
        ck = _make_checkpoint(
            phase="PHASE_2",
            completed_phases=["PHASE_1"],
            next_phase="PHASE_2",
        )
        action = next_action_from_checkpoint(ck)
        self.assertIsNotNone(action)
        # Should not require operator
        self.assertFalse(checkpoint_requires_operator(ck))

    def test_resume_during_ci_polling(self) -> None:
        ck = _make_checkpoint(
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_5_CI_POLL",
            pending_actions=["poll CI status"],
            next_action="poll CI status",
        )
        action = next_action_from_checkpoint(ck)
        self.assertEqual(action, "poll CI status")
        self.assertFalse(checkpoint_requires_operator(ck))

    def test_resume_during_codex_polling(self) -> None:
        ck = _make_checkpoint(
            phase="PHASE_6_CODEX_POLL",
            pending_actions=["poll Codex response"],
            next_action="poll Codex response",
        )
        action = next_action_from_checkpoint(ck)
        self.assertEqual(action, "poll Codex response")
        self.assertFalse(checkpoint_requires_operator(ck))

    def test_structural_resume_unchanged_by_pr_vs_primary_head_difference(
        self,
    ) -> None:
        # The structural resume helper does not compare heads.
        # A normal feature-branch PR has different PR head and
        # primary head; the helper must still return next_action.
        ck = _make_checkpoint(
            last_verified_pr_head="a" * 40,
            last_verified_primary_head="0" * 40,
            next_action="poll CI status",
        )
        action = next_action_from_checkpoint(ck)
        self.assertEqual(action, "poll CI status")
        self.assertFalse(checkpoint_requires_operator(ck))

    def test_stale_checkpoint_returns_hold(self) -> None:
        ck = _make_checkpoint(phase=None, next_action=None, terminal_state=None)
        # next_action from empty checkpoint should NOT be silent continuation
        action = next_action_from_checkpoint(ck)
        self.assertTrue(
            action is None or action.startswith("HOLD_"),
            f"expected None or HOLD_*, got {action!r}",
        )
        self.assertTrue(checkpoint_requires_operator(ck))

    def test_terminal_checkpoint_does_not_resume(self) -> None:
        ck = _make_checkpoint(
            terminal_state="MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
            next_action="poll CI",
        )
        # A terminal checkpoint is done; no next_action emission.
        action = next_action_from_checkpoint(ck)
        self.assertIsNone(action)


# ---------------------------------------------------------------------------
# Section 5: Bounded polling helper
# ---------------------------------------------------------------------------


class BoundedPollingTests(unittest.TestCase):
    """should_continue_polling must never permit unbounded polling."""

    def test_continues_within_bounds(self) -> None:
        verdict = should_continue_polling(
            started_at=0.0,
            now=10.0,
            max_wait_seconds=300.0,
            poll_count=2,
            max_polls=10,
            pending_state="HOLD_PR_CI_PENDING",
        )
        # Should return either True or a HOLD_* state, but should be safe
        self.assertTrue(verdict in (True, "HOLD_PR_CI_PENDING"))

    def test_stops_at_max_polls(self) -> None:
        verdict = should_continue_polling(
            started_at=0.0,
            now=10.0,
            max_wait_seconds=300.0,
            poll_count=10,
            max_polls=10,
            pending_state="HOLD_PR_CI_PENDING",
        )
        self.assertEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_stops_at_max_wait(self) -> None:
        verdict = should_continue_polling(
            started_at=0.0,
            now=400.0,
            max_wait_seconds=300.0,
            poll_count=2,
            max_polls=10,
            pending_state="HOLD_PR_CI_PENDING",
        )
        self.assertEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_never_permits_unbounded(self) -> None:
        # Excessively large poll_count and elapsed time both exceeded.
        verdict = should_continue_polling(
            started_at=0.0,
            now=10_000.0,
            max_wait_seconds=300.0,
            poll_count=10_000,
            max_polls=10,
            pending_state="HOLD_PR_CI_PENDING",
        )
        self.assertNotEqual(verdict, True)
        self.assertEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_codex_pending_state_round_trip(self) -> None:
        verdict = should_continue_polling(
            started_at=0.0,
            now=10.0,
            max_wait_seconds=300.0,
            poll_count=2,
            max_polls=10,
            pending_state="HOLD_CODEX_RESPONSE_PENDING",
        )
        # Within bounds, just continue.
        self.assertIn(verdict, (True, "HOLD_CODEX_RESPONSE_PENDING"))

        # Hit the max_polls limit.
        verdict2 = should_continue_polling(
            started_at=0.0,
            now=10.0,
            max_wait_seconds=300.0,
            poll_count=10,
            max_polls=10,
            pending_state="HOLD_CODEX_RESPONSE_PENDING",
        )
        self.assertEqual(verdict2, "HOLD_CODEX_RESPONSE_PENDING")


# ---------------------------------------------------------------------------
# Section 6: Checkpoint shape (the canonical field set)
# ---------------------------------------------------------------------------


class CheckpointShapeTests(unittest.TestCase):
    """The checkpoint dataclass must carry every field in the PR spec.

    The fields are listed in the task spec; this test pins them
    so a future refactor cannot silently drop one.
    """

    def test_all_required_fields_present(self) -> None:
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(CheckpointState)}
        required = {
            "repo",
            "pr_number",
            "branch",
            "current_head",
            "phase",
            "completed_phases",
            "next_phase",
            "next_action",
            "pending_actions",
            "last_verified_primary_head",
            "last_verified_pr_head",
            "authorized_thread_ids",
            "unresolved_thread_ids",
            "terminal_state",
            "updated_at",
        }
        missing = required - field_names
        self.assertEqual(missing, set(), f"missing checkpoint fields: {missing}")

    def test_checkpoint_is_json_round_trippable(self) -> None:
        ck = _make_checkpoint()
        # dataclasses.asdict gives a dict; ensure the round-trip works.
        d = json.loads(json.dumps(_ck_to_dict(ck)))
        self.assertEqual(d["pr_number"], 405)
        self.assertEqual(d["branch"], "tooling/aed-no-stall-watchdog-v1")


def _ck_to_dict(ck: CheckpointState) -> Dict[str, Any]:
    """Convert a CheckpointState to a plain JSON-safe dict."""
    import dataclasses

    return dataclasses.asdict(ck)


# ---------------------------------------------------------------------------
# Section 7: Fix B — phase header with next_action + checkpoint
# ---------------------------------------------------------------------------


class ClassifyPhaseHeaderProgressTests(unittest.TestCase):
    """A phase header is only STALL_PHASE_HEADER_ONLY when nothing
    else is in the message. When next_action and a checkpoint
    reference are both present, the message is a resumable
    progress update.
    """

    def test_pure_phase_header_still_stall(self) -> None:
        text = "Starting PHASE 1 — protected-state verification."
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_PHASE_HEADER_ONLY,
        )

    def test_phase_header_with_next_action_and_checkpoint_is_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — next_action: poll CI status, "
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_phase_header_with_next_action_only_is_stall_no_checkpoint(
        self,
    ) -> None:
        text = "Now PHASE 5 — next_action: poll Codex response"
        # Has phase header and next_action but no checkpoint
        # evidence: there is something for the runner to do,
        # but no resume point.
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_NO_CHECKPOINT,
        )

    def test_phase_header_with_checkpoint_only_is_stall_no_terminal(self) -> None:
        text = "Starting PHASE 3 — checkpoint: /tmp/ckpt.json"
        # Has phase header and checkpoint but no next_action.
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_NO_TERMINAL_STATE,
        )

    def test_phase_header_with_terminal_token_still_terminal(self) -> None:
        # The terminal token is on its own line, prefixed
        # with the explicit-assertion prefix. The phase
        # header is on a different line and is therefore
        # ignored by the terminal-assertion check.
        text = (
            "Now PHASE 5 — bounded polling reached limit\n"
            "Final lifecycle state: HOLD_PR_CI_PENDING"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_phase_header_with_buried_terminal_token_is_not_terminal(
        self,
    ) -> None:
        # A phase header that merely MENTIONS a terminal
        # token (no explicit assertion) is not an explicit
        # terminal-state assertion. The runner should not
        # stop on a buried mention.
        text = (
            "Now PHASE 5 — bounded polling reached limit, "
            "HOLD_PR_CI_PENDING"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_TERMINAL)


# ---------------------------------------------------------------------------
# Section 8: Fix C — canonical registry coverage
# ---------------------------------------------------------------------------


class CanonicalRegistryCoverageTests(unittest.TestCase):
    """is_terminal_lifecycle_state must cover every canonical
    HOLD/terminal state from the AED lifecycle registry at
    schemas/aed_lifecycle_states_v1.json.

    This test reads the schema directly and asserts coverage,
    so a future registry addition fails the suite until
    TERMINAL_LIFECYCLE_STATES is updated.
    """

    @classmethod
    def setUpClass(cls) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "schemas"
            / "aed_lifecycle_states_v1.json"
        )
        with path.open("r", encoding="utf-8") as f:
            cls.registry = json.load(f)
        cls.canonical_hold_states = [
            name
            for name, entry in cls.registry["states"].items()
            if entry.get("category") == "hold"
        ]
        cls.canonical_terminal_states = [
            name
            for name, entry in cls.registry["states"].items()
            if entry.get("category") == "terminal"
        ]

    def test_every_canonical_hold_state_is_recognized(self) -> None:
        for state in self.canonical_hold_states:
            with self.subTest(state=state):
                self.assertTrue(
                    is_terminal_lifecycle_state(state),
                    f"canonical HOLD state {state!r} is not in "
                    "TERMINAL_LIFECYCLE_STATES",
                )

    def test_every_canonical_terminal_state_is_recognized(self) -> None:
        for state in self.canonical_terminal_states:
            with self.subTest(state=state):
                self.assertTrue(
                    is_terminal_lifecycle_state(state),
                    f"canonical terminal state {state!r} is not in "
                    "TERMINAL_LIFECYCLE_STATES",
                )

    def test_specific_canonical_hold_states_required_by_pr_spec(self) -> None:
        # Per the PR #405 spec: these specific canonical states
        # must be recognized as terminal/parked.
        for state in [
            "HOLD_MAIN_HEAD_MISMATCH",
            "HOLD_MERGE_STATE_BLOCKED",
            "HOLD_POST_MERGE_CI_PENDING",
            "HOLD_RESUME_CHECKPOINT_NEEDED",
        ]:
            with self.subTest(state=state):
                self.assertTrue(
                    is_terminal_lifecycle_state(state),
                    f"PR-spec required canonical state {state!r} "
                    "is not recognized",
                )

    def test_merge_ready_awaiting_human_authorization_recognized(self) -> None:
        self.assertTrue(
            is_terminal_lifecycle_state("MERGE_READY_AWAITING_HUMAN_AUTHORIZATION")
        )

    def test_merged_and_failed_still_recognized(self) -> None:
        self.assertTrue(is_terminal_lifecycle_state("MERGED"))
        self.assertTrue(is_terminal_lifecycle_state("FAILED"))


# ---------------------------------------------------------------------------
# Section 9: Fix D — watchdog next_action requirement
# ---------------------------------------------------------------------------


class WatchdogNextActionRequirementTests(unittest.TestCase):
    """OK_PROGRESS_WITH_NEXT_ACTION requires BOTH checkpoint_path
    AND next_action. If either is missing, the verdict is
    STALL_RISK (or HOLD_* for budget reasons).
    """

    def _make_state(self, **overrides: Any) -> WatchdogState:
        base: Dict[str, Any] = dict(
            phase_name="PHASE_1",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=300.0,
            max_phase_seconds=1800.0,
            next_action=None,
            checkpoint_path=None,
            terminal_state=None,
        )
        base.update(overrides)
        return WatchdogState(**base)

    def test_checkpoint_path_present_next_action_missing_is_stall(self) -> None:
        # Codex finding 3412650321: a checkpoint without a
        # next_action is the checkpoint-without-continuation
        # stall case the protocol is trying to catch.
        st = self._make_state(checkpoint_path="/tmp/ckpt.json")
        verdict = evaluate_watchdog(st, now=10.0)
        self.assertEqual(verdict, STALL_RISK)

    def test_next_action_present_checkpoint_path_missing_is_stall(
        self,
    ) -> None:
        st = self._make_state(next_action="poll Codex")
        verdict = evaluate_watchdog(st, now=10.0)
        self.assertEqual(verdict, STALL_RISK)

    def test_both_present_within_bounds_is_progress(self) -> None:
        st = self._make_state(
            next_action="poll Codex",
            checkpoint_path="/tmp/ckpt.json",
        )
        verdict = evaluate_watchdog(st, now=10.0)
        self.assertEqual(verdict, "OK_PROGRESS_WITH_NEXT_ACTION")

    def test_terminal_state_present_is_ok(self) -> None:
        # Even with no checkpoint_path or next_action, a
        # terminal state means the run is parked. OK_TERMINAL
        # wins over the STALL_RISK branch.
        st = self._make_state(terminal_state="HOLD_PR_CI_PENDING")
        verdict = evaluate_watchdog(st, now=10.0)
        self.assertEqual(verdict, "OK_TERMINAL")


# ---------------------------------------------------------------------------
# Section 10: Whole-token terminal-state matching (Codex 3413237706)
# ---------------------------------------------------------------------------


class ClassifyWholeTokenTerminalTests(unittest.TestCase):
    """The terminal-state match in the classifier must be
    whole-token (word-boundary), not substring. A longer
    non-terminal name containing a shorter registered terminal
    name (e.g. ``CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED`` contains
    the substring ``CODEX_CLEAN_PASS``) must NOT trigger
    OK_TERMINAL.
    """

    def test_longer_non_terminal_with_substring_is_not_terminal(self) -> None:
        # CODEX_CLEAN_PASS is in TERMINAL_LIFECYCLE_STATES but
        # CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED is not. Whole-token
        # matching must reject the longer name even though it
        # contains the shorter one as a substring.
        text = "Now in CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED state."
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_TERMINAL)

    def test_explicit_terminal_assertion_matches(self) -> None:
        # The terminal state appears via an explicit prefix
        # on its own line.
        text = "Final lifecycle state: MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_at_string_end_via_em_dash(self) -> None:
        # Trailing em-dash separator: state at start of line.
        text = "HOLD_PR_CI_PENDING — bounded polling reached limit"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_alone_matches(self) -> None:
        # The state on its own line.
        text = "MERGED"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )


# ---------------------------------------------------------------------------
# Section 11: Optional checkpoint fields may be None (Codex 3413237709)
# ---------------------------------------------------------------------------


class CheckpointOptionalFieldsTests(unittest.TestCase):
    """A terminal checkpoint with optional fields set to None
    (e.g. no phase, no next_phase, no last_verified_*_head)
    must pass structural validation. The dataclass marks these
    fields as ``Optional[...]`` with sensible defaults, so
    None is a valid value.
    """

    def test_terminal_checkpoint_with_only_required_fields_passes(self) -> None:
        # Build a checkpoint with the truly required fields
        # only. Optional fields default to None / empty.
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase=None,
            terminal_state="MERGED",
        )
        errors = validate_checkpoint(ck)
        self.assertEqual(
            errors,
            [],
            f"terminal checkpoint with optional fields None must pass, got {errors}",
        )

    def test_checkpoint_with_none_for_all_optional_fields_passes(self) -> None:
        # All optional fields None, only the truly required
        # ones populated. Validation must succeed.
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase=None,
            next_phase=None,
            next_action=None,
            pending_actions=[],
            last_verified_primary_head=None,
            last_verified_pr_head=None,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state="HOLD_RESUME_CHECKPOINT_NEEDED",
            updated_at=None,
        )
        errors = validate_checkpoint(ck)
        self.assertEqual(
            errors,
            [],
            f"checkpoint with all optional fields None must pass, got {errors}",
        )

    def test_checkpoint_with_optional_lists_empty_passes(self) -> None:
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5",
            completed_phases=[],
            pending_actions=[],
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
        )
        errors = validate_checkpoint(ck)
        self.assertEqual(
            errors,
            [],
            f"empty required list fields must be allowed, got {errors}",
        )

    def test_checkpoint_missing_repo_string_fails(self) -> None:
        # Truly required string fields are still enforced.
        ck = CheckpointState(
            repo="",  # empty — should fail
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase=None,
        )
        errors = validate_checkpoint(ck)
        self.assertTrue(
            any("repo" in e for e in errors),
            f"empty repo must fail validation, got {errors}",
        )

    def test_checkpoint_missing_branch_string_fails(self) -> None:
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="",
            current_head="a" * 40,
            phase=None,
        )
        errors = validate_checkpoint(ck)
        self.assertTrue(
            any("branch" in e for e in errors),
            f"empty branch must fail validation, got {errors}",
        )


# ---------------------------------------------------------------------------
# Section 12: Recorded-head-missing error (Codex 3413328909)
# ---------------------------------------------------------------------------


class CheckpointRecordedHeadMissingTests(unittest.TestCase):
    """validate_resume_observations must NOT silently skip the
    head-drift check when the recorded head is None or empty.
    A checkpoint that lost its recorded heads is structurally
    unfit for resume and the runner must surface it as a hold.
    """

    def test_missing_recorded_pr_head_is_error(self) -> None:
        # A checkpoint with no recorded PR head but a valid
        # recorded primary head and a non-empty observation.
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action="poll CI",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head=None,  # missing
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )
        errors = validate_resume_observations(
            ck,
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertTrue(
            any("PR head" in e and "missing" in e for e in errors),
            f"missing recorded PR head must be an error, got {errors}",
        )

    def test_missing_recorded_primary_head_is_error(self) -> None:
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action="poll CI",
            pending_actions=[],
            last_verified_primary_head=None,  # missing
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )
        errors = validate_resume_observations(
            ck,
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertTrue(
            any("primary" in e.lower() and "missing" in e for e in errors),
            f"missing recorded primary head must be an error, got {errors}",
        )

    def test_both_recorded_heads_missing_yields_two_errors(self) -> None:
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action="poll CI",
            pending_actions=[],
            last_verified_primary_head=None,
            last_verified_pr_head=None,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )
        errors = validate_resume_observations(
            ck,
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertEqual(
            len(errors),
            2,
            f"both missing recorded heads must produce 2 errors, got {errors}",
        )


# ---------------------------------------------------------------------------
# Section 13: next_action must have a non-empty value (Codex 3413328918)
# ---------------------------------------------------------------------------


class ClassifyNextActionEmptyValueTests(unittest.TestCase):
    """The classifier must require a non-empty, non-placeholder
    value after a ``next_action:`` marker. A bare marker with
    no value (or with a placeholder like ``none`` / ``null``)
    must NOT classify as OK_PROGRESS_WITH_NEXT_ACTION.
    """

    def test_bare_next_action_marker_is_not_progress(self) -> None:
        # Newline-separated bare marker with no value.
        text = "checkpoint: /tmp/ckpt.json\nnext_action:"
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_next_action_none_is_not_progress(self) -> None:
        text = "next_action: none"
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_next_action_null_is_not_progress(self) -> None:
        text = "next_action: null"
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_next_action_todo_is_not_progress(self) -> None:
        text = "next_action: todo"
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_next_action_with_real_value_is_progress(self) -> None:
        # Fix G (Codex 3417011620): OK_PROGRESS_WITH_NEXT_ACTION
        # now requires BOTH a valid next_action value AND a
        # valid value-bearing checkpoint marker. The previous
        # version accepted a bare next_action without a
        # checkpoint as OK_PROGRESS_WITH_NEXT_ACTION, but a
        # runner with no resume point cannot safely continue.
        text = (
            "next_action: poll CI status\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_prose_next_step_with_real_value_is_NOT_top_level_progress(self) -> None:
        # Fix Q (Codex 3439736315): "next step:" is a prose
        # variant, NOT a top-level next_action marker. The
        # top-level extractor scans only the canonical
        # protocol markers (``next_action:`` and
        # ``next_action=``). When a runner emits only
        # ``next step:`` at the start of a line — with no
        # canonical ``next_action:`` marker anywhere in the
        # message — the parser must NOT classify as
        # OK_PROGRESS_WITH_NEXT_ACTION even if a checkpoint
        # is present.
        text = (
            "next step: poll CI status\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )


# ---------------------------------------------------------------------------
# Section 14: Explicit terminal-state assertions (Codex 3413417446)
# ---------------------------------------------------------------------------


class ClassifyExplicitTerminalAssertionTests(unittest.TestCase):
    """The terminal-state classifier must require an explicit
    assertion. A negated, future, or descriptive mention of
    a terminal state must NOT be classified as OK_TERMINAL.
    """

    # --- Should NOT be terminal ---

    def test_negated_terminal_mention_is_not_terminal(self) -> None:
        text = "Not MERGED yet"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_future_looking_terminal_mention_is_not_terminal(self) -> None:
        text = "will be MERGED after review"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_negated_hold_mention_is_not_terminal(self) -> None:
        text = "not HOLD_PR_CI_PENDING"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_uncertain_mention_is_not_terminal(self) -> None:
        text = "next state might be MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_no_terminal_mention_is_not_terminal(self) -> None:
        text = "No MERGED state yet"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_negated_with_next_action_is_not_terminal(self) -> None:
        # The exact example from the Codex finding.
        text = (
            "Not MERGED yet; next_action: poll CI status, "
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    # --- Should BE terminal ---

    def test_explicit_final_lifecycle_state_is_terminal(self) -> None:
        text = "Final lifecycle state: MERGED"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_explicit_terminal_state_prefix_is_terminal(self) -> None:
        text = "Terminal state: HOLD_NEW_CODEX_THREAD"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_bare_terminal_state_is_terminal(self) -> None:
        text = "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_explicit_assertion_with_em_dash_explanation_is_terminal(self) -> None:
        text = "Final lifecycle state: HOLD_PR_CI_PENDING — bounded polling reached limit"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_phase_header_plus_explicit_assertion_is_terminal(self) -> None:
        # Phase header + explicit assertion: the assertion wins.
        text = (
            "Now PHASE 5 — bounded polling reached limit\n"
            "Final lifecycle state: HOLD_PR_CI_PENDING"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_phase_header_plus_next_action_plus_checkpoint_is_still_progress(
        self,
    ) -> None:
        # A phase-header message with both next_action and
        # checkpoint evidence (no terminal mention) is
        # OK_PROGRESS_WITH_NEXT_ACTION. The terminal-assertion
        # rule must not change this case.
        text = (
            "Starting PHASE 2 — next_action: poll CI status, "
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )


# ---------------------------------------------------------------------------
# Section 15: CI token-match watchdog (Codex 3413417456)
# ---------------------------------------------------------------------------


class WatchdogCITokenMatchTests(unittest.TestCase):
    """The watchdog phase-time-exhaustion hold recommender
    must use a word-boundary / token pattern for CI detection,
    not a substring. Words like "decide" or "reconcile" must
    not be reported as CI pending.
    """

    def _state(self, next_action: str) -> WatchdogState:
        # Phase time exhausted: started_at=0, now=10_000,
        # max_phase=1800. Both fields set, but terminal is None.
        return WatchdogState(
            phase_name="PHASE_X",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10000.0,
            max_phase_seconds=1800.0,
            next_action=next_action,
            checkpoint_path="/tmp/ckpt.json",
            terminal_state=None,
        )

    def test_poll_ci_status_is_ci_pending(self) -> None:
        verdict = evaluate_watchdog(self._state("poll CI status"), now=10000.0)
        self.assertEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_github_actions_is_ci_pending(self) -> None:
        verdict = evaluate_watchdog(self._state("check github actions"), now=10000.0)
        self.assertEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_ci_poll_token_is_ci_pending(self) -> None:
        # Fix C (Codex 3414948257): a bare "ci" token is a CI
        # signal. The older `_status|_poll` suffix variants are
        # NOT in the new pattern — only the noun "ci" / "CI" /
        # "pr ci" is matched.
        verdict = evaluate_watchdog(self._state("poll ci"), now=10000.0)
        self.assertEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_decide_whether_to_merge_is_not_ci_pending(self) -> None:
        # "decide" contains the substring "ci" but is not a CI
        # action. Must NOT trigger HOLD_PR_CI_PENDING.
        verdict = evaluate_watchdog(
            self._state("decide whether to merge"), now=10000.0
        )
        self.assertNotEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_reconcile_threads_is_not_ci_pending(self) -> None:
        verdict = evaluate_watchdog(
            self._state("reconcile threads"), now=10000.0
        )
        self.assertNotEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_policy_review_is_not_ci_pending(self) -> None:
        verdict = evaluate_watchdog(self._state("policy review"), now=10000.0)
        self.assertNotEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_lifecycle_audit_is_not_ci_pending(self) -> None:
        # "lifecycle" alone is in the spec's "should not" list.
        # "lifecycle audit" has no CI token and no "ci" word.
        verdict = evaluate_watchdog(
            self._state("lifecycle audit"), now=10000.0
        )
        self.assertNotEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_suspicious_change_is_not_ci_pending(self) -> None:
        verdict = evaluate_watchdog(
            self._state("suspicious change review"), now=10000.0
        )
        self.assertNotEqual(verdict, "HOLD_PR_CI_PENDING")

    def test_codex_response_is_codex_pending(self) -> None:
        verdict = evaluate_watchdog(
            self._state("poll codex response"), now=10000.0
        )
        self.assertEqual(verdict, "HOLD_CODEX_RESPONSE_PENDING")

    def test_generic_unknown_action_is_operator_required(self) -> None:
        # "wait for feedback" has no CI token and no "ci" word.
        # The spec says a generic non-CI non-Codex action maps
        # to HOLD_OPERATOR_REQUIRED.
        verdict = evaluate_watchdog(
            self._state("wait for feedback"), now=10000.0
        )
        self.assertEqual(verdict, "HOLD_OPERATOR_REQUIRED")


# ---------------------------------------------------------------------------
# Section 16: next_action validation in checkpoint (Codex 3413417465)
# ---------------------------------------------------------------------------


class ValidateNextActionTests(unittest.TestCase):
    """validate_checkpoint must reject non-string, empty,
    whitespace-only, and placeholder ``next_action`` values.
    next_action_from_checkpoint must never return a non-string.
    """

    def _base_checkpoint(self, next_action):
        return CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action=next_action,
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )

    def test_none_next_action_allowed(self) -> None:
        # next_action=None is documented as optional.
        errors = validate_checkpoint(self._base_checkpoint(None))
        self.assertEqual(errors, [])

    def test_valid_string_next_action_allowed(self) -> None:
        errors = validate_checkpoint(
            self._base_checkpoint("poll CI status")
        )
        self.assertEqual(errors, [])

    def test_empty_string_next_action_rejected(self) -> None:
        errors = validate_checkpoint(self._base_checkpoint(""))
        self.assertTrue(
            any("next_action" in e and "empty" in e for e in errors),
            f"empty next_action must fail, got {errors}",
        )

    def test_whitespace_only_next_action_rejected(self) -> None:
        errors = validate_checkpoint(self._base_checkpoint("   "))
        self.assertTrue(
            any("next_action" in e for e in errors),
            f"whitespace-only next_action must fail, got {errors}",
        )

    def test_list_next_action_rejected(self) -> None:
        errors = validate_checkpoint(self._base_checkpoint(["poll"]))
        self.assertTrue(
            any("next_action" in e and "list" in e for e in errors),
            f"list next_action must fail, got {errors}",
        )

    def test_dict_next_action_rejected(self) -> None:
        errors = validate_checkpoint(self._base_checkpoint({"a": 1}))
        self.assertTrue(
            any("next_action" in e and "dict" in e for e in errors),
            f"dict next_action must fail, got {errors}",
        )

    def test_int_next_action_rejected(self) -> None:
        errors = validate_checkpoint(self._base_checkpoint(42))
        self.assertTrue(
            any("next_action" in e and "int" in e for e in errors),
            f"int next_action must fail, got {errors}",
        )

    def test_bool_next_action_rejected(self) -> None:
        errors = validate_checkpoint(self._base_checkpoint(True))
        self.assertTrue(
            any("next_action" in e and "bool" in e for e in errors),
            f"bool next_action must fail, got {errors}",
        )

    def test_placeholder_next_action_rejected(self) -> None:
        for placeholder in ("none", "null", "n/a", "todo", "tbd", "tba"):
            with self.subTest(placeholder=placeholder):
                errors = validate_checkpoint(
                    self._base_checkpoint(placeholder)
                )
                self.assertTrue(
                    any(
                        "next_action" in e and "placeholder" in e
                        for e in errors
                    ),
                    f"placeholder {placeholder!r} must fail, got {errors}",
                )

    def test_next_action_from_checkpoint_never_returns_non_string(self) -> None:
        # Bypass validation: set next_action to a list.
        ck = self._base_checkpoint(["not a string"])
        action = next_action_from_checkpoint(ck)
        # The helper must surface a hold, not the bad value.
        self.assertTrue(
            isinstance(action, str),
            f"next_action_from_checkpoint must return a string, got {type(action).__name__}: {action!r}",
        )
        self.assertEqual(action, "HOLD_OPERATOR_REQUIRED")

    def test_next_action_from_checkpoint_rejects_placeholder(self) -> None:
        ck = self._base_checkpoint("none")
        action = next_action_from_checkpoint(ck)
        self.assertEqual(action, "HOLD_OPERATOR_REQUIRED")


# ---------------------------------------------------------------------------
# Section 17: Regression coverage for the 4 reanchored findings (Fix D)
# ---------------------------------------------------------------------------


class ReanchoredFindingsRegressionTests(unittest.TestCase):
    """The 4 findings that Codex re-anchored to the current head
    (3412614313, 3412650321, 3413237709, 3413328918) were
    addressed in earlier rounds. These tests pin that the
    implementation still satisfies them after the new fix
    changes.
    """

    # --- 3412614313: canonical lifecycle registry vocabulary ---

    def test_canonical_registry_coverage_holds(self) -> None:
        # Read the schema and verify every canonical HOLD
        # and terminal state is recognized.
        path = (
            Path(__file__).resolve().parent.parent
            / "schemas"
            / "aed_lifecycle_states_v1.json"
        )
        with path.open("r", encoding="utf-8") as f:
            registry = json.load(f)
        for state_name, entry in registry["states"].items():
            if entry.get("category") in ("hold", "terminal"):
                with self.subTest(state=state_name):
                    self.assertTrue(
                        is_terminal_lifecycle_state(state_name),
                        f"canonical state {state_name!r} must be recognized",
                    )

    def test_specific_canonical_holds_required_by_pr_spec(self) -> None:
        # Per the PR #405 spec.
        for state in [
            "HOLD_MAIN_HEAD_MISMATCH",
            "HOLD_MERGE_STATE_BLOCKED",
            "HOLD_POST_MERGE_CI_PENDING",
            "HOLD_RESUME_CHECKPOINT_NEEDED",
        ]:
            with self.subTest(state=state):
                self.assertTrue(is_terminal_lifecycle_state(state))

    # --- 3412650321: watchdog requires next_action for OK progress ---

    def test_watchdog_checkpoint_without_next_action_is_stall(self) -> None:
        st = WatchdogState(
            phase_name="PHASE_1",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=300.0,
            max_phase_seconds=1800.0,
            next_action=None,
            checkpoint_path="/tmp/ckpt.json",
            terminal_state=None,
        )
        self.assertEqual(evaluate_watchdog(st, now=10.0), "STALL_RISK")

    def test_watchdog_next_action_without_checkpoint_is_stall(self) -> None:
        st = WatchdogState(
            phase_name="PHASE_1",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=300.0,
            max_phase_seconds=1800.0,
            next_action="poll Codex",
            checkpoint_path=None,
            terminal_state=None,
        )
        self.assertEqual(evaluate_watchdog(st, now=10.0), "STALL_RISK")

    def test_watchdog_both_present_is_progress(self) -> None:
        st = WatchdogState(
            phase_name="PHASE_1",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=300.0,
            max_phase_seconds=1800.0,
            next_action="poll Codex",
            checkpoint_path="/tmp/ckpt.json",
            terminal_state=None,
        )
        self.assertEqual(
            evaluate_watchdog(st, now=10.0),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    # --- 3413237709: optional checkpoint fields may be None ---

    def test_optional_fields_can_be_none(self) -> None:
        # Build a checkpoint with optional fields None.
        ck = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase=None,
            next_phase=None,
            next_action=None,
            pending_actions=[],
            last_verified_primary_head=None,
            last_verified_pr_head=None,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state="MERGED",
            updated_at=None,
        )
        errors = validate_checkpoint(ck)
        self.assertEqual(errors, [])

    # --- 3413328918: next_action marker requires value ---

    def test_next_action_marker_with_no_value_is_stall(self) -> None:
        # The classifier must reject a bare marker.
        text = "checkpoint: /tmp/ckpt.json\nnext_action:"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_action_marker_with_placeholder_value_is_stall(self) -> None:
        text = "next_action: none"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )


# ---------------------------------------------------------------------------
# Section 17: Fix A — reject non-string required checkpoint fields
#             (Codex 3414948246)
# ---------------------------------------------------------------------------


class RejectNonStringRequiredFieldsTests(unittest.TestCase):
    """Fix A: required string fields must be actual strings.

    The validator must reject non-string values such as
    ``int``, ``float``, ``bool``, ``list``, ``dict``, ``tuple``
    and ``object`` for the required string fields ``repo``,
    ``branch``, ``current_head``. Optional fields documented
    as ``Optional[...]`` must still be allowed to be ``None``.
    """

    def _base_args(self):
        return dict(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action="poll CI status",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )

    def _ck(self, **overrides):
        return CheckpointState(**{**self._base_args(), **overrides})

    def test_repo_int_rejected(self) -> None:
        errors = validate_checkpoint(self._ck(repo=123))
        self.assertTrue(any("'repo'" in e and "int" in e for e in errors))

    def test_branch_list_rejected(self) -> None:
        errors = validate_checkpoint(self._ck(branch=[]))
        self.assertTrue(any("'branch'" in e for e in errors))

    def test_current_head_int_rejected(self) -> None:
        errors = validate_checkpoint(self._ck(current_head=42))
        self.assertTrue(any("'current_head'" in e for e in errors))

    def test_repo_bool_rejected(self) -> None:
        errors = validate_checkpoint(self._ck(repo=True))
        self.assertTrue(any("'repo'" in e and "bool" in e for e in errors))

    def test_branch_float_rejected(self) -> None:
        errors = validate_checkpoint(self._ck(branch=3.14))
        self.assertTrue(any("'branch'" in e and "float" in e for e in errors))

    def test_current_head_dict_rejected(self) -> None:
        errors = validate_checkpoint(self._ck(current_head={"a": 1}))
        self.assertTrue(any("'current_head'" in e for e in errors))

    def test_repo_tuple_rejected(self) -> None:
        errors = validate_checkpoint(self._ck(repo=("a", "b")))
        self.assertTrue(any("'repo'" in e and "tuple" in e for e in errors))

    def test_valid_strings_still_accepted(self) -> None:
        errors = validate_checkpoint(self._ck())
        self.assertEqual(errors, [])

    def test_optional_fields_still_accepted_as_none(self) -> None:
        # Documented Optional fields (next_action, terminal_state,
        # updated_at, etc.) may still be None.
        errors = validate_checkpoint(
            self._ck(next_action=None, terminal_state=None, updated_at=None)
        )
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Section 18: Fix B — terminal assertions before disqualifying explanations
#             (Codex 3414948252)
# ---------------------------------------------------------------------------


class TerminalAssertionDisqualifierScopeTests(unittest.TestCase):
    """Fix B: the disqualifier must be scoped to the ambiguous
    portion of an explicit terminal assertion, not the
    explanation that follows.

    A line that starts with an explicit prefix and a canonical
    terminal state is an assertion even if the explanation
    contains ``"no"``, ``"not"``, ``"missing"``, or ``"after"``.
    Bare / ambiguous mentions still apply the full disqualifier.
    """

    def test_prefix_with_no_in_explanation(self) -> None:
        text = (
            "Final lifecycle state: HOLD_RESUME_CHECKPOINT_NEEDED — "
            "no next_action/checkpoint"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_prefix_with_not_in_explanation(self) -> None:
        text = "Terminal state: HOLD_OPERATOR_REQUIRED — not yet resumed"
        self.assertEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_prefix_with_missing_in_explanation(self) -> None:
        text = "Lifecycle state: HOLD_PR_CI_PENDING — missing result"
        self.assertEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_prefix_with_after_in_explanation(self) -> None:
        text = "Final state: MERGED after operator merge"
        self.assertEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_em_dash_state_with_no_in_explanation(self) -> None:
        text = "HOLD_PR_CI_PENDING — no final check result"
        self.assertEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_bare_not_merged_yet_not_terminal(self) -> None:
        text = "Not MERGED yet"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_bare_no_merged_state_not_terminal(self) -> None:
        text = "No MERGED state yet"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_bare_will_be_merged_not_terminal(self) -> None:
        text = "will be MERGED after review"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_bare_might_be_hold_not_terminal(self) -> None:
        text = "next state might be HOLD_PR_CI_PENDING"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text), OK_TERMINAL
        )

    def test_exact_terminal_state_alone_is_terminal(self) -> None:
        # Regression: exact terminal state on a line by itself
        # must still be classified OK_TERMINAL.
        self.assertEqual(
            classify_humphry_message_for_stall("MERGED"), OK_TERMINAL
        )


# ---------------------------------------------------------------------------
# Section 19: Fix C — narrow CI matching (Codex 3414948257)
# ---------------------------------------------------------------------------


class NarrowCITokenMatchingTests(unittest.TestCase):
    """Fix C: generic English verbs (``check``, ``checks``) alone
    are NOT CI tokens. Only the documented CI-specific phrases
    are matched. Codex token matching remains token-safe."""

    def _state(self, next_action: str) -> WatchdogState:
        return WatchdogState(
            phase_name="PHASE_X",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10000.0,
            max_phase_seconds=1800.0,
            next_action=next_action,
            checkpoint_path="/tmp/ckpt.json",
            terminal_state=None,
        )

    # --- Spec positives: these ARE CI ---

    def test_poll_ci_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("poll CI status"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_pr_ci_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("wait for pr ci"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_github_actions_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("check github actions"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_workflow_run_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("wait for workflow run"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_test_3_11_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("poll test (3.11)"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_test_3_11_bare_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("poll test 3.11"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_required_checks_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("required checks pending"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_status_check_is_ci_pending(self) -> None:
        self.assertEqual(
            evaluate_watchdog(self._state("wait for status check"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    # --- Spec negatives: these are NOT CI ---

    def test_check_docs_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(self._state("check docs"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_check_thread_inventory_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(
                self._state("check thread inventory"), now=10000.0
            ),
            "HOLD_PR_CI_PENDING",
        )

    def test_run_checks_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(self._state("run checks"), now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_reconcile_threads_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(
                self._state("reconcile threads"), now=10000.0
            ),
            "HOLD_PR_CI_PENDING",
        )

    def test_decide_whether_to_merge_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(
                self._state("decide whether to merge"), now=10000.0
            ),
            "HOLD_PR_CI_PENDING",
        )

    def test_policy_review_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(
                self._state("policy review"), now=10000.0
            ),
            "HOLD_PR_CI_PENDING",
        )

    def test_lifecycle_state_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(
                self._state("lifecycle state review"), now=10000.0
            ),
            "HOLD_PR_CI_PENDING",
        )

    def test_suspicious_activity_is_not_ci_pending(self) -> None:
        self.assertNotEqual(
            evaluate_watchdog(
                self._state("suspicious activity review"), now=10000.0
            ),
            "HOLD_PR_CI_PENDING",
        )

    def test_codex_token_match_remains_token_safe(self) -> None:
        # codex_response is a true Codex match.
        self.assertEqual(
            evaluate_watchdog(
                self._state("poll codex response"), now=10000.0
            ),
            "HOLD_CODEX_RESPONSE_PENDING",
        )


# ---------------------------------------------------------------------------
# Section 20: Fix D — checkpoint_requires_operator uses canonical
#             next_action validity check (Codex 3414948261)
# ---------------------------------------------------------------------------


class CheckpointRequiresOperatorInvalidNextActionTests(unittest.TestCase):
    """Fix D: ``checkpoint_requires_operator`` must share the
    canonical ``is_valid_next_action`` check used by
    ``validate_checkpoint`` and ``next_action_from_checkpoint``.

    A checkpoint with a phase but an unusable ``next_action``
    (``""``, ``"   "``, ``"none"``, ``"todo"``, a list, a dict,
    an int) must return ``True`` (operator required). Only a
    valid non-placeholder string next_action returns ``False``.
    """

    def _ck(self, next_action):
        return CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action=next_action,
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )

    def test_empty_string_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck("")))

    def test_whitespace_string_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck("   ")))

    def test_placeholder_none_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck("none")))

    def test_placeholder_todo_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck("todo")))

    def test_placeholder_null_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck("null")))

    def test_list_next_action_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck([])))

    def test_dict_next_action_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck({})))

    def test_int_next_action_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck(123)))

    def test_valid_string_does_not_require_operator(self) -> None:
        self.assertFalse(
            checkpoint_requires_operator(self._ck("poll CI status"))
        )

    def test_canonical_helper_is_string_invariant(self) -> None:
        # is_valid_next_action is the single canonical check.
        for bad in [None, "", "   ", "none", "todo", "null", "tbd",
                    123, [], {}, True]:
            self.assertFalse(is_valid_next_action(bad))
        for good in ["poll CI", "PHASE_5", "reconcile threads", "wait"]:
            self.assertTrue(is_valid_next_action(good))


class NextActionFromCheckpointNeverReturnsNonStringTests(unittest.TestCase):
    """Fix D: ``next_action_from_checkpoint`` must never
    return a non-string. Defensive: even with an invalid
    next_action, the function returns the literal string
    ``"HOLD_OPERATOR_REQUIRED"`` rather than passing the
    invalid value to the runner."""

    def _ck(self, next_action):
        return CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action=next_action,
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )

    def test_invalid_next_action_returns_hold_string(self) -> None:
        for bad in ["", "   ", "none", "todo", "tbd"]:
            result = next_action_from_checkpoint(self._ck(bad))
            self.assertIsInstance(result, str)
            self.assertEqual(result, "HOLD_OPERATOR_REQUIRED")

    def test_valid_next_action_returned_verbatim(self) -> None:
        result = next_action_from_checkpoint(self._ck("poll CI status"))
        self.assertIsInstance(result, str)
        self.assertEqual(result, "poll CI status")


# ---------------------------------------------------------------------------
# Section 21: Reanchored active findings — pinned regression coverage
# ---------------------------------------------------------------------------


class ReanchoredFindingsExtendedCoverageTests(unittest.TestCase):

    def test_canonical_registry_covers_schema_hold_and_terminal(self) -> None:
        import json
        from pathlib import Path
        schema_path = (
            Path(__file__).resolve().parent.parent
            / "schemas"
            / "aed_lifecycle_states_v1.json"
        )
        schema = json.loads(schema_path.read_text())
        canonical = set(schema.get("states", {}).keys())
        # Every canonical hold/terminal state must be in the registry.
        from aed_lifecycle.no_stall import TERMINAL_LIFECYCLE_STATES
        for state_name, state_def in schema.get("states", {}).items():
            if state_def.get("category") in {"hold", "terminal"}:
                self.assertIn(
                    state_name,
                    TERMINAL_LIFECYCLE_STATES,
                    f"{state_name} missing from TERMINAL_LIFECYCLE_STATES",
                )

    def test_watchdog_requires_both_checkpoint_and_next_action(self) -> None:
        # With checkpoint_path but no next_action, must be STALL_RISK.
        state = WatchdogState(
            phase_name="PHASE_X",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10000.0,
            max_phase_seconds=1800.0,
            next_action=None,
            checkpoint_path="/tmp/ckpt.json",
            terminal_state=None,
        )
        self.assertEqual(evaluate_watchdog(state, now=100.0), STALL_RISK)

    def test_optional_checkpoint_fields_may_be_none(self) -> None:
        # Documented Optional fields may be None.
        ck = CheckpointState(
            repo="r",
            pr_number=1,
            branch="b",
            current_head="a" * 40,
            phase=None,
            completed_phases=[],
            next_phase=None,
            next_action=None,
            pending_actions=[],
            last_verified_primary_head=None,
            last_verified_pr_head=None,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )
        errors = validate_checkpoint(ck)
        # Should not fail on the Optional None fields. (It may
        # still fail with "stale" or similar — but no field is
        # rejected for being None.)
        for e in errors:
            self.assertNotIn("must not be None", e)

    def test_next_action_marker_without_value_not_progress(self) -> None:
        text = "checkpoint: /tmp/ckpt.json\nnext_action:"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_ci_matching_token_safe(self) -> None:
        state = WatchdogState(
            phase_name="PHASE_X",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10000.0,
            max_phase_seconds=1800.0,
            next_action="decide whether to merge",
            checkpoint_path="/tmp/ckpt.json",
            terminal_state=None,
        )
        self.assertNotEqual(
            evaluate_watchdog(state, now=10000.0),
            "HOLD_PR_CI_PENDING",
        )

    def test_next_action_validation_shared_across_helpers(self) -> None:
        # The same canonical helper must back all three helpers.
        for bad in ["", "   ", "none", "todo"]:
            ck = CheckpointState(
                repo="r",
                pr_number=1,
                branch="b",
                current_head="a" * 40,
                phase="PHASE_1",
                completed_phases=[],
                next_phase="PHASE_2",
                next_action=bad,
                pending_actions=[],
                last_verified_primary_head="0" * 40,
                last_verified_pr_head="a" * 40,
                authorized_thread_ids=[],
                unresolved_thread_ids=[],
                terminal_state=None,
                updated_at=None,
            )
            # validate_checkpoint flags it
            self.assertTrue(validate_checkpoint(ck))
            # checkpoint_requires_operator returns True
            self.assertTrue(checkpoint_requires_operator(ck))
            # next_action_from_checkpoint returns the hold string
            self.assertEqual(
                next_action_from_checkpoint(ck), "HOLD_OPERATOR_REQUIRED"
            )


# ---------------------------------------------------------------------------
# Section 22: Fix A — strict hold/terminal registry coverage
#             (Codex 3415107647)
# ---------------------------------------------------------------------------


class StrictHoldTerminalRegistryCoverageTests(unittest.TestCase):
    """Fix A: the terminal/parked registry is the union of
    (a) every ``category=hold`` and ``category=terminal``
    schema state and (b) the spec-required extras
    (``MERGED``, ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``,
    ``FAILED``). States with other categories
    (``informational``, ``mutation_pending``, ``ready``) are
    NOT terminal/parked and must NOT be in the registry.
    """

    def test_every_schema_hold_is_terminal(self) -> None:
        from aed_lifecycle.no_stall import (
            TERMINAL_LIFECYCLE_STATES,
            _schema_terminal_or_parked_states,
        )
        schema_terminal = _schema_terminal_or_parked_states()
        for state in schema_terminal:
            self.assertIn(
                state,
                TERMINAL_LIFECYCLE_STATES,
                f"schema terminal {state!r} missing from "
                "TERMINAL_LIFECYCLE_STATES",
            )

    def test_every_schema_non_terminal_is_not_terminal(self) -> None:
        from aed_lifecycle.no_stall import (
            TERMINAL_LIFECYCLE_STATES,
            _schema_non_terminal_states,
        )
        schema_non_terminal = _schema_non_terminal_states()
        # Spec-required extras are allowed even though they
        # are not in the schema.
        spec_extras = {"MERGED", "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
                       "FAILED"}
        for state in schema_non_terminal:
            if state in spec_extras:
                continue
            self.assertNotIn(
                state,
                TERMINAL_LIFECYCLE_STATES,
                f"schema non-terminal {state!r} is incorrectly in "
                "TERMINAL_LIFECYCLE_STATES",
            )

    def test_codex_clean_pass_resolve_only_needed_not_terminal(self) -> None:
        # If present in the schema, must NOT be terminal.
        from aed_lifecycle.no_stall import is_terminal_lifecycle_state
        # This is informational / mutation_pending per the
        # schema. Even if the agent emits a final report
        # containing this name, the classifier must NOT treat
        # it as a terminal/parked state.
        self.assertFalse(
            is_terminal_lifecycle_state("CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED")
        )

    def test_codex_clean_pass_not_terminal(self) -> None:
        from aed_lifecycle.no_stall import is_terminal_lifecycle_state
        self.assertFalse(is_terminal_lifecycle_state("CODEX_CLEAN_PASS"))

    def test_pr_merged_pending_closeout_not_terminal(self) -> None:
        from aed_lifecycle.no_stall import is_terminal_lifecycle_state
        self.assertFalse(
            is_terminal_lifecycle_state("PR_MERGED_PENDING_CLOSEOUT")
        )

    def test_not_run_not_terminal(self) -> None:
        from aed_lifecycle.no_stall import is_terminal_lifecycle_state
        self.assertFalse(is_terminal_lifecycle_state("NOT_RUN"))

    def test_classifier_does_not_classify_codex_clean_pass_as_terminal(
        self,
    ) -> None:
        # Even an explicit-prefix assertion on
        # CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED must not
        # classify as OK_TERMINAL — the state is not in the
        # registry, so is_terminal_lifecycle_state returns
        # False, and the explicit-assertion path rejects it.
        text = (
            "Final lifecycle state: CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_TERMINAL)


# ---------------------------------------------------------------------------
# Section 23: Fix B — canonical next_action extractor (Codex 3415107653)
# ---------------------------------------------------------------------------


class CanonicalNextActionExtractorTests(unittest.TestCase):
    """Fix B: the ``next_action`` extractor must not consume
    past the marker's line boundary. A marker on one line
    followed by a different field on the next line is
    EMPTY, even if the next field name is well-formed.
    """

    def test_next_action_then_checkpoint_field_is_empty(self) -> None:
        text = "next_action:\ncheckpoint: /tmp/ckpt.json"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_action_none_then_checkpoint_is_empty(self) -> None:
        text = "next_action: none\ncheckpoint: /tmp/ckpt.json"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_action_todo_then_checkpoint_is_empty(self) -> None:
        text = "next_action: todo\ncheckpoint: /tmp/ckpt.json"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_action_null_is_empty(self) -> None:
        text = "next_action: null"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_action_na_is_empty(self) -> None:
        text = "next_action: n/a"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_action_checkpoint_field_collision_is_empty(self) -> None:
        # "checkpoint: /tmp/ckpt.json" is the value, not a
        # next_action. A naive extractor would consume the
        # next line and treat "checkpoint" as the value.
        text = "next_action: checkpoint: /tmp/ckpt.json"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_valid_next_action_with_checkpoint_is_progress(self) -> None:
        # All three required elements:
        # phase header + valid next_action + checkpoint
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: continue bounded CI polling\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_valid_next_action_poll_codex_is_progress(self) -> None:
        text = (
            "Starting PHASE 5 — Codex review.\n"
            "next_action: poll Codex response\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_valid_next_action_resume_from_checkpoint_is_progress(self) -> None:
        text = (
            "Now PHASE 3 — resume from checkpoint.\n"
            "next_action: resume from checkpoint\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_extractor_returns_actual_string_value(self) -> None:
        from aed_lifecycle.no_stall import _extract_next_action_value

        # Marker followed by a real value on the same line.
        self.assertEqual(
            _extract_next_action_value("next_action: poll Codex response"),
            "poll",
        )
        # Marker followed by nothing on the same line
        # (the next line is a different field). The
        # extractor must NOT walk past the newline.
        self.assertIsNone(
            _extract_next_action_value(
                "next_action:\ncheckpoint: /tmp/ckpt.json"
            )
        )
        # No marker at all.
        self.assertIsNone(_extract_next_action_value("checkpoint: /tmp/x"))


# ---------------------------------------------------------------------------
# Section 24: Fix C — validate watchdog next_action before OK progress
#             (Codex 3415107657)
# ---------------------------------------------------------------------------


class WatchdogValidatesNextActionTests(unittest.TestCase):
    """Fix C: ``evaluate_watchdog`` must use the canonical
    ``is_valid_next_action`` helper. A placeholder
    ``next_action`` (``"none"``, ``"todo"``, etc.) with a
    checkpoint path must produce :data:`STALL_RISK`, not
    :data:`OK_PROGRESS_WITH_NEXT_ACTION`.
    """

    def _state(self, next_action):
        return WatchdogState(
            phase_name="PHASE_X",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10000.0,
            max_phase_seconds=1800.0,
            next_action=next_action,
            checkpoint_path="/tmp/ckpt.json",
            terminal_state=None,
        )

    def test_next_action_none_is_not_ok_progress(self) -> None:
        verdict = evaluate_watchdog(self._state(None), now=100.0)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertEqual(verdict, STALL_RISK)

    def test_next_action_empty_is_not_ok_progress(self) -> None:
        verdict = evaluate_watchdog(self._state(""), now=100.0)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertEqual(verdict, STALL_RISK)

    def test_next_action_whitespace_is_not_ok_progress(self) -> None:
        verdict = evaluate_watchdog(self._state("   "), now=100.0)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertEqual(verdict, STALL_RISK)

    def test_next_action_none_placeholder_is_not_ok_progress(self) -> None:
        verdict = evaluate_watchdog(self._state("none"), now=100.0)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertEqual(verdict, STALL_RISK)

    def test_next_action_todo_placeholder_is_not_ok_progress(self) -> None:
        verdict = evaluate_watchdog(self._state("todo"), now=100.0)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertEqual(verdict, STALL_RISK)

    def test_next_action_null_placeholder_is_not_ok_progress(self) -> None:
        verdict = evaluate_watchdog(self._state("null"), now=100.0)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertEqual(verdict, STALL_RISK)

    def test_valid_next_action_with_checkpoint_is_ok_progress(self) -> None:
        verdict = evaluate_watchdog(
            self._state("poll CI status"), now=100.0
        )
        self.assertEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_checkpoint_path_without_valid_next_action_is_not_ok_progress(
        self,
    ) -> None:
        verdict = evaluate_watchdog(self._state(None), now=100.0)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertEqual(verdict, STALL_RISK)


# ---------------------------------------------------------------------------
# Section 25: Fix D — checkpoint_requires_operator for absent next_action
#             (Codex 3415107663)
# ---------------------------------------------------------------------------


class CheckpointRequiresOperatorAbsentNextActionTests(unittest.TestCase):
    """Fix D: ``checkpoint_requires_operator`` must return
    True for ``next_action=None`` with a populated
    ``phase`` (e.g. ``"PHASE_5_CI_POLL"``), not just when
    both phase and next_action are absent. Only a valid
    non-placeholder string next_action AND an otherwise
    structurally valid checkpoint returns False.
    """

    def _ck(self, next_action, phase="PHASE_5_CI_POLL"):
        return CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase=phase,
            completed_phases=[],
            next_phase="PHASE_6",
            next_action=next_action,
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )

    def test_none_with_phase_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck(None)))

    def test_none_with_phase_5_ci_poll_requires_operator(self) -> None:
        # The exact case from Codex 3415107663.
        self.assertTrue(
            checkpoint_requires_operator(self._ck(None, "PHASE_5_CI_POLL"))
        )

    def test_empty_string_requires_operator(self) -> None:
        self.assertTrue(checkpoint_requires_operator(self._ck("")))

    def test_placeholder_requires_operator(self) -> None:
        for bad in ["none", "todo", "null", "n/a"]:
            self.assertTrue(checkpoint_requires_operator(self._ck(bad)))

    def test_valid_string_does_not_require_operator(self) -> None:
        self.assertFalse(
            checkpoint_requires_operator(self._ck("poll CI status"))
        )

    def test_next_action_from_checkpoint_returns_hold_for_none(self) -> None:
        # next_action_from_checkpoint must return
        # HOLD_OPERATOR_REQUIRED for None, empty, and
        # placeholder values.
        for bad in [None, "", "   ", "none", "todo"]:
            result = next_action_from_checkpoint(self._ck(bad))
            self.assertIsInstance(result, str)
            self.assertEqual(result, "HOLD_OPERATOR_REQUIRED")


# ---------------------------------------------------------------------------
# Section 26: Fix 3415335299 — skip head-drift checks for terminal checkpoints
# ---------------------------------------------------------------------------


class TerminalCheckpointSkipsHeadDriftTests(unittest.TestCase):
    """Codex 3415335299: a terminal checkpoint persisted
    without ``last_verified_*_head`` fields must NOT
    surface ``HOLD_HEAD_CHANGED``. The runner stops on
    the terminal state, and the recorded-head-missing
    check is skipped for terminal checkpoints.
    """

    def _terminal_ck(
        self,
        terminal_state="MERGED",
        last_verified_pr_head=None,
        last_verified_primary_head=None,
    ):
        return CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_8",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3"],
            next_phase=None,
            next_action=None,
            pending_actions=[],
            # Optional fields default to None (terminal
            # checkpoint without recorded heads). Tests that
            # exercise a parked/hold terminal state's
            # observed-head check pass matching recorded
            # heads via the helper.
            last_verified_primary_head=last_verified_primary_head,
            last_verified_pr_head=last_verified_pr_head,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=terminal_state,
            updated_at="2026-06-15T17:30:00Z",
        )

    def test_terminal_merged_skips_head_drift_check(self) -> None:
        # A terminal checkpoint with no recorded heads
        # must NOT produce recorded-head-missing errors.
        errors = validate_resume_observations(
            self._terminal_ck("MERGED"),
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertEqual(errors, [])

    def test_terminal_merge_ready_skips_head_drift_check(self) -> None:
        # Fix G (Codex 3417849218): parked/hold terminal
        # states go through observed-head checks. With
        # recorded heads that match the observations, the
        # parked terminal state is still ok — no head-drift
        # errors. The terminal-state verdict remains
        # authoritative (the operator must acknowledge the
        # hold or authorize the closeout), but the head
        # checks produce no error when the heads match.
        errors = validate_resume_observations(
            self._terminal_ck(
                "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
                last_verified_pr_head="a" * 40,
                last_verified_primary_head="0" * 40,
            ),
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertEqual(errors, [])

    def test_terminal_hold_operator_required_skips_head_drift(self) -> None:
        # Same as above for ``HOLD_OPERATOR_REQUIRED``: the
        # parked terminal state goes through head checks, and
        # with matching recorded heads there is no head-drift
        # error.
        errors = validate_resume_observations(
            self._terminal_ck(
                "HOLD_OPERATOR_REQUIRED",
                last_verified_pr_head="a" * 40,
                last_verified_primary_head="0" * 40,
            ),
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertEqual(errors, [])

    def test_parked_terminal_with_moved_pr_head_errors(self) -> None:
        # Fix G (Codex 3417849218): a parked terminal state
        # such as ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``
        # with a moved observed PR head must surface
        # ``HOLD_HEAD_CHANGED`` (or the existing head-changed
        # error path). The runner must not surface a stale
        # "merge ready / awaiting authorization" verdict
        # when the PR head has moved.
        errors = validate_resume_observations(
            self._terminal_ck(
                "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
                last_verified_pr_head="a" * 40,
                last_verified_primary_head="0" * 40,
            ),
            observed_pr_head="b" * 40,  # PR head moved
            observed_primary_head="0" * 40,
        )
        self.assertTrue(
            any("PR head" in e for e in errors),
            f"expected PR head drift error, got {errors}",
        )

    def test_parked_terminal_with_moved_primary_head_errors(self) -> None:
        # Same as above for the primary head.
        errors = validate_resume_observations(
            self._terminal_ck(
                "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
                last_verified_pr_head="a" * 40,
                last_verified_primary_head="0" * 40,
            ),
            observed_pr_head="a" * 40,
            observed_primary_head="1" * 40,  # primary moved
        )
        self.assertTrue(
            any("primary" in e.lower() for e in errors),
            f"expected primary head drift error, got {errors}",
        )

    def test_hold_new_codex_thread_with_moved_pr_head_errors(self) -> None:
        # ``HOLD_NEW_CODEX_THREAD`` is a parked hold state.
        # A moved PR head must surface ``HOLD_HEAD_CHANGED``.
        errors = validate_resume_observations(
            self._terminal_ck(
                "HOLD_NEW_CODEX_THREAD",
                last_verified_pr_head="a" * 40,
                last_verified_primary_head="0" * 40,
            ),
            observed_pr_head="b" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertTrue(
            any("PR head" in e for e in errors),
            f"expected PR head drift error, got {errors}",
        )

    def test_hold_pr_ci_pending_with_moved_primary_head_errors(self) -> None:
        # ``HOLD_PR_CI_PENDING`` is a parked hold state. A
        # moved primary head must surface
        # ``HOLD_HEAD_CHANGED``.
        errors = validate_resume_observations(
            self._terminal_ck(
                "HOLD_PR_CI_PENDING",
                last_verified_pr_head="a" * 40,
                last_verified_primary_head="0" * 40,
            ),
            observed_pr_head="a" * 40,
            observed_primary_head="1" * 40,
        )
        self.assertTrue(
            any("primary" in e.lower() for e in errors),
            f"expected primary head drift error, got {errors}",
        )

    def test_completed_terminal_skips_head_drift_even_when_heads_moved(
        self,
    ) -> None:
        # Completed terminal states (``MERGED``, ``FAILED``,
        # ``PR_MERGED_AND_CLOSED_OUT``) skip head-drift
        # checks. The runner stops on the completed terminal
        # state and the operator has already authorized the
        # closeout. A moved head must NOT surface
        # ``HOLD_HEAD_CHANGED`` for a completed terminal.
        errors = validate_resume_observations(
            self._terminal_ck(
                "MERGED",
                last_verified_pr_head="a" * 40,
                last_verified_primary_head="0" * 40,
            ),
            observed_pr_head="b" * 40,  # PR head moved
            observed_primary_head="1" * 40,  # primary moved
        )
        self.assertEqual(errors, [])

    def test_terminal_failed_skips_head_drift(self) -> None:
        errors = validate_resume_observations(
            self._terminal_ck("FAILED"),
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertEqual(errors, [])

    def test_non_terminal_still_flags_missing_recorded_head(self) -> None:
        # A non-terminal checkpoint with a missing recorded
        # head must STILL surface the recorded-head-missing
        # error. The terminal-state short-circuit does not
        # apply here.
        ck = CheckpointState(
            repo="r",
            pr_number=1,
            branch="b",
            current_head="a" * 40,
            phase="PHASE_5",
            completed_phases=[],
            next_phase="PHASE_6",
            next_action="poll CI",
            pending_actions=[],
            last_verified_primary_head=None,
            last_verified_pr_head=None,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )
        errors = validate_resume_observations(
            ck, observed_pr_head="a" * 40, observed_primary_head="0" * 40
        )
        self.assertTrue(
            any("recorded PR head missing" in e for e in errors)
        )
        self.assertTrue(
            any("recorded primary head missing" in e for e in errors)
        )

    def test_unknown_terminal_state_surfaces_hold(self) -> None:
        # An unrecognized terminal state must surface as
        # "unknown terminal state" so the runner can report
        # HOLD_OPERATOR_REQUIRED — NOT a recorded-head error.
        errors = validate_resume_observations(
            self._terminal_ck("NOT_A_REAL_STATE"),
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        # Should NOT contain recorded-head-missing errors.
        self.assertFalse(
            any("recorded PR head missing" in e for e in errors)
        )
        self.assertFalse(
            any("recorded primary head missing" in e for e in errors)
        )
        # Should contain the unknown-terminal error.
        self.assertTrue(
            any("unknown terminal_state" in e for e in errors)
        )

    def test_terminal_resume_after_phase_1_succeeds(self) -> None:
        # The terminal-state short-circuit must apply
        # regardless of what phase is recorded. A
        # PHASE_1-completed terminal checkpoint must
        # resume without head-drift errors.
        ck = CheckpointState(
            repo="r",
            pr_number=1,
            branch="b",
            current_head="a" * 40,
            phase="PHASE_1",
            completed_phases=[],
            next_phase="PHASE_2",
            next_action=None,
            pending_actions=[],
            last_verified_primary_head=None,
            last_verified_pr_head=None,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state="MERGED",
            updated_at=None,
        )
        errors = validate_resume_observations(
            ck, observed_pr_head="a" * 40, observed_primary_head="0" * 40
        )
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Section 27: Fix A — completed terminal checkpoints do not require operator
#             (Codex 3415657744)
# ---------------------------------------------------------------------------


class CompletedTerminalDoesNotRequireOperatorTests(unittest.TestCase):
    """Codex 3415657744: ``checkpoint_requires_operator``
    must return False for a recognized COMPLETED terminal
    state (e.g. ``MERGED``, ``FAILED``,
    ``PR_MERGED_AND_CLOSED_OUT``) BEFORE the next-action
    validity check. Parked/hold terminal states
    (``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``,
    ``HOLD_OPERATOR_REQUIRED``, all ``HOLD_*`` schema
    states) still require operator attention.
    """

    def _ck(self, terminal_state, next_action=None, phase="PHASE_8"):
        return CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase=phase,
            completed_phases=[],
            next_phase=None,
            next_action=next_action,
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=terminal_state,
            updated_at=None,
        )

    def test_merged_does_not_require_operator(self) -> None:
        # Spec: completed terminal extras are not in schema
        # but are still completed.
        self.assertFalse(
            checkpoint_requires_operator(self._ck("MERGED"))
        )

    def test_failed_does_not_require_operator(self) -> None:
        # Spec: FAILED is a completed extra.
        self.assertFalse(
            checkpoint_requires_operator(self._ck("FAILED"))
        )

    def test_pr_merged_and_closed_out_does_not_require_operator(self) -> None:
        # Canonical schema category=terminal state.
        self.assertFalse(
            checkpoint_requires_operator(self._ck("PR_MERGED_AND_CLOSED_OUT"))
        )

    def test_hold_operator_required_requires_operator(self) -> None:
        # Parked/hold state: runner must surface to operator.
        self.assertTrue(
            checkpoint_requires_operator(self._ck("HOLD_OPERATOR_REQUIRED"))
        )

    def test_merge_ready_awaiting_human_authorization_requires_operator(
        self,
    ) -> None:
        # Parked, awaiting-human. NOT a completed state.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck("MERGE_READY_AWAITING_HUMAN_AUTHORIZATION")
            )
        )

    def test_unknown_terminal_state_requires_operator(self) -> None:
        # An unrecognized terminal state is a hold.
        self.assertTrue(
            checkpoint_requires_operator(self._ck("NOT_A_REAL_STATE"))
        )

    def test_non_terminal_with_no_next_action_requires_operator(self) -> None:
        # Non-terminal with absent next_action: the
        # runner cannot auto-resume. The original
        # behavior (Fix D, Codex 3415107663) still
        # applies for non-terminal checkpoints.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(terminal_state=None, next_action=None)
            )
        )

    def test_non_terminal_with_valid_next_action_does_not_require_operator(
        self,
    ) -> None:
        self.assertFalse(
            checkpoint_requires_operator(
                self._ck(terminal_state=None, next_action="poll CI")
            )
        )

    def test_is_completed_terminal_state_helper(self) -> None:
        from aed_lifecycle.no_stall import is_completed_terminal_state
        # Completed
        self.assertTrue(is_completed_terminal_state("MERGED"))
        self.assertTrue(is_completed_terminal_state("FAILED"))
        self.assertTrue(
            is_completed_terminal_state("PR_MERGED_AND_CLOSED_OUT")
        )
        # NOT completed
        self.assertFalse(
            is_completed_terminal_state("MERGE_READY_AWAITING_HUMAN_AUTHORIZATION")
        )
        self.assertFalse(is_completed_terminal_state("HOLD_OPERATOR_REQUIRED"))
        self.assertFalse(is_completed_terminal_state("HOLD_PR_CI_PENDING"))
        # Non-string / empty / None
        self.assertFalse(is_completed_terminal_state(None))
        self.assertFalse(is_completed_terminal_state(""))
        self.assertFalse(is_completed_terminal_state(123))


# ---------------------------------------------------------------------------
# Section 28: Fix B — terminal_state final-output assertions
#             (Codex 3415657751)
# ---------------------------------------------------------------------------


class TerminalStateFinalOutputAssertionTests(unittest.TestCase):
    """Codex 3415657751: the classifier must recognize
    ``terminal_state: <STATE>`` and ``terminal_state=<STATE>``
    (with optional whitespace around ``=``) as explicit
    terminal-state assertions, so a real done signal like
    ``terminal_state: MERGED`` or
    ``terminal_state=HOLD_PR_CI_PENDING`` classifies as
    ``OK_TERMINAL`` rather than a ``STALL_*`` result.
    Placeholder values, missing values, and non-terminal
    states are still rejected.
    """

    def test_terminal_state_colon_merged_is_terminal(self) -> None:
        self.assertEqual(
            classify_humphry_message_for_stall("terminal_state: MERGED"),
            OK_TERMINAL,
        )

    def test_terminal_state_equals_hold_pr_ci_pending_is_terminal(self) -> None:
        self.assertEqual(
            classify_humphry_message_for_stall(
                "terminal_state=HOLD_PR_CI_PENDING"
            ),
            OK_TERMINAL,
        )

    def test_terminal_state_spaced_equals_with_explanation(self) -> None:
        self.assertEqual(
            classify_humphry_message_for_stall(
                "terminal_state = HOLD_NEW_CODEX_THREAD — "
                "waiting for operator"
            ),
            OK_TERMINAL,
        )

    def test_terminal_state_colon_empty_not_terminal(self) -> None:
        self.assertNotEqual(
            classify_humphry_message_for_stall("terminal_state:"),
            OK_TERMINAL,
        )

    def test_terminal_state_colon_none_not_terminal(self) -> None:
        self.assertNotEqual(
            classify_humphry_message_for_stall("terminal_state: none"),
            OK_TERMINAL,
        )

    def test_terminal_state_colon_todo_not_terminal(self) -> None:
        self.assertNotEqual(
            classify_humphry_message_for_stall("terminal_state: todo"),
            OK_TERMINAL,
        )

    def test_terminal_state_equals_codex_clean_pass_not_terminal(self) -> None:
        # CODEX_CLEAN_PASS is informational / non-terminal.
        self.assertNotEqual(
            classify_humphry_message_for_stall(
                "terminal_state=CODEX_CLEAN_PASS"
            ),
            OK_TERMINAL,
        )

    def test_terminal_state_equals_pr_merged_pending_closeout_not_terminal(
        self,
    ) -> None:
        # PR_MERGED_PENDING_CLOSEOUT is mutation_pending /
        # non-terminal.
        self.assertNotEqual(
            classify_humphry_message_for_stall(
                "terminal_state=PR_MERGED_PENDING_CLOSEOUT"
            ),
            OK_TERMINAL,
        )

    def test_existing_final_lifecycle_state_still_works(self) -> None:
        # Regression: previous prefixes still work.
        self.assertEqual(
            classify_humphry_message_for_stall(
                "Final lifecycle state: HOLD_NEW_CODEX_THREAD"
            ),
            OK_TERMINAL,
        )

    def test_existing_terminal_state_colon_still_works(self) -> None:
        # Regression: the original Terminal state: prefix.
        self.assertEqual(
            classify_humphry_message_for_stall(
                "Terminal state: HOLD_PR_CI_PENDING"
            ),
            OK_TERMINAL,
        )

    def test_exact_state_alone_still_works(self) -> None:
        # Regression: bare state on a line by itself.
        self.assertEqual(
            classify_humphry_message_for_stall("MERGED"),
            OK_TERMINAL,
        )


# ---------------------------------------------------------------------------
# Section 29: Fix 3415785873 — parked terminal states with non-empty
#             next_action still require operator intervention
# ---------------------------------------------------------------------------


class ParkedTerminalRequiresOperatorTests(unittest.TestCase):
    """Codex 3415785873: ``checkpoint_requires_operator``
    must return True for a recognized but NON-COMPLETED
    terminal/parked state, even when ``next_action`` is
    non-empty. A checkpoint like
    ``terminal_state="HOLD_OPERATOR_REQUIRED"`` with
    ``next_action="poll CI"`` must require operator
    intervention — the runner stops on the terminal
    state, but the operator must acknowledge the hold
    or authorize the closeout.
    """

    def _ck(self, terminal_state, next_action=None):
        return CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=[],
            next_phase="PHASE_6",
            next_action=next_action,
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=terminal_state,
            updated_at=None,
        )

    def test_hold_operator_required_with_next_action_requires_operator(
        self,
    ) -> None:
        # The exact case from Codex 3415785873: a parked
        # terminal state with a non-empty next_action.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck("HOLD_OPERATOR_REQUIRED", "poll CI")
            )
        )

    def test_merge_ready_with_next_action_requires_operator(self) -> None:
        # Parked, awaiting-human. Not completed. Even with
        # a non-empty next_action, the operator must
        # acknowledge the await.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(
                    "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION", "poll CI"
                )
            )
        )

    def test_hold_pr_ci_pending_with_next_action_requires_operator(
        self,
    ) -> None:
        # Hold schema state with a non-empty next_action.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck("HOLD_PR_CI_PENDING", "poll CI")
            )
        )

    def test_hold_operator_required_with_no_next_action_requires_operator(
        self,
    ) -> None:
        # Parked terminal state with no next_action: also
        # requires operator. (Pre-existing behavior.)
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck("HOLD_OPERATOR_REQUIRED", None)
            )
        )

    def test_completed_merged_with_next_action_does_not_require_operator(
        self,
    ) -> None:
        # Completed state: even with a stale next_action
        # left over from a previous phase, the runner
        # stops on the terminal state and the operator
        # is NOT required to intervene. The completed
        # short-circuit fires BEFORE the parked check.
        self.assertFalse(
            checkpoint_requires_operator(
                self._ck("MERGED", "poll CI")
            )
        )


class ContinueScanningNextActionTests(unittest.TestCase):
    """Regression tests for Codex 3415861210.

    The next_action extractor must scan ALL ``next_action:``
    markers in a message, not just the first one. A runner
    that emits a placeholder (``next_action: none``) and
    then a real action (``next_action: poll CI status``)
    must be recognized as having a valid next action, not
    rejected because the first marker was a placeholder.
    """

    @staticmethod
    def _classify(message: str) -> str:
        return classify_humphry_message_for_stall(message)

    def test_placeholder_then_real_action_with_checkpoint_is_progress(
        self,
    ) -> None:
        msg = (
            "next_action: none\n"
            "next_action: poll CI status\n"
            "checkpoint: /tmp/ckpt.json"
        )
        # Real action is present, checkpoint is present.
        # The placeholder must be skipped, not short-circuit.
        self.assertEqual(self._classify(msg), OK_PROGRESS_WITH_NEXT_ACTION)

    def test_todo_placeholder_then_resume_with_checkpoint_is_progress(
        self,
    ) -> None:
        msg = (
            "next_action: todo\n"
            "next_action: resume from checkpoint\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(self._classify(msg), OK_PROGRESS_WITH_NEXT_ACTION)

    def test_empty_marker_then_poll_codex_with_checkpoint_is_progress(
        self,
    ) -> None:
        msg = (
            "next_action:\n"
            "next_action: poll Codex response\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(self._classify(msg), OK_PROGRESS_WITH_NEXT_ACTION)

    def test_placeholder_only_with_checkpoint_is_not_valid(self) -> None:
        msg = (
            "next_action: none\n"
            "checkpoint: /tmp/ckpt.json"
        )
        # No real action; the placeholder does not satisfy
        # the next_action contract.
        self.assertNotEqual(
            self._classify(msg), OK_PROGRESS_WITH_NEXT_ACTION
        )

    def test_todo_placeholder_only_with_checkpoint_is_not_valid(self) -> None:
        msg = (
            "next_action: todo\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertNotEqual(
            self._classify(msg), OK_PROGRESS_WITH_NEXT_ACTION
        )

    def test_empty_marker_only_with_checkpoint_is_not_valid(self) -> None:
        msg = (
            "next_action:\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertNotEqual(
            self._classify(msg), OK_PROGRESS_WITH_NEXT_ACTION
        )

    def test_extractor_returns_real_action_not_checkpoint(self) -> None:
        from aed_lifecycle.no_stall import _extract_next_action_value
        msg = (
            "next_action: none\n"
            "next_action: poll CI status\n"
            "checkpoint: /tmp/ckpt.json"
        )
        value = _extract_next_action_value(msg)
        # The extractor must return the real action, not the
        # checkpoint field name and not the placeholder.
        self.assertEqual(value, "poll")
        # And it must not include the checkpoint text.
        self.assertNotIn("checkpoint", (value or ""))
        self.assertNotIn("ckpt.json", (value or ""))

    def test_extractor_returns_first_valid_marker_only(self) -> None:
        from aed_lifecycle.no_stall import _extract_next_action_value
        msg = (
            "next_action: none\n"
            "next_action: todo\n"
            "next_action: resume from checkpoint\n"
            "checkpoint: /tmp/ckpt.json"
        )
        # Should return the first VALID token, which is
        # "resume". Placeholders "none" and "todo" must be
        # skipped.
        self.assertEqual(_extract_next_action_value(msg), "resume")

    def test_extractor_returns_none_when_only_placeholders(self) -> None:
        from aed_lifecycle.no_stall import _extract_next_action_value
        msg = (
            "next_action: none\n"
            "next_action: todo\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertIsNone(_extract_next_action_value(msg))


class PostMergeCIRecommendHoldTests(unittest.TestCase):
    """Regression tests for Codex 3415861213.

    When the watchdog times out (phase-time exhausted) and
    the next_action or phase_name clearly indicates a
    post-merge / main-CI closeout audit, the recommended
    hold must be ``HOLD_POST_MERGE_CI_PENDING`` (or its
    failed / not-observed variant) — NOT the pre-merge
    ``HOLD_PR_CI_PENDING``. The latter is the wrong
    recovery path for a runner that is auditing post-merge
    main CI.
    """

    @staticmethod
    def _exhausted_state(
        phase_name: str,
        next_action: str,
    ) -> "WatchdogState":
        from aed_lifecycle.watchdog import WatchdogState
        return WatchdogState(
            phase_name=phase_name,
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10.0,
            max_phase_seconds=10.0,
            next_action=next_action,
            checkpoint_path="/tmp/ckpt.json",
        )

    def test_phase_name_post_merge_returns_post_merge_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="post-merge CI",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_next_action_poll_remote_main_ci_returns_post_merge_hold(
        self,
    ) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="poll remote main CI",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_next_action_audit_post_merge_main_ci_returns_post_merge_hold(
        self,
    ) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="audit post-merge main CI",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_phase_name_closeout_returns_post_merge_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="closeout audit",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_pr_ci_phrase_remains_pre_merge(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_6",
            next_action="poll PR CI",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_wait_for_required_checks_remains_pre_merge(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_6",
            next_action="wait for required checks",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_poll_test_3_11_remains_pre_merge(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_6",
            next_action="poll test (3.11)",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_check_github_actions_for_pr_remains_pre_merge(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_6",
            next_action="check github actions for PR",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_generic_check_docs_is_not_a_ci_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_OPERATOR_REQUIRED,
            evaluate_watchdog,
        )
        # "check docs" has no CI signal — the CI token
        # pattern (with \b) does not match it. The
        # post-merge detector also does not match. So the
        # fallback HOLD_OPERATOR_REQUIRED must be returned.
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="check docs",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_OPERATOR_REQUIRED,
        )

    def test_reconcile_threads_is_not_a_ci_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_OPERATOR_REQUIRED,
            evaluate_watchdog,
        )
        # "reconcile threads" must not match the CI token
        # pattern ("reconcile" contains "ci" but the \b
        # boundary prevents a match). And there is no
        # post-merge / main-CI signal. Fallback hold.
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="reconcile threads",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_OPERATOR_REQUIRED,
        )

    def test_decide_whether_to_merge_is_not_a_ci_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_OPERATOR_REQUIRED,
            evaluate_watchdog,
        )
        # "decide whether to merge" must not match CI.
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="decide whether to merge",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_OPERATOR_REQUIRED,
        )

    def test_run_checks_is_not_a_ci_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_OPERATOR_REQUIRED,
            evaluate_watchdog,
        )
        # "run checks" alone is not CI without a context
        # word. The CI token pattern requires explicit
        # CI-context tokens, not bare "checks".
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="run checks",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_OPERATOR_REQUIRED,
        )

    def test_phase_name_post_merge_with_codex_token_returns_codex_hold(
        self,
    ) -> None:
        # When the next action is about Codex (not CI),
        # the post-merge detector must NOT promote it to a
        # CI hold. The Codex detector still fires first.
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="post-merge Codex re-review",
            next_action="poll codex",
        )
        # Codex has no CI signal in next_action, so the
        # Codex detector returns HOLD_CODEX_RESPONSE_PENDING.
        # The post-merge / main-CI detector is irrelevant
        # here because no CI token is present.
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )


class IdentifierStylePostMergePhaseTests(unittest.TestCase):
    """Regression tests for Codex 3416424655.

    The post-merge / main-CI closeout detector must recognize
    identifier-style (snake_case) phase names AND next_action
    strings, not just prose forms. The detector uses an
    identifier-aware token boundary class
    (``(?<![A-Za-z0-9_])...(?![A-Za-z0-9_])``) so that
    ``PHASE_POST_MERGE_CI`` matches the ``post_merge_ci``
    token while partial-word matches like ``postpone``,
    ``mergeable``, ``mainframe``, ``claim_ci``,
    ``domain_ci`` are still rejected.
    """

    @staticmethod
    def _exhausted_state(
        phase_name: str,
        next_action: str,
    ) -> "WatchdogState":
        from aed_lifecycle.watchdog import WatchdogState
        return WatchdogState(
            phase_name=phase_name,
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10.0,
            max_phase_seconds=10.0,
            next_action=next_action,
            checkpoint_path="/tmp/ckpt.json",
        )

    def test_phase_post_merge_ci_identifier_style(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_POST_MERGE_CI",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_phase_post_merge_ci_lowercase(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="post_merge_ci",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_phase_post_merge_main_ci_identifier_style(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="post_merge_main_ci",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_phase_post_merge_closeout_identifier_style(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="post_merge_closeout",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_next_action_poll_remote_main_ci_identifier_style(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="poll remote_main_ci",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
        )

    def test_unrelated_postpone_does_not_match_post_merge(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        # "postpone" contains "post" but the next char "p" is
        # a word char, so the identifier-aware boundary
        # rejects it as a post-merge token.
        state = self._exhausted_state(
            phase_name="phase_postpone",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_unrelated_mergeable_does_not_match_post_merge(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        # "mergeable" contains "merge" but the next char
        # "a" is a word char.
        state = self._exhausted_state(
            phase_name="phase_mergeable",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_unrelated_mainframe_does_not_match_main_ci(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        # "mainframe" contains "main" but the next char
        # "f" is a word char.
        state = self._exhausted_state(
            phase_name="phase_mainframe",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_unrelated_claim_ci_does_not_match_main_ci(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        # "claim_ci" contains the substring "aim_ci" but
        # not "main_ci" with a non-word-char prefix. The
        # "m" in "main_ci" is preceded by "i" (a word
        # char), so the boundary fails.
        state = self._exhausted_state(
            phase_name="phase_claim_ci",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )

    def test_unrelated_domain_ci_does_not_match_main_ci(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        # "domain_ci" contains the substring "main_ci"
        # starting at position 2. The "m" is preceded by
        # "o" (a word char), so the boundary fails.
        state = self._exhausted_state(
            phase_name="phase_domain_ci",
            next_action="poll github actions",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_PR_CI_PENDING,
        )


class SuffixCodexResponseActionTests(unittest.TestCase):
    """Regression tests for Codex 3416424661.

    The Codex action detector must recognize snake_case
    suffixed next_action strings such as
    ``codex_response_poll``, ``codex_response_pending``,
    ``poll_codex_response``, ``wait_for_codex_response``,
    not just the bare ``codex`` and ``codex response``
    forms. The detector uses the identifier-aware boundary
    class so that suffixed variants match while
    substring traps like ``codexia`` or
    ``codex_response_pollx`` do not.
    """

    @staticmethod
    def _exhausted_state(
        phase_name: str,
        next_action: str,
    ) -> "WatchdogState":
        from aed_lifecycle.watchdog import WatchdogState
        return WatchdogState(
            phase_name=phase_name,
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10.0,
            max_phase_seconds=10.0,
            next_action=next_action,
            checkpoint_path="/tmp/ckpt.json",
        )

    def test_codex_response_poll_returns_codex_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="codex_response_poll",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )

    def test_codex_response_pending_returns_codex_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="codex_response_pending",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )

    def test_poll_codex_response_returns_codex_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="poll_codex_response",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )

    def test_wait_for_codex_response_returns_codex_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="wait_for_codex_response",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )

    def test_poll_codex_response_prose_returns_codex_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        # The original prose form must still work via the
        # ``\bcodex(?:_response)?\b`` alternative.
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="poll Codex response",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )

    def test_bare_codex_returns_codex_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="codex",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )

    def test_codex_poll_returns_codex_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="codex_poll",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_CODEX_RESPONSE_PENDING,
        )

    def test_codex_does_not_return_pr_ci_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_CODEX_RESPONSE_PENDING,
            HOLD_PR_CI_PENDING,
            evaluate_watchdog,
        )
        # A pure codex action with no CI token must NOT be
        # misclassified as a PR-CI hold.
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="codex_response_poll",
        )
        verdict = evaluate_watchdog(state, now=1000.0)
        self.assertEqual(verdict, HOLD_CODEX_RESPONSE_PENDING)
        self.assertNotEqual(verdict, HOLD_PR_CI_PENDING)

    def test_unrelated_codex_substring_trap_does_not_match(self) -> None:
        # "codexia" is a substring trap: it contains
        # "codex" but with a non-word char boundary after.
        # The original ``\bcodex\b`` already rejected this.
        # Verify the new alternatives also reject it.
        from aed_lifecycle.watchdog import (
            HOLD_OPERATOR_REQUIRED,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="codexia",
        )
        # "codexia" is a single token, no CI signal. The
        # Codex pattern (with \b or identifier-aware
        # boundary) must not match because the char after
        # "codex" is "i" (a word char).
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_OPERATOR_REQUIRED,
        )

    def test_codex_response_pollx_suffix_trap_does_not_match(self) -> None:
        # "codex_response_pollx" must not match the
        # ``codex_response_poll`` token because the trailing
        # "x" is a word char so the identifier-aware
        # boundary fails.
        from aed_lifecycle.watchdog import (
            HOLD_OPERATOR_REQUIRED,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_7",
            next_action="codex_response_pollx",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_OPERATOR_REQUIRED,
        )


class NonEmptyCheckpointValueClassifierTests(unittest.TestCase):
    """Regression tests for Codex 3417011620.

    The classifier must require a value-bearing checkpoint
    marker (``checkpoint_path=``, ``checkpoint_path:``,
    ``checkpoint:``, ``Checkpoint:``) followed by a real,
    non-empty, non-placeholder path value on the same line.
    A bare marker with no value, or a placeholder value like
    ``none`` or ``todo``, must NOT count as a valid
    resume point. The OK_PROGRESS_WITH_NEXT_ACTION branch
    requires BOTH a valid next_action value AND a valid
    value-bearing checkpoint marker.
    """

    def test_phase_header_with_empty_checkpoint_path_equals_not_progress(
        self,
    ) -> None:
        # Bare marker ``checkpoint_path=`` with nothing
        # after the ``=`` is not a valid resume point.
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint_path="
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_phase_header_with_empty_checkpoint_colon_is_not_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint:"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_phase_header_with_empty_checkpoint_path_colon_is_not_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint_path:"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_phase_header_with_checkpoint_none_is_not_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint: none"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_phase_header_with_checkpoint_path_todo_is_not_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint_path: todo"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_phase_header_with_valid_checkpoint_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_phase_header_with_checkpoint_path_equals_value_is_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_extractor_does_not_consume_next_action(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        # The extractor must find the real ``/tmp/ckpt.json``
        # value, not consume the ``next_action`` text that
        # follows.
        text = (
            "checkpoint: /tmp/ckpt.json\n"
            "next_action: poll CI"
        )
        value = _extract_checkpoint_value(text)
        self.assertIsNotNone(value)
        self.assertEqual(value, "/tmp/ckpt.json")
        self.assertNotIn("next_action", value or "")
        self.assertNotIn("poll", value or "")

    def test_extractor_does_not_consume_following_field(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        # The extractor must stop at a field-boundary
        # delimiter, not consume the rest of the line.
        text = "checkpoint_path=/tmp/ckpt.json, resume: yes"
        value = _extract_checkpoint_value(text)
        self.assertIsNotNone(value)
        self.assertEqual(value, "/tmp/ckpt.json")
        self.assertNotIn("resume", value or "")

    def test_is_valid_checkpoint_path_rejects_empty(self) -> None:
        from aed_lifecycle.no_stall import is_valid_checkpoint_path
        self.assertFalse(is_valid_checkpoint_path(""))
        self.assertFalse(is_valid_checkpoint_path(None))
        self.assertFalse(is_valid_checkpoint_path("   "))
        self.assertFalse(is_valid_checkpoint_path("\t\n"))

    def test_is_valid_checkpoint_path_rejects_placeholders(self) -> None:
        from aed_lifecycle.no_stall import is_valid_checkpoint_path
        for placeholder in [
            "none", "None", "NONE", "null", "nil",
            "n/a", "na", "todo", "tbd", "tba",
        ]:
            self.assertFalse(
                is_valid_checkpoint_path(placeholder),
                f"placeholder {placeholder!r} should be invalid",
            )

    def test_is_valid_checkpoint_path_accepts_real_paths(self) -> None:
        from aed_lifecycle.no_stall import is_valid_checkpoint_path
        for path in [
            "/tmp/ckpt.json",
            "/tmp/aed_no_stall_checkpoint_pr405.json",
            "./ckpt.json",
            "../state/ckpt.json",
        ]:
            self.assertTrue(
                is_valid_checkpoint_path(path),
                f"path {path!r} should be valid",
            )


class NonEmptyCheckpointValueWatchdogTests(unittest.TestCase):
    """Regression tests for Codex 3417011624.

    The watchdog must use the canonical
    :func:`is_valid_checkpoint_path` helper (not a truthiness
    check) so that whitespace, placeholder, and non-string
    ``checkpoint_path`` values are rejected. A runner that
    continues past the stall guard without a real resume
    point is exactly the no-stall protocol's failure mode.
    """

    @staticmethod
    def _state(checkpoint_path):
        from aed_lifecycle.watchdog import WatchdogState
        return WatchdogState(
            phase_name="PHASE_8",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10.0,
            max_phase_seconds=10.0,
            next_action="poll CI",
            checkpoint_path=checkpoint_path,
        )

    def test_empty_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            STALL_RISK,
            evaluate_watchdog,
        )
        state = self._state("")
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, STALL_RISK)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_whitespace_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            STALL_RISK,
            evaluate_watchdog,
        )
        state = self._state("   ")
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, STALL_RISK)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_none_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            STALL_RISK,
            evaluate_watchdog,
        )
        state = self._state(None)
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, STALL_RISK)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_placeholder_none_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            STALL_RISK,
            evaluate_watchdog,
        )
        state = self._state("none")
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, STALL_RISK)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_placeholder_todo_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            STALL_RISK,
            evaluate_watchdog,
        )
        state = self._state("todo")
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, STALL_RISK)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_placeholder_tbd_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            STALL_RISK,
            evaluate_watchdog,
        )
        state = self._state("tbd")
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, STALL_RISK)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_placeholder_null_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            STALL_RISK,
            evaluate_watchdog,
        )
        state = self._state("null")
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, STALL_RISK)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_valid_checkpoint_path_is_progress(self) -> None:
        from aed_lifecycle.watchdog import (
            OK_PROGRESS_WITH_NEXT_ACTION,
            evaluate_watchdog,
        )
        state = self._state("/tmp/aed_no_stall_checkpoint_pr405.json")
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_terminal_state_skips_checkpoint_requirement(self) -> None:
        # The watchdog's branch 1 (terminal_state recognized)
        # returns OK_TERMINAL without consulting
        # checkpoint_path at all. A None checkpoint_path must
        # not block the OK_TERMINAL verdict.
        from aed_lifecycle.watchdog import (
            OK_TERMINAL,
            WatchdogState,
            evaluate_watchdog,
        )
        state = WatchdogState(
            phase_name="PHASE_8",
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10.0,
            max_phase_seconds=10.0,
            next_action=None,
            checkpoint_path=None,
            terminal_state="MERGED",
        )
        verdict = evaluate_watchdog(state, now=1.0)
        self.assertEqual(verdict, OK_TERMINAL)


class BareCheckpointMarkerRejectionTests(unittest.TestCase):
    """Regression tests for Codex 3417105899.

    The value-bearing checkpoint parser must reject bare
    ``Checkpoint `` / ``checkpoint `` markers (no colon,
    no equals) because a status phrase like
    ``Checkpoint pending`` or
    ``Checkpoint file will be written later`` would
    otherwise be treated as having a real checkpoint
    value. The classifier requires an explicit path/value
    marker (``checkpoint:``, ``checkpoint_path:``,
    ``checkpoint_path=``, ``checkpoint=``, ``checkpoint =``)
    for OK_PROGRESS_WITH_NEXT_ACTION. Bare prose
    references may still count for the broad
    STALL_NO_CHECKPOINT branch but NOT for the value-bearing
    OK_PROGRESS branch.
    """

    def test_checkpoint_pending_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "Checkpoint pending"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_checkpoint_file_will_be_written_later_is_not_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "Checkpoint file will be written later"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_checkpoint_missing_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "Checkpoint missing"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_lowercase_checkpoint_pending_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint pending"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_checkpoint_needed_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "Checkpoint still needed"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_checkpoint_todo_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "Checkpoint todo"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_checkpoint_created_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "Checkpoint created"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)

    def test_status_words_alone_are_not_progress(self) -> None:
        # The bare status words ``pending``, ``file``,
        # ``missing``, ``needed``, ``later``, ``written``,
        # ``created`` are never valid checkpoint paths. The
        # extractor must not classify them as such.
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        for status_word in [
            "pending",
            "file",
            "missing",
            "needed",
            "later",
            "written",
            "created",
            "checkpoint",
        ]:
            text = (
                "Starting PHASE 2.\n"
                "next_action: poll CI\n"
                f"Checkpoint {status_word}"
            )
            # The extractor must not find a real value
            # when the value is a status word.
            value = _extract_checkpoint_value(text)
            self.assertIsNone(
                value,
                f"status word {status_word!r} should not "
                f"extract as a value: got {value!r}",
            )
            # And the classifier must not return
            # OK_PROGRESS_WITH_NEXT_ACTION.
            verdict = classify_humphry_message_for_stall(text)
            self.assertNotEqual(
                verdict,
                OK_PROGRESS_WITH_NEXT_ACTION,
                f"status word {status_word!r} should not "
                f"classify as OK_PROGRESS: got {verdict!r}",
            )

    def test_checkpoint_colon_value_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "Checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_lowercase_colon_value_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_path_equals_value_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_space_equals_value_is_progress(self) -> None:
        # The new ``checkpoint =`` (with space before =)
        # form must be accepted.
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI\n"
            "checkpoint = /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_broad_prose_checkpoint_file_remains_stall(self) -> None:
        # The STALL_NO_CHECKPOINT branch still treats a
        # prose mention as a checkpoint reference. A
        # message like ``Wrote checkpoint file but no
        # next_action specified.`` continues to classify
        # as STALL_NO_CHECKPOINT because the runner wrote
        # a checkpoint but provided no value-bearing
        # resume point.
        text = "Wrote checkpoint file but no next_action specified."
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_NO_CHECKPOINT,
        )

    def test_parser_does_not_consume_next_action(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        text = (
            "Checkpoint: /tmp/ckpt.json\n"
            "next_action: poll CI"
        )
        value = _extract_checkpoint_value(text)
        self.assertEqual(value, "/tmp/ckpt.json")
        self.assertNotIn("next_action", value or "")
        self.assertNotIn("poll", value or "")

    def test_parser_does_not_consume_terminal_state(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        text = (
            "checkpoint: /tmp/ckpt.json\n"
            "terminal_state: MERGED"
        )
        value = _extract_checkpoint_value(text)
        self.assertEqual(value, "/tmp/ckpt.json")
        self.assertNotIn("terminal_state", value or "")
        self.assertNotIn("MERGED", value or "")


class ProseCheckpointPathExtractionTests(unittest.TestCase):
    """Regression tests for Codex 3417849222 (Fix B).

    The value-bearing checkpoint parser must recognize
    prose-style markers (``wrote checkpoint to``,
    ``saved checkpoint to``, ``checkpoint file:`` /
    ``checkpoint file``, ``checkpoint at``,
    ``checkpoint saved to``) when the marker is followed
    by a path-shaped value. The broader classifier
    vocabulary already treats those phrases as
    checkpoint references, but the previous extractor
    only accepted field-style markers, so a valid final
    output like ``next_action: poll CI status`` plus
    ``wrote checkpoint to /tmp/ckpt.json`` was misclassified
    as ``STALL_NO_CHECKPOINT`` even though both a
    continuation action and a concrete resume path were
    present.

    The parser must still reject prose/status forms
    (e.g. ``Checkpoint pending``,
    ``saved checkpoint to pending``,
    ``Checkpoint file will be written later``) so the
    previous bare-marker bug (Codex 3417105899) remains
    pinned: a status phrase must not satisfy
    ``OK_PROGRESS_WITH_NEXT_ACTION``.
    """

    def test_wrote_checkpoint_to_absolute_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "wrote checkpoint to /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_saved_checkpoint_to_absolute_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "saved checkpoint to /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_file_with_colon_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "checkpoint file: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_file_without_colon_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "checkpoint file /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_at_absolute_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "checkpoint at /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_saved_to_absolute_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "checkpoint saved to /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_wrote_checkpoint_to_home_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "wrote checkpoint to ~/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_wrote_checkpoint_to_relative_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "wrote checkpoint to ./ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_file_will_be_written_later_is_not_progress(
        self,
    ) -> None:
        # Status phrase with no path-shaped value. The
        # prose marker is not enough — the value is a
        # status word, not a path.
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint file will be written later"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_pending_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint pending"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_saved_checkpoint_to_pending_is_not_progress(self) -> None:
        # A prose marker followed by a non-path value.
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "saved checkpoint to pending"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_wrote_checkpoint_to_later_is_not_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "wrote checkpoint to later"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_field_style_checkpoints_still_progress(self) -> None:
        # Field-style markers must still work alongside
        # the new prose markers.
        for marker in [
            "checkpoint: /tmp/ckpt.json",
            "checkpoint_path=/tmp/ckpt.json",
            "checkpoint = /tmp/ckpt.json",
        ]:
            text = (
                "Starting PHASE 2 — protected-state verification.\n"
                "next_action: poll CI status\n"
                f"{marker}"
            )
            self.assertEqual(
                classify_humphry_message_for_stall(text),
                OK_PROGRESS_WITH_NEXT_ACTION,
                f"field-style marker {marker!r} should classify as progress",
            )

    def test_prose_checkpoint_extractor_returns_path(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        for text, expected in [
            (
                "wrote checkpoint to /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "saved checkpoint to /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "checkpoint file: /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "checkpoint file /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "checkpoint at /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "checkpoint saved to /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
        ]:
            value = _extract_checkpoint_value(text)
            self.assertEqual(
                value,
                expected,
                f"prose marker text {text!r} should extract {expected!r}, got {value!r}",
            )

    def test_prose_checkpoint_with_status_word_returns_none(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        for text in [
            "wrote checkpoint to pending",
            "saved checkpoint to pending",
            "saved checkpoint to later",
            "wrote checkpoint to later",
            "checkpoint at pending",
            "checkpoint saved to missing",
        ]:
            value = _extract_checkpoint_value(text)
            self.assertIsNone(
                value,
                f"prose marker with status-word value {text!r} should extract None, got {value!r}",
            )


class CapitalizedProseCheckpointPathExtractionTests(unittest.TestCase):
    """Regression tests for Codex 3420268720 (Fix J).

    The value-bearing checkpoint parser must recognize
    prose-style markers in sentence-cased form
    (``Wrote checkpoint to`` / ``Saved checkpoint to`` /
    ``Checkpoint file:`` / ``Checkpoint file`` /
    ``Checkpoint at`` / ``Checkpoint saved to``) when the
    marker is followed by a path-shaped value. The previous
    case-sensitive ``line.find(marker)`` for prose markers
    let a perfectly valid sentence-cased final output fall
    through to ``STALL_NO_CHECKPOINT`` even though both a
    continuation action and a concrete resume path were
    present.

    The parser must still reject capitalized status forms
    (e.g. ``Checkpoint pending``, ``Wrote checkpoint to
    pending``, ``Checkpoint file will be written later``) so
    the bare-marker bug (Codex 3417105899) remains pinned.
    Field-style markers (e.g. ``checkpoint:`` /
    ``checkpoint_path=``) keep their strict
    case-sensitive behavior; only prose-style markers
    accept sentence-cased variants.
    """

    def test_capitalized_wrote_checkpoint_to_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Wrote checkpoint to /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_saved_checkpoint_to_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Saved checkpoint to /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_checkpoint_file_with_colon_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint file: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_checkpoint_file_without_colon_is_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint file /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_checkpoint_at_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint at /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_checkpoint_saved_to_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint saved to /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_prose_extractor_returns_path(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        for text, expected in [
            (
                "Wrote checkpoint to /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "Saved checkpoint to /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "Checkpoint file: /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "Checkpoint file /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "Checkpoint at /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
            (
                "Checkpoint saved to /tmp/ckpt.json",
                "/tmp/ckpt.json",
            ),
        ]:
            value = _extract_checkpoint_value(text)
            self.assertEqual(
                value,
                expected,
                f"capitalized prose marker text {text!r} should extract {expected!r}, got {value!r}",
            )

    def test_capitalized_prose_with_status_word_returns_none(self) -> None:
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        for text in [
            "Wrote checkpoint to pending",
            "Saved checkpoint to pending",
            "Saved checkpoint to later",
            "Wrote checkpoint to later",
            "Checkpoint at pending",
            "Checkpoint saved to missing",
        ]:
            value = _extract_checkpoint_value(text)
            self.assertIsNone(
                value,
                f"capitalized prose marker with status-word value {text!r} should extract None, got {value!r}",
            )

    def test_capitalized_prose_with_home_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Wrote checkpoint to ~/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_prose_with_relative_path_is_progress(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Saved checkpoint to ./ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_bare_checkpoint_pending_is_not_progress(
        self,
    ) -> None:
        # The bare-marker bug (Codex 3417105899) must
        # remain pinned: a sentence-cased bare
        # ``Checkpoint pending`` is still not a valid
        # value-bearing checkpoint reference. The
        # case-insensitive prose-marker search is only
        # for the explicit prose markers (``Wrote
        # checkpoint to`` / etc.), not for the bare
        # ``Checkpoint`` token.
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint pending"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_capitalized_checkpoint_file_will_be_written_later_is_not_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Checkpoint file will be written later"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_field_style_markers_remain_case_sensitive(self) -> None:
        # Field-style markers like ``Checkpoint:`` are
        # case-sensitive. The agent must use exactly the
        # documented case (``Checkpoint:`` /
        # ``checkpoint:``). A non-matching case for a
        # non-explicit field-style marker must NOT
        # classify as progress.
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        # ``CHECKPOINT:`` is not in the field-style
        # marker list (which has lowercase
        # ``checkpoint:`` and the explicit
        # ``Checkpoint:``). It must not extract.
        self.assertIsNone(
            _extract_checkpoint_value("CHECKPOINT: /tmp/ckpt.json"),
        )
        # ``Checkpoint:`` (exact case) still works.
        self.assertEqual(
            _extract_checkpoint_value("Checkpoint: /tmp/ckpt.json"),
            "/tmp/ckpt.json",
        )
        # ``checkpoint:`` (lowercase) still works.
        self.assertEqual(
            _extract_checkpoint_value("checkpoint: /tmp/ckpt.json"),
            "/tmp/ckpt.json",
        )


class OkProgressRequiresBothFieldsTests(unittest.TestCase):
    """Regression tests for Codex 3420442393 (Fix L).

    The public contract for ``OK_PROGRESS_WITH_NEXT_ACTION``
    requires BOTH a valid ``next_action`` AND a value-bearing
    checkpoint path. A final output that omits either
    piece of evidence is NOT ``OK_PROGRESS_WITH_NEXT_ACTION``;
    it falls through to ``STALL_NO_CHECKPOINT`` (next_action
    without checkpoint) or ``STALL_NO_TERMINAL_STATE``
    (checkpoint without next_action). Fix L aligned the
    docstring and the docs protocol with the strict
    classifier implementation; these tests pin the
    contract so a future change cannot regress it.
    """

    def test_next_action_only_is_not_ok_progress(self) -> None:
        # A final output with a valid ``next_action`` and
        # no value-bearing checkpoint is NOT
        # ``OK_PROGRESS_WITH_NEXT_ACTION``. The
        # classifier falls through to ``STALL_NO_CHECKPOINT``
        # (or ``STALL_NO_TERMINAL_STATE`` when the message
        # has no checkpoint mention at all).
        text = "next_action: poll CI status"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_action_only_with_phase_header_is_stall_no_checkpoint(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            STALL_NO_CHECKPOINT,
        )

    def test_field_style_checkpoint_with_next_action_is_progress(
        self,
    ) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_prose_checkpoint_with_next_action_is_progress(self) -> None:
        # Sentence-cased prose form (Fix J territory)
        # combined with a valid ``next_action`` must
        # classify as ``OK_PROGRESS_WITH_NEXT_ACTION``.
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "Wrote checkpoint to /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_only_is_not_ok_progress(self) -> None:
        # A final output with a value-bearing checkpoint
        # but no ``next_action`` is NOT
        # ``OK_PROGRESS_WITH_NEXT_ACTION``. The runner
        # has a resume point but nothing executable to
        # act on, so the classifier falls through to
        # ``STALL_NO_TERMINAL_STATE`` (or
        # ``STALL_NO_CHECKPOINT`` when the message also
        # has a phase header).
        text = (
            "Starting PHASE 2 — protected-state verification.\n"
            "checkpoint: /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertNotEqual(verdict, OK_TERMINAL)

    def test_no_next_action_no_checkpoint_is_stall(self) -> None:
        text = (
            "Starting PHASE 2 — protected-state verification."
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(verdict, OK_PROGRESS_WITH_NEXT_ACTION)
        self.assertNotEqual(verdict, OK_TERMINAL)


class TerminalStateAssertionPrefixNormalizationTests(unittest.TestCase):
    """Regression tests for Codex 3420442396 (Fix K).

    The terminal_state assertion prefix list accepts
    uppercase with spaces (``TERMINAL_STATE = MERGED``)
    and other mixed-case / spaced forms in addition to
    the previously-recognized uppercase-without-space
    (``TERMINAL_STATE=``) and lowercase-spaced
    (``terminal_state =``) forms. The matching is
    case-insensitive so a sentence-cased or all-caps
    pretty-printed protocol field still classifies as
    an explicit terminal-state assertion.
    """

    def test_terminal_state_uppercase_spaced_equals_merged(self) -> None:
        # The specific form called out in the Codex
        # finding: ``TERMINAL_STATE = MERGED``.
        text = "TERMINAL_STATE = MERGED"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_uppercase_spaced_equals_hold(self) -> None:
        text = "TERMINAL_STATE = HOLD_PR_CI_PENDING"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_mixed_case_spaced_equals(self) -> None:
        text = "Terminal_State = HOLD_NEW_CODEX_THREAD"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_uppercase_no_space_equals(self) -> None:
        # Already-supported form: still works.
        text = "TERMINAL_STATE=MERGED"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_lowercase_spaced_equals(self) -> None:
        # Already-supported form: still works.
        text = "terminal_state = MERGED"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_lowercase_colon(self) -> None:
        # Already-supported form: still works.
        text = "terminal_state: MERGED"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_empty_colon_is_not_terminal(self) -> None:
        text = "terminal_state:"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_uppercase_spaced_equals_empty_is_not_terminal(
        self,
    ) -> None:
        text = "TERMINAL_STATE ="
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_uppercase_spaced_equals_non_terminal_value(
        self,
    ) -> None:
        # CODEX_CLEAN_PASS is informational, not a
        # parked/terminal state. The strict prefix match
        # still extracts the value and rejects it.
        text = "TERMINAL_STATE = CODEX_CLEAN_PASS"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_uppercase_spaced_equals_with_explanation(
        self,
    ) -> None:
        # The em-dash explanation form should still work
        # for the new uppercase-with-spaced-equals
        # prefix. Fix B's disqualifier is scoped to the
        # ambiguous portion, not the explanation.
        text = "TERMINAL_STATE = HOLD_PR_CI_PENDING — bounded polling reached limit"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_terminal_state_assertion_extractor(self) -> None:
        from aed_lifecycle.no_stall import (
            _line_has_explicit_terminal_assertion,
        )
        # Direct test of the assertion helper on each
        # canonical form called out in the spec.
        accepted_forms = [
            "terminal_state: MERGED",
            "Terminal State: MERGED",
            "TERMINAL_STATE: MERGED",
            "terminal_state=MERGED",
            "terminal_state = MERGED",
            "TERMINAL_STATE=MERGED",
            "TERMINAL_STATE = MERGED",
            "Terminal_State = MERGED",
        ]
        for line in accepted_forms:
            self.assertTrue(
                _line_has_explicit_terminal_assertion(line),
                f"line {line!r} should be an explicit terminal assertion",
            )

    def test_terminal_state_assertion_rejects_empty(self) -> None:
        from aed_lifecycle.no_stall import (
            _line_has_explicit_terminal_assertion,
        )
        rejected_forms = [
            "terminal_state:",
            "TERMINAL_STATE =",
            "terminal_state: none",
            "TERMINAL_STATE = none",
        ]
        for line in rejected_forms:
            self.assertFalse(
                _line_has_explicit_terminal_assertion(line),
                f"line {line!r} should NOT be an explicit terminal assertion",
            )


class RejectCheckpointAssignmentAsNextActionTests(unittest.TestCase):
    """Regression tests for Codex finding 3417182526 (Fix H).

    The next_action extractor must not consume a field=value
    assignment on the same line. A malformed message like
    ``next_action: checkpoint_path=/tmp/ckpt.json`` is a
    field-name-as-value collision: the runner used a protocol
    field name as a value, not as an executable action. The
    classifier must NOT classify the message as
    ``OK_PROGRESS_WITH_NEXT_ACTION``.

    Valid multi-line messages that contain a real action plus a
    checkpoint/terminal field must still be classified as
    ``OK_PROGRESS_WITH_NEXT_ACTION`` — the rejection only
    applies when the only next_action marker is a
    field-assignment collision.
    """

    def test_next_action_checkpoint_path_equals_is_not_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = "Starting PHASE 1 — protected-state verification.\nnext_action: checkpoint_path=/tmp/ckpt.json"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_next_action_checkpoint_equals_is_not_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = "Starting PHASE 1 — protected-state verification.\nnext_action: checkpoint=/tmp/ckpt.json"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_next_action_checkpoint_path_space_equals_is_not_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = "Starting PHASE 1 — protected-state verification.\nnext_action: checkpoint_path = /tmp/ckpt.json"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_next_action_terminal_state_equals_is_not_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = "Starting PHASE 1 — protected-state verification.\nnext_action: terminal_state=MERGED"
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_next_action_empty_value_then_checkpoint_path_is_not_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action:\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_valid_poll_ci_with_checkpoint_equals_is_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: continue bounded CI polling\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_valid_poll_codex_with_checkpoint_colon_is_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: poll Codex response\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_valid_resume_with_checkpoint_space_equals_is_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: resume from checkpoint\n"
            "checkpoint = /tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_invalid_marker_followed_by_valid_marker_is_progress(self) -> None:
        """If a runner emits a field-assignment marker first
        and a real action later, the scan must skip the
        collision and accept the real action (round-10
        continue-scanning behavior is preserved).
        """
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint_path=/tmp/ckpt.json\n"
            "next_action: poll CI status"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_extractor_does_not_include_checkpoint_text(self) -> None:
        from aed_lifecycle.no_stall import _extract_next_action_value
        text = "next_action: checkpoint_path=/tmp/ckpt.json"
        value = _extract_next_action_value(text)
        self.assertNotIn("checkpoint", value or "")
        self.assertNotIn("=", value or "")
        self.assertNotIn("/", value or "")

    def test_is_field_assignment_collision_rejects_field_names(self) -> None:
        from aed_lifecycle.no_stall import _is_field_assignment_collision
        for tok in (
            "checkpoint", "checkpoint_path", "terminal_state",
            "phase", "state", "next_action", "next_step",
        ):
            self.assertTrue(
                _is_field_assignment_collision(tok),
                f"expected {tok!r} to be a collision",
            )

    def test_is_field_assignment_collision_accepts_real_actions(self) -> None:
        from aed_lifecycle.no_stall import _is_field_assignment_collision
        for tok in ("poll", "continue", "resume", "wait", "proceed"):
            self.assertFalse(
                _is_field_assignment_collision(tok),
                f"expected {tok!r} NOT to be a collision",
            )

    def test_is_field_assignment_collision_handles_case(self) -> None:
        from aed_lifecycle.no_stall import _is_field_assignment_collision
        self.assertTrue(_is_field_assignment_collision("Checkpoint"))
        self.assertTrue(_is_field_assignment_collision("CHECKPOINT_PATH"))
        self.assertTrue(_is_field_assignment_collision("Terminal_State"))

    def test_is_field_assignment_collision_handles_garbage(self) -> None:
        from aed_lifecycle.no_stall import _is_field_assignment_collision
        self.assertFalse(_is_field_assignment_collision(""))
        self.assertFalse(_is_field_assignment_collision("   "))
        self.assertFalse(_is_field_assignment_collision(None))  # type: ignore[arg-type]


class RejectPersistedFieldAssignmentAsNextActionTests(unittest.TestCase):
    """Regression tests for Codex finding 3422779962 (Fix L).

    ``is_valid_next_action`` must reject field=value
    assignment collisions when they are passed as PERSISTED
    next_action values. The classifier
    (:func:`_extract_next_action_value`) already rejects the
    same collisions when they appear in final-output text
    (after the per-token boundary scan stops at ``=``), but
    the persisted-state validator used a full-string
    membership check against :data:`_FIELD_NAME_NEXT_ACTIONS`
    that let ``"checkpoint_path=/tmp/ckpt.json"`` and similar
    ``field=value`` strings slip through. The canonical
    :func:`is_valid_next_action` helper is the single entry
    point used by ``validate_checkpoint``,
    ``checkpoint_requires_operator``,
    ``next_action_from_checkpoint``, and ``evaluate_watchdog``;
    tightening the helper tightens all four callers in one
    place.

    The fix introduces :func:`_first_field_name_token`, which
    extracts the first identifier-like token from a value
    using the same boundary vocabulary as
    :func:`_extract_next_action_value`, and rejects a value
    when that token is a known protocol field name. The
    check is in addition to (not a replacement for) the
    full-string field-name check; both must pass.
    """

    # --- is_valid_next_action rejects the documented collisions ---

    def test_checkpoint_path_equals_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )

    def test_checkpoint_path_space_equals_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path = /tmp/ckpt.json")
        )

    def test_checkpoint_equals_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint=/tmp/ckpt.json")
        )

    def test_terminal_state_equals_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )

    def test_terminal_state_space_equals_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("terminal_state = MERGED")
        )

    def test_state_colon_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("state: MERGED"))

    def test_phase_colon_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("phase: PHASE_7"))

    def test_next_action_equals_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("next_action=poll CI"))

    def test_next_step_equals_value_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("next_step=continue"))

    def test_all_field_names_rejected_as_field_equals_value(self) -> None:
        """Every member of ``_FIELD_NAME_NEXT_ACTIONS`` must be
        rejected when used as ``field=value``. This pins the
        full vocabulary — a future addition to the set must
        automatically be rejected by this same check.
        """
        from aed_lifecycle.no_stall import (
            _FIELD_NAME_NEXT_ACTIONS,
            is_valid_next_action,
        )
        for fname in sorted(_FIELD_NAME_NEXT_ACTIONS):
            value = f"{fname}=somevalue"
            with self.subTest(fname=fname):
                self.assertFalse(
                    is_valid_next_action(value),
                    f"expected {value!r} to be rejected",
                )

    def test_field_equals_value_with_padding_whitespace_rejected(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("  checkpoint_path=/tmp/ckpt.json  ")
        )
        self.assertFalse(
            is_valid_next_action("\tterminal_state=MERGED\n")
        )

    def test_field_equals_value_case_insensitive_rejected(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("Checkpoint_Path=/tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("TERMINAL_STATE=MERGED")
        )

    # --- Real executable actions still pass ---

    def test_poll_ci_status_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(is_valid_next_action("poll CI status"))

    def test_poll_codex_response_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(is_valid_next_action("poll Codex response"))

    def test_continue_bounded_ci_polling_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("continue bounded CI polling")
        )

    def test_resume_from_checkpoint_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(is_valid_next_action("resume from checkpoint"))

    def test_wait_for_required_checks_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("wait for required checks")
        )

    def test_real_actions_still_accepted_after_fix(self) -> None:
        """Pin the canonical valid-action vocabulary used by
        earlier sections so the new field-token check does
        not accidentally tighten what counts as a real
        executable action.
        """
        from aed_lifecycle.no_stall import is_valid_next_action
        for good in [
            "poll CI",
            "PHASE_5",
            "reconcile threads",
            "wait",
            "resume",
            "continue",
            "proceed",
            "poll codex response",
        ]:
            with self.subTest(good=good):
                self.assertTrue(
                    is_valid_next_action(good),
                    f"expected {good!r} to remain valid",
                )

    # --- _first_field_name_token helper contract ---

    def test_first_field_name_token_extracts_field_name(self) -> None:
        from aed_lifecycle.no_stall import _first_field_name_token
        self.assertEqual(
            _first_field_name_token("checkpoint_path=/tmp/ckpt.json"),
            "checkpoint_path",
        )
        self.assertEqual(
            _first_field_name_token("terminal_state = MERGED"),
            "terminal_state",
        )
        self.assertEqual(
            _first_field_name_token("state: MERGED"),
            "state",
        )
        self.assertEqual(
            _first_field_name_token("next_action: poll CI"),
            "next_action",
        )

    def test_first_field_name_token_returns_first_token_of_real_action(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import _first_field_name_token
        self.assertEqual(
            _first_field_name_token("poll CI status"), "poll"
        )
        self.assertEqual(
            _first_field_name_token("continue bounded CI polling"),
            "continue",
        )
        self.assertEqual(_first_field_name_token("wait"), "wait")

    def test_first_field_name_token_handles_garbage(self) -> None:
        from aed_lifecycle.no_stall import _first_field_name_token
        self.assertIsNone(_first_field_name_token(""))
        self.assertIsNone(_first_field_name_token("   "))
        self.assertIsNone(_first_field_name_token("= value"))
        self.assertIsNone(_first_field_name_token(": value"))
        self.assertIsNone(_first_field_name_token(None))  # type: ignore[arg-type]

    # --- validate_checkpoint emits an error ---

    def test_validate_checkpoint_rejects_field_assignment_next_action(
        self,
    ) -> None:
        from aed_lifecycle.checkpoint import validate_checkpoint
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint_path=/tmp/ckpt.json",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        errors = validate_checkpoint(state)
        self.assertTrue(
            any("next_action" in e for e in errors),
            f"expected next_action error, got {errors!r}",
        )

    # --- checkpoint_requires_operator returns True ---

    def test_checkpoint_requires_operator_for_field_assignment_next_action(
        self,
    ) -> None:
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint_path=/tmp/ckpt.json",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        self.assertTrue(checkpoint_requires_operator(state))

    def test_checkpoint_requires_operator_for_terminal_state_equals(
        self,
    ) -> None:
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="terminal_state=MERGED",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        self.assertTrue(checkpoint_requires_operator(state))

    # --- next_action_from_checkpoint returns HOLD_OPERATOR_REQUIRED ---

    def test_next_action_from_checkpoint_returns_hold_for_field_assignment(
        self,
    ) -> None:
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint_path=/tmp/ckpt.json",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        result = next_action_from_checkpoint(state)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "HOLD_OPERATOR_REQUIRED")

    def test_next_action_from_checkpoint_returns_hold_for_state_colon(
        self,
    ) -> None:
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="state: MERGED",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        result = next_action_from_checkpoint(state)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "HOLD_OPERATOR_REQUIRED")

    # --- evaluate_watchdog does NOT return OK_PROGRESS_WITH_NEXT_ACTION ---

    def test_evaluate_watchdog_rejects_field_assignment_next_action(self) -> None:
        from aed_lifecycle.watchdog import STALL_RISK
        state = WatchdogState(
            phase_name="PHASE_5_CI_POLL",
            started_at=0.0,
            last_progress_at=0.0,
            max_phase_seconds=1000.0,
            max_idle_seconds=1000.0,
            terminal_state=None,
            checkpoint_path="/tmp/ckpt.json",
            next_action="checkpoint_path=/tmp/ckpt.json",
        )
        result = evaluate_watchdog(state, now=10.0)
        self.assertNotEqual(result, "OK_PROGRESS_WITH_NEXT_ACTION")
        self.assertEqual(result, STALL_RISK)

    def test_evaluate_watchdog_rejects_terminal_state_equals_next_action(
        self,
    ) -> None:
        from aed_lifecycle.watchdog import STALL_RISK
        state = WatchdogState(
            phase_name="PHASE_5_CI_POLL",
            started_at=0.0,
            last_progress_at=0.0,
            max_phase_seconds=1000.0,
            max_idle_seconds=1000.0,
            terminal_state=None,
            checkpoint_path="/tmp/ckpt.json",
            next_action="terminal_state=MERGED",
        )
        result = evaluate_watchdog(state, now=10.0)
        self.assertNotEqual(result, "OK_PROGRESS_WITH_NEXT_ACTION")
        self.assertEqual(result, STALL_RISK)

    # --- Existing final-output pinned tests still pass ---
    # The following tests are pinned copies of the existing
    # ``RejectCheckpointAssignmentAsNextActionTests`` cases
    # for final-output text. The persisted-state fix must
    # NOT regress the final-output rejection behavior.

    def test_final_output_next_action_checkpoint_path_equals_still_rejected(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_final_output_valid_action_with_checkpoint_still_progress(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )


class NarrowFieldAssignmentRejectionTests(unittest.TestCase):
    """Regression tests for Codex finding 3438724908 (Fix M).

    Tightens the persisted-state ``is_valid_next_action`` check
    so it rejects ONLY real ``field=value`` / ``field: value``
    assignments, not legitimate executable actions that merely
    begin with a field-name word. The previous fix (Fix L,
    Codex 3422779962) rejected any value whose first word was a
    protocol field name; that was too aggressive and rejected
    legitimate actions like ``"checkpoint current run state"``
    and ``"state current PR status"``.

    The new canonical helper
    :func:`_is_field_assignment_collision_value` is the single
    source of truth for persisted field-assignment collision
    detection. It is called by :func:`is_valid_next_action`,
    which is in turn called by ``validate_checkpoint``,
    ``checkpoint_requires_operator``,
    ``next_action_from_checkpoint``, and ``evaluate_watchdog``.
    """

    # --- Legitimate actions that begin with a field-name word
    #     are now ACCEPTED (the previous fix wrongly rejected
    #     them). ---

    def test_checkpoint_current_run_state_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("checkpoint current run state")
        )

    def test_state_current_pr_status_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("state current PR status")
        )

    def test_phase_current_retry_window_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("phase current retry window")
        )

    def test_next_action_review_codex_response_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("next_action review Codex response")
        )

    def test_terminal_state_current_view_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("terminal_state current view")
        )

    def test_lifecycle_current_phase_is_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("lifecycle current phase")
        )

    def test_all_field_names_accepted_when_followed_by_text(self) -> None:
        """Every member of ``_FIELD_NAME_NEXT_ACTIONS`` must be
        accepted when followed by ordinary action text (no
        ``=`` or ``:`` delimiter). This is the symmetric
        complement of ``test_all_field_names_rejected_as_field_equals_value``
        and pins the Fix M contract.
        """
        from aed_lifecycle.no_stall import (
            _FIELD_NAME_NEXT_ACTIONS,
            is_valid_next_action,
        )
        for fname in sorted(_FIELD_NAME_NEXT_ACTIONS):
            value = f"{fname} ordinary executable action text"
            with self.subTest(fname=fname):
                self.assertTrue(
                    is_valid_next_action(value),
                    f"expected {value!r} to be accepted",
                )

    def test_all_field_names_accepted_with_space_equals(self) -> None:
        """The boundary between "field assignment" and
        "legitimate action" is the presence of ``=`` or
        ``:`` after the field name. ``field = poll CI
        status`` IS a field assignment (the ``=`` is
        present) and must be rejected — the value side is
        not the gate. This test pins the rejection
        contract for ``field = value`` so a future
        refactor cannot silently accept these.
        """
        from aed_lifecycle.no_stall import (
            _FIELD_NAME_NEXT_ACTIONS,
            is_valid_next_action,
        )
        for fname in sorted(_FIELD_NAME_NEXT_ACTIONS):
            value = f"{fname} = poll CI status"
            with self.subTest(fname=fname):
                self.assertFalse(
                    is_valid_next_action(value),
                    f"expected {value!r} to be rejected (field assignment)",
                )

    # --- Real field-assignment collisions remain REJECTED.
    #     Pin the symmetric contract so the loosening of
    #     "any field-name word" does not accidentally relax
    #     "field=value" / "field: value" rejection. ---

    def test_checkpoint_path_equals_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )

    def test_checkpoint_path_space_equals_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path = /tmp/ckpt.json")
        )

    def test_checkpoint_equals_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint=/tmp/ckpt.json")
        )

    def test_checkpoint_space_equals_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint = /tmp/ckpt.json")
        )

    def test_terminal_state_equals_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )

    def test_terminal_state_space_equals_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("terminal_state = MERGED")
        )

    def test_state_colon_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("state: MERGED"))

    def test_phase_colon_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("phase: PHASE_7"))

    def test_next_action_equals_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("next_action=poll CI"))

    def test_next_step_colon_value_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("next_step: continue"))

    def test_all_field_names_rejected_as_field_equals_value(self) -> None:
        """Pin the full vocabulary: every member of
        ``_FIELD_NAME_NEXT_ACTIONS`` must be rejected when
        followed by ``=`` (with or without surrounding
        spaces).
        """
        from aed_lifecycle.no_stall import (
            _FIELD_NAME_NEXT_ACTIONS,
            is_valid_next_action,
        )
        for fname in sorted(_FIELD_NAME_NEXT_ACTIONS):
            for sep in ("=", " = "):
                value = f"{fname}{sep}somevalue"
                with self.subTest(fname=fname, sep=sep):
                    self.assertFalse(
                        is_valid_next_action(value),
                        f"expected {value!r} to be rejected",
                    )

    def test_all_field_names_rejected_as_field_colon_value(self) -> None:
        """Pin the full vocabulary: every member of
        ``_FIELD_NAME_NEXT_ACTIONS`` must be rejected when
        followed by ``:`` (with or without surrounding
        spaces).
        """
        from aed_lifecycle.no_stall import (
            _FIELD_NAME_NEXT_ACTIONS,
            is_valid_next_action,
        )
        for fname in sorted(_FIELD_NAME_NEXT_ACTIONS):
            for sep in (":", " : "):
                value = f"{fname}{sep}somevalue"
                with self.subTest(fname=fname, sep=sep):
                    self.assertFalse(
                        is_valid_next_action(value),
                        f"expected {value!r} to be rejected",
                    )

    # --- Helper-specific contract tests ---

    def test_field_assignment_collision_value_rejects_equals(self) -> None:
        from aed_lifecycle.no_stall import (
            _is_field_assignment_collision_value,
        )
        for v in (
            "checkpoint_path=/tmp/ckpt.json",
            "checkpoint_path = /tmp/ckpt.json",
            "checkpoint=/tmp/ckpt.json",
            "checkpoint = /tmp/ckpt.json",
            "terminal_state=MERGED",
            "terminal_state = MERGED",
        ):
            with self.subTest(v=v):
                self.assertTrue(
                    _is_field_assignment_collision_value(v),
                    f"expected {v!r} to be a collision",
                )

    def test_field_assignment_collision_value_rejects_colon(self) -> None:
        from aed_lifecycle.no_stall import (
            _is_field_assignment_collision_value,
        )
        for v in (
            "state: MERGED",
            "phase: PHASE_7",
            "next_step: continue",
            "next_action: poll CI",
        ):
            with self.subTest(v=v):
                self.assertTrue(
                    _is_field_assignment_collision_value(v),
                    f"expected {v!r} to be a collision",
                )

    def test_field_assignment_collision_value_accepts_text(self) -> None:
        from aed_lifecycle.no_stall import (
            _is_field_assignment_collision_value,
        )
        for v in (
            "checkpoint current run state",
            "state current PR status",
            "phase current retry window",
            "next_action review Codex response",
            "terminal_state current view",
        ):
            with self.subTest(v=v):
                self.assertFalse(
                    _is_field_assignment_collision_value(v),
                    f"expected {v!r} NOT to be a collision",
                )

    def test_field_assignment_collision_value_handles_garbage(self) -> None:
        from aed_lifecycle.no_stall import (
            _is_field_assignment_collision_value,
        )
        for v in ("", "   ", "=", ":", "= value", ": value",
                  "poll CI status", None, 123, [], {}):
            with self.subTest(v=v):
                self.assertFalse(
                    _is_field_assignment_collision_value(v),
                    f"expected {v!r} NOT to be a collision",
                )

    def test_field_assignment_collision_value_case_insensitive(self) -> None:
        from aed_lifecycle.no_stall import (
            _is_field_assignment_collision_value,
        )
        # Capitalized field names still trip the check.
        self.assertTrue(
            _is_field_assignment_collision_value("Checkpoint=val")
        )
        self.assertTrue(
            _is_field_assignment_collision_value("TERMINAL_STATE=MERGED")
        )
        # Capitalized field names followed by plain text
        # are accepted (case-insensitive comparison).
        self.assertFalse(
            _is_field_assignment_collision_value(
                "Checkpoint current run state"
            )
        )

    def test_field_assignment_collision_value_pads_whitespace(self) -> None:
        from aed_lifecycle.no_stall import (
            _is_field_assignment_collision_value,
        )
        self.assertTrue(
            _is_field_assignment_collision_value(
                "  checkpoint_path  =  /tmp/ckpt.json  "
            )
        )
        self.assertTrue(
            _is_field_assignment_collision_value(
                "  state  :  MERGED  "
            )
        )

    # --- validate_checkpoint integration ---

    def test_validate_checkpoint_accepts_legitimate_field_name_action(
        self,
    ) -> None:
        """A structurally valid checkpoint with a legitimate
        next_action that begins with a field-name word but
        has no assignment delimiter must NOT be flagged by
        ``validate_checkpoint``.
        """
        from aed_lifecycle.checkpoint import validate_checkpoint
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint current run state",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        errors = validate_checkpoint(state)
        self.assertFalse(
            any("next_action" in e for e in errors),
            f"unexpected next_action errors: {errors!r}",
        )

    def test_validate_checkpoint_rejects_field_assignment_value(self) -> None:
        """Pin: a checkpoint with ``next_action=
        'checkpoint_path=/tmp/ckpt.json'`` is still flagged.
        """
        from aed_lifecycle.checkpoint import validate_checkpoint
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint_path=/tmp/ckpt.json",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        errors = validate_checkpoint(state)
        self.assertTrue(
            any("next_action" in e for e in errors),
            f"expected next_action error, got {errors!r}",
        )

    # --- checkpoint_requires_operator integration ---

    def test_checkpoint_requires_operator_false_for_legitimate_action(
        self,
    ) -> None:
        """Pin: a structurally valid checkpoint with a
        legitimate ``next_action`` that begins with a
        field-name word does NOT require operator
        intervention. The runner may auto-resume.
        """
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint current run state",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        self.assertFalse(checkpoint_requires_operator(state))

    def test_checkpoint_requires_operator_true_for_field_assignment(
        self,
    ) -> None:
        """Pin: a structurally valid checkpoint with a
        field-assignment ``next_action`` still requires
        operator intervention.
        """
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint_path=/tmp/ckpt.json",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        self.assertTrue(checkpoint_requires_operator(state))

    # --- next_action_from_checkpoint integration ---

    def test_next_action_from_checkpoint_returns_legitimate_action(
        self,
    ) -> None:
        """Pin: ``next_action_from_checkpoint`` returns the
        legitimate action verbatim when the value is a real
        executable action that merely begins with a
        field-name word.
        """
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="state current PR status",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        result = next_action_from_checkpoint(state)
        self.assertEqual(result, "state current PR status")

    def test_next_action_from_checkpoint_holds_for_field_assignment(
        self,
    ) -> None:
        """Pin: ``next_action_from_checkpoint`` returns
        ``HOLD_OPERATOR_REQUIRED`` for a field-assignment
        collision.
        """
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="checkpoint_path=/tmp/ckpt.json",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        result = next_action_from_checkpoint(state)
        self.assertEqual(result, "HOLD_OPERATOR_REQUIRED")

    # --- evaluate_watchdog integration ---

    def test_evaluate_watchdog_accepts_legitimate_field_name_action(
        self,
    ) -> None:
        """Pin: a structurally valid WatchdogState with a
        legitimate next_action and a value-bearing checkpoint
        returns ``OK_PROGRESS_WITH_NEXT_ACTION`` even when the
        action begins with a field-name word.
        """
        state = WatchdogState(
            phase_name="PHASE_5_CI_POLL",
            started_at=0.0,
            last_progress_at=0.0,
            max_phase_seconds=1000.0,
            max_idle_seconds=1000.0,
            terminal_state=None,
            checkpoint_path="/tmp/ckpt.json",
            next_action="checkpoint current run state",
        )
        result = evaluate_watchdog(state, now=10.0)
        self.assertEqual(result, "OK_PROGRESS_WITH_NEXT_ACTION")

    def test_evaluate_watchdog_rejects_field_assignment_value(self) -> None:
        """Pin: a structurally valid WatchdogState with a
        field-assignment ``next_action`` does NOT return
        ``OK_PROGRESS_WITH_NEXT_ACTION`` — it returns
        ``STALL_RISK`` because the canonical
        :func:`is_valid_next_action` helper now treats the
        value as a field-assignment collision.
        """
        from aed_lifecycle.watchdog import STALL_RISK
        state = WatchdogState(
            phase_name="PHASE_5_CI_POLL",
            started_at=0.0,
            last_progress_at=0.0,
            max_phase_seconds=1000.0,
            max_idle_seconds=1000.0,
            terminal_state=None,
            checkpoint_path="/tmp/ckpt.json",
            next_action="checkpoint_path=/tmp/ckpt.json",
        )
        result = evaluate_watchdog(state, now=10.0)
        self.assertNotEqual(result, "OK_PROGRESS_WITH_NEXT_ACTION")
        self.assertEqual(result, STALL_RISK)

    # --- Existing final-output behavior remains pinned ---

    def test_final_output_pinned_collision_still_rejected(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )

    def test_final_output_pinned_legitimate_action_still_progress(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            "OK_PROGRESS_WITH_NEXT_ACTION",
        )


class StructuralValidityBeforeAutoResumeTests(unittest.TestCase):
    """Regression tests for Codex finding 3417410596 (Fix I).

    ``checkpoint_requires_operator`` must run
    ``validate_checkpoint`` (or equivalent structural
    validation) at the top, before any other branch. A
    checkpoint with structurally invalid required fields
    must never auto-resume, regardless of ``next_action``
    validity or ``terminal_state``. The previous
    implementation fell through to ``False`` when
    ``next_action`` was valid even when the structural
    fields were missing, empty, or wrong-typed — telling
    the caller that operator intervention was NOT
    required for a checkpoint the runner could not
    actually use.

    The structural-validity gate precedes:
      - the unknown-terminal-state hold
      - the completed-terminal-state short-circuit
        (MERGED, FAILED, PR_MERGED_AND_CLOSED_OUT)
      - the parked-terminal-state branch
        (HOLD_OPERATOR_REQUIRED, MERGE_READY_*, all HOLD_*)
      - the valid-next-action fast path
    """

    def _ck(self, **overrides):
        base = dict(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="a" * 40,
            phase="PHASE_5_CI_POLL",
            completed_phases=["PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"],
            next_phase="PHASE_6_CODEX_RE_REVIEW",
            next_action="poll CI status",
            pending_actions=[],
            last_verified_primary_head="0" * 40,
            last_verified_pr_head="a" * 40,
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at="2026-06-16T00:00:00Z",
        )
        base.update(overrides)
        return CheckpointState(**base)

    # --- Empty required string fields ---

    def test_empty_repo_with_valid_next_action_requires_operator(self) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(repo=""))
        )

    def test_empty_branch_with_valid_next_action_requires_operator(self) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(branch=""))
        )

    def test_empty_current_head_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(current_head=""))
        )

    def test_whitespace_repo_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(repo="   "))
        )

    def test_none_repo_with_valid_next_action_requires_operator(self) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(repo=None))  # type: ignore[arg-type]
        )

    # --- Non-string required fields ---

    def test_int_repo_with_valid_next_action_requires_operator(self) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(repo=123))  # type: ignore[arg-type]
        )

    def test_list_branch_with_valid_next_action_requires_operator(self) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(branch=[]))  # type: ignore[arg-type]
        )

    def test_dict_current_head_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(current_head={"a": 1})  # type: ignore[arg-type]
            )
        )

    # --- Invalid pr_number ---

    def test_none_pr_number_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(pr_number=None)  # type: ignore[arg-type]
            )
        )

    def test_zero_pr_number_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(pr_number=0))
        )

    def test_negative_pr_number_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(self._ck(pr_number=-1))
        )

    def test_string_pr_number_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(pr_number="405")  # type: ignore[arg-type]
            )
        )

    def test_bool_pr_number_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        # bool is a subclass of int — verify it is rejected.
        self.assertTrue(
            checkpoint_requires_operator(self._ck(pr_number=True))
        )

    # --- Invalid required list fields ---

    def test_string_completed_phases_with_valid_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(completed_phases="not a list")  # type: ignore[arg-type]
            )
        )

    def test_list_with_non_string_completed_phases_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(completed_phases=["PHASE_1", 2])  # type: ignore[list-item]
            )
        )

    # --- Invalid next_action when present ---

    def test_invalid_next_action_with_valid_structural_requires_operator(
        self,
    ) -> None:
        # Already covered by CheckpointRequiresOperatorInvalidNextActionTests;
        # included here to verify the structural gate does not
        # mask invalid next_action — both gates must run.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(next_action="none")
            )
        )

    # --- The positive case: structurally valid + valid next_action ---

    def test_structurally_valid_with_valid_next_action_does_not_require_operator(
        self,
    ) -> None:
        self.assertFalse(
            checkpoint_requires_operator(
                self._ck(next_action="poll CI status")
            )
        )

    def test_structurally_valid_with_no_next_action_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(next_action=None)
            )
        )

    # --- Completed terminal still requires operator if structurally broken ---

    def test_merged_terminal_with_empty_repo_requires_operator(self) -> None:
        # A completed terminal checkpoint with a broken
        # structural field MUST still surface to the operator.
        # There is no exception for completed terminal
        # states when the checkpoint is structurally broken.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(
                    repo="",
                    terminal_state="MERGED",
                    next_action=None,
                )
            )
        )

    def test_failed_terminal_with_invalid_pr_number_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(
                    pr_number=0,
                    terminal_state="FAILED",
                    next_action=None,
                )
            )
        )

    def test_merged_terminal_with_valid_structure_does_not_require_operator(
        self,
    ) -> None:
        # Pinned: the completed-terminal short-circuit
        # still works for structurally valid checkpoints.
        self.assertFalse(
            checkpoint_requires_operator(
                self._ck(terminal_state="MERGED", next_action=None)
            )
        )

    # --- Parked terminal still requires operator ---

    def test_hold_operator_required_with_valid_structure_requires_operator(
        self,
    ) -> None:
        # Pinned: the parked-terminal branch still works
        # for structurally valid checkpoints.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(
                    terminal_state="HOLD_OPERATOR_REQUIRED",
                    next_action="continue polling",
                )
            )
        )

    def test_merge_ready_awaiting_human_with_valid_structure_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(
                    terminal_state="MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
                    next_action="wait for human",
                )
            )
        )

    def test_hold_new_codex_thread_with_valid_structure_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(
                    terminal_state="HOLD_NEW_CODEX_THREAD",
                    next_action="wait for Codex",
                )
            )
        )

    # --- Combined broken structural + non-empty next_action ---

    def test_empty_repo_AND_valid_next_action_still_requires_operator(
        self,
    ) -> None:
        # The exact bug from Codex 3417410596.
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(repo="", next_action="poll CI status")
            )
        )

    def test_invalid_pr_number_AND_terminal_state_requires_operator(
        self,
    ) -> None:
        self.assertTrue(
            checkpoint_requires_operator(
                self._ck(
                    pr_number=-1,
                    terminal_state="MERGED",
                    next_action=None,
                )
            )
        )

    # --- Verify validate_checkpoint is called ---

    def test_validate_checkpoint_called_with_same_state(self) -> None:
        # Direct verification: passing a state with empty
        # repo returns True even with a fully-valid
        # next_action and a recognized completed terminal
        # state. This pins the exact code path of Fix I.
        from aed_lifecycle.checkpoint import validate_checkpoint
        state = self._ck(repo="", terminal_state="MERGED", next_action=None)
        # Sanity: validate_checkpoint reports the structural error.
        errors = validate_checkpoint(state)
        self.assertTrue(any("'repo'" in e for e in errors))
        # The fix: checkpoint_requires_operator must
        # agree with validate_checkpoint, regardless of
        # the completed-terminal short-circuit.
        self.assertTrue(checkpoint_requires_operator(state))


class FinalOutputFieldAssignmentRejectionTests(unittest.TestCase):
    """Regression tests for Codex finding 3438828758 (Fix N).

    The narrowed field-assignment collision check used by
    :func:`is_valid_next_action` must be applied to the
    final-output extractor
    :func:`_extract_next_action_value` so the parser and
    the persisted validator agree on what counts as a
    field-assignment collision. The previous per-token call
    :func:`_is_field_assignment_collision` only checked
    whether the bare first token was a field name, so a
    legitimate action like
    ``"next_action: checkpoint current run state"`` was
    wrongly rejected: the first token ``"checkpoint"`` IS
    a field name, but the next character is a space (not
    ``=`` or ``:``), so the value is a real executable
    action and the marker must be accepted. After Fix N:

    - The extractor uses the canonical
      :func:`_is_field_assignment_collision_value` helper
      (taking the full ``stripped`` remainder, not just the
      bare first token) so it can see the post-token
      context.
    - When the first word is a field name but the value is
      not a field assignment, the extractor returns the
      FULL value (not just the first word) so the caller's
      :func:`is_valid_next_action` can validate the
      multi-word action.
    - A field-assignment collision ``break``s the inner
      marker loop on the same line, preventing sub-marker
      re-parsing of a structured misuse.
    - The marker order in :data:`_NEXT_ACTION_TOKENS` is
      ``next_action:`` (colon) before ``next_action=``
      (equals) so the parser prefers the first occurrence
      of any marker in the line.
    """

    # --- Legitimate actions that begin with a field-name word
    #     are now ACCEPTED in final-output text ---

    def test_final_output_checkpoint_current_run_state_is_progress(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint current run state\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_final_output_state_current_pr_status_is_progress(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: state current PR status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_final_output_phase_current_retry_window_is_progress(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: phase current retry window\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_final_output_next_action_review_codex_response_is_progress(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: next_action review Codex response\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Real field-assignment collisions remain REJECTED ---

    def test_final_output_checkpoint_path_equals_value_rejected(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_final_output_terminal_state_equals_value_rejected(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: terminal_state=MERGED"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_final_output_state_colon_value_rejected(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: state: MERGED"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_final_output_phase_colon_value_rejected(self) -> None:
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: phase: PHASE_7"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_final_output_next_action_equals_value_rejected(self) -> None:
        """The ambiguous ``next_action: next_action=poll CI`` line
        must be rejected. Before Fix N, the parser found the
        ``next_action=`` sub-marker at position 13 and returned
        ``"poll"`` as the action; the message then incorrectly
        classified as ``OK_PROGRESS_WITH_NEXT_ACTION``. After
        Fix N, the colon-first marker ordering and the
        ``break`` after a field-assignment collision prevent
        sub-marker re-parsing of a structured misuse.
        """
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: next_action=poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Multi-marker (across lines) behavior pinned ---

    def test_multi_marker_collision_then_real_action_accepted(self) -> None:
        """The first marker is a field-assignment collision
        on line 1, the second marker is a real action on
        line 2, the third is a value-bearing checkpoint on
        line 3. The parser must skip the line-1 collision
        and accept the line-2 real action. The
        across-line ``scan past invalid markers`` behavior
        is preserved.
        """
        from aed_lifecycle.no_stall import classify_humphry_message_for_stall
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint_path=/tmp/ckpt.json\n"
            "next_action: checkpoint current run state\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Extractor direct tests ---

    def test_extractor_returns_full_value_for_field_name_action(
        self,
    ) -> None:
        """The extractor must return the FULL value (not
        just the first word) when the first word is a
        field name and the value is not a field
        assignment, so the caller's
        :func:`is_valid_next_action` can validate the
        multi-word action.
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        self.assertEqual(
            _extract_next_action_value(
                "next_action: checkpoint current run state"
            ),
            "checkpoint current run state",
        )
        self.assertEqual(
            _extract_next_action_value(
                "next_action: state current PR status"
            ),
            "state current PR status",
        )
        self.assertEqual(
            _extract_next_action_value(
                "next_action: phase current retry window"
            ),
            "phase current retry window",
        )

    def test_extractor_returns_first_word_for_non_field_name_action(
        self,
    ) -> None:
        """Pin the canonical extractor contract: for
        non-field-name first words, the extractor returns
        the first word (preserves existing test contracts).
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        self.assertEqual(
            _extract_next_action_value("next_action: poll CI status"),
            "poll",
        )
        self.assertEqual(
            _extract_next_action_value("next_action: poll Codex response"),
            "poll",
        )
        self.assertEqual(
            _extract_next_action_value(
                "next_action: resume from checkpoint"
            ),
            "resume",
        )

    def test_extractor_returns_none_for_field_assignment_collisions(
        self,
    ) -> None:
        """Pin: real field-assignment collisions in
        final-output text return ``None`` from the
        extractor, so the classifier does not see a
        valid next_action.
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        for v in (
            "next_action: checkpoint_path=/tmp/ckpt.json",
            "next_action: checkpoint_path = /tmp/ckpt.json",
            "next_action: checkpoint=/tmp/ckpt.json",
            "next_action: terminal_state=MERGED",
            "next_action: terminal_state = MERGED",
            "next_action: state: MERGED",
            "next_action: phase: PHASE_7",
            "next_action: next_action=poll CI",
        ):
            with self.subTest(v=v):
                self.assertIsNone(
                    _extract_next_action_value(v),
                    f"expected {v!r} to return None",
                )

    # --- Persisted validation tests from Fix M still pass
    #     (sanity: this is a final-output-only fix, the
    #     persisted validation contract is unchanged) ---

    def test_persisted_validation_unchanged_for_legitimate_action(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("checkpoint current run state")
        )
        self.assertTrue(
            is_valid_next_action("state current PR status")
        )

    def test_persisted_validation_unchanged_for_field_assignment(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )


class EarliestMarkerPositionSafeExtractionTests(unittest.TestCase):
    """Regression tests for Codex finding 3439399122 (Fix O).

    The final-output extractor
    :func:`_extract_next_action_value` must select the
    EARLIEST marker occurrence in the line by ``idx`` (with
    longer-marker tiebreak for the same ``idx``), NOT
    whichever marker appears first in the
    :data:`_NEXT_ACTION_TOKENS` tuple. Tuple-order priority
    is brittle and asymmetric: reordering the tuple to fix
    ``"next_action: next_action=poll CI"`` introduces the
    symmetric failure
    ``"next_action=next_action: poll CI"``. The fix is to
    walk all markers, collect the lowest ``idx`` (and the
    longest marker for ties), and parse that earliest
    occurrence only. Sub-markers inside an invalid earliest
    marker value CANNOT rescue the line; the across-line
    ``scan past invalid markers`` behavior is preserved by
    the outer ``for raw_line in text.splitlines()`` loop.
    """

    # --- Reject both symmetric forms of nested next_action
    #     field-assignment collisions ---

    def test_reject_next_action_colon_sub_marker_collision(self) -> None:
        """``next_action: next_action=poll CI`` (earliest
        marker is the colon-form ``next_action:``; its
        value is a field-assignment collision).
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: next_action=poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_action_equals_sub_marker_collision(self) -> None:
        """Symmetric case: ``next_action=next_action: poll CI``
        (earliest marker is the equals-form ``next_action=``;
        its value is a field-assignment collision).
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=next_action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_checkpoint_path_equals_via_colon_marker(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint_path=/tmp/ckpt.json\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_checkpoint_path_colon_via_equals_marker(self) -> None:
        """Symmetric case: ``next_action=checkpoint_path: /tmp/ckpt.json``
        (earliest marker is the equals-form ``next_action=``;
        its value is a field-assignment collision).
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=checkpoint_path: /tmp/ckpt.json\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_terminal_state_equals_via_colon_marker(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: terminal_state=MERGED\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_terminal_state_colon_via_equals_marker(self) -> None:
        """Symmetric case: ``next_action=terminal_state: MERGED``.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=terminal_state: MERGED\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Accept legitimate actions via both marker styles ---

    def test_accept_checkpoint_current_run_state_via_colon(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint current run state\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_accept_checkpoint_current_run_state_via_equals(self) -> None:
        """Symmetric: ``next_action=checkpoint current run state``
        uses the equals-form marker. Both marker forms must
        accept the same legitimate action.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=checkpoint current run state\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_accept_state_current_pr_status_via_colon(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: state current PR status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_accept_state_current_pr_status_via_equals(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=state current PR status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Multi-line recovery remains allowed ---

    def test_multiline_recovery_after_collision(self) -> None:
        """Line 1: ``next_action: next_action=poll CI``
        (collision, skip the line).
        Line 2: ``next_action: poll CI status`` (real action).
        Line 3: ``checkpoint_path=/tmp/ckpt.json``.
        The message must classify as
        ``OK_PROGRESS_WITH_NEXT_ACTION`` because the parser
        scans past the invalid line-1 marker and accepts the
        line-2 marker.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: next_action=poll CI\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_multiline_recovery_after_collision_equals_form(self) -> None:
        """Symmetric: line 1 uses the equals-form sub-marker
        collision.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=next_action: poll CI\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Same-line sub-marker rescue is NOT allowed ---

    def test_same_line_sub_marker_rescue_blocked(self) -> None:
        """``next_action: checkpoint_path=/tmp/ckpt.json; next_action: poll CI``
        must NOT be classified as
        ``OK_PROGRESS_WITH_NEXT_ACTION``. The earliest marker
        is the colon-form ``next_action:``; its value is a
        field-assignment collision. The sub-marker
        ``next_action: poll CI`` at a later position is
        inside the value of the earliest marker and cannot
        rescue the line.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: checkpoint_path=/tmp/ckpt.json; "
            "next_action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_same_line_sub_marker_rescue_blocked_equals_form(self) -> None:
        """Symmetric: ``next_action=checkpoint_path: /tmp/ckpt.json; next_action: poll CI``.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=checkpoint_path: /tmp/ckpt.json; "
            "next_action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_same_line_sub_marker_rescue_blocked_next_action_collision_colon(
        self,
    ) -> None:
        """``next_action: next_action=poll CI; next_action: poll CI status``
        — the earliest marker's value is a sub-marker
        field-assignment collision and the line is rejected
        even though a later real action appears on the same
        line.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action: next_action=poll CI; "
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_same_line_sub_marker_rescue_blocked_next_action_collision_equals(
        self,
    ) -> None:
        """Symmetric: ``next_action=next_action: poll CI; next_action: poll CI status``.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 — protected-state verification.\n"
            "next_action=next_action: poll CI; "
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Extractor direct tests for earliest-marker selection ---

    def test_extractor_selects_earliest_marker_colon_first(self) -> None:
        """For ``next_action: next_action=poll CI`` the
        earliest marker is the colon-form ``next_action:``
        at position 0; the extractor uses that marker's
        value (``next_action=poll CI``) which is a
        field-assignment collision, so the line is rejected
        and the extractor returns ``None``.
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        self.assertIsNone(
            _extract_next_action_value(
                "next_action: next_action=poll CI"
            )
        )

    def test_extractor_selects_earliest_marker_equals_first(self) -> None:
        """For ``next_action=next_action: poll CI`` the
        earliest marker is the equals-form ``next_action=``
        at position 0; the extractor uses that marker's
        value (``next_action: poll CI``) which is a
        field-assignment collision, so the line is rejected
        and the extractor returns ``None``.
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        self.assertIsNone(
            _extract_next_action_value(
                "next_action=next_action: poll CI"
            )
        )

    def test_extractor_rejects_value_with_nested_marker(self) -> None:
        """Fix P (Codex 3439619609): a value that contains
        another supported :data:`_NEXT_ACTION_TOKENS` marker
        is a nested-marker collision and must be rejected.
        For ``next_action: poll CI status; next_action: more text``
        the value ``poll CI status; next_action: more text``
        contains the marker ``next_action:`` at a later
        position. The narrowed field-assignment collision
        check does not fire (first token ``poll`` is not a
        protocol field name), but the canonical nested-
        marker substring check (Fix P) does fire, and the
        line is rejected.
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        self.assertIsNone(
            _extract_next_action_value(
                "next_action: poll CI status; next_action: more text"
            )
        )

    def test_extractor_returns_value_without_nested_marker(self) -> None:
        """The complement: a value that does NOT contain
        any supported marker is returned. Fix P is
        marker-token based, not broad-word based, so
        ordinary text like ``poll CI status; review next
        steps after CI`` is NOT rejected (the substring
        ``next step:`` is not in ``next steps``).
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        result = _extract_next_action_value(
            "next_action: poll CI status; review next steps after CI"
        )
        self.assertIsNotNone(result)
        self.assertIn("poll", result)

    # --- Persisted validation tests from Fix M still pass ---

    def test_persisted_validation_unchanged_for_legitimate_action(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("checkpoint current run state")
        )
        self.assertTrue(
            is_valid_next_action("state current PR status")
        )

    def test_persisted_validation_unchanged_for_field_assignment(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )


class NestedNextActionMarkerRejectionTests(unittest.TestCase):
    """Regression tests for Codex finding 3439619609 (Fix P).

    After the earliest-marker selection (Fix O), the
    extracted value can still contain a nested supported
    :data:`_NEXT_ACTION_TOKENS` marker. Examples:

    - ``next_action=next step: poll CI``
    - ``next_action=Next action: poll CI``
    - ``next_action=next action: poll CI``
    - ``next_action=Next step: poll CI``

    These are same-line nested-marker collisions and must
    NOT be accepted as valid executable actions. The
    narrowed field-assignment collision check (Fix N) does
    not fire for these cases because the first token of
    the value (``next`` / ``Next``) is not a protocol field
    name. The canonical
    :func:`_contains_nested_next_action_marker` helper
    performs a marker-token substring check on the full
    stripped value, so any literal marker occurrence is
    caught. The marker set is extended to include the
    missing variants ``Next step:`` and ``next action:``
    so the substring check finds the test cases listed in
    the Codex finding.

    Important: the check is marker-token based, NOT
    broad-word based. Ordinary text like ``review next
    steps after CI`` does NOT contain the marker
    ``next step:`` (it has ``next steps`` with a trailing
    ``s``), so the substring search correctly accepts it.
    """

    # --- Reject all documented nested-marker forms ---

    def test_reject_next_action_colon_sub_marker_collision(self) -> None:
        """``next_action: next_action=poll CI`` — value
        contains ``next_action=`` (the equals-form marker
        at a later position). Fix N's field-assignment
        check also fires here (first token ``next_action``
        is a field name), but Fix P's nested-marker check
        is the additional canonical contract.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action: next_action=poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_action_equals_sub_marker_collision(self) -> None:
        """``next_action=next_action: poll CI`` — value
        contains ``next_action:`` (the colon-form marker
        at a later position). Fix N's field-assignment
        check also fires here.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=next_action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_step_sub_marker_in_equals_value(self) -> None:
        """``next_action=next step: poll CI`` — value
        contains ``next step:`` (the spaced sub-marker).
        Fix N's field-assignment check does NOT fire
        because the first token ``next`` is not a protocol
        field name. Fix P's nested-marker check is the
        only thing that catches this.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=next step: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_step_capitalized_sub_marker(self) -> None:
        """``next_action=Next step: poll CI`` — value
        contains ``Next step:`` (capitalized spaced
        sub-marker). The marker set is extended to include
        ``Next step:`` so the substring check finds it.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=Next step: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_action_sub_marker_in_equals_value(self) -> None:
        """``next_action=next action: poll CI`` — value
        contains ``next action:`` (the spaced sub-marker
        with a different second word than ``next step:``).
        The marker set is extended to include
        ``next action:`` so the substring check finds it.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=next action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_action_capitalized_sub_marker(self) -> None:
        """``next_action=Next action: poll CI`` — value
        contains ``Next action:`` (capitalized spaced
        sub-marker). Already in the marker set.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=Next action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_step_sub_marker_in_colon_value(self) -> None:
        """``next_action: next step: poll CI`` — earliest
        marker is ``next_action:`` at position 0; value
        contains ``next step:`` at a later position.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action: next step: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_reject_next_action_capitalized_sub_marker_colon_value(
        self,
    ) -> None:
        """``next_action: Next action: poll CI`` — earliest
        marker is ``next_action:`` at position 0; value
        contains ``Next action:`` at a later position.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action: Next action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Accept legitimate actions that do NOT contain a marker ---

    def test_accept_review_next_steps_after_ci_colon(self) -> None:
        """``next_action: review next steps after CI`` does
        NOT contain any marker (the text has ``next steps``
        with a trailing ``s``, not ``next step:``). Fix P is
        marker-token based, not broad-word based.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action: review next steps after CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_accept_review_next_steps_after_ci_equals(self) -> None:
        """``next_action=review next steps after CI`` does
        NOT contain any marker. Accept via the equals-form
        marker.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=review next steps after CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Multi-line recovery ---

    def test_multiline_recovery_after_nested_marker_collision(self) -> None:
        """Line 1 has a nested-marker collision
        (``next_action=next step: poll CI``). Line 2 has a
        real action (``next_action: poll CI status``).
        Line 3 has a value-bearing checkpoint. The
        message must classify as
        ``OK_PROGRESS_WITH_NEXT_ACTION`` using the line-2
        action.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=next step: poll CI\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_multiline_recovery_after_capitalized_nested_marker(
        self,
    ) -> None:
        """Symmetric: line 1 has the capitalized variant
        ``next_action=Next action: poll CI``. Recovery
        from line 2.
        """
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Starting PHASE 1 \u2014 protected-state verification.\n"
            "next_action=Next action: poll CI\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Extractor direct tests for the canonical helper ---

    def test_helper_rejects_value_with_nested_marker(self) -> None:
        """Pin the canonical helper contract: a value
        containing any supported marker returns True.
        """
        from aed_lifecycle.no_stall import (
            _contains_nested_next_action_marker,
        )
        for v in (
            "next_action=next step: poll CI",
            "next_action=Next step: poll CI",
            "next_action=next action: poll CI",
            "next_action=Next action: poll CI",
            "next_action: next step: poll CI",
            "next_action: Next action: poll CI",
            "next_action: next_action=poll CI",
            "next_action=next_action: poll CI",
        ):
            with self.subTest(v=v):
                self.assertTrue(
                    _contains_nested_next_action_marker(v),
                    f"expected {v!r} to contain a nested marker",
                )

    def test_helper_accepts_value_without_nested_marker(self) -> None:
        """Pin the canonical helper contract: a value
        that does NOT contain any supported marker
        returns False. Ordinary text like ``review next
        steps after CI`` is NOT a marker-token collision
        (the substring ``next step:`` is not in
        ``next steps``).
        """
        from aed_lifecycle.no_stall import (
            _contains_nested_next_action_marker,
        )
        for v in (
            "poll CI status",
            "checkpoint current run state",
            "state current PR status",
            "review next steps after CI",
            "poll CI",
            "next steps after CI",
            "next actions to review",
        ):
            with self.subTest(v=v):
                self.assertFalse(
                    _contains_nested_next_action_marker(v),
                    f"expected {v!r} NOT to contain a nested marker",
                )

    def test_helper_handles_garbage(self) -> None:
        from aed_lifecycle.no_stall import (
            _contains_nested_next_action_marker,
        )
        for v in ("", "   ", None, 123, [], {}):
            with self.subTest(v=v):
                self.assertFalse(
                    _contains_nested_next_action_marker(v),
                    f"expected {v!r} NOT to contain a nested marker",
                )

    def test_extractor_rejects_value_with_nested_marker(self) -> None:
        """Pin the extractor contract: a line whose value
        contains a nested marker returns ``None`` from the
        extractor.
        """
        from aed_lifecycle.no_stall import _extract_next_action_value
        for v in (
            "next_action=next step: poll CI",
            "next_action=Next step: poll CI",
            "next_action=next action: poll CI",
            "next_action=Next action: poll CI",
            "next_action: next step: poll CI",
            "next_action: Next action: poll CI",
        ):
            with self.subTest(v=v):
                self.assertIsNone(
                    _extract_next_action_value(v),
                    f"expected {v!r} to be rejected",
                )

    # --- Persisted validation tests from Fix M still pass ---

    def test_persisted_validation_unchanged_for_legitimate_action(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(
            is_valid_next_action("checkpoint current run state")
        )
        self.assertTrue(
            is_valid_next_action("state current PR status")
        )

    def test_persisted_validation_unchanged_for_field_assignment(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )


class NestedOnlyMarkerSetSplitTests(unittest.TestCase):
    """Regression tests for Codex finding 3439736315 (Fix Q).

    Fix P (Codex 3439619609) added ``"next step:"``,
    ``"Next step:"``, ``"next action:"`` and
    ``"Next action:"`` to the top-level
    :data:`_NEXT_ACTION_TOKENS` set so the nested-marker
    substring check would catch forms like
    ``"next_action=next step: poll CI"``. But because the
    top-level :func:`_extract_next_action_value` scan
    matches any of these tokens anywhere in a line,
    ordinary prose like
    ``"Recommended next action: None. No repair needed."``
    or
    ``"Suggested next step: wait for CI."``
    can now be misclassified as
    OK_PROGRESS_WITH_NEXT_ACTION whenever a checkpoint
    path is also present.

    Fix Q splits the marker vocabulary into two sets:

    - :data:`_NEXT_ACTION_TOKENS` (top-level) — only the
      canonical protocol markers ``"next_action:"`` and
      ``"next_action="``. Anything else is prose.
    - :data:`_NESTED_NEXT_ACTION_MARKERS` (nested-only) —
      the full vocabulary including prose variants. Used
      only by :func:`_contains_nested_next_action_marker`
      to detect structured misuse inside an already
      extracted value.

    The top-level extractor is now narrow enough that
    ordinary prose never matches it, and the nested-marker
    check still catches the documented structured-misuse
    forms.
    """

    # --- Top-level extractor must NOT match prose ---

    def test_top_level_does_NOT_match_recommended_prose(self) -> None:
        """``Recommended next action: None. No repair needed.``
        plus a checkpoint path must NOT classify as
        OK_PROGRESS_WITH_NEXT_ACTION. The ``next action:``
        substring in the prose is NOT a top-level marker
        (it's nested-only)."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Recommended next action: None. No repair needed.\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_top_level_does_NOT_match_suggested_prose(self) -> None:
        """``Suggested next step: wait for CI.`` plus a
        checkpoint path must NOT classify as
        OK_PROGRESS_WITH_NEXT_ACTION. The ``next step:``
        substring in the prose is NOT a top-level marker
        (it's nested-only)."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Suggested next step: wait for CI.\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_top_level_does_NOT_match_capitalized_prose(self) -> None:
        """``Next step: continue once CI is green.`` plus
        a checkpoint path must NOT classify as
        OK_PROGRESS_WITH_NEXT_ACTION."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "Next step: continue once CI is green.\n"
            "checkpoint: /tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Top-level extractor still accepts canonical markers ---

    def test_top_level_still_matches_next_action_colon(self) -> None:
        """``next_action: poll CI status`` plus a checkpoint
        path still classifies as
        OK_PROGRESS_WITH_NEXT_ACTION. The canonical
        colon-form marker is in the top-level set."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_top_level_still_matches_next_action_equals(self) -> None:
        """``next_action=poll CI status`` plus a checkpoint
        path still classifies as
        OK_PROGRESS_WITH_NEXT_ACTION. The canonical
        equals-form marker is in the top-level set."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Nested-marker check still catches all documented forms ---

    def test_nested_check_still_rejects_equals_with_next_step(
        self,
    ) -> None:
        """``next_action=next step: poll CI`` plus a
        checkpoint path must still be rejected. The
        nested-marker check uses
        :data:`_NESTED_NEXT_ACTION_MARKERS` which still
        includes ``"next step:"``."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=next step: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_nested_check_still_rejects_equals_with_capital_step(
        self,
    ) -> None:
        """``next_action=Next step: poll CI`` plus a
        checkpoint path must still be rejected."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=Next step: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_nested_check_still_rejects_equals_with_next_action_prose(
        self,
    ) -> None:
        """``next_action=next action: poll CI`` plus a
        checkpoint path must still be rejected."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=next action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_nested_check_still_rejects_equals_with_capital_action_prose(
        self,
    ) -> None:
        """``next_action=Next action: poll CI`` plus a
        checkpoint path must still be rejected."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=Next action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_nested_check_still_rejects_colon_with_next_step(
        self,
    ) -> None:
        """``next_action: next step: poll CI`` plus a
        checkpoint path must still be rejected."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: next step: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_nested_check_still_rejects_colon_with_capital_action(
        self,
    ) -> None:
        """``next_action: Next action: poll CI`` plus a
        checkpoint path must still be rejected."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: Next action: poll CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Marker-set shape contracts ---

    def test_top_level_marker_set_is_narrow(self) -> None:
        """The top-level :data:`_NEXT_ACTION_TOKENS` set
        must contain ONLY the canonical protocol markers:
        ``next_action:`` and ``next_action=``. Any prose
        variant in this set would re-introduce the
        regression fixed by Fix Q (Codex 3439736315)."""
        from aed_lifecycle import no_stall
        self.assertEqual(
            set(no_stall._NEXT_ACTION_TOKENS),
            {"next_action:", "next_action="},
        )

    def test_nested_marker_set_contains_prose_variants(self) -> None:
        """The nested :data:`_NESTED_NEXT_ACTION_MARKERS`
        set must include the prose variants
        ``Next action:``, ``next step:``, ``Next step:``
        and ``next action:`` so the nested-marker check
        still catches all documented structured-misuse
        forms."""
        from aed_lifecycle import no_stall
        self.assertIn("next_action:", no_stall._NESTED_NEXT_ACTION_MARKERS)
        self.assertIn("Next action:", no_stall._NESTED_NEXT_ACTION_MARKERS)
        self.assertIn("next step:", no_stall._NESTED_NEXT_ACTION_MARKERS)
        self.assertIn("Next step:", no_stall._NESTED_NEXT_ACTION_MARKERS)
        self.assertIn("next action:", no_stall._NESTED_NEXT_ACTION_MARKERS)
        self.assertIn("next_action=", no_stall._NESTED_NEXT_ACTION_MARKERS)
        self.assertIn("next step=", no_stall._NESTED_NEXT_ACTION_MARKERS)

    def test_nested_marker_set_is_strict_superset_of_top_level(
        self,
    ) -> None:
        """The nested set must be a strict superset of
        the top-level set so every top-level marker is
        also caught by the nested-marker check."""
        from aed_lifecycle import no_stall
        top = set(no_stall._NEXT_ACTION_TOKENS)
        nested = set(no_stall._NESTED_NEXT_ACTION_MARKERS)
        self.assertTrue(top.issubset(nested))
        self.assertGreater(len(nested), len(top))

    # --- Real actions with prose-like substrings still pass ---

    def test_legitimate_action_with_next_steps_word_still_passes(
        self,
    ) -> None:
        """``next_action: review next steps after CI``
        plus a checkpoint path must still classify as
        OK_PROGRESS_WITH_NEXT_ACTION. The text contains
        ``next steps`` (with a trailing ``s``), which is
        NOT the marker ``next step:``."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: review next steps after CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_legitimate_action_with_equals_form_and_prose_substring(
        self,
    ) -> None:
        """``next_action=review next steps after CI`` plus
        a checkpoint path must still classify as
        OK_PROGRESS_WITH_NEXT_ACTION."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=review next steps after CI\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- later-line recovery after invalid nested-marker line ---

    def test_later_line_recovery_after_invalid_nested_marker_line(
        self,
    ) -> None:
        """Line 1: ``next_action=next step: poll CI``
        (rejected as nested-marker collision). Line 2:
        ``next_action: poll CI status`` (canonical,
        accepted). Line 3: ``checkpoint_path=/tmp/ckpt.json``
        (valid). The whole message should classify as
        OK_PROGRESS_WITH_NEXT_ACTION using the later
        canonical line."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=next step: poll CI\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- helper-level contracts ---

    def test_helper_contains_nested_uses_nested_set(self) -> None:
        """The :func:`_contains_nested_next_action_marker`
        helper must use :data:`_NESTED_NEXT_ACTION_MARKERS`,
        not :data:`_NEXT_ACTION_TOKENS`. This is the
        runtime guarantee that the nested-marker check
        continues to catch all documented forms after
        the top-level set is narrowed."""
        from aed_lifecycle import no_stall
        # The prose variant ``next step:`` is in the nested
        # set but NOT in the (now narrow) top-level set.
        # The helper should still detect it as a nested
        # marker.
        self.assertTrue(
            no_stall._contains_nested_next_action_marker(
                "next step: poll CI"
            )
        )
        # And prose is also caught.
        self.assertTrue(
            no_stall._contains_nested_next_action_marker(
                "Recommended next action: None. No repair needed."
            )
        )

    def test_extractor_only_scans_top_level_narrow_set(self) -> None:
        """:func:`_extract_next_action_value` must use only
        the narrow top-level set. A line that contains
        only ``next step:`` (no canonical ``next_action:``
        or ``next_action=``) must return ``None`` from the
        extractor."""
        from aed_lifecycle.no_stall import (
            _extract_next_action_value,
        )
        self.assertIsNone(
            _extract_next_action_value("next step: poll CI status")
        )
        self.assertIsNone(
            _extract_next_action_value(
                "Recommended next action: None. No repair needed."
            )
        )
        self.assertIsNone(
            _extract_next_action_value("Suggested next step: wait")
        )
        # But the canonical markers still extract (first
        # whitespace-delimited token after the marker).
        self.assertEqual(
            _extract_next_action_value(
                "next_action: poll CI status"
            ),
            "poll",
        )
        self.assertEqual(
            _extract_next_action_value(
                "next_action=poll CI status"
            ),
            "poll",
        )


class PunctuatedPlaceholderRejectionTests(unittest.TestCase):
    """Regression tests for Codex finding 3440952035 (Fix R).

    When a runner uses the canonical ``next_action:`` marker
    but writes a placeholder as prose — e.g.
    ``next_action: None. No repair needed.`` — the
    :func:`_extract_next_action_value` helper returns the
    first whitespace-delimited token after the marker, which
    is ``"None."`` (with a trailing period). The previous
    :func:`is_valid_next_action` implementation checked the
    placeholder set ``{"none", "null", ...}`` against the
    raw lowercased form, so ``"none."`` was NOT a member of
    the set and the validator accepted it as a real action.
    That made the classifier return
    OK_PROGRESS_WITH_NEXT_ACTION whenever a checkpoint
    path was also present, and let persisted
    ``next_action="none."`` pass validation — the runner
    could then try to resume with a punctuated placeholder
    instead of surfacing a stall/hold.

    Fix R strips a narrow set of trailing sentence-ending
    punctuation (``.,;:!?'\")}]}``) from the value before
    the placeholder and field-name checks, so a punctuated
    placeholder is recognised as the same placeholder it
    is when bare. The set is intentionally narrow: a
    legitimate action like ``"poll CI status."`` is
    rejected (the trailing period is a real sentence-ender
    and the action is incomplete), but ``"poll CI status"``
    continues to be accepted.
    """

    # --- is_valid_next_action rejects punctuated placeholders ---

    def test_period_suffix_none_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("none."))
        self.assertFalse(is_valid_next_action("None."))
        self.assertFalse(is_valid_next_action("NONE."))

    def test_exclamation_suffix_none_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("none!"))
        self.assertFalse(is_valid_next_action("none?"))

    def test_multiple_trailing_punctuation_none_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("none..."))
        self.assertFalse(is_valid_next_action("none!?!"))
        self.assertFalse(is_valid_next_action("none.;"))

    def test_punctuated_null_placeholder_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("null."))
        self.assertFalse(is_valid_next_action("NULL."))
        self.assertFalse(is_valid_next_action("nil."))

    def test_punctuated_todo_placeholder_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("todo."))
        self.assertFalse(is_valid_next_action("Todo."))
        self.assertFalse(is_valid_next_action("tbd."))
        self.assertFalse(is_valid_next_action("tba."))
        self.assertFalse(is_valid_next_action("n/a."))
        self.assertFalse(is_valid_next_action("na."))

    def test_punctuated_field_name_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        # Fix T (Codex 3441956963): a punctuated
        # field-name-looking word is now treated as a
        # first-token-of-real-action marker when the
        # raw stripped value is NOT a bare field name.
        # The raw form ``"checkpoint.`` is not in
        # ``_FIELD_NAME_NEXT_ACTIONS`` (which contains
        # only ``checkpoint``), and the wrapper-stripped
        # form ``checkpoint.`` is not a placeholder.
        # The token is accepted as the first token of a
        # real action.
        self.assertTrue(is_valid_next_action("checkpoint."))
        self.assertTrue(is_valid_next_action("phase."))
        # Bare (no trailing punctuation) is still rejected.
        self.assertFalse(is_valid_next_action("checkpoint"))
        self.assertFalse(is_valid_next_action("Checkpoint"))
        # But a field name with a trailing punctuation
        # followed by more text would be extracted as the
        # first token — same shape as the regression guard
        # test ``"checkpoint``. These are now accepted.

    # --- Legitimate action text without trailing punctuation still works ---

    def test_legitimate_action_no_trailing_punctuation_still_valid(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(is_valid_next_action("poll CI status"))
        self.assertTrue(is_valid_next_action("poll Codex response"))
        self.assertTrue(is_valid_next_action("continue bounded CI polling"))
        self.assertTrue(is_valid_next_action("review next steps after CI"))

    def test_bare_placeholders_still_rejected(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("none"))
        self.assertFalse(is_valid_next_action("null"))
        self.assertFalse(is_valid_next_action("None"))
        self.assertFalse(is_valid_next_action("todo"))
        self.assertFalse(is_valid_next_action("nil"))

    def test_empty_string_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action(""))
        self.assertFalse(is_valid_next_action("   "))

    def test_non_string_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action(None))
        self.assertFalse(is_valid_next_action(123))
        self.assertFalse(is_valid_next_action([]))
        self.assertFalse(is_valid_next_action({}))

    def test_leading_whitespace_stripped_then_checked(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("  none.  "))
        self.assertFalse(is_valid_next_action("\tnone.\n"))
        self.assertTrue(is_valid_next_action("  poll CI status  "))

    # --- Classifier / message-level integration ---

    def test_classifier_does_NOT_classify_punctuated_none_as_progress(
        self,
    ) -> None:
        """The headline case from the Codex finding.
        ``next_action: None. No repair needed.`` plus
        ``checkpoint_path=/tmp/ckpt.json`` must NOT classify
        as OK_PROGRESS_WITH_NEXT_ACTION. The extracted value
        is ``None.`` (the first whitespace-delimited token
        after the marker), which Fix R recognises as a
        punctuated placeholder."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: None. No repair needed.\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_does_NOT_classify_punctuated_equals_none(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action=None. Nothing to do.\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_does_NOT_classify_punctuated_todo(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: todo. wait for codex.\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_does_NOT_classify_punctuated_null(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: null. nothing pending.\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_still_accepts_legitimate_action_with_prose(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        # The next_action value is ``poll`` (first token
        # after the marker). The trailing prose "CI status"
        # is not part of the extracted value.
        text = (
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_later_line_recovery_after_punctuated_placeholder(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        # Line 1 has a punctuated placeholder, which is
        # rejected. Line 2 has a canonical real action, which
        # is accepted.
        text = (
            "next_action: None. No repair needed.\n"
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Watchdog / checkpoint integration (pinned) ---

    def test_punctuated_placeholder_does_NOT_pass_persisted_validation(
        self,
    ) -> None:
        """Pinned: persisted ``next_action='none.'`` must
        not pass validation. The same canonical
        :func:`is_valid_next_action` helper is used by
        :func:`validate_checkpoint`,
        :func:`next_action_from_checkpoint`,
        :func:`checkpoint_requires_operator`, and
        :func:`evaluate_watchdog`, so the punctuation-strip
        applies uniformly. A persisted value of ``none.``
        is treated the same as ``none``. Fix T
        (Codex 3441956963) regression guard: a value of
        ``"checkpoint"`` is now accepted (treated as a
        quoted first token of a real action), but bare
        ``checkpoint`` is still rejected and
        ``"checkpoint."`` is accepted as a quoted
        punctuated first token."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("none."))
        self.assertFalse(is_valid_next_action("None."))
        self.assertFalse(is_valid_next_action("null."))
        self.assertFalse(is_valid_next_action("todo."))
        # Fix T: quoted form is no longer rejected when
        # the underlying bare form is a field-name word
        # (treated as a quoted first token of a real
        # action).
        self.assertTrue(is_valid_next_action('"checkpoint"'))
        # But the bare form is still rejected.
        self.assertFalse(is_valid_next_action("checkpoint"))

    def test_punctuation_constant_is_narrow(self) -> None:
        """The punctuation set is intentionally narrow.
        It contains sentence-end marks and structural
        terminators, NOT characters that would corrupt
        real action values like ``"poll CI status"`` or
        ``"review next steps"``."""
        from aed_lifecycle import no_stall
        punct = no_stall._PLACEHOLDER_TRAILING_PUNCTUATION
        # Must contain sentence-end marks.
        self.assertIn(".", punct)
        self.assertIn("!", punct)
        self.assertIn("?", punct)
        # Must contain some structural terminators.
        self.assertIn(",", punct)
        self.assertIn(";", punct)
        # Must NOT contain characters that would strip
        # legitimate action text.
        self.assertNotIn(" ", punct)
        self.assertNotIn("\n", punct)
        self.assertNotIn("\t", punct)
        # Must not contain alphabetic or numeric characters.
        for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
            self.assertNotIn(ch, punct)


class LeadingWrapperPlaceholderRejectionTests(unittest.TestCase):
    """Regression tests for Codex finding 3441855393 (Fix S).

    When a runner quotes or brackets the placeholder — e.g.
    ``next_action: "None."`` or ``next_action: [none.]`` —
    :func:`_extract_next_action_value` passes the wrapped
    token to :func:`is_valid_next_action`. The previous
    Fix R (Codex 3440952035) implementation only stripped
    trailing sentence-ending punctuation, so a wrapped
    placeholder like ``"None."`` or ``[none.]`` was
    extracted as ``"None."`` or ``[none.`` (with a
    leading wrapper still attached), the placeholder set
    did not contain those wrapped forms, and the
    classifier still returned
    OK_PROGRESS_WITH_NEXT_ACTION whenever a checkpoint
    path was present. Final agent output is free-form
    text and quoted field values are common, so the
    canonical validator must also strip matching leading
    wrapper characters before checking the placeholder
    set.

    Fix S strips a narrow set of leading wrapper
    characters (``"'([{``) from the value before the
    placeholder and field-name checks, so wrapped or
    quoted placeholders are recognised as the same
    placeholder they are when bare. The set is
    intentionally narrow: only the standard ASCII
    opening delimiters and quote characters a runner is
    likely to wrap a placeholder in.
    """

    # --- is_valid_next_action rejects wrapped placeholders ---

    def test_double_quote_wrapper_none_period_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action('"None."'))
        self.assertFalse(is_valid_next_action('"none."'))
        self.assertFalse(is_valid_next_action('"NONE."'))

    def test_single_quote_wrapper_none_period_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("'None.'"))
        self.assertFalse(is_valid_next_action("'none.'"))

    def test_bracket_wrapper_none_period_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("[none.]"))
        self.assertFalse(is_valid_next_action("[None.]"))
        self.assertFalse(is_valid_next_action("[nil.]"))
        self.assertFalse(is_valid_next_action("[null.]"))

    def test_paren_wrapper_none_period_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("(none.)"))
        self.assertFalse(is_valid_next_action("(None.)"))

    def test_brace_wrapper_none_period_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("{none.}"))
        self.assertFalse(is_valid_next_action("{None.}"))

    def test_nested_wrappers_none_period_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action('"[none.]"'))
        self.assertFalse(is_valid_next_action("'[[none.]]'"))
        self.assertFalse(is_valid_next_action("((none.))"))

    def test_wrapped_placeholders_no_trailing_punct_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action('"none"'))
        self.assertFalse(is_valid_next_action("'none'"))
        self.assertFalse(is_valid_next_action("[none]"))
        self.assertFalse(is_valid_next_action("(none)"))
        self.assertFalse(is_valid_next_action("{none}"))

    def test_wrapped_other_placeholders_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action('"null."'))
        self.assertFalse(is_valid_next_action('"todo."'))
        self.assertFalse(is_valid_next_action('"tbd"'))
        self.assertFalse(is_valid_next_action("[n/a]"))
        self.assertFalse(is_valid_next_action("(tba)"))

    def test_wrapped_field_names_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        # Fix T (Codex 3441956963): wrapping a bare field
        # name in a quote is no longer rejected. The
        # wrapper-stripped form is used ONLY for the
        # placeholder check. The field-name check uses the
        # RAW stripped value (``"checkpoint`` is NOT in the
        # field-name set, only the bare ``checkpoint``
        # is). A quoted field-name-like token is treated
        # as the first token of a real quoted action
        # (``"checkpoint current run state"``), not as a
        # bare field name. Bare (unquoted) field names are
        # still rejected (covered by other tests).
        self.assertTrue(is_valid_next_action('"checkpoint"'))
        self.assertTrue(is_valid_next_action('"phase"'))
        self.assertTrue(is_valid_next_action('"next_action"'))
        self.assertTrue(is_valid_next_action('"terminal_state"'))
        # Bare form (no wrapper) IS still rejected.
        self.assertFalse(is_valid_next_action("checkpoint"))
        self.assertFalse(is_valid_next_action("phase"))

    # --- Legitimate action text without wrappers still works ---

    def test_legitimate_action_no_wrappers_still_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(is_valid_next_action("poll CI status"))
        self.assertTrue(is_valid_next_action("poll Codex response"))
        self.assertTrue(is_valid_next_action("continue bounded CI polling"))
        self.assertTrue(is_valid_next_action("review next steps after CI"))

    def test_legitimate_action_starting_with_quote_word_still_valid(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        # The leading character is not in the wrapper
        # alphabet. The token does not look like a wrapped
        # placeholder.
        self.assertTrue(is_valid_next_action('"poll CI status"'))
        self.assertTrue(is_valid_next_action('"continue polling"'))
        # The first character after the quote is not a
        # placeholder, so the wrapper is preserved.
        self.assertTrue(is_valid_next_action('"review steps"'))

    def test_bare_placeholders_still_rejected(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("none"))
        self.assertFalse(is_valid_next_action("null"))
        self.assertFalse(is_valid_next_action("None"))
        self.assertFalse(is_valid_next_action("todo"))
        self.assertFalse(is_valid_next_action("nil"))

    def test_punctuated_placeholders_still_rejected(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        # Pinned from Fix R: the trailing-period form.
        self.assertFalse(is_valid_next_action("none."))
        self.assertFalse(is_valid_next_action("null."))
        self.assertFalse(is_valid_next_action("todo."))

    # --- Classifier / message-level integration ---

    def test_classifier_rejects_quoted_punctuated_placeholder(
        self,
    ) -> None:
        """The headline case from the Codex finding.
        ``next_action: \"None.\"`` plus a checkpoint path
        must NOT classify as
        OK_PROGRESS_WITH_NEXT_ACTION. The extracted value
        is ``\"None.\"`` (the first whitespace-delimited
        token after the marker), which Fix S recognises as
        a wrapped punctuated placeholder."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "None."\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_rejects_bracketed_punctuated_placeholder(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: [none.]\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_rejects_paren_wrapped_placeholder(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: (None.)\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_rejects_single_quote_wrapped_placeholder(
        self,
    ) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: 'todo.'\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_rejects_brace_wrapped_placeholder(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: {null}\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_still_accepts_legitimate_action(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_later_line_recovery_after_wrapped_placeholder(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        # Line 1: wrapped placeholder, rejected. Line 2:
        # canonical real action, accepted.
        text = (
            'next_action: "None."\n'
            "next_action: poll CI status\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Watchdog / checkpoint integration (pinned) ---

    def test_wrapped_placeholder_does_NOT_pass_persisted_validation(
        self,
    ) -> None:
        """Pinned: persisted ``next_action='"None."'`` must
        not pass validation. The same canonical
        :func:`is_valid_next_action` helper is used by
        :func:`validate_checkpoint`,
        :func:`next_action_from_checkpoint`,
        :func:`checkpoint_requires_operator`, and
        :func:`evaluate_watchdog`, so the leading-wrapper
        strip applies uniformly. A persisted value of
        ``'"None."'`` is treated the same as ``'None.'``
        (Fix R) and ``'None'`` (Fix D). Fix T
        (Codex 3441956963) regression guard: a value of
        ``'"checkpoint"'`` is now accepted (treated as a
        quoted first token of a real action), but bare
        ``checkpoint`` is still rejected."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action('"None."'))
        self.assertFalse(is_valid_next_action('"none."'))
        self.assertFalse(is_valid_next_action("[none.]"))
        self.assertFalse(is_valid_next_action("'null.'"))
        self.assertFalse(is_valid_next_action("(todo)"))
        # Fix T: quoted field-name-looking tokens are
        # accepted as quoted first tokens of real actions.
        self.assertTrue(is_valid_next_action('"checkpoint"'))
        # But the bare form is still rejected.
        self.assertFalse(is_valid_next_action("checkpoint"))

    def test_leading_wrapper_constant_is_narrow(self) -> None:
        """The leading-wrapper set is intentionally narrow.
        It contains the standard ASCII opening delimiters
        and quote characters a runner is likely to wrap a
        placeholder in, NOT arbitrary characters that would
        corrupt real action values."""
        from aed_lifecycle import no_stall
        wraps = no_stall._PLACEHOLDER_LEADING_WRAPPERS
        # Must contain the standard opening delimiters.
        self.assertIn('"', wraps)
        self.assertIn("'", wraps)
        self.assertIn("(", wraps)
        self.assertIn("[", wraps)
        self.assertIn("{", wraps)
        # Must NOT contain characters that would strip
        # legitimate action text.
        self.assertNotIn(" ", wraps)
        self.assertNotIn("\n", wraps)
        self.assertNotIn("\t", wraps)
        # Must not contain closing delimiters (those are
        # trailing, not leading).
        self.assertNotIn(")", wraps)
        self.assertNotIn("]", wraps)
        self.assertNotIn("}", wraps)
        # Must not contain alphabetic or numeric characters.
        for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
            self.assertNotIn(ch, wraps)


class QuotedFieldNameActionRegressionGuardTests(unittest.TestCase):
    """Regression tests for Codex finding 3441956963 (Fix T).

    Fix S (Codex 3441855393) added unconditional leading
    wrapper stripping to :func:`is_valid_next_action`.
    That fix correctly caught wrapped placeholders like
    ``"None."`` and ``[none.]``, but it also introduced
    a regression: when a runner quotes a real action whose
    first word is a protocol field name (e.g.
    ``next_action: "checkpoint current run state"``), the
    extractor returns the first whitespace-delimited token
    (``"checkpoint``) and the unconditional wrapper strip
    turned it into bare ``checkpoint``, which the
    field-name check rejected. The classifier then fell
    to ``STALL_NO_CHECKPOINT`` even though the unquoted
    action and the full persisted quoted value were both
    valid.

    Fix T keeps Fix S's wrapper strip for the placeholder
    check (so ``"None."`` is still rejected) but uses the
    RAW stripped value (no wrapper strip) for the
    field-name check (so ``"checkpoint`` is not
    rejected just because it starts with a quote). The
    contract is now:

    - ``stripped`` after wrapper-strip is in the
      placeholder set → reject (wrapped placeholder).
    - ``stripped`` (raw) is a bare field name → reject.
    - everything else → accept (real action).
    """

    # --- Real quoted actions whose first word is a field name ---

    def test_quoted_checkpoint_field_name_word_accepted(self) -> None:
        """``"checkpoint`` (a quoted first token of a
        real action like ``"checkpoint current run
        state"``) is a valid action. The raw stripped
        value ``"checkpoint`` is NOT a member of
        :data:`_FIELD_NAME_NEXT_ACTIONS` (which contains
        only the bare form ``checkpoint``), and the
        wrapper-stripped form ``checkpoint`` is NOT a
        placeholder."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(is_valid_next_action('"checkpoint'))
        self.assertTrue(is_valid_next_action('"phase'))
        self.assertTrue(is_valid_next_action('"state'))
        self.assertTrue(is_valid_next_action('"terminal'))
        self.assertTrue(is_valid_next_action('"lifecycle'))
        self.assertTrue(is_valid_next_action('"next_action'))
        self.assertTrue(is_valid_next_action('"next_step'))

    def test_bare_field_name_still_rejected(self) -> None:
        """The bare form of a field name is still
        rejected. Fix T is a regression guard, not a
        relaxation of the field-name check."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("checkpoint"))
        self.assertFalse(is_valid_next_action("phase"))
        self.assertFalse(is_valid_next_action("state"))
        self.assertFalse(is_valid_next_action("terminal"))
        self.assertFalse(is_valid_next_action("lifecycle"))
        self.assertFalse(is_valid_next_action("next_action"))
        self.assertFalse(is_valid_next_action("next_step"))

    # --- Classifier / message-level integration ---

    def test_classifier_accepts_quoted_field_name_action(self) -> None:
        """The headline case from the Codex finding.
        ``next_action: "checkpoint current run state"``
        plus ``checkpoint_path=/tmp/ckpt.json`` must
        classify as OK_PROGRESS_WITH_NEXT_ACTION. The
        extracted value is ``"checkpoint`` (first
        whitespace-delimited token after the marker,
        including the leading quote), which Fix T
        recognises as a real quoted action — not a
        wrapped placeholder, not a bare field name."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "checkpoint current run state"\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_accepts_quoted_phase_field_name_action(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "phase transition to next run"\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_accepts_quoted_state_field_name_action(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "state current PR status"\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_still_rejects_bare_field_name(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: checkpoint\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- All prior-fix tests still pass ---

    def test_fix_r_punctuated_placeholders_still_rejected(self) -> None:
        """Pinned from Fix R: the trailing-period form."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action("none."))
        self.assertFalse(is_valid_next_action("null."))
        self.assertFalse(is_valid_next_action("todo."))
        self.assertFalse(is_valid_next_action("None."))

    def test_fix_s_wrapped_placeholders_still_rejected(self) -> None:
        """Pinned from Fix S: the wrapped-placeholder form."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(is_valid_next_action('"None."'))
        self.assertFalse(is_valid_next_action('"none."'))
        self.assertFalse(is_valid_next_action("[none.]"))
        self.assertFalse(is_valid_next_action("'null.'"))
        self.assertFalse(is_valid_next_action("(todo)"))
        self.assertFalse(is_valid_next_action('"none"'))
        self.assertFalse(is_valid_next_action("'none'"))
        self.assertFalse(is_valid_next_action("[none]"))
        self.assertFalse(is_valid_next_action("(none)"))
        self.assertFalse(is_valid_next_action("{none}"))

    def test_legitimate_action_no_wrappers_still_valid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertTrue(is_valid_next_action("poll CI status"))
        self.assertTrue(is_valid_next_action("poll Codex response"))
        self.assertTrue(is_valid_next_action("continue bounded CI polling"))
        self.assertTrue(is_valid_next_action("review next steps after CI"))

    def test_field_assignment_collision_still_rejected(self) -> None:
        """Pinned from Fix L/M: the field-assignment
        collision form. With Fix T, the field-name
        check uses the raw stripped value, but the
        field-assignment collision check still uses
        the full value and rejects the ``=``/``:`` form."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )
        self.assertFalse(
            is_valid_next_action("checkpoint_path = /tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("phase: PHASE_7")
        )

    def test_later_line_recovery_after_wrapped_field_name(self) -> None:
        """A real quoted action whose first word is a
        field name is accepted on the same line; no
        recovery needed."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "checkpoint current run state"\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )


class QuotedFieldAssignmentRejectionTests(unittest.TestCase):
    """Regression tests for Codex finding 3442047933 (Fix U).

    When a runner quotes a field-assignment value — e.g.
    ``next_action: "checkpoint_path=/tmp/ckpt.json"`` plus
    a valid ``checkpoint_path`` on another line — the
    extracted first whitespace-delimited token is
    ``"checkpoint_path=/tmp/ckpt.json`` (with the leading
    quote still attached). The previous
    :func:`_is_field_assignment_collision_value` check ran
    :func:`_first_field_name_token` on the raw stripped
    value; the helper treats the leading quote as a
    boundary character and returns ``None``, so the
    collision check passed and the validator accepted the
    quoted field-assignment value as a real action. The
    same bypass applied to persisted
    ``next_action='"terminal_state=MERGED"'``. The
    unquoted case (``checkpoint_path=/tmp/ckpt.json``) was
    already correctly rejected as non-executable protocol
    syntax.

    Fix U runs the field-assignment collision check on
    BOTH the raw stripped value AND the wrapper-stripped
    form, so a quoted field-assignment value is
    recognised as the same collision it is when bare.
    """

    # --- is_valid_next_action rejects quoted field assignments ---

    def test_quoted_checkpoint_path_equals_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action('"checkpoint_path=/tmp/ckpt.json"')
        )
        self.assertFalse(
            is_valid_next_action('"checkpoint_path = /tmp/ckpt.json"')
        )
        self.assertFalse(
            is_valid_next_action('"checkpoint=/tmp/ckpt.json"')
        )

    def test_quoted_terminal_state_equals_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action('"terminal_state=MERGED"')
        )
        self.assertFalse(
            is_valid_next_action('"terminal_state = MERGED"')
        )
        self.assertFalse(
            is_valid_next_action('"state: MERGED"')
        )
        self.assertFalse(
            is_valid_next_action('"phase: PHASE_7"')
        )

    def test_quoted_next_action_equals_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action('"next_action=poll CI"')
        )
        self.assertFalse(
            is_valid_next_action('"next_step=continue"')
        )

    def test_quoted_colon_form_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action('"phase: PHASE_7"')
        )
        self.assertFalse(
            is_valid_next_action('"state: MERGED"')
        )
        self.assertFalse(
            is_valid_next_action('"checkpoint: /tmp/ckpt.json"')
        )

    def test_bracketed_field_assignment_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action(
                '[checkpoint_path=/tmp/ckpt.json]'
            )
        )
        self.assertFalse(
            is_valid_next_action('(terminal_state=MERGED)')
        )
        self.assertFalse(
            is_valid_next_action('{phase: PHASE_7}')
        )

    def test_paren_wrapped_field_assignment_is_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action('(checkpoint=/tmp/ckpt.json)')
        )

    # --- Unquoted form still rejected (regression pinned from Fix M) ---

    def test_unquoted_field_assignment_still_invalid(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )
        self.assertFalse(
            is_valid_next_action("checkpoint_path = /tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("phase: PHASE_7")
        )

    # --- Legitimate quoted actions still accepted (regression pinned from Fix T) ---

    def test_quoted_legitimate_action_still_accepted(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        # Quoted real action whose first word is NOT a
        # field name and has no field-assignment form.
        self.assertTrue(
            is_valid_next_action('"poll CI status"')
        )
        self.assertTrue(
            is_valid_next_action('"continue bounded CI polling"')
        )
        self.assertTrue(
            is_valid_next_action('"review next steps after CI"')
        )

    def test_quoted_field_name_action_still_accepted(self) -> None:
        from aed_lifecycle.no_stall import is_valid_next_action
        # Quoted real action whose first word IS a
        # field name but has no field-assignment form
        # (no `=` or `:`). Fix T regression guard.
        self.assertTrue(
            is_valid_next_action('"checkpoint current run state"')
        )
        self.assertTrue(
            is_valid_next_action('"phase transition to next run"')
        )
        self.assertTrue(
            is_valid_next_action('"state current PR status"')
        )

    # --- Classifier / message-level integration ---

    def test_classifier_rejects_quoted_field_assignment(self) -> None:
        """The headline case from the Codex finding.
        ``next_action: "checkpoint_path=/tmp/ckpt.json"``
        plus ``checkpoint_path=/tmp/other.json`` on
        another line must NOT classify as
        OK_PROGRESS_WITH_NEXT_ACTION. The extracted
        value is ``"checkpoint_path=/tmp/ckpt.json``
        (first whitespace-delimited token after the
        marker, including the leading quote), which
        Fix U recognises as a quoted field-assignment
        collision."""
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "checkpoint_path=/tmp/ckpt.json"\n'
            "checkpoint_path=/tmp/other.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_rejects_quoted_terminal_state(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "terminal_state=MERGED"\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_rejects_bracketed_field_assignment(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            "next_action: [phase: PHASE_7]\n"
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertNotEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_still_accepts_legitimate_quoted_action(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "poll CI status"\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_classifier_still_accepts_quoted_field_name_action(self) -> None:
        from aed_lifecycle.no_stall import (
            classify_humphry_message_for_stall,
        )
        text = (
            'next_action: "checkpoint current run state"\n'
            "checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    # --- Persisted validation pinned (Fix L/M/S/T integration) ---

    def test_persisted_quoted_field_assignment_rejected(self) -> None:
        """Pinned: persisted ``next_action='"checkpoint_path=/tmp/ckpt.json"'``
        must not pass validation. The same canonical
        :func:`is_valid_next_action` helper is used by
        :func:`validate_checkpoint`,
        :func:`next_action_from_checkpoint`,
        :func:`checkpoint_requires_operator`, and
        :func:`evaluate_watchdog`, so the quoted
        field-assignment check applies uniformly. Fix U
        contracts: the wrapper-stripped form of
        :func:`_is_field_assignment_collision_value` is
        also checked, so a quoted field-assignment
        value is recognised as the same collision it is
        when bare. The field-name check (Fix T) uses
        only the raw form so a real quoted action whose
        first word is a field name — e.g.
        ``"checkpoint"`` (treated as a quoted first
        token of a real action) — is still accepted."""
        from aed_lifecycle.no_stall import is_valid_next_action
        self.assertFalse(
            is_valid_next_action('"checkpoint_path=/tmp/ckpt.json"')
        )
        self.assertFalse(
            is_valid_next_action('"terminal_state=MERGED"')
        )
        self.assertFalse(
            is_valid_next_action('"phase: PHASE_7"')
        )
        # Pinned from earlier fixes.
        self.assertFalse(
            is_valid_next_action('"None."')
        )
        self.assertFalse(
            is_valid_next_action('"none"')
        )
        # Fix T contract: a quoted bare field-name word is
        # accepted (treated as a quoted first token of a
        # real action). The field-name check uses only
        # the raw form.
        self.assertTrue(
            is_valid_next_action('"checkpoint"')
        )
        # Bare forms still rejected.
        self.assertFalse(
            is_valid_next_action("checkpoint_path=/tmp/ckpt.json")
        )
        self.assertFalse(
            is_valid_next_action("terminal_state=MERGED")
        )
        # Pinned: legitimate forms still accepted.
        self.assertTrue(
            is_valid_next_action('"poll CI status"')
        )
        self.assertTrue(
            is_valid_next_action('"checkpoint current run state"')
        )
        self.assertTrue(
            is_valid_next_action("poll CI status")
        )


if __name__ == "__main__":
    unittest.main()

class SentenceCasedCheckpointTokensBroadCheckTests(unittest.TestCase):
    """Regression tests for Codex 3442251126 (Fix W).

    The broad ``_CHECKPOINT_TOKENS`` substring scan is used by
    the phase-header branch of the classifier to decide
    whether a message mentions a checkpoint in any form. The
    strict value-bearing extractor
    (``_extract_checkpoint_value``) already accepted
    sentence-cased prose forms (Fix J, Codex 3420268720), but
    the broad check remained case-sensitive and missed the
    same sentence-cased forms. As a result, a phase-header
    message like::

        Starting PHASE 3 — Checkpoint: /tmp/ckpt.json

    fell through to ``STALL_PHASE_HEADER_ONLY`` even though a
    real checkpoint path was present — a runner that wrote a
    valid checkpoint in sentence-cased prose would be
    misclassified as a pure phase-header-only stall instead
    of the documented ``STALL_NO_TERMINAL_STATE`` (broad
    ``has_checkpoint`` is True, ``has_next_action`` is False).

    The broad token list now also includes the
    sentence-cased variants (``Wrote checkpoint to``,
    ``Saved checkpoint to``, ``Checkpoint: ``,
    ``Checkpoint file``, ``Checkpoint at``,
    ``Checkpoint saved to``, ``Checkpoint=``,
    ``Checkpoint_path=``, ``Checkpoint_path:``,
    ``Checkpoint path=``, ``Checkpoint path:``) so the broad
    check matches what the strict extractor already accepts.
    """

    def test_phase_header_with_capitalized_wrote_checkpoint_to_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Wrote checkpoint to /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + sentence-cased prose checkpoint should NOT be "
            f"STALL_PHASE_HEADER_ONLY (broad has_checkpoint should be True), "
            f"got {verdict!r}",
        )
        self.assertEqual(
            verdict,
            STALL_NO_TERMINAL_STATE,
            f"phase header + sentence-cased prose checkpoint (no next_action) "
            f"should be STALL_NO_TERMINAL_STATE, got {verdict!r}",
        )

    def test_phase_header_with_capitalized_saved_checkpoint_to_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Saved checkpoint to /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Saved checkpoint to' should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_capitalized_checkpoint_colon_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint: /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint: ' should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_capitalized_checkpoint_file_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint file /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint file' should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_capitalized_checkpoint_at_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint at /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint at' should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_capitalized_checkpoint_saved_to_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint saved to /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint saved to' should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_capitalized_checkpoint_path_equals_is_stall_no_terminal(self) -> None:
        # Field-style sentence-cased variants: ``Checkpoint=`` /
        # ``Checkpoint_path=`` / ``Checkpoint path=`` /
        # ``Checkpoint path:`` / ``Checkpoint_path:``. The
        # strict extractor is still case-sensitive on these
        # forms (Fix J), so the broad check is the only place
        # the classifier sees them. Verify the broad check
        # matches.
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint path: /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint path: ' should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_capitalized_bare_checkpoint_pending_still_phase_header_only(self) -> None:
        # Regression guard (Codex 3417105899): a sentence-cased
        # bare ``Checkpoint pending`` is still NOT a real
        # value-bearing checkpoint reference. The new
        # sentence-cased broad-check entries (e.g.
        # ``Checkpoint: ``) only match when followed by a
        # value, so a bare ``Checkpoint pending`` line is
        # still rejected by the value-bearing extractor and
        # the message still falls through to
        # ``STALL_PHASE_HEADER_ONLY`` (broad has_checkpoint is
        # False because none of the value-bearing tokens
        # match). Verify this is preserved.
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint pending"
        )
        verdict = classify_humphry_message_for_stall(text)
        # The broad check should still miss bare
        # ``Checkpoint pending`` — none of the listed tokens
        # match a bare ``Checkpoint`` word. The message
        # therefore falls through to STALL_PHASE_HEADER_ONLY
        # for the no-next-action case.
        self.assertEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + bare 'Checkpoint pending' should still be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )

    def test_capitalized_checkpoint_tokens_contain_all_variants(self) -> None:
        # Direct contract check: the broad token list must
        # include all sentence-cased forms the strict
        # extractor already accepts. This is a direct
        # enumeration of the contract the fix must satisfy.
        from aed_lifecycle.no_stall import _CHECKPOINT_TOKENS
        required = (
            "Checkpoint: ",
            "Checkpoint_path=",
            "Checkpoint_path:",
            "Checkpoint path=",
            "Checkpoint path:",
            "Checkpoint=",
            "Wrote checkpoint to",
            "Saved checkpoint to",
            "Checkpoint file",
            "Checkpoint at",
            "Checkpoint saved to",
        )
        for token in required:
            self.assertIn(
                token,
                _CHECKPOINT_TOKENS,
                f"sentence-cased token {token!r} must be in _CHECKPOINT_TOKENS",
            )


class MainCiPostMergeTokenRoutingTests(unittest.TestCase):
    """Regression tests for Codex 3442251134 (Fix V).

    The post-merge CI fast path is gated on
    ``_is_post_merge_closeout_phase(phase, action)``, which
    matches the action against
    ``_POST_MERGE_NEXT_ACTION_TOKENS``. The previous
    implementation omitted the identifier form ``main_ci``
    even though:

      * the prose form ``main ci`` was already in the list
      * ``_POST_MERGE_CI_ACTION_TOKENS`` already included
        ``main_ci``
      * the nearby contract said ``poll main_ci`` should
        route to ``HOLD_POST_MERGE_CI_PENDING``

    As a result, an exhausted phase with
    ``next_action="poll main_ci"`` returned
    ``HOLD_OPERATOR_REQUIRED`` (the generic fallback)
    because ``is_post_merge`` was False, ``_CI_TOKEN_PATTERN``
    did not match the underscore-prefixed ``main_ci``
    identifier, and the post-merge fast path was unreachable.

    The fix adds ``main_ci`` to
    ``_POST_MERGE_NEXT_ACTION_TOKENS`` so the post-merge
    detector matches the identifier form too, and the
    exhausted phase with ``poll main_ci`` correctly routes
    to ``HOLD_POST_MERGE_CI_PENDING``.
    """

    @staticmethod
    def _exhausted_state(
        phase_name: str,
        next_action: str,
    ) -> "WatchdogState":
        from aed_lifecycle.watchdog import WatchdogState
        return WatchdogState(
            phase_name=phase_name,
            started_at=0.0,
            last_progress_at=0.0,
            max_idle_seconds=10.0,
            max_phase_seconds=10.0,
            next_action=next_action,
            checkpoint_path="/tmp/ckpt.json",
        )

    def test_next_action_poll_main_ci_returns_post_merge_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="poll main_ci",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
            f"exhausted phase with next_action 'poll main_ci' should route to "
            f"HOLD_POST_MERGE_CI_PENDING, not the generic fallback",
        )

    def test_next_action_audit_main_ci_returns_post_merge_hold(self) -> None:
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        state = self._exhausted_state(
            phase_name="PHASE_8",
            next_action="audit main_ci",
        )
        self.assertEqual(
            evaluate_watchdog(state, now=1000.0),
            HOLD_POST_MERGE_CI_PENDING,
            f"exhausted phase with next_action 'audit main_ci' should route to "
            f"HOLD_POST_MERGE_CI_PENDING",
        )

    def test_post_merge_next_action_tokens_contain_main_ci(self) -> None:
        # Direct contract check: ``main_ci`` must be in the
        # post-merge next-action token list (the fix that
        # unblocks the post-merge fast path for the
        # identifier form).
        from aed_lifecycle.watchdog import _POST_MERGE_NEXT_ACTION_TOKENS
        self.assertIn(
            "main_ci",
            _POST_MERGE_NEXT_ACTION_TOKENS,
            "identifier form 'main_ci' must be in _POST_MERGE_NEXT_ACTION_TOKENS "
            "so the post-merge fast path matches 'poll main_ci' / "
            "'audit main_ci' (Codex 3442251134 / Fix V)",
        )

    def test_post_merge_phase_tokens_still_contain_main_ci(self) -> None:
        # Regression guard: the phase-name token list still
        # includes ``main_ci`` (it was already there before
        # this fix), so a phase like ``PHASE_MAIN_CI`` /
        # ``main_ci`` continues to route correctly.
        from aed_lifecycle.watchdog import _POST_MERGE_PHASE_TOKENS
        self.assertIn("main_ci", _POST_MERGE_PHASE_TOKENS)

    def test_post_merge_ci_action_tokens_still_contain_main_ci(self) -> None:
        # Regression guard: the post-merge CI action token
        # list (the pattern used by the post-merge fast
        # path) still includes ``main_ci``.
        from aed_lifecycle.watchdog import _POST_MERGE_CI_ACTION_TOKENS
        self.assertIn("main_ci", _POST_MERGE_CI_ACTION_TOKENS)

    def test_main_ci_does_not_match_claim_ci_or_domain_ci(self) -> None:
        # The identifier-aware boundary class means the
        # ``main_ci`` token in ``_POST_MERGE_NEXT_ACTION_TOKENS``
        # only matches at a non-word-character boundary.
        # Verify that prose / identifier neighbors like
        # ``claim_ci`` and ``domain_ci`` do not accidentally
        # match the new ``main_ci`` token (so they do NOT
        # route to the post-merge hold via the new
        # identifier-form fast path). Note: the generic CI
        # detector also rejects these because the leading
        # ``c`` is preceded by a word char, so the actions
        # fall through to the operator fallback rather than
        # ``HOLD_PR_CI_PENDING`` — that is the pre-existing
        # contract and is preserved by this fix.
        from aed_lifecycle.watchdog import (
            HOLD_POST_MERGE_CI_PENDING,
            evaluate_watchdog,
        )
        for action in ("poll claim_ci", "poll domain_ci"):
            state = self._exhausted_state(
                phase_name="PHASE_8",
                next_action=action,
            )
            verdict = evaluate_watchdog(state, now=1000.0)
            self.assertNotEqual(
                verdict,
                HOLD_POST_MERGE_CI_PENDING,
                f"action {action!r} should NOT route to HOLD_POST_MERGE_CI_PENDING "
                f"(it does not contain the standalone 'main_ci' token)",
            )


class TitleCaseCheckpointPathBroadCheckTests(unittest.TestCase):
    """Regression tests for Codex 3442626197 (Fix X).

    The strict value-bearing extractor
    (``_extract_checkpoint_value``) accepts the title-cased
    field marker ``Checkpoint Path:`` in
    ``_CHECKPOINT_FIELD_MARKERS``, but the broad
    ``_CHECKPOINT_TOKENS`` substring scan previously only
    included the lowercase-p variant ``Checkpoint path:``.
    For a final output like::

        Starting PHASE 3 — Checkpoint Path: /tmp/ckpt.json

    with no ``next_action``, ``_has_checkpoint_with_value()``
    returned True (strict extractor matched) while
    ``has_checkpoint`` (broad) returned False, so the
    classifier returned ``STALL_PHASE_HEADER_ONLY`` instead
    of the intended checkpoint-bearing stall
    (``STALL_NO_TERMINAL_STATE``). The title-case variant
    must be added to the broad token list so the broad check
    stays in sync with the strict extractor.

    Also covers the related ``checkpoint =`` (with space)
    field-style variant and verifies the strict source-of-truth
    sync contract: any title-cased field marker accepted by
    ``_CHECKPOINT_FIELD_MARKERS`` must also be present in the
    broad ``_CHECKPOINT_TOKENS`` list.
    """

    def test_phase_header_with_title_case_checkpoint_path_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint Path: /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint Path: ' (title-case) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(
            verdict,
            STALL_NO_TERMINAL_STATE,
            f"phase header + title-case field marker (no next_action) should "
            f"be STALL_NO_TERMINAL_STATE, got {verdict!r}",
        )

    def test_phase_header_with_lowercase_p_checkpoint_path_is_stall_no_terminal(self) -> None:
        # Pre-existing lowercase-p variant (Fix W) — pinned
        # to ensure the Fix W change is preserved.
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint path: /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint path: ' (lowercase p) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_checkpoint_equals_space_is_stall_no_terminal(self) -> None:
        # The strict extractor also accepts ``checkpoint =``
        # (with space, equals delimiter). Verify the broad
        # scan covers this form too.
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "checkpoint = /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'checkpoint = ' (with space) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_title_case_field_markers_in_broad_list_match_strict_list(self) -> None:
        # Source-of-truth sync contract: every
        # title-cased / sentence-cased entry in
        # ``_CHECKPOINT_FIELD_MARKERS`` (the strict
        # extractor vocabulary) must also be present in
        # the broad ``_CHECKPOINT_TOKENS`` list. This
        # prevents the broad check from drifting behind
        # the strict extractor on a future addition.
        from aed_lifecycle.no_stall import (
            _CHECKPOINT_FIELD_MARKERS,
            _CHECKPOINT_TOKENS,
        )
        broad_set = set(_CHECKPOINT_TOKENS)
        for marker in _CHECKPOINT_FIELD_MARKERS:
            # Skip lowercase-only field markers — they are
            # always present in the broad list (lowercase
            # is the canonical form). Only enforce the
            # sync for entries that contain an uppercase
            # letter (i.e. title-cased or sentence-cased
            # variants).
            if any(c.isupper() for c in marker):
                self.assertIn(
                    marker,
                    broad_set,
                    f"title-cased field marker {marker!r} (from "
                    f"_CHECKPOINT_FIELD_MARKERS) must also be in the "
                    f"broad _CHECKPOINT_TOKENS list so the broad "
                    f"phase-header check stays in sync with the strict "
                    f"extractor (Codex 3442626197 / Fix X)",
                )

    def test_checkpoint_path_tokens_contain_title_case_variants(self) -> None:
        # Direct contract check: the broad token list must
        # include ``Checkpoint Path:`` and ``checkpoint =``
        # — the two field-style title-case variants the
        # strict extractor accepts that the broad scan
        # previously missed.
        from aed_lifecycle.no_stall import _CHECKPOINT_TOKENS
        self.assertIn("Checkpoint Path:", _CHECKPOINT_TOKENS)
        self.assertIn("checkpoint =", _CHECKPOINT_TOKENS)


class LowercaseCheckpointColonNoSpaceBroadCheckTests(unittest.TestCase):
    """Regression tests for Codex 3442962725 (Fix Y).

    The strict value-bearing extractor
    (``_extract_checkpoint_value``) accepts the lowercase
    field marker ``checkpoint:`` (no trailing space) in
    ``_CHECKPOINT_FIELD_MARKERS``, but the broad
    ``_CHECKPOINT_TOKENS`` substring scan only had
    ``checkpoint: `` (with trailing space). A phase-header
    message like::

        Starting PHASE 3
        checkpoint:/tmp/ckpt.json

    has a real checkpoint value (the strict extractor
    matches ``checkpoint:`` with no space), but the broad
    scan misses the form (no trailing space) and the
    classifier falls through to ``STALL_PHASE_HEADER_ONLY``
    instead of the intended checkpoint-bearing stall
    (``STALL_NO_TERMINAL_STATE``).

    The fix adds ``checkpoint:`` (no trailing space) to
    ``_CHECKPOINT_TOKENS`` so the broad scan matches the
    strict field marker.
    """

    def test_phase_header_with_checkpoint_colon_no_space_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "checkpoint:/tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'checkpoint:' (no space) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(
            verdict,
            STALL_NO_TERMINAL_STATE,
            f"phase header + 'checkpoint:' (no space, no next_action) should "
            f"be STALL_NO_TERMINAL_STATE, got {verdict!r}",
        )

    def test_phase_header_with_checkpoint_colon_space_still_works(self) -> None:
        # Pre-existing trailing-space form — pinned to
        # ensure the Fix W change is preserved.
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "checkpoint: /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'checkpoint: ' (with space) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_lowercase_field_markers_in_broad_list_match_strict_list(self) -> None:
        # Source-of-truth sync contract: every entry in
        # ``_CHECKPOINT_FIELD_MARKERS`` (the strict
        # extractor vocabulary) must also be present in the
        # broad ``_CHECKPOINT_TOKENS`` list, either as an
        # exact entry or as a strict-prefix match (e.g. the
        # broad scan keeps ``checkpoint: `` with trailing
        # space to be a strict prefix of any line with a
        # value after the marker, but for the no-space form
        # the broad scan needs the exact ``checkpoint:``
        # entry because the no-space form is also a valid
        # value-bearing form that ends at the line boundary).
        from aed_lifecycle.no_stall import (
            _CHECKPOINT_FIELD_MARKERS,
            _CHECKPOINT_TOKENS,
        )
        broad_set = set(_CHECKPOINT_TOKENS)
        for marker in _CHECKPOINT_FIELD_MARKERS:
            # Acceptable broad coverage: exact match, OR
            # the strict marker is a prefix of a broad
            # entry (e.g. ``checkpoint:`` is a prefix of
            # ``checkpoint: ``). This is what the broad
            # scan's substring containment check actually
            # uses — a line containing ``checkpoint: /...``
            # satisfies ``checkpoint: `` containment, and
            # a line containing ``checkpoint:/...``
            # satisfies ``checkpoint:`` containment.
            covered = (
                marker in broad_set
                or any(b.startswith(marker) or marker.startswith(b) for b in broad_set)
            )
            self.assertTrue(
                covered,
                f"strict field marker {marker!r} (from _CHECKPOINT_FIELD_MARKERS) "
                f"must be covered by the broad _CHECKPOINT_TOKENS list "
                f"(either as an exact entry or as a prefix-of / prefixed-by "
                f"relationship). The broad scan uses substring containment, so "
                f"a line containing the marker plus a value will match if "
                f"the marker is a prefix of any broad entry OR any broad "
                f"entry is a prefix of the marker.",
            )

    def test_checkpoint_tokens_contain_lowercase_colon_no_space(self) -> None:
        # Direct contract check: ``checkpoint:`` (no
        # trailing space) must be in the broad token list.
        from aed_lifecycle.no_stall import _CHECKPOINT_TOKENS
        self.assertIn(
            "checkpoint:",
            _CHECKPOINT_TOKENS,
            "lowercase field marker 'checkpoint:' (no trailing space) must be "
            "in _CHECKPOINT_TOKENS so the broad phase-header scan matches "
            "the strict field-marker form (Codex 3442962725 / Fix Y)",
        )


class CheckpointPathEqualsMarkerExtractionTests(unittest.TestCase):
    """Regression tests for Codex 3443071841 (Fix AA).

    The broad ``_CHECKPOINT_TOKENS`` substring scan advertises
    sentence-cased ``Checkpoint_path=`` / ``Checkpoint path=``
    forms, but the strict ``_CHECKPOINT_FIELD_MARKERS``
    extractor previously only accepted the colon variants
    (``Checkpoint path:``, ``Checkpoint Path:``,
    ``Checkpoint_path:``, ``Checkpoint:``). A resumable
    message like::

        next_action: poll CI status
        Checkpoint path=/tmp/ckpt.json

    was downgraded to ``STALL_NO_CHECKPOINT`` instead of
    ``OK_PROGRESS_WITH_NEXT_ACTION`` because the strict
    extractor missed the equals form. The fix adds the
    equals sentence-cased field markers
    (``Checkpoint_path=``, ``Checkpoint path=``,
    ``Checkpoint Path=``, ``Checkpoint=``) to
    ``_CHECKPOINT_FIELD_MARKERS`` so the value-bearing
    extractor stays in sync with the broad scan.
    """

    def test_checkpoint_path_equals_is_progress(self) -> None:
        text = (
            "next_action: poll CI status\n"
            "Checkpoint path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_path_equals_capital_p_is_progress(self) -> None:
        text = (
            "next_action: poll CI status\n"
            "Checkpoint Path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_path_underscore_equals_is_progress(self) -> None:
        text = (
            "next_action: poll CI status\n"
            "Checkpoint_path=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_checkpoint_equals_is_progress(self) -> None:
        text = (
            "next_action: poll CI status\n"
            "Checkpoint=/tmp/ckpt.json"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_existing_colon_marker_forms_still_work(self) -> None:
        # Regression guard (Codex 3420268720 / Fix J and
        # Codex 3442626197 / Fix X): the colon variants
        # must continue to work after the equals additions.
        for line in (
            "checkpoint_path=/tmp/ckpt.json",
            "checkpoint_path:/tmp/ckpt.json",
            "Checkpoint path:/tmp/ckpt.json",
            "Checkpoint Path:/tmp/ckpt.json",
            "checkpoint=/tmp/ckpt.json",
            "Checkpoint:/tmp/ckpt.json",
        ):
            text = (
                "next_action: poll CI status\n" + line
            )
            self.assertEqual(
                classify_humphry_message_for_stall(text),
                OK_PROGRESS_WITH_NEXT_ACTION,
                f"existing colon form {line!r} must still classify as "
                f"OK_PROGRESS_WITH_NEXT_ACTION",
            )

    def test_checkpoint_field_markers_contain_equals_forms(self) -> None:
        # Direct contract check: the strict
        # ``_CHECKPOINT_FIELD_MARKERS`` must include all
        # four equals sentence-cased variants the broad
        # scan advertises.
        from aed_lifecycle.no_stall import _CHECKPOINT_FIELD_MARKERS
        for marker in (
            "Checkpoint_path=",
            "Checkpoint path=",
            "Checkpoint Path=",
            "Checkpoint=",
        ):
            self.assertIn(
                marker,
                _CHECKPOINT_FIELD_MARKERS,
                f"equals sentence-cased marker {marker!r} must be in "
                f"_CHECKPOINT_FIELD_MARKERS so the strict extractor stays "
                f"in sync with the broad _CHECKPOINT_TOKENS scan "
                f"(Codex 3443071841 / Fix AA)",
            )

    def test_checkpoint_path_equals_extractor_returns_path(self) -> None:
        # Direct extractor check: the value-bearing
        # checkpoint parser must extract the path from
        # the equals form.
        from aed_lifecycle.no_stall import _extract_checkpoint_value
        for text, expected in [
            ("Checkpoint path=/tmp/ckpt.json", "/tmp/ckpt.json"),
            ("Checkpoint Path=/tmp/ckpt.json", "/tmp/ckpt.json"),
            ("Checkpoint_path=/tmp/ckpt.json", "/tmp/ckpt.json"),
            ("Checkpoint=/tmp/ckpt.json", "/tmp/ckpt.json"),
        ]:
            value = _extract_checkpoint_value(text)
            self.assertEqual(
                value,
                expected,
                f"equals-form marker text {text!r} should extract "
                f"{expected!r}, got {value!r}",
            )


class CheckpointPrNumberHasattrGuardTests(unittest.TestCase):
    """Regression tests for Codex 3443071832 (Fix Z).

    The structural validator in
    ``aed_lifecycle/checkpoint.py`` accessed
    ``state.pr_number`` directly without a ``hasattr``
    guard. A partially deserialized checkpoint that is
    missing the ``pr_number`` attribute would raise
    ``AttributeError`` instead of returning a structural
    validation error, crashing before the runner could
    surface ``HOLD_OPERATOR_REQUIRED``. The other required
    fields already use the ``hasattr`` pattern; the fix
    brings ``pr_number`` into line with the rest.
    """

    def test_missing_pr_number_returns_validation_error_not_attribute_error(self) -> None:
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            validate_checkpoint,
        )
        # Build a minimal valid state, then delete
        # ``pr_number`` to simulate a partial
        # deserialization. The dataclass does not override
        # ``__delattr__``, so a ``del`` on the attribute
        # works as expected.
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="3c8d2316c99e471d41dca38dc1f6e9c67db3421b",
            phase="PHASE_3",
        )
        del state.pr_number
        # Must NOT raise AttributeError. The validator
        # returns a list of error strings.
        try:
            errors = validate_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"validate_checkpoint must not raise AttributeError for a "
                f"missing pr_number attribute, got: {exc!r}"
            )
        self.assertTrue(
            any("pr_number" in e for e in errors),
            f"missing pr_number should produce a 'pr_number' error, "
            f"got errors={errors!r}",
        )

    def test_present_invalid_pr_number_still_returns_validation_error(self) -> None:
        # Regression guard: present-but-invalid
        # ``pr_number`` values (bool, negative, zero,
        # non-int) must continue to produce validation
        # errors after the hasattr guard is added.
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            validate_checkpoint,
        )
        for bad_pr_number in (True, False, -1, 0, "405"):
            state = CheckpointState(
                repo="Slideshow11/Automated-Edge-Discovery",
                pr_number=405,
                branch="tooling/aed-no-stall-watchdog-v1",
                current_head="3c8d2316c99e471d41dca38dc1f6e9c67db3421b",
                phase="PHASE_3",
            )
            # Use ``setattr`` to bypass the static type
            # check (the dataclass declares ``pr_number`` as
            # ``int``; we are deliberately assigning
            # non-int values to verify the validator's
            # runtime rejection).
            setattr(state, "pr_number", bad_pr_number)
            errors = validate_checkpoint(state)
            self.assertTrue(
                any("pr_number" in e for e in errors),
                f"bad pr_number {bad_pr_number!r} should produce a "
                f"'pr_number' error, got errors={errors!r}",
            )

    def test_valid_pr_number_still_passes(self) -> None:
        # Regression guard: a structurally valid state
        # with a positive int pr_number must continue
        # to pass validation (no 'pr_number' error in
        # the error list).
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            validate_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="3c8d2316c99e471d41dca38dc1f6e9c67db3421b",
            phase="PHASE_3",
        )
        errors = validate_checkpoint(state)
        self.assertFalse(
            any("pr_number" in e for e in errors),
            f"valid pr_number should not produce a 'pr_number' error, "
            f"got errors={errors!r}",
        )


class BroadCheckpointScanCaseInsensitiveTests(unittest.TestCase):
    """Regression tests for Codex 3443570407 (Fix AB).

    The broad ``has_checkpoint`` scan previously used the
    case-sensitive ``_contains_any`` helper, but the
    strict ``_extract_checkpoint_value`` uses
    case-insensitive matching for prose markers. A
    case-varied prose marker that the strict extractor
    accepts (e.g. ``Wrote Checkpoint To /tmp/ckpt.json``
    or ``Checkpoint File: /tmp/ckpt.json``) was missed by
    the broad scan, and a phase-header message with such
    a marker and no ``next_action`` would fall through to
    ``STALL_PHASE_HEADER_ONLY`` instead of
    ``STALL_NO_TERMINAL_STATE``.

    The fix introduces ``_contains_any_ci`` and uses it for
    the broad scan so the two checkpoint vocabularies stay
    in sync for prose markers.
    """

    def test_phase_header_with_capitalized_wrote_checkpoint_to_is_stall_no_terminal(self) -> None:
        # Pre-existing Fix W test, pinned here under the
        # new case-insensitive contract.
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Wrote Checkpoint To /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Wrote Checkpoint To' (capital T) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_mixed_case_checkpoint_file_colon_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "Checkpoint File: /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'Checkpoint File:' (mixed case) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_phase_header_with_uppercase_checkpoint_at_is_stall_no_terminal(self) -> None:
        text = (
            "Starting PHASE 3 — protected-state verification.\n"
            "CHECKPOINT AT /tmp/ckpt.json"
        )
        verdict = classify_humphry_message_for_stall(text)
        self.assertNotEqual(
            verdict,
            STALL_PHASE_HEADER_ONLY,
            f"phase header + 'CHECKPOINT AT' (uppercase) should NOT be "
            f"STALL_PHASE_HEADER_ONLY, got {verdict!r}",
        )
        self.assertEqual(verdict, STALL_NO_TERMINAL_STATE)

    def test_contains_any_ci_helper(self) -> None:
        # Direct helper test: case-insensitive substring
        # containment.
        from aed_lifecycle.no_stall import _contains_any_ci
        self.assertTrue(
            _contains_any_ci("Wrote Checkpoint To", ("wrote checkpoint to",))
        )
        self.assertTrue(
            _contains_any_ci("wrote checkpoint to", ("Wrote Checkpoint To",))
        )
        self.assertTrue(
            _contains_any_ci("CHECKPOINT AT", ("checkpoint at",))
        )
        self.assertFalse(
            _contains_any_ci("no checkpoint here", ("wrote checkpoint to",))
        )
        self.assertFalse(_contains_any_ci("", ("checkpoint",)))
        self.assertFalse(_contains_any_ci("text", ()))

    def test_broad_scan_matches_strict_extractor_for_case_varied_prose(self) -> None:
        # Sync contract: for every case-varied prose form
        # the strict extractor accepts, the broad scan
        # must also recognize it. This is the same
        # source-of-truth contract as Fix W / Fix X / Fix
        # Y, extended to cover the case-varied prose
        # forms.
        from aed_lifecycle.no_stall import (
            _CHECKPOINT_PROSE_MARKERS,
            _CHECKPOINT_TOKENS,
            _contains_any_ci,
        )
        # Each prose marker in the strict extractor (the
        # source of truth) must be matched by the broad
        # scan in lower-case form. Strip the trailing
        # space from prose markers (they all have one)
        # and verify the broad scan finds a corresponding
        # entry that is a case-insensitive prefix.
        for prose in _CHECKPOINT_PROSE_MARKERS:
            prose_lower = prose.lower().rstrip()
            # The broad list should contain a case-varied
            # variant that lower-cases to prose_lower. The
            # simplest check: the broad scan must accept
            # the exact lower-cased prose form (since the
            # broad scan is now case-insensitive, the
            # lower-cased form will match any case-varied
            # version of the same prose marker).
            self.assertTrue(
                _contains_any_ci(prose, _CHECKPOINT_TOKENS),
                f"prose marker {prose!r} (from _CHECKPOINT_PROSE_MARKERS) "
                f"must be recognized by the broad _CHECKPOINT_TOKENS scan "
                f"via case-insensitive matching (Codex 3443570407 / Fix AB)",
            )


class CheckpointOptionalAttributeHasattrGuardTests(unittest.TestCase):
    """Regression tests for Codex 3443570411 (Fix AC).

    The structural validator in
    ``aed_lifecycle.checkpoint.py`` previously accessed
    ``state.next_action`` and ``state.terminal_state``
    directly without ``getattr`` / ``hasattr`` guards. A
    partially deserialized checkpoint missing these
    attributes would raise ``AttributeError`` instead of
    returning a structural validation error, crashing
    before the runner could surface
    ``HOLD_OPERATOR_REQUIRED``. Fix Z (Codex 3443071832)
    already brought ``pr_number`` into line; Fix AC
    extends the same pattern to ``next_action`` and
    ``terminal_state``.
    """

    def test_missing_next_action_returns_validation_error_not_attribute_error(self) -> None:
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            validate_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="c10413834de6c454800045d6be360ccc44ece183",
            phase="PHASE_3",
        )
        del state.next_action
        # Must NOT raise AttributeError.
        try:
            errors = validate_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"validate_checkpoint must not raise AttributeError for a "
                f"missing next_action attribute, got: {exc!r}"
            )
        # No next_action error should be present (a
        # missing optional field is treated as "absent /
        # not set", not as a validation error).
        self.assertFalse(
            any("next_action" in e for e in errors),
            f"missing next_action should NOT produce a 'next_action' error "
            f"(a missing optional field is treated as absent), got "
            f"errors={errors!r}",
        )

    def test_missing_terminal_state_returns_validation_error_not_attribute_error(self) -> None:
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            validate_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="c10413834de6c454800045d6be360ccc44ece183",
            phase="PHASE_3",
        )
        del state.terminal_state
        # Must NOT raise AttributeError.
        try:
            errors = validate_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"validate_checkpoint must not raise AttributeError for a "
                f"missing terminal_state attribute, got: {exc!r}"
            )
        # No terminal_state error should be present
        # (a missing optional field is treated as
        # "absent / not set", not as a validation error).
        self.assertFalse(
            any("terminal_state" in e for e in errors),
            f"missing terminal_state should NOT produce a 'terminal_state' "
            f"error (a missing optional field is treated as absent), got "
            f"errors={errors!r}",
        )

    def test_missing_all_three_optional_fields_returns_validation_errors_not_attribute_error(self) -> None:
        # Worst case: all three guarded fields missing.
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            validate_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="c10413834de6c454800045d6be360ccc44ece183",
            phase="PHASE_3",
        )
        del state.next_action
        del state.terminal_state
        # Must NOT raise AttributeError.
        try:
            errors = validate_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"validate_checkpoint must not raise AttributeError when "
                f"multiple optional fields are missing, got: {exc!r}"
            )
        # No specific 'next_action' or 'terminal_state'
        # error should be present (all three optional
        # fields are treated as absent when missing).
        self.assertFalse(
            any("next_action" in e for e in errors),
            f"missing next_action should not produce a 'next_action' error, "
            f"got errors={errors!r}",
        )
        self.assertFalse(
            any("terminal_state" in e for e in errors),
            f"missing terminal_state should not produce a 'terminal_state' "
            f"error, got errors={errors!r}",
        )

    def test_present_invalid_next_action_still_returns_validation_error(self) -> None:
        # Regression guard: present-but-invalid
        # ``next_action`` values (non-string, empty,
        # placeholder) must continue to produce
        # validation errors after the getattr guard is
        # added.
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            validate_checkpoint,
        )
        # Non-string next_action: 123 is not str.
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="c10413834de6c454800045d6be360ccc44ece183",
            phase="PHASE_3",
        )
        setattr(state, "next_action", 123)
        errors = validate_checkpoint(state)
        self.assertTrue(
            any("next_action" in e for e in errors),
            f"non-string next_action should produce a 'next_action' error, "
            f"got errors={errors!r}",
        )


class CheckpointResumeHelpersGetattrGuardTests(unittest.TestCase):
    """Regression tests for Codex 3443646997 (Fix AD).

    The Fix AC ``getattr(..., None)`` guards brought
    :func:`validate_checkpoint` into line with the rest of
    the validator, but the downstream resume helpers
    (:func:`next_action_from_checkpoint`,
    :func:`checkpoint_requires_operator`,
    :func:`validate_resume_observations`) still dereferenced
    ``state.next_action`` / ``state.terminal_state`` /
    related optional attributes directly. A partially
    deserialized checkpoint object that bypasses the
    validator (e.g. a decoded namespace/dict-like object
    rather than a ``CheckpointState`` instance with
    dataclass defaults) could pass validation and still
    raise ``AttributeError`` later in the resume flow,
    preventing the runner from surfacing
    ``HOLD_OPERATOR_REQUIRED`` for a corrupt checkpoint.

    Fix AD applies the same ``getattr(..., None)`` pattern
    consistently in the downstream resume helpers. Read
    each optional field once at the top of the helper and
    use the local variable throughout.
    """

    def test_namespace_object_missing_optional_attrs_does_not_raise_in_validation(self) -> None:
        # A SimpleNamespace-style object that lacks the
        # optional fields entirely. ``validate_checkpoint``
        # must NOT raise ``AttributeError``; it must return
        # the structural errors it can detect and not crash
        # on the optional fields.
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import validate_checkpoint
        # Provide the required fields but omit the
        # optional ones (next_action, terminal_state,
        # last_verified_*_head, unresolved_thread_ids).
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="fd2f5c1b3b80ca0a9a53094269a7beb9cc438bc2",
            phase="PHASE_3",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
        )
        # Must NOT raise AttributeError.
        try:
            errors = validate_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"validate_checkpoint must not raise AttributeError for a "
                f"namespace object missing optional attrs, got: {exc!r}"
            )
        # No specific optional-attr error should be
        # present (a missing optional field is treated as
        # absent, not as a validation error).
        self.assertFalse(
            any("next_action" in e for e in errors),
            f"missing next_action should not produce a 'next_action' error, "
            f"got errors={errors!r}",
        )
        self.assertFalse(
            any("terminal_state" in e for e in errors),
            f"missing terminal_state should not produce a 'terminal_state' "
            f"error, got errors={errors!r}",
        )

    def test_next_action_from_checkpoint_does_not_raise_for_namespace_without_next_action(self) -> None:
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import (
            next_action_from_checkpoint,
            validate_checkpoint,
        )
        # Required fields present, optional missing.
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="fd2f5c1b3b80ca0a9a53094269a7beb9cc438bc2",
            phase="PHASE_3",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
        )
        # Must NOT raise AttributeError. With a populated
        # ``phase`` and no ``next_action`` and no
        # ``terminal_state``, the helper must return
        # ``"HOLD_OPERATOR_REQUIRED"`` so the runner
        # surfaces the hold to the operator.
        try:
            result = next_action_from_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"next_action_from_checkpoint must not raise AttributeError "
                f"for a namespace object missing optional attrs, got: {exc!r}"
            )
        # Note: validate_checkpoint will fail for this
        # namespace (missing current_head is not present,
        # and structural fields are checked), but the
        # helper should still return a safe string
        # regardless of whether validation passed.
        self.assertEqual(result, "HOLD_OPERATOR_REQUIRED")

    def test_checkpoint_requires_operator_does_not_raise_for_namespace_without_optional_attrs(self) -> None:
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import (
            checkpoint_requires_operator,
            validate_checkpoint,
        )
        # Required fields present, optional missing.
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="fd2f5c1b3b80ca0a9a53094269a7beb9cc438bc2",
            phase="PHASE_3",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
        )
        # Must NOT raise AttributeError. The helper should
        # return a safe boolean.
        try:
            result = checkpoint_requires_operator(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"checkpoint_requires_operator must not raise AttributeError "
                f"for a namespace object missing optional attrs, got: {exc!r}"
            )
        # With no next_action and no terminal_state, the
        # helper should return True (operator required).
        self.assertIsInstance(result, bool)
        self.assertTrue(
            result,
            f"checkpoint with no next_action and no terminal_state must "
            f"require operator (Fix D), got result={result!r}",
        )

    def test_next_action_from_checkpoint_namespace_with_phase_no_next_action(self) -> None:
        # Specifically pin the documented decision-tree
        # behavior: a checkpoint with a populated phase
        # and no next_action and no terminal_state must
        # return ``"HOLD_OPERATOR_REQUIRED"`` (the runner
        # cannot auto-resume a phase that has no
        # executable next step).
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import next_action_from_checkpoint
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="fd2f5c1b3b80ca0a9a53094269a7beb9cc438bc2",
            phase="PHASE_5_CI_POLL",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
        )
        self.assertEqual(
            next_action_from_checkpoint(state),
            "HOLD_OPERATOR_REQUIRED",
        )

    def test_next_action_from_checkpoint_returns_action_for_valid_state(self) -> None:
        # Regression guard: a structurally valid
        # ``CheckpointState`` with a valid ``next_action``
        # must continue to return the action verbatim
        # after the Fix AD refactor.
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            next_action_from_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="fd2f5c1b3b80ca0a9a53094269a7beb9cc438bc2",
            phase="PHASE_3",
            next_action="poll CI",
        )
        self.assertEqual(
            next_action_from_checkpoint(state),
            "poll CI",
        )

    def test_next_action_from_checkpoint_returns_none_for_completed_terminal(self) -> None:
        # Regression guard: a checkpoint with a recognized
        # completed terminal state must continue to return
        # ``None`` (runner is done).
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            next_action_from_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="fd2f5c1b3b80ca0a9a53094269a7beb9cc438bc2",
            phase="PHASE_3",
            terminal_state="MERGED",
        )
        self.assertIsNone(next_action_from_checkpoint(state))

    def test_checkpoint_requires_operator_namespace_with_completed_terminal(self) -> None:
        # Namespace variant of the completed-terminal
        # short-circuit test. With a completed terminal
        # state, the helper should return ``False``
        # (operator not required).
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import checkpoint_requires_operator
        # Note: validate_checkpoint will fail for this
        # namespace (no validate_checkpoint guarantees
        # are made for namespace objects), so the helper
        # will short-circuit to True. We are testing that
        # it does NOT raise AttributeError, not the
        # specific return value (which depends on
        # validation behavior).
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="fd2f5c1b3b80ca0a9a53094269a7beb9cc438bc2",
            phase="PHASE_3",
            terminal_state="MERGED",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
        )
        try:
            result = checkpoint_requires_operator(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"checkpoint_requires_operator must not raise AttributeError "
                f"for a namespace object with completed terminal_state, "
                f"got: {exc!r}"
            )
        # With no required structural validation
        # guarantees for the namespace, the helper
        # returns True (validate_checkpoint fails for
        # the namespace, so the structural gate fires).
        self.assertIsInstance(result, bool)


class CheckpointPhaseRequiredPresentTests(unittest.TestCase):
    """Regression tests for Codex 3443863455 (Fix AE).

    The Fix AD ``getattr(..., None)`` guards brought the
    downstream resume helpers into line with
    :func:`validate_checkpoint`, but they made a TRULY
    MISSING ``phase`` attribute indistinguishable from an
    EXPLICIT ``phase=None``. The dataclass declares
    ``phase: Optional[str] = None`` so
    ``CheckpointState(phase=None)`` is valid for a stale
    / parked checkpoint, but a truly missing ``phase``
    attribute on a namespace / dict-like partially
    deserialized checkpoint is a structural error: the
    resume helpers must surface ``HOLD_OPERATOR_REQUIRED``
    so the operator acknowledges the malformed checkpoint
    before the runner can act on it.

    Fix AE applies the ``hasattr`` pattern to distinguish
    "truly missing" from "explicitly None" in the
    validator and the downstream resume helpers.
    """

    def test_validate_checkpoint_namespace_missing_phase_returns_error(self) -> None:
        # Namespace object with required fields, recorded
        # heads, and a valid next_action but no phase.
        # Must produce a structural validation error for
        # the missing phase attribute.
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import validate_checkpoint
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            # phase is intentionally omitted
            next_action="poll CI",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
            last_verified_primary_head="0a8cee5d2406c970e02e9e217c7f25b0767459e0",
            last_verified_pr_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )
        errors = validate_checkpoint(state)
        self.assertTrue(
            any("phase" in e for e in errors),
            f"missing phase should produce a 'phase' validation error, "
            f"got errors={errors!r}",
        )

    def test_next_action_from_checkpoint_namespace_missing_phase_returns_hold(self) -> None:
        # The resume helper must not emit a runnable
        # action when phase is truly missing.
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import next_action_from_checkpoint
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            # phase is intentionally omitted
            next_action="poll CI",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
            terminal_state=None,
            updated_at=None,
        )
        result = next_action_from_checkpoint(state)
        self.assertEqual(
            result,
            "HOLD_OPERATOR_REQUIRED",
            f"namespace object missing phase must NOT auto-resume, "
            f"got result={result!r}",
        )

    def test_checkpoint_requires_operator_namespace_missing_phase_returns_true(self) -> None:
        # The validate_checkpoint gate at the top of
        # checkpoint_requires_operator will catch the
        # missing phase, returning True.
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import checkpoint_requires_operator
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            # phase is intentionally omitted
            next_action="poll CI",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
            authorized_thread_ids=[],
            unresolved_thread_ids=[],
            terminal_state=None,
            updated_at=None,
        )
        result = checkpoint_requires_operator(state)
        self.assertTrue(
            result,
            f"namespace object missing phase must require operator "
            f"(validate_checkpoint should reject it), got result={result!r}",
        )

    def test_explicit_phase_none_still_works(self) -> None:
        # Regression guard: ``CheckpointState(phase=None)``
        # is valid for a stale / parked checkpoint. The
        # explicit None value must continue to work after
        # the Fix AE hasattr guard.
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            next_action_from_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            phase=None,  # explicitly None
            next_action="poll CI",
        )
        # The dataclass instance has the ``phase``
        # attribute (set to None), so the hasattr check
        # passes. The behavior is: ``phase=None`` and
        # ``next_action="poll CI"`` with no terminal_state
        # is a stale state — the helper should return
        # ``HOLD_OPERATOR_REQUIRED`` (per the existing
        # decision tree: no phase, has next_action, no
        # terminal_state → stale).
        result = next_action_from_checkpoint(state)
        # With phase=None and next_action="poll CI", the
        # decision tree returns the next_action IF it
        # passes is_valid_next_action. The function
        # returns the action verbatim (the "no phase" +
        # "has next_action" branch is the valid path).
        # Note: this is a behavior preservation test —
        # the explicit None phase is treated as a stale
        # state but the action is still returned because
        # the runner can act on it.
        self.assertEqual(
            result,
            "poll CI",
            f"explicit phase=None with valid next_action must continue to "
            f"return the action (preserve dataclass semantics), got "
            f"result={result!r}",
        )

    def test_present_phase_with_valid_next_action_returns_action(self) -> None:
        # Regression guard: a valid checkpoint with a
        # present phase and valid next_action must
        # continue to return the action verbatim.
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            next_action_from_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            phase="PHASE_3",
            next_action="poll CI",
        )
        self.assertEqual(
            next_action_from_checkpoint(state),
            "poll CI",
        )

    def test_completed_terminal_still_behaves_as_before(self) -> None:
        # Regression guard: a checkpoint with a
        # recognized completed terminal state must
        # continue to return None from the resume
        # helper, regardless of whether phase is
        # present.
        from aed_lifecycle.checkpoint import (
            CheckpointState,
            next_action_from_checkpoint,
        )
        state = CheckpointState(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            phase="PHASE_3",
            terminal_state="MERGED",
        )
        self.assertIsNone(next_action_from_checkpoint(state))

    def test_missing_optional_non_required_fields_still_behave(self) -> None:
        # Regression guard: missing optional fields
        # other than ``phase`` (e.g. ``next_action``,
        # ``terminal_state``, ``last_verified_*_head``)
        # should still be treated as absent (Fix AD
        # behavior) — not as validation errors. Only
        # ``phase`` is required to be present (Fix AE).
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import validate_checkpoint
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            phase="PHASE_3",
            completed_phases=[],
            next_phase=None,
            pending_actions=[],
            # next_action, terminal_state,
            # last_verified_*_head, unresolved_thread_ids,
            # updated_at all omitted
        )
        # Must NOT raise AttributeError.
        try:
            errors = validate_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(
                f"validate_checkpoint must not raise AttributeError for a "
                f"namespace object missing optional non-phase attrs, "
                f"got: {exc!r}"
            )
        # No specific optional-attr error should be
        # present (other than possibly the recorded-head
        # missing errors for last_verified_*_head, which
        # are NOT optional in the same sense — they are
        # required to be present and non-empty for
        # validate_resume_observations, but
        # validate_checkpoint does not check them).
        optional_attrs = ("next_action", "terminal_state")
        for attr in optional_attrs:
            self.assertFalse(
                any(attr in e for e in errors),
                f"missing {attr} should not produce a '{attr}' error, "
                f"got errors={errors!r}",
            )

    def test_no_attribute_error_escapes_from_validation_or_resume_helpers(self) -> None:
        # End-to-end safety: a SimpleNamespace object
        # missing ALL optional attributes (and the
        # required phase) must not raise AttributeError
        # from any of the three helpers.
        from types import SimpleNamespace
        from aed_lifecycle.checkpoint import (
            checkpoint_requires_operator,
            next_action_from_checkpoint,
            validate_checkpoint,
        )
        state = SimpleNamespace(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=405,
            branch="tooling/aed-no-stall-watchdog-v1",
            current_head="0bd888aac66433d13e1bc54f2df10d7bc2eb8a72",
            # ALL optional attrs missing: phase,
            # next_action, terminal_state,
            # last_verified_*_head, unresolved_thread_ids,
            # next_phase, updated_at, completed_phases,
            # pending_actions
        )
        try:
            v_errors = validate_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(f"validate_checkpoint raised: {exc!r}")
        try:
            n_result = next_action_from_checkpoint(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(f"next_action_from_checkpoint raised: {exc!r}")
        try:
            c_result = checkpoint_requires_operator(state)
        except AttributeError as exc:  # pragma: no cover
            self.fail(f"checkpoint_requires_operator raised: {exc!r}")
        # No AttributeError. The phase error should be
        # present in v_errors.
        self.assertTrue(
            any("phase" in e for e in v_errors),
            f"validate_checkpoint should report missing phase, "
            f"got errors={v_errors!r}",
        )
        # The resume helper should return a safe hold.
        self.assertEqual(n_result, "HOLD_OPERATOR_REQUIRED")
        # The operator-required check should return True.
        self.assertTrue(c_result)
