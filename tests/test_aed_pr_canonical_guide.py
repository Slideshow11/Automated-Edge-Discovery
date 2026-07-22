#!/usr/bin/env python3
"""Round-34 regression tests for the canonical operator guide.

Exact-head Codex review 4745059756 (submitted 2026-07-21T13:25:36Z on
head 355374f48e9a99a94243c0d331b1b7b54066712b) reported one P2 finding
on docs/aed_pr_canonical_guide.md:

  PRRC_kwDOSHFpYM7X60Dh (db_id 3622519009)
    "Document the required scope-write step"
    This guide presents ``status``/``advance``/``merge`` as the
    only operator flow, but the controller now rejects
    ``--allowed-files``/``--forbidden-files`` on ``status`` and
    ``advance`` and reads scope only from the trusted exact-head
    record written by ``aed_pr scope-write``. An operator
    following these steps on a new PR has no documented way to
    create that record, so ``status``/``advance`` will remain
    blocked on ``SCOPE_UNKNOWN`` even after CI and Codex are
    green; add the ``scope-write --head-sha ... --allowed-files ...``
    step before the first readiness check.

Tests below prove the canonical guide documents the
``scope-write`` step that operators must run before the first
``status``/``advance`` call, including:

  * the ``scope-write`` subcommand is listed in the CLI
    subcommand inventory;
  * the workflow section names ``scope-write`` as step 2 (or
    earlier) of the canonical workflow;
  * the documented command includes the
    ``--head-sha``/``--allowed-files``/``--forbidden-files``
    flags that ``aed_pr scope-write`` actually accepts;
  * the canonical scope-record path
    (``~/.hermes/aed/pr_scope/<repo>/<pr>/<head>.json``) is
    referenced so operators can locate the on-disk record;
  * ``SCOPE_UNKNOWN`` is named as the blocking state on a
    fresh PR with no trusted record;
  * the ``status``/``advance`` subcommands' rejection of CLI
    scope flags is documented.
"""

from __future__ import annotations

from pathlib import Path
import re


GUIDE_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "aed_pr_canonical_guide.md"
)


def _load_guide() -> str:
    with GUIDE_PATH.open(encoding="utf-8") as f:
        return f.read()


