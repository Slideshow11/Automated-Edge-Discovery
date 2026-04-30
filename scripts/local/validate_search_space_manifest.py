#!/usr/bin/env python3
"""
Local SearchSpaceManifest v1 validator.
Validates a single SearchSpaceManifest JSON entry against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys

SEARCH_MODE_ENUM = [
    "manual_grid",
    "fixed_sweep",
    "literature_replication",
    "ablation",
    "falsification",
    "exploratory_agent_assisted",
]

FORBIDDEN_MODE_KEYS = [
    "autonomous_search",
    "bayesian_optimization",
    "genetic_programming",
    "automated_promotion",
    "live_trading",
]


class Blocker:
    """Structured validation blocker."""
    def __init__(self, code, object_type, field, message):
        self.code = code
        self.object_type = object_type
        self.field = field
        self.message = message

    def to_dict(self):
        return {
            "code": self.code,
            "object_type": self.object_type,
            "field": self.field,
            "message": self.message,
        }


def validate(path):
    """Validate a SearchSpaceManifest JSON file. Returns list of blockers."""
    blockers = []

    # Read
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        blockers.append(Blocker("invalid_json", "search_space_manifest", "file", f"Could not read file: {e}"))
        return blockers

    # Parse
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as e:
        blockers.append(Blocker("invalid_json", "search_space_manifest", "$", f"Invalid JSON: {e}"))
        return blockers

    # Non-object root check
    if not isinstance(entry, dict):
        blockers.append(Blocker("invalid_object", "search_space_manifest", "$", "SearchSpaceManifest must be a JSON object"))
        return blockers

    required_fields = [
        "search_space_id",
        "search_mode",
        "allowed_data_manifests",
        "allowed_features",
        "allowed_labels",
        "allowed_parameter_ranges",
        "validation_scheme",
        "budget",
        "forbidden_modes",
    ]

    # Required fields
    for field in required_fields:
        if field not in entry:
            blockers.append(Blocker("missing_required_field", "search_space_manifest_entry", field, f"{field} is required"))

    # ID format
    ssm_id = entry.get("search_space_id")
    if ssm_id is not None:
        if not isinstance(ssm_id, str) or not re.match(r"^SSM-[0-9]{4}-[0-9]{4}$", ssm_id):
            blockers.append(Blocker("invalid_id_format", "search_space_manifest_entry", "search_space_id", f"search_space_id '{ssm_id}' does not match SSM-YYYY-NNNN format"))

    # search_mode enum
    mode = entry.get("search_mode")
    if mode is not None:
        if mode not in SEARCH_MODE_ENUM:
            blockers.append(Blocker("invalid_enum", "search_space_manifest_entry", "search_mode", f"search_mode '{mode}' not in allowed set"))

    # allowed_data_manifests
    adm = entry.get("allowed_data_manifests")
    if adm is not None:
        if not isinstance(adm, list):
            blockers.append(Blocker("invalid_list", "search_space_manifest_entry", "allowed_data_manifests", "allowed_data_manifests must be a list"))
        elif len(adm) == 0:
            blockers.append(Blocker("empty_required_list", "search_space_manifest_entry", "allowed_data_manifests", "allowed_data_manifests must not be empty"))

    # allowed_features
    af = entry.get("allowed_features")
    if af is not None and not isinstance(af, list):
        blockers.append(Blocker("invalid_list", "search_space_manifest_entry", "allowed_features", "allowed_features must be a list"))

    # allowed_labels
    al = entry.get("allowed_labels")
    if al is not None and not isinstance(al, list):
        blockers.append(Blocker("invalid_list", "search_space_manifest_entry", "allowed_labels", "allowed_labels must be a list"))

    # allowed_parameter_ranges
    apr = entry.get("allowed_parameter_ranges")
    if apr is not None and not isinstance(apr, dict):
        blockers.append(Blocker("invalid_object", "search_space_manifest_entry", "allowed_parameter_ranges", "allowed_parameter_ranges must be an object"))

    # forbidden_modes
    fm = entry.get("forbidden_modes")
    if fm is not None:
        if not isinstance(fm, dict):
            blockers.append(Blocker("invalid_object", "search_space_manifest_entry", "forbidden_modes", "forbidden_modes must be an object"))
        else:
            for key in FORBIDDEN_MODE_KEYS:
                val = fm.get(key)
                if val is True:
                    blockers.append(Blocker("forbidden_mode_enabled", "search_space_manifest_entry", key, f"{key} is forbidden and must not be enabled"))

    # budget
    budget = entry.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            blockers.append(Blocker("invalid_object", "search_space_manifest_entry", "budget", "budget must be an object"))
        else:
            mt = budget.get("max_trials")
            if mt is not None:
                if not isinstance(mt, int) or mt <= 0:
                    blockers.append(Blocker("invalid_budget", "search_space_manifest_entry", "budget.max_trials", "max_trials must be an integer > 0"))
            mpc = budget.get("max_parameter_combinations")
            if mpc is not None and (not isinstance(mpc, int) or mpc <= 0):
                blockers.append(Blocker("invalid_budget", "search_space_manifest_entry", "budget.max_parameter_combinations", "max_parameter_combinations must be an integer > 0"))
            mrt = budget.get("max_runtime_minutes")
            if mrt is not None and (not isinstance(mrt, int) or mrt <= 0):
                blockers.append(Blocker("invalid_budget", "search_space_manifest_entry", "budget.max_runtime_minutes", "max_runtime_minutes must be an integer > 0"))
            map_ = budget.get("max_agent_proposals")
            if map_ is not None and (not isinstance(map_, int) or map_ < 0):
                blockers.append(Blocker("invalid_budget", "search_space_manifest_entry", "budget.max_agent_proposals", "max_agent_proposals must be an integer >= 0"))

    return blockers


def main(args=None):
    parser = argparse.ArgumentParser(description="Validate a SearchSpaceManifest v1 JSON file")
    parser.add_argument("file", help="Path to SearchSpaceManifest JSON file")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parsed = parser.parse_args(args)

    blockers = validate(parsed.file)
    count = len(blockers)

    if parsed.format == "json":
        output = {
            "file": parsed.file,
            "blockers_count": count,
            "blockers": [b.to_dict() for b in blockers],
        }
        print(json.dumps(output, indent=2))
    else:
        if count == 0:
            print(f"File: {parsed.file}")
            print("blockers_count: 0")
        else:
            print(f"File: {parsed.file}")
            print(f"blockers_count: {count}")
            for b in blockers:
                print(f"  [{b.code}] {b.object_type}.{b.field}: {b.message}")

    # Exit 2 for usage/read/config errors (invalid_json code), exit 1 for blockers
    parse_errors = [b for b in blockers if b.code == "invalid_json"]
    sys.exit(2 if parse_errors else (1 if blockers else 0))


if __name__ == "__main__":
    main()
