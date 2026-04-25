import numpy as np
import pandas as pd
from engine.edge_discovery import inference as inf


def make_clustered_data(n_clusters=10, cluster_size=3, rng_seed=0):
    rng = np.random.default_rng(seed=rng_seed)
    rows = []
    for c in range(n_clusters):
        cluster_effect = rng.normal(scale=0.5)
        for i in range(cluster_size):
            phi = rng.lognormal(mean=-3, sigma=0.5)
            sqrt_phi = np.sqrt(phi)
            eps = rng.normal(scale=0.1)
            y = 0.2 * sqrt_phi + 0.05 * phi + cluster_effect + eps
            rows.append({"y": y, "sqrt_phi": sqrt_phi, "phi": phi, "cluster": f"c{c}"})
    return pd.DataFrame(rows)


def test_cluster_bootstrap_ci_coverage():
    df = make_clustered_data(n_clusters=12, cluster_size=4, rng_seed=1)
    df["sqrt_phi_x"] = df["sqrt_phi"]
    df["phi_x"] = df["phi"]
    df.to_csv('/tmp/test_cluster.csv', index=False)
    # formula
    formula = "y ~ sqrt_phi_x + phi_x"
    ci = inf.cluster_bootstrap_ci(formula=formula, df=df, cluster_col="cluster", n_bootstrap=100, rng_seed=1)
    # Expect keys for intercept, sqrt_phi_x and phi_x
    assert any("sqrt_phi_x" in k for k in ci.keys())