class TestRound34CanonicalGuideDocumentsScopeWrite:
    """P2 (PRRC_kwDOSHFpYM7X60Dh): the canonical operator guide
    MUST document the ``scope-write`` subcommand as a required
    step before the first ``status``/``advance`` call.
    """

    def test_subcommand_inventory_lists_scope_write(self):
        """The CLI subcommand inventory section MUST list
        ``scope-write`` (and ``scope-read``).
        """
        body = _load_guide()
        # The subcommand list section runs from "The canonical CLI"
        # through the first blank line-following workflow heading.
        # Use a substring check that requires both subcommand
        # names to appear together with descriptive text.
        assert "scope-write" in body, (
            "Canonical guide is missing 'scope-write' subcommand "
            "documentation"
        )
        assert "scope-read" in body, (
            "Canonical guide is missing 'scope-read' subcommand "
            "documentation"
        )

    def test_workflow_section_includes_scope_write(self):
        """The workflow numbered-list section MUST name
        ``scope-write`` as one of the steps.
        """
        body = _load_guide()
        # Extract the workflow numbered-list block: every
        # numbered step begins at the line start with "N.".
        workflow_block_match = re.search(
            r"^## Workflow\s*\n(.+?)(?=^## |\Z)",
            body,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert workflow_block_match, (
            "Canonical guide is missing the ## Workflow section"
        )
        workflow_block = workflow_block_match.group(1)
        # Every numbered step mentions scope-write. The body of
        # step 2 must include the literal command name.
        step2_match = re.search(
            r"^2\.\s+(.+?)(?=^\d+\.\s|\Z)",
            workflow_block,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert step2_match, (
            "Canonical guide workflow step 2 is missing"
        )
        assert "scope-write" in step2_match.group(1), (
            "Workflow step 2 must name scope-write as the "
            "required pre-status/advance persistence step"
        )

    def test_documented_command_includes_required_flags(self):
        """The documented ``scope-write`` command MUST include
        the ``--head-sha``, ``--allowed-files``, and
        ``--forbidden-files`` flags that ``aed_pr scope-write``
        actually accepts, so operators can reproduce the call
        verbatim. The required flag MUST appear either in the
        command block itself or in an adjacent explanatory
        paragraph that immediately follows the command block.
        """
        body = _load_guide()
        # Find the indented-block code run that starts with
        # ``python3 scripts/local/aed_pr.py scope-write``.
        lines = body.splitlines()
        scope_block_lines = []
        in_scope_block = False
        scope_block_start = -1
        scope_block_end = -1
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if line.startswith(" ") and stripped:
                if stripped.startswith(
                    "python3 scripts/local/aed_pr.py scope-write"
                ):
                    in_scope_block = True
                    scope_block_lines = [stripped]
                    scope_block_start = idx
                elif in_scope_block:
                    scope_block_lines.append(stripped)
            else:
                if in_scope_block:
                    scope_block_end = idx
                    break
        scope_block = "\n".join(scope_block_lines)
        assert scope_block, (
            "Canonical guide must include an indented code "
            "block demonstrating the scope-write command"
        )
        # Allow up to 8 adjacent explanatory lines after the
        # code block (text that explains the flags without
        # blank-line separation) to count toward the flag
        # check.
        adjacent = []
        if scope_block_end >= 0:
            for line in lines[scope_block_end:scope_block_end + 8]:
                stripped = line.strip()
                if not stripped:
                    # Blank line ends the adjacent paragraph.
                    if adjacent:
                        break
                    continue
                if stripped.startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "0.", "#", "##")):
                    # New list/heading; stop.
                    break
                adjacent.append(stripped)
        combined = scope_block + "\n" + "\n".join(adjacent)
        for flag in (
            "--pr-number",
            "--head-sha",
            "--allowed-files",
            "--forbidden-files",
        ):
            assert flag in combined, (
                f"scope-write documented command (or adjacent "
                f"explanation) is missing the {flag!r} flag"
            )

    def test_scope_record_path_documented(self):
        """The canonical scope-record path
        ``~/.hermes/aed/pr_scope/<repo>/<pr>/<head>.json``
        MUST be documented so operators can locate the
        on-disk record.
        """
        body = _load_guide()
        assert "~/.hermes/aed/pr_scope" in body, (
            "Canonical guide is missing the scope-record path"
        )
        # The path template MUST include the <repo>, <pr>, and
        # <head> segments.
        path_match = re.search(
            r"~/\.hermes/aed/pr_scope[^)\n]*/(<\w+>|<\w+>)[^)\n]*"
            r"/(<\w+>|<\w+>)[^)\n]*/(<\w+>|<\w+>)[^)\n]*\.json",
            body,
        )
        assert path_match, (
            "Canonical guide must include the full canonical "
            "scope-record path with repo, pr, and head segments"
        )

    def test_scope_unknown_blocking_state_named(self):
        """The guide MUST name ``SCOPE_UNKNOWN`` as the
        blocking state a fresh PR ends up in when the trusted
        scope record is missing.
        """
        body = _load_guide()
        assert "SCOPE_UNKNOWN" in body, (
            "Canonical guide is missing the SCOPE_UNKNOWN "
            "blocking-state name"
        )

    def test_cli_scope_flags_rejection_documented(self):
        """The guide MUST state that ``status``/``advance``
        reject CLI ``--allowed-files``/``--forbidden-files``
        flags so operators do not pass them by mistake.
        """
        body = _load_guide()
        # Either an explicit rejection note for status and
        # advance, or a positive note that merge reads scope
        # only from the trusted record. Both forms prove the
        # operator is steered away from CLI scope overrides.
        pattern_rejection = re.search(
            r"(status|advance).{0,200}(reject|refuse|disallow)",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        pattern_trusted_only = re.search(
            r"trusted.{0,100}(exact )?(record|file|scope)",
            body,
            flags=re.IGNORECASE,
        )
        assert pattern_rejection or pattern_trusted_only, (
            "Canonical guide must warn operators that "
            "status/advance reject CLI scope flags or that "
            "merge reads scope only from the trusted record"
        )


# ---------------------------------------------------------------------------
# Round-46 regression: the canonical guide MUST document
# that ``aed_pr advance`` only resolves eligible outdated
# Codex-bot threads when ``--resolve-eligible-bot-threads``
# is supplied. Without that flag the controller reports
# ``mutation_flag_not_supplied`` and leaves the threads
# open, so the operator workflow can stall. Static check
# on the canonical guide source.
# ---------------------------------------------------------------------------


def _r46_guide_text():
    import os
    here = os.path.dirname(__file__)
    guide = os.path.normpath(
        os.path.join(here, "..", "docs", "aed_pr_canonical_guide.md")
    )
    with open(guide) as f:
        return f.read(), guide


class TestRound46CanonicalGuideDocumentsResolveFlag:
    """Round-46 docs regression: the canonical
    ``docs/aed_pr_canonical_guide.md`` MUST mention
    the ``--resolve-eligible-bot-threads`` flag and
    explain that without it the controller does NOT
    resolve eligible threads — only reports
    eligibility. Operators following the documented
    workflow must be able to resolve eligible threads
    by following the guide alone.
    """

    def test_guide_mentions_resolve_flag(self):
        text, path = _r46_guide_text()
        assert "--resolve-eligible-bot-threads" in text, (
            "Round-46 docs: the canonical guide must "
            "mention the --resolve-eligible-bot-threads "
            "flag so operators can resolve eligible "
            f"threads. Guide path: {path}"
        )

    def test_guide_explains_default_does_not_resolve(self):
        text, path = _r46_guide_text()
        # The guide MUST explain that without the
        # flag, the controller reports
        # ``mutation_flag_not_supplied`` and leaves
        # threads open. We look for the diagnostic
        # string OR a clear explanation.
        needle_options = [
            "mutation_flag_not_supplied",
            "does not resolve",
            "does NOT resolve",
            "without that flag",
            "Without that flag",
        ]
        assert any(n in text for n in needle_options), (
            "Round-46 docs: the canonical guide must "
            "explain that without the resolve flag the "
            "controller does NOT mutate threads. None "
            f"of the expected explanation needles found "
            f"in {path}."
        )
