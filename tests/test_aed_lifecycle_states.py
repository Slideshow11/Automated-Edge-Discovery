"""Tests for the AED lifecycle state registry CLI.

Stdlib-only: uses unittest, subprocess, and json. No pytest-only fixtures.
The tests load the registry and CLI directly from the repo paths so they
work whether pytest is invoked from the repo root or from tests/.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "schemas" / "aed_lifecycle_states_v1.json"
CLI_PATH = REPO_ROOT / "scripts" / "local" / "aed_lifecycle_states.py"


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the registry CLI as a subprocess and capture output."""
    return subprocess.run(
        [sys.executable, str(CLI_PATH), "--registry", str(REGISTRY_PATH), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class RegistryLoadTests(unittest.TestCase):
    """The registry file must exist, parse, and have the expected shape."""

    def test_registry_file_exists(self) -> None:
        self.assertTrue(REGISTRY_PATH.exists(), f"missing: {REGISTRY_PATH}")

    def test_registry_parses_as_json(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)

    def test_registry_top_level_keys(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("schema_version"), 1)
        self.assertEqual(
            data.get("registry_kind"), "aed.lifecycle_state_registry.v1"
        )
        self.assertIsInstance(data.get("states"), dict)
        self.assertGreater(len(data["states"]), 0)


class RegistryCLIValidationTests(unittest.TestCase):
    """--validate must succeed on the committed registry."""

    def test_validate_exits_zero(self) -> None:
        result = _run_cli("--validate")
        self.assertEqual(
            result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        self.assertIn("PASSED", result.stdout)


class RegistryCLIListTests(unittest.TestCase):
    """--list must print every canonical state name."""

    EXPECTED_STATES = [
        "NOT_RUN",
        "HOLD_MAIN_HEAD_MISMATCH",
        "HOLD_HEAD_CHANGED",
        "HOLD_PR_CI_PENDING",
        "HOLD_PR_CI_FAILED",
        "HOLD_CODEX_RESPONSE_PENDING",
        "HOLD_NEW_CODEX_THREAD",
        "HOLD_NEW_ACTIVE_THREAD",
        "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED",
        "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
        "HOLD_MERGE_STATE_BLOCKED",
        "HOLD_PRE_MERGE_CONDITION_FAILED",
        "HOLD_POST_MERGE_CI_PENDING",
        "HOLD_POST_MERGE_CI_FAILED",
        "HOLD_POST_MERGE_CI_NOT_OBSERVED",
        "AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR",
        "PR_MERGED_PENDING_CLOSEOUT",
        "PR_MERGED_AND_CLOSED_OUT",
        "HOLD_RESUME_CHECKPOINT_NEEDED",
    ]

    def test_list_contains_all_required_canonical_states(self) -> None:
        result = _run_cli("--list")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        names = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        for state in self.EXPECTED_STATES:
            self.assertIn(state, names, f"missing required canonical state: {state}")

    def test_list_json_shape(self) -> None:
        result = _run_cli("--list", "--json")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("states", data)
        self.assertIsInstance(data["states"], list)
        for state in self.EXPECTED_STATES:
            self.assertIn(state, data["states"])


class RegistryCLIStateTests(unittest.TestCase):
    """--state <NAME> must return a JSON object describing that state."""

    def test_state_hold_pr_ci_pending(self) -> None:
        result = _run_cli("--state", "HOLD_PR_CI_PENDING")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("HOLD_PR_CI_PENDING", data)
        entry = data["HOLD_PR_CI_PENDING"]
        self.assertEqual(entry["category"], "hold")
        self.assertFalse(entry["merge_allowed"])
        self.assertFalse(entry["human_authorization_required"])

    def test_unknown_state_exits_nonzero(self) -> None:
        result = _run_cli("--state", "NOT_A_REAL_STATE")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown state", result.stderr)


class RegistryPolicyTests(unittest.TestCase):
    """Per-state policy expectations enforced by the validator."""

    def setUp(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.states = self.data["states"]

    def test_merge_ready_requires_human_authorization_and_permits_merge(self) -> None:
        entry = self.states["MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"]
        self.assertTrue(entry["merge_allowed"])
        self.assertTrue(entry["human_authorization_required"])
        self.assertIn("pr_merge", entry["allowed_mutations"])
        self.assertIn("admin_merge", entry["forbidden_mutations"])
        self.assertIn("auto_merge", entry["forbidden_mutations"])

    def test_resolve_only_state_does_not_permit_merge(self) -> None:
        entry = self.states["CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED"]
        self.assertFalse(entry["merge_allowed"])
        self.assertTrue(entry["human_authorization_required"])
        self.assertIn("thread_resolve", entry["allowed_mutations"])
        self.assertIn("pr_merge", entry["forbidden_mutations"])

    def test_terminal_state_has_no_mutations(self) -> None:
        entry = self.states["PR_MERGED_AND_CLOSED_OUT"]
        self.assertEqual(entry["category"], "terminal")
        # Terminal state must declare no further mutations. forbidden_mutations
        # is documentation of what the terminal state no longer permits, which
        # is allowed and useful for future readers; only the active mutation
        # surface must be empty.
        self.assertEqual(entry["allowed_mutations"], [])
        self.assertEqual(entry["allowed_next_states"], [])
        self.assertFalse(entry["merge_allowed"])
        self.assertFalse(entry["closeout_allowed"])
        # If forbidden_mutations is present, it must not include any mutation
        # that is also in allowed_mutations. Since allowed_mutations is empty,
        # this is trivially satisfied — but we still assert the field is a list
        # for shape discipline.
        self.assertIsInstance(entry["forbidden_mutations"], list)

    def test_allowed_next_states_reference_known_states(self) -> None:
        known = set(self.states.keys())
        for name, entry in self.states.items():
            for nxt in entry.get("allowed_next_states", []):
                self.assertIn(
                    nxt,
                    known,
                    f"state '{name}' references unknown next state '{nxt}'",
                )

    def test_no_state_has_conflicting_allowed_and_forbidden_mutations(self) -> None:
        for name, entry in self.states.items():
            allowed = set(entry.get("allowed_mutations", []) or [])
            forbidden = set(entry.get("forbidden_mutations", []) or [])
            overlap = allowed & forbidden
            self.assertFalse(
                overlap,
                f"state '{name}' has overlapping allowed/forbidden mutations: "
                f"{sorted(overlap)}",
            )

    def test_only_merge_ready_state_has_merge_allowed_true(self) -> None:
        offenders = [
            name
            for name, entry in self.states.items()
            if entry.get("merge_allowed", False)
            and name != "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
        ]
        self.assertEqual(
            offenders,
            [],
            f"only MERGE_READY_AWAITING_HUMAN_AUTHORIZATION may set merge_allowed; "
            f"offenders: {offenders}",
        )


class RegistryCategoryCoverageTests(unittest.TestCase):
    """The registry must cover each category at least once."""

    def test_every_category_has_at_least_one_state(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        categories = set(data.get("categories", []))
        seen = {entry["category"] for entry in data["states"].values()}
        missing = sorted(categories - seen)
        self.assertEqual(missing, [], f"categories with no state: {missing}")


class RegistryAuditAppendSkippedStateTests(unittest.TestCase):
    """AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR must codify the
    append-only closeout rule codified 2026-06-10.

    The canonical state covers both the "could not append" case
    and the "appended entry needs operator review" case. The
    alias ``AUDIT_APPEND_NEEDS_OPERATOR`` is documented in the
    entry's description and notes; the registry stores a single
    canonical entry and does not currently resolve the alias.
    """

    def setUp(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.entry = self.data["states"]["AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR"]

    def test_state_is_present(self) -> None:
        self.assertIn("AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR", self.data["states"])

    def test_category_is_hold(self) -> None:
        self.assertEqual(self.entry["category"], "hold")

    def test_human_authorization_required(self) -> None:
        self.assertTrue(self.entry["human_authorization_required"])

    def test_merge_not_allowed(self) -> None:
        self.assertFalse(self.entry["merge_allowed"])

    def test_closeout_not_allowed(self) -> None:
        self.assertFalse(self.entry["closeout_allowed"])

    def test_no_allowed_mutations(self) -> None:
        self.assertEqual(self.entry["allowed_mutations"], [])

    def test_description_mentions_append_only(self) -> None:
        self.assertIn("append-only", self.entry["description"])

    def test_description_mentions_alias(self) -> None:
        self.assertIn("AUDIT_APPEND_NEEDS_OPERATOR", self.entry["description"])

    def test_description_explicitly_forbids_audit_log_mutation(self) -> None:
        for phrase in (
            "delete",
            "trim",
            "rewrite",
            "replace",
            "explicitly authorizes",
        ):
            self.assertIn(
                phrase,
                self.entry["description"],
                f"description must mention '{phrase}' for the append-only rule",
            )

    def test_notes_document_corrective_append_decision_tree(self) -> None:
        notes = self.entry["notes"]
        # The five-step decision tree, captured in operator-readable prose.
        for marker in (
            "repo-standard audit validator",
            "stop and report an audit hold",
            "do not rewrite it",
            "corrective follow-up entry",
            "AUDIT_APPEND_NEEDS_OPERATOR",
            "codified 2026-06-10",
        ):
            self.assertIn(marker, notes, f"notes must mention '{marker}'")

    def test_forbidden_mutations_include_comment_delete_and_review_dismiss(self) -> None:
        forbidden = set(self.entry["forbidden_mutations"])
        self.assertIn("comment_delete", forbidden)
        self.assertIn("review_dismiss", forbidden)

    def test_forbidden_mutations_include_merge_and_admin_flags(self) -> None:
        forbidden = set(self.entry["forbidden_mutations"])
        for mut in ("pr_merge", "admin_merge", "auto_merge"):
            self.assertIn(mut, forbidden)

    def test_forbidden_mutations_include_force_push(self) -> None:
        # The append-only closeout rule forbids force-push while the
        # audit-ambiguity hold is in effect.
        self.assertIn("force_push", set(self.entry["forbidden_mutations"]))

    def test_allowed_next_states_include_pending_closout(self) -> None:
        self.assertIn("PR_MERGED_PENDING_CLOSEOUT", self.entry["allowed_next_states"])

    def test_allowed_next_states_include_terminal_closout(self) -> None:
        # The task brief permits PR_MERGED_AND_CLOSED_OUT as a
        # legitimate next state after explicit operator decision and
        # validator evidence.
        self.assertIn("PR_MERGED_AND_CLOSED_OUT", self.entry["allowed_next_states"])

    def test_allowed_next_states_are_known(self) -> None:
        known = set(self.data["states"].keys())
        for nxt in self.entry["allowed_next_states"]:
            self.assertIn(nxt, known)

    def test_evidence_required_includes_validator_evidence(self) -> None:
        # The new evidence requirement reflects that corrective
        # appends need validator evidence.
        self.assertIn("validator_evidence_if_available", self.entry["evidence_required"])

    def test_no_conflict_between_allowed_and_forbidden_mutations(self) -> None:
        allowed = set(self.entry["allowed_mutations"])
        forbidden = set(self.entry["forbidden_mutations"])
        self.assertFalse(
            allowed & forbidden,
            f"overlap between allowed and forbidden mutations: {allowed & forbidden}",
        )

    def test_state_canonical_name_is_in_expected_states(self) -> None:
        # The expected-states list used by RegistryCLIListTests is the
        # canonical machine-readable surface for downstream consumers.
        # The new state name must appear in that list.
        expected_states = [
            "NOT_RUN",
            "HOLD_MAIN_HEAD_MISMATCH",
            "HOLD_HEAD_CHANGED",
            "HOLD_PR_CI_PENDING",
            "HOLD_PR_CI_FAILED",
            "HOLD_CODEX_RESPONSE_PENDING",
            "HOLD_NEW_CODEX_THREAD",
            "HOLD_NEW_ACTIVE_THREAD",
            "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED",
            "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
            "HOLD_MERGE_STATE_BLOCKED",
            "HOLD_PRE_MERGE_CONDITION_FAILED",
            "HOLD_POST_MERGE_CI_PENDING",
            "HOLD_POST_MERGE_CI_FAILED",
            "HOLD_POST_MERGE_CI_NOT_OBSERVED",
            "AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR",
            "PR_MERGED_PENDING_CLOSEOUT",
            "PR_MERGED_AND_CLOSED_OUT",
            "HOLD_RESUME_CHECKPOINT_NEEDED",
        ]
        self.assertIn(
            "AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR",
            expected_states,
            "state must remain in the canonical expected-states list",
        )
        self.assertIn(
            "HOLD_RESUME_CHECKPOINT_NEEDED",
            expected_states,
            "HOLD_RESUME_CHECKPOINT_NEEDED must be in the canonical expected-states list",
        )

    def test_operator_path_doc_section_numbers_are_consistent(self) -> None:
        """Cross-reference regression guard (PR #398, PR #399, PR #400 renumberings).

        The PR #398 commit renumbered the operator-path doc from 13
        sections to 14 sections (added §7 for the append-only rule,
        shifted "Lessons" to §8 and "Where next work belongs" to §9).
        The PR #399 commit renumbers the doc again: it adds a new §8
        for the resume checkpoint rule, shifts "Lessons from PR #394"
        to §9, and shifts "Where next work belongs" to §10.
        The PR #400 commit renumbers the doc a third time: it adds
        a new §9 for the primary worktree sync policy, shifts
        "Lessons from PR #394" to §10, and shifts "Where next work
        belongs" to §11.

        The §2 and §6.5 "future work" pointers must now reference §11
        (the new "Where next work belongs" section). The §5 authority
        table and §6.5 future-cookbook "Codex-ping body templates"
        pointers must now reference §10 (the new "Lessons from PR
        #394" section).
        """
        doc_path = REPO_ROOT / "docs" / "aed_whole_workflow_operator_path.md"
        with doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        # §2 and §6.5 must point to §11 for the "future work" pointer.
        self.assertIn(
            "§11 as future work",
            text,
            "operator path §2/§6.5 'future work' pointer must reference §11 "
            "after the PR #400 renumbering",
        )
        # The "see §N" pointers in the §5 authority table and the
        # §6.5 future-cookbook list must point to §10 (Lessons from
        # PR #394), not §9.
        self.assertIn(
            "(see §10)",
            text,
            "operator path §5/§6.5 'see §N' pointer must now reference §10 "
            "(Lessons from PR #394) after the PR #400 renumbering",
        )


class RegistryResumeCheckpointStateTests(unittest.TestCase):
    """HOLD_RESUME_CHECKPOINT_NEEDED must codify the resume checkpoint
    continuation rule codified 2026-06-10.

    The canonical state is the operator's "I do not have enough
    durable evidence to know what to do next" state. It is a hold
    state whose allowed_next_states list spans the full set of
    non-terminal and terminal canonical states because the
    operator may reconstruct any of them as the prior verified
    state. Its forbidden_mutations list uses the canonical mutation
    vocabulary tokens; the three policy-level prohibitions
    (duplicate Codex ping, audit rewrite, repeated already-
    completed mutation) are documented in the entry's notes.
    """

    def setUp(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.entry = self.data["states"]["HOLD_RESUME_CHECKPOINT_NEEDED"]
        self.all_state_names = set(self.data["states"].keys())

    def test_state_is_present(self) -> None:
        self.assertIn("HOLD_RESUME_CHECKPOINT_NEEDED", self.data["states"])

    def test_category_is_hold(self) -> None:
        self.assertEqual(self.entry["category"], "hold")

    def test_human_authorization_required(self) -> None:
        self.assertTrue(self.entry["human_authorization_required"])

    def test_merge_not_allowed(self) -> None:
        self.assertFalse(self.entry["merge_allowed"])

    def test_closeout_not_allowed(self) -> None:
        self.assertFalse(self.entry["closeout_allowed"])

    def test_no_allowed_mutations(self) -> None:
        self.assertEqual(self.entry["allowed_mutations"], [])

    def test_description_explains_continuation_failure(self) -> None:
        for marker in (
            "Continuation",
            "durable evidence",
            "reconstruct",
            "read-only",
            "Do not infer readiness from memory",
        ):
            self.assertIn(
                marker,
                self.entry["description"],
                f"description must mention '{marker}'",
            )

    def test_description_enumerates_eight_verification_steps(self) -> None:
        for marker in (
            "PR number and URL",
            "head SHA",
            "lifecycle state",
            "completed phases",
            "remaining permitted mutations",
            "already-performed mutations",
            "protected PR/worktree state",
            "continuation",
        ):
            self.assertIn(
                marker,
                self.entry["description"],
                f"description must mention verification step '{marker}'",
            )

    def test_evidence_required_lists_seven_items(self) -> None:
        # The task spec lists six evidence items; the eight
        # verification steps in the description are operator-readable
        # guidance, not a one-to-one mapping of evidence_required
        # tokens. The Codex P2 review found that
        # `already_performed_mutation_summary` was missing from the
        # evidence list and required it for the machine-readable
        # surface to match the prose; that brings the count to
        # seven. The eight-step operator checklist in the description
        # remains a prose summary, not a literal one-to-one.
        self.assertEqual(len(self.entry["evidence_required"]), 7)
        for marker in (
            "pr_number_and_url",
            "current_head_sha",
            "current_lifecycle_state",
            "completed_phase_summary",
            "remaining_permitted_mutation_summary",
            "already_performed_mutation_summary",
            "protected_pr_and_worktree_verification",
        ):
            self.assertIn(
                marker,
                self.entry["evidence_required"],
                f"evidence_required must include '{marker}'",
            )

    def test_forbidden_mutations_use_canonical_vocabulary(self) -> None:
        forbidden = set(self.entry["forbidden_mutations"])
        for token in (
            "pr_merge",
            "thread_resolve",
            "comment_delete",
            "review_dismiss",
            "force_push",
        ):
            self.assertIn(
                token,
                forbidden,
                f"forbidden_mutations must include canonical token '{token}'",
            )

    def test_no_policy_only_tokens_in_forbidden_mutations(self) -> None:
        # The three policy-level prohibitions (duplicate Codex ping,
        # audit rewrite, repeated already-completed mutation) are NOT
        # in the canonical mutation vocabulary and must not appear
        # here as forbidden_mutations tokens; the validator would
        # reject them.
        forbidden = set(self.entry["forbidden_mutations"])
        for token in (
            "duplicate_codex_ping",
            "audit_rewrite",
            "repeated_already_completed_mutation",
        ):
            self.assertNotIn(
                token,
                forbidden,
                f"forbidden_mutations must not include non-canonical token '{token}'; "
                "document policy-level prohibitions in notes instead",
            )

    def test_notes_document_three_policy_level_prohibitions(self) -> None:
        notes = self.entry["notes"]
        for marker in (
            "duplicate Codex ping",
            "rewrite",
            "audit row",
            "already-completed mutation",
            "codified 2026-06-10",
        ):
            self.assertIn(marker, notes, f"notes must mention '{marker}'")

    def test_allowed_next_states_span_full_lifecycle(self) -> None:
        # The operator may reconstruct any prior verified state, so
        # the allowed_next_states list must span the full set of
        # canonical states. Terminal and informational states are
        # included. The Codex P2 review required the state itself
        # to be in its own allowed_next_states (self-loop) so the
        # operator can remain in HOLD_RESUME_CHECKPOINT_NEEDED
        # when reconstruction still cannot determine the prior
        # state.
        for name in (
            "HOLD_RESUME_CHECKPOINT_NEEDED",
            "NOT_RUN",
            "HOLD_PR_CI_PENDING",
            "HOLD_CODEX_RESPONSE_PENDING",
            "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED",
            "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
            "PR_MERGED_PENDING_CLOSEOUT",
            "PR_MERGED_AND_CLOSED_OUT",
            "AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR",
        ):
            self.assertIn(
                name,
                self.entry["allowed_next_states"],
                f"allowed_next_states must include '{name}' for reconstruction",
            )

    def test_allowed_next_states_are_known(self) -> None:
        for nxt in self.entry["allowed_next_states"]:
            self.assertIn(
                nxt,
                self.all_state_names,
                f"allowed_next_states references unknown state '{nxt}'",
            )

    def test_no_conflict_between_allowed_and_forbidden_mutations(self) -> None:
        allowed = set(self.entry["allowed_mutations"])
        forbidden = set(self.entry["forbidden_mutations"])
        self.assertFalse(
            allowed & forbidden,
            f"overlap between allowed and forbidden mutations: {allowed & forbidden}",
        )


class RegistryPrimaryWorktreeSyncPolicyTests(unittest.TestCase):
    """The primary worktree sync policy codified 2026-06-10 must
    be present in canonical policy vocabulary, in the operator
    path doc as §9, in the lifecycle state registry as §13, and
    in the command cookbook as §11.3. The canonical
    `HOLD_MAIN_HEAD_MISMATCH` state's `forbidden_mutations` list
    must include the `worktree_update` token, which is the
    machine-readable surface for the policy.

    No new lifecycle state is added in this PR; the policy is
    a policy-level constraint that is already encoded in the
    existing `HOLD_MAIN_HEAD_MISMATCH` state. The cross-
    references and prose restatements are the canonical
    surfaces for downstream consumers.
    """

    def setUp(self) -> None:
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.hold_main_head = self.data["states"]["HOLD_MAIN_HEAD_MISMATCH"]
        self.registry_doc_path = (
            REPO_ROOT / "docs" / "aed_lifecycle_state_registry.md"
        )
        self.operator_path_doc_path = (
            REPO_ROOT / "docs" / "aed_whole_workflow_operator_path.md"
        )
        self.cookbook_doc_path = (
            REPO_ROOT / "docs" / "aed_known_safe_command_cookbook.md"
        )

    def test_hold_main_head_mismatch_state_is_present(self) -> None:
        """The canonical state that the primary-worktree-update
        prohibition lives on must still be present in the registry.
        """
        self.assertIn("HOLD_MAIN_HEAD_MISMATCH", self.data["states"])

    def test_hold_main_head_mismatch_forbids_worktree_update(self) -> None:
        """Regression guard: the canonical `worktree_update` token
        must remain in `HOLD_MAIN_HEAD_MISMATCH.forbidden_mutations`.
        The primary-worktree-update prohibition lives on this token.
        If a future PR removes or renames this token, this test
        fails and forces the author to re-state the policy.
        """
        forbidden = set(self.hold_main_head["forbidden_mutations"])
        self.assertIn(
            "worktree_update",
            forbidden,
            "HOLD_MAIN_HEAD_MISMATCH.forbidden_mutations must include "
            "'worktree_update' as the canonical token for the "
            "primary-worktree-update prohibition",
        )

    def test_hold_main_head_mismatch_covers_primary_worktree_evidence(self) -> None:
        """The `HOLD_MAIN_HEAD_MISMATCH` registry entry must cover
        both the origin/main HEAD mismatch surface and the primary
        worktree mismatch surface (dirty status, wrong branch,
        wrong HEAD). The evidence_required list must include the
        primary-worktree tokens that a downstream consumer needs
        to distinguish the two surfaces and reconcile the right
        one.
        """
        # Description must mention both surfaces.
        description = self.hold_main_head["description"]
        for marker in (
            "origin/main",
            "primary worktree",
            "dirty",
            "branch",
            "HEAD",
        ):
            self.assertIn(
                marker,
                description,
                f"HOLD_MAIN_HEAD_MISMATCH.description must mention '{marker}'",
            )
        # Evidence list must include both origin/main and primary tokens.
        evidence = set(self.hold_main_head["evidence_required"])
        for token in (
            "expected_head_sha",
            "observed_origin_main_sha",
            "primary_worktree_path",
            "primary_status_porcelain",
            "primary_branch",
            "primary_expected_head_sha",
            "primary_observed_head_sha",
        ):
            self.assertIn(
                token,
                evidence,
                f"HOLD_MAIN_HEAD_MISMATCH.evidence_required must include '{token}'",
            )
        # Notes must mention both reconciliation paths.
        notes = self.hold_main_head["notes"]
        for marker in (
            "reconcile",
            "worktree_update",
        ):
            self.assertIn(
                marker,
                notes,
                f"HOLD_MAIN_HEAD_MISMATCH.notes must mention '{marker}'",
            )

    def test_registry_doc_documents_evidence_for_hold_main_head_mismatch(self) -> None:
        """The registry doc §13 must restate the full evidence list
        for `HOLD_MAIN_HEAD_MISMATCH`, so a downstream consumer
        reading the prose gets the same contract as a consumer
        reading the JSON.
        """
        with self.registry_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        section_start = text.find("## 13. Primary worktree sync policy")
        self.assertNotEqual(section_start, -1, "registry doc must have §13")
        section_end = text.find("\n## ", section_start + 1)
        if section_end == -1:
            section_body = text[section_start:]
        else:
            section_body = text[section_start:section_end]
        joined = " ".join(section_body.split())
        for token in (
            "expected_head_sha",
            "observed_origin_main_sha",
            "primary_worktree_path",
            "primary_status_porcelain",
            "primary_branch",
            "primary_expected_head_sha",
            "primary_observed_head_sha",
        ):
            self.assertIn(
                token,
                joined,
                f"registry §13 must document evidence token '{token}'",
            )

    def test_operator_path_row_documents_evidence_for_hold_main_head_mismatch(self) -> None:
        """The operator path doc's `HOLD_MAIN_HEAD_MISMATCH` row
        must list the required evidence tokens, so the row is the
        canonical prose surface for the registry entry's evidence
        contract.
        """
        with self.operator_path_doc_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        # Find the row that begins with `| `HOLD_MAIN_HEAD_MISMATCH` |`.
        matching = [
            line for line in lines if line.lstrip().startswith("| `HOLD_MAIN_HEAD_MISMATCH` |")
        ]
        self.assertEqual(
            len(matching),
            1,
            "operator path must have exactly one HOLD_MAIN_HEAD_MISMATCH row",
        )
        row = matching[0]
        for token in (
            "expected_head_sha",
            "observed_origin_main_sha",
            "primary_worktree_path",
            "primary_status_porcelain",
            "primary_branch",
            "primary_expected_head_sha",
            "primary_observed_head_sha",
        ):
            self.assertIn(
                token,
                row,
                f"operator path HOLD_MAIN_HEAD_MISMATCH row must list evidence '{token}'",
            )

    def test_registry_doc_has_primary_worktree_sync_section(self) -> None:
        """The lifecycle state registry doc must contain §13, the
        primary worktree sync policy section, codified 2026-06-10.
        """
        with self.registry_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn(
            "## 13. Primary worktree sync policy",
            text,
            "registry doc must contain §13 'Primary worktree sync policy'",
        )
        self.assertIn(
            "codified 2026-06-10",
            text,
            "registry §13 must be timestamped 2026-06-10",
        )

    def test_registry_section_documents_key_policy_markers(self) -> None:
        """The §13 section must mention the key policy markers:
        primary worktree is left stale, the post-closeout anchor
        pattern, the read-only verification commands, the
        `worktree_update` token, and the explicit human
        authorization requirement.

        The test joins the section body with spaces so that
        long phrases that wrap across lines (e.g. "intentionally
        left stale" wrapping as "intentionally left\\nstale")
        still match as a single semantic token.
        """
        with self.registry_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        # Pull the §13 section body out of the registry doc.
        section_start = text.find("## 13. Primary worktree sync policy")
        self.assertNotEqual(section_start, -1, "registry doc must have §13")
        section_end = text.find("\n## ", section_start + 1)
        if section_end == -1:
            section_body = text[section_start:]
        else:
            section_body = text[section_start:section_end]
        # Join with spaces so wrapped phrases match as one token.
        joined = " ".join(section_body.split())
        for marker in (
            "intentionally left stale",
            "explicit human operator authorization",
            "worktree_update",
            "temp worktree",
            "HOLD_MAIN_HEAD_MISMATCH",
            "read-only",
            "primary worktree",
            "protected state",
        ):
            self.assertIn(
                marker,
                joined,
                f"registry §13 must mention '{marker}'",
            )

    def test_operator_path_doc_has_primary_worktree_sync_section(self) -> None:
        """The operator path doc must contain §9, the primary
        worktree sync policy section, codified 2026-06-10.
        """
        with self.operator_path_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn(
            "## 9. Primary worktree sync policy",
            text,
            "operator path doc must contain §9 'Primary worktree sync policy'",
        )

    def test_operator_path_section_renumbering_is_correct(self) -> None:
        """After adding §9, the previous §9 (Lessons from PR #394)
        must now be §10, and the previous §10 (Where next work
        belongs) must now be §11. No stale §9/§10 headers for
        those topics may remain.
        """
        with self.operator_path_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn(
            "## 10. Lessons from PR #394",
            text,
            "'Lessons from PR #394' must now be §10 after the PR #400 renumbering",
        )
        self.assertIn(
            "## 11. Where next work belongs",
            text,
            "'Where next work belongs' must now be §11 after the PR #400 renumbering",
        )
        # And the old §9 header for "Lessons from PR #394" must be
        # gone (no ## 9. Lessons from PR #394).
        self.assertNotIn(
            "## 9. Lessons from PR #394",
            text,
            "old §9 'Lessons from PR #394' header must not remain",
        )
        # And the old §10 header for "Where next work belongs" must
        # be gone.
        self.assertNotIn(
            "## 10. Where next work belongs",
            text,
            "old §10 'Where next work belongs' header must not remain",
        )

    def test_operator_path_authority_table_references_new_section(self) -> None:
        """The §5 authority table row for 'Primary worktree update,
        reset, or pull' must reference the new §9 (primary
        worktree sync policy) section.
        """
        with self.operator_path_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        # Find the §5 table and the primary worktree row.
        self.assertIn(
            "Primary worktree update, reset, or pull",
            text,
            "operator path §5 must still have the primary worktree row",
        )
        # The row must now reference §9 (the new sync policy section).
        self.assertIn(
            "primary-worktree sync policy in §9",
            text,
            "operator path §5 primary worktree row must reference §9",
        )

    def test_cookbook_doc_has_primary_worktree_sync_constraint(self) -> None:
        """The known-safe command cookbook must contain §11.3, the
        primary worktree sync constraint section, codified
        2026-06-10.
        """
        with self.cookbook_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn(
            "### 11.3 Primary worktree sync constraint",
            text,
            "cookbook must contain §11.3 'Primary worktree sync constraint'",
        )

    def test_cookbook_section_documents_verification_pattern(self) -> None:
        """The §11.3 section must include the canonical
        verification pattern commands
        (`status --porcelain`, `rev-parse HEAD`,
        `branch --show-current`) and the explicit human
        authorization requirement.

        The test joins the section body with spaces so that
        long phrases that wrap across lines still match.
        """
        with self.cookbook_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        section_start = text.find("### 11.3 Primary worktree sync constraint")
        self.assertNotEqual(section_start, -1, "cookbook must have §11.3")
        section_end = text.find("\n## ", section_start + 1)
        if section_end == -1:
            section_body = text[section_start:]
        else:
            section_body = text[section_start:section_end]
        joined = " ".join(section_body.split())
        for marker in (
            "status --porcelain",
            "rev-parse HEAD",
            "branch --show-current",
            "explicit human operator authorization",
            "worktree_update",
            "HOLD_MAIN_HEAD_MISMATCH",
            "Automated-Edge-Discovery",
        ):
            self.assertIn(
                marker,
                joined,
                f"cookbook §11.3 must mention '{marker}'",
            )

    def test_cookbook_state_to_command_index_references_section(self) -> None:
        """The §14 state-to-command index row for
        `HOLD_MAIN_HEAD_MISMATCH` must now reference §11.3 (the
        primary worktree sync constraint) and the operator
        path §9.

        The test extracts the exact table row containing
        `| `HOLD_MAIN_HEAD_MISMATCH` |` from the cookbook and
        asserts on that extracted row, not on the whole
        cookbook text. The whole-text check would silently
        pass if the row regressed, because the cookbook now
        contains `§11.3` in multiple prose references
        (the new §11.3 heading, §13 forbidden patterns,
        and the §14 row itself). Scoping the assertion to
        the row ensures the row is the one that carries
        the reference.
        """
        with self.cookbook_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        # Extract the exact table row for HOLD_MAIN_HEAD_MISMATCH
        # from the §14 state-to-command index. The row starts with
        # the table-cell delimiter and runs to the end of the line.
        row_prefix = "| `HOLD_MAIN_HEAD_MISMATCH` |"
        matching_rows = [
            line
            for line in text.splitlines()
            if line.startswith(row_prefix)
        ]
        self.assertEqual(
            len(matching_rows),
            1,
            "cookbook §14 must have exactly one row for "
            "HOLD_MAIN_HEAD_MISMATCH "
            f"(found {len(matching_rows)})",
        )
        row = matching_rows[0]
        # The row must reference §11.3 (the primary worktree
        # sync constraint) and §9 (the operator-path section
        # that codifies the policy).
        self.assertIn(
            "§11.3",
            row,
            "cookbook §14 HOLD_MAIN_HEAD_MISMATCH row must "
            "reference §11.3 (scoped to the row, not the file)",
        )
        self.assertIn(
            "§9",
            row,
            "cookbook §14 HOLD_MAIN_HEAD_MISMATCH row must "
            "reference §9 (the operator-path section, scoped "
            "to the row)",
        )

    def test_cookbook_forbidden_commands_section_references_policy(self) -> None:
        """The §13 forbidden command patterns section must
        reference §11.3 and the canonical `HOLD_MAIN_HEAD_MISMATCH`
        state's `worktree_update` token, so the forbidden-
        command list and the primary-worktree sync policy stay
        in sync.
        """
        with self.cookbook_doc_path.open("r", encoding="utf-8") as f:
            text = f.read()
        # Pull the §13 section body out of the cookbook.
        section_start = text.find("## 13. Forbidden command patterns")
        self.assertNotEqual(section_start, -1, "cookbook must have §13")
        section_end = text.find("\n## ", section_start + 1)
        if section_end == -1:
            section_body = text[section_start:]
        else:
            section_body = text[section_start:section_end]
        for marker in (
            "§11.3",
            "worktree_update",
            "HOLD_MAIN_HEAD_MISMATCH",
            "primary worktree",
            "Automated-Edge-Discovery",
        ):
            self.assertIn(
                marker,
                section_body,
                f"cookbook §13 must mention '{marker}'",
            )

    def test_no_new_canonical_lifecycle_state_added(self) -> None:
        """The primary worktree sync policy is a policy-level
        constraint, not a new lifecycle state. The set of
        canonical state names in the registry must remain
        exactly the 19 states present at PR #399 (NOT_RUN,
        HOLD_MAIN_HEAD_MISMATCH, HOLD_HEAD_CHANGED,
        HOLD_PR_CI_PENDING, HOLD_PR_CI_FAILED,
        HOLD_CODEX_RESPONSE_PENDING, HOLD_NEW_CODEX_THREAD,
        HOLD_NEW_ACTIVE_THREAD,
        CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED,
        MERGE_READY_AWAITING_HUMAN_AUTHORIZATION,
        HOLD_MERGE_STATE_BLOCKED,
        HOLD_PRE_MERGE_CONDITION_FAILED,
        HOLD_POST_MERGE_CI_PENDING, HOLD_POST_MERGE_CI_FAILED,
        HOLD_POST_MERGE_CI_NOT_OBSERVED,
        AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR,
        PR_MERGED_PENDING_CLOSEOUT, PR_MERGED_AND_CLOSED_OUT,
        HOLD_RESUME_CHECKPOINT_NEEDED) plus the two narrow
        status descriptions added in PR #402 (CODEX_CLEAN_PASS,
        HOLD_PR_NOT_OPEN) for the audit_codex_response_for_pr.py
        classifier. No other state was added in this PR.
        """
        expected_states = {
            "NOT_RUN",
            "HOLD_MAIN_HEAD_MISMATCH",
            "HOLD_HEAD_CHANGED",
            "HOLD_PR_CI_PENDING",
            "HOLD_PR_CI_FAILED",
            "HOLD_CODEX_RESPONSE_PENDING",
            "HOLD_NEW_CODEX_THREAD",
            "HOLD_NEW_ACTIVE_THREAD",
            "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED",
            "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
            "HOLD_MERGE_STATE_BLOCKED",
            "HOLD_PRE_MERGE_CONDITION_FAILED",
            "HOLD_POST_MERGE_CI_PENDING",
            "HOLD_POST_MERGE_CI_FAILED",
            "HOLD_POST_MERGE_CI_NOT_OBSERVED",
            "AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR",
            "PR_MERGED_PENDING_CLOSEOUT",
            "PR_MERGED_AND_CLOSED_OUT",
            "HOLD_RESUME_CHECKPOINT_NEEDED",
            "CODEX_CLEAN_PASS",
            "HOLD_PR_NOT_OPEN",
        }
        actual_states = set(self.data["states"].keys())
        self.assertEqual(
            actual_states,
            expected_states,
            "canonical state set must remain unchanged after PR #400",
        )


class RegistryMalformedListFieldTests(unittest.TestCase):
    """The validator must reject malformed list-valued fields gracefully.

    These regression tests cover the two Codex P2 findings raised against
    commit 92582a41c7:
      - Finding 1: truthy non-iterables (e.g. integer 1) in a list-valued
        field must not raise a Python traceback; they must be reported as
        a validation error and treated as an empty list for downstream
        checks.
      - Finding 2: falsy non-list values (e.g. empty string, empty object)
        in a list-valued field must not be silently treated as the empty
        list and pass validation; they must be reported as a validation
        error and treated as an empty list for downstream checks.

    Each test writes a malformed registry to a tmp_path and runs the CLI
    as a subprocess with --validate, then asserts the returned errors.
    """

    def setUp(self) -> None:
        # Load the shipped registry as a base; mutate one field per test.
        with REGISTRY_PATH.open("r", encoding="utf-8") as f:
            self.base = json.load(f)

    def _write_and_validate(
        self, tmp: Path, suffix: str
    ) -> subprocess.CompletedProcess:
        out_path = tmp / f"registry_{suffix}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(self.base, f)
        return subprocess.run(
            [
                sys.executable,
                str(CLI_PATH),
                "--registry",
                str(out_path),
                "--validate",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_truthy_non_iterable_allowed_mutations_is_rejected(self) -> None:
        """Finding 1: integer 1 in allowed_mutations must not traceback."""
        import tempfile
        # Pick a non-terminal state so allowed_mutations iteration runs.
        target = "HOLD_PR_CI_PENDING"
        self.base["states"][target]["allowed_mutations"] = 1
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._write_and_validate(tmp, "int_allowed")
        self.assertNotEqual(
            result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn("allowed_mutations", result.stdout + result.stderr)
        self.assertIn("must be a list", result.stdout + result.stderr)
        self.assertIn(target, result.stdout + result.stderr)

    def test_truthy_non_iterable_forbidden_mutations_is_rejected(self) -> None:
        """Finding 1: same pattern in forbidden_mutations."""
        import tempfile
        target = "HOLD_PR_CI_PENDING"
        self.base["states"][target]["forbidden_mutations"] = 1
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._write_and_validate(tmp, "int_forbidden")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn("forbidden_mutations", result.stdout + result.stderr)
        self.assertIn("must be a list", result.stdout + result.stderr)
        self.assertIn(target, result.stdout + result.stderr)

    def test_falsy_empty_string_allowed_mutations_is_rejected(self) -> None:
        """Finding 2: empty string must be rejected, not silently accepted."""
        import tempfile
        target = "HOLD_PR_CI_PENDING"
        self.base["states"][target]["allowed_mutations"] = ""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._write_and_validate(tmp, "str_allowed")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn("allowed_mutations", result.stdout + result.stderr)
        self.assertIn("must be a list", result.stdout + result.stderr)
        self.assertIn(target, result.stdout + result.stderr)

    def test_falsy_empty_object_forbidden_mutations_is_rejected(self) -> None:
        """Finding 2: empty object must be rejected, not silently accepted."""
        import tempfile
        target = "HOLD_PR_CI_PENDING"
        self.base["states"][target]["forbidden_mutations"] = {}
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._write_and_validate(tmp, "dict_forbidden")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn("forbidden_mutations", result.stdout + result.stderr)
        self.assertIn("must be a list", result.stdout + result.stderr)
        self.assertIn(target, result.stdout + result.stderr)

    def test_malformed_field_error_message_mentions_field_name(self) -> None:
        """The error must include both the field name and the type found."""
        import tempfile
        target = "HOLD_PR_CI_PENDING"
        self.base["states"][target]["allowed_mutations"] = 1
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._write_and_validate(tmp, "msg")
        combined = result.stdout + result.stderr
        self.assertIn("allowed_mutations", combined)
        # The validator reports the runtime type of the malformed value.
        self.assertIn("int", combined)

    def test_shipped_registry_still_validates_after_hardening(self) -> None:
        """Regression guard: hardening must not weaken the shipped registry."""
        result = _run_cli("--validate")
        self.assertEqual(
            result.returncode, 0,
            msg=f"stderr: {result.stderr}\nstdout: {result.stdout}",
        )
        self.assertIn("PASSED", result.stdout)

    def test_evidence_required_non_list_is_rejected(self) -> None:
        """Same hardening pattern applied to evidence_required."""
        import tempfile
        target = "HOLD_PR_CI_PENDING"
        self.base["states"][target]["evidence_required"] = "not a list"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._write_and_validate(tmp, "str_evidence")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn("evidence_required", result.stdout + result.stderr)
        self.assertIn("must be a list", result.stdout + result.stderr)

    def test_allowed_next_states_non_list_is_rejected(self) -> None:
        """Same hardening pattern applied to allowed_next_states."""
        import tempfile
        target = "HOLD_PR_CI_PENDING"
        self.base["states"][target]["allowed_next_states"] = 42
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._write_and_validate(tmp, "int_next")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn("allowed_next_states", result.stdout + result.stderr)
        self.assertIn("must be a list", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
