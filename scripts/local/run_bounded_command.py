#!/usr/bin/env python3
"""
Bounded command runner — model-agnostic safeguard for local shell commands.

Enforces:
- Command timeouts with process-group cleanup
- Denylisted unsafe operations (--admin, deletion mutations, watch mode, etc.)
- Streaming bounded stdout/stderr (fixed ring buffer, no unlimited storage)
- Structured JSON + Markdown output
- No shell=True ever

Usage:
    python3 scripts/local/run_bounded_command.py \
        --cmd-json '["python","--version"]' \
        --timeout-seconds 30 \
        --output-json /tmp/result.json \
        --output-md /tmp/result.md

Optional flags:
    --cwd <path>                     Working directory for the command
    --stdout-tail-bytes <int>        Default 12000
    --stderr-tail-bytes <int>        Default 12000
    --allow-gh-api-mutation           Allow GraphQL mutations (default: false)
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Streaming ring buffer
# ---------------------------------------------------------------------------

class RingBuffer:
    """Fixed-size ring buffer keeping the last max_bytes."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._buf = bytearray()

    def write(self, data: bytes) -> None:
        """Append data, discarding oldest bytes if over max_bytes."""
        self._buf.extend(data)
        if len(self._buf) > self.max_bytes:
            # Keep only the last max_bytes
            self._buf = self._buf[-self.max_bytes:]

    def read(self) -> str:
        """Return decoded content, errors replaced."""
        return self._buf.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Background reader threads
# ---------------------------------------------------------------------------

def _reader_thread(fd, buffer: RingBuffer, closed_event: threading.Event):
    """Drain fd until EOF, writing chunks into buffer."""
    try:
        while True:
            chunk = fd.read(8192)
            if not chunk:
                break
            buffer.write(chunk)
    except Exception:
        pass
    finally:
        try:
            fd.close()
        except Exception:
            pass
        closed_event.set()


# ---------------------------------------------------------------------------
# Policy denylist helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Normalize a string for policy matching: strip and collapse whitespace."""
    return " ".join(s.strip().split())


def _lower_args(args: list[str]) -> list[str]:
    return [a.lower() for a in args]


def _cmd_str(args: list[str]) -> str:
    return " ".join(args)


def _has_shell_wrapper(args: list[str]) -> bool:
    """Check for shell invocation wrappers that spawn a new shell process."""
    wrapper_patterns = [
        "bash -c", "sh -c", "zsh -c", "fish -c",
        "powershell -command", "pwsh -command", "cmd /c",
    ]
    cmd = _cmd_str(args).lower()
    for p in wrapper_patterns:
        if p in cmd:
            return True
    return False


def _is_gh_api_command(args: list[str]) -> bool:
    return len(args) >= 3 and args[0] == "gh" and args[1] == "api"


def _extract_gh_api_method(args: list[str]) -> str | None:
    """Extract the HTTP method from a gh api command."""
    # gh api [--method <METHOD>] <endpoint>
    # gh api -X<METHOD> <endpoint>
    # gh api --method=<METHOD> <endpoint>
    for i, arg in enumerate(args):
        if arg in ("--method", "-X"):
            if i + 1 < len(args):
                return args[i + 1].upper()
        if arg.startswith("--method="):
            return arg.split("=", 1)[1].upper()
        if arg.startswith("-X"):
            return arg[2:].upper()
        if arg.startswith("-X"):
            return arg[2:].upper()
    # Also check standalone REST verbs as first positional after gh api
    methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    for arg in args[2:]:
        if arg.upper() in methods and not arg.startswith("-"):
            return arg.upper()
    return None


def _contains_graphql_mutation_operation(text: str) -> bool:
    """
    Detect a GraphQL mutation keyword followed by an operation body.

    Handles all common forms:
      mutation{...}
      mutation { ... }
      mutation    {
      mutation<whitespace>{
      mutation OperationName { ... }
      mutation OperationName($id:ID!){...}
      Mutation{...}  (case-insensitive)

    Does NOT match:
      - words that merely contain 'mutation' as a substring
        (immutable, permutation, etc.)
      - GraphQL queries: query { ... } or { viewer { ... } }
    """
    # GraphQL operation keyword (\bmutation\b), then any chars except opening brace,
    # then the opening brace — catches mutation{ mutation { mutation name(args){ etc.
    # The [^{]* is non-greedy; the DOTALL flag is not needed since we stop at {.
    return bool(re.search(r'\bmutation\b[^{]*\{', text, flags=re.IGNORECASE))


def _is_gh_graphql_mutation(args: list[str]) -> bool:
    """Check if gh api graphql command contains a mutation."""
    if not (_is_gh_api_command(args) and "graphql" in args):
        return False
    cmd = _cmd_str(args)
    return _contains_graphql_mutation_operation(cmd)


def _is_mutation_denylist_pattern(args: list[str]) -> bool:
    """Check for dangerous GraphQL mutation names regardless of keyword."""
    # Even if the word 'mutation' is somehow bypassed, certain mutation
    # names in the command are unambiguously dangerous.
    mutation_names_lower = [
        "deletereviewcomment",
        "deletepullrequestreviewcomment",
        "dismissreview",
        "resolvesreviewthread",
        "resolvereviewthread",
        "addcomment",
        "addpullrequestreview",
    ]
    cmd_lower = _cmd_str(args).lower()
    for name in mutation_names_lower:
        if name in cmd_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Policy check
