#!/usr/bin/env python3
"""Read-only one-shot PR gate watchdog runner.

Wrapper around watch_pr_gate_state.py that accepts a config file and/or
command-line arguments to run a single watchdog check. Designed to be
safe for cron, no-agent mode, and manual invocation.

Must NOT mutate GitHub, Kanban, repo files, request Codex, or merge.
"""

from __future__ import annotations

import argparse
import configparser
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from watch_pr_gate_state import run as watchdog_run, EXIT_ARGUMENT_ERROR

DEFAULT_OUTPUT_MODE = "summary"  # summary | compact | json


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only one-shot PR gate watchdog runner. "
        "Loads config from file and/or command-line args. "
        "Always calls watch_pr_gate_state.py; never mutates anything.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="INI config file path. Section [watchdog], key=arg name.",
    )
    parser.add_argument("--repo-owner", type=str, default=None)
    parser.add_argument("--repo-name", type=str, default=None)
    parser.add_argument("--pr-number", type=int, default=None)
    parser.add_argument("--base-branch", type=str, default=None)
    parser.add_argument(
        "--output",
        choices=["summary", "compact", "json"],
        default=None,
        help="Output mode: summary (default Telegram text), compact (one-liner), or json.",
    )
    parser.add_argument(
        "--allowed-file",
        action="append",
        default=[],
        dest="allowed_files",
        help="File path that the PR is allowed to change (repeatable). "
        "Passed through to classify_pr_gate_state.py.",
    )
    parser.add_argument(
        "--expected-head",
        type=str,
        default=None,
        help="Expected head SHA. Passed to classifier for strict head check.",
    )
    return parser.parse_args(argv)


def load_config(config_path: str) -> dict[str, str]:
    """Load values from an INI [watchdog] section."""
    cfg = configparser.ConfigParser()
    cfg.read(config_path)
    if not cfg.has_section("watchdog"):
        raise ValueError(f"Config file {config_path!r} has no [watchdog] section")
    return dict(cfg.items("watchdog"))


def _cli_provided_key(cli_args: argparse.Namespace) -> set[str]:
    """Return the set of argument names that CLI explicitly set (not None).

    Treats list defaults (e.g. allowed_files=[]) as UN-provided so that config
    values can override them. Only non-None scalar values count as provided.
    """
    provided = set()
    for key in dir(cli_args):
        if key.startswith("_"):
            continue
        value = getattr(cli_args, key)
        # Only treat non-None scalar values as explicitly provided.
        # Empty lists/dicts from defaults are not considered CLI input.
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            continue
        provided.add(key)
    return provided


def merge_args(cli_args: argparse.Namespace, config: dict[str, str]) -> list[str]:
    """Build a flat argv list for watchdog_run(), with CLI taking precedence over config.

    Config values are added only when CLI did not provide a value for that key.
    """
    argv: list[str] = []
    cli_keys = _cli_provided_key(cli_args)

    # Config defaults (lowest priority) — skip keys CLI already provided
    for key, value in config.items():
        cli_key = key.replace("-", "_")
        # Skip if CLI already provided this key
        if cli_key in cli_keys and key not in ("allowed_file", "allowed_files"):
            continue
        if key in ("allowed_file", "allowed_files"):
            # CLI allowed_files take precedence; config is skipped
            if cli_key in cli_keys:
                continue
            for item in value.split(","):
                item = item.strip()
                if item:
                    argv.extend(["--allowed-file", item])
        elif value and value != "None":
            argv.extend([f"--{key}", value])

    # CLI overrides (highest priority)
    for key in dir(cli_args):
        if key.startswith("_"):
            continue
        value = getattr(cli_args, key)
        if value is None:
            continue
        if key == "allowed_files":
            for f in value:
                argv.extend(["--allowed-file", f])
        elif key == "config":
            pass  # config is consumed before this function
        else:
            argv.extend([f"--{key.replace('_', '-')}", str(value)])

    return argv


def run(argv: list[str] | None = None) -> int:
    cli = parse_args(argv)

    if cli.config:
        try:
            config = load_config(cli.config)
        except ValueError:
            # Config errors are argument/configuration errors → exit 3
            sys.exit(EXIT_ARGUMENT_ERROR)
    else:
        config = {}

    merged_argv = merge_args(cli, config)

    # Determine output mode and append appropriate flag
    output = (cli.output or config.get("output", DEFAULT_OUTPUT_MODE)).lower()
    if output == "json":
        merged_argv.append("--json")
    elif output == "compact":
        merged_argv.append("--compact")
    # else: default summary — watch_pr_gate_state.py prints by default (no flag needed)

    # Strip wrapper-only flags that watchdog parser doesn't accept.
    # watch_pr_gate_state.py uses --json/--compact/--exit-code-only, not --output.
    wrapper_only_flags = {"--output"}
    filtered_argv = []
    i = 0
    while i < len(merged_argv):
        if merged_argv[i] in wrapper_only_flags:
            i += 2  # skip flag and its value
        else:
            filtered_argv.append(merged_argv[i])
            i += 1

    return watchdog_run(filtered_argv)


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())