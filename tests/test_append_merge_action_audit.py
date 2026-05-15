"""Tests for append_merge_action_audit.py"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
from append_merge_action_audit import (
    AUDIT_LOG_VERSION,
    VALID_EVENT_TYPES,
    build_entry,
    _is_valid_sha,
    _validate_entry,
    _validate_pr_merge_fields,
    _validate_controlled_smoke_create_fields,
    append_entry,
    main,
)


class TestIsValidSha:
    def test_valid_40_hex(self):
        assert _is_valid_sha("62e602e374cf666cf63e29de3bd28acb0fae97ea") is True

    def test_too_short(self):
        assert _is_valid_sha("62e602e374cf666cf63e29de3bd28acb0fae97e") is False

    def test_too_long(self):
        assert _is_valid_sha("62e602e374cf666cf63e29de3bd28acb0fae97ea00") is False

    def test_non_hex_char(self):
        assert _is_valid_sha("62e602e374cf666cf63e29de3bd28acb0fae97eg") is False

    def test_empty(self):
        assert _is_valid_sha("") is False

    def test_not_a_string(self):
        assert _is_valid_sha(None) is False
        assert _is_valid_sha(123) is False


class TestValidatePrMergeFields:
    def test_valid_pr_merge(self):
        entry = {
            "event_type": "pr_merge",
            "pr_number": 217,
            "head_sha": "62e602e374cf666cf63e29de3bd28acb0fae97ea",
            "merge_sha": "d3de12a348da42009767887d05ff6dcd66b1c900",
            "merged_at": "2026-05-14T20:09:40Z",
        }
        assert _validate_pr_merge_fields(entry) == []

    def test_missing_pr_number(self):
        entry = {
            "event_type": "pr_merge",
            "head_sha": "62e602e374cf666cf63e29de3bd28acb0fae97ea",
            "merge_sha": "d3de12a348da42009767887d05ff6dcd66b1c900",
            "merged_at": "2026-05-14T20:09:40Z",
        }
        errors = _validate_pr_merge_fields(entry)
        assert any("pr_number" in e for e in errors)

    def test_invalid_head_sha(self):
        entry = {
            "event_type": "pr_merge",
            "pr_number": 217,
            "head_sha": "bad_sha",
            "merge_sha": "d3de12a348da42009767887d05ff6dcd66b1c900",
            "merged_at": "2026-05-14T20:09:40Z",
        }
        errors = _validate_pr_merge_fields(entry)
        assert any("head_sha" in e for e in errors)

    def test_invalid_merge_sha(self):
        entry = {
            "event_type": "pr_merge",
            "pr_number": 217,
            "head_sha": "62e602e374cf666cf63e29de3bd28acb0fae97ea",
            "merge_sha": "bad",
            "merged_at": "2026-05-14T20:09:40Z",
        }
        errors = _validate_pr_merge_fields(entry)
        assert any("merge_sha" in e for e in errors)


class TestValidateControlledSmokeCreateFields:
    def test_valid_smoke_create(self):
        entry = {
            "event_type": "controlled_smoke_create",
            "board": "aed-test",
            "task_id": "t_58d1338c",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        assert _validate_controlled_smoke_create_fields(entry) == []

    def test_missing_board(self):
        entry = {
            "event_type": "controlled_smoke_create",
            "task_id": "t_58d1338c",
        }
        errors = _validate_controlled_smoke_create_fields(entry)
        assert any("board" in e for e in errors)

    def test_missing_task_id(self):
        entry = {
            "event_type": "controlled_smoke_create",
            "board": "aed-test",
        }
        errors = _validate_controlled_smoke_create_fields(entry)
        assert any("task_id" in e for e in errors)


class TestValidateEntry:
    def test_unknown_event_type(self):
        entry = {"event_type": "unknown_event"}
        errors = _validate_entry(entry)
        assert len(errors) > 0

    def test_valid_pr_merge(self):
        entry = {
            "event_type": "pr_merge",
            "pr_number": 217,
            "head_sha": "62e602e374cf666cf63e29de3bd28acb0fae97ea",
            "merge_sha": "d3de12a348da42009767887d05ff6dcd66b1c900",
            "merged_at": "2026-05-14T20:09:40Z",
        }
        assert _validate_entry(entry) == []

    def test_valid_controlled_smoke_create(self):
        entry = {
            "event_type": "controlled_smoke_create",
            "board": "aed-test",
            "task_id": "t_58d1338c",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        assert _validate_entry(entry) == []

    def test_valid_blocked_action(self):
        entry = {
            "event_type": "blocked_action",
            "action_requested": "gh pr merge",
            "blocked_reason": "CI failure",
            "stop_rule_triggered": "ci_not_green",
            "files_or_boards_involved": ["main"],
            "remediation_path": "Wait for CI to pass",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        assert _validate_entry(entry) == []

    def test_valid_external_action(self):
        entry = {"event_type": "external_action", "authorization": "human auth"}
        assert _validate_entry(entry) == []


class TestBuildEntry:
    def test_pr_merge_minimal(self):
        entry = build_entry(
            event_type="pr_merge",
            pr_number=217,
            head_sha="62e602e374cf666cf63e29de3bd28acb0fae97ea",
            merge_sha="d3de12a348da42009767887d05ff6dcd66b1c900",
            merged_at="2026-05-14T20:09:40Z",
        )
        assert entry["event_type"] == "pr_merge"
        assert entry["pr_number"] == 217
        assert entry["head_sha"] == "62e602e374cf666cf63e29de3bd28acb0fae97ea"
        assert entry["merge_sha"] == "d3de12a348da42009767887d05ff6dcd66b1c900"
        assert entry["audit_log_version"] == AUDIT_LOG_VERSION
        assert "timestamp" in entry

    def test_pr_merge_all_fields(self):
        entry = build_entry(
            event_type="pr_merge",
            pr_number=218,
            branch="ci/wfa-minute-optimization",
            head_sha="385529039a62b732409375db788e831c246a000e",
            merge_sha="50cc479af344df655a031ce1cfc09424d216bf50",
            merged_at="2026-05-14T22:46:01Z",
            ci_status="success",
            codex_status="clean",
            scope_status="clean",
            authorization="I confirm merge PR #218 ...",
            hermes_touched=False,
            dispatch_occurred=False,
        )
        assert entry["hermes_touched"] is False
        assert entry["dispatch_occurred"] is False
        assert entry["branch"] == "ci/wfa-minute-optimization"
        assert entry["ci_status"] == "success"
        assert entry["codex_status"] == "clean"
        assert entry["scope_status"] == "clean"

    def test_controlled_smoke_create(self):
        entry = build_entry(
            event_type="controlled_smoke_create",
            board="aed-test",
            task_id="t_58d1338c",
            smoke_artifact_ids=["t_58d1338c"],
            hermes_touched=True,
            dispatch_occurred=False,
            production_board_touched=False,
        )
        assert entry["event_type"] == "controlled_smoke_create"
        assert entry["board"] == "aed-test"
        assert entry["task_id"] == "t_58d1338c"
        assert entry["dispatch_occurred"] is False
        assert entry["hermes_touched"] is True
        assert entry["production_board_touched"] is False
        # Validation should pass with all governance fields present
        errors = _validate_entry(entry)
        assert errors == [], f"Expected no errors, got {errors}"

    def test_controlled_smoke_create_missing_governance_fields(self):
        """controlled_smoke_create without dispatch_occurred or production_board_touched fails."""
        entry = {
            "event_type": "controlled_smoke_create",
            "board": "aed-test",
            "task_id": "t_58d1338c",
            # missing dispatch_occurred and production_board_touched
        }
        errors = _validate_entry(entry)
        assert len(errors) == 2
        assert any("dispatch_occurred" in e for e in errors)
        assert any("production_board_touched" in e for e in errors)

    def test_controlled_smoke_create_true_governance_rejected(self):
        """controlled_smoke_create with dispatch_occurred=True must fail validation."""
        entry = {
            "event_type": "controlled_smoke_create",
            "board": "aed-test",
            "task_id": "t_58d1338c",
            "dispatch_occurred": True,  # should be False
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert len(errors) >= 1
        assert any("dispatch_occurred" in e and "False" in e for e in errors)

    def test_controlled_smoke_create_production_board_true_rejected(self):
        """controlled_smoke_create with production_board_touched=True must fail validation."""
        entry = {
            "event_type": "controlled_smoke_create",
            "board": "aed-test",
            "task_id": "t_58d1338c",
            "dispatch_occurred": False,
            "production_board_touched": True,  # should be False
        }
        errors = _validate_entry(entry)
        assert len(errors) >= 1
        assert any("production_board_touched" in e and "False" in e for e in errors)

    def test_controlled_smoke_create_all_governance_valid(self):
        """controlled_smoke_create with all governance fields explicitly False passes."""
        entry = {
            "event_type": "controlled_smoke_create",
            "board": "aed-test",
            "task_id": "t_58d1338c",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert errors == []

    def test_blocked_action(self):
        entry = build_entry(
            event_type="blocked_action",
            blocker_or_exception="CI failure on 3855290",
        )
        assert entry["event_type"] == "blocked_action"
        assert entry["blocker_or_exception"] == "CI failure on 3855290"

    def test_extra_forward_compatibility(self):
        entry = build_entry(
            event_type="external_action",
            authorization="human",
            extra={"custom_field": "value"},
        )
        assert entry["custom_field"] == "value"


class TestAppendEntry:
    def test_append_creates_parent_dirs(self, tmp_path):
        log_path = tmp_path / "sub" / "nested" / "log.jsonl"
        entry = build_entry(
            event_type="pr_merge",
            pr_number=999,
            head_sha="a" * 40,
            merge_sha="b" * 40,
            merged_at="2026-01-01T00:00:00Z",
        )
        append_entry(entry, log_path)
        assert log_path.exists()
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["pr_number"] == 999

    def test_append_multiple_lines(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        for i in range(3):
            entry = build_entry(
                event_type="pr_merge",
                pr_number=100 + i,
                head_sha="a" * 40,
                merge_sha="b" * 40,
                merged_at="2026-01-01T00:00:00Z",
            )
            append_entry(entry, log_path)
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "pr_number" in parsed

    def test_entry_roundtrips_jsonl(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        entry = build_entry(
            event_type="controlled_smoke_create",
            board="aed-test",
            task_id="t_58d1338c",
            dispatch_occurred=False,
        )
        append_entry(entry, log_path)
        with open(log_path) as f:
            parsed = json.loads(f.readline())
        assert parsed["event_type"] == "controlled_smoke_create"
        assert parsed["task_id"] == "t_58d1338c"
        assert parsed["dispatch_occurred"] is False


class TestMainCLI:
    def test_pr_merge_dry_run(self, capsys, monkeypatch):
        """Dry-run prints JSON to stdout without writing."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "217",
            "--head-sha", "62e602e374cf666cf63e29de3bd28acb0fae97ea",
            "--merge-sha", "d3de12a348da42009767887d05ff6dcd66b1c900",
            "--merged-at", "2026-05-14T20:09:40Z",
            "--ci-status", "success",
            "--codex-status", "clean",
            "--scope-status", "clean",
            "--authorization", "I confirm",
            "--no-dispatch-occurred",
            "--no-hermes-touched",
            "--dry-run",
        ])
        rc = main()
        captured = capsys.readouterr()
        assert rc == 0
        parsed = json.loads(captured.out.strip())
        assert parsed["pr_number"] == 217

    def test_pr_merge_writes_to_file(self, tmp_path, capsys, monkeypatch):
        """--output writes to the specified JSONL file."""
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "218",
            "--head-sha", "385529039a62b732409375db788e831c246a000e",
            "--merge-sha", "50cc479af344df655a031ce1cfc09424d216bf50",
            "--merged-at", "2026-05-14T22:46:01Z",
            "--ci-status", "success",
            "--codex-status", "clean",
            "--scope-status", "clean",
            "--no-dispatch-occurred",
            "--no-hermes-touched",
            "--output", str(log_path),
        ])
        rc = main()
        assert rc == 0
        assert log_path.exists()
        with open(log_path) as f:
            parsed = json.loads(f.readline())
        assert parsed["pr_number"] == 218

    def test_smoke_create_dry_run(self, capsys, monkeypatch):
        """controlled_smoke_create dry-run prints valid JSON."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "controlled_smoke_create",
            "--board", "aed-test",
            "--task-id", "t_58d1338c",
            "--no-dispatch-occurred",
            "--no-production-board-touched",
            "--dry-run",
        ])
        rc = main()
        captured = capsys.readouterr()
        assert rc == 0
        parsed = json.loads(captured.out.strip())
        assert parsed["event_type"] == "controlled_smoke_create"
        assert parsed["board"] == "aed-test"
        assert parsed["task_id"] == "t_58d1338c"

    def test_invalid_event_type(self, capsys, monkeypatch):
        """Unknown event_type exits 1 with error."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "invalid_event",
            "--dry-run",
        ])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0

    def test_pr_merge_missing_pr_number(self, capsys, monkeypatch):
        """pr_merge without --pr-number exits 1 with pr_number error."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--head-sha", "62e602e374cf666cf63e29de3bd28acb0fae97ea",
            "--merge-sha", "d3de12a348da42009767887d05ff6dcd66b1c900",
            "--merged-at", "2026-05-14T20:09:40Z",
            "--dry-run",
        ])
        rc = main()
        assert rc == 1
        captured = capsys.readouterr()
        assert "pr_number" in captured.err

    def test_invalid_sha_rejected(self, capsys, monkeypatch):
        """Malformed SHA exits 1 with SHA error."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "217",
            "--head-sha", "bad_sha",
            "--merge-sha", "d3de12a348da42009767887d05ff6dcd66b1c900",
            "--merged-at", "2026-05-14T20:09:40Z",
            "--dry-run",
        ])
        rc = main()
        assert rc == 1
        captured = capsys.readouterr()
        assert "head_sha" in captured.err

    def test_controlled_smoke_create_writes(self, tmp_path, capsys, monkeypatch):
        """controlled_smoke_create writes valid entry."""
        log_path = tmp_path / "smoke.jsonl"
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "controlled_smoke_create",
            "--board", "aed-test",
            "--task-id", "t_58d1338c",
            "--no-dispatch-occurred",
            "--no-production-board-touched",
            "--output", str(log_path),
        ])
        rc = main()
        assert rc == 0
        with open(log_path) as f:
            parsed = json.loads(f.readline())
        assert parsed["task_id"] == "t_58d1338c"
        assert parsed["dispatch_occurred"] is False

    def test_external_action_event_type(self, tmp_path, capsys, monkeypatch):
        """external_action is a valid event type."""
        log_path = tmp_path / "ext.jsonl"
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "external_action",
            "--authorization", "human direct",
            "--blocker-or-exception", "test",
            "--no-dispatch-occurred",
            "--output", str(log_path),
        ])
        rc = main()
        assert rc == 0
        with open(log_path) as f:
            parsed = json.loads(f.readline())
        assert parsed["event_type"] == "external_action"

    def test_blocked_action_event_type(self, tmp_path, capsys, monkeypatch):
        """blocked_action is a valid event type when all required fields are provided."""
        log_path = tmp_path / "blocked.jsonl"
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "blocked_action",
            "--action-requested", "gh pr merge",
            "--blocked-reason", "CI not green",
            "--stop-rule-triggered", "ci_not_green",
            "--files-or-boards-involved", "main",
            "--remediation-path", "Wait for CI to pass",
            "--no-dispatch-occurred",
            "--no-production-board-touched",
            "--output", str(log_path),
        ])
        rc = main()
        assert rc == 0
        with open(log_path) as f:
            parsed = json.loads(f.readline())
        assert parsed["event_type"] == "blocked_action"
        assert parsed["dispatch_occurred"] is False
        assert parsed["production_board_touched"] is False