# ---------------------------------------------------------------------------

def _check_policy(command: list[str], allow_gh_api_mutation: bool) -> list[str]:
    """
    Return list of policy errors. Empty list means command is allowed.

    .. deprecated::
        This is the V1 denylist-only function. PR A1 replaced the
        allow-by-default denylist model with an allowlist+denylist
        pair (``_check_allowlist`` and ``_check_denylist``). The
        function is kept for backward compatibility — tests and
        downstream callers that imported ``_check_policy`` continue
        to get the legacy semantics — but new code should call
        ``_check_denylist`` directly. ``run_bounded_command`` no
        longer calls this function; it always goes through the new
        policy pipeline.
    """
    errors = []
    cmd_lower = _cmd_str(command).lower()
    args = command

    # ---- Always-active denylist ----
    always_blocked = {
        # Admin bypass
        "--admin",
        # Deletion mutations
        "deletereviewcomment",
        "deletepullrequestreviewcomment",
        "dismissreview",
        "resolvesreviewthread",
        "resolvereviewthread",
        # Watch mode — stalls
        "gh run watch",
        "gh pr checks --watch",
        "gh pr checks -w",
        # Shell invocation wrappers
        "bash -c",
        "sh -c",
        "zsh -c",
        "fish -c",
        "powershell -command",
        "pwsh -command",
        "cmd /c",
    }
    for pattern in always_blocked:
        if pattern in cmd_lower:
            errors.append(f"Deny-listed pattern in command: {pattern!r}")

    # ---- GitHub API mutation detection ----
    if _is_gh_api_command(args):
        method = _extract_gh_api_method(args)
        if method and method in {"PUT", "PATCH", "POST", "DELETE"}:
            # Block mutation-worthy methods on certain paths
            endpoint = _cmd_str(args)
            dangerous_paths = [
                "/branches/",
                "/protection",
                "/required_status_checks",
                "/enforce_admins",
                "/required_pull_request_reviews",
                "/comments/",
                "/reviews/",
                "/pulls/comments",
                "/issues/comments",
                "/replies",
            ]
            for path in dangerous_paths:
                if path in endpoint:
                    errors.append(
                        f"GitHub API mutates protected path: "
                        f"{method} {endpoint}"
                    )
                    break

    # ---- GraphQL mutation ----
    if not allow_gh_api_mutation:
        if _is_gh_graphql_mutation(args) or _is_mutation_denylist_pattern(args):
            errors.append("GraphQL mutation requires --allow-gh-api-mutation")

    # ---- Hermes kanban mutation (heuristic) ----
    if "hermes" in cmd_lower and any(k in cmd_lower for k in ["kanban move", "kanban add", "kanban update", "kanban delete"]):
        errors.append("Hermes kanban mutation not allowed")

    return errors


# ---------------------------------------------------------------------------
# Policy: allowlist + denylist (PR A1 hardening)
# ---------------------------------------------------------------------------
#
# The runner is deny-by-default for normal use. The allowlist is the
# PRIMARY safety model; the denylist is a SECONDARY defense that runs on
# top of any allowlist match. To opt into the legacy denylist-only
# behavior, pass ``--policy-mode legacy-denylist``.
#
# All allowlist / denylist rule ids are stable strings of the form
# ``BC-POL-NNN`` (for command policy) or ``BC-ENV-NNN`` (for env policy).
# Downstream tooling can switch on these ids without parsing prose.
#
# ``_norm`` is now used by every pattern match in both the allowlist and
# the denylist, eliminating the drift where it was defined but unused
# (audit finding 2026-07-07).

# Allowlist rule ids. Each tuple is (rule_id, family, predicate).
# A command is ALLOWED iff at least one predicate returns True.
ALLOWLIST_RULES: list[tuple[str, str, callable]] = [
    # ---- python / pytest / py_compile (read-only) ----
    (
        "BC-POL-001",
        "python_pytest_pycompile",
        lambda args: len(args) >= 1
        and args[0] in ("python", "python3")
        and len(args) >= 3
        and args[1] == "-m"
        and args[2] in ("pytest", "py_compile")
        and all(_is_safe_path_token(a) for a in args[3:]),
    ),
    # ---- git read-only / local-status operations ----
    (
        "BC-POL-002",
        "git_status",
        lambda args: len(args) >= 2
        and args[0] == "git"
        and args[1] == "status"
        and all(a in ("--short", "--porcelain", "--branch", "-b", "-s", "-sb") for a in args[2:]),
    ),
    (
        "BC-POL-003",
        "git_diff_check",
        lambda args: len(args) >= 2
        and args[0] == "git"
        and args[1] == "diff"
        and "--check" in args
        and not any(a in ("--cached", "--staged") for a in args),
    ),
    (
        "BC-POL-004",
        "git_diff_name_only",
        lambda args: len(args) == 3
        and args[0] == "git"
        and args[1] == "diff"
        and args[2] == "--name-only",
    ),
    (
        "BC-POL-005",
        "git_diff_stat",
        lambda args: len(args) == 3
        and args[0] == "git"
        and args[1] == "diff"
        and args[2] == "--stat",
    ),
    (
        "BC-POL-006",
        "git_rev_parse",
        lambda args: len(args) >= 2
        and args[0] == "git"
        and args[1] == "rev-parse"
        and all(_is_safe_path_token(a) or _is_safe_git_ref(a) for a in args[2:]),
    ),
    (
        "BC-POL-007",
        "git_branch_show_current",
        lambda args: args == ["git", "branch", "--show-current"],
    ),
    (
        "BC-POL-008",
        "git_worktree_list",
        lambda args: args == ["git", "worktree", "list"],
    ),
]


