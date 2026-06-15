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
        text = "next_action: poll CI status"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_PROGRESS_WITH_NEXT_ACTION,
        )

    def test_next_step_with_real_value_is_progress(self) -> None:
        text = "next step: poll CI status"
        self.assertEqual(
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

    def _terminal_ck(self, terminal_state="MERGED"):
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
            # Optional fields omitted (None) — terminal
            # checkpoint without recorded heads.
            last_verified_primary_head=None,
            last_verified_pr_head=None,
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
        errors = validate_resume_observations(
            self._terminal_ck("MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"),
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
        )
        self.assertEqual(errors, [])

    def test_terminal_hold_operator_required_skips_head_drift(self) -> None:
        errors = validate_resume_observations(
            self._terminal_ck("HOLD_OPERATOR_REQUIRED"),
            observed_pr_head="a" * 40,
            observed_primary_head="0" * 40,
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


if __name__ == "__main__":
    unittest.main()
