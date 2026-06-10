"""AED Lifecycle State Registry CLI.

A small stdlib-only reader and validator for the canonical AED lifecycle
state registry at ``schemas/aed_lifecycle_states_v1.json``.

This script is a registry reader, not an authority grant. It records what
each canonical state means, what evidence is required, which mutations are
allowed or forbidden, and whether human authorization is required. It does
not itself perform any GitHub mutation.

Usage:
    python3 scripts/local/aed_lifecycle_states.py --list
    python3 scripts/local/aed_lifecycle_states.py --state HOLD_PR_CI_PENDING
    python3 scripts/local/aed_lifecycle_states.py --validate
    python3 scripts/local/aed_lifecycle_states.py --list --json
    python3 scripts/local/aed_lifecycle_states.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The registry file lives at the repo root in ``data/``. This script is in
# ``scripts/local/``, so the default registry path is two parents up plus
# ``schemas/aed_lifecycle_states_v1.json``.
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "schemas" / "aed_lifecycle_states_v1.json"
)

# Required fields for every state entry. A missing required field is a
# registry validation error.
REQUIRED_STATE_FIELDS = (
    "category",
    "description",
    "evidence_required",
    "allowed_next_states",
    "allowed_mutations",
    "forbidden_mutations",
    "human_authorization_required",
    "merge_allowed",
    "closeout_allowed",
    "notes",
)

# Valid category values. The registry must only use these.
VALID_CATEGORIES = frozenset(
    {"hold", "ready", "mutation_pending", "terminal", "informational"}
)

# Valid mutation tokens. Allowed and forbidden mutations are drawn from this
# set. Unknown mutation tokens are a validation error.
VALID_MUTATIONS = frozenset(
    {
        "pr_merge",
        "audit_append",
        "worktree_remove",
        "thread_resolve",
        "comment_delete",
        "review_dismiss",
        "admin_merge",
        "auto_merge",
        "force_push",
        "pr_close",
        "worktree_update",
    }
)


class RegistryError(Exception):
    """Raised when the registry is structurally invalid."""


def load_registry(path: Path) -> dict[str, Any]:
    """Load and parse the registry JSON file.

    Returns the parsed dict. Raises RegistryError on missing file or invalid
    JSON. Field-level validation is performed by :func:`validate_registry`.
    """
    if not path.exists():
        raise RegistryError(f"registry file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise RegistryError(f"registry file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError("registry root must be a JSON object")
    return data


def _coerce_list_field(
    entry: dict[str, Any], state_name: str, field_name: str, errors: list[str]
) -> list[Any]:
    """Return a list-typed value for ``field_name`` from ``entry``.

    Defensive type check: the registry is a hand-edited JSON file, and
    malformed list-valued fields (truthy non-iterables such as the integer
    ``1``; falsy non-lists such as the empty string ``""`` or empty object
    ``{}``) must be reported as a validation error and treated as an empty
    list for downstream checks. The previous implementation used
    ``entry.get(field, []) or []``, which would either raise a Python
    traceback on iteration of a truthy non-iterable or silently accept a
    falsy non-list (e.g. ``""`` -> ``[]``) and miss the malformed field.
    """
    if field_name not in entry:
        return []
    value = entry[field_name]
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(
            f"state '{state_name}' {field_name} must be a list, "
            f"got {type(value).__name__}"
        )
        return []
    return value


def _check_no_conflicting_mutations(entry: dict[str, Any], state_name: str) -> list[str]:
    """Return a list of mutation tokens that appear in both allowed and forbidden."""
    errors: list[str] = []
    allowed = _coerce_list_field(
        entry, state_name, "allowed_mutations", errors
    )
    forbidden = _coerce_list_field(
        entry, state_name, "forbidden_mutations", errors
    )
    if errors:
        # Type errors are reported by the caller via the validator loop.
        # Do not attempt to compute overlap on malformed inputs.
        return []
    return sorted(set(allowed) & set(forbidden))


def _check_merge_allowed_only_for_authorized(
    entry: dict[str, Any], state_name: str
) -> list[str]:
    """Return a list of human-readable errors when merge_allowed is set on
    a state that should not permit merge.

    The registry is conservative: ``merge_allowed`` may only be true on
    states that have already passed all pre-merge gates and are awaiting
    the human authorization phrase. The current single state with
    ``merge_allowed: true`` is ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``.
    Other states that set ``merge_allowed: true`` are flagged.
    """
    if entry.get("merge_allowed", False) and state_name != "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION":
        return [
            f"merge_allowed is true but only MERGE_READY_AWAITING_HUMAN_AUTHORIZATION "
            f"may set it; got '{state_name}'"
        ]
    return []


def _check_terminal_no_mutations(
    entry: dict[str, Any], state_name: str
) -> list[str]:
    """Terminal states must not declare allowed_mutations, must not permit
    merge or closeout, and must have an empty allowed_next_states list.
    """
    if entry.get("category") != "terminal":
        return []
    errors: list[str] = []
    if entry.get("allowed_mutations"):
        errors.append(
            f"terminal state '{state_name}' declares allowed_mutations; must be empty"
        )
    if entry.get("merge_allowed"):
        errors.append(
            f"terminal state '{state_name}' sets merge_allowed=true; must be false"
        )
    if entry.get("closeout_allowed"):
        errors.append(
            f"terminal state '{state_name}' sets closeout_allowed=true; must be false"
        )
    if entry.get("allowed_next_states"):
        errors.append(
            f"terminal state '{state_name}' declares allowed_next_states; must be empty"
        )
    return errors


def _check_merge_states_require_human_authorization(
    entry: dict[str, Any], state_name: str
) -> list[str]:
    """States that permit merge (or the resolve-only pre-merge step) must
    require human authorization.
    """
    permits_merge = entry.get("merge_allowed", False)
    allowed_muts = entry.get("allowed_mutations")
    permits_resolve_only = (
        isinstance(allowed_muts, list) and "thread_resolve" in allowed_muts
    )
    if (permits_merge or permits_resolve_only) and not entry.get(
        "human_authorization_required", False
    ):
        return [
            f"state '{state_name}' permits a guarded mutation "
            f"(merge_allowed={permits_merge}, thread_resolve allowed={permits_resolve_only}) "
            f"but does not set human_authorization_required=true"
        ]
    return []


def validate_registry(registry: dict[str, Any]) -> list[str]:
    """Validate the registry structure and per-state policy expectations.

    Returns a list of human-readable error messages. An empty list means
    the registry is valid. The validator is deliberately strict because
    governance vocabulary must be unambiguous.
    """
    errors: list[str] = []

    schema_version = registry.get("schema_version")
    if schema_version != 1:
        errors.append(f"schema_version must be 1, got {schema_version!r}")

    registry_kind = registry.get("registry_kind")
    if registry_kind != "aed.lifecycle_state_registry.v1":
        errors.append(
            f"registry_kind must be 'aed.lifecycle_state_registry.v1', got {registry_kind!r}"
        )

    categories = registry.get("categories")
    if not isinstance(categories, list) or not categories:
        errors.append("categories must be a non-empty list")
        known_categories: set[str] = set()
    else:
        known_categories = set(categories)
        unknown = sorted(known_categories - VALID_CATEGORIES)
        if unknown:
            errors.append(
                f"categories contains unknown values: {unknown}; "
                f"valid categories are {sorted(VALID_CATEGORIES)}"
            )
        missing = sorted(VALID_CATEGORIES - known_categories)
        if missing:
            errors.append(
                f"categories is missing required values: {missing}"
            )

    states = registry.get("states")
    if not isinstance(states, dict) or not states:
        errors.append("states must be a non-empty dict mapping name -> entry")
        return errors

    # Names must be unique (dict keys already guarantee that, but the
    # validator must be defensive). All names must be non-empty strings.
    state_names: list[str] = []
    for name, entry in states.items():
        if not isinstance(name, str) or not name:
            errors.append("state names must be non-empty strings")
            continue
        if not isinstance(entry, dict):
            errors.append(f"state '{name}' entry must be a JSON object")
            continue
        state_names.append(name)
        for field in REQUIRED_STATE_FIELDS:
            if field not in entry:
                errors.append(f"state '{name}' missing required field '{field}'")
        category = entry.get("category")
        if category is not None and category not in known_categories:
            errors.append(
                f"state '{name}' category '{category}' is not in registry.categories"
            )
        for mut in _coerce_list_field(
            entry, name, "allowed_mutations", errors
        ):
            if mut not in VALID_MUTATIONS:
                errors.append(
                    f"state '{name}' allowed_mutations contains unknown mutation '{mut}'"
                )
        for mut in _coerce_list_field(
            entry, name, "forbidden_mutations", errors
        ):
            if mut not in VALID_MUTATIONS:
                errors.append(
                    f"state '{name}' forbidden_mutations contains unknown mutation '{mut}'"
                )
        # The helper records a "must be a list" error if the field is
        # present but not a list. It also returns [] for missing or None
        # values, which is the documented default.
        _coerce_list_field(entry, name, "evidence_required", errors)
        _coerce_list_field(entry, name, "allowed_next_states", errors)
        if not isinstance(entry.get("description", None), str):
            errors.append(
                f"state '{name}' description must be a string"
            )
        if not isinstance(entry.get("notes", None), str):
            errors.append(
                f"state '{name}' notes must be a string"
            )
        for bfield in ("human_authorization_required", "merge_allowed", "closeout_allowed"):
            if not isinstance(entry.get(bfield, None), bool):
                errors.append(
                    f"state '{name}' {bfield} must be a boolean"
                )
        errors.extend(_check_no_conflicting_mutations(entry, name))
        errors.extend(_check_merge_allowed_only_for_authorized(entry, name))
        errors.extend(_check_terminal_no_mutations(entry, name))
        errors.extend(_check_merge_states_require_human_authorization(entry, name))

    # Forward-reference integrity: every state name referenced in
    # allowed_next_states must be a known state, UNLESS the referencing
    # state is terminal (in which case allowed_next_states is forbidden
    # by _check_terminal_no_mutations above).
    known_names = set(state_names)
    for name, entry in states.items():
        if not isinstance(entry, dict):
            continue
        # Coerce safely: the type-error case for allowed_next_states has
        # already been recorded by the validator loop above, so here we
        # only need to avoid raising on malformed input.
        nxt_list = entry.get("allowed_next_states", [])
        if not isinstance(nxt_list, list):
            nxt_list = []
        for nxt in nxt_list:
            if nxt not in known_names:
                errors.append(
                    f"state '{name}' allowed_next_states references unknown state '{nxt}'"
                )

    return errors


def list_states(registry: dict[str, Any]) -> list[str]:
    """Return the canonical state names in stable (insertion) order."""
    return list(registry.get("states", {}).keys())


def get_state(registry: dict[str, Any], state_name: str) -> dict[str, Any] | None:
    """Return the entry for ``state_name`` or None if absent."""
    entry = registry.get("states", {}).get(state_name)
    return entry if isinstance(entry, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AED lifecycle state registry CLI: list, inspect, or validate the "
            "canonical lifecycle state registry at schemas/aed_lifecycle_states_v1.json."
        )
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
        help=f"Path to registry JSON (default: {DEFAULT_REGISTRY_PATH})",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List canonical state names, one per line",
    )
    parser.add_argument(
        "--state",
        type=str,
        default=None,
        help="Print one state entry as JSON",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate registry structure and policy expectations",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print the full registry as JSON",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --list, print the full registry JSON instead of names only",
    )
    args = parser.parse_args(argv)

    try:
        registry = load_registry(args.registry)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.validate:
        errors = validate_registry(registry)
        if errors:
            print("registry validation FAILED:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print("registry validation PASSED")
        return 0

    if args.state:
        entry = get_state(registry, args.state)
        if entry is None:
            print(f"error: unknown state '{args.state}'", file=sys.stderr)
            return 1
        print(json.dumps({args.state: entry}, indent=2, sort_keys=True))
        return 0

    if args.all:
        print(json.dumps(registry, indent=2, sort_keys=True))
        return 0

    if args.list:
        if args.json:
            print(json.dumps({"states": list_states(registry)}, indent=2))
        else:
            for name in list_states(registry):
                print(name)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
