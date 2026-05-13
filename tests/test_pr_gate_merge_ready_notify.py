"""Tests for pr_gate_merge_ready_notify.py"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT = Path(__file__).parents[1] / "scripts" / "local" / "pr_gate_merge_ready_notify.py"


def _import_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pr_gate_merge_ready_notify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _import_mod()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_output_dir(tmp_path):
    out = tmp_path / "notify_out"
    out.mkdir()
    return out


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

VALID_PR = {
    "number": 199,
    "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/199",
    "head_sha": "81265a4ca530bb6af798606fd17baa34ed542211",
    "base_branch": "main",
}


# ---------------------------------------------------------------------------
# Source-safety helpers
# ---------------------------------------------------------------------------

def _forbidden_list_bounds(src_text: str):
    """Return (start, end) inclusive line indices (0-based) of FORBIDDEN_PATTERNS list."""
    src_lines = src_text.split("\n")
    start = end = -1
    for i, line in enumerate(src_lines):
        if "FORBIDDEN_PATTERNS" in line and "=" in line:
            start = i
        if start >= 0 and end < 0 and line.strip().startswith("]"):
            end = i
            break
    return start, end


def _line_has_call(line: str, pattern: str) -> bool:
    """Check if line contains pattern as a whole word (not substring of another word)."""
    return bool(re.search(r"\b" + re.escape(pattern) + r"\b", line))


# ---------------------------------------------------------------------------
# Test 1: valid direct input creates merge-ready packet
# ---------------------------------------------------------------------------

def test_valid_direct_input_creates_merge_ready_packet(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199",
            "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--base-branch", "main",
            "--ci-status", "green",
            "--codex-status", "clean",
            "--fallback-review-status", "clean",
            "--reviewer-status", "approved",
            "--scope-status", "clean",
            "--mergeable",
            "--changed-file", "scripts/local/pr_gate_controller.py",
            "--changed-file", "tests/test_pr_gate_merge_ready_notify.py",
            "--output-json", str(out_json),
            "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    packet = json.loads(out_json.read_text())
    assert packet["packet_kind"] == "aed.pr_gate.merge_ready_notification.v1"
    assert packet["schema_version"] == 1
    assert packet["pr"]["number"] == 199
    assert packet["pr"]["head_sha"] == VALID_PR["head_sha"]
    assert packet["recommendation"] == "merge_ready"
    assert packet["required_authorization_phrase"] == (
        f"I confirm merge PR #199 at {VALID_PR['head_sha']}"
    )
    assert "81265a4ca530bb6af798606fd17baa34ed542211" in packet["required_authorization_phrase"]
    assert "PR #199" in packet["required_authorization_phrase"]
    assert packet["merge_command_template"] is not None
    assert "--match-head-commit 81265a4ca530bb6af798606fd17baa34ed542211" in packet["merge_command_template"]
    assert packet["blockers_or_uncertainty"] == []


# ---------------------------------------------------------------------------
# Test 2: valid merge-ready-packet mode creates notification
# ---------------------------------------------------------------------------

def test_merge_ready_packet_mode_creates_notification(tmp_path):
    mrp = tmp_path / "MERGE_READY_PACKET.json"
    crp = tmp_path / "CONTROLLER_RUN_PACKET.json"
    out_json = tmp_path / "notification.json"
    out_md = tmp_path / "notification.md"
    mrp.write_text(json.dumps({
        "pr": {
            "number": 199, "url": VALID_PR["url"],
            "head_sha": VALID_PR["head_sha"], "base_branch": "main",
        },
        "ci_status": "green", "scope_status": "clean",
        "mergeable": True,
        "changed_files": ["scripts/local/pr_gate_controller.py"],
    }))
    crp.write_text(json.dumps({
        "result": {
            "ci_status": "green", "codex_status": "clean",
            "reviewer_status": "approved",
        }
    }))
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--merge-ready-packet", str(mrp),
            "--controller-run-packet", str(crp),
            "--output-json", str(out_json),
            "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "merge_ready"


# ---------------------------------------------------------------------------
# Test 3: required phrase includes PR number and full SHA
# ---------------------------------------------------------------------------

def test_required_phrase_includes_pr_number_and_full_sha():
    phrase = MOD._build_required_phrase(199, VALID_PR["head_sha"])
    assert "199" in phrase
    assert VALID_PR["head_sha"] in phrase
    assert len(VALID_PR["head_sha"]) == 40


# ---------------------------------------------------------------------------
# Test 4: short SHA fails
# ---------------------------------------------------------------------------

def test_short_sha_fails(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", "abc123",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "40-character" in result.stderr or "full" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Test 5: red CI produces not_merge_ready
# ---------------------------------------------------------------------------

def test_red_ci_produces_not_merge_ready(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "red", "--codex-status", "clean",
            "--fallback-review-status", "clean", "--reviewer-status", "approved",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "not_merge_ready"
    assert any("CI" in b for b in packet["blockers_or_uncertainty"])


# ---------------------------------------------------------------------------
# Test 6: dirty scope produces not_merge_ready
# ---------------------------------------------------------------------------

def test_dirty_scope_produces_not_merge_ready(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "clean",
            "--fallback-review-status", "clean", "--reviewer-status", "approved",
            "--scope-status", "dirty",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "not_merge_ready"
    assert any("Scope" in b for b in packet["blockers_or_uncertainty"])


# ---------------------------------------------------------------------------
# Test 7: missing review produces not_merge_ready
# ---------------------------------------------------------------------------

def test_missing_review_produces_not_merge_ready(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "pending",
            "--fallback-review-status", "none", "--reviewer-status", "pending",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "not_merge_ready"


# ---------------------------------------------------------------------------
# Test 8: blockers prevent merge-ready phrase
# ---------------------------------------------------------------------------

def test_blockers_prevent_merge_ready_phrase(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "pending",
            "--fallback-review-status", "none", "--reviewer-status", "pending",
            "--scope-status", "clean",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "not_merge_ready"
    assert packet["required_authorization_phrase"] is None
    assert packet["merge_command_template"] is None
    assert len(packet["blockers_or_uncertainty"]) > 0


# ---------------------------------------------------------------------------
# Test 9: markdown includes exact authorization phrase
# ---------------------------------------------------------------------------

def test_markdown_includes_exact_authorization_phrase(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "clean",
            "--fallback-review-status", "clean", "--reviewer-status", "approved",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    md_text = out_md.read_text()
    assert "I confirm merge PR #199 at" in md_text
    assert VALID_PR["head_sha"] in md_text


# ---------------------------------------------------------------------------
# Test 10: markdown includes --match-head-commit
# ---------------------------------------------------------------------------

def test_markdown_includes_match_head_commit(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "clean",
            "--fallback-review-status", "clean", "--reviewer-status", "approved",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    md_text = out_md.read_text()
    assert "--match-head-commit" in md_text
    assert VALID_PR["head_sha"] in md_text


# ---------------------------------------------------------------------------
# Test 11: output path under /home/max/.hermes is rejected
# ---------------------------------------------------------------------------

def test_hermes_output_path_rejected(tmp_output_dir):
    out_json = Path("/home/max/.hermes/test_notification.json")
    out_md = Path("/home/max/.hermes/test_notification.md")
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "clean",
            "--fallback-review-status", "clean", "--reviewer-status", "approved",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "/home/max/.hermes" in result.stderr


# ---------------------------------------------------------------------------
# Test 12: no forbidden API/network calls present (source check)
# ---------------------------------------------------------------------------

def test_no_forbidden_calls_in_source():
    """Verify no forbidden API/network calls exist outside FORBIDDEN_PATTERNS list."""
    src = SCRIPT.read_text()
    # Patterns that indicate actual calls (word-boundary matched to avoid STOP_RULES false positives)
    forbidden_calls = [
        "send_message", "requests.get", "requests.post",
        "requests.patch", "requests.put", "urllib.request", "httpx",
        "hermes kanban", "git push", "git commit",
        "memory.update", "fact_store", "skill_manage",
        "delegate_task", "cronjob",
    ]
    skip_start, skip_end = _forbidden_list_bounds(src)
    src_lines = src.split("\n")
    for lineno, line in enumerate(src_lines, 1):
        # Skip FORBIDDEN_PATTERNS list definition itself
        if skip_start >= 0 and skip_end >= 0 and skip_start <= lineno - 1 <= skip_end:
            continue
        # Skip f-strings (template strings, not actual calls)
        stripped = line.strip()
        if stripped.startswith("f\"") or stripped.startswith("f'"):
            continue
        # Skip help= strings (argparse help text, not actual calls)
        if "help=" in line:
            continue
        for pattern in forbidden_calls:
            if _line_has_call(line, pattern):
                assert False, f"Forbidden pattern '{pattern}' found at line {lineno}: {line.strip()}"


# ---------------------------------------------------------------------------
# Test 13: no GitHub merge/comment/create calls present (source check)
# ---------------------------------------------------------------------------

def test_no_github_calls_in_source():
    """Verify no GitHub merge/comment/create CLI calls exist outside FORBIDDEN_PATTERNS."""
    src = SCRIPT.read_text()
    github_call_terms = [
        "gh pr merge", "gh pr comment", "gh pr create", "gh api repos",
    ]
    skip_start, skip_end = _forbidden_list_bounds(src)
    src_lines = src.split("\n")
    for lineno, line in enumerate(src_lines, 1):
        if skip_start >= 0 and skip_end >= 0 and skip_start <= lineno - 1 <= skip_end:
            continue
        # Skip f-strings (command template strings like f"gh pr merge {pr_number} ...")
        stripped = line.strip()
        if stripped.startswith("f\"") or stripped.startswith("f'"):
            continue
        if "gh" in line and any(c in line for c in github_call_terms):
            assert False, f"GitHub call found at line {lineno}: {line.strip()}"


# ---------------------------------------------------------------------------
# Test 14: no Hermes Kanban calls present (source check)
# ---------------------------------------------------------------------------

def test_no_hermes_kanban_calls_in_source():
    """Verify no hermes kanban CLI calls exist outside FORBIDDEN_PATTERNS."""
    src = SCRIPT.read_text()
    skip_start, skip_end = _forbidden_list_bounds(src)
    src_lines = src.split("\n")
    for lineno, line in enumerate(src_lines, 1):
        if skip_start >= 0 and skip_end >= 0 and skip_start <= lineno - 1 <= skip_end:
            continue
        if "hermes kanban" in line.lower():
            assert False, f"hermes kanban found at line {lineno}: {line.strip()}"


# ---------------------------------------------------------------------------
# Test 15: CLI returns 0 for clean packet
# ---------------------------------------------------------------------------

def test_cli_returns_zero_for_clean_packet(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "clean",
            "--fallback-review-status", "clean", "--reviewer-status", "approved",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "merge_ready" in result.stdout


# ---------------------------------------------------------------------------
# Test 16: CLI returns not_merge_ready for blocked packet
# ---------------------------------------------------------------------------

def test_blocked_packet_returns_not_merge_ready(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "red", "--codex-status", "pending",
            "--fallback-review-status", "none", "--reviewer-status", "pending",
            "--scope-status", "dirty",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    # Returns 0 but sets recommendation to not_merge_ready
    assert result.returncode == 0
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "not_merge_ready"
    assert packet["required_authorization_phrase"] is None


# ---------------------------------------------------------------------------
# Test: reviewer_status = not_required_with_reason is treated as clean
# ---------------------------------------------------------------------------

def test_not_required_with_reason_treated_as_clean(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "clean",
            "--fallback-review-status", "clean",
            "--reviewer-status", "not_required_with_reason",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "merge_ready"


# ---------------------------------------------------------------------------
# Test: mergeable=False produces not_merge_ready
# ---------------------------------------------------------------------------

def test_unmergeable_produces_not_merge_ready(tmp_output_dir):
    """PR with all gates clean but mergeable=False should be not_merge_ready."""
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "green", "--codex-status", "clean",
            "--fallback-review-status", "clean", "--reviewer-status", "approved",
            "--scope-status", "clean",
            # --mergeable omitted = False
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "not_merge_ready"
    assert packet["required_authorization_phrase"] is None
    assert any("mergeable" in b.lower() for b in packet["blockers_or_uncertainty"])


# ---------------------------------------------------------------------------
# Test: not_merge_ready blocked phrase when CI is red
# ---------------------------------------------------------------------------

def test_blocked_packet_does_not_include_merge_phrase(tmp_output_dir):
    out_json = tmp_output_dir / "notification.json"
    out_md = tmp_output_dir / "notification.md"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--pr-number", "199", "--pr-url", VALID_PR["url"],
            "--head-sha", VALID_PR["head_sha"],
            "--ci-status", "red", "--codex-status", "clean",
            "--scope-status", "clean", "--mergeable",
            "--output-json", str(out_json), "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    packet = json.loads(out_json.read_text())
    assert packet["required_authorization_phrase"] is None
    assert packet["merge_command_template"] is None
    assert len(packet["blockers_or_uncertainty"]) > 0


# ---------------------------------------------------------------------------
# Test: old MERGE_READY_PACKET (reviewed_clean, no scope_status) produces merge_ready
# ---------------------------------------------------------------------------

def test_old_packet_format_compatibility(tmp_path):
    """Old MERGE_READY_PACKET from build_merge_ready_packet.py (PR #193) works."""
    mrp = tmp_path / "MERGE_READY_PACKET.json"
    crp = tmp_path / "CONTROLLER_RUN_PACKET.json"
    out_json = tmp_path / "notification.json"
    out_md = tmp_path / "notification.md"

    # Old format: codex_status = "reviewed_clean", no scope_status, no fallback_review_status
    mrp.write_text(json.dumps({
        "pr": {
            "number": 193, "url": VALID_PR["url"],
            "head_sha": "af386e4c75341a2a6e7a6f68b680844de5cef1df",
            "base_branch": "main",
        },
        "ci_status": "green",
        "codex_status": "reviewed_clean",
        "reviewer_status": "approved",
        "mergeable": True,
        "changed_files": ["docs/README.md"],
        # note: no scope_status, no fallback_review_status
    }))
    crp.write_text(json.dumps({
        "result": {
            "ci_status": "green",
            "codex_status": "reviewed_clean",
            "reviewer_status": "approved",
        }
    }))
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--merge-ready-packet", str(mrp),
            "--controller-run-packet", str(crp),
            "--output-json", str(out_json),
            "--output-md", str(out_md),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "merge_ready", f"Got: {packet['blockers_or_uncertainty']}"


# ── Review Evidence Packet integration tests ─────────────────────────────────

REPO_OWNER = "Slideshow11"
REPO_NAME = "Automated-Edge-Discovery"
HEAD = "abc123" + "0" * 34  # 40-char


class TestReviewEvidenceIntegration:
    """Tests 12-14: merge-ready notification with review evidence."""

    def _make_review_evidence(self, stale: bool = False, merge_allowed: bool = True, **overrides) -> dict:
        """Build a REVIEW_EVIDENCE_PACKET dict for testing.

        When stale=True: current_head_sha=HEAD, reviewed_head_sha=old_head (stale by SHA mismatch).
        This tests stale detection without triggering the PATCH-3 head-mismatch block.
        """
        old_head = "deadbeef" + "1" * 32
        current = HEAD
        reviewed = old_head if stale else HEAD
        status = "clean" if merge_allowed else "pending"
        base = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "schema_version": 1,
            "generated_at": "2026-05-13T00:00:00+00:00",
            "repo_owner": REPO_OWNER,
            "repo_name": REPO_NAME,
            "pr_number": 207,
            "current_head_sha": current,
            "reviewed_head_sha": reviewed,
            "review_source": "github_codex",
            "review_status": status,
            "review_is_stale": stale,
            "ci_status": "green",
            "ci_required_jobs": ["test", "validator", "governance-validators", "pr-gate-live-smoke"],
            "ci_all_green": True,
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "scope_status": "clean",
            "mergeable": True,
            "merge_allowed": merge_allowed,
            "blockers_or_uncertainty": [] if merge_allowed else ["review is pending"],
            "recommended_merge_command": f"gh pr merge 207 --repo {REPO_OWNER}/{REPO_NAME} --squash --delete-branch --match-head-commit {current}",
        }
        for k, v in overrides.items():
            base[k] = v
        return base

    def _run_with_review_evidence(self, tmp_path, review_evidence: dict, extra_cli: list[str] | None = None) -> subprocess.CompletedProcess:
        """Run pr_gate_merge_ready_notify.py with --review-evidence."""
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(review_evidence))

        cli = [
            sys.executable, str(SCRIPT),
            "--pr-number", "207",
            "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
            "--head-sha", HEAD,
            "--base-branch", "main",
            "--ci-status", "green",
            "--codex-status", "clean",
            "--fallback-review-status", "clean",
            "--reviewer-status", "approved",
            "--scope-status", "clean",
            "--mergeable",
            "--changed-file", "docs/README.md",
            "--output-json", str(out_json),
            "--output-md", str(out_md),
            "--review-evidence", str(rev_path),
        ]
        if extra_cli:
            cli.extend(extra_cli)
        return subprocess.run(cli, capture_output=True, text=True)

    def test_review_evidence_included_in_notification_json(self, tmp_path):
        """Test 12: merge-ready notification includes review_source and reviewed_head_sha."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_evidence_summary" in packet, f"Missing review_evidence_summary: {packet.keys()}"
        summary = packet["review_evidence_summary"]
        assert summary["review_source"] == "github_codex"
        assert summary["reviewed_head_sha"] == HEAD
        assert summary["current_head_sha"] == HEAD
        assert summary["review_is_stale"] is False
        assert summary["ci_all_green"] is True
        assert summary["scope_status"] == "clean"

    def test_stale_review_evidence_produces_not_merge_ready(self, tmp_path):
        """Test 13: merge-ready notification refuses authorization phrase when evidence stale."""
        rev = self._make_review_evidence(stale=True, merge_allowed=False)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["recommendation"] == "not_merge_ready"
        assert packet["required_authorization_phrase"] is None
        assert packet["merge_command_template"] is None
        assert any("stale" in b.lower() for b in packet["blockers_or_uncertainty"]), \
            f"Expected stale blocker: {packet['blockers_or_uncertainty']}"

    def test_merge_allowed_false_produces_not_merge_ready(self, tmp_path):
        """Test 13b: merge_allowed=False produces not_merge_ready regardless of other fields."""
        rev = self._make_review_evidence(stale=False, merge_allowed=False)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["recommendation"] == "not_merge_ready"
        assert packet["required_authorization_phrase"] is None

    def test_review_evidence_included_in_notification_md(self, tmp_path):
        """Test 12b: merge-ready notification markdown includes review evidence summary."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        md_text = (tmp_path / "notification.md").read_text()
        assert "review_source" in md_text.lower() or "Review Evidence Summary" in md_text

    def test_review_evidence_passed_through_packet_mode(self, tmp_path):
        """Test: --review-evidence works in packet mode (--merge-ready-packet + --controller-run-packet)."""
        mrp = tmp_path / "MERGE_READY_PACKET.json"
        crp = tmp_path / "CONTROLLER_RUN_PACKET.json"
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev))

        mrp.write_text(json.dumps({
            "pr": {
                "number": 207, "url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
                "head_sha": HEAD, "base_branch": "main",
            },
            "ci_status": "green", "scope_status": "clean",
            "mergeable": True,
            "changed_files": ["docs/README.md"],
        }))
        crp.write_text(json.dumps({
            "result": {
                "ci_status": "green", "codex_status": "clean",
                "reviewer_status": "approved",
            }
        }))
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--merge-ready-packet", str(mrp),
             "--controller-run-packet", str(crp),
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads(out_json.read_text())
        assert "review_evidence_summary" in packet
        assert packet["review_evidence_summary"]["review_source"] == "github_codex"

    def test_wrong_review_evidence_packet_kind_fails(self, tmp_path):
        """Test: wrong packet_kind in --review-evidence raises error."""
        rev = dict(self._make_review_evidence())
        rev["packet_kind"] = "aed.wrong.v1"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev))

        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", HEAD,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "aed.wrong.v1" in result.stderr or "packet_kind" in result.stderr.lower()

    def test_ci_all_green_field_reflects_ci_status(self, tmp_path):
        """Test: ci_all_green in review evidence summary reflects ci_status."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True, ci_status="green", ci_all_green=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["review_evidence_summary"]["ci_all_green"] is True

        rev_red = self._make_review_evidence(stale=False, merge_allowed=False, ci_status="red", ci_all_green=False)
        result2 = self._run_with_review_evidence(tmp_path, rev_red)
        assert result2.returncode == 0
        packet2 = json.loads((tmp_path / "notification.json").read_text())
        assert packet2["review_evidence_summary"]["ci_all_green"] is False

    def test_scope_status_reflects_file_scope(self, tmp_path):
        """Test: scope_status in review evidence summary reflects changed/allowed files."""
        rev = self._make_review_evidence(
            stale=False, merge_allowed=True,
            changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"],
            scope_status="clean",
        )
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["review_evidence_summary"]["scope_status"] == "clean"

    def test_review_status_included_in_summary(self, tmp_path):
        """Test: review_status field is included in review_evidence_summary."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True, review_status="clean")
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_status" in packet["review_evidence_summary"]
        assert packet["review_evidence_summary"]["review_status"] == "clean"

    def test_no_review_evidence_still_produces_notification(self, tmp_path):
        """Test: running without --review-evidence still works (backward compat)."""
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", HEAD,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--fallback-review-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--changed-file", "docs/README.md",
             "--output-json", str(out_json),
             "--output-md", str(out_md)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_evidence_summary" not in packet
        assert packet["recommendation"] == "merge_ready"


    def test_forbidden_files_touched_withholds_auth_phrase(self, tmp_path):
        """Test: forbidden_files_touched non-empty → authorization phrase withheld."""
        rev = self._make_review_evidence(
            stale=False, merge_allowed=True,
            changed_files=[".github/workflows/ci.yml"],
            allowed_files=[".github/workflows/**"],
            scope_status="violation",
            scope_passed=False,
            forbidden_files_touched=[".github/workflows/ci.yml"],
            blockers_or_uncertainty=["scope: forbidden_file_touched"],
        )
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        # scope_status != clean → authorization phrase must be withheld
        assert packet["review_evidence_summary"]["scope_status"] == "violation"
        assert ".github/workflows/ci.yml" in packet["review_evidence_summary"]["forbidden_files_touched"]
        # The notification should show not_merge_ready or auth phrase absent
        assert packet["recommendation"] in ("not_merge_ready",)

    def test_clean_scope_with_forbidden_file_param_still_passes(self, tmp_path):
        """Test: clean allowed_files with empty forbidden_files → passes."""
        rev = self._make_review_evidence(
            stale=False, merge_allowed=True,
            changed_files=["docs/README.md"],
            allowed_files=["docs/**"],
            forbidden_files=[],
            scope_status="clean",
            scope_passed=True,
            forbidden_files_touched=[],
        )
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["review_evidence_summary"]["scope_status"] == "clean"
        assert packet["review_evidence_summary"]["forbidden_files_touched"] == []


