Retention & Scheduler Examples

Example: Cron entry (daily cleanup at 03:30)
-------------------------------------------
0 3 * * * /usr/bin/python3 /path/to/repo/engine/edge_discovery/cleanup_audit_reports.py --max-age-days 30 --max-files 1000

Systemd timer + service example
-------------------------------
# /etc/systemd/system/audit-cleanup.service
[Unit]
Description=Edge Discovery audit reports cleanup

[Service]
Type=oneshot
User=deploy
WorkingDirectory=/path/to/repo
ExecStart=/usr/bin/python3 /path/to/repo/engine/edge_discovery/cleanup_audit_reports.py --max-age-days 30 --max-files 1000

# /etc/systemd/system/audit-cleanup.timer
[Unit]
Description=Run audit cleanup daily

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target

Enable and start the timer:
  sudo systemctl daemon-reload
  sudo systemctl enable --now audit-cleanup.timer

Prometheus metrics
------------------
If you enable the metrics module (engine.edge_discovery.metrics), make sure the
metrics server is started by your service supervisor (systemd unit or container
entrypoint). The metrics module is optional and the runner will still function
if prometheus_client is not installed.