# ---------------------------------------------------------------------------
# Authorization phrase — Trace Policy V1 alignment tests
# ---------------------------------------------------------------------------

class TestAuthorizationPhrase:
    """Tests for --authorization-phrase / authorization_phrase field."""

    def test_build_entry_authorization_phrase_emits_authorization_phrase(self):
        """build_entry with authorization_phrase emits authorization_phrase field."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=220,
            head_sha="9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            merge_sha="31a35dbb1b181554ebde85c2ff6f3837d949430c",
            merged_at="2026-05-15T04:03:20Z",
            authorization_phrase="MERGE SHA 31a35db from branch fix/audit ...",
        )
        assert "authorization_phrase" in entry
        assert entry["authorization_phrase"] == "MERGE SHA 31a35db from branch fix/audit ..."
        assert "authorization" not in entry  # canonical field only

    def test_build_entry_authorization_alias_converts_to_authorization_phrase(self):
        """build_entry with authorization (alias) emits authorization_phrase."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=220,
            head_sha="9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            merge_sha="31a35dbb1b181554ebde85c2ff6f3837d949430c",
            merged_at="2026-05-15T04:03:20Z",
            authorization="legacy auth phrase",
        )
        assert "authorization_phrase" in entry
        assert entry["authorization_phrase"] == "legacy auth phrase"
        assert "authorization" not in entry  # canonical field only

    def test_authorization_phrase_takes_precedence_over_authorization(self):
        """When both are passed, authorization_phrase wins."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=220,
            head_sha="9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            merge_sha="31a35dbb1b181554ebde85c2ff6f3837d949430c",
            merged_at="2026-05-15T04:03:20Z",
            authorization_phrase="canonical phrase",
            authorization="alias phrase",
        )
        # build_entry does not raise; it just uses authorization_phrase
        assert entry["authorization_phrase"] == "canonical phrase"
        assert "authorization" not in entry


class TestGateCatches:
    """Tests for --gate-catches / gate_catches field."""

    def test_gate_catches_empty_by_default(self):
        """gate_catches is absent when not provided."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=220,
            head_sha="9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            merge_sha="31a35dbb1b181554ebde85c2ff6f3837d949430c",
            merged_at="2026-05-15T04:03:20Z",
        )
        assert "gate_catches" not in entry

    def test_gate_catches_dict_emitted(self):
        """gate_catches dict is passed through as-is (Trace Policy V1 object format)."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=220,
            head_sha="9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            merge_sha="31a35dbb1b181554ebde85c2ff6f3837d949430c",
            merged_at="2026-05-15T04:03:20Z",
            gate_catches={"codex": "", "scope": "style suggestion", "ci": ""},
        )
        assert entry["gate_catches"] == {"codex": "", "scope": "style suggestion", "ci": ""}

    def test_gate_catches_default_empty_dict(self):
        """gate_catches defaults to {} when explicitly passed as empty-like."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=220,
            head_sha="9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            merge_sha="31a35dbb1b181554ebde85c2ff6f3837d949430c",
            merged_at="2026-05-15T04:03:20Z",
            gate_catches={},
        )
        assert entry["gate_catches"] == {}