# ── PATCH-3 notification tests ───────────────────────────────────────────────

class TestPatchFixesNotification:
    """Tests for PATCH-3 in pr_gate_merge_ready_notify.py."""

    def test_notification_rejects_review_evidence_for_different_head(self, tmp_path):
        """merge packet head is new_sha but review evidence current_head_sha is old_sha => not_merge_ready."""
        new_sha = "a" * 40
        old_sha = "b" * 40
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"

        rev = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "schema_version": 1,
            "generated_at": "2026-05-13T00:00:00+00:00",
            "repo_owner": REPO_OWNER,
            "repo_name": REPO_NAME,
            "pr_number": 207,
            "current_head_sha": old_sha,
            "reviewed_head_sha": old_sha,
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,
            "ci_status": "green",
            "ci_required_jobs": ["test", "validator", "governance-validators", "pr-gate-live-smoke"],
            "ci_all_green": True,
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "scope_status": "clean",
            "mergeable": True,
            "merge_allowed": True,
            "blockers_or_uncertainty": [],
        }
        rev_path.write_text(json.dumps(rev))

        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", new_sha,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--fallback-review-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--changed-file", "docs/README.md",
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads(out_json.read_text())
        assert packet["recommendation"] == "not_merge_ready", \
            f"Expected not_merge_ready for head mismatch: {packet['blockers_or_uncertainty']}"
        assert packet["required_authorization_phrase"] is None
        assert packet["merge_command_template"] is None
        head_mismatch = any("head mismatch" in b.lower() for b in packet["blockers_or_uncertainty"])
        assert head_mismatch, f"Expected head mismatch blocker: {packet['blockers_or_uncertainty']}"

    def test_notification_with_matching_head_produces_merge_ready(self, tmp_path):
        """review evidence current_head_sha matches notification head_sha => merge_ready."""
        sha = "a" * 40
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"

        rev = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "schema_version": 1,
            "generated_at": "2026-05-13T00:00:00+00:00",
            "repo_owner": REPO_OWNER,
            "repo_name": REPO_NAME,
            "pr_number": 207,
            "current_head_sha": sha,
            "reviewed_head_sha": sha,
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,
            "ci_status": "green",
            "ci_required_jobs": ["test", "validator", "governance-validators", "pr-gate-live-smoke"],
            "ci_all_green": True,
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "scope_status": "clean",
            "mergeable": True,
            "merge_allowed": True,
            "blockers_or_uncertainty": [],
        }
        rev_path.write_text(json.dumps(rev))

        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", sha,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--fallback-review-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--changed-file", "docs/README.md",
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads(out_json.read_text())
        assert packet["recommendation"] == "merge_ready", \
            f"Expected merge_ready for matching head: {packet['blockers_or_uncertainty']}"
        assert packet["required_authorization_phrase"] is not None
        assert sha in packet["required_authorization_phrase"]

    def test_notification_rejects_bogus_review_source_with_forged_merge_allowed(self, tmp_path):
        """review_source=bogus with merge_allowed=True (forged) => not_merge_ready via recompute."""
        sha = "a" * 40
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"

        rev = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "schema_version": 1,
            "generated_at": "2026-05-13T00:00:00+00:00",
            "repo_owner": REPO_OWNER,
            "repo_name": REPO_NAME,
            "pr_number": 207,
            "current_head_sha": sha,
            "reviewed_head_sha": sha,
            "review_source": "bogus",
            "review_status": "clean",
            "review_is_stale": False,
            "ci_status": "green",
            "ci_required_jobs": ["test", "validator", "governance-validators", "pr-gate-live-smoke"],
            "ci_all_green": True,
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "scope_status": "clean",
            "mergeable": True,
            "merge_allowed": True,
            "blockers_or_uncertainty": [],
        }
        rev_path.write_text(json.dumps(rev))

        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", sha,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--fallback-review-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--changed-file", "docs/README.md",
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads(out_json.read_text())
        assert packet["recommendation"] == "not_merge_ready", \
            f"Expected not_merge_ready for bogus source: {packet['blockers_or_uncertainty']}"
        assert packet["required_authorization_phrase"] is None
        assert any("merge_allowed=False" in b for b in packet["blockers_or_uncertainty"]), \
            f"Expected recomputed merge_allowed=False blocker: {packet['blockers_or_uncertainty']}"

    def test_notification_rejects_mergeable_false_forged_merge_allowed(self, tmp_path):
        """mergeable=False with merge_allowed=True (forged) => not_merge_ready via recompute."""
        sha = "a" * 40
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"

        rev = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "schema_version": 1,
            "generated_at": "2026-05-13T00:00:00+00:00",
            "repo_owner": REPO_OWNER,
            "repo_name": REPO_NAME,
            "pr_number": 207,
            "current_head_sha": sha,
            "reviewed_head_sha": sha,
            "review_source": "github_codex",
            "review_status": "clean",
            "review_is_stale": False,
            "ci_status": "green",
            "ci_required_jobs": ["test", "validator", "governance-validators", "pr-gate-live-smoke"],
            "ci_all_green": True,
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "scope_status": "clean",
            "mergeable": False,
            "merge_allowed": True,
            "blockers_or_uncertainty": [],
        }
        rev_path.write_text(json.dumps(rev))

        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", sha,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--fallback-review-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--changed-file", "docs/README.md",
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads(out_json.read_text())
        assert packet["recommendation"] == "not_merge_ready", \
            f"Expected not_merge_ready for mergeable=False: {packet['blockers_or_uncertainty']}"
        assert packet["required_authorization_phrase"] is None
        assert any("merge_allowed=False" in b for b in packet["blockers_or_uncertainty"]), \
            f"Expected recomputed merge_allowed=False: {packet['blockers_or_uncertainty']}"


