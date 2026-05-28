# DataManifest Examples

This document describes the JSON example manifests in `examples/data_manifests/`.

---

## What is a DataManifest example?

Example manifests are JSON files demonstrating how to use `DatasetManifest` to declare externally provisioned local datasets consumed by AED. They are documentation-by-example, not executable configuration files.

---

## Why local and externally provisioned?

AED does not download, scrape, or acquire data. All datasets are pre-provisioned by an upstream process before AED runs. The manifests in this directory describe what those upstream datasets are, where they live locally, and how they were obtained — without instructing AED to fetch them.

---

## Loading a manifest

```python
from engine.edge_discovery.data_manifest import load_dataset_manifest

manifest = load_dataset_manifest(
    "examples/data_manifests/preearn_options_2021_local.json"
)
print(manifest.dataset_id)      # preearn_options_2021_lane_0
print(manifest.role.value)       # options_backtest_db
print(manifest.source_kind.value) # local_sqlite
```

---

## Validating a manifest

`validate_dataset_manifest` checks that the declared path exists and is the correct type (file vs. directory) for the `source_kind`. It performs no schema inspection, no SQLite queries, and no network I/O.

```python
from engine.edge_discovery.data_manifest import validate_dataset_manifest

result = validate_dataset_manifest(manifest)
print(result.ok)          # True if path exists and has correct type/suffix
print(result.path_exists)
print(result.errors)
print(result.warnings)
```

---

## Path portability

The example manifests contain placeholder paths (e.g. `./examples/local_only/options_2021_lane_0.sqlite`). Replace with your local dataset paths. AED does not download or generate data.

If you run AED on a different machine:
1. Update `path` in the manifest to point to your local dataset location
2. Or pass the correct path at runtime via smoke script arguments (`--options-db-path`, `--preearn-repo-path`)

The smoke scripts (`scripts/local/smoke_preearn_lifecycle.py` and `scripts/local/smoke_preearn_bridge.py`) accept `--options-db-path` and `--preearn-repo-path` to override the manifest path without editing the JSON.

---

## These are not download instructions

AED has **no downloader**. The manifests describe data that already exists locally. Data acquisition (download, purchase, scraping, cleaning) happens upstream and is entirely outside AED's scope.

---

## No vendor dependency

These manifests reference local paths on a specific machine. They do not pull data from any vendor API. AED is data-source agnostic and has no core dependency on IVOL, FMP, Polygon, ORATS, CBOE, Yahoo, or any other data service.

---

## Available example manifests

| File | role | source_kind | Describes |
|---|---|---|---|
| `preearn_options_2021_local.json` | `options_backtest_db` | `local_sqlite` | Pre-provisioned SQLite options DB consumed by the pre-earnings adapter |
| `preearn_repo_local.json` | `preearn_repo` | `external_cli` | Local pre-earnings repo checkout, used via script interface only |