class TestBlockedActionValidation:
    """Tests for blocked_action event validation."""

    def test_blocked_action_valid_full_entry(self):
        """Complete blocked_action entry passes validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "hermes kanban dispatch t_abc123",
            "blocked_reason": "dispatch requires explicit authorization",
            "stop_rule_triggered": "unreviewed_external_mutation",
            "files_or_boards_involved": ["aed", "t_abc123"],
            "remediation_path": "Obtain explicit dispatch authorization from human operator",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert errors == [], f"Expected no errors, got {errors}"

    def test_blocked_action_missing_action_requested(self):
        """Missing action_requested fails validation."""
        entry = {
            "event_type": "blocked_action",
            "blocked_reason": "some reason",
            "stop_rule_triggered": "ci_not_green",
            "files_or_boards_involved": ["aed"],
            "remediation_path": "fix CI",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert any("action_requested" in e for e in errors)

    def test_blocked_action_missing_blocked_reason(self):
        """Missing blocked_reason fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "gh pr merge",
            "stop_rule_triggered": "ci_not_green",
            "files_or_boards_involved": [],
            "remediation_path": "wait for CI",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert any("blocked_reason" in e for e in errors)

    def test_blocked_action_missing_stop_rule_triggered(self):
        """Missing stop_rule_triggered fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "gh pr merge",
            "blocked_reason": "CI not green",
            "files_or_boards_involved": ["main"],
            "remediation_path": "wait",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert any("stop_rule_triggered" in e for e in errors)

    def test_blocked_action_missing_files_or_boards_involved(self):
        """Missing files_or_boards_involved fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "gh pr merge",
            "blocked_reason": "CI not green",
            "stop_rule_triggered": "ci_not_green",
            "remediation_path": "wait",
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert any("files_or_boards_involved" in e for e in errors)

    def test_blocked_action_missing_remediation_path(self):
        """Missing remediation_path fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "gh pr merge",
            "blocked_reason": "CI not green",
            "stop_rule_triggered": "ci_not_green",
            "files_or_boards_involved": ["main"],
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert any("remediation_path" in e for e in errors)

    def test_blocked_action_dispatch_occurred_true_rejected(self):
        """blocked_action with dispatch_occurred=True fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "dispatch",
            "blocked_reason": "reason",
            "stop_rule_triggered": "rule",
            "files_or_boards_involved": [],
            "remediation_path": "fix",
            "dispatch_occurred": True,  # must be False
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert any("dispatch_occurred" in e and "False" in e for e in errors)

    def test_blocked_action_production_board_true_rejected(self):
        """blocked_action with production_board_touched=True fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "dispatch",
            "blocked_reason": "reason",
            "stop_rule_triggered": "rule",
            "files_or_boards_involved": [],
            "remediation_path": "fix",
            "dispatch_occurred": False,
            "production_board_touched": True,  # must be False
        }
        errors = _validate_entry(entry)
        assert any("production_board_touched" in e and "False" in e for e in errors)

    def test_blocked_action_missing_dispatch_occurred(self):
        """blocked_action without dispatch_occurred fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "dispatch",
            "blocked_reason": "reason",
            "stop_rule_triggered": "rule",
            "files_or_boards_involved": [],
            "remediation_path": "fix",
            "production_board_touched": False,
        }
        errors = _validate_entry(entry)
        assert any("dispatch_occurred" in e for e in errors)

    def test_blocked_action_missing_production_board_touched(self):
        """blocked_action without production_board_touched fails validation."""
        entry = {
            "event_type": "blocked_action",
            "action_requested": "dispatch",
            "blocked_reason": "reason",
            "stop_rule_triggered": "rule",
            "files_or_boards_involved": [],
            "remediation_path": "fix",
            "dispatch_occurred": False,
        }
        errors = _validate_entry(entry)
        assert any("production_board_touched" in e for e in errors)