def test_old_packet_codex_unavailable(tmp_path):
    """Old MERGE_READY_PACKET with codex_status=unavailable also works."""
    mrp = tmp_path / "MERGE_READY_PACKET.json"
    crp = tmp_path / "CONTROLLER_RUN_PACKET.json"
    out_json = tmp_path / "notification.json"
    out_md = tmp_path / "notification.md"

    mrp.write_text(json.dumps({
        "pr": {
            "number": 193, "url": VALID_PR["url"],
            "head_sha": "af386e4c75341a2a6e7a6f68b680844de5cef1df",
            "base_branch": "main",
        },
        "ci_status": "green",
        "codex_status": "unavailable",
        "reviewer_status": "approved",
        "mergeable": True,
        "changed_files": ["docs/README.md"],
    }))
    crp.write_text(json.dumps({"result": {
        "ci_status": "green",
        "codex_status": "unavailable",
        "reviewer_status": "approved",
    }}))
    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--merge-ready-packet", str(mrp),
         "--controller-run-packet", str(crp),
         "--output-json", str(out_json), "--output-md", str(out_md)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "merge_ready", f"Got: {packet['blockers_or_uncertainty']}"


# ── Review Evidence Packet integration tests ─────────────────────────────────

REPO_OWNER = "Slideshow11"
REPO_NAME = "Automated-Edge-Discovery"
HEAD = "abc123" + "0" * 34  # 40-char