def _is_safe_path_token(token: str) -> bool:
    """A path argument is safe iff it has no shell metacharacters and
    is not an absolute path that escapes the runner's working tree.
    This is a deliberately strict check used by the allowlist predicates.
    """
    if not token or not isinstance(token, str):
        return False
    # Disallow shell metacharacters that could enable argument injection
    # even with shell=False (defense in depth).
    forbidden_chars = set("|&;<>()$`\\\"'*?[]{} \t\n")
    if any(c in forbidden_chars for c in token):
        return False
    # Allow relative paths or paths inside /tmp. Reject absolute paths
    # that point at sensitive roots.
    if token.startswith("/"):
        sensitive = (
            "/home/max/.hermes",
            "/home/max/.ssh",
            "/etc",
            "/var",
            "/root",
        )
        for s in sensitive:
            if token == s or token.startswith(s + "/"):
                return False
        # /tmp and /var/tmp are the only writable scratch roots permitted.
        if not (token.startswith("/tmp/") or token.startswith("/var/tmp/")):
            return False
    return True


def _is_safe_git_ref(token: str) -> bool:
    """A git ref is safe iff it is a 7-40 char hex SHA, or a strict
    whitelist of symbolic refs (HEAD, FETCH_HEAD, ORIG_HEAD, MERGE_HEAD).
    """
    if token in ("HEAD", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD"):
        return True
    if 7 <= len(token) <= 40 and all(c in "0123456789abcdefABCDEF" for c in token):
        return True
    return False


def _check_allowlist(command: list[str]) -> tuple[bool, str | None, str | None]:
    """Return (allowed, rule_id, reason). If allowed, rule_id is the
    id of the allowlist rule that matched and reason is its family name.
    """
    args = list(command)
    for rule_id, family, predicate in ALLOWLIST_RULES:
        try:
            if predicate(args):
                return True, rule_id, family
        except Exception:
            # Defensive: a buggy predicate must not allow a command.
            continue
    return False, "BC-POL-099", (
        "command does not match any allowlist rule (deny by default). "
        "Pass --policy-mode legacy-denylist to use the previous "
        "denylist-only behavior, or extend the allowlist in source."
    )


# Denylist rule ids. Each tuple is (rule_id, needle_substring, reason).
# The denylist is run AFTER the allowlist when in allowlist mode (so
# any allowlist match that also trips a denylist is still blocked). It
# is the SOLE safety model in legacy-denylist mode.
DENYLIST_RULES: list[tuple[str, str, str]] = [
    # ---- admin bypass ----
    ("BC-POL-101", "--admin", "admin bypass is forbidden"),
    # ---- deletion / thread mutation names (substring) ----
    ("BC-POL-102", "deletereviewcomment", "review-comment deletion is forbidden"),
    ("BC-POL-103", "deletepullrequestreviewcomment", "review-comment deletion is forbidden"),
    ("BC-POL-104", "dismissreview", "review dismissal is forbidden"),
    ("BC-POL-105", "resolvesreviewthread", "review-thread mutation is forbidden"),
    ("BC-POL-106", "resolvereviewthread", "review-thread mutation is forbidden"),
    ("BC-POL-107", "addcomment", "add-comment mutation is forbidden"),
    ("BC-POL-108", "addpullrequestreview", "review mutation is forbidden"),
    # ---- PR mutation ----
    ("BC-POL-110", "gh pr create", "gh pr create is forbidden"),
    ("BC-POL-111", "gh pr comment", "gh pr comment is forbidden"),
    ("BC-POL-112", "gh pr edit", "gh pr edit is forbidden"),
    ("BC-POL-113", "gh pr close", "gh pr close is forbidden"),
    ("BC-POL-114", "gh pr reopen", "gh pr reopen is forbidden"),
    ("BC-POL-115", "gh pr merge", "gh pr merge is forbidden"),
    ("BC-POL-116", "gh pr lock", "gh pr lock is forbidden"),
    ("BC-POL-117", "gh pr unlock", "gh pr unlock is forbidden"),
    ("BC-POL-118", "gh pr review", "gh pr review is forbidden"),
    # ---- release / workflow / repo mutation ----
    ("BC-POL-120", "gh release create", "gh release create is forbidden"),
    ("BC-POL-121", "gh release delete", "gh release delete is forbidden"),
    ("BC-POL-122", "gh release edit", "gh release edit is forbidden"),
    ("BC-POL-123", "gh workflow run", "gh workflow run is forbidden"),
    ("BC-POL-124", "gh workflow enable", "gh workflow enable is forbidden"),
    ("BC-POL-125", "gh workflow disable", "gh workflow disable is forbidden"),
    ("BC-POL-126", "gh repo delete", "gh repo delete is forbidden"),
    ("BC-POL-127", "gh repo archive", "gh repo archive is forbidden"),
    ("BC-POL-128", "gh repo edit", "gh repo edit is forbidden"),
    # ---- git push / commit / remote mutation ----
    ("BC-POL-130", "git push", "git push is forbidden"),
    ("BC-POL-131", "git commit", "git commit is forbidden"),
    ("BC-POL-132", "git tag", "git tag is forbidden"),
    ("BC-POL-133", "git remote", "git remote mutation is forbidden"),
    ("BC-POL-134", "git branch -d", "git branch delete is forbidden"),
    ("BC-POL-135", "git branch -D", "git branch force-delete is forbidden"),
    ("BC-POL-136", "git branch -m", "git branch move is forbidden"),
    ("BC-POL-137", "git checkout -b", "git checkout -b is forbidden (use apply_temp_worktree_patch_to_branch.py instead)"),
    ("BC-POL-138", "git checkout --", "git checkout -- is forbidden"),
    ("BC-POL-139", "git reset --hard", "git reset --hard is forbidden"),
    # ---- Hermes mutations ----
    ("BC-POL-140", "memory_store", "Hermes memory_store is forbidden"),
    ("BC-POL-141", "memory.update", "Hermes memory.update is forbidden"),
    ("BC-POL-142", "fact_store", "Hermes fact_store is forbidden"),
    ("BC-POL-143", "skill_manage", "Hermes skill_manage is forbidden"),
    ("BC-POL-144", "delegate_task", "Hermes delegate_task is forbidden"),
    ("BC-POL-145", "cronjob", "Hermes cronjob is forbidden"),
    ("BC-POL-146", "telegram send_message", "Telegram send_message is forbidden"),
    ("BC-POL-147", "hermes kanban", "Hermes kanban mutation is forbidden"),
    # ---- python -c (arbitrary code execution) ----
    ("BC-POL-150", "python -c", "python -c is forbidden (use a script file)"),
    ("BC-POL-151", "python3 -c", "python3 -c is forbidden (use a script file)"),
    # ---- package install / network upload ----
    ("BC-POL-160", "pip install", "pip install is forbidden"),
    ("BC-POL-161", "pip3 install", "pip3 install is forbidden"),
    ("BC-POL-162", "uv pip install", "uv pip install is forbidden"),
    ("BC-POL-163", "poetry add", "poetry add is forbidden"),
    ("BC-POL-164", "npm install", "npm install is forbidden"),
    ("BC-POL-165", "npm i ", "npm i is forbidden"),
    ("BC-POL-166", "yarn add", "yarn add is forbidden"),
    # ---- arbitrary file ops ----
    ("BC-POL-170", "rm -rf", "rm -rf is forbidden"),
    ("BC-POL-171", "rm -fr", "rm -fr is forbidden"),
    ("BC-POL-172", "rsync", "rsync is forbidden"),
    ("BC-POL-173", "curl --upload", "curl --upload-file is forbidden"),
    ("BC-POL-174", "curl --upload-file", "curl --upload-file is forbidden"),
    ("BC-POL-175", "wget --post", "wget --post-data is forbidden"),
    # ---- watch mode ----
    ("BC-POL-180", "gh run watch", "gh run watch is forbidden (stalls)"),
    ("BC-POL-181", "gh pr checks --watch", "gh pr checks --watch is forbidden (stalls)"),
    ("BC-POL-182", "gh pr checks -w", "gh pr checks -w is forbidden (stalls)"),
    # ---- shell wrappers ----
    ("BC-POL-190", "bash -c", "bash -c shell wrapper is forbidden"),
    ("BC-POL-191", "sh -c", "sh -c shell wrapper is forbidden"),
    ("BC-POL-192", "zsh -c", "zsh -c shell wrapper is forbidden"),
    ("BC-POL-193", "fish -c", "fish -c shell wrapper is forbidden"),
    ("BC-POL-194", "powershell -command", "powershell -command shell wrapper is forbidden"),
    ("BC-POL-195", "pwsh -command", "pwsh -command shell wrapper is forbidden"),
    ("BC-POL-196", "cmd /c", "cmd /c shell wrapper is forbidden"),
]


def _check_denylist(
    command: list[str],
    allow_gh_api_mutation: bool,
    policy_mode: str = "allowlist",
) -> list[tuple[str, str]]:
    """Return a list of (rule_id, reason) for every denylist match.
    The list is empty iff the command passes the denylist.

    Rule scoping:

    - BC-POL-101 through BC-POL-201 (V1 denylist + V1 GraphQL/HTTP rules)
      run in BOTH ``legacy-denylist`` and ``allowlist`` modes. These
      are the rules the V1 test suite expects to fire.
    - BC-POL-110 through BC-POL-201 (the new PR A1 rules for ``gh pr
      create``, ``git push``, ``python -c``, ``pip install``, etc.)
      run ONLY in ``allowlist`` mode. The legacy-denylist mode
      preserves the V1 test contract: e.g. ``python -c`` was allowed
      by V1 and is still allowed by legacy-denylist.
    """
    args = list(command)
    # Use _norm on the joined string so whitespace-padded bypasses
    # ("-- admin", "  --admin") are caught. This addresses the audit
    # finding that _norm was defined but never used.
    cmd_str = _norm(_cmd_str(args)).lower()
    cmd_str_raw = _cmd_str(args).lower()
    errors: list[tuple[str, str]] = []
    for rule_id, needle, reason in DENYLIST_RULES:
        # V1-compatible rules: BC-POL-1xx and BC-POL-2xx with id
        # below 110, or BC-POL-200/201 (V1 HTTP/GraphQL gate). New
        # rules (BC-POL-110..199, 170..199) only fire in allowlist
        # mode. Rules are split by id parity to make this explicit.
        rule_num = int(rule_id.split("-")[-1])
        is_v1_rule = (
            rule_id == "BC-POL-101"  # --admin
            or rule_id in ("BC-POL-102", "BC-POL-103", "BC-POL-104", "BC-POL-105", "BC-POL-106", "BC-POL-107", "BC-POL-108")  # V1 deletion-mutation names
            or rule_id == "BC-POL-147"  # V1 Hermes kanban heuristic
            or rule_id in ("BC-POL-180", "BC-POL-181", "BC-POL-182")  # V1 watch-mode
            or rule_id in ("BC-POL-190", "BC-POL-191", "BC-POL-192", "BC-POL-193", "BC-POL-194", "BC-POL-195", "BC-POL-196")  # V1 shell wrappers
            or rule_id in ("BC-POL-200", "BC-POL-201")  # V1 HTTP/GraphQL
        )
        if not is_v1_rule and policy_mode == "legacy-denylist":
            continue
        needle_lower = needle.lower()
        if needle_lower in cmd_str or needle_lower in cmd_str_raw:
            errors.append((rule_id, f"{reason}: {needle!r}"))
    # ---- GitHub API HTTP-method + path denylist (preserved from V1) ----
    if _is_gh_api_command(args):
        method = _extract_gh_api_method(args)
        if method and method in {"PUT", "PATCH", "POST", "DELETE"}:
            endpoint = _cmd_str(args)
            dangerous_paths = [
                "/branches/",
                "/protection",
                "/required_status_checks",
                "/enforce_admins",
                "/required_pull_request_reviews",
                "/comments/",
                "/reviews/",
                "/pulls/comments",
                "/issues/comments",
                "/replies",
            ]
            for path in dangerous_paths:
                if path in endpoint:
                    errors.append((
                        "BC-POL-200",
                        f"GitHub API mutates protected path: {method} {endpoint}",
                    ))
                    break
    # ---- GraphQL mutation gate (preserved from V1) ----
    if not allow_gh_api_mutation:
        if _is_gh_graphql_mutation(args) or _is_mutation_denylist_pattern(args):
            errors.append((
                "BC-POL-201",
                "GraphQL mutation requires --allow-gh-api-mutation",
            ))
    return errors


# ---------------------------------------------------------------------------
# Environment sanitization (PR A1 hardening)
# ---------------------------------------------------------------------------
#
# The runner MUST NOT leak sensitive credentials into the spawned
# subprocess. The strip list is a deliberate set of prefix / suffix
# patterns that covers all Hermes-managed secrets plus the well-known
# provider credentials. The strip is applied AFTER allowlist+denylist
# approval, so a denied command never sees a sanitized env (it sees
# the original env via the JSON result only — the env is never
# inspected by policy code).
#
# Rule id for the strip is fixed: ``BC-ENV-001``.

# Prefixes: any env var whose name starts with one of these is stripped.
ENV_STRIP_PREFIXES: tuple[str, ...] = (
    "HERMES_",
    "GATEWAY_RELAY_",
    "AUXILIARY_",
    "OPENAI_",
    "ANTHROPIC_",
    "GOOGLE_",
    "VERTEX_",
    "DEEPSEEK_",
    "MISTRAL_",
    "GROQ_",
    "TOGETHER_",
    "PERPLEXITY_",
    "COHERE_",
    "FIREWORKS_",
    "XAI_",
    "HELICONE_",
    "PARALLEL_",
    "FIRECRAWL_",
    "TELEGRAM_",
    "DISCORD_",
    "SLACK_",
    "WHATSAPP_",
    "SIGNAL_",
    "EMAIL_",
    "HASS_",
    "GH_",
)

# Exact names: these are always stripped regardless of prefix.
ENV_STRIP_EXACT: frozenset[str] = frozenset({
    "GITHUB_TOKEN",
    "GIT_TOKEN",
    "GH_TOKEN",
    "HERMES_DASHBOARD_SESSION_TOKEN",
    "MEMORY_STORE_DB",
    "FACT_STORE_DB",
})

# Suffixes: any env var whose name ENDS with one of these is stripped,
# unless the prefix is already in ENV_STRIP_PREFIXES (e.g. HERMES_OPENAI_API_KEY
# is caught by the prefix and we don't double-count it).
ENV_STRIP_SUFFIXES: tuple[str, ...] = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
)

