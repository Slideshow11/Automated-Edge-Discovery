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
