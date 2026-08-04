# Install — production

The source-controlled supervisor is a Python package. It can
be installed system-wide or into a virtualenv. The
recommended production deployment uses a dedicated, dedicated
system user and a systemd user service.

## 1. Install the Python package

Either system-wide:

```bash
# Install root is /opt/aed-supervisor. The package lives at
# <install-root>/autocoder_supervisor/ and the packaging
# manifest at <install-root>/pyproject.toml. Both must
# coexist at the install root for `pip install` to find
# the package.
sudo install -d /opt/aed-supervisor
sudo cp -r scripts/local/autocoder_supervisor/. /opt/aed-supervisor/
sudo python3 -m pip install --no-deps /opt/aed-supervisor
```

…or into a virtualenv:

```bash
sudo install -d /opt/aed-supervisor/venv
sudo python3 -m venv /opt/aed-supervisor/venv
# Copy the package contents to a temp install root and
# install from there, because the venv's pip needs the
# package and manifest to live side-by-side at the
# install root.
sudo install -d /opt/aed-supervisor-install
sudo cp -r scripts/local/autocoder_supervisor/. /opt/aed-supervisor-install/
sudo /opt/aed-supervisor/venv/bin/pip install --no-deps \
    /opt/aed-supervisor-install
sudo rm -rf /opt/aed-supervisor-install
```

## 2. Prepare state + log directories

```bash
sudo install -d -o aed-supervisor -g aed-supervisor -m 0700 \
    /var/lib/aed-supervisor/state
sudo install -d -o aed-supervisor -g aed-supervisor -m 0750 \
    /var/log/aed-supervisor
```

## 3. Configure

```bash
sudo install -d -o aed-supervisor -g aed-supervisor -m 0750 /etc/aed-supervisor
sudo cp scripts/local/autocoder_supervisor/examples/aed-supervisor.example.toml \
    /etc/aed-supervisor/aed-supervisor.toml
sudo chown aed-supervisor:aed-supervisor /etc/aed-supervisor/aed-supervisor.toml
sudo chmod 0640 /etc/aed-supervisor/aed-supervisor.toml
sudo -u aed-supervisor $EDITOR /etc/aed-supervisor/aed-supervisor.toml
```

The validator (run with `python3 -m autocoder_supervisor.validate --config …`)
will reject credentials or absolute user-specific paths in
the configuration.

## 4. Install the systemd service

The committed file is a systemd *template unit*. The systemd
convention requires template units to be named with the
literal `@` in the filename (so `foo@.service` becomes
`foo@<instance>.service` when instantiated). Copy the file to
`/etc/systemd/system/aed-supervisor@.service`:

```bash
sudo cp scripts/local/autocoder_supervisor/service/aed-supervisor@.service.template \
    /etc/systemd/system/aed-supervisor@.service
sudo systemctl daemon-reload
sudo systemctl enable --now aed-supervisor@<instance>.service
```

The `<instance>` placeholder names the supervisor instance —
e.g. `aed-supervisor@canary.service`. `%i` inside the unit
expands to `<instance>`, so different instances can coexist
with different state directories and configurations.

## 5. Verify

```bash
sudo systemctl status aed-supervisor@<instance>.service
sudo journalctl -u aed-supervisor@<instance>.service -f
```

You should see the supervisor log "supervisor started
(source-controlled v1)" within one heartbeat (default 120s).
