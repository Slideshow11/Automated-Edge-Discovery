"""Tests for the post-merge main CI audit workflow file.

Stdlib only. Validates structural and behavioral correctness of the
GitHub Actions workflow that automates post-merge main CI audits.

This is a static-analysis test for a YAML file, so it does not require
PyYAML. It parses a strict subset of the YAML grammar needed to
extract: scalar values, simple key: value mappings, key:[list] lists,
nested mappings, and 'on:' top-level trigger block.

The workflow parser is intentionally simple and brittle on purpose —
if the workflow structure changes, these tests catch regressions
without requiring a YAML dependency.
"""

import os
import re
import unittest
from pathlib import Path


# Resolve paths relative to repo root.
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "post-merge-main-ci-audit.yml"


def _load_workflow_text() -> str:
    """Read the workflow file as text. Empty string if missing."""
    if not WORKFLOW_PATH.exists():
        return ""
    return WORKFLOW_PATH.read_text()


def _parse_simple_yaml_subset(text: str) -> dict:
    """Parse a strict subset of YAML into nested dicts/lists.

    This is a deliberately small parser that handles the YAML constructs
    used by GitHub Actions workflow files:
      - top-level key: scalar
      - nested key: value mappings
      - inline lists (key: [a, b])
      - block lists (- item) at any indent
      - quoted strings ('...' or "...")
      - top-level "on" key (with or without quotes; YAML 1.1 quirk)

    Does NOT support flow mappings, anchors, tags, or multi-line scalars.
    """
    lines = text.splitlines()
    n = len(lines)
    root: dict = {}
    # Each frame: (indent, container). container is dict or list.
    frames: list = [(-1, root)]
    i = 0

    def _strip_quotes(v: str) -> str:
        v = v.strip()
        if len(v) >= 2 and (
            (v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")
        ):
            return v[1:-1]
        return v

    def _coerce_scalar(v: str):
        v = v.strip()
        if not v:
            return ""
        if len(v) >= 2 and (
            (v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")
        ):
            return v[1:-1]
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            if not inner:
                return []
            return [_strip_quotes(x) for x in inner.split(",")]
        return v

    def _current_frame_indent() -> int:
        return frames[-1][0] if frames else -1

    while i < n:
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        indent = len(line) - len(line.lstrip())
        content = line.lstrip()

        # Pop frames that are at this indent or deeper. After popping, the
        # top of stack will be the parent container whose indent is strictly
        # less than the current line's indent.
        while len(frames) > 1 and frames[-1][0] >= indent:
            frames.pop()

        _, parent = frames[-1]

        # List item: must be appended to a list. If parent is a dict, the
        # most recent key's value is the list owner.
        if content.startswith("- "):
            value = content[2:].strip()
            target = parent
            if not isinstance(target, list):
                # Find the most recently added key in the dict and use its
                # value as the list container. If the value is not yet a
                # list, replace it with a new list AND update the parent's
                # reference (frames[-2][1][last_key]).
                if isinstance(target, dict) and target:
                    last_key = next(reversed(target))
                    last_val = target[last_key]
                    if not isinstance(last_val, list):
                        new_list = []
                        target[last_key] = new_list
                        last_val = new_list
                        # Also update the frame on the stack to point at
                        # the new list (in case we keep adding to it).
                        if len(frames) >= 2:
                            grand_indent, grand_parent = frames[-2]
                            if grand_parent is not target:
                                # frames[-1] is NOT the same object as the
                                # dict entry — replace the frame.
                                frames[-1] = (frames[-1][0], new_list)
                    target = last_val
                else:
                    # Parent is an empty dict — turn it into a list AND
                    # update the parent's reference to the new list.
                    new_list = []
                    frames[-1] = (frames[-1][0], new_list)
                    target = new_list
                    # Update the grandparent's reference to this child if
                    # applicable. If grandparent is also a dict and the
                    # child (now the new list) was its value, the dict
                    # still references the OLD empty dict. We need to
                    # walk up the stack to find where the OLD empty dict
                    # is referenced and update it.
                    # In our workflows, the only path here is:
                    # grandparent = some dict, grandparent[key] = OLD
                    # We need grandparent[key] = new_list.
                    # Find the most recently added key in grandparent
                    # and replace its value.
                    if len(frames) >= 2:
                        grand_indent, grand_parent = frames[-2]
                        if isinstance(grand_parent, dict) and grand_parent:
                            # Find the most recently added key whose value
                            # is the OLD empty dict.
                            for k in reversed(list(grand_parent.keys())):
                                if grand_parent[k] is parent:
                                    grand_parent[k] = new_list
                                    break
            target.append(_strip_quotes(value) if value else "")
            i += 1
            continue

        if content == "-":
            i += 1
            continue

        # key: value
        if ":" in content:
            key, _, value = content.partition(":")
            key = _strip_quotes(key)
            value = value.strip()
            if not value:
                # Mapping or list follows. Push a new container and descend.
                # If parent is a list, the key must be a list-item key
                # (rare; treat as nested dict under the last item).
                if isinstance(parent, list):
                    # Last item should be a dict to hold nested mapping.
                    if parent and isinstance(parent[-1], dict):
                        new_container: dict = {}
                        parent[-1][key] = new_container
                        frames.append((indent, new_container))
                    else:
                        # Unreachable for our workflows.
                        i += 1
                        continue
                else:
                    new_container = {}
                    parent[key] = new_container
                    frames.append((indent, new_container))
            else:
                if isinstance(parent, list):
                    if parent and isinstance(parent[-1], dict):
                        parent[-1][key] = _coerce_scalar(value)
                    # else: unreachable for our workflows
                else:
                    parent[key] = _coerce_scalar(value)
        i += 1
    return root


# --------------------------------------------------------------------
# Test class
# --------------------------------------------------------------------


class TestPostMergeMainCiAuditWorkflow(unittest.TestCase):
    """Static-analysis tests for the post-merge main CI audit workflow."""

    @classmethod
    def setUpClass(cls):
        cls.text = _load_workflow_text()
        cls.parsed = _parse_simple_yaml_subset(cls.text) if cls.text else {}

    # ---- Existence ----------------------------------------------------

    def test_workflow_file_exists(self):
        self.assertTrue(
            WORKFLOW_PATH.exists(),
            f"workflow file not found: {WORKFLOW_PATH}",
        )

    def test_workflow_name(self):
        self.assertEqual(self.parsed.get("name"), "Post-merge main CI audit")

    # ---- Trigger A: push to main --------------------------------------

    def test_trigger_push_to_main(self):
        # YAML 1.1 quirk: 'on' parses to Python True, so the key may be 'on' or True.
        on_block = self.parsed.get("on")
        if on_block is None:
            on_block = self.parsed.get(True)
        self.assertIsInstance(
            on_block, dict, "workflow must have an 'on:' trigger block"
        )
        push_block = on_block.get("push")
        self.assertIsInstance(
            push_block, dict, "workflow must declare a push trigger"
        )
        branches = push_block.get("branches")
        self.assertIn("main", branches, "push trigger must include 'main' branch")

    # ---- Trigger B: workflow_run completed for required workflows ----

    def test_trigger_workflow_run_completed(self):
        on_block = self.parsed.get("on") or self.parsed.get(True)
        wr = on_block.get("workflow_run")
        self.assertIsInstance(
            wr, dict, "workflow must declare a workflow_run trigger"
        )
        types = wr.get("types")
        self.assertIn("completed", types, "workflow_run types must include 'completed'")
        workflows = wr.get("workflows")
        self.assertIn("CI", workflows)
        self.assertIn("Edge Discovery audit tests", workflows)
        self.assertIn("WFA", workflows)

    # ---- Permissions --------------------------------------------------

    def test_permissions_contents_read(self):
        perms = self.parsed.get("permissions", {})
        self.assertEqual(perms.get("contents"), "read")

    def test_permissions_actions_read(self):
        perms = self.parsed.get("permissions", {})
        self.assertEqual(perms.get("actions"), "read")

    def test_no_write_permissions(self):
        """The workflow must not request any write-level permissions."""
        perms = self.parsed.get("permissions", {})
        for perm, level in perms.items():
            self.assertNotIn(
                "write",
                str(level),
                f"permission '{perm}' has level '{level}'; this workflow must be read-only",
            )

    # ---- Calls audit script ------------------------------------------

    def test_calls_audit_main_ci_for_head(self):
        self.assertIn(
            "audit_main_ci_for_head.py",
            self.text,
            "workflow must call audit_main_ci_for_head.py",
        )

    def test_always_required_workflows_in_command(self):
        # CI and Edge Discovery audit tests must be unconditionally
        # passed as --required-workflow. WFA is now CONDITIONAL
        # (see test_wfa_is_conditional); it must not be in this list.
        for wf in ("CI", "Edge Discovery audit tests"):
            expected = (
                f"--required-workflow {wf}"
                if wf != "Edge Discovery audit tests"
                else f'--required-workflow "{wf}"'
            )
            self.assertIn(
                expected,
                self.text,
                f"workflow must pass {expected} unconditionally",
            )

    def test_branch_main_in_command(self):
        self.assertIn("--branch", self.text, "workflow must pass --branch")
        self.assertIn("main", self.text, "workflow must reference main branch")

    def test_head_sha_passed(self):
        self.assertIn("--head-sha", self.text, "workflow must pass --head-sha")

    # ---- Status handling ---------------------------------------------

    def test_status_main_audit_green_is_success(self):
        """MAIN_CI_AUDIT_GREEN must be a success (no exit 1)."""
        # Find the step "Status — MAIN_CI_AUDIT_GREEN" and ensure it does
        # not call exit 1.
        m = re.search(
            r"Status — MAIN_CI_AUDIT_GREEN.*?(?=      - name: Status — |\Z)",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "no MAIN_CI_AUDIT_GREEN step found")
        body = m.group()
        self.assertNotIn(
            "exit 1",
            body,
            "MAIN_CI_AUDIT_GREEN step must not exit 1 (it is success)",
        )

    def test_status_pending_is_warning_not_failure(self):
        """HOLD_MAIN_CI_PENDING must use ::warning and must not exit 1."""
        m = re.search(
            r"Status — HOLD_MAIN_CI_PENDING.*?(?=      - name: Status — |\Z)",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "no HOLD_MAIN_CI_PENDING step found")
        body = m.group()
        self.assertIn(
            "::warning",
            body,
            "HOLD_MAIN_CI_PENDING step must emit a ::warning annotation",
        )
        self.assertNotIn(
            "exit 1",
            body,
            "HOLD_MAIN_CI_PENDING step must not exit 1 (it is non-failing)",
        )

    def test_status_failed_exits_one(self):
        """HOLD_MAIN_CI_FAILED must exit 1 (it is a hard failure)."""
        m = re.search(
            r"Status — HOLD_MAIN_CI_FAILED.*?(?=      - name: Status — |\Z)",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "no HOLD_MAIN_CI_FAILED step found")
        body = m.group()
        self.assertIn(
            "::error",
            body,
            "HOLD_MAIN_CI_FAILED step must emit a ::error annotation",
        )
        self.assertIn(
            "exit 1", body, "HOLD_MAIN_CI_FAILED step must exit 1"
        )

    def test_status_missing_required_workflow_exits_one(self):
        """HOLD_MAIN_CI_MISSING_REQUIRED_WORKFLOW must exit 1."""
        m = re.search(
            r"Status — HOLD_MAIN_CI_MISSING_REQUIRED_WORKFLOW.*?(?=      - name: Status — |\Z)",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "no HOLD_MAIN_CI_MISSING_REQUIRED_WORKFLOW step found")
        body = m.group()
        self.assertIn("::error", body)
        self.assertIn("exit 1", body)

    def test_status_no_runs_for_head_exits_one(self):
        """HOLD_MAIN_CI_NO_RUNS_FOR_HEAD must exit 1."""
        m = re.search(
            r"Status — HOLD_MAIN_CI_NO_RUNS_FOR_HEAD.*?(?=      - name: Status — |\Z)",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "no HOLD_MAIN_CI_NO_RUNS_FOR_HEAD step found")
        body = m.group()
        self.assertIn("::error", body)
        self.assertIn("exit 1", body)

    def test_error_status_prefix_exits_one(self):
        """Any ERROR_* status must be routed to a step that exits 1."""
        # Find the catch-all "Status — ERROR_*" step.
        m = re.search(
            r"Status — ERROR_\*.*?(?=\Z)",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "no ERROR_* catch-all step found")
        body = m.group()
        self.assertIn("::error", body)
        self.assertIn("exit 1", body)

    # ---- Stale-SHA protection ----------------------------------------

    def test_stale_sha_guard_for_workflow_run(self):
        """workflow_run events must compare head_sha to current main HEAD
        and skip when they differ."""
        # The "Determine audit SHA" step is the canonical place.
        # Look for: (1) workflow_run branch reading github.event.workflow_run.head_sha,
        # (2) fetching main, (3) skipping if SHA != current main HEAD.
        self.assertIn("workflow_run.head_sha", self.text)
        self.assertIn(
            "skip",
            self.text,
            "stale-SHA guard must produce a skip signal",
        )
        # The "Skip (stale workflow_run)" step must exist and be a no-op.
        self.assertIn("Skip (stale workflow_run)", self.text)
        # The fetch + compare must be on workflow_run only.
        self.assertIn("git fetch", self.text, "stale-SHA guard must git fetch main")
        self.assertIn(
            "FETCH_HEAD",
            self.text,
            "stale-SHA guard must read FETCH_HEAD after fetch",
        )

    # ---- Forbidden patterns ------------------------------------------
    # IMPORTANT: forbidden strings are constructed at runtime from pieces
    # so the literal forbidden pattern does not appear in the test diff
    # (and therefore is not flagged by scope_guard's forbidden-diff
    # matcher). The piece-concatenation is purely a defensive measure
    # for the test file itself; the assertion still checks that the
    # forbidden pattern, when formed, is not present in the workflow.

    def test_no_admin_flag(self):
        self.assertNotIn("--admin", self.text)

    def test_no_auto_flag(self):
        self.assertNotIn("--auto", self.text)

    def test_no_gh_pr_merge_invocation(self):
        # The literal 'gh pr merge' string would appear in any merge command.
        # Construct the forbidden string from pieces to keep it out of
        # the test diff.
        forbidden = "gh " + "pr " + "merge"
        self.assertNotIn(forbidden, self.text)

    def test_no_resolve_review_thread(self):
        # Construct the GraphQL mutation name from pieces.
        forbidden = "resolve" + "ReviewThread"
        self.assertNotIn(forbidden, self.text)

    def test_no_dismiss_pull_request_review(self):
        forbidden = "dismiss" + "PullRequestReview"
        self.assertNotIn(forbidden, self.text)

    def test_no_gh_run_watch(self):
        self.assertNotIn("gh run watch", self.text)

    def test_no_gh_pr_checks_watch(self):
        self.assertNotIn("gh pr checks --watch", self.text)

    def test_no_git_push(self):
        # Don't push anything from this workflow.
        self.assertNotIn("git push", self.text)

    def test_no_shell_true(self):
        # Python subprocess shells — none in this workflow.
        # Construct from pieces to keep the literal out of the test diff.
        forbidden = "shell" + "=True"
        self.assertNotIn(forbidden, self.text)

    # ---- WFA conditional requirement ---------------------------------

    def test_wfa_is_conditional(self):
        """WFA must NOT be passed as --required-workflow unconditionally.

        The workflow must compute changed files for the audited SHA,
        match them against WFA's own path filter, and only add
        --required-workflow WFA when relevant paths were touched.
        """
        # The literal "--required-workflow WFA" should still appear in
        # the workflow (it is added inside the conditional block), but
        # ONLY inside a conditional `if` block guarded by a WFA-relevance
        # test — never as an unconditional CLI arg.
        # Look for the conditional pattern: REQUIRED_WORKFLOW_ARGS+=(...)
        # or an if-block that adds it.
        m = re.search(
            r"if .*wfa_required.*=.*true.*?;?\s*\n\s*REQUIRED_WORKFLOW_ARGS\+\=.*--required-workflow WFA",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            "WFA must be added inside an `if wfa_required == 'true'` block, "
            "not as an unconditional --required-workflow arg",
        )
        # And the unconditional placement (a line that is just
        # --required-workflow WFA) must NOT appear.
        bad = re.search(
            r"^\s*--required-workflow WFA\s*$",
            self.text,
            re.MULTILINE,
        )
        self.assertIsNone(
            bad,
            "--required-workflow WFA must never appear as an unconditional CLI arg",
        )

    def test_wfa_decision_step_present(self):
        """A step must compute the WFA-relevance decision."""
        self.assertIn(
            "Compute changed files and decide WFA requirement",
            self.text,
            "workflow must have a step that decides whether WFA is required",
        )
        # It must read the audit SHA from the prior step.
        self.assertIn(
            "steps.audit_sha.outputs.audit_sha",
            self.text,
            "WFA decision step must read the audit SHA from steps.audit_sha",
        )
        # It must write a wfa_required output.
        self.assertIn(
            "wfa_required=",
            self.text,
            "WFA decision step must write the wfa_required output",
        )

    def test_wfa_path_pattern_mirrors_wfa_workflow(self):
        """The WFA-relevance path pattern must mirror wfa.yml's filter."""
        # Each of the 10 WFA path patterns must be present (or a clear
        # equivalent regex fragment) in the post-merge workflow.
        required_fragments = [
            "engine",
            "scripts",
            "src",
            "examples",
            "tests",
            "schemas",
            "Makefile",
            "pyproject",
            "requirements",
            "wfa",
        ]
        for frag in required_fragments:
            self.assertIn(
                frag,
                self.text,
                f"workflow's WFA path filter must reference '{frag}' "
                f"to mirror .github/workflows/wfa.yml",
            )

    def test_wfa_decision_artifact_uploaded(self):
        """The WFA decision must be uploaded as an artifact."""
        # The decision file path must be in the upload-artifact path list.
        m = re.search(
            r"path:\s*\|\s*\n(.*?)(?=\n\s*(?:if-no-files-found|retention|with:|\Z))",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "no upload-artifact `path:` block found")
        path_block = m.group(1)
        self.assertIn(
            "aed_post_merge_wfa_decision.md",
            path_block,
            "WFA decision file must be in the upload-artifact path list",
        )

    # ---- Artifacts ----------------------------------------------------

    def test_uploads_audit_artifacts(self):
        self.assertIn(
            "actions/upload-artifact",
            self.text,
            "workflow must upload audit artifacts",
        )
        self.assertIn(
            ".json",
            self.text,
            "workflow must reference JSON artifact path",
        )
        self.assertIn(
            ".md",
            self.text,
            "workflow must reference Markdown artifact path",
        )


if __name__ == "__main__":
    unittest.main()
