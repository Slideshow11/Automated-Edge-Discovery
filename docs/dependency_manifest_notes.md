# AED Dependency Manifest Notes
# ================================
#
# This file documents the dependency manifest structure for the AED project.
# It is NOT a dependency declaration itself — see requirements.txt for the
# operational list used by CI.
#
# Classification key:
#   runtime  — imported by engine/scripts at runtime (required for normal operation)
#   dev      — needed only for testing, linting, or local development
#   workflow — present in requirements.txt but used only in CI/GitHub Actions workflows
#   optional — integration tools present in requirements.txt but not imported in source
#
# Package-by-package classification:
#   openai              : runtime  (LLM calls throughout engine + scripts)
#   python-dotenv       : optional  (listed; no source imports found)
#   fire                : optional  (listed; no source imports found)
#   httpx               : workflow  (HTTP client in PR gate scripts; no source imports confirmed — commented out in requirements.txt pending verification)
#   rich                : runtime  (CLI output throughout scripts)
#   tenacity            : optional  (listed; no source imports found)
#   prompt_toolkit      : optional  (listed; no source imports found)
#   pyyaml              : runtime  (config/schema parsing)
#   requests            : workflow (listed; no source imports; commented out in requirements.txt)
#   jinja2              : optional  (listed; no source imports found)
#   pydantic>=2.0       : optional  (listed; no source imports confirmed — commented out in requirements.txt pending verification)
#   PyJWT[crypto]       : runtime  (JWT decode in auth flows)
#   debugpy             : optional  (listed; no source imports found)
#   firecrawl-py        : optional  (listed; no source imports found)
#   parallel-web>=0.4.2  : optional  (listed; no source imports found)
#   fal-client          : optional  (listed; no source imports found)
#   edge-tts            : optional  (listed; no source imports found)
#   croniter            : optional  (listed; no source imports found)
#   python-telegram-bot : optional  (listed; no source imports found)
#   discord.py>=2.0     : optional  (listed; no source imports found)
#   aiohttp>=3.9.0      : runtime  (async HTTP for gateway integrations)
#   prometheus_client   : runtime  (metrics server; imported by engine/edge_discovery/metrics.py)
#   boto3               : runtime  (S3 upload; lazy import in engine/edge_discovery/auditor.py)
#   patsy               : runtime  (formula parsing in calibrate_costs)
#   pandas              : both     (dev: test fixtures; runtime: imported by engine modules)
#   statsmodels         : both     (dev: statistical estimation; runtime: imported by diagnostics/inference)
#   matplotlib          : dev      (calibrate_costs plotting; not imported by engine core)
#   numpy               : both     (dev: test fixtures; runtime: imported throughout engine)
#
# Manifest file roles:
#   requirements.txt    : Operational CI install file. CI installs this with pip install -r.
#                         The pyproject.toml annotation claiming it is "canonical" is
#                         technically misleading — pyproject.toml only declares test/dev
#                         optional-dependencies, not runtime dependencies.
#   pyproject.toml      : Build metadata + test/dev extras. install_requires is empty.
#                         canonical for PEP 517 build, not for runtime dependency management.
#   setup.cfg           : Legacy; install_requires is empty.
#   .github/workflows/  : ci.yml installs requirements.txt (operational)
#                         wfa.yml installs pip install -e . + explicit test deps
#                         audit-edge-discovery.yml installs pip install -e ".[test]"
#
# Notes:
#   - venv does NOT appear in requirements.txt or pyproject.toml.
#     The GitHub Dependency Graph may have reported it from a stale build artifact
#     (automated_edge_discovery.egg-info/top_level.txt) — that file is gitignored
#     and regenerated on each pip install -e. It is NOT a dependency declaration.
#   - Dependabot is disabled on this repository. No CVE visibility via gh CLI.

## opensrc dependency audit 002

Report path: /tmp/aed_runs/opensrc-dependency-audit-002/DEPENDENCY_AUDIT.md

Scan: 126 Python files scanned across scripts/, src/, tests/ using AST-based import detection.

### Removed (commented out in requirements.txt)

- pydantic>=2.0 — zero imports found across 126 scanned Python files
- requests — zero imports found; only referenced in test scope assertions (forbidden/not-used markers)
- httpx — zero imports found; only referenced in test scope assertions (forbidden/not-used markers)

### Deferred

- patsy: deferred because it may be statsmodels-adjacent and needs separate numeric workflow verification before removal
- dependency pinning policy (numpy, pandas, pytest, PyYAML, openai): deferred to a separate PR

### Verification method

opensrc v0.7.2 used to inspect package source code at:
OPENSRC_HOME=/tmp/aed_runs/opensrc-dependency-audit-002/opensrc_cache

Packages inspected: requests (2.34.2), pydantic (2.13.4), httpx, jsonschema (4.26.0), PyYAML (6.0.3), pytest (9.0.3), pandas (3.0.3), numpy (2.4.5 — partial)

### Safety invariants

- No package installed into repo
- No lockfile modified
- No opensrc cache committed
- No production board, dispatch, memory/profile, or skill changes