class TestCLIIntegration:
    """CLI-level integration tests via sys.argv."""

    def test_authorization_phrase_cli_emits_authorization_phrase(self, capsys, monkeypatch):
        """--authorization-phrase emits authorization_phrase in JSON."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "220",
            "--head-sha", "9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            "--merge-sha", "31a35dbb1b181554ebde85c2ff6f3837d949430c",
            "--merged-at", "2026-05-15T04:03:20Z",
            "--authorization-phrase", "MERGE SHA 31a35db from branch fix/audit ...",
            "--no-hermes-touched",
            "--no-dispatch-occurred",
            "--dry-run",
        ])
        rc = main()
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out.strip())
        assert "authorization_phrase" in parsed
        assert parsed["authorization_phrase"] == "MERGE SHA 31a35db from branch fix/audit ..."
        assert "authorization" not in parsed

    def test_authorization_alias_cli_emits_authorization_phrase(self, capsys, monkeypatch):
        """--authorization emits authorization_phrase (alias converts)."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "220",
            "--head-sha", "9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            "--merge-sha", "31a35dbb1b181554ebde85c2ff6f3837d949430c",
            "--merged-at", "2026-05-15T04:03:20Z",
            "--authorization", "legacy auth phrase",
            "--no-hermes-touched",
            "--no-dispatch-occurred",
            "--dry-run",
        ])
        rc = main()
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out.strip())
        assert "authorization_phrase" in parsed
        assert parsed["authorization_phrase"] == "legacy auth phrase"
        assert "authorization" not in parsed

    def test_conflicting_authorization_and_phrase_fails(self, capsys, monkeypatch):
        """--authorization and --authorization-phrase with different values exits 1."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "220",
            "--head-sha", "9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            "--merge-sha", "31a35dbb1b181554ebde85c2ff6f3837d949430c",
            "--merged-at", "2026-05-15T04:03:20Z",
            "--authorization", "phrase one",
            "--authorization-phrase", "phrase two",
            "--no-hermes-touched",
            "--no-dispatch-occurred",
            "--dry-run",
        ])
        rc = main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "different values" in err

    def test_matching_authorization_and_phrase_succeeds(self, capsys, monkeypatch):
        """--authorization and --authorization-phrase with same value exits 0."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "220",
            "--head-sha", "9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            "--merge-sha", "31a35dbb1b181554ebde85c2ff6f3837d949430c",
            "--merged-at", "2026-05-15T04:03:20Z",
            "--authorization", "same phrase",
            "--authorization-phrase", "same phrase",
            "--no-hermes-touched",
            "--no-dispatch-occurred",
            "--dry-run",
        ])
        rc = main()
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out.strip())
        assert parsed["authorization_phrase"] == "same phrase"

    def test_gate_catches_comma_separated(self, capsys, monkeypatch):
        """--gate-catches codex,ci,scope emits {"codex":"","ci":"","scope":""}."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "220",
            "--head-sha", "9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            "--merge-sha", "31a35dbb1b181554ebde85c2ff6f3837d949430c",
            "--merged-at", "2026-05-15T04:03:20Z",
            "--gate-catches", "codex,ci,scope",
            "--no-hermes-touched",
            "--no-dispatch-occurred",
            "--dry-run",
        ])
        rc = main()
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out.strip())
        assert parsed["gate_catches"] == {"codex": "", "ci": "", "scope": ""}

    def test_gate_catches_emits_empty_dict_when_not_provided(self, capsys, monkeypatch):
        """--gate-catches absent: emit gate_catches={} per Trace Policy V1."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "220",
            "--head-sha", "9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            "--merge-sha", "31a35dbb1b181554ebde85c2ff6f3837d949430c",
            "--merged-at", "2026-05-15T04:03:20Z",
            "--no-hermes-touched",
            "--no-dispatch-occurred",
            "--dry-run",
        ])
        rc = main()
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out.strip())
        assert "gate_catches" in parsed
        assert parsed["gate_catches"] == {}

    def test_gate_catches_single_value(self, capsys, monkeypatch):
        """--gate-catches codex emits {"codex":""}."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "pr_merge",
            "--pr-number", "220",
            "--head-sha", "9de6857f2aa27d0e4e27ff3f87357dec517ddf90",
            "--merge-sha", "31a35dbb1b181554ebde85c2ff6f3837d949430c",
            "--merged-at", "2026-05-15T04:03:20Z",
            "--gate-catches", "codex",
            "--no-hermes-touched",
            "--no-dispatch-occurred",
            "--dry-run",
        ])
        rc = main()
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out.strip())
        assert parsed["gate_catches"] == {"codex": ""}

    def test_blocked_action_cli_full_entry(self, capsys, monkeypatch):
        """Full blocked_action CLI invocation passes validation."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "blocked_action",
            "--action-requested", "hermes kanban dispatch t_abc123",
            "--blocked-reason", "dispatch requires explicit authorization",
            "--stop-rule-triggered", "unreviewed_external_mutation",
            "--files-or-boards-involved", "aed", "t_abc123",
            "--remediation-path", "Obtain explicit dispatch authorization",
            "--no-dispatch-occurred",
            "--no-production-board-touched",
            "--dry-run",
        ])
        rc = main()
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out.strip())
        assert parsed["event_type"] == "blocked_action"
        assert parsed["action_requested"] == "hermes kanban dispatch t_abc123"
        assert parsed["blocked_reason"] == "dispatch requires explicit authorization"
        assert parsed["stop_rule_triggered"] == "unreviewed_external_mutation"
        assert parsed["files_or_boards_involved"] == ["aed", "t_abc123"]
        assert parsed["remediation_path"] == "Obtain explicit dispatch authorization"
        assert parsed["dispatch_occurred"] is False
        assert parsed["production_board_touched"] is False

    def test_blocked_action_cli_missing_required_field(self, capsys, monkeypatch):
        """blocked_action missing required field exits 1."""
        monkeypatch.setattr("sys.argv", [
            "append_merge_action_audit.py",
            "--event-type", "blocked_action",
            # missing --action-requested
            "--blocked-reason", "some reason",
            "--stop-rule-triggered", "rule",
            "--files-or-boards-involved", "aed",
            "--remediation-path", "fix it",
            "--no-dispatch-occurred",
            "--no-production-board-touched",
            "--dry-run",
        ])
        rc = main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "action_requested" in err


