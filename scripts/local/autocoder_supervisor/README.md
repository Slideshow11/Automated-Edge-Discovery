# AED Autocoder Supervisor (source-controlled v1)

This package is the source-controlled version of the working
v5 supervisor that has been operating as a host-level daemon
under `~/.hermes/aed-supervisor/`. The package is a Python
module — `scripts.local.autocoder_supervisor` — that can be
installed under any prefix and configured via a TOML file.

The historical external supervisor at
`~/.hermes/aed-supervisor/` remains the source of truth for
the PR #416 audit evidence. It is **not** modified, restarted,
or replaced by this package. The new stabilization PR uses an
isolated canary instance of the source-controlled supervisor
that operates against a separate state directory and service
identity.

## What this package gives you

- **Importable and unit-testable** without depending on
  anything under `~/.hermes` or any user-specific absolute
  path.
- **Configurable** via a TOML file that the validator
  rejects if it contains tokens or absolute user-specific
  paths.
- **Operational documentation** for installation, upgrade,
  rollback, isolated testing, and runtime-state migration.
- **Strongly-typed contracts** (`contracts.py`) for every
  supervisor concept (configuration, provider policy,
  readiness state, exact-head evidence snapshot, actionable
  reviewer event, durable event-consumption record, worker
  lease, worker-launch receipt, cooldown state, terminal
  merge evidence).
- **Versioned invariant ledger** (`INVARIANTS.md`) listing
  15 invariants with enforcing implementation and asserting
  tests.
- **Dry-run validation** (`validate.py`) that checks
  configuration validity, required directories, file
  permissions, repository accessibility, provider policy,
  service-instance scope, and conflicting worker leases.

## What this package does NOT do

- It does **not** merge the PR. Merge is the only human
  boundary and is performed by the operator's merge
  authorization command.
- It does **not** redesign the reviewer-provider interface.
  The `ReviewProvider` abstraction described in the
  long-term README is deferred to the standalone Autocoder
  extraction.
- It does **not** modify, restart, or replace the historical
  external supervisor at `~/.hermes/aed-supervisor/`. That
  supervisor's audit evidence remains preserved.
- It does **not** contain tokens, credentials, or
  user-specific absolute paths in any committed file.

## Layout

```text
scripts/local/autocoder_supervisor/
├── __init__.py
├── contracts.py
├── config.py
├── supervisor.py
├── validate.py
├── INVARIANTS.md
├── README.md
├── examples/
│   └── aed-supervisor.example.toml
├── service/
│   └── aed-supervisor@.service.template
└── docs/
    ├── INSTALL.md
    ├── UPGRADE.md
    ├── ROLLBACK.md
    ├── ISOLATED_TEST.md
    └── STATE_MIGRATION.md
```

## Quick start (isolated canary)

```bash
# 1. Pick a non-user-specific install root.
sudo install -d /opt/aed-supervisor-canary
sudo cp -r scripts/local/autocoder_supervisor /opt/aed-supervisor-canary/
sudo cp scripts/local/pyproject.toml /opt/aed-supervisor-canary/pyproject.toml

# 2. Install into a venv (no PYTHONPATH needed once installed).
sudo python3 -m venv /opt/aed-supervisor-canary/venv
sudo /opt/aed-supervisor-canary/venv/bin/pip install --no-deps \
    /opt/aed-supervisor-canary

# 3. Copy the example config and edit it.
sudo cp /opt/aed-supervisor-canary/autocoder_supervisor/examples/aed-supervisor.example.toml \
    /etc/aed-supervisor-canary.toml
sudo $EDITOR /etc/aed-supervisor-canary.toml

# 4. Run the dry-run validation (installed package, no repo PYTHONPATH).
PYTHONPATH= \
    /opt/aed-supervisor-canary/venv/bin/python \
    -m autocoder_supervisor.validate --config /etc/aed-supervisor-canary.toml

# 5. Run a single iteration (--once exits after one heartbeat).
PYTHONPATH= \
    AED_PR_NUMBER=<N> AED_REPO_OWNER=<owner> AED_REPO_NAME=<repo> \
    AED_AUTHORITATIVE_HEAD=<head_sha> \
    /opt/aed-supervisor-canary/venv/bin/python \
    -m autocoder_supervisor.supervisor \
    --config /etc/aed-supervisor-canary.toml --once
```

See `docs/INSTALL.md` for production installation.