class TestReviewEvidenceIntegration:
    """Tests 12-14: merge-ready notification with review evidence."""

    def _make_review_evidence(self, stale: bool = False, merge_allowed: bool = True, **overrides) -> dict:
        """Build a REVIEW_EVIDENCE_PACKET dict for testing.

        When stale=True: current_head_sha=HEAD, reviewed_head_sha=old_head (stale by SHA mismatch).
        This tests stale detection without triggering the PATCH-3 head-mismatch block.
        """
        old_head = "deadbeef" + "1" * 32
        current = HEAD
        reviewed = old_head if stale else HEAD
        status = "clean" if merge_allowed else "pending"
        base = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "schema_version": 1,
            "generated_at": "2026-05-13T00:00:00+00:00",
            "repo_owner": REPO_OWNER,
            "repo_name": REPO_NAME,
            "pr_number": 207,
            "current_head_sha": current,
            "reviewed_head_sha": reviewed,
            "review_source": "github_codex",
            "review_status": status,
            "review_is_stale": stale,
            "ci_status": "green",
            "ci_required_jobs": ["test", "validator", "governance-validators", "pr-gate-live-smoke"],
            "ci_all_green": True,
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "scope_status": "clean",
            "mergeable": True,
            "merge_allowed": merge_allowed,
            "blockers_or_uncertainty": [] if merge_allowed else ["review is pending"],
            "recommended_merge_command": f"gh pr merge 207 --repo {REPO_OWNER}/{REPO_NAME} --squash --delete-branch --match-head-commit {current}",
        }
        for k, v in overrides.items():
            base[k] = v
        return base

    def _run_with_review_evidence(self, tmp_path, review_evidence: dict, extra_cli: list[str] | None = None) -> subprocess.CompletedProcess:
        """Run pr_gate_merge_ready_notify.py with --review-evidence."""
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(review_evidence))

        cli = [
            sys.executable, str(SCRIPT),
            "--pr-number", "207",
            "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
            "--head-sha", HEAD,
            "--base-branch", "main",
            "--ci-status", "green",
            "--codex-status", "clean",
            "--fallback-review-status", "clean",
            "--reviewer-status", "approved",
            "--scope-status", "clean",
            "--mergeable",
            "--changed-file", "docs/README.md",
            "--output-json", str(out_json),
            "--output-md", str(out_md),
            "--review-evidence", str(rev_path),
        ]
        if extra_cli:
            cli.extend(extra_cli)
        return subprocess.run(cli, capture_output=True, text=True)

    def test_review_evidence_included_in_notification_json(self, tmp_path):
        """Test 12: merge-ready notification includes review_source and reviewed_head_sha."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_evidence_summary" in packet, f"Missing review_evidence_summary: {packet.keys()}"
        summary = packet["review_evidence_summary"]
        assert summary["review_source"] == "github_codex"
        assert summary["reviewed_head_sha"] == HEAD
        assert summary["current_head_sha"] == HEAD
        assert summary["review_is_stale"] is False
        assert summary["ci_all_green"] is True
        assert summary["scope_status"] == "clean"

    def test_stale_review_evidence_produces_not_merge_ready(self, tmp_path):
        """Test 13: merge-ready notification refuses authorization phrase when evidence stale."""
        rev = self._make_review_evidence(stale=True, merge_allowed=False)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["recommendation"] == "not_merge_ready"
        assert packet["required_authorization_phrase"] is None
        assert packet["merge_command_template"] is None
        assert any("stale" in b.lower() for b in packet["blockers_or_uncertainty"]), \
            f"Expected stale blocker: {packet['blockers_or_uncertainty']}"

    def test_merge_allowed_false_produces_not_merge_ready(self, tmp_path):
        """Test 13b: merge_allowed=False produces not_merge_ready regardless of other fields."""
        rev = self._make_review_evidence(stale=False, merge_allowed=False)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["recommendation"] == "not_merge_ready"
        assert packet["required_authorization_phrase"] is None

    def test_review_evidence_included_in_notification_md(self, tmp_path):
        """Test 12b: merge-ready notification markdown includes review evidence summary."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        md_text = (tmp_path / "notification.md").read_text()
        assert "review_source" in md_text.lower() or "Review Evidence Summary" in md_text

    def test_review_evidence_passed_through_packet_mode(self, tmp_path):
        """Test: --review-evidence works in packet mode (--merge-ready-packet + --controller-run-packet)."""
        mrp = tmp_path / "MERGE_READY_PACKET.json"
        crp = tmp_path / "CONTROLLER_RUN_PACKET.json"
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev))

        mrp.write_text(json.dumps({
            "pr": {
                "number": 207, "url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
                "head_sha": HEAD, "base_branch": "main",
            },
            "ci_status": "green", "scope_status": "clean",
            "mergeable": True,
            "changed_files": ["docs/README.md"],
        }))
        crp.write_text(json.dumps({
            "result": {
                "ci_status": "green", "codex_status": "clean",
                "reviewer_status": "approved",
            }
        }))
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--merge-ready-packet", str(mrp),
             "--controller-run-packet", str(crp),
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads(out_json.read_text())
        assert "review_evidence_summary" in packet
        assert packet["review_evidence_summary"]["review_source"] == "github_codex"

    def test_wrong_review_evidence_packet_kind_fails(self, tmp_path):
        """Test: wrong packet_kind in --review-evidence raises error."""
        rev = dict(self._make_review_evidence())
        rev["packet_kind"] = "aed.wrong.v1"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev))

        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", HEAD,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "aed.wrong.v1" in result.stderr or "packet_kind" in result.stderr.lower()

    def test_ci_all_green_field_reflects_ci_status(self, tmp_path):
        """Test: ci_all_green in review evidence summary reflects ci_status."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True, ci_status="green", ci_all_green=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["review_evidence_summary"]["ci_all_green"] is True

        rev_red = self._make_review_evidence(stale=False, merge_allowed=False, ci_status="red", ci_all_green=False)
        result2 = self._run_with_review_evidence(tmp_path, rev_red)
        assert result2.returncode == 0
        packet2 = json.loads((tmp_path / "notification.json").read_text())
        assert packet2["review_evidence_summary"]["ci_all_green"] is False

    def test_scope_status_reflects_file_scope(self, tmp_path):
        """Test: scope_status in review evidence summary reflects changed/allowed files."""
        rev = self._make_review_evidence(
            stale=False, merge_allowed=True,
            changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"],
            scope_status="clean",
        )
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["review_evidence_summary"]["scope_status"] == "clean"

    def test_review_status_included_in_summary(self, tmp_path):
        """Test: review_status field is included in review_evidence_summary."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True, review_status="clean")
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_status" in packet["review_evidence_summary"]
        assert packet["review_evidence_summary"]["review_status"] == "clean"

    def test_no_review_evidence_still_produces_notification(self, tmp_path):
        """Test: running without --review-evidence still works (backward compat)."""
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", HEAD,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--fallback-review-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--changed-file", "docs/README.md",
             "--output-json", str(out_json),
             "--output-md", str(out_md)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_evidence_summary" not in packet
        assert packet["recommendation"] == "merge_ready"


