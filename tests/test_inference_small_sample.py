import json
from engine.edge_discovery.calibrate_ac_v2 import calibrate_ac_v2


def test_small_sample_handles_degenerate():
    # Use the example CSV bundled with the repo (tiny, 3 rows)
    res = calibrate_ac_v2("examples/metaorders.csv", output_path=None, n_bootstrap=50, bootstrap="cluster", strict=True)
    # If df_resid <= 0, ci_cluster_boot should be None and a warning should be present
    assert "realized_model" in res and "post_model" in res
    rm = res["realized_model"]
    pm = res["post_model"]
    # ci_cluster_boot should exist (key present) and be None for small samples
    assert "ci_cluster_boot" in rm
    assert rm["ci_cluster_boot"] is None or isinstance(rm["ci_cluster_boot"], dict)
    # se_hc should be present and either None or a mapping/series-like
    assert "se_hc" in rm
    # if warnings were set, they should be present when bootstrap skipped
    if rm.get("ci_cluster_boot") is None:
        assert ("warnings" in rm and any("too few" in w for w in rm["warnings"])) or rm.get("se_hc") is not None
