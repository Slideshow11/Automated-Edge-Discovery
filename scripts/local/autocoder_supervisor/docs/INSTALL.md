# Install — production

The source-controlled supervisor is a Python package. It can
be installed system-wide or into a virtualenv. The
recommended production deployment uses a dedicated, dedicated
system user and a systemd user service.

## 1. Install the Python package

Either system-wide:

```bash
sudo install -d /opt/aed-supervisor
sudo cp -r scripts/local/autocoder_supervisor /opt/aed-supervisor/
sudo python3 -m pip install --no-deps /opt/aed-supervisor
```

…or into a virtualenv:

```bash
sudo install -d /opt/aed-supervisor/venv
sudo python3 -m venv /opt/aed-supervisor/venv
sudo /opt/aed-supervisor/venv/bin/pip install --no-deps \
    /path/to/aed-supervisor
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

```bash
sudo cp scripts/local/autocoder_supervisor/service/aed-supervisor.service.template \
    /etc/systemd/system/aed-supervisor@aed-supervisor.service
sudo systemctl daemon-reload
sudo systemctl enable --now aed-supervisor@aed-supervisor.service
```

(The `%i` placeholder is replaced by the systemd instance
name — e.g. `aed-supervisor` above — which lets multiple
supervisor instances coexist under different names with
different state directories and configurations.)

## 5. Verify

```bash
systemctl --user status aed-supervisor@aed-supervisor.service
journalctl --user -u aed-supervisor@aed-supervisor.service -f
```

You should see the supervisor log "supervisor started
(source-controlled v1)" within one heartbeat (default 120s).