def test_old_packet_codex_not_requested(tmp_path):
    """Old MERGE_READY_PACKET with codex_status=not_requested also works."""
    mrp = tmp_path / "MERGE_READY_PACKET.json"
    crp = tmp_path / "CONTROLLER_RUN_PACKET.json"
    out_json = tmp_path / "notification.json"
    out_md = tmp_path / "notification.md"

    mrp.write_text(json.dumps({
        "pr": {
            "number": 193, "url": VALID_PR["url"],
            "head_sha": "af386e4c75341a2a6e7a6f68b680844de5cef1df",
            "base_branch": "main",
        },
        "ci_status": "green",
        "codex_status": "not_requested",
        "reviewer_status": "approved",
        "mergeable": True,
        "changed_files": ["docs/README.md"],
    }))
    crp.write_text(json.dumps({"result": {
        "ci_status": "green",
        "codex_status": "not_requested",
        "reviewer_status": "approved",
    }}))
    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--merge-ready-packet", str(mrp),
         "--controller-run-packet", str(crp),
         "--output-json", str(out_json), "--output-md", str(out_md)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    packet = json.loads(out_json.read_text())
    assert packet["recommendation"] == "merge_ready", f"Got: {packet['blockers_or_uncertainty']}"


# ── Review Evidence Packet integration tests ─────────────────────────────────

