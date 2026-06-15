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

    def test_terminal_with_checkpoint_buried_is_still_terminal(self) -> None:
        # A message that contains a terminal token anywhere is terminal.
        text = "I see HOLD_PR_CI_PENDING showing in the harness output."
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
        text = (
            "Now PHASE 5 — bounded polling reached limit, "
            "HOLD_PR_CI_PENDING"
        )
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )


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

    def test_whole_terminal_state_token_matches(self) -> None:
        # The terminal state appears as a whole token.
        text = "State is MERGE_READY_AWAITING_HUMAN_AUTHORIZATION."
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_whole_terminal_state_token_at_string_end(self) -> None:
        # Trailing word-boundary.
        text = "now in HOLD_PR_CI_PENDING"
        self.assertEqual(
            classify_humphry_message_for_stall(text),
            OK_TERMINAL,
        )

    def test_whole_terminal_state_token_at_string_start(self) -> None:
        # Leading word-boundary.
        text = "MERGED."
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


if __name__ == "__main__":
    unittest.main()
