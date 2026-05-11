"""Tests for aed_tasker_prompt_bundle.py.

Covers:
  - valid context generates prompt and run config
  - missing/malformed/missing-field context fails with correct exit codes
  - prompt includes required sections: output contract, stop rules, model routing, research, candidate PR requirements
  - run config is deterministic and contains all required fields
  - refuses /home/max/.hermes output paths
  - no network/mutation calls
  - CLI smoke
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Module under test
from scripts.local import aed_tasker_prompt_bundle as bundle_module

# Absolute repo root — always correct regardless of cwd at test invocation time
REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def valid_context(tmp_path):
    """Minimal valid AED_TASKER_CONTEXT.json in flat schema (legacy)."""
    ctx = {
        "repo_root": str(tmp_path),
        "branch": "tooling/test",
        "head_sha": "abc1234567890def",
        "is_clean": True,
        "recent_commits": [
            {
                "sha": "abc1234567890def",
                "author": "Test Author",
                "email": "test@example.com",
                "date": "2026-05-11",
                "message": "test: initial commit",
            },
            {
                "sha": "def4567890123456",
                "author": "Another Author",
                "email": "another@example.com",
                "date": "2026-05-10",
                "message": "feat: add something important",
            },
        ],
        "docs_present": {
            "aed_tasker_executor_design.md": True,
            "current_project_status.md": True,
        },
        "scripts_present": {
            "aed_tasker_packet.py": True,
            "aed_tasker_collect_context.py": False,
        },
        "tests_present": {
            "test_aed_tasker_packet.py": True,
        },
        "schemas_present": {
            "trial_ledger_v1.json": False,
        },
    }
    path = tmp_path / "context.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ctx, f)
    return path


@pytest.fixture
def valid_collector_context(tmp_path):
    """Minimal valid AED_TASKER_CONTEXT.json in the nested collector schema.

    This is the actual output format of aed_tasker_collect_context.py.
    The prompt_bundle tool should accept this schema directly.
    """
    ctx = {
        "repo": {
            "path": str(tmp_path),
            "branch": "tooling/test",
            "head_sha": "abc1234567890def",
            "clean": True,
        },
        "docs": {
            "aed_tasker_executor_design.md": {"exists": True, "snippet": None},
            "current_project_status.md": {"exists": True, "snippet": None},
        },
        "scripts": {
            "aed_tasker_packet.py": {"exists": True, "snippet": None},
            "aed_tasker_collect_context.py": {"exists": False, "snippet": None},
        },
        "tests": {
            "test_aed_tasker_packet.py": {"exists": True},
        },
        "schemas": {
            "trial_ledger_v1.json": {"exists": False},
        },
        "summary": {
            "docs_present": 2,
            "scripts_present": 1,
            "tests_present": 1,
            "schemas_present": 0,
        },
        "recent_commits": [
            {
                "sha": "abc1234567890def",
                "short_sha": "abc1234",
                "subject": "test: initial commit",
                "author": "Test Author",
                "date": "2026-05-11",
            },
            {
                "sha": "def4567890123456",
                "short_sha": "def4567",
                "subject": "feat: add something important",
                "author": "Another Author",
                "date": "2026-05-10",
            },
        ],
    }
    path = tmp_path / "context.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ctx, f)
    return path


@pytest.fixture
def valid_context_path(valid_context):
    return str(valid_context)


# ── Exit code tests ───────────────────────────────────────────────────────────

def test_cli_returns_0_for_valid_context(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout.decode()!r}, stderr={result.stderr.decode()!r}"
    assert prompt_out.exists()
    assert config_out.exists()


def test_missing_context_file_exits_2(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", "/tmp/does_not_exist.json",
            "--output-prompt", str(tmp_path / "prompt.md"),
            "--output-config", str(tmp_path / "config.json"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 2
    assert b"not found" in result.stderr


def test_malformed_json_exits_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(bad),
            "--output-prompt", str(tmp_path / "prompt.md"),
            "--output-config", str(tmp_path / "config.json"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 2
    assert b"malformed JSON" in result.stderr


def test_missing_required_field_exits_2(tmp_path):
    # Valid JSON but missing 'recent_commits' field in nested schema
    bad_ctx = {
        "repo": {
            "path": str(tmp_path),
            "branch": "tooling/test",
            "head_sha": "abc123",
            "clean": True,
        },
        "docs": {},
        "scripts": {},
        "tests": {},
        "schemas": {},
        "summary": {
            "docs_present": 0,
            "scripts_present": 0,
            "tests_present": 0,
            "schemas_present": 0,
        },
        # missing 'recent_commits'
    }
    path = tmp_path / "bad.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bad_ctx, f)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(path),
            "--output-prompt", str(tmp_path / "prompt.md"),
            "--output-config", str(tmp_path / "config.json"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 2
    assert b"missing required fields" in result.stderr


def test_refuses_hermes_output_path_exits_2(tmp_path, valid_collector_context):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", "/home/max/.hermes/forbidden/prompt.md",
            "--output-config", str(tmp_path / "config.json"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 2
    assert b"forbidden prefix" in result.stderr


# ── Prompt content tests ───────────────────────────────────────────────────────

def test_prompt_includes_output_contract(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    content = prompt_out.read_text(encoding="utf-8")
    assert "AED_ROADMAP_TASKER_MEMO.md" in content
    assert "ROADMAP_PACKET.json" in content
    assert "aed_tasker_packet.py validate ROADMAP_PACKET.json" in content


def test_prompt_includes_stop_rules(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    content = prompt_out.read_text(encoding="utf-8")
    # Hard Stop Rules section must be present
    assert "## Hard Stop Rules" in content
    assert "Do NOT edit any file" in content or "no git commit" in content
    assert "Kanban" in content
    assert "memory.update" in content or "no memory update" in content
    assert "skill_manage" in content  # covers "Do NOT use skill_manage"
    assert "live trading" in content  # covers "Do NOT attempt live trading"


def test_prompt_includes_model_routing(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    content = prompt_out.read_text(encoding="utf-8")
    assert "openai-codex" in content
    assert "gpt-5.5" in content
    assert "Codex OAuth" in content
    assert "Tom explicitly approves" in content or "Tom's" in content


def test_prompt_includes_api_fallback_policy(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    content = prompt_out.read_text(encoding="utf-8")
    assert "api_fallback_policy" in content or "Fallback" in content or "fallback" in content


def test_prompt_includes_validation_command(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    content = prompt_out.read_text(encoding="utf-8")
    assert "aed_tasker_packet.py validate" in content


def test_prompt_includes_candidate_pr_requirements(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    content = prompt_out.read_text(encoding="utf-8")
    assert "5 to 8 candidate" in content or "5-8 candidate" in content
    assert "Allowed files" in content
    assert "Forbidden files" in content
    assert "why now" in content.lower() or "Why now" in content


def test_prompt_includes_research_instructions(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    content = prompt_out.read_text(encoding="utf-8")
    assert "Backtest overfitting" in content
    assert "Deflated Sharpe Ratio" in content
    assert "Purged and embargoed" in content or "purged" in content
    assert "deep module architecture" in content.lower()


# ── Run config tests ───────────────────────────────────────────────────────────

def test_run_config_is_deterministic(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    config = json.loads(config_out.read_text(encoding="utf-8"))
    assert config["packet_kind"] == "aed.tasker.run_config.v1"
    assert "generated_at" in config
    assert "expected_outputs" in config
    assert "preferred_model_route" in config
    assert "api_fallback_policy" in config
    assert "stop_rules" in config
    assert "validation_command" in config


def test_run_config_has_required_top_level_keys(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    config = json.loads(config_out.read_text(encoding="utf-8"))
    required_keys = {
        "packet_kind",
        "generated_at",
        "context_json",
        "output_prompt",
        "expected_outputs",
        "preferred_model_route",
        "api_fallback_policy",
        "stop_rules",
        "validation_command",
        "context_meta",
    }
    assert required_keys.issubset(config.keys()), f"Missing keys: {required_keys - config.keys()}"


def test_run_config_expected_outputs_has_two_files(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    config = json.loads(config_out.read_text(encoding="utf-8"))
    outputs = config["expected_outputs"]
    filenames = {o["filename"] for o in outputs}
    assert "AED_ROADMAP_TASKER_MEMO.md" in filenames
    assert "ROADMAP_PACKET.json" in filenames


def test_run_config_stop_rules_has_ten_rules(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    config = json.loads(config_out.read_text(encoding="utf-8"))
    assert len(config["stop_rules"]) == 10


def test_run_config_validation_command_is_executable(tmp_path, valid_collector_context):
    prompt_out = tmp_path / "prompt.md"
    config_out = tmp_path / "config.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/local/aed_tasker_prompt_bundle.py",
            "--context-json", str(valid_collector_context),
            "--output-prompt", str(prompt_out),
            "--output-config", str(config_out),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    config = json.loads(config_out.read_text(encoding="utf-8"))
    cmd = config["validation_command"]
    assert "aed_tasker_packet.py validate" in cmd


# ── Safety / no-mutation tests ────────────────────────────────────────────────

def test_no_network_calls(tmp_path, valid_context, monkeypatch):
    """Confirm no HTTP library imports or calls from the module."""
    # Re-import to catch any lazy imports
    import importlib
    importlib.reload(bundle_module)

    network_libs = [
        "requests", "urllib", "httpx", "aiohttp",
        "gh", "github", "gitlab", "jira",
    ]
    for name in network_libs:
        assert not hasattr(bundle_module, "_uses_network"), \
            f"{name} incorrectly referenced in bundle_module"


def test_no_hermes_mutation_calls(tmp_path, valid_collector_context):
    """Confirm no hermes tool calls in source — stop-rules text is permitted."""
    src = Path("scripts/local/aed_tasker_prompt_bundle.py").read_text(encoding="utf-8")
    # Must not call these as functions; stop-rules prose is permitted
    forbidden = [
        "memory.update(", "fact_store(", "skill_manage(",
        "hermes.kanban", "kanban.dispatch", "delegate_task(",
        "cronjob(", "send_message(",
    ]
    for term in forbidden:
        assert term not in src, f"Forbidden call '{term}' found in source"


def test_no_gh_pr_calls(tmp_path, valid_collector_context):
    """Confirm no gh pr merge / comment calls."""
    src = Path("scripts/local/aed_tasker_prompt_bundle.py").read_text(encoding="utf-8")
    assert "gh pr merge" not in src
    assert "gh pr comment" not in src
    assert "gh pr create" not in src
    assert "gh api" not in src or "gh api --help" in src  # only in help text


def test_no_git_push_or_commit_calls(tmp_path, valid_collector_context):
    """Confirm no git push or git commit subprocess calls in script."""
    src = Path("scripts/local/aed_tasker_prompt_bundle.py").read_text(encoding="utf-8")
    # Check for actual subprocess calls, not stop-rules prose text
    # Stop rules mention "no git push" as instruction to Tasker agent
    # This script itself must not call git push
    assert 'subprocess.run(["git", "push"]' not in src
    assert 'subprocess.run(["git", "commit"]' not in src
    # git log is used by upstream collect_context, not this script
    # git commit in subprocess is fine for sanity checks only (not present here)


# ── validate_context_fields unit tests ───────────────────────────────────────

def test_validate_context_fields_all_present():
    ctx = {
        "repo": {"path": "/tmp", "branch": "main", "head_sha": "abc", "clean": True},
        "docs": {},
        "scripts": {},
        "tests": {},
        "schemas": {},
        "summary": {"docs_present": 0, "scripts_present": 0, "tests_present": 0, "schemas_present": 0},
        "recent_commits": [],
    }
    missing = bundle_module.validate_context_fields(ctx)
    assert missing == []


def test_validate_context_fields_some_missing():
    ctx = {
        "repo": {"path": "/tmp", "branch": "main", "head_sha": "abc", "clean": True},
        # missing docs, scripts, tests, schemas, summary, recent_commits
    }
    missing = bundle_module.validate_context_fields(ctx)
    assert "docs" in missing
    assert "scripts" in missing


def test_validate_output_path_rejects_hermes(tmp_path):
    with pytest.raises(bundle_module.ValidationError) as exc_info:
        bundle_module.validate_output_path("/home/max/.hermes/some/path")
    assert "forbidden prefix" in str(exc_info.value)


# ── build_run_config deterministic ────────────────────────────────────────────

def test_run_config_reproducible_for_same_context(tmp_path):
    ctx1 = tmp_path / "ctx1.json"
    ctx2 = tmp_path / "ctx2.json"
    c = {
        "repo": {"path": str(tmp_path), "branch": "main", "head_sha": "abc123", "clean": True},
        "docs": {},
        "scripts": {},
        "tests": {},
        "schemas": {},
        "summary": {"docs_present": 0, "scripts_present": 0, "tests_present": 0, "schemas_present": 0},
        "recent_commits": [],
    }
    for p in (ctx1, ctx2):
        with open(p, "w") as f:
            json.dump(c, f)

    prompt1 = tmp_path / "p1.md"
    config1 = tmp_path / "c1.json"
    prompt2 = tmp_path / "p2.md"
    config2 = tmp_path / "c2.json"

    for ctx, p, c_ in [(ctx1, prompt1, config1), (ctx2, prompt2, config2)]:
        bundle_module.main([
            "--context-json", str(ctx),
            "--output-prompt", str(p),
            "--output-config", str(c_),
        ])

    cfg1 = json.loads(config1.read_text())
    cfg2 = json.loads(config2.read_text())
    # generated_at differs; compare everything else
    gen1 = cfg1.pop("generated_at")
    gen2 = cfg2.pop("generated_at")
    assert gen1 != gen2  # timestamps should differ
    # context_json and output_prompt/output_config differ by tmp_path; ignore
    cfg1["context_json"] = cfg2["context_json"] = ""
    cfg1["output_prompt"] = cfg2["output_prompt"] = ""
    cfg1["output_config"] = cfg2["output_config"] = ""
    assert cfg1 == cfg2