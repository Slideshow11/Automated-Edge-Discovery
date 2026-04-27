import json
from pathlib import Path

from engine.edge_discovery.hypotheses.batch import BatchResult
from scripts.local.smoke_preearn_bridge import main as smoke_main


def test_smoke_argparse_and_dry_run(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    ledger = tmp_path / "ledger.jsonl"
    args = [
        "--preearn-repo-path",
        "/tmp/fake_repo",
        "--options-db-path",
        "/tmp/fake_options.db",
        "--dry-run",
        "--output-dir",
        str(out),
        "--ledger-path",
        str(ledger),
    ]

    # Run the script's main entry with args (dry-run should be default safe path)
    rc = smoke_main(args)
    assert rc == 0

    # Summary JSON must exist
    files = list(out.glob("batch_*.json"))
    assert len(files) == 1

    # Ledger must exist and contain at least one line
    assert ledger.exists()
    content = [ln.strip() for ln in ledger.read_text().splitlines() if ln.strip()]
    assert len(content) >= 1


def test_smoke_default_is_dry(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out2"
    args = [
        "--preearn-repo-path",
        "/tmp/fake_repo",
        "--options-db-path",
        "/tmp/fake_options.db",
        "--output-dir",
        str(out),
    ]
    rc = smoke_main(args)
    assert rc == 0
    files = list(out.glob("batch_*.json"))
    assert len(files) == 1