# ---------------------------------------------------------------------------
# Example entries from the spec (must round-trip through build_entry)
# ---------------------------------------------------------------------------

class TestSpecExampleEntries:
    def test_pr_217_merge_entry(self):
        """PR #217 merge as per spec example."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=217,
            head_sha="62e602e374cf666cf63e29de3bd28acb0fae97ea",
            merge_sha="d3de12a348da42009767887d05ff6dcd66b1c900",
            merged_at="2026-05-14T20:09:40Z",
            ci_status="success",
            codex_status="clean",
            scope_status="clean",
            dispatch_occurred=False,
            hermes_touched=False,
            production_board_touched=False,
        )
        errors = _validate_entry(entry)
        assert errors == [], f"PR #217 entry has validation errors: {errors}"
        assert entry["dispatch_occurred"] is False
        assert entry["production_board_touched"] is False

    def test_pr_218_merge_entry(self):
        """PR #218 merge as per spec example."""
        entry = build_entry(
            event_type="pr_merge",
            pr_number=218,
            branch="ci/wfa-minute-optimization",
            head_sha="385529039a62b732409375db788e831c246a000e",
            merge_sha="50cc479af344df655a031ce1cfc09424d216bf50",
            merged_at="2026-05-14T22:46:01Z",
            ci_status="success",
            codex_status="clean",
            scope_status="clean",
            dispatch_occurred=False,
            hermes_touched=False,
            production_board_touched=False,
        )
        errors = _validate_entry(entry)
        assert errors == [], f"PR #218 entry has validation errors: {errors}"
        assert entry["dispatch_occurred"] is False

    def test_smoke_artifact_entry(self):
        """Clean smoke artifact t_58d1338c as per spec example."""
        entry = build_entry(
            event_type="controlled_smoke_create",
            board="aed-test",
            task_id="t_58d1338c",
            status="triage",
            assignee="",
            dispatch_occurred=False,
            worker_run_spawned=False,
            production_board_touched=False,
        )
        errors = _validate_entry(entry)
        assert errors == [], f"Smoke artifact entry has validation errors: {errors}"
        assert entry["task_id"] == "t_58d1338c"
        assert entry["dispatch_occurred"] is False
        assert entry["worker_run_spawned"] is False
        assert entry["production_board_touched"] is False