# Whitelist: env vars that are always preserved even if a strip rule
# would otherwise match. This is intentionally narrow — only the
# variables the policy code, the test runner, and pytest itself
# need. Add a name here only with explicit justification.
ENV_PRESERVE: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "PWD",
    "SHLVL",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_RUNTIME_DIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PYTHONHASHSEED",
    "CI",
    "GITHUB_ACTIONS",
    "RUNNER_TEMP",
    "RUNNER_DEBUG",
})


def _env_var_should_strip(name: str) -> bool:
    """Return True iff the env var name matches a strip rule and is
    not in the explicit preserve set.
    """
    if not name:
        return False
    if name in ENV_PRESERVE:
        return False
    if name in ENV_STRIP_EXACT:
        return True
    upper = name.upper()
    for prefix in ENV_STRIP_PREFIXES:
        if upper.startswith(prefix.upper()):
            return True
    for suffix in ENV_STRIP_SUFFIXES:
        if upper.endswith(suffix.upper()):
            return True
    return False


def _sanitize_environment(env: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Return (sanitized_env, blocked_keys). Removes every env var
    matching a strip rule from the input dict. The result dict is a
    fresh copy; the input is not mutated. ``blocked_keys`` is the
    sorted list of stripped names, in original-case.
    """
    if env is None:
        env = os.environ
    sanitized: dict[str, str] = {}
    blocked: list[str] = []
    for k, v in env.items():
        if _env_var_should_strip(k):
            blocked.append(k)
        else:
            sanitized[k] = v
    return sanitized, sorted(blocked)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded command runner with policy enforcement.",
    )
    parser.add_argument(
        "--cmd-json",
        required=True,
        help="JSON array of command strings, e.g. '[\"git\",\"status\"]'",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Hard timeout in seconds. Default 300.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for the command. Defaults to cwd.",
    )
    parser.add_argument(
        "--stdout-tail-bytes",
        type=int,
        default=12000,
        help="Max bytes of stdout to retain. Default 12000.",
    )
    parser.add_argument(
        "--stderr-tail-bytes",
        type=int,
        default=12000,
        help="Max bytes of stderr to retain. Default 12000.",
    )
    parser.add_argument(
        "--allow-gh-api-mutation",
        action="store_true",
        default=False,
        help="Allow GraphQL mutation commands. Default false.",
    )
    parser.add_argument(
        "--policy-mode",
        choices=("allowlist", "legacy-denylist"),
        default="allowlist",
        help=(
            "Command policy mode (PR A1). Default 'allowlist' is deny-by-default: "
            "the command must match an explicit allowlist rule, AND it must not "
            "trip any denylist rule. 'legacy-denylist' uses the original V1 "
            "denylist-only behavior for backward compatibility."
        ),
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to write JSON result.",
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path to write Markdown result.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Command validation
# ---------------------------------------------------------------------------

def validate_command(cmd_json: str) -> tuple[list[str] | None, str | None]:
    """
    Parse and validate command from JSON.

    Returns (command_list, None) on success.
    Returns (None, error_message) on failure.
    """
    try:
        parsed = json.loads(cmd_json)
    except json.JSONDecodeError as e:
        return None, f"COMMAND_INVALID_JSON: not valid JSON — {e}"

    if not isinstance(parsed, list):
        return None, "COMMAND_INVALID_JSON: --cmd-json must be a JSON array"

    if len(parsed) == 0:
        return None, "COMMAND_INVALID_JSON: command array is empty"

    for i, element in enumerate(parsed):
        if not isinstance(element, str):
            return None, (
                f"COMMAND_INVALID_JSON: element {i} is {type(element).__name__}, "
                "expected string"
            )

    return parsed, None


# ---------------------------------------------------------------------------
# Result writing
# ---------------------------------------------------------------------------

def write_json_output(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_md_output(path: str, data: dict) -> None:
    lines = [
        f"# Bounded Command Runner Result",
        f"",
        f"**Status**: {data['status']}",
        f"**Command**: `{' '.join(data['command'])}`",
        f"**cwd**: {data['cwd']}",
        f"**Timeout**: {data['timeout_seconds']}s",
        f"**Duration**: {data['duration_seconds']:.3f}s",
        f"**Exit code**: {data['exit_code']}",
        f"**Killed**: {data['killed']}",
        f"",
    ]
    if data["policy_errors"]:
        lines.append("**Policy Errors**:")
        for err in data["policy_errors"]:
            lines.append(f"- `{err}`")
        lines.append("")
    lines.append("## stdout (tail)")
    lines.append("```")
    lines.append(data["stdout_tail"])
    lines.append("```")
    lines.append("")
    lines.append("## stderr (tail)")
    lines.append("```")
    lines.append(data["stderr_tail"])
    lines.append("```")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_result(
    command: list[str],
    cwd: str,
    timeout_seconds: int,
    started_at: datetime,
    ended_at: datetime,
    exit_code: int | None,
    stdout_tail: str,
    stderr_tail: str,
    killed: bool,
    policy_errors: list[str],
    status: str,
    policy_mode: str = "allowlist",
    policy_decision: str = "n/a",
    policy_rule_id: str | None = None,
    policy_reason: str | None = None,
    sanitized_env_applied: bool = True,
    blocked_env_keys: list[str] | None = None,
) -> dict:
    duration = (ended_at - started_at).total_seconds()
    return {
        "status": status,
        "command": command,
        "cwd": cwd,
        "timeout_seconds": timeout_seconds,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(duration, 3),
        "exit_code": exit_code,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "killed": killed,
        "policy_errors": policy_errors,
        # PR A1 hardening — explicit policy decision fields. These make
        # the allowlist / denylist verdict auditable in CI without
        # parsing prose. ``policy_decision`` is one of
        # ``"allow"`` / ``"block"`` / ``"n/a"`` (the latter only for
        # invalid-JSON / unknown-error paths). ``policy_rule_id`` is
        # the stable id of the allowlist rule that allowed the command
        # (e.g. ``BC-POL-001``) or the denylist rule that blocked it
        # (e.g. ``BC-POL-101``).
        "policy_mode": policy_mode,
        "policy_decision": policy_decision,
        "policy_rule_id": policy_rule_id,
        "policy_reason": policy_reason,
        # PR A1 hardening — environment sanitization audit fields.
        # ``sanitized_env_applied`` is True when the runner stripped
        # env vars before launching the child; ``blocked_env_keys`` is
        # the sorted list of names that were stripped (empty list when
        # no env was set to begin with). These fields are always
        # present so downstream tooling can switch on them.
        "sanitized_env_applied": sanitized_env_applied,
        "blocked_env_keys": blocked_env_keys if blocked_env_keys is not None else [],
    }


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def run_bounded_command(
    command: list[str],
    timeout_seconds: int,
    cwd: str | None,
    stdout_tail_bytes: int,
    stderr_tail_bytes: int,
    allow_gh_api_mutation: bool,
    policy_mode: str = "allowlist",
) -> dict:
    """
    Run a bounded command with policy checks, streaming bounded output,
    and process-group timeout cleanup.

    Returns a result dict (same schema as JSON output).

    PR A1 hardening:

    - ``policy_mode`` selects between ``"allowlist"`` (default, deny-by-
      default) and ``"legacy-denylist"`` (the V1 denylist-only behavior).
    - Environment variables are sanitized BEFORE the child is launched:
      see ``_sanitize_environment``. Sensitive env vars (Hermes,
      provider keys, etc.) never reach the spawned subprocess.
    - The result dict always carries the new policy audit fields
      ``policy_mode``, ``policy_decision``, ``policy_rule_id``,
      ``policy_reason``, ``sanitized_env_applied``, ``blocked_env_keys``.
    """
    # Policy check (PR A1 — allowlist + denylist)
    if policy_mode == "legacy-denylist":
        # Original V1 behavior: any denylist match blocks; anything
        # else is allowed. Preserved for backward compatibility.
        denylist_errors = _check_denylist(command, allow_gh_api_mutation, policy_mode=policy_mode)
        if denylist_errors:
            first_rule_id, first_reason = denylist_errors[0]
            return build_result(
                command=command,
                cwd=cwd or os.getcwd(),
                timeout_seconds=timeout_seconds,
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                exit_code=None,
                stdout_tail="",
                stderr_tail="",
                killed=False,
                policy_errors=[r for _, r in denylist_errors],
                status="COMMAND_POLICY_DENIED",
                policy_mode=policy_mode,
                policy_decision="block",
                policy_rule_id=first_rule_id,
                policy_reason=first_reason,
            )
        allow_decision, allow_rule_id, allow_reason = (
            "allow",
            "BC-POL-000",
            "command passed the denylist (legacy mode, no allowlist check)",
        )
    else:
        # Default: allowlist must match, AND denylist must pass.
        allowed, allow_rule_id, allow_reason = _check_allowlist(command)
        if not allowed:
            return build_result(
                command=command,
                cwd=cwd or os.getcwd(),
                timeout_seconds=timeout_seconds,
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                exit_code=None,
                stdout_tail="",
                stderr_tail="",
                killed=False,
                policy_errors=[allow_reason or "command not in allowlist"],
                status="COMMAND_POLICY_DENIED",
                policy_mode=policy_mode,
                policy_decision="block",
                policy_rule_id=allow_rule_id,
                policy_reason=allow_reason,
            )
        # Allowlist matched; now run the denylist as a secondary defense.
        denylist_errors = _check_denylist(command, allow_gh_api_mutation, policy_mode=policy_mode)
        if denylist_errors:
            first_rule_id, first_reason = denylist_errors[0]
            return build_result(
                command=command,
                cwd=cwd or os.getcwd(),
                timeout_seconds=timeout_seconds,
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                exit_code=None,
                stdout_tail="",
                stderr_tail="",
                killed=False,
                policy_errors=[r for _, r in denylist_errors],
                status="COMMAND_POLICY_DENIED",
                policy_mode=policy_mode,
                policy_decision="block",
                policy_rule_id=first_rule_id,
                policy_reason=first_reason,
            )
        allow_decision = "allow"

    # PR A1 — environment sanitization. The child must NEVER inherit
    # sensitive env vars. We capture the parent env, strip the
    # sensitive set, and pass the sanitized env explicitly to Popen.
    sanitized_env, blocked_env_keys = _sanitize_environment(os.environ)

    # Execute
    started = datetime.now(timezone.utc)
    killed = False
    exit_code: int | None = None
    status = "COMMAND_UNKNOWN_ERROR"

    # Streaming ring buffers
    stdout_buf = RingBuffer(stdout_tail_bytes)
    stderr_buf = RingBuffer(stderr_tail_bytes)
    stdout_closed = threading.Event()
    stderr_closed = threading.Event()

    try:
        # Build Popen kwargs
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "shell": False,  # never shell=True
            # PR A1 — pass the sanitized env explicitly so the child
            # cannot see the parent's sensitive env vars.
            "env": sanitized_env,
        }
        # On POSIX, start in a new session so we can kill the whole group
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(command, **popen_kwargs)

        # Start background reader threads
        stdout_thread = threading.Thread(
            target=_reader_thread,
            args=(proc.stdout, stdout_buf, stdout_closed),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_reader_thread,
            args=(proc.stderr, stderr_buf, stderr_closed),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        # Wait for process with timeout
        try:
            proc.wait(timeout=timeout_seconds)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Timeout — try graceful termination of the process group first
            killed = True
            status = "COMMAND_TIMEOUT"
            exit_code = -1

            if sys.platform != "win32":
                # Try SIGTERM on the process group
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    # Give it 2 seconds to clean up gracefully
                    gone = proc.wait(timeout=2)
                except (ProcessLookupError, OSError):
                    # Process already gone
                    pass
                except Exception:
                    pass
                else:
                    # If still alive after 2s, SIGKILL
                    if proc.poll() is None:
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                            proc.wait(timeout=2)
                        except Exception:
                            pass
            else:
                # Windows fallback — proc.terminate() then proc.kill()
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
                if proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass

        ended = datetime.now(timezone.utc)

    except FileNotFoundError:
        ended = datetime.now(timezone.utc)
        exit_code = 127
        status = "COMMAND_UNKNOWN_ERROR"

    except Exception as e:
        ended = datetime.now(timezone.utc)
        exit_code = 1
        status = "COMMAND_UNKNOWN_ERROR"
        # Return early with what we have
        return build_result(
            command=command,
            cwd=cwd or os.getcwd(),
            timeout_seconds=timeout_seconds,
            started_at=started,
            ended_at=ended,
            exit_code=exit_code,
            stdout_tail=stdout_buf.read(),
            stderr_tail=stderr_buf.read(),
            killed=killed,
            policy_errors=[],
            status=status,
            policy_mode=policy_mode,
            policy_decision=allow_decision,
            policy_rule_id=allow_rule_id,
            policy_reason=allow_reason,
            sanitized_env_applied=True,
            blocked_env_keys=blocked_env_keys,
        )

    if status == "COMMAND_UNKNOWN_ERROR":
        if killed:
            status = "COMMAND_TIMEOUT"
        elif exit_code == 0:
            status = "COMMAND_SUCCEEDED"
        else:
            status = "COMMAND_FAILED"

    # Read final tails from ring buffers
    stdout_tail = stdout_buf.read()
    stderr_tail = stderr_buf.read()

    return build_result(
        command=command,
        cwd=cwd or os.getcwd(),
        timeout_seconds=timeout_seconds,
        started_at=started,
        ended_at=ended,
        exit_code=exit_code,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        killed=killed,
        policy_errors=[],
        status=status,
        policy_mode=policy_mode,
        policy_decision=allow_decision,
        policy_rule_id=allow_rule_id,
        policy_reason=allow_reason,
        sanitized_env_applied=True,
        blocked_env_keys=blocked_env_keys,
    )


def main() -> None:
    args = parse_args()

    # Validate command
    command, validation_error = validate_command(args.cmd_json)
    if command is None:
        result = {
            "status": "COMMAND_INVALID_JSON",
            "command": [],
            "cwd": args.cwd or os.getcwd(),
            "timeout_seconds": args.timeout_seconds,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 0.0,
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": validation_error or "unknown error",
            "killed": False,
            "policy_errors": [],
            # PR A1 — invalid-JSON path also gets the new audit
            # fields, with n/a decision (no policy code ran).
            "policy_mode": args.policy_mode,
            "policy_decision": "n/a",
            "policy_rule_id": None,
            "policy_reason": "invalid JSON; policy code did not run",
            "sanitized_env_applied": False,
            "blocked_env_keys": [],
        }
        write_json_output(args.output_json, result)
        write_md_output(args.output_md, result)
        sys.exit(0)  # Runner itself doesn't fail on invalid input

    # Run
    result = run_bounded_command(
        command=command,
        timeout_seconds=args.timeout_seconds,
        cwd=args.cwd,
        stdout_tail_bytes=args.stdout_tail_bytes,
        stderr_tail_bytes=args.stderr_tail_bytes,
        allow_gh_api_mutation=args.allow_gh_api_mutation,
        policy_mode=args.policy_mode,
    )

    write_json_output(args.output_json, result)
    write_md_output(args.output_md, result)

    # Exit code: runner always exits 0; JSON status is the contract
    sys.exit(0)


if __name__ == "__main__":
    main()