REPO_OWNER = "Slideshow11"
REPO_NAME = "Automated-Edge-Discovery"
HEAD = "abc123" + "0" * 34  # 40-char


class TestReviewEvidenceIntegration:
    """Tests 12-14: merge-ready notification with review evidence."""

    def _make_review_evidence(self, stale: bool = False, merge_allowed: bool = True, **overrides) -> dict:
        """Build a REVIEW_EVIDENCE_PACKET dict for testing.

        When stale=True: current_head_sha=HEAD, reviewed_head_sha=old_head (stale by SHA mismatch).
        This tests stale detection without triggering the PATCH-3 head-mismatch block.
        """
        old_head = "deadbeef" + "1" * 32
        current = HEAD
        reviewed = old_head if stale else HEAD
        status = "clean" if merge_allowed else "pending"
        base = {
            "packet_kind": "aed.pr_gate.review_evidence.v1",
            "schema_version": 1,
            "generated_at": "2026-05-13T00:00:00+00:00",
            "repo_owner": REPO_OWNER,
            "repo_name": REPO_NAME,
            "pr_number": 207,
            "current_head_sha": current,
            "reviewed_head_sha": reviewed,
            "review_source": "github_codex",
            "review_status": status,
            "review_is_stale": stale,
            "ci_status": "green",
            "ci_required_jobs": ["test", "validator", "governance-validators", "pr-gate-live-smoke"],
            "ci_all_green": True,
            "changed_files": ["docs/README.md"],
            "allowed_files": ["docs/README.md"],
            "scope_status": "clean",
            "mergeable": True,
            "merge_allowed": merge_allowed,
            "blockers_or_uncertainty": [] if merge_allowed else ["review is pending"],
            "recommended_merge_command": f"gh pr merge 207 --repo {REPO_OWNER}/{REPO_NAME} --squash --delete-branch --match-head-commit {current}",
        }
        for k, v in overrides.items():
            base[k] = v
        return base

    def _run_with_review_evidence(self, tmp_path, review_evidence: dict, extra_cli: list[str] | None = None) -> subprocess.CompletedProcess:
        """Run pr_gate_merge_ready_notify.py with --review-evidence."""
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(review_evidence))

        cli = [
            sys.executable, str(SCRIPT),
            "--pr-number", "207",
            "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
            "--head-sha", HEAD,
            "--base-branch", "main",
            "--ci-status", "green",
            "--codex-status", "clean",
            "--fallback-review-status", "clean",
            "--reviewer-status", "approved",
            "--scope-status", "clean",
            "--mergeable",
            "--changed-file", "docs/README.md",
            "--output-json", str(out_json),
            "--output-md", str(out_md),
            "--review-evidence", str(rev_path),
        ]
        if extra_cli:
            cli.extend(extra_cli)
        return subprocess.run(cli, capture_output=True, text=True)

    def test_review_evidence_included_in_notification_json(self, tmp_path):
        """Test 12: merge-ready notification includes review_source and reviewed_head_sha."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_evidence_summary" in packet, f"Missing review_evidence_summary: {packet.keys()}"
        summary = packet["review_evidence_summary"]
        assert summary["review_source"] == "github_codex"
        assert summary["reviewed_head_sha"] == HEAD
        assert summary["current_head_sha"] == HEAD
        assert summary["review_is_stale"] is False
        assert summary["ci_all_green"] is True
        assert summary["scope_status"] == "clean"

    def test_stale_review_evidence_produces_not_merge_ready(self, tmp_path):
        """Test 13: merge-ready notification refuses authorization phrase when evidence stale."""
        rev = self._make_review_evidence(stale=True, merge_allowed=False)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["recommendation"] == "not_merge_ready"
        assert packet["required_authorization_phrase"] is None
        assert packet["merge_command_template"] is None
        assert any("stale" in b.lower() for b in packet["blockers_or_uncertainty"]), \
            f"Expected stale blocker: {packet['blockers_or_uncertainty']}"

    def test_merge_allowed_false_produces_not_merge_ready(self, tmp_path):
        """Test 13b: merge_allowed=False produces not_merge_ready regardless of other fields."""
        rev = self._make_review_evidence(stale=False, merge_allowed=False)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["recommendation"] == "not_merge_ready"
        assert packet["required_authorization_phrase"] is None

    def test_review_evidence_included_in_notification_md(self, tmp_path):
        """Test 12b: merge-ready notification markdown includes review evidence summary."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        md_text = (tmp_path / "notification.md").read_text()
        assert "review_source" in md_text.lower() or "Review Evidence Summary" in md_text

    def test_review_evidence_passed_through_packet_mode(self, tmp_path):
        """Test: --review-evidence works in packet mode (--merge-ready-packet + --controller-run-packet)."""
        mrp = tmp_path / "MERGE_READY_PACKET.json"
        crp = tmp_path / "CONTROLLER_RUN_PACKET.json"
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        rev = self._make_review_evidence(stale=False, merge_allowed=True)
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev))

        mrp.write_text(json.dumps({
            "pr": {
                "number": 207, "url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
                "head_sha": HEAD, "base_branch": "main",
            },
            "ci_status": "green", "scope_status": "clean",
            "mergeable": True,
            "changed_files": ["docs/README.md"],
        }))
        crp.write_text(json.dumps({
            "result": {
                "ci_status": "green", "codex_status": "clean",
                "reviewer_status": "approved",
            }
        }))
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--merge-ready-packet", str(mrp),
             "--controller-run-packet", str(crp),
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        packet = json.loads(out_json.read_text())
        assert "review_evidence_summary" in packet
        assert packet["review_evidence_summary"]["review_source"] == "github_codex"

    def test_wrong_review_evidence_packet_kind_fails(self, tmp_path):
        """Test: wrong packet_kind in --review-evidence raises error."""
        rev = dict(self._make_review_evidence())
        rev["packet_kind"] = "aed.wrong.v1"
        rev_path = tmp_path / "REVIEW_EVIDENCE.json"
        rev_path.write_text(json.dumps(rev))

        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", HEAD,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--output-json", str(out_json),
             "--output-md", str(out_md),
             "--review-evidence", str(rev_path)],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "aed.wrong.v1" in result.stderr or "packet_kind" in result.stderr.lower()

    def test_ci_all_green_field_reflects_ci_status(self, tmp_path):
        """Test: ci_all_green in review evidence summary reflects ci_status."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True, ci_status="green", ci_all_green=True)
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["review_evidence_summary"]["ci_all_green"] is True

        rev_red = self._make_review_evidence(stale=False, merge_allowed=False, ci_status="red", ci_all_green=False)
        result2 = self._run_with_review_evidence(tmp_path, rev_red)
        assert result2.returncode == 0
        packet2 = json.loads((tmp_path / "notification.json").read_text())
        assert packet2["review_evidence_summary"]["ci_all_green"] is False

    def test_scope_status_reflects_file_scope(self, tmp_path):
        """Test: scope_status in review evidence summary reflects changed/allowed files."""
        rev = self._make_review_evidence(
            stale=False, merge_allowed=True,
            changed_files=["docs/README.md"],
            allowed_files=["docs/README.md"],
            scope_status="clean",
        )
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert packet["review_evidence_summary"]["scope_status"] == "clean"

    def test_review_status_included_in_summary(self, tmp_path):
        """Test: review_status field is included in review_evidence_summary."""
        rev = self._make_review_evidence(stale=False, merge_allowed=True, review_status="clean")
        result = self._run_with_review_evidence(tmp_path, rev)
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_status" in packet["review_evidence_summary"]
        assert packet["review_evidence_summary"]["review_status"] == "clean"

    def test_no_review_evidence_still_produces_notification(self, tmp_path):
        """Test: running without --review-evidence still works (backward compat)."""
        out_json = tmp_path / "notification.json"
        out_md = tmp_path / "notification.md"
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--pr-number", "207",
             "--pr-url", f"https://github.com/{REPO_OWNER}/{REPO_NAME}/pull/207",
             "--head-sha", HEAD,
             "--base-branch", "main",
             "--ci-status", "green",
             "--codex-status", "clean",
             "--fallback-review-status", "clean",
             "--reviewer-status", "approved",
             "--scope-status", "clean",
             "--mergeable",
             "--changed-file", "docs/README.md",
             "--output-json", str(out_json),
             "--output-md", str(out_md)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        packet = json.loads((tmp_path / "notification.json").read_text())
        assert "review_evidence_summary" not in packet
        assert packet["recommendation"] == "merge_ready"
